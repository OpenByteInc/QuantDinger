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

_NON_INDICATOR_ASSET_TYPES = frozenset({"script_template", "bot_preset"})


def is_script_strategy_code(code: str) -> bool:
    """True when code defines ``on_bar(ctx, bar)`` script-strategy handlers."""
    raw = (code or "").strip()
    if not raw:
        return False
    return bool(re.search(r"^\s*def\s+on_bar\s*\(", raw, re.MULTILINE))


def is_indicator_ide_listable(*, code: str = "", asset_type: Optional[str] = "indicator") -> bool:
    """Whether a ``qd_indicator_codes`` row belongs in the indicator IDE sidebar."""
    at = (asset_type or "indicator").strip().lower()
    if at in _NON_INDICATOR_ASSET_TYPES:
        return False
    if is_script_strategy_code(code):
        return False
    return True


def resolve_indicator_asset_type(code: str, asset_type: Optional[str] = "indicator") -> str:
    """Map script-strategy source to ``script_template`` so it stays out of indicator IDE."""
    at = (asset_type or "indicator").strip().lower()
    if at in _NON_INDICATOR_ASSET_TYPES:
        return at
    if is_script_strategy_code(code):
        return "script_template"
    return "indicator"


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
        "version": "indicator-contract-v1",
        "doc": "docs/SIGNAL_EXECUTION_STANDARD_CN.md",
        "workflow": [
            "1. Call this contract (or MCP get_indicator_authoring_contract) before writing code.",
            "2. Write full Python indicator script (not natural language) following required_fields.",
            "3. POST /api/agent/v1/indicators/validate to check sandbox + output contract.",
            "4. POST /api/agent/v1/indicators to save into the user's indicator library (scope W).",
            "5. POST /api/agent/v1/strategies with indicator_id OR indicator_code; "
            "indicator_code is auto-saved to the library when missing indicator_id.",
            "6. POST /api/agent/v1/backtests with the same code to preview performance.",
        ],
        "required_fields": {
            "globals": ["my_indicator_name", "my_indicator_description"],
            "dataframe": "df = df.copy() at start",
            "signals": [
                "df['open_long']", "df['close_long']",
                "df['open_short']", "df['close_short']",
            ],
            "output": "output = {'name', 'plots', 'signals'} with list lengths == len(df)",
            "params": "every # @param must be read via params.get('name', default)",
            "strategy_annotations": "# @strategy entryPct 1  (0-1 ratio, 1 = 100%)",
        },
        "forbidden": [
            "Natural language in backtest `code` field",
            "import os/sys/requests/subprocess",
            "Chaining .rolling/.shift on np.where output without pd.Series(..., index=df.index)",
            "Using legacy df['buy']/df['sell'] for newly generated code",
        ],
        "starter_template": template,
        "minimal_backtest_snippet": (
            "my_indicator_name = \"Agent SMA\"\n"
            "my_indicator_description = \"SMA crossover\"\n"
            "df = df.copy()\n"
            "fast = close.rolling(10).mean()\n"
            "slow = close.rolling(30).mean()\n"
            "cross_up = (fast > slow) & (fast.shift(1) <= slow.shift(1))\n"
            "cross_dn = (fast < slow) & (fast.shift(1) >= slow.shift(1))\n"
            "df['open_long'] = cross_up.fillna(False).astype(bool)\n"
            "df['close_long'] = cross_dn.fillna(False).astype(bool)\n"
            "df['open_short'] = False\n"
            "df['close_short'] = False\n"
            "output = {'name': my_indicator_name, 'plots': [], 'signals': []}\n"
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

    asset_type = resolve_indicator_asset_type(raw)
    if asset_type == "script_template":
        from app.routes.strategy import _validate_strategy_code_internal

        validation = _validate_strategy_code_internal(raw)
        if not validation.get("success"):
            raise ValueError(validation.get("message") or "Unsafe script template code")
    else:
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
        try:
            cur.execute(
                "ALTER TABLE qd_indicator_codes ADD COLUMN IF NOT EXISTS asset_type VARCHAR(32) DEFAULT 'indicator'"
            )
        except Exception:
            pass
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
    """Ensure ``indicator_config`` has ``indicator_id`` when code is embedded.

    Strategies store a JSON snapshot in ``indicator_config``; the indicator IDE
    list reads ``qd_indicator_codes``. Agent-created strategies historically
    only set ``indicator_code``, so indicators never appeared in the library.
    """
    ic = dict(indicator_config or {})
    code = (ic.get("indicator_code") or ic.get("code") or "").strip()
    if not code or not auto_save:
        return ic
    if is_script_strategy_code(code):
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
