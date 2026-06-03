from __future__ import annotations

import importlib.util
import json
import sys
from argparse import Namespace
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "exchange_smoke_test.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("exchange_smoke_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_load_cases_from_config():
    mod = _load_module()
    cfg = {
        "defaults": {
            "symbol": "BTC/USDT",
            "market_type": "spot",
            "max_notional_usdt": 3,
            "exchange_config": {"environment": "testnet"},
        },
        "exchanges": [
            {
                "name": "bitget spot",
                "exchange": "bitget",
                "api_key": "key",
                "secret_key": "secret",
                "passphrase": "pass",
                "limit_price": 100,
                "limit_quantity": 0.01,
            }
        ],
    }
    path = Path(__file__).resolve().parent / "_tmp_smoke_config.json"
    try:
        path.write_text(json.dumps(cfg), encoding="utf-8")
        cases = mod.load_cases(path, Namespace())
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    assert len(cases) == 1
    case = cases[0]
    assert case.exchange == "bitget"
    assert case.market_type == "spot"
    assert case.symbol == "BTC/USDT"
    assert case.exchange_config["exchange_id"] == "bitget"
    assert case.exchange_config["environment"] == "testnet"
    assert case.exchange_config["api_key"] == "key"
    assert case.limit_price == 100
    assert case.limit_quantity == 0.01
    assert case.max_notional_usdt == 3


def test_orders_require_cli_and_env(monkeypatch):
    mod = _load_module()

    monkeypatch.delenv("EXCHANGE_SMOKE_ALLOW_ORDERS", raising=False)
    assert mod._orders_enabled(Namespace(allow_orders=True)) is False

    monkeypatch.setenv("EXCHANGE_SMOKE_ALLOW_ORDERS", "1")
    assert mod._orders_enabled(Namespace(allow_orders=False)) is False
    assert mod._orders_enabled(Namespace(allow_orders=True)) is True


def test_order_safety_caps_notional():
    mod = _load_module()
    case = mod.SmokeCase(
        exchange="bitget",
        market_type="spot",
        symbol="BTC/USDT",
        exchange_config={"exchange_id": "bitget"},
        limit_price=100,
        limit_quantity=0.2,
        max_notional_usdt=5,
    )

    ok, reason = mod._order_safety(case)

    assert ok is False
    assert "max_notional_usdt" in reason


def test_supported_exchanges_come_from_capability_matrix():
    mod = _load_module()
    from app.services.live_trading.capabilities import supported_crypto_exchange_ids

    assert set(mod.SUPPORTED_EXCHANGES) == supported_crypto_exchange_ids()
