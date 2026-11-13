"""Classify verified, non-tradable spot inventory left after an exit."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional

from app.services.instrument_rules import InstrumentRules, get_instrument_rules_provider
from app.services.live_trading.records import normalize_strategy_symbol
from app.services.live_trading.spot_sizing import fetch_spot_last_price, get_spot_base_holding
from app.utils.db import get_db_connection
from app.utils.logger import get_logger


logger = get_logger(__name__)
_ACTIVE_ORDER_STATUSES = {"pending", "processing", "sent", "syncing", "reconciling"}


@dataclass(frozen=True)
class ExitResidualDecision:
    classified: bool
    reason: str = ""
    tradable_size: float = 0.0
    total_size: float = 0.0
    available_size: float = 0.0
    price: float = 0.0


def evaluate_exit_residual(
    *,
    local_size: float,
    total_size: float,
    available_size: float,
    price: float,
    rules: InstrumentRules,
) -> ExitResidualDecision:
    """Return a classification only when the exchange snapshot is conclusive."""
    local = max(0.0, float(local_size or 0.0))
    total = max(0.0, float(total_size or 0.0))
    available = max(0.0, float(available_size or 0.0))
    px = max(0.0, float(price or 0.0))
    step = max(0.0, float(rules.amount_step or 0.0))
    tolerance = max(1e-12, step * 0.51, local * 1e-8)

    if local <= 0 or total <= 0:
        return ExitResidualDecision(False, total_size=total, available_size=available, price=px)
    if abs(total - local) > tolerance:
        return ExitResidualDecision(False, total_size=total, available_size=available, price=px)
    if total - available > tolerance:
        return ExitResidualDecision(False, total_size=total, available_size=available, price=px)

    tradable = max(0.0, float(rules.normalize_amount(available, enforce_minimum=True)))
    if tradable <= tolerance:
        return ExitResidualDecision(
            True,
            reason="below_min_amount",
            tradable_size=0.0,
            total_size=total,
            available_size=available,
            price=px,
        )

    min_notional = max(0.0, float(rules.min_notional or 0.0))
    if min_notional > 0:
        if px <= 0:
            return ExitResidualDecision(False, tradable_size=tradable, total_size=total, available_size=available)
        if tradable * px + 1e-12 < min_notional:
            return ExitResidualDecision(
                True,
                reason="below_min_notional",
                tradable_size=tradable,
                total_size=total,
                available_size=available,
                price=px,
            )

    return ExitResidualDecision(
        False,
        tradable_size=tradable,
        total_size=total,
        available_size=available,
        price=px,
    )


def _same_symbol(left: Any, right: Any) -> bool:
    return normalize_strategy_symbol(str(left or "")) == normalize_strategy_symbol(str(right or ""))


def _latest_exit_intent(strategy_id: int, symbol: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT symbol, type AS signal_type, 'trade' AS source, id, created_at, '' AS status
            FROM qd_strategy_trades
            WHERE strategy_id = %s
            ORDER BY id DESC
            LIMIT 100
            """,
            (int(strategy_id),),
        )
        trades = cur.fetchall() or []
        cur.execute(
            """
            SELECT symbol, signal_type, 'pending_order' AS source, id, created_at, status
            FROM pending_orders
            WHERE strategy_id = %s
            ORDER BY id DESC
            LIMIT 100
            """,
            (int(strategy_id),),
        )
        orders = cur.fetchall() or []
        cur.close()

    candidates = []
    for row in list(trades) + list(orders):
        item = dict(row)
        if not _same_symbol(item.get("symbol"), symbol):
            continue
        candidates.append(item)
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: item.get("created_at") or 0)
    signal_type = str(latest.get("signal_type") or "").strip().lower()
    if not (signal_type.startswith("close_") or signal_type.startswith("reduce_")):
        return None
    return latest


def _active_order_exists(strategy_id: int, symbol: str) -> bool:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT symbol, status
            FROM pending_orders
            WHERE strategy_id = %s
              AND status IN ('pending', 'processing', 'sent', 'syncing', 'reconciling')
            """,
            (int(strategy_id),),
        )
        rows = cur.fetchall() or []
        cur.close()
    return any(
        _same_symbol(dict(row).get("symbol"), symbol)
        and str(dict(row).get("status") or "").strip().lower() in _ACTIVE_ORDER_STATUSES
        for row in rows
    )


def _has_single_owner(position: Mapping[str, Any]) -> bool:
    credential_id = int(position.get("credential_id") or 0)
    symbol = str(position.get("symbol_canonical") or position.get("symbol") or "")
    if credential_id <= 0 or not symbol:
        return False
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT symbol_canonical, symbol
            FROM qd_strategy_positions
            WHERE credential_id = %s
              AND market_type = 'spot'
              AND size > 0
            """,
            (credential_id,),
        )
        rows = cur.fetchall() or []
        cur.close()
    owners = sum(1 for row in rows if _same_symbol(dict(row).get("symbol_canonical") or dict(row).get("symbol"), symbol))
    return owners == 1


def _set_state(position_id: int, decision: ExitResidualDecision, evidence: Mapping[str, Any]) -> None:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE qd_strategy_positions
            SET position_state = 'exit_residual',
                tradable_size = %s,
                residual_reason = %s,
                residual_evidence_json = %s::jsonb,
                residual_classified_at = NOW(),
                updated_at = NOW()
            WHERE id = %s AND size > 0
            """,
            (
                float(decision.tradable_size or 0.0),
                str(decision.reason or ""),
                json.dumps(dict(evidence), separators=(",", ":"), default=str),
                int(position_id),
            ),
        )
        db.commit()
        cur.close()


def _clear_state(position_id: int) -> None:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE qd_strategy_positions
            SET position_state = 'open',
                tradable_size = 0,
                residual_reason = '',
                residual_evidence_json = '{}'::jsonb,
                residual_classified_at = NULL,
                updated_at = NOW()
            WHERE id = %s AND position_state = 'exit_residual'
            """,
            (int(position_id),),
        )
        db.commit()
        cur.close()


def reconcile_spot_exit_residuals(
    *,
    strategy_id: int,
    exchange_id: str,
    client: Any,
    snapshot_from_cache: bool,
) -> int:
    """Persist exit-residual state from a fresh, strict exchange snapshot."""
    if snapshot_from_cache or client is None:
        return 0
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT *
            FROM qd_strategy_positions
            WHERE strategy_id = %s
              AND market_type = 'spot'
              AND side = 'long'
              AND size > 0
            """,
            (int(strategy_id),),
        )
        positions = [dict(row) for row in (cur.fetchall() or [])]
        cur.close()

    changed = 0
    for position in positions:
        position_id = int(position.get("id") or 0)
        symbol = str(position.get("symbol_canonical") or position.get("symbol") or "")
        if position_id <= 0 or not symbol:
            continue
        active_order = _active_order_exists(int(strategy_id), symbol)
        exit_intent = None if active_order else _latest_exit_intent(int(strategy_id), symbol)
        if active_order or exit_intent is None or not _has_single_owner(position):
            if str(position.get("position_state") or "open") == "exit_residual" and active_order:
                _clear_state(position_id)
                changed += 1
            continue

        try:
            holding = get_spot_base_holding(
                client,
                symbol=symbol,
                strict=True,
                require_available=True,
            )
            rules = get_instrument_rules_provider().get_rules(
                symbol,
                exchange_id=str(exchange_id or ""),
                market_type="spot",
                client=client,
            )
            price = fetch_spot_last_price(client, symbol=symbol)
            decision = evaluate_exit_residual(
                local_size=float(position.get("size") or 0.0),
                total_size=float(holding.get("total") or 0.0),
                available_size=float(holding.get("available") or 0.0),
                price=price,
                rules=rules,
            )
        except Exception as exc:
            logger.debug(
                "Exit residual verification skipped strategy=%s symbol=%s: %s",
                strategy_id,
                symbol,
                exc,
            )
            continue

        current_state = str(position.get("position_state") or "open")
        if decision.classified:
            evidence = {
                "version": 1,
                "decision": asdict(decision),
                "rules": rules.metadata(),
                "exit_intent": {
                    "source": exit_intent.get("source"),
                    "id": exit_intent.get("id"),
                    "signal_type": exit_intent.get("signal_type"),
                    "status": exit_intent.get("status"),
                },
            }
            _set_state(position_id, decision, evidence)
            if current_state != "exit_residual":
                changed += 1
        elif current_state == "exit_residual":
            _clear_state(position_id)
            changed += 1
    return changed
