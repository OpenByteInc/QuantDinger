"""MCP transport results and conservative tool risk annotations."""
from functools import wraps
import json
from typing import Any

from mcp.types import CallToolResult, TextContent, ToolAnnotations

from .security import redact_secrets


WRITE_TOOLS = {
    "stop_strategy": True,
    "place_quick_order": True,
    "emergency_stop_trading": True,
    "cancel_job": True,
    "save_indicator": True,
    "link_indicator_config": False,
    "create_strategy": False,
    "update_strategy": True,
    "save_strategy_source": True,
    "compile_strategy_code": False,
    "restore_strategy_source_version": True,
    "submit_backtest": False,
    "cancel_open_paper_orders": True,
    "add_watchlist": True,
    "remove_watchlist": True,
    "create_signal_alert": True,
    "update_signal_alert": True,
    "set_signal_alert_status": True,
    "delete_signal_alert": True,
    "run_signal_alert": True,
}


def register_tool(server):
    def register(fn):
        writable = fn.__name__ in WRITE_TOOLS
        annotations = ToolAnnotations(
            readOnlyHint=not writable,
            destructiveHint=WRITE_TOOLS.get(fn.__name__, False),
            idempotentHint=not writable,
            openWorldHint=True,
        )

        @wraps(fn)
        def invoke(*args, **kwargs):
            value = fn(*args, **kwargs)
            if isinstance(value, dict) and (value.get("error") or value.get("success") is False or value.get("ok") is False):
                safe = redact_secrets(value)
                return CallToolResult(
                    content=[TextContent(type="text", text=json.dumps(safe, ensure_ascii=False))],
                    structuredContent=safe,
                    isError=True,
                )
            return value

        # Gateway responses have heterogeneous shapes. An inferred Any output
        # model can require a synthetic `result` field that conflicts with our
        # explicit CallToolResult error payload on Python 3.10.
        server.tool(annotations=annotations, structured_output=False)(invoke)
        return fn

    return register
