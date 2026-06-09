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
    assert err["recommended_start"] == "2024-02-02"
    assert err["recommended_end"] == "2024-02-29"
    assert "Suggested range: 2024-02-02 to 2024-04-01" in err["msg"]
    assert "end by 2024-02-29" in err["msg"]


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
    assert err["fetch_start"] == "2023-12-31"
    assert err["recommended_start"] == "2024-02-03"
    assert err["recommended_end"] == "2024-02-28"
    assert "including 96 warmup bars" in err["msg"]
