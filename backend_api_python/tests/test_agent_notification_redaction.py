"""Secret redaction contracts for agent and human signal-alert responses."""
from __future__ import annotations

from app.routes import indicator_signal_alerts as human_notifications
from app.routes.agent_v1 import notifications as agent_notifications


def _task_with_secrets() -> dict:
    return {
        "id": 42,
        "targets": {
            "telegram_bot_token": "telegram-secret",
            "webhook_token": "webhook-secret",
            "webhook_signing_secret": "signing-secret",
            "webhook_headers": {
                "Authorization": "Bearer private",
                "X-Api-Key": "header-secret",
                "Content-Type": "application/json",
            },
            "telegram_chat_id": "12345",
            "webhook_url": "https://example.com/hook",
        },
    }


def _assert_task_redacted(task: dict) -> None:
    targets = task["targets"]
    assert targets["telegram_bot_token"] == "***"
    assert targets["webhook_token"] == "***"
    assert targets["webhook_signing_secret"] == "***"
    assert targets["webhook_headers"]["Authorization"] == "***"
    assert targets["webhook_headers"]["X-Api-Key"] == "***"
    assert targets["webhook_headers"]["Content-Type"] == "application/json"
    assert targets["telegram_chat_id"] == "12345"
    assert targets["webhook_url"] == "https://example.com/hook"


def _unwrapped(view):
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


def test_agent_signal_alert_list_redacts_targets(app, monkeypatch):
    monkeypatch.setattr(agent_notifications, "current_user_id", lambda: 7)
    monkeypatch.setattr(agent_notifications._alerts, "list_tasks", lambda user_id: [_task_with_secrets()])

    with app.test_request_context("/api/agent/v1/notifications/signal-alerts"):
        response, status = agent_notifications.list_signal_alerts.__wrapped__()

    assert status == 200
    _assert_task_redacted(response.get_json()["data"]["items"][0])


def test_agent_signal_alert_mutation_responses_redact_targets(app, monkeypatch):
    monkeypatch.setattr(agent_notifications, "current_user_id", lambda: 7)
    monkeypatch.setattr(agent_notifications._alerts, "create_task", lambda user_id, body: _task_with_secrets())
    monkeypatch.setattr(agent_notifications._alerts, "update_task", lambda user_id, task_id, body: _task_with_secrets())
    monkeypatch.setattr(agent_notifications._alerts, "set_status", lambda user_id, task_id, status: _task_with_secrets())

    calls = (
        (agent_notifications.create_signal_alert, (), {}),
        (agent_notifications.update_signal_alert, (42,), {}),
        (agent_notifications.set_signal_alert_status, (42,), {"status": "paused"}),
    )
    for route, args, body in calls:
        with app.test_request_context(json=body):
            response, status = route.__wrapped__(*args)
        assert status == 200
        _assert_task_redacted(response.get_json()["data"])


def test_human_signal_alert_responses_redact_targets(app, monkeypatch):
    monkeypatch.setattr(human_notifications, "_user_id", lambda: 7)
    monkeypatch.setattr(human_notifications.service, "list_tasks", lambda user_id: [_task_with_secrets()])
    monkeypatch.setattr(human_notifications.service, "update_task", lambda user_id, task_id, body: _task_with_secrets())

    with app.test_request_context():
        list_response = _unwrapped(human_notifications.list_indicator_signal_alerts)()
    _assert_task_redacted(list_response.get_json()["data"][0])

    with app.test_request_context(json={}):
        update_response = _unwrapped(human_notifications.update_indicator_signal_alert)(42)
    _assert_task_redacted(update_response.get_json()["data"])
