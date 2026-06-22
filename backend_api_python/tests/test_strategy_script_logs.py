"""Tests for StrategyScriptContext user log buffering (PR #123)."""
from app.services.strategy_script_runtime import StrategyScriptContext


def test_flush_logs_returns_and_clears_buffer():
    ctx = StrategyScriptContext()
    ctx.log("line-a")
    ctx.log("line-b")
    out = ctx.flush_logs()
    assert out == ["line-a", "line-b"]
    assert ctx.flush_logs() == []


def test_flush_logs_coerces_to_string():
    ctx = StrategyScriptContext()
    ctx.log(42)
    assert ctx.flush_logs() == ["42"]
