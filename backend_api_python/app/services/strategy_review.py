from __future__ import annotations

import json
import math
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app.services.llm import LLMService
from app.services.strategy import StrategyService
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.trade_close_reason import enrich_trade_row, is_exit_trade_type
from app.utils.trade_net_pnl import enrich_trades_net_pnl, net_pnl_for_equity_step


logger = get_logger(__name__)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _round(value: Any, digits: int = 4) -> float:
    try:
        f = float(value or 0.0)
        if not math.isfinite(f):
            return 0.0
        return round(f, digits)
    except Exception:
        return 0.0


def _jsonify_scalar(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    return value


def _jsonify_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k): _jsonify_scalar(v) for k, v in dict(row or {}).items()}


def _row_ts(row: Dict[str, Any]) -> int:
    value = row.get("created_at")
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    try:
        return int(value or 0)
    except Exception:
        return 0


class StrategyReviewService:
    """Build a strategy post-trade review from factual trade records first."""

    def __init__(self) -> None:
        self.strategy_service = StrategyService()

    def build_report(
        self,
        *,
        strategy_id: int,
        user_id: int,
        lookback_days: int = 30,
        include_ai: bool = True,
        language: str = "zh-CN",
    ) -> Dict[str, Any]:
        strategy = self.strategy_service.get_strategy(strategy_id, user_id=user_id)
        if not strategy:
            raise ValueError("Strategy not found")

        lookback_days = max(1, min(int(lookback_days or 30), 365))
        now_ts = int(time.time())
        since_ts = now_ts - lookback_days * 86400

        trading_config = strategy.get("trading_config") if isinstance(strategy.get("trading_config"), dict) else {}
        exchange_config = strategy.get("exchange_config") if isinstance(strategy.get("exchange_config"), dict) else {}
        bot_type = str(strategy.get("bot_type") or trading_config.get("bot_type") or "").strip().lower()
        lang_short = "zh" if str(language or "").lower().startswith("zh") else "en"

        all_trades = self._load_trades(strategy_id=strategy_id, bot_type=bot_type, lang=lang_short)
        trades = [t for t in all_trades if _row_ts(t) >= since_ts]
        positions = self._load_positions(strategy_id=strategy_id)
        logs = self._load_recent_logs(strategy_id=strategy_id, since_ts=since_ts)

        metrics = self._build_metrics(
            strategy=strategy,
            trading_config=trading_config,
            all_trades=all_trades,
            trades=trades,
            positions=positions,
            lookback_days=lookback_days,
        )
        diagnostics, recommendations = self._build_rule_review(
            metrics=metrics,
            strategy=strategy,
            trading_config=trading_config,
            bot_type=bot_type,
            language=language,
        )

        base_report = {
            "generated_at": now_ts,
            "lookback_days": lookback_days,
            "language": str(language or ""),
            "strategy": {
                "id": int(strategy_id),
                "name": strategy.get("strategy_name") or strategy.get("name") or str(strategy_id),
                "mode": strategy.get("strategy_mode") or "",
                "type": strategy.get("strategy_type") or strategy.get("type") or "",
                "status": strategy.get("status") or "",
                "execution_mode": strategy.get("execution_mode") or "",
                "symbol": strategy.get("symbol") or trading_config.get("symbol") or "",
                "market_type": trading_config.get("market_type") or strategy.get("market_type") or "",
                "exchange": exchange_config.get("exchange_id") or "",
                "timeframe": trading_config.get("timeframe") or "",
                "bot_type": bot_type,
            },
            "metrics": metrics,
            "diagnostics": diagnostics,
            "recommendations": recommendations,
            "samples": {
                "recent_trades": [self._compact_trade(t) for t in trades[-30:]],
                "recent_logs": logs[:20],
            },
            "ai": {
                "enabled": bool(include_ai),
                "status": "skipped",
                "summary": "",
                "diagnosis": [],
                "recommendations": [],
                "cautions": [],
                "report": "",
            },
        }

        if include_ai:
            base_report["ai"] = self._build_ai_review(
                base_report=base_report,
                language=language,
            )

        base_report["history_id"] = None
        base_report["history_saved"] = False
        history_id = self._save_report(
            strategy_id=int(strategy_id),
            user_id=int(user_id),
            lookback_days=lookback_days,
            language=language,
            include_ai=include_ai,
            report=base_report,
        )
        if history_id:
            base_report["history_id"] = history_id
            base_report["history_saved"] = True

        return base_report

    def list_history(self, *, strategy_id: int, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        self._ensure_history_table()
        limit = max(1, min(int(limit or 20), 50))
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, strategy_id, lookback_days, language, include_ai, ai_status,
                       summary, total_net_pnl, total_return_pct, win_rate,
                       profit_factor, max_drawdown_pct, created_at
                FROM qd_strategy_review_reports
                WHERE strategy_id = ? AND user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(strategy_id), int(user_id), limit),
            )
            rows = cur.fetchall() or []
            cur.close()
        return [_jsonify_row(row) for row in rows]

    def get_history_report(self, *, report_id: int, strategy_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        self._ensure_history_table()
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, strategy_id, lookback_days, language, include_ai, ai_status,
                       report_json, created_at
                FROM qd_strategy_review_reports
                WHERE id = ? AND strategy_id = ? AND user_id = ?
                LIMIT 1
                """,
                (int(report_id), int(strategy_id), int(user_id)),
            )
            row = cur.fetchone()
            cur.close()

        if not row:
            return None

        payload = row.get("report_json") if isinstance(row, dict) else None
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload["history_id"] = int(row.get("id") or report_id)
        payload["history_saved"] = True
        payload["created_at"] = _jsonify_scalar(row.get("created_at"))
        payload["lookback_days"] = int(row.get("lookback_days") or payload.get("lookback_days") or 30)
        payload["language"] = row.get("language") or payload.get("language") or ""
        return payload

    def _save_report(
        self,
        *,
        strategy_id: int,
        user_id: int,
        lookback_days: int,
        language: str,
        include_ai: bool,
        report: Dict[str, Any],
    ) -> Optional[int]:
        try:
            self._ensure_history_table()
            metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
            ai = report.get("ai") if isinstance(report.get("ai"), dict) else {}
            summary = str(ai.get("summary") or "")[:4000]
            payload_json = json.dumps(report, ensure_ascii=False, default=str)
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    INSERT INTO qd_strategy_review_reports (
                        user_id, strategy_id, lookback_days, language, include_ai,
                        ai_status, summary, total_net_pnl, total_return_pct,
                        win_rate, profit_factor, max_drawdown_pct, report_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, NOW())
                    RETURNING id
                    """,
                    (
                        int(user_id),
                        int(strategy_id),
                        int(lookback_days),
                        str(language or "")[:20],
                        bool(include_ai),
                        str(ai.get("status") or "")[:32],
                        summary,
                        _as_float(metrics.get("total_net_pnl")),
                        _as_float(metrics.get("total_return_pct")),
                        _as_float(metrics.get("win_rate")),
                        _as_float(metrics.get("profit_factor")),
                        _as_float(metrics.get("max_drawdown_pct")),
                        payload_json,
                    ),
                )
                row = cur.fetchone()
                db.commit()
                cur.close()
            if row:
                return int(row.get("id") if isinstance(row, dict) else row[0])
        except Exception as exc:
            logger.warning("strategy review history save failed: %s", exc, exc_info=True)
        return None

    def _ensure_history_table(self) -> None:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS qd_strategy_review_reports (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
                        strategy_id INTEGER REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
                        lookback_days INTEGER NOT NULL DEFAULT 30,
                        language VARCHAR(20) DEFAULT 'zh-CN',
                        include_ai BOOLEAN DEFAULT TRUE,
                        ai_status VARCHAR(32) DEFAULT '',
                        summary TEXT DEFAULT '',
                        total_net_pnl DECIMAL(20,8) DEFAULT 0,
                        total_return_pct DECIMAL(20,8) DEFAULT 0,
                        win_rate DECIMAL(20,8) DEFAULT 0,
                        profit_factor DECIMAL(20,8) DEFAULT 0,
                        max_drawdown_pct DECIMAL(20,8) DEFAULT 0,
                        report_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMP DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_strategy_review_reports_strategy
                        ON qd_strategy_review_reports(strategy_id, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_strategy_review_reports_user
                        ON qd_strategy_review_reports(user_id, created_at DESC);
                    """
                )
                db.commit()
                cur.close()
        except Exception as exc:
            logger.warning("strategy review history table ensure failed: %s", exc, exc_info=True)

    def _load_trades(self, *, strategy_id: int, bot_type: str, lang: str) -> List[Dict[str, Any]]:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, strategy_id, symbol, type, price, amount, value,
                       commission, commission_ccy, profit, close_reason,
                       matched_entry_price, grid_matched_profit, created_at
                FROM qd_strategy_trades
                WHERE strategy_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (int(strategy_id),),
            )
            rows = cur.fetchall() or []
            cur.close()

        trades = []
        for row in rows:
            trade = _jsonify_row(row)
            trade = enrich_trade_row(trade, bot_type=bot_type, lang=lang)
            trades.append(trade)
        enrich_trades_net_pnl(trades)
        return trades

    def _load_positions(self, *, strategy_id: int) -> List[Dict[str, Any]]:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT id, strategy_id, symbol, side, size, entry_price,
                           current_price, unrealized_pnl, created_at, updated_at
                    FROM qd_strategy_positions
                    WHERE strategy_id = ?
                    ORDER BY id ASC
                    """,
                    (int(strategy_id),),
                )
                rows = cur.fetchall() or []
                cur.close()
            return [_jsonify_row(r) for r in rows]
        except Exception as exc:
            logger.warning("strategy review positions load failed: %s", exc)
            return []

    def _load_recent_logs(self, *, strategy_id: int, since_ts: int) -> List[Dict[str, Any]]:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT id, level, message, timestamp AS created_at
                    FROM qd_strategy_logs
                    WHERE strategy_id = ?
                    ORDER BY id DESC
                    LIMIT 80
                    """,
                    (int(strategy_id),),
                )
                rows = cur.fetchall() or []
                cur.close()
            out = []
            for row in rows:
                item = _jsonify_row(row)
                if _row_ts(item) >= since_ts:
                    out.append(item)
            return out
        except Exception as exc:
            logger.warning("strategy review logs load failed: %s", exc)
            return []

    def _build_metrics(
        self,
        *,
        strategy: Dict[str, Any],
        trading_config: Dict[str, Any],
        all_trades: List[Dict[str, Any]],
        trades: List[Dict[str, Any]],
        positions: List[Dict[str, Any]],
        lookback_days: int,
    ) -> Dict[str, Any]:
        initial_capital = _as_float(
            strategy.get("initial_capital") or trading_config.get("initial_capital"),
            0.0,
        )
        leverage = _as_float(trading_config.get("leverage") or strategy.get("leverage"), 1.0)
        if leverage <= 0:
            leverage = 1.0
        performance = self._build_performance_snapshot(
            initial_capital=initial_capital,
            trades=all_trades,
            positions=positions,
        )

        opening_events = [t for t in trades if not is_exit_trade_type(str(t.get("type") or ""))]
        closing_events = [t for t in trades if is_exit_trade_type(str(t.get("type") or "")) or t.get("profit") is not None]
        close_pnls = []
        for t in closing_events:
            if t.get("profit") is None:
                continue
            close_pnls.append(_as_float(t.get("profit"), 0.0))

        wins = [p for p in close_pnls if p > 0]
        losses = [p for p in close_pnls if p < 0]
        decided = len(wins) + len(losses)
        win_rate = (len(wins) / decided * 100.0) if decided else 0.0
        gross_profit = sum(wins)
        gross_loss_abs = abs(sum(losses))
        profit_factor = (gross_profit / gross_loss_abs) if gross_loss_abs > 0 else (gross_profit if gross_profit > 0 else 0.0)
        expectancy = (sum(close_pnls) / len(close_pnls)) if close_pnls else 0.0

        equity = initial_capital if initial_capital > 0 else 0.0
        peak = equity
        max_drawdown = 0.0
        equity_steps: List[float] = []
        total_net_pnl = 0.0
        for t in trades:
            step = _as_float(net_pnl_for_equity_step(t), 0.0)
            total_net_pnl += step
            equity += step
            equity_steps.append(equity)
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_drawdown:
                max_drawdown = dd

        unrealized = sum(_as_float(p.get("unrealized_pnl"), 0.0) for p in positions)
        current_equity = equity + unrealized if initial_capital > 0 or trades else unrealized
        max_drawdown_pct = (max_drawdown / peak * 100.0) if peak > 0 else 0.0
        window_return_pct = (total_net_pnl / initial_capital * 100.0) if initial_capital > 0 else 0.0

        fee_total = sum(_as_float(t.get("commission"), 0.0) for t in trades)
        notional_total = sum(_as_float(t.get("value"), 0.0) for t in trades)
        fee_to_notional_pct = (fee_total / notional_total * 100.0) if notional_total > 0 else 0.0
        fee_to_abs_pnl = (fee_total / max(abs(total_net_pnl), 1e-9)) if abs(total_net_pnl) > 1e-9 else 0.0

        max_consecutive_losses = 0
        cur_losses = 0
        for p in close_pnls:
            if p < 0:
                cur_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, cur_losses)
            elif p > 0:
                cur_losses = 0

        reason_counter = Counter()
        for t in closing_events:
            reason = str(t.get("close_reason") or t.get("action_note_en") or "signal_close").strip() or "signal_close"
            reason_counter[reason] += 1

        grid_rows = [
            t for t in closing_events
            if _as_float(t.get("matched_entry_price"), 0.0) > 0
            or t.get("grid_matched_profit") not in (None, "")
        ]
        grid_pnls = [_as_float(t.get("grid_matched_profit"), 0.0) for t in grid_rows]
        grid_negative = len([p for p in grid_pnls if p < 0])

        return {
            "initial_capital": _round(initial_capital, 4),
            "leverage": _round(leverage, 4),
            "lookback_days": int(lookback_days),
            "trade_events": len(trades),
            "opening_events": len(opening_events),
            "closing_events": len(closing_events),
            "closed_trades_with_pnl": len(close_pnls),
            "open_position_count": len(positions),
            "open_position_notional": _round(sum(_as_float(p.get("size"), 0.0) * _as_float(p.get("current_price") or p.get("entry_price"), 0.0) for p in positions), 4),
            "unrealized_pnl": _round(unrealized, 4),
            "current_equity": _round(performance.get("latest_equity", current_equity), 4),
            "total_net_pnl": _round(total_net_pnl, 4),
            "window_net_pnl": _round(total_net_pnl, 4),
            "window_return_pct": _round(window_return_pct, 4),
            "window_max_drawdown": _round(max_drawdown, 4),
            "window_max_drawdown_pct": _round(max_drawdown_pct, 4),
            "performance_initial_equity": _round(performance.get("initial_equity", initial_capital), 4),
            "performance_latest_equity": _round(performance.get("latest_equity", current_equity), 4),
            "performance_total_return": _round(performance.get("total_return", 0.0), 4),
            "performance_total_return_pct": _round(performance.get("total_return_pct", 0.0), 4),
            "performance_max_drawdown": _round(performance.get("max_drawdown", 0.0), 4),
            "performance_max_drawdown_pct": _round(performance.get("max_drawdown_pct", 0.0), 4),
            "performance_points": int(performance.get("points") or 0),
            # Backward-compatible aliases used by the frontend. These are aligned
            # with the strategy performance page, not the selected lookback window.
            "total_return_pct": _round(performance.get("total_return_pct", 0.0), 4),
            "max_drawdown": _round(performance.get("max_drawdown", 0.0), 4),
            "max_drawdown_pct": _round(performance.get("max_drawdown_pct", 0.0), 4),
            "win_rate": _round(win_rate, 4),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "profit_factor": _round(profit_factor, 4),
            "expectancy": _round(expectancy, 4),
            "avg_win": _round(sum(wins) / len(wins) if wins else 0.0, 4),
            "avg_loss": _round(sum(losses) / len(losses) if losses else 0.0, 4),
            "max_win": _round(max(close_pnls) if close_pnls else 0.0, 4),
            "max_loss": _round(min(close_pnls) if close_pnls else 0.0, 4),
            "max_consecutive_losses": max_consecutive_losses,
            "total_commission": _round(fee_total, 6),
            "fee_to_notional_pct": _round(fee_to_notional_pct, 6),
            "fee_to_abs_pnl": _round(fee_to_abs_pnl, 4),
            "notional_total": _round(notional_total, 4),
            "exit_reason_counts": dict(reason_counter.most_common(8)),
            "grid_matched_pairs": len(grid_rows),
            "grid_matched_profit": _round(sum(grid_pnls), 4),
            "grid_negative_pairs": grid_negative,
            "entry_exit_imbalance": len(opening_events) - len(closing_events),
        }

    def _build_performance_snapshot(
        self,
        *,
        initial_capital: float,
        trades: List[Dict[str, Any]],
        positions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Mirror the strategy performance tab's equity-curve KPI math."""
        initial = _as_float(initial_capital, 0.0)
        if initial <= 0:
            initial = 1000.0

        equity = initial
        curve: List[Dict[str, Any]] = []
        if trades:
            curve.append({"time": _row_ts(trades[0]), "equity": round(initial, 2)})

        for trade in trades:
            equity += _as_float(net_pnl_for_equity_step(trade), 0.0)
            curve.append({"time": _row_ts(trade), "equity": round(equity, 2)})

        unrealized = sum(_as_float(p.get("unrealized_pnl"), 0.0) for p in positions)
        live_equity = equity + unrealized
        if abs(unrealized) > 1e-12 or not curve:
            curve.append({"time": int(time.time()), "equity": round(live_equity, 2)})

        summary = self._summarize_equity_curve(initial=initial, curve=curve)
        summary["points"] = len(curve)
        return summary

    def _summarize_equity_curve(self, *, initial: float, curve: List[Dict[str, Any]]) -> Dict[str, Any]:
        init = _as_float(initial, 0.0)
        if init <= 0:
            init = 1000.0
        latest = _as_float(curve[-1].get("equity"), init) if curve else init
        total_return = latest - init
        total_return_pct = (total_return / init * 100.0) if init > 0 else 0.0

        peak = init
        max_drawdown = 0.0
        max_drawdown_pct = 0.0
        for point in curve or []:
            equity = _as_float(point.get("equity"), 0.0)
            if equity > peak:
                peak = equity
            drawdown = peak - equity
            if drawdown > max_drawdown:
                max_drawdown = drawdown
            if peak > 0:
                drawdown_pct = drawdown / peak * 100.0
                if drawdown_pct > max_drawdown_pct:
                    max_drawdown_pct = drawdown_pct

        return {
            "initial_equity": round(init, 2),
            "latest_equity": round(latest, 2),
            "total_return": round(total_return, 2),
            "total_return_pct": round(total_return_pct, 2),
            "max_drawdown": round(max_drawdown, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
        }

    def _legacy_build_rule_review(
        self,
        *,
        metrics: Dict[str, Any],
        strategy: Dict[str, Any],
        trading_config: Dict[str, Any],
        bot_type: str,
        language: str = "zh-CN",
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        diagnostics: List[Dict[str, Any]] = []
        recommendations: List[Dict[str, Any]] = []
        language_norm = str(language or "").lower()
        is_zh = language_norm.startswith("zh")
        is_zh_tw = is_zh and ("tw" in language_norm or "hant" in language_norm)

        def text(en: str, zh: str) -> str:
            return zh if is_zh else en

        def diag(severity: str, code: str, title: str, detail: str, value: Any = None) -> None:
            diagnostics.append({
                "severity": severity,
                "code": code,
                "title": title,
                "detail": detail,
                "value": value,
            })

        def rec(priority: str, code: str, title: str, detail: str) -> None:
            recommendations.append({
                "priority": priority,
                "code": code,
                "title": title,
                "detail": detail,
            })

        close_count = _as_int(metrics.get("closed_trades_with_pnl"))
        if close_count < 5:
            diag("info", "sample_small",
                 text("Sample is still small", "样本仍然偏少"),
                 text("Fewer than 5 closed trades have realised PnL in the selected window. Treat conclusions as directional.",
                      "所选周期内已实现盈亏的平仓交易少于 5 笔，结论只能作为方向性参考。"),
                 close_count)
            rec("medium", "collect_more",
                text("Collect more closed trades", "先积累更多平仓样本"),
                text("Keep the report in observe mode until the strategy has at least 10-20 closed trades.",
                     "建议至少积累 10-20 笔已平仓交易后，再判断策略参数是否需要调整。"))

        total_net = _as_float(metrics.get("total_net_pnl"))
        if total_net < 0:
            diag("warning", "negative_expectancy_window",
                 text("Recent realised PnL is negative", "近期已实现净盈亏为负"),
                 text("The strategy lost money after fees in the selected window.",
                      "所选周期内策略扣除手续费后处于亏损状态。"),
                 total_net)
            rec("high", "reduce_risk_until_retested",
                text("Reduce risk before expanding capital", "扩大资金前先降低风险"),
                text("Do not increase leverage or capital until the next backtest/OOS check improves expectancy.",
                     "在回测或样本外验证改善前，不建议提高杠杆或投入资金。"))

        pf = _as_float(metrics.get("profit_factor"))
        if close_count >= 5 and pf < 1:
            diag("warning", "profit_factor_below_one",
                 text("Profit factor is below 1", "盈亏比低于 1"),
                 text("Winning trades are not covering losing trades and fees.",
                      "盈利交易不足以覆盖亏损交易和手续费。"),
                 pf)
            rec("high", "review_entry_exit_filters",
                text("Review entry and exit filters", "复查入场与出场过滤条件"),
                text("Check whether entries are too early, exits are too late, or the strategy is being used in the wrong market regime.",
                     "需要检查是否入场过早、出场过晚，或策略被用于不适合的市场状态。"))

        wr = _as_float(metrics.get("win_rate"))
        if close_count >= 5 and wr < 35:
            diag("warning", "low_win_rate",
                 text("Win rate is low", "胜率偏低"),
                 text("The strategy is losing more often than expected. This can be acceptable only when average wins are much larger than losses.",
                      "策略亏损频率偏高，只有当平均盈利显著大于平均亏损时才可能合理。"),
                 wr)
            rec("medium", "tighten_signal_quality",
                text("Tighten signal quality", "提高信号质量门槛"),
                text("Consider adding trend, volatility, or volume confirmation before new entries.",
                     "可考虑增加趋势、波动率或成交量确认后再开仓。"))

        max_dd_pct = _as_float(metrics.get("max_drawdown_pct"))
        if max_dd_pct >= 10:
            diag("danger", "large_drawdown",
                 text("Drawdown is elevated", "回撤偏高"),
                 text("The realised equity path shows a meaningful peak-to-trough drop.",
                      "已实现权益曲线出现了明显的峰谷回撤。"),
                 max_dd_pct)
            rec("high", "cap_position_size",
                text("Cap position size", "限制单次仓位"),
                text("Lower entry percentage or maximum position while this strategy is under review.",
                     "在复盘观察期内，建议降低开仓比例或最大持仓上限。"))

        consecutive_losses = _as_int(metrics.get("max_consecutive_losses"))
        if consecutive_losses >= 3:
            diag("warning", "loss_streak",
                 text("Consecutive losses detected", "出现连续亏损"),
                 text("The strategy had a losing streak that can pressure margin and operator confidence.",
                      "连续亏损会对保证金和执行信心造成压力。"),
                 consecutive_losses)
            rec("medium", "add_cooldown",
                text("Add a cooldown after loss streaks", "连续亏损后增加冷却期"),
                text("Consider pausing new entries after 3 consecutive realised losses until the next confirmed setup.",
                     "可考虑连续 3 笔已实现亏损后暂停新开仓，等待下一次确认信号。"))

        fee_ratio = _as_float(metrics.get("fee_to_abs_pnl"))
        if _as_float(metrics.get("total_commission")) > 0 and (fee_ratio >= 0.5 or (abs(total_net) < 1e-9 and close_count > 0)):
            diag("warning", "fees_are_material",
                 text("Fees are material", "手续费侵蚀明显"),
                 text("Commissions are large compared with realised PnL, so small edges may be erased.",
                      "手续费相对已实现盈亏占比较高，小幅优势可能被交易成本吞掉。"),
                 metrics.get("total_commission"))
            rec("medium", "reduce_churn",
                text("Reduce churn or improve execution", "降低交易频率或优化成交方式"),
                text("Use fewer but higher-quality signals, maker orders where appropriate, or wider grid spacing.",
                     "可减少低质量信号、优先使用 maker 挂单，或适当放宽网格间距。"))

        if bot_type in ("grid", "dca"):
            neg_pairs = _as_int(metrics.get("grid_negative_pairs"))
            matched = _as_int(metrics.get("grid_matched_pairs"))
            if matched > 0 and neg_pairs > 0:
                diag("warning", "grid_negative_pairs",
                     text("Some grid matches closed below net profit", "部分网格配对净利润为负"),
                     text("Grid matched rows include negative net PnL after fees.",
                          "网格配对成交中存在扣除手续费后为负的记录。"),
                     {"negative": neg_pairs, "matched": matched})
                rec("high", "grid_profit_floor",
                    text("Check grid spacing versus fees", "检查网格间距是否覆盖手续费"),
                    text("Increase grid spacing or lower trading frequency so each matched pair covers both entry and exit fees.",
                         "建议加大网格间距或降低交易频率，确保每次配对能覆盖开平两侧手续费。"))

        imbalance = _as_int(metrics.get("entry_exit_imbalance"))
        if imbalance >= 3:
            diag("info", "many_unclosed_entries",
                 text("Many entries are not closed yet", "存在较多未闭合开仓"),
                 text("The trade log has more opens than exits in the selected window.",
                      "所选周期内开仓次数明显多于平仓次数。"),
                 imbalance)
            rec("medium", "review_exposure",
                text("Review accumulated exposure", "复查累计敞口"),
                text("Make sure the strategy is intentionally scaling in and that max position limits are active.",
                     "确认这是有意加仓，并且最大持仓限制已经生效。"))

        if _as_int(metrics.get("open_position_count")) > 0:
            diag("info", "open_positions_present",
                 text("Open positions are present", "当前仍有未平仓"),
                 text("The report includes unrealized PnL, but final performance is not locked until positions close.",
                      "报告会展示未实现盈亏，但最终表现要等仓位平掉后才能确认。"),
                 metrics.get("open_position_count"))

        if not diagnostics:
            diag("success", "no_major_issue",
                 text("No obvious issue in this window", "当前周期未发现明显异常"),
                 text("The deterministic checks did not find a clear risk concentration. Continue monitoring sample size and live execution drift.",
                      "规则检查没有发现明显风险集中点，建议继续观察样本量和实盘偏差。"),
                 None)
            rec("low", "keep_monitoring",
                text("Keep monitoring", "继续观察"),
                text("No automatic parameter change is recommended from this sample alone.",
                     "仅凭当前样本，不建议自动修改策略参数。"))

        return diagnostics, recommendations

    def _legacy_build_ai_review(self, *, base_report: Dict[str, Any], language: str) -> Dict[str, Any]:
        default = {
            "enabled": True,
            "status": "fallback",
            "summary": "",
            "diagnosis": [],
            "recommendations": [],
            "cautions": [],
            "report": "",
        }

        is_zh = str(language or "").lower().startswith("zh")
        metrics = base_report.get("metrics") or {}
        diagnostics = base_report.get("diagnostics") or []
        recommendations = base_report.get("recommendations") or []

        if is_zh:
            default["summary"] = self._fallback_summary_zh(metrics, diagnostics)
            default["diagnosis"] = [d.get("detail") or d.get("title") for d in diagnostics[:5]]
            default["recommendations"] = [r.get("detail") or r.get("title") for r in recommendations[:5]]
            default["cautions"] = ["这是基于最近成交记录的复盘，不会自动修改策略参数。"]
        else:
            default["summary"] = self._fallback_summary_en(metrics, diagnostics)
            default["diagnosis"] = [d.get("detail") or d.get("title") for d in diagnostics[:5]]
            default["recommendations"] = [r.get("detail") or r.get("title") for r in recommendations[:5]]
            default["cautions"] = ["This review is based on recent trade records and does not change parameters automatically."]

        try:
            payload = {
                "strategy": base_report.get("strategy"),
                "metrics": metrics,
                "diagnostics": diagnostics[:8],
                "rule_recommendations": recommendations[:8],
            }
            system_prompt = (
                "You are a professional quantitative trading strategy reviewer. "
                "Only use the provided metrics and diagnostics. Do not invent trades, prices, dates, or parameters. "
                "Return strict JSON with keys: summary, diagnosis, recommendations, cautions. "
                "diagnosis/recommendations/cautions must be arrays of short strings."
            )
            if is_zh_tw:
                system_prompt += " Write in Traditional Chinese."
            elif is_zh:
                system_prompt += " Write in Simplified Chinese."
            else:
                system_prompt += " Write in concise professional English."
            user_prompt = json.dumps(payload, ensure_ascii=False, default=str)
            result = LLMService().safe_call_llm(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                default_structure=dict(default),
            )
            if not isinstance(result, dict):
                return default
            out = dict(default)
            out.update({
                "status": "ok" if not str(result.get("report") or "").startswith("Analysis failed") else "fallback",
                "summary": str(result.get("summary") or default["summary"]).strip(),
                "diagnosis": self._as_str_list(result.get("diagnosis"), default["diagnosis"]),
                "recommendations": self._as_str_list(result.get("recommendations"), default["recommendations"]),
                "cautions": self._as_str_list(result.get("cautions"), default["cautions"]),
                "report": str(result.get("report") or ""),
            })
            return out
        except Exception as exc:
            logger.warning("AI strategy review failed: %s", exc, exc_info=True)
            default["report"] = str(exc)
            return default

    def _as_str_list(self, value: Any, fallback: List[str]) -> List[str]:
        if isinstance(value, list):
            out = [str(x).strip() for x in value if str(x).strip()]
            return out[:8] if out else fallback
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return fallback

    def _legacy_fallback_summary_zh(self, metrics: Dict[str, Any], diagnostics: List[Dict[str, Any]]) -> str:
        return (
            f"最近{metrics.get('lookback_days', 30)}天共有{metrics.get('closed_trades_with_pnl', 0)}笔已实现盈亏交易，"
            f"净盈亏{metrics.get('total_net_pnl', 0)}，胜率{metrics.get('win_rate', 0)}%，"
            f"盈亏比{metrics.get('profit_factor', 0)}，最大回撤{metrics.get('max_drawdown_pct', 0)}%。"
            f"规则引擎识别到{len(diagnostics)}个复盘要点。"
        )

    def _legacy_fallback_summary_en(self, metrics: Dict[str, Any], diagnostics: List[Dict[str, Any]]) -> str:
        return (
            f"Over the last {metrics.get('lookback_days', 30)} days, the strategy has "
            f"{metrics.get('closed_trades_with_pnl', 0)} closed PnL trades, net PnL "
            f"{metrics.get('total_net_pnl', 0)}, win rate {metrics.get('win_rate', 0)}%, "
            f"profit factor {metrics.get('profit_factor', 0)}, and max drawdown "
            f"{metrics.get('max_drawdown_pct', 0)}%. The rule engine found "
            f"{len(diagnostics)} review point(s)."
        )

    def _build_rule_review(
        self,
        *,
        metrics: Dict[str, Any],
        strategy: Dict[str, Any],
        trading_config: Dict[str, Any],
        bot_type: str,
        language: str = "zh-CN",
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        diagnostics: List[Dict[str, Any]] = []
        recommendations: List[Dict[str, Any]] = []
        is_zh = str(language or "").lower().startswith("zh")

        def text(en: str, zh: str) -> str:
            return zh if is_zh else en

        def diag(severity: str, code: str, title: str, detail: str, value: Any = None) -> None:
            diagnostics.append({
                "severity": severity,
                "code": code,
                "title": title,
                "detail": detail,
                "value": value,
            })

        def rec(priority: str, code: str, title: str, detail: str) -> None:
            recommendations.append({
                "priority": priority,
                "code": code,
                "title": title,
                "detail": detail,
            })

        close_count = _as_int(metrics.get("closed_trades_with_pnl"))
        window_net = _as_float(metrics.get("window_net_pnl", metrics.get("total_net_pnl")))
        performance_return_pct = _as_float(metrics.get("performance_total_return_pct", metrics.get("total_return_pct")))

        if close_count < 5:
            diag(
                "info",
                "sample_small",
                text("Sample is still small", "样本仍然偏少"),
                text(
                    "Fewer than 5 closed trades have realised PnL in the selected review window. Treat the conclusion as directional.",
                    "所选复盘周期内已实现盈亏的平仓交易少于 5 笔，结论只能作为方向性参考。",
                ),
                close_count,
            )
            rec(
                "medium",
                "collect_more",
                text("Collect more closed trades", "先积累更多平仓样本"),
                text(
                    "Keep the report in observe mode until the strategy has at least 10-20 closed trades.",
                    "建议至少积累 10-20 笔已平仓交易后，再判断策略参数是否需要调整。",
                ),
            )

        if window_net < 0:
            diag(
                "warning",
                "negative_expectancy_window",
                text("Review-window realised PnL is negative", "复盘窗口已实现净盈亏为负"),
                text(
                    "The strategy lost money after fees in the selected review window.",
                    "所选复盘周期内，策略扣除手续费后的已实现净盈亏为负。",
                ),
                window_net,
            )
            rec(
                "high",
                "reduce_risk_until_retested",
                text("Reduce risk before expanding capital", "扩大资金前先降低风险"),
                text(
                    "Do not increase leverage or capital until the next backtest or out-of-sample check improves expectancy.",
                    "在回测或样本外验证改善前，不建议提高杠杆或投入资金。",
                ),
            )

        pf = _as_float(metrics.get("profit_factor"))
        if close_count >= 5 and pf < 1:
            diag(
                "warning",
                "profit_factor_below_one",
                text("Profit factor is below 1", "盈亏比低于 1"),
                text(
                    "Winning trades are not covering losing trades and fees.",
                    "盈利交易不足以覆盖亏损交易和手续费。",
                ),
                pf,
            )
            rec(
                "high",
                "review_entry_exit_filters",
                text("Review entry and exit filters", "复查入场与出场过滤条件"),
                text(
                    "Check whether entries are too early, exits are too late, or the strategy is being used in the wrong market regime.",
                    "需要检查是否入场过早、出场过晚，或者策略被用于不适合的市场状态。",
                ),
            )

        wr = _as_float(metrics.get("win_rate"))
        if close_count >= 5 and wr < 35:
            diag(
                "warning",
                "low_win_rate",
                text("Win rate is low", "胜率偏低"),
                text(
                    "The strategy loses more often than it wins. This is only acceptable when average wins are much larger than losses.",
                    "策略亏损频率偏高，只有当平均盈利显著大于平均亏损时才可能合理。",
                ),
                wr,
            )
            rec(
                "medium",
                "tighten_signal_quality",
                text("Tighten signal quality", "提高信号质量门槛"),
                text(
                    "Consider adding trend, volatility, or volume confirmation before new entries.",
                    "可考虑增加趋势、波动率或成交量确认后再开仓。",
                ),
            )

        max_dd_pct = _as_float(metrics.get("performance_max_drawdown_pct", metrics.get("max_drawdown_pct")))
        if max_dd_pct >= 10:
            diag(
                "danger",
                "large_drawdown",
                text("Performance drawdown is elevated", "绩效回撤偏高"),
                text(
                    "The performance-page equity path shows a meaningful peak-to-trough drop.",
                    "按绩效页同口径计算的权益曲线出现了明显峰谷回撤。",
                ),
                max_dd_pct,
            )
            rec(
                "high",
                "cap_position_size",
                text("Cap position size", "限制单次仓位"),
                text(
                    "Lower entry percentage or maximum position while this strategy is under review.",
                    "在复盘观察期内，建议降低开仓比例或最大持仓上限。",
                ),
            )

        consecutive_losses = _as_int(metrics.get("max_consecutive_losses"))
        if consecutive_losses >= 3:
            diag(
                "warning",
                "loss_streak",
                text("Consecutive losses detected", "出现连续亏损"),
                text(
                    "The strategy had a losing streak that can pressure margin and operator confidence.",
                    "连续亏损会对保证金和执行信心造成压力。",
                ),
                consecutive_losses,
            )
            rec(
                "medium",
                "add_cooldown",
                text("Add a cooldown after loss streaks", "连续亏损后增加冷却期"),
                text(
                    "Consider pausing new entries after 3 consecutive realised losses until the next confirmed setup.",
                    "可考虑连续 3 笔已实现亏损后暂停新开仓，等待下一次确认信号。",
                ),
            )

        fee_ratio = _as_float(metrics.get("fee_to_abs_pnl"))
        if _as_float(metrics.get("total_commission")) > 0 and (fee_ratio >= 0.5 or (abs(window_net) < 1e-9 and close_count > 0)):
            diag(
                "warning",
                "fees_are_material",
                text("Fees are material", "手续费侵蚀明显"),
                text(
                    "Commissions are large compared with realised PnL, so small edges may be erased.",
                    "手续费相对已实现盈亏占比较高，小幅优势可能被交易成本吞掉。",
                ),
                metrics.get("total_commission"),
            )
            rec(
                "medium",
                "reduce_churn",
                text("Reduce churn or improve execution", "降低交易频率或优化成交方式"),
                text(
                    "Use fewer but higher-quality signals, maker orders where appropriate, or wider grid spacing.",
                    "可减少低质量信号、优先使用 maker 挂单，或适当放宽网格间距。",
                ),
            )

        if bot_type in ("grid", "dca"):
            neg_pairs = _as_int(metrics.get("grid_negative_pairs"))
            matched = _as_int(metrics.get("grid_matched_pairs"))
            if matched > 0 and neg_pairs > 0:
                diag(
                    "warning",
                    "grid_negative_pairs",
                    text("Some grid matches closed below net profit", "部分网格配对净利润为负"),
                    text(
                        "Grid matched rows include negative net PnL after fees.",
                        "网格配对成交中存在扣除手续费后为负的记录。",
                    ),
                    {"negative": neg_pairs, "matched": matched},
                )
                rec(
                    "high",
                    "grid_profit_floor",
                    text("Check grid spacing versus fees", "检查网格间距是否覆盖手续费"),
                    text(
                        "Increase grid spacing or lower trading frequency so each matched pair covers both entry and exit fees.",
                        "建议加大网格间距或降低交易频率，确保每次配对能覆盖开平两侧手续费。",
                    ),
                )

        imbalance = _as_int(metrics.get("entry_exit_imbalance"))
        if imbalance >= 3:
            diag(
                "info",
                "many_unclosed_entries",
                text("Many entries are not closed yet", "存在较多未闭合开仓"),
                text(
                    "The trade log has more opens than exits in the selected review window.",
                    "所选复盘周期内开仓次数明显多于平仓次数。",
                ),
                imbalance,
            )
            rec(
                "medium",
                "review_exposure",
                text("Review accumulated exposure", "复查累计敞口"),
                text(
                    "Make sure the strategy is intentionally scaling in and that max position limits are active.",
                    "确认这是有意加仓，并且最大持仓限制已经生效。",
                ),
            )

        if _as_int(metrics.get("open_position_count")) > 0:
            diag(
                "info",
                "open_positions_present",
                text("Open positions are present", "当前仍有未平仓"),
                text(
                    "The report includes unrealized PnL, but final performance is not locked until positions close.",
                    "报告会展示未实现盈亏，但最终表现要等仓位平掉后才能确认。",
                ),
                metrics.get("open_position_count"),
            )

        if performance_return_pct < 0 and window_net >= 0:
            diag(
                "info",
                "window_and_performance_differ",
                text("Window PnL and performance return differ", "窗口盈亏与绩效收益率口径不同"),
                text(
                    "The selected review window can be profitable while the full performance equity curve remains negative.",
                    "所选复盘窗口可能是盈利的，但全量绩效权益曲线仍可能为负。",
                ),
                {"window_net_pnl": window_net, "performance_total_return_pct": performance_return_pct},
            )

        if not diagnostics:
            diag(
                "success",
                "no_major_issue",
                text("No obvious issue in this window", "当前周期未发现明显异常"),
                text(
                    "The deterministic checks did not find a clear risk concentration. Continue monitoring sample size and live execution drift.",
                    "规则检查没有发现明显风险集中点，建议继续观察样本量和实盘偏差。",
                ),
                None,
            )
            rec(
                "low",
                "keep_monitoring",
                text("Keep monitoring", "继续观察"),
                text(
                    "No automatic parameter change is recommended from this sample alone.",
                    "仅凭当前样本，不建议自动修改策略参数。",
                ),
            )

        return diagnostics, recommendations

    def _build_ai_review(self, *, base_report: Dict[str, Any], language: str) -> Dict[str, Any]:
        language_norm = str(language or "").lower()
        is_zh = language_norm.startswith("zh")
        is_zh_tw = is_zh and ("tw" in language_norm or "hant" in language_norm)
        metrics = base_report.get("metrics") or {}
        diagnostics = base_report.get("diagnostics") or []
        recommendations = base_report.get("recommendations") or []

        default = {
            "enabled": True,
            "status": "fallback",
            "source": "rules",
            "provider": "",
            "model": "",
            "elapsed_ms": 0,
            "summary": self._fallback_summary_zh(metrics, diagnostics) if is_zh else self._fallback_summary_en(metrics, diagnostics),
            "diagnosis": [d.get("detail") or d.get("title") for d in diagnostics[:5]],
            "recommendations": [r.get("detail") or r.get("title") for r in recommendations[:5]],
            "cautions": [
                "这是基于成交记录、持仓和规则指标生成的复盘，不会自动修改策略参数。"
                if is_zh else
                "This review is based on trades, positions, and rule metrics. It does not change parameters automatically."
            ],
            "report": "",
            "error": "",
        }

        started = time.time()
        try:
            llm = LLMService()
            provider = llm.provider
            model = llm.get_default_model(provider)
            default["provider"] = provider.value
            default["model"] = model

            if not llm.get_api_key(provider):
                default["error"] = f"LLM provider {provider.value} has no API key configured."
                default["report"] = default["error"]
                return default

            payload = {
                "strategy": base_report.get("strategy"),
                "metric_definitions": {
                    "window_net_pnl": "realized net PnL after fees inside the selected review window",
                    "performance_total_return_pct": "same return percentage as the performance page, based on the full equity curve",
                    "performance_max_drawdown_pct": "same max drawdown percentage as the performance page",
                },
                "metrics": metrics,
                "diagnostics": diagnostics[:8],
                "rule_recommendations": recommendations[:8],
            }
            system_prompt = (
                "You are a professional quantitative trading strategy reviewer. "
                "Use only the provided metrics and diagnostics. Do not invent trades, prices, dates, or parameters. "
                "Clearly distinguish selected-window realised PnL from full performance-page return/drawdown. "
                "Return strict JSON with keys: summary, diagnosis, recommendations, cautions. "
                "diagnosis, recommendations, and cautions must be arrays of short strings."
            )
            if is_zh_tw:
                system_prompt += " Write in Traditional Chinese."
            elif is_zh:
                system_prompt += " Write in Simplified Chinese."
            else:
                system_prompt += " Write in concise professional English."

            result = llm.safe_call_llm(
                system_prompt=system_prompt,
                user_prompt=json.dumps(payload, ensure_ascii=False, default=str),
                default_structure=dict(default),
                model=model,
                provider=provider,
            )
            default["elapsed_ms"] = int((time.time() - started) * 1000)
            if not isinstance(result, dict):
                default["error"] = "LLM returned a non-object result."
                default["report"] = default["error"]
                return default

            report_text = str(result.get("report") or "").strip()
            failed = report_text.startswith("Analysis failed") or report_text.startswith("Failed to parse")
            if failed:
                default["error"] = report_text
                default["report"] = report_text
                return default

            out = dict(default)
            out.update({
                "status": "ok",
                "source": "llm",
                "summary": str(result.get("summary") or default["summary"]).strip(),
                "diagnosis": self._as_str_list(result.get("diagnosis"), default["diagnosis"]),
                "recommendations": self._as_str_list(result.get("recommendations"), default["recommendations"]),
                "cautions": self._as_str_list(result.get("cautions"), default["cautions"]),
                "report": report_text,
                "error": "",
            })
            return out
        except Exception as exc:
            logger.warning("AI strategy review failed: %s", exc, exc_info=True)
            default["elapsed_ms"] = int((time.time() - started) * 1000)
            default["error"] = str(exc)
            default["report"] = str(exc)
            return default

    def _fallback_summary_zh(self, metrics: Dict[str, Any], diagnostics: List[Dict[str, Any]]) -> str:
        return (
            f"近{metrics.get('lookback_days', 30)}天复盘窗口内共有"
            f"{metrics.get('closed_trades_with_pnl', 0)}笔已实现盈亏交易，"
            f"窗口已实现净盈亏{metrics.get('window_net_pnl', metrics.get('total_net_pnl', 0))}；"
            f"绩效页同口径收益率{metrics.get('performance_total_return_pct', metrics.get('total_return_pct', 0))}%，"
            f"最大回撤{metrics.get('performance_max_drawdown_pct', metrics.get('max_drawdown_pct', 0))}%。"
            f"窗口胜率{metrics.get('win_rate', 0)}%，盈亏比{metrics.get('profit_factor', 0)}。"
            f"规则引擎识别到{len(diagnostics)}个复盘要点。"
        )

    def _fallback_summary_en(self, metrics: Dict[str, Any], diagnostics: List[Dict[str, Any]]) -> str:
        return (
            f"Over the last {metrics.get('lookback_days', 30)} days, the selected review window has "
            f"{metrics.get('closed_trades_with_pnl', 0)} closed PnL trades and realised net PnL "
            f"{metrics.get('window_net_pnl', metrics.get('total_net_pnl', 0))}. "
            f"The performance-page aligned return is "
            f"{metrics.get('performance_total_return_pct', metrics.get('total_return_pct', 0))}%, "
            f"with max drawdown {metrics.get('performance_max_drawdown_pct', metrics.get('max_drawdown_pct', 0))}%. "
            f"Window win rate is {metrics.get('win_rate', 0)}% and profit factor is {metrics.get('profit_factor', 0)}. "
            f"The rule engine found {len(diagnostics)} review point(s)."
        )

    def _compact_trade(self, trade: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": trade.get("id"),
            "time": _row_ts(trade),
            "symbol": trade.get("symbol"),
            "type": trade.get("type"),
            "price": _round(trade.get("price"), 8),
            "amount": _round(trade.get("amount"), 8),
            "value": _round(trade.get("value"), 4),
            "profit": None if trade.get("profit") is None else _round(trade.get("profit"), 4),
            "commission": _round(trade.get("commission"), 6),
            "close_reason": trade.get("close_reason") or "",
            "action_note": trade.get("action_note") or trade.get("action_note_en") or "",
        }
