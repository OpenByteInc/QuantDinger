import pandas as pd
import pytest

from app.services.ai_generation_contracts import (
    PORTFOLIO_STRATEGY_SYSTEM_PROMPT,
    SCRIPT_STRATEGY_SYSTEM_PROMPT,
)
from app.services.strategy_ai_generation import build_strategy_system_prompt, validate_generated_strategy
from app.services.strategy_v2 import compile_strategy_v2, StrategyV2ContractError
from app.services.strategy_v2.data import MultiAssetDataPortal
from app.services.strategy_v2.protection import ProtectionEngine, ProtectionSpec, ProtectionState
from app.services.strategy_v2.runtime import (
    PortfolioState, StrategyRuntimeContext, StrategyV2BacktestRunner, StrategyV2LiveSession,
)


SYMBOL = "Crypto:SOL/USDT@binance:swap"


def frames():
    return {SYMBOL: pd.DataFrame(
        {"open": [100.0] * 3, "high": [110.0] * 3, "low": [90.0] * 3,
         "close": [102.0] * 3, "volume": [10000.0] * 3},
        index=pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"),
    )}


def source(body="    pass", extra_initialize="", extra_handlers=""):
    return f'''"""Runtime contract probe"""
def initialize(context):
    context.set_universe(["{SYMBOL}"])
    context.subscribe(frequency="1h")
    context.set_metadata(direction_mode="long_only")
{extra_initialize}
def handle_data(context, data):
{body}
{extra_handlers}
'''


@pytest.mark.parametrize("instrument", ["Crypto:SOL/USDT@swap", SYMBOL, "Crypto:SOL/USDT@gate:swap"])
def test_venue_qualified_swaps_activate_the_required_generation_contract(instrument):
    system, intent = build_strategy_system_prompt(
        prompt="Convert the indicator", asset_type="script", generation_mode="indicator_conversion",
        context={"source": "indicator_ide_conversion", "conversionRequest": "", "instrument": instrument},
    )
    assert intent.capabilities == ("crypto_swap",)
    assert 'position_side="long"' in system
    assert compile_strategy_v2(source().replace(SYMBOL, instrument)).manifest.universe.instruments[0].market_type == "swap"


def test_order_reference_is_opt_in_and_not_a_fill_confirmation():
    portal = MultiAssetDataPortal(frames(), driving_frequency="1h")
    portal.set_clock(next(iter(frames().values())).index[0], include_current=True)
    ctx = StrategyRuntimeContext(portal=portal, portfolio=PortfolioState(10000, 10000))
    assert ctx.order_target_percent(SYMBOL, 0.2, position_side="long") is None
    reference = ctx.order_target_percent(SYMBOL, 0.2, position_side="long", client_order_id="probe-1")
    assert reference == "probe-1"
    assert ctx.get_order_status(reference)["status"] == "queued"
    assert ctx.get_position(SYMBOL, position_side="long").amount == 0
    assert "otherwise it returns `None`" in SCRIPT_STRATEGY_SYSTEM_PROMPT


def test_rebalance_receives_a_frame_dictionary_not_a_data_view():
    code = source(extra_initialize="    g.observed = False", extra_handlers=f'''
def on_rebalance(context, data):
    g.observed = "{SYMBOL}" in data and len(data["{SYMBOL}"]) > 0
''')
    runner = StrategyV2BacktestRunner(code=code, frames=frames(), initial_capital=10000)
    runner.run()
    assert runner.program.state.observed
    assert runner.program.manifest.strategy_type == "portfolio"
    assert "dictionary mapping canonical instruments" in PORTFOLIO_STRATEGY_SYSTEM_PROMPT


def test_default_protection_alone_does_not_pass_swap_entry_leg_validation():
    code = source(body=f'''    set_default_protection(stop_loss_pct=0.03)
    order_target_percent("{SYMBOL}", 0.2, position_side="long", reason="entry")''')
    with pytest.raises(StrategyV2ContractError, match="aiProtectionEntryLegsRequired:long"):
        validate_generated_strategy(code, asset_type="script", prompt="Add stop loss")
    protected = code.replace('reason="entry")', 'reason="entry", stop_loss_pct=0.03)')
    validate_generated_strategy(protected, asset_type="script", prompt="Add stop loss")
    system, _ = build_strategy_system_prompt(prompt="Add stop loss", asset_type="script")
    assert "each required entry leg must carry effective protection directly" in system


def test_native_bar_protection_uses_high_low_and_conservative_conflict_priority():
    state = ProtectionState.open(symbol=SYMBOL, side="long", entry_price=100,
                                 spec=ProtectionSpec(stop_loss_pct=0.03, take_profit_pct=0.05),
                                 opened_at="2026-01-01T00:00:00Z")
    decision = ProtectionEngine().evaluate_bar(state, timestamp="2026-01-01T01:00:00Z",
                                             open_price=100, high_price=110, low_price=90)
    assert decision.reason == "stop_loss"
    assert decision.price == 97
    system, _ = build_strategy_system_prompt(prompt="Add stop loss", asset_type="script")
    assert "open/high/low" in system
    assert "not a close-only signal exit" in system


def test_protection_v2_prompt_documents_absolute_points_and_partial_exit():
    system, _ = build_strategy_system_prompt(
        prompt="Add an absolute stop, trailing points, and a partial exit",
        asset_type="script",
    )

    assert "stop_loss_price" in system
    assert "trailing_stop_points" in system
    assert "exit_fraction" in system
    assert "one-shot" in system
    assert "runtime-owned" in system


def test_ai_protection_validation_accepts_zero_as_trigger_fraction_inheritance():
    code = source(body=f'''    order_target_percent(
        "{SYMBOL}", 0.2, position_side="long", reason="entry",
        stop_loss_pct=0.03, exit_fraction=0.5, stop_loss_exit_fraction=0,
    )''')
    validate_generated_strategy(code, asset_type="script", prompt="Add stop loss")


def test_lifecycle_dispatch_differs_between_backtest_and_live():
    code = source(extra_initialize="    g.before = 0\n    g.after = 0", extra_handlers='''
def before_trading_start(context, data):
    g.before += 1
def after_trading_end(context, data):
    g.after += 1
''')
    runner = StrategyV2BacktestRunner(code=code, frames=frames(), initial_capital=10000)
    runner.run()
    session = StrategyV2LiveSession(code=code, frames=frames(), initial_capital=10000)
    for length in range(1, 4):
        session.process({SYMBOL: frames()[SYMBOL].iloc[:length]})
    assert (runner.program.state.before, runner.program.state.after) == (3, 3)
    assert (session.program.state.before, session.program.state.after) == (1, 0)
    assert "does not dispatch `after_trading_end`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
