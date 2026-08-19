from datetime import datetime, timedelta
from typing import Any, Dict

import numpy as np
import pandas as pd

from app.services.indicator_code_quality import analyze_indicator_code_quality
from app.services.indicator_params import IndicatorParamsParser
from app.utils.safe_exec import safe_exec_indicator_isolated


def generate_mock_df(length: int = 200) -> pd.DataFrame:
    dates = [datetime.now() - timedelta(minutes=i) for i in range(length)]
    dates.reverse()

    returns = np.random.normal(0, 0.002, length)
    price_path = 10000 * np.exp(np.cumsum(returns))

    close = price_path
    high = close * (1 + np.abs(np.random.normal(0, 0.001, length)))
    low = close * (1 - np.abs(np.random.normal(0, 0.001, length)))
    open_price = close * (1 + np.random.normal(0, 0.001, length))
    high = np.maximum(high, np.maximum(open_price, close))
    low = np.minimum(low, np.minimum(open_price, close))
    volume = np.abs(np.random.normal(100, 50, length)) * 1000

    return pd.DataFrame({
        "time": [int(item.timestamp() * 1000) for item in dates],
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def merge_indicator_params(code: str, user_params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    declared_params = IndicatorParamsParser.parse_params(code or "")
    return IndicatorParamsParser.merge_params(declared_params, user_params or {})


def validate_indicator_code(code: str, user_params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    raw = (code or "").strip()
    if not raw:
        return {
            "success": False,
            "msg": "Code is empty",
            "error_type": "EmptyCode",
            "details": None,
            "plots_count": 0,
            "signals_count": 0,
            "hints": [{"severity": "error", "code": "EMPTY_CODE", "params": {}}],
        }

    hints = analyze_indicator_code_quality(raw)
    df = generate_mock_df()
    merged_params = merge_indicator_params(raw, user_params)
    exec_result = safe_exec_indicator_isolated(
        code=raw,
        df=df,
        params=merged_params,
        timeout=20,
    )
    if not exec_result.get("success"):
        error_detail = exec_result.get("error") or "Unknown error"
        is_security = error_detail.startswith("Unsafe code rejected")
        return {
            "success": False,
            "msg": f"{'Security' if is_security else 'Runtime'} Error: {error_detail}",
            "error_type": "SecurityError" if is_security else "RuntimeError",
            "details": error_detail,
            "plots_count": 0,
            "signals_count": 0,
            "hints": hints,
        }

    output = (exec_result.get("result") or {}).get("output")
    if output is None:
        return {
            "success": False,
            "msg": "Missing 'output' variable. Your code must define an 'output' dictionary.",
            "error_type": "MissingOutput",
            "details": None,
            "plots_count": 0,
            "signals_count": 0,
            "hints": hints,
        }
    if not isinstance(output, dict):
        return {
            "success": False,
            "msg": f"'output' must be a dictionary, got {type(output).__name__}",
            "error_type": "InvalidOutputType",
            "details": None,
            "plots_count": 0,
            "signals_count": 0,
            "hints": hints,
        }
    if "plots" not in output and "signals" not in output:
        return {
            "success": False,
            "msg": "'output' dict should contain 'plots' or 'signals' list.",
            "error_type": "InvalidOutputStructure",
            "details": None,
            "plots_count": 0,
            "signals_count": 0,
            "hints": hints,
        }

    plots = output.get("plots", [])
    signals = output.get("signals", [])
    for plot in plots:
        if "data" not in plot:
            return _validation_error("InvalidPlot", f"Plot '{plot.get('name')}' missing 'data' field.", plots, signals, hints)
        if len(plot["data"]) != len(df):
            return _validation_error(
                "LengthMismatch",
                f"Plot '{plot.get('name')}' data length ({len(plot['data'])}) does not match DataFrame length ({len(df)}).",
                plots,
                signals,
                hints,
            )

    for signal in signals:
        if "data" not in signal:
            return _validation_error("InvalidSignal", f"Signal '{signal.get('type')}' missing 'data' field.", plots, signals, hints)
        if len(signal["data"]) != len(df):
            return _validation_error(
                "LengthMismatch",
                f"Signal '{signal.get('type')}' data length ({len(signal['data'])}) does not match DataFrame length ({len(df)}).",
                plots,
                signals,
                hints,
            )

    return {
        "success": True,
        "msg": "Verification passed. Indicator code rendered successfully.",
        "error_type": None,
        "details": None,
        "plots_count": len(plots),
        "signals_count": len(signals),
        "hints": hints,
    }


def _validation_error(
    error_type: str,
    message: str,
    plots: list[Dict[str, Any]],
    signals: list[Dict[str, Any]],
    hints: list[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "success": False,
        "msg": message,
        "error_type": error_type,
        "details": None,
        "plots_count": len(plots),
        "signals_count": len(signals),
        "hints": hints,
    }


def indicator_debug_summary(validation: Dict[str, Any] | None = None) -> Dict[str, Any]:
    validation = validation or {}
    hints = validation.get("hints") or []
    return {
        "success": bool(validation.get("success")),
        "message": validation.get("msg"),
        "error_type": validation.get("error_type"),
        "hint_codes": [hint.get("code") for hint in hints if hint.get("code")],
        "hint_count": len(hints),
        "plots_count": validation.get("plots_count", 0),
        "signals_count": validation.get("signals_count", 0),
    }
