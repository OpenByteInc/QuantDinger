"""Strategy performance KPI must use initial capital as baseline."""
from app.routes.strategy import _strategy_performance_summary


def test_total_return_pct_uses_initial_capital_not_first_trade_point():
    curve = [
        {"time": 1, "equity": 10.0},
        {"time": 2, "equity": 9.55},
    ]
    summary = _strategy_performance_summary(10.0, curve)
    assert summary["total_return"] == -0.45
    assert summary["total_return_pct"] == -4.5


def test_old_buggy_baseline_would_have_given_wrong_pct():
    """First post-trade point was ~9.9 while initial was 10 — old UI math landed near -3.3%."""
    curve = [
        {"time": 1, "equity": 9.9},
        {"time": 2, "equity": 9.55},
    ]
    wrong_pct = (9.55 - 9.9) / 9.9 * 100.0
    summary = _strategy_performance_summary(10.0, curve)
    assert round(wrong_pct, 2) == -3.54
    assert summary["total_return_pct"] == -4.5
