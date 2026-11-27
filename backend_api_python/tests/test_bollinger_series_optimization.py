import numpy as np
import pandas as pd
import pytest

from app.services.factors import compute_factor, FactorError
from app.services.factors.registry import compute_bollinger_series
from app.services.strategy_v2.data import MultiAssetDataPortal
from app.services.strategy_v2.runtime import PortfolioState, StrategyRuntimeContext


@pytest.mark.parametrize("output", ["upper", "middle", "lower", "bandwidth", "position"])
@pytest.mark.parametrize("kind", ["random", "constant", "invalid", "zero"])
def test_vector_series_matches_every_causal_prefix(output, kind):
    values = np.random.default_rng(12).normal(100, 10, 120)
    if kind == "constant":
        values[:] = 100
    elif kind == "zero":
        values[:] = 0
    elif kind == "invalid":
        values[::7] = np.nan
        values[::13] = np.inf
    frame = pd.DataFrame({"close": values}, index=pd.date_range("2026-01-01", periods=120))
    params = dict(period=20, stddev=2, output=output)
    expected = []
    for end in range(1, len(frame) + 1):
        try:
            expected.append(compute_factor("bollinger_bands", frame.iloc[:end], params))
        except FactorError as exc:
            assert exc.code == "factor.insufficientHistory"
            expected.append(float("nan"))
    actual = compute_bollinger_series(frame, params)
    np.testing.assert_allclose(actual, expected, rtol=1e-10, atol=1e-10, equal_nan=True)
    pd.testing.assert_series_equal(compute_bollinger_series(frame.iloc[:70], params), actual.iloc[:70])


def test_runtime_indicator_uses_only_visible_prices(monkeypatch):
    import app.services.strategy_v2.runtime as runtime
    frame = pd.DataFrame({name: np.arange(100., 200.) for name in ("open", "high", "low", "close")},
                         index=pd.date_range("2026-01-01", periods=100))
    portal = MultiAssetDataPortal({"USStock:AAPL": frame})
    portal.set_clock(frame.index[49], include_current=True)
    context = StrategyRuntimeContext(portal=portal, portfolio=PortfolioState(1000, 1000))
    monkeypatch.setattr(runtime, "compute_factor", lambda *a, **kw: pytest.fail("quadratic prefix scan"))
    result = context.indicator("bollinger_bands", "USStock:AAPL", period=20, output="lower")
    assert len(result) == 50
    assert result.iloc[-1] == pytest.approx(compute_factor("bollinger_bands", frame.iloc[:50], dict(period=20, output="lower")))


def test_empty_indicator_keeps_existing_series_contract():
    frame = pd.DataFrame({name: [100.] for name in ("open", "high", "low", "close")},
                         index=pd.to_datetime(["2026-01-02"]))
    portal = MultiAssetDataPortal({"USStock:AAPL": frame})
    portal.set_clock(pd.Timestamp("2026-01-01"), include_current=True)
    context = StrategyRuntimeContext(portal=portal, portfolio=PortfolioState(1000, 1000))
    result = context.indicator("bollinger_bands", "USStock:AAPL")
    assert isinstance(result, pd.Series)
    assert result.empty
    assert result.name == "bollinger_bands"
