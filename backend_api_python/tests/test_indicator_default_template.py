"""Tests for default indicator template builder."""

from app.services.indicator_code_quality import analyze_indicator_code_quality
from app.services.indicator_default_template import build_default_indicator_template
from app.utils.safe_exec import build_safe_builtins, safe_exec_with_validation

import numpy as np
import pandas as pd


def _mock_df(length: int = 120) -> pd.DataFrame:
    close = 10000 * np.exp(np.cumsum(np.random.normal(0, 0.002, length)))
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": np.full(length, 1000.0),
        }
    )


def test_default_template_has_no_execution_columns():
    code = build_default_indicator_template()
    for col in ("open_long", "close_long", "open_short", "close_short"):
        assert f"df[\"{col}\"]" not in code
        assert f"df['{col}']" not in code
    assert "# @strategy" not in code
    assert "signal_form" not in code


def test_default_template_passes_quality_hints():
    code = build_default_indicator_template()
    hints = analyze_indicator_code_quality(code)
    codes = {h["code"] for h in hints}
    assert "MISSING_BUY_SELL_COLUMNS" not in codes
    assert "MISSING_OUTPUT" not in codes
    assert "EXECUTION_COLUMNS_IGNORED_FOR_INDICATOR" not in codes
    assert "STRATEGY_ANNOTATIONS_IGNORED_FOR_INDICATOR" not in codes


def test_default_template_executes_in_sandbox():
    code = build_default_indicator_template()
    df = _mock_df()
    env = {
        "df": df.copy(),
        "pd": pd,
        "np": np,
        "params": {},
        "output": None,
        "__builtins__": build_safe_builtins(),
    }
    result = safe_exec_with_validation(code=code, exec_globals=env, exec_locals=env, timeout=15)
    assert result.get("success"), result.get("error")
    out = env.get("output")
    assert isinstance(out, dict)
    assert out.get("schema_version") == 2
    assert out.get("plots")
    assert out.get("signals")
    assert out.get("tables") == []
    assert all(plot.get("type") == "line" for plot in out["plots"])
    assert all(signal.get("renderMode") == "events" for signal in out["signals"])
    executed = env["df"]
    for col in ("open_long", "close_long", "open_short", "close_short"):
        assert col not in executed.columns


def test_default_template_uses_boolean_dtype_safe_edge_detection():
    code = build_default_indicator_template()

    assert "shift(1, fill_value=False).astype(bool)" in code
    assert "shift(1).fillna(False)" not in code
