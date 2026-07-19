import re
from pathlib import Path

from app.services.ai_generation_contracts import (
    INDICATOR_GENERATION_CONTRACT,
    INDICATOR_REPAIR_REQUIREMENTS,
    INDICATOR_SYSTEM_CONTRACT,
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


def test_strategy_generation_prompt_enforces_crypto_swap_leverage_boundary():
    assert "Crypto perpetual contract ending in `@swap`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "context.allow_leverage(max_leverage=N)" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "Never call `allow_leverage`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "Crypto `@swap`" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS


def test_strategy_generation_prompt_exposes_v2_factor_and_fundamental_contract():
    assert "129-function adapter" in SCRIPT_STRATEGY_SYSTEM_PROMPT
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


def test_strategy_generation_prompt_documents_global_schedule_helpers():
    assert "Single-symbol signal strategies normally implement `handle_data(context, data)`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "global helpers `run_daily(callback, time=\"HH:MM\")`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "never as `context.run_daily`" in SCRIPT_STRATEGY_SYSTEM_PROMPT
    assert "Schedule helpers are global calls" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS
    assert "Never call them through `context`" in SCRIPT_STRATEGY_REPAIR_REQUIREMENTS


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
    assert "never infer activation from `text` or `textData`" in INDICATOR_REPAIR_REQUIREMENTS


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


def test_strategy_generator_repairs_invalid_model_output_once(monkeypatch):
    from app.routes import strategy as strategy_route

    compile_calls = []

    def fake_compile(code):
        compile_calls.append(code)
        if code == "invalid source":
            raise ValueError("missing initialize")
        return "compiled-program"

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

    code, program = strategy_route._compile_or_repair_generated_strategy(
        llm,
        "Build a moving-average strategy",
        "invalid source",
    )

    assert code == "repaired source"
    assert program == "compiled-program"
    assert compile_calls == ["invalid source", "repaired source"]
    assert len(llm.calls) == 1
    assert llm.calls[0]["temperature"] == 0.15
    assert SCRIPT_STRATEGY_REPAIR_REQUIREMENTS in llm.calls[0]["messages"][1]["content"]
