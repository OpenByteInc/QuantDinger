from importlib import import_module

from flask import Flask


def test_symbol_capabilities_route_does_not_expose_exception_details(monkeypatch):
    route = import_module("app.routes.market")
    app = Flask(__name__)
    secret_detail = "database password appeared in a stack trace"
    monkeypatch.setattr(
        route,
        "get_crypto_symbol_capabilities",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret_detail)),
    )

    with app.test_request_context(
        "/api/market/symbols/capabilities?market=Crypto&symbol=BTC%2FUSDT&exchange_id=binance"
    ):
        result = route.get_symbol_capabilities()

    if isinstance(result, tuple):
        response, status = result
    else:
        response, status = result, result.status_code

    payload = response.get_json()
    assert status == 500
    assert payload == {
        "code": 0,
        "msg": "internal_server_error",
        "data": None,
    }
    assert secret_detail not in response.get_data(as_text=True)


def test_asset_capabilities_route_does_not_expose_resolution_details(monkeypatch):
    route = import_module("app.routes.market")
    app = Flask(__name__)
    secret_detail = "database password appeared in asset resolution"
    monkeypatch.setattr(
        route,
        "get_asset_capabilities",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            route.AssetResolutionError(secret_detail)
        ),
    )

    with app.test_request_context("/api/market/assets/capabilities?asset_id=7"):
        result = route.asset_capabilities()

    if isinstance(result, tuple):
        response, status = result
    else:
        response, status = result, result.status_code

    payload = response.get_json()
    assert status == 404
    assert payload == {
        "code": 0,
        "msg": "market.assetNotFound",
        "data": None,
    }
    assert secret_detail not in response.get_data(as_text=True)
