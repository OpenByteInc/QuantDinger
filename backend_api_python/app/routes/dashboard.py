"""
Dashboard APIs (local-first).

Endpoints:
- GET /api/dashboard/summary
- GET /api/dashboard/pendingOrders?page=1&pageSize=20

Notes:
- Paper mode: no real trading execution. Metrics are best-effort based on local DB tables.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Tuple

from flask import g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint

from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.auth import login_required

logger = get_logger(__name__)

dashboard_blp = Blueprint("dashboard", __name__)


def _safe_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _net_trade_pnl(t: Dict[str, Any]) -> float:
    """Realised P&L after open + close commissions (USDT-margined pairs)."""
    from app.utils.trade_net_pnl import net_pnl_for_equity_step

    return float(net_pnl_for_equity_step(t))


def _format_datetime(dt: Any) -> Any:
    """Convert a datetime to a UTC ISO 8601 string for the frontend.

    Naive datetimes from the DB are interpreted in the server's wall-clock
    time zone (container ``TZ`` env var) — the previous implementation
    assumed UTC, which is only correct on UTC deployments and silently
    shifted timestamps by 8 hours on the default ``Asia/Shanghai`` setup.
    """
    if dt is None:
        return None
    if hasattr(dt, 'isoformat'):
        from app.utils.timeutil import to_utc_iso
        return to_utc_iso(dt)
    return dt


def _safe_json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return default
    s = value.strip()
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x or "").strip()]
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        # allow comma-separated
        if "," in s:
            return [p.strip() for p in s.split(",") if p.strip()]
        return [s]
    return []


def _is_bot_strategy(row: Dict[str, Any]) -> bool:
    try:
        mode = str((row or {}).get("strategy_mode") or "").strip().lower()
        return mode == "bot"
    except Exception:
        return False


def _strategy_bucket(row: Dict[str, Any]) -> str:
    """Classify a strategy row into signal (indicator) / script / bot."""
    if _is_bot_strategy(row):
        return "bot"
    mode = str((row or {}).get("strategy_mode") or "").strip().lower()
    if mode == "script":
        return "script"
    st = str((row or {}).get("strategy_type") or "").strip()
    if st == "ScriptStrategy":
        return "script"
    return "signal"


from app.utils.pnl import calc_pnl_percent, calc_unrealized_pnl


def _compute_performance_stats(trades: List[Dict[str, Any]], initial_capital: float = 0.0) -> Dict[str, Any]:
    """
    Compute performance statistics from trade history.

    Important: ``qd_strategy_trades`` stores one row per trade *event*, not per
    round-trip. There are two kinds of rows:
      * Opens / adds (``open_long``, ``open_short``, ``add_long`` …) — these
        carry ``profit = NULL`` because no P&L is realised yet.
      * Closes / reduces — these carry a signed numeric ``profit``.

    Historically the win-rate denominator here was ``len(trades)``, which is
    the count of *all* events including opens. That made a strategy with
    "2 wins / 0 losses" display as ``2 / 11 events ≈ 18.2%`` win rate. The
    correct denominator is the number of *decided* (closed and non-breakeven)
    trades. To make every downstream metric consistent we now also restrict
    profit-factor, averages, max-win/loss and the equity curve to the closing
    trades, and report ``total_trades`` as the count of closing trades — open
    rows are bookkeeping noise as far as performance scoring is concerned.

    Args:
        trades: List of trade records (mix of opens and closes — both fine).
        initial_capital: Initial capital for the equity curve; used to anchor
            the drawdown percentage when the cumulative profit is negative.

    Returns: {
        total_trades, winning_trades, losing_trades, win_rate,
        total_profit, total_loss, profit_factor,
        avg_win, avg_loss, avg_trade,
        max_win, max_loss, max_drawdown, max_drawdown_pct,
        best_day, worst_day
    }
    """
    empty_stats = {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "win_rate": 0.0,
        "total_profit": 0.0,
        "total_loss": 0.0,
        "profit_factor": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "avg_trade": 0.0,
        "max_win": 0.0,
        "max_loss": 0.0,
        "max_drawdown": 0.0,
        "max_drawdown_pct": 0.0,
        "best_day": 0.0,
        "worst_day": 0.0,
    }
    if not trades:
        return empty_stats

    # Keep only rows that carry a realised P&L. ``profit`` being None is the
    # canonical "this is an open / add event" signal in qd_strategy_trades;
    # rows where the column is missing entirely (legacy / migrated data) get
    # the same treatment. Explicit ``profit = 0`` rows are kept — they are
    # break-even closes and still count toward total_trades / avg_trade, just
    # not toward wins or losses.
    closing_trades = [t for t in trades if t.get("profit") is not None]
    total_trades = len(closing_trades)
    if total_trades == 0:
        return empty_stats

    profits = [_net_trade_pnl(t) for t in closing_trades]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]

    winning_trades = len(wins)
    losing_trades = len(losses)
    # The denominator must exclude break-even closes (profit == 0) because
    # they are neither a win nor a loss and would otherwise drag the rate
    # toward an arbitrary direction depending on rounding.
    decided_trades = winning_trades + losing_trades
    win_rate = (winning_trades / decided_trades * 100) if decided_trades > 0 else 0.0

    total_profit = sum(wins) if wins else 0.0
    total_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = (total_profit / total_loss) if total_loss > 0 else (total_profit if total_profit > 0 else 0.0)

    avg_win = (total_profit / winning_trades) if winning_trades > 0 else 0.0
    avg_loss = (total_loss / losing_trades) if losing_trades > 0 else 0.0
    avg_trade = sum(profits) / total_trades if total_trades > 0 else 0.0

    max_win = max(profits) if profits else 0.0
    max_loss = min(profits) if profits else 0.0

    # Calculate max drawdown from equity curve (initial_capital + cumulative profit)
    # This ensures proper percentage calculation even when cumulative profit is negative
    cumulative_profit = 0.0
    equity_curve = []
    for p in profits:
        cumulative_profit += p
        equity = initial_capital + cumulative_profit
        equity_curve.append(equity)

    # Calculate max drawdown from equity curve
    peak_equity = initial_capital if initial_capital > 0 else (equity_curve[0] if equity_curve else 0.0)
    max_drawdown = 0.0
    for equity in equity_curve:
        if equity > peak_equity:
            peak_equity = equity
        # Drawdown is the drop from peak
        drawdown = peak_equity - equity
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    # Calculate drawdown percentage: drawdown / peak_equity * 100
    # If peak_equity is 0 or very small, use a fallback calculation
    if peak_equity > 0:
        max_drawdown_pct = (max_drawdown / peak_equity * 100)
    elif initial_capital > 0:
        # Fallback: use initial capital as baseline
        max_drawdown_pct = (max_drawdown / initial_capital * 100) if initial_capital > 0 else 0.0
    else:
        # Last resort: if no initial capital and peak is 0, calculate from cumulative profit peak
        cumulative = []
        acc = 0.0
        for p in profits:
            acc += p
            cumulative.append(acc)
        peak_profit = max(cumulative) if cumulative else 0.0
        if peak_profit > 0:
            max_drawdown_pct = (max_drawdown / peak_profit * 100)
        else:
            max_drawdown_pct = 0.0
    
    # Cap drawdown percentage at reasonable maximum (e.g., 10000%) to avoid display issues
    if max_drawdown_pct > 10000:
        max_drawdown_pct = 10000.0

    # Best/worst day — only closing trades carry realised P&L, so iterate over
    # the same filtered set we used above. Open events with profit=NULL would
    # otherwise create empty "0 P&L" days for the date they were opened, which
    # is technically harmless but masks days that legitimately had no trades.
    day_profits: Dict[str, float] = {}
    for t in closing_trades:
        ts = _safe_int(t.get("created_at"), 0)
        if ts <= 0:
            continue
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        profit = _net_trade_pnl(t)
        day_profits[day] = day_profits.get(day, 0.0) + profit

    best_day = max(day_profits.values()) if day_profits else 0.0
    worst_day = min(day_profits.values()) if day_profits else 0.0

    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": round(win_rate, 2),
        "total_profit": round(total_profit, 2),
        "total_loss": round(total_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_trade": round(avg_trade, 2),
        "max_win": round(max_win, 2),
        "max_loss": round(max_loss, 2),
        "max_drawdown": round(max_drawdown, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "best_day": round(best_day, 2),
        "worst_day": round(worst_day, 2),
    }


def _compute_strategy_stats(trades: List[Dict[str, Any]], strategies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Compute per-strategy statistics.
    Only includes strategies that still exist (not deleted).
    """
    # Build set of existing strategy IDs
    existing_strategy_ids: set = set()
    sid_to_name: Dict[int, str] = {}
    sid_to_capital: Dict[int, float] = {}
    sid_to_bucket: Dict[int, str] = {}
    for s in strategies:
        sid = _safe_int(s.get("id"), 0)
        if sid > 0:
            existing_strategy_ids.add(sid)
            sid_to_name[sid] = str(s.get("strategy_name") or f"Strategy_{sid}")
            sid_to_capital[sid] = _safe_float(s.get("initial_capital"), 0.0)
            sid_to_bucket[sid] = _strategy_bucket(s)

    # Group trades by strategy (only for existing strategies)
    sid_to_trades: Dict[int, List[Dict[str, Any]]] = {}
    for t in trades:
        sid = _safe_int(t.get("strategy_id"), 0)
        # Skip trades from deleted strategies
        if sid not in existing_strategy_ids:
            continue
        if sid not in sid_to_trades:
            sid_to_trades[sid] = []
        sid_to_trades[sid].append(t)

    result = []
    for sid, strades in sid_to_trades.items():
        capital = sid_to_capital.get(sid, 0.0)
        stats = _compute_performance_stats(strades, initial_capital=capital)
        total_pnl = sum(_net_trade_pnl(t) for t in strades)
        roi = (total_pnl / capital * 100) if capital > 0 else 0.0

        result.append({
            "strategy_id": sid,
            "strategy_name": sid_to_name.get(sid, f"Strategy_{sid}"),
            "strategy_bucket": sid_to_bucket.get(sid, "signal"),
            "total_trades": stats["total_trades"],
            "win_rate": stats["win_rate"],
            "profit_factor": stats["profit_factor"],
            "total_pnl": round(total_pnl, 2),
            "roi": round(roi, 2),
            "max_drawdown": stats["max_drawdown"],
        })

    # Sort by total PnL descending
    result.sort(key=lambda x: x.get("total_pnl", 0), reverse=True)
    return result


@dashboard_blp.route("/summary", methods=["GET"])
@login_required
def summary():
    """
    Return dashboard summary used by the frontend dashboard view (private Vue repo).
    """
    try:
        user_id = g.user_id
        
        # Strategy counts (filtered by user_id)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, strategy_name, strategy_type, status, initial_capital, trading_config, strategy_mode
                FROM qd_strategies_trading
                WHERE user_id = ?
                """,
                (user_id,)
            )
            strategies = cur.fetchall() or []
            cur.close()

        running = [s for s in strategies if (s.get("status") or "").strip().lower() == "running"]
        running_strategy_count = len(running)
        running_indicator_count = len([s for s in running if _strategy_bucket(s) == "signal"])
        running_script_count = len([s for s in running if _strategy_bucket(s) == "script"])
        running_bot_count = len([s for s in running if _strategy_bucket(s) == "bot"])
        # Backward-compatible alias: previously only counted running indicator strategies.
        indicator_strategy_count = running_indicator_count

        # "AI strategies" in dashboard card: count strategies that enabled AI analysis/filtering.
        # This aligns with the UI toggle `enable_ai_filter` in trading_config.
        def _truthy(v: Any) -> bool:
            if v is True:
                return True
            if isinstance(v, (int, float)) and float(v) == 1:
                return True
            if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "y", "on"):
                return True
            return False

        ai_enabled_strategy_count = 0
        for s in strategies:
            tc = _safe_json_loads(s.get("trading_config"), {}) or {}
            if isinstance(tc, dict) and _truthy(tc.get("enable_ai_filter")):
                ai_enabled_strategy_count += 1

        # Positions (best-effort, filtered by user_id)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT p.*, s.strategy_name, s.initial_capital, s.leverage, s.market_type
                FROM qd_strategy_positions p
                INNER JOIN qd_strategies_trading s ON s.id = p.strategy_id
                WHERE p.user_id = ?
                  AND s.user_id = ?
                ORDER BY p.updated_at DESC
                """,
                (user_id, user_id)
            )
            rows = cur.fetchall() or []
            cur.close()

        current_positions: List[Dict[str, Any]] = []
        total_unrealized_pnl = 0.0
        for r in rows:
            pnl = calc_unrealized_pnl(
                side=str(r.get("side") or ""),
                entry_price=float(r.get("entry_price") or 0.0),
                current_price=float(r.get("current_price") or 0.0),
                size=float(r.get("size") or 0.0),
            )
            pct = calc_pnl_percent(
                float(r.get("entry_price") or 0.0),
                float(r.get("size") or 0.0),
                pnl,
                leverage=float(r.get("leverage") or 1.0),
                market_type=str(r.get("market_type") or "spot"),
            )
            total_unrealized_pnl += float(pnl)
            current_positions.append(
                {
                    **r,
                    "strategy_name": r.get("strategy_name") or "",
                    "unrealized_pnl": float(pnl),
                    "pnl_percent": float(pct),
                }
            )

        # Recent trades (best-effort, filtered by user_id)
        # Also compute all-time trade count for dashboard top cards. We count
        # only *closing* trades (``profit IS NOT NULL``) so this card agrees
        # with the per-strategy "Trades" column and with the win-rate
        # denominator — otherwise users see e.g. "11 trades, 18% win rate"
        # on the top card while a strategy shows "2 trades, 100% win rate",
        # and naturally assume one of them is broken.
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT COUNT(1) AS cnt
                FROM qd_strategy_trades t
                INNER JOIN qd_strategies_trading s ON s.id = t.strategy_id
                WHERE t.user_id = ?
                  AND s.user_id = ?
                  AND t.profit IS NOT NULL
                """,
                (user_id, user_id)
            )
            total_trades_all = int((cur.fetchone() or {}).get("cnt") or 0)
            cur.execute(
                """
                SELECT COALESCE(SUM(COALESCE(t.profit, 0) - COALESCE(t.commission, 0)), 0) AS total
                FROM qd_strategy_trades t
                INNER JOIN qd_strategies_trading s ON s.id = t.strategy_id
                WHERE t.user_id = ?
                  AND s.user_id = ?
                  AND t.profit IS NOT NULL
                """,
                (user_id, user_id)
            )
            total_realized_pnl_all = float((cur.fetchone() or {}).get("total") or 0.0)
            cur.close()

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT t.*, s.strategy_name
                FROM qd_strategy_trades t
                INNER JOIN qd_strategies_trading s ON s.id = t.strategy_id
                WHERE t.user_id = ?
                  AND s.user_id = ?
                ORDER BY t.created_at DESC
                LIMIT 500
                """,
                (user_id, user_id)
            )
            recent_trades_raw = cur.fetchall() or []
            cur.close()
        
        # Convert datetime to timestamp for frontend compatibility
        from datetime import timezone as _tz
        recent_trades = []
        for t in recent_trades_raw:
            trade = dict(t)
            ca = trade.get('created_at')
            if ca and hasattr(ca, 'timestamp'):
                if getattr(ca, 'tzinfo', None) is None:
                    ca = ca.replace(tzinfo=_tz.utc)
                trade['created_at'] = int(ca.timestamp())
            recent_trades.append(trade)

        # Total equity/pnl (best-effort) - calculate before performance stats for drawdown calculation
        total_initial_capital = 0.0
        for s in strategies:
            try:
                total_initial_capital += float(s.get("initial_capital") or 0.0)
            except Exception:
                pass

        # Compute performance statistics with initial capital for proper drawdown calculation
        from app.utils.trade_net_pnl import enrich_trades_net_pnl

        enrich_trades_net_pnl(recent_trades)
        perf_stats = _compute_performance_stats(recent_trades, initial_capital=total_initial_capital)
        # For dashboard top card: show all-time total trade count (not limited by LIMIT 500).
        perf_stats["total_trades"] = int(total_trades_all)

        # Compute per-strategy statistics
        strategy_stats = _compute_strategy_stats(recent_trades, strategies)

        # All-time realized PnL for equity cards; recent_trades is capped at 500 rows.
        total_realized_pnl = float(total_realized_pnl_all)
        total_pnl = float(total_unrealized_pnl + total_realized_pnl)
        total_equity = float(total_initial_capital + total_pnl)

        # Daily PnL chart (uses realized profit field if present, otherwise 0)
        # Keep output stable even if profit is mostly empty.
        day_to_profit: Dict[str, float] = {}
        for trow in recent_trades:
            ts = _safe_int(trow.get("created_at"), 0)
            if ts <= 0:
                continue
            day = time.strftime("%Y-%m-%d", time.localtime(ts))
            p = _net_trade_pnl(trow)
            day_to_profit[day] = float(day_to_profit.get(day, 0.0) + p)
        daily_pnl_chart = [{"date": d, "profit": float(v)} for d, v in sorted(day_to_profit.items())]

        # Strategy performance pie (use unrealized pnl by strategy as best-effort)
        sid_to_unreal: Dict[int, float] = {}
        sid_to_name: Dict[int, str] = {}
        for p in current_positions:
            sid = _safe_int(p.get("strategy_id"), 0)
            sid_to_name[sid] = str(p.get("strategy_name") or f"Strategy_{sid}")
            sid_to_unreal[sid] = float(sid_to_unreal.get(sid, 0.0) + float(p.get("unrealized_pnl") or 0.0))
        strategy_pnl_chart = [{"name": sid_to_name[sid], "value": float(val)} for sid, val in sid_to_unreal.items()]

        # Monthly returns for heatmap
        month_to_profit: Dict[str, float] = {}
        for trow in recent_trades:
            ts = _safe_int(trow.get("created_at"), 0)
            if ts <= 0:
                continue
            month = time.strftime("%Y-%m", time.localtime(ts))
            p = _net_trade_pnl(trow)
            month_to_profit[month] = month_to_profit.get(month, 0.0) + p
        monthly_returns = [{"month": m, "profit": round(v, 2)} for m, v in sorted(month_to_profit.items())]

        # Hourly distribution
        hour_to_count: Dict[int, int] = {}
        hour_to_profit: Dict[int, float] = {}
        for trow in recent_trades:
            ts = _safe_int(trow.get("created_at"), 0)
            if ts <= 0:
                continue
            hour = int(time.strftime("%H", time.localtime(ts)))
            hour_to_count[hour] = hour_to_count.get(hour, 0) + 1
            hour_to_profit[hour] = hour_to_profit.get(hour, 0.0) + _net_trade_pnl(trow)
        hourly_distribution = [
            {"hour": h, "count": hour_to_count.get(h, 0), "profit": round(hour_to_profit.get(h, 0.0), 2)}
            for h in range(24)
        ]

        # Calendar data: organized by month for monthly calendar view
        # Format: { "2024-01": { "days": { "01": 123.45, "02": -50.0, ... }, "total": 500.0 }, ... }
        import calendar as cal_module
        from datetime import datetime, timedelta

        calendar_data: Dict[str, Dict[str, Any]] = {}
        for d, p in day_to_profit.items():
            try:
                dt = datetime.strptime(d, "%Y-%m-%d")
                month_key = dt.strftime("%Y-%m")
                day_num = dt.strftime("%d")
                if month_key not in calendar_data:
                    # Get number of days in month
                    year, month = int(dt.strftime("%Y")), int(dt.strftime("%m"))
                    _, days_in_month = cal_module.monthrange(year, month)
                    # Get first day of month (0=Monday, 6=Sunday)
                    first_weekday = cal_module.monthrange(year, month)[0]
                    calendar_data[month_key] = {
                        "year": year,
                        "month": month,
                        "days_in_month": days_in_month,
                        "first_weekday": first_weekday,  # 0=Mon, 6=Sun
                        "days": {},
                        "total": 0.0,
                        "win_days": 0,
                        "lose_days": 0,
                    }
                calendar_data[month_key]["days"][day_num] = round(p, 2)
                calendar_data[month_key]["total"] = round(calendar_data[month_key]["total"] + p, 2)
                if p > 0:
                    calendar_data[month_key]["win_days"] += 1
                elif p < 0:
                    calendar_data[month_key]["lose_days"] += 1
            except Exception:
                pass

        # Convert to sorted list for frontend
        calendar_months = []
        for month_key in sorted(calendar_data.keys(), reverse=True):
            data = calendar_data[month_key]
            calendar_months.append({
                "month_key": month_key,
                **data
            })

        return jsonify(
            {
                "code": 1,
                "msg": "success",
                "data": {
                    "ai_strategy_count": int(ai_enabled_strategy_count),
                    "indicator_strategy_count": int(indicator_strategy_count),
                    "running_strategy_count": int(running_strategy_count),
                    "running_indicator_count": int(running_indicator_count),
                    "running_script_count": int(running_script_count),
                    "running_bot_count": int(running_bot_count),
                    "total_equity": round(total_equity, 2),
                    "total_pnl": round(total_pnl, 2),
                    "total_realized_pnl": round(total_realized_pnl, 2),
                    "total_unrealized_pnl": round(total_unrealized_pnl, 2),
                    # Performance KPIs
                    "performance": perf_stats,
                    # Strategy-level stats
                    "strategy_stats": strategy_stats,
                    # Chart data
                    "daily_pnl_chart": daily_pnl_chart,
                    "strategy_pnl_chart": strategy_pnl_chart,
                    "monthly_returns": monthly_returns,
                    "hourly_distribution": hourly_distribution,
                    "calendar_months": calendar_months,  # Monthly calendar data
                    # Lists
                    "recent_trades": recent_trades[:100],  # Limit for frontend
                    "current_positions": current_positions,
                },
            }
        )
    except Exception as e:
        logger.error(f"dashboard summary failed: {e}", exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500


@dashboard_blp.route("/pendingOrders", methods=["GET"])
@login_required
def pending_orders():
    """
    Return pending orders list for dashboard page.
    """
    try:
        user_id = g.user_id
        page = max(1, _safe_int(request.args.get("page"), 1))
        page_size = max(1, min(200, _safe_int(request.args.get("pageSize"), 20)))
        offset = (page - 1) * page_size

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT COUNT(1) AS cnt FROM pending_orders WHERE user_id = ?", (user_id,))
            total = int((cur.fetchone() or {}).get("cnt") or 0)
            cur.close()

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT o.*,
                       s.strategy_name,
                       s.notification_config AS strategy_notification_config,
                       s.exchange_config AS strategy_exchange_config,
                       s.market_type AS strategy_market_type,
                       s.market_category AS strategy_market_category,
                       s.execution_mode AS strategy_execution_mode
                FROM pending_orders o
                LEFT JOIN qd_strategies_trading s ON s.id = o.strategy_id
                WHERE o.user_id = ?
                ORDER BY o.id DESC
                LIMIT ? OFFSET ?
                """,
                (user_id, int(page_size), int(offset)),
            )
            rows = cur.fetchall() or []
            cur.close()

        out: List[Dict[str, Any]] = []
        for r in rows:
            status = (r.get("status") or "").strip().lower()
            if status == "sent":
                status = "completed"
            if status == "deferred":
                status = "pending"

            # Frontend expects these keys:
            # - filled_amount, filled_price, error_message
            filled_amount = float(r.get("filled") or 0.0)
            filled_price = float(r.get("avg_price") or 0.0) if float(r.get("avg_price") or 0.0) > 0 else float(r.get("price") or 0.0)

            # Derive exchange_id + notify channels without leaking secrets to frontend.
            ex_cfg = _safe_json_loads(r.get("strategy_exchange_config"), {}) or {}
            notify_cfg = _safe_json_loads(r.get("strategy_notification_config"), {}) or {}
            exchange_id = (r.get("exchange_id") or ex_cfg.get("exchange_id") or ex_cfg.get("exchangeId") or "").strip().lower()
            notify_channels = _as_list((notify_cfg or {}).get("channels"))
            if not notify_channels:
                notify_channels = ["browser"]
            market_type = (r.get("market_type") or r.get("strategy_market_type") or ex_cfg.get("market_type") or ex_cfg.get("marketType") or "").strip().lower()
            market_category = str(r.get("strategy_market_category") or "").strip().lower()
            execution_mode = str(r.get("strategy_execution_mode") or r.get("execution_mode") or "").strip().lower()

            # If non-crypto markets are "signal-only", show SIGNAL instead of blank exchange.
            exchange_display = exchange_id
            if not exchange_display:
                if execution_mode == "signal" or (market_category and market_category != "crypto"):
                    exchange_display = "signal"

            out.append(
                {
                    **r,
                    "strategy_name": r.get("strategy_name") or "",
                    "status": status,
                    "filled_amount": filled_amount,
                    "filled_price": filled_price,
                    "error_message": r.get("last_error") or "",
                    "exchange_id": exchange_id,
                    "exchange_display": exchange_display,
                    "notify_channels": notify_channels,
                    "market_type": market_type or (r.get("market_type") or ""),
                    # Format datetime fields for JSON serialization
                    "created_at": _format_datetime(r.get("created_at")),
                    "updated_at": _format_datetime(r.get("updated_at")),
                    "executed_at": _format_datetime(r.get("executed_at")),
                    "processed_at": _format_datetime(r.get("processed_at")),
                    "sent_at": _format_datetime(r.get("sent_at")),
                }
            )

        # Never expose these strategy-level config blobs.
        for item in out:
            try:
                item.pop("strategy_exchange_config", None)
                item.pop("strategy_notification_config", None)
                item.pop("strategy_market_type", None)
                item.pop("strategy_market_category", None)
                item.pop("strategy_execution_mode", None)
            except Exception:
                pass

        return jsonify(
            {
                "code": 1,
                "msg": "success",
                "data": {
                    "list": out,
                    "page": page,
                    "pageSize": page_size,
                    "total": total,
                },
            }
        )
    except Exception as e:
        logger.error(f"dashboard pendingOrders failed: {e}", exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500


@dashboard_blp.route("/pendingOrders/<int:order_id>", methods=["DELETE"])
@login_required
def delete_pending_order(order_id: int):
    """
    Delete a pending order record (dashboard operation).
    """
    try:
        user_id = g.user_id
        oid = int(order_id or 0)
        if oid <= 0:
            return jsonify({"code": 0, "msg": "invalid_id", "data": None}), 400

        with get_db_connection() as db:
            cur = db.cursor()
            # Verify the order belongs to current user
            cur.execute("SELECT id, status FROM pending_orders WHERE id = ? AND user_id = ?", (oid, user_id))
            row = cur.fetchone() or {}
            if not row:
                cur.close()
                return jsonify({"code": 0, "msg": "not_found", "data": None}), 404
            st = (row.get("status") or "").strip().lower()
            if st == "processing":
                cur.close()
                return jsonify({"code": 0, "msg": "cannot_delete_processing", "data": None}), 400
            cur.execute("DELETE FROM pending_orders WHERE id = ? AND user_id = ?", (oid, user_id))
            db.commit()
            cur.close()

        return jsonify({"code": 1, "msg": "success", "data": {"id": oid}})
    except Exception as e:
        logger.error(f"dashboard delete pendingOrders failed: {e}", exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500

# openapi-compat: legacy import name
dashboard_bp = dashboard_blp
