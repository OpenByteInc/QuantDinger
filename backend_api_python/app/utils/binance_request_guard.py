"""Coordinate Binance REST budgets and server-directed cooldowns across workers."""

from __future__ import annotations

import math
import logging
import os
import re
import time
import uuid
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from app.utils.exchange_request_budget import ExchangeRateLimitError, SharedRequestBudget

logger = logging.getLogger(__name__)


def _trace_enabled(url):
    return bool(_scope(url)) and os.getenv("BINANCE_REST_TRACE_ENABLED", "0").strip().lower() in {"1", "true", "yes"}


def _trace_event(url, method, event, trace_id, *, status="-", elapsed_ms=0, weight="-", retry_after=0):
    try:
        target = urlsplit(url)
        logger.info(
            "Binance REST trace event=%s trace_id=%s pid=%s method=%s target=%s%s "
            "status=%s elapsed_ms=%s used_weight_1m=%s retry_after_sec=%s",
            event, trace_id, os.getpid(), method.upper(), target.hostname, target.path,
            status, elapsed_ms, weight, retry_after,
        )
    except Exception:
        # Diagnostic failures must never alter an exchange order outcome.
        pass


def traced_binance_request(request, url, method="GET"):
    """Trace transport invocations, not guard admissions; never replay a request."""
    if not _trace_enabled(url):
        return request()
    trace_id = uuid.uuid4().hex
    started = time.monotonic()
    _trace_event(url, method, "start", trace_id)
    try:
        response = request()
    except Exception:
        _trace_event(url, method, "transport_error", trace_id,
                     elapsed_ms=int((time.monotonic() - started) * 1000))
        raise
    try:
        headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
        raw_weight = headers.get("x-mbx-used-weight-1m", "")
        weight = int(raw_weight) if raw_weight.isdigit() else "-"
        _trace_event(url, method, "response", trace_id, status=int(response.status_code),
                     elapsed_ms=int((time.monotonic() - started) * 1000), weight=weight)
    except Exception:
        pass
    return response


class BinanceRateLimitError(ExchangeRateLimitError):
    def __init__(self, retry_after: float, reason: str = "cooldown"):
        super().__init__(retry_after, reason, exchange="Binance")


def _scope(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host == "binance.com" or host.endswith(".binance.com"):
        return host if "demo" in host or "testnet" in host else "live"
    if host == "testnet.binance.vision":
        return host
    if host == "data-api.binance.vision":
        return "live"
    return ""


class BinanceRequestGuard(SharedRequestBudget):
    def __init__(self):
        super().__init__(exchange="Binance", env_prefix="BINANCE")

    def _limit(self, seconds, reason="cooldown"):
        return BinanceRateLimitError(seconds, reason)

    def before_request(self, url, method="GET", *, public=False):
        try:
            return self._before_request(url, method, public=public)
        except BinanceRateLimitError as exc:
            if _trace_enabled(url):
                _trace_event(url, method, "blocked", "-", retry_after=math.ceil(exc.retry_after))
            raise

    def _before_request(self, url, method="GET", *, public=False):
        scope = _scope(url)
        if not scope:
            return
        path = urlsplit(url).path
        lane = "background" if public or path.endswith(("/userTrades", "/myTrades")) else "account"
        if method.upper() not in {"GET", "HEAD"}:
            lane = "write"
        try:
            limit = max(1, int(os.getenv("BINANCE_REST_READS_PER_SECOND", "5")))
        except ValueError:
            limit = 5
        deadline = time.monotonic() + 2
        while True:
            delay, cooldown = self._reserve(scope, lane, limit)
            if delay <= 0:
                return
            if cooldown or time.monotonic() + delay > deadline:
                raise BinanceRateLimitError(delay)
            time.sleep(min(delay, 0.1))

    def observe(self, url, status, headers, text=""):
        scope = _scope(url)
        if not scope:
            return
        headers = {str(key).lower(): str(value) for key, value in headers.items()}
        now = time.time()
        if status in {418, 429}:
            delays = []
            retry = headers.get("retry-after", "")
            try:
                delays.append(float(retry))
            except ValueError:
                try:
                    delays.append(parsedate_to_datetime(retry).timestamp() - now)
                except (TypeError, ValueError, OverflowError):
                    pass
            match = re.search(r"banned until\s+(\d{10,13})", text, re.IGNORECASE)
            if match:
                stamp = int(match.group(1))
                delays.append((stamp / 1000 if stamp > 10**11 else stamp) - now)
            seconds = max([value for value in delays if math.isfinite(value) and value > 0]
                          or [120 if status == 418 else 60]) + 1
            logger.warning("Binance REST cooldown: scope=%s path=%s status=%s retry_after_sec=%s",
                           scope, urlsplit(url).path, status, math.ceil(seconds))
            self._extend(scope, "ban", seconds)
            raise BinanceRateLimitError(seconds, f"HTTP {status}")
        try:
            weight = int(headers.get("x-mbx-used-weight-1m", "0"))
            ceiling = max(1, int(os.getenv("BINANCE_REST_WEIGHT_SOFT_LIMIT", "1800")))
        except ValueError:
            return
        if weight >= ceiling:
            try:
                self._extend(scope, "pressure", 61 - now % 60)
            except BinanceRateLimitError:
                # A successful exchange response must never become a retryable order failure.
                pass


binance_request_guard = BinanceRequestGuard()


def guarded_binance_request(request, url, *, method="GET", public=True, **kwargs):
    """Apply the same guard to ancillary REST calls outside BaseRestClient."""
    if not _scope(url):
        from app.utils.exchange_request_guard import guarded_exchange_request

        return guarded_exchange_request(request, url, method=method, **kwargs)
    binance_request_guard.before_request(url, method, public=public)
    response = traced_binance_request(lambda: request(url, **kwargs), url, method)
    try:
        binance_request_guard.observe(
            url, response.status_code, getattr(response, "headers", {}), response.text,
        )
    except Exception:
        response.close()
        raise
    return response
