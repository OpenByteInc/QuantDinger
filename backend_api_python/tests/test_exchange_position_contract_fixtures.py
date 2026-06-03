from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from app.services.live_trading.position_row_parse import (
    infer_position_side_from_row,
    position_base_qty_for_side,
)


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "exchanges" / "position_contracts.json"


def _load_cases() -> list[Dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
def test_position_contract_fixture(case: Dict[str, Any]):
    row = case["row"]
    expected = case["expected"]

    inferred = infer_position_side_from_row(row)
    qty = position_base_qty_for_side(
        row,
        str(case["side"]),
        contracts_to_base=float(case.get("contracts_to_base") or 1.0),
    )

    assert inferred == expected["inferred_side"]
    assert qty == pytest.approx(float(expected["base_qty"]))


def test_position_contract_returns_zero_for_opposite_side():
    row = {"positionSide": "LONG", "positionAmt": "0.5"}

    assert position_base_qty_for_side(row, "short") == 0.0
