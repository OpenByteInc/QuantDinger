"""Contract tests for security-sensitive human API mutations."""

import pytest
from marshmallow import ValidationError

from app.openapi.schemas.high_risk import (
    CredentialCreateRequestSchema,
    QuickTradeCloseRequestSchema,
    QuickTradeOrderRequestSchema,
)


HIGH_RISK_REQUESTS = (
    ("/api/auth/login", "post"),
    ("/api/auth/register", "post"),
    ("/api/auth/reset-password", "post"),
    ("/api/auth/change-password", "post"),
    ("/api/strategies/{strategy_id}/start", "post"),
    ("/api/strategies/{strategy_id}/stop", "post"),
    ("/api/strategies/{strategy_id}", "delete"),
    ("/api/credentials/create", "post"),
    ("/api/credentials/delete", "delete"),
    ("/api/billing/usdt/create", "post"),
    ("/api/quick-trade/place-order", "post"),
    ("/api/quick-trade/close-position", "post"),
)


def test_high_risk_mutations_have_typed_requests(app):
    from app.openapi import get_openapi_api
    from app.openapi.register import enrich_spec

    api = get_openapi_api(app)
    with app.app_context():
        paths = enrich_spec(api.spec.to_dict())["paths"]

    for path, method in HIGH_RISK_REQUESTS:
        operation = paths[path][method]
        assert "requestBody" in operation or operation.get("parameters"), path


def test_login_validation_uses_human_error_envelope(client):
    response = client.post("/api/auth/login", json={"username": "demo"})

    assert response.status_code == 400
    assert response.get_json() == {
        "code": 0,
        "msg": "Invalid request data",
        "data": {"errors": {"json": {"password": ["Missing data for required field."]}}},
    }


def test_quick_trade_contract_normalizes_legacy_values():
    loaded = QuickTradeOrderRequestSchema().load(
        {
            "credential_id": 7,
            "symbol": "BTC/USDT",
            "side": "BUY",
            "order_type": "LIMIT",
            "amount": "50.5",
            "price": "60000",
            "reference_price": "60100",
            "reference_price_at_ms": "1234567890",
            "market_type": "PERP",
            "marginMode": "ISOLATED",
        }
    )

    assert loaded["side"] == "buy"
    assert loaded["order_type"] == "limit"
    assert loaded["amount"] == 50.5
    assert loaded["reference_price"] == 60100
    assert loaded["reference_price_at_ms"] == 1234567890
    assert loaded["market_type"] == "perp"
    assert loaded["marginMode"] == "isolated"


def test_quick_trade_contract_accepts_exact_spot_sell_quantity():
    loaded = QuickTradeOrderRequestSchema().load(
        {
            "credential_id": 7,
            "symbol": "BTC/USDT",
            "side": "SELL",
            "order_type": "MARKET",
            "quantity": "0.001",
            "market_type": "SPOT",
        }
    )

    assert loaded["amount"] == 0
    assert loaded["quantity"] == 0.001
    assert loaded["side"] == "sell"


def test_quick_trade_contract_accepts_exact_spot_buy_quantity():
    loaded = QuickTradeOrderRequestSchema().load(
        {
            "credential_id": 7,
            "symbol": "BTC/USDT",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": "0.001",
            "market_type": "SPOT",
        }
    )

    assert loaded["amount"] == 0
    assert loaded["quantity"] == 0.001
    assert loaded["side"] == "buy"


def test_quick_trade_contract_preserves_logical_asset_identity():
    order = QuickTradeOrderRequestSchema().load(
        {
            "asset_id": 11,
            "credential_id": 7,
            "symbol": "BTC/USDT",
            "side": "BUY",
            "order_type": "MARKET",
            "amount": "100",
            "market_type": "SWAP",
            "resolve_product": True,
        }
    )
    close = QuickTradeCloseRequestSchema().load(
        {
            "asset_id": 11,
            "credential_id": 7,
            "symbol": "BTC/USDT",
            "market_type": "SWAP",
            "size": "0.122",
            "position_side": "LONG",
            "resolve_product": True,
        }
    )

    assert order["asset_id"] == 11
    assert close["asset_id"] == 11
    assert close["position_side"] == "long"


def test_quick_trade_contract_rejects_ambiguous_spot_sizing():
    with pytest.raises(ValidationError):
        QuickTradeOrderRequestSchema().load(
            {
                "credential_id": 7,
                "symbol": "BTC/USDT",
                "side": "BUY",
                "order_type": "MARKET",
                "amount": "100",
                "quantity": "0.001",
                "market_type": "SPOT",
            }
        )


def test_credential_contract_requires_secrets_except_ibkr():
    with pytest.raises(ValidationError):
        CredentialCreateRequestSchema().load({"exchange_id": "binance"})

    loaded = CredentialCreateRequestSchema().load(
        {"exchange_id": "IBKR", "ibkr_port": 7497}
    )
    assert loaded["exchange_id"] == "ibkr"
