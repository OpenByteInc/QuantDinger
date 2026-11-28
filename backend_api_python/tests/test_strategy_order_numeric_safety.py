from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from app.services.grid.engine import GridEngine
from app.services.pending_orders.order_budget import strategy_order_budget_snapshot
from app.services.strategy_v2 import OrderIntent
from app.services.strategy_v2.live_execution import LiveOrderRequest, StrategyV2OrderGateway
from app.services.trading_executor import TradingExecutor


BAD_NUMBERS = [float("nan"), float("inf"), float("-inf")]


def request(**overrides):
    base = LiveOrderRequest(
        strategy_id=7, strategy_run_id=42, user_id=12, symbol="BTC/USDT",
        action="open_long", quantity=0.01, reference_price=60000,
        signal_timestamp=123, market_type="swap", execution_mode="live",
    )
    return replace(base, **overrides)


@pytest.mark.parametrize("field", [
    "quantity", "reference_price", "leverage", "limit_price", "maker_wait_sec", "maker_offset_bps",
])
@pytest.mark.parametrize("bad", BAD_NUMBERS + [True, None, "invalid"])
def test_gateway_rejects_invalid_numbers_before_any_side_effect(monkeypatch, field, bad):
    fence = Mock(side_effect=AssertionError("must not reach fencing or persistence"))
    monkeypatch.setattr(StrategyV2OrderGateway, "_assert_runtime_fence", fence)
    with pytest.raises(ValueError, match="strategyV2.invalidOrderSize"):
        StrategyV2OrderGateway().submit(request(**{field: bad}))
    fence.assert_not_called()


@pytest.mark.parametrize("action", ["close_long", "close_short"])
def test_gateway_preserves_zero_quantity_close_all(action):
    order = request(action=action, quantity=0)
    assert StrategyV2OrderGateway._validate(order) == order


def test_gateway_rejects_overflowed_notional():
    with pytest.raises(ValueError, match="strategyV2.invalidOrderSize"):
        StrategyV2OrderGateway._validate(request(quantity=1e308, reference_price=2))


@pytest.mark.parametrize("values", [
    dict(quantity=1e-300, reference_price=1e-300),
    dict(quantity=2, limit_price=1e308, order_type="limit"),
    dict(order_type="limit", limit_price=0),
])
def test_gateway_rejects_invalid_limit_or_underflow(values):
    with pytest.raises(ValueError, match="strategyV2.(invalidOrderSize|limitPriceRequired)"):
        StrategyV2OrderGateway._validate(request(**values))


@pytest.mark.parametrize("field", ["current", "capital", "price", "leverage", "value"])
@pytest.mark.parametrize("bad", BAD_NUMBERS)
def test_strategy_sizing_rejects_nonfinite_inputs(field, bad):
    values = dict(current=0, capital=1000, price=60000, leverage=2, value=100)
    values[field] = bad
    intent = OrderIntent(symbol="Crypto:BTC/USDT", kind="target_value", value=values.pop("value"))
    with pytest.raises(RuntimeError, match="strategyV2.invalidOrderSize"):
        TradingExecutor._target_amount(intent, market_type="swap", **values)


@pytest.mark.parametrize("kind,value,capital,price", [
    ("target_value", 1e308, 1000, 1e-308),
    ("target_value", 1e-308, 1000, 1e308),
    ("target_percent", 1e-300, 1e-300, 60000),
    ("quantity", 1e308, 1000, 60000),
])
def test_strategy_sizing_rejects_overflow_and_underflow(kind, value, capital, price):
    intent = OrderIntent(symbol="Crypto:BTC/USDT", kind=kind, value=value)
    with pytest.raises(RuntimeError, match="strategyV2.invalidOrderSize"):
        TradingExecutor._target_amount(intent, 1e308, capital, price, market_type="spot")


@pytest.mark.parametrize("kind", ["target_value", "target_percent", "target_quantity"])
def test_zero_target_remains_a_valid_exit(kind):
    intent = OrderIntent(symbol="Crypto:BTC/USDT", kind=kind, value=0)
    assert TradingExecutor._target_amount(intent, 0.5, 1000, 60000) == 0


@pytest.mark.parametrize("bad", BAD_NUMBERS + [0, -1])
def test_invalid_live_and_bar_prices_never_generate_order(bad):
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._execute_signal = Mock(side_effect=AssertionError("must not submit"))
    key = "Crypto:BTC/USDT@okx:swap"
    with pytest.raises(RuntimeError, match="strategyV2.priceUnavailable"):
        executor._execute_strategy_v2_intent(
            strategy_id=7, strategy_name="test",
            intent=OrderIntent(symbol=key, kind="target_value", value=100),
            frames={key: pd.DataFrame({"close": [bad]})},
            candidates=[dict(key=key, market="Crypto", symbol="BTC/USDT", exchange_id="okx", market_type="swap")],
            current_price_override=bad, initial_capital=1000, leverage=1,
            execution_mode="signal", notification_config={}, trading_config={},
            exchange_config={}, signal_ts=1, strategy_run_id=42,
        )
    executor._execute_signal.assert_not_called()


@pytest.mark.parametrize("field", ["quantity", "price", "initial_capital", "leverage", "buffer_ratio"])
@pytest.mark.parametrize("bad", BAD_NUMBERS)
def test_budget_rejects_nonfinite_inputs(field, bad):
    args = dict(action="open_long", quantity=0.01, price=60000, initial_capital=1000,
                leverage=1, market_type="swap", buffer_ratio=0.02)
    args[field] = bad
    assert not strategy_order_budget_snapshot(**args)["allowed"]


def test_budget_rejects_overflowed_capacity_and_position():
    args = dict(action="open_long", quantity=1, price=1, initial_capital=1e308,
                leverage=10, market_type="swap")
    assert not strategy_order_budget_snapshot(**args)["allowed"]
    args.update(initial_capital=1000, leverage=1, current_positions=[dict(side="long", size=float("inf"))])
    assert not strategy_order_budget_snapshot(**args)["allowed"]


@pytest.mark.parametrize("amount,price,leverage", [
    (100, bad, 1) for bad in BAD_NUMBERS + [0, -1]
] + [(bad, 60000, 1) for bad in BAD_NUMBERS] + [
    (100, 60000, bad) for bad in BAD_NUMBERS
] + [(1e308, 1e-308, 10), (1e-308, 1e308, 1)])
def test_grid_invalid_conversion_produces_no_order_quantity(amount, price, leverage):
    engine = GridEngine.__new__(GridEngine)
    engine.cfg = SimpleNamespace(market_type="swap", leverage=leverage)
    assert engine._qty_from_usdt(amount, price) == 0


@pytest.mark.parametrize("field", ["qty", "price"])
@pytest.mark.parametrize("bad", BAD_NUMBERS)
def test_grid_invalid_size_is_rejected_before_client_creation(field, bad):
    engine = GridEngine.__new__(GridEngine)
    engine._create_client = Mock(side_effect=AssertionError("must not contact exchange"))
    values = dict(qty=0.01, price=60000)
    values[field] = bad
    assert engine._normalize_grid_base_qty(**values) == 0
    engine._create_client.assert_not_called()


@pytest.mark.parametrize("market,expected", [("spot", 0.01), ("swap", 0.05)])
def test_valid_grid_conversion_keeps_leverage_semantics(market, expected):
    engine = GridEngine.__new__(GridEngine)
    engine.cfg = SimpleNamespace(market_type=market, leverage=5)
    assert engine._qty_from_usdt(600, 60000) == expected


@pytest.mark.parametrize("bad", BAD_NUMBERS)
def test_grid_rejects_nonfinite_exchange_normalization(monkeypatch, bad):
    engine = GridEngine.__new__(GridEngine)
    engine.cfg = SimpleNamespace(market_type="swap")
    engine.symbol = "BTC/USDT"
    engine.exchange_config = {}
    engine._create_client = lambda: object()
    monkeypatch.setattr("app.services.grid.engine.normalize_grid_order_quantity", lambda *args, **kwargs: bad)
    assert engine._normalize_grid_base_qty(0.01, 60000) == 0
