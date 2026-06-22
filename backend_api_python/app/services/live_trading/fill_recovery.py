"""
Recover live fill quantity when wait_for_fill returns zero but the exchange filled.

Used by PendingOrderWorker (indicator / script strategies) and shares the same
exchange-documented unit conversion as grid ``fill_units.parse_grid_order_fill``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.services.grid.exchange_orders import query_grid_order_fill
from app.services.live_trading.position_query import query_exchange_position_size
from app.utils.logger import get_logger

logger = get_logger(__name__)

_ENTRY_SIGNALS = frozenset(
    {
        "open_long",
        "open_short",
        "add_long",
        "add_short",
    }
)


def try_recover_zero_fill(
    client: Any,
    *,
    symbol: str,
    market_type: str,
    exchange_config: Optional[Dict[str, Any]],
    exchange_order_id: str,
    client_order_id: str,
    requested_qty: float,
    signal_type: str,
    pos_side: str,
    pre_position_qty: float,
    ref_price: float,
) -> Tuple[float, float, str]:
    """
    Best-effort fill recovery when polling reported filled=0.

    Returns (filled_base_qty, avg_price, source) where source is
    ``order_requery``, ``position_delta``, or empty when nothing recovered.
    """
    ex_oid = str(exchange_order_id or "").strip()
    coid = str(client_order_id or "").strip()
    req = float(requested_qty or 0.0)
    pre = max(0.0, float(pre_position_qty or 0.0))
    px_hint = float(ref_price or 0.0)
    ex_cfg = exchange_config if isinstance(exchange_config, dict) else {}

    if ex_oid or coid:
        try:
            filled, avg, status = query_grid_order_fill(
                client,
                symbol=str(symbol),
                market_type=str(market_type or "swap"),
                exchange_order_id=ex_oid,
                client_order_id=coid,
                exchange_config=ex_cfg,
            )
            if filled > 0:
                use_avg = float(avg or 0.0) or px_hint
                logger.info(
                    "fill_recovery order_requery: symbol=%s oid=%s filled=%s avg=%s status=%s",
                    symbol,
                    ex_oid or coid,
                    filled,
                    use_avg,
                    status,
                )
                return float(filled), use_avg, "order_requery"
        except Exception as e:
            logger.debug("fill_recovery order_requery failed symbol=%s: %s", symbol, e)

    sig = str(signal_type or "").strip().lower()
    if sig not in _ENTRY_SIGNALS or req <= 0:
        return 0.0, 0.0, ""

    side = str(pos_side or "").strip().lower()
    if side not in ("long", "short"):
        return 0.0, 0.0, ""

    try:
        post = float(
            query_exchange_position_size(
                client=client,
                symbol=str(symbol),
                pos_side=side,
                market_type=str(market_type or "swap"),
                exchange_config=ex_cfg,
            )
            or 0.0
        )
    except Exception as e:
        logger.debug("fill_recovery position query failed symbol=%s: %s", symbol, e)
        return 0.0, 0.0, ""

    delta = max(0.0, post - pre)
    if delta < req * 0.85:
        return 0.0, 0.0, ""

    record_qty = min(delta, req * 1.15) if req > 0 else delta
    if record_qty <= 0:
        return 0.0, 0.0, ""

    logger.info(
        "fill_recovery position_delta: symbol=%s signal=%s pre=%s post=%s delta=%s record=%s",
        symbol,
        sig,
        pre,
        post,
        delta,
        record_qty,
    )
    return float(record_qty), px_hint, "position_delta"
