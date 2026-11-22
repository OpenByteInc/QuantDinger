"""
Grid resting-order fill sync — exchange contract tests (no API keys required).

Run after any change to query_grid_order_fill / poller / pending worker:

    cd backend_api_python
    python -m pytest tests/test_grid_exchange_fill_contracts.py -v

These tests mock each exchange client's get_order() with realistic JSON shapes
(documented REST fields). They verify:
  1) correct API call signature per exchange (e.g. OKX inst_id, Gate order_id-only)
  2) filled / partial / open / cancelled status normalization

Optional live smoke (your own testnet keys): see test_grid_exchange_fill_live.py
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple, Type
from unittest.mock import MagicMock

import pytest

from app.services.grid.exchange_orders import query_grid_order_fill
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.okx import OkxClient


@pytest.mark.parametrize("client_class,market", [(GateSpotClient, "spot"), (GateUsdtFuturesClient, "swap")])
def test_gate_unconfirmed_submit_queries_original_text(client_class, market):
    client = client_class.__new__(client_class)
    client.get_order = MagicMock(return_value={"id": "12345", "status": "open", "size": 1, "left": 1})
    client.get_contract = MagicMock(return_value={"quanto_multiplier": "0.001"})
    details = {}
    _, _, status = query_grid_order_fill(client, symbol="BTC/USDT", market_type=market,
        client_order_id="g0001c001e123456789012", details=details)
    assert status == "open"
    assert client.get_order.call_args.kwargs["order_id"] == "t-g0001c001e123456789012"
    assert details["exchange_order_id"] == "12345"


@dataclass(frozen=True)
class FillContractCase:
    case_id: str
    client_cls: Type
    response: Dict[str, Any]
    expected: Tuple[float, float, str]
    market_type: str = "swap"
    exchange_config: Optional[Dict[str, Any]] = None
    call_assert: Optional[Callable[[Dict[str, Any]], None]] = None


def _okx_call_assert(kw: Dict[str, Any]) -> None:
    assert kw.get("inst_id") == "BTC-USDT-SWAP"
    assert kw.get("ord_id") == "oid-1"


def _gate_call_assert(kw: Dict[str, Any]) -> None:
    assert "symbol" not in kw
    assert kw.get("order_id") == "oid-1"


def _gate_spot_call_assert(kw: Dict[str, Any]) -> None:
    assert kw == {"order_id": "oid-1", "symbol": "BTC/USDT"}


FILL_CONTRACT_CASES: Tuple[FillContractCase, ...] = (
    FillContractCase(
        "binance_futures_filled",
        BinanceFuturesClient,
        {"executedQty": "0.005", "avgPrice": "70000.5", "status": "FILLED"},
        (0.005, 70000.5, "filled"),
    ),
    FillContractCase(
        "binance_futures_partial",
        BinanceFuturesClient,
        {"executedQty": "0.002", "avgPrice": "69900", "status": "PARTIALLY_FILLED"},
        (0.002, 69900.0, "partial"),
    ),
    FillContractCase(
        "binance_futures_open",
        BinanceFuturesClient,
        {"executedQty": "0", "avgPrice": "0", "status": "NEW"},
        (0.0, 0.0, "open"),
    ),
    FillContractCase(
        "binance_spot_cancelled",
        BinanceSpotClient,
        {"executedQty": "0", "avgPrice": "0", "status": "CANCELED"},
        (0.0, 0.0, "cancelled"),
        market_type="spot",
    ),
    FillContractCase(
        "okx_filled",
        OkxClient,
        {"state": "filled", "accFillSz": "5", "avgPx": "65000.1"},
        (0.05, 65000.1, "filled"),
        call_assert=_okx_call_assert,
    ),
    FillContractCase(
        "okx_partial",
        OkxClient,
        {"state": "partially_filled", "accFillSz": "1", "avgPx": "64000"},
        (0.01, 64000.0, "partial"),
    ),
    FillContractCase(
        "okx_cancelled",
        OkxClient,
        {"state": "canceled", "accFillSz": "0", "avgPx": "0"},
        (0.0, 0.0, "cancelled"),
    ),
    FillContractCase(
        "bitget_mix_filled",
        BitgetMixClient,
        {"filled": 0.0042, "avg_price": 73472.95, "status": "filled"},
        (0.0042, 73472.95, "filled"),
        exchange_config={"product_type": "USDT-FUTURES"},
    ),
    FillContractCase(
        "bitget_mix_live_open",
        BitgetMixClient,
        {"filled": 0.0, "avg_price": 0.0, "status": "live"},
        (0.0, 0.0, "open"),
        exchange_config={"product_type": "USDT-FUTURES"},
    ),
    FillContractCase(
        "bitget_spot_filled",
        BitgetSpotClient,
        {"filled": "0.1", "avgPrice": "100.5", "status": "full-fill"},
        (0.1, 100.5, "filled"),
        market_type="spot",
    ),
    FillContractCase(
        "bybit_filled",
        BybitClient,
        {"orderStatus": "Filled", "cumExecQty": "0.012", "avgPrice": "72000"},
        (0.012, 72000.0, "filled"),
    ),
    FillContractCase(
        "bybit_partial",
        BybitClient,
        {"orderStatus": "PartiallyFilled", "cumExecQty": "0.003", "avgPrice": "71500"},
        (0.003, 71500.0, "partial"),
    ),
    FillContractCase(
        "bybit_open",
        BybitClient,
        {"orderStatus": "New", "cumExecQty": "0", "avgPrice": "0"},
        (0.0, 0.0, "open"),
    ),
    FillContractCase(
        "gate_spot_filled",
        GateSpotClient,
        {"status": "closed", "filled_amount": "0.02", "filled_total": "1300.4",
         "fill_price": "1300.4", "avg_deal_price": "65020", "price": "66000"},
        (0.02, 65020.0, "filled"),
        call_assert=_gate_spot_call_assert,
    ),
    FillContractCase(
        "gate_futures_finished",
        GateUsdtFuturesClient,
        {"status": "finished", "filled_size": "100", "fill_price": "65000"},
        (1.0, 65000.0, "filled"),
        call_assert=_gate_call_assert,
    ),
    FillContractCase(
        "htx_filled",
        HtxClient,
        {"status": 6, "trade_volume": "6", "trade_avg_price": "67500"},
        (0.006, 67500.0, "filled"),
    ),
    FillContractCase(
        "htx_open",
        HtxClient,
        {"status": 3, "trade_volume": "0", "trade_avg_price": "0"},
        (0.0, 0.0, "open"),
    ),
)


def _make_client(client_cls: Type) -> MagicMock:
    client = MagicMock()
    client.__class__ = client_cls
    return client


@pytest.mark.parametrize("case", FILL_CONTRACT_CASES, ids=lambda c: c.case_id)
def test_query_grid_order_fill_contract(case: FillContractCase):
    client = _make_client(case.client_cls)
    client.get_order.return_value = case.response
    if case.client_cls is OkxClient and case.market_type != "spot":
        client.get_instrument.return_value = {"ctVal": "0.01"}
    if case.client_cls is GateUsdtFuturesClient:
        client.get_contract.return_value = {"quanto_multiplier": "0.01"}
    if case.client_cls is HtxClient:
        client.get_contract_info.return_value = {"contract_size": "0.001"}
    filled, avg, status = query_grid_order_fill(
        client,
        symbol="BTC/USDT",
        market_type=case.market_type,
        exchange_order_id="oid-1",
        exchange_config=case.exchange_config or {},
    )
    assert (filled, avg, status) == case.expected
    assert client.get_order.called
    if case.call_assert:
        case.call_assert(client.get_order.call_args.kwargs)


def test_query_grid_order_fill_returns_unknown_when_get_order_raises():
    client = _make_client(BybitClient)
    client.get_order.side_effect = RuntimeError("network down")
    filled, avg, status = query_grid_order_fill(
        client,
        symbol="BTC/USDT",
        market_type="swap",
        exchange_order_id="oid-1",
    )
    assert (filled, avg, status) == (0.0, 0.0, "unknown")
