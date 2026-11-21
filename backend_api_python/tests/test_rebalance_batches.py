"""Portfolio batch outcomes and mail use a disposable DB and mocked SMTP only."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest

from app.services.pending_orders import rebalance_batches as batches
from app.services.pending_orders.live_order_support import LiveOrderNotifier
from app.services.pending_orders.signal_execution import dispatch_virtual_signal_order
from app.services.pending_order_worker import PendingOrderWorker
from app.services.signal_notifier import SignalNotifier
from tests.test_order_failure_stop import ledger  # noqa: F401


def add_batch(ledger, group, ids, *, sealed=True, mode="live", user=3, run=11, token=0):
    for order_id in ids:
        ledger.add(order_id, user_id=user, strategy_run_id=run, execution_mode=mode,
                   payload_json=json.dumps({"rebalance_group_id": group}))
        with ledger.connect() as db:
            with db.cursor() as cur:
                batches.register_order_batch(cur, SimpleNamespace(
                    user_id=user, strategy_id=7, strategy_run_id=run, rebalance_group_id=group,
                    execution_mode=mode, runtime_fencing_token=token,
                ), order_id)
            db.commit()
    row = ledger.query("SELECT id FROM qd_strategy_order_batches WHERE group_id = %s", (group,))[0]
    if sealed:
        ledger.query("UPDATE qd_strategy_order_batches SET sealed_at = NOW() WHERE id = %s", (row["id"],))
    return row["id"]


def accept(ledger, order_id):
    ledger.worker._mark_sent(order_id, exchange_order_id=f"venue-{order_id}")


def test_partial_batch_waits_for_late_success_and_resets_instead_of_stopping(ledger):
    for order_id in range(1, 5):
        ledger.fail(ledger.add(order_id))
    batch_id = add_batch(ledger, "mixed", range(10, 20))
    for order_id in range(10, 16):
        ledger.fail(order_id)
    batches._reconcile_batch(batch_id)
    assert ledger.count() == 4
    assert ledger.query("SELECT status FROM qd_strategies_trading")[0]["status"] == "running"
    for order_id in range(16, 20):
        accept(ledger, order_id)
    batches._reconcile_batch(batch_id)
    assert ledger.count() == 0
    assert not ledger.query("SELECT * FROM qd_strategy_commands")


def test_five_failed_batches_not_five_orders_stop_once(ledger):
    for batch_index in range(5):
        ids = range(batch_index * 10 + 1, batch_index * 10 + 11)
        batch_id = add_batch(ledger, f"batch-{batch_index}", ids)
        for order_id in ids:
            ledger.fail(order_id)
        assert ledger.count() == batch_index
        batches._reconcile_batch(batch_id)
        batches._reconcile_batch(batch_id)
        assert ledger.count() == batch_index + 1
    commands = ledger.query("SELECT * FROM qd_strategy_commands")
    assert len(commands) == 1
    assert commands[0]["payload_json"]["failure_unit"] == "rebalance"
    assert commands[0]["payload_json"]["close_positions"] is False


def test_protective_batch_ignored_and_unknown_submission_not_failure(ledger):
    ledger.fail(ledger.add(1))
    guarded = add_batch(ledger, "guards", [2, 3])
    for order_id in [2, 3]:
        ledger.fail(order_id, "opposite_account_inventory_would_be_netted")
    batches._reconcile_batch(guarded)
    assert ledger.count() == 1
    unknown = add_batch(ledger, "unknown", [4, 5])
    ledger.fail(4)
    ledger.query("UPDATE pending_orders SET status='sent', dispatch_note='submit_outcome_unknown' WHERE id=5")
    batches._reconcile_batch(unknown)
    assert ledger.count() == 1
    assert ledger.query("SELECT evaluated_at FROM qd_strategy_order_batches WHERE id=%s", (unknown,))[0]["evaluated_at"] is None


def test_order_and_batch_failure_units_never_mix(ledger):
    for order_id in range(1, 5):
        ledger.fail(ledger.add(order_id))
    batch_id = add_batch(ledger, "new-unit", [5, 6])
    ledger.fail(5)
    ledger.fail(6)
    batches._reconcile_batch(batch_id)
    assert ledger.count() == 1
    assert not ledger.query("SELECT * FROM qd_strategy_commands")


def test_no_assessment_before_queueing_sealed(ledger):
    batch_id = add_batch(ledger, "building", [1, 2], sealed=False)
    for order_id in [1, 2]:
        ledger.fail(order_id)
    assert batches._reconcile_batch(batch_id) is None
    assert ledger.count() == 0
    ledger.query("UPDATE qd_strategy_order_batches SET sealed_at=NOW() WHERE id=%s", (batch_id,))
    batches._reconcile_batch(batch_id)
    assert ledger.count() == 1


def test_batches_evaluated_in_creation_order_not_response_order(ledger):
    first = add_batch(ledger, "first", [1])
    second = add_batch(ledger, "second", [2])
    ledger.fail(2)
    batches._reconcile_batch(second)
    assert ledger.count() == 0
    accept(ledger, 1)
    batches._reconcile_batch(first)
    batches._reconcile_batch(second)
    assert ledger.count() == 1


def test_concurrent_reconciliation_claims_one_notice_and_one_outcome(ledger):
    batch_id = add_batch(ledger, "concurrent", range(1, 11))
    for order_id in range(1, 11):
        ledger.fail(order_id)
    with ThreadPoolExecutor(max_workers=8) as pool:
        notices = list(pool.map(batches._reconcile_batch, [batch_id] * 10))
    assert sum(notice is not None for notice in notices) == 1
    assert ledger.count() == 1


def test_summary_snapshot_at_deadline_does_not_treat_pending_as_failed(ledger):
    batch_id = add_batch(ledger, "slow", [1, 2])
    ledger.fail(1)
    assert batches._reconcile_batch(batch_id) is None
    ledger.query("UPDATE qd_strategy_order_batches SET sealed_at=NOW()-INTERVAL '31 seconds' WHERE id=%s", (batch_id,))
    notice = batches._reconcile_batch(batch_id)
    assert [order["summary_status"] for order in notice[1]] == ["failed", "pending"]
    assert ledger.count() == 0
    accept(ledger, 2)
    assert batches._reconcile_batch(batch_id) is None
    assert ledger.count() == 0


@pytest.mark.parametrize("mode,user,run", [("signal", 3, 11), ("live", 99, 11), ("live", 3, 99)])
def test_signal_other_tenant_or_old_run_cannot_stop_current_run(ledger, mode, user, run):
    for number in range(5):
        batch_id = add_batch(ledger, f"isolated-{number}", [number + 1], mode=mode, user=user, run=run)
        ledger.fail(number + 1)
        batches._reconcile_batch(batch_id)
    assert ledger.count() == 0
    assert not ledger.query("SELECT * FROM qd_strategy_commands")


def test_dead_producer_sealed_after_fence_lost(ledger):
    batch_id = add_batch(ledger, "abandoned", [1], sealed=False, token=99)
    ledger.fail(1)
    batches._reconcile_batch(batch_id)
    assert ledger.count() == 1


def test_active_producer_never_sealed_by_elapsed_time(ledger):
    batch_id = add_batch(ledger, "active", [1], sealed=False, token=99)
    ledger.query("""INSERT INTO qd_strategy_runtime_leases
                    (strategy_id, owner_id, fencing_token, lease_expires_at)
                    VALUES (7, 'test-owner', 99, NOW()+INTERVAL '1 hour')""")
    ledger.query("UPDATE qd_strategy_order_batches SET created_at=NOW()-INTERVAL '1 hour'")
    assert batches._reconcile_batch(batch_id) is None
    assert ledger.query("SELECT sealed_at FROM qd_strategy_order_batches WHERE id=%s", (batch_id,))[0]["sealed_at"] is None


def test_closed_batch_cannot_receive_new_orders(ledger):
    add_batch(ledger, "closed", [1])
    ledger.add(2)
    with pytest.raises(RuntimeError, match="rebalanceBatchClosed"), ledger.connect() as db:
        batches.register_order_batch(db.cursor(), SimpleNamespace(
            user_id=3, strategy_id=7, strategy_run_id=11, rebalance_group_id="closed",
            execution_mode="live", runtime_fencing_token=0,
        ), 2)


def test_batch_context_seals_on_exception_and_replay_uses_same_key(ledger):
    group = ""
    with pytest.raises(ValueError), batches.rebalance_batch(strategy_id=7, run_id=11, timestamp=123, enabled=True) as group:
        add_batch(ledger, group, [1], sealed=False)
        raise ValueError("producer failed")
    assert ledger.query("SELECT sealed_at FROM qd_strategy_order_batches")[0]["sealed_at"]
    with batches.rebalance_batch(strategy_id=7, run_id=11, timestamp=123, enabled=True) as replay:
        assert replay == group


def test_ten_results_send_once_per_channel_and_delivery_failure_does_not_spam(ledger, monkeypatch):
    channels = ["email", "browser", "telegram", "discord", "webhook", "phone"]
    ledger.query("UPDATE qd_strategies_trading SET notification_config=%s", (json.dumps({"channels": channels}),))
    ledger.query("UPDATE qd_users SET email='batch@example.com', notification_settings=%s WHERE id=3",
                 (json.dumps({"language": "zh-CN", "telegram_chat_id": "batch-chat"}),))
    add_batch(ledger, "email", range(1, 11))
    for order_id in range(1, 9):
        accept(ledger, order_id)
    for order_id in [9, 10]:
        ledger.fail(order_id)
    notifier = SignalNotifier()
    transports = {}
    for channel in channels:
        transports[channel] = Mock(return_value=(channel != "email", ""))
        monkeypatch.setattr(notifier, f"_notify_{channel}", transports[channel])
    batches.reconcile_order_batches(notifier)
    ledger.query("UPDATE qd_strategy_order_batches SET next_check_at=NOW()")
    batches.reconcile_order_batches(notifier)
    for transport in transports.values():
        transport.assert_called_once()
    payload = transports["webhook"].call_args.kwargs["payload"]
    assert len(payload["orders"]) == 10
    assert payload["strategy"]["name"] == "Failure test strategy"
    assert payload["counts"]["accepted"] == 8
    assert payload["counts"]["failed"] == 2
    assert transports["email"].call_args.kwargs["to_email"] == "batch@example.com"
    assert transports["telegram"].call_args.kwargs["chat_id"] == "batch-chat"


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_summary_localization_status_and_html_escape(monkeypatch, language):
    notifier = SignalNotifier()
    email = Mock(return_value=(True, ""))
    monkeypatch.setattr(notifier, "_notify_email", email)
    notifier.notify_rebalance_summary(
        strategy_id=7, strategy_name="<Portfolio>", group_id="batch-id", execution_mode="live",
        orders=[{"symbol": "<META>", "signal_type": "close_long", "amount": 1,
                 "filled": 0, "summary_status": "pending", "last_error": ""}],
        notification_config={"channels": ["email"], "language": language, "targets": {"email": "test@example.com"}},
    )
    email.assert_called_once()
    assert "&lt;META&gt;" in email.call_args.kwargs["body_html"]
    assert ("待确认" if language == "zh-CN" else "Pending confirmation") in email.call_args.kwargs["body_text"]


def test_batch_suppresses_individual_delivery_on_all_channels():
    cfg = {"channels": ["email", "browser", "webhook", "telegram", "discord", "phone"]}
    assert batches.individual_notification_config(cfg, {"rebalance_group_id": "a"})["channels"] == []
    assert len(cfg["channels"]) == 6
    assert batches.individual_notification_config(cfg, {}) == cfg
    notifier = Mock()
    facade = LiveOrderNotifier(1, 7, {}, {"rebalance_group_id": "a", "notification_config": cfg},
                               notifier, Mock(), Mock())
    facade.notify(status="failed", error="insufficient balance")
    notifier.notify_signal.assert_not_called()


def test_summary_respects_email_opt_out(monkeypatch):
    notifier = SignalNotifier()
    email = Mock()
    monkeypatch.setattr(notifier, "_notify_email", email)
    browser = Mock(return_value=(True, ""))
    monkeypatch.setattr(notifier, "_notify_browser", browser)
    assert notifier.notify_rebalance_summary(
        strategy_id=7, strategy_name="P", group_id="batch", execution_mode="live",
        orders=[], notification_config={"channels": ["browser"]},
    ) == {"browser": {"ok": True, "error": ""}}
    email.assert_not_called()
    browser.assert_called_once()


def test_stale_epoch_batch_cannot_stop_restarted_strategy(ledger):
    batch_id = add_batch(ledger, "stale", range(1, 6))
    for order_id in range(1, 6):
        ledger.fail(order_id)
    ledger.query("UPDATE qd_strategies_trading SET desired_runtime_epoch=3")
    batches._reconcile_batch(batch_id)
    assert ledger.count() == 0
    assert not ledger.query("SELECT * FROM qd_strategy_commands")


def test_batch_registration_failure_rolls_back_order_and_batch(ledger):
    with pytest.raises(ValueError), ledger.connect() as db:
        cur = db.cursor()
        cur.execute("""INSERT INTO pending_orders
                       (id, user_id, strategy_id, strategy_run_id, symbol, signal_type, idempotency_key)
                       VALUES (1, 3, 7, 11, 'TEST', 'open_long', 'rolled-back')""")
        batches.register_order_batch(cur, SimpleNamespace(
            user_id=3, strategy_id=7, strategy_run_id=11, rebalance_group_id="rolled-back",
            execution_mode="live", runtime_fencing_token=0,
        ), 1)
        raise ValueError("transaction failed")
    assert not ledger.query("SELECT * FROM pending_orders")
    assert not ledger.query("SELECT * FROM qd_strategy_order_batches")


def test_batch_stop_email_identifies_failed_rounds(monkeypatch):
    notifier = SignalNotifier()
    email = Mock(return_value=(True, ""))
    monkeypatch.setattr(notifier, "_notify_email", email)
    monkeypatch.setattr(notifier, "_notify_browser", Mock(return_value=(True, "")))
    notifier.notify_strategy_stopped(
        strategy_id=7, strategy_name="P", reason="insufficient margin", consecutive_failures=5,
        failure_unit="rebalance", notification_config={"channels": ["email"], "language": "zh-CN"},
    )
    assert "连续调仓批次失败：5" in email.call_args.kwargs["body_text"]


def test_signal_batch_keeps_virtual_execution_but_does_not_send_individual_notifications(monkeypatch):
    from app.services import virtual_trading
    execute = Mock(return_value={"virtual_order_id": 9, "fill_price": 100, "fill_quantity": 1, "status": "filled"})
    monkeypatch.setattr(virtual_trading, "execute_virtual_signal_order", execute)
    worker = Mock()
    dispatch_virtual_signal_order(
        worker=worker, order_row={}, payload={"rebalance_group_id": "signal-batch"}, order_id=1,
        strategy_id=7, strategy_name="P", signal_type="open_long", symbol="META", price=100,
        amount=1, direction="long", notification_config={"channels": ["email", "telegram", "webhook", "browser"]},
        append_log=Mock(), logger=Mock(),
    )
    execute.assert_called_once()
    worker._notifier.notify_signal.assert_not_called()
    assert worker._mark_sent.call_args.kwargs["final_filled"] is True
    assert "batch_notification_pending=" in worker._mark_sent.call_args.kwargs["note"]
    assert "notified_ok" not in worker._mark_sent.call_args.kwargs["note"]


def test_slow_email_never_blocks_order_dispatch_or_reconciliation(monkeypatch):
    entered, release, synced, dispatched = (threading.Event() for _ in range(4))
    worker = PendingOrderWorker()
    def blocked_mail(_notifier):
        entered.set()
        release.wait(3)
    monkeypatch.setattr(batches, "reconcile_order_batches", blocked_mail)
    worker._sync_quick_trade_orders = synced.set
    worker._sync_alpaca_sent_orders = Mock()
    worker._sync_live_sent_orders = Mock()
    worker._maybe_sync_positions = Mock()
    worker._fetch_pending_orders = lambda **_: [{"id": 1}]
    worker._mark_processing = lambda **_: True
    worker._dispatch_one = lambda _: dispatched.set()
    threads = [threading.Thread(target=worker._run_batch_loop), threading.Thread(target=worker._run_sync_loop)]
    for thread in threads:
        thread.start()
    try:
        assert entered.wait(1)
        assert synced.wait(1)
        worker._tick()
        assert dispatched.is_set()
    finally:
        worker._stop_event.set()
        release.set()
        for thread in threads:
            thread.join(3)


@pytest.mark.parametrize("event", ["stop", "live", "signal", "trade"])
@pytest.mark.parametrize("channels", [
    ["browser", "email", "telegram", "discord", "phone", "webhook", " TELEGRAM "],
    ["telegram", "phone"], ["webhook"], ["discord"],
])
def test_strategy_events_deliver_once_to_each_selected_channel(monkeypatch, event, channels):
    from app.services import signal_notifier

    notifier = SignalNotifier()
    transports = {}
    for channel in ["browser", "email", "telegram", "discord", "phone", "webhook"]:
        transports[channel] = Mock(return_value=(True, ""))
        monkeypatch.setattr(notifier, f"_notify_{channel}", transports[channel])
    transports["email"].side_effect = RuntimeError("mail unavailable")
    monkeypatch.setattr(signal_notifier, "_load_user_notification_preferences_for_strategy", lambda _: {"timezone": "UTC"})
    targets = {"email": "test@example.com", "telegram": "test-chat", "telegram_bot_token": "private-token",
               "phone": "+12025550123", "discord": "https://discord.com/api/webhooks/1/test",
               "webhook": "https://example.com/hook", "webhook_token": "private-bearer",
               "webhook_headers": {"X-Test": "test-header"}, "webhook_signing_secret": "private-signing"}
    cfg = {"channels": channels, "targets": targets}
    args = {"strategy_id": 7, "strategy_name": "<Portfolio>", "notification_config": cfg}
    if event == "stop":
        result = notifier.notify_strategy_stopped(**args, reason="insufficient margin", consecutive_failures=5,
                                                 failure_unit="rebalance")
    elif event == "trade":
        result = notifier.notify_signal(**args, symbol="META", signal_type="open_long")
    else:
        result = notifier.notify_rebalance_summary(
            **args, group_id="batch", execution_mode=event,
            orders=[{"id": i, "symbol": f"STOCK{i}", "signal_type": "open_long", "amount": 1,
                     "filled": 0, "summary_status": "pending", "payload_json": {"secret": "private-order"}}
                    for i in range(10)],
        )
    expected = {channel.strip().lower() for channel in channels}
    if event == "stop":
        expected.add("browser")
    assert set(result) == expected
    for channel, transport in transports.items():
        assert transport.call_count == int(channel in expected)
        if channel in expected:
            assert result[channel]["ok"] is (channel != "email")
    if "telegram" in expected:
        telegram = transports["telegram"].call_args.kwargs
        assert telegram["token_override"] == "private-token"
        assert telegram["chat_id"] == "test-chat"
        assert telegram["parse_mode"] == ("HTML" if event == "trade" else "")
    if "phone" in expected:
        assert transports["phone"].call_args.kwargs["to_phone"] == targets["phone"]
    if "discord" in expected:
        assert transports["discord"].call_args.kwargs["url"] == targets["discord"]
    if "webhook" in expected:
        hook = transports["webhook"].call_args.kwargs
        assert hook["token_override"] == "private-bearer"
        assert hook["signing_secret_override"] == "private-signing"
        assert hook["headers_override"] == {"X-Test": "test-header"}
        payload = hook["payload"]
        assert "private-" not in json.dumps(payload)
        expected_event = "qd.signal" if event == "trade" else (
            "qd.strategy.stopped" if event == "stop" else "qd.strategy.rebalance")
        assert payload["event"] == expected_event
        if event in {"live", "signal"}:
            assert payload["execution_mode"] == event
            assert payload["counts"]["pending"] == 10
            assert len(payload["orders"]) == 10
            for dialect in ["feishu", "dingtalk", "wecom", "slack"]:
                adapted = signal_notifier._adapt_payload_for_dialect(dialect, payload)
                assert "STOCK9" in json.dumps(adapted)


@pytest.mark.parametrize("event", ["qd.strategy.stopped", "qd.strategy.rebalance"])
def test_discord_strategy_events_do_not_render_fake_trade_fields(monkeypatch, event):
    from app.services import signal_notifier

    notifier = SignalNotifier()
    monkeypatch.setattr(signal_notifier, "_validate_outbound_http_url", lambda url, **_: (url, ""))
    post = Mock(return_value=(True, ""))
    monkeypatch.setattr(notifier, "_post_discord_embed", post)
    assert notifier._notify_discord(
        url="https://discord.com/api/webhooks/1/test", fallback_text="summary",
        payload={"event": event, "title": "Event title", "message": "Event summary"},
    ) == (True, "")
    embed = post.call_args.kwargs["embed"]
    assert embed["title"] == "Event title"
    assert embed["description"] == "Event summary"
    assert "fields" not in embed


@pytest.mark.parametrize("override", [False, True])
def test_profile_resolution_covers_discord_and_phone_without_enabling_other_channels(monkeypatch, override):
    from app.services import portfolio_monitor_notifications as delivery
    from contextlib import contextmanager

    cursor = Mock()
    cursor.fetchone.return_value = {
        "email": "account@example.com",
        "notification_settings": {"phone": "profile-phone", "discord_webhook": "profile-discord",
                                  "default_channels": ["email", "telegram", "webhook"]},
    }
    db = Mock()
    db.cursor.return_value = cursor

    @contextmanager
    def connection():
        yield db

    monkeypatch.setattr(delivery, "get_db_connection", connection)
    cfg = {"channels": "phone, discord", "targets": {"phone": "strategy-phone", "discord": "strategy-discord"}
           if override else {}}
    resolved = delivery.resolve_notification_delivery(3, cfg)
    assert resolved["channels"] == ["phone", "discord"]
    assert resolved["targets"]["phone"] == ("strategy-phone" if override else "profile-phone")
    assert resolved["targets"]["discord"] == ("strategy-discord" if override else "profile-discord")
    assert cfg["targets"] == ({"phone": "strategy-phone", "discord": "strategy-discord"} if override else {})
    assert cursor.execute.call_args.args[1] == (3,)
