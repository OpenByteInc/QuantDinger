from app.utils.pnl import (
    calc_margin_notional,
    calc_notional_value,
    calc_pnl_percent,
    calc_unrealized_pnl,
)


def test_swap_pnl_percent_uses_margin_denominator():
    # 10x, +1% price move on notional 1000 -> margin ROI ~10%
    pnl = calc_unrealized_pnl("long", 100.0, 101.0, 10.0)  # notional move 1%
    assert round(pnl, 6) == 10.0
    pct = calc_pnl_percent(100.0, 10.0, pnl, leverage=10.0, market_type="swap")
    assert round(pct, 4) == 10.0


def test_spot_pnl_percent_is_price_change():
    pnl = calc_unrealized_pnl("long", 100.0, 101.0, 10.0)
    pct = calc_pnl_percent(100.0, 10.0, pnl, leverage=1.0, market_type="spot")
    assert round(pct, 4) == 1.0


def test_margin_notional_for_swap():
    assert calc_margin_notional(1000.0, 10.0, "swap") == 100.0
    assert calc_margin_notional(1000.0, 10.0, "spot") == 1000.0


def test_notional_value():
    assert calc_notional_value(50.0, 2.0) == 100.0
