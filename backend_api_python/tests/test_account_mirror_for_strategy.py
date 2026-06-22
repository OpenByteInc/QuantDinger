"""Tests for live account mirror used by trading-robot positions API."""

from unittest.mock import patch

from app.services.live_trading.account_positions import (
    live_account_mirror_for_strategy,
    snapshot_rows_to_account_legs,
)


def test_snapshot_rows_to_account_legs_normalizes_market_type():
    rows = snapshot_rows_to_account_legs(
        [
            {
                "symbol": "ETH/USDT",
                "side": "long",
                "size": 0.0121,
                "entry_price": 2055.12,
                "market_type": "spot",
                "inst_id": "ETH-USDT",
            }
        ]
    )
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ETH/USDT"
    assert rows[0]["market_type"] == "spot"


@patch("app.services.live_trading.account_snapshot.fetch_account_snapshot")
@patch("app.services.live_trading.leg_context.resolve_leg_context")
def test_live_account_mirror_includes_spot_and_swap(mock_ctx, mock_snap):
    from app.services.live_trading.leg_context import LegContext

    mock_ctx.return_value = LegContext(market_type="swap", credential_id=7)
    mock_snap.return_value = {
        "swap_positions": [],
        "spot_positions": [
            {
                "symbol": "ETH/USDT",
                "side": "long",
                "size": 0.0121,
                "entry_price": 2055.0,
                "market_type": "spot",
                "inst_id": "ETH-USDT",
            },
            {
                "symbol": "USDT",
                "side": "long",
                "size": 363.21,
                "entry_price": 0.0,
                "market_type": "spot",
                "inst_id": "USDT",
            },
        ],
        "fetched_at": 1717200000,
        "warnings": [],
    }

    out = live_account_mirror_for_strategy(
        strategy_id=1,
        user_id=2,
        strategy_market_type="swap",
        allowed_symbols={"ETH/USDT"},
    )

    assert out["source"] == "live_snapshot"
    assert len(out["spot_legs"]) == 2
    assert len(out["swap_legs"]) == 0
    assert len(out["account_legs"]) == 2
    assert len(out["reconcile_legs"]) == 0
    assert out["fetched_at"] == 1717200000
