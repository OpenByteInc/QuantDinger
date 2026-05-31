"""Tests for bar-boundary-aligned K-line polling schedule."""

from datetime import datetime, timezone

from app.services.trading_executor import next_kline_boundary_poll_ts


def _ts(y, m, d, h, mi, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc).timestamp()


def test_30m_boundary_poll_offset_two_seconds():
    tf = 1800
    offset = 2.0
    # 17:04:53 UTC → next close 17:30:00 → poll 17:30:02
    now = _ts(2026, 5, 31, 17, 4, 53)
    nxt = next_kline_boundary_poll_ts(now, tf, offset)
    assert nxt == _ts(2026, 5, 31, 17, 30, 2)

    # Exactly at poll time → schedule following bar
    at_poll = _ts(2026, 5, 31, 17, 30, 2)
    nxt2 = next_kline_boundary_poll_ts(at_poll, tf, offset)
    assert nxt2 == _ts(2026, 5, 31, 18, 0, 2)


def test_1h_boundary():
    tf = 3600
    now = _ts(2026, 5, 31, 17, 15, 0)
    nxt = next_kline_boundary_poll_ts(now, tf, 2.0)
    assert nxt == _ts(2026, 5, 31, 18, 0, 2)
