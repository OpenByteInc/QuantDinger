"""Persistence for Strategy API V2 backtest runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.utils.db import get_db_connection

from .contract import strategy_source_code_hash
from .curve_sampling import sample_equity_curve


class StrategyBacktestRepository:
    def has_successful_run(
        self,
        *,
        user_id: int,
        source_id: int,
        code_hash: str = "",
    ) -> bool:
        where = ["user_id = ?", "source_id = ?", "status = 'success'"]
        params: list[Any] = [int(user_id), int(source_id)]
        normalized_hash = str(code_hash or "").strip()
        if normalized_hash:
            where.append("code_hash = ?")
            params.append(normalized_hash)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                f"SELECT id FROM qd_backtest_runs WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT 1",
                tuple(params),
            )
            found = cur.fetchone() is not None
            cur.close()
        return found

    def persist_run(
        self,
        *,
        user_id: int,
        strategy_id: int | None,
        strategy_name: str,
        source_id: int | None,
        market: str,
        symbol: str,
        timeframe: str,
        start_date: str,
        end_date: str,
        initial_capital: float,
        commission: float,
        slippage: float,
        leverage: float,
        manifest: dict[str, Any],
        params: dict[str, Any],
        result: dict[str, Any],
        code: str,
    ) -> int | None:
        compact_result = compact_backtest_result(result)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_backtest_runs
                (user_id, strategy_id, source_id, strategy_name, market, symbol, market_type,
                 timeframe, start_date, end_date, initial_capital, commission, slippage, leverage,
                 params_json, manifest_json, engine_version, code_hash, status, error_message,
                 result_json, result_compacted, total_return, win_rate, total_trades, total_executions,
                 max_drawdown, sharpe_ratio, result_status, data_kind, benchmark_total_return,
                 summary_backfilled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'success', '', ?, TRUE, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, NOW())
                """,
                (
                    int(user_id),
                    int(strategy_id) if strategy_id is not None else None,
                    int(source_id) if source_id is not None else 0,
                    str(strategy_name or ""),
                    str(market or ""),
                    str(symbol or ""),
                    str(
                        next(
                            (
                                item.get("market_type")
                                for item in (manifest.get("universe") or {}).get("instruments", [])
                                if item.get("market_type")
                            ),
                            "spot",
                        )
                    ),
                    str(timeframe or ""),
                    str(start_date),
                    str(end_date),
                    float(initial_capital),
                    float(commission),
                    float(slippage),
                    float(leverage),
                    json.dumps(params, ensure_ascii=False),
                    json.dumps(manifest, ensure_ascii=False),
                    str((result.get("engine") or {}).get("version") or "strategy-api-v2"),
                    strategy_source_code_hash(code),
                    json.dumps(compact_result, ensure_ascii=False),
                    _nullable_number(result.get("totalReturn")),
                    _nullable_number(result.get("winRate")),
                    _nullable_int(result.get("totalTrades")),
                    _nullable_int(result.get("totalExecutions")),
                    _nullable_number(result.get("maxDrawdown")),
                    _nullable_number(result.get("sharpeRatio")),
                    str(result.get("resultStatus") or "unknown"),
                    str((result.get("dataProvenance") or {}).get("kind") or "unknown"),
                    _nullable_number(result.get("benchmarkTotalReturn")),
                ),
            )
            run_id = int(cur.lastrowid or 0) or None
            if run_id is not None:
                self._persist_details(cur, run_id, user_id, strategy_id, compact_result)
            db.commit()
            cur.close()
        return run_id

    def list_runs(
        self,
        *,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
        strategy_id: int | None = None,
        source_id: int | None = None,
        symbol: str = "",
        market: str = "",
        timeframe: str = "",
    ) -> list[dict[str, Any]]:
        where = ["user_id = ?"]
        params: list[Any] = [int(user_id)]
        for clause, value in (
            ("strategy_id = ?", strategy_id),
            ("source_id = ?", source_id),
            ("symbol = ?", symbol),
            ("market = ?", market),
            ("timeframe = ?", timeframe),
        ):
            if value not in (None, ""):
                where.append(clause)
                params.append(value)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                f"""
                SELECT id, user_id, strategy_id, source_id, strategy_name, market, symbol, market_type, timeframe,
                       start_date, end_date, initial_capital, commission, slippage, leverage,
                       params_json, manifest_json, engine_version, code_hash, status, created_at,
                       total_return, win_rate, total_trades, total_executions, result_status,
                       data_kind, benchmark_total_return, max_drawdown, sharpe_ratio
                FROM qd_backtest_runs
                WHERE {' AND '.join(where)}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, int(limit), int(offset)),
            )
            rows = cur.fetchall() or []
            cur.close()
        return [self._hydrate_summary(row) for row in rows]

    def get_run(self, *, user_id: int, run_id: int) -> Optional[dict[str, Any]]:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, user_id, strategy_id, source_id, strategy_name, market, symbol, market_type, timeframe,
                       start_date, end_date, initial_capital, commission, slippage, leverage,
                       params_json, manifest_json, engine_version, code_hash, status,
                       CASE WHEN result_compacted THEN result_json ELSE '{}' END AS result_json,
                       result_compacted, total_return, win_rate, total_trades, total_executions,
                       result_status, data_kind, benchmark_total_return, max_drawdown, sharpe_ratio, created_at
                FROM qd_backtest_runs
                WHERE id = ? AND user_id = ?
                """,
                (int(run_id), int(user_id)),
            )
            row = cur.fetchone()
            fallback_result = None
            if row and not bool(row.get("result_compacted")):
                fallback_result = self._load_lightweight_details(cur, int(run_id))
            cur.close()
        return self._hydrate(row, include_result=True, fallback_result=fallback_result) if row else None

    @staticmethod
    def _load_lightweight_details(cur, run_id: int) -> dict[str, Any]:
        """Open old runs without reading their oversized monolithic result JSON."""
        cur.execute(
            """
            SELECT payload_json
            FROM qd_backtest_trades
            WHERE run_id = ?
            ORDER BY trade_index ASC
            LIMIT 5000
            """,
            (int(run_id),),
        )
        trades = []
        for row in cur.fetchall() or []:
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except (TypeError, ValueError):
                payload = {}
            if isinstance(payload, dict):
                trades.append(payload)

        cur.execute(
            """
            WITH ranked AS (
                SELECT point_time, point_value,
                       ROW_NUMBER() OVER (ORDER BY point_index ASC) AS row_number,
                       COUNT(*) OVER () AS row_count
                FROM qd_backtest_equity_points
                WHERE run_id = ?
            )
            SELECT point_time, point_value
            FROM ranked
            WHERE row_count <= 2400
               OR row_number = 1
               OR row_number = row_count
               OR MOD(row_number - 1, GREATEST(1, CEIL(row_count / 2400.0)::INTEGER)) = 0
            ORDER BY row_number ASC
            """,
            (int(run_id),),
        )
        equity_curve = [
            {"time": row.get("point_time"), "value": _number(row.get("point_value"))}
            for row in (cur.fetchall() or [])
        ]
        return {
            "closedTrades": trades,
            "equityCurve": equity_curve,
            "historyStorage": {"version": 2, "lightweightLegacyRead": True},
        }

    @staticmethod
    def _persist_details(cur, run_id: int, user_id: int, strategy_id: int | None, result: dict[str, Any]) -> None:
        trade_rows = [
            (
                run_id,
                int(user_id),
                int(strategy_id) if strategy_id is not None else None,
                index,
                str(trade.get("exit_time") or ""),
                "close",
                str(trade.get("side") or ""),
                float(trade.get("exit_price") or 0),
                float(trade.get("quantity") or 0),
                float(trade.get("profit") or 0),
                float(trade.get("balance") or 0),
                str(trade.get("close_reason") or ""),
                json.dumps(trade, ensure_ascii=False),
            )
            for index, trade in enumerate(result.get("closedTrades") or [], start=1)
        ]
        if trade_rows:
            cur.executemany(
                """
                INSERT INTO qd_backtest_trades
                (run_id, user_id, strategy_id, trade_index, trade_time, trade_type, side,
                 price, amount, profit, balance, reason, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW())
                """,
                trade_rows,
            )

        equity_rows = [
            (
                run_id,
                index,
                str(point.get("time") or ""),
                float(point.get("value") or 0),
            )
            for index, point in enumerate(result.get("equityCurve") or [], start=1)
        ]
        if equity_rows:
            cur.executemany(
                """
                INSERT INTO qd_backtest_equity_points
                (run_id, point_index, point_time, point_value, created_at)
                VALUES (?, ?, ?, ?, NOW())
                """,
                equity_rows,
            )

    @staticmethod
    def _hydrate_summary(row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        try:
            item["params"] = json.loads(item.pop("params_json", "") or "{}")
        except (TypeError, ValueError):
            item["params"] = {}
        try:
            item["manifest"] = json.loads(item.pop("manifest_json", "") or "{}")
        except (TypeError, ValueError):
            item["manifest"] = {}
        item["result_status"] = item.get("result_status") or "unknown"
        item["data_kind"] = item.get("data_kind") or "unknown"
        return item

    @staticmethod
    def _hydrate(
        row: dict[str, Any],
        *,
        include_result: bool,
        fallback_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = dict(row)
        try:
            result = json.loads(item.pop("result_json", "") or "{}")
        except (TypeError, ValueError):
            result = {}
        if fallback_result:
            result = {**result, **fallback_result}
        try:
            item["params"] = json.loads(item.pop("params_json", "") or "{}")
        except (TypeError, ValueError):
            item["params"] = {}
        try:
            item["manifest"] = json.loads(item.pop("manifest_json", "") or "{}")
        except (TypeError, ValueError):
            item["manifest"] = {}
        summary_fields = {
            "total_return": "totalReturn",
            "win_rate": "winRate",
            "total_trades": "totalTrades",
            "total_executions": "totalExecutions",
            "benchmark_total_return": "benchmarkTotalReturn",
            "max_drawdown": "maxDrawdown",
            "sharpe_ratio": "sharpeRatio",
        }
        for column, field in summary_fields.items():
            value = item.get(column)
            if value is None:
                value = result.get(field)
            item[column] = value
            result.setdefault(field, value)
        item["result_status"] = item.get("result_status") or result.get("resultStatus") or "unknown"
        item["data_kind"] = item.get("data_kind") or (result.get("dataProvenance") or {}).get("kind") or "unknown"
        result.setdefault("resultStatus", item["result_status"])
        result.setdefault("dataProvenance", {"kind": item["data_kind"]})
        result.setdefault("manifest", item.get("manifest") or {})
        result.setdefault("timeframe", item.get("timeframe") or "")
        if include_result:
            item["result"] = _normalize_backtest_result(result, item)
        return item


def _sample_evenly(rows: Any, limit: int) -> list[Any]:
    items = list(rows or []) if isinstance(rows, (list, tuple)) else []
    if len(items) <= limit:
        return items
    step = (len(items) - 1) / float(limit - 1)
    indexes = sorted({min(len(items) - 1, int(round(index * step))) for index in range(limit)})
    return [items[index] for index in indexes]


def compact_backtest_result(result: dict[str, Any]) -> dict[str, Any]:
    """Bound history payload size and remove duplicate compatibility arrays."""
    if not isinstance(result, dict):
        return {}
    compact = dict(result)
    compact["closedTrades"] = _sample_evenly(
        result.get("closedTrades") or result.get("trades"),
        1500,
    )
    compact["executions"] = _sample_evenly(
        result.get("executions") or result.get("rawTrades"),
        1500,
    )
    compact.pop("trades", None)
    compact.pop("rawTrades", None)
    compact["equityCurve"] = sample_equity_curve(
        result.get("equityCurve") or [], 1200,
        _number(result.get("initialCapital"), _number(
            (result.get("executionAssumptions") or {}).get("initialCapital"),
        )),
    )
    for field, limit in (
        ("benchmarkCurve", 1200),
        ("holdingSnapshots", 600),
        ("rebalanceRecords", 800),
        ("orderLedger", 1500),
    ):
        compact[field] = _sample_evenly(result.get(field), limit)
    snapshots = result.get("reviewCandles") if isinstance(result.get("reviewCandles"), dict) else {}
    compact["reviewCandles"] = {
        str(symbol): {
            "timeframe": str((snapshot or {}).get("timeframe") or ""),
            "candles": list((snapshot or {}).get("candles") or [])[-1500:],
        }
        for symbol, snapshot in list(snapshots.items())[:12]
        if isinstance(snapshot, dict)
    }
    compact["historyStorage"] = {
        "version": 2,
        "compacted": True,
        "limits": {
            "equityCurve": 1200,
            "benchmarkCurve": 1200,
            "holdingSnapshots": 600,
            "rebalanceRecords": 800,
            "orderLedger": 1500,
            "closedTrades": 1500,
            "executions": 1500,
            "reviewCandleSymbols": 12,
            "reviewCandlesPerSymbol": 1500,
        },
    }
    return compact


def _compact_backtest_result(result: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible alias for tests and internal callers."""
    return compact_backtest_result(result)


def _normalize_backtest_result(
    result: dict[str, Any],
    run: dict[str, Any],
) -> dict[str, Any]:
    """Backfill detail fields that were not stored by early Strategy API V2 runs."""
    if not isinstance(result, dict):
        return {}

    initial_capital = _number(
        result.get("initialCapital"),
        _number(
            (result.get("executionAssumptions") or {}).get("initialCapital"),
            _number(run.get("initial_capital")),
        ),
    )
    result.setdefault("initialCapital", initial_capital)
    assumptions = result.get("executionAssumptions")
    if not isinstance(assumptions, dict):
        assumptions = {}
        result["executionAssumptions"] = assumptions
    leverage = _number(run.get("leverage"), 1.0)
    assumption_defaults = {
        "initialCapital": initial_capital,
        "startDate": str(run.get("start_date") or ""),
        "endDate": str(run.get("end_date") or ""),
        "leverageEnabled": leverage > 1.0,
        "leverage": leverage,
        "commission": _number(run.get("commission")),
        "slippage": _number(run.get("slippage")),
    }
    backfilled_fields: list[str] = []
    for field, value in assumption_defaults.items():
        if assumptions.get(field) is None:
            assumptions[field] = value
            backfilled_fields.append(f"executionAssumptions.{field}")

    executions = [
        item
        for item in (result.get("executions") or result.get("rawTrades") or [])
        if isinstance(item, dict)
    ]
    curve = result.get("equityCurve")
    if result.get("liquidated") is None and isinstance(curve, list):
        values = [
            _number(point.get("value"))
            for point in curve
            if isinstance(point, dict) and point.get("value") is not None
        ]
        first_insolvent = next((index for index, value in enumerate(values) if value <= 0), None)
        if first_insolvent is not None and (
            values[first_insolvent] < 0 or first_insolvent < len(values) - 1
        ):
            result["legacyInsolventContinuation"] = True
            backfilled_fields.append("legacyInsolventContinuation")
    if isinstance(curve, list) and curve and any(
        not isinstance(point, dict)
        or any(name not in point for name in ("cash", "grossExposure", "netExposure"))
        for point in curve
    ):
        _backfill_equity_curve(curve, executions, initial_capital)
        backfilled_fields.append("equityCurve.cashAndExposure")

    ledger = result.get("orderLedger")
    if not isinstance(ledger, list) or (not ledger and executions):
        result["orderLedger"] = _legacy_execution_ledger(executions)
        ledger = result["orderLedger"]
        backfilled_fields.append("orderLedger")

    attribution = result.get("attribution")
    if not isinstance(attribution, dict):
        attribution = {}
        result["attribution"] = attribution

    if attribution.get("feeDrag") is None:
        total_commission = _number(result.get("totalCommission"))
        if total_commission == 0.0:
            total_commission = sum(_number(item.get("commission")) for item in executions)
        attribution["feeDrag"] = total_commission / initial_capital if initial_capital else 0.0
        backfilled_fields.append("attribution.feeDrag")

    order_status = attribution.get("orderStatus")
    if not isinstance(order_status, dict) or not any(
        name in order_status for name in ("filled", "partial", "deferred", "rejected")
    ):
        attribution["orderStatus"] = _order_status_counts(ledger, executions)
        backfilled_fields.append("attribution.orderStatus")

    if backfilled_fields:
        compatibility = result.get("compatibility")
        if not isinstance(compatibility, dict):
            compatibility = {}
            result["compatibility"] = compatibility
        compatibility["legacyBackfill"] = True
        compatibility["backfilledFields"] = backfilled_fields

    return result


def _backfill_equity_curve(
    curve: list[Any],
    executions: list[dict[str, Any]],
    initial_capital: float,
) -> None:
    ordered_executions = sorted(
        (item for item in executions if isinstance(item, dict)),
        key=lambda item: _time_key(item.get("time") or item.get("eventTime")),
    )
    cash = float(initial_capital)
    execution_index = 0

    for point in curve:
        if not isinstance(point, dict):
            continue
        point_time = _time_key(point.get("time"))
        while execution_index < len(ordered_executions):
            execution = ordered_executions[execution_index]
            if _time_key(execution.get("time") or execution.get("eventTime")) > point_time:
                break
            side = str(execution.get("side") or "").strip().lower()
            quantity = abs(_number(execution.get("quantity") or execution.get("filledQuantity")))
            price = _number(execution.get("price"))
            notional = abs(_number(execution.get("notional")))
            if notional <= 0.0:
                notional = quantity * price
            commission = abs(_number(execution.get("commission")))
            if side == "buy":
                cash -= notional + commission
            elif side == "sell":
                cash += notional - commission
            execution_index += 1

        value = _number(point.get("value"))
        point.setdefault("cash", round(cash, 8))
        net_market_value = value - _number(point.get("cash"))
        net_exposure = net_market_value / value if value else 0.0
        point.setdefault("netExposure", net_exposure)

        # Legacy results did not retain per-symbol marks, so absolute net exposure
        # is the only recoverable gross-exposure estimate for mixed books.
        point.setdefault("grossExposure", abs(net_exposure))


def _legacy_execution_ledger(executions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ledger: list[dict[str, Any]] = []
    for index, execution in enumerate(executions, start=1):
        if not isinstance(execution, dict):
            continue
        quantity = abs(_number(execution.get("quantity")))
        status = str(execution.get("status") or "filled").strip().lower()
        if status not in {"filled", "partial", "deferred", "rejected"}:
            status = "filled"
        ledger.append({
            "orderId": str(execution.get("order_id") or f"legacy-execution-{index}"),
            "symbol": str(execution.get("symbol") or ""),
            "kind": str(execution.get("type") or "legacy_execution"),
            "value": _number(execution.get("notional")),
            "reason": str(execution.get("reason") or "strategy"),
            "status": status,
            "statusReason": "legacy_execution_record",
            "signalTime": str(execution.get("signal_time") or execution.get("time") or ""),
            "eventTime": str(execution.get("time") or ""),
            "attempt": 1,
            "requestedQuantity": quantity,
            "filledQuantity": quantity,
            "price": _number(execution.get("price")),
            "commission": _number(execution.get("commission")),
        })
    return ledger


def _order_status_counts(
    ledger: list[dict[str, Any]],
    executions: list[dict[str, Any]],
) -> dict[str, int]:
    names = ("filled", "partial", "deferred", "rejected")
    if ledger:
        return {
            name: sum(
                1 for item in ledger
                if isinstance(item, dict) and str(item.get("status") or "").lower() == name
            )
            for name in names
        }
    return {
        "filled": len(executions),
        "partial": 0,
        "deferred": 0,
        "rejected": 0,
    }


def _time_key(value: Any) -> float:
    raw = str(value or "").strip()
    if not raw:
        return float("inf")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return float("inf")


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)
    return number if number == number and number not in (float("inf"), float("-inf")) else float(default)


def _nullable_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _nullable_int(value: Any) -> int | None:
    number = _nullable_number(value)
    return int(number) if number is not None else None


class FactorResearchRepository:
    def persist_run(
        self,
        *,
        user_id: int,
        source_id: int | None,
        source_name: str,
        market: str,
        timeframe: str,
        start_date: str,
        end_date: str,
        factor_id: str,
        groups: int,
        holding_period: int,
        commission: float,
        slippage: float,
        neutralize_industry: bool,
        manifest: dict[str, Any],
        result: dict[str, Any],
        code: str,
        universe_id: int | None = None,
        universe_name: str = "",
        research_mode: str = "strategy",
    ) -> int | None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_factor_research_runs
                (user_id, source_id, source_name, universe_id, universe_name, research_mode,
                 market, timeframe, start_date, end_date,
                 factor_id, groups_count, holding_period, commission, slippage,
                 neutralize_industry, universe_size, manifest_json, code_hash, result_json,
                 rank_ic, icir, coverage, net_long_short_return, observation_count,
                 summary_backfilled, status, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, 'success', '', NOW())
                """,
                (
                    int(user_id), int(source_id) if source_id else None, str(source_name or ""),
                    int(universe_id) if universe_id else None, str(universe_name or ""),
                    str(research_mode or "strategy"), str(market or ""),
                    str(timeframe or ""), str(start_date), str(end_date), str(factor_id or ""),
                    int(groups), int(holding_period), float(commission), float(slippage),
                    bool(neutralize_industry), int(result.get("symbolsUsed") or 0),
                    json.dumps(manifest, ensure_ascii=False),
                    strategy_source_code_hash(code) if code else "",
                    json.dumps(result, ensure_ascii=False),
                    _nullable_number(result.get("rankIc")),
                    _nullable_number(result.get("icir")),
                    _nullable_number(result.get("coverage")),
                    _nullable_number(result.get("netLongShortReturn")),
                    len(result.get("icSeries") or []),
                ),
            )
            run_id = int(cur.lastrowid or 0) or None
            db.commit()
            cur.close()
        return run_id

    def list_runs(
        self,
        *,
        user_id: int,
        source_id: int | None = None,
        research_type: str = "all",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where = ["user_id = ?"]
        params: list[Any] = [int(user_id)]
        if source_id is not None:
            where.append("source_id = ?")
            params.append(int(source_id))
        if research_type == "single":
            where.append("research_mode <> 'benchmark'")
        elif research_type == "benchmark":
            where.append("research_mode = 'benchmark'")
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                f"""
                SELECT id, user_id, source_id, source_name, universe_id, universe_name,
                       research_mode, market, timeframe, start_date,
                       end_date, factor_id, groups_count, holding_period, commission, slippage,
                       neutralize_industry, universe_size, manifest_json, code_hash,
                       rank_ic, icir, coverage, net_long_short_return, observation_count,
                       status, created_at
                FROM qd_factor_research_runs
                WHERE {' AND '.join(where)}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, int(limit), int(offset)),
            )
            rows = cur.fetchall() or []
            cur.close()
        return [self._hydrate_summary(row) for row in rows]

    def list_benchmark_runs(
        self,
        *,
        user_id: int,
        benchmark_id: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, user_id, source_id, source_name, universe_id, universe_name,
                       research_mode, market, timeframe, start_date,
                       end_date, factor_id, groups_count, holding_period, commission, slippage,
                       neutralize_industry, universe_size, manifest_json, code_hash, result_json,
                       status, created_at
                FROM qd_factor_research_runs
                WHERE user_id = ? AND research_mode = 'benchmark'
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(user_id), max(1, min(5000, int(limit)))),
            )
            rows = cur.fetchall() or []
            cur.close()
        hydrated = [self._hydrate(row, include_result=True) for row in rows]
        if not benchmark_id:
            return hydrated
        target = str(benchmark_id)
        return [
            item
            for item in hydrated
            if str((item.get("result") or {}).get("benchmarkId") or "") == target
        ]

    def get_run(self, *, user_id: int, run_id: int) -> Optional[dict[str, Any]]:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, user_id, source_id, source_name, universe_id, universe_name,
                       research_mode, market, timeframe, start_date,
                       end_date, factor_id, groups_count, holding_period, commission, slippage,
                       neutralize_industry, universe_size, manifest_json, code_hash, result_json,
                       status, created_at
                FROM qd_factor_research_runs
                WHERE id = ? AND user_id = ?
                """,
                (int(run_id), int(user_id)),
            )
            row = cur.fetchone()
            cur.close()
        return self._hydrate(row, include_result=True) if row else None

    @staticmethod
    def _hydrate_summary(row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        try:
            item["manifest"] = json.loads(item.pop("manifest_json", "") or "{}")
        except (TypeError, ValueError):
            item["manifest"] = {}
        return item

    @staticmethod
    def _hydrate(row: dict[str, Any], *, include_result: bool) -> dict[str, Any]:
        item = dict(row)
        try:
            result = json.loads(item.pop("result_json", "") or "{}")
        except (TypeError, ValueError):
            result = {}
        try:
            item["manifest"] = json.loads(item.pop("manifest_json", "") or "{}")
        except (TypeError, ValueError):
            item["manifest"] = {}
        item["rank_ic"] = result.get("rankIc")
        item["icir"] = result.get("icir")
        item["coverage"] = result.get("coverage")
        item["net_long_short_return"] = result.get("netLongShortReturn")
        item["observation_count"] = len(result.get("icSeries") or [])
        if include_result:
            item["result"] = result
        return item
