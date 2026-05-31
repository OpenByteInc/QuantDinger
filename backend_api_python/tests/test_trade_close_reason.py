"""Tests for trade close reason codes and API enrichment."""
from app.utils.trade_close_reason import (
    GRID_REDUCE_LONG,
    GRID_WATERFALL_CLOSE,
    SERVER_STOP_LOSS,
    enrich_trade_row,
    infer_legacy_close_reason,
    is_exit_trade_type,
    label_for_reason,
)


def test_is_exit_trade_type():
    assert is_exit_trade_type("close_long")
    assert is_exit_trade_type("reduce_short")
    assert not is_exit_trade_type("open_long")


def test_infer_legacy_grid():
    assert infer_legacy_close_reason("close_long", bot_type="grid") == GRID_REDUCE_LONG
    assert infer_legacy_close_reason("close_short", bot_type="grid") == "grid_reduce_short"


def test_enrich_trade_row_stored_reason():
    row = enrich_trade_row(
        {"type": "close_long", "close_reason": SERVER_STOP_LOSS},
        bot_type="grid",
    )
    assert row["close_reason"] == SERVER_STOP_LOSS
    assert row["action_note"] == "止损平仓"


def test_enrich_waterfall():
    row = enrich_trade_row(
        {"type": "close_short", "close_reason": GRID_WATERFALL_CLOSE},
        bot_type="grid",
    )
    assert "防瀑布" in row["action_note"]


def test_label_unknown_passthrough():
    assert label_for_reason("custom_reason") == "custom_reason"


def test_enrich_indicator_close_without_stored_reason():
    row = enrich_trade_row({"type": "close_long", "close_reason": ""}, bot_type="")
    assert row["action_note"] == ""
    assert row["close_reason"] == ""


def test_enrich_take_profit_label():
    row = enrich_trade_row(
        {"type": "close_long", "close_reason": "server_take_profit"},
        bot_type="",
    )
    assert row["action_note"] == "止盈平仓"
