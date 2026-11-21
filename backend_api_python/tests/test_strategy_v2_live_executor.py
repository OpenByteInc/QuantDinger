import inspect

import pandas as pd
import pytest
import app.services.trading_executor as trading_executor

from app.services.strategy_v2 import OrderIntent
from app.services.trading_executor import TradingExecutor, live_history_days


def test_load_source_upgrades_legacy_robot_allocations_at_runtime(monkeypatch):
    legacy = """AMOUNTS = [100.0, 300.0]
INITIAL_POSITION_PCT = 0.2
initial_value = sum(AMOUNTS) * INITIAL_POSITION_PCT
g.target_value += float(AMOUNTS[g.next_level] or 0.0)
"""

    class _Sources:
        @staticmethod
        def get_version(_version_id, user_id=None):
            return {"id": 109, "source_id": 9, "code": legacy}

    logs = []
    monkeypatch.setattr(trading_executor, "get_script_source_service", lambda: _Sources())
    monkeypatch.setattr(trading_executor, "append_strategy_log", lambda *args: logs.append(args))

    source_version_id, code = TradingExecutor._load_source({
        "id": 11,
        "user_id": 7,
        "template_key": "robot_v2_layered_martingale",
        "source_version_id": 109,
        "trading_config": {
            "script_source_id": 9,
            "script_source_version_id": 109,
            "executor_type": "layered_martingale",
        },
    })

    assert source_version_id == 109
    assert "AMOUNT_WEIGHTS = [0.25, 0.75]" in code
    assert "AMOUNTS" not in code
    assert logs and logs[0][0] == 11


def test_load_source_requires_the_deployment_pinned_version():
    with pytest.raises(RuntimeError, match="strategyV2.sourceVersionRequired"):
        TradingExecutor._load_source({
            "id": 11,
            "user_id": 7,
            "trading_config": {"script_source_id": 9},
        })


def test_live_history_lookback_is_frequency_aware():
    assert live_history_days("1m", 2) == 1
    assert live_history_days("4h", 100) == 50
    assert live_history_days("1d", 50) == 150


def test_live_history_lookback_covers_stock_sessions_and_weekends():
    candidates = [{"market": "Crypto", "api_family": "stock", "underlying_market": "USStock"}]

    assert live_history_days("1m", 500, candidates) == 7
    assert live_history_days("3m", 500, candidates) == 7
    assert live_history_days("5m", 5000, candidates) == 59


def test_intent_signal_timestamp_prefers_scheduled_wall_clock():
    intent = OrderIntent(
        symbol="Crypto:BTC/USDT@okx:swap",
        kind="target_percent",
        value=0.5,
        signal_time=pd.Timestamp("2026-07-19 09:35:00+08:00"),
    )

    result = TradingExecutor._intent_signal_timestamp(
        intent,
        pd.Timestamp("2026-07-18 00:00:00Z"),
    )

    assert result == int(pd.Timestamp("2026-07-19 01:35:00Z").timestamp())


def _frame(price: float = 100.0) -> pd.DataFrame:
    index = pd.DatetimeIndex([pd.Timestamp("2026-07-13T00:00:00Z")])
    return pd.DataFrame(
        {"open": [price], "high": [price], "low": [price], "close": [price], "volume": [1.0]},
        index=index,
    )


def _member() -> dict:
    return {
        "key": "Crypto:BTC/USDT@okx:swap",
        "market": "Crypto",
        "symbol": "BTC/USDT",
        "exchange_id": "okx",
        "market_type": "swap",
    }


def test_target_percent_opens_position_with_explicit_quantity():
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._get_current_positions = lambda *_args: []
    captured = {}

    def execute_signal(**kwargs):
        captured.update(kwargs)
        return True

    executor._execute_signal = execute_signal
    intent = OrderIntent(symbol=_member()["key"], kind="target_percent", value=0.25)

    result = executor._execute_strategy_v2_intent(
        strategy_id=7,
        strategy_name="V2 CTA",
        intent=intent,
        frames={_member()["key"]: _frame()},
        candidates=[_member()],
        initial_capital=10_000.0,
        leverage=2.0,
        execution_mode="signal",
        notification_config={},
        trading_config={},
        exchange_config={},
        signal_ts=1,
        strategy_run_id=42,
    )

    assert result is True
    assert captured["signal_type"] == "open_long"
    assert captured["script_base_qty"] == 50.0
    assert captured["market_type"] == "swap"
    assert captured["price_exchange_id"] == "okx"
    assert captured["strategy_run_id"] == 42


def test_equity_intent_matches_legacy_venue_suffixed_candidate_and_frame():
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._get_current_positions = lambda *_args: []
    captured = {}
    executor._execute_signal = lambda **kwargs: captured.update(kwargs) or True
    legacy_key = "USStock:DXCM@NASDAQ:spot"
    candidate = {
        "key": legacy_key,
        "market": "USStock",
        "symbol": "DXCM",
        "exchange_id": "NASDAQ",
        "market_type": "spot",
    }

    result = executor._execute_strategy_v2_intent(
        strategy_id=1099,
        strategy_name="NASDAQ portfolio",
        intent=OrderIntent(symbol="USStock:DXCM", kind="target_percent", value=0.1),
        frames={legacy_key: _frame()},
        frequency_frames={"1d": {legacy_key: _frame(101.0)}},
        candidates=[candidate],
        initial_capital=10_000.0,
        leverage=1.0,
        execution_mode="signal",
        notification_config={},
        trading_config={},
        exchange_config={},
        signal_ts=1,
        strategy_run_id=42,
    )

    assert result is True
    assert captured["symbol"] == "DXCM"
    assert captured["market_frames"]["1d"]["close"].iloc[-1] == 101.0


def test_live_target_percent_compounds_with_strategy_equity():
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._get_current_positions = lambda *_args: []
    captured = {}

    def execute_signal(**kwargs):
        captured.update(kwargs)
        return True

    executor._execute_signal = execute_signal
    intent = OrderIntent(symbol=_member()["key"], kind="target_percent", value=0.25)

    result = executor._execute_strategy_v2_intent(
        strategy_id=7,
        strategy_name="V2 CTA",
        intent=intent,
        frames={_member()["key"]: _frame()},
        candidates=[_member()],
        initial_capital=10_000.0,
        strategy_equity=12_000.0,
        leverage=2.0,
        execution_mode="live",
        notification_config={},
        trading_config={},
        exchange_config={},
        signal_ts=1,
        strategy_run_id=42,
    )

    assert result is True
    assert captured["script_base_qty"] == 60.0
    assert captured["initial_capital"] == 10_000.0
    assert captured["strategy_equity"] == 12_000.0


def test_spot_target_percent_does_not_expand_with_leverage():
    intent = OrderIntent(symbol="USStock:AAPL", kind="target_percent", value=0.25)

    target = TradingExecutor._target_amount(
        intent,
        current=0.0,
        capital=10_000.0,
        price=100.0,
        leverage=5.0,
        market_type="spot",
    )

    assert target == 25.0


def test_explicit_quantity_is_not_scaled_by_leverage():
    intent = OrderIntent(symbol=_member()["key"], kind="target_quantity", value=2.5)

    target = TradingExecutor._target_amount(
        intent,
        current=0.0,
        capital=10_000.0,
        price=100.0,
        leverage=5.0,
        market_type="swap",
    )

    assert target == 2.5


def test_direction_constraints_convert_opposite_targets_to_flat():
    assert TradingExecutor._direction_constrained_target(
        -2.0,
        direction_mode="long_only",
    ) == 0.0
    assert TradingExecutor._direction_constrained_target(
        2.0,
        direction_mode="short_only",
    ) == 0.0
    assert TradingExecutor._direction_constrained_target(
        -2.0,
        direction_mode="both",
    ) == -2.0
    assert TradingExecutor._direction_constrained_target(
        -2.0,
        direction_mode="one_way",
    ) == -2.0


def test_target_zero_closes_existing_long_position():
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._get_current_positions = lambda *_args: [{"side": "long", "size": 3.0}]
    captured = {}

    def execute_signal(**kwargs):
        captured.update(kwargs)
        return True

    executor._execute_signal = execute_signal
    intent = OrderIntent(symbol=_member()["key"], kind="target_quantity", value=0.0)

    result = executor._execute_strategy_v2_intent(
        strategy_id=8,
        strategy_name="V2 CTA",
        intent=intent,
        frames={_member()["key"]: _frame()},
        candidates=[_member()],
        initial_capital=10_000.0,
        leverage=1.0,
        execution_mode="signal",
        notification_config={},
        trading_config={},
        exchange_config={},
        signal_ts=2,
    )

    assert result is True
    assert captured["signal_type"] == "close_long"
    assert captured["script_base_qty"] == 3.0


def test_hedged_target_updates_only_the_requested_leg():
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._get_current_positions = lambda *_args: [
        {"side": "long", "size": 2.0},
        {"side": "short", "size": 5.0},
    ]
    calls = []
    executor._execute_signal = lambda **kwargs: calls.append(kwargs) or True
    intent = OrderIntent(
        symbol=_member()["key"],
        kind="target_quantity",
        value=-3.0,
        position_side="short",
    )

    result = executor._execute_strategy_v2_intent(
        strategy_id=8,
        strategy_name="V2 Neutral Grid",
        intent=intent,
        frames={_member()["key"]: _frame()},
        candidates=[_member()],
        initial_capital=10_000.0,
        leverage=1.0,
        execution_mode="signal",
        notification_config={},
        trading_config={},
        exchange_config={},
        signal_ts=2,
    )

    assert result is True
    assert len(calls) == 1
    assert calls[0]["signal_type"] == "reduce_short"
    assert calls[0]["script_base_qty"] == 2.0


def test_live_reversal_waits_for_close_fill_before_opening_opposite_side():
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._get_current_positions = lambda *_args: [{"side": "long", "size": 3.0}]
    calls = []
    executor._execute_signal = lambda **kwargs: calls.append(kwargs) or True
    intent = OrderIntent(symbol=_member()["key"], kind="target_quantity", value=-2.0)

    result = executor._execute_strategy_v2_intent(
        strategy_id=8,
        strategy_name="V2 CTA",
        intent=intent,
        frames={_member()["key"]: _frame()},
        candidates=[_member()],
        initial_capital=10_000.0,
        leverage=1.0,
        execution_mode="live",
        notification_config={},
        trading_config={},
        exchange_config={},
        signal_ts=2,
        strategy_run_id=42,
        direction_mode="both",
    )

    assert result is True
    assert [(call["signal_type"], call["script_base_qty"]) for call in calls] == [
        ("close_long", 3.0),
    ]


def test_long_only_negative_target_closes_long_without_short_entry():
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._get_current_positions = lambda *_args: [{"side": "long", "size": 3.0}]
    calls = []
    executor._execute_signal = lambda **kwargs: calls.append(kwargs) or True
    intent = OrderIntent(symbol=_member()["key"], kind="target_quantity", value=-2.0)

    result = executor._execute_strategy_v2_intent(
        strategy_id=8,
        strategy_name="V2 Long Only",
        intent=intent,
        frames={_member()["key"]: _frame()},
        candidates=[_member()],
        initial_capital=10_000.0,
        leverage=1.0,
        execution_mode="live",
        notification_config={},
        trading_config={},
        exchange_config={},
        signal_ts=2,
        strategy_run_id=42,
        direction_mode="long_only",
    )

    assert result is True
    assert [(call["signal_type"], call["script_base_qty"]) for call in calls] == [
        ("close_long", 3.0),
    ]


def test_long_only_negative_target_is_noop_after_position_is_flat():
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._get_current_positions = lambda *_args: []
    calls = []
    executor._execute_signal = lambda **kwargs: calls.append(kwargs) or True
    intent = OrderIntent(symbol=_member()["key"], kind="target_quantity", value=-2.0)

    result = executor._execute_strategy_v2_intent(
        strategy_id=8,
        strategy_name="V2 Long Only",
        intent=intent,
        frames={_member()["key"]: _frame()},
        candidates=[_member()],
        initial_capital=10_000.0,
        leverage=1.0,
        execution_mode="live",
        notification_config={},
        trading_config={},
        exchange_config={},
        signal_ts=2,
        strategy_run_id=42,
        direction_mode="long_only",
    )

    assert result is False
    assert calls == []


def test_target_rebalance_skips_sub_dollar_dust_order():
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._get_current_positions = lambda *_args: [{"side": "long", "size": 10.0}]
    calls = []
    executor._execute_signal = lambda **kwargs: calls.append(kwargs) or True
    intent = OrderIntent(symbol="USStock:AAPL", kind="target_quantity", value=10.004)
    member = {
        "key": "USStock:AAPL",
        "market": "USStock",
        "symbol": "AAPL",
        "exchange_id": "alpaca",
        "market_type": "spot",
    }

    result = executor._execute_strategy_v2_intent(
        strategy_id=9,
        strategy_name="Portfolio",
        intent=intent,
        frames={member["key"]: _frame(price=200.0)},
        candidates=[member],
        initial_capital=10_000.0,
        leverage=1.0,
        execution_mode="live",
        notification_config={},
        trading_config={},
        exchange_config={},
        signal_ts=3,
        strategy_run_id=43,
    )

    assert result is False
    assert calls == []


@pytest.mark.parametrize(
    "size, target, kind, mode, market, member_exchange, exchange_config, expected_action",
    [
        pytest.param(1e-8, 0, "target_quantity", "live", "USStock", "alpaca", {}, None, id="meta-tail"),
        pytest.param(0.009, 0, "target_quantity", "live", "USStock", "alpaca", {}, None, id="below-threshold"),
        pytest.param(0.01, 0, "target_quantity", "live", "USStock", "alpaca", {}, None, id="inclusive-threshold"),
        pytest.param(0.01, 0, "target_percent", "live", "USStock", "alpaca", {}, None, id="zero-weight"),
        pytest.param(0.01, 0, "target_value", "live", "USStock", "alpaca", {}, None, id="zero-value"),
        pytest.param(0.01, -0.01, "quantity", "live", "USStock", "alpaca", {}, None, id="sell-entire-tail"),
        pytest.param(0.01, 0, "target_quantity", "live", "USStock", "", {"exchange_id": "alpaca"}, None, id="account-exchange"),
        pytest.param(0.01, 0, "target_quantity", "live", "USStock", "", {"exchangeId": "Alpaca"}, None, id="account-exchange-alias"),
        pytest.param(0.01000001, 0, "target_quantity", "live", "USStock", "alpaca", {}, "close_long", id="above-threshold"),
        pytest.param(0.25, 0, "target_quantity", "live", "USStock", "alpaca", {}, "close_long", id="fractional-close"),
        pytest.param(0.01, 0, "target_quantity", "signal", "USStock", "alpaca", {}, "close_long", id="signal-unchanged"),
        pytest.param(0.01, 0, "target_quantity", "live", "USStock", "ibkr", {}, "close_long", id="ibkr-unchanged"),
        pytest.param(0.01, 0, "target_quantity", "live", "USStock", "alpaca", {"exchange_id": "ibkr"}, "close_long", id="account-takes-precedence"),
        pytest.param(1e-8, 0, "target_quantity", "live", "Crypto", "okx", {}, "close_long", id="crypto-unchanged"),
        pytest.param(3, 2.995, "target_quantity", "live", "USStock", "alpaca", {}, "reduce_long", id="partial-reduction-unchanged"),
        pytest.param(0, 0.005, "target_quantity", "live", "USStock", "alpaca", {}, "open_long", id="entry-unchanged"),
        pytest.param(0.005, 0.1, "target_quantity", "live", "USStock", "alpaca", {}, "add_long", id="scale-in-unchanged"),
    ],
)
def test_alpaca_residual_close_is_filtered_before_queueing(
    size, target, kind, mode, market, member_exchange, exchange_config, expected_action,
):
    executor = TradingExecutor.__new__(TradingExecutor)
    positions = [{"side": "long", "size": size}] if size else []
    executor._get_current_positions = lambda *_args: positions
    calls = []
    executor._execute_signal = lambda **kwargs: calls.append(kwargs) or True
    member = _member() if market == "Crypto" else {
        "key": "USStock:META",
        "market": "USStock",
        "symbol": "META",
        "market_type": "spot",
    }
    member["exchange_id"] = member_exchange
    intent = OrderIntent(symbol=member["key"], kind=kind, value=target)

    for signal_ts in range(1, 11):
        result = executor._execute_strategy_v2_intent(
            strategy_id=9,
            strategy_name="Portfolio",
            intent=intent,
            frames={member["key"]: _frame(price=700.0)},
            candidates=[member],
            initial_capital=1000.0,
            leverage=1.0,
            execution_mode=mode,
            notification_config={"channels": ["email"]},
            trading_config={},
            exchange_config=exchange_config,
            signal_ts=signal_ts,
            strategy_run_id=43,
        )
        assert result is (expected_action is not None)

    assert positions == ([{"side": "long", "size": size}] if size else [])
    if expected_action is None:
        assert calls == []
    else:
        assert len(calls) == 10
        assert all(call["signal_type"] == expected_action for call in calls)
        assert all(call["script_base_qty"] == pytest.approx(abs(target - size)) for call in calls)


@pytest.mark.parametrize("lease_owned", [None, True, False])
def test_live_order_carries_run_sizing_diagnostics(lease_owned):
    executor = TradingExecutor()
    guard_calls = []
    if lease_owned is not None:
        def guard(strategy_id):
            guard_calls.append(strategy_id)
            return lease_owned
        executor.runtime_guard = guard
    executor._load_strategy = lambda _strategy_id: {"user_id": 12}
    captured = {}

    class Gateway:
        def submit(self, request):
            captured["request"] = request
            return None

    executor.order_gateway = Gateway()
    result = executor._execute_signal(
        strategy_id=7,
        strategy_run_id=42,
        symbol="BTC/USDT",
        signal_type="open_long",
        script_base_qty=0.006,
        current_price=10_000.0,
        market_type="swap",
        execution_mode="live",
        leverage=2.0,
        initial_capital=100.0,
        signal_ts=4,
    )

    assert result is False
    assert guard_calls == ([] if lease_owned is None else [7])
    if lease_owned is False:
        assert captured == {}
        return
    assert captured["request"].sizing == {
        "initial_capital": 100.0,
        "entry_pct": 30.0,
        "leverage": 2.0,
        "source": "strategy_v2",
    }


def test_live_order_is_not_submitted_when_position_leg_has_inflight_work():
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._load_strategy = lambda _strategy_id: {
        "user_id": 12,
        "direction_mode": "both",
        "trading_config": {},
    }

    class Gateway:
        @staticmethod
        def has_inflight(request):
            return request.symbol == "BTC/USDT" and request.action == "open_long"

        @staticmethod
        def submit(_request):
            raise AssertionError("in-flight semantic duplicate must not be submitted")

    executor.order_gateway = Gateway()

    result = executor._execute_signal(
        strategy_id=7,
        strategy_run_id=42,
        symbol="BTC/USDT",
        signal_type="open_long",
        script_base_qty=0.006,
        current_price=10_000.0,
        market_type="swap",
        execution_mode="live",
        leverage=2.0,
        initial_capital=100.0,
        signal_ts=5,
    )

    assert result is False


def test_stopped_live_strategy_does_not_queue_remaining_callback_orders():
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._load_strategy = lambda _strategy_id: {
        "user_id": 12,
        "status": "stopped",
        "trading_config": {},
    }

    class Gateway:
        @staticmethod
        def submit(_request):
            raise AssertionError("stopped strategy must not submit another order")

    executor.order_gateway = Gateway()

    result = executor._execute_signal(
        strategy_id=7,
        strategy_run_id=42,
        symbol="SOL/USDT",
        signal_type="add_long",
        script_base_qty=0.22,
        current_price=103.0,
        market_type="swap",
        execution_mode="live",
        leverage=3.0,
        initial_capital=1_000.0,
        signal_ts=5,
    )

    assert result is False


def test_limit_queue_log_identifies_grid_level_order(monkeypatch):
    executor = TradingExecutor()
    executor._load_strategy = lambda _strategy_id: {
        "user_id": 12,
        "status": "running",
        "trading_config": {},
    }
    logs = []
    monkeypatch.setattr(
        trading_executor,
        "append_strategy_log",
        lambda *args: logs.append(args),
    )

    class Gateway:
        @staticmethod
        def submit(_request):
            return 81

    executor.order_gateway = Gateway()

    result = executor._execute_signal(
        strategy_id=7,
        strategy_run_id=42,
        symbol="SOL/USDT",
        signal_type="add_long",
        script_base_qty=0.22,
        current_price=103.0,
        market_type="swap",
        execution_mode="live",
        leverage=3.0,
        initial_capital=1_000.0,
        signal_ts=5,
        order_type="limit",
        execution_algo="limit",
        limit_price=98.7654321,
        client_order_id="grid-58-long-entry-1",
    )

    assert result is True
    assert logs
    message = logs[-1][2]
    assert "pending_id=81" in message
    assert "limit_price=98.7654321" in message
    assert "client_order_id=grid-58-long-entry-1" in message


def test_signal_mode_queue_log_is_not_presented_as_an_exchange_order(monkeypatch):
    executor = TradingExecutor()
    executor._load_strategy = lambda _strategy_id: {"user_id": 12, "trading_config": {}}
    logs = []
    monkeypatch.setattr(
        trading_executor,
        "append_strategy_log",
        lambda *args: logs.append(args),
    )

    class Gateway:
        @staticmethod
        def submit(_request):
            return 82

    executor.order_gateway = Gateway()

    result = executor._execute_signal(
        strategy_id=7,
        strategy_run_id=42,
        symbol="BTC/USDT",
        signal_type="open_long",
        script_base_qty=0.01,
        current_price=60_000.0,
        market_type="swap",
        execution_mode="signal",
        leverage=1.0,
        initial_capital=1_000.0,
        signal_ts=5,
    )

    assert result is True
    assert logs[-1][1] == "signal"
    assert logs[-1][2].startswith("Signal notification queued:")
    assert "Order queued:" not in logs[-1][2]


def test_demo_account_price_overrides_public_market_price(monkeypatch):
    from app.services.live_trading import factory

    class Client:
        def get_mark_price(self, *, symbol):
            assert symbol == "BTC/USDT"
            return 63_943.1

    monkeypatch.setattr(factory, "create_client", lambda *_args, **_kwargs: Client())
    monkeypatch.setattr(
        TradingExecutor,
        "_live_prices",
        staticmethod(lambda _candidates: {"Crypto:BTC/USDT@binance:swap": 64_294.6}),
    )
    candidates = [_member() | {"key": "Crypto:BTC/USDT@binance:swap", "exchange_id": "binance"}]

    prices = TradingExecutor._execution_account_prices(
        candidates,
        {"exchange_id": "binance", "environment": "demo"},
        {},
    )

    assert prices["Crypto:BTC/USDT@binance:swap"] == 63_943.1


def test_special_equity_price_skips_generic_crypto_quote(monkeypatch):
    from app.services.live_trading import factory

    class Client:
        def get_ticker(self, *, symbol):
            assert symbol == "NVDA/USD"
            return {"last": 210.25}

    monkeypatch.setattr(factory, "create_client", lambda *_args, **_kwargs: Client())
    monkeypatch.setattr(
        TradingExecutor,
        "_live_prices",
        staticmethod(
            lambda _candidates: (_ for _ in ()).throw(
                AssertionError("generic crypto quote must not run for Gate stocks")
            )
        ),
    )
    candidates = [{
        "market": "Crypto",
        "symbol": "NVDA/USD",
        "key": "Crypto:NVDA/USD@gate:spot",
        "exchange_id": "gate",
        "market_type": "spot",
        "instrument_id": "NVDA",
        "product_type": "direct_equity",
        "api_family": "stock",
    }]

    prices = TradingExecutor._execution_account_prices(
        candidates,
        {"exchange_id": "gate", "environment": "live"},
        {},
    )

    assert prices["Crypto:NVDA/USD@gate:spot"] == 210.25


def test_live_frame_latest_completed_bar_is_not_overwritten_by_execution_price():
    frame = _frame(price=64_294.6)
    before = frame.iloc[-1][["open", "high", "low", "close"]].tolist()

    # Signal frames come only from the completed-candle fetch path. Execution
    # account prices are passed separately to protection/equity evaluation.
    assert not hasattr(TradingExecutor, "_align_latest_frame_prices")
    assert frame.iloc[-1][["open", "high", "low", "close"]].tolist() == before == [
        64_294.6,
        64_294.6,
        64_294.6,
        64_294.6,
    ]


def test_live_loop_uses_realtime_price_hook_for_realtime_robot_templates():
    source = inspect.getsource(TradingExecutor._run_strategy_loop)

    assert ".evaluate_price_tick(" in source
    assert 'bot_type in {"martingale", "layered_martingale"}' in source
    assert ".evaluate_equity_risk(" in source
    assert ".evaluate_protections(" in source
