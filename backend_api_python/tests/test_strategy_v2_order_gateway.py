import json
from types import SimpleNamespace

import pytest

from app.services.strategy_v2 import live_execution
from app.services.strategy_v2.live_execution import LiveOrderRequest, StrategyV2OrderGateway
from app.services.strategy_runtime.order_intents import OrderIntentService


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.params = ()

    def execute(self, _query, params):
        self.query = _query
        self.params = tuple(params)

    def fetchone(self):
        return self.row

    def close(self):
        return None


class _Db:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return self._cursor

    def commit(self):
        return None


def _request(action="open_long", **overrides):
    values = {
        "strategy_id": 7,
        "strategy_run_id": 42,
        "user_id": 12,
        "symbol": "BTC/USDT",
        "action": action,
        "quantity": 0.01,
        "reference_price": 60_000.0,
        "signal_timestamp": 123,
        "market_type": "swap",
        "execution_mode": "live",
        "margin_mode": "isolated",
    }
    values.update(overrides)
    return LiveOrderRequest(
        **values,
    )


def test_inflight_lookup_serializes_the_same_long_position_leg(monkeypatch):
    cursor = _Cursor({"id": 99})
    monkeypatch.setattr(live_execution, "get_db_connection", lambda: _Db(cursor))

    assert StrategyV2OrderGateway().has_inflight(_request("close_long")) is True
    assert cursor.params[:2] == (7, "BTC/USDT")
    assert cursor.params[2:6] == (
        "open_long",
        "add_long",
        "reduce_long",
        "close_long",
    )
    assert cursor.params[6:] == (
        "pending",
        "processing",
        "sent",
        "syncing",
        "reconciling",
    )


def test_inflight_lookup_keeps_short_hedge_leg_independent(monkeypatch):
    cursor = _Cursor({})
    monkeypatch.setattr(live_execution, "get_db_connection", lambda: _Db(cursor))

    assert StrategyV2OrderGateway().has_inflight(_request("open_short")) is False
    assert cursor.params[2:6] == (
        "open_short",
        "add_short",
        "reduce_short",
        "close_short",
    )


def test_signal_grid_limits_allow_parallel_price_levels_without_database_lookup(monkeypatch):
    monkeypatch.setattr(
        live_execution,
        "get_db_connection",
        lambda: (_ for _ in ()).throw(AssertionError("parallel virtual grid must bypass lane lock")),
    )

    request = _request(
        execution_mode="signal",
        strategy_type="grid",
        order_type="limit",
        client_order_id="grid-7-long-entry-1",
    )

    assert StrategyV2OrderGateway().has_inflight(request) is False


def test_live_grid_limits_keep_position_lane_serialization(monkeypatch):
    cursor = _Cursor({"id": 99})
    monkeypatch.setattr(live_execution, "get_db_connection", lambda: _Db(cursor))

    request = _request(
        execution_mode="live",
        strategy_type="grid",
        order_type="limit",
        client_order_id="grid-7-long-entry-1",
    )

    assert StrategyV2OrderGateway().has_inflight(request) is True


def test_submit_does_not_reconsider_an_ai_rejected_signal(monkeypatch):
    class _IntentService:
        def __init__(self, **_kwargs):
            pass

        def build_signal_idempotency_key(self, **_kwargs):
            return "same-signal"

        def create_intent(self, **_kwargs):
            return SimpleNamespace(id=91, existing=True, status="ai_rejected")

    monkeypatch.setattr(live_execution, "OrderIntentService", _IntentService)
    assert StrategyV2OrderGateway().submit(_request()) is None


def test_submit_persists_margin_mode_in_pending_order_payload(monkeypatch):
    class _IntentService:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def build_signal_idempotency_key(**_kwargs):
            return "margin-mode-signal"

        @staticmethod
        def create_intent(**_kwargs):
            return SimpleNamespace(id=91, existing=False, status="intent_created")

    cursor = _Cursor({"id": 123})
    monkeypatch.setattr(live_execution, "OrderIntentService", _IntentService)
    monkeypatch.setattr(live_execution, "get_db_connection", lambda: _Db(cursor))

    assert StrategyV2OrderGateway().submit(_request(
        margin_mode="isolated", exchange_id="okx", credential_id=25,
    )) == 123
    payload = json.loads(cursor.params[10])
    assert payload["margin_mode"] == "isolated"
    assert payload["exchange_id"] == "okx" and payload["credential_id"] == 25
    assert cursor.params[-2:] == ("okx", 25)


def test_ai_rejection_is_latched_until_the_signal_disappears(monkeypatch):
    decisions = []
    next_intent_id = iter((91, 92))

    class _IntentService:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def build_signal_idempotency_key(**kwargs):
            return f"signal-{kwargs['signal_ts']}"

        def create_intent(self, **_kwargs):
            return SimpleNamespace(
                id=next(next_intent_id),
                existing=False,
                status="intent_created",
            )

    class _RejectingFilter:
        @staticmethod
        def evaluate(request, *, enabled):
            decisions.append((request.action, enabled))
            return SimpleNamespace(allowed=False)

    monkeypatch.setattr(live_execution, "OrderIntentService", _IntentService)
    monkeypatch.setattr(
        live_execution,
        "get_db_connection",
        lambda: _Db(_Cursor({})),
    )
    gateway = StrategyV2OrderGateway(decision_filter_factory=_RejectingFilter)
    request = _request(ai_decision_filter=True, reason="dual_ma_open_long")

    gateway.begin_signal_cycle(42)
    assert gateway.submit(request) is None
    gateway.finish_signal_cycle(42)

    gateway.begin_signal_cycle(42)
    assert gateway.submit(_request(
        ai_decision_filter=True,
        reason="dual_ma_open_long",
        signal_timestamp=124,
    )) is None
    gateway.finish_signal_cycle(42)
    assert decisions == [("open_long", True)]

    gateway.begin_signal_cycle(42)
    gateway.finish_signal_cycle(42)

    gateway.begin_signal_cycle(42)
    assert gateway.submit(_request(
        ai_decision_filter=True,
        reason="dual_ma_open_long",
        signal_timestamp=125,
    )) is None
    gateway.finish_signal_cycle(42)
    assert decisions == [("open_long", True), ("open_long", True)]


def test_fallback_idempotency_distinguishes_different_same_second_orders():
    common = {
        "strategy_run_id": 42,
        "strategy_id": 7,
        "symbol": "BTC/USDT",
        "signal_type": "add_long",
        "signal_ts": 123,
    }

    first = OrderIntentService.build_signal_idempotency_key(
        **common,
        signal_discriminator={"quantity": 0.01, "reason": "scale-1"},
    )
    second = OrderIntentService.build_signal_idempotency_key(
        **common,
        signal_discriminator={"quantity": 0.02, "reason": "scale-2"},
    )
    retry = OrderIntentService.build_signal_idempotency_key(
        **common,
        signal_discriminator={"reason": "scale-1", "quantity": 0.01},
    )

    assert first != second
    assert first == retry


def _size_failure(**overrides):
    values = {
        "status": "failed", "signal_type": "close_long", "amount": 0.01, "filled": 0,
        "last_error": "qty too small", "age_seconds": 5,
        "payload_json": {"amount": 0.01, "ref_price": 60000, "reason": "",
                         "order_type": "market", "execution_algo": "market"},
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize("symbol,market_type,error", [
    ("DOGE/USDT", "spot", "Filter failure: MIN_NOTIONAL"),
    ("BTC/USDT", "swap", "quantity below minimum"),
    ("HK.00700", "spot", "Quantity below minimum lot size"),
    ("META", "spot", "qty too small"),
])
def test_unchanged_rejected_exit_is_not_requeued(monkeypatch, symbol, market_type, error):
    cursor = _Cursor(_size_failure(last_error=error))
    monkeypatch.setattr(live_execution, "get_db_connection", lambda: _Db(cursor))
    monkeypatch.setattr(live_execution, "OrderIntentService", lambda **kw: pytest.fail("must not create intent"))
    gateway = StrategyV2OrderGateway()
    for timestamp in range(200, 220):
        assert gateway.submit(_request("close_long", symbol=symbol, market_type=market_type,
                                       signal_timestamp=timestamp)) is None
    assert cursor.params[:4] == (12, 7, symbol, market_type)
    assert "strategy_run_id =" not in cursor.query
    assert "execution_mode = 'live'" in cursor.query
    assert "ORDER BY id DESC LIMIT 1" in cursor.query


@pytest.mark.parametrize("changes", [
    {"status": "sent"}, {"status": "filled"}, {"status": "processing"},
    {"signal_type": "open_long"}, {"filled": 0.001},
    {"last_error": "insufficient balance"}, {"last_error": "connection timed out"},
    {"last_error": "strategyRuntime.noStrategyInventory"},
    {"last_error": "ReduceOnly order is rejected"}, {"last_error": "order size too large"},
    {"last_error": "Filter failure: LOT_SIZE"}, {"last_error": "bad precision"},
    {"last_error": "invalid amount; actual order quantity is 0; minimum notional is about 50"},
    {"payload_json": "broken JSON"}, {"payload_json": []},
    {"payload_json": {"amount": 0.02}},
])
def test_retry_guard_does_not_block_recovery_or_unrelated_failures(monkeypatch, changes):
    cursor = _Cursor(_size_failure(**changes))
    monkeypatch.setattr(live_execution, "get_db_connection", lambda: _Db(cursor))
    assert StrategyV2OrderGateway()._exit_size_retry_deferred(_request("close_long")) is False


def test_changed_quantity_bypasses_previous_size_rejection(monkeypatch):
    monkeypatch.setattr(live_execution, "get_db_connection", lambda: _Db(_Cursor(_size_failure())))
    assert StrategyV2OrderGateway()._exit_size_retry_deferred(_request("close_long", quantity=0.02)) is False


@pytest.mark.parametrize("changes", [
    {"reason": "new reason"}, {"order_type": "limit", "limit_price": 100},
    {"execution_algo": "maker_then_market"}, {"strategy_run_id": 99},
    {"signal_timestamp": 999999999}, {"reference_price": 1_000_000}, {"action": "reduce_long"},
])
def test_same_rejected_quantity_has_no_time_or_restart_escape(monkeypatch, changes):
    monkeypatch.setattr(live_execution, "get_db_connection",
                        lambda: _Db(_Cursor(_size_failure(age_seconds=86400 * 30))))
    request = _request(**{"action": "close_long", **changes})
    assert StrategyV2OrderGateway()._exit_size_retry_deferred(request) is True


@pytest.mark.parametrize("changes", [
    {"execution_mode": "signal"}, {"quantity": 0}, {"action": "open_long"},
    {"action": "add_short"},
])
def test_virtual_entries_and_native_close_all_do_not_use_size_backoff(monkeypatch, changes):
    monkeypatch.setattr(live_execution, "get_db_connection", lambda: pytest.fail("unexpected DB lookup"))
    values = {"action": "close_long", **changes}
    assert StrategyV2OrderGateway()._exit_size_retry_deferred(_request(**values)) is False


def test_notional_tail_is_ignored_instead_of_retried_on_price_moves(monkeypatch):
    cursor = _Cursor(_size_failure(last_error="Filter failure: MIN_NOTIONAL"))
    monkeypatch.setattr(live_execution, "get_db_connection", lambda: _Db(cursor))
    gateway = StrategyV2OrderGateway()
    assert gateway._exit_size_retry_deferred(_request("close_long", reference_price=60100)) is True
    assert gateway._exit_size_retry_deferred(_request("close_long", reference_price=63000)) is True


def test_short_exit_uses_its_own_leg(monkeypatch):
    cursor = _Cursor(_size_failure(signal_type="close_short"))
    monkeypatch.setattr(live_execution, "get_db_connection", lambda: _Db(cursor))
    assert StrategyV2OrderGateway()._exit_size_retry_deferred(_request("close_short")) is True
    assert cursor.params[4:8] == ("open_short", "add_short", "reduce_short", "close_short")


@pytest.mark.parametrize("exchange_id", ["binance", "okx", "bybit", "bitget", "gate", "htx",
                                          "alpaca", "ibkr", "tiger", "webull"])
def test_guard_is_shared_across_venues_and_scoped_to_credential(monkeypatch, exchange_id):
    cursor = _Cursor(_size_failure())
    monkeypatch.setattr(live_execution, "get_db_connection", lambda: _Db(cursor))
    request = _request("close_long", exchange_id=exchange_id, credential_id=25)
    assert StrategyV2OrderGateway()._exit_size_retry_deferred(request) is True
    assert cursor.params[-2:] == (exchange_id, 25)
    assert "COALESCE(credential_id, 0) = %s" in cursor.query
