from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Type
from unittest.mock import MagicMock

import pytest

from app.services.grid.fill_units import parse_grid_order_fill
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.coinbase_exchange import CoinbaseExchangeClient
from app.services.live_trading.kraken import KrakenClient
from app.services.live_trading.kraken_futures import KrakenFuturesClient
from app.services.live_trading.okx import OkxClient


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "exchanges" / "order_fill_contracts.json"

CLIENTS: Dict[str, Type] = {
    "BinanceFuturesClient": BinanceFuturesClient,
    "BinanceSpotClient": BinanceSpotClient,
    "OkxClient": OkxClient,
    "BitgetMixClient": BitgetMixClient,
    "BitgetSpotClient": BitgetSpotClient,
    "BybitClient": BybitClient,
    "CoinbaseExchangeClient": CoinbaseExchangeClient,
    "GateSpotClient": GateSpotClient,
    "GateUsdtFuturesClient": GateUsdtFuturesClient,
    "KrakenClient": KrakenClient,
    "KrakenFuturesClient": KrakenFuturesClient,
    "HtxClient": HtxClient,
}


def _load_cases() -> list[Dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _make_client(case: Dict[str, Any]) -> MagicMock:
    cls = CLIENTS[str(case["client"])]
    client = MagicMock()
    client.__class__ = cls
    metadata = case.get("metadata") or {}
    if cls is OkxClient:
        client.get_instrument.return_value = metadata or {"ctVal": "1"}
    if cls is GateUsdtFuturesClient:
        client.get_contract.return_value = metadata or {"quanto_multiplier": "1"}
    if cls is HtxClient:
        client.get_contract_info.return_value = metadata or {"contract_size": "1"}
    return client


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
def test_order_fill_contract_fixture(case: Dict[str, Any]):
    client = _make_client(case)
    expected = case["expected"]

    filled, avg, status = parse_grid_order_fill(
        client,
        symbol=str(case.get("symbol") or "BTC/USDT"),
        market_type=str(case.get("market_type") or "swap"),
        exchange_config=case.get("exchange_config") or {},
        data=case["response"],
    )

    assert filled == pytest.approx(float(expected["filled"]))
    assert avg == pytest.approx(float(expected["avg"]))
    assert status == expected["status"]
