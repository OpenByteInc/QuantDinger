from app.routes import strategy as strategy_routes
from app.services.strategy_runtime import health


def test_strategy_rows_include_runtime_health(monkeypatch):
    captured = {}

    def load(strategy_ids, *, strategy_statuses=None):
        captured["ids"] = list(strategy_ids)
        captured["statuses"] = dict(strategy_statuses or {})
        return {
            20: {
                "health": "healthy",
                "last_heartbeat_at": 1784434455,
                "loop_latency_ms": 37,
            }
        }

    monkeypatch.setattr(strategy_routes, "load_runtime_health", load)

    rows = strategy_routes._attach_runtime_health([
        {"id": 20, "status": "running", "strategy_name": "Momentum"}
    ])

    assert captured == {"ids": [20], "statuses": {20: "running"}}
    assert rows[0]["runtime_health"]["health"] == "healthy"
    assert rows[0]["runtime_health"]["loop_latency_ms"] == 37


def test_strategy_rows_include_daily_pnl_metrics(monkeypatch):
    captured = {}

    monkeypatch.setattr(strategy_routes, "load_runtime_health", lambda *_args, **_kwargs: {})

    def load_metrics(rows, *, user_id, client_timezone=""):
        captured["rows"] = list(rows)
        captured["user_id"] = user_id
        captured["timezone"] = client_timezone
        return {20: {"today_pnl": 23.0, "today_pnl_estimated": False}}

    monkeypatch.setattr(strategy_routes, "load_strategy_daily_metrics", load_metrics)
    rows = strategy_routes._attach_runtime_health(
        [{"id": 20, "status": "running", "strategy_name": "Momentum"}],
        user_id=7,
        client_timezone="Asia/Shanghai",
    )

    assert captured["user_id"] == 7
    assert captured["timezone"] == "Asia/Shanghai"
    assert rows[0]["today_pnl"] == 23.0
    assert rows[0]["today_pnl_estimated"] is False


def test_runtime_heartbeat_persists_loop_latency(monkeypatch):
    saved = {}

    class Store:
        def __init__(self, **kwargs):
            saved["identity"] = kwargs

        def save(self, values):
            saved["values"] = values

    from app.services.strategy_runtime import state

    monkeypatch.setattr(state, "RuntimeStateStore", Store)
    monkeypatch.setattr(health.time, "time", lambda: 1784434455)

    health.record_runtime_heartbeat(
        strategy_id=20,
        strategy_run_id=7,
        symbol="BTC/USDT",
        price=64655.9,
        pending_signal_count=2,
        loop_latency_ms=41,
    )

    assert saved["identity"] == {
        "strategy_id": 20,
        "strategy_run_id": 7,
        "state_key": "health",
    }
    assert saved["values"]["last_heartbeat_at"] == 1784434455
    assert saved["values"]["loop_latency_ms"] == 41
    assert saved["values"]["latency_ms"] == 41


def test_historical_failed_order_does_not_degrade_current_run(monkeypatch):
    snapshots = {
        20: {
            **health._empty_snapshot(),
            "run_id": 7,
        }
    }

    monkeypatch.setattr(
        health,
        "_query",
        lambda _sql, _params: [
            {
                "strategy_id": 20,
                "strategy_run_id": 6,
                "pending_orders": 0,
                "failed_orders": 1,
                "historical_failed_orders": 3,
            },
            {
                "strategy_id": 20,
                "strategy_run_id": 7,
                "pending_orders": 0,
                "failed_orders": 0,
                "historical_failed_orders": 1,
            },
        ],
    )

    health._load_pending_orders(snapshots, "%s", [20])

    assert snapshots[20]["failed_orders"] == 0
    assert snapshots[20]["historical_failed_orders"] == 1


def test_recent_failed_order_degrades_until_attention_window_expires():
    snapshot = {
        **health._empty_snapshot(),
        "run_id": 7,
        "last_heartbeat_at": 1_000,
        "failed_orders": 1,
    }

    assert health._health_state(snapshot, strategy_status="running", now=1_010) == "degraded"

    snapshot["failed_orders"] = 0
    assert health._health_state(snapshot, strategy_status="running", now=1_010) == "healthy"
