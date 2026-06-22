from datetime import datetime

from app.services.backtest_limits import validate_backtest_range


def test_forex_intraday_range_error_includes_actionable_recommendation():
    err = validate_backtest_range(
        market="Forex",
        symbol="EURUSD",
        timeframe="15m",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 4, 1, 23, 59, 59),
    )

    assert err is not None
    assert err["error_type"] == "BACKTEST_RANGE_LIMIT"
    assert err["max_days"] == 60
    assert err["recommendation_available"] is True
    assert err["recommended_start"] == "2024-02-02"
    assert err["recommended_end"] == "2024-02-29"
    assert "Suggested fix: use 2024-02-02 to 2024-04-01" in err["msg"]
    assert "set end date to 2024-02-29" in err["msg"]


def test_recommendation_accounts_for_indicator_warmup_bars():
    err = validate_backtest_range(
        market="Forex",
        symbol="EURUSD",
        timeframe="15m",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 4, 1, 23, 59, 59),
        warmup_bars=96,
    )

    assert err is not None
    assert err["warmup_bars"] == 96
    assert err["warmup_days"] == 1
    assert err["fetch_start"] == "2023-12-31"
    assert err["recommended_start"] == "2024-02-03"
    assert err["recommended_end"] == "2024-02-28"
    assert "including 96 warmup bars" in err["msg"]


def test_range_equal_to_limit_is_allowed():
    err = validate_backtest_range(
        market="Forex",
        symbol="EURUSD",
        timeframe="15m",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 3, 1, 0, 0, 0),
    )

    assert err is None


def test_warmup_larger_than_policy_has_no_fake_date_recommendation():
    err = validate_backtest_range(
        market="USStock",
        symbol="TSLA",
        timeframe="1m",
        start_date=datetime(2024, 1, 10),
        end_date=datetime(2024, 1, 10, 23, 59, 59),
        warmup_bars=60 * 24 * 10,
    )

    assert err is not None
    assert err["max_days"] == 7
    assert err["warmup_days"] == 10
    assert err["recommendation_available"] is False
    assert err["recommended_start"] is None
    assert err["recommended_end"] is None
    assert "warmup alone exceeds" in err["msg"]
