"""Live indicator both-mode signal mapping must match backtest semantics."""

from app.services.trading_executor import TradingExecutor


def test_both_mode_allows_open_long_from_short_state():
    ex = TradingExecutor()
    assert ex._is_signal_allowed("short", "open_long", indicator_both_mode=True) is True
    assert ex._is_signal_allowed("short", "open_long", indicator_both_mode=False) is False


def test_both_mode_allows_open_short_from_long_state():
    ex = TradingExecutor()
    assert ex._is_signal_allowed("long", "open_short", indicator_both_mode=True) is True
    assert ex._is_signal_allowed("long", "open_short", indicator_both_mode=False) is False


def test_both_mode_does_not_use_buy_as_close_short():
    """Regression: buy used to map to close_short+open_long; backtest only uses open_long (flip)."""
    import pandas as pd

    ex = TradingExecutor()
    idx = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [1.0, 1.0, 1.0],
            "high": [1.0, 1.0, 1.0],
            "low": [1.0, 1.0, 1.0],
            "close": [1.0, 1.0, 1.0],
            "volume": [1.0, 1.0, 1.0],
            "buy": [False, True, False],
            "sell": [False, False, False],
        },
        index=idx,
    )
    tc = {"trade_direction": "both"}
    result = ex._execute_indicator_with_prices(
        indicator_code="# noop\noutput = {}",
        df=df,
        trading_config=tc,
    )
    assert result is not None
    pending = result.get("pending_signals") or []
    types = {s.get("type") for s in pending}
    assert "close_short" not in types
    assert result.get("indicator_both_mode") is True
