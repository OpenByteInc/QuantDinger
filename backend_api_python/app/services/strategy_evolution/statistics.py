"""Robustness statistics and chart-ready datasets."""

from __future__ import annotations

import math
import random
from statistics import NormalDist, mean, median, pstdev
from typing import Any, Iterable


def metric_snapshot(result: dict[str, Any]) -> dict[str, float]:
    closed_trades = _number(result.get("totalTrades"))
    executions = _number(result.get("totalExecutions"))
    metric_values = [result.get(name) for name in ("totalReturn", "sharpeRatio", "maxDrawdown", "winRate")]
    return {
        "return": _number(result.get("totalReturn")),
        "sharpe": _number(result.get("sharpeRatio")),
        "drawdown": _number(result.get("maxDrawdown")),
        "winRate": _number(result.get("winRate")),
        "trades": closed_trades,
        "activity": max(closed_trades, executions),
        "valid": 1.0 if all(_finite(value) for value in metric_values) else 0.0,
        "samples": _number(result.get("sampleCount")),
    }


def training_score(train: list[dict[str, float]], weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    train_sharpe = mean(row["sharpe"] for row in train) if train else 0.0
    train_return = _compounded_return(row["return"] for row in train)
    drawdown = mean(abs(row["drawdown"]) for row in train) if train else 100.0
    variation = pstdev(row["return"] for row in train) if len(train) > 1 else 0.0
    stability = max(0.0, 1.0 - min(1.0, variation / (abs(train_return) + 12.0)))
    activity = sum(max(0.0, row.get("activity", row.get("trades", 0.0))) for row in train)
    components = {
        "return": _sigmoid_score(train_return / 12.0),
        "sharpe": _sigmoid_score(train_sharpe),
        "drawdown": max(0.0, 1.0 - min(drawdown / 50.0, 1.0)),
        "stability": stability,
    }
    active_weights = {key: max(0.0, float(weights.get(key, 0.0))) for key in components}
    total_weight = sum(active_weights.values()) or 1.0
    raw_score = 100.0 * sum(active_weights[key] * components[key] for key in components) / total_weight
    evidence = min(1.0, activity / max(8.0, len(train) * 2.0))
    score = raw_score * math.sqrt(evidence) if activity > 0 else 0.0
    return round(score, 4), {
        **{key: round(value * 100.0, 2) for key, value in components.items()},
        "trainSharpe": round(train_sharpe, 4),
        "trainReturn": round(train_return, 4),
        "trainDrawdown": round(drawdown, 4),
        "trainingActivity": round(activity, 2),
        "trainingActivityEvidence": round(evidence * 100.0, 2),
    }


def composite_score(train: list[dict[str, float]], validation: list[dict[str, float]], weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    train_sharpe = mean(row["sharpe"] for row in train) if train else 0.0
    oos_sharpe = mean(row["sharpe"] for row in validation) if validation else 0.0
    train_return = _compounded_return(row["return"] for row in train)
    oos_return = _compounded_return(row["return"] for row in validation)
    drawdown = mean(abs(row["drawdown"]) for row in validation) if validation else 100.0
    variation = pstdev(row["return"] for row in validation) if len(validation) > 1 else 0.0
    stability = max(0.0, 1.0 - min(1.0, variation / (abs(oos_return) + 12.0)))
    decay = max(0.0, (train_sharpe - oos_sharpe) / max(abs(train_sharpe), 0.5))
    validation_activity = sum(max(0.0, row.get("activity", row.get("trades", 0.0))) for row in validation)
    active_folds = sum(1 for row in validation if row.get("activity", row.get("trades", 0.0)) > 0)
    active_fold_rate = active_folds / len(validation) if validation else 0.0
    components = {
        "return": _sigmoid_score(oos_return / 12.0),
        "sharpe": _sigmoid_score(oos_sharpe),
        "drawdown": max(0.0, 1.0 - min(drawdown / 50.0, 1.0)),
        "stability": stability,
        "decay": max(0.0, 1.0 - min(decay, 1.0)),
    }
    raw_score = 100.0 * sum(weights.get(key, 0.0) * value for key, value in components.items())
    evidence = min(1.0, validation_activity / max(8.0, len(validation) * 2.0))
    score = raw_score * math.sqrt(evidence) * active_fold_rate if validation_activity > 0 else 0.0
    return round(score, 4), {
        **{key: round(value * 100.0, 2) for key, value in components.items()},
        "trainSharpe": round(train_sharpe, 4),
        "trainReturn": round(train_return, 4),
        "oosSharpe": round(oos_sharpe, 4),
        "oosReturn": round(oos_return, 4),
        "maxDrawdown": round(drawdown, 4),
        "decayRate": round(decay * 100.0, 2),
        "validationActivity": round(validation_activity, 2),
        "activeValidationFolds": active_folds,
        "activityEvidence": round(evidence * 100.0, 2),
    }


def _compounded_return(values: Iterable[float]) -> float:
    growth = 1.0
    found = False
    for value in values:
        number = float(value)
        if not math.isfinite(number):
            continue
        growth *= max(0.0, 1.0 + number / 100.0)
        found = True
    return (growth - 1.0) * 100.0 if found else 0.0


def deflated_sharpe(returns: list[float], *, observed_sharpe: float, trials: int) -> dict[str, float | int | bool | str]:
    values = [float(value) for value in returns if math.isfinite(float(value))]
    sample_count = len(values)
    if sample_count < 3:
        return {"available": False, "reason": "insufficientObservations", "probability": 0.0, "benchmarkSharpe": 0.0, "observations": sample_count}
    avg = mean(values)
    sigma = pstdev(values)
    if sigma <= 1e-12:
        return {"available": False, "reason": "insufficientVariation", "probability": 0.0, "benchmarkSharpe": 0.0, "observations": sample_count}
    skew = mean(((value - avg) / sigma) ** 3 for value in values)
    kurtosis = mean(((value - avg) / sigma) ** 4 for value in values)
    effective_trials = max(1, int(trials))
    denominator = math.sqrt(max(1e-12, 1.0 - skew * observed_sharpe + ((kurtosis - 1.0) / 4.0) * observed_sharpe**2))
    if effective_trials == 1:
        benchmark = 0.0
    else:
        gamma = 0.5772156649
        normal = NormalDist()
        sharpe_standard_error = denominator / math.sqrt(sample_count - 1)
        benchmark = sharpe_standard_error * (
            (1.0 - gamma) * normal.inv_cdf(1.0 - 1.0 / effective_trials)
            + gamma * normal.inv_cdf(1.0 - 1.0 / (effective_trials * math.e))
        )
    statistic = (observed_sharpe - benchmark) * math.sqrt(sample_count - 1) / denominator
    return {
        "available": True,
        "probability": round(NormalDist().cdf(statistic), 6),
        "observedSharpe": round(observed_sharpe, 6),
        "benchmarkSharpe": round(benchmark, 6),
        "observations": sample_count,
        "skew": round(skew, 6),
        "kurtosis": round(kurtosis, 6),
    }


def probability_of_backtest_overfitting(score_matrix: list[list[float]], *, max_combinations: int = 2000) -> dict[str, Any]:
    matrix = [row for row in score_matrix if len(row) >= 4]
    if len(matrix) < 2:
        return {"available": False, "reason": "insufficientCandidates", "probability": 0.0, "samples": 0, "logits": []}
    periods = min(len(row) for row in matrix)
    if periods < 4:
        return {"available": False, "reason": "insufficientFolds", "probability": 0.0, "samples": 0, "logits": []}
    half = periods // 2
    combinations = list(_combinations(range(periods), half))
    if len(combinations) > max_combinations:
        rng = random.Random(1776)
        combinations = rng.sample(combinations, max_combinations)
    logits: list[float] = []
    for train_indexes in combinations:
        train_set = set(train_indexes)
        test_indexes = [index for index in range(periods) if index not in train_set]
        train_scores = [mean(row[index] for index in train_indexes) for row in matrix]
        winner = max(range(len(matrix)), key=lambda index: train_scores[index])
        test_scores = [mean(row[index] for index in test_indexes) for row in matrix]
        ordered = sorted(test_scores)
        rank = ordered.index(test_scores[winner]) + 1
        percentile = min(1.0 - 1e-9, max(1e-9, rank / (len(matrix) + 1.0)))
        logits.append(math.log(percentile / (1.0 - percentile)))
    probability = sum(1 for value in logits if value <= 0.0) / len(logits) if logits else 0.0
    return {
        "available": bool(logits),
        "probability": round(probability, 6),
        "samples": len(logits),
        "medianLogit": round(median(logits), 6) if logits else 0.0,
        "logits": [round(value, 5) for value in logits[:200]],
    }


def monte_carlo_bootstrap(returns: list[float], *, paths: int, block_size: int, seed: int) -> dict[str, Any]:
    samples = [float(value) for value in returns if math.isfinite(float(value))]
    if len(samples) < 2:
        return {"available": False, "reason": "insufficientObservations", "paths": 0, "terminalReturns": [], "percentiles": {}, "drawdownPercentiles": {}, "lossProbability": 0.0}
    if pstdev(samples) <= 1e-12:
        return {"available": False, "reason": "insufficientVariation", "paths": 0, "terminalReturns": [], "percentiles": {}, "drawdownPercentiles": {}, "lossProbability": 0.0}
    rng = random.Random(seed)
    terminal: list[float] = []
    drawdowns: list[float] = []
    length = len(samples)
    adaptive_block = max(1, int(round(length ** (1.0 / 3.0))))
    block = max(1, min(block_size, adaptive_block, length - 1))
    for _ in range(paths):
        path: list[float] = []
        while len(path) < length:
            start = rng.randrange(length)
            path.extend(samples[(start + offset) % length] for offset in range(block))
        equity = 1.0
        peak = 1.0
        worst = 0.0
        for value in path[:length]:
            equity *= 1.0 + value
            peak = max(peak, equity)
            worst = min(worst, (equity / peak - 1.0) * 100.0)
        terminal.append((equity - 1.0) * 100.0)
        drawdowns.append(worst)
    return {
        "available": True,
        "paths": paths,
        "observations": length,
        "requestedBlockSize": block_size,
        "effectiveBlockSize": block,
        "uniqueTerminalReturns": len({round(value, 10) for value in terminal}),
        "terminalReturns": _histogram(terminal, bins=24),
        "percentiles": _percentiles(terminal),
        "drawdownPercentiles": _percentiles(drawdowns),
        "lossProbability": round(sum(1 for value in terminal if value < 0) / paths, 6),
    }


def sample_sharpe(returns: list[float]) -> float:
    values = [float(value) for value in returns if math.isfinite(float(value))]
    if len(values) < 2:
        return 0.0
    sigma = pstdev(values)
    return mean(values) / sigma if sigma > 1e-12 else 0.0


def parameter_heatmap(trials: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in trials if not row.get("pruned") and row.get("params")]
    numeric_names: list[str] = []
    if completed:
        for name in completed[0]["params"]:
            values = {row["params"].get(name) for row in completed}
            if len(values) > 1 and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
                numeric_names.append(name)
    if len(numeric_names) < 2:
        return {"xParameter": "", "yParameter": "", "points": []}
    numeric_names.sort(key=lambda name: len({row["params"].get(name) for row in completed}), reverse=True)
    x_name, y_name = numeric_names[:2]
    points = [
        [row["params"][x_name], row["params"][y_name], round(float(row.get("searchScore", row.get("score")) or 0.0), 4)]
        for row in completed
    ]
    scores = [float(point[2]) for point in points]
    return {
        "xParameter": x_name,
        "yParameter": y_name,
        "scoreMetric": "trainingCompositeScore",
        "scoreMin": round(min(scores), 4) if scores else 0.0,
        "scoreMax": round(max(scores), 4) if scores else 0.0,
        "points": points,
    }


def equity_returns(curve: Iterable[dict[str, Any]]) -> list[float]:
    points = list(curve)
    daily_values: dict[str, float] = {}
    for index, point in enumerate(points):
        timestamp = str(point.get("time") or point.get("date") or "").strip()
        day = timestamp[:10] if len(timestamp) >= 10 else str(index)
        daily_values[day] = _number(point.get("value"))
    values = list(daily_values.values())
    if len(values) < 2:
        values = [_number(point.get("value")) for point in points]
    output: list[float] = []
    for previous, current in zip(values, values[1:]):
        if previous > 0:
            output.append(current / previous - 1.0)
    return output


def _histogram(values: list[float], *, bins: int) -> list[dict[str, float | int]]:
    low, high = min(values), max(values)
    if math.isclose(low, high):
        return [{"value": round(low, 4), "count": len(values)}]
    width = (high - low) / bins
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, int((value - low) / width))
        counts[index] += 1
    return [{"value": round(low + (index + 0.5) * width, 4), "count": count} for index, count in enumerate(counts)]


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {str(item): round(_quantile(ordered, item / 100.0), 4) for item in (1, 5, 25, 50, 75, 95, 99)}


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    position = (len(values) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return values[lower]
    return values[lower] * (upper - position) + values[upper] * (position - lower)


def _combinations(values: Iterable[int], size: int):
    values = tuple(values)
    if size == 0:
        yield ()
        return
    for index in range(len(values)):
        for tail in _combinations(values[index + 1 :], size - 1):
            yield (values[index],) + tail


def _number(value: Any) -> float:
    try:
        number = float(value or 0.0)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _sigmoid_score(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, value))))
