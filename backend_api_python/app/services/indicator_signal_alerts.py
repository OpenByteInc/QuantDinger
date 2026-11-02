"""Indicator signal alert tasks.

This service monitors chart-only indicator ``output.signals`` and delivers a
notification for every selected marker the chart draws: on each candle as it
closes, or already on the forming candle for pre-close and realtime alerts. It
never places orders and does not reuse strategy execution state.
"""
from __future__ import annotations

import functools
import hashlib
import html
import json
import math
import os
import threading
import traceback
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.services.indicator_signal_chart import render_signal_chart
from app.services.indicator_signal_i18n import indicator_signal_texts, indicator_signal_timezone
from app.services.kline import KlineService
from app.services.signal_notifier import SignalNotifier
from app.services.strategy_v2.frequencies import frequency_seconds, normalize_frequency
from app.services.user_preferences import get_notification_settings
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.notification_display import with_display
from app.utils.safe_exec import safe_exec_indicator_isolated


logger = get_logger(__name__)

# Closed candles re-scanned after a missed check (e.g. a worker restart); older markers are stale.
_CATCH_UP_BARS = 3
# Fingerprints kept so a marker sent on the forming candle is not sent again when that candle closes.
_SENT_MEMORY = 32
# Pre-close alerts read the forming candle from this long before it closes, so people have time to
# act. Replaying a 5m indicator on ETH/USDT with 5 s checks, markers present a minute before the
# close rarely vanished (3 in 100 candles) while markers sent on first sight often did (44).
_PRE_CLOSE_SECONDS = 60
# Gales after the entry candle for alerts that mark their Telegram messages WIN/LOSS ('off' marks none).
_RESULT_GALES = {"next": 0, "gale1": 1, "gale2": 2}
# A result still undecided this long after its last candle should have closed is given up.
_RESULT_GIVE_UP_SECONDS = 3600


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


def _fmt_price(value: Any) -> str:
    """Round a price for people: about six significant digits, never fewer than two decimals."""
    try:
        magnitude = abs(float(value))
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(magnitude):
        return ""
    exponent = math.floor(math.log10(magnitude)) if magnitude > 0 else 0
    return _fmt_decimal(value, min(10, max(2, 5 - exponent)))


def _zone(name: Any) -> Optional[tzinfo]:
    try:
        return ZoneInfo(str(name or "").strip())
    except Exception:  # empty, unknown or malformed names
        return None


def _alert_zone(task: Dict[str, Any]) -> tzinfo:
    """Zone for people-facing times: the profile's, else the browser's when the alert was
    saved, else the usual one for the alert's UI language, else UTC."""
    targets = task.get("targets") or {}
    for name in (task.get("user_timezone"), targets.get("timezone"), indicator_signal_timezone(targets.get("language"))):
        zone = _zone(name)
        if zone:
            return zone
    return timezone.utc


def _trigger_mode(task: Dict[str, Any]) -> str:
    """When an alert reads the forming candle, whose marker can still vanish before it closes:
    'close' (default) never, 'preclose' from ``_PRE_CLOSE_SECONDS`` before it closes, 'realtime' always."""
    mode = (task.get("targets") or {}).get("trigger_mode")
    return mode if mode in ("preclose", "realtime") else "close"


def _pre_close_lead(timeframe: Any) -> timedelta:
    # At most half a candle, so 1m alerts are not read on the candle's first tick.
    return timedelta(seconds=min(_PRE_CLOSE_SECONDS, frequency_seconds(timeframe) // 2))


def _utc(epoch: float) -> datetime:
    """Epoch seconds as naive UTC, like ``_now()``."""
    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None)


def _candle_close_at(df: pd.DataFrame, timeframe: Any) -> Optional[datetime]:
    """When the forming (last) candle closes, as naive UTC like ``_now()``; None unless bar times are epoch seconds."""
    try:
        return _utc(float(df["time"].iloc[-1]) + frequency_seconds(timeframe))
    except (IndexError, KeyError, TypeError, ValueError, OverflowError, OSError):
        return None


def _result_gales(task: Dict[str, Any]) -> Optional[int]:
    """Gales after the entry candle when the alert marks its Telegram messages WIN/LOSS; None when it does not."""
    return _RESULT_GALES.get((task.get("targets") or {}).get("result_tracking"))


def _epoch(bar_time: Any) -> Optional[int]:
    """A bar time (naive UTC ISO, see _format_bar_time) as epoch seconds; None when bar times are not clock times."""
    try:
        return int(datetime.fromisoformat(str(bar_time)).replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def _trade_outcome(df: pd.DataFrame, entry_time: int, side: str, gales: int, step_seconds: int) -> Dict[str, Any]:
    """Fixed-time result of entering at ``entry_time`` (a candle open, epoch seconds): the candle has to
    close past the close before it in the signal's direction (a tie loses, like the indicators' hit
    counters); a lost candle moves on to the next one while gales remain. Until the candle it needs has
    closed (the last row is the forming candle): ``{"status": "pending", "closes_at": epoch}``."""
    times = [int(float(t)) for t in df["time"]]
    closes = [float(c) for c in df["close"]]
    sign = 1 if side == "buy" else -1
    for step in range(gales + 1):
        opened = entry_time + step * step_seconds
        i = times.index(opened) if opened in times else -1
        if i < 1 or i == len(times) - 1:  # not served yet, still forming, or no candle before it
            return {"status": "pending", "closes_at": opened + step_seconds}
        entry, exit_ = closes[i - 1], closes[i]
        if sign * (exit_ - entry) > 0:
            return {"status": "win", "step": step, "entry": entry, "exit": exit_}
    return {"status": "loss", "step": gales, "entry": entry, "exit": exit_}


def _reads_forming_candle(mode: str, timeframe: Any, now: datetime, close_at: Optional[datetime]) -> bool:
    if mode == "realtime":
        return True
    return mode == "preclose" and close_at is not None and now >= close_at - _pre_close_lead(timeframe)


def _fmt_candle_time(value: Any, zone: tzinfo = timezone.utc) -> str:
    """Show a bar time (naive UTC ISO, see _format_bar_time) in ``zone`` as 'YYYY-MM-DD HH:MM UTC-03:00'."""
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value or "-")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(zone)
    return f"{local:%Y-%m-%d %H:%M} {_zone_label(local)}"


def _zone_label(local: datetime) -> str:
    offset = local.strftime("%:z")
    return "UTC" if offset == "+00:00" else f"UTC{offset}"


def _fmt_clock(epoch: float, zone: tzinfo, step_seconds: int) -> str:
    """A candle time for people: the clock for intraday candles, the date for daily and longer ones."""
    local = datetime.fromtimestamp(epoch, timezone.utc).astimezone(zone)
    return f"{local:%Y-%m-%d}" if step_seconds >= 86400 else f"{local:%H:%M}"


def _html_escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _signal_side(label: Any, signal_type: Any) -> str:
    kind = str(signal_type or "").strip().lower()
    # The indicator contract's marker type is the direction; labels such as "L" or
    # "Exit Long" are display text and only decide the side when the type is generic.
    if kind in ("buy", "sell"):
        return kind
    text = f"{label or ''} {kind}".strip().lower()
    if any(token in text for token in ("buy", "long", "cross up", "enter", "一买", "二买", "三买")):
        return "buy"
    if any(token in text for token in ("sell", "short", "cross down", "exit", "一卖", "二卖", "三卖")):
        return "sell"
    if "watch" in text or "observe" in text:
        return "watch"
    return "signal"


# One row per Telegram signal message whose WIN/LOSS is edited in once its entry candles close.
_RESULTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS qd_indicator_signal_results (
    id SERIAL PRIMARY KEY,
    alert_id INTEGER NOT NULL REFERENCES qd_indicator_signal_alerts(id) ON DELETE CASCADE,
    side VARCHAR(8) NOT NULL,
    label VARCHAR(160) DEFAULT '',
    entry_time BIGINT NOT NULL,
    gales INTEGER NOT NULL DEFAULT 0,
    chat_id VARCHAR(128) NOT NULL,
    message_id BIGINT NOT NULL,
    message_kind VARCHAR(8) NOT NULL DEFAULT 'photo',
    caption TEXT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    step INTEGER NULL,
    entry_price DOUBLE PRECISION NULL,
    exit_price DOUBLE PRECISION NULL,
    last_error TEXT DEFAULT '',
    due_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    resolved_at TIMESTAMP NULL
)
"""
_RESULTS_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_indicator_signal_results_pending "
    "ON qd_indicator_signal_results(due_at) WHERE status = 'pending'"
)


@functools.cache
def ensure_indicator_signal_alert_schema() -> None:
    """Create the alert and result tables for local/updated databases (once per process; a failure retries)."""
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
        cur.execute(_RESULTS_TABLE_SQL)
        cur.execute(_RESULTS_INDEX_SQL)
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

    def create_task(self, user_id: int, payload: Dict[str, Any], *, language: str = "", timezone_name: str = "") -> Dict[str, Any]:
        ensure_indicator_signal_alert_schema()
        data = self._sanitize_payload(user_id, payload)
        if language:
            data["targets"]["language"] = language
        if _zone(timezone_name):
            data["targets"]["timezone"] = timezone_name
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

    def update_task(self, user_id: int, task_id: int, payload: Dict[str, Any], *, language: str = "", timezone_name: str = "") -> Dict[str, Any]:
        ensure_indicator_signal_alert_schema()
        self._assert_task_owner(user_id, task_id)
        data = self._sanitize_payload(user_id, payload)
        # Callers without a UI language or browser time zone (Agent Gateway) keep the ones saved with the alert.
        saved = (self._load_task(task_id) or {}).get("targets") or {}
        language = language or saved.get("language")
        timezone_name = timezone_name if _zone(timezone_name) else saved.get("timezone")
        if language:
            data["targets"]["language"] = language
        if timezone_name:
            data["targets"]["timezone"] = timezone_name
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
                    last_bar_time = '',
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
            # Pending results would be scored on the edited alert's instrument; drop them instead.
            cur.execute(
                "UPDATE qd_indicator_signal_results SET status = 'expired', last_error = 'alert edited', resolved_at = NOW() "
                "WHERE alert_id = ? AND status = 'pending'",
                (int(task_id),),
            )
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

        mode = _trigger_mode(task)
        try:
            indicator = self._load_indicator_for_user(int(task["user_id"]), int(task["indicator_id"]))
            code = str(indicator.get("runtime_code") or indicator.get("code") or "")
            if not code.strip():
                raise ValueError("Indicator code is empty")

            targets = task.get("targets") or {}
            # As much history as the chart loads (up to 1000 candles): indicators with long
            # warm-ups (rolling z-scores, pivots) otherwise draw different markers than the chart.
            bars = self.kline.get_kline(
                market=task["market"],
                symbol=task["symbol"],
                timeframe=task["timeframe"],
                limit=1000,
                exchange_id=targets.get("exchange_id") or None,
                market_type=targets.get("market_type") or None,
            )
            df = self._bars_to_df(bars)
            if df.empty:
                raise ValueError("No K-line data")

            output = self._execute_indicator(code, df, task.get("params") or {})
            now = _now()
            close_at = _candle_close_at(df, task.get("timeframe"))
            scan_forming = _reads_forming_candle(mode, task.get("timeframe"), now, close_at)
            signals, last_closed_time, sent = self._pending_signals(output, df, task, scan_forming=scan_forming)
            next_check = self._next_check(mode, task.get("timeframe"), now, close_at)
            if not signals:
                self._mark_checked(task_id, next_check=next_check, last_error="", last_bar_time=last_closed_time)
                return {"triggered": False, "reason": "no_signal"}

            deliveries = [self._deliver_signal(task, signal, df) for signal in signals]
            self._mark_triggered(task_id, signals, deliveries, last_closed_time, sent, next_check)
            return {"triggered": True, "signal": signals[-1], "delivery": deliveries[-1], "signals": signals}
        except Exception as exc:
            logger.warning("Indicator signal alert evaluation failed for %s: %s", task_id, exc)
            logger.debug(traceback.format_exc())
            # A broken alert retries at the regular pace, not every few seconds.
            retry_at = _now() + timedelta(seconds=self._poll_seconds(task.get("timeframe")))
            self._mark_checked(task_id, next_check=retry_at, last_error=str(exc)[:1000])
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
        trigger_mode = str(payload.get("trigger_mode") or targets.get("trigger_mode") or "").strip().lower()
        targets["trigger_mode"] = trigger_mode if trigger_mode in ("preclose", "realtime") else "close"
        result_tracking = str(payload.get("result_tracking") or targets.get("result_tracking") or "").strip().lower()
        targets["result_tracking"] = result_tracking if result_tracking in _RESULT_GALES else "off"
        # The chart's candle source, so the alert reads the candles the chart drew its markers on.
        for key in ("exchange_id", "market_type"):
            targets[key] = str(payload.get(key) or targets.get(key) or "").strip().lower()[:32]
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
        data["trigger_mode"] = _trigger_mode(data)
        data["result_tracking"] = data["targets"].get("result_tracking") if _result_gales(data) is not None else "off"
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
            # The owner's profile time zone goes along for people-facing times (see _alert_zone).
            cur.execute(
                """
                SELECT a.*, COALESCE(u.timezone, '') AS user_timezone
                FROM qd_indicator_signal_alerts a
                LEFT JOIN qd_users u ON u.id = a.user_id
                WHERE a.id = ?
                """,
                (int(task_id),),
            )
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

    def _pending_signals(
        self, output: Dict[str, Any], df: pd.DataFrame, task: Dict[str, Any], *, scan_forming: bool = False
    ) -> tuple[List[Dict[str, Any]], Optional[str], List[str]]:
        """Return the markers to deliver now, the newest closed candle's time and the sent fingerprints.

        The last row is the forming candle, scanned only with ``scan_forming``. Closed candles after
        ``last_bar_time`` are scanned once (at most ``_CATCH_UP_BARS``, so a missed check still
        delivers); a new alert starts at the newest closed candle. Every selected marker on a scanned
        candle is returned unless its fingerprint was already sent.
        """
        signals = output.get("signals") or []
        if not isinstance(signals, list) or len(df) < 2:
            return [], None, []
        times = [self._format_bar_time(value, idx) for idx, value in enumerate(df["time"].tolist())]
        last_closed = len(df) - 2
        start = last_closed
        watermark = str(task.get("last_bar_time") or "")
        if watermark in times:
            start = max(times.index(watermark) + 1, last_closed - _CATCH_UP_BARS + 1)
        end = len(df) - 1 if scan_forming else last_closed
        saved = task.get("last_signal_payload")
        sent = list(saved.get("sent") or []) if isinstance(saved, dict) else []
        selected = {_normalize_key(k) for k in (task.get("signal_keys") or ["any"])}
        pending = []
        for idx in range(start, end + 1):
            for signal in self._bar_signals(signals, df, idx, selected, times):
                fingerprint = self._signal_fingerprint(task, signal)
                if fingerprint not in sent:
                    sent.append(fingerprint)
                    pending.append(signal)
        return pending, times[last_closed], sent[-_SENT_MEMORY:]

    def _bar_signals(self, signals: List[Any], df: pd.DataFrame, idx: int, selected: set[str], times: List[str]) -> List[Dict[str, Any]]:
        """Every selected marker the chart draws on candle ``idx``, sent while the last candle forms."""
        bar = df.iloc[idx]
        notify_idx = len(df) - 1
        found = []
        for sig in signals:
            if not isinstance(sig, dict):
                continue
            data = sig.get("data") if isinstance(sig.get("data"), list) else []
            # Match chart rendering semantics: labels never create a signal,
            # and dense state arrays notify only when they become active.
            if not _signal_marker_should_notify(sig, data, idx):
                continue
            text_data = sig.get("textData") if isinstance(sig.get("textData"), list) else []
            point_text = text_data[idx] if idx < len(text_data) else None
            signal_type = str(sig.get("type") or "signal").strip() or "signal"
            label = str(point_text or sig.get("text") or signal_type).strip()
            if not self._matches_selected_signal(selected, signal_type, label):
                continue
            found.append({
                "type": signal_type,
                "label": label,
                # The candle's close: marker data is where the chart draws the marker
                # (often offset above the high or below the low), not a price anyone traded.
                "price": _safe_float(bar.get("close")),
                "bar_time": times[idx],
                "bar_index": idx,
                "notify_bar_time": times[notify_idx],
                "notify_bar_index": notify_idx,
                "live": idx == notify_idx,
            })
        return found

    def _latest_chart_signal(self, signals: List[Dict[str, Any]], df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Return the newest visible marker for the mobile chart summary."""
        if df.empty:
            return None
        for idx in range(len(df) - 1, -1, -1):
            for signal in signals:
                if not isinstance(signal, dict):
                    continue
                data = signal.get("data") if isinstance(signal.get("data"), list) else []
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
                    "price": _safe_float(bar.get("close")),
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
                return datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=None).isoformat()
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
        live = bool(signal.get("live"))

        # People-facing text follows the UI language saved with the alert and times its zone
        # (see _alert_zone); the webhook payload and browser display params below stay locale-neutral.
        texts = indicator_signal_texts((task.get("targets") or {}).get("language"))
        side_text = texts.get(side, texts["signal"])
        emoji = {"buy": "🟢", "sell": "🔴", "watch": "🟡"}.get(side, "🔔")
        display_price = _fmt_price(signal.get("price"))
        zone = _alert_zone(task)
        signal_time = _fmt_candle_time(signal_bar_time, zone)
        notify_time = _fmt_candle_time(notify_bar_time, zone)
        marker = label if label.lower() != signal_type.lower() else ""
        rule = texts["rule_live" if live else "rule"]

        headline = f"{side_text} {symbol} {timeframe}".strip()
        title = f"{emoji} {headline} · {indicator_name}"
        summary = f"{headline} @ {display_price}" if display_price else headline
        plain_lines = [
            f"{emoji} {summary}",
            f"{texts['indicator']}: {indicator_name}",
            f"{texts['market']}: {market or '-'}",
            f"{texts['symbol']}: {symbol or '-'}{f' ({symbol_name})' if symbol_name else ''}",
            f"{texts['timeframe']}: {timeframe or '-'}",
            f"{texts['signal_candle']}: {signal_time}",
            f"{texts['notify_candle']}: {notify_time}",
            f"{texts['marker']}: {marker}" if marker else "",
            rule,
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
                "live": live,
            },
            "alert": {
                "task_id": task.get("id"),
                "channels": task.get("channels") or [],
            },
            "trace": {
                "source": "indicator_signal_alerts",
                "delivery_rule": "live_bar" if live else "closed_bar_next_open",
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
              <div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#9ca3af;">QuantDinger · {_html_escape(texts["heading"])}</div>
              <div style="font-size:20px;font-weight:700;margin-top:6px;color:#fff;">{_html_escape(indicator_name)}</div>
              <div style="margin-top:12px;display:inline-block;padding:7px 11px;border-radius:6px;border:1px solid {side_color};background:rgba(255,255,255,.04);color:{side_color};font-weight:800;">{emoji} {_html_escape(side_text)}</div>
            </div>
            <div style="padding:20px 22px;">
              <table style="width:100%;border-collapse:collapse;font-size:14px;">
                <tr><td style="padding:8px 0;color:#9ca3af;">{_html_escape(texts["symbol"])}</td><td style="padding:8px 0;text-align:right;font-weight:700;">{_html_escape(symbol)}</td></tr>
                <tr><td style="padding:8px 0;color:#9ca3af;">{_html_escape(texts["market"])}</td><td style="padding:8px 0;text-align:right;">{_html_escape(market)}</td></tr>
                <tr><td style="padding:8px 0;color:#9ca3af;">{_html_escape(texts["timeframe"])}</td><td style="padding:8px 0;text-align:right;">{_html_escape(timeframe)}</td></tr>
                <tr><td style="padding:8px 0;color:#9ca3af;">{_html_escape(texts["price"])}</td><td style="padding:8px 0;text-align:right;font-weight:700;color:{side_color};">{_html_escape(display_price or "-")}</td></tr>
                <tr><td style="padding:8px 0;color:#9ca3af;">{_html_escape(texts["signal_candle"])}</td><td style="padding:8px 0;text-align:right;">{_html_escape(signal_time)}</td></tr>
                <tr><td style="padding:8px 0;color:#9ca3af;">{_html_escape(texts["notify_candle"])}</td><td style="padding:8px 0;text-align:right;">{_html_escape(notify_time)}</td></tr>
                {f'<tr><td style="padding:8px 0;color:#9ca3af;">{_html_escape(texts["marker"])}</td><td style="padding:8px 0;text-align:right;">{_html_escape(marker)}</td></tr>' if marker else ""}
              </table>
              <div style="margin-top:18px;padding:12px 14px;border-radius:8px;background:#111827;color:#9ca3af;">
                {_html_escape(rule)}
              </div>
            </div>
          </div>
        </div>
        """
        # Telegram reads as a trade ticket: enter on the candle after the signal candle, and with
        # result tracking, when it expires and when each gale runs.
        step = frequency_seconds(timeframe)
        opened = _epoch(signal_bar_time)

        def clock(epoch: int) -> str:
            return _html_escape(_fmt_clock(epoch, zone, step))

        telegram_lines = [f"{emoji} <b>{_html_escape(side_text)}</b> · <b>{_html_escape(symbol)}</b> {_html_escape(timeframe)}"]
        warning = _html_escape(rule)
        if opened is not None:
            entry_at, gales = opened + step, _result_gales(task)
            zone_label = _zone_label(datetime.fromtimestamp(entry_at, timezone.utc).astimezone(zone))
            telegram_lines.append(
                f"⏱ {_html_escape(texts['entry'])}: <b>{clock(entry_at)}</b> ({_html_escape(texts['entry_next'])}) · {zone_label}"
            )
            if gales is not None:
                telegram_lines.append(f"⌛ {_html_escape(texts['expiry'])}: {clock(entry_at + step)}")
                telegram_lines += [
                    f"🔁 {_html_escape(texts['gale'].format(n=n))}: {clock(entry_at + n * step)} → {clock(entry_at + (n + 1) * step)}"
                    for n in range(1, gales + 1)
                ]
            warning = _html_escape(texts["live_warning"].format(time=_fmt_clock(entry_at, zone, step)))
        telegram_lines += [
            f"💲 {_html_escape(texts['price'])}: <b>{_html_escape(display_price)}</b>" if display_price else "",
            f"📍 {_html_escape(texts['signal_at'].format(marker=label, time=_fmt_clock(opened, zone, step) if opened is not None else signal_time))}",
            f"📊 {_html_escape(indicator_name)}",
            f"⚠️ <i>{warning}</i>" if live else "",
        ]
        telegram_html = "\n".join(line for line in telegram_lines if line)
        return title, message_plain, email_html, telegram_html, payload

    def _deliver_signal(self, task: Dict[str, Any], signal: Dict[str, Any], df: pd.DataFrame) -> Dict[str, Any]:
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
            result["telegram"] = self._send_telegram(task, signal, df, telegram_html)
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

    def _send_telegram(self, task: Dict[str, Any], signal: Dict[str, Any], df: pd.DataFrame, caption: str) -> Dict[str, Any]:
        """Send the signal as a chart photo captioned ``caption`` (as text when the photo cannot be
        drawn or sent) and, when the alert tracks results, queue the message for its WIN/LOSS edit."""
        targets = task.get("targets") or {}
        token = str(targets.get("telegram_bot_token") or targets.get("telegram_token") or "").strip()
        chat_id = str(targets.get("telegram_chat_id") or targets.get("telegram") or "").strip()
        if not token or not chat_id:
            return {"ok": False, "message": "missing_telegram_chat_id" if token else "missing_telegram_bot_token"}
        side = _signal_side(signal.get("label"), signal.get("type"))
        opened = _epoch(signal.get("bar_time")) if side in ("buy", "sell") else None
        png = None
        if opened is not None:
            png = self._signal_chart(task, side, str(signal.get("label") or ""), df.iloc[: int(signal["bar_index"]) + 1], opened)
        sent = None
        if png:
            ok, res = self.notifier._telegram_api(
                token, "sendPhoto", {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                files={"photo": ("signal.png", png, "image/png")},
            )
            if ok:
                sent = ("photo", res)
            else:
                logger.warning("Indicator signal chart photo failed for alert %s, sending text: %s", task.get("id"), res)
        if sent is None:
            ok, res = self.notifier._telegram_api(
                token, "sendMessage", {"chat_id": chat_id, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": True}
            )
            if not ok:
                return {"ok": False, "message": res}
            sent = ("text", res)
        kind, message_id = sent[0], (sent[1] or {}).get("message_id")
        if message_id and opened is not None and _result_gales(task) is not None:
            self._track_result(task, signal, side, opened + frequency_seconds(task.get("timeframe")), chat_id, int(message_id), kind, caption)
        return {"ok": True, "message": "", "message_id": message_id, "kind": kind}

    def _signal_chart(
        self, task: Dict[str, Any], side: str, label: str, candles: pd.DataFrame, signal_time: int,
        result: Optional[tuple[int, bool, str]] = None,
    ) -> Optional[bytes]:
        """The signal's chart photo (see render_signal_chart), or None when it cannot be drawn."""
        texts = indicator_signal_texts((task.get("targets") or {}).get("language"))
        step, zone = frequency_seconds(task.get("timeframe")), _alert_zone(task)
        try:
            return render_signal_chart(
                candles,
                signal_time,
                side,
                [texts["chart_entry"]] + [f"G{n}" for n in range(1, (_result_gales(task) or 0) + 1)],
                title=f"{task.get('symbol') or ''} · {task.get('timeframe') or ''}",
                subtitle=str(task.get("indicator_name") or ""),
                badge=texts[f"chart_{side}"],
                marker=label,
                time_text=lambda epoch: _fmt_clock(epoch, zone, step),
                price_text=_fmt_price,
                result=result,
            )
        except Exception as exc:
            logger.warning("Indicator signal chart failed for alert %s: %s", task.get("id"), exc)
            return None

    def _track_result(
        self, task: Dict[str, Any], signal: Dict[str, Any], side: str, entry_time: int,
        chat_id: str, message_id: int, kind: str, caption: str,
    ) -> None:
        step = frequency_seconds(task.get("timeframe"))
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    INSERT INTO qd_indicator_signal_results
                        (alert_id, side, label, entry_time, gales, chat_id, message_id, message_kind, caption, due_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(task["id"]), side, str(signal.get("label") or "")[:160], int(entry_time), _result_gales(task),
                        chat_id, message_id, kind, caption,
                        _utc(entry_time + step + 2),  # 2 s after the entry candle closes, like _next_check
                    ),
                )
                db.commit()
                cur.close()
        except Exception as exc:  # the signal is already out; only its WIN/LOSS edit is lost
            logger.warning("Indicator signal result not queued for alert %s: %s", task.get("id"), exc)

    def run_due_results(self, limit: int = 20) -> int:
        """Edit WIN/LOSS into the Telegram signal messages whose entry candles have closed."""
        ensure_indicator_signal_alert_schema()
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT *
                FROM qd_indicator_signal_results
                WHERE status = 'pending' AND due_at <= NOW()
                ORDER BY due_at, id
                LIMIT ?
                """,
                (int(max(1, limit)),),
            )
            rows = [dict(row) for row in cur.fetchall() or []]
            cur.close()
        for row in rows:
            try:
                self._resolve_result(row)
            except Exception as exc:
                logger.warning("Indicator signal result %s failed: %s", row.get("id"), exc)
                self._update_result(int(row["id"]), "pending", due_at=_now() + timedelta(seconds=30), error=str(exc))
        return len(rows)

    def _resolve_result(self, row: Dict[str, Any]) -> None:
        task = self._load_task(int(row["alert_id"]))
        if not task:  # deleting an alert deletes its results; this one raced the delete
            return
        targets = task.get("targets") or {}
        step, entry_time, gales = frequency_seconds(task.get("timeframe")), int(row["entry_time"]), int(row["gales"] or 0)
        bars = self.kline.get_kline(
            market=task["market"],
            symbol=task["symbol"],
            timeframe=task["timeframe"],
            limit=100,
            exchange_id=targets.get("exchange_id") or None,
            market_type=targets.get("market_type") or None,
        )
        df = self._bars_to_df(bars)
        outcome = _trade_outcome(df, entry_time, row["side"], gales, step)
        if outcome["status"] == "pending":
            if _now() > _utc(entry_time + (gales + 1) * step + _RESULT_GIVE_UP_SECONDS):
                self._update_result(int(row["id"]), "expired", error="entry candles not available")
            else:  # 2 s after the candle it needs closes; every few seconds while the exchange rolls over
                self._update_result(int(row["id"]), "pending", due_at=max(_now() + timedelta(seconds=3), _utc(outcome["closes_at"] + 2)))
            return
        won, n = outcome["status"] == "win", outcome["step"]
        texts = indicator_signal_texts(targets.get("language"))
        verdict = texts["result_win_gale"].format(n=n) if won and n else texts["result_win" if won else "result_loss"]
        line = (
            f"{'✅' if won else '❌'} <b>{_html_escape(verdict)}</b> · "
            f"{_html_escape(_fmt_price(outcome['entry']))} → {_html_escape(_fmt_price(outcome['exit']))}"
        )
        png = None
        if row["message_kind"] == "photo":
            candles = df[df["time"].astype(float) <= entry_time + n * step]  # up to the deciding candle
            badge = f"WIN G{n}" if won and n else ("WIN" if won else "LOSS")
            png = self._signal_chart(task, row["side"], str(row.get("label") or ""), candles, entry_time - step, result=(n, won, badge))
        error = self._edit_telegram(targets, row, f"{row['caption']}\n{line}", png)
        self._update_result(int(row["id"]), outcome["status"], outcome=outcome, error=error)

    def _edit_telegram(self, targets: Dict[str, Any], row: Dict[str, Any], caption: str, png: Optional[bytes]) -> str:
        """Edit a sent signal message to ``caption`` and, for photos, to the chart ``png``; '' or the error."""
        token = str(targets.get("telegram_bot_token") or targets.get("telegram_token") or "").strip()
        if not token:
            return "missing_telegram_bot_token"
        ref = {"chat_id": row["chat_id"], "message_id": int(row["message_id"])}
        if row["message_kind"] != "photo":
            ok, res = self.notifier._telegram_api(
                token, "editMessageText", {**ref, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": True}
            )
            return "" if ok else str(res)
        if png:
            media = {"type": "photo", "media": "attach://chart", "caption": caption, "parse_mode": "HTML"}
            ok, res = self.notifier._telegram_api(
                token, "editMessageMedia", {**ref, "media": json.dumps(media)}, files={"chart": ("signal.png", png, "image/png")}
            )
            if ok:
                return ""
        ok, res = self.notifier._telegram_api(token, "editMessageCaption", {**ref, "caption": caption, "parse_mode": "HTML"})
        return "" if ok else str(res)

    def _update_result(
        self, result_id: int, status: str, *, due_at: Optional[datetime] = None,
        outcome: Optional[Dict[str, Any]] = None, error: str = "",
    ) -> None:
        outcome = outcome or {}
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_indicator_signal_results
                SET status = ?, due_at = COALESCE(?, due_at), step = ?, entry_price = ?, exit_price = ?,
                    last_error = ?, resolved_at = ?
                WHERE id = ?
                """,
                (
                    status, due_at, outcome.get("step"), outcome.get("entry"), outcome.get("exit"),
                    str(error)[:1000], None if status == "pending" else _now(), int(result_id),
                ),
            )
            db.commit()
            cur.close()

    def _mark_checked(self, task_id: int, *, next_check: datetime, last_error: str, last_bar_time: Optional[str] = None) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_indicator_signal_alerts
                SET check_count = check_count + 1,
                    last_checked_at = NOW(),
                    next_check_at = ?,
                    last_bar_time = COALESCE(?, last_bar_time),
                    last_error = ?,
                    updated_at = NOW()
                WHERE id = ?
                """,
                (next_check, last_bar_time, last_error, int(task_id)),
            )
            db.commit()
            cur.close()

    def _mark_triggered(
        self,
        task_id: int,
        signals: List[Dict[str, Any]],
        deliveries: List[Dict[str, Any]],
        last_bar_time: Optional[str],
        sent: List[str],
        next_check: datetime,
    ) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_indicator_signal_alerts
                SET check_count = check_count + 1,
                    trigger_count = trigger_count + ?,
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
                    len(signals),
                    next_check,
                    str(last_bar_time or ""),
                    sent[-1] if sent else "",
                    _json_dumps({"signal": signals[-1], "delivery": deliveries[-1], "sent": sent}),
                    int(task_id),
                ),
            )
            db.commit()
            cur.close()

    def _poll_seconds(self, timeframe: str, realtime: bool = False) -> int:
        tf = normalize_frequency(timeframe, default="")
        mapping = {
            "1m": 20,
            "3m": 25,
            "5m": 30,
            "15m": 60,
            "30m": 90,
            "1h": 120,
            "4h": 300,
            "1d": 600,
            "1w": 1800,
            "1mo": 3600,
        }
        base = mapping.get(tf, 120)
        # Realtime alerts re-read the forming candle every few seconds (slower on long timeframes).
        return max(5, base // 6) if realtime else base

    def _next_check(self, mode: str, timeframe: Any, now: datetime, close_at: Optional[datetime]) -> datetime:
        """The regular poll, pulled in to just after the forming candle closes and, for pre-close
        alerts, to the pre-close lead before it. Bar times that are not clock times keep the poll."""
        poll = timedelta(seconds=self._poll_seconds(timeframe, realtime=mode == "realtime"))
        if mode == "realtime" or close_at is None or close_at < now - poll:
            return now + poll
        lead_at = close_at - _pre_close_lead(timeframe)
        # 2 s after the close so the exchange already serves the new candle.
        target = lead_at if mode == "preclose" and now < lead_at else close_at + timedelta(seconds=2)
        # Never sooner than 2 s, so a candle the exchange has not rolled over yet is retried calmly.
        return max(now + timedelta(seconds=2), min(now + poll, target))


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
        # ponytail: one thread evaluates every due alert in turn; shard tasks across workers
        # if many realtime alerts (a few seconds of indicator time each) start running late.
        while not self._stop.is_set():
            try:
                self._service.run_due_tasks(limit=20)
            except Exception as exc:
                logger.warning("Indicator signal alert worker loop failed: %s", exc)
            try:
                self._service.run_due_results(limit=20)
            except Exception as exc:
                logger.warning("Indicator signal result loop failed: %s", exc)
            # Short wake-up so realtime alerts keep their few-second poll; each wake is one indexed query.
            self._stop.wait(float(os.getenv("INDICATOR_SIGNAL_ALERT_WORKER_INTERVAL", "3")))


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
