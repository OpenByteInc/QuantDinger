import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask, g

from app.routes import quick_trade
from app.services.live_trading.native_protection import NativeProtectionRequest
from app.services.quick_trade import orders


def test_attach_quick_trade_protection_supports_trailing_stop_alone(monkeypatch):
    """Verify that attach_quick_trade_protection does not exit when only trailing stop is specified."""
    captured_request = []

    def mock_place_native(client, request):
        captured_request.append(request)
        return [{"role": "trailing_stop", "order_id": "trail-123"}]

    monkeypatch.setattr(
        "app.services.live_trading.native_protection.place_native_protection_orders",
        mock_place_native,
    )

    client = object()
    result = orders.attach_quick_trade_protection(
        client,
        symbol="BTC/USDT",
        side="buy",
        filled_qty=0.5,
        avg_price=50000.0,
        tp_price=0.0,
        sl_price=0.0,
        market_type="swap",
        exchange_config={"product_type": "USDT-FUTURES"},
        leverage=5.0,
        margin_mode="cross",
        client_order_id="testorder123",
        trailing_stop_pct=0.02,
        trailing_activation_pct=0.05,
    )

    assert len(result) == 1
    assert result[0]["role"] == "trailing_stop"
    assert len(captured_request) == 1
    req = captured_request[0]
    assert isinstance(req, NativeProtectionRequest)
    assert req.symbol == "BTC/USDT"
    assert req.pos_side == "long"
    assert req.quantity == 0.5
    assert req.entry_price == 50000.0
    assert req.stop_loss_price == 0.0
    assert req.take_profit_price == 0.0
    assert req.trailing_stop_pct == 0.02
    assert req.trailing_activation_pct == 0.05


def test_place_order_parses_and_attaches_trailing_stop(monkeypatch):
    """Verify that place_order parses trailing_stop_pct and forwards it to attach_quick_trade_protection."""
    captured_kwargs = {}

    def mock_attach(client, **kwargs):
        captured_kwargs.update(kwargs)
        return [{"role": "trailing_stop", "order_id": "trail-999"}]

    monkeypatch.setattr(quick_trade, "attach_quick_trade_protection", mock_attach)
    monkeypatch.setattr(
        quick_trade,
        "_build_product_aware_config",
        lambda *a, **k: ({"exchange_id": "binance"}, {}),
    )
    monkeypatch.setattr(quick_trade, "configure_quick_trade_derivatives", lambda *a, **k: None)
    monkeypatch.setattr(quick_trade, "create_exchange_client", lambda *a, **k: object())
    monkeypatch.setattr(quick_trade, "_recent_strategy_conflict", lambda *a, **k: None)
    monkeypatch.setattr(quick_trade, "_convert_usdt_to_base_qty", lambda *a, **k: 0.1)
    monkeypatch.setattr(
        quick_trade,
        "normalized_submission_quantity",
        lambda *a, **k: (0.1, {}),
    )

    from app.services.live_trading import execution
    monkeypatch.setattr(
        execution,
        "place_order_from_signal",
        lambda *a, **k: SimpleNamespace(
            exchange_order_id="binance-order-1",
            filled=0.1,
            avg_price=50000.0,
            raw={},
        ),
    )
    monkeypatch.setattr(
        quick_trade,
        "enrich_fill",
        lambda *a, **k: {
            "filled": 0.1,
            "avg_price": 50000.0,
            "fee": 0.0,
            "fee_ccy": "",
            "fees_by_ccy": {},
            "fee_status": "done",
            "status": "filled",
        },
    )
    recorded_trades = {}
    monkeypatch.setattr(
        quick_trade,
        "_record_quick_trade",
        lambda **kwargs: recorded_trades.update(kwargs) or 42,
    )

    app = Flask(__name__)
    with app.test_request_context():
        g.user_id = 101
        payload = {
            "credential_id": 1,
            "symbol": "BTC/USDT",
            "side": "buy",
            "order_type": "market",
            "amount": 5000.0,
            "leverage": 5,
            "market_type": "swap",
            "trailing_stop_pct": 0.015,
            "trailing_activation_pct": 0.03,
        }
        response = inspect.unwrap(quick_trade.place_order)(payload)
        data = response.get_json()

        assert data["code"] == 1
        assert data["data"]["trade_id"] == 42
        assert data["data"]["protection_status"] == "attached"

        # Verify arguments forwarded to attach_quick_trade_protection
        assert captured_kwargs["trailing_stop_pct"] == 0.015
        assert captured_kwargs["trailing_activation_pct"] == 0.03

        # Verify metadata saved into trade record
        raw_metadata = recorded_trades["raw_result"]["_quick_trade"]
        assert raw_metadata["trailing_stop_pct"] == 0.015
        assert raw_metadata["trailing_activation_pct"] == 0.03
        assert raw_metadata["protection_status"] == "attached"
