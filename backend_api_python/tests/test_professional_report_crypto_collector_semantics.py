import pytest

from app.services import market_data_collector as collector_module
from app.services.market_data_collector import MarketDataCollector


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200
        self.headers = {}
        self.text = "{}"

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _collector():
    instance = MarketDataCollector.__new__(MarketDataCollector)
    instance._crypto_metric_cache = {}
    return instance


def test_coingecko_turnover_is_not_mislabeled_as_volume_change(monkeypatch):
    monkeypatch.setattr(
        collector_module.requests,
        "get",
        lambda *_args, **_kwargs: _Response([{"total_volume": 200, "market_cap": 1000}]),
    )
    result = _collector()._get_crypto_market_structure("ETH", {}, [])

    assert result["volume_24h"] == 200
    assert result["volume_change_24h"] is None
    assert result["volume_to_market_cap_pct"] == 20
    assert result["field_metadata"]["volume_to_market_cap_pct"]["unit"] == "percent"


def test_binance_decimal_funding_is_normalized_to_legacy_percent(monkeypatch):
    from app.utils import binance_request_guard as guard_module

    guard = guard_module.BinanceRequestGuard()
    guard._redis_url = ""
    monkeypatch.setattr(guard_module, "binance_request_guard", guard)

    def fake_get(url, **_kwargs):
        if url.endswith("/fundingRate"):
            return _Response([{"fundingRate": "0.0001"}])
        if url.endswith("/openInterestHist"):
            return _Response([
                {"sumOpenInterestValue": "1000000"},
                {"sumOpenInterestValue": "1100000"},
            ])
        if url.endswith("/globalLongShortAccountRatio"):
            return _Response([{"longShortRatio": "1.2"}])
        raise AssertionError(url)

    monkeypatch.setattr(collector_module.requests, "get", fake_get)
    result = _collector()._fill_crypto_derivatives_from_binance("ETHUSDT", {
        "funding_rate": None,
        "funding_rate_decimal": None,
        "open_interest": None,
        "open_interest_change_24h": None,
        "long_short_ratio": None,
        "source": "",
        "field_metadata": {},
    })

    assert result["funding_rate_decimal"] == 0.0001
    assert result["funding_rate"] == pytest.approx(0.01)
    assert result["field_metadata"]["funding_rate"]["unit"] == "percent"
    assert result["field_metadata"]["funding_rate"]["source_unit"] == "decimal"
    assert result["open_interest_change_24h"] == pytest.approx(10)


def test_gate_public_snapshot_normalizes_units_and_keeps_one_venue(monkeypatch):
    def fake_get(url, **_kwargs):
        if "/contracts/" in url:
            return _Response({"funding_rate": "0.0002"})
        if url.endswith("/contract_stats"):
            return _Response([
                {"open_interest_usd": 1000, "lsr_account": 0.9},
                {"open_interest_usd": 1100, "lsr_account": 1.1},
            ])
        raise AssertionError(url)

    monkeypatch.setattr(collector_module.requests, "get", fake_get)
    result = _collector()._get_gate_public_derivatives("BTC_USDT")

    assert result["funding_rate"] == pytest.approx(0.02)
    assert result["open_interest"] == 1100
    assert result["open_interest_change_24h"] == pytest.approx(10)
    assert result["long_short_ratio"] == 1.1
    assert {item["venue"] for item in result["field_metadata"].values()} == {"gate"}


def test_derivatives_fallback_replaces_partial_aggregate_instead_of_mixing_venues():
    collector = _collector()
    collector._coinglass_get = lambda path, *_args, **_kwargs: (
        {"funding_rate": 0.01} if "fundingRate" in path else None
    )
    gate = {
        "funding_rate": 0.02,
        "funding_rate_decimal": 0.0002,
        "open_interest": 1000,
        "open_interest_change_24h": 2,
        "long_short_ratio": 1.1,
        "source": "gate_public",
        "field_metadata": {
            "funding_rate": {"venue": "gate"},
            "open_interest": {"venue": "gate"},
        },
    }
    collector._get_gate_public_derivatives = lambda _pair: gate
    collector._get_okx_public_derivatives = lambda _pair: pytest.fail("OKX should not be called")

    result = collector._get_crypto_derivatives_metrics("BTC")

    assert result is gate
    assert result["source"] == "gate_public"
