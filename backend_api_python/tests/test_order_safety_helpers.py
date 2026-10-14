from decimal import Decimal

import pytest

from app.services.pending_orders.error_classification import (
    classify_exchange_order_error,
    classify_strategy_exchange_log,
)
from app.services.pending_orders.order_budget import strategy_order_budget_snapshot
from app.services.pending_orders.order_quantities import (
    exchange_executable_base_quantity,
    reconciled_queue_status,
)
from app.services.strategy_runtime.bot_type import resolve_bot_type
from app.services.trading_executor import TradingExecutor


def test_spot_quote_amount_misread_as_base_quantity_is_blocked():
    snapshot = strategy_order_budget_snapshot(
        action="add_long",
        quantity=36.1659,
        price=63_000,
        initial_capital=1_000,
        leverage=1,
        market_type="spot",
        current_positions=(),
    )
    assert snapshot["allowed"] is False
    assert snapshot["reason"] == "strategy_budget_exceeded"
    assert snapshot["order_notional"] > 2_000_000


def test_reduce_order_is_never_blocked_by_entry_budget():
    snapshot = strategy_order_budget_snapshot(
        action="reduce_long",
        quantity=36,
        price=63_000,
        initial_capital=1_000,
        leverage=1,
        market_type="spot",
    )
    assert snapshot["allowed"] is True


class _OkxClient:
    def _normalize_order_size(self, **_kwargs):
        return Decimal("149"), 0

    def get_instrument(self, **_kwargs):
        return {"ctVal": "0.0001"}


class _GateClient:
    def _resolve_order_size(self, **_kwargs):
        return "74", None

    def contracts_signed_to_base_qty(self, **kwargs):
        return float(kwargs["contracts_signed"]) * 0.0001


@pytest.mark.parametrize(
    ("exchange_id", "client", "requested", "filled"),
    [
        ("okx", _OkxClient(), 0.014967300387, 0.0149),
        ("gate", _GateClient(), 0.007485690512, 0.0074),
    ],
)
def test_exchange_precision_quantity_is_terminal_after_full_executable_fill(
    exchange_id, client, requested, filled
):
    executable = exchange_executable_base_quantity(
        client,
        exchange_id=exchange_id,
        symbol="BTC/USDT",
        market_type="swap",
        requested=requested,
        exchange_config={},
    )
    status, reconciled = reconciled_queue_status(
        client,
        exchange_id=exchange_id,
        symbol="BTC/USDT",
        market_type="swap",
        requested=requested,
        filled=filled,
        avg_price=63_000,
        exchange_status="open",
        exchange_config={},
    )
    assert executable == pytest.approx(filled)
    assert reconciled == pytest.approx(filled)
    assert status == "filled"


def test_http_502_is_not_classified_as_order_size():
    result = classify_exchange_order_error("Binance HTTP 502: 502 Bad Gateway")
    assert result["category"] == "transport"
    assert result["retryable"] is True


@pytest.mark.parametrize(
    "error",
    [
        "OKX error: {'sCode': '51138'}",
        'Bybit error: {"retCode": 110121}',
        "Bitget error 25205: trading price cannot be below 5%",
        "Gate error PRICE_TOO_DEVIATED",
    ],
)
def test_dynamic_price_band_error_has_retryable_category(error):
    result = classify_exchange_order_error(error)
    assert result["category"] == "price_band"
    assert result["retryable"] is True


@pytest.mark.parametrize(
    ("message", "category", "exchange"),
    [
        (
            'Auto-stopped (position_sync_binance): Binance HTTP 401: {"code":-2015,"msg":"Invalid API-key, IP, or permissions for action"}',
            "credentials",
            "binance",
        ),
        (
            'Exchange order failed (gate BTC/USDT close_short): Gate HTTP 400: {"label":"MARKET_PRICE_TOO_DEVIATED","message":"price deviates too much"}',
            "price_band",
            "gate",
        ),
        (
            'Exchange order failed (gate BTC/USDT open_short): Gate HTTP 400: {"label":"INSUFFICIENT_AVAILABLE","message":"margin 950 while available 800"}',
            "insufficient_funds",
            "gate",
        ),
        (
            'Exchange order failed (bybit BTC/USDT open_long): Bybit error: {"retCode":10006,"retMsg":"Too many visits"}',
            "rate_limit",
            "bybit",
        ),
        (
            'Exchange order failed (okx BTC/USDT open_long): OKX error: {"sCode":"50102","sMsg":"Timestamp request expired"}',
            "clock_skew",
            "okx",
        ),
    ],
)
def test_strategy_exchange_logs_have_stable_user_facing_categories(message, category, exchange):
    result = classify_strategy_exchange_log(message)
    assert result is not None
    assert result["category"] == category
    assert result["exchange"] == exchange
    assert result["technical_detail"] == message


def test_non_exchange_strategy_log_is_not_misclassified():
    assert classify_strategy_exchange_log("Strategy runtime scheduled") is None


def test_binance_max_leverage_error_is_risk_limit_not_balance_shortfall():
    result = classify_exchange_order_error(
        'Binance HTTP 400: {"code":-2027,"msg":"Exceeded the maximum allowable position at current leverage."}'
    )
    assert result["category"] == "risk_limit"


def test_legacy_executor_type_routes_to_grid_engine():
    assert resolve_bot_type({"trading_config": {"executor_type": "grid"}}) == "grid"
    assert resolve_bot_type({"template_key": "robot_v2_layered_martingale"}) == "layered_martingale"


def test_current_manifest_metadata_routes_to_grid_engine():
    assert resolve_bot_type({
        "trading_config": {
            "strategy_manifest": {
                "metadata": {"strategy_family": "grid", "executor_type": "grid"},
            },
        },
    }) == "grid"


def test_manifest_trend_contract_overrides_stale_grid_runtime_metadata():
    assert resolve_bot_type({
        "trading_config": {
            "bot_type": "grid",
            "executor_type": "grid",
            "bot_params": {
                "gridCount": 20,
                "lowerPrice": 0.98,
                "upperPrice": 1.02,
            },
            "strategy_manifest": {
                "metadata": {"strategy_family": "trend"},
            },
        },
    }) == "trend"


def test_legacy_grid_bot_params_route_to_resting_grid_engine():
    assert resolve_bot_type({
        "trading_config": {
            "bot_params": {
                "gridCount": 100,
                "lowerPrice": 0.63,
                "upperPrice": 1.36,
            },
        },
    }) == "grid"


def test_generated_grid_source_recovers_missing_runtime_metadata():
    source = """
GRID_TEMPLATE_VERSION = 6
CELL_LOWER = [0.9]
CELL_UPPER = [1.0]
CELL_ROLES = ['long_entry']
MAX_OPEN_ENTRY_ORDERS = 1
"""

    assert resolve_bot_type({}, {}, source_code=source) == "grid"


@pytest.mark.parametrize(
    ("expected", "source"),
    [
        (
            "dca",
            "DCA_TEMPLATE_VERSION\nDCA_INTERVAL_MINUTES\nDCA_MAX_ORDERS\n"
            "DCA_TOTAL_BUDGET_PCT\ndef _reconcile_purchase():\n    pass\n",
        ),
        (
            "martingale",
            '"""Strategy API V2 martingale robot generated from the visual builder."""\n'
            "ROBOT_TEMPLATE_VERSION = 6\nENTRY_TRIGGER_MODE = 'realtime_price'\n"
            "PRICE_LEVELS = [1]\ndef on_price_tick(context, prices):\n    pass\n",
        ),
        (
            "layered_martingale",
            '"""Strategy API V2 layered martingale robot generated from the visual builder."""\n'
            "ROBOT_TEMPLATE_VERSION = 6\nENTRY_TRIGGER_MODE = 'realtime_price'\n"
            "PRICE_LEVELS = [1]\ndef on_price_tick(context, prices):\n    pass\n",
        ),
    ],
)
def test_generated_non_grid_robot_source_recovers_missing_runtime_metadata(expected, source):
    assert resolve_bot_type({}, {}, source_code=source) == expected


def test_generated_grid_constants_recover_missing_deployed_bot_params():
    recovered = TradingExecutor._recover_generated_grid_config(
        {"leverage": 3},
        {
            "CELL_LOWER": [0.90, 0.95],
            "CELL_UPPER": [0.95, 1.00],
            "GRID_SIDE": "long",
            "DYNAMIC_ANCHOR": True,
            "INITIAL_POSITION_PCT": 0.55,
            "MAX_OPEN_ENTRY_ORDERS": 2,
            "EQUITY_TAKE_PROFIT": 0.2,
        },
    )

    assert recovered["bot_type"] == "grid"
    assert recovered["executor_type"] == "grid"
    assert recovered["bot_params"] == {
        "lowerPrice": 0.90,
        "upperPrice": 1.00,
        "gridCount": 2,
        "gridCountUnit": "cells",
        "amountPerGridPct": pytest.approx(0.5),
        "gridMode": "arithmetic",
        "gridDirection": "long",
        "initialPositionPct": pytest.approx(0.55),
        "orderMode": "maker",
        "boundaryAction": "pause",
        "maxOpenOrders": 2,
        "dynamicAnchor": True,
    }
    assert recovered["equity_take_profit_pct"] == pytest.approx(0.2)


def test_v7_grid_source_overrides_stale_editor_runtime_params():
    recovered = TradingExecutor._recover_generated_grid_config(
        {
            "bot_type": "grid",
            "bot_params": {
                "lowerPrice": 0.90,
                "upperPrice": 1.10,
                "gridCount": 120,
                "gridCountUnit": "cells",
                "gridMode": "arithmetic",
                "dynamicAnchor": True,
            },
            "equity_take_profit_pct": 0.10,
        },
        {
            "GRID_TEMPLATE_VERSION": 7,
            "CELL_LOWER": [2 / 3, 0.80],
            "CELL_UPPER": [0.80, 4 / 3],
            "GRID_SIDE": "long",
            "DYNAMIC_ANCHOR": True,
            "INITIAL_POSITION_PCT": 0.60,
            "MAX_OPEN_ENTRY_ORDERS": 2,
            "CELL_BUDGET_PCTS": [0.4, 0.6],
            "CELL_ROLES": ["long_entry", "long_seed"],
            "EQUITY_TAKE_PROFIT": 0.30,
        },
    )

    assert recovered["bot_params"]["lowerPrice"] == pytest.approx(2 / 3)
    assert recovered["bot_params"]["upperPrice"] == pytest.approx(4 / 3)
    assert recovered["bot_params"]["gridCount"] == 2
    assert recovered["bot_params"]["gridMode"] == "geometric"
    assert recovered["bot_params"]["initialPositionPct"] == pytest.approx(0.60)
    assert recovered["bot_params"]["maxOpenOrders"] == 2
    assert recovered["bot_params"]["cellBudgetPcts"] == pytest.approx([0.4, 0.6])
    assert recovered["bot_params"]["cellRoles"] == ["long_entry", "long_seed"]
    assert recovered["bot_params"]["adaptiveBounds"] is False
    assert recovered["equity_take_profit_pct"] == pytest.approx(0.30)
