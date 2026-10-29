from datetime import UTC, datetime

import pandas as pd

from app.data_providers.cross_asset_research import (
    fetch_cftc_positioning,
    fetch_forex_drivers,
    fetch_futures_curve,
)
from app.research.intelligence import build_research_intelligence


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_cftc_positioning_keeps_weekly_scope_and_participant_group():
    fields = ["GOLD - COMMODITY EXCHANGE INC.", "261020", "2026-10-20"]
    fields.extend([""] * 10)
    fields.extend(["150000", "42000"])
    text = ",".join(fields) + "\n"
    result = fetch_cftc_positioning("GC", http_get=lambda *args, **kwargs: FakeResponse(text))

    assert result["participant_group"] == "money_manager"
    assert result["net_contracts"] == 108000
    assert result["scope"] == "weekly_cftc_commitments_of_traders"


class FakeMacroProvider:
    def fetch_fred_series(self, series_id, limit):
        values = {"ECBDFR": "2.0", "FEDFUNDS": "4.5"}
        return {"observations": [{"date": "2026-10-01", "value": values[series_id]}]}


def test_forex_drivers_are_base_quote_relative_and_auditable():
    result = fetch_forex_drivers("EUR/USD", macro_provider=FakeMacroProvider())

    assert result["base_currency"] == "EUR"
    assert result["quote_currency"] == "USD"
    assert result["policy_rate_differential_pct"] == -2.5
    assert result["source"] == "fred"


class FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol

    def history(self, **kwargs):
        if self.symbol == "GC=F":
            return pd.DataFrame(
                {"Close": [100.0, 101.0]},
                index=pd.to_datetime(["2026-10-20", "2026-10-21"], utc=True),
            )
        price = 101.0 if "Z26" in self.symbol else 102.0 if "F27" in self.symbol else None
        if price is None:
            return pd.DataFrame()
        return pd.DataFrame(
            {"Close": [price]},
            index=pd.to_datetime(["2026-10-21"], utc=True),
        )


class FakeYFinance:
    @staticmethod
    def Ticker(symbol):
        return FakeTicker(symbol)


def test_futures_curve_identifies_curve_state_from_distinct_expiries():
    result = fetch_futures_curve("GC", yf_client=FakeYFinance())

    assert result["scope"] == "nearby_expiry_settlement_proxy"
    assert result["curve_state"] == "contango"
    assert len(result["contracts"]) == 2


def test_research_intelligence_exposes_calculation_audit_and_gaps():
    now = datetime.now(UTC).isoformat()
    result = build_research_intelligence({
        "market": "Forex",
        "symbol": "EURUSD",
        "kline": [
            {"time": now, "close": 1.0},
            {"time": now, "close": 1.1},
            {"time": now, "close": 1.05},
        ],
        "forex_drivers": {"base_currency": "EUR", "quote_currency": "USD"},
    }, quality={"quality_score": 0.7})

    assert result["workflow_id"] == "fx_macro_relative_value_v1"
    assert result["readiness_posture"] == "conditional"
    assert result["performance"]["period_return_pct"] == 5.0
    assert {item["metric"] for item in result["calculation_audit"]} == {
        "period_return_pct", "realized_volatility_per_bar_pct", "max_drawdown_pct",
    }


def test_research_intelligence_builds_evidence_linked_brief_and_watchlist():
    result = build_research_intelligence(
        {"market": "USStock", "symbol": "TEST"},
        quality={"quality_score": 0.8},
        summary="Balanced summary",
        claims=[
            {"kind": "thesis", "text": "Core thesis", "evidence_refs": ["ev_1"]},
            {"kind": "counter_argument", "text": "Variant view", "evidence_refs": ["ev_2"]},
            {"kind": "catalyst", "text": "Catalyst", "evidence_refs": ["ev_3"]},
        ],
        scenarios=[
            {"case": "bull", "trigger": "Bull trigger", "invalidation": 90, "evidence_refs": ["ev_1"]},
        ],
        risk_plan={"invalidation_conditions": ["Risk limit breached"]},
    )

    brief = result["research_brief"]
    assert brief["core_thesis"] == {
        "kind": "thesis", "text": "Core thesis", "evidence_refs": ["ev_1"],
    }
    assert brief["variant_view"]["text"] == "Variant view"
    assert brief["catalysts"][0]["evidence_refs"] == ["ev_3"]
    assert [item["kind"] for item in brief["watch_items"]] == ["scenario", "invalidation"]
    assert brief["claim_balance"]["counter_argument"] == 1
