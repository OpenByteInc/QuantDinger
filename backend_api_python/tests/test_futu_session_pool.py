from __future__ import annotations

import time

import pytest

from app.services.futu_trading.session_pool import (
    FutuSessionPool,
    reset_futu_session_pool_for_tests,
)


class _FakeClient:
    def __init__(self):
        self.connected = True
        self.closed = 0

    def disconnect(self, force: bool = False):
        self.closed += 1
        if force:
            self.connected = False


def test_pool_reuses_one_session_and_release_does_not_close():
    reset_futu_session_pool_for_tests()
    pool = FutuSessionPool()
    created = []

    def factory():
        client = _FakeClient()
        created.append(client)
        return client

    cfg = {"exchange_id": "futu", "futu_host": "127.0.0.1", "futu_port": 11111, "trade_env": "demo"}
    first = pool.acquire(cfg, mode="trade", factory=factory)
    second = pool.acquire(cfg, mode="trade", factory=factory)
    assert first is second
    assert len(created) == 1
    pool.release(first)
    pool.release(second)
    assert first.closed == 0
    assert first.connected
    snap = pool.snapshot()
    assert snap["sessions"] == 1
    assert snap["refs"] == 0


def test_trade_mode_reuses_existing_both_session():
    pool = FutuSessionPool()
    created = []

    def factory():
        client = _FakeClient()
        created.append(client)
        return client

    cfg = {"futu_host": "127.0.0.1", "futu_port": 11111, "trade_env": "demo"}
    both = pool.acquire(cfg, mode="both", factory=factory)
    trade = pool.acquire(cfg, mode="trade", factory=factory)
    assert both is trade
    assert len(created) == 1


def test_connect_failure_backs_off():
    pool = FutuSessionPool()
    cfg = {"futu_host": "127.0.0.1", "futu_port": 11111, "trade_env": "demo"}

    def boom():
        raise RuntimeError("opend down")

    with pytest.raises(RuntimeError, match="opend down"):
        pool.acquire(cfg, mode="trade", factory=boom)
    with pytest.raises(RuntimeError, match="backoff"):
        pool.acquire(cfg, mode="trade", factory=boom)


def test_drain_force_closes():
    pool = FutuSessionPool()
    client = pool.acquire(
        {"futu_host": "127.0.0.1", "futu_port": 11111},
        mode="quote",
        factory=_FakeClient,
    )
    pool.drain()
    assert client.closed >= 1
    assert not client.connected


def test_backoff_delay_is_not_tight(monkeypatch):
    pool = FutuSessionPool()
    cfg = {"futu_host": "10.0.0.8", "futu_port": 11112}
    pool.note_connect_failure(cfg, mode="trade", error="fail")
    entry = next(iter(pool._entries.values()))
    assert entry.fail_until > time.monotonic()
    assert entry.fail_streak == 1


def test_repeated_acquire_does_not_grow_session_count():
    pool = FutuSessionPool()
    cfg = {"futu_host": "127.0.0.1", "futu_port": 11111, "trade_env": "demo"}
    created = []

    def factory():
        client = _FakeClient()
        created.append(client)
        return client

    clients = [pool.acquire(cfg, mode="trade", factory=factory) for _ in range(20)]
    assert len(created) == 1
    assert pool.snapshot()["sessions"] == 1
    for client in clients:
        pool.release(client)
    assert pool.snapshot()["refs"] == 0

