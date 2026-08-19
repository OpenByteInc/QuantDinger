"""Regression coverage for exchange step edges and generated numeric literals."""

from __future__ import annotations

import ast
import re
import time
from decimal import Decimal

import pytest

from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.gate import GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.okx import OkxClient
from app.services.pending_order_worker import PendingOrderWorker
from app.services.strategy_runtime.robot_v2 import build_robot_v2_source
from app.utils.numeric_precision import (
    clean_generated_number,
    floor_decimal_to_step,
    format_decimal,
)


@pytest.mark.parametrize(
    "normalizer",
    [
        BinanceFuturesClient._floor_to_step,
        BinanceSpotClient._floor_to_step,
        BybitClient._floor_to_step,
        OkxClient._floor_to_step,
        BitgetMixClient._floor_to_step,
        BitgetSpotClient._floor_to_step,
    ],
)
def test_exchange_step_normalizers_snap_float_noise_at_exact_boundary(normalizer):
    noisy_minimum = Decimal("0.00009999999999999994")

    assert normalizer(noisy_minimum, Decimal("0.0001")) == Decimal("0.0001")
    assert normalizer(Decimal("0.0000999"), Decimal("0.0001")) == Decimal("0")


def test_integer_contract_normalizers_snap_float_noise_at_exact_boundary():
    noisy_contract = Decimal("0.9999999999999994")

    assert GateUsdtFuturesClient._floor(noisy_contract) == Decimal("1")
    assert HtxClient._floor_to_int(noisy_contract) == 1


@pytest.mark.parametrize(
    "normalizer",
    [
        BinanceFuturesClient._floor_to_precision,
        BinanceSpotClient._floor_to_precision,
    ],
)
def test_binance_precision_fallback_snaps_float_noise_at_boundary(normalizer):
    assert normalizer(Decimal("0.009999999999999994"), 2) == Decimal("0.01")
    assert normalizer(Decimal("0.00999"), 2) == Decimal("0")


def test_okx_minimum_swap_close_survives_base_to_contract_conversion():
    client = OkxClient(api_key="k", secret_key="s", passphrase="p")
    client._inst_cache["SWAP:BTC-USDT-SWAP"] = (
        time.time(),
        {
            "instId": "BTC-USDT-SWAP",
            "ctVal": "0.01",
            "lotSz": "0.01",
            "minSz": "0.01",
        },
    )

    normalized, precision = client._normalize_order_size(
        inst_id="BTC-USDT-SWAP",
        market_type="swap",
        size=9.999999999999994e-05,
    )

    assert normalized == Decimal("0.01")
    assert precision == 2
    assert client._dec_str(normalized, strict_precision=precision) == "0.01"


def test_precision_helpers_keep_user_facing_values_short():
    assert floor_decimal_to_step("9.999999999999994e-05", "0.0001") == Decimal("0.0001")
    assert clean_generated_number(1 / 3) == 0.333333333333
    assert format_decimal(9.999999999999994e-05) == "0.0001"
    assert format_decimal(1.0) == "1"


def test_close_size_error_does_not_recommend_increasing_leverage():
    worker = object.__new__(PendingOrderWorker)
    message = worker._friendly_order_error(
        "Invalid size (below lot/min size): requested=0.0001",
        client=object(),
        exchange_id="okx",
        symbol="BTC/USDT",
        signal_type="close_long",
        amount=0.0001,
        price=63_500,
        payload={
            "sizing": {
                "initial_capital": 1_000,
                "entry_pct": 0.635,
                "leverage": 1,
                "source": "strategy_v2",
            }
        },
    )

    assert "remaining strategy position" in message
    assert "reconcile the residual position" in message
    assert "Increase capital" not in message


def test_grid_template_limits_fractional_literals_and_preserves_budget():
    source = build_robot_v2_source(
        "grid",
        {
            "side": "long",
            "dynamic_anchor": True,
            "start_price": 0.98,
            "end_price": 1.02,
            "grid_count": 7,
            "grid_mode": "arithmetic",
            "initial_position_pct": 1 / 3,
            "max_open_orders": 7,
        },
        {"levels": []},
        symbol="BTC/USDT",
        market_type="swap",
        timeframe="1m",
    )
    constants = source.split("def initialize", 1)[0]

    assert "0.3333333333333333" not in constants
    assert all(
        len(fraction) <= 12
        for fraction in re.findall(r"(?<![\w.])\d+\.(\d+)", constants)
    )
    budget_line = next(
        line for line in constants.splitlines() if line.startswith("CELL_BUDGET_PCTS =")
    )
    budget_values = ast.literal_eval(budget_line.split("=", 1)[1].strip())
    assert sum(budget_values) == pytest.approx(1.0, abs=1e-12)
