import pytest

from app.services.strategy_v2.factor_benchmark import FactorBenchmarkEngine


def _result(factor_id, values, *, coverage=0.95, net_return=0.1, turnover=0.4):
    return {
        "factorId": factor_id,
        "factorParams": {},
        "icSeries": [
            {"time": f"2026-01-{index + 1:02d}", "value": value}
            for index, value in enumerate(values)
        ],
        "coverage": coverage,
        "averageTurnover": turnover,
        "netLongShortReturn": net_return,
        "monotonicity": 0.8,
        "pointInTimeUniverseApplied": True,
        "executionAssumptions": {"periodsPerYear": 252},
    }


def test_benchmark_ranks_alive_and_reversed_factors_with_oos_metrics():
    engine = FactorBenchmarkEngine()
    result = engine.run([
        _result("quality", [0.05, 0.06, 0.04, 0.07, 0.05, 0.06, 0.05, 0.07, 0.06, 0.05]),
        _result("inverse_value", [-0.06, -0.05, -0.07, -0.04, -0.06, -0.05, -0.06, -0.07, -0.05, -0.06], net_return=-0.12),
    ], minimum_oos_observations=3)

    by_factor = {row["factorId"]: row for row in result["rankings"]}
    assert by_factor["quality"]["classification"] == "alive"
    assert by_factor["inverse_value"]["classification"] == "reversed"
    assert by_factor["quality"]["testObservationCount"] >= 3
    assert by_factor["inverse_value"]["orientedNetLongShortReturn"] == pytest.approx(0.12)
    assert result["summary"]["counts"]["alive"] == 1
    assert result["summary"]["counts"]["reversed"] == 1


def test_benchmark_marks_sign_flip_as_decaying():
    result = FactorBenchmarkEngine().run([
        _result("decay", [0.08, 0.07, 0.06, 0.05, 0.07, 0.06, -0.03, -0.04, -0.05, -0.02]),
    ], minimum_oos_observations=3)

    row = result["rankings"][0]
    assert row["classification"] == "decaying"
    assert row["sameSign"] is False


def test_benchmark_applies_benjamini_hochberg_monotonically():
    adjusted = FactorBenchmarkEngine._benjamini_hochberg([0.01, 0.04, 0.03, 0.5])

    assert adjusted == pytest.approx([0.04, 0.0533333333, 0.0533333333, 0.5])


def test_benchmark_reports_insufficient_oos_without_inventing_strength():
    result = FactorBenchmarkEngine().run([
        _result("short", [0.1, 0.1, 0.1, 0.1]),
    ], minimum_oos_observations=5)

    row = result["rankings"][0]
    assert row["classification"] == "insufficient"
    assert row["score"] == 0.0


def test_benchmark_treats_below_gate_coverage_as_insufficient():
    result = FactorBenchmarkEngine().run([
        _result("low_coverage", [0.08] * 10, coverage=0.4),
        _result("usable", [0.03] * 10, coverage=0.95),
    ], minimum_oos_observations=3, minimum_coverage=0.5)

    by_factor = {row["factorId"]: row for row in result["rankings"]}
    assert by_factor["low_coverage"]["classification"] == "insufficient"
    assert by_factor["low_coverage"]["score"] == 0.0
    assert result["summary"]["bestFactorId"] == "usable"
    assert result["summary"]["counts"]["insufficient"] == 1


def test_benchmark_summarizes_oos_direction_and_absolute_strength():
    result = FactorBenchmarkEngine().run([
        _result("positive", [0.01] * 6 + [0.03] * 4),
        _result("negative", [-0.01] * 6 + [-0.06] * 4, net_return=-0.1),
        _result("flat", [0.0] * 10),
    ], minimum_oos_observations=3)

    summary = result["summary"]
    assert summary["positiveOosCount"] == 1
    assert summary["negativeOosCount"] == 1
    assert summary["zeroOosCount"] == 1
    assert summary["strongestOosFactorId"] == "negative"
    assert summary["strongestOosRankIc"] == pytest.approx(-0.06)
