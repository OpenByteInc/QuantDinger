"""Free, best-effort US equity research adapters.

The report layer consumes stable, source-labelled dictionaries.  This module
keeps SEC EDGAR and Yahoo response shapes out of the contract and explicitly
labels proxies such as Form 4 filing activity and nearest-expiry option data.
No API key is required.
"""

from __future__ import annotations

import math
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import requests

from app.data_providers import get_cached, set_cached
from app.config import APIKeys
from app.utils.logger import get_logger


logger = get_logger(__name__)

_CACHE_TTL_SECONDS = 10_800
_SEC_TICKERS_TTL_SECONDS = 86_400
_SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_NASDAQ_OWNERSHIP_URL = "https://api.nasdaq.com/api/company/{symbol}/institutional-holdings"
_ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
_FINRA_SHORT_VOLUME_URL = "https://api.finra.org/data/group/otcMarket/name/regShoDaily"
_SEC_USER_AGENT = os.getenv(
    "SEC_USER_AGENT",
    "QuantDinger/5.0 open-source-research support@quantdinger.com",
).strip()
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _symbol(value: str) -> str:
    return str(value or "").strip().upper().replace(".", "-")


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(
            str(value)
            .replace(",", "")
            .replace("%", "")
            .replace("$", "")
            .strip()
        )
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _timestamp(value: Any) -> str | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    # Yahoo timestamps are seconds; tolerate millisecond inputs from fixtures.
    if number > 10_000_000_000:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OSError, OverflowError):
        return None


def _request_json(
    url: str,
    *,
    http_get: Callable[..., Any],
    timeout: float,
) -> Any:
    response = http_get(
        url,
        headers={"User-Agent": _SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _sec_ticker_map(*, http_get: Callable[..., Any], timeout: float) -> dict[str, dict[str, Any]]:
    cache_key = "sec_edgar:ticker_cik:v1"
    cached = get_cached(cache_key, _SEC_TICKERS_TTL_SECONDS)
    if isinstance(cached, dict) and cached:
        return cached
    payload = _request_json(_SEC_TICKERS_URL, http_get=http_get, timeout=timeout) or {}
    rows = payload.values() if isinstance(payload, Mapping) else payload
    result: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        ticker = _symbol(str(row.get("ticker") or ""))
        try:
            cik = int(row.get("cik_str"))
        except (TypeError, ValueError):
            continue
        if ticker:
            result[ticker] = {"cik": cik, "title": str(row.get("title") or "").strip()}
    if result:
        set_cached(cache_key, result, _SEC_TICKERS_TTL_SECONDS)
    return result


def fetch_sec_research(
    symbol: str,
    *,
    http_get: Callable[..., Any] = requests.get,
    timeout: float = 6.0,
) -> dict[str, Any]:
    """Fetch recent issuer filings and Form 4 filing activity from EDGAR."""
    ticker = _symbol(symbol)
    company = _sec_ticker_map(http_get=http_get, timeout=timeout).get(ticker)
    if not company:
        return {}
    cik = int(company["cik"])
    cik_padded = f"{cik:010d}"
    payload = _request_json(
        _SEC_SUBMISSIONS_URL.format(cik=cik_padded),
        http_get=http_get,
        timeout=timeout,
    ) or {}
    recent = ((payload.get("filings") or {}).get("recent") or {})
    forms = list(recent.get("form") or [])
    filings: list[dict[str, Any]] = []
    form4_dates: list[str] = []
    for index, form in enumerate(forms):
        filing_date = _at(recent.get("filingDate"), index)
        accession = str(_at(recent.get("accessionNumber"), index) or "").strip()
        primary_document = str(_at(recent.get("primaryDocument"), index) or "").strip()
        if str(form).upper() in {"4", "4/A"} and filing_date:
            form4_dates.append(str(filing_date))
        if not form or len(filings) >= 12:
            continue
        accession_path = accession.replace("-", "")
        filing_url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_path}/{primary_document}"
            if accession_path and primary_document else
            f"https://www.sec.gov/edgar/browse/?CIK={cik_padded}"
        )
        filings.append({
            "form": str(form),
            "filing_date": filing_date,
            "report_date": _at(recent.get("reportDate"), index),
            "accession_number": accession or None,
            "description": _at(recent.get("primaryDocDescription"), index),
            "source": "sec_edgar",
            "url": filing_url,
            "as_of": filing_date,
        })
    insider = {}
    if form4_dates:
        insider = {
            "recent_form4_filing_count": len(form4_dates[:20]),
            "latest_form4_filing_date": max(form4_dates),
            "scope": "form4_filing_activity_not_trade_direction",
            "source": "sec_edgar",
            "source_url": f"https://www.sec.gov/edgar/browse/?CIK={cik_padded}&owner=include",
            "as_of": max(form4_dates),
        }
    return {
        "sec_filings": filings,
        "insider_activity": insider,
        "identity": {
            "ticker": ticker,
            "cik": cik_padded,
            "company_name": payload.get("name") or company.get("title"),
        },
    }


def _at(values: Any, index: int) -> Any:
    try:
        return values[index]
    except (TypeError, IndexError, KeyError):
        return None


def _frame_total(frame: Any, column: str) -> float | None:
    if frame is None or getattr(frame, "empty", True) or column not in frame.columns:
        return None
    try:
        values = frame[column].fillna(0)
        return float(values.sum())
    except Exception:
        return None


def _nearest_atm_iv(calls: Any, puts: Any, spot: float | None) -> float | None:
    if spot is None:
        return None
    candidates: list[tuple[float, float]] = []
    for frame in (calls, puts):
        if frame is None or getattr(frame, "empty", True):
            continue
        if "strike" not in frame.columns or "impliedVolatility" not in frame.columns:
            continue
        for _, row in frame.iterrows():
            strike = _number(row.get("strike"))
            iv = _number(row.get("impliedVolatility"))
            # Yahoo may expose 0.00001 as a missing/placeholder IV.  Treat
            # implausibly small or malformed values as unavailable rather than
            # presenting them as a real 0.0% volatility observation.
            if strike is not None and iv is not None and 0.001 <= iv <= 10:
                candidates.append((abs(strike - spot), iv))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    nearest = [iv for distance, iv in candidates if distance == candidates[0][0]]
    return sum(nearest) / len(nearest) * 100


def _option_expiry_summary(calls: Any, puts: Any, spot: float | None, expiry: str) -> dict[str, Any]:
    call_volume = _frame_total(calls, "volume")
    put_volume = _frame_total(puts, "volume")
    call_oi = _frame_total(calls, "openInterest")
    put_oi = _frame_total(puts, "openInterest")
    return _compact({
        "expiry": expiry,
        "call_volume": call_volume,
        "put_volume": put_volume,
        "put_call_volume_ratio": put_volume / call_volume if put_volume is not None and call_volume else None,
        "call_open_interest": call_oi,
        "put_open_interest": put_oi,
        "put_call_open_interest_ratio": put_oi / call_oi if put_oi is not None and call_oi else None,
        "nearest_atm_implied_volatility_pct": _nearest_atm_iv(calls, puts, spot),
    }, keep={"expiry"})


def fetch_alpha_vantage_transcript(
    symbol: str,
    *,
    http_get: Callable[..., Any] = requests.get,
    timeout: float = 6.0,
) -> dict[str, Any]:
    """Fetch and compress the latest available earnings transcript."""
    api_key = APIKeys.ALPHA_VANTAGE_API_KEY
    if not api_key:
        return {}
    now = datetime.now(timezone.utc)
    quarter_number = (now.month - 1) // 3 + 1
    candidates = [(now.year, quarter_number - 1), (now.year, quarter_number - 2)]
    normalized_candidates = []
    for year, quarter in candidates:
        while quarter <= 0:
            year -= 1
            quarter += 4
        normalized_candidates.append(f"{year}Q{quarter}")
    for quarter in normalized_candidates:
        response = http_get(
            _ALPHA_VANTAGE_URL,
            params={
                "function": "EARNINGS_CALL_TRANSCRIPT",
                "symbol": _symbol(symbol),
                "quarter": quarter,
                "apikey": api_key,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json() or {}
        turns = payload.get("transcript") or []
        if not isinstance(turns, list) or not turns:
            continue
        normalized: list[dict[str, Any]] = []
        sentiments: list[float] = []
        for item in turns:
            if not isinstance(item, Mapping):
                continue
            sentiment = _number(item.get("sentiment"))
            if sentiment is not None:
                sentiments.append(sentiment)
            content = str(item.get("content") or "").strip()
            normalized.append({
                "speaker": str(item.get("speaker") or "").strip() or None,
                "title": str(item.get("title") or "").strip() or None,
                "sentiment": sentiment,
                "excerpt": content[:280] if content else None,
            })
        notable = sorted(
            normalized,
            key=lambda item: abs(item.get("sentiment") or 0),
            reverse=True,
        )[:8]
        return {
            "quarter": payload.get("quarter") or quarter,
            "fiscal_date": payload.get("fiscal_date") or payload.get("date"),
            "turn_count": len(normalized),
            "average_turn_sentiment": sum(sentiments) / len(sentiments) if sentiments else None,
            "notable_segments": notable,
            "scope": "provider_transcript_sentiment_and_bounded_excerpts",
            "source": "alpha_vantage",
            "source_url": "https://www.alphavantage.co/documentation/#earnings-call-transcript",
            "as_of": payload.get("fiscal_date") or payload.get("date") or _utc_now(),
        }
    return {}


def fetch_finra_short_volume(
    symbol: str,
    *,
    http_post: Callable[..., Any] = requests.post,
    timeout: float = 6.0,
) -> dict[str, Any]:
    """Fetch and aggregate the latest official FINRA short-volume rows."""
    ticker = _symbol(symbol)
    response = http_post(
        _FINRA_SHORT_VOLUME_URL,
        json={
            "limit": 5000,
            "fields": [
                "tradeReportDate", "securitiesInformationProcessorSymbolIdentifier",
                "shortParQuantity", "shortExemptParQuantity", "totalParQuantity",
                "reportingFacilityCode", "marketCode",
            ],
            "compareFilters": [{
                "compareType": "equal",
                "fieldName": "securitiesInformationProcessorSymbolIdentifier",
                "fieldValue": ticker,
            }],
        },
        headers={"User-Agent": _BROWSER_USER_AGENT, "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    rows = response.json() or []
    if not isinstance(rows, list) or not rows:
        return {}
    dates = [str(row.get("tradeReportDate") or "") for row in rows if isinstance(row, Mapping)]
    latest_date = max((value for value in dates if value), default="")
    latest_rows = [row for row in rows if str(row.get("tradeReportDate") or "") == latest_date]
    short_volume = sum(_number(row.get("shortParQuantity")) or 0 for row in latest_rows)
    exempt_volume = sum(_number(row.get("shortExemptParQuantity")) or 0 for row in latest_rows)
    total_volume = sum(_number(row.get("totalParQuantity")) or 0 for row in latest_rows)
    if not latest_rows or total_volume <= 0:
        return {}
    return {
        "short_volume": short_volume,
        "short_exempt_volume": exempt_volume,
        "total_reported_volume": total_volume,
        "short_volume_ratio_pct": short_volume / total_volume * 100,
        "reporting_facility_count": len({row.get("reportingFacilityCode") for row in latest_rows if row.get("reportingFacilityCode")}),
        "scope": "finra_consolidated_short_volume_not_short_interest_or_directional_shorting",
        "source": "finra_public",
        "source_url": _FINRA_SHORT_VOLUME_URL,
        "as_of": latest_date,
    }


def _row_value(row: Any, *keys: str) -> Any:
    for key in keys:
        try:
            value = row.get(key)
        except AttributeError:
            value = None
        if value is not None and value != "":
            return value
    return None


def _reported_percent(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if 0 <= number <= 1:
        number *= 100
    if not 0 <= number <= 100:
        return None
    return round(number, 6)


def _iso_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _configure_yfinance_cache(yf_client: Any) -> None:
    cache_dir = os.getenv("YFINANCE_CACHE_DIR") or os.path.join(
        tempfile.gettempdir(), "quantdinger-yfinance"
    )
    try:
        os.makedirs(cache_dir, mode=0o700, exist_ok=True)
        os.environ.setdefault("XDG_CACHE_HOME", cache_dir)
        set_location = getattr(yf_client, "set_tz_cache_location", None)
        if callable(set_location):
            set_location(cache_dir)
        try:
            from yfinance import cache as yf_cache

            set_cache_location = getattr(yf_cache, "set_cache_location", None)
            if callable(set_cache_location):
                set_cache_location(cache_dir)
        except Exception:
            pass
    except Exception as exc:
        logger.debug("Unable to configure yfinance cache at %s: %s", cache_dir, exc)


def _normalize_nasdaq_ownership(payload: Any, ticker_symbol: str) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        return {}
    transactions = data.get("holdingsTransactions")
    table = transactions.get("table") if isinstance(transactions, Mapping) else None
    rows = table.get("rows") if isinstance(table, Mapping) else None
    if not isinstance(rows, list):
        return {}

    summary = data.get("ownershipSummary")
    summary = summary if isinstance(summary, Mapping) else {}
    outstanding_entry = summary.get("ShareoutstandingTotal")
    outstanding_entry = outstanding_entry if isinstance(outstanding_entry, Mapping) else {}
    shares_outstanding = _number(outstanding_entry.get("value"))
    outstanding_label = str(outstanding_entry.get("label") or "").lower()
    if shares_outstanding is not None and "million" in outstanding_label:
        shares_outstanding *= 1_000_000

    holders: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("ownerName") or "").strip()
        shares = _number(row.get("sharesHeld"))
        if not name or shares is None or shares <= 0:
            continue
        pct_held = (
            round(shares / shares_outstanding * 100, 6)
            if shares_outstanding and shares_outstanding > 0
            else None
        )
        market_value = _number(row.get("marketValue"))
        holders.append(_compact({
            "name": name,
            "shares": shares,
            "value_usd": market_value * 1000 if market_value is not None else None,
            "pct_held": pct_held,
            "report_date": _iso_date(row.get("date")),
        }, keep={"name"}))
    if not holders:
        return {}
    holders.sort(key=lambda item: item.get("shares") or 0, reverse=True)
    top_holders = holders[:10]
    reported_pct = sum(item.get("pct_held") or 0 for item in top_holders)
    report_dates = [item.get("report_date") for item in top_holders if item.get("report_date")]
    institutional_entry = summary.get("SharesOutstandingPCT")
    institutional_entry = institutional_entry if isinstance(institutional_entry, Mapping) else {}
    return {
        "top_institutional_holders": top_holders,
        "top_holders_reported_pct": round(reported_pct, 6) if reported_pct else None,
        "other_shareholders_pct": round(max(0.0, 100 - reported_pct), 6) if reported_pct else None,
        "institutional_ownership_pct": _reported_percent(institutional_entry.get("value")),
        "shares_outstanding": shares_outstanding,
        "scope": "latest_available_reported_institutional_holders_not_realtime_ownership",
        "source": "nasdaq",
        "source_url": f"https://www.nasdaq.com/market-activity/stocks/{ticker_symbol.lower()}/institutional-holdings",
        "as_of": max(report_dates) if report_dates else _utc_now(),
    }


def fetch_nasdaq_us_ownership(
    symbol: str,
    *,
    http_get: Callable[..., Any] = requests.get,
    http_post: Callable[..., Any] = requests.post,
    timeout: float = 12.0,
) -> dict[str, Any]:
    ticker_symbol = _symbol(symbol)
    response = http_get(
        _NASDAQ_OWNERSHIP_URL.format(symbol=ticker_symbol),
        params={
            "limit": 10,
            "type": "TOTAL",
            "sortColumn": "marketValue",
            "sortOrder": "DESC",
        },
        headers={
            "User-Agent": _BROWSER_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.nasdaq.com",
            "Referer": f"https://www.nasdaq.com/market-activity/stocks/{ticker_symbol.lower()}/institutional-holdings",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    ownership = _normalize_nasdaq_ownership(response.json(), ticker_symbol)
    return {"ownership": ownership} if ownership else {}


def _normalize_institutional_holders(frame: Any, ticker_symbol: str) -> dict[str, Any]:
    if frame is None or getattr(frame, "empty", True):
        return {}
    holders: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        name = str(_row_value(row, "Holder", "holder", "Organization") or "").strip()
        if not name:
            continue
        report_date = _row_value(row, "Date Reported", "dateReported", "Report Date")
        if hasattr(report_date, "isoformat"):
            report_date = report_date.isoformat()
        holder = _compact({
            "name": name,
            "shares": _number(_row_value(row, "Shares", "shares")),
            "value_usd": _number(_row_value(row, "Value", "value")),
            "pct_held": _reported_percent(_row_value(row, "pctHeld", "% Out", "Percent Out")),
            "report_date": str(report_date) if report_date is not None else None,
        }, keep={"name"})
        holders.append(holder)
    if not holders:
        return {}
    holders.sort(
        key=lambda item: (
            item.get("pct_held") is not None,
            item.get("pct_held") or 0,
            item.get("shares") or 0,
        ),
        reverse=True,
    )
    top_holders = holders[:10]
    reported_pct = sum(item.get("pct_held") or 0 for item in top_holders)
    report_dates = [item.get("report_date") for item in top_holders if item.get("report_date")]
    return {
        "top_institutional_holders": top_holders,
        "top_holders_reported_pct": round(reported_pct, 6) if reported_pct else None,
        "other_shareholders_pct": round(max(0.0, 100 - reported_pct), 6) if reported_pct else None,
        "scope": "latest_available_reported_institutional_holders_not_realtime_ownership",
        "source": "yahoo_finance",
        "source_url": f"https://finance.yahoo.com/quote/{ticker_symbol}/holders",
        "as_of": max(report_dates) if report_dates else _utc_now(),
    }


def _ticker_ownership(ticker: Any, ticker_symbol: str) -> dict[str, Any]:
    try:
        institutional_holders = getattr(ticker, "institutional_holders", None)
    except Exception as exc:
        logger.info("Yahoo institutional holders unavailable for %s: %s", ticker_symbol, exc)
        return {}
    return _normalize_institutional_holders(institutional_holders, ticker_symbol)


def fetch_yahoo_us_ownership(symbol: str, *, yf_client: Any = None) -> dict[str, Any]:
    """Fetch reported institutional holders without loading unrelated research endpoints."""
    if yf_client is None:
        import yfinance as yf_client
        _configure_yfinance_cache(yf_client)
    ticker_symbol = _symbol(symbol)
    institutional_holders = getattr(yf_client.Ticker(ticker_symbol), "institutional_holders", None)
    ownership = _normalize_institutional_holders(institutional_holders, ticker_symbol)
    return {"ownership": ownership} if ownership else {}


def collect_us_ownership(
    symbol: str,
    *,
    http_get: Callable[..., Any] = requests.get,
    yf_client: Any = None,
    timeout: float = 12.0,
) -> dict[str, Any]:
    ticker = _symbol(symbol)
    cache_key = f"us_ownership:{ticker}:v2"
    cached = get_cached(cache_key, _CACHE_TTL_SECONDS)
    if isinstance(cached, dict):
        return cached
    attempted: list[str] = []
    errors: dict[str, str] = {}
    result: dict[str, Any] = {}
    for provider, fetcher in (
        ("nasdaq_ownership", lambda: fetch_nasdaq_us_ownership(ticker, http_get=http_get, timeout=timeout)),
        ("yahoo_ownership", lambda: fetch_yahoo_us_ownership(ticker, yf_client=yf_client)),
    ):
        attempted.append(provider)
        try:
            result = fetcher()
        except Exception as exc:
            errors[provider] = f"{type(exc).__name__}: {exc}"
            logger.info("US ownership source %s unavailable for %s: %s", provider, ticker, exc)
            continue
        if result.get("ownership"):
            break
    result["_provider_status"] = {
        "attempted": attempted,
        "available": ["ownership"] if result.get("ownership") else [],
        "unavailable": [] if result.get("ownership") else ["ownership"],
        "errors": errors,
        "collected_at": _utc_now(),
    }
    if result.get("ownership"):
        set_cached(cache_key, result, _CACHE_TTL_SECONDS)
    return result


def fetch_yahoo_us_research(symbol: str, *, yf_client: Any = None) -> dict[str, Any]:
    """Fetch a compact analyst, options and short-interest snapshot."""
    if yf_client is None:
        import yfinance as yf_client
        _configure_yfinance_cache(yf_client)
    ticker_symbol = _symbol(symbol)
    ticker = yf_client.Ticker(ticker_symbol)
    info = ticker.info or {}
    source_url = f"https://finance.yahoo.com/quote/{ticker_symbol}"
    as_of = _utc_now()

    expectations = {
        "rating_direction": info.get("recommendationKey"),
        "recommendation_mean": _number(info.get("recommendationMean")),
        "analyst_count": _number(info.get("numberOfAnalystOpinions")),
        "target_price_median_usd": _number(info.get("targetMedianPrice")),
        "target_price_mean_usd": _number(info.get("targetMeanPrice")),
        "target_price_low_usd": _number(info.get("targetLowPrice")),
        "target_price_high_usd": _number(info.get("targetHighPrice")),
        "scope": "provider_consensus_snapshot",
        "source": "yahoo_finance",
        "source_url": f"{source_url}/analysis",
        "as_of": as_of,
    }
    expectations = _compact(expectations, keep={"scope", "source", "source_url", "as_of"})

    short_interest = {
        "shares_short": _number(info.get("sharesShort")),
        "short_ratio_days": _number(info.get("shortRatio")),
        "short_percent_of_float_pct": (
            _number(info.get("shortPercentOfFloat")) * 100
            if _number(info.get("shortPercentOfFloat")) is not None else None
        ),
        "shares_short_prior_month": _number(info.get("sharesShortPriorMonth")),
        "settlement_date": _timestamp(info.get("dateShortInterest")),
        "scope": "reported_short_interest_not_daily_short_volume",
        "source": "yahoo_finance",
        "source_url": f"{source_url}/key-statistics",
        "as_of": _timestamp(info.get("dateShortInterest")) or as_of,
    }
    short_interest = _compact(short_interest, keep={"scope", "source", "source_url", "as_of"})

    options: dict[str, Any] = {}
    expiries = list(getattr(ticker, "options", ()) or ())
    if expiries:
        spot = _number(info.get("currentPrice") or info.get("regularMarketPrice"))
        surface = []
        for expiry_value in expiries[:4]:
            expiry = str(expiry_value)
            try:
                chain = ticker.option_chain(expiry)
            except Exception as exc:
                logger.debug("Yahoo option expiry unavailable for %s/%s: %s", ticker_symbol, expiry, exc)
                continue
            surface.append(_option_expiry_summary(
                getattr(chain, "calls", None),
                getattr(chain, "puts", None),
                spot,
                expiry,
            ))
        nearest = surface[0] if surface else {}
        options = _compact({
            **nearest,
            "expiries": surface,
            "expiry_count": len(surface),
            "scope": "bounded_multi_expiry_surface_summary",
            "source": "yahoo_finance",
            "source_url": f"{source_url}/options",
            "as_of": as_of,
        }, keep={"scope", "source", "source_url", "as_of", "expiry", "expiries"})

    ownership = _ticker_ownership(ticker, ticker_symbol)

    return {
        "analyst_expectations": expectations if _has_measurement(expectations) else {},
        "options": options if _has_measurement(options) else {},
        "short_interest": short_interest if _has_measurement(short_interest) else {},
        "ownership": ownership,
    }


def _compact(value: Mapping[str, Any], *, keep: set[str]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key in keep or item is not None}


def _has_measurement(value: Mapping[str, Any]) -> bool:
    metadata = {"scope", "source", "source_url", "as_of", "expiry"}
    return any(item is not None and item != "" for key, item in value.items() if key not in metadata)


def collect_us_research(
    symbol: str,
    *,
    timeout: float | None = None,
    http_get: Callable[..., Any] = requests.get,
    http_post: Callable[..., Any] = requests.post,
    yf_client: Any = None,
) -> dict[str, Any]:
    ticker = _symbol(symbol)
    # Bump when normalized semantics change so an upgrade never serves an old
    # snapshot with values that the current contract would reject.
    cache_key = f"us_research:{ticker}:v4"
    cached = get_cached(cache_key, _CACHE_TTL_SECONDS)
    if isinstance(cached, dict):
        return cached
    deadline = float(timeout or 12)
    jobs: dict[str, Callable[[], dict[str, Any]]] = {
        "sec_edgar": lambda: fetch_sec_research(ticker, http_get=http_get, timeout=min(6.0, deadline)),
        "yahoo_research": lambda: fetch_yahoo_us_research(ticker, yf_client=yf_client),
        "finra_short_volume": lambda: {"short_volume": fetch_finra_short_volume(ticker, http_post=http_post)},
        "alpha_vantage_transcript": lambda: {"earnings_transcript": fetch_alpha_vantage_transcript(ticker, http_get=http_get)},
    }
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="us-research")
    futures = {executor.submit(job): key for key, job in jobs.items()}
    result: dict[str, Any] = {}
    errors: dict[str, str] = {}
    try:
        for future in as_completed(futures, timeout=deadline):
            key = futures[future]
            try:
                payload = future.result() or {}
                for payload_key, value in payload.items():
                    if value and payload_key != "identity":
                        result[payload_key] = value
                if payload.get("identity"):
                    result["sec_identity"] = payload["identity"]
            except Exception as exc:
                errors[key] = f"{type(exc).__name__}: {exc}"
                logger.info("US research source unavailable for %s/%s: %s", ticker, key, exc)
    except TimeoutError:
        for future, key in futures.items():
            if not future.done():
                errors[key] = "timeout"
                future.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    supported = (
        "sec_filings", "insider_activity", "analyst_expectations", "options",
        "short_interest", "short_volume", "ownership", "earnings_transcript",
    )
    available = [key for key in supported if result.get(key)]
    result["_provider_status"] = {
        "attempted": sorted(jobs),
        "available": available,
        "unavailable": sorted(set(supported) - set(available)),
        "errors": errors,
        "collected_at": _utc_now(),
    }
    if available:
        set_cached(cache_key, result, _CACHE_TTL_SECONDS)
    return result


__all__ = [
    "collect_us_ownership",
    "collect_us_research",
    "fetch_nasdaq_us_ownership",
    "fetch_alpha_vantage_transcript",
    "fetch_finra_short_volume",
    "fetch_sec_research",
    "fetch_yahoo_us_ownership",
    "fetch_yahoo_us_research",
]
