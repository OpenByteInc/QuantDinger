"""Unit tests for HTX swap API V5 helpers and client."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.live_trading import htx_v5
from app.services.live_trading.htx import HtxClient


def test_v5_ok_accepts_status_and_code():
    assert htx_v5.v5_ok({"status": "ok", "data": {}})
    assert htx_v5.v5_ok({"code": 200, "data": {}})
    assert not htx_v5.v5_ok({"code": 400, "message": "fail"})


def test_normalize_balance_from_v5_data_list():
    raw = {
        "code": 200,
        "data": [
            {"currency": "USDT", "available": "42", "equity": "42"},
        ],
    }
    out = htx_v5.normalize_balance(raw)
    assert len(out["data"]) == 1
    assert out["data"][0]["margin_available"] == 42.0


def test_normalize_balance_from_v5_details():
    raw = {
        "code": 200,
        "data": {
            "equity": "1000",
            "available_margin": "800",
            "details": [
                {
                    "currency": "USDT",
                    "equity": "1000",
                    "available": "750",
                    "withdraw_available": "750",
                }
            ],
        },
    }
    out = htx_v5.normalize_balance(raw)
    assert out["status"] == "ok"
    assert len(out["data"]) == 1
    row = out["data"][0]
    assert row["margin_asset"] == "USDT"
    assert row["margin_available"] == 750.0
    assert row["margin_balance"] == 1000.0


def test_normalize_positions_list():
    raw = {
        "code": 200,
        "data": [
            {"contract_code": "BTC-USDT", "volume": 10, "direction": "buy"},
        ],
    }
    out = htx_v5.normalize_positions(raw)
    assert len(out["data"]) == 1
    assert out["data"][0]["contract_code"] == "BTC-USDT"
    assert out["data"][0]["volume"] == 10


def test_normalize_order_place():
    raw = {"status": "ok", "data": {"order_id": "12345", "order_id_str": "12345"}}
    out = htx_v5.normalize_order_place(raw)
    assert out["data"]["order_id_str"] == "12345"


def test_swap_balance_uses_v5_only():
    c = HtxClient(api_key="k", secret_key="s", market_type="swap")
    v5_raw = {
        "code": 200,
        "data": {
            "details": [{"currency": "USDT", "available": "100", "equity": "100"}],
        },
    }
    with patch.object(c, "_swap_v5_request", return_value=v5_raw) as mock_v5:
        out = c.get_balance()
    mock_v5.assert_called_once_with("GET", "/v5/account/balance")
    assert out["data"][0]["margin_available"] == 100.0


def test_map_v1_order_price_type_to_v5_type():
    assert htx_v5.map_v1_order_price_type_to_v5_type("opponent") == "market"
    assert htx_v5.map_v1_order_price_type_to_v5_type("limit", has_limit_price=True) == "limit"
    assert htx_v5.map_v1_order_price_type_to_v5_type("ioc") == "ioc"


def test_parse_position_mode_hedged():
    assert htx_v5.parse_position_mode_hedged({"position_mode": "dual_side"}) is True
    assert htx_v5.parse_position_mode_hedged({"position_mode": "single_side"}) is False


def test_build_v1_cross_order_body_market():
    body = htx_v5.build_v1_cross_order_body(
        contract_code="DOGE-USDT",
        volume=2,
        side="buy",
        lever_rate=5,
        order_price_type="opponent",
        hedge_mode=False,
    )
    assert body.get("direction") == "buy"
    assert body.get("order_price_type") == "opponent"
    assert body.get("offset") == "both"
    assert "position_mode" not in body
    assert "side" not in body


def test_build_swap_order_body_one_way_uses_position_side_both():
    body = htx_v5.build_swap_order_body(
        contract_code="DOGE-USDT",
        volume=1,
        side="buy",
        order_price_type="opponent",
        position_side="both",
    )
    assert body.get("position_side") == "both"
    assert "offset" not in body
    assert "trade_type" not in body
    assert "lever_rate" not in body
    assert "position_mode" not in body
    assert "reduce_only" not in body


def test_build_swap_order_body_one_way_close_uses_reduce_only():
    body = htx_v5.build_swap_order_body(
        contract_code="DOGE-USDT",
        volume=1,
        side="sell",
        order_price_type="opponent",
        reduce_only=True,
        position_side="both",
    )
    assert body.get("reduce_only") == 1
    assert body.get("position_side") == "both"


def test_build_swap_order_body_hedge_uses_position_side_long():
    body = htx_v5.build_swap_order_body(
        contract_code="DOGE-USDT",
        volume=1,
        side="buy",
        order_price_type="opponent",
        position_side="long",
    )
    assert body.get("position_side") == "long"
    assert "offset" not in body
    assert "trade_type" not in body
    assert "lever_rate" not in body


def test_resolve_v5_position_side():
    # dual_side, infer from side/reduce_only
    assert htx_v5.resolve_v5_position_side(
        side="buy", reduce_only=False, hedge_mode=True
    ) == "long"
    assert htx_v5.resolve_v5_position_side(
        side="sell", reduce_only=False, hedge_mode=True
    ) == "short"
    assert htx_v5.resolve_v5_position_side(
        side="sell", reduce_only=True, hedge_mode=True
    ) == "long"
    assert htx_v5.resolve_v5_position_side(
        side="buy", reduce_only=True, hedge_mode=True
    ) == "short"
    # explicit pos_side wins
    assert htx_v5.resolve_v5_position_side(
        side="buy", reduce_only=False, hedge_mode=True, pos_side="short"
    ) == "short"
    # one-way always both
    assert htx_v5.resolve_v5_position_side(
        side="buy", reduce_only=False, hedge_mode=False
    ) == "both"


def test_swap_order_body_variants_uses_position_side():
    variants = htx_v5.swap_order_body_variants(
        contract_code="DOGE-USDT",
        volume=1,
        side="buy",
        order_price_type="opponent",
        preferred_hedge=True,
    )
    assert all("offset" not in v and "trade_type" not in v for v in variants)
    assert any(v.get("position_side") == "long" for v in variants)
    assert any(v.get("position_side") == "both" for v in variants)
    assert all(v.get("type") == "market" for v in variants)
    hedge_only = htx_v5.swap_order_body_variants(
        contract_code="DOGE-USDT",
        volume=1,
        side="sell",
        order_price_type="opponent",
        reduce_only=True,
        hedge_only=True,
    )
    assert hedge_only[0].get("position_side") == "long"
    assert hedge_only[0].get("reduce_only") == 1


def test_is_multi_asset_v1_unavailable():
    assert htx_v5.is_multi_asset_v1_unavailable(
        "The Multi-Assets Collateral mode is temporarily unavailable."
    )


def test_place_swap_order_v1():
    c = HtxClient(api_key="k", secret_key="s", market_type="swap")
    body = htx_v5.build_v1_cross_order_body(
        contract_code="BTC-USDT",
        volume=1,
        side="buy",
        lever_rate=5,
        order_price_type="opponent",
    )
    v1_resp = {"status": "ok", "data": {"order_id_str": "888"}}
    with patch.object(c, "_swap_private_request_raw", return_value=v1_resp) as mock_v1:
        res = c._place_swap_order_v1(body)
    assert res.exchange_order_id == "888"
    sent = mock_v1.call_args.kwargs.get("json_body") or mock_v1.call_args[1].get("json_body")
    assert sent.get("direction") == "buy"
    assert sent.get("order_price_type") == "opponent"


def test_place_swap_order_v5_only():
    c = HtxClient(api_key="k", secret_key="s", market_type="swap")
    body = htx_v5.build_swap_order_body(
        contract_code="BTC-USDT",
        volume=1,
        side="buy",
        order_price_type="opponent",
        position_side="long",
    )
    assert body.get("side") == "buy"
    assert body.get("type") == "market"
    assert body.get("position_side") == "long"
    assert "direction" not in body
    assert "order_price_type" not in body
    assert "offset" not in body
    assert "lever_rate" not in body
    v5_resp = {"status": "ok", "data": {"order_id_str": "999"}}
    with patch.object(c, "_swap_v5_request", return_value=v5_resp) as mock_v5:
        res = c._place_swap_order(body)
    assert res.exchange_order_id == "999"
    sent = mock_v5.call_args.kwargs.get("json_body") or mock_v5.call_args[1].get("json_body")
    assert sent.get("side") == "buy"
    assert sent.get("type") == "market"


def test_htx_swap_market_order_uses_mode_fallback():
    c = HtxClient(api_key="k", secret_key="s", market_type="swap")
    with patch.object(c, "get_swap_hedge_mode", return_value=False):
        with patch.object(c, "_base_to_contracts", return_value=2):
            with patch.object(c, "_place_swap_order_with_mode_fallback") as mock_place:
                mock_place.return_value = type("R", (), {"exchange_order_id": "1"})()
                c.place_market_order(symbol="DOGE/USDT", side="buy", qty=100.0)
    mock_place.assert_called_once()


def test_is_single_asset_mode_unavailable():
    assert htx_v5.is_single_asset_mode_unavailable(
        "The Single-Asset Collateral mode is temporarily unavailable."
    )
    assert not htx_v5.is_single_asset_mode_unavailable("insufficient margin")


def test_v5_request_retries_after_mode_switch():
    c = HtxClient(api_key="k", secret_key="s", market_type="swap")
    fail_raw = {"code": 400, "message": "The Single-Asset Collateral mode is temporarily unavailable."}
    ok_raw = {"code": 200, "data": {"details": [{"currency": "USDT", "available": "1", "equity": "1"}]}}
    with patch.object(c, "_swap_private_request_raw", side_effect=[fail_raw, ok_raw]):
        with patch.object(c, "_try_upgrade_to_multi_asset_mode", return_value=True):
            out = c._swap_v5_request("GET", "/v5/account/balance")
    assert htx_v5.v5_ok(out)


def test_cancel_order_swap_v5():
    c = HtxClient(api_key="k", secret_key="s", market_type="swap")
    with patch.object(c, "_swap_v5_request", return_value={"code": 200}) as mock_v5:
        c.cancel_order(symbol="BTC/USDT", order_id="123")
    mock_v5.assert_called_once()
    assert mock_v5.call_args[0][1] == "/v5/trade/cancel_order"
