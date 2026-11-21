from unittest.mock import MagicMock

import pytest

from app.services import instrument_rules
from app.services.grid.exchange_orders import normalize_grid_order_quantity
from app.services.grid.engine import GridEngine
from app.services.instrument_rules import InstrumentRules
from app.services.live_trading.base import BaseRestClient
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient


def _gate_client(order_size_min="1", multiplier="0.0001"):
    client = GateUsdtFuturesClient.__new__(GateUsdtFuturesClient)
    client.get_contract = lambda **_kwargs: {
        "order_size_min": order_size_min,
        "quanto_multiplier": multiplier,
    }
    return client


def test_gate_futures_never_rounds_sub_minimum_quantity_up():
    client = _gate_client()

    size, headers = client._resolve_order_size(
        contract="BTC_USDT",
        side="sell",
        base_size=0.00005,
    )

    assert size == "0"
    assert headers is None


def test_gate_grid_quantity_is_floored_and_returned_in_base_units():
    client = _gate_client()

    assert normalize_grid_order_quantity(
        client,
        symbol="BTC/USDT",
        quantity=0.00039,
        market_type="swap",
    ) == 0.0003
    assert normalize_grid_order_quantity(
        client,
        symbol="BTC/USDT",
        quantity=0.00005,
        market_type="swap",
    ) == 0.0


def test_htx_futures_never_rounds_sub_contract_quantity_up():
    client = HtxClient.__new__(HtxClient)
    client.get_contract_info = lambda **_kwargs: {"contract_size": "0.001"}

    assert client._base_to_contracts(symbol="BTC/USDT", qty=0.0005) == 0


def test_gate_spot_grid_rejects_float_dust_and_floors_native_precision():
    client = GateSpotClient.__new__(GateSpotClient)
    client.get_currency_pair = lambda **_kwargs: {
        "amount_precision": 6,
        "precision": 2,
        "min_base_amount": "0.0001",
        "min_quote_amount": "1",
    }

    assert normalize_grid_order_quantity(
        client,
        symbol="BTC/USDT",
        quantity=3.3532799999999997e-07,
        market_type="spot",
        price=100_000,
    ) == 0.0
    assert normalize_grid_order_quantity(
        client,
        symbol="BTC/USDT",
        quantity=0.0001239,
        market_type="spot",
        price=100_000,
    ) == pytest.approx(0.000123)


def test_htx_spot_grid_rejects_float_dust_and_minimum_notional():
    client = HtxClient.__new__(HtxClient)
    client.get_spot_symbol_info = lambda **_kwargs: {
        "amount-precision": 6,
        "price-precision": 2,
        "min-order-amt": "0.0001",
        "min-order-value": "1",
    }

    assert normalize_grid_order_quantity(
        client,
        symbol="BTC/USDT",
        quantity=3.3532799999999997e-07,
        market_type="spot",
        price=100_000,
    ) == 0.0
    assert normalize_grid_order_quantity(
        client,
        symbol="BTC/USDT",
        quantity=0.0001,
        market_type="spot",
        price=5_000,
    ) == 0.0


@pytest.mark.parametrize("exchange_id", ["binance", "okx", "bitget", "bybit", "gate", "htx"])
@pytest.mark.parametrize("market_type", ["spot", "swap"])
def test_all_grid_exchanges_apply_common_minimum_rules(
    monkeypatch,
    exchange_id,
    market_type,
):
    rules = InstrumentRules(
        key=f"Crypto:BTC/USDT@{exchange_id}:{market_type}",
        exchange_id=exchange_id,
        market_type=market_type,
        symbol="BTC/USDT",
        amount_step=0.001,
        min_amount=0.01,
        min_notional=5.0,
    )

    class FakeProvider:
        def get_rules(self, *_args, **_kwargs):
            return rules

    monkeypatch.setattr(instrument_rules, "get_instrument_rules_provider", lambda: FakeProvider())
    client = BaseRestClient("https://example.invalid")

    assert normalize_grid_order_quantity(
        client,
        symbol="BTC/USDT",
        quantity=0.0099,
        market_type=market_type,
        exchange_config={"exchange_id": exchange_id},
        price=1_000,
    ) == 0.0
    assert normalize_grid_order_quantity(
        client,
        symbol="BTC/USDT",
        quantity=0.0129,
        market_type=market_type,
        exchange_config={"exchange_id": exchange_id},
        price=100,
    ) == 0.0
    assert normalize_grid_order_quantity(
        client,
        symbol="BTC/USDT",
        quantity=0.1239,
        market_type=market_type,
        exchange_config={"exchange_id": exchange_id},
        price=100,
    ) == pytest.approx(0.123)


def _startup_engine(monkeypatch, rules, *, budget, leverage=1, lower=100, upper=110):
    monkeypatch.setattr("app.services.grid.engine.GridRestingOrderRepository", MagicMock())
    monkeypatch.setattr("app.services.grid.engine.GridCellRepository", MagicMock())
    provider = MagicMock()
    provider.get_rules.return_value = rules
    monkeypatch.setattr(instrument_rules, "get_instrument_rules_provider", lambda: provider)
    client = BaseRestClient("https://example.invalid")
    engine = GridEngine(
        91, rules.symbol,
        {
            "market_type": rules.market_type, "leverage": leverage,
            "bot_params": {
                "lowerPrice": lower, "upperPrice": upper, "gridCount": 2,
                "gridCountUnit": "lines", "amountPerGrid": budget,
            },
        },
        {"exchange_id": rules.exchange_id},
        create_client_fn=lambda: client, enqueue_market=MagicMock(),
    )
    return engine, client


@pytest.mark.parametrize("exchange_id", ["binance", "okx", "bitget", "bybit", "gate", "htx"])
@pytest.mark.parametrize("market_type", ["spot", "swap"])
def test_grid_startup_checks_paired_exit_notional(monkeypatch, exchange_id, market_type):
    rules = InstrumentRules(
        key="test-pair", exchange_id=exchange_id, market_type=market_type,
        symbol="BTC/USDT", amount_step=0.01, min_amount=0.01, min_notional=5.5,
    )
    engine, client = _startup_engine(monkeypatch, rules, budget=6)
    assert normalize_grid_order_quantity(
        client, symbol=rules.symbol, quantity=6 / 110, market_type=market_type,
        exchange_config=engine.exchange_config, price=110,
    ) == pytest.approx(0.05)

    ok, detail = engine.validate_startup_order_sizes(client)

    assert not ok
    assert "price=110 paired_price=100" in detail
    assert "min_notional=5.5" in detail
    engine, client = _startup_engine(monkeypatch, rules, budget=7)
    assert engine.validate_startup_order_sizes(client) == (True, "")


@pytest.mark.parametrize("market_type, expected", [("spot", False), ("swap", True)])
def test_grid_startup_applies_leverage_only_to_swaps(monkeypatch, market_type, expected):
    rules = InstrumentRules(
        key="test-leverage", exchange_id="binance", market_type=market_type,
        symbol="BTC/USDT", amount_step=0.01, min_amount=0.01, min_notional=5.5,
    )
    engine, client = _startup_engine(monkeypatch, rules, budget=0.7, leverage=10)

    assert engine.validate_startup_order_sizes(client)[0] is expected


@pytest.mark.parametrize("price_tick, expected", [(0.01, True), (1, False)])
def test_grid_startup_checks_notional_at_tick_normalized_price(monkeypatch, price_tick, expected):
    rules = InstrumentRules(
        key="test-price", exchange_id="binance", market_type="spot",
        symbol="BTC/USDT", amount_step=0.001, min_amount=0.001,
        min_notional=5.0005, price_tick=price_tick,
    )
    engine, client = _startup_engine(monkeypatch, rules, budget=5.55, lower=100.09, upper=110.09)

    assert engine.validate_startup_order_sizes(client)[0] is expected
