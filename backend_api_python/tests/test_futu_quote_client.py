from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.data_sources.factory import DataSourceFactory
from app.data_sources.errors import UnsupportedMarketError
from app.data_sources.futu import FutuDataSource
from app.services.futu_trading.config import FutuConfig
from app.services.futu_trading.quote_client import FutuQuoteClient
from app.services.futu_trading.timezones import futu_time_key_to_timestamp


def test_futu_time_key_uses_hk_exchange_timezone():
    timestamp = futu_time_key_to_timestamp("2026-08-10 09:30:00", "HKStock")
    assert datetime.fromtimestamp(timestamp, tz=timezone.utc) == datetime(
        2026, 8, 10, 1, 30, tzinfo=timezone.utc
    )


def test_futu_time_key_uses_us_dst_exchange_timezone():
    timestamp = futu_time_key_to_timestamp("2026-08-10 09:30:00", "USStock")
    assert datetime.fromtimestamp(timestamp, tz=timezone.utc) == datetime(
        2026, 8, 10, 13, 30, tzinfo=timezone.utc
    )


def test_market_data_source_opens_quote_only_client(monkeypatch):
    created = []

    class FakeQuoteClient:
        def __init__(self, config):
            created.append(config)
            self.connected = False

        def connect(self):
            self.connected = True
            return True

        def close(self, force: bool = False):
            self.connected = False

        disconnect = close

    from app.services.futu_trading.session_pool import reset_futu_session_pool_for_tests

    reset_futu_session_pool_for_tests()
    monkeypatch.setattr(
        "app.services.futu_trading.quote_client.FutuQuoteClient",
        FakeQuoteClient,
    )
    source = FutuDataSource(
        market="HKStock",
        exchange_config={"futu_host": "10.0.0.8", "futu_port": 11112},
    )

    client = source._get_client()

    assert isinstance(client, FakeQuoteClient)
    assert created[0].host == "10.0.0.8"
    assert created[0].port == 11112
    assert not hasattr(client, "_trade_ctx")
    source.close()


def test_futu_boundaries_are_converted_to_exchange_dates(monkeypatch):
    client = MagicMock()
    client.connected = True
    client.get_history_kline.return_value = []
    source = FutuDataSource(market="USStock")
    source._client = client

    source.get_kline(
        "AAPL",
        "1D",
        5,
        before_time=int(datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc).timestamp()),
        after_time=int(datetime(2026, 8, 9, 22, 0, tzinfo=timezone.utc).timestamp()),
    )

    kwargs = client.get_history_kline.call_args.kwargs
    # UTC Aug 9 22:00 / Aug 10 02:00 are both Aug 9 in New York (EDT).
    assert kwargs["start"] == "2026-08-09"
    assert kwargs["end"] == "2026-08-09"


@pytest.mark.parametrize("raises", [False, True])
def test_factory_closes_request_scoped_futu_source(monkeypatch, raises):
    class FakeSource:
        close_after_request = True

        def __init__(self):
            self.closed = False

        def get_kline(self, *_args):
            if raises:
                raise RuntimeError("quote failed")
            return [{"time": 1}]

        def close(self):
            self.closed = True

    source = FakeSource()
    monkeypatch.setattr(
        DataSourceFactory,
        "_resolve_source",
        lambda *_args, **_kwargs: source,
    )

    if raises:
        with pytest.raises(RuntimeError, match="quote failed"):
            DataSourceFactory.get_kline(
                "HKStock",
                "00700.HK",
                "1D",
                1,
                strict_data_source=True,
            )
    else:
        assert DataSourceFactory.get_kline("HKStock", "00700.HK", "1D", 1)
    assert source.closed


def test_quote_client_rejects_remote_host_before_loading_sdk(monkeypatch):
    monkeypatch.delenv("FUTU_ALLOW_REMOTE_OPEND", raising=False)
    ensure_futu = MagicMock()
    monkeypatch.setattr(
        "app.services.futu_trading.quote_client._ensure_futu",
        ensure_futu,
    )
    client = FutuQuoteClient(FutuConfig(host="169.254.169.254"))

    assert client.connect() is False
    ensure_futu.assert_not_called()


def test_four_hour_resampling_aligns_to_first_exchange_session_bar():
    source = FutuDataSource(market="USStock")
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    offsets = [0, 1, 4]  # Missing source bars must not shift the 13:30 bucket.
    rows = [
        {
            "time": int((start + timedelta(hours=hour_offset)).timestamp()),
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100.5 + index,
            "volume": 10,
        }
        for index, hour_offset in enumerate(offsets)
    ]

    resampled = source._resample_hours(rows, hours=4)

    assert len(resampled) == 2
    assert resampled[0]["time"] == rows[0]["time"]
    assert resampled[0]["open"] == 100
    assert resampled[0]["close"] == 101.5
    assert resampled[0]["volume"] == 20
    assert resampled[1]["time"] == rows[2]["time"]


def test_futu_broker_alias_requires_explicit_market():
    with pytest.raises(UnsupportedMarketError, match="HKStock or USStock"):
        DataSourceFactory.normalize_market("futu")
    with pytest.raises(UnsupportedMarketError, match="HKStock or USStock"):
        DataSourceFactory.get_data_source("futu")
