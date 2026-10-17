from __future__ import annotations

import pandas as pd
import pytest

from app.services import indicator_signal_alerts
from app.services.indicator_signal_alerts import IndicatorSignalAlertService


def _df(count: int = 4) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": list(range(count)),
            "open": [10.0 + i for i in range(count)],
            "high": [10.5 + i for i in range(count)],
            "low": [9.5 + i for i in range(count)],
            "close": [10.2 + i for i in range(count)],
            "volume": [100.0 for _ in range(count)],
        }
    )


def test_signal_alert_static_text_does_not_trigger_every_bar():
    service = IndicatorSignalAlertService()
    output = {
        "signals": [
            {
                "type": "buy",
                "text": "Cross Up",
                "data": [None, 9.9, None, None],
            }
        ]
    }

    assert service._latest_matching_signal(output, _df(4), ["any"]) is None


def test_signal_alert_uses_closed_signal_bar_on_next_bar_open():
    service = IndicatorSignalAlertService()
    output = {
        "signals": [
            {
                "type": "buy",
                "text": "Cross Up",
                "data": [None, None, 11.9, None],
            }
        ]
    }

    signal = service._latest_matching_signal(output, _df(4), ["any"])

    assert signal is not None
    assert signal["label"] == "Cross Up"
    assert signal["bar_index"] == 2
    assert signal["bar_time"] == "1970-01-01T00:00:02"
    assert signal["notify_bar_index"] == 3


@pytest.mark.parametrize(
    "marker",
    [False, 0, 0.0, "0", "false", "None", {"active": False, "price": 12.0}],
)
def test_signal_alert_ignores_inactive_marker_values_even_with_text(marker):
    service = IndicatorSignalAlertService()
    output = {
        "signals": [
            {
                "type": "buy",
                "text": "Long Entry",
                "textData": [None, None, "Long Entry", None],
                "data": [None, None, marker, None],
            }
        ]
    }

    assert service._latest_matching_signal(output, _df(4), ["any"]) is None


def test_signal_alert_boolean_marker_uses_candle_close_as_price():
    service = IndicatorSignalAlertService()
    output = {
        "signals": [
            {
                "type": "buy",
                "text": "Long Entry",
                "data": [False, False, True, False],
            }
        ]
    }

    signal = service._latest_matching_signal(output, _df(4), ["text:long entry"])

    assert signal is not None
    assert signal["label"] == "Long Entry"
    assert signal["price"] == pytest.approx(12.2)


def test_signal_alert_dense_state_notifies_only_on_transition():
    service = IndicatorSignalAlertService()
    output = {
        "signals": [
            {
                "type": "buy",
                "text": "Long Entry",
                "data": [None, 10.0, 11.0, None],
            }
        ]
    }

    assert service._latest_matching_signal(output, _df(4), ["any"]) is None


def test_signal_alert_dense_state_transition_still_notifies():
    service = IndicatorSignalAlertService()
    output = {
        "signals": [
            {
                "type": "buy",
                "text": "Long Entry",
                "data": [None, None, 11.0, 12.0],
            }
        ]
    }

    signal = service._latest_matching_signal(output, _df(4), ["any"])

    assert signal is not None
    assert signal["bar_index"] == 2


def test_signal_alert_points_mode_allows_consecutive_markers():
    service = IndicatorSignalAlertService()
    output = {
        "signals": [
            {
                "type": "buy",
                "text": "Long Entry",
                "renderMode": "points",
                "data": [None, 10.0, 11.0, None],
            }
        ]
    }

    signal = service._latest_matching_signal(output, _df(4), ["any"])

    assert signal is not None
    assert signal["bar_index"] == 2


def test_signal_alert_events_mode_keeps_dense_events_explicit():
    service = IndicatorSignalAlertService()
    output = {
        "signals": [
            {
                "type": "buy",
                "text": "Long Entry",
                "renderMode": "events",
                "data": [None, 10.0, 11.0, None],
            }
        ]
    }

    signal = service._latest_matching_signal(output, _df(4), ["any"])

    assert signal is not None
    assert signal["bar_index"] == 2


def test_signal_alert_payload_uses_profile_notification_defaults(monkeypatch):
    monkeypatch.setattr(
        indicator_signal_alerts,
        "get_notification_settings",
        lambda user_id: {
            "default_channels": ["email", "telegram", "webhook"],
            "email": "alerts@example.com",
            "telegram_chat_id": "123456",
            "telegram_bot_token": "profile-token",
            "webhook_url": "https://example.com/hook",
            "webhook_token": "hook-token",
            "webhook_signing_secret": "hook-secret",
        },
    )

    payload = IndicatorSignalAlertService()._sanitize_payload(
        7,
        {
            "indicator_id": 12,
            "symbol": "BTC/USDT",
            "targets": {"email": "task@example.com"},
        },
    )

    assert payload["channels"] == ["email", "telegram", "webhook"]
    assert payload["targets"]["email"] == "task@example.com"
    assert payload["targets"]["telegram_chat_id"] == "123456"
    assert payload["targets"]["telegram_bot_token"] == "profile-token"
    assert payload["targets"]["webhook_url"] == "https://example.com/hook"
    assert payload["targets"]["webhook_token"] == "hook-token"
    assert payload["targets"]["webhook_signing_secret"] == "hook-secret"


@pytest.mark.parametrize(
    ("timeframe", "expected"),
    [
        ("1m", 20),
        ("3m", 25),
        ("5m", 30),
        ("1h", 120),
        ("1H", 120),
        ("4h", 300),
        ("4H", 300),
        ("1d", 600),
        ("1D", 600),
        ("1w", 1800),
        ("1W", 1800),
        ("", 120),
        (None, 120),
    ],
)
def test_signal_alert_poll_seconds_handles_timeframe_case(timeframe, expected):
    service = IndicatorSignalAlertService.__new__(IndicatorSignalAlertService)

    assert service._poll_seconds(timeframe) == expected
