from types import SimpleNamespace
from unittest.mock import Mock
from contextlib import contextmanager

import pytest

from app.services.live_trading import position_ownership as ownership
from app.services.live_trading.account_positions import reconcile_strategy_vs_account
from app.services.strategy_lifecycle import (
    is_fatal_exchange_error,
    maybe_auto_stop_on_exchange_error,
)
from app.workers.trading import TradingWorker


@pytest.mark.parametrize("difference,allowed", [(9.99, True), (10, True), (10.01, True), (-9.99, True), (-10, True), (-10.01, False)])
def test_quote_tolerance_covers_both_signs_and_boundary(difference, allowed):
    snap = ownership.calculate_position_ownership(symbol="BTC/USDT", side="long", account_qty=1 + difference / 100, strategy_qty=1, reference_price=100)
    assert snap.allowed is allowed


def test_missing_price_does_not_apply_quote_tolerance():
    assert ownership.quote_drift_tolerance("BTC/USDT", 0) == 0
    assert ownership.quote_drift_tolerance("ETH/BTC", 100) == 0
    assert ownership.quote_drift_tolerance("BTC/USDT", float("nan")) == 0


def test_ui_and_entry_ownership_agree_on_small_shortfall():
    account = [{"symbol": "BTC/USDT", "side": "long", "size": 0.91, "mark_price": 100}]
    allocated = [{"symbol": "BTC/USDT", "side": "long", "size": 1}]
    rows = ownership.build_ownership_rows(account_rows=account, allocated_rows=allocated, reservation_rows=[])
    assert rows[0]["status"] == "ok"
    assert rows[0]["difference_quote"] == pytest.approx(-9)
    assert reconcile_strategy_vs_account(allocated, account)["status"] == "ok"


@pytest.mark.parametrize("account_size,strategy_size,protected,kind", [
    (0.0045177, 0.0094404, 0.0012018, "allocation_shortfall"),
    (0.02, 0.01, 0.015, "none"),
    (0.02, 0.01, 0, "none"),
])
def test_negative_difference_has_an_explicit_repair_kind(account_size, strategy_size, protected, kind):
    rows = ownership.build_ownership_rows(
        account_rows=[{"symbol": "BTC/USDT", "side": "long", "size": account_size, "mark_price": 77290.7}],
        allocated_rows=[{"symbol": "BTC/USDT", "side": "long", "size": strategy_size}],
        reservation_rows=[{"symbol": "BTC/USDT", "side": "long", "manual_reserved_qty": protected, "coexistence_mode": "advanced"}],
    )
    assert rows[0]["repair_kind"] == kind


def test_protection_update_never_covers_missing_strategy_inventory(monkeypatch):
    monkeypatch.setattr(ownership, "_fetch_reservation", lambda **kwargs: {"coexistence_mode": "advanced", "manual_reserved_qty": 0.1})
    with pytest.raises(ValueError, match="accountBelowStrategyAllocation"):
        ownership.repair_position_ownership(user_id=1, credential_id=2, exchange_id="okx", market_type="spot", symbol="BTC/USDT", side="long", account_qty=0.01, strategy_qty=0.02, action="reset_protection")


def test_update_protection_releases_only_obsolete_manual_baseline(monkeypatch):
    monkeypatch.setattr(ownership, "_fetch_reservation", lambda **kwargs: {"coexistence_mode": "advanced", "manual_reserved_qty": 0.015})
    cursor = Mock()
    db = SimpleNamespace(cursor=lambda: cursor, commit=Mock())
    @contextmanager
    def connection():
        yield db
    monkeypatch.setattr(ownership, "get_db_connection", connection)
    result = ownership.repair_position_ownership(user_id=1, credential_id=2, exchange_id="okx", market_type="spot", symbol="BTC/USDT", side="long", account_qty=0.02, strategy_qty=0.01, action="reset_protection", reference_price=10000)
    assert result.protected_qty == pytest.approx(0.01)
    assert result.allowed
    assert result.strategy_qty == pytest.approx(0.01)
    assert cursor.execute.call_count == 1
    assert "qd_position_reservations" in cursor.execute.call_args.args[0]
    db.commit.assert_called_once()


@pytest.mark.parametrize("reason", ["position_drift_detected", "minimum_trade_unit", "min_notional", "Invalid quantity (below step/minQty)"])
def test_recoverable_position_rejections_keep_runtime_alive(monkeypatch, reason):
    stop = Mock()
    monkeypatch.setattr("app.services.strategy_lifecycle.auto_stop_live_strategy", stop)
    assert not maybe_auto_stop_on_exchange_error(1, reason, consecutive_failures=20)
    stop.assert_not_called()


@pytest.mark.parametrize(
    "reason",
    (
        "TRADING_VENUE_DISABLED",
        "HTX spot error: api-signature-not-valid",
        "Signature not valid: Incorrect IP address",
        "IP地址错误",
    ),
)
def test_permanent_venue_and_credential_errors_are_fatal(reason):
    assert is_fatal_exchange_error(reason)


def _worker(monkeypatch):
    monkeypatch.setenv("STRATEGY_DISTRIBUTED_BAR_ENABLED", "false")
    service = SimpleNamespace(
        get_running_strategies_with_type=lambda: [{"id": 7}],
        get_strategy=lambda strategy_id: {"id": strategy_id, "status": "running"},
        update_strategy_status=Mock(),
    )
    monkeypatch.setattr("app.services.strategy.StrategyService", lambda: service)
    executor = SimpleNamespace(running_strategies={}, start_strategy=Mock(return_value=True), wait_strategy_running=Mock(return_value=(True, "")))
    repository = SimpleNamespace(acquire_strategy_lease=Mock(side_effect=[None, 1]), release_strategy_lease=Mock(), has_pending_stop=Mock(return_value=False))
    return TradingWorker(executor, repository), service


def test_restart_retries_after_old_container_lease_expires(monkeypatch):
    worker, service = _worker(monkeypatch)
    worker.restore_desired_strategies()
    worker.executor.start_strategy.assert_not_called()
    service.update_strategy_status.assert_not_called()
    worker.restore_desired_strategies()
    worker.executor.start_strategy.assert_called_once_with(7)


def test_recovery_skips_existing_local_runtime(monkeypatch):
    worker, _ = _worker(monkeypatch)
    worker.executor.running_strategies[7] = object()
    worker.restore_desired_strategies()
    worker.repository.acquire_strategy_lease.assert_not_called()


def test_recovery_respects_stop_between_list_and_start(monkeypatch):
    worker, service = _worker(monkeypatch)
    service.get_strategy = lambda _: {"id": 7, "status": "stopped"}
    worker.restore_desired_strategies()
    worker.executor.start_strategy.assert_not_called()
    worker.repository.acquire_strategy_lease.assert_not_called()


def test_database_recovery_failure_does_not_crash_worker(monkeypatch):
    worker, service = _worker(monkeypatch)
    service.get_running_strategies_with_type = Mock(side_effect=[RuntimeError("temporarily unavailable"), []])
    worker._restore_if_due(force=True)
    worker._restore_if_due(force=True)
    assert service.get_running_strategies_with_type.call_count == 2


def test_permanent_restore_failure_stops_without_unbounded_retries(monkeypatch):
    worker, _service = _worker(monkeypatch)
    worker._start = Mock(side_effect=RuntimeError("TRADING_VENUE_DISABLED"))
    auto_stop = Mock()
    monkeypatch.setattr(
        "app.services.strategy_lifecycle.auto_stop_live_strategy",
        auto_stop,
    )

    worker.restore_desired_strategies()

    auto_stop.assert_called_once_with(
        7,
        "TRADING_VENUE_DISABLED",
        source="strategy_restore",
    )
    assert worker._restore_failure_counts == {}


def test_transient_restore_failure_stops_after_configured_limit(monkeypatch):
    monkeypatch.setenv("STRATEGY_RESTORE_MAX_ATTEMPTS", "5")
    worker, _service = _worker(monkeypatch)
    worker._start = Mock(side_effect=RuntimeError("temporary exchange outage"))
    auto_stop = Mock()
    monkeypatch.setattr(
        "app.services.strategy_lifecycle.auto_stop_live_strategy",
        auto_stop,
    )

    for _ in range(4):
        worker.restore_desired_strategies()
    auto_stop.assert_not_called()

    worker.restore_desired_strategies()

    auto_stop.assert_called_once_with(
        7,
        "Repeated exchange errors (5): temporary exchange outage",
        source="strategy_restore",
    )
    assert worker._restore_failure_counts == {}


def test_recovery_does_not_overtake_a_queued_stop(monkeypatch):
    worker, _ = _worker(monkeypatch)
    worker.repository.has_pending_stop.return_value = True
    worker.restore_desired_strategies()
    worker.repository.acquire_strategy_lease.assert_not_called()
    worker.executor.start_strategy.assert_not_called()


def test_recovery_runs_again_without_container_restart(monkeypatch):
    worker, _ = _worker(monkeypatch)
    worker.restore_desired_strategies = Mock()
    monkeypatch.setattr("app.workers.trading.time.monotonic", Mock(side_effect=[100, 101, 116]))
    worker._restore_if_due(force=True)
    worker._restore_if_due()
    worker._restore_if_due()
    assert worker.restore_desired_strategies.call_count == 2


def test_live_rules_preserve_exact_steps_after_float_ledger_arithmetic():
    from app.services.instrument_rules import InstrumentRules
    rules = InstrumentRules(key="BTC/USDT", exchange_id="okx", market_type="spot", symbol="BTC/USDT", amount_step=0.0001, min_amount=0.0001, price_tick=0.1)
    assert rules.normalize_amount(0.00009999999999999994) == pytest.approx(0.0001)
    assert rules.normalize_amount(0.000099) == 0
    assert rules.normalize_price(99.99999999999999) == pytest.approx(100)


def test_grid_precision_rejection_does_not_count_towards_runtime_stop(monkeypatch):
    from app.services.grid.engine import GridEngine
    engine = GridEngine.__new__(GridEngine)
    engine._stop_requested = False
    engine._consecutive_order_errors = 4
    engine.strategy_id = 7
    log = Mock()
    monkeypatch.setattr("app.services.grid.engine.append_strategy_log", log)
    engine._record_order_error("exit", RuntimeError("Invalid quantity (below step/minQty)"))
    assert not engine._stop_requested
    assert engine._consecutive_order_errors == 0
    assert log.call_args.args[1] == "warning"


def test_fresh_repair_rejects_partial_snapshot_before_reading_allocations(monkeypatch):
    from app.routes import strategy_position_ownership_routes as routes
    monkeypatch.setattr(routes, "_ownership_context", lambda *args: ({"execution_mode": "live"}, {"exchange_id": "okx"}, 2, "spot", {"BTC/USDT"}))
    monkeypatch.setattr("app.services.live_trading.account_snapshot.fetch_account_snapshot", lambda **kwargs: {"spot_positions": [], "partial": True})
    allocations = Mock(side_effect=AssertionError("Must not plan repairs from incomplete data"))
    monkeypatch.setattr("app.services.live_trading.account_positions.list_strategy_allocations_for_account", allocations)
    with pytest.raises(ValueError, match="snapshotUnavailable"):
        routes._load_ownership_rows(7, 1, fresh=True)
    allocations.assert_not_called()
