import pandas as pd
import pytest

from app.services.factors import compute_factor, list_factors
from app.services.strategy_v2.factor_research import FactorResearchEngine


@pytest.mark.parametrize("direction", [1.0, -1.0])
def test_factor_research_rank_ic_sign_uses_next_open_forward_return(direction):
    index = pd.date_range("2026-01-01", periods=8, freq="B")
    frames = {}
    for offset, symbol in enumerate(["A", "B", "C", "D", "E", "F"], start=1):
        open_prices = [
            100.0 * (1.0 + direction * offset * 0.005 * day)
            for day in range(len(index))
        ]
        close_prices = [
            100.0 * (1.0 - direction * offset * 0.005 * day)
            for day in range(len(index))
        ]
        frames[symbol] = pd.DataFrame({
            "open": open_prices,
            "close": close_prices,
            "signal": [float(offset)] * len(index),
        }, index=index)

    result = FactorResearchEngine().run(
        frames=frames,
        factor_id="signal",
        start_date=index[0],
        end_date=index[-1],
        groups=3,
        holding_period=1,
        commission=0.0,
        slippage=0.0,
    )

    assert result["rankIc"] == pytest.approx(direction)
    assert result["icSeries"][0]["value"] == pytest.approx(direction)


def test_factor_research_evaluates_custom_formula_point_in_time():
    index = pd.date_range("2025-01-01", periods=90, freq="B")
    frames = {}
    for offset, symbol in enumerate(["A", "B", "C", "D", "E", "F"], start=1):
        prices = [100.0 + day * offset * 0.1 for day in range(len(index))]
        frames[symbol] = pd.DataFrame({
            "open": prices,
            "high": [value + 1.0 for value in prices],
            "low": [value - 1.0 for value in prices],
            "close": prices,
            "volume": [1000.0 + offset] * len(index),
        }, index=index)

    result = FactorResearchEngine().run(
        frames=frames,
        factor_id="custom:1",
        factor_params={"lookback": 20},
        custom_factor={
            "expression": "momentum(period=lookback)",
            "default_params": {"lookback": 20},
        },
        start_date=index[30],
        end_date=index[-1],
        groups=3,
        holding_period=5,
    )

    assert result["factorId"] == "custom:1"
    assert result["rankIc"] > 0
    assert result["coverage"] == pytest.approx(1.0)


def test_factor_research_returns_ic_groups_costs_and_stability():
    index = pd.date_range("2025-01-01", periods=90, freq="B")
    frames = {}
    for offset, symbol in enumerate(["A", "B", "C", "D", "E", "F"]):
        prices = [100 + offset * 3 + day * (0.1 + offset * 0.02) for day in range(len(index))]
        frames[f"USStock:{symbol}"] = pd.DataFrame({
            "open": prices,
            "high": [value * 1.01 for value in prices],
            "low": [value * 0.99 for value in prices],
            "close": prices,
            "volume": [100000] * len(index),
            "industry": ["Tech" if offset < 3 else "Finance"] * len(index),
        }, index=index)

    result = FactorResearchEngine().run(
        frames=frames,
        factor_id="momentum_20",
        start_date=index[0],
        end_date=index[-1],
        groups=3,
        holding_period=5,
        commission=0.0005,
        slippage=0.0005,
        neutralize_industry=True,
    )

    assert result["icSeries"]
    assert len(result["groupCurves"]) == 3
    assert result["coverage"] > 0
    assert result["missingRate"] < 1
    assert result["neutralized"] is True
    assert result["factorCorrelation"]["factors"]
    assert "rankAutocorrelation" in result["stability"]


def test_factor_research_rejects_empty_cross_sectional_observations():
    index = pd.date_range("2025-01-01", periods=30, freq="B")
    frames = {
        f"USStock:{symbol}": pd.DataFrame({
            "open": [100.0] * len(index),
            "close": [100.0] * len(index),
        }, index=index)
        for symbol in ["A", "B", "C"]
    }

    with pytest.raises(ValueError, match="factorResearchInsufficientObservations"):
        FactorResearchEngine().run(
            frames=frames,
            factor_id="quality",
            start_date=index[0],
            end_date=index[-1],
            groups=3,
        )


def test_factor_research_preserves_warmup_before_selected_start():
    index = pd.date_range("2025-01-01", periods=70, freq="B")
    frames = {}
    for offset, symbol in enumerate(["A", "B", "C", "D", "E", "F"]):
        prices = [100 + offset + day * (0.1 + offset * 0.03) for day in range(len(index))]
        frames[symbol] = pd.DataFrame({"open": prices, "close": prices}, index=index)

    result = FactorResearchEngine().run(
        frames=frames,
        factor_id="momentum_20",
        start_date=index[25],
        end_date=index[-1],
        groups=3,
        holding_period=5,
    )

    assert pd.Timestamp(result["icSeries"][0]["time"]) == index[25]


def test_factor_research_builds_talib_081_panel_with_selected_parameters():
    pytest.importorskip("talib")
    index = pd.date_range("2025-01-01", periods=80, freq="B")
    frames = {}
    for offset, symbol in enumerate(["A", "B", "C"]):
        close = pd.Series(
            [100 + offset * 5 + day * 0.2 + ((day + offset * 3) % 11) for day in range(len(index))],
            index=index,
            dtype=float,
        )
        frames[symbol] = pd.DataFrame({
            "open": close - 0.2,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1000 + close * (offset + 1),
        }, index=index)
    close_panel = FactorResearchEngine._panel(frames, "close")

    panel = FactorResearchEngine()._factor_panel(
        frames,
        "talib:percentrank",
        close_panel,
        {"timeperiod": 15},
    )

    assert panel.shape == close_panel.shape
    assert panel.iloc[-1].notna().all()
    assert FactorResearchEngine.warmup_bars("talib:PERCENTRANK", {"timeperiod": 150}) >= 150


def test_factor_research_builds_registered_factor_panel_with_selected_parameters():
    index = pd.date_range("2025-01-01", periods=60, freq="B")
    frames = {}
    for offset, symbol in enumerate(["A", "B", "C"]):
        close = pd.Series(
            [100 + offset * 4 + day * 0.15 + ((day + offset) % 7) for day in range(len(index))],
            index=index,
            dtype=float,
        )
        frames[symbol] = pd.DataFrame({
            "open": close - 0.2,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1000 + close * (offset + 1),
        }, index=index)
    close_panel = FactorResearchEngine._panel(frames, "close")
    timestamp = index[40]

    panel = FactorResearchEngine()._factor_panel(
        frames,
        "rsi",
        close_panel,
        {"period": 7},
        [timestamp],
    )

    assert panel.loc[timestamp].notna().all()
    for symbol, frame in frames.items():
        expected = compute_factor("rsi", frame.loc[:timestamp], {"period": 7})
        assert panel.at[timestamp, symbol] == pytest.approx(expected)
    assert panel.drop(index=timestamp).isna().all().all()


def test_registered_factor_research_is_point_in_time_and_uses_canonical_fundamentals():
    index = pd.date_range("2025-01-01", periods=50, freq="B")
    timestamp = index[30]
    clean = {}
    changed = {}
    for offset, symbol in enumerate(["A", "B", "C"]):
        prices = [100.0 + offset * 3 + day * 0.2 + ((day + offset) % 5) for day in range(len(index))]
        altered = list(prices)
        for day in range(31, len(index)):
            altered[day] *= 2.0 if offset % 2 == 0 else 0.5
        clean[symbol] = pd.DataFrame({"open": prices, "close": prices}, index=index)
        changed[symbol] = pd.DataFrame({"open": altered, "close": altered}, index=index)

    engine = FactorResearchEngine()
    clean_panel = engine._factor_panel(
        clean,
        "skewness",
        engine._panel(clean, "close"),
        {"period": 10},
        [timestamp],
    )
    changed_panel = engine._factor_panel(
        changed,
        "skewness",
        engine._panel(changed, "close"),
        {"period": 10},
        [timestamp],
    )

    pd.testing.assert_series_equal(clean_panel.loc[timestamp], changed_panel.loc[timestamp])
    assert FactorResearchEngine.required_fields("book_to_price") == {
        "shareholder_equity",
        "market_cap",
    }
    assert FactorResearchEngine.required_fields("free_cash_flow_yield") == {
        "free_cash_flow",
        "market_cap",
    }


def test_factor_research_supports_every_registered_technical_factor():
    index = pd.date_range("2025-01-01", periods=260, freq="B")
    step = pd.Series(range(len(index)), index=index, dtype=float)
    frames = {}
    for offset, symbol in enumerate(["A", "B", "C"]):
        close = 100.0 + step * (0.15 + offset * 0.01) + ((step + offset) % 11)
        frames[symbol] = pd.DataFrame({
            "open": close - 0.3,
            "high": close + 1.2,
            "low": close - 1.1,
            "close": close,
            "volume": 1000.0 + step * 3.0 + offset * 100.0,
        }, index=index)
    engine = FactorResearchEngine()
    close_panel = engine._panel(frames, "close")
    timestamp = index[-1]

    for definition in list_factors(factor_type="technical"):
        panel = engine._factor_panel(
            frames,
            definition["factor_id"],
            close_panel,
            definition["default_params"],
            [timestamp],
        )
        assert panel.loc[timestamp].notna().all(), definition["factor_id"]


def test_factor_research_applies_membership_intervals_on_each_research_date():
    index = pd.date_range("2025-01-01", periods=70, freq="B")
    symbols = ["A", "B", "C", "D", "E", "F"]
    frames = {}
    members = []
    for offset, symbol in enumerate(symbols):
        prices = [100 + offset + day * (0.1 + offset * 0.02) for day in range(len(index))]
        frames[symbol] = pd.DataFrame({"open": prices, "close": prices}, index=index)
        members.append({
            "key": symbol,
            "valid_from": index[0] if symbol in {"A", "B", "C"} else index[35],
            "valid_to": None,
        })

    result = FactorResearchEngine().run(
        frames=frames,
        factor_id="momentum_20",
        start_date=index[25],
        end_date=index[-1],
        groups=3,
        holding_period=5,
        members=members,
    )

    first_time = result["icSeries"][0]["time"]
    first_members = {
        symbol
        for row in result["groupObservations"]
        if row["time"] == first_time
        for symbol in row["members"]
    }
    assert first_members == {"A", "B", "C"}
    assert result["pointInTimeUniverseApplied"] is True


def test_factor_research_preserves_membership_gaps_and_reentry():
    index = pd.date_range("2025-01-01", periods=6, freq="D")

    panel = FactorResearchEngine._membership_panel(
        [{
            "key": "A",
            "membership_periods": [
                {"valid_from": index[0], "valid_to": index[2]},
                {"valid_from": index[4], "valid_to": None},
            ],
        }],
        index,
        pd.Index(["A", "B"]),
    )

    assert panel["A"].tolist() == [True, True, False, False, True, True]
    assert panel["B"].tolist() == [False] * len(index)


def test_factor_research_long_short_net_return_deducts_both_legs_costs():
    long_values = [{
        "time": "2025-01-01",
        "grossReturn": 0.10,
        "netReturn": 0.09,
        "cost": 0.01,
    }]
    short_values = [{
        "time": "2025-01-01",
        "grossReturn": -0.05,
        "netReturn": -0.07,
        "cost": 0.02,
    }]

    points = FactorResearchEngine._long_short_curve(long_values, short_values)

    assert points[0]["gross"] == pytest.approx(1.15)
    assert points[0]["net"] == pytest.approx(1.12)


def test_factor_research_reduces_groups_for_small_cross_sections():
    index = pd.date_range("2025-01-01", periods=70, freq="B")
    frames = {}
    for offset, symbol in enumerate(["A", "B", "C", "D", "E", "F", "G"]):
        prices = [100 + offset + day * (0.1 + offset * 0.02) for day in range(len(index))]
        frames[symbol] = pd.DataFrame({"open": prices, "close": prices}, index=index)

    result = FactorResearchEngine().run(
        frames=frames,
        factor_id="momentum_20",
        start_date=index[25],
        end_date=index[-1],
        groups=5,
        holding_period=5,
        annualization_periods=365.25,
    )

    assert result["effectiveGroups"] == 3
    assert result["requestedGroups"] == 5
    assert result["methodologyVersion"] == 2
    assert "groupsReduced" in result["sampleDiagnostics"]["warnings"]
    assert result["executionAssumptions"]["periodsPerYear"] == pytest.approx(365.25)


def test_factor_research_sizes_groups_from_usable_factor_cross_section():
    index = pd.date_range("2025-01-01", periods=40, freq="B")
    frames = {}
    for offset, symbol in enumerate("ABCDEFGHIJ"):
        prices = [100 + offset + day * (0.1 + offset * 0.02) for day in range(len(index))]
        frame = pd.DataFrame({"open": prices, "close": prices}, index=index)
        if offset < 4:
            frame["pe_ratio"] = 10.0 + offset
        frames[symbol] = frame

    result = FactorResearchEngine().run(
        frames=frames,
        factor_id="value",
        start_date=index[0],
        end_date=index[-1],
        groups=5,
        holding_period=5,
    )

    assert result["effectiveGroups"] == 2
    assert result["sampleDiagnostics"]["medianCrossSectionSize"] == 4


def test_factor_research_warmup_bars_handles_shorthand_and_custom_multistage_factors():
    index = pd.date_range("2025-01-01", periods=200, freq="B")
    step = pd.Series(range(len(index)), index=index, dtype=float)
    frames = {}
    for offset, symbol in enumerate(["A", "B", "C"]):
        close = 100.0 + step * (0.15 + offset * 0.01) + ((step + offset) % 11)
        frames[symbol] = pd.DataFrame({
            "open": close - 0.3,
            "high": close + 1.2,
            "low": close - 1.1,
            "close": close,
            "volume": 1000.0 + step * 3.0 + offset * 100.0,
        }, index=index)
    engine = FactorResearchEngine()

    cases = [
        ("momentum_20", None, 21),
        ("momentum_60", None, 61),
        ("volatility_45", None, 46),
        ("reversal", None, 6),
        ("reversal_5", None, 6),
        ("tema", {"period": 50}, 150),
        ("trix", {"period": 30}, 91),
        ("dema", {"period": 50}, 100),
        ("adx", {"period": 30}, 60),
        ("zlema", {"period": 40}, 59),
        ("hma", {"period": 100}, 110),
        ("kama", {"period": 20}, 21),
        ("ema_slope", {"slope_period": 15}, 35),
        ("macd", {"signal_period": 20}, 46),
        ("stochastic", {"smooth_k": 10, "smooth_d": 10}, 32),
        ("tsi", {"fast_period": 20}, 46),
        ("keltner_channels", {"atr_period": 30}, 30),
    ]
    for factor_id, params, expected_warmup in cases:
        warmup = FactorResearchEngine.warmup_bars(factor_id, params)
        assert warmup == expected_warmup, factor_id
        sliced_frames = {symbol: frame.iloc[:warmup] for symbol, frame in frames.items()}
        sliced_close = engine._panel(sliced_frames, "close")
        timestamp = sliced_close.index[-1]
        panel = engine._factor_panel(
            sliced_frames,
            factor_id,
            sliced_close,
            params,
            [timestamp],
        )
        assert panel.loc[timestamp].notna().all(), factor_id
