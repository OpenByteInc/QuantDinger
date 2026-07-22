from decimal import Decimal

from app.routes import strategy_review_routes
from app.services.billing_config import load_billing_config


def test_ai_review_cost_defaults_to_10_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("BILLING_COST_AI_REVIEW", raising=False)
    assert load_billing_config()["cost_ai_review"] == 10

    monkeypatch.setenv("BILLING_COST_AI_REVIEW", "25")
    assert load_billing_config()["cost_ai_review"] == 25


def test_rule_only_review_does_not_consume_credits(monkeypatch):
    class FakeBilling:
        def is_billing_enabled(self):
            return True

        def get_feature_cost(self, feature):
            assert feature == "ai_review"
            return 10

        def get_user_credits(self, user_id):
            assert user_id == 7
            return Decimal("50")

        def check_and_consume(self, **kwargs):
            raise AssertionError("rule-only review must not consume credits")

    monkeypatch.setattr(
        strategy_review_routes,
        "get_billing_service",
        lambda: FakeBilling(),
    )

    _billing, charge = strategy_review_routes._consume_ai_review_credits(
        7,
        42,
        include_ai=False,
    )

    assert charge["requested"] is False
    assert charge["cost"] == 10
    assert charge["charged"] == 0
    assert charge["remaining"] == 50


def test_ai_review_charge_reports_insufficient_credits(monkeypatch):
    class FakeBilling:
        def is_billing_enabled(self):
            return True

        def get_feature_cost(self, feature):
            return 10

        def get_user_credits(self, user_id):
            return Decimal("4")

        def check_and_consume(self, **kwargs):
            assert kwargs["feature"] == "ai_review"
            assert kwargs["reference_id"].startswith("strategy_review:42:")
            return False, "insufficient_credits:4:10"

    monkeypatch.setattr(
        strategy_review_routes,
        "get_billing_service",
        lambda: FakeBilling(),
    )

    _billing, charge = strategy_review_routes._consume_ai_review_credits(
        7,
        42,
        include_ai=True,
    )

    assert charge["error"] == "insufficient_credits"
    assert charge["current"] == 4
    assert charge["required"] == 10
    assert charge["shortage"] == 6
    assert charge["charged"] == 0


def test_ai_review_charge_and_failed_ai_refund_share_reference(monkeypatch):
    calls = []

    class FakeBilling:
        def __init__(self):
            self.balance = Decimal("40")

        def is_billing_enabled(self):
            return True

        def get_feature_cost(self, feature):
            return 10

        def get_user_credits(self, user_id):
            return self.balance

        def check_and_consume(self, **kwargs):
            calls.append(("consume", kwargs))
            self.balance -= Decimal("10")
            return True, "consumed"

        def add_credits(self, **kwargs):
            calls.append(("refund", kwargs))
            self.balance += Decimal(str(kwargs["amount"]))
            return True, "refunded"

    fake = FakeBilling()
    monkeypatch.setattr(
        strategy_review_routes,
        "get_billing_service",
        lambda: fake,
    )

    billing, charge = strategy_review_routes._consume_ai_review_credits(
        7,
        42,
        include_ai=True,
    )
    strategy_review_routes._refund_ai_review_credits(
        billing,
        7,
        charge,
        reason="returned fallback",
    )

    consume = calls[0][1]
    refund = calls[1][1]
    assert charge["charged"] == 0
    assert charge["refunded"] == 10
    assert charge["remaining"] == 40
    assert refund["reference_id"] == consume["reference_id"]
    assert refund["action"] == "refund"

