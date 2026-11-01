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


def test_factor_research_uses_universe_without_loading_strategy_source(app, monkeypatch):
    captured = {}

    class BacktestService:
        @staticmethod
        def research_universe_factor(**kwargs):
            captured["execution"] = kwargs
            return {
                "manifest": {"markets": ["USStock"], "primaryFrequency": "1d"},
                "universe": {"name": "NASDAQ 100"},
                "universeId": kwargs["universe_id"],
                "frequency": kwargs["frequency"],
            }

    class ResearchRepository:
        @staticmethod
        def persist_run(**kwargs):
            captured["storage"] = kwargs
            return 42

    monkeypatch.setattr(backtest_center, "get_strategy_backtest_service", lambda: BacktestService())
    monkeypatch.setattr(backtest_center, "get_factor_research_repository", lambda: ResearchRepository())
    monkeypatch.setattr(
        backtest_center,
        "_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("strategy source must not be loaded")),
    )

    with app.test_request_context("/api/backtest/factor-research", method="POST", json={
        "universeId": 9,
        "frequency": "1d",
        "startDate": "2026-01-01",
        "endDate": "2026-02-01",
        "factorId": "momentum_20",
    }):
        g.user_id = 7
        response = inspect.unwrap(backtest_center.run_factor_research)()

    body = response.get_json()
    assert body["code"] == 1, body
    assert body["data"]["runId"] == 42
    assert captured["execution"]["universe_id"] == 9
    assert captured["execution"]["frequency"] == "1d"
    assert captured["storage"]["source_id"] is None
    assert captured["storage"]["universe_id"] == 9
    assert captured["storage"]["universe_name"] == "NASDAQ 100"
    assert captured["storage"]["research_mode"] == "universe"


def test_factor_benchmark_persists_ranked_factor_runs(app, monkeypatch):
    captured = {"persisted": []}

    class BacktestService:
        @staticmethod
        def benchmark_universe_factors(**kwargs):
            captured["execution"] = kwargs
            return {
                "researchMode": "benchmark",
                "manifest": {
                    "markets": ["USStock"],
                    "primaryFrequency": "1d",
                },
                "universe": {"name": "NASDAQ 100"},
                "frequency": "1d",
                "symbolsRequested": 100,
                "symbolsUsed": 98,
                "symbolsSkipped": [],
                "rankings": [
                    {
                        "factorId": "quality",
                        "rank": 1,
                        "classification": "alive",
                        "score": 88.0,
                        "pValue": 0.01,
                        "qValue": 0.02,
                    },
                    {
                        "factorId": "value",
                        "rank": 2,
                        "classification": "candidate",
                        "score": 67.0,
                        "pValue": 0.04,
                        "qValue": 0.04,
                    },
                ],
                "factorResults": [
                    {"factorId": "quality", "effectiveGroups": 5},
                    {"factorId": "value", "effectiveGroups": 5},
                ],
            }

    class ResearchRepository:
        @staticmethod
        def persist_run(**kwargs):
            captured["persisted"].append(kwargs)
            return 100 + len(captured["persisted"])

    monkeypatch.setattr(backtest_center, "get_strategy_backtest_service", lambda: BacktestService())
    monkeypatch.setattr(backtest_center, "get_factor_research_repository", lambda: ResearchRepository())

    with app.test_request_context("/api/backtest/factor-benchmark", method="POST", json={
        "universeId": 9,
        "frequency": "1d",
        "startDate": "2025-01-01",
        "endDate": "2026-01-01",
        "factors": [{"factorId": "quality"}, {"factorId": "value"}],
        "trainFraction": 0.65,
        "falseDiscoveryRate": 0.08,
    }):
        g.user_id = 7
        response = inspect.unwrap(backtest_center.run_factor_benchmark)()

    body = response.get_json()
    assert body["code"] == 1, body
    assert body["data"]["benchmarkId"]
    assert body["data"]["rankings"][0]["runId"] == 101
    assert body["data"]["rankings"][1]["runId"] == 102
    assert "factorResults" not in body["data"]
    assert captured["execution"]["train_fraction"] == 0.65
    assert captured["execution"]["false_discovery_rate"] == 0.08
    assert [item["research_mode"] for item in captured["persisted"]] == [
        "benchmark", "benchmark"
    ]
    assert captured["persisted"][0]["result"]["benchmarkClassification"] == "alive"
    assert captured["persisted"][0]["result"]["benchmarkSnapshot"]["benchmarkId"] == body["data"]["benchmarkId"]
    assert "factorResults" not in captured["persisted"][0]["result"]["benchmarkSnapshot"]


def test_factor_benchmark_history_groups_child_runs(app, monkeypatch):
    benchmark_id = "benchmark-batch-1"
    snapshot = {
        "benchmarkId": benchmark_id,
        "researchMode": "benchmark",
        "rankings": [
            {"factorId": "quality", "classification": "alive", "score": 88.0},
            {"factorId": "value", "classification": "candidate", "score": 67.0},
        ],
        "summary": {"requestedFactorCount": 3, "bestFactorId": "quality"},
        "errors": [{"factorId": "size"}],
    }
    runs = [
        {
            "id": index,
            "universe_id": 9,
            "universe_name": "NASDAQ 100",
            "market": "USStock",
            "timeframe": "1d",
            "created_at": "2026-11-01T10:00:00",
            "result": {"benchmarkId": benchmark_id, "benchmarkSnapshot": snapshot},
        }
        for index in (101, 102)
    ]

    class ResearchRepository:
        @staticmethod
        def list_benchmark_runs(**kwargs):
            assert kwargs["user_id"] == 7
            return runs

    monkeypatch.setattr(backtest_center, "get_factor_research_repository", lambda: ResearchRepository())

    with app.test_request_context("/api/backtest/factor-benchmark/history", method="GET"):
        g.user_id = 7
        response = inspect.unwrap(backtest_center.list_factor_benchmark_runs)()

    body = response.get_json()
    assert body["code"] == 1
    assert body["data"][0]["id"] == benchmark_id
    assert body["data"][0]["factor_count"] == 3
    assert body["data"][0]["completed_count"] == 2
    assert body["data"][0]["validated_count"] == 1
    assert body["data"][0]["failed_count"] == 1


def test_factor_benchmark_detail_restores_complete_snapshot(app, monkeypatch):
    benchmark_id = "benchmark-batch-2"
    snapshot = {
        "benchmarkId": benchmark_id,
        "researchMode": "benchmark",
        "rankings": [{"factorId": "quality", "classification": "alive"}],
        "summary": {"requestedFactorCount": 1},
    }
    run = {
        "universe_id": 9,
        "universe_name": "NASDAQ 100",
        "timeframe": "1d",
        "groups_count": 5,
        "holding_period": 5,
        "result": {"benchmarkId": benchmark_id, "benchmarkSnapshot": snapshot},
    }

    class ResearchRepository:
        @staticmethod
        def list_benchmark_runs(**kwargs):
            assert kwargs["benchmark_id"] == benchmark_id
            return [run]

    monkeypatch.setattr(backtest_center, "get_factor_research_repository", lambda: ResearchRepository())

    with app.test_request_context(
        f"/api/backtest/factor-benchmark/get?benchmarkId={benchmark_id}",
        method="GET",
    ):
        g.user_id = 7
        response = inspect.unwrap(backtest_center.get_factor_benchmark_run)()

    body = response.get_json()
    assert body["code"] == 1
    assert body["data"]["research_mode"] == "benchmark"
    assert body["data"]["result"]["rankings"][0]["factorId"] == "quality"


def test_factor_benchmark_rejects_exception_detail_exposure(app, monkeypatch):
    class BacktestService:
        @staticmethod
        def benchmark_universe_factors(**_kwargs):
            raise ValueError("database password=secret")

    monkeypatch.setattr(backtest_center, "get_strategy_backtest_service", lambda: BacktestService())

    with app.test_request_context("/api/backtest/factor-benchmark", method="POST", json={
        "universeId": 9,
        "startDate": "2025-01-01",
        "endDate": "2026-01-01",
        "factors": [{"factorId": "quality"}],
    }):
        g.user_id = 7
        response, status = inspect.unwrap(backtest_center.run_factor_benchmark)()

    body = response.get_json()
    assert status == 400
    assert body["msg"] == "strategyV2.generationInvalid"
    assert "secret" not in response.get_data(as_text=True)


def test_factor_benchmark_history_rejects_exception_detail_exposure(app, monkeypatch):
    class ResearchRepository:
        @staticmethod
        def list_benchmark_runs(**_kwargs):
            raise RuntimeError("postgres://user:password@database")

    monkeypatch.setattr(backtest_center, "get_factor_research_repository", lambda: ResearchRepository())

    with app.test_request_context("/api/backtest/factor-benchmark/history", method="GET"):
        g.user_id = 7
        response, status = inspect.unwrap(backtest_center.list_factor_benchmark_runs)()

    body = response.get_json()
    assert status == 500
    assert body["msg"] == "strategyV2.backtest.runFailed"
    assert "password" not in response.get_data(as_text=True)


def test_factor_benchmark_missing_id_returns_public_error_key(app):
    with app.test_request_context("/api/backtest/factor-benchmark/get", method="GET"):
        g.user_id = 7
        response, status = inspect.unwrap(backtest_center.get_factor_benchmark_run)()

    body = response.get_json()
    assert status == 400
    assert body["msg"] == "strategyV2.factorBenchmarkIdRequired"


def test_human_backtest_submission_uses_persisted_async_job(app, monkeypatch):
    captured = {}

    def submit_job(**kwargs):
        captured.update(kwargs)
        return {
            "job_id": "job-7",
            "status": "queued",
            "kind": "backtest",
            "created_at": "2026-01-01T00:00:00Z",
            "billing": {"remaining": 970},
        }

    monkeypatch.setattr(backtest_center.agent_jobs, "submit_job", submit_job)
    payload = {
        "code": "def initialize(context):\n    pass",
        "startDate": "2026-01-01",
        "endDate": "2026-02-01",
        "initialCapital": 20000,
    }

    with app.test_request_context("/api/backtest/jobs", method="POST", json=payload):
        g.user_id = 7
        response, status = inspect.unwrap(backtest_center.create_strategy_backtest_job)()

    body = response.get_json()
    assert status == 202
    assert body["data"]["jobId"] == "job-7"
    assert body["data"]["billing"]["remaining"] == 970
    assert captured["kind"] == "backtest"
    assert captured["agent_token_id"] is None
    assert captured["request_payload"]["__user_id"] == 7
    assert captured["request_payload"]["initialCapital"] == 20000
