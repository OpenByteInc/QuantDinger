"""Regular-session enforcement without contacting a broker or database."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import market_schedule as schedule
from app.services.pending_orders.broker_support import submit_broker_order
from app.services.pending_orders.error_classification import classify_exchange_order_error
from app.services.pending_orders.order_retry import is_safe_explicit_order_retry
from app.services.strategy_lifecycle import counts_toward_order_failure_stop


def at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.fixture
def clock(monkeypatch):
    current = [at("2026-11-25T15:00:00Z")]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz or timezone.utc)

    monkeypatch.setattr(schedule, "datetime", Clock)
    return current


@pytest.mark.parametrize("market,now,opened", [
    ("USStock", "2026-11-25T09:01:20Z", False),
    ("USStock", "2026-11-24T21:09:13Z", False),
    ("USStock", "2026-11-25T14:29:59Z", False),
    ("USStock", "2026-11-25T14:30:00Z", True),
    ("USStock", "2026-11-25T21:00:00Z", False),
    ("USStock", "2026-07-06T13:30:00Z", True),
    ("USStock", "2026-07-06T20:00:00Z", False),
    ("USStock", "2026-11-26T15:00:00Z", False),
    ("USStock", "2026-11-27T17:59:59Z", True),
    ("USStock", "2026-11-27T18:00:00Z", False),
    ("USStock", "2026-11-28T15:00:00Z", False),
    ("HKStock", "2025-11-25T04:30:00Z", False),
    ("HKStock", "2025-11-25T05:00:00Z", True),
    ("AStock", "2025-11-25T04:30:00Z", False),
    ("CNStock", "2025-11-25T05:00:00Z", True),
    ("Crypto", "2026-11-28T15:00:00Z", True),
])
def test_regular_sessions_include_dst_holidays_breaks_and_early_close(market, now, opened):
    assert (schedule.equity_order_session_error(market, now=at(now)) == "") == opened


@pytest.mark.parametrize("queued,expected", [
    ("2026-11-24T20:59:00Z", "equity_order_session_expired"),
    ("2026-11-25T09:01:20Z", "equity_order_session_expired"),
    ("2026-11-25T14:30:00Z", ""),
    ("2026-11-25T14:59:00", ""),
    ("2026-11-25T15:00:01Z", "equity_order_session_expired"),
])
def test_only_current_session_queue_entries_can_be_submitted(clock, queued, expected):
    assert schedule.equity_order_session_error("USStock", queued_at=queued) == expected


def test_no_replay_across_lunch_break():
    assert schedule.equity_order_session_error(
        "HKStock", now=at("2025-11-25T05:01:00Z"), queued_at="2025-11-25T03:59:00Z",
    ) == "equity_order_session_expired"


def test_calendar_unavailable_blocks_equities_but_not_crypto(monkeypatch):
    monkeypatch.setattr(schedule, "_calendar", Mock(side_effect=RuntimeError("calendar unavailable")))
    assert schedule.equity_order_session_error("USStock") == "equity_calendar_unavailable"
    assert schedule.equity_order_session_error("Crypto", queued_at="2020-01-01") == ""


@pytest.mark.parametrize("code", ["equity_market_closed", "equity_order_session_expired", "equity_calendar_unavailable"])
def test_session_rejections_neither_retry_nor_stop_strategy(code):
    assert classify_exchange_order_error(code)["category"] == "market_unavailable"
    assert not is_safe_explicit_order_retry(code)
    for action in ("open_long", "close_long", "close_short"):
        assert not counts_toward_order_failure_stop(code, action)


def test_session_text_cannot_override_ambiguous_transport():
    assert classify_exchange_order_error("HTTP 503 equity_market_closed")["category"] == "transport"


@pytest.mark.parametrize("kind,bracket", [("market", False), ("limit", False), ("market", True)])
def test_last_moment_guard_runs_for_all_broker_order_types(clock, kind, bracket):
    clock[0] = at("2026-11-25T21:00:00Z")
    client = Mock()
    with pytest.raises(schedule.EquityOrderSessionError, match="equity_market_closed"):
        submit_broker_order(
            client, order_type=kind, limit_price=100, bracket=bracket,
            session_market="USStock", symbol="AAPL", quantity=1, side="sell",
            market_type="USStock", queued_at="2026-11-25T20:59:59Z",
        )
    assert not client.mock_calls


def test_rate_limit_retry_cannot_cross_close(clock, monkeypatch):
    clock[0] = at("2026-11-25T20:59:59Z")
    monkeypatch.setenv("LIVE_TRADING_ORDER_SAFE_RETRIES", "1")
    monkeypatch.setenv("LIVE_TRADING_ORDER_RETRY_BACKOFF_SEC", "0")
    client = Mock()
    client.place_market_order.return_value = SimpleNamespace(success=False, message="rate limit exceeded")
    with pytest.raises(schedule.EquityOrderSessionError, match="equity_market_closed"):
        submit_broker_order(
            client, order_type="market", limit_price=0, session_market="USStock",
            symbol="AAPL", quantity=1, side="sell", client_order_id="stable-id",
            queued_at="2026-11-25T20:59:58Z",
            on_retry=lambda *args: clock.__setitem__(0, at("2026-11-25T21:00:00Z")),
        )
    client.place_market_order.assert_called_once()
    assert client.place_market_order.call_args.kwargs["client_order_id"] == "stable-id"


@pytest.mark.parametrize("broker", ["alpaca", "ibkr", "tiger", "webull"])
@pytest.mark.parametrize("action", ["open_long", "close_long_stop", "close_long_profit", "close_long"])
def test_worker_expires_unsent_orders_before_client_creation(clock, monkeypatch, broker, action):
    from app.services import pending_order_worker as module

    worker = object.__new__(module.PendingOrderWorker)
    worker._mark_failed = Mock()
    ctx = SimpleNamespace(
        strategy_id=1, signal_type=action, symbol="AAPL", amount=1, cfg={},
        exchange_config={}, safe_exchange_config={}, exchange_id=broker,
        market_category="USStock", market_type="spot",
    )
    monkeypatch.setattr(module, "build_live_order_context", Mock(return_value=ctx))
    create = Mock()
    notify = Mock()
    monkeypatch.setattr(module, "create_client", create)
    monkeypatch.setattr(module, "LiveOrderNotifier", notify)
    logs = Mock()
    monkeypatch.setattr(module, "append_strategy_log", logs)
    worker._execute_live_order(order_id=2, order_row={"created_at": "2026-11-25T09:01:20Z"}, payload={})
    worker._mark_failed.assert_called_once_with(order_id=2, error="equity_order_session_expired")
    create.assert_not_called()
    notify.assert_not_called()
    assert logs.call_args.args[1] == "info"


@pytest.mark.parametrize("broker", ["alpaca", "ibkr"])
def test_final_worker_guard_does_not_notify_or_mark_submission_unknown(clock, monkeypatch, broker):
    from app.services import pending_order_worker as module

    clock[0] = at("2026-11-25T21:00:00Z")
    worker = object.__new__(module.PendingOrderWorker)
    worker._mark_failed = Mock()
    worker._mark_submit_unknown = Mock()
    monkeypatch.setattr(module, "append_strategy_log", Mock())
    client, notify = Mock(), Mock()
    execute = worker._execute_ibkr_order if broker == "ibkr" else worker._execute_alpaca_order_locked
    execute(
        order_id=2, strategy_id=1, order_row={"created_at": "2026-11-25T20:59:59Z"},
        payload={"signal_type": "close_long", "symbol": "AAPL", "amount": 1},
        client=client, exchange_config={}, market_category="USStock",
        _notify_live_best_effort=notify, _console_print=Mock(),
    )
    worker._mark_failed.assert_called_once_with(order_id=2, error="equity_market_closed")
    worker._mark_submit_unknown.assert_not_called()
    notify.assert_not_called()
    assert not client.mock_calls


@pytest.mark.parametrize("action", ["open_long", "close_long", "close_long_stop", "close_long_profit"])
def test_queue_guard_blocks_off_hours_before_budget_or_gateway(clock, action):
    from app.services.trading_executor import TradingExecutor

    clock[0] = at("2026-11-25T09:00:00Z")
    executor = object.__new__(TradingExecutor)
    executor._load_strategy = Mock(return_value={"status": "running", "market_category": "USStock"})
    executor.order_gateway = Mock()
    assert executor._execute_signal(strategy_id=1, execution_mode="live", signal_type=action) is False
    assert not executor.order_gateway.mock_calls
