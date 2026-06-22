"""Indicator validation enforces executable four-way columns."""

from __future__ import annotations

from app.routes.indicator import _validate_indicator_code_internal


OUTPUT_SIGNALS_ONLY = """
my_indicator_name = "Old Sample"
my_indicator_description = "Chart markers only"
df = df.copy()
marks = [None] * len(df)
output = {
    'name': my_indicator_name,
    'plots': [],
    'signals': [
        {'type': 'buy', 'text': 'B', 'color': '#00E676', 'data': marks},
    ],
}
"""


def test_validation_rejects_output_signals_without_execution_columns():
    result = _validate_indicator_code_internal(OUTPUT_SIGNALS_ONLY)
    assert result["success"] is False
    assert result["error_type"] == "MissingExecutionColumns"
    assert "output['signals'] is chart-only" in result["msg"]

