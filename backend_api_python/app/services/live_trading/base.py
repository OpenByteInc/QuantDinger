"""
Base REST client helpers for direct exchange connections.

Notes:
- Keep this minimal and dependency-light (requests only).
- All secrets must be excluded from logs.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

from app.utils.resource_guard import (
    ResourceExhaustedError,
    assert_fd_available,
    is_fd_exhaustion,
    mark_fd_exhausted,
)

logger = logging.getLogger(__name__)

# Cached SSL verify setting for all live-trading REST calls (requests + SOCKS proxy).
_requests_verify_value: Optional[Union[bool, str]] = None
_ssl_verify_disabled_logged = False

# OS CA bundles (Docker / slim images: install ``ca-certificates``; corporate roots often added here too).
_SYSTEM_CA_BUNDLE_CANDIDATES: Tuple[str, ...] = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian/Ubuntu
    "/etc/ssl/cert.pem",  # Alpine, some slim images
    "/etc/pki/tls/certs/ca-bundle.crt",  # RHEL/Fedora
)


def _get_requests_verify() -> Union[bool, str]:
    """
    Resolve ``verify`` for ``requests`` when calling exchanges through proxies (e.g. PROXY_URL=socks5h://...).

    - LIVE_TRADING_SSL_VERIFY=0|false|no|off: disable verification (insecure; mitm risk).
    - LIVE_TRADING_CA_BUNDLE / REQUESTS_CA_BUNDLE / SSL_CERT_FILE / CURL_CA_BUNDLE: path to a PEM CA bundle
      (needed for corporate TLS inspection or custom roots).
    - Else a non-empty OS CA file if present (helps Gate/HTX/hbdm etc. in minimal images).
    - Otherwise certifi's bundle when available.
    """
    global _requests_verify_value, _ssl_verify_disabled_logged
    if _requests_verify_value is not None:
        return _requests_verify_value

    flag = (os.environ.get("LIVE_TRADING_SSL_VERIFY") or "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        if not _ssl_verify_disabled_logged:
            logger.warning(
                "LIVE_TRADING_SSL_VERIFY is disabled: HTTPS certificate verification is OFF for live trading "
                "requests (MITM risk). Fix CA trust or set LIVE_TRADING_CA_BUNDLE instead for production."
            )
            _ssl_verify_disabled_logged = True
        _requests_verify_value = False
        return _requests_verify_value

    for key in ("LIVE_TRADING_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"):
        path = (os.environ.get(key) or "").strip()
        if path and os.path.isfile(path):
            _requests_verify_value = path
            return _requests_verify_value

    for path in _SYSTEM_CA_BUNDLE_CANDIDATES:
        try:
            if path and os.path.isfile(path) and os.path.getsize(path) >= 256:
                _requests_verify_value = path
                return _requests_verify_value
        except OSError:
            continue

    try:
        import certifi

        _requests_verify_value = certifi.where()
    except ImportError:
        _requests_verify_value = True
    return _requests_verify_value


# Cached proxy setting for all live-trading REST calls.
_proxies_value: Optional[Dict[str, str]] = None
_proxies_resolved = False


def _get_proxies() -> Optional[Dict[str, str]]:
    """
    Resolve the proxy for live-trading REST calls.

    ``PROXY_URL`` is documented (see ``env.example``) to cover "market data,
    exchange and broker API traffic": CCXT already honors it for market data,
    but plain ``requests`` does not understand that variable, so without this
    bridge live-trading REST calls bypass the configured proxy entirely.

    - ``PROXY_URL`` set (http(s):// or socks5(h)://, PySocks is in requirements):
      returned explicitly and takes precedence over environment proxies.
    - ``PROXY_URL`` unset: returns ``None`` — nothing is forced and standard
      ``HTTPS_PROXY`` / ``HTTP_PROXY`` / ``ALL_PROXY`` environment variables
      still apply through ``requests`` trust_env (with ``NO_PROXY`` bypasses).
    """
    global _proxies_value, _proxies_resolved
    if not _proxies_resolved:
        proxy_url = (os.environ.get("PROXY_URL") or "").strip()
        if proxy_url:
            _proxies_value = {"http": proxy_url, "https": proxy_url}
        _proxies_resolved = True
    return _proxies_value


@dataclass
class LiveOrderResult:
    exchange_id: str
    exchange_order_id: str
    filled: float
    avg_price: float
    raw: Dict[str, Any]


class LiveTradingError(Exception):
    pass


def is_file_descriptor_exhausted(exc: BaseException | str) -> bool:
    """Detect process file-descriptor exhaustion across wrapped exception chains."""
    if isinstance(exc, str):
        text = exc.lower()
        return "too many open files" in text or "errno 24" in text

    return is_fd_exhaustion(exc)


class BaseRestClient:
    def __init__(self, base_url: str, timeout_sec: float = 15.0):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout_sec = float(timeout_sec)

    def _url(self, path: str) -> str:
        p = str(path or "")
        if not p.startswith("/"):
            p = "/" + p
        return f"{self.base_url}{p}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[Any] = None,
    ) -> Tuple[int, Dict[str, Any], str]:
        url = self._url(path)
        try:
            assert_fd_available("exchange REST")
            request_headers = dict(headers or {})
            request_headers.setdefault("Connection", "close")
            with requests.request(
                    method=str(method or "GET").upper(),
                    url=url,
                    params=params or None,
                    json=json_body if json_body is not None else None,
                    data=data,
                    headers=request_headers or None,
                    timeout=self.timeout_sec,
                    verify=_get_requests_verify(),
                    proxies=_get_proxies(),
            ) as resp:
                text = resp.text or ""
                parsed: Dict[str, Any] = {}
                try:
                    parsed = resp.json() if text else {}
                except Exception:
                    parsed = {"raw_text": text[:2000]}
                return int(resp.status_code), parsed, text
        except UnicodeEncodeError as e:
            # requests/http.client requires header values to be latin-1 encodable.
            # This usually means user pasted API keys/passphrases containing non-ASCII characters,
            # or included invisible whitespace/newlines.
            raise LiveTradingError(
                "Auth failed: request headers contain non-ASCII characters and cannot be encoded. "
                "Please re-enter API key/secret/passphrase (ASCII only; no Chinese characters, smart quotes, "
                "full-width symbols, newlines, or trailing spaces). "
                f"Original error: {e}"
            )
        except requests.exceptions.SSLError as e:
            if is_file_descriptor_exhausted(e):
                mark_fd_exhausted(e)
                raise LiveTradingError(f"Resource exhausted: too many open files while calling exchange REST: {e}") from e
            logger.warning(
                "Exchange HTTPS TLS verify failed (%s). Same setting applies to all REST exchanges (Gate, HTX/hbdm, etc.). "
                "Behind PROXY_URL/SOCKS or TLS inspection: set LIVE_TRADING_CA_BUNDLE to a PEM bundle (or REQUESTS_CA_BUNDLE), "
                "ensure ca-certificates in the image, or dev-only LIVE_TRADING_SSL_VERIFY=false. %s",
                url,
                e,
            )
            raise
        except requests.exceptions.RequestException as e:
            if is_file_descriptor_exhausted(e):
                mark_fd_exhausted(e)
                raise LiveTradingError(f"Resource exhausted: too many open files while calling exchange REST: {e}") from e
            raise
        except ResourceExhaustedError as e:
            raise LiveTradingError(f"Resource exhausted: too many open files while calling exchange REST: {e}") from e

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _json_dumps(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    def get_fee_rate(self, symbol: str, market_type: str = "swap") -> Optional[Dict[str, float]]:
        """Query account fee rate from exchange. Returns {"maker": 0.0002, "taker": 0.0005} or None."""
        return None

    def get_funding_payments(
        self,
        *,
        symbol: str,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Return settled perpetual funding cash flows (positive=received, negative=paid)."""
        return []

