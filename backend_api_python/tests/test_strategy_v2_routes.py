import inspect

from flask import g

from app.routes import backtest_center


def test_v2_source_prefers_script_source_code(monkeypatch):
    class SourceService:
        @staticmethod
        def get_source(source_id, user_id):
            assert source_id == 104
            assert user_id == 7
            return {"name": "V2", "code": "def initialize(context):\n    pass"}

    monkeypatch.setattr(backtest_center, "get_script_source_service", lambda: SourceService())
    code, source_id, strategy_id, name = backtest_center._source({"sourceId": 104}, 7)

    assert code.startswith("def initialize")
    assert source_id == 104
    assert strategy_id is None
    assert name == "V2"


def test_factor_research_forwards_factor_parameters_to_execution_and_storage(app, monkeypatch):
    captured = {}

    class BacktestService:
        @staticmethod
        def research_factor(**kwargs):
            captured["execution"] = kwargs
            return {
                "manifest": {"markets": ["USStock"], "primaryFrequency": "1d"},
                "factorParams": kwargs["factor_params"],
            }

    class ResearchRepository:
        @staticmethod
        def persist_run(**kwargs):
            captured["storage"] = kwargs
            return 41

    monkeypatch.setattr(
        backtest_center,
        "get_strategy_backtest_service",
        lambda: BacktestService(),
    )
    monkeypatch.setattr(
        backtest_center,
        "get_factor_research_repository",
        lambda: ResearchRepository(),
    )
    payload = {
        "code": "def initialize(context):\n    pass",
        "sourceId": 12,
        "startDate": "2026-01-01",
        "endDate": "2026-02-01",
        "factorId": "talib:SUPERTREND",
        "factorParams": {"timeperiod": 21, "multiplier": 2.5, "output": "SUPERTREND"},
    }

    with app.test_request_context("/api/backtest/factor-research", method="POST", json=payload):
        g.user_id = 7
        response = inspect.unwrap(backtest_center.run_factor_research)()

    body = response.get_json()
    assert body["code"] == 1, body
    assert body["data"]["runId"] == 41
    assert captured["execution"]["factor_params"] == payload["factorParams"]
    assert captured["storage"]["result"]["factorParams"] == payload["factorParams"]
