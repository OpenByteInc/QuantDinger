import pandas as pd
import pytest

from app.services.strategy_v2.factor_research import FactorResearchEngine


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
