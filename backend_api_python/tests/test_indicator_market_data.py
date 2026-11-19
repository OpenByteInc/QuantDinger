from __future__ import annotations

import pytest

from app.services.indicator_market_data import (
    IndicatorDataRequest,
    IndicatorMarketDataService,
    extract_indicator_data_requests,
    normalize_indicator_data_requests,
)


class _FakeKlineService:
    def __init__(self) -> None:
        self.calls = []

    def get_kline(self, **kwargs):
        self.calls.append(kwargs)
        timeframe = kwargs["timeframe"]
        limit = int(kwargs["limit"])
        seconds = 60 if timeframe == "1m" else 86_400
        end = int(kwargs.get("before_time") or (seconds * 100_000))
        start = max(0, end - limit * seconds)
        return [
            {
                "time": timestamp,
                "open": 1,
                "high": 2,
                "low": 0.5,
                "close": 1.5,
                "volume": 10,
            }
            for timestamp in range(start, end, seconds)
        ]


def test_literal_indicator_data_requests_are_extracted_without_execution():
    code = """
indicator_timeframes = ['1D', '4H']
indicator_lower_timeframes = ('1m',)
daily = request_series('1D', field='close')
minute = ctx.request_lower_tf('1m', fields='volume')
"""
    assert [request.as_dict() for request in extract_indicator_data_requests(code)] == [
        {"timeframe": "1D", "mode": "series"},
        {"timeframe": "4H", "mode": "series"},
        {"timeframe": "1m", "mode": "lower_tf"},
    ]


def test_bound_timeframe_parameter_selects_one_declared_lower_timeframe():
    code = """
indicator_lower_timeframes = ['1m', '5m', '15m']
indicator_lower_timeframe_param = 'lower_timeframe'
lower_timeframe = params.get('lower_timeframe', '1m')
rows = request_lower_tf(lower_timeframe, fields=['open', 'close', 'volume'])
"""
    assert [
        request.as_dict()
        for request in extract_indicator_data_requests(code, {"lower_timeframe": "15m"})
    ] == [{"timeframe": "15m", "mode": "lower_tf"}]


def test_context_fetches_same_symbol_requests_with_one_bounded_call_each():
    fake = _FakeKlineService()
    service = IndicatorMarketDataService(fake)
    context = service.build_context(
        market="Crypto",
        symbol="BTC/USDT",
        base_timeframe="4H",
        base_count=10,
        requests=normalize_indicator_data_requests([
            {"timeframe": "1D", "mode": "series"},
            {"timeframe": "1m", "mode": "lower_tf"},
        ]),
        exchange_id="gate",
        market_type="spot",
    )

    assert context["market"] == "Crypto"
    assert context["symbol"] == "BTC/USDT"
    assert context["requests"] == [
        {"timeframe": "1D", "mode": "series"},
        {"timeframe": "1m", "mode": "lower_tf"},
    ]
    assert context["series"]["1D"]
    assert len(context["series"]["1m"]) == 2880
    assert context["coverage"]["1m"] == {
        "mode": "lower_tf",
        "target_bars": 2880,
        "returned_bars": 2880,
    }
    assert context["limits"] == {
        "series_bars": 1000,
        "lower_tf_bars": 5000,
        "host_fetches_per_timeframe": 1,
    }
    assert all(call["symbol"] == "BTC/USDT" for call in fake.calls)
    assert len([call for call in fake.calls if call["timeframe"] == "1m"]) == 1
    assert all(call.get("before_time") is None for call in fake.calls)


def test_lower_timeframe_fetch_is_capped_without_historical_pagination():
    fake = _FakeKlineService()
    service = IndicatorMarketDataService(fake)

    context = service.build_context(
        market="Crypto",
        symbol="BTC/USDT",
        base_timeframe="4H",
        base_count=300,
        requests=[{"timeframe": "1m", "mode": "lower_tf"}],
    )

    assert len(context["series"]["1m"]) == 5000
    assert context["coverage"]["1m"]["target_bars"] == 5000
    assert len(fake.calls) == 1
    assert fake.calls[0]["limit"] == 5000
    assert fake.calls[0].get("before_time") is None


def test_context_request_limit_applies_to_typed_requests():
    requests = [
        IndicatorDataRequest(timeframe=timeframe)
        for timeframe in ["1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H", "1D"]
    ]

    with pytest.raises(ValueError, match="at most 8"):
        normalize_indicator_data_requests(requests)
