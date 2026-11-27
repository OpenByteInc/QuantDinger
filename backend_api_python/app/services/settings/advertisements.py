"""Deployment-wide image advertisements; URLs are never fetched by the server."""

import json
from urllib.parse import urlsplit

from app.utils.db import get_db_connection

POSITIONS = frozenset({"sidebar", "accounts"})


def valid_url(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        return False
    if any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value:
        return False
    try:
        parsed = urlsplit(value)
        return bool(parsed.scheme in ("https", "http") and parsed.hostname
                    and not parsed.username and not parsed.password)
    except ValueError:
        return False


def validate_items(items):
    if not isinstance(items, list) or len(items) > 40:
        raise ValueError("advertisements.invalid")
    result, ids = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("advertisements.invalid")
        identifier = item.get("id")
        name = item.get("name")
        order = item.get("sort_order")
        if (not isinstance(identifier, str) or not identifier or len(identifier) > 64
                or identifier in ids or not isinstance(name, str) or not name.strip()
                or len(name) > 80 or item.get("position") not in POSITIONS
                or type(order) is not int or not 0 <= order <= 9999
                or type(item.get("enabled")) is not bool
                or not valid_url(item.get("image_url"))
                or not valid_url(item.get("target_url"))):
            raise ValueError("advertisements.invalid")
        ids.add(identifier)
        result.append({key: item[key] for key in
                       ("id", "name", "position", "sort_order", "enabled", "image_url", "target_url")})
    return result


def read_advertisements():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT items, revision FROM qd_advertisement_config WHERE id = 1")
        row = cursor.fetchone()
    if not row:
        return {"items": [], "revision": 0}
    items = row["items"]
    return {"items": json.loads(items) if isinstance(items, str) else items,
            "revision": row["revision"]}


def save_advertisements(items, revision):
    items = validate_items(items)
    if type(revision) is not int or revision < 0:
        raise ValueError("advertisements.invalid")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO qd_advertisement_config (id, items, revision)
            VALUES (1, '[]'::jsonb, 0) ON CONFLICT (id) DO NOTHING RETURNING id
        """)
        cursor.execute("""
            UPDATE qd_advertisement_config SET items = %s::jsonb,
                revision = revision + 1, updated_at = NOW()
            WHERE id = 1 AND revision = %s RETURNING revision
        """, (json.dumps(items), revision))
        row = cursor.fetchone()
        if not row:
            raise ValueError("advertisements.conflict")
        conn.commit()
    return {"items": items, "revision": row["revision"]}


def public_advertisements():
    items = read_advertisements()["items"]
    return sorted([item for item in items if item.get("enabled") is True
                   and item.get("position") in POSITIONS
                   and valid_url(item.get("image_url")) and valid_url(item.get("target_url"))],
                  key=lambda item: item.get("sort_order", 0))
