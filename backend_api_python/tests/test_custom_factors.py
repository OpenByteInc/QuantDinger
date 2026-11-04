import numpy as np
import pandas as pd
import pytest

from app.services.custom_factors import (
    CustomFactorError,
    analyze_custom_factor,
    compute_custom_factor,
)


def _frame(length=80):
    step = np.arange(length, dtype=float)
    close = 100.0 + step * 0.5
    return pd.DataFrame({
        "open": close - 0.2,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1_000.0 + step * 10.0,
    })


def test_custom_factor_analyzes_dependencies_fields_and_warmup():
    analysis = analyze_custom_factor(
        "momentum(period=lookback) / max(realized_volatility(period=vol_window), floor)",
        {"lookback": 20, "vol_window": 15, "floor": 0.0001},
    )

    assert analysis.dependencies == ("momentum", "realized_volatility")
    assert analysis.required_fields == ("close",)
    assert analysis.warmup_bars == 21


def test_custom_factor_computes_registered_factor_expression():
    definition = {
        "expression": "momentum(period=lookback) / max(realized_volatility(period=vol_window), floor)",
        "default_params": {"lookback": 20, "vol_window": 15, "floor": 0.0001},
    }

    value = compute_custom_factor(definition, _frame())

    assert np.isfinite(value)
    assert value > 0


@pytest.mark.parametrize("expression", [
    "__import__('os').system('whoami')",
    "close.__class__",
    "factor('custom:2')",
])
def test_custom_factor_rejects_unsafe_or_recursive_expressions(expression):
    with pytest.raises(CustomFactorError):
        analyze_custom_factor(expression)


@pytest.mark.parametrize("expression", [
    "momentum(20)", "factor('momentum', 20)", "factor()",
    "abs(1, 2)", "clip(1, 2)", "min()", "sqrt()",
    "momentum(**{'period': 20})", "momentum(period=20, period=30)",
    "momentum(peroid=20)", "momentum(period=-1)", "momentum(period=1.5)",
    "factor('qlib:alpha158:MA20', period=5)",
])
def test_invalid_signatures_fail_validation_and_execution(expression):
    with pytest.raises(CustomFactorError):
        analyze_custom_factor(expression)
    with pytest.raises(CustomFactorError):
        compute_custom_factor({"expression": expression}, _frame())


@pytest.mark.parametrize("params", [{"close": 3}, {"for": 3}, {"a-b": 3}, {"n": float("inf")}, {"n": "20"}])
def test_invalid_default_parameter_contract(params):
    with pytest.raises(CustomFactorError, match="customInvalidParameter"):
        analyze_custom_factor("1", params)


def test_formula_supports_zero_negative_defaults_and_short_circuit_guards():
    definition = {"expression": "den != 0 and close / den > threshold", "default_params": {"den": 0, "threshold": -1}}
    assert compute_custom_factor(definition, _frame()) == 0
    assert compute_custom_factor({"expression": "True or 1 / 0"}, _frame()) == 1
    assert compute_custom_factor({"expression": "clip(-5, -2, 2)"}, _frame()) == -2


def test_custom_latest_field_cannot_fall_back_to_stale_data():
    frame = _frame()
    frame.loc[frame.index[-1], "close"] = np.nan
    with pytest.raises(CustomFactorError, match="factor.missingFields"):
        compute_custom_factor({"expression": "close"}, frame)


def test_contract_examples_have_correct_warmup_and_execute():
    pytest.importorskip("talib")
    from app.services.factors import compute_catalog_factor
    for expression, params, warmup, dependency in [
        ('factor("talib:RSI", timeperiod=period)', {"period": 14}, 15, "talib:RSI"),
        ('factor("qlib:alpha158:IMAX20")', {}, 20, "qlib:alpha158:IMAX20"),
        ('factor("talib:SMA", timeperiod=period)', {"period": 60}, 60, "talib:SMA"),
    ]:
        assert analyze_custom_factor(expression, params).warmup_bars == warmup
        result = compute_custom_factor({"expression": expression, "default_params": params}, _frame())
        native_params = {"timeperiod": params["period"]} if params else {}
        assert result == pytest.approx(compute_catalog_factor(dependency, _frame(), native_params))


def test_talib_zero_matype_is_a_valid_enum_value():
    pytest.importorskip("talib")
    expression = 'factor("talib:BBANDS", timeperiod=20, matype=0, output="upperband")'
    assert analyze_custom_factor(expression).warmup_bars == 20
    assert np.isfinite(compute_custom_factor({"expression": expression}, _frame()))
