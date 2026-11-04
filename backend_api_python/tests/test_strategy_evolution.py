from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from flask import Flask, g

from app.services.strategy_evolution import (
    EvolutionConfig,
    SearchParameter,
    StrategyEvolutionEngine,
    StrategyEvolutionFailure,
    StrategyEvolutionService,
)
from app.services.strategy_evolution.search import ParameterSampler
from app.services.strategy_evolution.statistics import (
    deflated_sharpe,
    equity_returns,
    monte_carlo_bootstrap,
    probability_of_backtest_overfitting,
)
from app.services.strategy_evolution.walk_forward import build_walk_forward_plan, walk_forward_requirements
from app.services.strategy_evolution import service as evolution_service_module
from app.services.strategy_evolution.evaluator import PreparedEvolutionEvaluator
from app.services.strategy_evolution.constraints import discover_parameter_constraints
from app.services.strategy_evolution.parameter_space import adapt_source_for_parameters, build_parameter_space
from app.services.strategy_v2 import StrategyV2BacktestService
from app.routes import strategy_evolution as evolution_routes
from app.tasks import agent_jobs as celery_agent_jobs


def test_strategy_evolution_validation_messages_are_allowlisted():
    assert evolution_routes._public_validation_message(
        ValueError("strategyEvolution.sourceRequired")
    ) == "strategyEvolution.sourceRequired"
    assert evolution_routes._public_validation_message(
        ValueError("database connection details")
    ) == "strategyEvolution.runFailed"


def _evaluator(params, start, end, commission, slippage):
    fast = float(params["fast"])
    slow = float(params["slow"])
    quality = max(-1.0, 1.0 - abs(fast - 12.0) / 12.0 - abs(slow - 48.0) / 48.0)
    days = max(2, (end.date() - start.date()).days + 1)
    cost = (commission + slippage) * 1000.0
    total_return = quality * 18.0 - cost
    curve = []
    equity = 10_000.0
    for index in range(min(days, 90)):
        base_return = (total_return / 100.0) / min(days, 90)
        equity *= 1.0 + base_return * (0.85 + 0.15 * (index % 3))
        curve.append({
            "time": (start + timedelta(days=index)).isoformat(),
            "value": equity,
            "drawdown": min(0.0, -abs(index % 7 - 3) * 0.08),
        })
    return {
        "totalReturn": total_return,
        "sharpeRatio": quality * 2.0 - cost / 10.0,
        "maxDrawdown": -max(2.0, (1.0 - quality) * 25.0 + cost),
        "winRate": 55.0 + quality * 10.0,
        "totalTrades": 16,
        "equityCurve": curve,
    }


def _parameters():
    return [
        SearchParameter("fast", "integer", 6, 18, 3, default=12),
        SearchParameter("slow", "integer", 30, 66, 6, default=48),
    ]


@pytest.mark.parametrize("method", ["random", "grid", "tpe"])
def test_engine_runs_all_search_methods_and_returns_v3_validation(method):
    config = EvolutionConfig.from_payload({
        "method": method,
        "trials": 10,
        "folds": 4,
        "monteCarloPaths": 120,
        "costMultipliers": [1, 2, 3],
        "autoPrune": False,
    })
    result = StrategyEvolutionEngine(_evaluator).run(
        parameters=_parameters(),
        config=config,
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2025, 12, 31),
        commission=0.0005,
        slippage=0.0005,
    )
    assert result["status"] == "complete"
    assert len(result["trials"]) == config.trials
    assert result["bestParams"]
    assert result["heatmap"]["points"]
    assert result["validation"]["pbo"]["available"] is False
    assert result["validation"]["pbo"]["samples"] == 0
    assert result["validation"]["deflatedSharpe"]["observations"] > 0
    assert result["validation"]["monteCarlo"]["paths"] == 120
    assert [row["multiplier"] for row in result["validation"]["costStress"]] == [1.0, 2.0, 3.0]


def test_selection_ranks_candidates_by_compounded_validation_return():
    def evaluator(params, start, end, commission, slippage):
        high_return = bool(params["high_return"])
        total_return = 8.0 if high_return else 5.0
        return {
            "totalReturn": total_return,
            "sharpeRatio": 0.2 if high_return else 3.0,
            "maxDrawdown": -35.0 if high_return else -2.0,
            "winRate": 45.0 if high_return else 75.0,
            "totalTrades": 8,
            "totalExecutions": 16,
            "equityCurve": [
                {"time": start.isoformat(), "value": 10_000.0},
                {"time": end.isoformat(), "value": 10_000.0 * (1.0 + total_return / 100.0)},
            ],
        }

    result = StrategyEvolutionEngine(evaluator).run(
        parameters=[SearchParameter("high_return", "boolean", default=False)],
        config=EvolutionConfig.from_payload({
            "method": "grid",
            "trials": 2,
            "folds": 4,
            "blindRatio": 0,
            "autoPrune": False,
            "monteCarloPaths": 100,
            "costMultipliers": [1],
        }),
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2025, 12, 31),
        commission=0.0,
        slippage=0.0,
    )

    assert result["bestParams"] == {"high_return": True}
    assert result["resultVersion"] == 3
    assert result["summary"]["selectionObjective"] == "trainingCompositeThenOosReturn"
    assert result["summary"]["candidateScoreMetric"] == "oosCompoundedReturn"
    assert result["summary"]["searchScoreMetric"] == "trainingCompositeScore"
    assert result["summary"]["oosReturn"] == pytest.approx(8.0)
    assert result["summary"]["recommended"] is True


def test_equal_single_trade_validation_results_are_reported_as_an_indistinguishable_tie():
    def evaluator(params, start, end, commission, slippage):
        return {
            "totalReturn": 0.25,
            "sharpeRatio": 0.4,
            "maxDrawdown": -0.2,
            "winRate": 100.0,
            "totalTrades": 1,
            "totalExecutions": 1,
            "equityCurve": [
                {"time": start.isoformat(), "value": 10_000.0},
                {"time": end.isoformat(), "value": 10_025.0},
            ],
        }

    result = StrategyEvolutionEngine(evaluator).run(
        parameters=[SearchParameter("enabled", "boolean", default=True)],
        config=EvolutionConfig.from_payload({
            "method": "grid",
            "trials": 2,
            "folds": 1,
            "blindRatio": 0,
            "autoPrune": False,
            "monteCarloPaths": 100,
            "costMultipliers": [1],
        }),
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2025, 12, 31),
        commission=0.0,
        slippage=0.0,
    )

    assert result["summary"]["validationDiscriminationSufficient"] is False
    assert result["summary"]["distinctValidationBehaviors"] == 1
    assert result["summary"]["distinctValidationReturns"] == 1
    assert [row["rank"] for row in result["topCandidates"]] == [1, 1]
    assert all(row["validationTieCount"] == 2 for row in result["topCandidates"])
    assert result["summary"]["recommended"] is False


def test_validation_without_trades_is_rejected_instead_of_using_training_return():
    start = datetime(2024, 1, 1)
    end = datetime(2025, 12, 31)
    config = EvolutionConfig.from_payload({
        "method": "grid",
        "trials": 2,
        "folds": 4,
        "blindRatio": 0,
        "autoPrune": False,
        "monteCarloPaths": 100,
        "costMultipliers": [1],
    })
    plan = build_walk_forward_plan(
        start,
        end,
        folds=config.folds,
        train_ratio=config.train_ratio,
        blind_ratio=config.blind_ratio,
        embargo_bars=config.embargo_bars,
    )
    validation_range = (plan.folds[0].validation_start, plan.folds[0].validation_end)

    def evaluator(params, segment_start, segment_end, commission, slippage):
        is_validation = (segment_start, segment_end) == validation_range
        total_return = 0.0 if is_validation else (8.0 if params["profile"] == "strong" else 4.0)
        activity = 0 if is_validation else 8
        return {
            "totalReturn": total_return,
            "sharpeRatio": 1.0,
            "maxDrawdown": -5.0,
            "winRate": 55.0,
            "totalTrades": activity,
            "totalExecutions": activity * 2,
            "equityCurve": [
                {"time": segment_start.isoformat(), "value": 10_000.0},
                {"time": segment_end.isoformat(), "value": 10_000.0 * (1.0 + total_return / 100.0)},
            ],
        }

    with pytest.raises(StrategyEvolutionFailure) as captured:
        StrategyEvolutionEngine(evaluator).run(
            parameters=[SearchParameter("profile", "enum", choices=("weak", "strong"), default="weak")],
            config=config,
            start_date=start,
            end_date=end,
            commission=0.0,
            slippage=0.0,
        )

    assert captured.value.code == "strategyEvolution.allTrialsPruned"
    assert captured.value.details["dominantReason"] == "noValidationActivity"
    assert captured.value.details["activeTrainingTrials"] == 2
    assert captured.value.details["activeValidationTrials"] == 0


def test_blind_holdout_reports_failure_without_changing_the_selected_winner():
    start = datetime(2024, 1, 1)
    end = datetime(2025, 12, 31)
    config = EvolutionConfig.from_payload({
        "method": "grid",
        "trials": 2,
        "folds": 4,
        "blindRatio": 0.15,
        "autoPrune": False,
        "monteCarloPaths": 100,
        "costMultipliers": [1],
    })
    plan = build_walk_forward_plan(
        start,
        end,
        folds=config.folds,
        train_ratio=config.train_ratio,
        blind_ratio=config.blind_ratio,
        embargo_bars=config.embargo_bars,
    )

    def evaluator(params, segment_start, segment_end, commission, slippage):
        selected = bool(params["selected"])
        is_blind = segment_start == plan.blind_start and segment_end == plan.blind_end
        total_return = (-20.0 if selected else 20.0) if is_blind else (8.0 if selected else 5.0)
        return {
            "totalReturn": total_return,
            "sharpeRatio": 1.0 if total_return > 0 else -1.0,
            "maxDrawdown": -5.0,
            "winRate": 55.0,
            "totalTrades": 8,
            "totalExecutions": 16,
            "equityCurve": [
                {"time": segment_start.isoformat(), "value": 10_000.0},
                {"time": segment_end.isoformat(), "value": 10_000.0 * (1.0 + total_return / 100.0)},
            ],
        }

    result = StrategyEvolutionEngine(evaluator).run(
        parameters=[SearchParameter("selected", "boolean", default=False)],
        config=config,
        start_date=start,
        end_date=end,
        commission=0.0,
        slippage=0.0,
    )

    assert result["bestParams"] == {"selected": True}
    assert result["selection"]["blindUsedForSelection"] is False
    assert result["summary"]["blindReturn"] == -20.0
    assert result["summary"]["blindPassed"] is False
    assert result["summary"]["recommended"] is False
    assert result["selection"]["recommended"] is False


def test_walk_forward_keeps_blind_holdout_after_validation():
    plan = build_walk_forward_plan(
        datetime(2024, 1, 1),
        datetime(2025, 12, 31),
        folds=5,
        train_ratio=0.7,
        blind_ratio=0.15,
        embargo_days=2,
    )
    assert len(plan.folds) == 1
    assert plan.requested_folds == 1
    assert plan.effective_folds == 1
    assert plan.blind_start is not None
    assert all(fold.train_end < fold.validation_start <= fold.validation_end < plan.blind_start for fold in plan.folds)


def test_walk_forward_uses_intraday_observations_instead_of_calendar_days():
    start = datetime(2026, 8, 27)
    end = datetime(2026, 9, 25, 23, 59)
    observations = tuple(pd.date_range(start, end, freq="1min").to_pydatetime())
    plan = build_walk_forward_plan(
        start,
        end,
        folds=4,
        train_ratio=0.7,
        blind_ratio=0.15,
        embargo_bars=1,
        warmup_bars=310,
        observations=observations,
    )

    assert plan.sample_count == 43_200
    assert plan.effective_folds == 1
    assert plan.blind_bars == 6_480
    assert plan.minimum_train_bars == 1
    assert plan.minimum_validation_bars == 1
    assert plan.folds[0].train_bars == 30_240
    assert plan.folds[0].validation_bars == 6_478
    assert plan.folds[-1].validation_end < plan.blind_start


def test_walk_forward_uses_one_split_for_the_selected_observations():
    start = datetime(2026, 1, 1)
    observations = tuple(pd.date_range(start, periods=100, freq="1min").to_pydatetime())
    plan = build_walk_forward_plan(
        start,
        observations[-1],
        folds=4,
        train_ratio=0.7,
        blind_ratio=0,
        embargo_bars=1,
        warmup_bars=10,
        observations=observations,
    )

    assert plan.requested_folds == 1
    assert plan.effective_folds == 1
    assert len(plan.folds) == 1
    assert plan.folds[0].train_bars == 70
    assert plan.folds[0].validation_bars == 29


def test_walk_forward_rejects_only_when_market_observations_are_insufficient():
    start = datetime(2026, 1, 1)
    observations = tuple(pd.date_range(start, periods=2, freq="1min").to_pydatetime())
    with pytest.raises(ValueError, match="strategyEvolution.observationRangeTooShort"):
        build_walk_forward_plan(
            start,
            observations[-1],
            folds=4,
            train_ratio=0.7,
            blind_ratio=0.15,
            embargo_bars=1,
            warmup_bars=310,
            observations=observations,
        )


def test_walk_forward_requirements_match_runtime_thresholds():
    requirements = walk_forward_requirements(warmup_bars=260, embargo_bars=1)

    assert requirements.warmup_bars == 260
    assert requirements.minimum_train_bars == 1
    assert requirements.minimum_validation_bars == 1
    assert requirements.minimum_required_bars == 5


def test_service_preflight_accepts_the_user_selected_range(monkeypatch):
    source = {
        "id": 31,
        "name": "HK CTA",
        "code": "def initialize(context):\n    context.set_universe(['HKStock:03690'])\n    context.subscribe(frequency='1h')\n\ndef handle_data(context, data):\n    pass",
        "param_schema": {
            "params": [
                {"name": "entry_period", "type": "integer", "min": 10, "max": 50, "step": 1},
            ],
        },
    }

    class SourceService:
        @staticmethod
        def get_source(source_id, *, user_id):
            assert source_id == 31
            assert user_id == 7
            return source

    class Evaluator:
        def __init__(self, **kwargs):
            pass

        @staticmethod
        def walk_forward_context(start_date, end_date):
            observations = tuple(
                start_date + timedelta(hours=index)
                for index in range(147)
            )
            return {"observations": observations, "warmupBars": 260, "frequency": "1h"}

    monkeypatch.setattr(evolution_service_module, "get_script_source_service", lambda: SourceService())
    monkeypatch.setattr(evolution_service_module, "PreparedEvolutionEvaluator", Evaluator)
    result = StrategyEvolutionService(backtest_service=object()).preflight(
        user_id=7,
        payload={
            "sourceId": 31,
            "startDate": "2026-10-02",
            "endDate": "2026-11-02",
            "parameterSpace": source["param_schema"]["params"],
            "config": {"method": "random", "folds": 4},
        },
    )

    assert result["sufficient"] is True
    assert result["sampleCount"] == 147
    assert result["minimumRequiredBars"] == 3
    assert result["shortageBars"] == 0
    assert result["plan"]["effectiveFolds"] == 1
    assert result["plan"]["folds"][0]["trainBars"] == 102
    assert result["plan"]["folds"][0]["validationBars"] == 23
    assert result["plan"]["blindBars"] == 22
    assert result["suggestedStartDate"] is None


def test_service_preflight_blocks_incomplete_static_portfolio_data(monkeypatch):
    source = {
        "id": 44,
        "name": "Portfolio",
        "code": "def initialize(context):\n    context.set_universe(['USStock:AAPL', 'USStock:MSFT'])\n    context.subscribe(frequency='1h')\n\ndef handle_data(context, data):\n    pass",
        "param_schema": {
            "params": [
                {"name": "top_n", "type": "integer", "min": 2, "max": 10, "step": 1},
            ],
        },
    }

    class SourceService:
        @staticmethod
        def get_source(source_id, *, user_id):
            return source if source_id == 44 and user_id == 7 else None

    class Evaluator:
        def __init__(self, **kwargs):
            pass

        @staticmethod
        def walk_forward_context(start_date, end_date):
            return {
                "observations": tuple(start_date + timedelta(hours=index) for index in range(147)),
                "warmupBars": 20,
                "frequency": "1h",
                "strategyType": "portfolio",
                "universeKind": "static",
                "universeSize": 10,
                "symbolsUsed": 9,
                "skippedSymbols": 1,
                "symbolCoveragePercent": 90.0,
            }

    monkeypatch.setattr(evolution_service_module, "get_script_source_service", lambda: SourceService())
    monkeypatch.setattr(evolution_service_module, "PreparedEvolutionEvaluator", Evaluator)
    result = StrategyEvolutionService(backtest_service=object()).preflight(
        user_id=7,
        payload={
            "sourceId": 44,
            "startDate": "2026-10-02",
            "endDate": "2026-11-02",
            "parameterSpace": source["param_schema"]["params"],
            "config": {"method": "random"},
        },
    )

    assert result["barSufficient"] is True
    assert result["symbolCoverageSufficient"] is False
    assert result["sufficient"] is False
    assert result["symbolsUsed"] == 9
    assert result["universeSize"] == 10


def test_equity_returns_uses_daily_close_for_intraday_curves():
    returns = equity_returns([
        {"time": "2025-01-01T09:30:00", "value": 100.0},
        {"time": "2025-01-01T16:00:00", "value": 102.0},
        {"time": "2025-01-02T09:30:00", "value": 101.0},
        {"time": "2025-01-02T16:00:00", "value": 103.0},
    ])
    assert returns == pytest.approx([103.0 / 102.0 - 1.0])


def test_grid_sampler_is_bounded_and_deterministic():
    first = ParameterSampler(_parameters(), seed=7).grid(7)
    second = ParameterSampler(_parameters(), seed=7).grid(7)
    assert first == second
    assert len(first) == 7
    assert len({(row["fast"], row["slow"]) for row in first}) == 7
    assert first[0] == {"fast": 6, "slow": 30}
    assert first[-1] == {"fast": 18, "slow": 66}


def test_parameter_domains_keep_space_size_grid_and_random_sampling_in_sync():
    parameters = [
        SearchParameter("fast", "integer", 10, 50, 40, default=10),
        SearchParameter("slow", "integer", 30, 150, 100, default=30),
        SearchParameter("enabled", "boolean", default=True),
    ]
    sampler = ParameterSampler(parameters, seed=19)

    assert sampler.space_size() == 12
    grid = sampler.grid(20)
    assert len(grid) == 12
    assert {row["fast"] for row in grid} == {10, 50}
    assert {row["slow"] for row in grid} == {30, 130, 150}
    assert {row["enabled"] for row in grid} == {False, True}

    random_rows = [sampler.random_candidate() for _ in range(500)]
    assert {row["fast"] for row in random_rows} <= {10, 50}
    assert {row["slow"] for row in random_rows} <= {30, 130, 150}
    assert 150 in {row["slow"] for row in random_rows}


def test_stratified_random_candidates_are_unique_deterministic_and_cover_each_axis():
    parameters = [
        SearchParameter("fast", "integer", 1, 100, 1, default=20),
        SearchParameter("slow", "integer", 1, 100, 1, default=60),
    ]
    first = ParameterSampler(parameters, seed=29).stratified_candidates(64)
    second = ParameterSampler(parameters, seed=29).stratified_candidates(64)

    assert first == second
    assert len(first) == 64
    assert len({(row["fast"], row["slow"]) for row in first}) == 64
    assert len({row["fast"] for row in first}) >= 50
    assert len({row["slow"] for row in first}) >= 50


def test_convergence_tracks_negative_training_scores_without_a_zero_baseline():
    rows = [
        {"number": 1, "score": -0.8, "searchScore": -0.8, "pruned": False},
        {"number": 2, "score": -0.4, "searchScore": -0.4, "pruned": False},
        {"number": 3, "score": -0.6, "searchScore": -0.6, "pruned": False},
    ]

    convergence = StrategyEvolutionEngine._convergence(rows)

    assert [row["best"] for row in convergence] == [-0.8, -0.4, -0.4]


def test_high_dimensional_grid_stays_bounded_by_the_candidate_budget():
    parameters = [
        SearchParameter(f"parameter_{index}", "integer", 0, 99, 1, default=0)
        for index in range(5)
    ]
    candidates, stats = ParameterSampler(parameters, seed=23).grid_with_stats(64)

    assert len(candidates) == 64
    assert len({tuple(row.values()) for row in candidates}) == 64
    assert stats["scanned"] <= 64 * 30


def test_tpe_sampler_avoids_candidates_already_in_history():
    sampler = ParameterSampler(_parameters(), seed=17)
    history = [
        ({"fast": 6 + index * 3, "slow": 30 + index * 6}, float(index))
        for index in range(5)
    ]
    for index in range(12):
        candidate = sampler.tpe_candidate(history)
        identity = (candidate["fast"], candidate["slow"])
        existing = {(row["fast"], row["slow"]) for row, _score in history}
        assert identity not in existing
        history.append((candidate, float(index + 5)))


def test_tpe_budget_counts_unique_valid_candidates_after_resampling():
    constraints = discover_parameter_constraints(
        """
def handle_data(context, data):
    fast = int(context.params.get("fast", 12))
    slow = int(context.params.get("slow", 24))
    if fast >= slow:
        return
""",
        {"fast", "slow"},
    )
    result = StrategyEvolutionEngine(_evaluator).run(
        parameters=[
            SearchParameter("fast", "integer", 10, 20, 1, default=12),
            SearchParameter("slow", "integer", 15, 30, 1, default=24),
        ],
        config=EvolutionConfig.from_payload({
            "method": "tpe",
            "trials": 24,
            "folds": 4,
            "blindRatio": 0,
            "autoPrune": False,
            "monteCarloPaths": 100,
            "costMultipliers": [1],
        }),
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2025, 12, 31),
        commission=0.0005,
        slippage=0.0005,
        constraints=constraints,
    )

    candidates = [row["params"] for row in result["trials"]]
    assert len(candidates) == 24
    assert len({(row["fast"], row["slow"]) for row in candidates}) == 24
    assert all(row["fast"] < row["slow"] for row in candidates)
    assert result["summary"]["evaluatedTrials"] == 24
    assert result["summary"]["candidateAttempts"] >= 24
    assert result["summary"]["candidateAttempts"] == (
        result["summary"]["evaluatedTrials"]
        + result["summary"]["constraintRejectedTrials"]
        + result["summary"]["duplicateTrials"]
    )
    assert result["summary"]["sampleSufficient"] is False


def test_dual_moving_average_guard_becomes_parameter_constraint():
    constraints = discover_parameter_constraints(
        """
def handle_data(context, data):
    fast_period = int(context.params.get("fast_period", 20))
    slow_period = int(context.params.get("slow_period", 60))
    if fast_period >= slow_period:
        return
""",
        {"fast_period", "slow_period"},
    )
    assert [item.metadata() for item in constraints] == [
        {"expression": "fast_period >= slow_period", "source": "guard"}
    ]
    assert constraints[0].accepts({"fast_period": 20, "slow_period": 60}) is True
    assert constraints[0].accepts({"fast_period": 83, "slow_period": 62}) is False


def test_generic_constraints_support_declarations_compound_guards_and_arithmetic():
    constraints = discover_parameter_constraints(
        """
# @constraint stop_loss > 0 and take_profit >= stop_loss * 1.5
# @constraint mode in ("trend", "breakout")
def handle_data(context, data):
    lookback = int(context.params.get("lookback", 20))
    smoothing = int(context.params.get("smoothing", 5))
    if smoothing <= 0 or lookback + smoothing > 100:
        return
""",
        {"stop_loss", "take_profit", "mode", "lookback", "smoothing"},
    )
    assert [item.metadata()["source"] for item in constraints] == ["declaration", "declaration", "guard"]
    valid = {"stop_loss": 2, "take_profit": 4, "mode": "trend", "lookback": 20, "smoothing": 5}
    assert all(item.accepts(valid) for item in constraints)
    assert constraints[0].accepts({**valid, "take_profit": 2.5}) is False
    assert constraints[1].accepts({**valid, "mode": "mean_reversion"}) is False
    assert constraints[2].accepts({**valid, "lookback": 98}) is False


def test_constraint_rejections_and_flat_candidates_never_rank_as_best():
    calls = []

    def evaluator(params, start, end, commission, slippage):
        calls.append((dict(params), start, end))
        active = params["fast"] < params["slow"] and params["enabled"]
        return {
            "totalReturn": 3.0 if active else 0.0,
            "sharpeRatio": 1.0 if active else 0.0,
            "maxDrawdown": -2.0 if active else 0.0,
            "winRate": 55.0 if active else 0.0,
            "totalTrades": 4 if active else 0,
            "totalExecutions": 8 if active else 0,
            "equityCurve": [
                {"time": start.isoformat(), "value": 10_000.0},
                {"time": end.isoformat(), "value": 10_300.0 if active else 10_000.0},
            ],
        }

    parameters = [
        SearchParameter("fast", "integer", 20, 80, 60, default=20),
        SearchParameter("slow", "integer", 60, 60, 1, default=60),
        SearchParameter("enabled", "boolean", default=True),
    ]
    constraints = discover_parameter_constraints(
        """
def handle_data(context, data):
    fast = int(context.params.get("fast", 20))
    slow = int(context.params.get("slow", 60))
    if fast >= slow:
        return
""",
        {"fast", "slow", "enabled"},
    )
    result = StrategyEvolutionEngine(evaluator).run(
        parameters=parameters,
        config=EvolutionConfig.from_payload({
            "method": "grid",
            "trials": 8,
            "folds": 4,
            "autoPrune": True,
            "monteCarloPaths": 100,
            "costMultipliers": [1],
        }),
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2025, 12, 31),
        commission=0.0005,
        slippage=0.0005,
        constraints=constraints,
    )
    assert result["bestParams"] == {"fast": 20, "slow": 60, "enabled": True}
    assert result["summary"]["constraintRejectedTrials"] == 2
    assert all(item["components"]["validationActivity"] > 0 for item in result["topCandidates"])
    assert len(calls) < len(result["trials"]) * 8


def test_non_finite_candidate_metrics_are_pruned_before_ranking():
    def evaluator(params, start, end, commission, slippage):
        valid = bool(params["enabled"])
        return {
            "totalReturn": 2.0 if valid else float("nan"),
            "sharpeRatio": 0.8 if valid else float("inf"),
            "maxDrawdown": -3.0,
            "winRate": 50.0,
            "totalTrades": 3,
            "totalExecutions": 6,
            "equityCurve": [
                {"time": start.isoformat(), "value": 10_000.0},
                {"time": end.isoformat(), "value": 10_200.0},
            ],
        }

    result = StrategyEvolutionEngine(evaluator).run(
        parameters=[SearchParameter("enabled", "boolean", default=True)],
        config=EvolutionConfig.from_payload({
            "method": "grid",
            "trials": 2,
            "folds": 4,
            "autoPrune": False,
            "monteCarloPaths": 100,
            "costMultipliers": [1],
        }),
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2025, 12, 31),
        commission=0.0005,
        slippage=0.0005,
    )
    invalid = next(item for item in result["trials"] if item["params"]["enabled"] is False)
    assert invalid["pruned"] is True
    assert invalid["reason"] == "invalidMetrics"
    assert result["bestParams"] == {"enabled": True}


def test_all_inactive_candidates_report_structured_failure_diagnostics():
    def evaluator(_params, start, end, _commission, _slippage):
        return {
            "totalReturn": 0.0,
            "sharpeRatio": 0.0,
            "maxDrawdown": 0.0,
            "winRate": 0.0,
            "totalTrades": 0,
            "totalExecutions": 0,
            "equityCurve": [
                {"time": start.isoformat(), "value": 10_000.0},
                {"time": end.isoformat(), "value": 10_000.0},
            ],
        }

    with pytest.raises(StrategyEvolutionFailure) as captured:
        StrategyEvolutionEngine(evaluator).run(
            parameters=[SearchParameter("enabled", "boolean", default=True)],
            config=EvolutionConfig.from_payload({
                "method": "grid",
                "trials": 2,
                "folds": 4,
                "autoPrune": False,
                "monteCarloPaths": 100,
                "costMultipliers": [1],
            }),
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2025, 12, 31),
            commission=0.0005,
            slippage=0.0005,
        )

    failure = captured.value
    assert str(failure) == "strategyEvolution.allTrialsPruned"
    assert failure.details == {
        "messageKey": "strategyEvolution.failure.noValidationActivity",
        "dominantReason": "noValidationActivity",
        "totalTrials": 2,
        "evaluatedTrials": 2,
        "activeTrainingTrials": 0,
        "activeValidationTrials": 0,
        "actualBacktestRuns": None,
        "reasonCounts": {"noValidationActivity": 2},
    }


def test_grid_estimate_uses_available_parameter_combinations():
    estimate = StrategyEvolutionService.estimate({
        "parameterSpace": [{"name": "enabled", "type": "boolean"}],
        "config": {"method": "grid", "trials": 40, "folds": 4, "autoPrune": False, "costMultipliers": [1]},
    })
    assert estimate["trials"] == 2
    assert estimate["requestedTrials"] == 40
    assert estimate["backtestRuns"] == 3
    assert estimate["searchSpaceSize"] == 2
    assert estimate["coveragePercent"] == 100.0
    assert estimate["isUpperBound"] is False


def test_estimate_counts_portfolio_symbols_in_relative_workload():
    estimate = StrategyEvolutionService.estimate({
        "parameterSpace": [{"name": "enabled", "type": "boolean"}],
        "universeSize": 120,
        "config": {"method": "grid", "trials": 2, "costMultipliers": [1]},
    })

    assert estimate["universeSize"] == 120
    assert estimate["relativeUnits"] == estimate["backtestRuns"] * 120


def test_estimate_multiplies_three_or_more_parameter_domains_consistently():
    estimate = StrategyEvolutionService.estimate({
        "parameterSpace": [
            {"name": "fast", "type": "integer", "min": 10, "max": 50, "step": 40},
            {"name": "slow", "type": "integer", "min": 30, "max": 150, "step": 100},
            {"name": "enabled", "type": "boolean"},
        ],
        "config": {"method": "tpe", "trials": 64, "folds": 5, "autoPrune": False},
    })

    assert estimate["parameterCount"] == 3
    assert estimate["searchSpaceSize"] == 12
    assert estimate["requestedTrials"] == 64
    assert estimate["trials"] == 12
    assert estimate["coveragePercent"] == 100.0
    assert estimate["backtestRuns"] == 16


def test_random_standard_budget_meets_the_three_parameter_sample_floor():
    estimate = StrategyEvolutionService.estimate({
        "parameterSpace": [
            {"name": "fast", "type": "integer", "min": 1, "max": 100, "step": 1},
            {"name": "slow", "type": "integer", "min": 1, "max": 100, "step": 1},
            {"name": "exit", "type": "integer", "min": 1, "max": 20, "step": 1},
        ],
        "config": {"method": "random", "trials": 64},
    })

    assert estimate["minimumRecommendedTrials"] == 64
    assert estimate["sampleSufficient"] is True


def test_tpe_estimate_keeps_requested_trials_when_range_changes():
    estimate = StrategyEvolutionService.estimate({
        "parameterSpace": [{"name": "period", "type": "integer", "min": 5, "max": 30, "step": 1}],
        "config": {"method": "tpe", "trials": 24, "folds": 4, "autoPrune": True},
    })
    assert estimate["trials"] == 24
    assert estimate["backtestRuns"] == 28
    assert estimate["minimumRecommendedTrials"] == 26
    assert estimate["sampleSufficient"] is False
    assert estimate["isUpperBound"] is False


def test_default_estimate_uses_interactive_trial_budget():
    estimate = StrategyEvolutionService.estimate({
        "parameterSpace": [{"name": "period", "type": "integer", "min": 5, "max": 30, "step": 1}],
        "config": {"method": "tpe", "folds": 4, "autoPrune": True},
    })
    assert estimate["trials"] == 26
    assert estimate["backtestRuns"] == 30
    assert estimate["searchSpaceSize"] == 26
    assert estimate["sampleSufficient"] is True


def test_prepared_evaluator_fetches_full_market_window_once():
    calls = []
    index = pd.date_range("2023-12-01", "2024-12-31", freq="1D")
    frame = pd.DataFrame({
        "open": range(100, 100 + len(index)),
        "high": range(101, 101 + len(index)),
        "low": range(99, 99 + len(index)),
        "close": range(100, 100 + len(index)),
        "volume": [1000.0] * len(index),
    }, index=index)

    def fetch(_market, _symbol, _timeframe, start_date, end_date, **_kwargs):
        calls.append((start_date, end_date))
        start = pd.Timestamp(start_date).tz_localize(None) if pd.Timestamp(start_date).tzinfo else pd.Timestamp(start_date)
        end = pd.Timestamp(end_date).tz_localize(None) if pd.Timestamp(end_date).tzinfo else pd.Timestamp(end_date)
        return frame.loc[(frame.index >= start) & (frame.index <= end)].copy()

    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")
    context.set_warmup(2)

def handle_data(context, data):
    pass
"""
    evaluator = PreparedEvolutionEvaluator(
        backtest_service=StrategyV2BacktestService(frame_fetcher=fetch),
        user_id=7,
        code=code,
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 12, 31),
        initial_capital=10_000,
        leverage_enabled=False,
        leverage=1,
        source_id=31,
        strategy_name="Prepared",
    )
    assert len(calls) == 1

    first = evaluator({"period": 10}, datetime(2024, 2, 1), datetime(2024, 4, 30), 0.0005, 0.0005)
    second = evaluator({"period": 20}, datetime(2024, 5, 1), datetime(2024, 7, 31), 0.0005, 0.0005)
    repeated = evaluator({"period": 20}, datetime(2024, 5, 1), datetime(2024, 7, 31), 0.0005, 0.0005)

    assert len(calls) == 1
    assert first["sampleCount"] > 0
    assert second["sampleCount"] > 0
    assert repeated is second
    assert evaluator.stats == {"backtestRuns": 2, "cacheHits": 1}

    context = evaluator.walk_forward_context(datetime(2024, 1, 1), datetime(2024, 12, 31))
    assert context["frequency"] == "1d"
    assert context["warmupBars"] == 2
    assert context["strategyType"] == "cta"
    assert context["universeSize"] == 1
    assert context["symbolsUsed"] == 1
    assert context["symbolCoveragePercent"] == 100.0
    assert len(context["observations"]) == 366

    plan = build_walk_forward_plan(
        datetime(2024, 1, 1),
        datetime(2024, 12, 31),
        folds=4,
        train_ratio=0.7,
        blind_ratio=0.15,
        embargo_bars=2,
        warmup_bars=context["warmupBars"],
        observations=context["observations"],
    )
    segments = evaluator.evaluate_plan({"period": 30}, plan.folds, 0.0005, 0.0005)
    assert len(segments) == 1
    assert all(train["sampleCount"] > 0 and validation["sampleCount"] > 0 for train, validation in segments)
    assert evaluator.stats["backtestRuns"] == 3


def test_prepared_evaluator_reports_portfolio_universe_coverage():
    index = pd.date_range("2024-01-01", "2024-04-30", freq="1D")
    frame = pd.DataFrame({
        "open": [100.0] * len(index),
        "high": [101.0] * len(index),
        "low": [99.0] * len(index),
        "close": [100.0] * len(index),
        "volume": [1000.0] * len(index),
    }, index=index)

    def fetch(_market, symbol, _timeframe, start_date, end_date, **_kwargs):
        if symbol == "MSFT":
            return pd.DataFrame()
        start = pd.Timestamp(start_date).tz_localize(None) if pd.Timestamp(start_date).tzinfo else pd.Timestamp(start_date)
        end = pd.Timestamp(end_date).tz_localize(None) if pd.Timestamp(end_date).tzinfo else pd.Timestamp(end_date)
        return frame.loc[(frame.index >= start) & (frame.index <= end)].copy()

    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL", "USStock:MSFT"])
    context.subscribe(frequency="1d")
    context.set_warmup(2)

def handle_data(context, data):
    pass
"""
    evaluator = PreparedEvolutionEvaluator(
        backtest_service=StrategyV2BacktestService(frame_fetcher=fetch),
        user_id=7,
        code=code,
        start_date=datetime(2024, 2, 1),
        end_date=datetime(2024, 4, 30),
        initial_capital=10_000,
        leverage_enabled=False,
        leverage=1,
        source_id=32,
        strategy_name="Portfolio",
    )

    context = evaluator.walk_forward_context(datetime(2024, 2, 1), datetime(2024, 4, 30))
    assert context["strategyType"] == "portfolio"
    assert context["universeSize"] == 2
    assert context["symbolsUsed"] == 1
    assert context["skippedSymbols"] == 1
    assert context["symbolCoveragePercent"] == 50.0


def test_robustness_statistics_are_bounded():
    pbo = probability_of_backtest_overfitting([
        [20, 21, 19, 18, 17, 16],
        [10, 9, 11, 12, 10, 9],
        [14, 13, 15, 12, 11, 13],
    ])
    dsr = deflated_sharpe([0.01, -0.005, 0.008, 0.012, -0.002, 0.006], observed_sharpe=1.2, trials=20)
    monte_carlo = monte_carlo_bootstrap([0.01, -0.005, 0.008, 0.012], paths=150, block_size=2, seed=5)
    assert 0 <= pbo["probability"] <= 1
    assert 0 <= dsr["probability"] <= 1
    assert monte_carlo["paths"] == 150
    assert sum(row["count"] for row in monte_carlo["terminalReturns"]) == 150
    assert monte_carlo["uniqueTerminalReturns"] > 1
    assert monte_carlo["effectiveBlockSize"] < monte_carlo["observations"]


def test_monte_carlo_short_series_does_not_collapse_to_circular_permutations():
    result = monte_carlo_bootstrap(
        [0.01, -0.02, 0.005, 0.03],
        paths=1000,
        block_size=5,
        seed=42,
    )

    assert result["available"] is True
    assert result["paths"] == 1000
    assert result["observations"] == 4
    assert result["effectiveBlockSize"] < result["observations"]
    assert result["uniqueTerminalReturns"] > 1
    assert len(result["terminalReturns"]) > 1


def test_parameter_contract_rejects_invalid_numeric_range():
    with pytest.raises(ValueError, match="strategyEvolution.parameterRangeInvalid"):
        SearchParameter.from_payload({"name": "period", "type": "integer", "min": 20, "max": 5})


def test_parameter_contract_accepts_canonical_text_values():
    parameter = SearchParameter.from_payload({"name": "mode", "type": "text", "values": ["trend", "mean_reversion"]})
    assert parameter.kind == "string"
    assert parameter.choices == ("trend", "mean_reversion")


def test_parameter_space_recovers_declared_ranges_and_infers_missing_bounds():
    contract = build_parameter_space({
        "asset_type": "script",
        "code": """
# @param fast_period int 20 range=2:100:1
# @param threshold float 0.2
# @param enabled bool true
def initialize(context):
    pass
""",
        "param_schema": {},
    })

    assert contract["strategyClass"] == "cta"
    assert contract["scope"] == "declared"
    by_name = {item["name"]: item for item in contract["parameters"]}
    assert (by_name["fast_period"]["min"], by_name["fast_period"]["max"], by_name["fast_period"]["step"]) == (2, 100, 1)
    assert by_name["threshold"]["min"] == pytest.approx(0.1)
    assert by_name["threshold"]["max"] == pytest.approx(0.3)
    assert by_name["enabled"]["choices"] == [False, True]
    assert by_name["fast_period"]["recommendedMin"] == 10
    assert by_name["fast_period"]["recommendedMax"] == 50
    assert by_name["fast_period"]["defaultSelected"] is True
    assert by_name["enabled"]["defaultSelected"] is False
    assert by_name["enabled"]["optimizable"] is False


def test_boolean_strategy_modes_cannot_enter_the_evolution_search_space():
    source = {
        "param_schema": {
            "params": [
                {"name": "period", "type": "integer", "default": 20, "min": 5, "max": 100},
                {"name": "allow_short", "type": "boolean", "default": True},
            ],
        },
    }
    defaults = StrategyEvolutionService._parameters(source, None)
    assert [item.name for item in defaults] == ["period"]
    with pytest.raises(ValueError, match="strategyEvolution.parameterNotOptimizable"):
        StrategyEvolutionService._parameters(source, [{"name": "allow_short", "type": "boolean"}])


def test_robot_parameter_space_exposes_only_runtime_bound_safe_controls():
    source = {
        "asset_type": "script",
        "template_key": "robot_v2_dca",
        "metadata": {"last_run_config": {"executor_type": "dca"}},
        "param_schema": {},
        "code": """
DCA_INTERVAL_MINUTES = 1440
DCA_MAX_ORDERS = 12
DCA_TOTAL_BUDGET_PCT = 0.8
DCA_ORDER_PCT = 0.1
DCA_PRICE_FILTER_ENABLED = True
DCA_MAX_ADVERSE_PRICE_PCT = 0.1
TAKE_PROFIT = 0.05
HARD_STOP = 0.1
PRICE_LEVELS = [0.9, 0.8]
def initialize(context):
    pass
""",
    }

    contract = build_parameter_space(source)
    assert contract["strategyClass"] == "robot"
    assert contract["robotType"] == "dca"
    assert contract["scope"] == "riskExecution"
    names = [item["name"] for item in contract["parameters"]]
    assert names == [
        "dca_interval_minutes",
        "dca_max_orders",
        "dca_total_budget_pct",
        "dca_order_pct",
        "dca_price_filter_enabled",
        "dca_max_adverse_price_pct",
        "take_profit_pct",
        "hard_stop_pct",
    ]
    assert all("PRICE_LEVELS" != item.get("runtimeConstant") for item in contract["parameters"])


def test_robot_adapter_binds_candidate_params_before_original_initialize():
    code = """
TAKE_PROFIT = 0.05
HARD_STOP = 0.1
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    g.values = (TAKE_PROFIT, HARD_STOP)
"""
    rows = [
        {"name": "take_profit_pct", "type": "percent", "runtimeConstant": "TAKE_PROFIT"},
        {"name": "hard_stop_pct", "type": "percent", "runtimeConstant": "HARD_STOP"},
    ]
    adapted = adapt_source_for_parameters(code, rows)
    StrategyV2BacktestService().compile(adapted)
    namespace = {"g": type("State", (), {})()}
    exec(adapted, namespace)

    class Context:
        params = {"take_profit_pct": 0.08, "hard_stop_pct": 0.12}

        @staticmethod
        def set_universe(_instruments):
            return None

        @staticmethod
        def subscribe(**_kwargs):
            return None

    namespace["initialize"](Context())
    namespace["handle_data"](Context(), None)
    assert namespace["g"].values == pytest.approx((0.08, 0.12))


def test_parameter_request_can_narrow_but_not_expand_declared_range():
    source = {"param_schema": {"params": [{"name": "period", "type": "integer", "min": 5, "max": 100, "step": 1}]}}
    narrowed = StrategyEvolutionService._parameters(source, [{"name": "period", "min": 10, "max": 50, "step": 5, "type": "string"}])
    assert narrowed[0].kind == "integer"
    assert narrowed[0].minimum == 10
    assert narrowed[0].maximum == 50
    assert narrowed[0].step == 5
    with pytest.raises(ValueError, match="strategyEvolution.parameterRangeOutsideDeclaration"):
        StrategyEvolutionService._parameters(source, [{"name": "period", "min": 1, "max": 120}])
    with pytest.raises(ValueError, match="strategyEvolution.parameterStepInvalid"):
        StrategyEvolutionService._parameters(source, [{"name": "period", "min": 10, "max": 50, "step": 0.5}])


def test_parameter_request_defaults_to_the_server_generated_space_when_omitted():
    source = {"param_schema": {"params": [{"name": "period", "type": "integer", "default": 20, "min": 5, "max": 100, "step": 1}]}}
    parameters = StrategyEvolutionService._parameters(source, None)
    assert len(parameters) == 1
    assert parameters[0].name == "period"
    assert parameters[0].minimum == 5
    assert parameters[0].maximum == 100


def test_parameter_contract_compacts_regular_numeric_values_but_preserves_irregular_choices():
    contract = build_parameter_space({
        "param_schema": {
            "params": [
                {"name": "period", "type": "integer", "values": [5, 10, 15, 20]},
                {"name": "threshold", "type": "number", "values": [0.1, 0.25, 0.9]},
            ],
        },
    })
    regular, irregular = contract["parameters"]
    assert regular["min"] == 5
    assert regular["max"] == 20
    assert regular["step"] == 5
    assert "values" not in regular
    assert "choices" not in regular
    assert irregular["choices"] == [0.1, 0.25, 0.9]
    assert "values" not in irregular


def test_service_runs_single_asset_cta_through_strategy_v2_backtester(monkeypatch):
    source = {
        "id": 31,
        "name": "BTC CTA",
        "asset_type": "script",
        "code": "def initialize(context):\n    context.set_universe(['Crypto:BTC/USDT@swap'])",
        "param_schema": {
            "params": [
                {"name": "fast", "type": "integer", "min": 6, "max": 18, "step": 6},
                {"name": "slow", "type": "integer", "min": 30, "max": 60, "step": 15},
            ],
        },
    }

    class SourceService:
        @staticmethod
        def get_source(source_id, *, user_id):
            assert source_id == 31
            assert user_id == 7
            return source

    calls = []

    class BacktestService:
        @staticmethod
        def run(**kwargs):
            calls.append(kwargs)
            return 1, _evaluator(
                kwargs["params"],
                kwargs["start_date"],
                kwargs["end_date"],
                kwargs["commission"],
                kwargs["slippage"],
            )

    monkeypatch.setattr(evolution_service_module, "get_script_source_service", lambda: SourceService())
    result = StrategyEvolutionService(backtest_service=BacktestService()).run(
        user_id=7,
        payload={
            "sourceId": 31,
            "startDate": "2024-01-01",
            "endDate": "2025-12-31",
            "commission": 0.0005,
            "slippage": 0.0005,
            "parameterSpace": source["param_schema"]["params"],
            "config": {
                "method": "grid",
                "trials": 4,
                "folds": 4,
                "autoPrune": False,
                "monteCarloPaths": 100,
                "costMultipliers": [1],
            },
        },
    )

    assert result["status"] == "complete"
    assert result["source"] == {"id": 31, "name": "BTC CTA"}
    assert calls
    assert all(call["source_id"] == 31 for call in calls)
    assert all(call["code"] == source["code"] for call in calls)
    assert all(set(call["params"]) == {"fast", "slow"} for call in calls)


def test_grid_engine_does_not_repeat_exhausted_combinations():
    result = StrategyEvolutionEngine(lambda params, start, end, commission, slippage: _evaluator({"fast": 12, "slow": 48}, start, end, commission, slippage)).run(
        parameters=[SearchParameter("enabled", "boolean", default=True)],
        config=EvolutionConfig.from_payload({"method": "grid", "trials": 40, "folds": 4, "autoPrune": False, "monteCarloPaths": 100}),
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2025, 12, 31),
        commission=0.0005,
        slippage=0.0005,
    )
    assert len(result["trials"]) == 2


def test_background_runner_keeps_user_scope_out_of_engine_payload(monkeypatch):
    captured = {}

    class Service:
        @staticmethod
        def run(*, user_id, payload, on_progress):
            captured.update(user_id=user_id, payload=payload, on_progress=on_progress)
            return {"status": "complete"}

    monkeypatch.setattr(evolution_routes, "get_strategy_evolution_service", lambda: Service())
    progress = lambda event: event
    result = evolution_routes._run_evolution_job({"__userId": 17, "sourceId": 9}, progress)
    assert result == {"status": "complete"}
    assert captured == {"user_id": 17, "payload": {"sourceId": 9}, "on_progress": progress}


def test_celery_dispatches_strategy_evolution_to_durable_runner(monkeypatch):
    captured = {}

    def runner(payload, on_progress):
        captured.update(payload=payload, on_progress=on_progress)
        return {"status": "complete"}

    monkeypatch.setattr(evolution_routes, "_run_evolution_job", runner)
    progress = lambda event: event

    assert celery_agent_jobs.supports_kind("strategy_evolution") is True
    result = celery_agent_jobs._execute(
        "strategy_evolution",
        {"__userId": 17, "sourceId": 9},
        progress,
    )

    assert result == {"status": "complete"}
    assert captured == {
        "payload": {"__userId": 17, "sourceId": 9},
        "on_progress": progress,
    }


def test_celery_persists_structured_evolution_failure(monkeypatch):
    from app.utils import agent_jobs

    details = {
        "messageKey": "strategyEvolution.failure.noValidationActivity",
        "dominantReason": "noActivity",
        "totalTrials": 12,
        "reasonCounts": {"noActivity": 12},
    }
    events = []
    monkeypatch.setattr(agent_jobs, "get_job_for_worker", lambda _job_id: {
        "status": "queued",
        "kind": "strategy_evolution",
        "request": {"sourceId": 189},
    })
    monkeypatch.setattr(agent_jobs, "_set_status", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(agent_jobs, "_set_failure", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(agent_jobs, "_publish_progress", lambda _job_id, event, **_kwargs: events.append(event))
    monkeypatch.setattr(
        celery_agent_jobs,
        "_execute",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            StrategyEvolutionFailure("strategyEvolution.allTrialsPruned", details)
        ),
    )

    with pytest.raises(StrategyEvolutionFailure):
        celery_agent_jobs.execute_agent_job.run("evolution-job")

    assert events[-1]["phase"] == "failed"
    assert events[-1]["error"] == "strategyEvolution.allTrialsPruned"
    assert events[-1]["failure"] == details


def test_public_job_decodes_persisted_json():
    output = evolution_routes._public_job({
        "job_id": "job-1",
        "status": "succeeded",
        "result": '{"status":"complete"}',
        "progress": '{"completed":4,"total":4}',
        "request": '{"sourceId":9,"startDate":"2025-01-01","__userId":17}',
    })
    assert output["jobId"] == "job-1"
    assert output["result"]["status"] == "complete"
    assert output["progress"]["completed"] == 4
    assert output["request"] == {"sourceId": 9, "startDate": "2025-01-01"}


def test_history_endpoint_lists_only_selected_strategy_evolution_jobs(monkeypatch):
    captured = {}

    def list_jobs(**kwargs):
        captured.update(kwargs)
        return [{"job_id": "job-1"}]

    def get_job(job_id, *, user_id):
        assert job_id == "job-1"
        assert user_id == 23
        return {
            "job_id": job_id,
            "kind": "strategy_evolution",
            "status": "running",
            "request": {"sourceId": 7, "__userId": 23},
            "progress": {"completed": 2, "total": 8},
        }

    monkeypatch.setattr(evolution_routes.agent_jobs, "list_jobs", list_jobs)
    monkeypatch.setattr(evolution_routes.agent_jobs, "get_job", get_job)
    app = Flask(__name__)
    with app.test_request_context("/?limit=10&sourceId=7"):
        g.user_id = 23
        response = inspect.unwrap(evolution_routes.list_strategy_evolution_jobs)()

    payload = response.get_json()
    assert captured == {
        "user_id": 23,
        "kind": "strategy_evolution",
        "request_source_id": 7,
        "limit": 10,
    }
    assert payload["data"]["items"][0]["request"] == {"sourceId": 7}


def test_history_detects_only_running_jobs_past_the_worker_time_limit(monkeypatch):
    monkeypatch.setenv("CELERY_TASK_TIME_LIMIT", "3600")
    stale = datetime.now(timezone.utc) - timedelta(seconds=3901)
    recent = datetime.now(timezone.utc) - timedelta(seconds=3899)
    assert evolution_routes._is_stale_running_job({"status": "running", "started_at": stale}) is True
    assert evolution_routes._is_stale_running_job({"status": "running", "started_at": recent}) is False
    assert evolution_routes._is_stale_running_job({"status": "succeeded", "started_at": stale}) is False
