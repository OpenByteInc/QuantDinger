"""
Sync qd_strategy_positions (L3 strategy ledger) from exchange snapshots.

Only symbols in ``strategy_allowed_symbols`` are written — never the full
credential wallet (multi-strategy safe).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Set

from app.services.live_trading.leg_context import credential_id_from_exchange_config
from app.services.live_trading.records import (
    _delete_position,
    lookup_exchange_entry_price,
    lookup_exchange_side_qty,
    normalize_strategy_symbol,
    strategy_allowed_symbols,
    upsert_position,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_FILL_LEDGER_BOT_TYPES = frozenset({"grid", "martingale", "dca"})


def strategy_uses_fill_ledger(strategy_config: Dict[str, Any]) -> bool:
    """
    Strategies whose L3 ledger is maintained by fill application, not exchange
    reconciliation. Exchange sync can delete rows when API/cache lag behind fills.
    """
    sc = strategy_config if isinstance(strategy_config, dict) else {}
    tc = sc.get("trading_config") if isinstance(sc.get("trading_config"), dict) else {}
    bot_type = str(
        sc.get("bot_type") or tc.get("bot_type") or ""
    ).strip().lower()
    if bot_type == "grid":
        return True
    stype = str(sc.get("strategy_type") or "").strip()
    if stype != "ScriptStrategy":
        return False
    if bot_type in _FILL_LEDGER_BOT_TYPES:
        return False
    return True


def apply_exchange_snapshot_to_strategy_ledger(
    *,
    strategy_id: int,
    strategy_config: Dict[str, Any],
    exch_size: Dict[str, Dict[str, float]],
    exch_entry_price: Dict[str, Dict[str, float]],
    exch_inst_id: Optional[Dict[str, Dict[str, str]]] = None,
    market_type: str = "swap",
    exchange_config: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Upsert or delete L3 rows for allowed symbols from an exchange snapshot.

    Returns the number of non-flat legs written.
    """
    sid = int(strategy_id)
    if sid <= 0:
        return 0

    allowed = strategy_allowed_symbols(strategy_config or {})
    if not allowed:
        return 0

    mt = str(market_type or "swap").strip().lower()
    if mt in ("futures", "future", "perp", "perpetual"):
        mt = "swap"

    cred = 0
    if isinstance(exchange_config, dict):
        cred = int(credential_id_from_exchange_config(exchange_config) or 0)

    inst_map = exch_inst_id or {}
    eps = 1e-12
    written = 0

    canon_symbols: Set[str] = set()
    for raw in allowed:
        norm = normalize_strategy_symbol(str(raw or "").strip())
        if norm:
            canon_symbols.add(norm)

    for sym in sorted(canon_symbols):
        for side in ("long", "short"):
            qty = lookup_exchange_side_qty(exch_size, sym, side)
            if qty <= eps:
                _delete_position(sid, sym, side)
                continue

            ep = lookup_exchange_entry_price(exch_entry_price, sym, side)
            iid = ""
            for key in (sym, sym.upper(), sym.replace("/", "")):
                leg_map = inst_map.get(key) or {}
                iid = str(leg_map.get(side) or "").strip()
                if iid:
                    break

            mark = ep if ep > 0 else 0.0
            upsert_position(
                strategy_id=sid,
                symbol=sym,
                side=side,
                size=float(qty),
                entry_price=float(ep or 0.0),
                current_price=mark,
                market_type=mt,
                credential_id=cred,
                inst_id=iid,
            )
            written += 1

    return written


def sync_strategy_positions_from_exchange(strategy_id: int) -> None:
    """On-demand exchange → L3 sync for one strategy (positions API / startup)."""
    from app.services.exchange_execution import load_strategy_configs
    from app.services.pending_order_worker import PendingOrderWorker

    sc = load_strategy_configs(int(strategy_id))
    if strategy_uses_fill_ledger(sc):
        logger.debug(
            "position sync skipped for strategy %s: fill-ledger strategy",
            strategy_id,
        )
        return

    worker = PendingOrderWorker()
    worker._sync_positions_best_effort(target_strategy_id=int(strategy_id))
