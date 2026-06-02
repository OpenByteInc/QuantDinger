"""
Net realised P&L for live trade rows — gross profit minus open + close commissions.

``qd_strategy_trades`` stores one row per fill. Opens carry ``commission`` only;
closes carry ``profit`` (gross, price diff × qty) plus close ``commission``.
The UI should show net P&L on close rows by also allocating the matched open
leg fee (FIFO per symbol + side).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.live_trading.records import normalize_strategy_symbol
from app.utils.trade_close_reason import is_exit_trade_type


def _leg_side(trade_type: str) -> str:
    t = str(trade_type or "").strip().lower()
    if "long" in t:
        return "long"
    if "short" in t:
        return "short"
    return ""


def _symbol_key(row: Dict[str, Any]) -> str:
    raw = row.get("symbol_canonical") or row.get("symbol") or ""
    return normalize_strategy_symbol(str(raw)) or str(raw or "").strip().upper()


def _sort_key(row: Dict[str, Any]) -> Tuple[int, int]:
    ts = row.get("created_at")
    if isinstance(ts, (int, float)):
        ts_i = int(ts)
    elif hasattr(ts, "timestamp"):
        try:
            ts_i = int(ts.timestamp())
        except Exception:
            ts_i = 0
    else:
        ts_i = 0
    try:
        tid = int(row.get("id") or 0)
    except Exception:
        tid = 0
    return ts_i, tid


def allocate_open_commissions_fifo(trades: List[Dict[str, Any]]) -> Dict[int, float]:
    """
    Walk trades chronologically and return {trade_id: allocated_open_commission}
    for each exit row.
    """
    out: Dict[int, float] = {}
    if not trades:
        return out

    lots: Dict[Tuple[str, str], List[List[float]]] = {}
    ordered = sorted(trades, key=_sort_key)

    for row in ordered:
        ttype = str(row.get("type") or "")
        side = _leg_side(ttype)
        if not side:
            continue
        key = (_symbol_key(row), side)
        try:
            amount = float(row.get("amount") or 0.0)
        except Exception:
            amount = 0.0
        try:
            commission = float(row.get("commission") or 0.0)
        except Exception:
            commission = 0.0

        if not is_exit_trade_type(ttype):
            if amount > 1e-12:
                comm_per_unit = commission / amount if amount > 0 else 0.0
                lots.setdefault(key, []).append([amount, comm_per_unit])
            continue

        if row.get("profit") is None:
            continue

        try:
            trade_id = int(row.get("id") or 0)
        except Exception:
            trade_id = 0

        close_qty = amount
        open_comm = 0.0
        queue = lots.get(key, [])
        idx = 0
        while close_qty > 1e-12 and idx < len(queue):
            rem, cpu = queue[idx]
            if rem <= 1e-12:
                idx += 1
                continue
            take = min(rem, close_qty)
            open_comm += take * cpu
            queue[idx][0] = rem - take
            close_qty -= take
            if queue[idx][0] <= 1e-12:
                idx += 1
        lots[key] = [lot for lot in queue if lot[0] > 1e-12]
        if trade_id > 0:
            out[trade_id] = float(open_comm)
    return out


def net_realized_pnl(
    trade: Dict[str, Any],
    *,
    open_commission: float = 0.0,
) -> Optional[float]:
    """Gross profit minus close commission minus allocated open commission."""
    if trade.get("profit") is None:
        return None
    try:
        gross = float(trade.get("profit_gross") if trade.get("profit_gross") is not None else trade.get("profit") or 0.0)
    except Exception:
        gross = 0.0
    try:
        close_comm = float(trade.get("close_commission") if trade.get("close_commission") is not None else trade.get("commission") or 0.0)
    except Exception:
        close_comm = 0.0
    return float(gross) - close_comm - float(open_commission or 0.0)


def enrich_trades_net_pnl(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Mutate trade dicts in place:
      - exit rows: profit_gross, open_commission_allocated, net_pnl; profit -> net
      - entry rows: unchanged (profit stays None)
    """
    if not trades:
        return trades

    open_map = allocate_open_commissions_fifo(trades)
    for row in trades:
        ttype = str(row.get("type") or "")
        if not is_exit_trade_type(ttype) or row.get("profit") is None:
            continue
        try:
            trade_id = int(row.get("id") or 0)
        except Exception:
            trade_id = 0
        open_comm = float(open_map.get(trade_id, 0.0) if trade_id > 0 else 0.0)
        try:
            gross = float(row.get("profit") or 0.0)
        except Exception:
            gross = 0.0
        try:
            close_comm = float(row.get("commission") or 0.0)
        except Exception:
            close_comm = 0.0
        net = gross - close_comm - open_comm
        row["profit_gross"] = gross
        row["open_commission_allocated"] = round(open_comm, 8)
        row["close_commission"] = close_comm
        row["total_commission"] = round(close_comm + open_comm, 8)
        row["net_pnl"] = round(net, 8)
        row["profit"] = round(net, 8)
        try:
            gmp = row.get("grid_matched_profit")
            if gmp is not None and abs(float(gmp) - gross) <= max(1e-8, abs(gross) * 1e-6):
                row["grid_matched_profit"] = round(net, 8)
        except Exception:
            pass
    return trades


def net_pnl_for_equity_step(trade: Dict[str, Any]) -> float:
    """
    Single-row equity delta (opens: -commission; closes: net realised P&L).

    Prefer enriched rows; falls back to gross profit minus close commission only.
    """
    if trade.get("profit") is not None:
        if trade.get("net_pnl") is not None:
            try:
                return float(trade.get("net_pnl"))
            except Exception:
                pass
        open_comm = float(trade.get("open_commission_allocated") or 0.0)
        val = net_realized_pnl(trade, open_commission=open_comm)
        return float(val or 0.0)
    try:
        return -float(trade.get("commission") or 0.0)
    except Exception:
        return 0.0
