from app.routes.agent_v1 import research


def test_agent_factor_benchmark_runner_maps_job_payload(monkeypatch):
    captured = {}
    progress = []

    class BenchmarkService:
        @staticmethod
        def benchmark_universe_factors(**kwargs):
            captured.update(kwargs)
            return {"researchMode": "benchmark", "rankings": []}

    monkeypatch.setattr(research, "_factor_benchmark", BenchmarkService())
    result = research._run_factor_benchmark({
        "__user_id": 7,
        "universeId": 19,
        "frequency": "1d",
        "startDate": "2025-01-01",
        "endDate": "2026-01-01",
        "factors": [{"factorId": "quality"}, {"factorId": "value"}],
        "trainFraction": 0.7,
        "falseDiscoveryRate": 0.08,
        "minimumAbsOosIc": 0.03,
    }, progress.append)

    assert result["researchMode"] == "benchmark"
    assert captured["user_id"] == 7
    assert captured["universe_id"] == 19
    assert captured["train_fraction"] == 0.7
    assert captured["false_discovery_rate"] == 0.08
    assert captured["minimum_abs_oos_ic"] == 0.03
    assert progress[0]["phase"] == "loading_universe"
    assert progress[-1]["phase"] == "validated"
