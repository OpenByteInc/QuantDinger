"""Tests for UTC serialization of naive DB timestamps."""

from datetime import datetime, timezone

from app.utils.timeutil import to_utc_iso


def test_naive_datetime_from_pg_session_is_utc_not_container_tz():
    # PG pool uses timezone=UTC: 17:59 Shanghai event → 09:59 naive in DB.
    naive = datetime(2026, 5, 25, 9, 59, 30)
    assert to_utc_iso(naive) == "2026-05-25T09:59:30Z"


def test_aware_utc_datetime_emits_z():
    aware = datetime(2026, 5, 25, 9, 59, 30, tzinfo=timezone.utc)
    assert to_utc_iso(aware) == "2026-05-25T09:59:30Z"


def test_iso_string_with_z_re_emitted():
    assert to_utc_iso("2026-05-25T09:59:30Z") == "2026-05-25T09:59:30Z"
