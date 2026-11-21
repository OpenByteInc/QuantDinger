"""Grid resting engine must resolve credential_id before limit/cancel/close paths."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.exchange_execution import _load_credential_config, resolve_exchange_config
from app.services.grid.config import GridBotConfig
from app.services.grid.runner import GridRestingRunner
from app.services.grid.validator import validate_grid_config


def test_resolve_exchange_config_merges_credential_id():
    base = {"exchange_id": "okx", "api_key": "k", "secret_key": "s", "passphrase": "p"}
    merged = resolve_exchange_config({"credential_id": 9, "market_type": "swap"}, user_id=1)
    # Without DB this stays credential-only; with mock below we verify merge logic separately.
    assert merged.get("credential_id") == 9


def test_load_credential_config_restores_exchange_id_from_credential_row(monkeypatch):
    class Cursor:
        def execute(self, *_args):
            return None

        def fetchone(self):
            return {"exchange_id": "Gate", "encrypted_config": "encrypted"}

        def close(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

    class Context:
        def __enter__(self):
            return Connection()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        "app.services.exchange_execution.get_db_connection",
        lambda: Context(),
    )
    monkeypatch.setattr(
        "app.services.exchange_execution.decrypt_credential_blob",
        lambda _raw: '{"api_key":"key","environment":"testnet"}',
    )

    config = _load_credential_config(7, user_id=1)

    assert config == {
        "api_key": "key",
        "environment": "testnet",
        "exchange_id": "gate",
    }


def test_grid_startup_fails_before_initial_market_when_client_missing():
    calls = {"market": 0}

    def _enqueue(sig, usdt, px, reason):
        calls["market"] += 1
        return True

    def _create_client():
        raise RuntimeError("Missing OKX api_key/secret_key/passphrase")

    runner = GridRestingRunner(
        1,
        "BTC/USDT",
        {
            "leverage": 5,
            "market_type": "swap",
            "initial_capital": 1000,
            "bot_params": {
                "upperPrice": 100000,
                "lowerPrice": 90000,
                "gridCount": 5,
                "amountPerGrid": 50,
                "gridDirection": "long",
                "initialPositionPct": 0,
            },
        },
        {"exchange_id": "okx", "api_key": "", "secret_key": "", "passphrase": ""},
        user_id=1,
        initial_capital=1000,
        enqueue_market_fn=_enqueue,
        create_client_fn=_create_client,
    )
    ok, msg = runner.startup(95000.0)
    assert ok is False
    assert "Missing OKX" in msg
    assert calls["market"] == 0


def test_grid_startup_places_limits_when_client_ok():
    calls = {"limits": 0}

    def _enqueue(sig, usdt, px, reason):
        return True

    client = MagicMock()
    client.get_instrument.return_value = {"ctVal": "0.01", "lotSz": "0.01", "minSz": "0.01"}
    client.place_limit_order.return_value = MagicMock(exchange_order_id="ex1")

    def _create_client():
        return client

    with patch("app.services.grid.engine.place_grid_limit_order") as place:
        place.return_value = MagicMock(exchange_order_id="ex1")
        with patch(
            "app.services.grid.engine.GridEngine._grid_entry_ownership_allowed",
            return_value=(True, {}),
        ), patch("app.services.grid.engine.GridRestingOrderRepository") as repo_cls, patch(
            "app.services.grid.engine.GridCellRepository"
        ) as cell_repo_cls:
            repo = repo_cls.return_value
            open_orders = []
            repo.list_open.side_effect = lambda *_args: open_orders
            repo.has_open_for_cell.return_value = False
            repo.insert.side_effect = lambda order: open_orders.append(order) or len(open_orders)
            cell_repo_cls.return_value.list_cells.return_value = []
            runner = GridRestingRunner(
                2,
                "BTC/USDT",
                {
                    "leverage": 5,
                    "market_type": "swap",
                    "initial_capital": 1000,
                    "bot_params": {
                        "upperPrice": 100000,
                        "lowerPrice": 90000,
                        "gridCount": 5,
                        "amountPerGrid": 50,
                        "gridDirection": "long",
                        "initialPositionPct": 0,
                    },
                },
                {
                    "exchange_id": "okx",
                    "credential_id": 9,
                    "api_key": "k",
                    "secret_key": "s",
                    "passphrase": "p",
                },
                user_id=1,
                initial_capital=1000,
                enqueue_market_fn=_enqueue,
                create_client_fn=_create_client,
            )
            ok, msg = runner.startup(95000.0)
    assert ok is True
    assert msg == ""
    assert place.called


@pytest.mark.parametrize("rollback_ok", [True, False])
def test_grid_startup_rolls_back_new_seed_without_resting_coverage(monkeypatch, rollback_ok):
    runner = GridRestingRunner(
        3,
        "ETH/USDT",
        {
            "market_type": "spot",
            "initial_capital": 1000,
            "bot_params": {
                "upperPrice": 3000,
                "lowerPrice": 2000,
                "gridCount": 20,
                "amountPerGrid": 10,
                "gridDirection": "long",
                "initialPositionPct": 20,
                "maxOpenOrders": 10,
            },
        },
        {"exchange_id": "bybit", "credential_id": 9},
        user_id=1,
        initial_capital=1000,
        enqueue_market_fn=lambda *_args, **_kwargs: True,
        create_client_fn=lambda: object(),
    )
    engine = runner.engine
    monkeypatch.setattr(engine, "bootstrap", lambda _price: (True, ""))
    monkeypatch.setattr(engine, "handle_boundary", lambda _price: False)
    monkeypatch.setattr(engine, "validate_startup_order_sizes", lambda _client: (True, ""))

    def seed(_price):
        engine._initial_done = True
        engine._startup_initial_fills = [{"signal_type": "open_long", "quantity": 0.08}]
        return True

    monkeypatch.setattr(engine, "run_initial_market_position", seed)
    monkeypatch.setattr(engine, "sync_grid_orders", lambda _price: 0)
    monkeypatch.setattr(engine, "sync_exit_coverage", lambda _price: 0)
    monkeypatch.setattr(engine._orders, "list_open", lambda *_args, **_kwargs: [])
    cancel_all = MagicMock()
    rollback = MagicMock(return_value=rollback_ok)
    monkeypatch.setattr(engine, "cancel_all_orders_on_exchange", cancel_all)
    monkeypatch.setattr(engine, "rollback_startup_initial_fills", rollback)
    monkeypatch.setattr(
        "app.services.grid.exchange_requirements.validate_neutral_grid_exchange_support",
        lambda *_args, **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "app.services.grid.exchange_requirements.fetch_exchange_dual_leg_snapshot",
        lambda *_args, **_kwargs: {
            "long_size": 0.0,
            "short_size": 0.0,
            "position_mode_label": "spot",
        },
    )

    ok, message = runner.startup(2500.0)

    assert ok is False
    assert message == (
        "strategyRuntime.gridStartupCoverageFailedRolledBack"
        if rollback_ok else "strategyRuntime.gridStartupCoverageFailedRollbackFailed"
    )
    cancel_all.assert_called_once_with()
    rollback.assert_called_once_with(2500.0)


def test_grid_tick_stops_processing_after_boundary_trigger(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class FakeEngine:
        cfg = SimpleNamespace(initial_position_pct=0.0, grid_direction="long")
        stop_requested = False

        def set_runtime_params(self, _params):
            calls.append("params")

        def handle_boundary(self, _price):
            calls.append("boundary")
            return True

        def sync_exit_coverage(self, _price):
            calls.append("exits")

        def sync_grid_orders(self, _price):
            calls.append("entries")

    monkeypatch.setattr(
        "app.services.grid.runner.prepare_bot_market_guards",
        lambda *args, **kwargs: None,
    )
    runner = GridRestingRunner.__new__(GridRestingRunner)
    runner._started = True
    runner._engine = FakeEngine()
    runner._runtime_params = {}
    runner._risk_exit_fn = None
    runner._last_exit_sync_ts = 0.0
    runner._last_sync_ts = 0.0

    runner.tick(50.0)

    assert calls == ["params", "boundary"]


def test_grid_config_rejects_spacing_that_cannot_cover_round_trip_fees():
    cfg = GridBotConfig.from_trading_config(
        {
            "leverage": 5,
            "market_type": "swap",
            "initial_capital": 100,
            "commission": 0.1,
            "bot_params": {
                "upperPrice": 1647.07,
                "lowerPrice": 1644.47,
                "gridCount": 2,
                "amountPerGrid": 5,
                "gridDirection": "long",
                "initialPositionPct": 0,
            },
        }
    )
    ok, msg, warnings = validate_grid_config(cfg, initial_capital=100, fee_rate=0.001)

    assert ok is False
    assert warnings == []
    assert "too narrow after fees" in msg


def _doge_startup_runner(monkeypatch, *, capital=5.0, params=None):
    from app.services import instrument_rules
    from app.services.live_trading.okx import OkxClient

    orders = MagicMock()
    orders.list_open.return_value = []
    orders.has_open_for_cell.return_value = False
    cells = MagicMock()
    cells.list_cells.return_value = []
    monkeypatch.setattr("app.services.grid.engine.GridRestingOrderRepository", lambda: orders)
    monkeypatch.setattr("app.services.grid.engine.GridCellRepository", lambda: cells)
    monkeypatch.setattr("app.services.grid.actor.GridActorRepository", MagicMock())
    monkeypatch.setattr("app.services.grid.engine.persist_grid_resting_state", lambda *_args: None)
    monkeypatch.setattr("app.services.grid.runner.register_runner", MagicMock())
    monkeypatch.setattr("app.services.grid.poller.sync_strategy_grid_orders", lambda _sid: 0)
    logs = []
    monkeypatch.setattr("app.services.grid.runner.append_strategy_log", lambda *args: logs.append(args))
    monkeypatch.setattr("app.services.grid.engine.append_strategy_log", lambda *args: logs.append(args))
    monkeypatch.setattr(
        "app.services.grid.exchange_requirements.validate_neutral_grid_exchange_support",
        lambda *_args, **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "app.services.grid.exchange_requirements.fetch_exchange_dual_leg_snapshot",
        lambda *_args, **_kwargs: {"long_size": 0.0, "short_size": 0.0, "position_mode_label": "okx_net_mode"},
    )
    provider = instrument_rules.InstrumentRulesProvider()
    monkeypatch.setattr(instrument_rules, "get_instrument_rules_provider", lambda: provider)
    client = OkxClient.__new__(OkxClient)
    client.get_instrument = lambda **_kwargs: {
        "instId": "DOGE-USDT-SWAP", "ctVal": "1000", "ctMult": "1", "ctValCcy": "DOGE",
        "lotSz": "0.01", "minSz": "0.01", "tickSz": "0.00001",
    }
    runner = GridRestingRunner(
        901, "DOGE/USDT",
        {
            "market_type": "swap", "leverage": 1,
            "bot_params": {
                "lowerPrice": 0.0295, "upperPrice": 0.0738,
                "gridCount": 80, "gridCountUnit": "cells", "amountPerGridPct": 1 / 80,
                "gridDirection": "long", "initialPositionPct": 0.6, "maxOpenOrders": 4,
                **(params or {}),
            },
        },
        {"exchange_id": "okx", "credential_id": 9},
        initial_capital=capital, create_client_fn=lambda: client,
        enqueue_market_fn=MagicMock(),
    )
    return runner, client, provider, logs


@pytest.mark.parametrize("direction", ["long", "short", "neutral"])
def test_grid_startup_rejects_doge_sub_contract_cells_before_any_order(monkeypatch, direction):
    runner, client, _provider, logs = _doge_startup_runner(monkeypatch, params={"gridDirection": direction})
    initial = MagicMock()
    limits = MagicMock()
    rollback = MagicMock()
    reconcile = MagicMock()
    monkeypatch.setattr(runner.engine, "run_initial_market_position", initial)
    monkeypatch.setattr(runner.engine, "sync_grid_orders", limits)
    monkeypatch.setattr(runner.engine, "rollback_startup_initial_fills", rollback)
    monkeypatch.setattr(runner.engine, "reconcile_grid_ladder_orders", reconcile)

    assert runner.startup(0.0515) == (False, "strategyRuntime.gridStartupCoverageFailed")
    assert not runner._started
    initial.assert_not_called()
    limits.assert_not_called()
    rollback.assert_not_called()
    reconcile.assert_not_called()
    assert any("requested_qty=" in row[2] and "min_qty=10 qty_step=10" in row[2] for row in logs)


def test_grid_startup_size_check_accepts_tradable_doge_without_mutating_budget(monkeypatch):
    runner, client, _provider, _logs = _doge_startup_runner(monkeypatch, capital=100)
    before = dict(runner.trading_config["bot_params"])

    assert runner.engine.validate_startup_order_sizes(client) == (True, "")
    assert runner.trading_config["bot_params"] == before
    assert runner.engine.cfg.leverage == 1


def test_grid_startup_checks_source_weights_beyond_open_order_cap(monkeypatch):
    weights = [1 / 80] * 80
    weights[50] = 0.0001
    runner, client, _provider, _logs = _doge_startup_runner(
        monkeypatch, capital=100, params={"cellBudgetPcts": weights, "maxOpenOrders": 1},
    )

    ok, detail = runner.engine.validate_startup_order_sizes(client)

    assert not ok
    assert "cell=50 " in detail


@pytest.mark.parametrize("funded", [False, True])
def test_grid_startup_ignores_zero_weight_cells_but_requires_funded_cells(monkeypatch, funded):
    weights = [0.0] * 80
    if funded:
        weights[40] = 1.0
    runner, client, _provider, _logs = _doge_startup_runner(
        monkeypatch, params={"cellBudgetPcts": weights},
    )

    ok, detail = runner.engine.validate_startup_order_sizes(client)

    assert ok is funded
    if not funded:
        assert "reason=grid_no_funded_cells" in detail


def test_grid_startup_missing_rules_fails_before_initial_order(monkeypatch):
    runner, _client, provider, logs = _doge_startup_runner(monkeypatch, capital=100)
    monkeypatch.setattr(provider, "get_rules", MagicMock(side_effect=RuntimeError("offline")))
    initial = MagicMock()
    monkeypatch.setattr(runner.engine, "run_initial_market_position", initial)

    assert runner.startup(0.0515)[0] is False
    initial.assert_not_called()
    assert any("reason=instrument_rules_unavailable" in row[2] for row in logs)


@pytest.mark.parametrize("initial_pct", [0, 0.6])
def test_grid_startup_with_no_fill_and_no_orders_cannot_report_success(monkeypatch, initial_pct):
    runner, _client, _provider, logs = _doge_startup_runner(
        monkeypatch, capital=100, params={"initialPositionPct": initial_pct},
    )
    monkeypatch.setattr(runner.engine, "run_initial_market_position", lambda _price: not initial_pct)
    monkeypatch.setattr(runner.engine, "sync_grid_orders", lambda _price: 0)
    monkeypatch.setattr(runner.engine, "sync_exit_coverage", lambda _price: 0)
    cancel_entries, cancel_all, rollback = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(runner.engine, "cancel_entry_orders_on_exchange", cancel_entries)
    monkeypatch.setattr(runner.engine, "cancel_all_orders_on_exchange", cancel_all)
    monkeypatch.setattr(runner.engine, "rollback_startup_initial_fills", rollback)

    assert runner.startup(0.0515) == (False, "strategyRuntime.gridStartupCoverageFailed")
    assert not runner._started
    assert not any("live started" in row[2] for row in logs)
    cancel_entries.assert_called_once()
    cancel_all.assert_not_called()
    rollback.assert_not_called()


@pytest.mark.parametrize("purpose", ["long_entry", "long_exit"])
def test_grid_startup_preserves_verified_orders_on_recovery(monkeypatch, purpose):
    runner, _client, _provider, _logs = _doge_startup_runner(monkeypatch, capital=100)
    monkeypatch.setattr(runner.engine, "reconcile_grid_ladder_orders", lambda: 0)
    runner.engine._orders.list_open.return_value = [
        SimpleNamespace(purpose=purpose, exchange_order_id="verified-order"),
    ]
    monkeypatch.setattr(runner.engine, "run_initial_market_position", lambda _price: True)
    monkeypatch.setattr(runner.engine, "sync_grid_orders", lambda _price: 0)
    monkeypatch.setattr(runner.engine, "sync_exit_coverage", lambda _price: 0)
    cancel_all = MagicMock()
    monkeypatch.setattr(runner.engine, "cancel_all_orders_on_exchange", cancel_all)

    assert runner.startup(0.0515) == (True, "")
    assert runner._started
    cancel_all.assert_not_called()


def test_grid_startup_rejects_unverified_orders_without_new_fill(monkeypatch):
    runner, _client, _provider, _logs = _doge_startup_runner(monkeypatch, capital=100)
    monkeypatch.setattr(runner.engine, "reconcile_grid_ladder_orders", lambda: 0)
    runner.engine._orders.list_open.return_value = [
        SimpleNamespace(purpose="long_entry", exchange_order_id=""),
    ]
    monkeypatch.setattr(runner.engine, "run_initial_market_position", lambda _price: False)
    monkeypatch.setattr(runner.engine, "sync_grid_orders", lambda _price: 0)
    monkeypatch.setattr(runner.engine, "cancel_entry_orders_on_exchange", MagicMock())

    assert runner.startup(0.0515) == (False, "strategyRuntime.gridStartupCoverageFailed")
    assert not runner._started


def test_grid_startup_can_wait_outside_bounds_without_orders(monkeypatch):
    runner, _client, _provider, _logs = _doge_startup_runner(
        monkeypatch, capital=100, params={"boundaryAction": "pause"},
    )
    initial, limits = MagicMock(), MagicMock()
    monkeypatch.setattr(runner.engine, "run_initial_market_position", initial)
    monkeypatch.setattr(runner.engine, "sync_grid_orders", limits)

    assert runner.startup(0.02) == (True, "")
    assert runner._started
    assert runner.engine._paused_entries
    initial.assert_not_called()
    limits.assert_not_called()
