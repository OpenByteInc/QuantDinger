import json

from app.services.market_share import (
    build_public_share_evidence,
    generate_market_share_draft,
    normalize_share_locale,
    validate_market_share_draft,
)


def test_public_share_evidence_excludes_private_listing_fields():
    evidence = build_public_share_evidence(
        {
            "name": "Momentum confirmation",
            "description": "A public description",
            "asset_type": "script_template",
            "author": {"nickname": "Ada", "email": "private@example.com"},
            "view_count": 12,
            "purchase_count": 4,
            "avg_rating": 4.8,
            "code": "SECRET_SOURCE",
            "parameters": {"api_key": "SECRET_KEY"},
            "your_purchase_price": 99,
        },
        {
            "sample_size": 8,
            "total_return": 0.12,
            "sharpe": 1.4,
            "max_drawdown": -0.08,
            "win_rate": 56,
            "trades": [{"private": True}],
        },
    )

    serialized = json.dumps(evidence)
    assert evidence["name"] == "Momentum confirmation"
    assert evidence["verified_performance"]["sample_size"] == 8
    assert "SECRET_SOURCE" not in serialized
    assert "SECRET_KEY" not in serialized
    assert "private@example.com" not in serialized
    assert "your_purchase_price" not in serialized
    assert "trades" not in serialized


def test_ai_share_generation_is_grounded_and_validated():
    class FakeLlm:
        def __init__(self):
            self.messages = None
            self.last_model_used = "test/article-model"
            self.last_provider_used = "test-provider"

        def call_llm_api(self, messages, **kwargs):
            self.messages = messages
            assert kwargs["use_json_mode"] is True
            return json.dumps({
                "title": "A measured look at momentum",
                "body": "A clear hook backed by the public listing facts.",
                "hashtags": ["#QuantDinger", "MarketResearch", "bad tag!"],
            })

    fake = FakeLlm()
    result = generate_market_share_draft(
        {"name": "Momentum", "description": "Public evidence", "author": {"nickname": "Ada"}},
        None,
        share_format="professional",
        locale="de-DE",
        llm=fake,
    )

    assert result["body"].startswith("A clear hook")
    assert result["hashtags"] == ["QuantDinger", "MarketResearch", "badtag"]
    assert result["locale"] == "de-DE"
    assert result["model"] == "test/article-model"
    assert result["provider"] == "test-provider"
    assert "Write in German" in fake.messages[0]["content"]
    assert "Translate or naturally paraphrase" in fake.messages[0]["content"]
    assert "Public evidence" in fake.messages[1]["content"]


def test_article_prompt_requires_a_complete_long_form_structure():
    class FakeLlm:
        last_model_used = "test/article-model"
        last_provider_used = "test-provider"

        def __init__(self):
            self.messages = None

        def call_llm_api(self, messages, **kwargs):
            self.messages = messages
            return json.dumps({"title": "标题", "body": "正文", "hashtags": []})

    fake = FakeLlm()
    generate_market_share_draft(
        {"name": "Momentum", "description": "Public evidence"},
        None,
        share_format="article",
        locale="zh-CN",
        llm=fake,
    )

    prompt = fake.messages[0]["content"]
    assert "complete, publication-ready article" in prompt
    assert "900 to 1,400 characters" in prompt
    assert "due-diligence questions" in prompt


def test_ai_share_validation_rejects_missing_body_and_normalizes_locale():
    assert normalize_share_locale("zh_CN") == "zh-CN"
    assert normalize_share_locale("fr") == "fr-FR"
    try:
        validate_market_share_draft({"title": "No body"}, "short")
    except ValueError as exc:
        assert str(exc) == "ai_share_body_missing"
    else:
        raise AssertionError("missing AI body must be rejected")
