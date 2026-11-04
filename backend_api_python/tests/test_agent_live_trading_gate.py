from app.routes.agent_v1 import quick_trade


def test_agent_live_trading_requires_global_live_execution(monkeypatch):
    monkeypatch.setenv("AGENT_LIVE_TRADING_ENABLED", "true")
    monkeypatch.setattr(quick_trade, "is_live_trading_enabled", lambda: False)

    assert quick_trade._live_trading_kill_switch() is False


def test_agent_live_trading_requires_agent_switch(monkeypatch):
    monkeypatch.setenv("AGENT_LIVE_TRADING_ENABLED", "false")
    monkeypatch.setattr(quick_trade, "is_live_trading_enabled", lambda: True)

    assert quick_trade._live_trading_kill_switch() is False


def test_agent_live_trading_is_enabled_only_when_both_switches_are_on(monkeypatch):
    monkeypatch.setenv("AGENT_LIVE_TRADING_ENABLED", "true")
    monkeypatch.setattr(quick_trade, "is_live_trading_enabled", lambda: True)

    assert quick_trade._live_trading_kill_switch() is True
