import re
from pathlib import Path

from app.services.ai_generation_contracts import (
    INDICATOR_GENERATION_CONTRACT,
    INDICATOR_REPAIR_REQUIREMENTS,
    INDICATOR_SYSTEM_CONTRACT,
    SCRIPT_STRATEGY_QUICK_TOOL_SYSTEM_PROMPT,
    SCRIPT_STRATEGY_REPAIR_REQUIREMENTS,
    SCRIPT_STRATEGY_SYSTEM_PROMPT,
)


def test_strategy_generation_prompt_is_v2_and_source_controlled():
    assert "Strategy API V2" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "initialize(context)" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "context.set_universe" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "context.subscribe" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "run panel owns only initial capital" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "handle_data(context, data)" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "empty or missing existing source as a normal new-strategy state" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "never ask the user to repeat or reconfirm" in SCRIPT_STRATEGY_SYSTEM_PROMPT


def test_strategy_generation_prompt_enforces_crypto_swap_leverage_boundary():
    assert "Crypto perpetual contract ending in `@swap`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "context.allow_leverage(max_leverage=N)" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "Never call `allow_leverage`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "Crypto `@swap`" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS


def test_strategy_generation_prompt_exposes_v2_factor_and_fundamental_contract():
    assert "at least 129 functions, not exactly 129" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "get_fundamentals" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "point-in-time" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "get_index_stocks" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "get_universe_stocks" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "context.set_universe(pool=" in SCRIPT_STRATEGY_SYSTEM_PROMPT


def test_strategy_generation_prompt_documents_exact_history_and_order_signatures():
    assert "get_history(count, frequency=None, field=None, security_list=None)" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "data.history(symbols, count, fields=None)" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "one symbol returns a pandas `DataFrame` directly" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "order_target_percent(symbol, percent)" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "Never pass `context` as their first argument" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "`get_position(symbol)` returns a `Position` object" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "`position.amount`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "single-symbol result is already a DataFrame" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS
    assert "Treat `get_position(symbol)` as a `Position` object" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS


def test_strategy_generation_prompt_rejects_legacy_market_and_position_apis():
    assert 'data.current(symbol, field="close")' in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "There is no `get_current_data` API" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "no `.quantity` or `.cost_basis`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "Replace every `get_current_data` call" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS
    assert "Replace legacy `.quantity` and `.cost_basis`" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS


def test_strategy_generation_prompt_documents_global_schedule_helpers():
    assert "Single-symbol signal strategies normally implement `handle_data(context, data)`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "global helpers `run_daily(callback, time=\"HH:MM\")`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "never as `context.run_daily`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "Schedule helpers are global calls" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS
    assert "Never call them through `context`" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS


def test_strategy_generation_prompt_documents_parameter_discovery_boundary():
    assert "# @param <name>" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert 'context.params.get("name", same_default)' in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "discovery context used by `initialize(context)` has no `params`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "Never read `context.params` in `initialize`" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS
    assert "initial capital, date range, commission, or slippage" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS


def test_strategy_generation_prompt_distinguishes_net_reversal_from_hedge_legs():
    for prompt in (SCRIPT_STRATEGY_SYSTEM_PROMPT, SCRIPT_STRATEGY_REPAIR_REQUIREMENTS):
        assert "`long_only`, `short_only`, `one_way`, `both`, or `neutral`" in prompt
        assert "`one_way`" in prompt
        assert "`both`" in prompt
        assert "changing only metadata" in prompt or "changing metadata alone" in prompt


def test_strategy_generation_prompt_preserves_native_multi_timeframes():
    for frequency in ("5m", "15m", "30m", "1h", "4h", "1d", "1w"):
        assert f"`{frequency}`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "preserve every requested timeframe" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "never collapse `1d + 4h + 1h`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "fastest subscribed timeframe drives" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "monthly bars are not part" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "Do not collapse, resample" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS
    assert "Single-timeframe is the default" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "A request naming one timeframe must remain single-timeframe" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "Otherwise generate a single-timeframe strategy" in SCRIPT_STRATEGY_QUICK_TOOL_SYSTEM_PROMPT
    assert "Keep single-timeframe source single-timeframe" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS


def test_strategy_generation_prompt_matches_market_and_execution_boundaries():
    assert "hard market-data route" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "Never silently replace it with another exchange" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "already filtered to completed bars" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "queued `signal` intent can never become a live order" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "Risk-reducing exits may still complete" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "hard data route and execution identity" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS
    assert "Keep deployment `execution_mode` out of strategy source" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS


def test_indicator_prompt_remains_chart_only():
    assert "chart indicator is visual analysis code only" in INDICATOR_SYSTEM_CONTRACT
    assert "must not open, close, size, backtest, or live trade" in INDICATOR_SYSTEM_CONTRACT
    assert "initialize(context)" in INDICATOR_SYSTEM_CONTRACT


def test_indicator_generation_prompt_uses_notification_safe_signal_contract():
    assert "finite numeric value" in INDICATOR_GENERATION_CONTRACT
    assert "Static `text` or `textData` labels never activate" in INDICATOR_GENERATION_CONTRACT
    assert "Signal names are dynamic" in INDICATOR_GENERATION_CONTRACT
    assert "does not restrict signal names" in INDICATOR_GENERATION_CONTRACT
    assert "one-bar edge events" in INDICATOR_GENERATION_CONTRACT
    assert "never emit `type='up'` or `type='down'`" in INDICATOR_GENERATION_CONTRACT
    assert "never infer activation from `text` or `textData`" in INDICATOR_REPAIR_REQUIREMENTS


def test_indicator_generation_prompt_preserves_pine_projection_semantics():
    assert "Pine pivot backpainting is a visual exception" in INDICATOR_GENERATION_CONTRACT
    assert "`extendRight=True`" in INDICATOR_GENERATION_CONTRACT
    assert "Wilder/RMA seeding" in INDICATOR_GENERATION_CONTRACT
    assert "canonical renderer style fields" in INDICATOR_GENERATION_CONTRACT


def test_indicator_generation_prompt_defines_zone_layer_contract():
    assert "'top': number, 'bottom': number" in INDICATOR_GENERATION_CONTRACT
    assert "never cloud-only `upper`/`lower`" in INDICATOR_GENERATION_CONTRACT
    assert "connecting successive boxes with continuous line plots" in INDICATOR_GENERATION_CONTRACT
    assert "final mutated fill, border, and dashed state" in INDICATOR_REPAIR_REQUIREMENTS


def test_indicator_generation_prompt_preserves_stateful_pine_object_lifecycle():
    assert "chronological single-pass object state machine" in INDICATOR_GENERATION_CONTRACT
    assert "freeze the previous object's endpoint and style" in INDICATOR_GENERATION_CONTRACT
    assert "apply later `set_*` mutations only to the object currently referenced" in INDICATOR_GENERATION_CONTRACT
    assert "merge support, resistance, bullish, bearish" in INDICATOR_GENERATION_CONTRACT
    assert "never concatenate objects by semantic type" in INDICATOR_GENERATION_CONTRACT
    assert "solid historical trend plot" in INDICATOR_GENERATION_CONTRACT
    assert "two source points preserve the exact slope" in INDICATOR_GENERATION_CONTRACT
    assert "without adding a distant autoscale point" in INDICATOR_GENERATION_CONTRACT
    assert "Do not approximate the projection with a horizontal ray" in INDICATOR_GENERATION_CONTRACT


def test_indicator_generation_prompt_uses_native_cloud_contract():
    assert "`line`, `bar`, `circle`, and `cloud`" in INDICATOR_GENERATION_CONTRACT
    assert "'upper': number" in INDICATOR_GENERATION_CONTRACT
    assert "'lower': number" in INDICATOR_GENERATION_CONTRACT
    assert "Translate Pine `fill()`" in INDICATOR_GENERATION_CONTRACT
    assert "never emit one `zone` or box layer per bar" in INDICATOR_GENERATION_CONTRACT
    assert "Preserve valid `cloud` plots" in INDICATOR_REPAIR_REQUIREMENTS
    assert "replace them with one `cloud` plot" in INDICATOR_REPAIR_REQUIREMENTS


def test_indicator_generation_prompt_routes_sparse_price_events_to_signals():
    assert "Pine `plotshape`, labels, diamonds" in INDICATOR_GENERATION_CONTRACT
    assert "to `signals`, not to `circle` plots" in INDICATOR_GENERATION_CONTRACT
    assert "do not change renderer or plot contracts" in INDICATOR_REPAIR_REQUIREMENTS


def test_indicator_generation_and_repair_prompts_are_english_only():
    prompt_text = INDICATOR_GENERATION_CONTRACT + INDICATOR_REPAIR_REQUIREMENTS

    assert not re.search(r"[\u4e00-\u9fff]", prompt_text)
    assert "use English for identifiers" in INDICATOR_GENERATION_CONTRACT
    assert "explicitly requests a target language" in INDICATOR_GENERATION_CONTRACT


def test_indicator_generation_prompt_matches_runtime_sandbox():
    assert "Do not use `locals()`" in INDICATOR_GENERATION_CONTRACT
    assert "`pd` and `np` are preloaded" in INDICATOR_GENERATION_CONTRACT


def test_indicator_ide_hidden_prompt_is_english_and_uses_central_contract():
    route_path = Path(__file__).parents[1] / "app" / "routes" / "indicator.py"
    route_source = route_path.read_text(encoding="utf-8")
    start = route_source.index("def ai_generate():")
    end = route_source.index('@indicator_blp.route("/codeQualityHints"', start)
    ai_generate_source = route_source[start:end]

    assert not re.search(r"[\u4e00-\u9fff]", ai_generate_source)
    assert '"\\n\\n" + INDICATOR_GENERATION_CONTRACT' in ai_generate_source
    assert "`locals()` is allowed" not in ai_generate_source
    assert "shift(1, fill_value=False).astype(bool)" in ai_generate_source
    assert "~s.shift(1).fillna(False)" not in ai_generate_source
    assert "Recognize native `line`, `bar`, `circle`, and `cloud` plots" in ai_generate_source


def test_indicator_quality_endpoint_can_skip_runtime_smoke_for_publishing():
    route_path = Path(__file__).parents[1] / "app" / "routes" / "indicator.py"
    route_source = route_path.read_text(encoding="utf-8")
    start = route_source.index("def code_quality_hints():")
    quality_source = route_source[start:]

    assert 'data.get("runtimeCheck", True) is not False' in quality_source
    assert "if runtime_check and not has_static_error" in quality_source


def test_indicator_publish_enqueues_translation_off_the_request_path():
    root = Path(__file__).parents[1]
    route_source = (root / "app" / "routes" / "indicator.py").read_text(encoding="utf-8")
    celery_source = (root / "app" / "celery_app.py").read_text(encoding="utf-8")
    task_source = (root / "app" / "tasks" / "indicator_translation.py").read_text(encoding="utf-8")

    assert "translate_indicator_metadata.delay(" in route_source
    assert '"app.tasks.indicator_translation"' in celery_source
    assert 'name="quantdinger.tasks.indicator_translation"' in task_source
    assert "WHERE id = ? AND user_id = ? AND name = ? AND description = ?" in task_source


def test_strategy_generator_repairs_invalid_model_output_once(monkeypatch):
    from app.routes import strategy as strategy_route

    compile_calls = []

    class FakeManifest:
        strategy_type = "cta"

    class FakeProgram:
        manifest = FakeManifest()

    def fake_compile(code):
        compile_calls.append(code)
        if code == "invalid source":
            raise ValueError("missing initialize")
        return FakeProgram()

    class FakeLLM:
        def __init__(self):
            self.calls = []

        def call_llm_api(self, **kwargs):
            self.calls.append(kwargs)
            return "```python\nrepaired source\n```"

        def get_code_generation_model(self):
            return "test-model"

    monkeypatch.setattr(strategy_route, "compile_strategy_v2", fake_compile)
    llm = FakeLLM()

    code, program, behavior = strategy_route._compile_or_repair_generated_strategy(
        llm,
        "Build a moving-average strategy",
        "invalid source",
    )

    assert code == "repaired source"
    assert isinstance(program, FakeProgram)
    assert behavior["executed"] is False
    assert compile_calls == ["invalid source", "repaired source"]
    assert len(llm.calls) == 1
    assert llm.calls[0]["temperature"] == 0.15
    assert SCRIPT_STRATEGY_REPAIR_REQUIREMENTS in llm.calls[0]["messages"][1]["content"]


def test_strategy_generator_uses_a_bounded_second_error_directed_repair(monkeypatch):
    from app.routes import strategy as strategy_route

    compile_calls = []

    class FakeManifest:
        strategy_type = "cta"

    class FakeProgram:
        manifest = FakeManifest()

    def fake_compile(code):
        compile_calls.append(code)
        if code != "valid source":
            raise ValueError(f"invalid:{code}")
        return FakeProgram()

    class FakeLLM:
        def __init__(self):
            self.calls = []

        def call_llm_api(self, **kwargs):
            self.calls.append(kwargs)
            return "still invalid" if len(self.calls) == 1 else "valid source"

        def get_code_generation_model(self):
            return "test-model"

    monkeypatch.setattr(strategy_route, "compile_strategy_v2", fake_compile)
    llm = FakeLLM()

    code, _program, _behavior = strategy_route._compile_or_repair_generated_strategy(
        llm,
        "Build a moving-average strategy",
        "first invalid",
    )

    assert code == "valid source"
    assert compile_calls == ["first invalid", "still invalid", "valid source"]
    assert len(llm.calls) == 2
    assert "invalid:still invalid" in llm.calls[1]["messages"][1]["content"]


def test_strategy_generator_repairs_runtime_behavior_failures(monkeypatch):
    from app.routes import strategy as strategy_route

    class FakeManifest:
        strategy_type = "cta"

    class FakeProgram:
        manifest = FakeManifest()

    behavior_calls = []

    def fake_behavior(code, _manifest, _intent):
        behavior_calls.append(code)
        if code == "static-only candidate":
            raise ValueError("strategyV2.aiBehaviorOpenLegMissing:long,short")
        return {"executed": True}

    class FakeLLM:
        def __init__(self):
            self.calls = []

        def call_llm_api(self, **kwargs):
            self.calls.append(kwargs)
            return "runtime-valid candidate"

        def get_code_generation_model(self):
            return "test-model"

    monkeypatch.setattr(strategy_route, "compile_strategy_v2", lambda _code: FakeProgram())
    monkeypatch.setattr(strategy_route, "validate_strategy_ai_behavior", fake_behavior)
    llm = FakeLLM()

    code, _program, behavior = strategy_route._compile_or_repair_generated_strategy(
        llm,
        "Build a bidirectional Supertrend strategy",
        "static-only candidate",
    )

    assert code == "runtime-valid candidate"
    assert behavior == {"executed": True}
    assert behavior_calls == ["static-only candidate", "runtime-valid candidate"]
    assert "aiBehaviorOpenLegMissing" in llm.calls[0]["messages"][1]["content"]
