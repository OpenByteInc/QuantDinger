"""Optimization engine independent from HTTP and existing backtest routes."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from statistics import mean, median
from time import perf_counter
from typing import Any

from .constraints import ParameterConstraint, first_rejection
from .models import EvolutionConfig, SearchParameter
from .search import ParameterSampler
from .statistics import (
    composite_score,
    deflated_sharpe,
    equity_returns,
    metric_snapshot,
    monte_carlo_bootstrap,
    parameter_heatmap,
    probability_of_backtest_overfitting,
    sample_sharpe,
    training_score,
)
from .walk_forward import WalkForwardFold, build_walk_forward_plan


Evaluator = Callable[[dict[str, Any], datetime, datetime, float, float], dict[str, Any]]
ProgressCallback = Callable[[dict[str, Any]], None]


class StrategyEvolutionFailure(ValueError):
    """A user-facing evolution failure with structured diagnostics."""

    def __init__(self, code: str, details: dict[str, Any]) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


class StrategyEvolutionEngine:
    def __init__(self, evaluator: Evaluator) -> None:
        self.evaluator = evaluator

    def run(
        self,
        *,
        parameters: list[SearchParameter],
        config: EvolutionConfig,
        start_date: datetime,
        end_date: datetime,
        commission: float,
        slippage: float,
        constraints: tuple[ParameterConstraint, ...] = (),
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        started_at = perf_counter()
        if not parameters:
            raise ValueError("strategyEvolution.parametersRequired")
        if len(parameters) > 8:
            raise ValueError("strategyEvolution.parameterLimitExceeded")
        context_provider = getattr(self.evaluator, "walk_forward_context", None)
        walk_forward_context = (
            context_provider(start_date, end_date)
            if callable(context_provider)
            else {}
        )
        plan = build_walk_forward_plan(
            start_date,
            end_date,
            folds=config.folds,
            train_ratio=config.train_ratio,
            blind_ratio=config.blind_ratio,
            observations=walk_forward_context.get("observations"),
            warmup_bars=max(
                int(walk_forward_context.get("warmupBars") or 0),
                parameter_lookback_bars(parameters),
            ),
            embargo_bars=config.embargo_bars,
        )
        sampler = ParameterSampler(parameters, seed=config.seed)
        search_space_size = sampler.space_size()
        constraint_rejections = 0
        duplicate_rejections = 0
        if config.method == "grid":
            grid, grid_stats = sampler.grid_with_stats(
                config.trials,
                accept=lambda params: first_rejection(constraints, params) is None,
            )
            constraint_rejections = int(grid_stats["rejected"])
        elif config.method == "random":
            grid = sampler.stratified_candidates(min(config.trials, search_space_size))
        else:
            grid = []
        trial_count = len(grid) if config.method in {"grid", "random"} else min(config.trials, search_space_size)
        fallback_grid: list[dict[str, Any]] = []
        if config.method != "grid" and search_space_size <= 100_000:
            fallback_grid, _fallback_stats = sampler.grid_with_stats(
                min(search_space_size, max(trial_count * 3, trial_count)),
                accept=lambda params: first_rejection(constraints, params) is None,
            )
        fallback_index = 0
        minimum_recommended_trials = _minimum_recommended_trials(
            method=config.method,
            parameter_count=len(parameters),
            search_space_size=search_space_size,
        )
        history: list[tuple[dict[str, Any], float]] = []
        trials: list[dict[str, Any]] = []
        seen: set[tuple[tuple[str, str], ...]] = set()
        attempts = 0
        max_attempts = trial_count if config.method == "grid" else max(trial_count * 30, trial_count + 100)
        while len(trials) < trial_count and attempts < max_attempts:
            params = self._candidate(config, sampler, grid, history, len(trials))
            attempts += 1
            identity = tuple(sorted((str(key), repr(value)) for key, value in params.items()))
            rejection = first_rejection(constraints, params)
            if rejection is not None or identity in seen:
                if rejection is not None:
                    constraint_rejections += 1
                else:
                    duplicate_rejections += 1
                replacement = None
                while fallback_index < len(fallback_grid):
                    candidate = fallback_grid[fallback_index]
                    fallback_index += 1
                    candidate_identity = tuple(sorted((str(key), repr(value)) for key, value in candidate.items()))
                    if candidate_identity not in seen:
                        replacement = (candidate, candidate_identity)
                        break
                if replacement is None:
                    continue
                params, identity = replacement
            seen.add(identity)
            trial = self._evaluate_trial(
                len(trials) + 1,
                params,
                plan.folds,
                config,
                commission,
                slippage,
                [float(row[1]) for row in history],
            )
            trials.append(trial)
            if not trial["pruned"]:
                history.append((params, float(trial["searchScore"])))
            if on_progress:
                on_progress({
                    "phase": "search",
                    "completed": len(trials),
                    "total": trial_count,
                    "attempted": attempts,
                    "bestScore": max((row["searchScore"] for row in trials if not row["pruned"]), default=0.0),
                })
        searched = sorted(
            (row for row in trials if not row["pruned"]),
            key=lambda row: row["searchScore"],
            reverse=True,
        )
        if not searched:
            raise StrategyEvolutionFailure(
                "strategyEvolution.allTrialsPruned",
                self._failure_diagnostics(trials),
            )
        shortlist_size = min(
            len(searched),
            max(config.top_candidates * 2, int(math.ceil(math.sqrt(len(searched))))),
        )
        validation_eligible = [
            row for row in searched
            if float(row["components"].get("validationActivity") or 0.0) > 0
        ]
        if not validation_eligible:
            raise StrategyEvolutionFailure(
                "strategyEvolution.allTrialsPruned",
                self._failure_diagnostics(trials),
            )
        completed = sorted(
            validation_eligible[:shortlist_size],
            key=lambda row: (
                float(row["score"]),
                -float(row["components"].get("maxDrawdown") or 0.0),
                float(row["components"].get("validationActivity") or 0.0),
                float(row["searchScore"]),
            ),
            reverse=True,
        )
        validation_diagnostics = self._rank_validation_candidates(completed)
        best = completed[0]
        blind_result = None
        if plan.blind_start and plan.blind_end:
            blind_result = self.evaluator(best["params"], plan.blind_start, plan.blind_end, commission, slippage)
        final_result = blind_result or best["validationResults"][-1]
        blind_metrics = metric_snapshot(blind_result) if blind_result else None
        diagnostic_results = list(best["validationResults"])
        if blind_result:
            diagnostic_results.append(blind_result)
        returns = [
            value
            for diagnostic_result in diagnostic_results
            for value in equity_returns(diagnostic_result.get("equityCurve") or [])
        ]
        score_matrix = [[float(item["score"]) for item in row["folds"] if "score" in item] for row in completed]
        pbo = probability_of_backtest_overfitting(score_matrix)
        dsr = deflated_sharpe(
            returns,
            observed_sharpe=sample_sharpe(returns),
            trials=len(completed),
        )
        monte_carlo = monte_carlo_bootstrap(
            returns,
            paths=config.monte_carlo_paths,
            block_size=config.block_size,
            seed=config.seed + 991,
        )
        cost_stress = self._cost_stress(
            best["params"],
            plan.blind_start or start_date,
            plan.blind_end or end_date,
            commission,
            slippage,
            config.cost_multipliers,
        )
        robustness = self._robustness_grade(pbo, dsr, monte_carlo, cost_stress)
        blind_passed = bool(
            blind_metrics
            and blind_metrics["activity"] > 0
            and blind_metrics["return"] > 0
        )
        sample_sufficient = len(trials) >= min(minimum_recommended_trials, search_space_size)
        validation_discrimination_sufficient = bool(
            best["components"].get("validationActivity", 0) >= 2
            and validation_diagnostics["distinctValidationBehaviors"] >= min(3, len(completed))
            and validation_diagnostics["distinctValidationReturns"] >= min(2, len(completed))
        )
        recommended = bool(
            sample_sufficient
            and validation_discrimination_sufficient
            and best["components"]["oosReturn"] > 0
            and (blind_metrics is None or blind_passed)
        )
        selection_objective = "trainingCompositeThenOosReturn"
        return {
            "resultVersion": 3,
            "status": "complete",
            "method": config.method,
            "config": asdict(config),
            "plan": {
                **plan.metadata(),
                **{
                    key: value
                    for key, value in walk_forward_context.items()
                    if key != "observations"
                },
            },
            "summary": {
                "bestScore": best["score"],
                "bestSearchScore": best["searchScore"],
                "selectionObjective": selection_objective,
                "candidateScoreMetric": "oosCompoundedReturn",
                "searchScoreMetric": "trainingCompositeScore",
                "robustnessScore": robustness["score"],
                "robustnessGrade": robustness["grade"],
                "availableChecks": robustness["availableChecks"],
                "totalChecks": robustness["totalChecks"],
                "oosReturn": best["components"]["oosReturn"],
                "oosSharpe": best["components"]["oosSharpe"],
                "maxDrawdown": best["components"]["maxDrawdown"],
                "decayRate": best["components"]["decayRate"],
                "positiveValidationFolds": best["components"]["positiveValidationFolds"],
                "validationFolds": best["components"]["validationFolds"],
                "profitableFoldRate": best["components"]["profitableFoldRate"],
                "blindReturn": blind_metrics["return"] if blind_metrics else None,
                "blindActivity": blind_metrics["activity"] if blind_metrics else None,
                "blindPassed": blind_passed,
                "recommended": recommended,
                "sampleSufficient": sample_sufficient,
                "validationDiscriminationSufficient": validation_discrimination_sufficient,
                **validation_diagnostics,
                "requestedTrials": trial_count,
                "evaluatedTrials": len(trials),
                "candidateAttempts": len(trials) + constraint_rejections + duplicate_rejections,
                "minimumRecommendedTrials": minimum_recommended_trials,
                "searchSpaceSize": search_space_size,
                "coveragePercent": round((len(trials) / search_space_size) * 100.0, 4) if search_space_size else 0.0,
                "completedTrials": len(searched),
                "validationShortlistTrials": len(completed),
                "prunedTrials": sum(1 for row in trials if row["pruned"]),
                "constraintRejectedTrials": constraint_rejections,
                "overfitRejectedTrials": sum(1 for row in trials if row.get("reason") == "segmentedOverfit"),
                "duplicateTrials": duplicate_rejections,
                "actualBacktestRuns": self._evaluation_count(),
                "elapsedSeconds": round(perf_counter() - started_at, 2),
            },
            "bestParams": best["params"],
            "trials": [self._public_trial(row) for row in trials],
            "topCandidates": [self._public_trial(row) for row in completed[: config.top_candidates]],
            "convergence": self._convergence(trials),
            "heatmap": parameter_heatmap(trials),
            "equityCurve": final_result.get("equityCurve") or [],
            "validation": {
                "pbo": pbo,
                "deflatedSharpe": dsr,
                "monteCarlo": monte_carlo,
                "costStress": cost_stress,
            },
            "selection": {
                "objective": selection_objective,
                "searchObjective": "trainingCompositeScore",
                "validationObjective": "oosCompoundedReturn",
                "overfitControl": "chronologicalHoldout",
                "blindUsedForSelection": False,
                "blindPassed": blind_passed,
                "validationDiscriminationSufficient": validation_discrimination_sufficient,
                "recommended": recommended,
            },
            "constraints": [constraint.metadata() for constraint in constraints],
        }

    def _evaluate_trial(
        self,
        number: int,
        params: dict[str, Any],
        folds: tuple[WalkForwardFold, ...],
        config: EvolutionConfig,
        commission: float,
        slippage: float,
        prior_scores: list[float],
    ) -> dict[str, Any]:
        train_metrics: list[dict[str, float]] = []
        validation_metrics: list[dict[str, float]] = []
        validation_results: list[dict[str, Any]] = []
        fold_rows: list[dict[str, Any]] = []
        pruned = False
        evaluate_plan = getattr(self.evaluator, "evaluate_plan", None)
        prepared_results = evaluate_plan(params, folds, commission, slippage) if callable(evaluate_plan) else None
        for fold_index, fold in enumerate(folds):
            if prepared_results is not None:
                train_result, validation_result = prepared_results[fold_index]
            else:
                train_result = self.evaluator(params, fold.train_start, fold.train_end, commission, slippage)
                validation_result = self.evaluator(params, fold.validation_start, fold.validation_end, commission, slippage)
            train_metric = metric_snapshot(train_result)
            validation_metric = metric_snapshot(validation_result)
            train_metrics.append(train_metric)
            validation_metrics.append(validation_metric)
            validation_results.append(validation_result)
            quality_score, _partial_components = composite_score(train_metrics, validation_metrics, config.weights)
            partial_score, _partial_training_components = training_score(train_metrics, config.weights)
            fold_rows.append({
                **fold.metadata(),
                "train": train_metric,
                "validation": validation_metric,
                "score": validation_metric["return"],
                "searchScore": partial_score,
                "selectionScore": validation_metric["return"],
                "qualityScore": quality_score,
            })
            no_training_activity = train_metric.get("activity", 0) <= 0
            invalid_metrics = train_metric.get("valid", 0) <= 0 or validation_metric.get("valid", 0) <= 0
            if invalid_metrics:
                pruned = True
                break
            if config.auto_prune and no_training_activity:
                pruned = True
                break
            if config.auto_prune and len(fold_rows) >= 2 and len(prior_scores) >= 6:
                threshold = median(prior_scores) - 8.0
                catastrophic = train_metric["drawdown"] <= -70.0
                if partial_score < threshold or catastrophic:
                    pruned = True
                    break
        quality_score, components = composite_score(train_metrics, validation_metrics, config.weights)
        search_score, training_components = training_score(train_metrics, config.weights)
        score = components["oosReturn"]
        positive_folds = sum(1 for row in validation_metrics if row["return"] > 0)
        validation_folds = len(validation_metrics)
        profitable_fold_rate = positive_folds / validation_folds if validation_folds else 0.0
        components.update({
            "qualityScore": quality_score,
            "trainingScore": search_score,
            "training": training_components,
            "positiveValidationFolds": positive_folds,
            "validationFolds": validation_folds,
            "profitableFoldRate": round(profitable_fold_rate * 100.0, 2),
        })
        no_validation_activity = components.get("validationActivity", 0) <= 0
        reason = (
            "invalidMetrics"
            if any(row.get("valid", 0) <= 0 for row in train_metrics + validation_metrics)
            else "noValidationActivity"
            if no_validation_activity
            else "autoPruned"
            if pruned
            else ""
        )
        return {
            "number": number,
            "params": params,
            "score": score,
            "searchScore": search_score,
            "components": components,
            "folds": fold_rows,
            "pruned": pruned,
            "reason": reason,
            "validationResults": validation_results,
            "validationSignature": self._validation_signature(validation_results),
        }

    @staticmethod
    def _skipped_trial(number: int, params: dict[str, Any], reason: str) -> dict[str, Any]:
        return {
            "number": number,
            "params": params,
            "score": 0.0,
            "searchScore": 0.0,
            "components": {
                "oosReturn": 0.0,
                "oosSharpe": 0.0,
                "maxDrawdown": 0.0,
                "decayRate": 0.0,
                "validationActivity": 0.0,
                "activeValidationFolds": 0,
                "activityEvidence": 0.0,
                "qualityScore": 0.0,
                "positiveValidationFolds": 0,
                "validationFolds": 0,
                "profitableFoldRate": 0.0,
            },
            "folds": [],
            "pruned": True,
            "reason": reason,
            "validationResults": [],
            "validationSignature": "",
        }

    def _evaluation_count(self) -> int | None:
        stats = getattr(self.evaluator, "stats", None)
        if isinstance(stats, dict):
            return int(stats.get("backtestRuns") or 0)
        return None

    @staticmethod
    def _segmented_overfit_reason(
        validation_metrics: list[dict[str, float]],
        components: dict[str, float],
    ) -> str:
        if len(validation_metrics) < 4 or components.get("oosReturn", 0.0) <= 0:
            return ""
        positive_returns = [row["return"] for row in validation_metrics if row["return"] > 0]
        if len(positive_returns) < len(validation_metrics) / 2:
            return "segmentedOverfit"
        positive_total = sum(positive_returns)
        if positive_total > 0 and max(positive_returns) / positive_total > 0.85:
            return "segmentedOverfit"
        return ""

    def _failure_diagnostics(self, trials: list[dict[str, Any]]) -> dict[str, Any]:
        reason_counts = Counter(str(row.get("reason") or "unknown") for row in trials)
        evaluated = [
            row for row in trials
            if row.get("reason") not in {"parameterConstraint", "duplicateCandidate"}
        ]
        active_training_trials = sum(
            1 for row in evaluated
            if any(float((fold.get("train") or {}).get("activity") or 0) > 0 for fold in row.get("folds") or [])
        )
        active_validation_trials = sum(
            1 for row in evaluated
            if float((row.get("components") or {}).get("validationActivity") or 0) > 0
        )
        dominant_reason = max(reason_counts, key=reason_counts.get) if reason_counts else "unknown"
        message_keys = {
            "noActivity": "strategyEvolution.failure.noValidationActivity",
            "noValidationActivity": "strategyEvolution.failure.noValidationActivity",
            "parameterConstraint": "strategyEvolution.failure.parameterConstraints",
            "invalidMetrics": "strategyEvolution.failure.invalidMetrics",
            "duplicateCandidate": "strategyEvolution.failure.duplicateCandidates",
            "autoPruned": "strategyEvolution.failure.autoPruned",
            "segmentedOverfit": "strategyEvolution.failure.segmentedOverfit",
        }
        return {
            "messageKey": message_keys.get(dominant_reason, "strategyEvolution.allTrialsPruned"),
            "dominantReason": dominant_reason,
            "totalTrials": len(trials),
            "evaluatedTrials": len(evaluated),
            "activeTrainingTrials": active_training_trials,
            "activeValidationTrials": active_validation_trials,
            "actualBacktestRuns": self._evaluation_count(),
            "reasonCounts": dict(reason_counts),
        }

    def _cost_stress(
        self,
        params: dict[str, Any],
        start: datetime,
        end: datetime,
        commission: float,
        slippage: float,
        multipliers: tuple[float, ...],
    ) -> list[dict[str, float]]:
        output = []
        for multiplier in multipliers:
            result = self.evaluator(params, start, end, commission * multiplier, slippage * multiplier)
            metrics = metric_snapshot(result)
            output.append({
                "multiplier": multiplier,
                "commission": commission * multiplier,
                "slippage": slippage * multiplier,
                **metrics,
            })
        if output:
            baseline_return = output[0]["return"]
            for row in output:
                row["returnDelta"] = round(row["return"] - baseline_return, 6)
        return output

    @staticmethod
    def _candidate(config, sampler, grid, history, index):
        if config.method in {"grid", "random"}:
            return grid[index % len(grid)]
        if config.method == "tpe":
            if index > 0 and index % 5 == 0:
                return sampler.random_candidate()
            return sampler.tpe_candidate(history)
        return sampler.random_candidate()

    @staticmethod
    def _public_trial(trial: dict[str, Any]) -> dict[str, Any]:
        private_keys = {"validationResults", "validationSignature"}
        return {key: value for key, value in trial.items() if key not in private_keys}

    @staticmethod
    def _convergence(trials: list[dict[str, Any]]) -> list[dict[str, float | int | bool]]:
        best: float | None = None
        output = []
        for row in trials:
            score = float(row.get("searchScore") or 0.0)
            if not row["pruned"]:
                best = score if best is None else max(best, score)
            output.append({
                "trial": row["number"],
                "score": round(score, 4),
                "best": round(best if best is not None else score, 4),
                "pruned": row["pruned"],
            })
        return output

    @staticmethod
    def _validation_signature(results: list[dict[str, Any]]) -> str:
        event_rows: list[tuple[str, ...]] = []
        event_keys = (
            "symbol",
            "time",
            "datetime",
            "side",
            "action",
            "type",
            "position_side",
            "price",
            "quantity",
            "pnl",
            "profit",
            "reason",
        )
        metric_rows: list[tuple[float, float, float]] = []
        for result in results:
            events = result.get("executions") or result.get("closedTrades") or result.get("trades") or []
            for event in events:
                if isinstance(event, dict):
                    event_rows.append(tuple(str(event.get(key) or "") for key in event_keys))
            metrics = metric_snapshot(result)
            metric_rows.append((
                round(metrics["return"], 6),
                round(metrics["drawdown"], 6),
                round(metrics["activity"], 2),
            ))
        return repr((tuple(event_rows), tuple(metric_rows)))

    @staticmethod
    def _rank_validation_candidates(candidates: list[dict[str, Any]]) -> dict[str, int]:
        return_counts = Counter(round(float(row["score"]), 4) for row in candidates)
        behavior_counts = Counter(str(row.get("validationSignature") or "") for row in candidates)
        previous_return: float | None = None
        current_rank = 0
        for index, row in enumerate(candidates, start=1):
            return_bucket = round(float(row["score"]), 4)
            if previous_return is None or return_bucket != previous_return:
                current_rank = index
                previous_return = return_bucket
            row["rank"] = current_rank
            row["validationTieCount"] = return_counts[return_bucket]
            row["behaviorEquivalentCount"] = behavior_counts[str(row.get("validationSignature") or "")]
        return {
            "distinctValidationReturns": len(return_counts),
            "distinctValidationBehaviors": len(behavior_counts),
        }

    @staticmethod
    def _robustness_grade(pbo, dsr, monte_carlo, cost_stress) -> dict[str, Any]:
        checks = []
        if pbo.get("available"):
            checks.append(1.0 - float(pbo.get("probability") or 0.0))
        if dsr.get("available"):
            checks.append(float(dsr.get("probability") or 0.0))
        if monte_carlo.get("available"):
            checks.append(1.0 - float(monte_carlo.get("lossProbability") or 0.0))
        stressed = cost_stress[-1]["return"] if cost_stress else -100.0
        cost_available = bool(cost_stress and any(row.get("activity", 0) > 0 for row in cost_stress))
        if cost_available:
            checks.append(1.0 / (1.0 + pow(2.718281828, -stressed / 10.0)))
        score = round(100.0 * mean(checks), 2) if checks else 0.0
        grade = "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D"
        return {
            "score": score,
            "grade": grade,
            "availableChecks": len(checks),
            "totalChecks": 4,
        }


def _minimum_recommended_trials(*, method: str, parameter_count: int, search_space_size: int) -> int:
    if method == "grid":
        return min(search_space_size, 300)
    if method == "random":
        return min(search_space_size, max(64, parameter_count * 16))
    return min(search_space_size, max(32, parameter_count * 16))


def parameter_lookback_bars(parameters: list[SearchParameter]) -> int:
    tokens = ("period", "lookback", "window", "length", "bars")
    values = [
        int(parameter.maximum)
        for parameter in parameters
        if parameter.maximum is not None
        and parameter.maximum > 0
        and any(token in parameter.name.lower() for token in tokens)
    ]
    return max(values, default=0)


_parameter_lookback_bars = parameter_lookback_bars
