"""Account-leg ownership, protected manual inventory, and drift controls.

The exchange exposes one aggregate position per account instrument leg.  This
module keeps the missing ownership layer between the L1 exchange mirror and L3
strategy ledgers:

    account quantity = strategy allocations + protected manual quantity

Strict mode is the default.  Advanced coexistence is enabled explicitly by a
user repair action and records the currently unallocated quantity as protected
manual inventory.  A negative or unexplained delta blocks new entries on that
leg, while reduce-only exits remain available and are capped so they cannot
consume the protected quantity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Tuple

from app.services.live_trading.records import normalize_strategy_symbol
from app.utils.db import get_db_connection
from app.utils.numeric_precision import format_decimal


STRICT_MODE = "strict"
ADVANCED_MODE = "advanced"
STATUS_OK = "ok"
STATUS_BLOCKED = "drift_blocked"
COEXISTENCE_MARKET_TYPES = frozenset({"spot", "swap"})
CRYPTO_COEXISTENCE_EXCHANGES = frozenset({
    "binance", "bitget", "bybit", "gate", "htx", "okx",
})


def normalize_market_type(value: str) -> str:
    market = str(value or "swap").strip().lower()
    if market in {"future", "futures", "perp", "perpetual"}:
        return "swap"
    return market


def supports_position_coexistence(value: str, exchange_id: str = "") -> bool:
    """Return whether account/strategy inventory can share one Crypto market leg."""
    market = normalize_market_type(value)
    if market not in COEXISTENCE_MARKET_TYPES:
        return False
    exchange = str(exchange_id or "").strip().lower()
    return market == "swap" or not exchange or exchange in CRYPTO_COEXISTENCE_EXCHANGES


def normalize_side(value: str) -> str:
    side = str(value or "").strip().lower()
    return side if side in {"long", "short"} else ""


def canonical_symbol(value: str) -> str:
    return normalize_strategy_symbol(str(value or "")).upper()


@dataclass(frozen=True)
class OwnershipSnapshot:
    symbol: str
    side: str
    account_qty: float
    strategy_qty: float
    protected_qty: float
    expected_qty: float
    unknown_qty: float
    tolerance: float
    coexistence_mode: str
    status: str
    reason: str
    allowed: bool
    should_log: bool = False

    def metadata(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_position_ownership(
    *,
    symbol: str,
    side: str,
    account_qty: float,
    strategy_qty: float,
    protected_qty: float = 0.0,
    coexistence_mode: str = STRICT_MODE,
    previous_status: str = "",
    previous_reason: str = "",
    absolute_tolerance: float = 0.0,
) -> OwnershipSnapshot:
    """Pure ownership calculation used by execution, APIs, and tests."""
    account = max(0.0, float(account_qty or 0.0))
    strategy = max(0.0, float(strategy_qty or 0.0))
    protected = max(0.0, float(protected_qty or 0.0))
    mode = ADVANCED_MODE if str(coexistence_mode or "").lower() == ADVANCED_MODE else STRICT_MODE
    if mode == STRICT_MODE:
        # A stale protected value must never silently weaken strict mode.
        protected = 0.0
    expected = strategy + protected
    tolerance = max(1e-8, expected * 0.001, max(0.0, float(absolute_tolerance or 0.0)))
    unknown = account - expected
    if abs(unknown) <= tolerance:
        status = STATUS_OK
        reason = ""
        allowed = True
    elif unknown > 0:
        status = STATUS_BLOCKED
        reason = "unallocated_account_position"
        allowed = False
    else:
        status = STATUS_BLOCKED
        reason = "account_below_protected_allocation"
        allowed = False
    should_log = status == STATUS_BLOCKED and (
        str(previous_status or "") != STATUS_BLOCKED
        or str(previous_reason or "") != reason
    )
    return OwnershipSnapshot(
        symbol=canonical_symbol(symbol),
        side=normalize_side(side),
        account_qty=account,
        strategy_qty=strategy,
        protected_qty=protected,
        expected_qty=expected,
        unknown_qty=unknown,
        tolerance=tolerance,
        coexistence_mode=mode,
        status=status,
        reason=reason,
        allowed=allowed,
        should_log=should_log,
    )


def _fetch_reservation(
    *,
    user_id: int,
    credential_id: int,
    market_type: str,
    symbol: str,
    side: str,
) -> Dict[str, Any]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT * FROM qd_position_reservations
            WHERE user_id = %s AND credential_id = %s AND market_type = %s
              AND symbol_canonical = %s AND side = %s
            LIMIT 1
            """,
            (
                int(user_id or 0),
                int(credential_id or 0),
                normalize_market_type(market_type),
                canonical_symbol(symbol),
                normalize_side(side),
            ),
        )
        row = cur.fetchone() or {}
        cur.close()
    return dict(row)


def evaluate_and_record_ownership(
    *,
    user_id: int,
    credential_id: int,
    exchange_id: str,
    market_type: str,
    symbol: str,
    side: str,
    account_qty: float,
    strategy_qty: float,
    inst_id: str = "",
    absolute_tolerance: float = 0.0,
) -> OwnershipSnapshot:
    """Evaluate one leg and persist its block/recovery state atomically enough for workers."""
    row = _fetch_reservation(
        user_id=user_id,
        credential_id=credential_id,
        market_type=market_type,
        symbol=symbol,
        side=side,
    )
    snapshot = calculate_position_ownership(
        symbol=symbol,
        side=side,
        account_qty=account_qty,
        strategy_qty=strategy_qty,
        protected_qty=float(row.get("manual_reserved_qty") or 0.0),
        coexistence_mode=str(row.get("coexistence_mode") or STRICT_MODE),
        previous_status=str(row.get("status") or ""),
        previous_reason=str(row.get("drift_reason") or ""),
        absolute_tolerance=float(absolute_tolerance or 0.0),
    )
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO qd_position_reservations
              (user_id, credential_id, exchange_id, market_type, inst_id, symbol,
               symbol_canonical, side, coexistence_mode, manual_reserved_qty,
               observed_account_qty, allocated_qty, status, drift_reason,
               last_log_at, created_at, updated_at)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
               CASE WHEN %s THEN NOW() ELSE NULL END, NOW(), NOW())
            ON CONFLICT (user_id, credential_id, market_type, symbol_canonical, side)
            DO UPDATE SET
              exchange_id = excluded.exchange_id,
              inst_id = CASE WHEN excluded.inst_id <> '' THEN excluded.inst_id ELSE qd_position_reservations.inst_id END,
              symbol = excluded.symbol,
              observed_account_qty = excluded.observed_account_qty,
              allocated_qty = excluded.allocated_qty,
              status = excluded.status,
              drift_reason = excluded.drift_reason,
              last_log_at = CASE WHEN %s THEN NOW() ELSE qd_position_reservations.last_log_at END,
              updated_at = NOW()
            """,
            (
                int(user_id or 0),
                int(credential_id or 0),
                str(exchange_id or "").strip().lower(),
                normalize_market_type(market_type),
                str(inst_id or "").strip(),
                normalize_strategy_symbol(symbol),
                snapshot.symbol,
                snapshot.side,
                snapshot.coexistence_mode,
                snapshot.protected_qty,
                snapshot.account_qty,
                snapshot.strategy_qty,
                snapshot.status,
                snapshot.reason,
                bool(snapshot.should_log),
                bool(snapshot.should_log),
            ),
        )
        db.commit()
        cur.close()
    return snapshot


def is_position_leg_blocked(
    *,
    user_id: int,
    credential_id: int,
    market_type: str,
    symbol: str,
    side: str,
) -> bool:
    """Fast queue-boundary check; exits intentionally never call this."""
    row = _fetch_reservation(
        user_id=user_id,
        credential_id=credential_id,
        market_type=market_type,
        symbol=symbol,
        side=side,
    )
    return str(row.get("status") or "") == STATUS_BLOCKED


def protected_quantity(
    *,
    user_id: int,
    credential_id: int,
    market_type: str,
    symbol: str,
    side: str,
) -> float:
    row = _fetch_reservation(
        user_id=user_id,
        credential_id=credential_id,
        market_type=market_type,
        symbol=symbol,
        side=side,
    )
    if str(row.get("coexistence_mode") or STRICT_MODE) != ADVANCED_MODE:
        return 0.0
    return max(0.0, float(row.get("manual_reserved_qty") or 0.0))


def repair_position_ownership(
    *,
    user_id: int,
    credential_id: int,
    exchange_id: str,
    market_type: str,
    symbol: str,
    side: str,
    account_qty: float,
    strategy_qty: float,
    action: str,
    inst_id: str = "",
) -> OwnershipSnapshot:
    """Apply an explicit user repair action and return the resulting snapshot."""
    action_name = str(action or "").strip().lower()
    if action_name not in {"protect_manual", "strict_mode", "recheck"}:
        raise ValueError("positionOwnership.invalidRepairAction")
    if action_name == "protect_manual" and not supports_position_coexistence(
        market_type, exchange_id
    ):
        raise ValueError("positionOwnership.coexistenceMarketUnsupported")
    existing = _fetch_reservation(
        user_id=user_id,
        credential_id=credential_id,
        market_type=market_type,
        symbol=symbol,
        side=side,
    )
    mode = str(existing.get("coexistence_mode") or STRICT_MODE)
    manual = max(0.0, float(existing.get("manual_reserved_qty") or 0.0))
    if action_name == "protect_manual":
        if float(account_qty or 0.0) + 1e-8 < float(strategy_qty or 0.0):
            raise ValueError("positionOwnership.accountBelowStrategyAllocation")
        mode = ADVANCED_MODE
        manual = max(0.0, float(account_qty or 0.0) - float(strategy_qty or 0.0))
    elif action_name == "strict_mode":
        mode = STRICT_MODE
        manual = 0.0

    snapshot = calculate_position_ownership(
        symbol=symbol,
        side=side,
        account_qty=account_qty,
        strategy_qty=strategy_qty,
        protected_qty=manual,
        coexistence_mode=mode,
    )
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO qd_position_reservations
              (user_id, credential_id, exchange_id, market_type, inst_id, symbol,
               symbol_canonical, side, coexistence_mode, manual_reserved_qty,
               observed_account_qty, allocated_qty, status, drift_reason,
               created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (user_id, credential_id, market_type, symbol_canonical, side)
            DO UPDATE SET
              exchange_id = excluded.exchange_id,
              inst_id = CASE WHEN excluded.inst_id <> '' THEN excluded.inst_id ELSE qd_position_reservations.inst_id END,
              symbol = excluded.symbol,
              coexistence_mode = excluded.coexistence_mode,
              manual_reserved_qty = excluded.manual_reserved_qty,
              observed_account_qty = excluded.observed_account_qty,
              allocated_qty = excluded.allocated_qty,
              status = excluded.status,
              drift_reason = excluded.drift_reason,
              last_log_at = NULL,
              updated_at = NOW()
            """,
            (
                int(user_id or 0), int(credential_id or 0), str(exchange_id or "").lower(),
                normalize_market_type(market_type), str(inst_id or ""),
                normalize_strategy_symbol(symbol), snapshot.symbol, snapshot.side,
                mode, manual, snapshot.account_qty, snapshot.strategy_qty,
                snapshot.status, snapshot.reason,
            ),
        )
        db.commit()
        cur.close()
    return snapshot


def list_reservations(
    *, user_id: int, credential_id: int, market_type: str
) -> List[Dict[str, Any]]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT * FROM qd_position_reservations
            WHERE user_id = %s AND credential_id = %s AND market_type = %s
            ORDER BY symbol_canonical, side
            """,
            (int(user_id or 0), int(credential_id or 0), normalize_market_type(market_type)),
        )
        rows = [dict(row) for row in (cur.fetchall() or [])]
        cur.close()
    return rows


def build_ownership_rows(
    *,
    account_rows: Iterable[Dict[str, Any]],
    allocated_rows: Iterable[Dict[str, Any]],
    reservation_rows: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build UI/API rows without mutating ownership state."""
    def aggregate(rows: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str], float]:
        values: Dict[Tuple[str, str], float] = {}
        for row in rows or []:
            key = (canonical_symbol(row.get("symbol") or row.get("symbol_canonical") or ""), normalize_side(row.get("side") or ""))
            if not key[0] or not key[1]:
                continue
            values[key] = values.get(key, 0.0) + max(0.0, float(row.get("size") or 0.0))
        return values

    account = aggregate(account_rows)
    allocated = aggregate(allocated_rows)
    reservations = {
        (canonical_symbol(row.get("symbol_canonical") or row.get("symbol") or ""), normalize_side(row.get("side") or "")): row
        for row in reservation_rows or []
    }
    output: List[Dict[str, Any]] = []
    for key in sorted(set(account) | set(allocated) | set(reservations)):
        row = reservations.get(key) or {}
        snap = calculate_position_ownership(
            symbol=key[0], side=key[1], account_qty=account.get(key, 0.0),
            strategy_qty=allocated.get(key, 0.0),
            protected_qty=float(row.get("manual_reserved_qty") or 0.0),
            coexistence_mode=str(row.get("coexistence_mode") or STRICT_MODE),
        )
        item = snap.metadata()
        item["inst_id"] = str(row.get("inst_id") or "")
        item["updated_at"] = row.get("updated_at")
        output.append(item)
    return output


def ownership_log_message(snapshot: OwnershipSnapshot) -> str:
    return (
        f"Position ownership drift blocked new entries: {snapshot.symbol} {snapshot.side}; "
        f"account={format_decimal(snapshot.account_qty)}, "
        f"strategy={format_decimal(snapshot.strategy_qty)}, "
        f"protected={format_decimal(snapshot.protected_qty)}, "
        f"unknown={format_decimal(snapshot.unknown_qty)}, "
        f"mode={snapshot.coexistence_mode}, reason={snapshot.reason}. "
        "Reduce-only exits remain enabled."
    )
