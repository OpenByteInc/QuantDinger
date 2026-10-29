"""Fail-closed ownership checks for Alpaca's netted account positions."""

from contextlib import contextmanager
import math

from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.services.live_trading.leg_context import credential_id_from_exchange_config
from app.services.live_trading.account_positions import list_strategy_allocations_for_account
from app.services.live_trading.position_ownership import (
    canonical_symbol, evaluate_and_record_ownership,
)

logger = get_logger(__name__)


@contextmanager
def alpaca_account_lock(credential_id):
    """Serialize application submissions and manual-baseline repairs across workers."""
    if int(credential_id or 0) <= 0:
        raise ValueError("positionOwnership.snapshotUnavailable")
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT pg_try_advisory_xact_lock(%s, %s) AS acquired", (7421, int(credential_id)))
        row = cur.fetchone() or {}
        if not row.get("acquired"):
            raise ValueError("positionOwnership.accountBusy")
        try:
            yield
        finally:
            db.rollback()
            cur.close()


def _quantity(value):
    result = float(value or 0.0)
    if not math.isfinite(result):
        raise ValueError("positionOwnership.snapshotUnavailable")
    return result


def ensure_alpaca_settled(*, user_id, credential_id, order_id=0, symbol=""):
    """Do not base a new submission or repair on an in-flight local order."""
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT po.symbol FROM pending_orders po
            JOIN qd_strategies_trading s ON s.id = po.strategy_id
            WHERE s.user_id = %s AND po.credential_id = %s AND po.id <> %s
              AND (po.status IN ('processing', 'sent', 'syncing')
                   OR COALESCE(po.filled, 0) > COALESCE(
                       (SELECT SUM(t.amount) FROM qd_strategy_trades t
                        WHERE t.pending_order_id = po.id), 0) + 0.00000001)
            """,
            (int(user_id), int(credential_id), int(order_id)),
        )
        rows = cur.fetchall() or []
        cur.close()
    if any(not symbol or canonical_symbol(row.get("symbol")) == canonical_symbol(symbol) for row in rows):
        raise ValueError("positionOwnership.ordersPending")


def guarded_alpaca_quantity(*, client, strategy_id, user_id, credential_id, symbol, signal_type, amount, order_id, exchange_id="alpaca"):
    """Return a safe submission quantity; caller must hold the account lock."""
    amount = _quantity(amount)
    if amount <= 0:
        raise ValueError("positionOwnership.noStrategyInventory")
    ensure_alpaca_settled(user_id=user_id, credential_id=credential_id, order_id=order_id, symbol=symbol)
    positions = client.get_positions(raise_on_error=True)
    orders = client.get_orders(status="open", limit=500, raise_on_error=True)
    if not isinstance(positions, list) or not isinstance(orders, list) or len(orders) >= 500:
        raise ValueError("positionOwnership.snapshotUnavailable")
    wanted = canonical_symbol(symbol)
    # An existing broker order can consume inventory after this snapshot.
    if any(canonical_symbol(row.get("symbol")) == wanted for row in orders):
        raise ValueError("positionOwnership.ordersPending")
    account = {"long": 0.0, "short": 0.0}
    for row in positions:
        if canonical_symbol(row.get("symbol")) != wanted:
            continue
        if row.get("quantity", row.get("qty")) is None:
            raise ValueError("positionOwnership.snapshotUnavailable")
        qty = _quantity(row.get("quantity", row.get("qty")))
        side = str(row.get("side") or ("short" if qty < 0 else "long")).lower()
        if side not in account:
            raise ValueError("positionOwnership.snapshotUnavailable")
        account[side] += abs(qty)
    allocations = list_strategy_allocations_for_account(
        user_id=user_id, credential_id=credential_id, market_type="spot",
        allowed_symbols={symbol}, exchange_id=exchange_id,
    )
    signal = str(signal_type or "").lower()
    side = "short" if "short" in signal else "long"
    opposite = "long" if side == "short" else "short"
    own = total = 0.0
    for row in allocations:
        if row.get("side") != side:
            continue
        qty = _quantity(row.get("size"))
        if qty < 0:
            raise ValueError("positionOwnership.snapshotUnavailable")
        total += qty
        if int(row.get("strategy_id") or 0) == int(strategy_id):
            own += qty
    ownership = evaluate_and_record_ownership(
        user_id=user_id, credential_id=credential_id, exchange_id=exchange_id,
        market_type="spot", symbol=symbol, side=side,
        account_qty=account[side], strategy_qty=total,
    )
    if signal.startswith(("open_", "add_")):
        if account[opposite] > 1e-8:
            raise ValueError("positionOwnership.oppositeInventory")
        if not ownership.allowed:
            raise ValueError("positionOwnership.driftBlocked")
        return amount
    if not signal.startswith(("close_", "reduce_")):
        raise ValueError("positionOwnership.invalidRepairRequest")
    available = max(0.0, account[side] - max(0.0, total - own))
    quantity = min(amount, own, available)
    if quantity <= 1e-8:
        raise ValueError("positionOwnership.noStrategyInventory")
    return quantity


def execute_guarded_alpaca_order(worker, **kwargs):
    """Validate broker capabilities before checking and submitting owned inventory."""
    from app.services.live_trading.records import _get_user_id_from_strategy

    order_id = int(kwargs["order_id"])
    payload = kwargs["payload"]
    signal = str(payload.get("signal_type") or kwargs["order_row"].get("signal_type") or "").lower()
    exchange_id = str(kwargs.pop("exchange_id", "alpaca") or "alpaca").strip().lower()
    market = str(kwargs.get("market_category") or "USStock").strip().lower()
    if market in {"crypto", "cryptocurrency"} and "short" in signal:
        reason = "alpaca_crypto_short_not_supported"
        worker._mark_failed(order_id=order_id, error=reason)
        kwargs["_notify_live_best_effort"](status="failed", error=reason)
        return
    credential_id = credential_id_from_exchange_config(kwargs["exchange_config"])
    prepare_submission = kwargs.pop("prepare_submission", None)
    try:
        with alpaca_account_lock(credential_id):
            payload = dict(kwargs["payload"])
            row = kwargs["order_row"]
            payload["amount"] = guarded_alpaca_quantity(
                client=kwargs["client"], strategy_id=kwargs["strategy_id"],
                user_id=_get_user_id_from_strategy(kwargs["strategy_id"]),
                credential_id=credential_id,
                symbol=payload.get("symbol") or row.get("symbol"),
                signal_type=payload.get("signal_type") or row.get("signal_type"),
                amount=payload.get("amount") or row.get("amount") or 0,
                order_id=order_id, exchange_id=exchange_id,
            )
            if callable(prepare_submission):
                prepare_submission()
            worker._execute_alpaca_order_locked(**{**kwargs, "payload": payload, "exchange_id": exchange_id})
    except Exception as exc:
        reason = str(exc)
        if reason in {"positionOwnership.accountBusy", "positionOwnership.ordersPending"}:
            worker._mark_deferred(order_id, reason)
        else:
            if not reason.startswith("positionOwnership."):
                logger.exception("Broker ownership check failed: exchange=%s pending_id=%s", exchange_id, order_id)
                reason = "positionOwnership.snapshotUnavailable"
            worker._mark_failed(order_id=order_id, error=reason)
            kwargs["_notify_live_best_effort"](status="failed", error=reason)
