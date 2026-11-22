"""Point-in-time fundamental observations and panel enrichment."""

from __future__ import annotations

import json
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping

import pandas as pd

from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

FUNDAMENTAL_FIELDS = (
    "revenue",
    "net_income",
    "net_income_ttm",
    "book_value",
    "shareholder_equity",
    "total_debt",
    "free_cash_flow",
    "shares_outstanding",
    "market_cap",
    "pe_ratio",
    "pb_ratio",
    "return_on_equity",
    "revenue_growth",
    "debt_to_equity",
)

_ANALYSIS_FIELD_ALIASES = {
    "return_on_equity": "roe",
}

_ANALYSIS_FIELD_UNITS = {
    "revenue": "currency",
    "net_income": "currency",
    "net_income_ttm": "currency",
    "book_value": "currency_per_share",
    "shareholder_equity": "currency",
    "total_debt": "currency",
    "free_cash_flow": "currency",
    "shares_outstanding": "shares",
    "market_cap": "currency",
    "pe_ratio": "multiple",
    "pb_ratio": "multiple",
    "return_on_equity": "percent",
    "revenue_growth": "percent",
    "debt_to_equity": "multiple",
}


class FundamentalDataService:
    """Load only observations that were public at each simulated date."""

    @staticmethod
    @lru_cache(maxsize=1)
    def ensure_schema() -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            for field in FUNDAMENTAL_FIELDS:
                cur.execute(
                    f"ALTER TABLE qd_fundamental_snapshots ADD COLUMN IF NOT EXISTS {field} DOUBLE PRECISION"
                )
            db.commit()
            cur.close()

    def enrich_panel(
        self,
        frames: Mapping[str, pd.DataFrame],
        members: list[dict],
    ) -> dict[str, pd.DataFrame]:
        self.ensure_schema()
        identities = {}
        for item in members:
            symbol = str(item.get("symbol") or "").upper()
            market = str(item.get("market") or "")
            key = str(item.get("key") or "")
            fundamental_market = str(item.get("underlying_market") or market)
            fundamental_symbol = str(item.get("underlying_symbol") or symbol).upper()
            identities[symbol] = (fundamental_market, fundamental_symbol)
            if key:
                identities[key] = (fundamental_market, fundamental_symbol)
        output = {}
        for key, frame in frames.items():
            market, symbol = identities.get(key, identities.get(str(key).upper(), ("", str(key))))
            output[key] = self.enrich_frame(
                market=market,
                symbol=symbol,
                frame=frame,
            )
        return output

    def enrich_frame(self, *, market: str, symbol: str, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty or not market or not symbol:
            return frame
        try:
            rows = self._load_rows(market, symbol, frame.index.max())
        except Exception as exc:
            logger.warning("fundamental point-in-time load failed market=%s symbol=%s: %s", market, symbol, exc)
            return frame
        if not rows:
            return frame
        enriched = frame.copy()
        dates = pd.DatetimeIndex(pd.to_datetime(enriched.index, utc=True)).tz_localize(None).normalize()
        observations = pd.DataFrame(rows)
        observations["available_at"] = pd.to_datetime(observations["available_at"], utc=True).dt.tz_localize(None)
        observations = observations.sort_values(["available_at", "period_end"]).drop_duplicates("available_at", keep="last")
        observations = observations.set_index("available_at")
        for field in FUNDAMENTAL_FIELDS:
            source = observations[field] if field in observations.columns else pd.Series(index=observations.index, dtype=float)
            values = pd.to_numeric(source, errors="coerce")
            enriched[field] = values.reindex(dates, method="ffill").to_numpy()
        derived_market_cap = pd.to_numeric(enriched["close"], errors="coerce") * pd.to_numeric(
            enriched["shares_outstanding"], errors="coerce"
        )
        enriched["market_cap"] = derived_market_cap.where(derived_market_cap > 0).fillna(
            pd.to_numeric(enriched["market_cap"], errors="coerce")
        )
        return enriched

    @staticmethod
    def _load_rows(market: str, symbol: str, end: Any) -> list[dict]:
        FundamentalDataService.ensure_schema()
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                f"""
                SELECT period_end, available_at, {', '.join(FUNDAMENTAL_FIELDS)}
                FROM qd_fundamental_snapshots
                WHERE market = ? AND symbol = ? AND available_at <= ?
                ORDER BY available_at, period_end, ingested_at
                """,
                (market, symbol, pd.Timestamp(end).date()),
            )
            rows = cur.fetchall() or []
            cur.close()
        return rows

    @staticmethod
    def upsert(payload: dict) -> None:
        FundamentalDataService.ensure_schema()
        values = [payload.get(field) for field in FUNDAMENTAL_FIELDS]
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                f"""
                INSERT INTO qd_fundamental_snapshots
                  (market, symbol, period_end, available_at, frequency, currency,
                   {', '.join(FUNDAMENTAL_FIELDS)}, source, source_version, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, {', '.join(['?'] * len(FUNDAMENTAL_FIELDS))}, ?, ?, ?)
                ON CONFLICT (market, symbol, period_end, available_at, source) DO UPDATE SET
                  {', '.join(f'{field} = EXCLUDED.{field}' for field in FUNDAMENTAL_FIELDS)},
                  frequency = EXCLUDED.frequency,
                  currency = EXCLUDED.currency,
                  source_version = EXCLUDED.source_version,
                  metadata_json = EXCLUDED.metadata_json,
                  ingested_at = NOW()
                """,
                (
                    str(payload.get("market") or ""),
                    str(payload.get("symbol") or "").upper(),
                    payload.get("period_end"),
                    payload.get("available_at"),
                    str(payload.get("frequency") or "quarterly"),
                    str(payload.get("currency") or ""),
                    *values,
                    str(payload.get("source") or "manual"),
                    str(payload.get("source_version") or ""),
                    json.dumps(payload.get("metadata") or {}, ensure_ascii=False),
                ),
            )
            db.commit()
            cur.close()

    def coverage(self) -> dict[str, Any]:
        self.ensure_schema()
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT market, COUNT(*) AS observations, COUNT(DISTINCT symbol) AS symbols,
                       MIN(available_at) AS first_available_at, MAX(available_at) AS last_available_at,
                       MAX(ingested_at) AS last_ingested_at
                FROM qd_fundamental_snapshots
                GROUP BY market
                ORDER BY market
                """
            )
            rows = cur.fetchall() or []
            cur.close()
        return {
            "available": bool(rows),
            "fields": list(FUNDAMENTAL_FIELDS),
            "markets": rows,
        }

    def latest_for_analysis(
        self,
        *,
        market: str,
        symbol: str,
        as_of: date | None = None,
        max_age_seconds: int = 86_400,
        stale_report_days: int = 200,
    ) -> dict[str, Any] | None:
        """Return the newest persisted payload and its refresh state for AI analysis."""
        normalized_market = str(market or "").strip()
        normalized_symbol = str(symbol or "").strip().upper()
        if normalized_market not in {"USStock", "CNStock", "HKStock"} or not normalized_symbol:
            return None
        self.ensure_schema()
        cutoff = as_of or date.today()
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                f"""
                SELECT period_end, available_at, frequency, currency,
                       {', '.join(FUNDAMENTAL_FIELDS)}, source, source_version,
                       metadata_json, ingested_at
                FROM qd_fundamental_snapshots
                WHERE market = ? AND symbol = ? AND available_at <= ?
                ORDER BY CASE
                           WHEN jsonb_typeof(metadata_json -> 'analysisPayload') = 'object'
                            AND metadata_json -> 'analysisPayload' <> '{{}}'::jsonb
                           THEN 0 ELSE 1
                         END,
                         ingested_at DESC, available_at DESC, period_end DESC
                LIMIT 1
                """,
                (normalized_market, normalized_symbol, cutoff),
            )
            row = cur.fetchone()
            cur.close()
        if not row:
            return None

        row = dict(row)
        metadata = _mapping_value(row.get("metadata_json"))
        stored_payload = metadata.get("analysisPayload")
        has_provider_payload = isinstance(stored_payload, dict) and bool(stored_payload)
        payload = _json_safe(stored_payload) if has_provider_payload else {}
        if not isinstance(payload, dict):
            payload = {}

        source = str(payload.get("source") or row.get("source") or "local_snapshot")
        period_end = _date_value(row.get("period_end"))
        available_at = _date_value(row.get("available_at"))
        ingested_at = _datetime_value(row.get("ingested_at"))
        is_history = str(row.get("source") or "") == "yfinance_quarterly"
        field_metadata = dict(payload.get("field_metadata") or {})
        for field in FUNDAMENTAL_FIELDS:
            value = _finite_or_none(row.get(field))
            if value is None:
                continue
            analysis_key = _ANALYSIS_FIELD_ALIASES.get(field, field)
            from_provider_payload = payload.get(analysis_key) is not None
            if not from_provider_payload:
                if is_history and field in {"return_on_equity", "revenue_growth"}:
                    value *= 100.0
                payload[analysis_key] = value
            field_metadata.setdefault(
                analysis_key,
                {
                    "source": str(row.get("source") or "local_snapshot"),
                    "unit": _ANALYSIS_FIELD_UNITS.get(field),
                    "period_type": "provider_latest" if from_provider_payload else str(row.get("frequency") or "snapshot"),
                    "period_end": period_end.isoformat() if period_end and not from_provider_payload else None,
                    # A cache availability DATE is not a UTC observation instant.
                    "as_of": (metadata.get("collectedAt") or (ingested_at.isoformat() if ingested_at else None))
                    if has_provider_payload else (available_at.isoformat() if available_at else None),
                    "currency": str(row.get("currency") or "") or None,
                },
            )
        payload["field_metadata"] = field_metadata
        payload["source"] = source

        now = datetime.now(timezone.utc)
        cache_age_seconds = max(0.0, (now - ingested_at).total_seconds()) if ingested_at else None
        report_age_days = (cutoff - period_end).days if period_end else None
        stale_reasons = []
        if cache_age_seconds is None or cache_age_seconds > max(60, int(max_age_seconds)):
            stale_reasons.append("snapshot_age")
        if report_age_days is None or report_age_days > max(1, int(stale_report_days)):
            stale_reasons.append("report_age")
        refresh_required = "snapshot_age" in stale_reasons

        data_quality = dict(payload.get("data_quality") or {})
        data_quality["storage"] = {
            "source": "qd_fundamental_snapshots",
            "persisted_source": str(row.get("source") or ""),
            "ingested_at": ingested_at.isoformat() if ingested_at else None,
            "available_at": available_at.isoformat() if available_at else None,
            "period_end": period_end.isoformat() if period_end else None,
            "cache_age_seconds": cache_age_seconds,
            "report_age_days": report_age_days,
            "fresh": not stale_reasons,
            "stale_reasons": stale_reasons,
            "refresh_required": refresh_required,
        }
        payload["data_quality"] = data_quality
        return {
            "payload": payload,
            "fresh": not stale_reasons,
            "has_provider_payload": has_provider_payload,
            "stale_reasons": stale_reasons,
            "refresh_required": refresh_required,
        }

    def persist_analysis_payload(self, *, market: str, symbol: str, raw: dict[str, Any]) -> dict[str, Any]:
        """Persist an externally collected analysis payload as the current snapshot."""
        normalized_market = str(market or "").strip()
        normalized_symbol = str(symbol or "").strip().upper()
        if normalized_market not in {"USStock", "CNStock", "HKStock"} or not normalized_symbol:
            raise ValueError("factor.fundamentalMarketUnsupported")
        if not isinstance(raw, dict) or not raw:
            raise ValueError("factor.fundamentalDataUnavailable")

        statements = raw.get("financial_statements") if isinstance(raw.get("financial_statements"), dict) else {}
        latest_quarter = statements.get("latest_quarter") if isinstance(statements.get("latest_quarter"), dict) else {}
        income = latest_quarter.get("income_statement") if isinstance(latest_quarter.get("income_statement"), dict) else {}
        balance = latest_quarter.get("balance_sheet") if isinstance(latest_quarter.get("balance_sheet"), dict) else {}
        cashflow = latest_quarter.get("cash_flow") if isinstance(latest_quarter.get("cash_flow"), dict) else {}
        if not any((income, balance, cashflow)):
            income = statements.get("income_statement") if isinstance(statements.get("income_statement"), dict) else {}
            balance = statements.get("balance_sheet") if isinstance(statements.get("balance_sheet"), dict) else {}
            cashflow = statements.get("cash_flow") if isinstance(statements.get("cash_flow"), dict) else {}
        today = date.today()
        period_end = (
            latest_quarter.get("period_end")
            or income.get("latest_date")
            or balance.get("latest_date")
            or cashflow.get("latest_date")
            or today
        )
        values = {
            "revenue": income.get("total_revenue") if income.get("total_revenue") is not None else raw.get("revenue"),
            "net_income": income.get("net_income") if income.get("net_income") is not None else raw.get("net_income"),
            "net_income_ttm": raw.get("net_income_ttm") if raw.get("net_income_ttm") is not None else raw.get("trailing_net_income"),
            "book_value": raw.get("book_value"),
            "shareholder_equity": _first_present(
                raw.get("shareholder_equity"),
                balance.get("total_equity"),
                balance.get("stockholders_equity"),
            ),
            "total_debt": _first_present(raw.get("total_debt"), raw.get("debt"), balance.get("debt")),
            "free_cash_flow": cashflow.get("free_cash_flow") if cashflow.get("free_cash_flow") is not None else raw.get("free_cash_flow"),
            "shares_outstanding": raw.get("shares_outstanding"),
            "market_cap": raw.get("market_cap"),
            "pe_ratio": raw.get("pe_ratio"),
            "pb_ratio": raw.get("pb_ratio"),
            "return_on_equity": raw.get("return_on_equity") if raw.get("return_on_equity") is not None else raw.get("roe"),
            "revenue_growth": raw.get("revenue_growth"),
            "debt_to_equity": raw.get("debt_to_equity"),
        }
        usable = {key: _finite_or_none(value) for key, value in values.items()}
        if not any(value is not None for value in usable.values()):
            raise ValueError("factor.fundamentalDataUnavailable")
        payload = {
            "market": normalized_market,
            "symbol": normalized_symbol,
            "period_end": pd.Timestamp(period_end).date(),
            "available_at": today,
            "frequency": "quarterly" if latest_quarter else "snapshot",
            "currency": latest_quarter.get("currency") or (statements.get("_meta") or {}).get("currency") or "",
            "source": str(raw.get("source") or "market_data_collector")[:80],
            "source_version": today.isoformat(),
            "metadata": {
                "pointInTime": True,
                "collectedAt": pd.Timestamp.now(tz="UTC").isoformat(),
                "periodBasis": "latest_reported_quarter" if latest_quarter else "provider_latest",
                "dataQuality": _json_safe(raw.get("data_quality") or {}),
                "identity": _json_safe(raw.get("identity") or {}),
                "analysisPayload": _json_safe(raw),
            },
            **usable,
        }
        self.upsert(payload)
        return payload

    def sync_current(self, *, market: str, symbol: str) -> dict[str, Any]:
        normalized_market = str(market or "").strip()
        normalized_symbol = str(symbol or "").strip().upper()
        if normalized_market not in {"USStock", "CNStock", "HKStock"} or not normalized_symbol:
            raise ValueError("factor.fundamentalMarketUnsupported")
        from app.services.market_data_collector import MarketDataCollector

        raw = MarketDataCollector()._fetch_fundamental_uncached(normalized_market, normalized_symbol) or {}
        return self.persist_analysis_payload(
            market=normalized_market,
            symbol=normalized_symbol,
            raw=raw,
        )

    def sync_history(self, *, market: str, symbol: str) -> dict[str, Any]:
        normalized_market = str(market or "").strip()
        normalized_symbol = str(symbol or "").strip().upper()
        if normalized_market not in {"USStock", "HKStock"} or not normalized_symbol:
            raise ValueError("factor.fundamentalHistoryMarketUnsupported")

        import yfinance as yf

        provider_symbol = _yfinance_history_symbol(normalized_market, normalized_symbol)
        ticker = yf.Ticker(provider_symbol)
        quote_currency, financial_currency = _ticker_currencies(ticker)
        income = ticker.quarterly_income_stmt
        balance = ticker.quarterly_balance_sheet
        cashflow = ticker.quarterly_cash_flow
        periods = sorted(
            {
                pd.Timestamp(column).tz_localize(None).normalize()
                for frame in (income, balance, cashflow)
                if frame is not None and not frame.empty
                for column in frame.columns
                if pd.Timestamp(column).date() <= date.today()
            }
        )
        if not periods:
            raise ValueError("factor.fundamentalDataUnavailable")

        earnings_dates = _earnings_dates(ticker)
        prices = ticker.history(
            start=(periods[0] + pd.Timedelta(days=1)).date().isoformat(),
            end=(date.today() + timedelta(days=1)).isoformat(),
            auto_adjust=False,
        )
        fx_prices = _currency_history(
            yf,
            financial_currency,
            quote_currency,
            start=(periods[0] + pd.Timedelta(days=1)).date(),
            end=date.today() + timedelta(days=1),
        )
        stored = 0
        stored_dates = []
        for index, period in enumerate(periods):
            available_at, availability_source = _availability_date(period, earnings_dates)
            revenue = _statement_value(income, period, "Total Revenue", "Revenue")
            net_income = _statement_value(
                income,
                period,
                "Net Income",
                "Net Income Common Stockholders",
            )
            quarters = periods[max(0, index - 3):index + 1]
            quarterly_income = [_statement_value(income, item, "Net Income", "Net Income Common Stockholders") for item in quarters]
            contiguous = len(quarters) == 4 and all(60 <= (right - left).days <= 120 for left, right in zip(quarters, quarters[1:]))
            net_income_ttm = sum(quarterly_income) if contiguous and all(value is not None for value in quarterly_income) else None
            year_ago = periods[index - 4] if index >= 4 else None
            previous_revenue = _statement_value(income, year_ago, "Total Revenue", "Revenue") if year_ago is not None else None
            revenue_growth = (
                revenue / previous_revenue - 1.0
                if revenue is not None and previous_revenue not in (None, 0)
                and 330 <= (period - year_ago).days <= 400 else None
            )
            equity = _statement_value(balance, period, "Stockholders Equity", "Total Equity Gross Minority Interest")
            debt = _statement_value(balance, period, "Total Debt")
            shares = _statement_value(
                balance,
                period,
                "Ordinary Shares Number",
                "Share Issued",
            ) or _statement_value(income, period, "Diluted Average Shares", "Basic Average Shares")
            free_cash_flow = _statement_value(cashflow, period, "Free Cash Flow")
            close = _close_as_of(prices, available_at)
            fx_rate = _close_as_of(fx_prices, available_at)
            conversion_required = bool(
                quote_currency and financial_currency and quote_currency != financial_currency
            )
            conversion_available = not conversion_required or fx_rate is not None
            if conversion_required and fx_rate is not None:
                revenue = _scale_value(revenue, fx_rate)
                net_income = _scale_value(net_income, fx_rate)
                net_income_ttm = _scale_value(net_income_ttm, fx_rate)
                equity = _scale_value(equity, fx_rate)
                debt = _scale_value(debt, fx_rate)
                free_cash_flow = _scale_value(free_cash_flow, fx_rate)
            market_cap = close * shares if close is not None and shares is not None else None
            if conversion_required and not conversion_available:
                market_cap = None
            annual_income = net_income_ttm if net_income_ttm is not None else net_income * 4.0 if net_income is not None else None
            roe = annual_income / equity if annual_income is not None and equity not in (None, 0.0) else None
            pe_ratio = market_cap / net_income_ttm if market_cap is not None and net_income_ttm not in (None, 0.0) else None
            pb_ratio = market_cap / equity if market_cap is not None and equity not in (None, 0.0) else None
            payload = {
                "market": normalized_market,
                "symbol": normalized_symbol,
                "period_end": period.date(),
                "available_at": available_at,
                "frequency": "quarterly",
                "currency": quote_currency or financial_currency,
                "revenue": revenue,
                "net_income": net_income,
                "net_income_ttm": net_income_ttm,
                "revenue_growth": revenue_growth,
                "book_value": equity / shares if equity is not None and shares not in (None, 0.0) else None,
                "shareholder_equity": equity,
                "total_debt": debt,
                "free_cash_flow": free_cash_flow,
                "shares_outstanding": shares,
                "market_cap": market_cap,
                "pe_ratio": pe_ratio,
                "pb_ratio": pb_ratio,
                "return_on_equity": roe,
                "debt_to_equity": debt / equity if debt is not None and equity not in (None, 0.0) else None,
                "source": "yfinance_quarterly",
                "source_version": date.today().isoformat(),
                "metadata": {
                    "pointInTime": True,
                    "availabilitySource": availability_source,
                    "marketCapMethod": "close_on_or_before_available_at_x_reported_shares",
                    "quoteCurrency": quote_currency or None,
                    "financialCurrency": financial_currency or None,
                    "financialToQuoteFxRate": fx_rate if conversion_required else 1.0,
                    "currencyConversion": (
                        "financial_to_quote"
                        if conversion_required and conversion_available
                        else "unavailable" if conversion_required else "not_required"
                    ),
                },
            }
            if any(_finite_or_none(payload.get(field)) is not None for field in FUNDAMENTAL_FIELDS):
                self.upsert(payload)
                stored += 1
                stored_dates.append(available_at)
        if not stored:
            raise ValueError("factor.fundamentalDataUnavailable")
        return {
            "market": normalized_market,
            "symbol": normalized_symbol,
            "providerSymbol": provider_symbol,
            "observations": stored,
            "firstAvailableAt": min(stored_dates).isoformat(),
            "lastAvailableAt": max(stored_dates).isoformat(),
        }


def _yfinance_history_symbol(market: str, symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if market != "HKStock":
        return normalized
    if normalized.endswith(".HK"):
        normalized = normalized[:-3]
    if normalized.startswith("HK") and normalized[2:].isdigit():
        normalized = normalized[2:]
    if normalized.isdigit():
        return f"{str(int(normalized)).zfill(4)}.HK"
    return normalized


def _ticker_currencies(ticker: Any) -> tuple[str, str]:
    try:
        info = ticker.get_info()
    except Exception:
        try:
            info = ticker.info
        except Exception:
            info = {}
    info = info if isinstance(info, dict) else {}
    return (
        str(info.get("currency") or "").strip().upper(),
        str(info.get("financialCurrency") or info.get("financial_currency") or "").strip().upper(),
    )


def _currency_history(
    yf: Any,
    financial_currency: str,
    quote_currency: str,
    *,
    start: date,
    end: date,
) -> pd.DataFrame:
    if not financial_currency or not quote_currency or financial_currency == quote_currency:
        return pd.DataFrame()
    try:
        frame = yf.download(
            f"{financial_currency}{quote_currency}=X",
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception:
        return pd.DataFrame()
    if frame is None or frame.empty:
        return pd.DataFrame()
    if isinstance(frame.columns, pd.MultiIndex):
        close_columns = [item for item in frame.columns if str(item[0]).lower() == "close"]
        if not close_columns:
            return pd.DataFrame()
        return pd.DataFrame({"Close": frame[close_columns[0]]}, index=frame.index)
    return frame if "Close" in frame.columns else pd.DataFrame()


def _scale_value(value: float | None, rate: float) -> float | None:
    return value * rate if value is not None else None


def _mapping_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _first_present(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _date_value(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return pd.Timestamp(value).date()
    except (TypeError, ValueError):
        return None


def _datetime_value(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return stamp.to_pydatetime().astimezone(timezone.utc)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(child) for child in value]
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return pd.Timestamp(value).isoformat()
    finite = _finite_or_none(value)
    if finite is not None:
        return finite
    return str(value)


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _statement_value(frame: pd.DataFrame, period: pd.Timestamp, *names: str) -> float | None:
    if frame is None or frame.empty:
        return None
    column = next(
        (
            item for item in frame.columns
            if pd.Timestamp(item).tz_localize(None).normalize() == period
        ),
        None,
    )
    if column is None:
        return None
    for name in names:
        if name in frame.index:
            value = _finite_or_none(frame.loc[name, column])
            if value is not None:
                return value
    return None


def _earnings_dates(ticker: Any) -> list[date]:
    try:
        values = ticker.get_earnings_dates(limit=32)
    except Exception:
        return []
    if values is None or values.empty:
        return []
    return sorted({pd.Timestamp(item).date() for item in values.index})


def _availability_date(period: pd.Timestamp, earnings_dates: list[date]) -> tuple[date, str]:
    period_date = period.date()
    candidates = [item for item in earnings_dates if period_date < item <= period_date + timedelta(days=120)]
    if candidates:
        # Date-only observations cannot safely enter a pre-open handler on the
        # earnings day: US companies commonly report after the market closes.
        return candidates[0] + timedelta(days=1), "reported_earnings_date"
    conservative_date = period_date + timedelta(days=91)
    if conservative_date <= date.today():
        return conservative_date, "conservative_91_day_lag"
    return date.today(), "observed_at_sync"


def _close_as_of(prices: pd.DataFrame, available_at: date) -> float | None:
    if prices is None or prices.empty or "Close" not in prices.columns:
        return None
    index = pd.DatetimeIndex(prices.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    visible = prices.copy()
    visible.index = index
    visible = visible.loc[visible.index.normalize() <= pd.Timestamp(available_at)]
    if visible.empty:
        return None
    return _finite_or_none(visible["Close"].iloc[-1])


_service: FundamentalDataService | None = None


def get_fundamental_data_service() -> FundamentalDataService:
    global _service
    if _service is None:
        _service = FundamentalDataService()
    return _service
