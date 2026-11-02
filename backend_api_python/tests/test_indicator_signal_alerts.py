from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import requests

from app.services import indicator_signal_alerts, signal_notifier
from app.services.indicator_signal_alerts import IndicatorSignalAlertService
from app.services.signal_notifier import SignalNotifier


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


def _pending(output, keys=("any",), df=None, **task):
    """Markers a check would deliver now (the last candle of ``df`` is the forming one)."""
    task = {"signal_keys": list(keys), **task}
    return IndicatorSignalAlertService()._pending_signals(output, _df(4) if df is None else df, task)[0]


def test_signal_alert_static_text_does_not_trigger_every_bar():
    output = {
        "signals": [
            {
                "type": "buy",
                "text": "Cross Up",
                "data": [None, 9.9, None, None],
            }
        ]
    }

    assert _pending(output) == []


def test_signal_alert_uses_closed_signal_bar_on_next_bar_open():
    output = {
        "signals": [
            {
                "type": "buy",
                "text": "Cross Up",
                "data": [None, None, 11.9, None],
            }
        ]
    }

    [signal] = _pending(output)

    assert signal["label"] == "Cross Up"
    assert signal["bar_index"] == 2
    assert signal["bar_time"] == "1970-01-01T00:00:02"
    assert signal["notify_bar_index"] == 3


@pytest.mark.parametrize(
    "marker",
    [False, 0, 0.0, "0", "false", "None", {"active": False, "price": 12.0}],
)
def test_signal_alert_ignores_inactive_marker_values_even_with_text(marker):
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

    assert _pending(output) == []


def test_signal_alert_price_is_the_candle_close_not_the_marker_position():
    output = {
        "signals": [
            {
                "type": "buy",
                "text": "Long Entry",
                "data": [None, None, 11.5, None],  # drawn below the candle's low
            }
        ]
    }

    [signal] = _pending(output, ["text:long entry"])

    assert signal["label"] == "Long Entry"
    assert signal["price"] == pytest.approx(12.2)


def test_signal_alert_dense_state_notifies_only_on_transition():
    output = {
        "signals": [
            {
                "type": "buy",
                "text": "Long Entry",
                "data": [None, 10.0, 11.0, None],
            }
        ]
    }

    assert _pending(output) == []


def test_signal_alert_dense_state_transition_still_notifies():
    output = {
        "signals": [
            {
                "type": "buy",
                "text": "Long Entry",
                "data": [None, None, 11.0, 12.0],
            }
        ]
    }

    [signal] = _pending(output)

    assert signal["bar_index"] == 2


def test_signal_alert_points_mode_allows_consecutive_markers():
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

    [signal] = _pending(output)

    assert signal["bar_index"] == 2


def test_signal_alert_events_mode_keeps_dense_events_explicit():
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

    [signal] = _pending(output)

    assert signal["bar_index"] == 2


def test_signal_alert_sends_every_marker_on_the_closed_candle():
    output = {
        "signals": [
            {"type": "buy", "text": "Next Up", "renderMode": "events", "data": [None, None, 9.0, None]},
            {"type": "buy", "text": "Pullback Buy", "renderMode": "events", "data": [None, None, 8.5, None]},
        ]
    }

    assert [s["label"] for s in _pending(output)] == ["Next Up", "Pullback Buy"]


def test_close_alert_waits_for_the_forming_candle_to_close():
    output = {"signals": [{"type": "buy", "text": "Next Up", "renderMode": "events", "data": [None, None, None, 13.0]}]}

    assert _pending(output) == []


def test_forming_candle_marker_is_sent_once():
    service = IndicatorSignalAlertService()
    task = {"id": 1, "signal_keys": ["any"], "last_bar_time": "1970-01-01T00:00:02"}
    forming = {"signals": [{"type": "buy", "text": "Next Up", "renderMode": "events", "data": [None, None, None, 13.0]}]}

    signals, last_closed, sent = service._pending_signals(forming, _df(4), task, scan_forming=True)

    assert [(s["bar_index"], s["live"]) for s in signals] == [(3, True)]
    assert last_closed == "1970-01-01T00:00:02"

    # The next poll on the same forming candle sends nothing again.
    task.update(last_bar_time=last_closed, last_signal_payload={"sent": sent})
    assert service._pending_signals(forming, _df(4), task, scan_forming=True)[0] == []

    # Once candle 3 closes, only the marker it gained at the close goes out.
    closed = {
        "signals": [
            {"type": "buy", "text": "Next Up", "renderMode": "events", "data": [None, None, None, 13.0, None]},
            {"type": "buy", "text": "LTA Bounce", "renderMode": "events", "data": [None, None, None, 12.5, None]},
        ]
    }
    signals, last_closed, _ = service._pending_signals(closed, _df(5), task, scan_forming=True)

    assert [(s["label"], s["live"]) for s in signals] == [("LTA Bounce", False)]
    assert last_closed == "1970-01-01T00:00:03"


def test_preclose_alert_reads_the_forming_candle_from_a_minute_before_it_closes():
    service = IndicatorSignalAlertService.__new__(IndicatorSignalAlertService)
    reads = indicator_signal_alerts._reads_forming_candle
    close_at = datetime(2026, 11, 1, 21, 30)

    assert service._next_check("preclose", "5m", datetime(2026, 11, 1, 21, 28, 45), close_at) == datetime(2026, 11, 1, 21, 29)
    assert not reads("preclose", "5m", datetime(2026, 11, 1, 21, 28, 58), close_at)
    assert reads("preclose", "5m", datetime(2026, 11, 1, 21, 29, 1), close_at)
    assert not reads("close", "5m", datetime(2026, 11, 1, 21, 29, 1), close_at)
    # A 1m candle is read from its second half, not on its first tick.
    assert not reads("preclose", "1m", datetime(2026, 11, 1, 21, 29, 29), close_at)
    assert reads("preclose", "1m", datetime(2026, 11, 1, 21, 29, 31), close_at)


def test_confirmed_alert_checks_right_after_the_candle_closes():
    service = IndicatorSignalAlertService.__new__(IndicatorSignalAlertService)
    opened = datetime(2026, 11, 1, 21, 25, tzinfo=timezone.utc).timestamp()
    close_at = indicator_signal_alerts._candle_close_at(pd.DataFrame({"time": [opened - 300, opened]}), "5m")
    before_close = datetime(2026, 11, 1, 21, 29, 35)

    assert close_at == datetime(2026, 11, 1, 21, 30)
    assert service._next_check("close", "5m", before_close, close_at) == datetime(2026, 11, 1, 21, 30, 2)
    assert service._next_check("preclose", "5m", before_close, close_at) == datetime(2026, 11, 1, 21, 30, 2)
    # The exchange has not rolled the candle over yet: retry shortly, not on the next poll.
    assert service._next_check("close", "5m", datetime(2026, 11, 1, 21, 30, 3), close_at) == datetime(2026, 11, 1, 21, 30, 5)
    # Realtime alerts keep their few-second poll; bar times that are not clock times keep the regular one.
    assert service._next_check("realtime", "5m", before_close, close_at) == before_close + timedelta(seconds=5)
    assert service._next_check("close", "5m", before_close, datetime(1970, 1, 1)) == before_close + timedelta(seconds=30)


def test_signal_alert_catches_up_closed_candles_missed_since_last_check():
    output = {
        "signals": [
            {"type": "sell", "text": "Next Down", "renderMode": "events", "data": [14.0, None, 14.0, 15.0, 16.0, 17.0, None]}
        ]
    }

    # Candles 0-5 are closed and 6 is forming; the last check covered candle 3.
    assert [s["bar_index"] for s in _pending(output, df=_df(7), last_bar_time="1970-01-01T00:00:03")] == [4, 5]
    # A long outage replays only the newest closed candles; older markers are stale.
    assert [s["bar_index"] for s in _pending(output, df=_df(7), last_bar_time="1970-01-01T00:00:00")] == [3, 4, 5]


def test_evaluate_task_delivers_each_marker_once_from_the_chart_exchange(monkeypatch):
    service = IndicatorSignalAlertService()
    task = {
        "id": 9,
        "user_id": 1,
        "indicator_id": 5,
        "market": "Crypto",
        "symbol": "ETH/USDT",
        "timeframe": "5m",
        "status": "running",
        "signal_keys": ["any"],
        "params": {},
        "last_bar_time": "",
        "targets": {"trigger_mode": "realtime", "exchange_id": "binance", "market_type": "swap"},
    }
    output = {
        "signals": [
            {"type": "buy", "text": "Next Up", "renderMode": "events", "data": [None, None, 9.0, 13.0]},
            {"type": "sell", "text": "Pullback Sell", "renderMode": "events", "data": [None, None, 12.0, None]},
        ]
    }
    kline_calls, delivered, saved = [], [], {}
    monkeypatch.setattr(indicator_signal_alerts, "ensure_indicator_signal_alert_schema", lambda: None)
    monkeypatch.setattr(service, "_load_task", lambda task_id: dict(task))
    monkeypatch.setattr(service, "_load_indicator_for_user", lambda user_id, indicator_id: {"code": "output = {}"})
    monkeypatch.setattr(service.kline, "get_kline", lambda **kwargs: kline_calls.append(kwargs) or [])
    monkeypatch.setattr(service, "_bars_to_df", lambda bars: _df(4))
    monkeypatch.setattr(service, "_execute_indicator", lambda code, df, params: output)
    monkeypatch.setattr(service, "_deliver_signal", lambda task, signal, df: delivered.append(signal["label"]) or {})
    monkeypatch.setattr(
        service,
        "_mark_triggered",
        lambda task_id, signals, deliveries, last_bar_time, sent, next_check: saved.update(last_bar_time=last_bar_time, sent=sent),
    )
    monkeypatch.setattr(service, "_mark_checked", lambda task_id, **kwargs: saved.update(checked=kwargs))

    assert service.evaluate_task(9)["triggered"] is True
    # Both markers on closed candle 2, then the live marker on forming candle 3.
    assert delivered == ["Next Up", "Pullback Sell", "Next Up"]
    assert (kline_calls[0]["exchange_id"], kline_calls[0]["market_type"]) == ("binance", "swap")

    task.update(last_bar_time=saved["last_bar_time"], last_signal_payload={"sent": saved["sent"]})
    assert service.evaluate_task(9)["triggered"] is False
    assert len(delivered) == 3
    assert saved["checked"]["last_bar_time"] == "1970-01-01T00:00:02"


def test_signal_alert_payload_normalizes_trigger_mode_and_chart_exchange(monkeypatch):
    monkeypatch.setattr(indicator_signal_alerts, "get_notification_settings", lambda user_id: {})
    service = IndicatorSignalAlertService()

    realtime = service._sanitize_payload(
        7,
        {"indicator_id": 12, "symbol": "ETH/USDT", "trigger_mode": "realtime", "exchange_id": "Binance", "market_type": "swap"},
    )
    preclose = service._sanitize_payload(
        7, {"indicator_id": 12, "symbol": "ETH/USDT", "trigger_mode": "PreClose", "result_tracking": "Gale1"}
    )
    unknown = service._sanitize_payload(7, {"indicator_id": 12, "symbol": "ETH/USDT", "trigger_mode": "tick", "result_tracking": "gale9"})

    assert (realtime["targets"]["trigger_mode"], realtime["targets"]["exchange_id"], realtime["targets"]["market_type"]) == (
        "realtime",
        "binance",
        "swap",
    )
    assert (preclose["targets"]["trigger_mode"], preclose["targets"]["result_tracking"]) == ("preclose", "gale1")
    assert (unknown["targets"]["trigger_mode"], unknown["targets"]["exchange_id"]) == ("close", "")
    assert realtime["targets"]["result_tracking"] == unknown["targets"]["result_tracking"] == "off"


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
        ("1mo", 3600),
        ("1MO", 3600),
        ("monthly", 3600),
        ("", 120),
        (None, 120),
    ],
)
def test_signal_alert_poll_seconds_handles_timeframe_case(timeframe, expected):
    service = IndicatorSignalAlertService.__new__(IndicatorSignalAlertService)

    assert service._poll_seconds(timeframe) == expected


def test_signal_side_follows_marker_type_before_label():
    assert indicator_signal_alerts._signal_side("Exit Long", "sell") == "sell"
    assert indicator_signal_alerts._signal_side("L", "buy") == "buy"
    assert indicator_signal_alerts._signal_side("Long entry", "signal") == "buy"


@pytest.mark.parametrize(
    ("language", "side_text", "marker_label", "signal_time", "entry_line", "signal_line"),
    [
        (
            "pt-BR", "COMPRA", "Marcador no gráfico", "2026-11-01 10:05 UTC-03:00",
            "⏱ Entrada: <b>10:10</b> (próxima vela) · UTC-03:00", "📍 L na vela das 10:05",
        ),
        ("zh-CN", "买入", "图表标记", "2026-11-01 21:05 UTC+08:00", "⏱ 入场: <b>21:10</b> (下一根K线) · UTC+08:00", "📍 21:05 K线的 L"),
        ("", "BUY", "Chart marker", "2026-11-01 13:05 UTC", "⏱ Entry: <b>13:10</b> (next candle) · UTC", "📍 L on the 13:05 candle"),
    ],
)
def test_signal_notification_states_side_in_alert_language(language, side_text, marker_label, signal_time, entry_line, signal_line):
    task = {
        "indicator_name": "[5m] Crypto SuperTrend Pullback + RSI",
        "market": "Crypto",
        "symbol": "ETH/USDT",
        "timeframe": "5m",
        "targets": {"language": language},
    }
    signal = {
        "type": "buy",
        "label": "L",
        "price": 2261.7278648738,
        "bar_time": "2026-11-01T13:05:00",
        "notify_bar_time": "2026-11-01T13:10:00",
    }

    title, plain, email_html, telegram_html, payload = IndicatorSignalAlertService()._build_indicator_notification(task, signal)

    assert telegram_html.splitlines()[:2] == [f"🟢 <b>{side_text}</b> · <b>ETH/USDT</b> 5m", entry_line]
    assert "<b>2261.73</b>" in telegram_html and signal_line in telegram_html
    assert signal_time in plain and f"{marker_label}: L" in plain
    assert all(side_text in text for text in (title, plain, email_html))
    # Webhook fields stay locale-neutral and keep full precision.
    assert payload["signal"]["side"] == "buy"
    assert payload["signal"]["price"] == "2261.7278648738"
    assert payload["signal"]["bar_time"] == "2026-11-01T13:05:00"


def test_live_marker_notification_says_it_can_still_change():
    task = {"symbol": "ETH/USDT", "timeframe": "5m", "targets": {"language": "pt-BR"}}
    signal = {
        "type": "buy",
        "label": "Next Up",
        "price": 2263.46,
        "bar_time": "2026-11-01T21:20:00",
        "notify_bar_time": "2026-11-01T21:20:00",
        "live": True,
    }

    _, plain, _, telegram_html, payload = IndicatorSignalAlertService()._build_indicator_notification(task, signal)

    assert "ainda pode sumir" in plain and "ainda pode sumir" in telegram_html
    assert (payload["signal"]["live"], payload["trace"]["delivery_rule"]) == (True, "live_bar")


@pytest.mark.parametrize(
    ("user_timezone", "targets", "expected"),
    [
        ("Asia/Tokyo", {"timezone": "America/Manaus", "language": "pt-BR"}, "2026-11-01 22:05 UTC+09:00"),
        ("", {"timezone": "America/Manaus", "language": "pt-BR"}, "2026-11-01 09:05 UTC-04:00"),
        ("Mars/Base", {"timezone": "../etc/passwd", "language": "pt-BR"}, "2026-11-01 10:05 UTC-03:00"),
        ("", {"language": "en-US"}, "2026-11-01 13:05 UTC"),
    ],
)
def test_candle_time_uses_profile_then_browser_then_language_zone(user_timezone, targets, expected):
    zone = indicator_signal_alerts._alert_zone({"user_timezone": user_timezone, "targets": targets})

    assert indicator_signal_alerts._fmt_candle_time("2026-11-01T13:05:00", zone) == expected


def test_tracked_signal_caption_says_when_to_enter_expire_and_gale():
    task = {
        "symbol": "ETH/USDT",
        "timeframe": "5m",
        "indicator_name": "[Crypto] 5M Next-Candle",
        "targets": {"language": "pt-BR", "result_tracking": "gale1"},
    }
    signal = {"type": "sell", "label": "Next Down", "price": 2293.6, "bar_time": "2026-11-02T01:20:00", "notify_bar_time": "2026-11-02T01:25:00"}

    telegram_html = IndicatorSignalAlertService()._build_indicator_notification(task, signal)[3]

    assert telegram_html.splitlines() == [
        "🔴 <b>VENDA</b> · <b>ETH/USDT</b> 5m",
        "⏱ Entrada: <b>22:25</b> (próxima vela) · UTC-03:00",
        "⌛ Expira: 22:30",
        "🔁 Gale 1: 22:30 → 22:35",
        "💲 Preço: <b>2293.6</b>",
        "📍 Next Down na vela das 22:20",
        "📊 [Crypto] 5M Next-Candle",
    ]


def _candles(closes, start=1_793_581_200, step=300):
    """5m candles from 2026-11-02 01:00 UTC; each opens at the previous close. The last one is forming."""
    opens = [closes[0]] + list(closes[:-1])
    return pd.DataFrame(
        {
            "time": [start + i * step for i in range(len(closes))],
            "open": opens,
            "high": [max(o, c) + 0.5 for o, c in zip(opens, closes)],
            "low": [min(o, c) - 0.5 for o, c in zip(opens, closes)],
            "close": closes,
        }
    )


@pytest.mark.parametrize(
    ("closes", "gales", "expected"),
    [
        ([10, 9, 8, 8], 0, ("win", 0)),
        ([10, 11, 10, 10], 1, ("win", 1)),
        ([10, 11, 12, 12], 1, ("loss", 1)),
        ([10, 10, 9, 9], 0, ("loss", 0)),  # a tie loses
    ],
)
def test_sell_wins_when_its_entry_or_gale_candle_closes_lower(closes, gales, expected):
    df = _candles(closes)

    outcome = indicator_signal_alerts._trade_outcome(df, int(df.time[1]), "sell", gales, 300)

    assert (outcome["status"], outcome["step"]) == expected


def test_result_waits_for_the_candle_it_needs_to_close():
    df = _candles([10, 11, 12])
    entry = int(df.time[1])

    assert indicator_signal_alerts._trade_outcome(df.iloc[:2], entry, "sell", 0, 300) == {"status": "pending", "closes_at": entry + 300}
    # The entry lost; its gale candle is still forming.
    assert indicator_signal_alerts._trade_outcome(df, entry, "sell", 1, 300) == {"status": "pending", "closes_at": entry + 600}


def _telegram_task(tracking="next"):
    return {
        "id": 3,
        "market": "Crypto",
        "symbol": "ETH/USDT",
        "timeframe": "5m",
        "indicator_name": "[Crypto] 5M Next-Candle",
        "targets": {"telegram_chat_id": "@signals", "telegram_bot_token": "token", "language": "pt-BR", "result_tracking": tracking},
    }


def test_telegram_signal_is_a_chart_photo_queued_for_its_result(monkeypatch):
    service = IndicatorSignalAlertService()
    df = _candles([2295.0, 2294.0, 2293.6, 2293.0])
    signal = {"type": "sell", "label": "Next Down", "bar_time": "2026-11-02T01:10:00", "bar_index": 2}
    calls, queued = [], []
    monkeypatch.setattr(
        service.notifier, "_telegram_api", lambda token, method, data, files=None: calls.append((method, data, files)) or (True, {"message_id": 77})
    )
    monkeypatch.setattr(service, "_track_result", lambda *args: queued.append(args))

    assert service._send_telegram(_telegram_task(), signal, df, "caption") == {"ok": True, "message": "", "message_id": 77, "kind": "photo"}
    [(method, data, files)] = calls
    assert (method, data["caption"], files["photo"][1][:4]) == ("sendPhoto", "caption", b"\x89PNG")
    assert queued[0][3:6] == (int(df.time[3]), "@signals", 77)  # entry candle, chat, message

    # A photo Telegram refuses still goes out as text.
    monkeypatch.setattr(
        service.notifier, "_telegram_api", lambda token, method, data, files=None: (True, {"message_id": 78}) if method == "sendMessage" else (False, "http_400")
    )
    assert service._send_telegram(_telegram_task("off"), signal, df, "caption")["kind"] == "text"
    assert len(queued) == 1  # untracked alerts queue nothing


def test_result_is_edited_into_the_message_once_the_entry_candle_closes(monkeypatch):
    service = IndicatorSignalAlertService()
    df = _candles([2295.0, 2294.0, 2293.6, 2292.97, 2293.1])
    entry = int(df.time[3])
    row = {
        "id": 5, "alert_id": 3, "side": "sell", "label": "Next Down", "entry_time": entry, "gales": 0,
        "chat_id": "@signals", "message_id": 77, "message_kind": "photo", "caption": "caption",
    }
    calls, updates = [], []
    monkeypatch.setattr(service, "_load_task", lambda task_id: _telegram_task())
    monkeypatch.setattr(service.kline, "get_kline", lambda **kwargs: [])
    monkeypatch.setattr(
        service.notifier, "_telegram_api", lambda token, method, data, files=None: calls.append((method, data, files)) or (True, {})
    )
    monkeypatch.setattr(service, "_update_result", lambda result_id, status, **kwargs: updates.append((status, kwargs)))

    # Still forming: retried 2 s after it closes.
    monkeypatch.setattr(service, "_bars_to_df", lambda bars: df.iloc[:4])
    monkeypatch.setattr(indicator_signal_alerts, "_now", lambda: indicator_signal_alerts._utc(entry + 100))
    service._resolve_result(row)
    assert updates.pop() == ("pending", {"due_at": indicator_signal_alerts._utc(entry + 302)})

    monkeypatch.setattr(service, "_bars_to_df", lambda bars: df)
    service._resolve_result(row)
    [(method, data, files)] = calls
    assert method == "editMessageMedia" and files["chart"][1][:4] == b"\x89PNG"
    assert json.loads(data["media"])["caption"] == "caption\n✅ <b>WIN</b> · 2293.6 → 2292.97"
    assert updates.pop()[0] == "win"

    # Candles that never arrive are given up an hour after the entry candle should have closed.
    monkeypatch.setattr(service, "_bars_to_df", lambda bars: df.iloc[:3])
    monkeypatch.setattr(indicator_signal_alerts, "_now", lambda: indicator_signal_alerts._utc(entry + 300 + 3601))
    service._resolve_result(row)
    assert updates.pop()[0] == "expired"


def test_telegram_errors_never_carry_the_bot_token(monkeypatch):
    def refuse(url, **kwargs):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(signal_notifier.requests, "post", refuse)

    ok, error = SignalNotifier()._telegram_api("123:SECRET", "sendMessage", {"chat_id": "1"})

    assert not ok and "SECRET" not in error and "/bot***/sendMessage" in error
