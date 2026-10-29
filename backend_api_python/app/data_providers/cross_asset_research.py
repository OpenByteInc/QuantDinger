"""Best-effort research enrichment for FX and traditional futures."""

from __future__ import annotations

import csv
import io
import math
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import datetime, timezone
from calendar import monthrange
from typing import Any, Callable, Mapping

import requests

from app.data_providers import get_cached, set_cached
from app.utils.logger import get_logger


logger = get_logger(__name__)

_CACHE_TTL_SECONDS = 10_800
_CFTC_DISAGGREGATED_URL = "https://www.cftc.gov/dea/newcot/f_disagg.txt"
_CFTC_FINANCIAL_URL = "https://www.cftc.gov/dea/newcot/FinFutWk.txt"
_CFTC_MARKET_TOKENS = {
    "GC": ("GOLD",),
    "SI": ("SILVER",),
    "CL": ("CRUDE OIL, LIGHT SWEET", "WTI"),
    "NG": ("NATURAL GAS",),
    "HG": ("COPPER",),
    "ZC": ("CORN",),
    "ZW": ("WHEAT",),
    "ZS": ("SOYBEANS",),
    "ES": ("E-MINI S&P 500",),
    "NQ": ("NASDAQ-100",),
    "YM": ("DOW JONES",),
    "RTY": ("RUSSELL 2000",),
}
_FINANCIAL_ROOTS = {"ES", "NQ", "YM", "RTY"}
_YAHOO_FUTURES_SUFFIX = {
    "GC": "CMX", "SI": "CMX", "HG": "CMX",
    "CL": "NYM", "NG": "NYM",
    "ES": "CME", "NQ": "CME", "YM": "CBT", "RTY": "CME",
    "ZC": "CBT", "ZW": "CBT", "ZS": "CBT",
}
_FUTURES_MONTH_CODES = "FGHJKMNQUVXZ"
_QUARTERLY_ROOTS = {"ES", "NQ", "YM", "RTY"}
_FRED_POLICY_SERIES = {
    "USD": "FEDFUNDS",
    "EUR": "ECBDFR",
    "GBP": "IUDERB3",
    "JPY": "IRSTCI01JPM156N",
    "CHF": "IRSTCI01CHM156N",
    "CAD": "IRSTCI01CAM156N",
    "AUD": "IRSTCI01AUM156N",
    "NZD": "IRSTCI01NZM156N",
    "CNY": "INTDSRCNM193N",
    "CNH": "INTDSRCNM193N",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any) -> float | None:
    try:
        result = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _root_symbol(symbol: str) -> str:
    return str(symbol or "").upper().split("=", 1)[0].split("/", 1)[0].strip()


def _find_column(row: Mapping[str, Any], *tokens: str) -> Any:
    for key, value in row.items():
        normalized = str(key).lower().replace(" ", "_")
        if all(token.lower() in normalized for token in tokens):
            return value
    return None


def fetch_cftc_positioning(
    symbol: str,
    *,
    http_get: Callable[..., Any] = requests.get,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """Read the current official CFTC weekly report for a mapped futures root."""
    root = _root_symbol(symbol)
    tokens = _CFTC_MARKET_TOKENS.get(root)
    if not tokens:
        return {}
    url = _CFTC_FINANCIAL_URL if root in _FINANCIAL_ROOTS else _CFTC_DISAGGREGATED_URL
    response = http_get(url, timeout=timeout, headers={"User-Agent": "QuantDinger/5.0 research"})
    response.raise_for_status()
    rows = list(csv.reader(io.StringIO(response.text.lstrip("\ufeff"))))
    has_header = bool(rows and rows[0] and "market_and_exchange" in rows[0][0].lower())
    header = rows[0] if has_header else []
    for values in rows[1:] if has_header else rows:
        if not values:
            continue
        row = dict(zip(header, values)) if has_header else {}
        market_name = str(row.get("Market_and_Exchange_Names") or values[0]).upper()
        if not any(token in market_name for token in tokens):
            continue
        if root in _FINANCIAL_ROOTS:
            long_value = _number(_find_column(row, "asset", "mgr", "long") if row else values[11] if len(values) > 12 else None)
            short_value = _number(_find_column(row, "asset", "mgr", "short") if row else values[12] if len(values) > 12 else None)
            participant = "asset_manager"
        else:
            long_value = _number(_find_column(row, "money", "long") if row else values[13] if len(values) > 14 else None)
            short_value = _number(_find_column(row, "money", "short") if row else values[14] if len(values) > 14 else None)
            participant = "money_manager"
        report_date = (
            row.get("Report_Date_as_YYYY-MM-DD")
            or row.get("As_of_Date_In_Form_YYMMDD")
            or (values[2] if len(values) > 2 else None)
        )
        return {
            "market_name": market_name,
            "participant_group": participant,
            "long_contracts": long_value,
            "short_contracts": short_value,
            "net_contracts": long_value - short_value if long_value is not None and short_value is not None else None,
            "scope": "weekly_cftc_commitments_of_traders",
            "source": "cftc",
            "source_url": url,
            "as_of": report_date or _utc_now(),
        }
    return {}


def fetch_futures_curve(symbol: str, *, yf_client: Any = None) -> dict[str, Any]:
    """Collect nearby expiry prices, falling back to continuous-contract context."""
    if yf_client is None:
        import yfinance as yf_client
    root = _root_symbol(symbol)
    continuous = f"{root}=F"
    suffix = _YAHOO_FUTURES_SUFFIX.get(root)
    curve_points: list[dict[str, Any]] = []
    if suffix:
        now = datetime.now(timezone.utc)
        candidates: list[tuple[str, str]] = []
        for offset in range(14):
            month_index = now.month - 1 + offset
            year = now.year + month_index // 12
            month = month_index % 12 + 1
            if root in _QUARTERLY_ROOTS and month not in {3, 6, 9, 12}:
                continue
            code = _FUTURES_MONTH_CODES[month - 1]
            ticker_symbol = f"{root}{code}{str(year)[-2:]}.{suffix}"
            expiry_label = f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"
            candidates.append((ticker_symbol, expiry_label))
            if len(candidates) >= 6:
                break

        def latest_contract(candidate: tuple[str, str]) -> dict[str, Any] | None:
            ticker_symbol, expiry_label = candidate
            try:
                frame = yf_client.Ticker(ticker_symbol).history(period="5d", interval="1d", auto_adjust=False)
                if frame is None or getattr(frame, "empty", True):
                    return None
                closes = frame["Close"].dropna().tolist()
                if not closes:
                    return None
                return {
                    "contract": ticker_symbol,
                    "expiry_month_end": expiry_label,
                    "price": float(closes[-1]),
                    "as_of": str(frame.index[-1]),
                }
            except Exception as exc:
                logger.debug("Yahoo futures contract unavailable for %s: %s", ticker_symbol, exc)
                return None

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="futures-curve") as executor:
            for point in executor.map(latest_contract, candidates):
                if point:
                    curve_points.append(point)
        curve_points.sort(key=lambda item: item["expiry_month_end"])
    if len(curve_points) >= 2:
        front = curve_points[0]["price"]
        next_price = curve_points[1]["price"]
        spread_pct = (next_price / front - 1) * 100 if front else None
        return {
            "root_symbol": root,
            "contracts": curve_points,
            "front_to_next_spread_pct": spread_pct,
            "curve_state": "contango" if spread_pct and spread_pct > 0 else "backwardation" if spread_pct and spread_pct < 0 else "flat",
            "scope": "nearby_expiry_settlement_proxy",
            "source": "yahoo_finance",
            "source_url": f"https://finance.yahoo.com/quote/{continuous}",
            "as_of": curve_points[0]["as_of"],
        }
    ticker = yf_client.Ticker(continuous)
    history = ticker.history(period="6mo", interval="1d", auto_adjust=False)
    if history is None or getattr(history, "empty", True):
        return {}
    closes = [float(item) for item in history["Close"].dropna().tolist() if float(item) > 0]
    if not closes:
        return {}
    latest = closes[-1]
    moving_average = sum(closes[-60:]) / min(60, len(closes))
    return {
        "continuous_symbol": continuous,
        "nearby_price": latest,
        "moving_average_60d": moving_average,
        "premium_to_60d_average_pct": (latest / moving_average - 1) * 100 if moving_average else None,
        "scope": "continuous_front_contract_history_not_expiry_term_structure",
        "source": "yahoo_finance",
        "source_url": f"https://finance.yahoo.com/quote/{continuous}",
        "as_of": str(history.index[-1]),
    }


def _pair(symbol: str) -> tuple[str, str] | None:
    normalized = str(symbol or "").upper().replace("/", "").replace("-", "").replace("=X", "")
    if len(normalized) != 6:
        return None
    return normalized[:3], normalized[3:]


def _latest_fred_value(series_id: str, provider: Any) -> dict[str, Any]:
    payload = provider.fetch_fred_series(series_id, limit=12)
    for item in payload.get("observations") or []:
        value = _number(item.get("value"))
        if value is not None:
            return {"value_pct": value, "as_of": item.get("date"), "series_id": series_id}
    return {}


def fetch_forex_drivers(symbol: str, *, macro_provider: Any = None) -> dict[str, Any]:
    """Build base/quote policy-rate context when FRED is configured."""
    currencies = _pair(symbol)
    if not currencies:
        return {}
    base, quote = currencies
    result: dict[str, Any] = {
        "base_currency": base,
        "quote_currency": quote,
        "scope": "base_quote_relative_value",
        "source": "quantdinger",
        "as_of": _utc_now(),
    }
    if macro_provider is None:
        from app.data_providers.macro_series import get_macro_series_provider
        macro_provider = get_macro_series_provider()
    rates: dict[str, Any] = {}
    for currency in (base, quote):
        series_id = _FRED_POLICY_SERIES.get(currency)
        if not series_id:
            continue
        try:
            rate = _latest_fred_value(series_id, macro_provider)
        except Exception as exc:
            logger.debug("Policy-rate series unavailable for %s: %s", currency, exc)
            continue
        if rate:
            rates[currency] = rate
    result["policy_rates"] = rates
    if base in rates and quote in rates:
        result["policy_rate_differential_pct"] = rates[base]["value_pct"] - rates[quote]["value_pct"]
        result["source"] = "fred"
        result["source_url"] = "https://fred.stlouisfed.org/"
    return result


def collect_cross_asset_research(
    market: str,
    symbol: str,
    *,
    timeout: float = 12.0,
    http_get: Callable[..., Any] = requests.get,
    yf_client: Any = None,
    macro_provider: Any = None,
) -> dict[str, Any]:
    """Collect bounded research enrichments and expose every missing source."""
    cache_key = f"cross_asset_research:{market}:{symbol}:v1"
    cached = get_cached(cache_key, _CACHE_TTL_SECONDS)
    if isinstance(cached, dict):
        return cached
    if market == "Forex":
        jobs = {"forex_drivers": lambda: fetch_forex_drivers(symbol, macro_provider=macro_provider)}
    elif market == "Futures":
        jobs = {
            "futures_curve": lambda: fetch_futures_curve(symbol, yf_client=yf_client),
            "futures_positioning": lambda: fetch_cftc_positioning(symbol, http_get=http_get, timeout=min(8.0, timeout)),
        }
    else:
        return {}
    executor = ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="cross-asset-research")
    futures = {executor.submit(job): key for key, job in jobs.items()}
    result: dict[str, Any] = {}
    errors: dict[str, str] = {}
    try:
        for future in as_completed(futures, timeout=timeout):
            key = futures[future]
            try:
                value = future.result() or {}
                if value:
                    result[key] = value
            except Exception as exc:
                errors[key] = f"{type(exc).__name__}: {exc}"
    except TimeoutError:
        for future, key in futures.items():
            if not future.done():
                errors[key] = "timeout"
                future.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    available = sorted(result)
    result["_provider_status"] = {
        "attempted": sorted(jobs),
        "available": available,
        "unavailable": sorted(set(jobs) - set(available)),
        "errors": errors,
        "collected_at": _utc_now(),
    }
    if available:
        set_cached(cache_key, result, _CACHE_TTL_SECONDS)
    return result


__all__ = [
    "collect_cross_asset_research",
    "fetch_cftc_positioning",
    "fetch_forex_drivers",
    "fetch_futures_curve",
]
