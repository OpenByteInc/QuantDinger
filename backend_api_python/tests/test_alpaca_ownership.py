"""Alpaca inventory protection without broker submissions or business data."""

from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from app.services.live_trading import alpaca_ownership as guard
from app.services.live_trading.position_ownership import calculate_position_ownership


MANUAL = 907.768327


@pytest.fixture
def scenario(monkeypatch):
    state = SimpleNamespace(account=MANUAL + 10, own=10, other=0, protected=MANUAL,
                            orders=[], side="long", mode="advanced", allowed=True)
    monkeypatch.setattr(guard, "ensure_alpaca_settled", lambda **kw: None)

    def allocations(**kw):
        assert kw["credential_id"] == 33
        assert kw["exchange_id"] == "alpaca"
        return [dict(strategy_id=1, symbol="NVDA", side=state.side, size=state.own),
                dict(strategy_id=2, symbol="NVDA", side=state.side, size=state.other)]

    def ownership(**kw):
        return calculate_position_ownership(
            symbol=kw["symbol"], side=kw["side"], account_qty=kw["account_qty"],
            strategy_qty=kw["strategy_qty"], protected_qty=state.protected,
            coexistence_mode=state.mode,
        )

    monkeypatch.setattr(guard, "list_strategy_allocations_for_account", allocations)
    monkeypatch.setattr(guard, "evaluate_and_record_ownership", ownership)

    def positions(**kw):
        assert kw["raise_on_error"] is True
        return [dict(symbol="NVDA", quantity=state.account, side=state.side)]

    def orders(**kw):
        assert kw["raise_on_error"] is True
        return state.orders

    state.client = SimpleNamespace(get_positions=positions, get_orders=orders)
    state.kwargs = dict(client=state.client, strategy_id=1, user_id=7, credential_id=33,
                        symbol="NVDA", signal_type="close_long", amount=1000, order_id=88)
    return state


def test_original_nvda_inventory_survives_oversized_exit(scenario):
    qty = guard.guarded_alpaca_quantity(**scenario.kwargs)
    assert qty == pytest.approx(10)
    assert scenario.account - qty == pytest.approx(MANUAL)


def test_exit_reserves_other_strategies_during_real_shortfall(scenario):
    scenario.account = 12
    scenario.other = 4
    assert guard.guarded_alpaca_quantity(**scenario.kwargs) == pytest.approx(8)


@pytest.mark.parametrize("signal", ["close_long", "reduce_long", "close_long_stop", "close_long_profit", "close_long_trailing"])
def test_all_exit_variants_close_only_strategy_inventory(scenario, signal):
    scenario.kwargs["signal_type"] = signal
    scenario.account = MANUAL + 0.125
    assert guard.guarded_alpaca_quantity(**scenario.kwargs) == pytest.approx(10)


@pytest.mark.parametrize("account,own", [(MANUAL, 0), (0, 10)])
def test_no_available_strategy_inventory_rejects(scenario, account, own):
    scenario.account, scenario.own = account, own
    with pytest.raises(ValueError, match="noStrategyInventory"):
        guard.guarded_alpaca_quantity(**scenario.kwargs)


def test_same_side_entry_allowed_after_manual_protection(scenario):
    scenario.kwargs.update(signal_type="open_long", amount=2)
    assert guard.guarded_alpaca_quantity(**scenario.kwargs) == 2


def test_manual_inventory_does_not_require_registration_before_entry(scenario):
    scenario.kwargs["signal_type"] = "open_long"
    scenario.mode = "strict"
    scenario.protected = 0
    assert guard.guarded_alpaca_quantity(**scenario.kwargs) == 1000


@pytest.mark.parametrize("side,signal", [("long", "open_short"), ("long", "add_short"), ("short", "open_long")])
def test_entry_cannot_net_away_opposite_inventory(scenario, side, signal):
    scenario.side = side
    scenario.kwargs["signal_type"] = signal
    with pytest.raises(ValueError, match="oppositeInventory"):
        guard.guarded_alpaca_quantity(**scenario.kwargs)


def test_negative_broker_quantity_can_cover_only_owned_short(scenario):
    scenario.side = "short"
    scenario.account = -(MANUAL + 10)
    scenario.kwargs["signal_type"] = "close_short"
    assert guard.guarded_alpaca_quantity(**scenario.kwargs) == pytest.approx(10)


@pytest.mark.parametrize("source", ["get_positions", "get_orders"])
def test_broker_failure_never_uses_empty_inventory(scenario, source):
    def unavailable(**kw):
        raise RuntimeError("broker unavailable")
    setattr(scenario.client, source, unavailable)
    with pytest.raises(RuntimeError, match="broker unavailable"):
        guard.guarded_alpaca_quantity(**scenario.kwargs)


@pytest.mark.parametrize("orders", [[dict(symbol="NVDA")], [dict(symbol="AAPL")] * 500])
def test_pending_or_truncated_broker_orders_block(scenario, orders):
    scenario.orders = orders
    with pytest.raises(ValueError, match="ordersPending|snapshotUnavailable"):
        guard.guarded_alpaca_quantity(**scenario.kwargs)


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), -1])
def test_invalid_quantities_reject(scenario, amount):
    scenario.kwargs["amount"] = amount
    with pytest.raises(ValueError):
        guard.guarded_alpaca_quantity(**scenario.kwargs)


def test_worker_submits_capped_quantity_inside_account_lock(scenario, monkeypatch):
    from app.services import pending_order_worker as worker_module
    from app.services.live_trading import records
    events = []

    @contextmanager
    def lock(credential_id):
        assert credential_id == 33
        events.append("locked")
        yield
        events.append("released")

    monkeypatch.setattr(guard, "alpaca_account_lock", lock)
    monkeypatch.setattr(records, "_get_user_id_from_strategy", lambda sid: 7)
    worker = worker_module.PendingOrderWorker.__new__(worker_module.PendingOrderWorker)

    def submit(**kw):
        assert events == ["locked", "prepared"]
        assert kw["payload"]["amount"] == pytest.approx(10)
        events.append("submitted")

    worker._execute_alpaca_order_locked = submit
    worker._mark_failed = lambda **kw: pytest.fail(str(kw))
    worker._execute_alpaca_order(
        order_id=88, order_row={}, payload=dict(symbol="NVDA", signal_type="close_long", amount=1000),
        client=scenario.client, strategy_id=1, exchange_config=dict(exchange_id="alpaca", credential_id=33),
        market_category="USStock", prepare_submission=lambda: events.append("prepared"),
        _notify_live_best_effort=lambda **kw: None, _console_print=lambda *a: None,
    )
    assert events == ["locked", "prepared", "submitted", "released"]


def test_worker_never_submits_when_guard_rejects(scenario, monkeypatch):
    from app.services.pending_order_worker import PendingOrderWorker
    from app.services.live_trading import records
    monkeypatch.setattr(guard, "alpaca_account_lock", lambda cred: nullcontext())
    monkeypatch.setattr(records, "_get_user_id_from_strategy", lambda sid: 7)
    scenario.own = 0
    worker = PendingOrderWorker.__new__(PendingOrderWorker)
    failures = []
    worker._execute_alpaca_order_locked = lambda **kw: pytest.fail("must not submit")
    worker._mark_failed = lambda **kw: failures.append(kw)
    worker._execute_alpaca_order(
        order_id=88, order_row={}, payload=dict(symbol="NVDA", signal_type="close_long", amount=1000),
        client=scenario.client, strategy_id=1, exchange_config=dict(exchange_id="alpaca", credential_id=33),
        market_category="USStock", _notify_live_best_effort=lambda **kw: None, _console_print=lambda *a: None,
    )
    assert failures == [dict(order_id=88, error="positionOwnership.noStrategyInventory")]


@pytest.mark.parametrize("side,signal,action", [("long", "close_long", "sell"), ("short", "close_short", "buy")])
def test_worker_broker_and_fill_record_receive_only_owned_quantity(scenario, monkeypatch, side, signal, action, equity_market_clock):
    from app.services import pending_order_worker as module
    from app.services.live_trading import records
    monkeypatch.setattr(guard, "alpaca_account_lock", lambda cred: nullcontext())
    monkeypatch.setattr(records, "_get_user_id_from_strategy", lambda sid: 7)
    monkeypatch.setattr(module, "append_strategy_log", lambda *a, **kw: None)
    fills, submissions, sent = [], [], []

    def persist(**kwargs):
        fills.append(kwargs)
        return None, None

    def broker(**kwargs):
        submissions.append(kwargs)
        return SimpleNamespace(success=True, filled=kwargs["quantity"], avg_price=220,
                               order_id="test-only", raw={}, status="filled")

    monkeypatch.setattr(module, "persist_strategy_fill", persist)
    scenario.side = side
    scenario.client.place_market_order = broker
    worker = module.PendingOrderWorker.__new__(module.PendingOrderWorker)
    worker._mark_sent = lambda **kw: sent.append(kw)
    worker._unrecorded_pending_fill = lambda oid, qty: qty
    worker._mark_failed = lambda **kw: pytest.fail(str(kw))
    worker._execute_alpaca_order(
        order_id=88, order_row={}, payload=dict(symbol="NVDA", signal_type=signal, amount=1000, ref_price=220),
        client=scenario.client, strategy_id=1, exchange_config=dict(exchange_id="alpaca", credential_id=33),
        market_category="USStock", _notify_live_best_effort=lambda **kw: None, _console_print=lambda *a: None,
    )
    assert len(submissions) == 1 and submissions[0]["quantity"] == pytest.approx(10)
    assert submissions[0]["side"] == action
    assert submissions[0]["market_type"] == "USStock"
    assert fills[0]["filled"] == pytest.approx(10)
    assert sent[0]["final_filled"] is True
