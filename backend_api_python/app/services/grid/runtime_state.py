"""Persist grid resting runtime flags in trading_config.script_runtime_state."""

from __future__ import annotations

import json
from typing import Any, Dict

from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

_GRID_STATE_KEY = "grid_resting"


def load_grid_resting_state(trading_config: Dict[str, Any]) -> Dict[str, Any]:
    tc = trading_config if isinstance(trading_config, dict) else {}
    raw = tc.get("script_runtime_state") or {}
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        return {}
    gs = raw.get(_GRID_STATE_KEY)
    return dict(gs) if isinstance(gs, dict) else {}


def persist_grid_resting_state(strategy_id: int, updates: Dict[str, Any]) -> None:
    if not updates:
        return
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT trading_config FROM qd_strategies_trading WHERE id = %s", (int(strategy_id),))
            row = cur.fetchone()
            if not row:
                cur.close()
                return
            tc = row.get("trading_config")
            if isinstance(tc, str) and tc.strip():
                try:
                    tc = json.loads(tc)
                except Exception:
                    tc = {}
            elif not isinstance(tc, dict):
                tc = {}
            raw = tc.get("script_runtime_state") or {}
            if isinstance(raw, str) and raw.strip():
                try:
                    raw = json.loads(raw)
                except Exception:
                    raw = {}
            if not isinstance(raw, dict):
                raw = {}
            gs = raw.get(_GRID_STATE_KEY)
            merged = dict(gs) if isinstance(gs, dict) else {}
            merged.update(updates)
            raw[_GRID_STATE_KEY] = merged
            tc["script_runtime_state"] = raw
            cur.execute(
                "UPDATE qd_strategies_trading SET trading_config = %s WHERE id = %s",
                (json.dumps(tc, ensure_ascii=False), int(strategy_id)),
            )
            db.commit()
            cur.close()
    except Exception as e:
        logger.warning("persist_grid_resting_state sid=%s: %s", strategy_id, e)
