"""Venue-specific REST throttling without replaying HTTP or ambiguous orders."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit

from app.utils.exchange_request_budget import ExchangeRateLimitError, SharedRequestBudget

logger = logging.getLogger(__name__)

_DOMAINS = {
    "okx": ("okx.com", "okx.cab", "okx.hk"),
    "bybit": ("bybit.com", "bybit.eu", "bybit.kz", "bybit.nl", "bybit-tr.com", "bybit.ae", "bybitgeorgia.ge", "bybick.com"),
    "bitget": ("bitget.com",),
    "gate": ("gateio.ws", "gate.com", "gateapi.io"),
    "htx": ("htx.com", "huobi.pro", "hbdm.com"),
}
_KEY_HEADERS = {"okx": "ok-access-key", "bybit": "x-bapi-api-key", "bitget": "access-key", "gate": "key"}
_CODES = {
    "okx": {"50011", "50061"},
    "bybit": {"10006", "429"},
    "bitget": {"429"},
    "gate": {"TOO_MANY_REQUESTS", "CONCURRENCY_LIMIT_EXCEEDED"},
    "htx": {"1032", "frequent-invoke", "base-request-exceed-number-limit", "base_request_exceed_number_limit", "base-user-request-exceed-limit"},
}
_HEADER_LIMITS = {
    "bybit": (("x-bapi-limit-status", "x-bapi-limit-reset-timestamp"),),
    "gate": (("x-gate-ratelimit-requests-remain", "x-gate-ratelimit-reset-timestamp"),),
    "htx": (("ratelimit-remaining", "ratelimit-reset"), ("x-hb-ratelimit-requests-remain", "x-hb-ratelimit-requests-expire")),
}
_FALLBACK_SECONDS = {"okx": 2, "bybit": 1, "bitget": 1, "gate": 10, "htx": 3}


def venue_for_url(url):
    host = (urlsplit(url).hostname or "").lower()
    return next((venue for venue, domains in _DOMAINS.items()
                 if any(host == domain or host.endswith("." + domain) for domain in domains)), "")


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _endpoint(path):
    path = re.sub(r"/(orders|accounts)/[^/]+(?=/|$)", r"/\1/*", path)
    return re.sub(r"/(contracts|currency_pairs)/[^/]+(?=/|$)", r"/\1/*", path)


@dataclass(frozen=True)
class RequestScope:
    venue: str
    ip: str
    endpoint: str
    lane: str
    limit: int
    order_budget: str = ""


class ExchangeRequestGuard:
    def __init__(self):
        self.budgets = {venue: SharedRequestBudget(exchange=venue) for venue in _DOMAINS}

    def scope(self, url, method="GET", *, headers=None, params=None):
        venue = venue_for_url(url)
        if not venue:
            return None
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        query = parse_qs(parts.query)
        params = params or {}
        key = headers.get(_KEY_HEADERS.get(venue, ""), "")
        if venue == "htx":
            key = str(params.get("AccessKeyId") or (query.get("AccessKeyId") or [""])[0])
        demo = any(token in host for token in ("testnet", "demo", "test")) or (
            headers.get("x-simulated-trading") == "1" or headers.get("paptrading") == "1"
        )
        ip = f"{venue}:{'demo' if demo else 'live'}"
        # A high-entropy API key keys a domain-separated MAC, not a password hash.
        # Keep the identifier stable across workers without storing the credential.
        owner = hmac.new(key.encode(), b"quantdinger:exchange-rest:account:v1", hashlib.sha256).hexdigest() if key else "public"
        method = method.upper()
        read = method in {"GET", "HEAD"}
        # HTX swap reads also use POST. Match known read operations, never arbitrary POSTs.
        if venue == "htx" and method == "POST":
            read = bool(re.search(r"(?:account_info|position_info|order_info|order_detail|matchresults|openorders|hisorders|query|balance|positions|position_all|order_opens|order_history|order_trades)$", parts.path))
        lane = "public" if not key else "account"
        if not read:
            lane = "write"
        endpoint = f"{ip}:{owner}:{method}:{_endpoint(parts.path)}"
        # HTX derivatives publish shared read/trade budgets for each contract family.
        if venue == "htx" and (host == "hbdm.com" or host.endswith(".hbdm.com")) and key and not parts.path.startswith("/v5/"):
            family = "linear" if "linear" in parts.path else ("inverse" if "swap" in parts.path else "delivery")
            endpoint = f"{ip}:{owner}:{family}:{'read' if read else 'trade'}"
        try:
            limit = max(1, int(os.getenv(f"{venue.upper()}_REST_READS_PER_SECOND", "5")))
        except ValueError:
            limit = 5
        if venue == "okx" and parts.path == "/api/v5/account/config":
            limit = min(limit, 2)
        order_budget = ""
        if venue == "okx" and method == "POST" and parts.path in {
            "/api/v5/trade/order", "/api/v5/trade/batch-orders",
            "/api/v5/trade/amend-order", "/api/v5/trade/amend-batch-orders",
        }:
            order_budget = f"{ip}:{owner}:new-amend-orders"
        return RequestScope(venue, ip, endpoint, lane, limit, order_budget)

    def before_request(self, url, method="GET", *, headers=None, params=None):
        scope = self.scope(url, method, headers=headers, params=params)
        if scope is None:
            return None
        budget = self.budgets[scope.venue]
        deadline = time.monotonic() + 2
        while True:
            # Cooldowns are checked separately so writes never spend a read budget.
            for key in (scope.ip, scope.endpoint, scope.order_budget):
                if not key:
                    continue
                delay, _ = budget._reserve(key, "write", scope.limit)
                if delay:
                    raise budget._limit(delay)
            if scope.lane == "write":
                return scope
            delay, _ = budget._reserve(scope.ip, scope.lane, scope.limit)
            if delay <= 0:
                return scope
            if time.monotonic() + delay > deadline:
                raise budget._limit(delay)
            time.sleep(min(delay, 0.1))

    def observe(self, scope, status, headers, text="", payload=None):
        if scope is None:
            return
        if payload is None:
            try:
                payload = json.loads(text)
            except (TypeError, ValueError):
                payload = {}
        body = payload if isinstance(payload, dict) else {}
        headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        code = next((str(body[key]) for key in ("retCode", "code", "err_code", "err-code", "label")
                     if body.get(key) is not None), "")
        limited = status == 429 or code in _CODES[scope.venue]
        ip_banned = scope.venue == "bybit" and status == 403 and "access too frequent" in text.lower()
        global_limit = ip_banned or (scope.venue == "bybit" and status == 429)
        delays = []
        retry = headers.get("retry-after", "")
        value = _number(retry)
        if value is not None:
            delays.append(value)
        elif retry:
            try:
                delays.append(parsedate_to_datetime(retry).timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                pass
        exhausted = False
        for remaining, reset in _HEADER_LIMITS.get(scope.venue, ()):
            available = _number(headers.get(remaining))
            if available is not None and available <= 0:
                exhausted = True
            if limited or exhausted:
                stamp = _number(headers.get(reset))
                if stamp is not None:
                    delays.append((stamp / 1000 if stamp > 10**11 else stamp) - time.time())
        if not (limited or ip_banned or exhausted):
            return
        seconds = max([v for v in delays if v > 0] or [_FALLBACK_SECONDS[scope.venue]]) + 0.1
        if ip_banned:
            seconds = max(seconds, 600.1)
        target = scope.ip if global_limit else scope.endpoint
        if scope.venue == "okx" and code == "50061" and scope.order_budget:
            target = scope.order_budget
        budget = self.budgets[scope.venue]
        try:
            budget._extend(target, "ban", seconds)
        except ExchangeRateLimitError:
            if limited or ip_banned:
                raise
            # A success must stay successful, even when its last quota was consumed.
        if limited or ip_banned:
            logger.warning("Exchange REST cooldown: venue=%s status=%s code=%s retry_after_sec=%s",
                           scope.venue, status, code, math.ceil(seconds))
            raise budget._limit(seconds, "IP cooldown" if global_limit else "endpoint cooldown")


exchange_request_guard = ExchangeRequestGuard()


def guarded_exchange_request(request, url, *, method="GET", **kwargs):
    scope = exchange_request_guard.before_request(url, method, headers=kwargs.get("headers"), params=kwargs.get("params"))
    response = request(url, **kwargs)
    if scope is not None:
        try:
            exchange_request_guard.observe(scope, response.status_code, getattr(response, "headers", {}), response.text)
        except Exception:
            response.close()
            raise
    return response
