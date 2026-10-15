"""TA-Lib-backed indicator catalog and deterministic computation adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd


MINIMUM_FUNCTION_COUNT = 201

TA_LIB_081_FUNCTIONS = frozenset({
    "AC", "ADR", "AO", "CMF", "CMOU", "COPPOCK", "CUMSUM", "CVI",
    "DONCHIAN", "DPO", "EFI", "ER", "ERI", "FOSC", "FRACTAL", "HA",
    "HMA", "KC", "KDJ", "MARKETFI", "MASSI", "NVI", "PERCENTILE",
    "PERCENTRANK", "PVI", "PVO", "PVT", "QSTICK", "RMA", "RVI", "RVOL",
    "SMI", "SUPERTREND", "TSI", "VHF", "VORTEX", "VWAP", "VWMA", "WAD",
    "ZLEMA",
})

class TalibFactorError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def is_talib_available() -> bool:
    try:
        _load_talib()
        return True
    except TalibFactorError:
        return False


def list_talib_factors() -> list[dict[str, Any]]:
    talib, abstract = _load_talib()
    groups = talib.get_function_groups()
    group_by_name = {
        str(name).upper(): str(group)
        for group, names in groups.items()
        for name in names
    }
    output = []
    for name in sorted(talib.get_functions()):
        function = abstract.Function(name)
        info = function.info
        required_fields = tuple(sorted(_flatten_input_names(info.get("input_names") or {})))
        parameters = dict(info.get("parameters") or {})
        outputs = list(info.get("output_names") or [])
        if len(outputs) > 1:
            parameters["output"] = outputs[0]
        output.append({
            "factor_id": f"talib:{name.upper()}",
            "library_id": name.upper(),
            "version": str(getattr(talib, "__version__", "")),
            "display_name": str(info.get("display_name") or name),
            "name_i18n_key": f"factor.talib.{name.lower()}.name",
            "description_i18n_key": f"factor.talib.{name.lower()}.description",
            "category": _category(group_by_name.get(name.upper(), "")),
            "factor_type": "technical",
            "provider": "ta-lib",
            "introduced_in": "0.8.1" if name.upper() in TA_LIB_081_FUNCTIONS else "",
            "required_fields": list(required_fields),
            "default_params": parameters,
            "parameter_schema": _parameter_schema(parameters, outputs),
            "outputs": outputs,
            "direction_hint": "neutral",
            "supported_contexts": ["cta", "portfolio"],
            "default_warmup_bars": int(getattr(function, "lookback", 0) or 0),
        })
    return output


def assert_talib_catalog_ready() -> int:
    catalog = list_talib_factors()
    count = len(catalog)
    available = {item["library_id"] for item in catalog}
    missing = sorted(TA_LIB_081_FUNCTIONS - available)
    if count < MINIMUM_FUNCTION_COUNT or missing:
        suffix = f":{','.join(missing)}" if missing else ""
        raise TalibFactorError(f"factor.talibCatalogIncomplete:{count}{suffix}")
    return count


def compute_talib_indicator(
    name: str,
    frame: pd.DataFrame,
    params: Mapping[str, Any] | None = None,
) -> pd.Series | pd.DataFrame:
    _, abstract = _load_talib()
    library_id = str(name or "").strip().upper().replace("TALIB:", "")
    if not library_id:
        raise TalibFactorError("factor.notFound")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise TalibFactorError("factor.noData")
    function = abstract.Function(library_id)
    required = _flatten_input_names(function.info.get("input_names") or {})
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise TalibFactorError(f"factor.missingFields:{','.join(missing)}")
    clean = frame.copy()
    clean.columns = [str(column).strip().lower() for column in clean.columns]
    normalized = _normalized_parameters(function, params)
    try:
        result = function(clean, **normalized)
    except Exception as exc:
        raise TalibFactorError("factor.computeFailed") from exc
    outputs = list(function.info.get("output_names") or [])
    if isinstance(result, (tuple, list)):
        data = {outputs[index] if index < len(outputs) else f"output_{index}": value for index, value in enumerate(result)}
        return pd.DataFrame(data, index=clean.index)
    if isinstance(result, pd.Series):
        return result
    values = np.asarray(result)
    if values.ndim == 2:
        output_count = len(outputs)
        if output_count and values.shape[0] == output_count and values.shape[1] == len(clean.index):
            values = values.T
        if values.shape[0] != len(clean.index):
            raise TalibFactorError("factor.computeFailed")
        columns = outputs or [f"output_{index}" for index in range(values.shape[1])]
        if len(columns) != values.shape[1]:
            columns = [f"output_{index}" for index in range(values.shape[1])]
        return pd.DataFrame(values, index=clean.index, columns=columns)
    if values.ndim != 1:
        raise TalibFactorError("factor.computeFailed")
    output_name = outputs[0] if outputs else library_id.lower()
    return pd.Series(values, index=clean.index, name=output_name)


def compute_talib_factor(
    name: str,
    frame: pd.DataFrame,
    params: Mapping[str, Any] | None = None,
    *,
    output: str = "",
) -> float:
    normalized = dict(params or {})
    selected_from_params = normalized.pop("output", "")
    result = compute_talib_indicator(name, frame, normalized)
    if isinstance(result, pd.DataFrame):
        selected = str(output or selected_from_params or "").strip()
        if not selected:
            selected = str(result.columns[0])
        if selected not in result.columns:
            raise TalibFactorError("factor.outputNotFound")
        values = result[selected]
    else:
        values = result
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        raise TalibFactorError("factor.insufficientHistory")
    return float(clean.iloc[-1])


def _load_talib():
    try:
        import talib
        from talib import abstract
    except Exception as exc:
        raise TalibFactorError("factor.talibUnavailable") from exc
    return talib, abstract


def _flatten_input_names(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip().lower()]
    if isinstance(value, Mapping):
        output = []
        for nested in value.values():
            output.extend(_flatten_input_names(nested))
        return output
    if isinstance(value, (list, tuple, set)):
        output = []
        for nested in value:
            output.extend(_flatten_input_names(nested))
        return output
    return []


def _parameter_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _parameter_schema(parameters: Mapping[str, Any], outputs: list[str]) -> dict[str, dict[str, Any]]:
    schema = {}
    for key, value in parameters.items():
        if key == "output":
            schema[key] = {"type": "enum", "default": value, "options": list(outputs)}
            continue
        item = {"type": _parameter_type(value), "default": value}
        if item["type"] == "integer":
            item.update({"minimum": 1, "maximum": 5000, "step": 1})
        elif item["type"] == "number":
            item.update({"step": 0.1})
        schema[key] = item
    return schema


def _normalized_parameters(function: Any, params: Mapping[str, Any] | None) -> dict[str, Any]:
    supported = set((function.info.get("parameters") or {}).keys())
    normalized = dict(params or {})
    normalized.pop("output", None)
    aliases = {
        "period": "timeperiod",
        "fast_period": "fastperiod",
        "slow_period": "slowperiod",
        "signal_period": "signalperiod",
    }
    for source, target in aliases.items():
        if source in normalized and target in supported:
            value = normalized.pop(source)
            normalized.setdefault(target, value)
    return normalized


def _category(group: str) -> str:
    raw = str(group or "").strip().lower()
    if "pattern" in raw:
        return "pattern_recognition"
    if "momentum" in raw:
        return "momentum"
    if "volume" in raw:
        return "volume"
    if "volatility" in raw:
        return "volatility"
    if "price" in raw:
        return "price_transform"
    if "cycle" in raw:
        return "cycle"
    if "statistic" in raw:
        return "statistic"
    if "math" in raw:
        return "math"
    return "overlap"
