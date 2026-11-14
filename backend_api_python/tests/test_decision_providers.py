import pytest
import requests

from app.services import ai_decision_filter
from app.services.decision_providers import (
    ChoiceAnswer,
    DecisionProviderError,
    JevDecisionProvider,
    question_options,
)


QUESTIONS = {
    "direction": {
        "type": "choice",
        "instructions": "Pick a direction.",
        "criteria": {"up": "Price rises.", "down": "Price falls.", "flat": "No material move."},
    },
}


class _Response:
    def __init__(self, body, error=None):
        self.body = body
        self.error = error

    def raise_for_status(self):
        if self.error is not None:
            raise self.error

    def json(self):
        return self.body


def _provider(body=None, *, captured=None, error=None, **overrides):
    def post(url, **kwargs):
        if captured is not None:
            captured.update(kwargs, url=url)
        return _Response(body or {}, error=error)

    values = {
        "api_key": "secret",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-latest",
        "timeout_seconds": "8",
        "post": post,
    }
    values.update(overrides)
    return JevDecisionProvider(**values)


def _answer(choice="up", probabilities=None, **extra):
    return {
        "choice": choice,
        "probabilities": probabilities or {"up": 0.7, "down": 0.2, "flat": 0.1},
        **extra,
    }


def test_jev_provider_sends_systemone_request_and_normalizes_answers():
    captured = {}
    provider = _provider({"answers": {"direction": _answer(confidence=0.66)}}, captured=captured)

    answers = provider.evaluate({"symbol": "BTC/USDT"}, QUESTIONS)

    assert answers == {
        "direction": ChoiceAnswer(
            choice="up",
            probabilities={"up": 0.7, "down": 0.2, "flat": 0.1},
            confidence=0.66,
        )
    }
    assert captured["url"] == "https://api.typesafe.ai/v1/systemone"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["json"] == {"model": "jev-latest", "state": {"symbol": "BTC/USDT"}, "questions": QUESTIONS}
    assert captured["timeout"] == 8.0
    assert provider.name == "jev"


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.typesafe.ai/v1/", "https://api.typesafe.ai/v1/systemone"),
        (" https://example.test/v1/systemone ", "https://example.test/v1/systemone"),
    ],
)
def test_jev_provider_appends_systemone_once(base_url, expected):
    assert _provider(base_url=base_url).url == expected


def test_jev_provider_defaults_blank_model_and_clamps_timeout():
    assert _provider(model="  ").model == "jev-latest"
    assert _provider(timeout_seconds="").timeout_seconds == 8.0
    assert _provider(timeout_seconds="0.2").timeout_seconds == 1.0
    assert _provider(timeout_seconds=120).timeout_seconds == 30.0


@pytest.mark.parametrize(
    "body",
    [
        {"result": {"direction": _answer()}},
        {"data": {"answers": {"direction": _answer()}}},
        {"answers": {"direction": {"selected": "UP", "probability": {"up": 0.7, "down": 0.2, "flat": 0.1}}}},
    ],
)
def test_jev_provider_accepts_documented_answer_shapes(body):
    answer = _provider(body).evaluate({}, QUESTIONS)["direction"]

    assert answer.choice == "up"
    assert answer.probabilities["up"] == 0.7


def test_jev_provider_keeps_raw_confidence_for_service_policy():
    missing = _provider({"answers": {"direction": _answer()}}).evaluate({}, QUESTIONS)["direction"]
    numeric_text = _provider({"answers": {"direction": _answer(confidence="0.7")}}).evaluate({}, QUESTIONS)
    unclamped = _provider({"answers": {"direction": _answer(confidence=1.2)}}).evaluate({}, QUESTIONS)["direction"]

    assert missing.confidence is None
    assert numeric_text["direction"].confidence == 0.7
    assert unclamped.confidence == 1.2


def test_jev_provider_accepts_a_choice_tied_for_highest_probability():
    probabilities = {"up": 0.45, "down": 0.45, "flat": 0.1}
    answer = _provider({"answers": {"direction": _answer("down", probabilities)}}).evaluate({}, QUESTIONS)

    assert answer["direction"].choice == "down"


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ({}, "did not contain a valid direction"),
        (_answer("sideways"), "did not contain a valid direction"),
        (_answer(probabilities={"up": 0.8, "down": 0.2}), "probabilities were incomplete for direction"),
        (_answer(probabilities={"up": "x", "down": 0.2, "flat": 0.1}), "probability was invalid for direction"),
        (_answer(probabilities={"up": 1.2, "down": -0.1, "flat": -0.1}), "probability was out of range for direction"),
        (_answer(probabilities={"up": 0.5, "down": 0.2, "flat": 0.1}), "probabilities did not sum to one for direction"),
        (_answer("down"), "choice was not the highest probability for direction"),
        (_answer(confidence=None), "confidence was invalid for direction"),
        (_answer(confidence="high"), "confidence was invalid for direction"),
    ],
)
def test_jev_provider_rejects_malformed_answers(answer, message):
    provider = _provider({"answers": {"direction": answer}})

    with pytest.raises(DecisionProviderError, match=message):
        provider.evaluate({}, QUESTIONS)


def test_jev_provider_propagates_http_errors_and_timeouts():
    unavailable = _provider(error=requests.HTTPError("503 Service Unavailable"))

    def timeout(*args, **kwargs):
        raise requests.Timeout("read timed out")

    with pytest.raises(requests.HTTPError):
        unavailable.evaluate({}, QUESTIONS)
    with pytest.raises(requests.Timeout):
        _provider(post=timeout).evaluate({}, QUESTIONS)


def test_question_options_follow_question_criteria():
    assert question_options(QUESTIONS["direction"]) == {"up", "down", "flat"}
    assert question_options({"type": "choice"}) == set()


def test_filter_option_sets_match_question_criteria():
    questions = ai_decision_filter.JEV_QUESTIONS
    options = ai_decision_filter.JEV_CHECK_OPTIONS
    assert list(questions) == list(options)
    assert {name: question_options(question) for name, question in questions.items()} == options
