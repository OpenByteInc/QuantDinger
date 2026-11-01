"""Account-mode routing for Bitget Classic V2 and UTA V3 clients."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Tuple

from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_classic import BitgetClassicMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bitget_spot_classic import BitgetClassicSpotClient
from app.utils.security_fingerprint import opaque_fingerprint


_MODE_CACHE: Dict[str, Tuple[float, str]] = {}
_MODE_CACHE_LOCK = threading.Lock()
_MODE_CACHE_TTL_SEC = 300.0


def _normalize_account_mode(value: Any) -> str:
    mode = str(value or "auto").strip().lower().replace("-", "_")
    if mode in {"classic", "v2", "legacy"}:
        return "classic"
    if mode in {"uta", "unified", "v3"}:
        return "uta"
    return "auto"


def _cache_key(*, api_key: str, base_url: str, market_type: str, simulated: bool) -> str:
    digest = opaque_fingerprint(api_key, namespace="bitget-account-mode")
    return f"{str(base_url or '').rstrip('/')}:{market_type}:{int(bool(simulated))}:{digest}"


def _cached_mode(key: str) -> str:
    now = time.monotonic()
    with _MODE_CACHE_LOCK:
        cached = _MODE_CACHE.get(key)
        if not cached:
            return ""
        created_at, mode = cached
        if now - created_at > _MODE_CACHE_TTL_SEC:
            _MODE_CACHE.pop(key, None)
            return ""
        return mode


def _remember_mode(key: str, mode: str) -> None:
    with _MODE_CACHE_LOCK:
        _MODE_CACHE[key] = (time.monotonic(), mode)


class _BitgetAutoRouter:
    _ROUTED_METHODS: frozenset[str] = frozenset()
    _market_type = ""

    def _initialize_router(
        self,
        *,
        uta_client: Any,
        classic_client: Any,
        account_mode: str,
        api_key: str,
        base_url: str,
        simulated_trading: bool,
    ) -> None:
        object.__setattr__(self, "_uta_client", uta_client)
        object.__setattr__(self, "_classic_client", classic_client)
        object.__setattr__(self, "_configured_account_mode", _normalize_account_mode(account_mode))
        object.__setattr__(self, "_resolved_account_mode", "")
        object.__setattr__(self, "_resolved_account_mode_at", 0.0)
        object.__setattr__(
            self,
            "_account_mode_cache_key",
            _cache_key(
                api_key=api_key,
                base_url=base_url,
                market_type=self._market_type,
                simulated=simulated_trading,
            ),
        )

    @property
    def bitget_account_mode(self) -> str:
        return str(object.__getattribute__(self, "_resolved_account_mode") or "auto")

    def _sync_request_session(self, client: Any) -> None:
        session = getattr(self, "_request_session", None)
        if session is not None:
            client._request_session = session

    def _probe_classic(self, client: Any) -> None:
        self._sync_request_session(client)
        if self._market_type == "spot":
            client.get_assets()
        else:
            client.get_accounts(product_type="USDT-FUTURES")

    def _resolve_bitget_client(self) -> Any:
        resolved = str(object.__getattribute__(self, "_resolved_account_mode") or "")
        uta_client = object.__getattribute__(self, "_uta_client")
        classic_client = object.__getattribute__(self, "_classic_client")
        configured = str(object.__getattribute__(self, "_configured_account_mode") or "auto")
        if resolved:
            resolved_at = float(object.__getattribute__(self, "_resolved_account_mode_at") or 0.0)
            if configured != "auto" or time.monotonic() - resolved_at <= _MODE_CACHE_TTL_SEC:
                selected = classic_client if resolved == "classic" else uta_client
                self._sync_request_session(selected)
                return selected
            resolved = ""
            object.__setattr__(self, "_resolved_account_mode", "")

        cache_key = str(object.__getattribute__(self, "_account_mode_cache_key"))
        if configured in {"classic", "uta"}:
            resolved = configured
        else:
            resolved = _cached_mode(cache_key)

        if not resolved:
            self._sync_request_session(uta_client)
            uta_error: Optional[Exception] = None
            try:
                response = uta_client._signed_request("GET", "/api/v3/account/settings")
                data = response.get("data") if isinstance(response, dict) else None
                remote_mode = str(
                    data.get("accountMode") if isinstance(data, dict) else ""
                ).strip().lower()
                if remote_mode == "unified":
                    resolved = "uta"
                elif remote_mode == "hybrid":
                    resolved = "classic"
                elif remote_mode in {"switching", "upgrading"}:
                    raise LiveTradingError("BITGET_ACCOUNT_MODE_TRANSITIONING")
            except LiveTradingError as exc:
                if "BITGET_ACCOUNT_MODE_TRANSITIONING" in str(exc):
                    raise
                uta_error = exc
            except Exception as exc:
                uta_error = exc

            if not resolved:
                try:
                    self._probe_classic(classic_client)
                    resolved = "classic"
                except Exception:
                    if uta_error is not None:
                        raise uta_error
                    resolved = "uta"

            _remember_mode(cache_key, resolved)

        object.__setattr__(self, "_resolved_account_mode", resolved)
        object.__setattr__(self, "_resolved_account_mode_at", time.monotonic())
        selected = classic_client if resolved == "classic" else uta_client
        self._sync_request_session(selected)
        return selected

    def __getattribute__(self, name: str) -> Any:
        routed = object.__getattribute__(self, "_ROUTED_METHODS")
        if name in routed:
            client = object.__getattribute__(self, "_resolve_bitget_client")()
            return getattr(client, name)
        return super().__getattribute__(name)


class BitgetCompatibleMixClient(_BitgetAutoRouter, BitgetMixClient):
    """Route one Bitget futures credential to Classic V2 or UTA V3."""

    _market_type = "swap"
    _ROUTED_METHODS = frozenset(
        {
            "get_funding_payments",
            "get_contract",
            "get_account_pos_mode",
            "prepare_ws_order_entry",
            "normalize_base_order_size",
            "get_accounts",
            "get_positions",
            "get_fee_rate",
            "set_leverage",
            "place_market_order",
            "place_limit_order",
            "cancel_order",
            "get_order_detail",
            "get_order_fills",
            "get_order",
            "wait_for_fill",
        }
    )

    def __init__(self, *, account_mode: str = "auto", **kwargs: Any):
        super().__init__(**kwargs)
        self._initialize_router(
            uta_client=BitgetMixClient(**kwargs),
            classic_client=BitgetClassicMixClient(**kwargs),
            account_mode=account_mode,
            api_key=str(kwargs.get("api_key") or ""),
            base_url=str(kwargs.get("base_url") or "https://api.bitget.com"),
            simulated_trading=bool(kwargs.get("simulated_trading")),
        )


class BitgetCompatibleSpotClient(_BitgetAutoRouter, BitgetSpotClient):
    """Route one Bitget spot credential to Classic V2 or UTA V3."""

    _market_type = "spot"
    _ROUTED_METHODS = frozenset(
        {
            "get_fee_rate",
            "place_limit_order",
            "place_market_order",
            "cancel_order",
            "get_order",
            "get_fills",
            "wait_for_fill",
            "get_assets",
        }
    )

    def __init__(self, *, account_mode: str = "auto", **kwargs: Any):
        super().__init__(**kwargs)
        self._initialize_router(
            uta_client=BitgetSpotClient(**kwargs),
            classic_client=BitgetClassicSpotClient(**kwargs),
            account_mode=account_mode,
            api_key=str(kwargs.get("api_key") or ""),
            base_url=str(kwargs.get("base_url") or "https://api.bitget.com"),
            simulated_trading=bool(kwargs.get("simulated_trading")),
        )
