import numpy as np
import pandas as pd
import pytest

from app.services.factors import (
    FactorError,
    compute_catalog_factor,
    compute_qlib_factor,
    get_qlib_factor_metadata,
    list_factor_catalog,
    list_qlib_factors,
)
from app.services.strategy_v2.factor_research import FactorResearchEngine


def _frame(rows=90):
    index = np.arange(rows, dtype=float)
    close = 100.0 + index
    return pd.DataFrame({
        "open": close - 0.5,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "vwap": close - 0.1,
        "volume": 1_000.0 + index * 10.0,
    })


def test_qlib_catalog_exposes_complete_official_feature_families():
    factors = list_qlib_factors()
    alpha158 = [item for item in factors if item["family"] == "alpha158"]
    alpha360 = [item for item in factors if item["family"] == "alpha360"]

    assert len(alpha158) == 158
    assert len(alpha360) == 360
    assert {item["usage_mode"] for item in factors} == {"model_feature"}
    assert get_qlib_factor_metadata("qlib:alpha158:KMID")["expression"] == "($close-$open)/$open"


def test_qlib_alpha360_lag_features_match_reference_normalization():
    frame = _frame()
    expected_close = frame["close"].iloc[-3] / frame["close"].iloc[-1]
    expected_volume = frame["volume"].iloc[-6] / frame["volume"].iloc[-1]

    assert compute_qlib_factor("qlib:alpha360:CLOSE2", frame) == pytest.approx(expected_close)
    assert compute_qlib_factor("qlib:alpha360:VOLUME5", frame) == pytest.approx(expected_volume)


def test_qlib_alpha158_kbar_and_rolling_features_are_executable():
    frame = _frame()
    expected_body = (frame["close"].iloc[-1] - frame["open"].iloc[-1]) / frame["open"].iloc[-1]
    expected_ma = frame["close"].iloc[-5:].mean() / frame["close"].iloc[-1]

    assert compute_catalog_factor("qlib:alpha158:KMID", frame) == pytest.approx(expected_body)
    assert compute_catalog_factor("qlib:alpha158:MA5", frame) == pytest.approx(expected_ma)


def test_public_catalog_can_include_qlib_without_expanding_internal_contracts():
    internal = list_factor_catalog()
    public = list_factor_catalog(include_feature_families=True)

    assert not any(item["factor_id"].startswith("qlib:") for item in internal)
    assert sum(item["factor_id"].startswith("qlib:") for item in public) == 518


def test_factor_research_can_build_a_cross_section_from_qlib_features():
    first = _frame()
    second = _frame()
    second["close"] *= 1.05
    second["open"] *= 1.05
    second["high"] *= 1.05
    second["low"] *= 1.05
    second["vwap"] *= 1.05
    frames = {"A": first, "B": second}
    close = pd.DataFrame({symbol: frame["close"] for symbol, frame in frames.items()})

    result = FactorResearchEngine()._factor_panel(
        frames,
        "qlib:alpha158:KMID",
        close,
        timestamps=[close.index[-1]],
    )

    assert np.isfinite(result.loc[close.index[-1], "A"])
    assert np.isfinite(result.loc[close.index[-1], "B"])


@pytest.mark.parametrize("window", [5, 10, 20, 30, 60])
@pytest.mark.parametrize("pattern", ["rising", "falling", "ties"])
def test_extreme_positions_follow_qlib_one_based_oldest_first_semantics(window, pattern):
    values = np.arange(window, dtype=float) + 10
    if pattern == "falling":
        values = values[::-1]
    elif pattern == "ties":
        values[:] = 10
    frame = pd.DataFrame({"high": values + 1, "low": values - 1})
    maximum = (np.argmax(frame.high.to_numpy()) + 1) / window
    minimum = (np.argmin(frame.low.to_numpy()) + 1) / window
    for name, expected in [("IMAX", maximum), ("IMIN", minimum), ("IMXD", maximum - minimum)]:
        assert compute_qlib_factor(f"qlib:alpha158:{name}{window}", frame) == pytest.approx(expected)


@pytest.mark.parametrize("window", [5, 10, 20, 30, 60])
@pytest.mark.parametrize("step", [0, 1e-8])
def test_rsquare_does_not_invent_perfect_fit_for_flat_prices(window, step):
    frame = pd.DataFrame({"close": 100 + np.arange(window) * step})
    with pytest.raises(FactorError, match="factor.insufficientHistory"):
        compute_qlib_factor(f"qlib:alpha158:RSQR{window}", frame)
    assert np.isfinite(compute_qlib_factor(f"qlib:alpha158:BETA{window}", frame))


def test_rsquare_linear_prices_and_missing_vwap_contract():
    assert compute_qlib_factor("qlib:alpha158:RSQR20", _frame()) == pytest.approx(1)
    with pytest.raises(FactorError, match="factor.missingFields:vwap"):
        compute_qlib_factor("qlib:alpha360:VWAP0", _frame().drop(columns="vwap"))
