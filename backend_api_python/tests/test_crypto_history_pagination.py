"""Exercise real adapters against exchange-shaped public candle responses."""
import time
from types import SimpleNamespace

import pytest

from app.data_sources.crypto import CryptoDataSource
from app.data_sources.native_crypto import NativeCryptoPublicClient, _TIMEFRAME_SECONDS


def make_source(venue, market, monkeypatch):
    source = object.__new__(CryptoDataSource)
    source.exchange = NativeCryptoPublicClient(venue, market)
    source._allow_public_fallback = False
    source._preferred_public_exchange_id = ''
    source._scoped_market_type = market
    source._symbol_for_scoped_market = lambda _symbol: 'BTC/USDT'
    source._is_invalid_symbol_cached = lambda _symbol: False
    return source


def mock_public_api(client, step, monkeypatch, retained_bars=4000):
    end = int(time.time() * 1000) // step * step
    all_times = list(range(end - retained_bars * step, end + step, step))
    calls = []

    def get(url, params):
        calls.append((url, dict(params)))
        venue = client.id
        count = int(params.get('limit', params.get('size', 300)))
        if venue == 'gate':
            if 'from' in params:
                assert 'limit' not in params
                low, high = params['from'] * 1000, params['to'] * 1000
                assert (high - low) // step + 1 <= 1000
                times = [t for t in all_times if low <= t <= high]
            else:
                times = all_times[-count:]
        elif venue == 'binance':
            times = ([t for t in all_times if t >= params['startTime']][:count]
                     if 'startTime' in params else all_times[-count:])
        elif venue == 'bybit':
            times = ([t for t in all_times if params['start'] <= t <= params['end']][-count:]
                     if 'start' in params else all_times[-count:])
        elif venue == 'bitget':
            if 'startTime' in params:
                assert url.endswith('/history-candles')
                assert count <= 100
                assert params['endTime'] - params['startTime'] <= 90 * 86400000
                times = [t for t in all_times if params['startTime'] <= t < params['endTime']][-count:]
            else:
                times = all_times[-count:]
        elif venue == 'okx':
            times = ([t for t in all_times if t < params['after']][-count:]
                     if 'after' in params else all_times[-count:])
        else:
            times = all_times[-count:]
        if venue == 'gate':
            if client.market_type == 'spot':
                return [[str(t // 1000), '10', '1.5', '2', '1', '1.1', '5'] for t in times]
            return [dict(t=t // 1000, o='1.1', h='2', l='1', c='1.5', v='5') for t in times]
        if venue == 'htx':
            return dict(status='ok', data=[dict(id=t // 1000, open=1.1, high=2, low=1, close=1.5, amount=5) for t in reversed(times)])
        rows = [[t, '1.1', '2', '1', '1.5', '5'] for t in times]
        if venue == 'binance':
            return rows
        if venue == 'bybit':
            return dict(retCode=0, result=dict(list=list(reversed(rows))))
        return dict(code='0' if venue == 'okx' else '00000', data=list(reversed(rows)))

    monkeypatch.setattr(client, '_get', get)
    return end, calls


@pytest.mark.parametrize('venue', ['gate', 'binance', 'bybit', 'okx', 'bitget', 'htx'])
@pytest.mark.parametrize('market', ['spot', 'swap'])
@pytest.mark.parametrize('tf', ['1m', '5m', '15m', '30m', '1H', '4H', '1D'])
def test_two_history_pages_join_without_missing_or_duplicate_bars(venue, market, tf, monkeypatch):
    source = make_source(venue, market, monkeypatch)
    step = _TIMEFRAME_SECONDS[tf.lower()] * 1000
    mock_public_api(source.exchange, step, monkeypatch)
    latest = source.get_kline('BTC/USDT', tf, 480)
    assert latest
    for _ in range(2):
        boundary = latest[0]['time']
        history = source.get_kline('BTC/USDT', tf, 500, boundary)
        assert len(history) == 500
        assert history[-1]['time'] + step // 1000 == boundary
        times = [row['time'] for row in history + latest]
        assert len(times) == len(set(times))
        assert all(b - a == step // 1000 for a, b in zip(times, times[1:]))
        latest = history


@pytest.mark.parametrize('venue', ['gate', 'binance', 'bybit', 'okx', 'bitget', 'htx'])
@pytest.mark.parametrize('fault', ['right_edge', 'internal_gap', 'network_error', 'stalled_cursor'])
def test_historical_partial_data_never_becomes_a_successful_page(venue, fault, monkeypatch):
    now = int(time.time()) // 60 * 60
    boundary = now - 600 * 60
    calls = []

    def fetch(_symbol, _tf, since=None, limit=None):
        calls.append(since)
        assert since is not None
        start = (boundary - 600 * 60) * 1000
        if fault == 'network_error' and len(calls) > 1:
            raise RuntimeError('temporary public API failure')
        if fault == 'right_edge' and len(calls) > 1:
            return []
        if fault in ('network_error', 'right_edge', 'stalled_cursor'):
            return [[start + i * 60000, 1, 2, 1, 1.5, 5] for i in range(300)]
        return [[start + i * 60000, 1, 2, 1, 1.5, 5] for i in range(600) if i != 450]

    monkeypatch.setattr(time, 'sleep', lambda _seconds: None)
    source = object.__new__(CryptoDataSource)
    source.exchange = SimpleNamespace(id=venue, enableRateLimit=True, fetch_ohlcv=fetch)
    assert source._fetch_ohlcv('BTC/USDT', '1m', 500, boundary, '1m') == []
    assert calls and None not in calls


def test_gate_reported_4h_boundary_returns_the_adjacent_candle(monkeypatch):
    source = make_source('gate', 'spot', monkeypatch)
    mock_public_api(source.exchange, 14400000, monkeypatch)
    rows = source.get_kline('BTC/USDT', '4H', 500, 1786723200)
    assert len(rows) == 500
    assert rows[-1]['time'] == 1786708800


@pytest.mark.parametrize('venue', ['gate', 'binance', 'bybit', 'okx', 'bitget'])
def test_explicit_strategy_window_spans_multiple_exchange_pages(venue, monkeypatch):
    source = make_source(venue, 'swap', monkeypatch)
    end, _ = mock_public_api(source.exchange, 60000, monkeypatch)
    before, after = end // 1000, end // 1000 - 1500 * 60
    rows = source.get_kline('BTC/USDT', '1m', 2000, before, after)
    assert len(rows) == 1500
    assert rows[0]['time'] == after
    assert rows[-1]['time'] == before - 60


def test_htx_does_not_substitute_recent_data_for_expired_history(monkeypatch):
    source = make_source('htx', 'spot', monkeypatch)
    end, calls = mock_public_api(source.exchange, 60000, monkeypatch)
    assert source.get_kline('BTC/USDT', '1m', 500, end // 1000 - 2500 * 60) == []
    assert not calls


@pytest.mark.parametrize('venue', ['gate', 'bitget', 'htx'])
def test_resampled_three_minute_pages_use_stable_time_buckets(venue, monkeypatch):
    source = make_source(venue, 'spot', monkeypatch)
    mock_public_api(source.exchange, 60000, monkeypatch)
    latest = source.get_kline('BTC/USDT', '3m', 80)
    history = source.get_kline('BTC/USDT', '3m', 80, latest[0]['time'])
    assert len(history) == 80
    assert history[-1]['time'] + 180 == latest[0]['time']
    assert all(row['time'] % 180 == 0 for row in history + latest)


def test_resampled_weekly_history_joins_latest_week(monkeypatch):
    source = make_source('bitget', 'spot', monkeypatch)
    mock_public_api(source.exchange, 86400000, monkeypatch)
    latest = source.get_kline('BTC/USDT', '1W', 40)
    history = source.get_kline('BTC/USDT', '1W', 40, latest[0]['time'])
    assert len(history) == 40
    assert history[-1]['time'] + 604800 == latest[0]['time']


def test_aligned_resampling_never_aggregates_across_a_missing_source_bar():
    rows = [[i * 60000, 1, 2, 1, 1.5, 5] for i in [0, 1, 3, 4, 5]]
    result = CryptoDataSource._resample_ohlcv(rows, 3, source_ms=60000, target_ms=180000)
    assert [row[0] for row in result] == [180000]


def test_bitget_weekly_page_can_start_before_listing(monkeypatch):
    source = make_source('bitget', 'spot', monkeypatch)
    mock_public_api(source.exchange, 86400000, monkeypatch, retained_bars=2000)
    latest = source.get_kline('BTC/USDT', '1W', 480)
    history = source.get_kline('BTC/USDT', '1W', 500, latest[0]['time'])
    assert 200 < len(history) < 500
    assert history[-1]['time'] + 604800 == latest[0]['time']
    assert all(b['time'] - a['time'] == 604800 for a, b in zip(history, history[1:]))


def test_htx_chart_can_use_retained_history_without_the_extra_buffer(monkeypatch):
    source = make_source('htx', 'spot', monkeypatch)
    end, _ = mock_public_api(source.exchange, 60000, monkeypatch)
    rows = source.get_kline('BTC/USDT', '3m', 500, end // 1000 - 300 * 60)
    assert len(rows) == 500
    assert rows[-1]['time'] + 180 == (end // 1000 - 300 * 60) // 180 * 180
