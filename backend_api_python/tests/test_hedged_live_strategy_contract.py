from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.services.live_trading.account_positions import reconcile_strategy_vs_account
from app.services.pending_orders.live_order_support import FillAccumulator, signal_to_side_pos_reduce
from app.services.live_trading import account_risk, records
from app.services import strategy_live_guard
from app.services.trading_executor import TradingExecutor


def _strategy(strategy_id: int, side: str, *, status: str = "running") -> dict:
    return {
        "id": strategy_id,
        "user_id": 7,
        "status": status,
        "execution_mode": "live",
        "market_type": "swap",
        "symbol": "BTC/USDT",
        "position_side": side,
        "initial_capital": 1_000.0,
        "leverage": 5,
        "exchange_config": {"exchange_id": "okx", "credential_id": 17},
        "trading_config": {
            "symbol": "BTC/USDT",
            "market_type": "swap",
            "position_side": side,
            "leverage": 5,
        },
    }


def _neutral_grid_strategy(strategy_id: int = 20) -> dict:
    row = _strategy(strategy_id, "neutral")
    row["trading_config"].update({
        "bot_type": "grid",
        "bot_params": {
            "gridDirection": "neutral",
            "lowerPrice": 0.98,
            "upperPrice": 1.02,
            "dynamicAnchor": True,
        },
    })
    return row


def test_position_reader_returns_existing_leg_size(monkeypatch):
    monkeypatch.setattr(records, "_fetch_position_fuzzy", lambda *_args: ({"size": "1.25"}, "BTC/USDT"))
    assert records.fetch_position_size_for_side(3, "BTCUSDT", "long") == pytest.approx(1.25)


def test_position_reader_returns_zero_for_missing_or_invalid_leg(monkeypatch):
    monkeypatch.setattr(records, "_fetch_position_fuzzy", lambda *_args: (None, ""))
    assert records.fetch_position_size_for_side(3, "BTCUSDT", "long") == 0.0
    monkeypatch.setattr(records, "_fetch_position_fuzzy", lambda *_args: ({"size": "invalid"}, "BTC/USDT"))
    assert records.fetch_position_size_for_side(3, "BTCUSDT", "long") == 0.0


class _Cursor:
    def __init__(self, rows=None, one=None):
        self.rows = list(rows or [])
        self.one = one or {}

    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return dict(self.one)

    def close(self):
        return None


class _Db:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        return None


def test_live_lock_allows_opposite_leg_and_rejects_same_leg(monkeypatch):
    target = _strategy(10, "long")
    others = {
        11: _strategy(11, "short"),
        12: _strategy(12, "long"),
    }
    keys = {
        10: (7, 17, "okx", "swap", "BTC/USDT", "long"),
        11: (7, 17, "okx", "swap", "BTC/USDT", "short"),
        12: (7, 17, "okx", "swap", "BTC/USDT", "long"),
    }

    class _Service:
        @staticmethod
        def get_strategy(strategy_id, user_id=None):
            return others.get(strategy_id)

    monkeypatch.setattr(strategy_live_guard, "strategy_live_lock_key", lambda row, _uid: keys[int(row["id"])])
    monkeypatch.setattr(strategy_live_guard, "get_strategy_service", lambda: _Service())

    monkeypatch.setattr(strategy_live_guard, "get_db_connection", lambda: _Db(_Cursor([{"id": 11}])))
    assert strategy_live_guard.find_live_strategy_conflict(target, 7) is None

    monkeypatch.setattr(strategy_live_guard, "get_db_connection", lambda: _Db(_Cursor([{"id": 11}, {"id": 12}])))
    conflict = strategy_live_guard.find_live_strategy_conflict(target, 7)
    assert conflict["strategy_id"] == 12
    assert conflict["position_side"] == "long"


def test_swap_preflight_fails_closed_when_position_mode_is_unknown(monkeypatch):
    from app.services.grid import exchange_requirements
    from app.services.live_trading import factory
    from app.services import exchange_execution

    executor = TradingExecutor()
    monkeypatch.setattr(executor, "_load_strategy", lambda _sid: _strategy(20, "long"))
    monkeypatch.setattr(strategy_live_guard, "find_live_strategy_conflict", lambda *_args: None)
    monkeypatch.setattr(exchange_execution, "resolve_exchange_config", lambda *_args, **_kwargs: {"exchange_id": "okx"})
    monkeypatch.setattr(factory, "create_client", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        exchange_requirements,
        "detect_hedge_position_mode",
        lambda *_args, **_kwargs: (None, "OKX"),
    )

    with pytest.raises(RuntimeError, match="strategyV2.hedgeModeUnknown"):
        executor._preflight_live_strategy(20)


def test_swap_preflight_accepts_one_way_mode_and_locks_the_whole_instrument(monkeypatch):
    from app.services.grid import exchange_requirements
    from app.services.live_trading import factory
    from app.services import exchange_execution

    executor = TradingExecutor()
    monkeypatch.setattr(executor, "_load_strategy", lambda _sid: _strategy(20, "long"))
    conflict_calls = []
    monkeypatch.setattr(
        strategy_live_guard,
        "find_live_strategy_conflict",
        lambda *_args, **kwargs: conflict_calls.append(kwargs) or None,
    )
    monkeypatch.setattr(exchange_execution, "resolve_exchange_config", lambda *_args, **_kwargs: {"exchange_id": "okx"})
    monkeypatch.setattr(factory, "create_client", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        exchange_requirements,
        "detect_hedge_position_mode",
        lambda *_args, **_kwargs: (False, "okx_net_mode"),
    )

    executor._preflight_live_strategy(20)
    assert conflict_calls == [{"allow_opposite_leg": False}]


def test_swap_preflight_accepts_confirmed_hedge_mode(monkeypatch):
    from app.services.grid import exchange_requirements
    from app.services.live_trading import factory
    from app.services import exchange_execution

    executor = TradingExecutor()
    monkeypatch.setattr(executor, "_load_strategy", lambda _sid: _strategy(20, "short"))
    conflict_calls = []
    monkeypatch.setattr(
        strategy_live_guard,
        "find_live_strategy_conflict",
        lambda *_args, **kwargs: conflict_calls.append(kwargs) or None,
    )
    monkeypatch.setattr(exchange_execution, "resolve_exchange_config", lambda *_args, **_kwargs: {"exchange_id": "okx"})
    monkeypatch.setattr(factory, "create_client", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        exchange_requirements,
        "detect_hedge_position_mode",
        lambda *_args, **_kwargs: (True, "OKX"),
    )
    executor._preflight_live_strategy(20)
    assert conflict_calls == [{"allow_opposite_leg": True}]


def test_neutral_grid_preflight_requires_confirmed_hedge_mode(monkeypatch):
    from app.services.grid import exchange_requirements
    from app.services.live_trading import factory
    from app.services import exchange_execution

    executor = TradingExecutor()
    monkeypatch.setattr(executor, "_load_strategy", lambda _sid: _neutral_grid_strategy())
    monkeypatch.setattr(strategy_live_guard, "find_live_strategy_conflict", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(exchange_execution, "resolve_exchange_config", lambda *_args, **_kwargs: {"exchange_id": "okx"})
    monkeypatch.setattr(factory, "create_client", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        exchange_requirements,
        "detect_hedge_position_mode",
        lambda *_args, **_kwargs: (False, "okx_net_mode"),
    )

    with pytest.raises(RuntimeError, match="strategyV2.dualDirectionHedgeModeRequired"):
        executor._preflight_live_strategy(20)


def test_direction_capability_rejects_incompatible_signal():
    strategy = _strategy(21, "long")

    strategy_live_guard.validate_strategy_signal_direction(strategy, "open_long")
    strategy_live_guard.validate_strategy_signal_direction(strategy, "close_long")
    strategy_live_guard.validate_strategy_signal_direction(strategy, "close_short")
    with pytest.raises(RuntimeError, match="strategyV2.directionModeViolation:long_only:short"):
        strategy_live_guard.validate_strategy_signal_direction(strategy, "open_short")


def test_live_direction_guard_logs_warning_without_failing_runtime(monkeypatch):
    import app.services.trading_executor as trading_executor_module

    executor = TradingExecutor()
    logs = []
    monkeypatch.setattr(executor, "_load_strategy", lambda _sid: _strategy(21, "long"))
    monkeypatch.setattr(
        trading_executor_module,
        "append_strategy_log",
        lambda *args: logs.append(args),
    )

    submitted = executor._execute_signal(
        strategy_id=21,
        execution_mode="live",
        signal_type="open_short",
    )

    assert submitted is False
    assert len(logs) == 1
    assert logs[0][0:2] == (21, "warning")
    assert "strategyV2.directionModeViolation:long_only:short" in logs[0][2]


def test_dual_direction_strategy_conflicts_with_either_owned_leg(monkeypatch):
    target = _strategy(30, "neutral")
    other = _strategy(31, "long")
    keys = {
        30: (7, 17, "okx", "swap", "BTC/USDT", "neutral"),
        31: (7, 17, "okx", "swap", "BTC/USDT", "long"),
    }

    class _Service:
        @staticmethod
        def get_strategy(strategy_id, user_id=None):
            return other if strategy_id == 31 else None

    monkeypatch.setattr(strategy_live_guard, "strategy_live_lock_key", lambda row, _uid: keys[int(row["id"])])
    monkeypatch.setattr(strategy_live_guard, "get_strategy_service", lambda: _Service())
    monkeypatch.setattr(strategy_live_guard, "get_db_connection", lambda: _Db(_Cursor([{"id": 31}])))

    conflict = strategy_live_guard.find_live_strategy_conflict(target, 7)
    assert conflict["strategy_id"] == 31


def test_neutral_grid_preflight_owns_both_legs(monkeypatch):
    from app.services.grid import exchange_requirements
    from app.services.live_trading import factory
    from app.services import exchange_execution

    executor = TradingExecutor()
    monkeypatch.setattr(executor, "_load_strategy", lambda _sid: _neutral_grid_strategy())
    conflict_calls = []
    monkeypatch.setattr(
        strategy_live_guard,
        "find_live_strategy_conflict",
        lambda *_args, **kwargs: conflict_calls.append(kwargs) or None,
    )
    monkeypatch.setattr(exchange_execution, "resolve_exchange_config", lambda *_args, **_kwargs: {"exchange_id": "okx"})
    monkeypatch.setattr(factory, "create_client", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        exchange_requirements,
        "detect_hedge_position_mode",
        lambda *_args, **_kwargs: (True, "okx_long_short_mode"),
    )

    executor._preflight_live_strategy(20)
    assert conflict_calls == [{"allow_opposite_leg": False}]


def test_dynamic_grid_anchor_materializes_absolute_live_bounds():
    source = _neutral_grid_strategy()["trading_config"]
    runtime = TradingExecutor._materialize_grid_anchor(source, 50_000.0)

    assert runtime["bot_params"]["lowerPrice"] == pytest.approx(49_000.0)
    assert runtime["bot_params"]["upperPrice"] == pytest.approx(51_000.0)
    assert runtime["bot_params"]["dynamicAnchor"] is False
    assert source["bot_params"]["lowerPrice"] == 0.98


def test_restart_recovery_repeats_live_preflight(monkeypatch):
    import app.services.trading_executor as trading_executor_module

    started = []

    class _Thread:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def start(self):
            started.append(self.kwargs.get("name"))

        def is_alive(self):
            return True

    monkeypatch.setattr(trading_executor_module.threading, "Thread", _Thread)
    monkeypatch.setattr(trading_executor_module, "append_strategy_log", lambda *_args, **_kwargs: None)

    checked = []
    first = TradingExecutor()
    monkeypatch.setattr(first, "_preflight_live_strategy", lambda sid: checked.append(("first", sid)))
    assert first.start_strategy(30) is True

    restarted = TradingExecutor()
    monkeypatch.setattr(restarted, "_preflight_live_strategy", lambda sid: checked.append(("restart", sid)))
    assert restarted.start_strategy(30) is True
    assert checked == [("first", 30), ("restart", 30)]
    assert started == ["strategy-30", "strategy-30"]


def test_reconciliation_allocates_long_and_short_to_separate_strategies():
    result = reconcile_strategy_vs_account(
        [{"strategy_id": 1, "symbol": "BTC/USDT", "side": "long", "size": 1.0}],
        [
            {"symbol": "BTC/USDT", "side": "long", "size": 1.0},
            {"symbol": "BTC/USDT", "side": "short", "size": 2.0},
        ],
        allocated_rows=[
            {"strategy_id": 1, "symbol": "BTC/USDT", "side": "long", "size": 1.0},
            {"strategy_id": 2, "symbol": "BTC/USDT", "side": "short", "size": 2.0},
        ],
    )
    assert result["status"] == "ok"
    assert result["notes"] == []
    assert result["strategy_allocations"] == [{
        "symbol": "BTC/USDT",
        "side": "long",
        "strategy_size": 1.0,
        "allocated_size": 1.0,
        "account_size": 1.0,
        "allocation_share": 1.0,
    }]


def test_reconciliation_detects_manual_account_drift_without_blaming_other_leg():
    result = reconcile_strategy_vs_account(
        [{"symbol": "BTC/USDT", "side": "long", "size": 1.0}],
        [
            {"symbol": "BTC/USDT", "side": "long", "size": 1.4},
            {"symbol": "BTC/USDT", "side": "short", "size": 2.0},
        ],
        allocated_rows=[
            {"strategy_id": 1, "symbol": "BTC/USDT", "side": "long", "size": 1.0},
            {"strategy_id": 2, "symbol": "BTC/USDT", "side": "short", "size": 2.0},
        ],
    )
    assert result["status"] == "mismatch"
    assert result["notes"] == ["size_mismatch:BTC/USDT:long:allocated=1.0:account=1.4"]


def _risk_row(strategy_id: int, side: str, size: float, price: float = 100.0) -> dict:
    row = _strategy(strategy_id, side)
    row.update({
        "strategy_id": strategy_id,
        "strategy_market_type": "swap",
        "credential_id": 17,
        "symbol": "BTC/USDT",
        "symbol_canonical": "BTC/USDT",
        "side": side,
        "size": size,
        "entry_price": price,
        "current_price": price,
    })
    return row


def test_account_risk_uses_gross_exposure_instead_of_net_exposure(monkeypatch):
    monkeypatch.setattr(account_risk, "_load_account_rows", lambda **_kwargs: [
        _risk_row(1, "long", 5.0),
        _risk_row(2, "short", 5.0),
    ])
    snapshot = account_risk.account_risk_snapshot(
        user_id=7,
        credential_id=17,
        market_type="swap",
        strategy_id=1,
        limits={"max_gross_notional": 900.0},
    )
    assert snapshot["net_notional"] == pytest.approx(0.0)
    assert snapshot["gross_notional"] == pytest.approx(1_000.0)
    assert snapshot["allowed"] is False
    assert "accountRisk.grossNotionalExceeded" in snapshot["violations"]


def test_account_risk_covers_margin_fee_funding_and_symbol_budgets(monkeypatch):
    rows = [_risk_row(1, "long", 5.0), _risk_row(2, "short", 5.0)]
    for row in rows:
        row["trading_config"]["account_risk"] = {
            "fee_rate": 0.01,
            "funding_rate_estimate": 0.005,
        }
    monkeypatch.setattr(account_risk, "_load_account_rows", lambda **_kwargs: rows)
    snapshot = account_risk.account_risk_snapshot(
        user_id=7,
        credential_id=17,
        market_type="swap",
        strategy_id=1,
        proposed_symbol="BTC/USDT",
        proposed_side="long",
        proposed_quantity=1.0,
        proposed_price=100.0,
        proposed_leverage=2.0,
        limits={
            "max_margin_estimate": 240.0,
            "max_round_trip_fee": 20.0,
            "max_funding_per_interval": 5.0,
            "max_symbol_gross_notional": 1_050.0,
        },
    )
    assert snapshot["allowed"] is False
    assert set(snapshot["violations"]) >= {
        "accountRisk.marginEstimateExceeded",
        "accountRisk.feeBudgetExceeded",
        "accountRisk.fundingBudgetExceeded",
        "accountRisk.symbolGrossNotionalExceeded",
    }


def test_account_risk_fails_closed_when_a_position_cannot_be_valued(monkeypatch):
    row = _risk_row(1, "long", 5.0, price=0.0)
    monkeypatch.setattr(account_risk, "_load_account_rows", lambda **_kwargs: [row])
    snapshot = account_risk.account_risk_snapshot(
        user_id=7,
        credential_id=17,
        market_type="swap",
        strategy_id=1,
        proposed_symbol="BTC/USDT",
        proposed_side="long",
        proposed_quantity=1.0,
        proposed_price=0.0,
    )
    assert snapshot["allowed"] is False
    assert set(snapshot["violations"]) >= {
        "accountRisk.positionPriceMissing",
        "accountRisk.proposedPriceMissing",
    }


class _StopCursor:
    def __init__(self):
        self.query = ""

    def execute(self, query, *_args):
        self.query = str(query)

    def fetchall(self):
        return [
            {"symbol": "BTC/USDT", "side": "long", "size": 1.0, "current_price": 100.0, "market_type": "swap"},
            {"symbol": "ETH/USDT", "side": "short", "size": 2.0, "current_price": 50.0, "market_type": "swap"},
        ]

    def fetchone(self):
        return {"id": 901}

    def close(self):
        return None


def test_stop_policy_distinguishes_pause_only_from_pause_and_close(monkeypatch):
    executor = TradingExecutor()
    strategy = _strategy(40, "long")
    monkeypatch.setattr(executor, "_load_strategy", lambda _sid: strategy)
    monkeypatch.setattr(executor, "stop_strategy", lambda _sid: True)

    submitted = []
    executor.order_gateway.submit = lambda request: submitted.append(request) or len(submitted)

    pause_only = executor.stop_strategy_with_policy(40, close_positions=False)
    assert pause_only["success"] is True
    assert pause_only["close_orders_queued"] == 0
    assert submitted == []

    import app.services.trading_executor as trading_executor_module

    monkeypatch.setattr(trading_executor_module, "get_db_connection", lambda: _Db(_StopCursor()))
    close_result = executor.stop_strategy_with_policy(40, close_positions=True)
    assert close_result["success"] is True
    assert close_result["close_orders_queued"] == 2
    assert [(item.symbol, item.action, item.quantity) for item in submitted] == [
        ("BTC/USDT", "close_long", 1.0),
        ("ETH/USDT", "close_short", 2.0),
    ]


@dataclass
class _HedgeBook:
    long: float = 0.0
    short: float = 0.0
    fills: dict[str, FillAccumulator] = field(default_factory=dict)

    def fill(self, signal: str, quantity: float, price: float) -> None:
        _side, pos_side, reduce_only = signal_to_side_pos_reduce(signal)
        acc = self.fills.setdefault(signal, FillAccumulator())
        acc.apply_fill(quantity, price)
        current = getattr(self, pos_side)
        setattr(self, pos_side, max(0.0, current - quantity) if reduce_only else current + quantity)


def test_open_close_stop_and_partial_fills_keep_hedge_legs_independent():
    book = _HedgeBook()
    book.fill("open_long", 1.0, 100.0)
    book.fill("open_short", 2.0, 101.0)
    book.fill("close_long", 0.4, 102.0)
    book.fill("close_long", 0.6, 103.0)
    assert book.long == pytest.approx(0.0)
    assert book.short == pytest.approx(2.0)
    assert book.fills["close_long"].total_base == pytest.approx(1.0)
    assert book.fills["close_long"].avg_price() == pytest.approx(102.6)

    book.fill("close_short_stop", 2.0, 99.0)
    assert book.long == pytest.approx(0.0)
    assert book.short == pytest.approx(0.0)
