"""Shared decimal helpers for exchange constraints and generated strategy source."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any


_STEP_EDGE_RELATIVE_TOLERANCE = Decimal("1e-12")


def to_decimal(value: Any) -> Decimal:
    """Convert through ``str`` so binary float noise is not expanded further."""
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def floor_decimal_to_step(value: Any, step: Any) -> Decimal:
    """Floor a positive value to a step while absorbing float-only edge noise.

    Order quantities commonly cross float-based ledgers before reaching an
    exchange adapter.  A mathematically exact step such as ``0.0001`` can then
    arrive as ``0.00009999999999999994``.  Blind ``ROUND_DOWN`` turns that into
    the previous step (often zero).  Values within one part per trillion of an
    integer step are therefore snapped to that integer before flooring; real
    below-step quantities remain below the boundary.
    """
    number = to_decimal(value)
    increment = to_decimal(step)
    if number <= 0:
        return Decimal("0")
    if increment <= 0:
        return number
    try:
        units = number / increment
        nearest = units.to_integral_value(rounding=ROUND_HALF_UP)
        tolerance = max(
            _STEP_EDGE_RELATIVE_TOLERANCE,
            abs(units) * _STEP_EDGE_RELATIVE_TOLERANCE,
        )
        if nearest > 0 and abs(units - nearest) <= tolerance:
            units = nearest
        else:
            units = units.to_integral_value(rounding=ROUND_DOWN)
        return units * increment
    except Exception:
        return Decimal("0")


def clean_generated_number(value: Any, *, decimal_places: int = 12) -> float:
    """Return a stable, bounded-precision float for generated Python source."""
    places = min(18, max(0, int(decimal_places)))
    number = to_decimal(value)
    quantum = Decimal("1").scaleb(-places)
    try:
        cleaned = number.quantize(quantum, rounding=ROUND_HALF_UP)
    except Exception:
        cleaned = number
    if cleaned == 0:
        return 0.0
    return float(cleaned)


def format_decimal(value: Any, *, decimal_places: int = 12) -> str:
    """Format a user-facing number without scientific notation or zero padding."""
    cleaned = clean_generated_number(value, decimal_places=decimal_places)
    text = format(cleaned, f".{min(18, max(0, int(decimal_places)))}f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


__all__ = [
    "clean_generated_number",
    "floor_decimal_to_step",
    "format_decimal",
    "to_decimal",
]
