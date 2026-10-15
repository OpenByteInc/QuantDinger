import pandas as pd
import pytest
import numpy as np

from app.services.factors import FactorError, compute_factor, compute_panel_factor, list_factors
from app.services.factors.research import information_coefficient, quantile_returns, winsorize_zscore
from app.utils.technical_indicators import compute_kdj_cn, compute_rsi_wilder


def test_factor_catalog_contains_technical_and_fundamental_definitions():
    factors = list_factors()
    types = {item["factor_type"] for item in factors}
    assert types == {"technical", "fundamental"}
    assert {item["factor_id"] for item in factors} >= {
        "momentum",
        "realized_volatility",
        "skewness",
        "kurtosis",
        "earnings_yield",
        "return_on_equity",
    }
    technical = [item for item in factors if item["factor_type"] == "technical"]
    assert len(technical) >= 50
    assert all(set(item["supported_contexts"]) == {"cta", "portfolio"} for item in technical)
    macd = next(item for item in technical if item["factor_id"] == "macd")
    assert macd["parameter_schema"]["output"]["options"] == ["line", "signal", "histogram"]
    assert macd["parameter_schema"]["fast_period"]["type"] == "integer"


def test_momentum_uses_only_requested_lookback():
    frame = pd.DataFrame({"close": [100, 101, 102, 110]})
    assert compute_factor("momentum", frame, {"period": 3}) == pytest.approx(0.10)


def test_fundamental_factor_reads_latest_point_in_time_row():
    frame = pd.DataFrame({
        "net_income": [10, 12],
        "market_cap": [100, 120],
    }, index=pd.to_datetime(["2025-12-31", "2026-03-31"]))
    assert compute_factor("earnings_yield", frame) == pytest.approx(0.10)


def test_panel_factor_skips_symbols_without_required_history():
    output = compute_panel_factor(
        "momentum",
        {
            "AAPL": pd.DataFrame({"close": [100, 110, 120]}),
            "NEW": pd.DataFrame({"close": [10]}),
        },
        {"period": 2},
    )
    assert output == {"AAPL": pytest.approx(0.2)}


def test_missing_factor_fields_are_explicit():
    with pytest.raises(FactorError) as caught:
        compute_factor("earnings_yield", pd.DataFrame({"net_income": [10]}))
    assert caught.value.code == "factor.missingFields"


def test_factor_research_returns_rank_ic_and_quantile_results():
    scores = {"A": -100, "B": 2, "C": 3, "D": 4, "E": 100}
    returns = {"A": -0.05, "B": 0.01, "C": 0.02, "D": 0.03, "E": 0.08}
    normalized = winsorize_zscore(scores)
    stats = information_coefficient(normalized, returns)
    buckets = quantile_returns(normalized, returns, quantiles=5)

    assert stats["rank_ic"] == pytest.approx(1.0)
    assert stats["coverage"] == 1.0
    assert len(buckets) == 5


def test_all_registered_technical_factors_compute_finite_defaults():
    index = np.arange(240, dtype=float)
    close = 100.0 + index * 0.2 + np.sin(index / 5.0) * 2.0
    frame = pd.DataFrame({
        "open": close - 0.2,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1_000.0 + (index % 17) * 25.0,
    })
    for definition in list_factors(factor_type="technical"):
        value = compute_factor(definition["factor_id"], frame)
        assert np.isfinite(value), definition["factor_id"]


def test_registered_rsi_and_kdj_match_shared_terminal_conventions():
    close = [100, 101, 102, 101, 100, 99, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107]
    high = [value + 1 for value in close]
    low = [value - 1 for value in close]
    frame = pd.DataFrame({"high": high, "low": low, "close": close})
    rsi = compute_rsi_wilder(close, 14)[-1]
    _k, _d, j = compute_kdj_cn(high, low, close, 9, 3, 3)
    assert compute_factor("rsi", frame, {"period": 14}) == pytest.approx(rsi)
    assert compute_factor("kdj", frame, {"period": 9, "k_period": 3, "d_period": 3, "output": "j"}) == pytest.approx(j[-1], abs=1e-4)


def test_extended_factor_families_have_expected_invariants():
    index = np.arange(120, dtype=float)
    close = 50.0 + index * 0.5
    frame = pd.DataFrame({
        "open": close - 0.1,
        "high": close + 0.8,
        "low": close - 0.8,
        "close": close,
        "volume": 2_000.0 + index * 5.0,
    })

    assert compute_factor("efficiency_ratio", frame, {"period": 20}) == pytest.approx(1.0)
    assert compute_factor("ppo", frame) > 0
    assert compute_factor("vortex", frame, {"period": 14, "output": "plus"}) > 0
    assert compute_factor("ulcer_index", frame) == pytest.approx(0.0)
    assert compute_factor("parkinson_volatility", frame) > 0
    assert np.isfinite(compute_factor("skewness", frame))
    assert np.isfinite(compute_factor("kurtosis", frame))
