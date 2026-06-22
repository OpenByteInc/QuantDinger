"""Unit tests for login_notify helpers (no network)."""
import json
from unittest.mock import MagicMock, patch

from app.services import login_notify as ln


def test_parse_user_agent_mobile():
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15"
    label = ln._parse_user_agent(ua)
    assert "Mobile" in label


def test_device_fingerprint_stable():
    a = ln._device_fingerprint("Mozilla/5.0 Chrome/120")
    b = ln._device_fingerprint("Mozilla/5.0 Chrome/120")
    assert a == b and len(a) == 16


def test_location_key():
    geo = {"country": "China", "city": "Shanghai"}
    assert ln._location_key(geo) == "china|shanghai"


def test_action_label_oauth():
    assert "Google" in ln._action_label("oauth_login", {"provider": "google"}, zh=True)


@patch("app.services.login_notify.get_db_connection")
def test_list_login_logs_parses_details(mock_db):
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = {"cnt": 1}
    mock_cur.fetchall.return_value = [
        {
            "id": 1,
            "action": "login_success",
            "ip_address": "1.2.3.4",
            "user_agent": "Mozilla/5.0 Chrome/120",
            "details": json.dumps({
                "device_label": "Desktop · Chrome",
                "location": "China · Shanghai",
                "is_new_device": True,
                "is_new_region": False,
            }),
            "created_at": "2026-01-01T00:00:00",
        }
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_db.return_value.__enter__.return_value = mock_conn

    out = ln.list_login_logs(1, page=1, page_size=10)
    assert out["total"] == 1
    assert len(out["items"]) == 1
    assert out["items"][0]["is_new_device"] is True
    assert out["items"][0]["device"] == "Desktop · Chrome"
