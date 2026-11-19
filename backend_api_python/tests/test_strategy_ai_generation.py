from pathlib import Path

import pytest

from app.services.ai_generation_contracts import (
    CTA_STRATEGY_SYSTEM_PROMPT,
    INDICATOR_TO_STRATEGY_SYSTEM_PROMPT,
    PORTFOLIO_STRATEGY_SYSTEM_PROMPT,
)
from app.services.strategy_ai_generation import (
    apply_deterministic_strategy_edit,
    build_strategy_generation_request,
    select_strategy_system_prompt,
    validate_generated_strategy,
)
from app.services.strategy_v2 import StrategyV2ContractError
from app.routes.strategy import (
    STRATEGY_CANDIDATE_MESSAGE_KEY,
    _strategy_ai_billing_feature,
    _strategy_ai_text,
)
from app.services.strategy_ai_workspace import (
    RECENT_MESSAGE_LIMIT,
    WORKSPACE_MESSAGE_LIMIT,
    classify_strategy_ai_intent,
    normalize_asset_type,
    _owned_source,
)


CTA_SPOT = '''"""
BTC Spot Trend

Long-only BTC spot strategy.
"""

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1h")
    context.set_metadata(direction_mode="long_only")
    context.set_warmup(30)

def handle_data(context, data):
    bars = get_history(25, "1h", "close", g.symbol)
    if len(bars) < 25:
        return
    target = 0.8 if float(bars["close"].iloc[-1]) > float(bars["close"].tail(20).mean()) else 0.0
    order_target_percent(g.symbol, target, reason="btc_spot_trend_target")
'''


CTA_SWAP = '''"""
ETH Swap Trend

Explicit leveraged perpetual strategy.
"""

def initialize(context):
    g.symbol = "Crypto:ETH/USDT@swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="4h")
    context.allow_leverage(max_leverage=3)
    context.set_metadata(direction_mode="both")

def handle_data(context, data):
    bars = get_history(2, "4h", "close", g.symbol)
    if len(bars) < 2:
        return
    rising = float(bars["close"].iloc[-1]) > float(bars["close"].iloc[-2])
    get_position(g.symbol, position_side="long")
    get_position(g.symbol, position_side="short")
    order_target_percent(
        g.symbol,
        0.5 if rising else 0.0,
        position_side="long",
        reason="eth_swap_long_target",
    )
    order_target_percent(
        g.symbol,
        0.0 if rising else -0.5,
        position_side="short",
        reason="eth_swap_short_target",
    )
'''


CTA_STOCK = '''"""
MSFT Daily Trend

Long-only US equity strategy.
"""

def initialize(context):
    g.symbol = "USStock:MSFT"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1d")
    context.set_metadata(direction_mode="long_only")

def handle_data(context, data):
    pass
'''


PORTFOLIO = '''"""
US Momentum Basket

Weekly point-in-time portfolio rebalance.
"""

def initialize(context):
    context.set_universe(pool="sp500")
    context.subscribe(frequency="1d")
    context.set_warmup(130)
    run_weekly(rebalance, weekday=1, time="09:35")

def rebalance(context, data):
    symbols = get_universe_stocks()
    selected = symbols[:5]
    positions = get_positions()
    for symbol in positions.keys():
        if symbol not in selected:
            order_target_percent(symbol, 0.0, reason="weekly_selection_exit")
    weight = 1.0 / len(selected) if selected else 0.0
    for symbol in selected:
        order_target_percent(symbol, weight, reason="weekly_selection_target")
'''


def test_strategy_ai_prompts_are_workspace_specific_and_market_explicit():
    assert select_strategy_system_prompt("script") is CTA_STRATEGY_SYSTEM_PROMPT
    assert select_strategy_system_prompt("portfolio_strategy") is PORTFOLIO_STRATEGY_SYSTEM_PROMPT
    assert select_strategy_system_prompt("script", "indicator_conversion") is INDICATOR_TO_STRATEGY_SYSTEM_PROMPT

    assert "exactly one instrument" in CTA_STRATEGY_SYSTEM_PROMPT.lower()
    assert "USStock:MSFT" in CTA_STRATEGY_SYSTEM_PROMPT
    assert "Crypto:BTC/USDT@spot" in CTA_STRATEGY_SYSTEM_PROMPT
    assert "Crypto:BTC/USDT@swap" in CTA_STRATEGY_SYSTEM_PROMPT
    assert "manifest.strategyType == \"cta\"" in CTA_STRATEGY_SYSTEM_PROMPT

    assert "dynamic universe" in PORTFOLIO_STRATEGY_SYSTEM_PROMPT
    assert "point-in-time" in PORTFOLIO_STRATEGY_SYSTEM_PROMPT
    assert "manifest.strategyType == \"portfolio\"" in PORTFOLIO_STRATEGY_SYSTEM_PROMPT
    assert "source instrument and source timeframe exactly" in INDICATOR_TO_STRATEGY_SYSTEM_PROMPT


def test_strategy_ai_request_preserves_a_share_instrument_identity():
    request = build_strategy_generation_request(
        prompt="生成一个贵州茅台日线均线策略",
        asset_type="script",
        context={"instrument": "CNStock:600519.SH", "timeframe": "1d"},
    )
    assert '"required_instrument": "CNStock:600519.SH"' in request
    assert '"required_timeframe": "1d"' in request
    assert "CNStock:600519.SH" in CTA_STRATEGY_SYSTEM_PROMPT


@pytest.mark.parametrize(
    "code,instrument,timeframe",
    [
        (CTA_SPOT, "Crypto:BTC/USDT@spot", "1h"),
        (CTA_SWAP, "Crypto:ETH/USDT@swap", "4h"),
        (CTA_STOCK, "USStock:MSFT", "1d"),
    ],
)
def test_cta_contract_accepts_stock_spot_and_swap_without_reinterpreting_markets(code, instrument, timeframe):
    program = validate_generated_strategy(
        code,
        asset_type="script",
        context={"instrument": instrument, "timeframe": timeframe},
    )
    assert program.manifest.strategy_type == "cta"
    assert [item.key for item in program.manifest.universe.instruments] == [instrument]


def test_portfolio_contract_accepts_dynamic_point_in_time_universe():
    program = validate_generated_strategy(PORTFOLIO, asset_type="portfolio_strategy")
    assert program.manifest.strategy_type == "portfolio"
    assert program.manifest.universe.kind == "dynamic"


def test_workspace_type_and_indicator_context_are_machine_enforced():
    with pytest.raises(StrategyV2ContractError, match="aiManifestTypeMismatch:cta:portfolio"):
        validate_generated_strategy(PORTFOLIO, asset_type="script")
    with pytest.raises(StrategyV2ContractError, match="aiManifestTypeMismatch:portfolio:cta"):
        validate_generated_strategy(CTA_STOCK, asset_type="portfolio_strategy")
    with pytest.raises(StrategyV2ContractError, match="aiInstrumentMismatch"):
        validate_generated_strategy(
            CTA_STOCK,
            asset_type="script",
            generation_mode="indicator_conversion",
            context={"instrument": "Crypto:BTC/USDT@spot", "timeframe": "1d"},
        )
    with pytest.raises(StrategyV2ContractError, match="aiTimeframeMismatch"):
        validate_generated_strategy(
            CTA_STOCK,
            asset_type="script",
            generation_mode="indicator_conversion",
            context={"instrument": "USStock:MSFT", "timeframe": "4h"},
        )


def test_structured_generation_request_keeps_current_source_authoritative():
    request = build_strategy_generation_request(
        prompt="only change the exit rule",
        asset_type="script",
        existing_code=CTA_SPOT,
        context={"instrument": "Crypto:BTC/USDT@spot", "timeframe": "1h"},
    )
    assert '"required_manifest_strategy_type": "cta"' in request
    assert '"required_instrument": "Crypto:BTC/USDT@spot"' in request
    assert '"required_timeframe": "1h"' in request
    assert "Current Strategy API V2 source (source of truth)" in request

    patch_request = build_strategy_generation_request(
        prompt="only change the exit rule",
        asset_type="script",
        existing_code=CTA_SPOT,
        response_mode="patch",
    )
    assert "minimal exact replacements" in patch_request
    assert "complete replacement candidate" not in patch_request


def test_strategy_workspace_memory_is_bounded_and_asset_scoped():
    assert normalize_asset_type("script") == "script"
    assert normalize_asset_type("portfolio") == "portfolio_strategy"
    assert RECENT_MESSAGE_LIMIT == 8
    assert RECENT_MESSAGE_LIMIT <= WORKSPACE_MESSAGE_LIMIT <= 100
    assert classify_strategy_ai_intent("解释当前止损逻辑") == "discussion"
    assert classify_strategy_ai_intent("把止损改成 ATR 两倍") == "modify"
    assert classify_strategy_ai_intent("我让你直接在代码里改啊") == "modify"
    assert classify_strategy_ai_intent("我让你改啊") == "modify"
    assert classify_strategy_ai_intent("不要再解释，直接改代码") == "modify"


def test_strategy_workspace_turn_uses_model_routing_and_filters_discussion_history():
    route_path = Path(__file__).parents[1] / "app" / "routes" / "strategy.py"
    source = route_path.read_text(encoding="utf-8")

    assert "resolve_authoring_intent(" in source
    assert 'asset_kind="portfolio_strategy" if asset_type == "portfolio_strategy" else "cta_strategy"' in source
    assert 'item.get("message_type") or "") == "discussion"' in source


def test_single_timeframe_edit_is_minimal_and_updates_runtime_dependencies():
    result = apply_deterministic_strategy_edit(
        CTA_SWAP,
        "把策略周期从 4h 改成 1h",
    )

    assert result is not None
    candidate, plan = result
    assert candidate == CTA_SWAP.replace('"4h"', '"1h"')
    assert plan["executor"] == "deterministic"
    assert plan["operation"] == "set_single_timeframe"
    assert plan["from"] == "4h"
    assert plan["to"] == "1h"
    assert plan["changed"] is True
    assert plan["replacement_count"] == 2
    assert plan["resolved_from"] == "把策略周期从 4h 改成 1h"
    assert plan["operation_count"] == 2
    assert all(operation["oldText"] in CTA_SWAP for operation in plan["operations"])
    program = validate_generated_strategy(candidate, asset_type="script")
    assert program.manifest.frequencies == ("1h",)


def test_referential_followup_resolves_previous_timeframe_request():
    result = apply_deterministic_strategy_edit(
        CTA_SWAP,
        "我让你直接在代码里改啊",
        summary={"timeframe": "1h"},
        recent_messages=[
            {"role": "user", "content": "把策略周期从 4h 改成 1h"},
            {"role": "assistant", "content": "需要将 frequency 改为 1h。"},
        ],
    )

    assert result is not None
    candidate, plan = result
    assert 'context.subscribe(frequency="1h")' in candidate
    assert 'get_history(2, "1h", "close", g.symbol)' in candidate
    assert plan["resolved_from"] == "把策略周期从 4h 改成 1h"


def test_deterministic_editor_falls_through_for_broad_or_ambiguous_requests():
    assert apply_deterministic_strategy_edit(
        CTA_SWAP,
        "把周期改成 1h，并增加止损",
    ) is None
    assert apply_deterministic_strategy_edit(
        CTA_SWAP.replace(
            '    context.subscribe(frequency="4h")',
            '    context.subscribe(frequency="4h")\n    context.subscribe(frequency="1d")',
        ),
        "把周期改成 1h",
    ) is None


@pytest.mark.parametrize("metadata", [{"code_hidden": True}, {"hide_code": True}])
def test_hidden_purchased_strategy_is_rejected_before_ai_workspace_access(metadata):
    class Cursor:
        def execute(self, *_args, **_kwargs):
            return None

        def fetchone(self):
            return {
                "id": 72,
                "user_id": 7,
                "name": "Protected strategy",
                "description": "",
                "code": "secret",
                "asset_type": "script",
                "metadata": metadata,
            }

    with pytest.raises(PermissionError, match="strategy_source_hidden"):
        _owned_source(Cursor(), 7, 72, "script")


def test_strategy_candidate_status_message_follows_interface_language():
    assert _strategy_ai_text(STRATEGY_CANDIDATE_MESSAGE_KEY, "zh-CN") == (
        "策略候选已生成，并已通过当前 Strategy API V2 工作区契约检查。"
    )
    assert _strategy_ai_text(STRATEGY_CANDIDATE_MESSAGE_KEY, "en-US") == (
        "Candidate generated and validated against the current Strategy API V2 workspace contract."
    )


def test_strategy_ai_billing_matches_indicator_ai_tariff():
    assert _strategy_ai_billing_feature("discussion") == "ai_copilot_chat"
    assert _strategy_ai_billing_feature("modify") == "ai_code_gen"
