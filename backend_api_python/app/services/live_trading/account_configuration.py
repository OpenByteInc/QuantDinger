"""Fail-closed derivatives account configuration across supported venues."""

from __future__ import annotations

import logging
from typing import Any, Dict

from app.services.live_trading.base import LiveTradingError

logger = logging.getLogger(__name__)


def requires_derivatives_account_configuration(*, market_type: str, reduce_only: bool) -> bool:
    """Only opening derivative orders may mutate symbol account settings."""
    market = str(market_type or "").strip().lower()
    return market in {"swap", "future", "futures", "perp", "perpetual"} and not bool(reduce_only)


def _is_binance_ambiguous_configuration_error(exc: BaseException | str) -> bool:
    text = str(exc or "").lower()
    return (
        "-1000" in text
        or "unknown error occurred while processing the request" in text
        or "-1006" in text
        or "-1007" in text
        or "execution status unknown" in text
        or ("http 408" in text and "timeout" in text)
    )


def resolve_derivatives_margin_mode(
    *,
    payload: Dict[str, Any],
    strategy_config: Dict[str, Any],
    exchange_config: Dict[str, Any] | None = None,
) -> str:
    """Resolve the persisted strategy margin mode for order dispatch."""
    trading_config = strategy_config.get("trading_config") or {}
    if not isinstance(trading_config, dict):
        trading_config = {}
    venue_config = exchange_config or {}
    if not isinstance(venue_config, dict):
        venue_config = {}
    return normalize_margin_mode(
        payload.get("margin_mode")
        or payload.get("marginMode")
        or trading_config.get("margin_mode")
        or trading_config.get("marginMode")
        or venue_config.get("margin_mode")
        or venue_config.get("marginMode")
        or "cross"
    )


def _confirm_binance_configuration(
    client: Any,
    *,
    symbol: str,
    margin_mode: str,
    leverage: int,
    require_margin: bool,
    require_leverage: bool,
) -> Dict[str, Any]:
    observed = client.get_symbol_configuration(symbol=symbol) or {}
    observed_mode = normalize_margin_mode(str(observed.get("margin_mode") or ""))
    try:
        observed_leverage = int(float(observed.get("leverage") or 0))
    except Exception:
        observed_leverage = 0
    if require_margin and observed_mode != margin_mode:
        raise LiveTradingError(
            f"Binance configuration outcome could not be confirmed: "
            f"margin_mode expected={margin_mode}, observed={observed_mode}"
        )
    if require_leverage and observed_leverage != int(leverage):
        raise LiveTradingError(
            f"Binance configuration outcome could not be confirmed: "
            f"leverage expected={int(leverage)}, observed={observed_leverage}"
        )
    return observed


def normalize_margin_mode(value: str) -> str:
    raw = str(value or "cross").strip().lower()
    if raw in {"cross", "crossed"}:
        return "cross"
    if raw in {"isolated", "iso"}:
        return "isolated"
    raise LiveTradingError(f"Unsupported margin mode: {value}")


def configure_derivatives_account(
    client: Any,
    *,
    exchange_id: str,
    symbol: str,
    leverage: float,
    margin_mode: str,
) -> Dict[str, Any]:
    """Apply requested leverage and margin mode or reject the order."""
    from app.services.live_trading.binance import BinanceFuturesClient
    from app.services.live_trading.bitget import BitgetMixClient
    from app.services.live_trading.bybit import BybitClient
    from app.services.live_trading.gate import GateUsdtFuturesClient
    from app.services.live_trading.htx import HtxClient
    from app.services.live_trading.okx import OkxClient
    from app.services.live_trading.symbols import to_gate_currency_pair, to_okx_swap_inst_id

    mode = normalize_margin_mode(margin_mode)
    try:
        target_leverage = int(float(leverage or 1))
    except (TypeError, ValueError) as exc:
        raise LiveTradingError(f"Invalid leverage: {leverage}") from exc
    if target_leverage < 1:
        raise LiveTradingError(f"Invalid leverage: {leverage}")

    details: Dict[str, Any] = {
        "exchange": str(exchange_id or "").strip().lower(),
        "symbol": str(symbol or ""),
        "leverage": target_leverage,
        "margin_mode": mode,
    }

    if isinstance(client, BinanceFuturesClient):
        observed_before: Dict[str, Any] | None = None
        try:
            observed_before = client.get_symbol_configuration(symbol=symbol) or None
        except Exception:
            observed_before = None
        if observed_before:
            details["configuration_before"] = observed_before

        observed_mode = ""
        if observed_before:
            try:
                observed_mode = normalize_margin_mode(
                    str(observed_before.get("margin_mode") or "")
                )
            except LiveTradingError:
                observed_mode = ""
        margin_changed = observed_mode != mode
        observed_after_margin: Dict[str, Any] | None = None
        if margin_changed:
            try:
                client.set_margin_type(symbol=symbol, margin_mode=mode)
            except Exception as exc:
                text = str(exc).lower()
                if "-4046" not in text and "no need to change margin type" not in text:
                    if not _is_binance_ambiguous_configuration_error(exc):
                        raise LiveTradingError(f"Binance margin mode setup failed: {exc}") from exc
                    observed_after_margin = _confirm_binance_configuration(
                        client,
                        symbol=symbol,
                        margin_mode=mode,
                        leverage=target_leverage,
                        require_margin=True,
                        require_leverage=False,
                    )
                    details["readback_after_margin_error"] = observed_after_margin
        else:
            details["margin_mode_already_configured"] = True

        effective_observed = observed_after_margin or observed_before or {}
        try:
            effective_leverage = int(float(effective_observed.get("leverage") or 0))
        except (TypeError, ValueError):
            effective_leverage = 0
        if margin_changed or effective_leverage != target_leverage:
            try:
                client.set_leverage(symbol=symbol, leverage=target_leverage)
            except Exception as exc:
                if not _is_binance_ambiguous_configuration_error(exc):
                    raise
                observed = _confirm_binance_configuration(
                    client,
                    symbol=symbol,
                    margin_mode=mode,
                    leverage=target_leverage,
                    require_margin=True,
                    require_leverage=True,
                )
                details["readback_after_leverage_error"] = observed
        else:
            details["leverage_already_configured"] = True
        return details

    if isinstance(client, OkxClient):
        account_config = client.get_account_config() or {}
        account_level = str(account_config.get("acctLv") or "").strip()
        position_mode = str(account_config.get("posMode") or "").strip().lower()
        if account_level:
            details["account_mode"] = account_level
        if account_level == "1":
            raise LiveTradingError("OKX_SWAP_ACCOUNT_MODE_REQUIRED")

        inst_id = to_okx_swap_inst_id(symbol)

        # Proactively cancel algo orders that would block leverage changes.
        # This prevents the common 59669 error on strategy restart where stale
        # algo orders from a previous run remain on the exchange.
        try:
            cancelled = client.cancel_all_algo_orders(inst_id=inst_id, inst_type="SWAP")
            if cancelled > 0:
                logger.info(
                    f"Pre-cleaned {cancelled} lingering algo order(s) on {inst_id} "
                    "before setting leverage."
                )
        except Exception:
            pass

        if position_mode in ("long_short_mode", "longshort_mode"):
            long_ok = client.set_leverage(
                inst_id=inst_id,
                lever=target_leverage,
                mgn_mode=mode,
                pos_side="long",
            )
            short_ok = client.set_leverage(
                inst_id=inst_id,
                lever=target_leverage,
                mgn_mode=mode,
                pos_side="short",
            )
            ok = bool(long_ok and short_ok)
            details["position_mode"] = "hedge"
        else:
            ok = client.set_leverage(
                inst_id=inst_id,
                lever=target_leverage,
                mgn_mode=mode,
                pos_side="net",
            )
    elif isinstance(client, BitgetMixClient):
        bitget_mode = client.get_account_pos_mode(
            symbol=symbol,
            margin_coin="USDT",
            product_type="USDT-FUTURES",
        )
        kwargs = {
            "symbol": symbol,
            "leverage": target_leverage,
            "margin_mode": "crossed" if mode == "cross" else "isolated",
        }
        if bitget_mode == "hedge_mode":
            long_ok = client.set_leverage(**kwargs, hold_side="long")
            short_ok = client.set_leverage(**kwargs, hold_side="short")
            ok = bool(long_ok and short_ok)
            details["position_mode"] = "hedge"
        else:
            ok = client.set_leverage(**kwargs)
    elif isinstance(client, BybitClient):
        ok = client.set_margin_mode(mode) and client.set_leverage(
            symbol=symbol,
            leverage=target_leverage,
        )
    elif isinstance(client, GateUsdtFuturesClient):
        position_mode = str(client.get_position_mode() or "").strip().lower()
        if position_mode == "dual_plus":
            raise LiveTradingError(
                "Gate dual_plus split-position mode is not supported; "
                "switch the futures account to single or dual mode"
            )
        ok = client.set_leverage(
            contract=to_gate_currency_pair(symbol),
            leverage=target_leverage,
            margin_mode=mode,
        )
    elif isinstance(client, HtxClient):
        client.margin_mode = mode
        ok = client.set_leverage(symbol=symbol, leverage=target_leverage, margin_mode=mode)
    else:
        raise LiveTradingError(
            f"Derivatives account configuration is not implemented for {type(client).__name__}"
        )

    if not ok:
        raise LiveTradingError(
            f"{details['exchange'] or type(client).__name__} rejected leverage/margin configuration"
        )
    return details
