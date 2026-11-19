"""Indicator workspace helpers shared by human routes and Agent Gateway."""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from app.services.indicator_default_template import build_default_indicator_template
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.safe_exec import validate_code_safety

logger = get_logger(__name__)

_NON_INDICATOR_ASSET_TYPES = frozenset({"script_template"})


def is_indicator_ide_listable(*, code: str = "", asset_type: Optional[str] = "indicator") -> bool:
    """Whether a ``qd_indicator_codes`` row belongs in the indicator IDE sidebar."""
    del code
    at = (asset_type or "indicator").strip().lower()
    return at not in _NON_INDICATOR_ASSET_TYPES


def extract_indicator_meta_from_code(code: str) -> Dict[str, str]:
    """Parse ``my_indicator_name`` / ``my_indicator_description`` assignments."""
    if not code or not isinstance(code, str):
        return {"name": "", "description": ""}
    name_match = re.search(
        r'^\s*my_indicator_name\s*=\s*([\'"])(.*?)\1\s*$', code, re.MULTILINE,
    )
    desc_match = re.search(
        r'^\s*my_indicator_description\s*=\s*([\'"])(.*?)\1\s*$', code, re.MULTILINE,
    )
    name = (name_match.group(2).strip() if name_match else "")[:100]
    description = (desc_match.group(2).strip() if desc_match else "")[:500]
    return {"name": name, "description": description}


def get_indicator_authoring_contract() -> Dict[str, Any]:
    """Machine-readable contract + starter template for external AI agents."""
    template = build_default_indicator_template()
    return {
        "version": "indicator-contract-v3-chart-only",
        "doc": "docs/trading/INDICATOR_DEV_GUIDE_CN.md",
        "workflow": [
            "1. Call this contract (or MCP get_indicator_authoring_contract) before writing code.",
            "2. Write a full Python chart-only indicator script (not natural language) following required_fields.",
            "3. POST /api/agent/v1/indicators/validate to check sandbox + output contract.",
            "4. POST /api/agent/v1/indicators to save into the user's indicator library (scope W).",
            "5. If the user wants backtest/live trading, use the separate Indicator-to-Strategy workflow. Do not add execution behavior to indicator code.",
        ],
        "required_fields": {
            "globals": ["my_indicator_name", "my_indicator_description"],
            "dataframe": "Use df = df.copy() before computations/mutations. Expected columns: open, high, low, close, volume.",
            "output": "Use schema_version 2. output may contain name, plots, signals, layers, tables, bar_colors, background_colors, and calculatedVars; every per-bar rendering list must have exactly len(df) values and use None instead of NaN or infinity.",
            "plots": "Each plot has name, data, color, overlay. Supported types are line, bar, circle, cloud, candlestick, and background. A candlestick point provides open/high/low/close plus optional body, border, and wick colors.",
            "signals": "Chart markers only. Prefer one-bar edge/transition events, not continuous state markers, so notifications do not repeat every bar.",
            "layers": "Optional sparse zones, lines, polylines, and labels only when explicitly useful; default [] for normal indicators.",
            "tables": "Optional fixed chart panels with position, columns, and rows. Supported positions are top-left, top-right, bottom-left, and bottom-right.",
            "chart_styles": "Use bar_colors and background_colors for per-bar host-candle and chart-background styling.",
            "multi_timeframe": "At most eight same-symbol requests are available through request_series(timeframe, field=None, gaps='off', lookahead='off') and request_lower_tf(timeframe, fields=None). Direct calls use literal supported intervals; user-selectable intervals use indicator_timeframes/indicator_lower_timeframes allowlists and the matching indicator_timeframe_param/indicator_lower_timeframe_param binding. Series history is capped at 1,000 bars and lower-timeframe history at 5,000 bars, so code must guard unavailable coverage.",
            "visual_quality": "Keep 1H and 4H previews readable: use sparse bar-spaced events, bound repeated historical layers, require full rolling-window warm-up unless progressive behavior is intentional, hide helper titles, and do not draw stale or distant levels that distort the active price scale.",
            "params": "Declare knobs with # @param name type default label and read each via params.get('name', default) with matching fallback.",
            "calculatedVars": "Optional JSON-safe diagnostics for the UI; never required for rendering.",
        },
        "forbidden": [
            "Execution/order columns: open_long, close_long, open_short, close_short, add_long, add_short, reduce_long, reduce_short",
            "# @strategy, # signal_form, # exit_owner, # flip_mode, # timeframe, risk, leverage, trade direction, stop loss, take profit, trailing stop",
            "Strategy lifecycle or live-trading handlers",
            "Backtest or broker/account configuration inside indicator code",
            "Natural language instead of Python source",
            "import os/sys/requests/socket/subprocess/threading/sqlite3/multiprocessing",
            "Chaining .rolling/.shift on np.where output without pd.Series(..., index=df.index)",
            "Using legacy df['buy']/df['sell'] for newly generated code",
        ],
        "starter_template": template,
        "minimal_indicator_snippet": (
            "my_indicator_name = \"Agent EMA Viewer\"\n"
            "my_indicator_description = \"Chart-only EMA crossover viewer.\"\n"
            "# @param fast_len int 12 Fast EMA period\n"
            "# @param slow_len int 26 Slow EMA period\n"
            "df = df.copy()\n"
            "fast_len = int(params.get('fast_len', 12))\n"
            "slow_len = int(params.get('slow_len', 26))\n"
            "fast = df['close'].ewm(span=fast_len, adjust=False).mean()\n"
            "slow = df['close'].ewm(span=slow_len, adjust=False).mean()\n"
            "cross_up = (fast > slow) & (fast.shift(1) <= slow.shift(1))\n"
            "cross_dn = (fast < slow) & (fast.shift(1) >= slow.shift(1))\n"
            "buy_marks = [float(df['low'].iloc[i] * 0.995) if bool(cross_up.iloc[i]) else None for i in range(len(df))]\n"
            "sell_marks = [float(df['high'].iloc[i] * 1.005) if bool(cross_dn.iloc[i]) else None for i in range(len(df))]\n"
            "output = {\n"
            "    'schema_version': 2,\n"
            "    'name': my_indicator_name,\n"
            "    'plots': [\n"
            "        {'name': 'EMA Fast', 'data': fast.tolist(), 'color': '#ffb020', 'type': 'line', 'lineWidth': 2, 'overlay': True},\n"
            "        {'name': 'EMA Slow', 'data': slow.tolist(), 'color': '#2d8cff', 'type': 'line', 'lineWidth': 2, 'overlay': True},\n"
            "    ],\n"
            "    'signals': [\n"
            "        {'type': 'buy', 'text': 'Cross Up', 'color': '#22c55e', 'data': buy_marks, 'renderMode': 'events'},\n"
            "        {'type': 'sell', 'text': 'Cross Down', 'color': '#ef4444', 'data': sell_marks, 'renderMode': 'events'},\n"
            "    ],\n"
            "    'layers': [],\n"
            "    'tables': [],\n"
            "}\n"
        ),
    }


def validate_indicator_code(code: str, indicator_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run the same validation pipeline as ``/api/indicator/verifyCode``."""
    from app.routes.indicator import _validate_indicator_code_internal

    return _validate_indicator_code_internal(code, indicator_params)


def save_user_indicator(
    *,
    user_id: int,
    code: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    indicator_id: int = 0,
) -> int:
    """Create or update a private (non-market) indicator row for the tenant."""
    raw = (code or "").strip()
    if not raw:
        raise ValueError("code is required")

    asset_type = "indicator"
    is_safe, unsafe_reason = validate_code_safety(raw)
    if not is_safe:
        raise ValueError(f"Unsafe indicator code: {unsafe_reason}")

    meta = extract_indicator_meta_from_code(raw)
    name = (name or meta.get("name") or "").strip() or "Custom Indicator"
    description = (description or meta.get("description") or "").strip()
    now = int(time.time())
    iid = int(indicator_id or 0)

    with get_db_connection() as db:
        cur = db.cursor()
        if iid > 0:
            cur.execute(
                """
                UPDATE qd_indicator_codes
                SET name = ?, code = ?, description = ?, asset_type = ?,
                    updatetime = ?, updated_at = NOW()
                WHERE id = ? AND user_id = ? AND (is_buy IS NULL OR is_buy = 0)
                """,
                (name, raw, description, asset_type, now, iid, int(user_id)),
            )
            if cur.rowcount == 0:
                cur.close()
                raise ValueError(f"Indicator {iid} not found or not editable")
        else:
            cur.execute(
                """
                INSERT INTO qd_indicator_codes
                  (user_id, is_buy, end_time, name, code, description,
                   publish_to_community, pricing_type, price, preview_image, vip_free, asset_type,
                   createtime, updatetime, created_at, updated_at)
                VALUES (?, 0, 1, ?, ?, ?, 0, 'free', 0, '', FALSE, ?, ?, ?, NOW(), NOW())
                """,
                (int(user_id), name, raw, description, asset_type, now, now),
            )
            iid = int(cur.lastrowid or 0)
        db.commit()
        cur.close()
    if iid <= 0:
        raise RuntimeError("Failed to persist indicator")
    return iid


def list_user_indicators(user_id: int, *, limit: int = 50) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 200))
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, user_id, is_buy, name, description, code,
                   COALESCE(asset_type, 'indicator') as asset_type,
                   createtime, updatetime
            FROM qd_indicator_codes
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (int(user_id),),
        )
        rows = cur.fetchall() or []
        cur.close()
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not is_indicator_ide_listable(
            code=r.get("code") or "",
            asset_type=r.get("asset_type") or "indicator",
        ):
            continue
        out.append(
            {
                "id": r.get("id"),
                "name": r.get("name") or "",
                "description": r.get("description") or "",
                "is_buy": int(r.get("is_buy") or 0),
                "createtime": r.get("createtime"),
                "updatetime": r.get("updatetime"),
            }
        )
        if len(out) >= limit:
            break
    return out


def get_user_indicator(user_id: int, indicator_id: int) -> Optional[Dict[str, Any]]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, user_id, is_buy, name, code, description, createtime, updatetime
            FROM qd_indicator_codes
            WHERE id = ? AND user_id = ?
            """,
            (int(indicator_id), int(user_id)),
        )
        row = cur.fetchone()
        cur.close()
    if not row:
        return None
    return {
        "id": row.get("id"),
        "name": row.get("name") or "",
        "description": row.get("description") or "",
        "code": row.get("code") or "",
        "is_buy": int(row.get("is_buy") or 0),
        "createtime": row.get("createtime"),
        "updatetime": row.get("updatetime"),
    }


def link_indicator_config(
    user_id: int,
    indicator_config: Optional[Dict[str, Any]],
    *,
    auto_save: bool = True,
) -> Dict[str, Any]:
    """Ensure an embedded indicator source has a persisted indicator ID."""
    ic = dict(indicator_config or {})
    code = (ic.get("indicator_code") or ic.get("code") or "").strip()
    if not code or not auto_save:
        return ic
    existing_id = ic.get("indicator_id")
    try:
        existing_id = int(existing_id) if existing_id not in (None, "", 0) else 0
    except (TypeError, ValueError):
        existing_id = 0

    if existing_id > 0:
        owned = get_user_indicator(user_id, existing_id)
        if owned:
            ic.setdefault("indicator_name", owned.get("name") or "")
            ic["indicator_code"] = code
            return ic

    meta = extract_indicator_meta_from_code(code)
    name = (ic.get("indicator_name") or meta.get("name") or "Agent Indicator").strip()
    description = (ic.get("indicator_description") or meta.get("description") or "").strip()
    try:
        new_id = save_user_indicator(
            user_id=user_id,
            code=code,
            name=name,
            description=description,
        )
    except Exception as exc:
        logger.warning(f"link_indicator_config: auto-save skipped: {exc}")
        return ic

    ic["indicator_id"] = new_id
    ic["indicator_name"] = name
    ic["indicator_description"] = description
    ic["indicator_code"] = code
    return ic
