from app.services import fast_analysis


class _FakeLLM:
    def __init__(self, decisions):
        self._decisions = iter(decisions)
        self.models = []

    def safe_call_llm(self, _system, _user, *, default_structure, model):
        self.models.append(model)
        result = dict(default_structure)
        result["decision"] = next(self._decisions)
        return result


def _service(decisions):
    service = object.__new__(fast_analysis.FastAnalysisService)
    service.llm_service = _FakeLLM(decisions)
    return service


def test_two_model_disagreement_falls_back_to_hold(monkeypatch):
    monkeypatch.setenv("ENABLE_AI_ENSEMBLE", "true")
    monkeypatch.setenv("AI_ENSEMBLE_MODELS", "model-a,model-b")
    monkeypatch.setattr(fast_analysis, "build_evidence_snapshot", lambda _data: {"observations": []})
    monkeypatch.setattr(fast_analysis, "validate_llm_analysis", lambda value, *_args, **_kwargs: value)
    service = _service(["BUY", "SELL"])

    result = service._call_analysis_models("system", "user", {"decision": "HOLD"}, None, {})

    assert result["decision"] == "HOLD"
    assert result["_ensemble_consensus"] is False
    assert result["_ensemble_vote"] == {"BUY": 1, "SELL": 1}


def test_ensemble_deduplicates_models_before_voting(monkeypatch):
    monkeypatch.setenv("ENABLE_AI_ENSEMBLE", "true")
    monkeypatch.setenv("AI_ENSEMBLE_MODELS", "model-a, model-a, model-b, model-c")
    monkeypatch.setattr(fast_analysis, "build_evidence_snapshot", lambda _data: {"observations": []})
    monkeypatch.setattr(fast_analysis, "validate_llm_analysis", lambda value, *_args, **_kwargs: value)
    service = _service(["BUY", "BUY", "SELL"])

    result = service._call_analysis_models("system", "user", {"decision": "HOLD"}, None, {})

    assert service.llm_service.models == ["model-a", "model-b", "model-c"]
    assert result["decision"] == "BUY"
    assert result["_ensemble_consensus"] is True

