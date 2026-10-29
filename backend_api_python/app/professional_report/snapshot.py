"""Convert legacy collector payloads into an immutable evidence snapshot."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


_MARKET_DEFAULTS = {
    "USStock": {"currency": "USD", "timezone": "America/New_York", "asset_type": "equity"},
    "HKStock": {"currency": "HKD", "timezone": "Asia/Hong_Kong", "asset_type": "equity"},
    "Crypto": {"currency": None, "timezone": "UTC", "asset_type": "crypto"},
    "Forex": {"currency": None, "timezone": "UTC", "asset_type": "forex"},
    "Futures": {"currency": "USD", "timezone": "America/New_York", "asset_type": "futures"},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_timestamp(value: Any, fallback: str | None = None) -> str:
    """Normalize provider timestamps to timezone-aware ISO-8601 UTC."""
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            pass
    text = str(value or "").strip()
    if text:
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            return _iso_timestamp(float(text), fallback)
        for pattern in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S", "%Y%m%d"):
            try:
                parsed = datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
                return parsed.isoformat().replace("+00:00", "Z")
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return fallback or _now_iso()


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _structured_evidence_value(value: Any) -> Any:
    """Remove provenance fields already represented by the observation itself."""
    metadata_keys = {"source", "provider", "url", "source_url", "as_of"}
    if isinstance(value, Mapping):
        return {
            str(key): _structured_evidence_value(child)
            for key, child in value.items()
            if key not in metadata_keys and child not in (None, "")
        }
    if isinstance(value, (list, tuple)):
        return [_structured_evidence_value(child) for child in value]
    return _jsonable(value)


def _evidence_id(market: str, symbol: str, metric: str, source: str, as_of: str, value: Any) -> str:
    raw = json.dumps(
        [market, symbol, metric, source, as_of, _jsonable(value)],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "ev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _quote_currency(market: str, symbol: str) -> str:
    default = _MARKET_DEFAULTS.get(market, {}).get("currency")
    if default:
        return str(default)
    raw = str(symbol or "").upper().split(":", 1)[0]
    if "/" in raw:
        return raw.split("/", 1)[1].split("@", 1)[0]
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if raw.endswith(quote):
            return quote
    return "USD"


def _instrument(payload: Mapping[str, Any]) -> dict[str, Any]:
    market = str(payload.get("market") or "")
    symbol = str(payload.get("symbol") or "")
    fundamental = payload.get("fundamental") or {}
    identity = fundamental.get("identity") or {}
    company = payload.get("company") or {}
    defaults = _MARKET_DEFAULTS.get(market, {})
    market_type = "spot"
    venue = identity.get("exchange") or company.get("exchange")
    if market == "Crypto":
        low = symbol.lower()
        if "@swap" in low or ":swap" in low or "perp" in low:
            market_type = "perpetual"
        crypto_meta = payload.get("crypto_instrument") or {}
        venue = crypto_meta.get("venue") or payload.get("exchange_id") or venue
        market_type = crypto_meta.get("market_type") or market_type
    base_currency = None
    if market == "Forex":
        normalized = symbol.upper().replace("/", "").replace("-", "").replace("=X", "")
        if len(normalized) == 6:
            base_currency = normalized[:3]
    product_type = {
        "Crypto": market_type,
        "Forex": "spot_fx",
        "Futures": "futures_contract",
    }.get(market, "equity")
    return {
        "market": market,
        "symbol": symbol,
        "canonical_symbol": identity.get("reported_symbol") or symbol,
        "name": company.get("name") or identity.get("company_name") or symbol,
        "asset_type": defaults.get("asset_type") or market.lower(),
        "exchange": venue,
        "venue": venue,
        "product_type": product_type,
        "base_currency": base_currency,
        "quote_currency": _quote_currency(market, symbol),
        "timezone": defaults.get("timezone") or "UTC",
        "identity_verified": identity.get("verified"),
    }


class _ObservationCollector:
    def __init__(self, payload: Mapping[str, Any]):
        self.payload = payload
        self.market = str(payload.get("market") or "")
        self.symbol = str(payload.get("symbol") or "")
        self.retrieved_at = _iso_timestamp(payload.get("collected_at"))
        self.currency = _quote_currency(self.market, self.symbol)
        self.items: list[dict[str, Any]] = []
        self.quality_flags: set[str] = set()
        self.excluded_future_timestamp_items = 0

    def add(
        self,
        metric: str,
        value: Any,
        *,
        category: str,
        source: str | None = None,
        as_of: str | None = None,
        unit: str | None = None,
        currency: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        source_url: str | None = None,
        required: bool = False,
        quality_flags: Iterable[str] = (),
        freshness_limit_seconds: int | None = None,
    ) -> None:
        if value is None or value == "":
            return
        source_name = str(source or "unknown")
        observed_at = _iso_timestamp(as_of or period_end, self.retrieved_at)
        observed_datetime = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        retrieved_datetime = datetime.fromisoformat(
            self.retrieved_at.replace("Z", "+00:00")
        )
        if observed_datetime > retrieved_datetime:
            # Provider clocks and timezone bugs must not invalidate the whole
            # report.  Exclude future-dated evidence from prompts, claims and
            # scoring, while exposing the data-quality problem on the snapshot.
            self.excluded_future_timestamp_items += 1
            self.quality_flags.add("future_timestamp_evidence_excluded")
            return
        freshness_default = {
            "market": 172_800,
            "technical": 172_800,
            "fundamental": 15_552_000,
            "macro": 604_800,
            "news": 1_209_600,
            "crypto": 172_800,
            "positioning": 864_000,
            "quality": 172_800,
        }.get(category, 86_400)
        clean_value = _jsonable(value)
        self.items.append({
            "evidence_id": _evidence_id(
                self.market, self.symbol, metric, source_name, observed_at, clean_value
            ),
            "category": category,
            "metric": metric,
            "value": clean_value,
            "unit": unit,
            "currency": currency,
            "source": source_name,
            "source_url": source_url,
            "as_of": observed_at,
            "retrieved_at": self.retrieved_at,
            "period_start": period_start,
            "period_end": period_end,
            "required": required,
            "quality_flags": list(quality_flags),
            "freshness_limit_seconds": int(freshness_limit_seconds or freshness_default),
        })


def _flatten_numeric(prefix: str, value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"raw", "financial_statements", "field_metadata", "data_quality", "identity"}:
                continue
            yield from _flatten_numeric(f"{prefix}.{key}" if prefix else str(key), child)
    elif isinstance(value, (int, float, str, bool)) and value not in (None, ""):
        yield prefix, value


def build_evidence_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Create the V1 evidence snapshot without mutating collector data."""
    collector = _ObservationCollector(payload)
    price = payload.get("price") or {}
    price_source = price.get("source") or "unknown"
    for key, unit in (
        ("price", "price"),
        ("open", "price"),
        ("high", "price"),
        ("low", "price"),
        ("previousClose", "price"),
        ("change", "price"),
        ("changePercent", "percent"),
    ):
        collector.add(
            f"quote.{key}",
            price.get(key),
            category="market",
            source=price_source,
            unit=unit,
            currency=collector.currency if unit == "price" else None,
            required=key == "price",
        )

    klines = payload.get("kline") or []
    if klines:
        last = klines[-1] or {}
        collector.add(
            "ohlcv.latest_bar",
            {k: last.get(k) for k in ("timestamp", "time", "open", "high", "low", "close", "volume")},
            category="market",
            source=last.get("source") or price_source,
            as_of=last.get("timestamp") or last.get("time"),
            unit="ohlcv",
            currency=collector.currency,
            required=True,
        )
        collector.add(
            "ohlcv.bar_count",
            len(klines),
            category="quality",
            source=last.get("source") or price_source,
            unit="count",
        )

    indicators = payload.get("indicators") or {}
    for metric, value in _flatten_numeric("indicator", indicators):
        unit = "percent" if any(token in metric.lower() for token in ("pct", "rsi", "position", "width")) else None
        collector.add(metric, value, category="technical", source="quantdinger", unit=unit)

    fundamental = payload.get("fundamental") or {}
    field_metadata = fundamental.get("field_metadata") or {}
    skip = {"source", "field_metadata", "financial_statements", "earnings", "data_quality", "identity"}
    for key, value in fundamental.items():
        if key in skip or isinstance(value, (dict, list, tuple)):
            continue
        metadata = field_metadata.get(key) or {}
        collector.add(
            key,
            value,
            category="fundamental",
            source=metadata.get("source") or fundamental.get("source"),
            as_of=metadata.get("as_of") or metadata.get("period_end"),
            period_start=metadata.get("period_start"),
            period_end=metadata.get("period_end"),
            unit=metadata.get("unit"),
            currency=metadata.get("currency"),
            required=key in {"market_cap", "revenue_growth", "profit_margin"},
        )

    # Preserve period separation in the evidence layer.  Quarterly, TTM and
    # annual figures receive distinct metric names and dates so a model cannot
    # silently present values from different reporting bases as one period.
    statement_currency = (
        fundamental.get("financial_currency")
        or (fundamental.get("identity") or {}).get("financial_currency")
        or collector.currency
    )
    statements = fundamental.get("financial_statements") or {}
    for period_key in ("latest_quarter", "ttm", "latest_annual"):
        period = statements.get(period_key) or {}
        period_end = period.get("period_end") or period.get("as_of")
        for section in ("income_statement", "balance_sheet", "cash_flow", "derived"):
            values = period.get(section) or {}
            if not isinstance(values, Mapping):
                continue
            for key, value in values.items():
                if isinstance(value, (Mapping, list, tuple)) or value in (None, ""):
                    continue
                is_ratio = any(token in str(key).lower() for token in (
                    "margin", "growth", "roe", "roa", "ratio", "yield"
                ))
                collector.add(
                    f"financial.{period_key}.{section}.{key}",
                    value,
                    category="fundamental",
                    source=fundamental.get("source"),
                    as_of=period_end,
                    period_end=period_end,
                    unit="percent" if is_ratio else "currency",
                    currency=None if is_ratio else statement_currency,
                )

    earnings = fundamental.get("earnings") or {}
    history = earnings.get("history") if isinstance(earnings, Mapping) else None
    for index, item in enumerate((history or [])[:8]):
        if not isinstance(item, Mapping):
            continue
        collector.add(
            f"earnings.history.{index}",
            {
                key: item.get(key)
                for key in ("eps_actual", "eps_estimate", "surprise", "revenue_actual", "revenue_estimate")
                if item.get(key) is not None
            },
            category="fundamental",
            source=item.get("source") or fundamental.get("source"),
            as_of=item.get("date") or item.get("period_end"),
            period_end=item.get("date") or item.get("period_end"),
            unit="mixed_earnings",
            currency=statement_currency,
        )

    equity_datasets = {
        "sec_filings": ("filings.sec", "sec_edgar"),
        "analyst_expectations": ("expectations.analyst", "analyst_provider"),
        "options": ("options.snapshot", "options_provider"),
        "short_interest": ("short_interest.snapshot", "short_data_provider"),
        "short_volume": ("short_volume.snapshot", "finra_public"),
        "insider_activity": ("insider_activity.snapshot", "filing_provider"),
        "hkex_announcements": ("filings.hkex", "hkex_public"),
        "southbound_flow": ("southbound_flow.snapshot", "hkex_public"),
        "short_selling": ("short_selling.snapshot", "hkex_public"),
        "ccass": ("ccass.snapshot", "hkex_public"),
        "ah_premium": ("ah_premium.snapshot", "market_provider"),
        "hk_security_profile": ("security_profile.hk", "eastmoney_hk"),
        "ownership": ("ownership.snapshot", "ownership_provider"),
        "earnings_transcript": ("earnings.transcript", "transcript_provider"),
        "forex_drivers": ("forex.drivers", "macro_provider"),
        "futures_curve": ("futures.curve", "market_provider"),
        "futures_positioning": ("futures.positioning", "cftc"),
    }
    for payload_key, (metric, default_source) in equity_datasets.items():
        value = payload.get(payload_key)
        if not value:
            continue
        first = value[0] if isinstance(value, list) and value else value
        metadata = first if isinstance(first, Mapping) else {}
        evidence_value = _structured_evidence_value(value[:20] if isinstance(value, list) else value)
        collector.add(
            metric,
            evidence_value,
            category=("positioning" if payload_key == "futures_positioning" else "macro" if payload_key == "forex_drivers" else "market" if payload_key == "futures_curve" else "fundamental"),
            source=metadata.get("source") or metadata.get("provider") or default_source,
            source_url=metadata.get("url") or metadata.get("source_url"),
            as_of=metadata.get("as_of") or metadata.get("published_at") or metadata.get("date"),
            unit=metadata.get("unit") or "structured_dataset",
        )

    macro_payload = payload.get("macro") or {}
    hkma_macro = macro_payload.get("HKMA") if isinstance(macro_payload, Mapping) else {}
    for metric, value in _flatten_numeric("macro", macro_payload):
        metric_lower = metric.lower()
        if metric_lower.startswith("macro.fred"):
            source = "fred"
        elif metric_lower.startswith("macro.hkma"):
            source = "hkma_open_api"
        else:
            source = "market_provider"
        collector.add(
            metric,
            value,
            category="macro",
            source=source,
            source_url=(hkma_macro or {}).get("source_url") if source == "hkma_open_api" else None,
            as_of=(hkma_macro or {}).get("as_of") if source == "hkma_open_api" else None,
        )

    crypto_factors = payload.get("crypto_factors") or {}
    crypto_sources = crypto_factors.get("sources") or {}
    crypto_metadata = crypto_factors.get("metric_metadata") or {}
    for key, value in crypto_factors.items():
        if key in {"signals", "summary", "sources", "symbol", "metric_metadata", "funding_rate_decimal"} or isinstance(value, (dict, list, tuple)):
            continue
        metadata = crypto_metadata.get(key) or {}
        source = metadata.get("provider") or (crypto_sources.get("derivatives") if key in {
            "funding_rate", "open_interest", "open_interest_change_24h", "long_short_ratio"
        } else crypto_sources.get("capital_flow") if "netflow" in key else crypto_sources.get("market_structure"))
        unit = metadata.get("unit") or ("percent" if key.endswith("_24h") or key == "funding_rate" else "usd" if key in {
            "volume_24h", "open_interest", "exchange_netflow", "stablecoin_netflow"
        } else "ratio" if key == "long_short_ratio" else None)
        collector.add(
            f"crypto.{key}",
            value,
            category="crypto",
            source=source,
            unit=unit,
            currency=metadata.get("currency") or ("USD" if unit == "usd" else None),
            quality_flags=(
                ["aggregate_venue"] if metadata.get("venue") == "aggregate" else []
            ),
        )

    for idx, item in enumerate((payload.get("news") or [])[:10]):
        if not isinstance(item, Mapping):
            continue
        title = item.get("title") or item.get("headline")
        if not title:
            continue
        collector.add(
            f"news.{idx}",
            {
                "title": title,
                "summary": item.get("summary"),
                "sentiment": item.get("sentiment"),
                "publisher": item.get("source") or item.get("publisher"),
            },
            category="news",
            source=item.get("source") or item.get("publisher") or "news_provider",
            source_url=item.get("url") or item.get("link"),
            as_of=item.get("datetime") or item.get("published_at") or item.get("time"),
        )

    meta = payload.get("_meta") or {}
    required_by_market = {
        "USStock": [
            "quote.price", "ohlcv.latest_bar", "indicator.rsi.value",
            "indicator.moving_averages.trend", "market_cap", "revenue_growth", "profit_margin",
        ],
        "HKStock": [
            "quote.price", "ohlcv.latest_bar", "indicator.rsi.value",
            "indicator.moving_averages.trend", "market_cap", "revenue_growth", "profit_margin",
        ],
        "Crypto": [
            "quote.price", "ohlcv.latest_bar", "indicator.rsi.value",
            "indicator.moving_averages.trend", "crypto.volume_24h",
            "crypto.funding_rate", "crypto.open_interest",
        ],
        "Forex": [
            "quote.price", "ohlcv.latest_bar", "indicator.rsi.value",
            "indicator.moving_averages.trend",
        ],
        "Futures": [
            "quote.price", "ohlcv.latest_bar", "indicator.rsi.value",
            "indicator.moving_averages.trend",
        ],
    }
    return {
        "version": "evidence_snapshot_v1",
        "instrument": _instrument(payload),
        "as_of": collector.retrieved_at,
        "retrieved_at": collector.retrieved_at,
        "timeframe": str(payload.get("timeframe") or "1D"),
        "observations": collector.items,
        "required_metrics": required_by_market.get(collector.market, ["quote.price", "ohlcv.latest_bar"]),
        "quality_flags": sorted(collector.quality_flags),
        "collection": {
            "success_items": list(meta.get("success_items") or []),
            "failed_items": list(meta.get("failed_items") or []),
            "duration_ms": int(meta.get("duration_ms") or 0),
            "excluded_future_timestamp_items": collector.excluded_future_timestamp_items,
        },
    }


__all__ = ["build_evidence_snapshot"]
