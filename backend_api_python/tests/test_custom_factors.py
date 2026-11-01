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
