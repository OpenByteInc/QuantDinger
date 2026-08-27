"""Regression coverage for generic K-line normalization."""

import math

import pytest

from app.data_sources.base import BaseDataSource


class _TestDataSource(BaseDataSource):
    """Concrete shell for exercising the shared normalization method."""

    def get_kline(self, symbol, timeframe, limit, before_time=None, after_time=None):
        return []


@pytest.fixture
def data_source():
    return _TestDataSource()


@pytest.mark.parametrize(
    "price",
    [0.01038, 0.001038, 123.456789],
)
def test_format_kline_preserves_source_price_precision(data_source, price):
    row = data_source.format_kline(1_700_000_000, price, price, price, price, 12.3456)

    assert row["open"] == price
    assert row["high"] == price
    assert row["low"] == price
    assert row["close"] == price


def test_format_kline_keeps_timestamp_and_existing_volume_normalization(data_source):
    row = data_source.format_kline(
        1_700_000_123,
        10.123456,
        10.234567,
        10.012345,
        10.200001,
        9876.54321,
    )

    assert row["time"] == 1_700_000_123
    assert row["volume"] == 9876.54


def test_format_kline_preserves_nan_price_behavior(data_source):
    row = data_source.format_kline(1, math.nan, 2.0, 1.0, 1.5, 0.0)

    assert math.isnan(row["open"])


def test_format_kline_rejects_non_numeric_prices(data_source):
    with pytest.raises(ValueError):
        data_source.format_kline(1, "not-a-price", 2.0, 1.0, 1.5, 0.0)
