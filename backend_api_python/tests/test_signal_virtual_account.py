from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.services import pending_order_worker as worker_module
from app.services.virtual_trading import (
    calculate_virtual_fill,
    calculate_virtual_limit_fill_price,
    execute_virtual_signal_order,
    mark_virtual_positions,
    match_virtual_limit_orders,
)
from app.services.virtual_execution_costs import (
    VIRTUAL_COMMISSION_RATE,
    VIRTUAL_SLIPPAGE_RATE,
    resolve_virtual_execution_cost_policy,
)


def test_virtual_fill_opens_adds_and_closes_long_position():
    opened = calculate_virtual_fill(
        action="open_long",
        requested_quantity=2,
        fill_price=100,
    )
    assert opened.status == "filled"
    assert opened.next_size == pytest.approx(2)
    assert opened.next_entry_price == pytest.approx(100)

    added = calculate_virtual_fill(
        action="add_long",
        requested_quantity=1,
        fill_price=130,
        current_size=opened.next_size,
        current_entry_price=opened.next_entry_price,
    )
    assert added.next_size == pytest.approx(3)
    assert added.next_entry_price == pytest.approx(110)

    closed = calculate_virtual_fill(
        action="close_long",
        requested_quantity=0,
        fill_price=120,
        current_size=added.next_size,
        current_entry_price=added.next_entry_price,
    )
    assert closed.fill_quantity == pytest.approx(3)
    assert closed.next_size == pytest.approx(0)
    assert closed.gross_realized_pnl == pytest.approx(30)


def test_virtual_fill_short_pnl_and_over_reduce_are_bounded():
    reduced = calculate_virtual_fill(
        action="reduce_short",
        requested_quantity=10,
        fill_price=80,
        current_size=2,
        current_entry_price=100,
    )
    assert reduced.fill_quantity == pytest.approx(2)
    assert reduced.next_size == pytest.approx(0)
    assert reduced.gross_realized_pnl == pytest.approx(40)


def test_virtual_fill_without_position_is_not_fabricated():
    result = calculate_virtual_fill(
        action="close_long",
        requested_quantity=0,
        fill_price=100,
    )
    assert result.status == "no_position"
    assert result.fill_quantity == 0
    assert result.gross_realized_pnl == 0


def test_virtual_limit_order_waits_for_touch_and_never_fills_worse_than_limit():
    assert calculate_virtual_limit_fill_price(
        action="open_long", limit_price=100, market_price=101, slippage_rate=0.0005,
    ) is None
    assert calculate_virtual_limit_fill_price(
        action="open_long", limit_price=100, market_price=99, slippage_rate=0.0005,
    ) == pytest.approx(99.0495)
    assert calculate_virtual_limit_fill_price(
        action="open_long", limit_price=100, market_price=100, slippage_rate=0.0005,
    ) == pytest.approx(100)
    assert calculate_virtual_limit_fill_price(
        action="close_long", limit_price=110, market_price=109, slippage_rate=0.0005,
    ) is None
    assert calculate_virtual_limit_fill_price(
        action="close_long", limit_price=110, market_price=111, slippage_rate=0.0005,
    ) == pytest.approx(110.9445)


def test_virtual_limit_matcher_uses_fresh_price_and_preserves_limit(monkeypatch):
    from app.services import virtual_trading

    row = {
        "id": 8,
        "user_id": 7,
        "strategy_id": 3,
        "strategy_run_id": 4,
        "pending_order_id": 14,
        "order_intent_id": 22,
        "symbol": "Crypto:BTC/USDT@spot",
        "action": "open_long",
        "requested_qty": 2,
        "limit_price": 95,
        "slippage_rate": 0.0005,
        "payload_json": "{}",
    }

    class Cursor:
        def execute(self, *args, **kwargs):
            return None

        def fetchall(self):
            return [row]

        def close(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connection():
        yield Connection()

    executions = []
    monkeypatch.setattr(virtual_trading, "get_db_connection", connection)
    monkeypatch.setattr(
        virtual_trading,
        "execute_virtual_signal_order",
        lambda order, payload: executions.append((order, payload)) or {"status": "filled"},
    )

    assert match_virtual_limit_orders(3, {"Crypto:BTC/USDT@spot": 96}, strategy_run_id=4) == []
    matched = match_virtual_limit_orders(3, {"Crypto:BTC/USDT@spot": 94}, strategy_run_id=4)

    assert matched == [{"status": "filled"}]
    assert executions[0][1]["ref_price"] == pytest.approx(94)
    assert executions[0][1]["_virtual_fill_price"] == pytest.approx(94.047)
    assert executions[0][1]["_virtual_fill_price"] <= row["limit_price"]


def test_virtual_position_mark_uses_strategy_scoped_price_and_updates_short_pnl(monkeypatch):
    from app.services import virtual_trading

    updates = []

    class Cursor:
        rowcount = 0

        def execute(self, query, params=()):
            if "SELECT id, symbol_canonical" in query:
                self.rowcount = 0
            elif "UPDATE qd_strategy_virtual_positions" in query:
                updates.append(params)
                self.rowcount = 1

        def fetchall(self):
            return [{
                "id": 17,
                "symbol_canonical": "BTC/USDT",
                "side": "short",
                "size": 2,
                "entry_price": 100,
            }]

        def close(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            return None

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr(virtual_trading, "get_db_connection", connection)

    count = mark_virtual_positions(
        9,
        {"Crypto:BTC/USDT@binance:swap": 90},
        strategy_run_id=12,
    )

    assert count == 1
    assert updates[0][0] == pytest.approx(90)
    assert updates[0][4] == pytest.approx(20)
    assert updates[0][5] == pytest.approx(10)


def test_virtual_position_mark_rejects_ambiguous_cross_venue_prices(monkeypatch):
    from app.services import virtual_trading

    monkeypatch.setattr(
        virtual_trading,
        "get_db_connection",
        lambda: pytest.fail("ambiguous venue prices must not reach the position ledger"),
    )

    count = mark_virtual_positions(
        9,
        {
            "Crypto:BTC/USDT@binance:swap": 90,
            "Crypto:BTC/USDT@okx:swap": 91,
        },
        strategy_run_id=12,
    )

    assert count == 0


@pytest.mark.parametrize(
    ("exchange_id", "market_type"),
    [
        ("", "spot"),
        ("", "swap"),
        ("binance", "spot"),
        ("okx", "swap"),
        ("bybit", "spot"),
        ("bitget", "swap"),
        ("gate", "spot"),
        ("htx", "swap"),
    ],
)
def test_virtual_cost_policy_uses_fixed_rate_independent_of_venue_and_product(
    exchange_id,
    market_type,
):
    policy = resolve_virtual_execution_cost_policy(
        payload={"exchange_id": exchange_id, "market_type": market_type, "leverage": 10},
        strategy={"market_category": "Crypto"},
    )

    assert policy.commission_rate == pytest.approx(VIRTUAL_COMMISSION_RATE)
    assert policy.slippage_rate == pytest.approx(VIRTUAL_SLIPPAGE_RATE)
    assert policy.leverage == pytest.approx(10)


def test_virtual_fee_uses_executed_notional_without_double_counting_leverage():
    policy = resolve_virtual_execution_cost_policy(
        payload={"exchange_id": "binance", "market_type": "swap", "leverage": 10},
        strategy={"market_category": "Crypto"},
    )

    assert policy.commission_for(quantity=0.1, fill_price=100_000) == pytest.approx(5)
    assert policy.slippage_quote_for(
        quantity=0.1,
        reference_price=100_000,
        fill_price=100_050,
    ) == pytest.approx(5)


def test_virtual_cost_policy_does_not_invent_a_venue_when_strategy_has_none():
    policy = resolve_virtual_execution_cost_policy(
        strategy={"market_category": "Crypto", "market_type": "swap", "leverage": 3},
    )

    assert policy.exchange_id == ""
    assert policy.commission_rate == pytest.approx(VIRTUAL_COMMISSION_RATE)
    assert policy.leverage == pytest.approx(3)


def test_virtual_execution_rejects_live_mode_before_database_access():
    with pytest.raises(ValueError, match="virtualTrading.signalModeRequired"):
        execute_virtual_signal_order(
            {"id": 1, "user_id": 2, "strategy_id": 3, "execution_mode": "live"},
            {"execution_mode": "live"},
        )


def test_signal_dispatch_fills_virtual_account_without_live_execution(monkeypatch):
    from app.services import virtual_trading
    from app.services.strategy_runtime import cancellations

    virtual_calls = []
    sent = []
    monkeypatch.setattr(cancellations, "intercept_cancelled_dispatch", lambda row: False)
    monkeypatch.setattr(worker_module, "load_strategy_configs", lambda strategy_id: {"execution_mode": "signal"})
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        virtual_trading,
        "execute_virtual_signal_order",
        lambda row, payload: virtual_calls.append((row, payload)) or {
            "virtual_order_id": 91,
            "status": "filled",
            "fill_quantity": 2,
            "fill_price": 101,
        },
    )

    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._notifier = SimpleNamespace(notify_signal=lambda **kwargs: {"browser": {"ok": True}})
    worker._load_strategy_name = lambda strategy_id: "Test"
    worker._load_notification_config = lambda strategy_id: {"browser": True}
    worker._mark_sent = lambda **kwargs: sent.append(kwargs)
    worker._mark_failed = lambda **kwargs: pytest.fail(str(kwargs))
    worker._execute_live_order = lambda **kwargs: pytest.fail("signal mode reached live execution")

    worker._dispatch_one({
        "id": 12,
        "user_id": 7,
        "strategy_id": 3,
        "strategy_run_id": 4,
        "execution_mode": "signal",
        "symbol": "BTC/USDT",
        "signal_type": "open_long",
        "amount": 2,
        "price": 100,
        "payload_json": (
            '{"strategy_id":3,"strategy_run_id":4,"execution_mode":"signal",'
            '"symbol":"BTC/USDT","signal_type":"open_long","amount":2,"price":100}'
        ),
    })

    assert len(virtual_calls) == 1
    assert sent[0]["final_filled"] is True
    assert sent[0]["filled"] == pytest.approx(2)
    assert sent[0]["avg_price"] == pytest.approx(101)


def test_live_dispatch_never_calls_virtual_account(monkeypatch):
    from app.services import virtual_trading
    from app.services.strategy_runtime import cancellations

    live_calls = []
    monkeypatch.setattr(cancellations, "intercept_cancelled_dispatch", lambda row: False)
    monkeypatch.setattr(worker_module, "load_strategy_configs", lambda strategy_id: {"execution_mode": "live"})
    monkeypatch.setattr(
        virtual_trading,
        "execute_virtual_signal_order",
        lambda *args, **kwargs: pytest.fail("live mode reached virtual execution"),
    )
    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._execute_live_order = lambda **kwargs: live_calls.append(kwargs)

    worker._dispatch_one({
        "id": 13,
        "user_id": 7,
        "strategy_id": 3,
        "execution_mode": "live",
        "symbol": "BTC/USDT",
        "signal_type": "open_long",
        "amount": 2,
        "price": 100,
        "payload_json": "{}",
    })

    assert len(live_calls) == 1


def test_signal_order_is_rejected_when_strategy_is_live_and_never_calls_live_executor(monkeypatch):
    from app.services import virtual_trading
    from app.services.strategy_runtime import cancellations

    live_calls = []
    virtual_calls = []
    failures = []
    monkeypatch.setattr(cancellations, "intercept_cancelled_dispatch", lambda row: False)
    monkeypatch.setattr(worker_module, "load_strategy_configs", lambda strategy_id: {"execution_mode": "live"})
    monkeypatch.setattr(
        virtual_trading,
        "execute_virtual_signal_order",
        lambda *args, **kwargs: virtual_calls.append((args, kwargs)),
    )

    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._load_strategy_name = lambda strategy_id: "Test"
    worker._mark_failed = lambda **kwargs: failures.append(kwargs)
    worker._execute_live_order = lambda **kwargs: live_calls.append(kwargs)

    worker._dispatch_one({
        "id": 15,
        "user_id": 7,
        "strategy_id": 3,
        "execution_mode": "signal",
        "symbol": "BTC/USDT",
        "signal_type": "open_long",
        "amount": 2,
        "price": 100,
        "payload_json": '{"strategy_id":3,"execution_mode":"signal"}',
    })

    assert live_calls == []
    assert virtual_calls == []
    assert failures and "execution_mode_mismatch" in failures[0]["error"]


def test_order_payload_mode_mismatch_is_rejected_before_either_dispatch_path(monkeypatch):
    from app.services import virtual_trading
    from app.services.strategy_runtime import cancellations

    live_calls = []
    virtual_calls = []
    failures = []
    monkeypatch.setattr(cancellations, "intercept_cancelled_dispatch", lambda row: False)
    monkeypatch.setattr(worker_module, "load_strategy_configs", lambda strategy_id: {"execution_mode": "signal"})
    monkeypatch.setattr(
        virtual_trading,
        "execute_virtual_signal_order",
        lambda *args, **kwargs: virtual_calls.append((args, kwargs)),
    )

    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._load_strategy_name = lambda strategy_id: "Test"
    worker._mark_failed = lambda **kwargs: failures.append(kwargs)
    worker._execute_live_order = lambda **kwargs: live_calls.append(kwargs)

    worker._dispatch_one({
        "id": 16,
        "user_id": 7,
        "strategy_id": 3,
        "execution_mode": "signal",
        "symbol": "BTC/USDT",
        "signal_type": "open_long",
        "amount": 2,
        "price": 100,
        "payload_json": '{"strategy_id":3,"execution_mode":"live"}',
    })

    assert live_calls == []
    assert virtual_calls == []
    assert failures and "execution_mode_mismatch" in failures[0]["error"]


def test_live_order_is_rejected_when_stored_strategy_is_signal(monkeypatch):
    from app.services import virtual_trading
    from app.services.strategy_runtime import cancellations

    live_calls = []
    virtual_calls = []
    failures = []
    monkeypatch.setattr(cancellations, "intercept_cancelled_dispatch", lambda row: False)
    monkeypatch.setattr(worker_module, "load_strategy_configs", lambda strategy_id: {"execution_mode": "signal"})
    monkeypatch.setattr(
        virtual_trading,
        "execute_virtual_signal_order",
        lambda *args, **kwargs: virtual_calls.append((args, kwargs)),
    )

    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._load_strategy_name = lambda strategy_id: "Test"
    worker._mark_failed = lambda **kwargs: failures.append(kwargs)
    worker._execute_live_order = lambda **kwargs: live_calls.append(kwargs)

    worker._dispatch_one({
        "id": 17,
        "user_id": 7,
        "strategy_id": 3,
        "execution_mode": "live",
        "symbol": "BTC/USDT",
        "signal_type": "open_long",
        "amount": 2,
        "price": 100,
        "payload_json": '{"strategy_id":3,"execution_mode":"live"}',
    })

    assert live_calls == []
    assert virtual_calls == []
    assert failures and "execution_mode_mismatch" in failures[0]["error"]


def test_signal_limit_dispatch_keeps_virtual_order_open(monkeypatch):
    from app.services import virtual_trading
    from app.services.strategy_runtime import cancellations

    sent = []
    monkeypatch.setattr(cancellations, "intercept_cancelled_dispatch", lambda row: False)
    monkeypatch.setattr(worker_module, "load_strategy_configs", lambda strategy_id: {"execution_mode": "signal"})
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        virtual_trading,
        "execute_virtual_signal_order",
        lambda row, payload: {
            "virtual_order_id": 92,
            "status": "open",
            "fill_quantity": 0,
            "fill_price": 0,
            "limit_price": 95,
        },
    )

    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._notifier = SimpleNamespace(notify_signal=lambda **kwargs: {"browser": {"ok": True}})
    worker._load_strategy_name = lambda strategy_id: "Grid"
    worker._load_notification_config = lambda strategy_id: {"browser": True}
    worker._mark_sent = lambda **kwargs: sent.append(kwargs)
    worker._mark_failed = lambda **kwargs: pytest.fail(str(kwargs))

    worker._dispatch_one({
        "id": 14,
        "user_id": 7,
        "strategy_id": 3,
        "strategy_run_id": 4,
        "execution_mode": "signal",
        "symbol": "BTC/USDT",
        "signal_type": "open_long",
        "order_type": "limit",
        "amount": 2,
        "price": 95,
        "payload_json": (
            '{"strategy_id":3,"strategy_run_id":4,"execution_mode":"signal",'
            '"symbol":"BTC/USDT","signal_type":"open_long","order_type":"limit",'
            '"amount":2,"price":95,"limit_price":95,"ref_price":100}'
        ),
    })

    assert sent[0]["final_filled"] is False
    assert sent[0]["filled"] == 0
    assert sent[0]["executed_at"] is None
