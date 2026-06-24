"""
Trade close / reduce reason codes for qd_strategy_trades.close_reason.

Machine codes are stored in DB; API enriches rows with human-readable labels.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Grid resting order purposes (stored in close_reason on open/close fills)
GRID_LONG_ENTRY = "long_entry"
GRID_LONG_EXIT = "long_exit"
GRID_SHORT_ENTRY = "short_entry"
GRID_SHORT_EXIT = "short_exit"
GRID_INITIAL_LONG = "grid_initial_long"
GRID_INITIAL_SHORT = "grid_initial_short"

# Grid bot — crossing a grid line and reducing opposite exposure
GRID_REDUCE_LONG = "grid_reduce_long"
GRID_REDUCE_SHORT = "grid_reduce_short"
GRID_CLOSE_ALL = "grid_close_all"
GRID_WATERFALL_CLOSE = "grid_waterfall_close"

# Grid bot — dedicated risk exits (P0-2). Triggered by _grid_bot_risk_exits,
# never by the entry_price-based server SL/TP path (which is a no-op for grids).
GRID_EQUITY_STOP_LOSS = "grid_equity_stop_loss"
GRID_EQUITY_TAKE_PROFIT = "grid_equity_take_profit"
GRID_OUT_OF_BOUNDS_UP = "grid_out_of_bounds_up"
GRID_OUT_OF_BOUNDS_DOWN = "grid_out_of_bounds_down"

# Server-side risk exits (trading_config driven)
SERVER_STOP_LOSS = "server_stop_loss"
SERVER_TAKE_PROFIT = "server_take_profit"
SERVER_TRAILING_STOP = "server_trailing_stop"

# Indicator / generic script signal close (non-grid)
INDICATOR_SIGNAL = "indicator_signal"

# Legacy fallback when close_reason column is empty
LEGACY_SIGNAL_TRIGGER = "signal_trigger"

_LABELS_ZH: Dict[str, str] = {
    GRID_LONG_ENTRY: "网格买入开多",
    GRID_LONG_EXIT: "网格卖出平多",
    GRID_SHORT_ENTRY: "网格卖出开空",
    GRID_SHORT_EXIT: "网格买入平空",
    GRID_INITIAL_LONG: "初始底仓开多",
    GRID_INITIAL_SHORT: "初始底仓开空",
    GRID_REDUCE_LONG: "网格减多",
    GRID_REDUCE_SHORT: "网格减空",
    GRID_CLOSE_ALL: "网格全平",
    GRID_WATERFALL_CLOSE: "防瀑布平仓",
    GRID_EQUITY_STOP_LOSS: "网格净值止损",
    GRID_EQUITY_TAKE_PROFIT: "网格净值止盈",
    GRID_OUT_OF_BOUNDS_UP: "网格上轨突破平仓",
    GRID_OUT_OF_BOUNDS_DOWN: "网格下轨突破平仓",
    SERVER_STOP_LOSS: "止损平仓",
    SERVER_TAKE_PROFIT: "止盈平仓",
    SERVER_TRAILING_STOP: "追踪止损平仓",
    INDICATOR_SIGNAL: "信号触发平仓",
    LEGACY_SIGNAL_TRIGGER: "信号触发平仓",
}

_LABELS_EN: Dict[str, str] = {
    GRID_LONG_ENTRY: "Grid buy long",
    GRID_LONG_EXIT: "Grid sell close long",
    GRID_SHORT_ENTRY: "Grid sell short",
    GRID_SHORT_EXIT: "Grid buy cover short",
    GRID_INITIAL_LONG: "Initial long position",
    GRID_INITIAL_SHORT: "Initial short position",
    GRID_REDUCE_LONG: "Grid reduce long",
    GRID_REDUCE_SHORT: "Grid reduce short",
    GRID_CLOSE_ALL: "Grid close all",
    GRID_WATERFALL_CLOSE: "Waterfall protection close",
    GRID_EQUITY_STOP_LOSS: "Grid equity stop-loss",
    GRID_EQUITY_TAKE_PROFIT: "Grid equity take-profit",
    GRID_OUT_OF_BOUNDS_UP: "Grid upper-bound breakout",
    GRID_OUT_OF_BOUNDS_DOWN: "Grid lower-bound breakdown",
    SERVER_STOP_LOSS: "Stop-loss close",
    SERVER_TAKE_PROFIT: "Take-profit close",
    SERVER_TRAILING_STOP: "Trailing stop close",
    INDICATOR_SIGNAL: "Signal close",
    LEGACY_SIGNAL_TRIGGER: "Signal close",
}


def is_exit_trade_type(trade_type: str) -> bool:
    t = str(trade_type or "").strip().lower()
    return t.startswith("close_") or t.startswith("reduce_")


def label_for_reason(reason: str, *, lang: str = "zh") -> str:
    code = str(reason or "").strip()
    if not code:
        return ""
    table = _LABELS_ZH if str(lang or "zh").lower().startswith("zh") else _LABELS_EN
    return table.get(code, code)


def infer_legacy_close_reason(
    trade_type: str,
    *,
    bot_type: str = "",
    stored_reason: str = "",
) -> str:
    """Best-effort reason for rows inserted before close_reason existed."""
    if stored_reason:
        return str(stored_reason).strip()
    if not is_exit_trade_type(trade_type):
        return ""
    bt = str(bot_type or "").strip().lower()
    t = str(trade_type or "").strip().lower()
    if bt in ("grid", "dca"):
        if "long" in t:
            return GRID_REDUCE_LONG
        if "short" in t:
            return GRID_REDUCE_SHORT
    return LEGACY_SIGNAL_TRIGGER


def resolve_close_reason_for_record(
    trade_type: str,
    *,
    signal_reason: str = "",
    trading_config: Optional[Dict[str, Any]] = None,
) -> str:
    """Pick DB close_reason when persisting a close/reduce fill."""
    if not is_exit_trade_type(trade_type):
        return ""
    explicit = str(signal_reason or "").strip()
    if explicit:
        return explicit
    tc = trading_config if isinstance(trading_config, dict) else {}
    bot_type = str(tc.get("bot_type") or "").strip().lower()
    if bot_type in ("grid", "dca"):
        return infer_legacy_close_reason(trade_type, bot_type=bot_type, stored_reason="")
    # Indicator/script closes without an explicit reason: leave empty so UI uses
    return ""


def enrich_trade_row(
    trade: Dict[str, Any],
    *,
    bot_type: str = "",
    lang: str = "zh",
) -> Dict[str, Any]:
    """Add close_reason (resolved), action_note, action_note_en for API consumers.

    Also normalises the new ``grid_matched_profit`` / ``matched_entry_price``
    columns to floats so the frontend can render them without parsing strings.
    """
    out = dict(trade)
    trade_type = str(out.get("type") or "")
    stored = str(out.get("close_reason") or "").strip()
    # Only label rows with a real stored reason (server SL/TP/trailing, grid, etc.).
    # Do not guess "signal_trigger" for legacy indicator closes — that hid stop/TP labels.
    if stored:
        out["close_reason"] = stored
        out["action_note"] = label_for_reason(stored, lang=lang)
        out["action_note_en"] = label_for_reason(stored, lang="en")
    else:
        inferred = ""
        if bot_type in ("grid", "dca"):
            inferred = infer_legacy_close_reason(trade_type, bot_type=bot_type, stored_reason="")
        if inferred:
            out["close_reason"] = inferred
            out["action_note"] = label_for_reason(inferred, lang=lang)
            out["action_note_en"] = label_for_reason(inferred, lang="en")
        else:
            out["close_reason"] = ""
            out["action_note"] = ""
            out["action_note_en"] = ""

    for k in ("matched_entry_price", "grid_matched_profit"):
        if k in out:
            try:
                out[k] = float(out[k] or 0.0)
            except Exception:
                out[k] = 0.0
    return out
