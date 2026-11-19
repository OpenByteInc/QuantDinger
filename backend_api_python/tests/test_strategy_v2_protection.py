import pandas as pd
import pytest

from app.services.strategy_v2 import (
    OrderIntent,
    ProtectionEngine,
    ProtectionSpec,
    ProtectionState,
    StrategyV2BacktestRunner,
    StrategyV2LiveSession,
)


def _frame(rows):
    index = pd.date_range("2026-01-01", periods=len(rows), freq="4h")
    return pd.DataFrame(rows, index=index, columns=["open", "high", "low", "close", "volume"])


PROTECTED_ENTRY = """
def initialize(context):
    g.symbol = "Crypto:BTC/USDT"
    context.set_universe([g.symbol])
    context.subscribe(frequency="4h")

def handle_data(context, data):
    if get_position(g.symbol).amount == 0:
        order_target_percent(
            g.symbol,
            1.0,
            reason="entry",
            stop_loss_pct=0.02,
            take_profit_pct=0.05,
        )
"""


def test_backtest_protection_fills_at_gap_open():
    frame = _frame([
        (100, 101, 99, 100, 1000),
        (100, 101, 99, 100, 1000),
        (95, 96, 94, 95, 1000),
        (95, 96, 94, 95, 1000),
    ])
    result = StrategyV2BacktestRunner(
        code=PROTECTED_ENTRY,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
        commission=0,
        slippage=0,
    ).run()

    closed = result["closedTrades"][0]
    assert closed["close_reason"] == "stop_loss"
    assert closed["exit_price"] == pytest.approx(95.0)
    assert result["protectionEvents"][0]["triggerPrice"] == pytest.approx(98.0)


def test_backtest_protection_fills_at_stop_inside_bar():
    frame = _frame([
        (100, 101, 99, 100, 1000),
        (100, 101, 99, 100, 1000),
        (100, 101, 97, 99, 1000),
        (99, 100, 98, 99, 1000),
    ])
    result = StrategyV2BacktestRunner(
        code=PROTECTED_ENTRY,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
        commission=0,
        slippage=0,
    ).run()

    assert result["closedTrades"][0]["exit_price"] == pytest.approx(98.0)


def test_backtest_protection_closes_a_single_crypto_lot_without_repeating():
    code = """
def initialize(context):
    g.symbol = "Crypto:BTC/USDT"
    g.sent = False
    context.set_universe([g.symbol])
    context.subscribe(frequency="4h")

def handle_data(context, data):
    if not g.sent:
        order(g.symbol, 0.00000001, reason="entry", stop_loss_pct=0.02)
        g.sent = True
"""
    frame = _frame([
        (1_000_000, 1_010_000, 990_000, 1_000_000, 1000),
        (1_000_000, 1_010_000, 990_000, 1_000_000, 1000),
        (950_000, 960_000, 940_000, 950_000, 1000),
        (950_000, 960_000, 940_000, 950_000, 1000),
    ])
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
        commission=0,
        slippage=0,
    ).run()

    assert len(result["protectionEvents"]) == 1
    assert result["protectionEvents"][0]["reason"] == "stop_loss"
    assert result["positions"] == {}
    assert result["totalTrades"] == 1


def test_same_bar_protection_exits_are_recorded_as_one_risk_batch():
    symbols = ["USStock:AAPL", "USStock:MSFT"]
    code = f'''
def initialize(context):
    context.set_universe({symbols!r})
    context.subscribe(frequency="1d")
    g.sent = False

def on_rebalance(context, data):
    if g.sent:
        return
    for symbol in {symbols!r}:
        order_target_percent(symbol, 0.4, reason="portfolio_target", stop_loss_pct=0.05)
    g.sent = True
'''
    frame = pd.DataFrame(
        [(100, 101, 90, 100, 10_000)],
        columns=["open", "high", "low", "close", "volume"],
        index=pd.date_range("2026-01-01", periods=1, freq="D"),
    )
    result = StrategyV2BacktestRunner(
        code=code,
        frames={symbol: frame.copy() for symbol in symbols},
        initial_capital=10_000,
        commission=0,
        slippage=0,
    ).run()

    records = result["rebalanceRecords"]
    assert [record["recordType"] for record in records] == ["rebalance", "protection"]
    assert records[1]["reasons"] == ["stop_loss"]
    assert records[1]["orderCount"] == 2
    assert records[1]["filled"] == 2
    assert len(result["protectionEvents"]) == 2
    assert result["positions"] == {}


def test_conservative_intrabar_mode_prioritizes_stop_loss():
    spec = ProtectionSpec(stop_loss_pct=0.02, take_profit_pct=0.05)
    state = ProtectionState.open(
        symbol="Crypto:BTC/USDT@spot",
        side="long",
        entry_price=100,
        spec=spec,
        opened_at="2026-01-01",
    )
    decision = ProtectionEngine().evaluate_bar(
        state,
        timestamp="2026-01-01 04:00:00",
        open_price=100,
        high_price=106,
        low_price=97,
    )

    assert decision is not None
    assert decision.reason == "stop_loss"
    assert decision.price == pytest.approx(98.0)


def test_absolute_price_protection_has_priority_over_percentage_rules():
    spec = ProtectionSpec(
        stop_loss_pct=0.10,
        stop_loss_price=97,
        take_profit_pct=0.20,
        take_profit_price=105,
    )
    state = ProtectionState.open(
        symbol="Crypto:BTC/USDT@spot",
        side="long",
        entry_price=100,
        spec=spec,
        opened_at="2026-01-01",
    )

    decision = ProtectionEngine().evaluate_bar(
        state,
        timestamp="2026-01-01 04:00:00",
        open_price=100,
        high_price=104,
        low_price=96,
    )

    assert decision is not None
    assert decision.reason == "stop_loss"
    assert decision.trigger_price == pytest.approx(97.0)


def test_point_trailing_supports_activation_distance_for_short_positions():
    spec = ProtectionSpec(
        trailing_stop_points=3,
        trailing_activation_points=5,
    )
    state = ProtectionState.open(
        symbol="Crypto:BTC/USDT@swap",
        side="short",
        entry_price=100,
        spec=spec,
        opened_at="2026-01-01",
    )
    engine = ProtectionEngine()

    assert engine.evaluate_price(
        state,
        timestamp="2026-01-01 00:01:00",
        price=94,
    ) is None
    assert state.trailing_active is True
    decision = engine.evaluate_price(
        state,
        timestamp="2026-01-01 00:02:00",
        price=97,
    )

    assert decision is not None
    assert decision.reason == "trailing_stop"
    assert decision.trigger_price == pytest.approx(97.0)


def test_invalid_exit_fraction_is_rejected():
    with pytest.raises(ValueError, match="exit_fraction"):
        ProtectionSpec(stop_loss_pct=0.02, exit_fraction=0)
    with pytest.raises(ValueError, match="exit_fraction"):
        ProtectionSpec(stop_loss_pct=0.02, exit_fraction=1.1)


def test_trigger_specific_exit_fraction_overrides_generic_fraction():
    spec = ProtectionSpec(
        stop_loss_price=95,
        take_profit_price=110,
        exit_fraction=1,
        take_profit_exit_fraction=0.5,
    )

    assert spec.exit_fraction_for("stop_loss") == 1
    assert spec.exit_fraction_for("take_profit") == pytest.approx(0.5)
    assert ProtectionSpec.from_value(spec.metadata()) == spec


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "invalid", -1, 1.1])
@pytest.mark.parametrize("field", ["exit_fraction", "take_profit_exit_fraction"])
def test_invalid_fractions_never_silently_become_full_exits(field, value):
    with pytest.raises(ValueError, match="exit fraction|exit_fraction"):
        ProtectionSpec(stop_loss_pct=0.02, **{field: value})


def test_partial_take_profit_disables_only_take_profit_rule():
    spec = ProtectionSpec(
        stop_loss_price=95,
        take_profit_price=110,
        take_profit_exit_fraction=0.5,
    )
    state = ProtectionState.open(
        symbol="Crypto:BTC/USDT@spot",
        side="long",
        entry_price=100,
        spec=spec,
        opened_at="2026-01-01",
    )
    state.disabled_reasons.add("take_profit")

    assert state.has_active_protection is True
    assert ProtectionEngine().evaluate_price(
        state,
        timestamp="2026-01-01 00:01:00",
        price=111,
    ) is None
    decision = ProtectionEngine().evaluate_price(
        state,
        timestamp="2026-01-01 00:02:00",
        price=94,
    )
    assert decision is not None
    assert decision.reason == "stop_loss"


def test_backtest_partial_protection_exits_once_and_keeps_remainder_open():
    code = """
def initialize(context):
    g.symbol = "Crypto:BTC/USDT"
    g.sent = False
    context.set_universe([g.symbol])
    context.subscribe(frequency="4h")

def handle_data(context, data):
    if not g.sent:
        order_target(g.symbol, 10, reason="entry", stop_loss_price=98, exit_fraction=0.4)
        g.sent = True
"""
    frame = _frame([
        (100, 101, 99, 100, 1000),
        (100, 101, 99, 100, 1000),
        (100, 101, 97, 99, 1000),
        (97, 98, 90, 92, 1000),
    ])

    result = StrategyV2BacktestRunner(
        code=code,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
        commission=0,
        slippage=0,
    ).run()

    assert len(result["closedTrades"]) == 1
    assert result["closedTrades"][0]["quantity"] == pytest.approx(4.0)
    assert result["positions"]["Crypto:BTC/USDT@spot"]["amount"] == pytest.approx(6.0)
    assert result["protectionEvents"][0]["exitFraction"] == pytest.approx(0.4)


def test_backtest_partial_protection_completes_deferred_liquidity_target():
    code = """
def initialize(context):
    g.symbol = "Crypto:BTC/USDT"
    g.sent = False
    context.set_universe([g.symbol])
    context.subscribe(frequency="4h")

def handle_data(context, data):
    if not g.sent:
        order_target(
            g.symbol,
            10,
            reason="entry",
            take_profit_price=105,
            take_profit_exit_fraction=0.4,
        )
        g.sent = True
"""
    frame = _frame([
        (100, 101, 99, 100, 1000),
        (100, 101, 99, 100, 1000),
        (105, 106, 104, 105, 20),
        (106, 107, 105, 106, 20),
        (107, 108, 106, 107, 1000),
    ])

    result = StrategyV2BacktestRunner(
        code=code,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
        commission=0,
        slippage=0,
    ).run()

    assert len(result["protectionEvents"]) == 1
    assert result["positions"]["Crypto:BTC/USDT@spot"]["amount"] == pytest.approx(6.0)
    partial_events = [
        item for item in result["orderLedger"]
        if item["reason"] == "take_profit" and item["status"] == "partial"
    ]
    assert len(partial_events) == 1


@pytest.mark.parametrize("suspended,fraction,expected_remaining", [(False, 1.0, 2.0), (True, 0.4, 6.0)])
def test_deferred_protection_keeps_one_target_and_one_attempt_per_bar(
    suspended, fraction, expected_remaining,
):
    code = f'''
def initialize(context):
    g.symbol = "Crypto:BTC/USDT"
    g.sent = False
    context.set_universe([g.symbol])
    context.subscribe(frequency="4h")

def handle_data(context, data):
    if not g.sent:
        order_target(g.symbol, 10, take_profit_price=105, exit_fraction={fraction})
        g.sent = True
'''
    frame = _frame([
        (100, 101, 99, 100, 1000),
        (100, 101, 99, 100, 1000),
        (105, 106, 104, 105, 20),
        (106, 107, 105, 106, 20),
        (107, 108, 106, 107, 20),
        (108, 109, 107, 108, 20),
    ])
    frame["suspended"] = False
    frame.loc[frame.index[2], "suspended"] = suspended
    result = StrategyV2BacktestRunner(
        code=code, frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000, commission=0, slippage=0,
    ).run()

    fills = [item for item in result["executions"] if item["reason"] == "take_profit"]
    times = [item["time"] for item in fills]
    assert len(times) == len(set(times))
    assert all(item["quantity"] <= 2.0 + 1e-12 for item in fills)
    assert result["positions"]["Crypto:BTC/USDT@spot"]["amount"] == pytest.approx(expected_remaining)
    assert len(result["protectionEvents"]) == 1


def test_live_protection_uses_price_ticks_without_new_bar():
    frame = _frame([(100, 101, 99, 100, 1000)])
    session = StrategyV2LiveSession(
        code=PROTECTED_ENTRY,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
    )
    intents, _, _ = session.process({"Crypto:BTC/USDT": frame})
    assert intents[0].protection == ProtectionSpec(stop_loss_pct=0.02, take_profit_pct=0.05)

    session.synchronize_positions({
        "BTC/USDT": {"side": "long", "amount": 1, "avg_cost": 100, "last_price": 100}
    })
    exits = session.evaluate_protections(
        {"Crypto:BTC/USDT@spot": 97.5},
        timestamp="2026-01-01 00:00:30",
    )

    assert len(exits) == 1
    assert exits[0].reason == "stop_loss"
    assert exits[0].kind == "target_quantity"
    assert exits[0].value == 0
    assert session.evaluate_protections(
        {"Crypto:BTC/USDT@spot": 97.0},
        timestamp="2026-01-01 00:00:31",
    ) == []


def test_live_partial_protection_targets_remainder_and_disarms_after_fill():
    code = PROTECTED_ENTRY.replace(
        "take_profit_pct=0.05,",
        "take_profit_price=105, take_profit_exit_fraction=0.25,",
    )
    frame = _frame([(100, 101, 99, 100, 1000)])
    session = StrategyV2LiveSession(
        code=code,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
    )
    session.process({"Crypto:BTC/USDT": frame})
    session.synchronize_positions({
        "BTC/USDT": {"side": "long", "amount": 8, "avg_cost": 100, "last_price": 100}
    })

    exits = session.evaluate_protections(
        {"Crypto:BTC/USDT@spot": 105},
        timestamp="2026-01-01 00:00:30",
    )
    snapshot = session.protection_snapshot()

    assert len(exits) == 1
    assert exits[0].value == pytest.approx(6.0)
    assert snapshot["exitTargets"]["Crypto:BTC/USDT@spot"] == pytest.approx(6.0)
    session.synchronize_positions({
        "BTC/USDT": {"side": "long", "amount": 6, "avg_cost": 100.01, "last_price": 105}
    })
    assert session.pending_protection_exit_symbols() == set()
    restored_state = session.protection_snapshot()["states"]["Crypto:BTC/USDT@spot"]
    assert restored_state["disabled_reasons"] == ["take_profit"]
    assert session.evaluate_protections(
        {"Crypto:BTC/USDT@spot": 106},
        timestamp="2026-01-01 00:00:31",
    ) == []
    stop_exits = session.evaluate_protections(
        {"Crypto:BTC/USDT@spot": 97},
        timestamp="2026-01-01 00:00:32",
    )
    assert len(stop_exits) == 1
    assert stop_exits[0].reason == "stop_loss"
    assert stop_exits[0].value == 0
    session.synchronize_positions({
        "BTC/USDT": {"side": "long", "amount": 0, "avg_cost": 100, "last_price": 97}
    })
    assert session.protection_snapshot()["states"] == {}


def test_live_partial_protection_snapshot_prevents_duplicate_exit_after_restart():
    code = PROTECTED_ENTRY.replace(
        "take_profit_pct=0.05,",
        "take_profit_price=105, take_profit_exit_fraction=0.5,",
    )
    frame = _frame([(100, 101, 99, 100, 1000)])
    first = StrategyV2LiveSession(
        code=code,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
    )
    first.process({"Crypto:BTC/USDT": frame})
    position = {
        "BTC/USDT": {"side": "long", "amount": 8, "avg_cost": 100, "last_price": 100}
    }
    first.synchronize_positions(position)
    assert len(first.evaluate_protections(
        {"Crypto:BTC/USDT@spot": 105},
        timestamp="2026-01-01 00:00:30",
    )) == 1

    restored = StrategyV2LiveSession(
        code=code,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
    )
    restored.restore_protection_snapshot(first.protection_snapshot())
    restored.synchronize_positions(position)

    assert restored.evaluate_protections(
        {"Crypto:BTC/USDT@spot": 106},
        timestamp="2026-01-01 00:00:31",
    ) == []


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("restart", [False, True])
def test_live_rounded_partial_exit_requires_terminal_fill_and_ledger_sync(side, restart):
    frame = _frame([(100, 101, 99, 100, 1000)])
    code = PROTECTED_ENTRY.replace("take_profit_pct=0.05,", "take_profit_pct=0.05, take_profit_exit_fraction=0.4,")
    session = StrategyV2LiveSession(code=code, frames={"Crypto:BTC/USDT": frame}, initial_capital=10_000)
    session.process({"Crypto:BTC/USDT": frame})
    def sync(amount):
        session.synchronize_positions({"BTC/USDT": {
            "side": side, "amount": amount, "avg_cost": 100, "last_price": 100,
        }})
    sync(8)
    price = 106 if side == "long" else 94
    intent = session.evaluate_protections({"Crypto:BTC/USDT@spot": price}, timestamp="2026-01-01 00:00:30")[0]
    assert abs(intent.value) == pytest.approx(4.8)
    if restart:
        snapshot = session.session_snapshot()
        session = StrategyV2LiveSession(code=code, frames={"Crypto:BTC/USDT": frame}, initial_capital=10_000)
        session.restore_session_snapshot(snapshot)
    assert intent.client_order_id in session.context.order_references()
    session.context.update_order_statuses({intent.client_order_id: {"status": "partially_filled", "filled_quantity": 2}})
    sync(6)
    assert session.pending_protection_exit_symbols()
    session.context.update_order_statuses({intent.client_order_id: {"status": "filled", "filled_quantity": 3}})
    sync(6)
    assert session.pending_protection_exit_symbols()
    sync(5)
    assert not session.pending_protection_exit_symbols()
    assert session.evaluate_protections({"Crypto:BTC/USDT@spot": price}) == []


@pytest.mark.parametrize("status,filled,remaining", [("rejected", 0, 8), ("cancelled", 1, 7), ("expired", 2, 6)])
def test_live_failed_partial_exit_retries_original_target_after_restart(status, filled, remaining):
    frame = _frame([(100, 101, 99, 100, 1000)])
    code = PROTECTED_ENTRY.replace("take_profit_pct=0.05,", "take_profit_pct=0.05, take_profit_exit_fraction=0.5,")
    session = StrategyV2LiveSession(code=code, frames={"Crypto:BTC/USDT": frame}, initial_capital=10_000)
    session.process({"Crypto:BTC/USDT": frame})
    session.synchronize_positions({"BTC/USDT": {"amount": 8, "avg_cost": 100}})
    first = session.evaluate_protections({"Crypto:BTC/USDT@spot": 106}, timestamp="2026-01-01 00:00:30")[0]
    session.context.update_order_statuses({first.client_order_id: {"status": status, "filled_quantity": filled}})
    session.synchronize_positions({"BTC/USDT": {"amount": remaining, "avg_cost": 100}})
    assert not session.pending_protection_exit_symbols()
    snapshot = session.session_snapshot()
    restored = StrategyV2LiveSession(code=code, frames={"Crypto:BTC/USDT": frame}, initial_capital=10_000)
    restored.restore_session_snapshot(snapshot)
    restored.synchronize_positions({"BTC/USDT": {"amount": remaining, "avg_cost": 100}})
    retry = restored.evaluate_protections({"Crypto:BTC/USDT@spot": 106}, timestamp="2026-01-01 00:00:31")[0]
    assert retry.value == first.value == 4
    assert retry.client_order_id != first.client_order_id


def test_rejected_live_partial_protection_can_retry_original_fraction():
    frame = _frame([(100, 101, 99, 100, 1000)])
    session = StrategyV2LiveSession(
        code=PROTECTED_ENTRY.replace("take_profit_pct=0.05,", "take_profit_pct=0.05, take_profit_exit_fraction=0.5,"),
        frames={"Crypto:BTC/USDT": frame}, initial_capital=10_000,
    )
    session.process({"Crypto:BTC/USDT": frame})
    session.synchronize_positions({
        "BTC/USDT": {"side": "long", "amount": 8, "avg_cost": 100, "last_price": 100}
    })
    first = session.evaluate_protections({"Crypto:BTC/USDT@spot": 106}, timestamp="2026-01-01 00:00:30")
    session.release_protection_exit("Crypto:BTC/USDT@spot")
    retry = session.evaluate_protections({"Crypto:BTC/USDT@spot": 106}, timestamp="2026-01-01 00:00:31")
    assert len(first) == len(retry) == 1
    assert first[0].value == retry[0].value == 4


def test_live_protection_pending_exit_survives_snapshot_and_clears_when_flat():
    frame = _frame([(100, 101, 99, 100, 1000)])
    first = StrategyV2LiveSession(
        code=PROTECTED_ENTRY,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
    )
    first.process({"Crypto:BTC/USDT": frame})
    position = {
        "BTC/USDT": {"side": "long", "amount": 1, "avg_cost": 100, "last_price": 100}
    }
    first.synchronize_positions(position)
    assert len(first.evaluate_protections(
        {"Crypto:BTC/USDT@spot": 97.5},
        timestamp="2026-01-01 00:00:30",
    )) == 1

    restored = StrategyV2LiveSession(
        code=PROTECTED_ENTRY,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
    )
    restored.restore_protection_snapshot(first.protection_snapshot())
    restored.synchronize_positions(position)
    assert restored.evaluate_protections(
        {"Crypto:BTC/USDT@spot": 97.0},
        timestamp="2026-01-01 00:00:31",
    ) == []

    restored.synchronize_positions({})
    assert restored.pending_protection_exit_symbols() == set()
    assert restored.protection_snapshot()["states"] == {}


def test_new_protected_order_does_not_unlock_an_inflight_protection_exit():
    frame = _frame([(100, 101, 99, 100, 1000)])
    session = StrategyV2LiveSession(
        code=PROTECTED_ENTRY,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
    )
    session.process({"Crypto:BTC/USDT": frame})
    session.synchronize_positions({
        "BTC/USDT": {"side": "long", "amount": 1, "avg_cost": 100, "last_price": 100}
    })
    assert len(session.evaluate_protections(
        {"Crypto:BTC/USDT@spot": 97.5},
        timestamp="2026-01-01 00:00:30",
    )) == 1

    session._capture_protection_intents([OrderIntent(
        "Crypto:BTC/USDT@spot",
        "target_value",
        100,
        protection=ProtectionSpec(stop_loss_pct=0.02),
    )])

    assert session.pending_protection_exit_symbols() == {"Crypto:BTC/USDT@spot"}


def test_ordinary_zero_target_order_does_not_create_a_protection_exit_latch():
    frame = _frame([(100, 101, 99, 100, 1000)])
    session = StrategyV2LiveSession(
        code=PROTECTED_ENTRY,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
    )

    session._capture_protection_intents([OrderIntent(
        "Crypto:BTC/USDT@spot",
        "target_percent",
        0,
        reason="dual_ma_close_short",
    )])

    assert session.pending_protection_exit_symbols() == set()


def test_restore_discards_legacy_orphaned_protection_exit_latch():
    frame = _frame([(100, 101, 99, 100, 1000)])
    session = StrategyV2LiveSession(
        code=PROTECTED_ENTRY,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
    )

    session.restore_protection_snapshot({
        "specs": {},
        "states": {},
        "exitPending": ["Crypto:BTC/USDT@spot"],
    })

    assert session.pending_protection_exit_symbols() == set()


def test_live_protection_snapshot_restores_after_restart():
    frame = _frame([(100, 101, 99, 100, 1000)])
    first = StrategyV2LiveSession(
        code=PROTECTED_ENTRY,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
    )
    first.process({"Crypto:BTC/USDT": frame})
    first.synchronize_positions({
        "BTC/USDT": {"side": "long", "amount": 1, "avg_cost": 100, "last_price": 100}
    })

    second = StrategyV2LiveSession(
        code=PROTECTED_ENTRY,
        frames={"Crypto:BTC/USDT": frame},
        initial_capital=10_000,
    )
    second.restore_protection_snapshot(first.protection_snapshot())
    second.synchronize_positions({
        "BTC/USDT": {"side": "long", "amount": 1, "avg_cost": 100, "last_price": 100}
    })

    exits = second.evaluate_protections(
        {"Crypto:BTC/USDT@spot": 97},
        timestamp="2026-01-01 00:01:00",
    )
    assert exits[0].reason == "stop_loss"


def test_scale_in_preserves_an_activated_trailing_peak():
    spec = ProtectionSpec(
        trailing_stop_pct=0.003,
        trailing_activation_pct=0.01,
        trailing_rebase_on_scale_in=False,
    )
    state = ProtectionState.open(
        symbol="Crypto:BTC/USDT@spot",
        side="long",
        entry_price=100,
        spec=spec,
        opened_at="2026-01-01",
    )
    engine = ProtectionEngine()

    assert engine.evaluate_price(
        state,
        timestamp="2026-01-01 00:01:00",
        price=102,
    ) is None
    assert state.trailing_active is True
    assert state.highest_price == pytest.approx(102)

    state.apply_scale_in(
        entry_price=95,
        fill_price=90,
        spec=spec,
    )

    assert state.entry_price == pytest.approx(95)
    assert state.trailing_active is True
    assert state.highest_price == pytest.approx(102)
    decision = engine.evaluate_price(
        state,
        timestamp="2026-01-01 00:02:00",
        price=101.6,
    )
    assert decision is not None
    assert decision.reason == "trailing_stop"
    assert decision.trigger_price == pytest.approx(102 * (1 - 0.003))


def test_scale_in_rebases_unactivated_trailing_extremes_to_the_new_basket():
    spec = ProtectionSpec(
        trailing_stop_pct=0.003,
        trailing_activation_pct=0.05,
        trailing_rebase_on_scale_in=False,
    )
    state = ProtectionState.open(
        symbol="Crypto:BTC/USDT@spot",
        side="long",
        entry_price=100,
        spec=spec,
        opened_at="2026-01-01",
    )
    state.highest_price = 103

    state.apply_scale_in(
        entry_price=95,
        fill_price=90,
        spec=spec,
    )

    assert state.trailing_active is False
    assert state.highest_price == pytest.approx(95)
    assert state.lowest_price == pytest.approx(90)


def test_scale_in_defaults_to_legacy_trailing_reset_for_custom_strategies():
    spec = ProtectionSpec(
        trailing_stop_pct=0.003,
        trailing_activation_pct=0.01,
    )
    state = ProtectionState.open(
        symbol="Crypto:BTC/USDT@spot",
        side="long",
        entry_price=100,
        spec=spec,
        opened_at="2026-01-01",
    )
    state.highest_price = 103
    state.trailing_active = True

    state.apply_scale_in(
        entry_price=95,
        fill_price=90,
        scaled_at="2026-01-02",
    )

    assert state.trailing_active is False
    assert state.highest_price == pytest.approx(95)
    assert state.lowest_price == pytest.approx(90)
    assert state.opened_at == pd.Timestamp("2026-01-02")
