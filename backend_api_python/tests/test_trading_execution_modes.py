"""Tests for strict_mode → live execution mode normalization."""
from app.services.trading_executor import (
    _coerce_bool,
    normalize_trading_execution_modes,
)


def test_coerce_bool_string_false():
    assert _coerce_bool("false") is False
    assert _coerce_bool("0") is False
    assert _coerce_bool("true") is True


def test_strict_mode_off_forces_aggressive():
    tc = {"strict_mode": False, "signal_mode": "confirmed", "entry_trigger_mode": "price"}
    normalize_trading_execution_modes(tc)
    assert tc["strict_mode"] is False
    assert tc["signal_mode"] == "aggressive"
    assert tc["exit_signal_mode"] == "aggressive"
    assert tc["entry_trigger_mode"] == "immediate"


def test_strict_mode_on_forces_confirmed():
    tc = {"strictMode": True, "signal_mode": "aggressive"}
    normalize_trading_execution_modes(tc)
    assert tc["strict_mode"] is True
    assert tc["signal_mode"] == "confirmed"
    assert tc["exit_signal_mode"] == "confirmed"


def test_legacy_without_strict_key_keeps_explicit_signal_mode():
    tc = {"signal_mode": "confirmed"}
    normalize_trading_execution_modes(tc)
    assert tc["signal_mode"] == "confirmed"


def test_legacy_without_strict_key_defaults_confirmed():
    tc = {}
    normalize_trading_execution_modes(tc)
    assert tc["signal_mode"] == "confirmed"
    assert tc["exit_signal_mode"] == "confirmed"
