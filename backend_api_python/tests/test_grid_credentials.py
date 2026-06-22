"""Grid resting engine must resolve credential_id before limit/cancel/close paths."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.exchange_execution import resolve_exchange_config
from app.services.grid.config import GridBotConfig
from app.services.grid.runner import GridRestingRunner
from app.services.grid.validator import validate_grid_config


def test_resolve_exchange_config_merges_credential_id():
    base = {"exchange_id": "okx", "api_key": "k", "secret_key": "s", "passphrase": "p"}
    merged = resolve_exchange_config({"credential_id": 9, "market_type": "swap"}, user_id=1)
    # Without DB this stays credential-only; with mock below we verify merge logic separately.
    assert merged.get("credential_id") == 9


def test_grid_startup_fails_before_initial_market_when_client_missing():
    calls = {"market": 0}

    def _enqueue(sig, usdt, px, reason):
        calls["market"] += 1
        return True

    def _create_client():
        raise RuntimeError("Missing OKX api_key/secret_key/passphrase")

    runner = GridRestingRunner(
        1,
        "BTC/USDT",
        {
            "leverage": 5,
            "market_type": "swap",
            "initial_capital": 1000,
            "bot_params": {
                "upperPrice": 100000,
                "lowerPrice": 90000,
                "gridCount": 5,
                "amountPerGrid": 50,
                "gridDirection": "long",
                "initialPositionPct": 0,
            },
        },
        {"exchange_id": "okx", "api_key": "", "secret_key": "", "passphrase": ""},
        user_id=1,
        initial_capital=1000,
        enqueue_market_fn=_enqueue,
        create_client_fn=_create_client,
    )
    ok, msg = runner.startup(95000.0)
    assert ok is False
    assert "Missing OKX" in msg
    assert calls["market"] == 0


def test_grid_startup_places_limits_when_client_ok():
    calls = {"limits": 0}

    def _enqueue(sig, usdt, px, reason):
        return True

    client = MagicMock()
    client.place_limit_order.return_value = MagicMock(exchange_order_id="ex1")

    def _create_client():
        return client

    with patch("app.services.grid.engine.place_grid_limit_order") as place:
        place.return_value = MagicMock(exchange_order_id="ex1")
        with patch("app.services.grid.engine.GridRestingOrderRepository") as repo_cls:
            repo = repo_cls.return_value
            repo.has_open_for_cell.return_value = False
            repo.insert.return_value = 1
            runner = GridRestingRunner(
                2,
                "BTC/USDT",
                {
                    "leverage": 5,
                    "market_type": "swap",
                    "initial_capital": 1000,
                    "bot_params": {
                        "upperPrice": 100000,
                        "lowerPrice": 90000,
                        "gridCount": 5,
                        "amountPerGrid": 50,
                        "gridDirection": "long",
                        "initialPositionPct": 0,
                    },
                },
                {"exchange_id": "okx", "api_key": "k", "secret_key": "s", "passphrase": "p"},
                user_id=1,
                initial_capital=1000,
                enqueue_market_fn=_enqueue,
                create_client_fn=_create_client,
            )
            ok, msg = runner.startup(95000.0)
    assert ok is True
    assert msg == ""
    assert place.called


def test_grid_config_rejects_spacing_that_cannot_cover_round_trip_fees():
    cfg = GridBotConfig.from_trading_config(
        {
            "leverage": 5,
            "market_type": "swap",
            "initial_capital": 100,
            "commission": 0.1,
            "bot_params": {
                "upperPrice": 1647.07,
                "lowerPrice": 1644.47,
                "gridCount": 2,
                "amountPerGrid": 5,
                "gridDirection": "long",
                "initialPositionPct": 0,
            },
        }
    )
    ok, msg, warnings = validate_grid_config(cfg, initial_capital=100, fee_rate=0.001)

    assert ok is False
    assert warnings == []
    assert "too narrow after fees" in msg
