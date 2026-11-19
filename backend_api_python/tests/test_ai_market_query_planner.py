import pytest

from app.services.ai_market_query import (
    build_market_query_plan,
    closed_ohlcv,
    compute_technical_evidence,
    evaluate_plan_completeness,
    extract_timeframes,
    snapshot_options_from_plan,
)


def _instrument(symbol="BTC/USDT", market="Crypto"):
    return {"market": market, "symbol": symbol, "name": symbol}


@pytest.mark.parametrize(
    ("message", "expected_task", "expected_metric"),
    [
        ("BTC是不是站上前高了", "breakout_analysis", "breakout"),
        ("比特币这里算不算放量上破", "breakout_analysis", "volume_ratio"),
        ("SOL跌破关键位了吗", "breakout_analysis", "support_resistance"),
        ("ETH超卖了吗", "indicator_analysis", "rsi14"),
        ("MSFT压力位在哪里", "support_resistance", "support_resistance"),
        ("AAPL MACD金叉了吗", "indicator_analysis", "macd"),
        ("看看BTC布林带位置", "indicator_analysis", "bollinger20"),
        ("BTC当前价是多少", "quote", "price"),
    ],
)
def test_paraphrases_normalize_to_stable_market_tasks(message, expected_task, expected_metric):
    plan = build_market_query_plan(message, {}, [_instrument()])
    assert plan["task"] == expected_task
    assert expected_metric in plan["metrics"]


def test_comparison_and_multi_timeframe_plan_is_deterministic():
    plan = build_market_query_plan(
        "比较 BTC 和 ETH 的15分钟与4小时趋势、RSI和成交量",
        {"exchange_id": "gate", "market_type": "spot"},
        [_instrument("BTC/USDT"), _instrument("ETH/USDT")],
    )
    assert plan["task"] == "comparison"
    assert plan["timeframes"] == ["15m", "4H"]
    assert {"trend", "rsi14", "volume_ratio"}.issubset(plan["metrics"])
    assert plan["lookback"] == 100
    options = snapshot_options_from_plan(plan)
    assert options["snapshot_timeframes"] == ["15m", "4H"]
    assert options["snapshot_limit"] == 100
    assert options["exchange_id"] == "gate"


def test_a_share_technical_plan_preserves_cn_stock_identity():
    plan = build_market_query_plan(
        "贵州茅台日线是否站上 EMA200",
        {"timeframe": "1D"},
        [_instrument("600519", "CNStock")],
    )
    assert plan["instruments"] == [
        {"market": "CNStock", "symbol": "600519", "name": "600519"}
    ]
    assert plan["timeframes"] == ["1D"]
    assert "ema200" in plan["metrics"]


def test_quote_plan_skips_klines_but_ema200_allocates_history():
    quote = build_market_query_plan("BTC现在多少钱", {}, [_instrument()])
    assert quote["timeframes"] == []
    assert snapshot_options_from_plan(quote)["skip_klines"] is True

    ema = build_market_query_plan("BTC日线是否站上EMA200", {}, [_instrument()])
    assert ema["timeframes"] == ["1D"]
    assert ema["lookback"] == 240


def test_explicit_return_horizon_is_carried_into_the_execution_contract():
    plan = build_market_query_plan(
        "比较 BTC 和 ETH 最近 7 个交易日收益",
        {},
        [_instrument("BTC/USDT"), _instrument("ETH/USDT")],
    )
    assert plan["parameters"]["return_horizons"] == [7]
    assert plan["metrics"] == ["returns"]
    assert plan["lookback"] == 21


def test_semantic_hints_are_validated_and_do_not_invent_metric_ids():
    plan = build_market_query_plan(
        "帮我看一下这个形态",
        {},
        [_instrument()],
        {
            "market_task": "breakout_analysis",
            "metrics": ["breakout", "volume_ratio", "made_up_indicator"],
            "analysis_timeframes": ["4h"],
        },
    )
    assert plan["task"] == "breakout_analysis"
    assert "made_up_indicator" not in plan["metrics"]
    assert plan["timeframes"] == ["4H"]
    assert plan["semantic_hints_used"] is True


def test_timeframe_extraction_handles_languages_and_order():
    assert extract_timeframes("先看15分钟，再看1小时和日线") == ["15m", "1H", "1D"]
    assert extract_timeframes("Review 4h and 1d MACD") == ["4H", "1D"]


def test_forming_candle_is_excluded_from_closed_evidence():
    rows = [
        {"time": 0, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 100},
        {"time": 3600, "open": 101, "high": 103, "low": 100, "close": 102, "volume": 110},
        {"time": 7200, "open": 102, "high": 120, "low": 101, "close": 119, "volume": 1000},
    ]
    closed, forming = closed_ohlcv(rows, "1H", now_ts=7500)
    evidence = compute_technical_evidence(
        rows,
        "1H",
        ["price", "returns"],
        closed_candle_only=True,
        now_ts=7500,
    )
    assert forming is True
    assert len(closed) == 2
    assert evidence["forming_candle_excluded"] is True
    assert evidence["metrics"]["price"] == 102


def _breakout_rows(last_close=113.0, last_volume=220.0):
    rows = []
    for index in range(60):
        close = 100 + (index % 8) * 0.5
        rows.append({
            "time": index * 86400,
            "open": close - 0.2,
            "high": 110.0 if index == 30 else close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 100.0,
        })
    rows.append({
        "time": 60 * 86400,
        "open": 109.0,
        "high": last_close + 1.0,
        "low": 108.5,
        "close": last_close,
        "volume": last_volume,
    })
    return rows


def test_breakout_uses_prior_levels_and_closed_volume_confirmation():
    rows = _breakout_rows()
    now_ts = 62 * 86400
    evidence = compute_technical_evidence(
        rows,
        "1D",
        ["breakout", "support_resistance", "volume_ratio", "macd"],
        now_ts=now_ts,
    )
    breakout = evidence["metrics"]["breakout"]
    levels = evidence["metrics"]["support_resistance"]
    assert levels["excludes_signal_candle"] is True
    assert levels["prior_high"] == 110.0
    assert breakout["reference_excludes_signal_candle"] is True
    assert breakout["status"] == "confirmed_up"
    assert breakout["volume_confirmed"] is True
    assert breakout["signal_close"] == 113.0


def test_low_volume_breakout_is_not_reported_as_confirmed():
    evidence = compute_technical_evidence(
        _breakout_rows(last_volume=80.0),
        "1D",
        ["breakout", "volume_ratio"],
        now_ts=62 * 86400,
    )
    assert evidence["metrics"]["breakout"]["status"] == "unconfirmed_up"
    assert evidence["metrics"]["breakout"]["volume_confirmed"] is False


def test_missing_signal_candle_volume_cannot_reuse_a_previous_bars_volume():
    rows = _breakout_rows()
    rows[-1]["volume"] = None
    evidence = compute_technical_evidence(
        rows,
        "1D",
        ["breakout", "volume_ratio"],
        closed_candle_only=True,
        now_ts=10_000_000,
    )
    assert "volume_ratio" in evidence["missing_metrics"]
    assert evidence["metrics"]["breakout"]["status"] == "unconfirmed_up"
    assert evidence["metrics"]["breakout"]["volume_confirmed"] is False


def test_completeness_reports_missing_instrument_timeframe_and_metrics():
    plan = build_market_query_plan("BTC 4小时RSI和MACD", {}, [_instrument()])
    snapshot = {
        "market": "Crypto",
        "symbol": "BTC/USDT",
        "timeframes": {
            "4H": {
                "available": True,
                "technical": {"missing_metrics": ["macd"]},
            }
        },
    }
    status = evaluate_plan_completeness(plan, [snapshot])
    assert status["complete"] is False
    assert status["missing"][0]["reason"] == "metric_history_insufficient"
    assert status["missing"][0]["metrics"] == ["macd"]
