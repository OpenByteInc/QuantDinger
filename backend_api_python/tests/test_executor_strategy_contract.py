import pytest
import pandas as pd

from app.services.strategy_runtime.executors import (
    build_executor_strategy_payload,
    executor_templates,
    preview_executor,
)
from app.services.strategy_v2 import compile_strategy_v2
from app.services.strategy_v2 import StrategyV2BacktestRunner, StrategyV2LiveSession
from app.services.strategy_runtime.robot_v2 import migrate_legacy_robot_v2_source


def _robot_payload(executor_type: str, **overrides):
    payload = {
        "executor_type": executor_type,
        "execution_mode": "signal",
        "strategy_name": f"V2 {executor_type}",
        "symbol": "BTC/USDT",
        "market_type": "swap",
        "side": "long",
        "timeframe": "15m",
        "leverage": 3,
        "initial_capital": 1000,
        "entry_price": 100,
        "start_price": 90,
        "end_price": 110,
        "grid_count": 5,
        "total_amount_quote": 500,
        "base_order_size": 50,
        "safety_order_size": 75,
        "price_deviation_pct": 0.01,
        "step_multiplier": 1.5,
        "volume_multiplier": 1.5,
        "max_layers": 4,
        "layer_count": 3,
        "orders_per_layer": 2,
        "take_profit_pct": 0.02,
        "trailing_take_profit_enabled": True,
        "trailing_activation_pct": 0.01,
        "trailing_callback_pct": 0.003,
        "hard_stop_pct": 0.1,
        "dca_interval_minutes": 60,
    }
    payload.update(overrides)
    return payload


def test_executor_templates_expose_only_supported_robot_types():
    catalog = executor_templates()
    items = catalog["items"]
    assert {item["executor_type"] for item in items} == {
        "grid",
        "dca",
        "martingale",
        "layered_martingale",
    }
    assert catalog["compatibility"]["strategy"]["api_version"] == 2
    assert catalog["compatibility"]["backtest"]["supported"] is True
    assert catalog["compatibility"]["live"]["credential_required"] is True
    assert catalog["compatibility"]["markets"] == ["Crypto"]
    for item in items:
        defaults = item["defaults"]
        assert defaults["dynamic_anchor"] is True
        assert "initial_capital" not in defaults
        assert "leverage" not in defaults
        if item["executor_type"] in {"dca", "martingale", "layered_martingale"}:
            assert defaults["trailing_take_profit_enabled"] is True
            assert 0 < defaults["trailing_callback_pct"] < defaults["trailing_activation_pct"]


@pytest.mark.parametrize("executor_type", ["grid", "dca", "martingale", "layered_martingale"])
def test_every_robot_generates_a_compilable_strategy_v2_source(executor_type):
    payload = build_executor_strategy_payload(_robot_payload(executor_type), user_id=7)
    program = compile_strategy_v2(payload["code"])

    assert payload["strategy_type"] == "StrategyV2"
    assert payload["template_key"] == f"robot_v2_{executor_type}"
    assert payload["trading_config"]["api_version"] == 2
    assert payload["trading_config"]["strategy_family"] == "robot"
    assert program.manifest.api_version == 2
    assert program.manifest.strategy_type == "cta"
    if executor_type == "dca":
        assert program.manifest.primary_frequency == "1h"
        assert program.manifest.leverage_allowed is False
        assert program.manifest.universe.instruments[0].key == "Crypto:BTC/USDT@spot"
    else:
        assert program.manifest.primary_frequency == "15m"
        assert program.manifest.leverage_allowed is True
        assert program.manifest.max_leverage == 100
        assert program.manifest.universe.instruments[0].key == "Crypto:BTC/USDT@swap"
    assert payload["compatibility"]["strategy"]["editable_source"] is True


def _runtime_frame():
    prices = [100.0, 99.0, 98.0, 101.0, 103.0]
    index = pd.date_range("2026-01-01", periods=len(prices), freq="15min")
    return pd.DataFrame({
        "open": prices,
        "high": [price + 2.0 for price in prices],
        "low": [price - 2.0 for price in prices],
        "close": prices,
        "volume": [100000.0] * len(prices),
    }, index=index)


@pytest.mark.parametrize("executor_type", ["grid", "dca", "martingale", "layered_martingale"])
def test_every_robot_runs_in_backtest_and_live_v2_engines(executor_type):
    payload = build_executor_strategy_payload(
        _robot_payload(
            executor_type,
            initial_position_pct=0.2,
            hard_stop_pct=0.2,
        ),
        user_id=7,
    )
    instrument = (
        "Crypto:BTC/USDT@spot"
        if executor_type == "dca"
        else "Crypto:BTC/USDT@swap"
    )
    frame = _runtime_frame()

    result = StrategyV2BacktestRunner(
        code=payload["code"],
        frames={instrument: frame},
        initial_capital=1000,
        commission=0,
        slippage=0,
        leverage_enabled=executor_type != "dca",
        leverage=1 if executor_type == "dca" else 3,
    ).run()
    session = StrategyV2LiveSession(
        code=payload["code"],
        frames={instrument: frame.iloc[:2]},
        initial_capital=1000,
    )
    intents, _, _ = session.process({instrument: frame.iloc[:2]})

    assert result["engine"]["version"] == "quantdinger-strategy-api-v2"
    assert result["manifest"]["apiVersion"] == 2
    assert result["totalExecutions"] >= 1
    assert intents
    assert all(abs(float(intent.value)) <= 1000 for intent in intents)


@pytest.mark.parametrize("executor_type", ["dca", "martingale", "layered_martingale"])
def test_robot_trailing_take_profit_activates_and_closes_after_pullback(executor_type):
    payload = build_executor_strategy_payload(_robot_payload(executor_type), user_id=7)
    instrument = (
        "Crypto:BTC/USDT@spot"
        if executor_type == "dca"
        else "Crypto:BTC/USDT@swap"
    )
    frame = _runtime_frame().iloc[:2]
    session = StrategyV2LiveSession(
        code=payload["code"],
        frames={instrument: frame},
        initial_capital=1000,
    )

    intents, _, _ = session.process({instrument: frame})

    assert intents
    assert "TAKE_PROFIT = 0.0" in payload["code"]
    assert "trailing_stop_pct=TRAILING_CALLBACK" in payload["code"]
    assert intents[0].protection is not None
    assert intents[0].protection.take_profit_pct == 0
    assert intents[0].protection.trailing_activation_pct == pytest.approx(0.01)
    assert intents[0].protection.trailing_stop_pct == pytest.approx(0.003)

    session.synchronize_positions({
        instrument: {"side": "long", "amount": 1, "avg_cost": 100, "last_price": 100}
    })
    assert session.evaluate_protections(
        {instrument: 102},
        timestamp="2026-01-01 01:00:00",
    ) == []
    restored = StrategyV2LiveSession(
        code=payload["code"],
        frames={instrument: frame},
        initial_capital=1000,
    )
    restored.restore_protection_snapshot(session.protection_snapshot())
    restored.synchronize_positions({
        instrument: {"side": "long", "amount": 1, "avg_cost": 100, "last_price": 102}
    })
    exits = restored.evaluate_protections(
        {instrument: 101.5},
        timestamp="2026-01-01 01:00:01",
    )

    assert len(exits) == 1
    assert exits[0].kind == "target_quantity"
    assert exits[0].value == 0
    assert exits[0].reason == "trailing_stop"


@pytest.mark.parametrize("executor_type", ["dca", "martingale", "layered_martingale"])
def test_robot_can_disable_trailing_take_profit_and_keep_fixed_take_profit(executor_type):
    payload = build_executor_strategy_payload(
        _robot_payload(executor_type, trailing_take_profit_enabled=False),
        user_id=7,
    )
    instrument = (
        "Crypto:BTC/USDT@spot"
        if executor_type == "dca"
        else "Crypto:BTC/USDT@swap"
    )
    frame = _runtime_frame().iloc[:2]
    session = StrategyV2LiveSession(
        code=payload["code"],
        frames={instrument: frame},
        initial_capital=1000,
    )

    intents, _, _ = session.process({instrument: frame})

    assert "TAKE_PROFIT = 0.02" in payload["code"]
    assert intents[0].protection is not None
    assert intents[0].protection.take_profit_pct == pytest.approx(0.02)
    assert intents[0].protection.trailing_stop_pct == 0
    assert intents[0].protection.trailing_activation_pct == 0


@pytest.mark.parametrize("executor_type", ["dca", "martingale", "layered_martingale"])
def test_robot_preview_rejects_invalid_trailing_take_profit(executor_type):
    preview = preview_executor(_robot_payload(
        executor_type,
        trailing_activation_pct=0.002,
        trailing_callback_pct=0.003,
    ))

    assert "invalid_trailing_take_profit" in preview["warnings"]


def test_robot_preview_keeps_each_algorithm_shape():
    grid = preview_executor(_robot_payload("grid"))
    dca = preview_executor(_robot_payload("dca"))
    martingale = preview_executor(_robot_payload("martingale", side="short"))
    layered = preview_executor(_robot_payload("layered_martingale"))

    assert len(grid["levels"]) == 5
    assert len(dca["levels"]) == 4
    assert [level["amount_quote"] for level in dca["levels"]] == [0.25] * 4
    assert [level.get("scheduled_offset_minutes", 0) for level in dca["levels"]] == [0, 60, 120, 180]
    assert {level["side"] for level in martingale["levels"]} == {"short"}
    assert len({level["amount_quote"] for level in martingale["levels"]}) > 1
    assert all("scheduled_bar" not in level for level in martingale["levels"])
    assert len(layered["levels"]) == 6


def test_dca_catalog_and_source_use_a_time_based_fixed_allocation_plan():
    defaults = next(
        item["defaults"] for item in executor_templates()["items"]
        if item["executor_type"] == "dca"
    )
    preview = preview_executor({
        "executor_type": "dca",
        "symbol": "BTC/USDT",
        **defaults,
    })
    payload = build_executor_strategy_payload({
        "executor_type": "dca",
        "execution_mode": "signal",
        "symbol": "BTC/USDT",
        **defaults,
    }, user_id=7)

    assert defaults["market_type"] == "spot"
    assert defaults["side"] == "long"
    assert defaults["timeframe"] == "1H"
    assert defaults["dca_interval_minutes"] == 1440
    assert defaults["dca_max_orders"] == 5
    assert defaults["dca_total_budget_pct"] == pytest.approx(1.0)
    assert "volume_multiplier" not in defaults
    assert [level["amount_quote"] for level in preview["levels"]] == [0.2] * 5
    assert [level.get("scheduled_offset_minutes", 0) for level in preview["levels"]] == [0, 1440, 2880, 4320, 5760]
    assert "DCA_INTERVAL_MINUTES = 1440" in payload["code"]
    assert "DCA_ORDER_PCT = 0.2" in payload["code"]
    assert "Crypto:BTC/USDT@spot" in payload["code"]
    assert "allow_leverage" not in payload["code"]
    assert 'reason="dca_scheduled_order"' in payload["code"]
    assert "PRICE_LEVELS" not in payload["code"]
    assert 'reason="robot_level"' not in payload["code"]


def test_dca_backtest_places_equal_orders_on_the_configured_time_schedule():
    payload = build_executor_strategy_payload(
        _robot_payload(
            "dca",
            dca_interval_minutes=30,
            dca_max_orders=3,
            dca_total_budget_pct=0.6,
            trailing_take_profit_enabled=False,
            take_profit_pct=0,
            hard_stop_pct=0,
        ),
        user_id=7,
    )
    instrument = "Crypto:BTC/USDT@spot"
    index = pd.date_range("2026-01-01", periods=10, freq="15min")
    frame = pd.DataFrame({
        "open": [100.0] * len(index),
        "high": [100.0] * len(index),
        "low": [100.0] * len(index),
        "close": [100.0] * len(index),
        "volume": [100000.0] * len(index),
    }, index=index)

    result = StrategyV2BacktestRunner(
        code=payload["code"],
        frames={instrument: frame},
        initial_capital=1000,
        commission=0,
        slippage=0,
        leverage_enabled=True,
        leverage=1,
    ).run()

    dca_orders = [
        item for item in result["executions"]
        if item.get("reason") == "dca_scheduled_order"
    ]
    assert len(dca_orders) == 3
    assert [item["notional"] for item in dca_orders] == pytest.approx([200, 200, 200])


def test_default_catalog_robot_can_anchor_levels_to_first_market_price():
    payload = build_executor_strategy_payload(
        _robot_payload("grid", dynamic_anchor=True, initial_position_pct=0.2),
        user_id=7,
    )

    assert payload["trading_config"]["executor_config"]["dynamic_anchor"] is True
    assert "DYNAMIC_ANCHOR = True" in payload["code"]
    assert "context.portfolio.starting_cash" in payload["code"]
    assert "AMOUNT_WEIGHTS" in payload["code"]
    assert "reason=\"grid_initial\"" in payload["code"]


def test_default_grid_uses_weights_and_a_minimum_notional_friendly_initial_share():
    defaults = next(
        item["defaults"] for item in executor_templates()["items"]
        if item["executor_type"] == "grid"
    )
    preview = preview_executor({
        "executor_type": "grid",
        "symbol": "BTC/USDT",
        **defaults,
    })

    assert defaults["total_amount_quote"] == defaults["grid_count"]
    assert defaults["initial_position_pct"] == pytest.approx(0.6)
    assert len(preview["levels"]) == 4
    assert all(level["price"] < 1.0 for level in preview["levels"])
    assert all(level["amount_quote"] == pytest.approx(2.0) for level in preview["levels"])
    assert preview["summary"]["total_amount_quote"] == pytest.approx(defaults["grid_count"])

    payload = build_executor_strategy_payload({
        "executor_type": "grid",
        "execution_mode": "signal",
        "symbol": "BTC/USDT",
        **defaults,
    }, user_id=7)
    assert "PRICE_LEVELS = [0.99714286, 0.99142857, 0.98571429, 0.98]" in payload["code"]
    assert "if amount != 0:" in payload["code"]
    assert "restored_value = max(0.0, g.target_value - initial_value)" in payload["code"]


def test_legacy_robot_absolute_allocations_migrate_to_run_capital_weights():
    legacy = """AMOUNTS = [100.0, 300.0]
INITIAL_POSITION_PCT = 0.2
initial_value = sum(AMOUNTS) * INITIAL_POSITION_PCT
g.target_value += float(AMOUNTS[g.next_level] or 0.0)
"""

    migrated = migrate_legacy_robot_v2_source(legacy, "grid")

    assert "AMOUNT_WEIGHTS = [0.25, 0.75]" in migrated
    assert "LEVEL_CAPITAL_FRACTION = 0.8" in migrated
    assert "context.portfolio.starting_cash" in migrated
    assert "AMOUNTS" not in migrated


def test_live_robot_requires_a_saved_exchange_credential():
    with pytest.raises(ValueError, match="LIVE_EXECUTOR_CREDENTIAL_REQUIRED"):
        build_executor_strategy_payload(_robot_payload("grid", execution_mode="live"), user_id=7)

    payload = build_executor_strategy_payload(
        _robot_payload(
            "grid",
            execution_mode="live",
            exchange_config={"credential_id": 42, "exchange_id": "okx"},
        ),
        user_id=7,
    )
    assert payload["exchange_config"]["credential_id"] == 42


def test_dca_is_forced_to_spot_long_and_cannot_enable_leverage():
    payload = build_executor_strategy_payload(
        _robot_payload("dca", market_type="swap", side="short", leverage=20, timeframe="1m"),
        user_id=7,
    )
    program = compile_strategy_v2(payload["code"])

    assert payload["trade_direction"] == "long"
    assert payload["market_type"] == "spot"
    assert payload["timeframe"] == "1H"
    assert payload["leverage"] == 1
    assert payload["leverage_enabled"] is False
    assert program.manifest.leverage_allowed is False
    assert program.manifest.direction_mode == "long_only"
    assert program.manifest.universe.instruments[0].key == "Crypto:BTC/USDT@spot"
    assert 'context.set_metadata(direction_mode="long_only", market_type="spot")' in payload["code"]
    assert "DIRECTION" not in payload["code"]


def test_neutral_grid_generates_dual_leg_v2_and_resting_live_config():
    payload = build_executor_strategy_payload(
        _robot_payload("grid", side="neutral", dynamic_anchor=False),
        user_id=7,
    )

    assert payload["trade_direction"] == "neutral"
    assert payload["compatibility"]["sides"] == ["long", "short", "neutral"]
    assert payload["trading_config"]["bot_type"] == "grid"
    assert payload["trading_config"]["bot_params"]["gridDirection"] == "neutral"
    assert payload["trading_config"]["bot_params"]["initialPositionPct"] == 0
    assert 'position_side="long"' in payload["code"]
    assert 'position_side="short"' in payload["code"]
    assert compile_strategy_v2(payload["code"]).manifest.direction_mode == "neutral"

    instrument = "Crypto:BTC/USDT@swap"
    index = pd.date_range("2026-01-01", periods=3, freq="15min")
    frame = pd.DataFrame({
        "open": [100.0, 100.0, 100.0],
        "high": [111.0, 111.0, 111.0],
        "low": [89.0, 89.0, 89.0],
        "close": [100.0, 100.0, 100.0],
        "volume": [100000.0, 100000.0, 100000.0],
    }, index=index)
    session = StrategyV2LiveSession(
        code=payload["code"],
        frames={instrument: frame.iloc[:2]},
        initial_capital=1000,
    )
    intents, _, _ = session.process({instrument: frame.iloc[:2]})

    assert {intent.position_side for intent in intents} == {"long", "short"}
    assert any(intent.position_side == "long" and intent.value > 0 for intent in intents)
    assert any(intent.position_side == "short" and intent.value < 0 for intent in intents)

    result = StrategyV2BacktestRunner(
        code=payload["code"],
        frames={instrument: frame},
        initial_capital=1000,
        commission=0,
        slippage=0,
        leverage_enabled=True,
        leverage=3,
    ).run()
    assert {row["position_side"] for row in result["executions"]} == {"long", "short"}
    assert result["audit"]["passed"] is True
