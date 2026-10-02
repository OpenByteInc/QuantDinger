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


def test_okx_set_leverage_59669_accepts_existing_leverage():
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
