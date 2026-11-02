"""Indicator validation is chart-only."""

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


def test_validation_accepts_output_signals_without_execution_columns():
    result = _validate_indicator_code_internal(OUTPUT_SIGNALS_ONLY)
    assert result["success"] is True
    assert result["error_type"] is None
    assert result["signals_count"] == 1


def test_validation_rejects_zone_without_renderable_bounds():
    code = OUTPUT_SIGNALS_ONLY + "\noutput['layers'] = [{'type': 'zone', 'upper': 120}]\n"
    result = _validate_indicator_code_internal(code)
    assert result["success"] is False
    assert result["error_type"] == "InvalidZoneLayer"


def test_validation_rejects_legacy_zone_bounds():
    code = OUTPUT_SIGNALS_ONLY + "\noutput['layers'] = [{'type': 'zone', 'upper': 120, 'lower': 100}]\n"
    result = _validate_indicator_code_internal(code)
    assert result["success"] is False
    assert result["error_type"] == "InvalidZoneLayer"
