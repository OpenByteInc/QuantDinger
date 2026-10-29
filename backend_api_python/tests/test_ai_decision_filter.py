from contextlib import contextmanager

import pytest

from app.services import ai_decision_filter as module


_REAL_CONSUME_CREDITS = module.AIDecisionFilter._consume_credits


@pytest.fixture(autouse=True)
def _free_ai_decision_billing(monkeypatch):
    monkeypatch.setattr(
        module.AIDecisionFilter,
        "_consume_credits",
        staticmethod(lambda *_: _billing_receipt(charged=0, cost=0, status="free", message="billing_disabled")),
    )


def _request(**overrides):
    values = {
        "user_id": 7,
        "source_type": "strategy",
        "strategy_id": 12,
        "strategy_run_id": 21,
        "symbol": "BTC/USDT",
        "action": "open_long",
        "market_type": "swap",
        "quantity": 0.01,
        "reference_price": 80_000,
        "leverage": 2,
    }
    values.update(overrides)
    return module.AIDecisionRequest(**values)


def _jev_answers(**choices):
    defaults = {
        "data_quality": ("sufficient", {"sufficient": 0.9, "partial": 0.08, "insufficient": 0.02}),
        "signal_alignment": ("aligned", {"aligned": 0.85, "mixed": 0.1, "conflict": 0.03, "insufficient": 0.02}),
        "market_regime": ("favorable", {"favorable": 0.8, "neutral": 0.15, "adverse": 0.03, "insufficient": 0.02}),
        "risk_check": ("clear", {"clear": 0.85, "caution": 0.1, "block": 0.03, "insufficient": 0.02}),
        "execution_quality": ("clear", {"clear": 0.85, "caution": 0.1, "block": 0.03, "insufficient": 0.02}),
    }
    defaults.update(choices)
    return {
        name: {"choice": choice, "probabilities": probabilities, "confidence": probabilities[choice]}
        for name, (choice, probabilities) in defaults.items()
    }


def _billing_receipt(**overrides):
    receipt = {
        "feature": "ai_decision_filter",
        "reference_id": "ai-decision:test",
        "accepted": True,
        "cost": 1,
        "charged": 1,
        "refunded": 0,
        "status": "charged",
        "message": "consumed",
    }
    receipt.update(overrides)
    return receipt


class _AuditCursor:
    def __init__(self, *, billing_column=True, rows=None):
        self.billing_column = billing_column
        self.rows = list(rows or [])
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((query, params))

    def fetchone(self):
        return {"present": self.billing_column}

    def fetchall(self):
        return self.rows

    def close(self):
        return None


class _AuditDb:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.commits = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1


def _audit_connection(db):
    @contextmanager
    def connect():
        yield db

    return connect


@pytest.mark.parametrize("billing_column", [True, False])
def test_ai_decision_audit_persists_across_schema_upgrade(monkeypatch, billing_column):
    cursor = _AuditCursor(billing_column=billing_column)
    db = _AuditDb(cursor)
    monkeypatch.setattr(module, "get_db_connection", _audit_connection(db))
    result = module.AIDecisionResult(
        allowed=True,
        decision="pass",
        provider="jev",
        reason="clear",
        decision_id="decision-1",
        latency_ms=12,
        billing=_billing_receipt(),
    )

    module.AIDecisionFilter._persist(_request(), result)

    insert_query, insert_params = cursor.calls[-1]
    assert "INSERT INTO qd_ai_decisions" in insert_query
    assert ("billing_json" in insert_query) is billing_column
    assert len(insert_params) == (21 if billing_column else 20)
    assert db.commits == 1


def test_ai_decision_history_reads_legacy_schema_without_billing_column(monkeypatch):
    row = {"decision_uid": "decision-1", "decision": "pass", "billing_json": {}}
    cursor = _AuditCursor(rows=[row])
    db = _AuditDb(cursor)
    monkeypatch.setattr(module, "get_db_connection", _audit_connection(db))

    rows = module.list_ai_decisions(user_id=7, source_type="strategy", source_id=12)

    query, params = cursor.calls[-1]
    assert "to_jsonb(qd_ai_decisions) -> 'billing_json'" in query
    assert "checks_json, billing_json" not in query
    assert params == (7, "strategy", 12, 100)
    assert rows == [row]


def test_exit_orders_bypass_ai(monkeypatch):
    captured = []
    monkeypatch.setattr(module.AIDecisionFilter, "_persist", staticmethod(lambda request, result: captured.append(result)))
    result = module.AIDecisionFilter().evaluate(_request(action="close_long"), enabled=True)
    assert result.allowed is True
    assert result.decision == "skipped"
    assert result.reason == "exit_orders_are_not_filtered"
    assert captured and captured[0].decision_id == result.decision_id


def test_special_strategy_types_bypass_ai(monkeypatch):
    monkeypatch.setattr(module.AIDecisionFilter, "_persist", staticmethod(lambda request, result: None))
    result = module.AIDecisionFilter().evaluate(_request(strategy_type="grid"), enabled=True)
    assert result.allowed is True
    assert result.reason == "strategy_type_not_supported"


def test_jev_rejection_blocks_entry(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"answers": _jev_answers(
                risk_check=("block", {"clear": 0.03, "caution": 0.05, "block": 0.9, "insufficient": 0.02}),
            )}

    captured = {}
    monkeypatch.setattr(module.AIDecisionFilter, "_jev_config", staticmethod(lambda: {
        "api_key": "secret",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-latest",
        "timeout_seconds": "8",
        "min_confidence": "0.65",
    }))
    monkeypatch.setattr(module.requests, "post", lambda *args, **kwargs: (captured.update(kwargs) or Response()))
    monkeypatch.setattr(module.AIDecisionFilter, "_persist", staticmethod(lambda request, result: None))
    result = module.AIDecisionFilter().evaluate(_request(), enabled=True)
    assert result.allowed is False
    assert result.provider == "jev"
    assert result.decision == "reject"
    assert result.reason == "jev_entry_rejected:risk_block"
    risk_check = next(item for item in result.checks if item["name"] == "risk_check")
    assert risk_check["confidence"] == 0.9
    assert isinstance(captured["json"]["state"], dict)


def test_calibrated_jev_confidence_accepts_clear_entry(monkeypatch):
    answers = _jev_answers(
        risk_check=("clear", {"clear": 0.71, "caution": 0.2, "block": 0.05, "insufficient": 0.04}),
        execution_quality=("clear", {"clear": 0.71, "caution": 0.2, "block": 0.05, "insufficient": 0.04}),
    )
    answers["risk_check"]["confidence"] = 0.57
    answers["execution_quality"]["confidence"] = 0.56

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"answers": answers}

    class LLM:
        def __init__(self):
            raise AssertionError("LLM fallback should not run for a valid JEV result")

    monkeypatch.setattr(module.AIDecisionFilter, "_jev_config", staticmethod(lambda: {
        "api_key": "secret",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-latest",
        "timeout_seconds": "8",
        "min_confidence": "0.55",
    }))
    monkeypatch.setattr(module.requests, "post", lambda *args, **kwargs: Response())
    monkeypatch.setattr(module, "LLMService", LLM)
    monkeypatch.setattr(module.AIDecisionFilter, "_persist", staticmethod(lambda request, result: None))

    result = module.AIDecisionFilter().evaluate(_request(), enabled=True)

    assert result.allowed is True
    assert result.provider == "jev"
    assert result.decision == "pass"


def test_malformed_jev_answer_falls_back_to_llm(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "answers": {
                    "risk_check": {
                        "choice": "clear",
                        "probabilities": {"clear": 0.8, "caution": 0.2},
                        "confidence": 0.6,
                    }
                }
            }

    class LLM:
        def is_configured(self):
            return True

        def get_default_model(self):
            return "fallback-model"

        def call_llm_api(self, *args, **kwargs):
            return '{"decision":"reject","confidence":0.7,"reason":"fallback_check","checks":[]}'

    monkeypatch.setattr(module.AIDecisionFilter, "_jev_config", staticmethod(lambda: {
        "api_key": "secret",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-latest",
        "timeout_seconds": "8",
        "min_confidence": "0.65",
    }))
    monkeypatch.setattr(module.requests, "post", lambda *args, **kwargs: Response())
    monkeypatch.setattr(module, "LLMService", LLM)
    monkeypatch.setattr(module.AIDecisionFilter, "_persist", staticmethod(lambda request, result: None))
    result = module.AIDecisionFilter().evaluate(_request(), enabled=True)
    assert result.allowed is False
    assert result.provider == "llm"
    assert result.fallback_reason.startswith("jev:")


def test_llm_is_used_when_jev_is_not_configured(monkeypatch):
    class LLM:
        def is_configured(self):
            return True

        def get_default_model(self):
            return "configured-model"

        def call_llm_api(self, *args, **kwargs):
            return '{"decision":"pass","confidence":0.8,"reason":"clear","checks":[]}'

    monkeypatch.setattr(module.AIDecisionFilter, "_jev_config", staticmethod(lambda: {
        "api_key": "",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-latest",
        "timeout_seconds": "8",
        "min_confidence": "0.65",
    }))
    monkeypatch.setattr(module, "LLMService", LLM)
    monkeypatch.setattr(module.AIDecisionFilter, "_persist", staticmethod(lambda request, result: None))
    result = module.AIDecisionFilter().evaluate(_request(), enabled=True)
    assert result.allowed is True
    assert result.provider == "llm"
    assert result.model == "configured-model"


def test_jev_config_reads_the_latest_persisted_settings(monkeypatch):
    from app.services.settings import store

    monkeypatch.setenv("JEV_API_KEY", "stale-process-key")
    monkeypatch.setattr(store, "read_settings", lambda: {
        "JEV_API_KEY": "saved-key",
        "JEV_BASE_URL": "https://example.test/v1",
        "JEV_MODEL": "saved-model",
        "JEV_TIMEOUT_SECONDS": "5",
        "JEV_MIN_CONFIDENCE": "0.72",
    })

    assert module.AIDecisionFilter._jev_config() == {
        "api_key": "saved-key",
        "base_url": "https://example.test/v1",
        "model": "saved-model",
        "timeout_seconds": "5",
        "min_confidence": "0.72",
    }


def test_low_confidence_jev_result_falls_back_to_llm(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"answers": _jev_answers(
                risk_check=("clear", {"clear": 0.6, "caution": 0.2, "block": 0.1, "insufficient": 0.1}),
            )}

    class LLM:
        def is_configured(self):
            return True

        def get_default_model(self):
            return "fallback-model"

        def call_llm_api(self, *args, **kwargs):
            return '{"decision":"pass","confidence":0.8,"reason":"evidence_reviewed","checks":[]}'

    monkeypatch.setattr(module.AIDecisionFilter, "_jev_config", staticmethod(lambda: {
        "api_key": "secret",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-latest",
        "timeout_seconds": "8",
        "min_confidence": "0.65",
    }))
    monkeypatch.setattr(module.requests, "post", lambda *args, **kwargs: Response())
    monkeypatch.setattr(module, "LLMService", LLM)
    monkeypatch.setattr(module.AIDecisionFilter, "_persist", staticmethod(lambda request, result: None))

    result = module.AIDecisionFilter().evaluate(_request(), enabled=True)

    assert result.allowed is True
    assert result.provider == "llm"
    assert "confidence below threshold" in result.fallback_reason
    assert "confidence=0.600" in result.fallback_reason
    assert "threshold=0.650" in result.fallback_reason


def test_jev_uses_atomic_checks_without_composite_entry_question(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"answers": _jev_answers()}

    monkeypatch.setattr(module.AIDecisionFilter, "_jev_config", staticmethod(lambda: {
        "api_key": "secret",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-latest",
        "timeout_seconds": "8",
        "min_confidence": "0.55",
    }))
    monkeypatch.setattr(module.requests, "post", lambda *args, **kwargs: (captured.update(kwargs) or Response()))
    monkeypatch.setattr(module.AIDecisionFilter, "_persist", staticmethod(lambda request, result: None))

    result = module.AIDecisionFilter().evaluate(_request(), enabled=True)

    assert "entry_decision" not in captured["json"]["questions"]
    assert result.allowed is True
    assert result.provider == "jev"
    assert result.decision == "pass"
    assert result.confidence == 0.85


def test_billing_charge_and_refund_use_one_decision_reference(monkeypatch):
    from app.services import billing_service

    calls = []

    class Billing:
        def get_feature_cost(self, feature):
            assert feature == "ai_decision_filter"
            return 1

        def check_and_consume(self, user_id, feature, reference_id):
            calls.append(("consume", user_id, feature, reference_id))
            return True, "consumed"

        def add_credits(self, **kwargs):
            calls.append(("refund", kwargs))
            return True, "100"

    monkeypatch.setattr(billing_service, "get_billing_service", lambda: Billing())

    receipt = _REAL_CONSUME_CREDITS(7, "decision-1")
    refunded = module.AIDecisionFilter._refund_credits(7, receipt)

    assert receipt["charged"] == 1
    assert receipt["reference_id"] == "ai-decision:decision-1"
    assert refunded["status"] == "refunded"
    assert refunded["refunded"] == 1
    assert calls[0] == ("consume", 7, "ai_decision_filter", "ai-decision:decision-1")
    assert calls[1][1]["reference_id"] == "ai-decision:decision-1"


def test_ai_decision_filter_default_cost_is_one(monkeypatch):
    from app.services.billing_config import load_billing_config

    monkeypatch.delenv("BILLING_COST_AI_DECISION_FILTER", raising=False)
    assert load_billing_config()["cost_ai_decision_filter"] == 1


def test_insufficient_credits_skip_provider_without_blocking_order(monkeypatch):
    monkeypatch.setattr(module.AIDecisionFilter, "_jev_config", staticmethod(lambda: {
        "api_key": "secret",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-latest",
        "timeout_seconds": "8",
        "min_confidence": "0.65",
    }))
    monkeypatch.setattr(module.AIDecisionFilter, "_consume_credits", staticmethod(lambda *_: _billing_receipt(
        accepted=False,
        charged=0,
        status="rejected",
        message="insufficient_credits:0:1",
    )))
    monkeypatch.setattr(module.requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("provider must not run without credits")
    ))
    monkeypatch.setattr(module.AIDecisionFilter, "_persist", staticmethod(lambda request, result: None))

    result = module.AIDecisionFilter().evaluate(_request(), enabled=True)

    assert result.allowed is True
    assert result.decision == "skipped"
    assert result.reason == "billing_insufficient_credits"
    assert result.billing["charged"] == 0


def test_jev_to_llm_fallback_charges_once(monkeypatch):
    calls = []

    class LLM:
        def is_configured(self):
            return True

        def get_default_model(self):
            return "fallback-model"

        def call_llm_api(self, *args, **kwargs):
            return '{"decision":"pass","confidence":0.8,"reason":"clear","checks":[]}'

    monkeypatch.setattr(module.AIDecisionFilter, "_jev_config", staticmethod(lambda: {
        "api_key": "secret",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-latest",
        "timeout_seconds": "8",
        "min_confidence": "0.65",
    }))
    monkeypatch.setattr(module.AIDecisionFilter, "_consume_credits", staticmethod(
        lambda *_: (calls.append("consume") or _billing_receipt())
    ))
    monkeypatch.setattr(module.AIDecisionFilter, "_refund_credits", staticmethod(
        lambda *_: (_ for _ in ()).throw(AssertionError("successful fallback must not refund"))
    ))
    monkeypatch.setattr(module.requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("jev unavailable")
    ))
    monkeypatch.setattr(module, "LLMService", LLM)
    monkeypatch.setattr(module.AIDecisionFilter, "_persist", staticmethod(lambda request, result: None))

    result = module.AIDecisionFilter().evaluate(_request(), enabled=True)

    assert calls == ["consume"]
    assert result.provider == "llm"
    assert result.billing["charged"] == 1


def test_all_provider_failures_refund_charge(monkeypatch):
    class LLM:
        def is_configured(self):
            return False

    monkeypatch.setattr(module.AIDecisionFilter, "_jev_config", staticmethod(lambda: {
        "api_key": "secret",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-latest",
        "timeout_seconds": "8",
        "min_confidence": "0.65",
    }))
    monkeypatch.setattr(module.AIDecisionFilter, "_consume_credits", staticmethod(lambda *_: _billing_receipt()))
    monkeypatch.setattr(module.AIDecisionFilter, "_refund_credits", staticmethod(
        lambda user_id, receipt: {**receipt, "refunded": 1, "status": "refunded"}
    ))
    monkeypatch.setattr(module.requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("jev unavailable")
    ))
    monkeypatch.setattr(module, "LLMService", LLM)
    monkeypatch.setattr(module.AIDecisionFilter, "_persist", staticmethod(lambda request, result: None))

    result = module.AIDecisionFilter().evaluate(_request(), enabled=True)

    assert result.allowed is True
    assert result.reason == "ai_provider_unavailable"
    assert result.billing["refunded"] == 1
    assert result.billing["status"] == "refunded"


def test_unconfigured_ai_does_not_charge(monkeypatch):
    class LLM:
        def is_configured(self):
            return False

    monkeypatch.setattr(module.AIDecisionFilter, "_jev_config", staticmethod(lambda: {
        "api_key": "",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-latest",
        "timeout_seconds": "8",
        "min_confidence": "0.65",
    }))
    monkeypatch.setattr(module.AIDecisionFilter, "_consume_credits", staticmethod(
        lambda *_: (_ for _ in ()).throw(AssertionError("unconfigured AI must not charge"))
    ))
    monkeypatch.setattr(module, "LLMService", LLM)
    monkeypatch.setattr(module.AIDecisionFilter, "_persist", staticmethod(lambda request, result: None))

    result = module.AIDecisionFilter().evaluate(_request(), enabled=True)

    assert result.allowed is True
    assert result.reason == "ai_not_configured"
