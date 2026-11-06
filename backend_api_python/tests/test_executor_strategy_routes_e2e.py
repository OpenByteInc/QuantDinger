from app.utils.auth import generate_token


class _FakeStrategyService:
    def __init__(self):
        self.rows = {}

    def get_strategy(self, strategy_id, user_id=None):
        row = self.rows.get(int(strategy_id))
        if not row or (user_id is not None and int(row["user_id"]) != int(user_id)):
            return None
        return dict(row)

    def update_strategy_status(self, strategy_id, status, user_id=None):
        row = self.rows.get(int(strategy_id))
        if not row or (user_id is not None and int(row["user_id"]) != int(user_id)):
            return False
        row["status"] = status
        return True


class _FakeSourceService:
    def __init__(self):
        self.sources = []

    def create_source(self, payload):
        self.sources.append(dict(payload))
        return 500 + len(self.sources)


class _FakeDeploymentService:
    def __init__(self, strategies):
        self.strategies = strategies
        self.payloads = []

    def save(self, *, user_id, payload, strategy_id=None):
        self.payloads.append(dict(payload))
        new_id = int(strategy_id or 1000 + len(self.payloads))
        self.strategies.rows[new_id] = {
            "id": new_id,
            "user_id": user_id,
            "strategy_name": payload["name"],
            "strategy_type": "StrategyV2",
            "status": "stopped",
        }
        return new_id


class _FakeTradingExecutor:
    def __init__(self):
        self.started = []

    def start_strategy(self, strategy_id):
        self.started.append(int(strategy_id))
        return True

    def wait_strategy_running(self, strategy_id, timeout=0):
        return int(strategy_id) in self.started, ""

    def is_running(self, strategy_id):
        return int(strategy_id) in self.started


def _auth_headers(monkeypatch):
    from app.utils import auth as auth_module

    monkeypatch.setattr(auth_module, "_verify_token_version", lambda user_id, token_version: True)
    monkeypatch.setattr(auth_module, "_verify_client_token_version", lambda *_: True)
    monkeypatch.setattr(
        auth_module,
        "_get_user_auth_state",
        lambda user_id: {
            "username": "executor-test",
            "role": "user",
            "status": "active",
            "token_version": 1,
        },
    )
    token = generate_token(7, "executor-test", "user", token_version=1)
    return {"Authorization": f"Bearer {token}"}


def test_executor_strategy_create_and_start_routes(client, monkeypatch):
    from app.routes import strategy as strategy_routes
    from app.routes import strategy_executor_routes

    strategies = _FakeStrategyService()
    sources = _FakeSourceService()
    deployments = _FakeDeploymentService(strategies)
    executor = _FakeTradingExecutor()
    monkeypatch.setattr(strategy_executor_routes, "get_strategy_service", lambda: strategies)
    monkeypatch.setattr(strategy_executor_routes, "get_script_source_service", lambda: sources)
    monkeypatch.setattr(strategy_executor_routes, "get_strategy_v2_deployment_service", lambda: deployments)
    monkeypatch.setattr(strategy_routes, "get_strategy_service", lambda: strategies)
    monkeypatch.setattr(strategy_routes, "get_trading_executor", lambda: executor)

    headers = _auth_headers(monkeypatch)
    created_ids = []
    for executor_type in ("grid", "dca", "martingale", "layered_martingale"):
        response = client.post(
            "/api/strategies/executors/create",
            headers=headers,
            json={
                "executor_type": executor_type,
                "strategy_name": f"E2E {executor_type}",
                "symbol": "BTC/USDT",
                "execution_mode": "signal",
                "start_price": 98000,
                "end_price": 102000,
                "grid_count": 6,
                "total_amount_quote": 600,
                "entry_price": 100000,
                "base_order_size": 100,
                "safety_order_size": 120,
                "price_deviation_pct": 0.01,
                "volume_multiplier": 1.5,
                "max_layers": 4,
                "layer_count": 5,
                "orders_per_layer": 3,
            },
        )
        body = response.get_json()
        assert response.status_code == 200
        assert body["code"] == 1
        created_ids.append(int(body["data"]["id"]))
        assert sources.sources[-1]["code"]
        assert deployments.payloads[-1]["sourceId"] == body["data"]["source_id"]

    for strategy_id in created_ids:
        response = client.post(f"/api/strategies/{strategy_id}/start", headers=headers)
        assert response.status_code == 200
        assert response.get_json()["code"] == 1

    assert executor.started == created_ids
    assert all(strategies.rows[strategy_id]["status"] == "running" for strategy_id in created_ids)
