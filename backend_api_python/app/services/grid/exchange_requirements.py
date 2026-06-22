"""Exchange account requirements for live grid bots."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.services.grid.config import GridBotConfig
from app.utils.logger import get_logger

logger = get_logger(__name__)


def neutral_grid_requires_hedge_mode(cfg: GridBotConfig) -> bool:
    """Neutral swap/futures grids must hold long and short legs concurrently."""
    mt = str(cfg.market_type or "swap").strip().lower()
    if mt in ("spot", "cash"):
        return False
    return str(cfg.grid_direction or "").strip().lower() == "neutral"


def detect_hedge_position_mode(
    client: Any,
    *,
    symbol: str,
    market_type: str,
    exchange_config: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[bool], str]:
    """
    Return (is_hedge, exchange_label).

    ``is_hedge`` is True/False when known, None when the exchange could not be queried.
    """
    if client is None:
        return None, "unknown"

    mt = str(market_type or "swap").strip().lower()
    if mt in ("spot", "cash"):
        return True, "spot"

    cfg = exchange_config if isinstance(exchange_config, dict) else {}
    sym = str(symbol or "").strip()
    exchange_id = str(cfg.get("exchange_id") or cfg.get("exchangeId") or "").strip().lower()

    if hasattr(client, "get_account_pos_mode") and callable(getattr(client, "get_account_pos_mode")):
        product_type = str(cfg.get("product_type") or cfg.get("productType") or "USDT-FUTURES")
        margin_coin = str(cfg.get("margin_coin") or cfg.get("marginCoin") or "USDT")
        mode = str(
            client.get_account_pos_mode(
                symbol=sym,
                margin_coin=margin_coin,
                product_type=product_type,
            )
            or ""
        ).strip().lower()
        if mode == "hedge_mode":
            return True, "bitget_hedge_mode"
        if mode == "one_way_mode":
            return False, "bitget_one_way_mode"
        return None, f"bitget_{mode or 'unknown'}"

    if hasattr(client, "get_dual_side_position") and callable(getattr(client, "get_dual_side_position")):
        dual = client.get_dual_side_position()
        if dual is True:
            return True, "binance_hedge_mode"
        if dual is False:
            return False, "binance_one_way_mode"
        return None, "binance_unknown"

    if hasattr(client, "get_account_config") and callable(getattr(client, "get_account_config")):
        acct = client.get_account_config() or {}
        pm = str(acct.get("posMode") or "").strip().lower()
        if pm in ("long_short_mode", "longshort_mode", "long_short", "longshort"):
            return True, "okx_long_short_mode"
        if pm in ("net_mode", "net"):
            return False, "okx_net_mode"
        return None, f"okx_{pm or 'unknown'}"

    if hasattr(client, "is_hedge_position_mode") and callable(getattr(client, "is_hedge_position_mode")):
        hedge = client.is_hedge_position_mode(symbol=sym)
        return bool(hedge), "bybit_hedge_mode" if hedge else "bybit_one_way_mode"

    if exchange_id in ("bitget", "bitget_mix"):
        return None, "bitget_unknown"
    if exchange_id in ("binance", "binanceusdm"):
        return None, "binance_unknown"
    if exchange_id == "okx":
        return None, "okx_unknown"
    if exchange_id == "bybit":
        return None, "bybit_unknown"

    try:
        from app.services.live_trading.bitget import BitgetMixClient
        from app.services.live_trading.binance import BinanceFuturesClient
        from app.services.live_trading.okx import OkxClient
        from app.services.live_trading.bybit import BybitClient
    except Exception:
        return None, "unknown"

    if isinstance(client, BitgetMixClient):
        product_type = str(cfg.get("product_type") or cfg.get("productType") or "USDT-FUTURES")
        margin_coin = str(cfg.get("margin_coin") or cfg.get("marginCoin") or "USDT")
        mode = str(
            client.get_account_pos_mode(
                symbol=sym,
                margin_coin=margin_coin,
                product_type=product_type,
            )
            or ""
        ).strip().lower()
        if mode == "hedge_mode":
            return True, "bitget_hedge_mode"
        if mode == "one_way_mode":
            return False, "bitget_one_way_mode"
        return None, f"bitget_{mode or 'unknown'}"

    if isinstance(client, BinanceFuturesClient):
        dual = client.get_dual_side_position()
        if dual is True:
            return True, "binance_hedge_mode"
        if dual is False:
            return False, "binance_one_way_mode"
        return None, "binance_unknown"

    if isinstance(client, OkxClient):
        acct = client.get_account_config() or {}
        pm = str(acct.get("posMode") or "").strip().lower()
        if pm in ("long_short_mode", "longshort_mode", "long_short", "longshort"):
            return True, "okx_long_short_mode"
        if pm in ("net_mode", "net"):
            return False, "okx_net_mode"
        return None, f"okx_{pm or 'unknown'}"

    if isinstance(client, BybitClient):
        hedge = client.is_hedge_position_mode(symbol=sym)
        return bool(hedge), "bybit_hedge_mode" if hedge else "bybit_one_way_mode"

    return None, type(client).__name__


def validate_neutral_grid_exchange_support(
    cfg: GridBotConfig,
    client: Any,
    *,
    symbol: str,
    exchange_config: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Block neutral grid startup when the account cannot hold dual legs."""
    if not neutral_grid_requires_hedge_mode(cfg):
        return True, ""

    is_hedge, label = detect_hedge_position_mode(
        client,
        symbol=symbol,
        market_type=cfg.market_type,
        exchange_config=exchange_config,
    )
    if is_hedge is False:
        return False, (
            "Neutral grid requires hedge (dual-side) position mode on the exchange. "
            f"Detected {label}. Switch the contract account to hedge/dual-side mode "
            "before starting a neutral grid; one-way mode nets long+short and desyncs "
            "local strategy positions from the exchange."
        )
    if is_hedge is None:
        logger.warning(
            "neutral grid hedge mode unknown for %s (%s); proceeding best-effort",
            symbol,
            label,
        )
    return True, ""


def fetch_exchange_dual_leg_snapshot(
    client: Any,
    *,
    symbol: str,
    market_type: str,
    exchange_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Best-effort long/short sizes on the connected exchange for UI comparison."""
    from app.services.live_trading.position_query import query_exchange_position_size

    mt = str(market_type or "swap").strip().lower()
    legs: Dict[str, float] = {}
    for side in ("long", "short"):
        try:
            legs[side] = float(
                query_exchange_position_size(
                    client=client,
                    symbol=str(symbol or ""),
                    pos_side=side,
                    market_type=mt,
                    exchange_config=exchange_config if isinstance(exchange_config, dict) else {},
                )
                or 0.0
            )
        except Exception as e:
            logger.debug("exchange leg snapshot %s: %s", side, e)
            legs[side] = 0.0

    is_hedge, label = detect_hedge_position_mode(
        client,
        symbol=symbol,
        market_type=mt,
        exchange_config=exchange_config,
    )
    return {
        "long_size": legs.get("long") or 0.0,
        "short_size": legs.get("short") or 0.0,
        "hedge_mode": is_hedge,
        "position_mode_label": label,
    }
