"""Optional Qveris market-data adapter with existing-source fallback.

Qveris discovers a suitable upstream tool, executes it, and returns the
provider response. This adapter keeps provider-specific response shapes inside
the integration boundary and normalizes common OHLCV/quote layouts for
QuantDinger.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from app.data_sources.base import BaseDataSource
from app.utils.logger import get_logger

logger = get_logger(__name__)


class QverisError(RuntimeError):
    """Raised when Qveris discovery, execution, or normalization fails."""


_PARAM_ALIASES = {
    "symbol": {
        "symbol",
        "symbols",
        "symbolexchange",
        "ticker",
        "tickers",
        "pair",
        "instrument",
        "instid",
        "code",
        "asset",
    },
    "market": {"market", "assetclass", "assettype"},
    "timeframe": {"timeframe", "interval", "resolution", "period", "granularity", "frequency"},
    "limit": {"limit", "count", "rows", "size", "outputsize", "numresults", "numberofresults"},
    "start": {"start", "startdate", "starttime", "from", "fromdate", "fromtime", "since"},
    "end": {"end", "enddate", "endtime", "to", "todate", "totime", "until", "before"},
}

_ROW_ALIASES = {
    "time": {"time", "times", "timestamp", "timestamps", "datetime", "datetimes", "date", "dates", "opentime", "t"},
    "open": {"open", "o", "1open"},
    "high": {"high", "h", "2high"},
    "low": {"low", "l", "3low"},
    "close": {"close", "c", "price", "4close", "adjustedclose", "adjclose"},
    "volume": {"volume", "v", "5volume", "6volume"},
}

_QUOTE_ALIASES = {
    "last": {"last", "price", "close", "currentprice", "latestprice", "regularmarketprice", "c"},
    "change": {"change", "netchange", "d"},
    "changePercent": {"changepercent", "percentchange", "percentagechange", "changepct", "dp"},
    "high": {"high", "dayhigh", "regularmarketdayhigh", "h"},
    "low": {"low", "daylow", "regularmarketdaylow", "l"},
    "open": {"open", "regularmarketopen", "o"},
    "previousClose": {"previousclose", "prevclose", "regularmarketpreviousclose", "pc"},
}

_TIMEFRAME_OPTIONS = {
    "1m": ("1m", "1min", "1minute"),
    "3m": ("3m", "3min", "3minute"),
    "5m": ("5m", "5min", "5minute"),
    "15m": ("15m", "15min", "15minute"),
    "30m": ("30m", "30min", "30minute"),
    "1H": ("1H", "1h", "60m", "hour", "hourly"),
    "4H": ("4H", "4h", "240m", "4hour"),
    "1D": ("1D", "1d", "d", "day", "daily"),
    "1W": ("1W", "1w", "w", "week", "weekly"),
}

_MARKET_RANK_TERMS = {
    "USStock": ({"stock", "equity", "nasdaq", "nyse"}, {"crypto", "cryptocurrency", "forex"}),
    "CNStock": ({"stock", "equity", "china", "a-share"}, {"crypto", "cryptocurrency", "forex"}),
    "HKStock": ({"stock", "equity", "hong kong", "hk"}, {"crypto", "cryptocurrency", "forex"}),
    "Crypto": ({"crypto", "cryptocurrency", "digital asset"}, {"stock", "equity", "forex"}),
    "Forex": ({"forex", "foreign exchange", "currency"}, {"stock", "equity", "crypto"}),
    "Futures": ({"futures", "commodity", "derivative"}, {"stock", "equity", "crypto"}),
    "MOEX": ({"moex", "russia", "russian"}, {"crypto", "cryptocurrency", "forex"}),
}


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            value = value.strip().replace(",", "").replace("$", "").replace("%", "")
            if not value or value.lower() in {"none", "null", "n/a", "nan", "-"}:
                return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        return int(number) if number > 0 else None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return _timestamp(float(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except ValueError:
        return None


def _field(row: Dict[str, Any], aliases: set[str]) -> Any:
    normalized = {_key(name): value for name, value in row.items()}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


class QverisDataSource(BaseDataSource):
    """Use Qveris first and preserve the configured QuantDinger fallback."""

    name = "Qveris"
    DEFAULT_BASE_URL = "https://qveris.ai/api/v1"

    def __init__(
        self,
        market: str,
        fallback: BaseDataSource,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        session: Optional[requests.Session] = None,
    ):
        self.market = str(market or "").strip()
        self.fallback = fallback
        self.api_key = str(api_key if api_key is not None else os.getenv("QVERIS_API_KEY", "")).strip()
        self.base_url = str(base_url or os.getenv("QVERIS_BASE_URL") or self.DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout or os.getenv("QVERIS_TIMEOUT", "30"))
        self.discovery_ttl = max(0, int(os.getenv("QVERIS_DISCOVERY_TTL_SECONDS", "3600")))
        self.session = session or requests.Session()
        self.name = f"Qveris/{self.market}+{fallback.name}"
        self._discovery_cache: Dict[str, tuple[float, Dict[str, Any], str]] = {}

    @classmethod
    def is_enabled_for(cls, market: str) -> bool:
        if not (os.getenv("QVERIS_API_KEY") or "").strip():
            return False
        configured = {
            value.strip().lower()
            for value in (os.getenv("QVERIS_DATA_SOURCE_MARKETS") or "").split(",")
            if value.strip()
        }
        return "*" in configured or str(market or "").strip().lower() in configured

    def get_kline(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        before_time: Optional[int] = None,
        after_time: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        try:
            result = self._execute(
                "kline",
                symbol=symbol,
                timeframe=timeframe,
                limit=max(1, int(limit or 1)),
                before_time=before_time,
                after_time=after_time,
            )
            rows = self._normalize_klines(result)
            rows = self.filter_and_limit(
                rows,
                limit=max(1, int(limit or 1)),
                before_time=before_time,
                after_time=after_time,
                truncate=(after_time is None),
            )
            if rows:
                self.log_result(symbol, rows, timeframe)
                return rows
            raise QverisError("Qveris returned no normalizable OHLCV rows")
        except Exception as exc:
            logger.warning(
                "Qveris K-line request failed for %s:%s (%s); using %s",
                self.market,
                symbol,
                str(exc)[:240],
                self.fallback.name,
            )
            return self.fallback.get_kline(symbol, timeframe, limit, before_time, after_time)

    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        try:
            result = self._execute("ticker", symbol=symbol)
            quote = self._normalize_ticker(result)
            if quote and float(quote.get("last") or 0) > 0:
                quote.setdefault("symbol", symbol)
                return quote
            raise QverisError("Qveris returned no normalizable quote")
        except Exception as exc:
            logger.warning(
                "Qveris ticker request failed for %s:%s (%s); using %s",
                self.market,
                symbol,
                str(exc)[:240],
                self.fallback.name,
            )
            return self.fallback.get_ticker(symbol)

    def _execute(self, operation: str, **context: Any) -> Any:
        if not self.api_key:
            raise QverisError("QVERIS_API_KEY is not configured")
        tool, discovery_id = self._discover(operation, str(context.get("timeframe") or ""))
        parameters = self._build_parameters(self._tool_parameters(tool), context)
        payload = self._request_json(
            "/tools/execute",
            params={"tool_id": tool.get("tool_id")},
            json={
                "search_id": discovery_id,
                "parameters": parameters,
                "max_response_size": 262144,
            },
        )
        if payload.get("success") is False:
            raise QverisError(str(payload.get("error_message") or "Qveris tool execution failed"))
        result = payload.get("result", payload)
        if isinstance(result, str):
            try:
                return json.loads(result)
            except ValueError:
                return result
        return result

    def _discover(self, operation: str, timeframe: str) -> tuple[Dict[str, Any], str]:
        cache_key = f"{operation}:{self.market}:{timeframe}"
        cached = self._discovery_cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            return cached[1], cached[2]

        if operation == "kline":
            query = (
                f"read-only historical OHLCV candlestick market data API for {self.market}; "
                "accept symbol, interval or timeframe, limit, and optional start/end date"
            )
            preferred = (os.getenv("QVERIS_KLINE_TOOL_ID") or "").strip()
        else:
            query = f"read-only latest market price quote API for {self.market} accepting a symbol"
            preferred = (os.getenv("QVERIS_TICKER_TOOL_ID") or "").strip()

        payload = self._request_json("/search", json={"query": query, "limit": 10})
        discovery_id = str(payload.get("search_id") or payload.get("discovery_id") or "").strip()
        candidates = payload.get("results") or []
        if not discovery_id or not isinstance(candidates, list):
            raise QverisError("Qveris discovery response is missing search_id or results")

        tool = self._select_tool(candidates, preferred, operation, timeframe)
        expires = time.monotonic() + self.discovery_ttl
        self._discovery_cache[cache_key] = (expires, tool, discovery_id)
        return tool, discovery_id

    def _select_tool(
        self,
        candidates: List[Dict[str, Any]],
        preferred: str,
        operation: str,
        timeframe: str,
    ) -> Dict[str, Any]:
        if preferred:
            for candidate in candidates:
                if str(candidate.get("tool_id") or "") == preferred:
                    if self._supports_parameters(self._tool_parameters(candidate), operation, timeframe):
                        return candidate
                    raise QverisError(f"Configured Qveris tool {preferred} has unsupported required parameters")
            raise QverisError(f"Configured Qveris tool {preferred} was not returned by discovery")

        compatible = [
            candidate
            for candidate in candidates
            if isinstance(candidate, dict)
            and candidate.get("tool_id")
            and self._supports_parameters(self._tool_parameters(candidate), operation, timeframe)
        ]
        if not compatible:
            raise QverisError("Qveris discovery returned no compatible tool")

        terms = (
            ("ohlcv", "candlestick", "historical", "time series")
            if operation == "kline"
            else ("quote", "price", "ticker")
        )

        def rank(candidate: Dict[str, Any]) -> tuple[int, int, float, float]:
            text = f"{candidate.get('name', '')} {candidate.get('description', '')}".lower()
            relevance = sum(1 for term in terms if term in text)
            positive, negative = _MARKET_RANK_TERMS.get(self.market, (set(), set()))
            market_affinity = sum(1 for term in positive if term in text)
            market_affinity -= 2 * sum(1 for term in negative if term in text)
            stats = candidate.get("stats") if isinstance(candidate.get("stats"), dict) else {}
            success_rate = _float(candidate.get("success_rate"))
            if success_rate is None:
                success_rate = _float(stats.get("success_rate")) or 0.0
            execution_time = _float(candidate.get("avg_execution_time_ms"))
            if execution_time is None:
                execution_time = _float(stats.get("avg_execution_time_ms"))
            return relevance, market_affinity, success_rate, -(execution_time or float("inf"))

        compatible.sort(key=rank, reverse=True)
        return compatible[0]

    @staticmethod
    def _tool_parameters(tool: Dict[str, Any]) -> Any:
        """Read current Qveris metadata while accepting older cached results."""
        return tool.get("parameters") or tool.get("params") or []

    @staticmethod
    def _supports_parameters(specs: Any, operation: str, timeframe: str = "") -> bool:
        if not isinstance(specs, list) or not specs:
            return False
        known = set().union(*_PARAM_ALIASES.values())
        normalized = {_key(spec.get("name")) for spec in specs if isinstance(spec, dict)}
        if not any(name in _PARAM_ALIASES["symbol"] for name in normalized):
            return False
        if operation == "kline" and not any(name in _PARAM_ALIASES["timeframe"] for name in normalized):
            return False
        if operation == "kline":
            candidates = _TIMEFRAME_OPTIONS.get(timeframe, (timeframe,))
            for spec in specs:
                if not isinstance(spec, dict):
                    continue
                if _key(spec.get("name")) not in _PARAM_ALIASES["timeframe"]:
                    continue
                options = spec.get("enum") or spec.get("options") or []
                option_values = [str(option.get("value") if isinstance(option, dict) else option) for option in options]
                if option_values and not any(
                    _key(candidate) == _key(option) for candidate in candidates for option in option_values
                ):
                    return False
        for spec in specs:
            if not isinstance(spec, dict) or not spec.get("required"):
                continue
            options = spec.get("enum") or spec.get("options") or []
            if _key(spec.get("name")) not in known and len(options) != 1:
                return False
        return True

    def _build_parameters(self, specs: Any, context: Dict[str, Any]) -> Dict[str, Any]:
        parameters: Dict[str, Any] = {}
        if not isinstance(specs, list):
            raise QverisError("Qveris tool parameter metadata is missing")
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("name") or "").strip()
            normalized = _key(name)
            value = self._parameter_value(normalized, spec, context)
            if value is None:
                if spec.get("required"):
                    raise QverisError(f"Cannot map required Qveris tool parameter: {name}")
                continue
            parameters[name] = value
        return parameters

    def _parameter_value(self, name: str, spec: Dict[str, Any], context: Dict[str, Any]) -> Any:
        if name in _PARAM_ALIASES["symbol"]:
            symbol = str(context.get("symbol") or "").strip()
            param_type = str(spec.get("type") or "").lower()
            description = str(spec.get("description") or "").lower()
            if (
                self.market == "USStock"
                and "." not in symbol
                and (name == "symbolexchange" or "exchange suffix" in description or ".us" in description)
            ):
                symbol = f"{symbol}.US"
            return [symbol] if "array" in param_type or name in {"symbols", "tickers"} else symbol
        if name in _PARAM_ALIASES["market"]:
            return self.market
        if name in _PARAM_ALIASES["timeframe"]:
            return self._timeframe_value(str(context.get("timeframe") or "1D"), spec)
        if name in _PARAM_ALIASES["limit"]:
            return int(context.get("limit") or 300)
        if name in _PARAM_ALIASES["start"]:
            start = context.get("after_time")
            if start is None:
                end = int(context.get("before_time") or time.time())
                start = end - self.calculate_time_range(
                    str(context.get("timeframe") or "1D"),
                    int(context.get("limit") or 300),
                    buffer_ratio=1.5,
                )
            return self._time_parameter(int(start), name, spec)
        if name in _PARAM_ALIASES["end"]:
            end = int(context.get("before_time") or time.time())
            return self._time_parameter(end, name, spec)
        options = spec.get("enum") or spec.get("options") or []
        option_values = [str(option.get("value") if isinstance(option, dict) else option) for option in options]
        if name in {"fmt", "format", "datatype"}:
            for option in option_values:
                if option.lower() == "json":
                    return option
        if len(option_values) == 1:
            return option_values[0]
        return None

    @staticmethod
    def _time_parameter(timestamp: int, name: str, spec: Dict[str, Any]) -> Any:
        description = str(spec.get("description") or "").lower()
        if "date" in name or "yyyy-mm-dd" in description:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
        return timestamp

    @staticmethod
    def _timeframe_value(timeframe: str, spec: Dict[str, Any]) -> str:
        candidates = _TIMEFRAME_OPTIONS.get(timeframe, (timeframe,))
        options = spec.get("enum") or spec.get("options") or []
        option_values = [str(option.get("value") if isinstance(option, dict) else option) for option in options]
        for candidate in candidates:
            for option in option_values:
                if _key(candidate) == _key(option):
                    return option
        return candidates[0]

    def _request_json(self, path: str, *, params: Optional[dict] = None, json: Optional[dict] = None) -> Dict[str, Any]:
        try:
            response = self.session.post(
                f"{self.base_url}{path}",
                params=params or {},
                json=json or {},
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise QverisError(f"Qveris HTTP request failed: {exc}") from exc
        except ValueError as exc:
            raise QverisError("Qveris returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise QverisError("Qveris response must be a JSON object")
        return payload

    def _normalize_klines(self, payload: Any) -> List[Dict[str, Any]]:
        candidates: List[List[Dict[str, Any]]] = []
        self._collect_row_lists(payload, candidates, depth=0)
        for dated_rows in self._dated_row_dicts(payload, depth=0):
            candidates.append(dated_rows)
        normalized = [self._normalize_row(row) for rows in candidates for row in rows]
        valid = [row for row in normalized if row is not None]
        deduped = {row["time"]: row for row in valid}
        return [deduped[timestamp] for timestamp in sorted(deduped)]

    def _collect_row_lists(self, value: Any, output: List[List[Dict[str, Any]]], *, depth: int) -> None:
        if depth > 7:
            return
        if isinstance(value, list):
            dict_rows = [row for row in value if isinstance(row, dict)]
            if dict_rows:
                output.append(dict_rows)
            for item in value:
                self._collect_row_lists(item, output, depth=depth + 1)
        elif isinstance(value, dict):
            column_rows = self._column_rows(value)
            if column_rows:
                output.append(column_rows)
            for item in value.values():
                self._collect_row_lists(item, output, depth=depth + 1)

    def _dated_row_dicts(self, value: Any, *, depth: int) -> List[List[Dict[str, Any]]]:
        if depth > 7:
            return []
        output: List[List[Dict[str, Any]]] = []
        if isinstance(value, dict):
            rows: List[Dict[str, Any]] = []
            for key, item in value.items():
                if isinstance(item, dict) and _timestamp(key) is not None:
                    rows.append({"time": key, **item})
            if rows:
                output.append(rows)
            for item in value.values():
                output.extend(self._dated_row_dicts(item, depth=depth + 1))
        elif isinstance(value, list):
            for item in value:
                output.extend(self._dated_row_dicts(item, depth=depth + 1))
        return output

    @staticmethod
    def _column_rows(value: Dict[str, Any]) -> List[Dict[str, Any]]:
        normalized = {_key(name): item for name, item in value.items()}

        def column(field: str) -> Optional[List[Any]]:
            for alias in _ROW_ALIASES[field]:
                item = normalized.get(alias)
                if isinstance(item, list):
                    return item
            return None

        times = column("time")
        opens = column("open")
        highs = column("high")
        lows = column("low")
        closes = column("close")
        volumes = column("volume") or []
        if not all((times, opens, highs, lows, closes)):
            return []
        length = min(len(times), len(opens), len(highs), len(lows), len(closes))
        return [
            {
                "time": times[index],
                "open": opens[index],
                "high": highs[index],
                "low": lows[index],
                "close": closes[index],
                "volume": volumes[index] if index < len(volumes) else 0,
            }
            for index in range(length)
        ]

    def _normalize_row(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        timestamp = _timestamp(_field(row, _ROW_ALIASES["time"]))
        open_price = _float(_field(row, _ROW_ALIASES["open"]))
        high = _float(_field(row, _ROW_ALIASES["high"]))
        low = _float(_field(row, _ROW_ALIASES["low"]))
        close = _float(_field(row, _ROW_ALIASES["close"]))
        volume = _float(_field(row, _ROW_ALIASES["volume"])) or 0.0
        if timestamp is None or None in (open_price, high, low, close):
            return None
        return self.format_kline(timestamp, open_price, high, low, close, volume)

    def _normalize_ticker(self, payload: Any, *, depth: int = 0) -> Optional[Dict[str, Any]]:
        if depth > 7:
            return None
        if isinstance(payload, dict):
            last = _float(_field(payload, _QUOTE_ALIASES["last"]))
            if last is not None and last > 0:
                result: Dict[str, Any] = {"last": last}
                for output_name, aliases in _QUOTE_ALIASES.items():
                    if output_name == "last":
                        continue
                    value = _float(_field(payload, aliases))
                    result[output_name] = value if value is not None else 0
                return result
            for item in payload.values():
                quote = self._normalize_ticker(item, depth=depth + 1)
                if quote:
                    return quote
        elif isinstance(payload, list):
            for item in payload:
                quote = self._normalize_ticker(item, depth=depth + 1)
                if quote:
                    return quote
        return None
