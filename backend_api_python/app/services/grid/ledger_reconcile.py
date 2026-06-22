"""Reconcile grid strategy ledger (L2/L3) against exchange holdings."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.utils.logger import get_logger

logger = get_logger(__name__)

_INITIAL_REASONS = ("grid_initial_long", "grid_initial_short")
_EPS = 1e-8


def fetch_initial_market_trades(strategy_id: int) -> List[Dict[str, Any]]:
    sid = int(strategy_id)
    with __import__("app.utils.db", fromlist=["get_db_connection"]).get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, strategy_id, symbol, type, price, amount, close_reason, created_at
            FROM qd_strategy_trades
            WHERE strategy_id = %s
              AND close_reason = ANY(%s)
            ORDER BY id ASC
            """,
            (sid, list(_INITIAL_REASONS)),
        )
        rows = cur.fetchall() or []
        cur.close()
    return [dict(r) for r in rows]


def fetch_ledger_positions(strategy_id: int) -> List[Dict[str, Any]]:
    sid = int(strategy_id)
    with __import__("app.utils.db", fromlist=["get_db_connection"]).get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, strategy_id, symbol, side, size, entry_price
            FROM qd_strategy_positions
            WHERE strategy_id = %s
            ORDER BY id ASC
            """,
            (sid,),
        )
        rows = cur.fetchall() or []
        cur.close()
    return [dict(r) for r in rows]


def exchange_is_flat(exchange_snapshot: Optional[Dict[str, Any]]) -> bool:
    snap = exchange_snapshot if isinstance(exchange_snapshot, dict) else {}
    long_sz = float(snap.get("long_size") or 0.0)
    short_sz = float(snap.get("short_size") or 0.0)
    return long_sz <= _EPS and short_sz <= _EPS


def ledger_has_open_legs(positions: List[Dict[str, Any]]) -> bool:
    for row in positions or []:
        if float(row.get("size") or 0.0) > _EPS:
            return True
    return False


def should_clear_phantom_ledger(
    *,
    exchange_snapshot: Optional[Dict[str, Any]],
    ledger_positions: List[Dict[str, Any]],
    initial_trades: List[Dict[str, Any]],
    force: bool = False,
) -> Tuple[bool, str]:
    if not initial_trades and not ledger_has_open_legs(ledger_positions):
        return False, "no initial trades or ledger legs to clear"
    if force:
        return True, "forced by operator"
    if exchange_is_flat(exchange_snapshot) and ledger_has_open_legs(ledger_positions):
        return True, "exchange flat but strategy ledger shows open legs"
    if exchange_is_flat(exchange_snapshot) and initial_trades:
        return True, "exchange flat but initial market trades exist in ledger"
    return False, "exchange still holds positions; use --force to clear ledger anyway"


def clear_phantom_grid_ledger(
    strategy_id: int,
    *,
    apply: bool = False,
    force: bool = False,
    reset_initial_flag: bool = True,
    sanitize_neutral_initial_pct: bool = True,
) -> Dict[str, Any]:
    """
    Remove phantom grid initial-market ledger rows when exchange is flat.

    Dry-run by default (``apply=False``). Returns a report dict.
    """
    from app.services.exchange_execution import load_strategy_configs, resolve_exchange_config
    from app.services.grid.config import sanitize_grid_bot_params
    from app.services.grid.exchange_requirements import fetch_exchange_dual_leg_snapshot
    from app.services.grid.runtime_state import persist_grid_resting_state
    from app.services.live_trading.factory import create_client
    from app.services.live_trading.records import rebuild_positions_from_trades
    from app.utils.db import get_db_connection

    sid = int(strategy_id)
    report: Dict[str, Any] = {
        "strategy_id": sid,
        "apply": bool(apply),
        "cleared": False,
        "reason": "",
    }

    sc = load_strategy_configs(sid) or {}
    if not sc:
        report["reason"] = "strategy not found"
        return report

    tc = sc.get("trading_config") if isinstance(sc.get("trading_config"), dict) else {}
    bot_type = str(sc.get("bot_type") or tc.get("bot_type") or "").strip().lower()
    if bot_type != "grid":
        report["reason"] = f"not a grid strategy (bot_type={bot_type or 'unknown'})"
        return report

    symbol = str(tc.get("symbol") or sc.get("symbol") or "").strip()
    user_id = int(sc.get("user_id") or 1)
    ex_cfg = resolve_exchange_config(sc.get("exchange_config") or {}, user_id=user_id)
    mt = str(tc.get("market_type") or "swap").strip().lower()

    exchange_snapshot = None
    try:
        client = create_client(ex_cfg, market_type=mt)
        exchange_snapshot = fetch_exchange_dual_leg_snapshot(
            client,
            symbol=symbol,
            market_type=mt,
            exchange_config=ex_cfg,
        )
    except Exception as e:
        logger.warning("ledger reconcile exchange snapshot sid=%s: %s", sid, e)
        report["exchange_snapshot_error"] = str(e)

    initial_trades = fetch_initial_market_trades(sid)
    ledger_positions = fetch_ledger_positions(sid)
    ok, reason = should_clear_phantom_ledger(
        exchange_snapshot=exchange_snapshot,
        ledger_positions=ledger_positions,
        initial_trades=initial_trades,
        force=force,
    )

    report["exchange_snapshot"] = exchange_snapshot
    report["initial_trades"] = initial_trades
    report["ledger_positions"] = ledger_positions
    report["reason"] = reason

    if not ok:
        return report

    report["would_delete_trade_ids"] = [int(t.get("id")) for t in initial_trades if t.get("id")]
    report["would_delete_position_ids"] = [int(p.get("id")) for p in ledger_positions if p.get("id")]

    if not apply:
        report["cleared"] = False
        report["dry_run"] = True
        return report

    deleted_trades = 0
    if initial_trades:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                DELETE FROM qd_strategy_trades
                WHERE strategy_id = %s AND close_reason = ANY(%s)
                """,
                (sid, list(_INITIAL_REASONS)),
            )
            deleted_trades = int(cur.rowcount or 0)
            db.commit()
            cur.close()

    deleted_positions = 0
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("DELETE FROM qd_strategy_positions WHERE strategy_id = %s", (sid,))
        deleted_positions = int(cur.rowcount or 0)
        db.commit()
        cur.close()

    rebuilt = bool(rebuild_positions_from_trades(sid))

    if reset_initial_flag:
        persist_grid_resting_state(sid, {"initial_market_done": False})

    if sanitize_neutral_initial_pct:
        bp = tc.get("bot_params") if isinstance(tc.get("bot_params"), dict) else {}
        sanitized = sanitize_grid_bot_params(bp)
        if sanitized != bp:
            tc = dict(tc)
            tc["bot_params"] = sanitized
            import json

            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    "UPDATE qd_strategies_trading SET trading_config = %s WHERE id = %s",
                    (json.dumps(tc, ensure_ascii=False), sid),
                )
                db.commit()
                cur.close()
            report["sanitized_initial_position_pct"] = True

    report.update(
        {
            "cleared": True,
            "deleted_trades": deleted_trades,
            "deleted_positions": deleted_positions,
            "rebuilt_from_remaining_trades": rebuilt,
        }
    )
    return report
