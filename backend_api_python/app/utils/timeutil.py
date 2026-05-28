"""
Time / time-zone helpers for serializing datetimes to the frontend.

Background
----------
Most ``qd_*`` tables use ``TIMESTAMP WITHOUT TIME ZONE`` columns.  Our PostgreSQL
pool sets ``options="-c timezone=UTC"`` (see ``db_postgres.py``), so ``NOW()``
and driver round-trips store a *naive* **UTC wall-clock** value.  When the
backend serializes that ``datetime`` with ``.isoformat()`` the result has **no
time zone suffix** (e.g. ``"2026-05-08T19:36:00"``).

The frontend uses ``new Date(text)`` to parse it; modern browsers interpret a
naive ISO string as the *browser's local time*, which yields wrong values for
any user whose browser time zone differs from the server's.

To fix this we always serialize timestamps as **UTC ISO 8601 with a ``Z``
suffix**.  The browser then renders them in whatever locale the user is in,
without any further work on the frontend.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo  # py>=3.9
except Exception:  # pragma: no cover - fallback for very old runtimes
    ZoneInfo = None  # type: ignore[misc,assignment]


def _db_naive_tzinfo() -> timezone:
    """Timezone for naive ``datetime`` values read from PostgreSQL.

    The connection pool pins the session to UTC.  Naive timestamps are UTC wall
    clock — **not** the backend container's ``TZ`` (e.g. Asia/Shanghai).
    """
    override = (os.getenv("DB_NAIVE_TIMESTAMP_TZ") or "UTC").strip() or "UTC"
    if override.upper() in ("UTC", "GMT", "ETC/UTC", "ETC/GMT"):
        return timezone.utc
    if ZoneInfo is not None:
        try:
            return ZoneInfo(override)  # type: ignore[return-value]
        except Exception:
            pass
    return timezone.utc


def to_utc_iso(value: Any) -> Optional[str]:
    """Convert a value to a UTC ISO 8601 string with a ``Z`` suffix.

    Accepts ``datetime``, ISO strings, numeric epoch seconds, or ``None``.
    Returns ``None`` for falsy inputs that aren't valid timestamps.

    Rules
    -----
    * Aware ``datetime`` → converted to UTC.
    * Naive ``datetime`` → assumed to be UTC wall clock from our PG session
      (``options=-c timezone=UTC``), then converted/emitted as UTC ``Z``.
    * Numeric input → treated as epoch seconds (or milliseconds when too large).
    * String input that parses as ISO 8601 → re-emitted in UTC.  If the string
      has no time-zone designator we treat it as server local time.
    * Anything else → ``None`` (the route can decide to fall back to ``str()``).
    """
    if value is None or value == "":
        return None

    dt: Optional[datetime] = None

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        ts = float(value)
        # Heuristic: > 1e12 is milliseconds.
        if ts > 1e12:
            ts /= 1000.0
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            # Support trailing "Z" (Python <3.11 does not accept it directly).
            normalized = s.replace("Z", "+00:00") if s.endswith("Z") else s
            # ``fromisoformat`` accepts both ``T`` and space separators since
            # Python 3.11; on 3.9/3.10 it tolerates space too but not trailing
            # microsecond rounding edge cases.  Replace space defensively.
            if " " in normalized and "T" not in normalized:
                normalized = normalized.replace(" ", "T", 1)
            dt = datetime.fromisoformat(normalized)
        except Exception:
            return None
    else:
        return None

    if dt is None:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_db_naive_tzinfo())
    dt_utc = dt.astimezone(timezone.utc)
    # Always emit with trailing Z and second-precision (drop microseconds for
    # smaller, cleaner payloads).  ISO 8601 with Z is unambiguous for all
    # browsers.
    return dt_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = ["to_utc_iso"]
