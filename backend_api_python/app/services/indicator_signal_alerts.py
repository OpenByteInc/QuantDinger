"""Indicator signal alert tasks.

This service monitors chart-only indicator ``output.signals`` and delivers
notifications when the latest bar emits a selected signal. It never places
orders and does not reuse strategy execution state.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
import threading
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from app.services.kline import KlineService
from app.services.signal_notifier import SignalNotifier
from app.services.user_preferences import get_notification_settings
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.notification_display import with_display
from app.utils.safe_exec import safe_exec_indicator_isolated


logger = get_logger(__name__)


def _json_loads(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return fallback


def _json_dumps(value: Any) -> str:
    def _default(obj: Any) -> Any:
        if isinstance(obj, (datetime, pd.Timestamp)):
            return obj.isoformat()
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            if math.isnan(float(obj)):
                return None
            return float(obj)
        if isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        return str(obj)

    return json.dumps(value or {}, ensure_ascii=False, default=_default)


def _json_safe(value: Any) -> Any:
    """Convert indicator output into a strict JSON-compatible structure."""
    if value is None:
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.ndarray):
        return [_json_safe(item) for item in value.tolist()]
    if isinstance(value, pd.Series):
        return [_json_safe(item) for item in value.tolist()]
    if isinstance(value, pd.DataFrame):
        return [_json_safe(row) for row in value.to_dict(orient="records")]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return str(value)


def _now() -> datetime:
    return datetime.utcnow()


def _normalize_channel(channel: str) -> str:
    c = (channel or "").strip().lower()
    if c in ("in_app", "site", "browser", "站内"):
        return "browser"
    if c in ("mail", "email", "邮件"):
        return "email"
    if c in ("tg", "telegram"):
        return "telegram"
    if c in ("webhook", "hook"):
        return "webhook"
    return c


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower()


_FALSE_SIGNAL_STRINGS = {"", "0", "false", "none", "null", "nan", "na", "n/a"}


def _signal_marker_is_active(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, dict):
        for key in ("active", "signal", "triggered", "hit", "visible"):
            if key in value and value.get(key) is not None:
                return _signal_marker_is_active(value.get(key))
        for key in ("price", "value", "y", "data"):
            if key in value and _signal_marker_is_active(value.get(key)):
                return True
        return False
    try:
        if pd.isna(value):
            return False
    except Exception:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_SIGNAL_STRINGS
    try:
        number = float(value)
        return math.isfinite(number) and number != 0
    except (TypeError, ValueError):
        return False


def _signal_marker_price(value: Any, fallback: float) -> float:
    if isinstance(value, dict):
        for key in ("price", "value", "y", "data"):
            if key in value:
                return _safe_float(value.get(key), fallback)
        return fallback
    if isinstance(value, (bool, np.bool_)):
        return fallback
    return _safe_float(value, fallback)


def _signal_render_mode(signal: Dict[str, Any], data: List[Any]) -> str:
    raw_mode = str(signal.get("renderMode") or signal.get("mode") or "").strip().lower()
    if raw_mode in ("event", "events"):
        return "events"
    if raw_mode in ("point", "points", "marker", "markers", "raw"):
        return "points"
    if raw_mode in ("state", "continuous", "condition"):
        return "edge"
    if not data:
        return "events"
    active_count = sum(1 for value in data if _signal_marker_is_active(value))
    return "edge" if active_count / len(data) > 0.18 else "events"


def _signal_marker_should_notify(signal: Dict[str, Any], data: List[Any], idx: int) -> bool:
    if idx < 0 or idx >= len(data) or not _signal_marker_is_active(data[idx]):
        return False
    if _signal_render_mode(signal, data) != "edge":
        return True
    return idx == 0 or not _signal_marker_is_active(data[idx - 1])


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default


def _fmt_decimal(value: Any, max_decimals: int = 8) -> str:
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return ""
        return f"{f:.{max_decimals}f}".rstrip("0").rstrip(".") or "0"
    except Exception:
        return ""


def _html_escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _signal_side(label: Any, signal_type: Any) -> str:
    text = f"{label or ''} {signal_type or ''}".strip().lower()
    if any(token in text for token in ("buy", "long", "cross up", "enter", "一买", "二买", "三买")):
        return "buy"
    if any(token in text for token in ("sell", "short", "cross down", "exit", "一卖", "二卖", "三卖")):
        return "sell"
    if "watch" in text or "observe" in text:
        return "watch"
    return "signal"


def ensure_indicator_signal_alert_schema() -> None:
    """Create the task table for local/updated databases."""
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS qd_indicator_signal_alerts (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
                indicator_id INTEGER NOT NULL,
                indicator_name VARCHAR(160) DEFAULT '',
                market VARCHAR(32) NOT NULL,
                symbol VARCHAR(64) NOT NULL,
                symbol_name VARCHAR(128) DEFAULT '',
                timeframe VARCHAR(16) NOT NULL DEFAULT '1D',
                signal_keys TEXT DEFAULT '[]',
                channels TEXT DEFAULT '["browser"]',
                target_json TEXT DEFAULT '{}',
                param_json TEXT DEFAULT '{}',
                status VARCHAR(16) NOT NULL DEFAULT 'running',
                last_bar_time VARCHAR(64) DEFAULT '',
                last_fingerprint VARCHAR(255) DEFAULT '',
                last_signal_payload TEXT DEFAULT '{}',
                last_error TEXT DEFAULT '',
                check_count INTEGER NOT NULL DEFAULT 0,
                trigger_count INTEGER NOT NULL DEFAULT 0,
                next_check_at TIMESTAMP DEFAULT NOW(),
                last_checked_at TIMESTAMP NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_indicator_signal_alerts_user_id ON qd_indicator_signal_alerts(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_indicator_signal_alerts_status_next ON qd_indicator_signal_alerts(status, next_check_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_indicator_signal_alerts_indicator_id ON qd_indicator_signal_alerts(indicator_id)")
        db.commit()
        cur.close()


class IndicatorSignalAlertService:
    """CRUD and evaluation logic for indicator signal alerts."""

    def __init__(self) -> None:
        self.kline = KlineService()
        self.notifier = SignalNotifier()

    def preview_chart(self, user_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Run a user's saved indicator once and return chart-only data."""
        try:
            indicator_id = int(payload.get("indicator_id") or 0)
        except (TypeError, ValueError):
            indicator_id = 0
        if indicator_id <= 0:
            raise ValueError("indicator_id is required")

        market = str(payload.get("market") or "Crypto").strip() or "Crypto"
        symbol = str(payload.get("symbol") or "").strip()
        timeframe = str(payload.get("timeframe") or "1h").strip() or "1h"
        if not symbol:
            raise ValueError("symbol is required")
        try:
            limit = max(60, min(int(payload.get("limit") or 180), 360))
        except (TypeError, ValueError):
            limit = 180
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}

        indicator = self._load_indicator_for_user(int(user_id), indicator_id)
        code = str(indicator.get("runtime_code") or indicator.get("code") or "")
        if not code.strip():
            raise ValueError("Indicator code is empty")

        bars = self.kline.get_kline(
            market=market,
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            exchange_id=str(payload.get("exchange_id") or "").strip() or None,
            market_type=str(payload.get("market_type") or "").strip() or None,
        )
        df = self._bars_to_df(bars)
        if df.empty:
            raise ValueError("No K-line data")

        output = self._execute_indicator(code, df, params)
        plots = output.get("plots") if isinstance(output.get("plots"), list) else []
        layers = output.get("layers") if isinstance(output.get("layers"), list) else []
        signals = output.get("signals") if isinstance(output.get("signals"), list) else []
        latest_signal = self._latest_chart_signal(signals, df)

        return _json_safe({
            "indicator": {
                "id": indicator_id,
                "name": indicator.get("display_name") or indicator.get("name") or "",
            },
            "market": market,
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": df.to_dict(orient="records"),
            "plots": plots,
            "layers": layers,
            "signals": signals,
            "latest_signal": latest_signal,
        })

    def list_tasks(self, user_id: int) -> List[Dict[str, Any]]:
        ensure_indicator_signal_alert_schema()
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT *
                FROM qd_indicator_signal_alerts
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (int(user_id),),
            )
            rows = cur.fetchall() or []
            cur.close()
        return [self._row_to_task(row) for row in rows]

    def create_task(self, user_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        ensure_indicator_signal_alert_schema()
        data = self._sanitize_payload(user_id, payload)
        indicator = self._load_indicator_for_user(user_id, data["indicator_id"])
        data["indicator_name"] = data.get("indicator_name") or indicator.get("display_name") or indicator.get("name") or ""
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_indicator_signal_alerts
                (user_id, indicator_id, indicator_name, market, symbol, symbol_name, timeframe,
                 signal_keys, channels, target_json, param_json, status, next_check_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', NOW(), NOW(), NOW())
                RETURNING *
                """,
                (
                    int(user_id),
                    data["indicator_id"],
                    data["indicator_name"],
                    data["market"],
                    data["symbol"],
                    data["symbol_name"],
                    data["timeframe"],
                    _json_dumps(data["signal_keys"]),
                    _json_dumps(data["channels"]),
                    _json_dumps(data["targets"]),
                    _json_dumps(data["params"]),
                ),
            )
            row = cur.fetchone()
            db.commit()
            cur.close()
        return self._row_to_task(row)

    def update_task(self, user_id: int, task_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        ensure_indicator_signal_alert_schema()
        self._assert_task_owner(user_id, task_id)
        data = self._sanitize_payload(user_id, payload)
        indicator = self._load_indicator_for_user(user_id, data["indicator_id"])
        data["indicator_name"] = data.get("indicator_name") or indicator.get("display_name") or indicator.get("name") or ""
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_indicator_signal_alerts
                SET indicator_id = ?,
                    indicator_name = ?,
                    market = ?,
                    symbol = ?,
                    symbol_name = ?,
                    timeframe = ?,
                    signal_keys = ?,
                    channels = ?,
                    target_json = ?,
                    param_json = ?,
                    last_error = '',
                    next_check_at = NOW(),
                    updated_at = NOW()
                WHERE id = ? AND user_id = ?
                RETURNING *
                """,
                (
                    data["indicator_id"],
                    data["indicator_name"],
                    data["market"],
                    data["symbol"],
                    data["symbol_name"],
                    data["timeframe"],
                    _json_dumps(data["signal_keys"]),
                    _json_dumps(data["channels"]),
                    _json_dumps(data["targets"]),
                    _json_dumps(data["params"]),
                    int(task_id),
                    int(user_id),
                ),
            )
            row = cur.fetchone()
            db.commit()
            cur.close()
        return self._row_to_task(row)

    def delete_task(self, user_id: int, task_id: int) -> None:
        ensure_indicator_signal_alert_schema()
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "DELETE FROM qd_indicator_signal_alerts WHERE id = ? AND user_id = ?",
                (int(task_id), int(user_id)),
            )
            db.commit()
            cur.close()

    def set_status(self, user_id: int, task_id: int, status: str) -> Dict[str, Any]:
        ensure_indicator_signal_alert_schema()
        status = "paused" if status == "paused" else "running"
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_indicator_signal_alerts
                SET status = ?, next_check_at = CASE WHEN ? = 'running' THEN NOW() ELSE next_check_at END, updated_at = NOW()
                WHERE id = ? AND user_id = ?
                RETURNING *
                """,
                (status, status, int(task_id), int(user_id)),
            )
            row = cur.fetchone()
            db.commit()
            cur.close()
        if not row:
            raise ValueError("Task not found")
        return self._row_to_task(row)

    def run_due_tasks(self, limit: int = 20) -> int:
        ensure_indicator_signal_alert_schema()
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT *
                FROM qd_indicator_signal_alerts
                WHERE status = 'running'
                  AND (next_check_at IS NULL OR next_check_at <= NOW())
                ORDER BY COALESCE(next_check_at, created_at), id
                LIMIT ?
                """,
                (int(max(1, limit)),),
            )
            rows = cur.fetchall() or []
            cur.close()
        count = 0
        for row in rows:
            try:
                self.evaluate_task(int(row["id"] if isinstance(row, dict) else row[0]))
                count += 1
            except Exception as exc:
                logger.warning("Indicator signal alert task failed: %s", exc)
        return count

    def evaluate_task(self, task_id: int) -> Dict[str, Any]:
        ensure_indicator_signal_alert_schema()
        task = self._load_task(task_id)
        if not task:
            raise ValueError("Task not found")
        if task.get("status") != "running":
            return {"triggered": False, "reason": "paused"}

        next_check = _now() + timedelta(seconds=self._poll_seconds(task.get("timeframe")))
        try:
            indicator = self._load_indicator_for_user(int(task["user_id"]), int(task["indicator_id"]))
            code = str(indicator.get("runtime_code") or indicator.get("code") or "")
            if not code.strip():
                raise ValueError("Indicator code is empty")

            bars = self.kline.get_kline(
                market=task["market"],
                symbol=task["symbol"],
                timeframe=task["timeframe"],
                limit=360,
            )
            df = self._bars_to_df(bars)
            if df.empty:
                raise ValueError("No K-line data")

            output = self._execute_indicator(code, df, task.get("params") or {})
            signal = self._latest_matching_signal(output, df, task.get("signal_keys") or ["any"])
            if not signal:
                self._mark_checked(task_id, next_check=next_check, last_error="")
                return {"triggered": False, "reason": "no_signal"}

            fingerprint = self._signal_fingerprint(task, signal)
            if fingerprint == (task.get("last_fingerprint") or ""):
                self._mark_checked(task_id, next_check=next_check, last_error="")
                return {"triggered": False, "reason": "duplicate"}

            delivery = self._deliver_signal(task, signal)
            self._mark_triggered(task_id, signal, fingerprint, next_check, delivery)
            return {"triggered": True, "signal": signal, "delivery": delivery}
        except Exception as exc:
            logger.warning("Indicator signal alert evaluation failed for %s: %s", task_id, exc)
            logger.debug(traceback.format_exc())
            self._mark_checked(task_id, next_check=next_check, last_error=str(exc)[:1000])
            return {"triggered": False, "reason": "error", "error": str(exc)}

    def _sanitize_payload(self, user_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        profile_settings = get_notification_settings(int(user_id)) or {}
        indicator_id = int(payload.get("indicator_id") or payload.get("indicatorId") or 0)
        if indicator_id <= 0:
            raise ValueError("Missing indicator_id")
        market = str(payload.get("market") or "Crypto").strip()[:32]
        symbol = str(payload.get("symbol") or "").strip()[:64]
        if not symbol:
            raise ValueError("Missing symbol")
        timeframe = str(payload.get("timeframe") or "1D").strip()[:16]
        signal_keys = payload.get("signal_keys") or payload.get("signalKeys") or ["any"]
        if not isinstance(signal_keys, list):
            signal_keys = ["any"]
        signal_keys = [str(k).strip() for k in signal_keys if str(k).strip()] or ["any"]
        channels = payload.get("channels") or profile_settings.get("default_channels") or ["browser"]
        if not isinstance(channels, list):
            channels = ["browser"]
        channels = [_normalize_channel(c) for c in channels]
        channels = [c for c in dict.fromkeys(channels) if c in ("browser", "email", "telegram", "webhook")]
        if not channels:
            channels = ["browser"]
        targets = dict(payload.get("targets")) if isinstance(payload.get("targets"), dict) else {}
        for key in (
            "email",
            "telegram_chat_id",
            "telegram_bot_token",
            "webhook_url",
            "webhook_token",
            "webhook_signing_secret",
        ):
            if key in payload and key not in targets:
                targets[key] = payload.get(key)
        for key in (
            "email",
            "telegram_chat_id",
            "telegram_bot_token",
            "webhook_url",
            "webhook_token",
            "webhook_signing_secret",
        ):
            if not str(targets.get(key) or "").strip():
                targets[key] = str(profile_settings.get(key) or "").strip()

        if "email" in channels and not str(targets.get("email") or "").strip():
            raise ValueError("Missing email notification target")
        if "telegram" in channels and not str(targets.get("telegram_chat_id") or "").strip():
            raise ValueError("Missing Telegram Chat ID")
        if "webhook" in channels and not str(targets.get("webhook_url") or "").strip():
            raise ValueError("Missing Webhook URL")
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        return {
            "user_id": int(user_id),
            "indicator_id": indicator_id,
            "indicator_name": str(payload.get("indicator_name") or payload.get("indicatorName") or "").strip()[:160],
            "market": market,
            "symbol": symbol,
            "symbol_name": str(payload.get("symbol_name") or payload.get("symbolName") or "").strip()[:128],
            "timeframe": timeframe,
            "signal_keys": signal_keys,
            "channels": channels,
            "targets": targets,
            "params": params,
        }

    def _row_to_task(self, row: Any) -> Dict[str, Any]:
        if not row:
            return {}
        data = dict(row)
        data["signal_keys"] = _json_loads(data.get("signal_keys"), ["any"])
        data["channels"] = _json_loads(data.get("channels"), ["browser"])
        data["targets"] = _json_loads(data.get("target_json"), {})
        data["params"] = _json_loads(data.get("param_json"), {})
        data["last_signal_payload"] = _json_loads(data.get("last_signal_payload"), {})
        for key in ("created_at", "updated_at", "next_check_at", "last_checked_at"):
            if data.get(key) is not None:
                data[key] = str(data[key])
        data.pop("target_json", None)
        data.pop("param_json", None)
        return data

    def _load_indicator_for_user(self, user_id: int, indicator_id: int) -> Dict[str, Any]:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, user_id, name, code, description, is_buy, is_encrypted
                FROM qd_indicator_codes
                WHERE id = ? AND user_id = ?
                LIMIT 1
                """,
                (int(indicator_id), int(user_id)),
            )
            row = cur.fetchone()
            cur.close()
        if not row:
            raise ValueError("Indicator not found")
        data = dict(row)
        code = str(data.get("code") or "")
        data["runtime_code"] = code
        data["display_name"] = self._extract_indicator_name(code) or str(data.get("name") or "")
        return data

    def _extract_indicator_name(self, code: str) -> str:
        import re

        match = re.search(r'^\s*my_indicator_name\s*=\s*([\'"])(.*?)\1\s*$', code or "", re.MULTILINE)
        return match.group(2).strip()[:160] if match else ""

    def _assert_task_owner(self, user_id: int, task_id: int) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "SELECT id FROM qd_indicator_signal_alerts WHERE id = ? AND user_id = ?",
                (int(task_id), int(user_id)),
            )
            row = cur.fetchone()
            cur.close()
        if not row:
            raise ValueError("Task not found")

    def _load_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT * FROM qd_indicator_signal_alerts WHERE id = ?", (int(task_id),))
            row = cur.fetchone()
            cur.close()
        return self._row_to_task(row) if row else None

    def _bars_to_df(self, bars: Iterable[Dict[str, Any]]) -> pd.DataFrame:
        rows: List[Dict[str, Any]] = []
        for item in bars or []:
            row = dict(item or {})
            ts = row.get("time") or row.get("timestamp") or row.get("datetime") or row.get("date") or row.get("open_time")
            if isinstance(ts, (int, float)) and ts > 10_000_000_000:
                ts = ts / 1000.0
            rows.append({
                "time": ts,
                "open": _safe_float(row.get("open", row.get("o"))),
                "high": _safe_float(row.get("high", row.get("h"))),
                "low": _safe_float(row.get("low", row.get("l"))),
                "close": _safe_float(row.get("close", row.get("c"))),
                "volume": _safe_float(row.get("volume", row.get("v"))),
            })
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
        return df

    def _execute_indicator(self, code: str, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        result = safe_exec_indicator_isolated(
            code=code,
            df=df,
            params=params,
            timeout=20,
        )
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "Indicator execution failed")

        output = (result.get("result") or {}).get("output")
        if not isinstance(output, dict):
            raise ValueError("Indicator output must be a dict")
        return output

    def _latest_matching_signal(self, output: Dict[str, Any], df: pd.DataFrame, keys: List[str]) -> Optional[Dict[str, Any]]:
        signals = output.get("signals") or []
        # Alert timing follows the live-safe next-bar-open rule:
        # evaluate the last fully closed bar only after a newer bar exists.
        if not isinstance(signals, list) or df.empty or len(df) < 2:
            return None
        idx = len(df) - 2
        notify_idx = len(df) - 1
        bar = df.iloc[idx]
        notify_bar = df.iloc[notify_idx]
        bar_time = self._format_bar_time(bar.get("time"), idx)
        notify_bar_time = self._format_bar_time(notify_bar.get("time"), notify_idx)
        selected = {_normalize_key(k) for k in (keys or ["any"])}
        for sig in signals:
            if not isinstance(sig, dict):
                continue
            data = sig.get("data") if isinstance(sig.get("data"), list) else []
            text_data = sig.get("textData") if isinstance(sig.get("textData"), list) else []
            marker = data[idx] if idx < len(data) else None
            point_text = text_data[idx] if idx < len(text_data) else None
            # Match chart rendering semantics: labels never create a signal,
            # and dense state arrays notify only when they become active.
            if not _signal_marker_should_notify(sig, data, idx):
                continue
            signal_type = str(sig.get("type") or "signal").strip() or "signal"
            label = str(point_text or sig.get("text") or signal_type).strip()
            if not self._matches_selected_signal(selected, signal_type, label):
                continue
            return {
                "type": signal_type,
                "label": label,
                "price": _signal_marker_price(marker, _safe_float(bar.get("close"))),
                "bar_time": bar_time,
                "bar_index": idx,
                "notify_bar_time": notify_bar_time,
                "notify_bar_index": notify_idx,
            }
        return None

    def _latest_chart_signal(self, signals: List[Dict[str, Any]], df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Return the newest visible marker for the mobile chart summary."""
        if df.empty:
            return None
        for idx in range(len(df) - 1, -1, -1):
            for signal in signals:
                if not isinstance(signal, dict):
                    continue
                data = signal.get("data") if isinstance(signal.get("data"), list) else []
                marker = data[idx] if idx < len(data) else None
                if not _signal_marker_should_notify(signal, data, idx):
                    continue
                text_data = signal.get("textData") if isinstance(signal.get("textData"), list) else []
                point_text = text_data[idx] if idx < len(text_data) else None
                signal_type = str(signal.get("type") or "signal").strip() or "signal"
                bar = df.iloc[idx]
                return {
                    "type": signal_type,
                    "label": str(point_text or signal.get("text") or signal_type).strip(),
                    "side": _signal_side(point_text or signal.get("text"), signal_type),
                    "price": _signal_marker_price(marker, _safe_float(bar.get("close"))),
                    "bar_time": self._format_bar_time(bar.get("time"), idx),
                    "bar_index": idx,
                    "color": signal.get("color"),
                }
        return None

    def _matches_selected_signal(self, selected: set[str], signal_type: str, label: str) -> bool:
        if "any" in selected:
            return True
        t = _normalize_key(signal_type)
        l = _normalize_key(label)
        candidates = {f"type:{t}", f"text:{l}", f"signal:{t}", l, t}
        return bool(selected.intersection(candidates))

    def _format_bar_time(self, value: Any, idx: int) -> str:
        if isinstance(value, (int, float)):
            try:
                return datetime.utcfromtimestamp(value).isoformat()
            except Exception:
                return str(value)
        return str(value or idx)

    def _signal_fingerprint(self, task: Dict[str, Any], signal: Dict[str, Any]) -> str:
        raw = "|".join([
            str(task.get("id")),
            str(task.get("indicator_id")),
            str(task.get("market")),
            str(task.get("symbol")),
            str(task.get("timeframe")),
            str(signal.get("bar_time")),
            str(signal.get("type")),
            str(signal.get("label")),
        ])
        return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()

    def _build_indicator_notification(self, task: Dict[str, Any], signal: Dict[str, Any]) -> tuple[str, str, str, str, Dict[str, Any]]:
        indicator_name = str(task.get("indicator_name") or task.get("indicator_id") or "Indicator")
        indicator_id = task.get("indicator_id") or ""
        market = str(task.get("market") or "")
        symbol = str(task.get("symbol") or "")
        symbol_name = str(task.get("symbol_name") or "")
        timeframe = str(task.get("timeframe") or "")
        label = str(signal.get("label") or signal.get("type") or "Signal")
        signal_type = str(signal.get("type") or "")
        side = _signal_side(label, signal_type)
        price = _fmt_decimal(signal.get("price"), 10)
        signal_bar_time = str(signal.get("bar_time") or "")
        notify_bar_time = str(signal.get("notify_bar_time") or "")

        title = f"Indicator Signal | {indicator_name}"
        headline = f"{symbol} {timeframe} {label}".strip()
        summary = f"{headline} @ {price}" if price else headline
        plain_lines = [
            title,
            summary,
            f"Market: {market or '-'}",
            f"Symbol: {symbol or '-'}{f' ({symbol_name})' if symbol_name else ''}",
            f"Timeframe: {timeframe or '-'}",
            f"Signal bar: {signal_bar_time or '-'}",
            f"Notify bar: {notify_bar_time or '-'}",
            "Rule: Signal is confirmed on a closed candle and delivered on the next candle.",
        ]
        message_plain = "\n".join([line for line in plain_lines if line])

        payload: Dict[str, Any] = {
            "event": "qd.indicator_signal",
            "kind": "indicator_signal",
            "version": 1,
            "title": title,
            "message": summary,
            "plain": message_plain,
            "indicator": {
                "id": indicator_id,
                "name": indicator_name,
            },
            "instrument": {
                "market": market,
                "symbol": symbol,
                "name": symbol_name,
                "timeframe": timeframe,
            },
            "signal": {
                "type": signal_type,
                "label": label,
                "side": side,
                "price": price,
                "raw_price": signal.get("price"),
                "bar_time": signal_bar_time,
                "bar_index": signal.get("bar_index"),
                "notify_bar_time": notify_bar_time,
                "notify_bar_index": signal.get("notify_bar_index"),
            },
            "alert": {
                "task_id": task.get("id"),
                "channels": task.get("channels") or [],
            },
            "trace": {
                "source": "indicator_signal_alerts",
                "delivery_rule": "closed_bar_next_open",
                "generated_at": _now().isoformat(),
            },
        }
        payload = with_display(
            payload,
            "indicator.signal",
            {
                "indicatorName": indicator_name,
                "indicatorId": indicator_id,
                "market": market,
                "symbol": symbol,
                "symbolName": symbol_name,
                "timeframe": timeframe,
                "signalType": signal_type,
                "signalLabel": label,
                "signalSide": side,
                "price": price,
                "signalBarTime": signal_bar_time,
                "notifyBarTime": notify_bar_time,
                "taskId": task.get("id") or "",
            },
        )

        side_color = "#22c55e" if side == "buy" else "#ef4444" if side == "sell" else "#f59e0b"
        email_html = f"""
        <div style="font-family:Inter,Arial,sans-serif;background:#0f1115;padding:24px;color:#e5e7eb;">
          <div style="max-width:620px;margin:0 auto;background:#181b20;border:1px solid #2b3139;border-radius:8px;overflow:hidden;">
            <div style="padding:20px 22px;background:linear-gradient(135deg, rgba(239,68,68,.24), rgba(34,197,94,.12));border-bottom:1px solid #2b3139;">
              <div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#9ca3af;">QuantDinger Indicator Signal</div>
              <div style="font-size:20px;font-weight:700;margin-top:6px;color:#fff;">{_html_escape(indicator_name)}</div>
              <div style="margin-top:12px;display:inline-block;padding:7px 11px;border-radius:6px;border:1px solid {side_color};background:rgba(255,255,255,.04);color:{side_color};font-weight:800;">{_html_escape(label)}</div>
            </div>
            <div style="padding:20px 22px;">
              <table style="width:100%;border-collapse:collapse;font-size:14px;">
                <tr><td style="padding:8px 0;color:#9ca3af;">Symbol</td><td style="padding:8px 0;text-align:right;font-weight:700;">{_html_escape(symbol)}</td></tr>
                <tr><td style="padding:8px 0;color:#9ca3af;">Market</td><td style="padding:8px 0;text-align:right;">{_html_escape(market)}</td></tr>
                <tr><td style="padding:8px 0;color:#9ca3af;">Timeframe</td><td style="padding:8px 0;text-align:right;">{_html_escape(timeframe)}</td></tr>
                <tr><td style="padding:8px 0;color:#9ca3af;">Trigger price</td><td style="padding:8px 0;text-align:right;font-weight:700;color:{side_color};">{_html_escape(price or "-")}</td></tr>
                <tr><td style="padding:8px 0;color:#9ca3af;">Signal candle</td><td style="padding:8px 0;text-align:right;">{_html_escape(signal_bar_time or "-")}</td></tr>
                <tr><td style="padding:8px 0;color:#9ca3af;">Notification candle</td><td style="padding:8px 0;text-align:right;">{_html_escape(notify_bar_time or "-")}</td></tr>
              </table>
              <div style="margin-top:18px;padding:12px 14px;border-radius:8px;background:#111827;color:#9ca3af;">
                Signal is confirmed on a closed candle and delivered on the next candle to avoid repaint drift.
              </div>
            </div>
          </div>
        </div>
        """
        telegram_html = (
            f"<b>Indicator Signal</b>\n"
            f"<b>{_html_escape(indicator_name)}</b>\n"
            f"{_html_escape(symbol)} {_html_escape(timeframe)} · {_html_escape(label)}"
            f"{' @ ' + _html_escape(price) if price else ''}\n"
            f"Signal candle: {_html_escape(signal_bar_time or '-')}\n"
            f"Notify candle: {_html_escape(notify_bar_time or '-')}"
        )
        telegram_html = (
            f"<b>Indicator Signal</b>\n"
            f"<b>{_html_escape(indicator_name)}</b>\n"
            f"{_html_escape(symbol)} {_html_escape(timeframe)} | {_html_escape(label)}"
            f"{' @ ' + _html_escape(price) if price else ''}\n"
            f"Signal candle: {_html_escape(signal_bar_time or '-')}\n"
            f"Notify candle: {_html_escape(notify_bar_time or '-')}\n"
            f"<i>Confirmed candle signal, delivered on the next candle.</i>"
        )
        return title, message_plain, email_html, telegram_html, payload

    def _deliver_signal(self, task: Dict[str, Any], signal: Dict[str, Any]) -> Dict[str, Any]:
        channels = sorted({c for c in (_normalize_channel(c) for c in (task.get("channels") or ["browser"])) if c})
        targets = task.get("targets") or {}
        title, message_plain, email_html, telegram_html, payload = self._build_indicator_notification(task, signal)
        result: Dict[str, Any] = {}
        if "browser" in channels:
            ok, msg = self.notifier._notify_browser(
                strategy_id=None,
                symbol=str(task.get("symbol") or ""),
                signal_type="indicator_signal",
                channels=channels,
                title=title,
                message=message_plain,
                payload=payload,
                user_id=int(task.get("user_id") or 1),
            )
            result["browser"] = {"ok": ok, "message": msg}
        if "email" in channels:
            ok, msg = self.notifier._notify_email(
                to_email=str(targets.get("email") or ""),
                subject=title,
                body_text=message_plain,
                body_html=email_html,
            )
            result["email"] = {"ok": ok, "message": msg}
        if "telegram" in channels:
            ok, msg = self.notifier._notify_telegram(
                chat_id=str(targets.get("telegram_chat_id") or targets.get("telegram") or ""),
                text=telegram_html,
                token_override=str(targets.get("telegram_bot_token") or targets.get("telegram_token") or ""),
                parse_mode="HTML",
            )
            result["telegram"] = {"ok": ok, "message": msg}
        if "webhook" in channels:
            ok, msg = self.notifier._notify_webhook(
                url=str(targets.get("webhook_url") or targets.get("webhook") or ""),
                payload=payload,
                headers_override=targets.get("webhook_headers") or targets.get("webhookHeaders") or None,
                token_override=targets.get("webhook_token") or targets.get("webhookToken") or None,
                signing_secret_override=targets.get("webhook_signing_secret") or targets.get("webhookSigningSecret") or None,
            )
            result["webhook"] = {"ok": ok, "message": msg}
        return result

    def _mark_checked(self, task_id: int, *, next_check: datetime, last_error: str) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_indicator_signal_alerts
                SET check_count = check_count + 1,
                    last_checked_at = NOW(),
                    next_check_at = ?,
                    last_error = ?,
                    updated_at = NOW()
                WHERE id = ?
                """,
                (next_check, last_error, int(task_id)),
            )
            db.commit()
            cur.close()

    def _mark_triggered(self, task_id: int, signal: Dict[str, Any], fingerprint: str, next_check: datetime, delivery: Dict[str, Any]) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_indicator_signal_alerts
                SET check_count = check_count + 1,
                    trigger_count = trigger_count + 1,
                    last_checked_at = NOW(),
                    next_check_at = ?,
                    last_bar_time = ?,
                    last_fingerprint = ?,
                    last_signal_payload = ?,
                    last_error = '',
                    updated_at = NOW()
                WHERE id = ?
                """,
                (
                    next_check,
                    str(signal.get("bar_time") or ""),
                    fingerprint,
                    _json_dumps({"signal": signal, "delivery": delivery}),
                    int(task_id),
                ),
            )
            db.commit()
            cur.close()

    def _poll_seconds(self, timeframe: str) -> int:
        tf = str(timeframe or "").strip()
        mapping = {
            "1m": 20,
            "5m": 30,
            "15m": 60,
            "30m": 90,
            "1H": 120,
            "4H": 300,
            "1D": 600,
            "1W": 1800,
        }
        return mapping.get(tf, 120)


class IndicatorSignalAlertWorker:
    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._service = IndicatorSignalAlertService()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="IndicatorSignalAlertWorker", daemon=True)
        self._thread.start()
        logger.info("Indicator signal alert worker started")

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._service.run_due_tasks(limit=20)
            except Exception as exc:
                logger.warning("Indicator signal alert worker loop failed: %s", exc)
            self._stop.wait(float(os.getenv("INDICATOR_SIGNAL_ALERT_WORKER_INTERVAL", "15")))


_worker: Optional[IndicatorSignalAlertWorker] = None


def get_indicator_signal_alert_worker() -> IndicatorSignalAlertWorker:
    global _worker
    if _worker is None:
        _worker = IndicatorSignalAlertWorker()
    return _worker


def start_indicator_signal_alert_worker() -> None:
    if os.getenv("ENABLE_INDICATOR_SIGNAL_ALERT_WORKER", "true").lower() not in ("1", "true", "yes", "on"):
        logger.info("Indicator signal alert worker disabled")
        return
    ensure_indicator_signal_alert_schema()
    get_indicator_signal_alert_worker().start()
