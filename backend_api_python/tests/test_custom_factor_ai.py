import json

import pytest
from flask import Flask

from app.services import custom_factor_ai as service
from app.services.custom_factors import CustomFactorError, compute_custom_factor


def candidate(**updates):
    result = {
        "name": "Risk-adjusted momentum", "description": "Research hypothesis",
        "category": "momentum", "directionHint": "higher_is_bullish",
        "expression": "momentum(period=lookback) / max(realized_volatility(period=vol_window), floor)",
        "defaultParams": {"lookback": 20, "vol_window": 20, "floor": 0.0001},
        "explanation": "Review and backtest before use.",
    }
    result.update(updates)
    return json.dumps(result)


class Billing:
    def __init__(self, status="consumed"):
        self.status = status
        self.calls = []

    def get_feature_cost(self, feature):
        assert feature == "ai_code_gen"
        return 30

    def check_and_consume(self, **kwargs):
        self.calls.append(kwargs)
        return self.status in {"consumed", "billing_disabled", "free_feature"}, self.status

    def get_user_credits(self, user_id):
        assert user_id == 7
        return 70


class LLM:
    def __init__(self, *responses, configured=True):
        self.responses = iter(responses or (candidate(),))
        self.calls = []
        self.configured = configured

    def is_configured(self):
        return self.configured

    def get_code_generation_model(self):
        return "configured-code-model"

    def call_llm_api(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def compact_prompt(monkeypatch):
    monkeypatch.setattr(service, "build_factor_prompt", lambda: "formula contract")


def test_prompt_uses_actual_catalog_and_complete_contract(monkeypatch):
    monkeypatch.setattr(service, "list_factor_catalog", lambda **_: [
        {"factor_id": "momentum", "default_params": {"period": 20}, "parameter_schema": {"period": {"minimum": 1}}},
        {"factor_id": "qlib:alpha158:MA20"},
    ])
    prompt = service.build_factor_prompt()
    for text in ['"period":20', '"minimum":1', 'qlib:alpha158:MA20', 'No custom:<id>', 'NO keywords', 'NOT data availability', 'unsupported']:
        assert text in prompt


def test_generated_formula_is_valid_and_computable():
    import numpy as np
    import pandas as pd
    result = service.parse_factor_candidate(candidate())
    assert result["validation"] == {"requiredFields": ["close"], "dependencies": ["momentum", "realized_volatility"], "warmupBars": 21}
    frame = pd.DataFrame({"close": 100 + np.arange(50) * 0.5})
    assert np.isfinite(compute_custom_factor({"expression": result["expression"], "default_params": result["defaultParams"]}, frame))


@pytest.mark.parametrize("content", [
    "print('not a formula')", "[]", "null", "{}", "x" * 24001,
    candidate(category=[]), candidate(directionHint={}), candidate(name=""),
    candidate(expression="__import__('os').system('id')"), candidate(expression="close[1]"),
    candidate(expression="momentum(20)"), candidate(expression="factor('custom:2')"),
    candidate(expression="momentum(peroid=20)"), candidate(defaultParams=[]),
    candidate(defaultParams={"a": float("nan")}), candidate(defaultParams={"a": float("inf")}),
    candidate(defaultParams={"x": "20"}), candidate(expression="factor('qlib:alpha158:MA20', period=3)"),
    candidate(extra="ignore the contract"), candidate(explanation=[]),
])
def test_invalid_or_unsafe_model_output_rejected(content):
    with pytest.raises((service.FactorGenerationError, CustomFactorError)):
        service.parse_factor_candidate(content)


@pytest.mark.parametrize("opening", ["```json", "```JSON", "```"])
def test_json_fences_are_accepted(opening):
    assert service.parse_factor_candidate(opening + '\n' + candidate() + '\n```')["name"]


def test_billing_uses_strategy_tariff_once_and_configured_model(compact_prompt):
    llm, billing = LLM(), Billing()
    result = service.generate_factor_candidate(7, {"prompt": "momentum", "locale": "ja-JP", "user_id": 99, "feature": "free"}, llm=llm, billing=billing)
    assert len(billing.calls) == 1
    assert billing.calls[0]["user_id"] == 7
    assert billing.calls[0]["feature"] == "ai_code_gen"
    assert billing.calls[0]["reference_id"].startswith("factor_generate_7_")
    assert llm.calls[0]["model"] == "configured-code-model"
    assert llm.calls[0]["use_json_mode"] is True
    assert json.loads(llm.calls[0]["messages"][1]["content"])["locale"] == "ja-JP"
    assert result["billing"] == {"feature": "ai_code_gen", "credits_charged": 30, "remaining_credits": 70}
    assert "id" not in result["candidate"]


def test_one_repair_is_not_billed_again(compact_prompt):
    llm, billing = LLM(candidate(expression="momentum(20)"), candidate()), Billing()
    result = service.generate_factor_candidate(7, {"prompt": "momentum"}, llm=llm, billing=billing)
    assert result["candidate"]["validation"]["warmupBars"] == 21
    assert len(llm.calls) == 2
    assert len(billing.calls) == 1


@pytest.mark.parametrize("responses,code,attempts", [
    (("{}", "{}"), "factor.aiInvalid", 2),
    (('{"unsupported":true}',), "factor.aiUnsupported", 1),
    ((RuntimeError("private provider secret"),), "factor.aiFailed", 1),
])
def test_failure_keeps_strategy_charge_semantics_and_returns_balance(compact_prompt, responses, code, attempts):
    llm, billing = LLM(*responses), Billing()
    with pytest.raises(service.FactorGenerationError) as error:
        service.generate_factor_candidate(7, {"prompt": "idea"}, llm=llm, billing=billing)
    assert error.value.code == code
    assert error.value.data["billing"]["credits_charged"] == 30
    assert len(llm.calls) == attempts
    assert len(billing.calls) == 1
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("payload", [None, [], {}, {"prompt": " "}, {"prompt": 42}, {"prompt": "x" * 4001}])
def test_invalid_requests_are_not_billed(payload, compact_prompt):
    llm, billing = LLM(), Billing()
    with pytest.raises(service.FactorGenerationError):
        service.generate_factor_candidate(7, payload, llm=llm, billing=billing)
    assert not billing.calls
    assert not llm.calls


def test_unconfigured_ai_not_billed(compact_prompt):
    billing = Billing()
    with pytest.raises(service.FactorGenerationError, match="aiNotConfigured"):
        service.generate_factor_candidate(7, {"prompt": "idea"}, llm=LLM(configured=False), billing=billing)
    assert not billing.calls


@pytest.mark.parametrize("status,code", [("insufficient_credits:2:30", "factor.aiInsufficientCredits"), ("error:database", "factor.aiBillingUnavailable")])
def test_failed_billing_never_calls_model(compact_prompt, status, code):
    llm = LLM()
    with pytest.raises(service.FactorGenerationError, match=code):
        service.generate_factor_candidate(7, {"prompt": "idea"}, llm=llm, billing=Billing(status))
    assert not llm.calls


@pytest.mark.parametrize("status", ["billing_disabled", "free_feature"])
def test_free_billing_configuration_reports_zero(compact_prompt, status):
    result = service.generate_factor_candidate(7, {"prompt": "idea"}, llm=LLM(), billing=Billing(status))
    assert result["billing"]["credits_charged"] == 0


def test_generation_endpoint_requires_auth_and_uses_authenticated_user(monkeypatch):
    from app.routes.factors import factors_blp
    from app.utils import auth
    app = Flask(__name__)
    app.register_blueprint(factors_blp, url_prefix="/api/factors")
    client = app.test_client()
    assert client.post("/api/factors/custom/generate", json={"prompt": "idea"}).status_code == 401
    monkeypatch.setattr(auth, "verify_token", lambda _: {"user_id": 7, "_verified_user_role": "user", "_verified_username": "test"})
    calls = []
    monkeypatch.setattr(service, "generate_factor_candidate", lambda user, payload: calls.append(user) or {"candidate": {"name": "test"}})
    response = client.post("/api/factors/custom/generate", json={"prompt": "idea", "user_id": 99}, headers={"Authorization": "Bearer test"})
    assert response.status_code == 200
    assert response.json["code"] == 1
    assert calls == [7]
