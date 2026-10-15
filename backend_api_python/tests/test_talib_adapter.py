import numpy as np
import pandas as pd
import pytest

talib = pytest.importorskip("talib")
from talib import abstract

from app.services.factors.talib_adapter import (
    TA_LIB_081_FUNCTIONS,
    assert_talib_catalog_ready,
    compute_talib_factor,
    compute_talib_indicator,
    list_talib_factors,
)


def _frame():
    close = np.linspace(100.0, 150.0, 200)
    return pd.DataFrame({
        "open": close - 0.2,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": np.linspace(1000, 2000, 200),
    })


def test_talib_catalog_exposes_081_functions_and_metadata():
    assert assert_talib_catalog_ready() >= 201
    catalog = {item["factor_id"]: item for item in list_talib_factors()}
    ids = set(catalog)
    assert {"talib:RSI", "talib:MACD", "talib:CDLDOJI"} <= ids
    assert {f"talib:{name}" for name in TA_LIB_081_FUNCTIONS} <= ids
    assert catalog["talib:SUPERTREND"]["introduced_in"] == "0.8.1"
    assert catalog["talib:SUPERTREND"]["default_params"]["output"] == "supertrend"
    assert catalog["talib:SUPERTREND"]["parameter_schema"]["output"]["options"] == [
        "supertrend",
        "trend",
    ]


@pytest.mark.parametrize("name", ["APO", "BBANDS", "PPO"])
def test_talib_catalog_uses_081_native_defaults(name):
    catalog = {item["library_id"]: item for item in list_talib_factors()}
    actual = dict(catalog[name]["default_params"])
    actual.pop("output", None)

    assert actual == dict(abstract.Function(name).info["parameters"])


def test_talib_single_and_multi_output_computation():
    frame = _frame()
    rsi = compute_talib_factor("RSI", frame, {"timeperiod": 14})
    macd = compute_talib_indicator("MACD", frame)

    assert np.isfinite(rsi)
    assert list(macd.columns) == ["macd", "macdsignal", "macdhist"]
    assert np.isfinite(macd["macdhist"].dropna().iloc[-1])


def test_talib_081_supertrend_supports_series_and_selected_factor_output():
    frame = _frame()
    result = compute_talib_indicator("talib:SUPERTREND", frame, {"period": 10, "multiplier": 3.0})

    assert list(result.columns) == ["supertrend", "trend"]
    assert np.isfinite(result["supertrend"].dropna().iloc[-1])
    assert compute_talib_factor(
        "talib:SUPERTREND",
        frame,
        {"timeperiod": 10, "multiplier": 3.0, "output": "trend"},
    ) in {-1.0, 1.0}


@pytest.mark.parametrize("name", ["sma", "SMA", "talib:SMA"])
@pytest.mark.parametrize("period", [20, 60])
def test_sma_platform_period_matches_native_talib_parameter(name, period):
    frame = _frame()
    params = {"period": period}
    actual = compute_talib_indicator(name, frame, params)
    expected = frame["close"].rolling(period).mean()

    np.testing.assert_allclose(actual, expected, equal_nan=True)
    assert params == {"period": period}
    assert compute_talib_factor(name, frame, params) == pytest.approx(expected.iloc[-1])


def test_sma_explicit_timeperiod_takes_precedence_over_alias():
    frame = _frame()
    actual = compute_talib_indicator("SMA", frame, {"period": 20, "timeperiod": 60})
    expected = compute_talib_indicator("SMA", frame, {"timeperiod": 60})

    pd.testing.assert_series_equal(actual, expected)
