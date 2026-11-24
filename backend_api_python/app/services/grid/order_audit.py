"""Audit persisted grid orders without depending on an execution worker's memory."""

from datetime import datetime, timezone

from app.services.exchange_execution import coalesce_exchange_config_from_payload, resolve_exchange_config
from app.services.grid.exchange_orders import query_grid_order_fill
from app.services.grid.resting_orders_repo import GridRestingOrderRepository
from app.services.live_trading.factory import create_client
from app.services.live_trading.open_orders import fetch_exchange_open_orders
from app.services.live_trading.records import normalize_strategy_symbol
from app.services.pending_orders.live_order_support import bind_instrument_product_contract
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _reconcile_missing_order(client, row, *, symbol: str, market_type: str, exchange_config: dict, ledger) -> dict:
    """Look up an order that the open-orders snapshot no longer lists.

    Exchanges drop cancelled, expired and filled orders from their open-orders
    endpoint, so absence alone says nothing about the terminal state. A
    per-order query (for example Binance GET /fapi/v1/order) does. Only a
    confirmed cancel without any fill is applied to the local ledger here;
    orders with fills are left to the fill poller, which posts them to the
    strategy trade ledger.
    """
    filled, avg, status = query_grid_order_fill(
        client,
        symbol=symbol,
        market_type=market_type,
        exchange_order_id=str(row.exchange_order_id or ""),
        client_order_id=str(row.client_order_id or ""),
        exchange_config=exchange_config,
    )
    if status not in {"open", "partial", "filled", "cancelled"}:
        return {"status": "not_open", "price": None, "quantity": None, "filled": None}
    entry = {"status": status, "price": avg or None, "quantity": None, "filled": filled}
    unfilled = (
        float(filled or 0.0) <= 0
        and float(row.filled_quantity or 0.0) <= 0
        and float(row.processed_fill_qty or 0.0) <= 0
    )
    if status == "cancelled" and unfilled and str(row.status or "") == "open":
        if ledger.mark_cancelled_if_unfilled(int(row.id), str(row.exchange_order_id)):
            row.status = "cancelled"
            entry["local_status"] = "cancelled"
    return entry


def audit_grid_orders(strategy: dict, rows: list, *, user_id: int) -> dict:
    audit = {"completed": False, "active": 0, "terminal": 0, "unknown": len(rows), "error": "",
             "checked_at": None, "orders": {}}
    tc = strategy.get("trading_config") or {}
    mt = str(tc.get("market_type") or strategy.get("market_type") or "swap").lower()
    ledger = None
    try:
        config = resolve_exchange_config(coalesce_exchange_config_from_payload(strategy), user_id=user_id)
        ex = str(config.get("exchange_id") or "").lower()
        symbols = sorted({str(row.symbol) for row in rows})
        for symbol in symbols:
            bound = bind_instrument_product_contract(config, tc, symbol=symbol, exchange_id=ex, market_type=mt)
            client = create_client(bound, market_type=mt)
            remote = fetch_exchange_open_orders(client, exchange_id=ex, market_type=mt, symbol=symbol)
            by_id = {str(item["exchange_order_id"]): item for item in remote
                     if normalize_strategy_symbol(item["symbol"]) == normalize_strategy_symbol(symbol)}
            for row in rows:
                if row.symbol != symbol:
                    continue
                item = by_id.get(str(row.exchange_order_id or ""))
                if item is None and row.exchange_order_id:
                    if ledger is None:
                        ledger = GridRestingOrderRepository()
                    entry = _reconcile_missing_order(
                        client, row, symbol=symbol, market_type=mt, exchange_config=bound, ledger=ledger,
                    )
                    state = entry["status"]
                    audit["orders"][row.id] = entry
                else:
                    if item is None:
                        state = "unverified"
                    else:
                        raw_status = str(item.get("status") or "").lower()
                        if raw_status in {"new", "open", "live", "created", "untriggered"}:
                            state = "open"
                        elif raw_status in {"partial", "partiallyfilled", "partially_filled"}:
                            state = "partial"
                        else:
                            state = "unverified"
                    audit["orders"][row.id] = {"status": state, "price": item.get("price") if item else None,
                                               "quantity": item.get("amount") if item else None,
                                               "filled": item.get("filled") if item else None}
                if state in {"open", "partial"}:
                    audit["active"] += 1
                elif state in {"filled", "cancelled"}:
                    audit["terminal"] += 1
        audit["unknown"] = len(rows) - audit["active"] - audit["terminal"]
        audit["completed"] = True
        audit["checked_at"] = datetime.now(timezone.utc).isoformat()
        if audit["unknown"]:
            audit["error"] = "grid_exchange_orders_unverified"
    except Exception:
        logger.warning("Grid order audit failed sid=%s", strategy.get("id"), exc_info=True)
        audit["error"] = "grid_exchange_snapshot_failed"
    return audit
