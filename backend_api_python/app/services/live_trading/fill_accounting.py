"""Quantity and cumulative-value rules shared by REST and private streams."""

from __future__ import annotations

import math
import json
from decimal import Decimal
from typing import Any

from app.services.live_trading.base import LiveTradingError


_CUMULATIVE_QUANTITY_ABS_TOLERANCE = Decimal("1e-12")
_CUMULATIVE_QUANTITY_REL_TOLERANCE = Decimal("1e-12")


def positive_number(value: Any) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError):
        number = 0.0
    if not math.isfinite(number) or number <= 0:
        raise LiveTradingError("strategyRuntime.fillContractMetadataUnavailable")
    return number


def contract_multiplier(client: Any, exchange_id: str, symbol: str) -> float:
    exchange = str(exchange_id).lower()
    if exchange == "okx":
        from app.services.live_trading.symbols import to_okx_swap_inst_id

        meta = client.get_instrument(inst_type="SWAP", inst_id=to_okx_swap_inst_id(symbol)) or {}
        base = symbol.split("/")[0].upper()
        currency = str(meta.get("ctValCcy") or base).upper()
        if currency != base or str(meta.get("ctType") or "linear").lower() != "linear":
            raise LiveTradingError("strategyRuntime.unsupportedFillContract")
        return positive_number(meta.get("ctVal")) * positive_number(meta.get("ctMult") or 1)
    if exchange == "gate":
        from app.services.live_trading.gate import to_gate_currency_pair

        meta = client.get_contract(contract=to_gate_currency_pair(symbol)) or {}
        return positive_number(meta.get("quanto_multiplier") or meta.get("quantoMultiplier"))
    if exchange == "htx":
        meta = client.get_contract_info(symbol=symbol) or {}
        return positive_number(meta.get("contract_size") or meta.get("contractSize"))
    return 1.0


def base_quantity(quantity: float, multiplier: float) -> float:
    value = Decimal(str(quantity)) * Decimal(str(multiplier))
    if not value.is_finite() or value < 0:
        raise LiveTradingError("strategyRuntime.invalidFillQuantity")
    return float(value)


def cumulative_delta(
    previous_qty: float, previous_avg: float, total_qty: float, total_avg: float
) -> tuple[float, float]:
    """Price only the unposted quantity, using the difference in notionals."""
    prev, total = Decimal(str(previous_qty)), Decimal(str(total_qty))
    if not prev.is_finite() or not total.is_finite() or min(prev, total) < 0:
        raise LiveTradingError("strategyRuntime.invalidFillQuantity")
    quantity_tolerance = max(
        _CUMULATIVE_QUANTITY_ABS_TOLERANCE,
        max(abs(prev), abs(total)) * _CUMULATIVE_QUANTITY_REL_TOLERANCE,
    )
    if total <= prev + quantity_tolerance:
        return 0.0, 0.0
    average = positive_number(total_avg)
    if prev > 0:
        positive_number(previous_avg)
    delta = total - prev
    notional = total * Decimal(str(average)) - prev * Decimal(str(previous_avg))
    price = float(notional / delta)
    if not math.isfinite(price) or price <= 0:
        raise LiveTradingError("strategyRuntime.inconsistentFillSnapshot")
    return float(delta), price


def posted_totals(owner_column: str, owner_id: int, exchange_order_id: str = "") -> dict:
    from app.utils.db import get_db_connection

    if owner_column not in {"grid_order_id", "pending_order_id"}:
        raise ValueError("Invalid fill owner")
    with get_db_connection() as db:
        cur = db.cursor()
        clause = " AND exchange_order_id = %s" if exchange_order_id else ""
        params = (int(owner_id), exchange_order_id) if exchange_order_id else (int(owner_id),)
        cur.execute(
            f"SELECT amount, price, commission, commission_ccy, commission_quote, commission_breakdown FROM qd_strategy_trades WHERE {owner_column} = %s{clause} ORDER BY id",
            params,
        )
        rows = cur.fetchall() or []
        cur.close()
    quantity = sum(float(row.get("amount") or 0) for row in rows)
    value = sum(float(row.get("amount") or 0) * float(row.get("price") or 0) for row in rows)
    fees = {}
    for row in rows:
        breakdown = row.get("commission_breakdown") or {}
        if isinstance(breakdown, str):
            breakdown = json.loads(breakdown)
        if breakdown:
            for currency, amount in breakdown.items():
                fees[currency] = fees.get(currency, 0.0) + float(amount)
            continue
        currency = str(row.get("commission_ccy") or "")
        if currency:
            fees[currency] = fees.get(currency, 0.0) + float(row.get("commission") or 0)
    quote = sum(float(row.get("commission_quote") or 0) for row in rows)
    return dict(quantity=quantity, average=value / quantity if quantity > 0 else 0.0, fees=fees, quote=quote)


def lock_strategy_fills(strategy_id: int) -> None:
    from app.utils.db import get_db_connection

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(248, %s)", (int(strategy_id),))
        cur.close()


def adjust_order_fee(owner_column, owner_id, fees, quote, status, source="websocket", *, update_inventory=True):
    from app.utils.db import get_db_connection

    if owner_column not in {"grid_order_id", "pending_order_id", "id"}:
        raise ValueError("Invalid fill owner")
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"SELECT * FROM qd_strategy_trades WHERE {owner_column} = %s ORDER BY id DESC LIMIT 1 FOR UPDATE",
            (owner_id,),
        )
        row = cur.fetchone()
        if not row:
            raise LiveTradingError("strategyRuntime.fillSnapshotNotReady")
        if update_inventory and str(row.get("market_type") or "").lower() in {"spot", "crypto"}:
            from app.services.live_trading.fee_quote import symbol_currencies

            base, _ = symbol_currencies(row.get("symbol") or "")
            base_fee = float(fees.get(base) or 0)
            if base_fee:
                cur.execute(
                    """UPDATE qd_strategy_positions SET size = GREATEST(0, size - %s), updated_at = NOW()
                    WHERE strategy_id = %s AND symbol = %s AND side = 'long'
                    AND COALESCE(credential_id, 0) = %s AND market_type = %s""",
                    (
                        base_fee,
                        row["strategy_id"],
                        row["symbol"],
                        int(row.get("credential_id") or 0),
                        row["market_type"],
                    ),
                )
                if owner_column == "grid_order_id":
                    cur.execute(
                        """UPDATE qd_grid_cells c SET leg_size = GREATEST(0, c.leg_size - %s), last_event_ts = NOW()
                        FROM qd_grid_resting_orders o WHERE o.id = %s AND c.strategy_id = o.strategy_id
                        AND c.symbol = o.symbol AND c.cell_index = o.cell_index AND c.state = 'long_held'""",
                        (base_fee, owner_id),
                    )
        previous = row.get("commission_breakdown") or {}
        if isinstance(previous, str):
            previous = json.loads(previous)
        if not previous and row.get("commission_ccy") not in {None, "", "MIXED"}:
            previous = {row["commission_ccy"]: float(row.get("commission") or 0)}
        for currency, amount in fees.items():
            previous[currency] = float(previous.get(currency) or 0) + amount
        currency, amount = next(iter(previous.items())) if len(previous) == 1 else ("MIXED", 0)
        cur.execute(
            """UPDATE qd_strategy_trades SET commission = %s, commission_ccy = %s,
            commission_breakdown = %s::jsonb, commission_quote = %s, fee_status = %s, fee_source = %s
            WHERE id = %s""",
            (
                amount,
                currency,
                json.dumps(previous),
                float(row.get("commission_quote") or 0) + quote if quote is not None else None,
                status,
                source,
                row["id"],
            ),
        )
        if status in {"actual", "actual_zero"}:
            cur.execute(
                f"UPDATE qd_strategy_trades SET fee_status = %s, fee_source = %s WHERE {owner_column} = %s AND COALESCE(fee_status, 'pending') = 'pending'",
                (status, source, owner_id),
            )
        if owner_column == "pending_order_id":
            _sync_runtime_order_fee(cur, owner_id, row, quote, status)
        cur.close()


def _sync_runtime_order_fee(cur, pending_order_id, trade, quote_delta, status):
    """Keep strategy callback fee totals aligned after a fee-only update."""
    intent_id = int(trade.get("order_intent_id") or 0)
    exchange_order_id = str(trade.get("exchange_order_id") or "")
    if not intent_id or not exchange_order_id:
        return
    cur.execute(
        """SELECT id, quantity, fee, commission_quote FROM strategy_order_fills
        WHERE strategy_id = %s AND order_intent_id = %s AND credential_id = %s
          AND exchange_id = (SELECT exchange_id FROM pending_orders WHERE id = %s)
          AND exchange_order_id = %s
        ORDER BY id FOR UPDATE""",
        (trade["strategy_id"], intent_id, int(trade.get("credential_id") or 0),
         pending_order_id, exchange_order_id),
    )
    rows = cur.fetchall() or []
    if not rows:
        return
    totals = posted_totals("pending_order_id", pending_order_id)
    if not math.isclose(sum(float(row["quantity"]) for row in rows), totals["quantity"], rel_tol=1e-12, abs_tol=1e-12):
        return
    currency, amount = next(iter(totals["fees"].items())) if len(totals["fees"]) == 1 else ("MIXED", 0)
    # Replace the remaining total rather than re-applying the observed fee delta.
    fee = amount - sum(float(row.get("fee") or 0) for row in rows[:-1])
    quote = (totals["quote"] - sum(float(row.get("commission_quote") or 0) for row in rows[:-1])
             if quote_delta is not None else None)
    cur.execute(
        """UPDATE strategy_order_fills SET fee = %s, fee_ccy = %s,
        commission_quote = %s, fee_status = %s WHERE id = %s""",
        (fee, currency, quote, status, rows[-1]["id"]),
    )
    for row in rows[:-1]:
        cur.execute("UPDATE strategy_order_fills SET fee_status = %s WHERE id = %s", (status, row["id"]))
