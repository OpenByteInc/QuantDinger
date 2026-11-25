from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.services.indicator_validation import merge_indicator_params
from app.utils.safe_exec import safe_exec_indicator_isolated, validate_code_safety


LIBRARY_ROOT = Path(__file__).resolve().parents[2] / "library" / "indicators"
SAVE_REGRESSION_IDS = tuple(range(1, 200)) + (
    211, 213, 216, 219, 222, 224, 228, 230, 233, 234, 236,
    238, 242, 251, 260, 262, 275, 278, 285, 300, 303, 308,
    311, 312, 314, 322, 341, 342, 351, 353, 355, 365, 379,
    380, 383, 384, 385, 387, 392, 398, 403, 408, 410, 430,
)


def _indicator_source(indicator_id: int) -> str:
    matches = list(LIBRARY_ROOT.glob(f"{indicator_id:03d}_*.py"))
    assert len(matches) == 1, f"Expected one library file for indicator {indicator_id}"
    return matches[0].read_text(encoding="utf-8")


def _market_frame(step_seconds: int, count: int = 200) -> pd.DataFrame:
    start_ms = int(pd.Timestamp("2026-01-01T00:00:00Z").timestamp() * 1000)
    times = start_ms + np.arange(count, dtype=np.int64) * step_seconds * 1000
    trend = np.linspace(59_000.0, 61_000.0, count)
    close = trend + np.sin(np.arange(count) / 7.0) * 300.0
    return pd.DataFrame({
        "time": times,
        "open": close - 20.0,
        "high": close + 100.0,
        "low": close - 100.0,
        "close": close,
        "volume": np.full(count, 1_000.0),
    })


def _execute(indicator_id: int, timeframe: str, step_seconds: int) -> dict:
    code = _indicator_source(indicator_id)
    result = safe_exec_indicator_isolated(
        code,
        _market_frame(step_seconds),
        merge_indicator_params(code),
        context={
            "version": 1,
            "market": "Crypto",
            "symbol": "BTC/USDT",
            "timeframe": timeframe,
            "requests": [],
            "series": {},
        },
        timeout=20,
    )
    assert result["success"] is True, result.get("error")
    return result["result"]["output"]


@pytest.mark.parametrize("indicator_id", SAVE_REGRESSION_IDS)
def test_reported_library_indicators_pass_save_safety(indicator_id: int):
    code = _indicator_source(indicator_id)
    accepted, error = validate_code_safety(code)
    assert accepted is True, error
    compile(code, f"indicator-{indicator_id:03d}", "exec")


def test_position_risk_planner_has_a_renderable_default_plan():
    output = _execute(365, "1H", 3_600)
    plots = {plot["name"]: plot["data"] for plot in output["plots"]}

    assert all(value is not None for value in plots["Entry"])
    assert all(value is not None for value in plots["Take Profit"])
    assert all(value is not None for value in plots["Stop Loss"])
    assert output["tables"][0]["rows"][0][1]["text"] == "Latest Close"
    assert output["calculatedVars"]["openPlPercent"][-1] == pytest.approx(0.0)


def test_market_sessions_rejects_inaccurate_four_hour_sampling():
    output = _execute(385, "4H", 14_400)

    assert output["layers"] == []
    assert len(output["plots"]) == 1
    assert output["tables"][0]["rows"][0][1]["text"] == "Use an intraday chart below 4H"


def test_market_sessions_keeps_intraday_annotations_sparse():
    output = _execute(385, "1H", 3_600)
    labels = [layer for layer in output["layers"] if layer.get("type") == "label"]
    zones = [layer for layer in output["layers"] if layer.get("type") == "zone"]

    assert zones
    assert len(labels) <= 16
    assert max(zone["opacity"] for zone in zones) <= 0.06
