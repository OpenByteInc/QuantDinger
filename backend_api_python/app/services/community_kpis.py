"""KPI aggregation helpers for marketplace backtest evidence."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logger import get_logger

logger = get_logger(__name__)
MARKETPLACE_SCORE_VERSION = 2


def _finite_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _score_result(result: Dict[str, Any]) -> float:
    trades = max(0.0, _finite_number(result.get("totalTrades")))
    if trades <= 0:
        return 0.0
    total_return = _finite_number(result.get("totalReturn"))
    sharpe = _finite_number(result.get("sharpeRatio"))
    drawdown = abs(_finite_number(result.get("maxDrawdown")))
    win_rate = _finite_number(result.get("winRate"))
    profit_factor = max(0.0, _finite_number(result.get("profitFactor")))

    return_score = _clamp(
        50.0 + math.copysign(20.0 * math.log1p(abs(total_return) / 10.0), total_return)
    )
    sharpe_score = _clamp(50.0 + sharpe * 20.0)
    drawdown_score = _clamp(70.0 - drawdown * 1.4)
    win_rate_score = _clamp(win_rate)
    profit_factor_score = _clamp(50.0 + 30.0 * math.log(max(profit_factor, 0.05)))
    evidence_score = _clamp(trades / 30.0 * 100.0)

    weighted_score = (
        return_score * 0.25
        + sharpe_score * 0.30
        + drawdown_score * 0.20
        + win_rate_score * 0.10
        + profit_factor_score * 0.10
        + evidence_score * 0.05
    )
    evidence_confidence = 0.65 + 0.35 * min(trades / 30.0, 1.0)
    return _clamp(weighted_score * evidence_confidence)


def parse_backtest_result(raw: str) -> Optional[Dict[str, Any]]:
    """Decode a backtest result JSON string."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        result = json.loads(raw)
        return result if isinstance(result, dict) else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def summarise_backtest_runs(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate successful backtest runs into a representative KPI block.

    The displayed KPIs intentionally come from the same representative
    backtest as the equity curve. This keeps marketplace cards, detail
    numbers, symbol/timeframe labels, and the curve from telling different
    stories.
    """
    empty = {
        "score_version": MARKETPLACE_SCORE_VERSION,
        "score": 0.0,
        "total_return": 0.0,
        "annual_return": 0.0,
        "sharpe": 0.0,
        "max_drawdown": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "profit_loss_ratio": 0.0,
        "winning_trades": 0,
        "losing_trades": 0,
        "sample_size": 0,
        "best_run_id": None,
        "symbols": [],
        "timeframes": [],
    }
    if not runs:
        return empty

    scored: List[Tuple[float, int, Dict[str, Any]]] = []
    symbols: List[str] = []
    timeframes: List[str] = []

    for run in runs:
        result = parse_backtest_result(run.get("result_json"))
        if not result:
            continue
        try:
            score_val = _score_result(result)
        except Exception:
            logger.debug("score_result failed for run %s", run.get("id"), exc_info=True)
            score_val = 0.0

        scored.append((score_val, int(run.get("id") or 0), result))

        symbol = (run.get("symbol") or "").strip()
        timeframe = (run.get("timeframe") or "").strip()
        if symbol:
            symbols.append(symbol)
        if timeframe:
            timeframes.append(timeframe)

    if not scored:
        return empty

    def dedupe(values: List[str]) -> List[str]:
        seen = set()
        output = []
        for value in values:
            if value and value not in seen:
                seen.add(value)
                output.append(value)
        return output

    best = max(scored, key=lambda item: (item[0], item[1]))
    best_score, best_run_id, best_result = best
    annual_return = best_result.get("annualizedReturn")
    if annual_return is None:
        annual_return = best_result.get("annualReturn")
    if annual_return is None:
        annual_return = best_result.get("annual_return")
    profit_loss_ratio = best_result.get("profitLossRatio")
    if profit_loss_ratio is None:
        profit_loss_ratio = best_result.get("profit_loss_ratio")
    if profit_loss_ratio is None:
        average_win = float(best_result.get("avgWin") or best_result.get("avg_win") or 0)
        average_loss = abs(float(best_result.get("avgLoss") or best_result.get("avg_loss") or 0))
        profit_loss_ratio = average_win / average_loss if average_loss > 0 else 0.0
    return {
        "score_version": MARKETPLACE_SCORE_VERSION,
        "score": round(float(best_score or 0), 2),
        "total_return": round(float(best_result.get("totalReturn") or 0), 2),
        "annual_return": round(float(annual_return or 0), 2),
        "sharpe": round(float(best_result.get("sharpeRatio") or 0), 2),
        "max_drawdown": round(float(best_result.get("maxDrawdown") or 0), 2),
        "win_rate": round(float(best_result.get("winRate") or 0), 2),
        "profit_factor": round(float(best_result.get("profitFactor") or 0), 2),
        # Payoff ratio is average winning trade / average losing trade. Keep it
        # separate from profit factor (gross profit / gross loss): the latter
        # can become extremely large when a run contains only one tiny loss.
        "profit_loss_ratio": round(float(profit_loss_ratio or 0), 2),
        "winning_trades": int(best_result.get("winningTrades") or best_result.get("winning_trades") or 0),
        "losing_trades": int(best_result.get("losingTrades") or best_result.get("losing_trades") or 0),
        "sample_size": len(scored),
        "best_run_id": best_run_id or None,
        "symbols": dedupe(symbols),
        "timeframes": dedupe(timeframes),
    }


def _decode_marketplace_kpis(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    if not isinstance(value, dict):
        return None
    if int(value.get("score_version") or 0) != MARKETPLACE_SCORE_VERSION:
        return None
    normalized = summarise_backtest_runs([])
    normalized.update({key: value[key] for key in normalized if key in value})
    return normalized


def fetch_market_asset_kpis(
    cur: Any,
    assets: List[Dict[str, Any]],
    *,
    prefer_persisted: bool = True,
    persist_missing: bool = False,
) -> Dict[int, Dict[str, Any]]:
    """Load representative KPIs for marketplace assets.

    Chart-only indicators do not own backtest records. Script templates and
    bot presets use their persisted source ids because their backtests are
    stored as strategy-script/strategy runs. Marketplace listings normally
    use the immutable KPI snapshot created at publication time. Missing legacy
    snapshots can be rebuilt in batches and persisted once.
    """
    if not assets:
        return {}

    output: Dict[int, Dict[str, Any]] = {}
    unresolved_assets: List[Dict[str, Any]] = []
    for asset in assets:
        asset_id = int(asset.get("id") or 0)
        asset_type = asset.get("asset_type") or "indicator"
        if not asset_id:
            continue
        if asset_type == "indicator":
            output[asset_id] = summarise_backtest_runs([])
            continue
        persisted = _decode_marketplace_kpis(asset.get("marketplace_kpis")) if prefer_persisted else None
        if persisted is not None:
            output[asset_id] = persisted
        else:
            unresolved_assets.append(asset)

    source_ids = sorted({
        int(asset.get("source_script_source_id") or 0)
        for asset in unresolved_assets
        if int(asset.get("source_script_source_id") or 0) > 0
    })
    strategy_ids = sorted({
        int(asset.get("source_strategy_id") or 0)
        for asset in unresolved_assets
        if not int(asset.get("source_script_source_id") or 0)
        and int(asset.get("source_strategy_id") or 0) > 0
    })
    rows_by_source: Dict[int, List[Dict[str, Any]]] = {source_id: [] for source_id in source_ids}
    rows_by_strategy: Dict[int, List[Dict[str, Any]]] = {strategy_id: [] for strategy_id in strategy_ids}

    def load_grouped_runs(column: str, identifiers: List[int], target: Dict[int, List[Dict[str, Any]]]) -> None:
        if not identifiers:
            return
        placeholders = ",".join(["%s"] * len(identifiers))
        cur.execute(
            f"""
            SELECT id, source_id, strategy_id, symbol, timeframe, start_date, end_date,
                   code_hash, result_json
            FROM qd_backtest_runs
            WHERE {column} IN ({placeholders})
              AND status = 'success'
              AND result_json IS NOT NULL AND result_json != ''
            """,
            tuple(identifiers),
        )
        for raw_row in cur.fetchall() or []:
            row = dict(raw_row)
            identifier = int(row.get(column) or 0)
            if identifier in target:
                target[identifier].append(row)

    loaded_columns = set()
    for column, identifiers, target in (
        ("source_id", source_ids, rows_by_source),
        ("strategy_id", strategy_ids, rows_by_strategy),
    ):
        if not identifiers:
            continue
        try:
            load_grouped_runs(column, identifiers, target)
            loaded_columns.add(column)
        except Exception:
            logger.debug("Marketplace KPI batch query failed for %s", column, exc_info=True)

    persistable_ids = []
    for asset in unresolved_assets:
        asset_id = int(asset.get("id") or 0)
        source_script_id = int(asset.get("source_script_source_id") or 0)
        source_strategy_id = int(asset.get("source_strategy_id") or 0)
        rows = rows_by_source.get(source_script_id, []) if source_script_id else rows_by_strategy.get(source_strategy_id, [])
        source_code_hash = str(asset.get("source_code_hash") or "").strip()
        if source_code_hash:
            rows = [row for row in rows if str(row.get("code_hash") or "").strip() == source_code_hash]
        output[asset_id] = summarise_backtest_runs(rows)
        identity_column = "source_id" if source_script_id else "strategy_id"
        if identity_column in loaded_columns:
            persistable_ids.append(asset_id)

    if persist_missing and persistable_ids:
        cur.executemany(
            """
            UPDATE qd_indicator_codes
            SET marketplace_kpis = ?::jsonb, marketplace_kpis_updated_at = NOW()
            WHERE id = ?
            """,
            [
                (json.dumps(output[asset_id], ensure_ascii=False), asset_id)
                for asset_id in persistable_ids
            ],
        )

    return {int(asset.get("id") or 0): output.get(int(asset.get("id") or 0), summarise_backtest_runs([])) for asset in assets}
