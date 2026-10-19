"""Regression tests for _strip_code_fences.

Reasoning models (MiniMax-M3, DeepSeek-R1, ...) prepend <think>...</think>
chains of thought, and models often wrap code in markdown fences with
surrounding prose. Both shapes used to survive stripping and fail the
ast.parse safety validation with "Unsafe code rejected: Code syntax error".
"""
import ast

import pytest

from app.services.indicator_ai_generation import _strip_code_fences

CODE = "def sma(close, n):\n    return close.rolling(window=n).mean()"


def assert_valid_python(result: str) -> None:
    ast.parse(result)


def test_plain_code_unchanged():
    assert _strip_code_fences(CODE) == CODE


def test_simple_fence_stripped():
    assert _strip_code_fences(f"```python\n{CODE}\n```") == CODE


def test_bare_fence_stripped():
    assert _strip_code_fences(f"```\n{CODE}\n```") == CODE


def test_think_block_removed():
    raw = (
        "<think>The user wants an SMA. I will use pandas rolling.</think>\n\n"
        f"{CODE}"
    )
    result = _strip_code_fences(raw)
    assert_valid_python(result)
    assert result == CODE


def test_think_block_multiline_removed():
    raw = (
        "<think>\nLine one of reasoning.\nLine two.\n</think>\n\n"
        f"{CODE}"
    )
    result = _strip_code_fences(raw)
    assert_valid_python(result)
    assert result == CODE


def test_think_then_fenced_code():
    raw = (
        "<think>Reasoning here.</think>\n\n"
        f"```python\n{CODE}\n```"
    )
    result = _strip_code_fences(raw)
    assert_valid_python(result)
    assert result == CODE


def test_unterminated_think_block_truncated():
    raw = f"{CODE}\n\n<think>model output was cut off mid-reasoning"
    result = _strip_code_fences(raw)
    assert_valid_python(result)
    assert result == CODE


def test_prose_around_fenced_code_returns_last_block():
    raw = (
        "Here is your indicator:\n\n"
        f"```python\n{CODE}\n```\n\n"
        "This computes a simple moving average."
    )
    result = _strip_code_fences(raw)
    assert_valid_python(result)
    assert result == CODE


def test_multiple_fences_returns_last_block():
    draft = "def draft():\n    pass\n"
    raw = f"```python\n{draft}\n```\n\nRevised:\n\n```python\n{CODE}\n```"
    result = _strip_code_fences(raw)
    assert result == CODE


def test_empty_and_none_inputs():
    assert _strip_code_fences(None) == ""
    assert _strip_code_fences("") == ""
    assert _strip_code_fences("   ") == ""


def test_case_insensitive_think_tag():
    raw = f"<THINK>reasoning</THINK>\n\n{CODE}"
    result = _strip_code_fences(raw)
    assert_valid_python(result)
    assert result == CODE


def test_prose_only_without_fences_is_returned_as_is():
    # No fenced block and no think tag: behavior is unchanged (the caller's
    # validation will reject it), we must not silently invent code.
    raw = "Sorry, I cannot generate that indicator."
    assert _strip_code_fences(raw) == raw
