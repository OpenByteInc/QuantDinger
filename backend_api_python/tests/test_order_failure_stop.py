"""Definitive order rejection circuit breaker; no exchange or SMTP calls."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services import strategy_lifecycle as lifecycle
from app.services import pending_order_worker as worker_module
from app.services.pending_orders import claims
from app.services.pending_orders import alpaca_sync_persistence
from app.services.pending_orders import rebalance_batches
from app.services.signal_notifier import SignalNotifier


@pytest.mark.parametrize("reason,signal,counted", [
    ("gate BALANCE_NOT_ENOUGH", "open_long", True),
    ("Insufficient buying power", "open_long", True),
    ("OKX error 51008 insufficient margin", "open_short", True),
    ("invalid symbol", "open_long", True),
    ("unsupported account mode", "open_long", True),
    ("bad precision", "open_long", True),
    ("bad precision", "close_long", False),
    ("opposite_account_inventory_would_be_netted:BTC/USDT", "open_short", False),
    ("Entry rejected because one-way position mode would net opposite account inventory", "open_short", False),
    ("position_ownership_drift", "open_long", False),
    ("minimum_trade_unit", "close_long", False),
    ("ReduceOnly order is rejected", "close_long", False),
    ("rate limit exceeded", "open_long", False),
    ("network timeout", "open_long", False),
    ("Order price exceeds the allowable range", "open_long", False),
    ("market is closed", "open_long", False),
    ("unrecognized response", "open_long", False),
])
def test_only_definitive_failures_count(reason, signal, counted):
    assert lifecycle.counts_toward_order_failure_stop(reason, signal) is counted


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_stop_notice_has_reason_and_never_looks_like_a_trade(monkeypatch, language):
    notifier = SignalNotifier()
    email, browser = Mock(return_value=(True, "")), Mock(return_value=(True, ""))
    monkeypatch.setattr(notifier, "_notify_email", email)
    monkeypatch.setattr(notifier, "_notify_browser", browser)
    result = notifier.notify_strategy_stopped(
        strategy_id=7, strategy_name="<strategy>", reason="insufficient margin <error>", consecutive_failures=5,
        notification_config={"language": language, "channels": ["email", "email"], "targets": {"email": "test@example.com"}},
    )
    assert result["email"]["ok"]
    email.assert_called_once()
    assert "insufficient margin" in email.call_args.kwargs["body_text"]
    assert "&lt;strategy&gt;" in email.call_args.kwargs["body_html"]
    assert "<error>" not in email.call_args.kwargs["body_html"]
    assert browser.call_args.kwargs["signal_type"] == "strategy_stopped"


def test_stop_email_respects_channel_opt_out(monkeypatch):
    notifier = SignalNotifier()
    email = Mock()
    monkeypatch.setattr(notifier, "_notify_email", email)
    monkeypatch.setattr(notifier, "_notify_browser", Mock(return_value=(True, "")))
    notifier.notify_strategy_stopped(strategy_id=7, strategy_name="S", reason="funds", consecutive_failures=5,
                                     notification_config={"channels": ["browser"]})
    email.assert_not_called()


@pytest.fixture
def ledger(monkeypatch, request):
    """Opt-in isolated schema on a disposable PostgreSQL instance."""
    dsn = os.getenv("ORDER_FAILURE_TEST_DSN")
    if not dsn:
        pytest.skip("Set ORDER_FAILURE_TEST_DSN to a disposable PostgreSQL instance")
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extras import RealDictCursor

    schema = "order_failure_test_" + uuid4().hex
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def cleanup():
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()

    request.addfinalizer(cleanup)

    @contextmanager
    def connect():
        conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            yield conn
        finally:
            conn.rollback()
            conn.close()

    def query(statement, params=()):
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(statement, params or None)
                rows = cur.fetchall() if cur.description else []
            conn.commit()
        return rows

    @contextmanager
    def app_connect():
        from app.utils.db_postgres import PostgresCursor

        with connect() as conn:
            yield SimpleNamespace(cursor=lambda: PostgresCursor(conn.cursor()),
                                  commit=conn.commit, rollback=conn.rollback)

    migrations = Path(__file__).parents[1] / "migrations"
    query((migrations / "init.sql").read_text(encoding="utf-8"))
    query("""
        INSERT INTO qd_users (id, username, password_hash)
            VALUES (3, 'failure-test', 'unused'), (99, 'other-tenant', 'unused');
        INSERT INTO qd_strategies_trading (id, user_id, strategy_name, status, execution_mode, desired_runtime_epoch)
            VALUES (7, 3, 'Failure test strategy', 'running', 'live', 2);
        INSERT INTO strategy_runs (id, strategy_id, user_id, runtime_status, runtime_epoch)
            VALUES (11, 7, 3, 'running', 2);
    """)
    migration = (migrations / "20261121_strategy_order_batches.sql").read_text(encoding="utf-8")
    query(migration)
    query(migration)
    for module in (lifecycle, worker_module, claims, alpaca_sync_persistence, rebalance_batches):
        monkeypatch.setattr(module, "get_db_connection", app_connect)
    from app.services import portfolio_monitor_notifications, signal_notifier, strategy_command_repository
    for module in (portfolio_monitor_notifications, signal_notifier, strategy_command_repository):
        monkeypatch.setattr(module, "get_db_connection", app_connect)
    worker = object.__new__(worker_module.PendingOrderWorker)
    monkeypatch.setattr(worker, "_register_pending_order_binding", Mock())
    def add(order_id, **kwargs):
        query("""INSERT INTO pending_orders
                 (id, user_id, strategy_id, strategy_run_id, execution_mode, status,
                  signal_type, symbol, amount, idempotency_key, payload_json)
                 VALUES (%s, 3, 7, 11, 'live', 'processing', 'open_long', 'TEST', 1, %s, '{}')""",
              (order_id, f"test-order-{order_id}"))
        for key, value in kwargs.items():
            query(sql.SQL("UPDATE pending_orders SET {} = %s WHERE id = %s").format(sql.Identifier(key)), (value, order_id))
        return order_id
    def fail(order_id, reason="gate BALANCE_NOT_ENOUGH"):
        worker._mark_failed(order_id, reason)
    def count():
        rows = query("SELECT state_json FROM strategy_runtime_state WHERE state_key = 'exchange_failures'")
        return rows[0]["state_json"]["count"] if rows else 0
    return SimpleNamespace(query=query, connect=connect, add=add, fail=fail, count=count, worker=worker)


def test_fifth_failure_stops_and_claim_cancels_queued_orders(ledger):
    for i in range(1, 5):
        ledger.fail(ledger.add(i))
    assert ledger.count() == 4
    assert ledger.query("SELECT status FROM qd_strategies_trading")[0]["status"] == "running"
    ledger.fail(ledger.add(5))
    assert ledger.count() == 5
    assert ledger.query("SELECT status FROM qd_strategies_trading")[0]["status"] == "stopped"
    commands = ledger.query("SELECT * FROM qd_strategy_commands")
    assert len(commands) == 1
    assert commands[0]["payload_json"]["close_positions"] is False
    assert not claims.claim_pending_order(ledger.add(6, status="pending"))
    assert ledger.query("SELECT status FROM pending_orders WHERE id=6")[0]["status"] == "cancelled"


def test_protective_rejections_do_not_count_or_reset_real_failures(ledger):
    for i in range(1, 5):
        ledger.fail(ledger.add(i))
    for i in range(5, 12):
        ledger.fail(ledger.add(i), "opposite_account_inventory_would_be_netted:BTC/USDT")
    assert ledger.count() == 4
    ledger.fail(ledger.add(12))
    assert len(ledger.query("SELECT * FROM qd_strategy_commands")) == 1


def test_only_new_exchange_acceptance_resets_streak(ledger):
    for i in range(1, 5):
        ledger.fail(ledger.add(i))
    ledger.add(5)
    assert ledger.count() == 4
    ledger.worker._mark_sent(5, exchange_order_id="exchange-5")
    assert ledger.count() == 0
    ledger.fail(ledger.add(6))
    ledger.worker._mark_sent(5, exchange_order_id="exchange-5", filled=1, final_filled=True)
    assert ledger.count() == 1


def test_duplicate_and_concurrent_failure_results_count_once(ledger):
    for i in range(1, 6):
        ledger.add(i)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(ledger.fail, [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]))
    assert ledger.count() == 5
    assert len(ledger.query("SELECT * FROM qd_strategy_commands")) == 1


@pytest.mark.parametrize("broker", [False, True])
def test_unknown_submission_reconciled_success_resets_only_once(ledger, broker):
    for i in range(1, 5):
        ledger.fail(ledger.add(i))
    ledger.add(5, status="sent", dispatch_note="submit_outcome_unknown")
    kwargs = {"order_id": 5, "status": "sent", "exchange_status": "unknown", "filled": 0,
              "avg_price": 100, "exchange_response_json": "{}"}
    if broker:
        update = ledger.worker._update_alpaca_sent_order_snapshot
        kwargs["final"] = False
    else:
        update = ledger.worker._update_live_sent_order_snapshot
    update(**kwargs)
    assert ledger.count() == 4
    update(**{**kwargs, "exchange_status": "open"})
    assert ledger.count() == 0
    ledger.fail(ledger.add(6))
    update(**{**kwargs, "status": "filled", "exchange_status": "filled", "filled": 1})
    assert ledger.count() == 1


@pytest.mark.parametrize("change", ["user_id=99", "strategy_run_id=99", "execution_mode='signal'"])
def test_unrelated_order_does_not_affect_strategy(ledger, change):
    ledger.add(1)
    ledger.query("UPDATE pending_orders SET " + change)
    ledger.fail(1)
    assert ledger.count() == 0


def test_old_run_does_not_stop_restarted_strategy(ledger):
    for i in range(1, 5):
        ledger.fail(ledger.add(i))
    ledger.query("UPDATE qd_strategies_trading SET desired_runtime_epoch = 3")
    ledger.fail(ledger.add(5))
    assert ledger.count() == 4
    assert not ledger.query("SELECT * FROM qd_strategy_commands")


def test_new_run_starts_with_independent_failure_count(ledger):
    for i in range(1, 6):
        ledger.fail(ledger.add(i))
    ledger.query("UPDATE qd_strategies_trading SET status='running', desired_runtime_epoch=3")
    ledger.query("INSERT INTO strategy_runs (id, strategy_id, user_id, runtime_status, runtime_epoch) VALUES (12,7,3,'running',3)")
    assert claims.claim_pending_order(ledger.add(6, strategy_run_id=12, status="pending"))
    ledger.fail(6)
    assert ledger.query("SELECT state_json FROM strategy_runtime_state WHERE strategy_run_id=12")[0]["state_json"]["count"] == 1
    assert ledger.query("SELECT status FROM qd_strategies_trading")[0]["status"] == "running"


def test_stop_cleanup_failure_does_not_claim_success_notice(ledger, monkeypatch):
    for i in range(1, 6):
        ledger.fail(ledger.add(i))
    row = ledger.query("SELECT * FROM qd_strategy_commands")[0]
    command = SimpleNamespace(strategy_id=7, user_id=3, payload=row["payload_json"])
    notify = Mock()
    monkeypatch.setattr(lifecycle, "notify_order_failure_stop", notify)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        lifecycle.complete_order_failure_stop(command, Mock(side_effect=RuntimeError("cleanup failed")))
    notify.assert_not_called()


def test_stop_notice_is_deduplicated_and_stale_stop_is_ignored(ledger, monkeypatch):
    for i in range(1, 6):
        ledger.fail(ledger.add(i))
    row = ledger.query("SELECT * FROM qd_strategy_commands")[0]
    command = SimpleNamespace(strategy_id=7, user_id=3, payload=row["payload_json"])
    notify = Mock(return_value={"email": {"ok": True}})
    monkeypatch.setattr(SignalNotifier, "notify_strategy_stopped", notify)
    monkeypatch.setattr(lifecycle, "append_strategy_log", Mock())
    from app.services import portfolio_monitor_notifications
    monkeypatch.setattr(portfolio_monitor_notifications, "resolve_notification_delivery", lambda user_id, config: config)
    stop = Mock(return_value={"status": "stopped"})
    lifecycle.complete_order_failure_stop(command, stop)
    lifecycle.complete_order_failure_stop(command, stop)
    notify.assert_called_once()
    assert notify.call_args.kwargs["strategy_name"] == "Failure test strategy"
    stop.assert_called_with(7, close_positions=False, persist_status=False)
    ledger.query("UPDATE qd_strategies_trading SET status='running', desired_runtime_epoch=3")
    stop.reset_mock()
    assert lifecycle.complete_order_failure_stop(command, stop)["status"] == "superseded"
    stop.assert_not_called()


def test_outcome_transaction_rollback_does_not_count(ledger):
    order_id = ledger.add(1)
    with ledger.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM pending_orders WHERE id=%s FOR UPDATE", (order_id,))
        lifecycle.record_pending_order_outcome(cur, cur.fetchone(), accepted=False, reason="insufficient margin")
        conn.rollback()
    assert ledger.count() == 0


@pytest.mark.parametrize("close_positions", [False, True])
def test_auto_stop_command_completes_and_notifies_with_real_schema(ledger, monkeypatch, close_positions):
    from app.services.strategy_command_repository import StrategyCommandRepository
    from app.workers.trading import TradingWorker
    from tests.test_trading_worker_boundaries import FakeExecutor

    monkeypatch.setenv("STRATEGY_DISTRIBUTED_BAR_ENABLED", "false")
    monkeypatch.setattr(lifecycle, "append_strategy_log", Mock())
    monkeypatch.setattr("app.workers.trading.append_strategy_log", Mock())
    channels = ["email", "telegram", "discord", "webhook", "phone"]
    ledger.query("UPDATE qd_strategies_trading SET notification_config=%s",
                 (json.dumps({"channels": channels}),))
    ledger.query("UPDATE qd_users SET email=%s, notification_settings=%s WHERE id=3", (
        "account@example.com", json.dumps({"language": "zh-CN", "telegram_chat_id": "test-chat",
            "telegram_bot_token": "test-token", "discord_webhook": "https://example.com/discord",
            "webhook_url": "https://example.com/webhook", "phone": "+15555550123"}),
    ))
    transports = {}
    for channel in channels:
        transports[channel] = Mock(return_value=(channel != "email", "simulated delivery failure"))
        monkeypatch.setattr(SignalNotifier, f"_notify_{channel}", transports[channel])
    for number in range(1, 6):
        ledger.fail(ledger.add(number))

    repository = StrategyCommandRepository()
    if close_positions:
        repository.enqueue(strategy_id=7, user_id=3, command_type="stop", payload={"close_positions": True})
    executor = FakeExecutor()
    executor.running_strategies[7] = object()
    worker = TradingWorker(executor, repository)
    command = repository.claim_next(owner_id=worker.worker_id, lease_seconds=30, max_attempts=3)
    worker._execute(command)
    assert repository.get(command.id).status == "succeeded"
    assert executor.stopped == [7]
    assert executor.policy_stops == ([(7, True)] if close_positions else [])
    for transport in transports.values():
        transport.assert_called_once()
    assert transports["email"].call_args.kwargs["to_email"] == "account@example.com"
    assert "Failure test strategy" in transports["email"].call_args.kwargs["body_text"]
    assert "连续" in transports["email"].call_args.kwargs["body_text"]
    assert transports["telegram"].call_args.kwargs["chat_id"] == "test-chat"
    notices = ledger.query("SELECT user_id, signal_type, payload_json FROM qd_strategy_notifications")
    assert len(notices) == 1
    assert notices[0]["user_id"] == 3
    assert notices[0]["signal_type"] == "strategy_stopped"
    assert json.loads(notices[0]["payload_json"])["strategy"]["name"] == "Failure test strategy"
    assert repository.claim_next(owner_id=worker.worker_id, lease_seconds=30, max_attempts=3) is None
    lifecycle.complete_order_failure_stop(command, Mock())
    assert len(ledger.query("SELECT id FROM qd_strategy_notifications")) == 1
    for transport in transports.values():
        transport.assert_called_once()
