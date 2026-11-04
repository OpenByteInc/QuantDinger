from __future__ import annotations

import pytest
from flask import g

from app.routes.agent_v1.backtests import _validate_request
from app.routes.agent_v1.quick_trade import _paper_fill_outcome
from app.utils import agent_auth


STATIC_CODE = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.set_benchmark("USStock:SPY")
    context.subscribe(frequency="1d")

def handle_data(context, data):
    pass
"""


DYNAMIC_CODE = """
def initialize(context):
    context.set_universe(pool="sp500")
    context.subscribe(frequency="1d")
    run_weekly(rebalance)

def rebalance(context, data):
    pass
"""


def _payload(code: str) -> dict:
    return {
        "code": code,
        "startDate": "2025-01-01",
        "endDate": "2025-12-31",
        "params": {},
    }


def test_backtest_rejects_missing_dates(app):
    with app.test_request_context("/"):
        g.agent_token = {"markets": "*", "instruments": "*"}
        _, err = _validate_request({"code": STATIC_CODE})
        assert err[1] == 400
        assert err[0].get_json()["message"].startswith("startDate and endDate")


def test_backtest_checks_benchmark_against_instrument_allowlist(app):
    with app.test_request_context("/"):
        g.agent_token = {"markets": "USStock", "instruments": "AAPL"}
        _, err = _validate_request(_payload(STATIC_CODE))
        assert err[1] == 403
        assert err[0].get_json()["message"] == "Instrument not allowed: SPY"


def test_backtest_rejects_dynamic_universe_for_restricted_token(app):
    with app.test_request_context("/"):
        g.agent_token = {"markets": "*", "instruments": "AAPL"}
        _, err = _validate_request(_payload(DYNAMIC_CODE))
        assert err[1] == 403
        assert "Dynamic universes" in err[0].get_json()["message"]


@pytest.fixture(autouse=True)
def _reset_agent_auth(monkeypatch):
    agent_auth._rate_state.clear()
    monkeypatch.setattr(agent_auth, "_reserve_idempotency", lambda *_: ("reserved", None))
    monkeypatch.setattr(agent_auth, "_complete_idempotency", lambda *_: None)
    yield
    agent_auth._rate_state.clear()


def test_live_capable_token_never_silently_falls_back_to_paper(client, monkeypatch):
    token = {
        "id": 9,
        "user_id": 1,
        "name": "live-agent",
        "scopes": "R,T",
        "markets": "*",
        "instruments": "*",
        "paper_only": False,
        "rate_limit_per_min": 60,
        "status": "active",
        "expires_at": None,
    }
    agent_auth._schema_ready = True
    monkeypatch.setattr(agent_auth, "_lookup_token", lambda _: token)
    monkeypatch.setattr(agent_auth, "_touch_token_last_used", lambda *_: None)
    monkeypatch.setattr(agent_auth, "_audit", lambda *args, **kwargs: None)
    monkeypatch.delenv("AGENT_LIVE_TRADING_ENABLED", raising=False)

    response = client.post(
        "/api/agent/v1/quick-trade/orders",
        headers={
            "Authorization": "Bearer qd_agent_TESTTOKEN12345",
            "Idempotency-Key": "live-disabled-check",
        },
        json={"market": "Crypto", "symbol": "BTC/USDT", "side": "buy", "qty": 0.01},
    )
    assert response.status_code == 501
    assert "AGENT_LIVE_TRADING_ENABLED" in response.get_json()["message"]


def test_agent_gateway_switch_rejects_all_agent_routes(client, monkeypatch):
    monkeypatch.setenv("AGENT_GATEWAY_ENABLED", "false")

    response = client.get("/api/agent/v1/health")

    assert response.status_code == 503
    assert response.get_json()["message"] == "agentGateway.disabled"


def test_paper_limit_order_only_fills_when_marketable():
    waiting = _paper_fill_outcome(
        {"side": "buy", "order_type": "limit", "limit_price": 90},
        100,
    )
    assert waiting[0] is None
    assert waiting[1] == "submitted"

    filled = _paper_fill_outcome(
        {"side": "buy", "order_type": "limit", "limit_price": 110},
        100,
    )
    assert filled == (100.0, "filled", "")
