import pytest

from app.services.strategy_ai_behavior import validate_strategy_ai_behavior
from app.services.strategy_ai_capabilities import resolve_strategy_generation_intent
from app.services.strategy_ai_generation import validate_generated_strategy
from app.services.strategy_v2 import StrategyV2ContractError


NATIVE_SUPERTREND_SOURCE = '''"""Native bidirectional Supertrend behavior test."""

def initialize(context):
    g.symbol = "Crypto:ETH/USDT@swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1h")
    context.set_warmup(30)
    context.set_metadata(direction_mode="both")

def handle_data(context, data):
    trend = indicator(
        "supertrend",
        g.symbol,
        period=10,
        multiplier=3.0,
        output="direction",
        frequency="1h",
    )
    valid_trend = trend.dropna()
    if len(valid_trend) < 2:
        return
    previous = float(valid_trend.iloc[-2])
    current = float(valid_trend.iloc[-1])
    if previous < 0 and current > 0:
        order_target_percent(
            g.symbol,
            0.15,
            position_side="long",
            stop_loss_pct=0.03,
            take_profit_pct=0.06,
            reason="supertrend_open_long",
        )
    if previous > 0 and current < 0:
        order_target_percent(
            g.symbol,
            -0.15,
            position_side="short",
            stop_loss_pct=0.03,
            take_profit_pct=0.06,
            reason="supertrend_open_short",
        )
'''


NATIVE_RSI_SOURCE = '''"""Native RSI runtime behavior test."""

def initialize(context):
    g.symbol = "USStock:SPY"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1h")
    context.set_warmup(30)
    context.set_metadata(direction_mode="long_only")

def handle_data(context, data):
    values = indicator("rsi", g.symbol, period=14, frequency="1h").dropna()
    if len(values) < 2:
        return
    previous = float(values.iloc[-2])
    current = float(values.iloc[-1])
    if previous <= 50 and current > 50:
        order_target_percent(g.symbol, 0.2, reason="rsi_open_long")
'''


VOLUME_CONFIRMED_RSI_SOURCE = '''"""Volume-confirmed RSI runtime behavior test."""

def initialize(context):
    g.symbol = "USStock:SPY"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1h")
    context.set_warmup(30)
    context.set_metadata(direction_mode="long_only")

def handle_data(context, data):
    values = indicator("rsi", g.symbol, period=14, frequency="1h").dropna()
    bars = get_history(20, "1h", None, g.symbol)
    if len(values) < 1 or len(bars) < 20:
        return
    volume = bars["volume"]
    if float(values.iloc[-1]) > 50 and float(volume.iloc[-1]) > 1.5 * float(volume.mean()):
        order_target_percent(g.symbol, 0.2, reason="volume_rsi_open_long")
'''


def _intent():
    return resolve_strategy_generation_intent(
        prompt="ETH 永续 Supertrend 对冲模式双向持仓，3% 止损和 6% 止盈"
    )


def test_native_supertrend_candidate_opens_both_legs_in_runtime_smoke_test():
    intent = _intent()
    program = validate_generated_strategy(
        NATIVE_SUPERTREND_SOURCE,
        asset_type="script",
        intent=intent,
    )

    result = validate_strategy_ai_behavior(
        NATIVE_SUPERTREND_SOURCE,
        program.manifest,
        intent,
    )

    assert result["executed"] is True
    assert result["total_executions"] > 0
    assert result["opened_sides"] == ["long", "short"]


def test_runtime_smoke_rejects_static_order_paths_that_never_execute():
    source = NATIVE_SUPERTREND_SOURCE.replace(
        "    if previous < 0 and current > 0:\n",
        "    if False and previous < 0 and current > 0:\n",
    ).replace(
        "    if previous > 0 and current < 0:\n",
        "    if False and previous > 0 and current < 0:\n",
    )
    intent = _intent()
    program = validate_generated_strategy(source, asset_type="script", intent=intent)

    with pytest.raises(StrategyV2ContractError, match="aiBehaviorOpenLegMissing:long,short"):
        validate_strategy_ai_behavior(source, program.manifest, intent)


def test_runtime_smoke_applies_to_any_registered_technical_factor():
    intent = resolve_strategy_generation_intent(prompt="使用 RSI 构建 SPY 策略")
    program = validate_generated_strategy(
        NATIVE_RSI_SOURCE,
        asset_type="script",
        intent=intent,
    )

    result = validate_strategy_ai_behavior(
        NATIVE_RSI_SOURCE,
        program.manifest,
        intent,
    )

    assert intent.factor_ids == ("rsi",)
    assert result["executed"] is True
    assert result["opened_sides"] == ["long"]


def test_runtime_smoke_scenario_volume_can_confirm_entries():
    intent = resolve_strategy_generation_intent(prompt="使用 RSI 构建 SPY 策略")
    program = validate_generated_strategy(
        VOLUME_CONFIRMED_RSI_SOURCE,
        asset_type="script",
        intent=intent,
    )

    result = validate_strategy_ai_behavior(
        VOLUME_CONFIRMED_RSI_SOURCE,
        program.manifest,
        intent,
    )

    assert result["opened_sides"] == ["long"]
