"""Bounded same-symbol market-data context for chart indicators."""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from app.services.kline import KlineService


SUPPORTED_INDICATOR_TIMEFRAMES = {
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1H",
    "2H",
    "4H",
    "6H",
    "8H",
    "12H",
    "1D",
    "1W",
}

_TIMEFRAME_ALIASES = {
    "1h": "1H",
    "2h": "2H",
    "4h": "4H",
    "6h": "6H",
    "8h": "8H",
    "12h": "12H",
    "1d": "1D",
    "1w": "1W",
}

_TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "2H": 7200,
    "4H": 14400,
    "6H": 21600,
    "8H": 28800,
    "12H": 43200,
    "1D": 86400,
    "1W": 604800,
}

MAX_INDICATOR_CONTEXT_REQUESTS = 8
MAX_INDICATOR_SERIES_BARS = 1_000
MAX_INDICATOR_LOWER_TF_BARS = 5_000


def normalize_indicator_timeframe(value: Any) -> str:
    raw = str(value or "").strip()
    normalized = _TIMEFRAME_ALIASES.get(raw, raw)
    if normalized not in SUPPORTED_INDICATOR_TIMEFRAMES:
        raise ValueError(f"Unsupported indicator timeframe: {raw or value}")
    return normalized


def indicator_timeframe_seconds(value: Any) -> int:
    return _TIMEFRAME_SECONDS[normalize_indicator_timeframe(value)]


@dataclass(frozen=True)
class IndicatorDataRequest:
    timeframe: str
    mode: str = "series"

    def as_dict(self) -> dict[str, str]:
        return {"timeframe": self.timeframe, "mode": self.mode}


def _literal_timeframes(value: ast.AST) -> list[str]:
    try:
        raw = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError):
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return []
    return [str(item) for item in raw if isinstance(item, str)]


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def extract_indicator_data_requests(
    code: str,
    params: Mapping[str, Any] | None = None,
) -> list[IndicatorDataRequest]:
    """Read literal same-symbol timeframe requirements without executing code."""
    requests: list[IndicatorDataRequest] = []

    def add(value: Any, mode: str) -> None:
        try:
            timeframe = normalize_indicator_timeframe(value)
        except ValueError:
            return
        request = IndicatorDataRequest(timeframe=timeframe, mode=mode)
        if request not in requests:
            requests.append(request)

    for match in re.finditer(
        r"^\s*#\s*@(lower[_-]?timeframe|timeframe)\s+([^\s#]+)",
        str(code or ""),
        flags=re.MULTILINE | re.IGNORECASE,
    ):
        mode = "lower_tf" if str(match.group(1)).lower().startswith("lower") else "series"
        add(match.group(2), mode)

    try:
        tree = ast.parse(str(code or ""))
    except SyntaxError:
        return requests[:MAX_INDICATOR_CONTEXT_REQUESTS]

    bound_params: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id not in {"indicator_timeframe_param", "indicator_lower_timeframe_param"}:
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                bound_params[target.id] = node.value.value

    selected_series = str((params or {}).get(bound_params.get("indicator_timeframe_param", "")) or "")
    selected_lower = str((params or {}).get(bound_params.get("indicator_lower_timeframe_param", "")) or "")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id == "indicator_timeframes":
                    declared = _literal_timeframes(value)
                    chosen = [selected_series] if selected_series in declared else declared
                    for timeframe in chosen:
                        add(timeframe, "series")
                elif target.id == "indicator_lower_timeframes":
                    declared = _literal_timeframes(value)
                    chosen = [selected_lower] if selected_lower in declared else declared
                    for timeframe in chosen:
                        add(timeframe, "lower_tf")
        elif isinstance(node, ast.Call) and node.args:
            name = _call_name(node)
            if name not in {"request_series", "request_lower_tf"}:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                add(first.value, "lower_tf" if name == "request_lower_tf" else "series")

    return requests[:MAX_INDICATOR_CONTEXT_REQUESTS]


def normalize_indicator_data_requests(values: Iterable[Any]) -> list[IndicatorDataRequest]:
    requests: list[IndicatorDataRequest] = []
    for value in values or []:
        if isinstance(value, IndicatorDataRequest):
            timeframe = value.timeframe
            mode = str(value.mode or "series").strip().lower()
        elif isinstance(value, Mapping):
            timeframe = value.get("timeframe") or value.get("interval")
            mode = str(value.get("mode") or "series").strip().lower()
        else:
            timeframe = value
            mode = "series"
        if mode not in {"series", "lower_tf"}:
            raise ValueError(f"Unsupported indicator data request mode: {mode}")
        request = IndicatorDataRequest(
            timeframe=normalize_indicator_timeframe(timeframe),
            mode=mode,
        )
        if request not in requests:
            requests.append(request)
        if len(requests) > MAX_INDICATOR_CONTEXT_REQUESTS:
            raise ValueError(
                f"Indicators may request at most {MAX_INDICATOR_CONTEXT_REQUESTS} timeframes"
            )
    return requests


def _timestamp_seconds(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp,)):
        return int(value.timestamp())
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        try:
            return int(pd.Timestamp(value).timestamp())
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(numeric):
        return None
    if numeric > 10_000_000_000:
        numeric /= 1000.0
    return int(numeric)


def _row_time(row: Mapping[str, Any]) -> int | None:
    return _timestamp_seconds(
        row.get("time")
        or row.get("timestamp")
        or row.get("datetime")
        or row.get("date")
        or row.get("open_time")
    )


def _normalize_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_time: dict[int, dict[str, Any]] = {}
    for item in rows or []:
        row = dict(item or {})
        timestamp = _row_time(row)
        if timestamp is None:
            continue
        normalized: dict[str, Any] = {"time": timestamp}
        for target, aliases in {
            "open": ("open", "o"),
            "high": ("high", "h"),
            "low": ("low", "l"),
            "close": ("close", "c"),
            "volume": ("volume", "v"),
        }.items():
            raw = next((row.get(alias) for alias in aliases if row.get(alias) is not None), None)
            try:
                numeric = float(raw)
            except (TypeError, ValueError, OverflowError):
                numeric = None
            normalized[target] = numeric if numeric is not None and math.isfinite(numeric) else None
        by_time[timestamp] = normalized
    return [by_time[key] for key in sorted(by_time)]


class IndicatorMarketDataService:
    """Fetch bounded same-symbol timeframes for the indicator sandbox."""

    def __init__(self, kline_service: KlineService | None = None) -> None:
        self.kline = kline_service or KlineService()

    def build_context(
        self,
        *,
        market: str,
        symbol: str,
        base_timeframe: str,
        base_count: int,
        requests: Iterable[Any],
        exchange_id: str | None = None,
        market_type: str | None = None,
        instrument_id: str | None = None,
    ) -> dict[str, Any]:
        base_tf = normalize_indicator_timeframe(base_timeframe)
        normalized_requests = normalize_indicator_data_requests(requests)
        base_count = max(1, min(int(base_count or 1), 1000))
        series: dict[str, list[dict[str, Any]]] = {}
        coverage: dict[str, dict[str, Any]] = {}

        for request in normalized_requests:
            target = self._target_bar_count(base_tf, request, base_count)
            rows = self._fetch_rows(
                market=market,
                symbol=symbol,
                timeframe=request.timeframe,
                target=target,
                exchange_id=exchange_id,
                market_type=market_type,
                instrument_id=instrument_id,
            )
            normalized_rows = _normalize_rows(rows)
            series[request.timeframe] = normalized_rows
            coverage[request.timeframe] = {
                "mode": request.mode,
                "target_bars": target,
                "returned_bars": len(normalized_rows),
            }

        return {
            "version": 1,
            "market": str(market or ""),
            "symbol": str(symbol or ""),
            "timeframe": base_tf,
            "exchange_id": str(exchange_id or ""),
            "market_type": str(market_type or ""),
            "instrument_id": str(instrument_id or ""),
            "requests": [request.as_dict() for request in normalized_requests],
            "series": series,
            "coverage": coverage,
            "limits": {
                "series_bars": MAX_INDICATOR_SERIES_BARS,
                "lower_tf_bars": MAX_INDICATOR_LOWER_TF_BARS,
                "host_fetches_per_timeframe": 1,
            },
        }

    @staticmethod
    def _target_bar_count(
        base_timeframe: str,
        request: IndicatorDataRequest,
        base_count: int,
    ) -> int:
        if request.mode != "lower_tf":
            return min(MAX_INDICATOR_SERIES_BARS, max(300, base_count + 120))
        base_seconds = indicator_timeframe_seconds(base_timeframe)
        source_seconds = indicator_timeframe_seconds(request.timeframe)
        if source_seconds >= base_seconds:
            return min(MAX_INDICATOR_SERIES_BARS, max(300, base_count + 120))
        ratio = int(math.ceil(base_seconds / source_seconds))
        return min(MAX_INDICATOR_LOWER_TF_BARS, max(300, (base_count + 2) * ratio))

    def _fetch_rows(
        self,
        *,
        market: str,
        symbol: str,
        timeframe: str,
        target: int,
        exchange_id: str | None,
        market_type: str | None,
        instrument_id: str | None,
    ) -> list[dict[str, Any]]:
        # One bounded host fetch per timeframe keeps indicator previews from
        # multiplying provider calls. Provider adapters may still batch within
        # their own documented limits, retries, and throttling policies.
        rows = self.kline.get_kline(
            market=market,
            symbol=symbol,
            timeframe=timeframe,
            limit=target,
            exchange_id=exchange_id,
            market_type=market_type,
            instrument_id=instrument_id,
        ) or []
        return _normalize_rows(rows)[-target:]
