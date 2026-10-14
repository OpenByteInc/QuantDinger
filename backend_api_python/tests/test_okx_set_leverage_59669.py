from __future__ import annotations

from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.okx import OkxClient


def _client(**overrides) -> OkxClient:
    client = OkxClient.__new__(OkxClient)
    client._lev_cache = {}
    client._lev_cache_ttl_sec = 60.0
    for key, value in overrides.items():
        setattr(client, key, value)
    return client


def test_okx_set_leverage_skips_when_position_already_at_target():
    client = _client(
        _read_configured_leverage=lambda **kwargs: 1,
        _signed_request=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("set-leverage should not be called")
        ),
    )

    assert client.set_leverage(inst_id="BTC-USDT-SWAP", lever=1, mgn_mode="cross", pos_side="net") is True


def test_okx_set_leverage_skips_when_leverage_info_already_at_target():
    client = _client(
        _read_effective_leverage=lambda **kwargs: None,
        _read_configured_leverage=lambda **kwargs: 1,
        _signed_request=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("set-leverage should not be called")
        ),
    )

    assert client.set_leverage(inst_id="BTC-USDT-SWAP", lever=1, mgn_mode="cross", pos_side="net") is True


def test_okx_set_leverage_59669_accepts_existing_leverage(monkeypatch):
    monkeypatch.setattr("app.services.live_trading.okx.time.sleep", lambda *_a, **_k: None)
    calls = {"set": 0}

    def fake_read_configured_leverage(**_kwargs):
        # Keep attempting set-leverage until retries are exhausted, then
        # report the target leverage as already configured on the venue.
        if calls["set"] >= 4:
            return 1
        return None

    def fake_signed_request(method, path, **kwargs):
        if path == "/api/v5/account/set-leverage":
            calls["set"] += 1
            raise LiveTradingError("OKX error: {'code': '59669', 'msg': 'blocked'}")
        raise AssertionError(f"unexpected path: {path}")

    client = _client(
        _read_configured_leverage=fake_read_configured_leverage,
        _signed_request=fake_signed_request,
        cancel_all_algo_orders=lambda **kwargs: 0,
    )

    assert client.set_leverage(inst_id="BTC-USDT-SWAP", lever=1, mgn_mode="cross", pos_side="net") is True
    assert calls["set"] == 4


def _59669_client(monkeypatch, sweeps):
    """Client whose set-leverage always fails with 59669 until retries run out."""
    monkeypatch.setattr("app.services.live_trading.okx.time.sleep", lambda *_a, **_k: None)
    calls = {"set": 0}

    def fake_read_configured_leverage(**_kwargs):
        return 1 if calls["set"] >= 4 else None

    def fake_signed_request(method, path, **kwargs):
        if path == "/api/v5/account/set-leverage":
            calls["set"] += 1
            raise LiveTradingError("OKX error: code 59669, algo orders block leverage")
        raise AssertionError(f"unexpected path: {path}")

    def fake_cancel(**kwargs):
        sweeps.append(kwargs)
        return 1

    return _client(
        _read_configured_leverage=fake_read_configured_leverage,
        _signed_request=fake_signed_request,
        cancel_all_algo_orders=fake_cancel,
    )


def test_okx_59669_never_cancels_other_instruments_without_opt_in(monkeypatch):
    monkeypatch.delenv("OKX_CANCEL_ALL_ALGO_ORDERS", raising=False)
    sweeps = []
    client = _59669_client(monkeypatch, sweeps)

    assert client.set_leverage(inst_id="BTC-USDT-SWAP", lever=1, mgn_mode="cross", pos_side="net") is True
    assert sweeps
    assert all(call.get("inst_id") for call in sweeps)


def test_okx_59669_cancels_across_instruments_when_opted_in(monkeypatch):
    monkeypatch.setenv("OKX_CANCEL_ALL_ALGO_ORDERS", "true")
    sweeps = []
    client = _59669_client(monkeypatch, sweeps)

    assert client.set_leverage(inst_id="BTC-USDT-SWAP", lever=1, mgn_mode="cross", pos_side="net") is True
    assert any(not call.get("inst_id") for call in sweeps)
