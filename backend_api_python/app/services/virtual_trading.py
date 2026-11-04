"""Isolated virtual account ledger for signal-only strategies."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

from app.utils.db import get_db_connection
from app.services.market_schedule import is_equity_market_open
from app.services.virtual_execution_costs import resolve_virtual_execution_cost_policy
from app.utils.trade_close_reason import INDICATOR_SIGNAL


_ENTRY_ACTIONS = {"open_long", "add_long", "open_short", "add_short"}
_EXIT_ACTIONS = {"reduce_long", "close_long", "reduce_short", "close_short"}


def canonical_symbol(value: Any) -> str:
    text = str(value or "").strip()
    if ":" in text:
        text = text.split(":", 1)[-1]
    return text.split("@", 1)[0].upper()


@dataclass(frozen=True)
class VirtualFillTransition:
    fill_quantity: float
    next_size: float
    next_entry_price: float
    gross_realized_pnl: float
    status: str


def calculate_virtual_fill(
    *,
    action: str,
    requested_quantity: float,
    fill_price: float,
    current_size: float = 0.0,
    current_entry_price: float = 0.0,
) -> VirtualFillTransition:
    """Calculate one deterministic virtual fill without external state."""
    normalized = str(action or "").strip().lower()
    requested = max(0.0, float(requested_quantity or 0.0))
    price = max(0.0, float(fill_price or 0.0))
    size = max(0.0, float(current_size or 0.0))
    entry = max(0.0, float(current_entry_price or 0.0))
    if normalized in _ENTRY_ACTIONS:
        if requested <= 0 or price <= 0:
            return VirtualFillTransition(0.0, size, entry, 0.0, "rejected")
        next_size = size + requested
        next_entry = ((size * entry) + (requested * price)) / next_size
        return VirtualFillTransition(requested, next_size, next_entry, 0.0, "filled")
    if normalized in _EXIT_ACTIONS:
        if size <= 0 or price <= 0:
            return VirtualFillTransition(0.0, size, entry, 0.0, "no_position")
        fill_quantity = size if requested <= 0 else min(size, requested)
        is_short = normalized.endswith("_short")
        gross = (entry - price) * fill_quantity if is_short else (price - entry) * fill_quantity
        return VirtualFillTransition(
            fill_quantity,
            max(0.0, size - fill_quantity),
            entry,
            gross,
            "filled",
        )
    return VirtualFillTransition(0.0, size, entry, 0.0, "rejected")


def _fill_price(action: str, reference_price: float, slippage_rate: float) -> float:
    is_buy = str(action or "").strip().lower() in {
        "open_long", "add_long", "reduce_short", "close_short",
    }
    multiplier = 1.0 + slippage_rate if is_buy else 1.0 - slippage_rate
    return max(0.0, float(reference_price or 0.0) * multiplier)


def calculate_virtual_limit_fill_price(
    *,
    action: str,
    limit_price: float,
    market_price: float,
    slippage_rate: float,
) -> float | None:
    """Return a limit-safe virtual fill price once the market reaches the order."""
    limit_value = max(0.0, float(limit_price or 0.0))
    market_value = max(0.0, float(market_price or 0.0))
    if limit_value <= 0 or market_value <= 0:
        return None
    is_buy = str(action or "").strip().lower() in {
        "open_long", "add_long", "reduce_short", "close_short",
    }
    if is_buy:
        if market_value > limit_value:
            return None
        return min(limit_value, _fill_price(action, market_value, slippage_rate))
    if market_value < limit_value:
        return None
    return max(limit_value, _fill_price(action, market_value, slippage_rate))


def execute_virtual_signal_order(order_row: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """Atomically fill a signal order in the isolated virtual ledger."""
    mode = str(order_row.get("execution_mode") or payload.get("execution_mode") or "").strip().lower()
    if mode != "signal" or str(payload.get("execution_mode") or "signal").strip().lower() != "signal":
        raise ValueError("virtualTrading.signalModeRequired")

    order_id = int(order_row.get("id") or 0)
    strategy_id = int(payload.get("strategy_id") or order_row.get("strategy_id") or 0)
    user_id = int(order_row.get("user_id") or payload.get("user_id") or 0)
    if order_id <= 0 or strategy_id <= 0 or user_id <= 0:
        raise ValueError("virtualTrading.invalidIdentity")

    action = str(payload.get("signal_type") or order_row.get("signal_type") or "").strip().lower()
    if action not in _ENTRY_ACTIONS | _EXIT_ACTIONS:
        raise ValueError("virtualTrading.unsupportedAction")
    side = "short" if action.endswith("_short") else "long"
    symbol = str(payload.get("symbol") or order_row.get("symbol") or "").strip()
    symbol_key = canonical_symbol(symbol)
    requested_quantity = float(payload.get("amount") or order_row.get("amount") or 0.0)
    reference_price = float(payload.get("ref_price") or payload.get("price") or order_row.get("price") or 0.0)
    order_type = str(payload.get("order_type") or order_row.get("order_type") or "market").strip().lower()
    limit_price = float(payload.get("limit_price") or (payload.get("price") if order_type == "limit" else 0.0) or 0.0)
    forced_fill_price = float(payload.get("_virtual_fill_price") or 0.0)
    sizing = payload.get("sizing") if isinstance(payload.get("sizing"), dict) else {}
    strategy_run_id = int(payload.get("strategy_run_id") or order_row.get("strategy_run_id") or 0)
    order_intent_id = int(payload.get("order_intent_id") or order_row.get("order_intent_id") or 0)

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, user_id, execution_mode, initial_capital, exchange_config,
                   trading_config, market_type, leverage, market_category
            FROM qd_strategies_trading
            WHERE id = %s AND user_id = %s
            FOR UPDATE
            """,
            (strategy_id, user_id),
        )
        strategy = cur.fetchone() or {}
        if int(strategy.get("id") or 0) != strategy_id:
            cur.close()
            raise ValueError("virtualTrading.strategyNotFound")
        if str(strategy.get("execution_mode") or "signal").strip().lower() != "signal":
            cur.close()
            raise ValueError("virtualTrading.liveStrategyRejected")

        def _json_mapping(value: Any) -> dict[str, Any]:
            if isinstance(value, dict):
                return value
            if isinstance(value, str) and value.strip():
                try:
                    decoded = json.loads(value)
                    return decoded if isinstance(decoded, dict) else {}
                except (TypeError, ValueError):
                    return {}
            return {}

        exchange_config = _json_mapping(strategy.get("exchange_config"))
        trading_config = _json_mapping(strategy.get("trading_config"))
        cost_policy = resolve_virtual_execution_cost_policy(
            payload=payload,
            order_row=order_row,
            strategy=strategy,
            exchange_config=exchange_config,
            trading_config=trading_config,
        )
        commission_rate = cost_policy.commission_rate
        slippage_rate = cost_policy.slippage_rate
        fill_price = forced_fill_price or _fill_price(action, reference_price, slippage_rate)
        market_type = cost_policy.market_type
        market_category = str(strategy.get("market_category") or "").strip()
        market_open = is_equity_market_open(market_category)

        cur.execute(
            """
            SELECT id, status, fill_qty, fill_price
            FROM qd_strategy_virtual_orders
            WHERE pending_order_id = %s
            FOR UPDATE
            """,
            (order_id,),
        )
        existing = cur.fetchone() or {}
        if existing and not (
            market_open
            and forced_fill_price > 0
            and str(existing.get("status") or "").strip().lower() == "open"
        ):
            cur.close()
            return {
                "virtual_order_id": int(existing.get("id") or 0),
                "status": str(existing.get("status") or "filled"),
                "fill_quantity": float(existing.get("fill_qty") or 0.0),
                "fill_price": float(existing.get("fill_price") or 0.0),
                "idempotent": True,
            }

        initial_cash = float(strategy.get("initial_capital") or sizing.get("initial_capital") or 0.0)
        cur.execute(
            """
            INSERT INTO qd_strategy_virtual_accounts
                (strategy_id, user_id, initial_cash, cash_balance, realized_pnl, total_commission)
            VALUES (%s, %s, %s, %s, 0, 0)
            ON CONFLICT (strategy_id) DO NOTHING
            """,
            (strategy_id, user_id, initial_cash, initial_cash),
        )
        cur.execute(
            "SELECT * FROM qd_strategy_virtual_accounts WHERE strategy_id = %s FOR UPDATE",
            (strategy_id,),
        )
        account = cur.fetchone() or {}
        if not market_open or (order_type == "limit" and forced_fill_price <= 0):
            immediate_fill = None
            if market_open:
                immediate_fill = calculate_virtual_limit_fill_price(
                    action=action,
                    limit_price=limit_price,
                    market_price=reference_price,
                    slippage_rate=slippage_rate,
                )
            if immediate_fill is None:
                cur.execute(
                    """
                    INSERT INTO qd_strategy_virtual_orders
                        (user_id, strategy_id, strategy_run_id, pending_order_id, order_intent_id,
                         symbol, side, action, order_type, requested_qty, fill_qty,
                         reference_price, limit_price, fill_price, exchange_id, market_type, leverage,
                         commission_rate, commission_quote, slippage_rate, slippage_quote,
                         status, reason, filled_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, 0,
                            %s, %s, %s, %s, 0, %s, 0, 'open', %s, NULL)
                    RETURNING id
                    """,
                    (
                        user_id, strategy_id, strategy_run_id, order_id, order_intent_id,
                        symbol, side, action, order_type, requested_quantity, reference_price, limit_price,
                        cost_policy.exchange_id, market_type, cost_policy.leverage,
                        commission_rate, slippage_rate, str(payload.get("reason") or "")[:255],
                    ),
                )
                virtual_order_id = int((cur.fetchone() or {}).get("id") or 0)
                db.commit()
                cur.close()
                return {
                    "virtual_order_id": virtual_order_id,
                    "status": "open",
                    "fill_quantity": 0.0,
                    "fill_price": 0.0,
                    "limit_price": limit_price,
                    "commission": 0.0,
                    "commission_rate": commission_rate,
                    "slippage_rate": slippage_rate,
                    "slippage_quote": 0.0,
                    "exchange_id": cost_policy.exchange_id,
                    "market_type": market_type,
                    "leverage": cost_policy.leverage,
                    "account_equity": float(account.get("initial_cash") or initial_cash),
                    "deferred_reason": "market_closed" if not market_open else "limit_not_reached",
                    "idempotent": False,
                }
            fill_price = immediate_fill
        cur.execute(
            """
            SELECT * FROM qd_strategy_virtual_positions
            WHERE strategy_id = %s AND symbol_canonical = %s AND side = %s
            FOR UPDATE
            """,
            (strategy_id, symbol_key, side),
        )
        position = cur.fetchone() or {}
        transition = calculate_virtual_fill(
            action=action,
            requested_quantity=requested_quantity,
            fill_price=fill_price,
            current_size=float(position.get("size") or 0.0),
            current_entry_price=float(position.get("entry_price") or 0.0),
        )
        commission = cost_policy.commission_for(
            quantity=transition.fill_quantity,
            fill_price=fill_price,
        )
        slippage_quote = cost_policy.slippage_quote_for(
            quantity=transition.fill_quantity,
            reference_price=reference_price,
            fill_price=fill_price,
        )
        current_realized = float(account.get("realized_pnl") or 0.0)
        next_realized = current_realized + transition.gross_realized_pnl - commission
        total_commission = float(account.get("total_commission") or 0.0) + commission

        if existing:
            virtual_order_id = int(existing.get("id") or 0)
            cur.execute(
                """
                UPDATE qd_strategy_virtual_orders
                SET fill_qty = %s, reference_price = %s, fill_price = %s,
                    commission_quote = %s, slippage_quote = %s,
                    status = %s, filled_at = NOW()
                WHERE id = %s AND status = 'open'
                """,
                (
                    transition.fill_quantity, reference_price, fill_price,
                    commission, slippage_quote, transition.status, virtual_order_id,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO qd_strategy_virtual_orders
                    (user_id, strategy_id, strategy_run_id, pending_order_id, order_intent_id,
                     symbol, side, action, order_type, requested_qty, fill_qty,
                     reference_price, limit_price, fill_price, exchange_id, market_type, leverage,
                     commission_rate, commission_quote, slippage_rate, slippage_quote,
                     status, reason, filled_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (
                    user_id, strategy_id, strategy_run_id, order_id, order_intent_id,
                    symbol, side, action, order_type, requested_quantity,
                    transition.fill_quantity, reference_price, limit_price, fill_price,
                    cost_policy.exchange_id, market_type, cost_policy.leverage,
                    commission_rate, commission, slippage_rate, slippage_quote,
                    transition.status, str(payload.get("reason") or "")[:255],
                ),
            )
            virtual_order_id = int((cur.fetchone() or {}).get("id") or 0)

        if transition.status == "filled" and transition.next_size > 1e-12:
            unrealized = (
                (transition.next_entry_price - fill_price) * transition.next_size
                if side == "short"
                else (fill_price - transition.next_entry_price) * transition.next_size
            )
            cur.execute(
                """
                INSERT INTO qd_strategy_virtual_positions
                    (user_id, strategy_id, strategy_run_id, symbol, symbol_canonical, side,
                     size, entry_price, current_price, highest_price, lowest_price,
                     unrealized_pnl, pnl_percent, market_type, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, NOW())
                ON CONFLICT (strategy_id, symbol_canonical, side) DO UPDATE SET
                    strategy_run_id = EXCLUDED.strategy_run_id,
                    symbol = EXCLUDED.symbol,
                    size = EXCLUDED.size,
                    entry_price = EXCLUDED.entry_price,
                    current_price = EXCLUDED.current_price,
                    highest_price = GREATEST(qd_strategy_virtual_positions.highest_price, EXCLUDED.current_price),
                    lowest_price = CASE
                        WHEN qd_strategy_virtual_positions.lowest_price <= 0 THEN EXCLUDED.current_price
                        ELSE LEAST(qd_strategy_virtual_positions.lowest_price, EXCLUDED.current_price)
                    END,
                    unrealized_pnl = EXCLUDED.unrealized_pnl,
                    market_type = EXCLUDED.market_type,
                    updated_at = NOW()
                """,
                (
                    user_id, strategy_id, strategy_run_id, symbol, symbol_key, side,
                    transition.next_size, transition.next_entry_price, fill_price, fill_price,
                    fill_price, unrealized, market_type,
                ),
            )
        elif transition.status == "filled":
            cur.execute(
                "DELETE FROM qd_strategy_virtual_positions WHERE strategy_id = %s AND symbol_canonical = %s AND side = %s",
                (strategy_id, symbol_key, side),
            )

        cur.execute(
            """
            UPDATE qd_strategy_virtual_accounts
            SET cash_balance = initial_cash + %s,
                realized_pnl = %s,
                total_commission = %s,
                updated_at = NOW()
            WHERE strategy_id = %s
            """,
            (next_realized, next_realized, total_commission, strategy_id),
        )
        cur.execute(
            "SELECT COALESCE(SUM(unrealized_pnl), 0) AS unrealized FROM qd_strategy_virtual_positions WHERE strategy_id = %s",
            (strategy_id,),
        )
        unrealized_total = float((cur.fetchone() or {}).get("unrealized") or 0.0)
        account_equity = initial_cash + next_realized + unrealized_total

        if transition.status == "filled":
            cur.execute(
                """
                INSERT INTO qd_strategy_virtual_trades
                    (user_id, strategy_id, strategy_run_id, virtual_order_id, pending_order_id,
                     order_intent_id, symbol, symbol_canonical, type, side, price, amount,
                     value, commission, commission_quote, profit, close_reason,
                     matched_entry_price, account_equity, market_type, exchange_id, leverage,
                     reference_price, commission_rate, slippage_rate, slippage_quote,
                     fill_source, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, 'virtual_signal', NOW())
                """,
                (
                    user_id, strategy_id, strategy_run_id, virtual_order_id, order_id,
                    order_intent_id, symbol, symbol_key, action, side, fill_price,
                    transition.fill_quantity, transition.fill_quantity * fill_price,
                    commission, commission, transition.gross_realized_pnl,
                    str(payload.get("reason") or INDICATOR_SIGNAL)[:255] if action in _EXIT_ACTIONS else "",
                    float(position.get("entry_price") or 0.0), account_equity, market_type,
                    cost_policy.exchange_id, cost_policy.leverage, reference_price,
                    commission_rate, slippage_rate, slippage_quote,
                ),
            )
            fill_side = "buy" if action in {
                "open_long", "add_long", "reduce_short", "close_short",
            } else "sell"
            virtual_fill_id = f"virtual:{virtual_order_id}"
            cur.execute(
                """
                INSERT INTO strategy_order_fills
                    (order_intent_id, strategy_run_id, strategy_id,
                     exchange_id, exchange_order_id, exchange_fill_id,
                     side, position_side, price, quantity, notional, fee, fee_ccy,
                     credential_id, commission_quote, fee_status, filled_at, raw_json)
                VALUES (%s, %s, %s, 'virtual', %s, %s, %s, %s, %s, %s, %s, %s,
                        'USDT', 0, %s, 'actual', NOW(), '{}'::jsonb)
                ON CONFLICT (exchange_id, credential_id, exchange_fill_id)
                WHERE exchange_fill_id <> '' DO NOTHING
                """,
                (
                    order_intent_id, strategy_run_id, strategy_id,
                    str(virtual_order_id), virtual_fill_id, fill_side, side,
                    fill_price, transition.fill_quantity,
                    transition.fill_quantity * fill_price, commission, commission,
                ),
            )
            cur.execute(
                """
                UPDATE pending_orders
                SET status = 'filled', filled = %s, avg_price = %s,
                    executed_at = COALESCE(executed_at, NOW()), updated_at = NOW()
                WHERE id = %s
                """,
                (transition.fill_quantity, fill_price, order_id),
            )
            if order_intent_id > 0:
                cur.execute(
                    """
                    UPDATE strategy_order_intents
                    SET status = 'filled', updated_at = NOW()
                    WHERE id = %s
                    """,
                    (order_intent_id,),
                )
        db.commit()
        cur.close()

    return {
        "virtual_order_id": virtual_order_id,
        "status": transition.status,
        "fill_quantity": transition.fill_quantity,
        "fill_price": fill_price,
        "gross_realized_pnl": transition.gross_realized_pnl,
        "commission": commission,
        "commission_rate": commission_rate,
        "slippage_rate": slippage_rate,
        "slippage_quote": slippage_quote,
        "exchange_id": cost_policy.exchange_id,
        "market_type": market_type,
        "leverage": cost_policy.leverage,
        "account_equity": account_equity,
        "idempotent": False,
    }


def settle_virtual_pending_order(pending_order_id: int) -> dict[str, Any]:
    """Fill one already-persisted signal order without waiting for the worker poll."""
    order_id = int(pending_order_id or 0)
    if order_id <= 0:
        raise ValueError("virtualTrading.invalidIdentity")
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT * FROM pending_orders WHERE id = %s", (order_id,))
        row = dict(cur.fetchone() or {})
        cur.close()
    if not row:
        raise ValueError("virtualTrading.pendingOrderNotFound")
    raw_payload = row.get("payload_json")
    if isinstance(raw_payload, Mapping):
        payload = dict(raw_payload)
    elif isinstance(raw_payload, str) and raw_payload.strip():
        decoded = json.loads(raw_payload)
        payload = dict(decoded) if isinstance(decoded, Mapping) else {}
    else:
        payload = {}
    return execute_virtual_signal_order(row, payload)


def _cancel_virtual_order(pending_order_id: int, virtual_order_id: int, order_intent_id: int) -> None:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE qd_strategy_virtual_orders
            SET status = 'cancelled', filled_at = NULL
            WHERE id = %s AND status = 'open'
            """,
            (int(virtual_order_id),),
        )
        cur.execute(
            """
            UPDATE pending_orders
            SET status = 'cancelled', updated_at = NOW()
            WHERE id = %s AND status IN ('pending', 'processing', 'sent', 'syncing')
            """,
            (int(pending_order_id),),
        )
        if int(order_intent_id or 0) > 0:
            cur.execute(
                """
                UPDATE strategy_order_intents
                SET status = 'cancelled', updated_at = NOW()
                WHERE id = %s AND status NOT IN ('filled', 'cancelled', 'rejected', 'failed', 'expired')
                """,
                (int(order_intent_id),),
            )
        db.commit()
        cur.close()


def cancel_virtual_limit_orders(strategy_id: int, strategy_run_id: int = 0) -> int:
    """Cancel open virtual limits when a signal strategy is stopped or replaced."""
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, pending_order_id, order_intent_id
            FROM qd_strategy_virtual_orders
            WHERE strategy_id = %s AND status = 'open'
              AND (%s = 0 OR strategy_run_id = %s)
            """,
            (int(strategy_id), int(strategy_run_id), int(strategy_run_id)),
        )
        rows = [dict(row) for row in (cur.fetchall() or [])]
        cur.close()
    for row in rows:
        _cancel_virtual_order(
            int(row.get("pending_order_id") or 0),
            int(row.get("id") or 0),
            int(row.get("order_intent_id") or 0),
        )
    return len(rows)


def match_virtual_limit_orders(
    strategy_id: int,
    prices: Mapping[str, Any],
    *,
    strategy_run_id: int = 0,
) -> list[dict[str, Any]]:
    """Match open signal-mode orders against fresh in-session market prices."""
    normalized_prices = {
        canonical_symbol(symbol): float(price or 0.0)
        for symbol, price in (prices or {}).items()
        if canonical_symbol(symbol) and float(price or 0.0) > 0
    }
    if not normalized_prices:
        return []
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT vo.*, po.payload_json, po.execution_mode AS pending_execution_mode,
                   s.market_category AS strategy_market_category
            FROM qd_strategy_virtual_orders vo
            JOIN pending_orders po ON po.id = vo.pending_order_id
            JOIN qd_strategies_trading s ON s.id = vo.strategy_id
            WHERE vo.strategy_id = %s AND vo.status = 'open'
              AND (%s = 0 OR vo.strategy_run_id = %s)
            ORDER BY vo.id ASC
            """,
            (int(strategy_id), int(strategy_run_id), int(strategy_run_id)),
        )
        rows = [dict(row) for row in (cur.fetchall() or [])]
        cur.close()

    matched: list[dict[str, Any]] = []
    for row in rows:
        raw_payload = row.get("payload_json") or {}
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload) or {}
            except (TypeError, ValueError):
                raw_payload = {}
        payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
        if payload.get("strategy_cancel_requested"):
            _cancel_virtual_order(
                int(row.get("pending_order_id") or 0),
                int(row.get("id") or 0),
                int(row.get("order_intent_id") or 0),
            )
            continue
        market_category = str(
            row.get("strategy_market_category") or payload.get("market_category") or ""
        ).strip()
        if not is_equity_market_open(market_category):
            continue
        market_price = normalized_prices.get(canonical_symbol(row.get("symbol"))) or 0.0
        order_type = str(row.get("order_type") or "limit").strip().lower()
        if order_type == "market":
            fill_price = _fill_price(
                str(row.get("action") or ""),
                market_price,
                float(row.get("slippage_rate") or 0.0),
            ) if market_price > 0 else None
        else:
            fill_price = calculate_virtual_limit_fill_price(
                action=str(row.get("action") or ""),
                limit_price=float(row.get("limit_price") or 0.0),
                market_price=market_price,
                slippage_rate=float(row.get("slippage_rate") or 0.0),
            )
        if fill_price is None:
            continue
        payload.update({
            "strategy_id": int(row.get("strategy_id") or 0),
            "strategy_run_id": int(row.get("strategy_run_id") or 0),
            "order_intent_id": int(row.get("order_intent_id") or 0),
            "execution_mode": "signal",
            "signal_type": str(row.get("action") or ""),
            "symbol": str(row.get("symbol") or ""),
            "amount": float(row.get("requested_qty") or 0.0),
            "order_type": order_type,
            "limit_price": float(row.get("limit_price") or 0.0),
            "ref_price": market_price,
            "_virtual_fill_price": fill_price,
        })
        result = execute_virtual_signal_order(
            {
                "id": int(row.get("pending_order_id") or 0),
                "user_id": int(row.get("user_id") or 0),
                "strategy_id": int(row.get("strategy_id") or 0),
                "strategy_run_id": int(row.get("strategy_run_id") or 0),
                "order_intent_id": int(row.get("order_intent_id") or 0),
                "execution_mode": "signal",
                "signal_type": str(row.get("action") or ""),
                "symbol": str(row.get("symbol") or ""),
                "amount": float(row.get("requested_qty") or 0.0),
                "order_type": order_type,
            },
            payload,
        )
        matched.append(result)
    return matched


def mark_virtual_positions(
    strategy_id: int,
    prices: Mapping[str, Any],
    *,
    strategy_run_id: int = 0,
) -> int:
    """Mark one strategy's virtual positions with its venue-scoped prices."""
    normalized_prices: dict[str, float] = {}
    ambiguous_symbols: set[str] = set()
    for instrument_key, raw_price in (prices or {}).items():
        symbol_key = canonical_symbol(instrument_key)
        try:
            price = float(raw_price or 0.0)
        except (TypeError, ValueError):
            continue
        if not symbol_key or price <= 0:
            continue
        previous = normalized_prices.get(symbol_key)
        if previous is not None and abs(previous - price) > max(1e-10, abs(previous) * 1e-10):
            ambiguous_symbols.add(symbol_key)
            continue
        normalized_prices[symbol_key] = price
    for symbol_key in ambiguous_symbols:
        normalized_prices.pop(symbol_key, None)
    if not normalized_prices:
        return 0

    updated = 0
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, symbol_canonical, side, size, entry_price
            FROM qd_strategy_virtual_positions
            WHERE strategy_id = %s
              AND (%s = 0 OR strategy_run_id = %s)
            FOR UPDATE
            """,
            (int(strategy_id), int(strategy_run_id), int(strategy_run_id)),
        )
        rows = cur.fetchall() or []
        for row in rows:
            symbol_key = canonical_symbol(row.get("symbol_canonical") or "")
            current_price = normalized_prices.get(symbol_key)
            if not current_price:
                continue
            size = max(0.0, float(row.get("size") or 0.0))
            entry_price = max(0.0, float(row.get("entry_price") or 0.0))
            if size <= 1e-12 or entry_price <= 0:
                continue
            side = str(row.get("side") or "long").strip().lower()
            unrealized = (
                (entry_price - current_price) * size
                if side == "short"
                else (current_price - entry_price) * size
            )
            notional = entry_price * size
            pnl_percent = unrealized / notional * 100.0 if notional > 0 else 0.0
            cur.execute(
                """
                UPDATE qd_strategy_virtual_positions
                SET current_price = %s,
                    highest_price = GREATEST(highest_price, %s),
                    lowest_price = CASE
                        WHEN lowest_price <= 0 THEN %s
                        ELSE LEAST(lowest_price, %s)
                    END,
                    unrealized_pnl = %s,
                    pnl_percent = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    current_price,
                    current_price,
                    current_price,
                    current_price,
                    unrealized,
                    pnl_percent,
                    int(row.get("id") or 0),
                ),
            )
            updated += int(cur.rowcount or 0)
        db.commit()
        cur.close()
    return updated


def list_virtual_limit_orders(
    strategy_id: int,
    *,
    status: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    """List virtual limit orders for the signal-mode operations screen."""
    normalized_status = str(status or "").strip().lower()
    row_limit = max(1, min(int(limit or 200), 500))
    with get_db_connection() as db:
        cur = db.cursor()
        if normalized_status in {"all", "any"}:
            cur.execute(
                """
                SELECT vo.*, po.client_order_id, po.payload_json
                FROM qd_strategy_virtual_orders vo
                LEFT JOIN pending_orders po ON po.id = vo.pending_order_id
                WHERE vo.strategy_id = %s AND vo.order_type = 'limit'
                ORDER BY vo.id DESC
                LIMIT %s
                """,
                (int(strategy_id), row_limit),
            )
        else:
            target_status = normalized_status or "open"
            cur.execute(
                """
                SELECT vo.*, po.client_order_id, po.payload_json
                FROM qd_strategy_virtual_orders vo
                LEFT JOIN pending_orders po ON po.id = vo.pending_order_id
                WHERE vo.strategy_id = %s AND vo.order_type = 'limit' AND vo.status = %s
                ORDER BY vo.id DESC
                LIMIT %s
                """,
                (int(strategy_id), target_status, row_limit),
            )
        rows = [dict(row) for row in (cur.fetchall() or [])]
        cur.close()
    return rows


def list_virtual_positions(strategy_id: int, symbol: str | None = None) -> list[dict[str, Any]]:
    symbol_key = canonical_symbol(symbol) if symbol else ""
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, strategy_id, symbol, symbol_canonical, side, size, entry_price,
                   current_price, highest_price, lowest_price, unrealized_pnl,
                   pnl_percent, market_type, strategy_run_id, updated_at
            FROM qd_strategy_virtual_positions
            WHERE strategy_id = %s AND (%s = '' OR symbol_canonical = %s)
            ORDER BY id DESC
            """,
            (int(strategy_id), symbol_key, symbol_key),
        )
        rows = cur.fetchall() or []
        cur.close()
    return [dict(row) for row in rows]


def list_virtual_trades(strategy_id: int) -> list[dict[str, Any]]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, strategy_id, symbol, symbol_canonical, type, side, price, amount,
                   value, commission, commission_quote, profit, close_reason,
                   matched_entry_price, market_type, strategy_run_id, pending_order_id,
                   order_intent_id, fill_source, account_equity, exchange_id, leverage,
                   reference_price, commission_rate, slippage_rate, slippage_quote, created_at
            FROM qd_strategy_virtual_trades
            WHERE strategy_id = %s
            ORDER BY id DESC
            """,
            (int(strategy_id),),
        )
        rows = cur.fetchall() or []
        cur.close()
    return [dict(row) for row in rows]


def build_virtual_equity_curve(strategy_id: int, initial_capital: float) -> list[dict[str, Any]]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT created_at, account_equity
            FROM qd_strategy_virtual_trades
            WHERE strategy_id = %s
            ORDER BY id ASC
            """,
            (int(strategy_id),),
        )
        rows = cur.fetchall() or []
        cur.execute(
            "SELECT COALESCE(SUM(unrealized_pnl), 0) AS unrealized FROM qd_strategy_virtual_positions WHERE strategy_id = %s",
            (int(strategy_id),),
        )
        unrealized = float((cur.fetchone() or {}).get("unrealized") or 0.0)
        cur.execute(
            "SELECT realized_pnl FROM qd_strategy_virtual_accounts WHERE strategy_id = %s",
            (int(strategy_id),),
        )
        account = cur.fetchone() or {}
        cur.close()
    curve: list[dict[str, Any]] = []
    if rows:
        curve.append({"time": _timestamp(rows[0].get("created_at")), "equity": round(float(initial_capital), 2)})
        curve.extend(
            {"time": _timestamp(row.get("created_at")), "equity": round(float(row.get("account_equity") or initial_capital), 2)}
            for row in rows
        )
    latest = float(initial_capital) + float(account.get("realized_pnl") or 0.0) + unrealized
    if not curve or abs(unrealized) > 1e-12:
        curve.append({"time": int(time.time()), "equity": round(latest, 2)})
    return curve


def _timestamp(value: Any) -> int:
    if hasattr(value, "timestamp"):
        return int(value.timestamp())
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(time.time())


__all__ = [
    "VirtualFillTransition",
    "build_virtual_equity_curve",
    "calculate_virtual_limit_fill_price",
    "calculate_virtual_fill",
    "cancel_virtual_limit_orders",
    "canonical_symbol",
    "execute_virtual_signal_order",
    "list_virtual_limit_orders",
    "list_virtual_positions",
    "list_virtual_trades",
    "mark_virtual_positions",
    "match_virtual_limit_orders",
    "settle_virtual_pending_order",
]
