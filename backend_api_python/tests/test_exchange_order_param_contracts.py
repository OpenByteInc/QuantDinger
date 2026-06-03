from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Type
from unittest.mock import MagicMock

import pytest

from app.services.grid.exchange_orders import place_grid_limit_order
from app.services.live_trading.base import LiveOrderResult
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.coinbase_exchange import CoinbaseExchangeClient
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
from app.services.live_trading.kraken import KrakenClient
from app.services.live_trading.kraken_futures import KrakenFuturesClient
from app.services.live_trading.okx import OkxClient


@dataclass(frozen=True)
class OrderParamCase:
    case_id: str
    client_cls: Type
    market_type: str
    side: str
    pos_side: str
    reduce_only: bool
    expected: Dict[str, Any]
    exchange_config: Dict[str, Any] | None = None


CASES = (
    OrderParamCase(
        "binance_futures_close_long_reduce_only",
        BinanceFuturesClient,
        "swap",
        "sell",
        "long",
        True,
        {
            "symbol": "BTC/USDT",
            "side": "SELL",
            "quantity": 0.01,
            "price": 70000.0,
            "reduce_only": True,
            "position_side": "long",
            "client_order_id": "coid-1",
        },
    ),
    OrderParamCase(
        "binance_spot_open_buy_no_reduce_only",
        BinanceSpotClient,
        "spot",
        "buy",
        "",
        False,
        {
            "symbol": "BTC/USDT",
            "side": "BUY",
            "quantity": 0.01,
            "price": 70000.0,
            "client_order_id": "coid-1",
        },
    ),
    OrderParamCase(
        "okx_swap_close_short",
        OkxClient,
        "swap",
        "buy",
        "short",
        True,
        {
            "market_type": "swap",
            "symbol": "BTC/USDT",
            "side": "buy",
            "size": 0.01,
            "price": 70000.0,
            "pos_side": "short",
            "td_mode": "cross",
            "reduce_only": True,
            "client_order_id": "coid-1",
        },
    ),
    OrderParamCase(
        "bitget_mix_open_short_with_product_type",
        BitgetMixClient,
        "swap",
        "sell",
        "short",
        False,
        {
            "symbol": "BTC/USDT",
            "side": "sell",
            "size": 0.01,
            "price": 70000.0,
            "margin_coin": "USDT",
            "product_type": "USDT-FUTURES",
            "margin_mode": "cross",
            "reduce_only": False,
            "post_only": True,
            "client_order_id": "coid-1",
            "hold_side": "short",
        },
        {"product_type": "USDT-FUTURES", "margin_coin": "USDT"},
    ),
    OrderParamCase(
        "bitget_spot_buy",
        BitgetSpotClient,
        "spot",
        "buy",
        "",
        False,
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "size": 0.01,
            "price": 70000.0,
            "client_order_id": "coid-1",
        },
    ),
    OrderParamCase(
        "bybit_close_long",
        BybitClient,
        "swap",
        "sell",
        "long",
        True,
        {
            "symbol": "BTC/USDT",
            "side": "sell",
            "qty": 0.01,
            "price": 70000.0,
            "reduce_only": True,
            "pos_side": "long",
            "client_order_id": "coid-1",
        },
    ),
    OrderParamCase(
        "gate_spot_sell",
        GateSpotClient,
        "spot",
        "sell",
        "",
        False,
        {
            "symbol": "BTC/USDT",
            "side": "sell",
            "size": 0.01,
            "price": 70000.0,
            "client_order_id": "coid-1",
        },
    ),
    OrderParamCase(
        "coinbase_spot_buy",
        CoinbaseExchangeClient,
        "spot",
        "buy",
        "",
        False,
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "size": 0.01,
            "price": 70000.0,
            "client_order_id": "coid-1",
        },
    ),
    OrderParamCase(
        "kraken_spot_sell",
        KrakenClient,
        "spot",
        "sell",
        "",
        False,
        {
            "symbol": "BTC/USDT",
            "side": "sell",
            "size": 0.01,
            "price": 70000.0,
            "client_order_id": "coid-1",
        },
    ),
    OrderParamCase(
        "kraken_futures_close_short",
        KrakenFuturesClient,
        "swap",
        "buy",
        "short",
        True,
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "size": 0.01,
            "price": 70000.0,
            "reduce_only": True,
            "post_only": True,
            "client_order_id": "coid-1",
        },
    ),
    OrderParamCase(
        "gate_futures_close_short",
        GateUsdtFuturesClient,
        "swap",
        "buy",
        "short",
        True,
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "size": 0.01,
            "price": 70000.0,
            "reduce_only": True,
            "client_order_id": "coid-1",
        },
    ),
)


def _make_client(client_cls: Type) -> MagicMock:
    client = MagicMock()
    client.__class__ = client_cls
    client.place_limit_order.return_value = LiveOrderResult(
        exchange_id="test",
        exchange_order_id="oid-1",
        filled=0.0,
        avg_price=0.0,
        raw={},
    )
    return client


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.case_id)
def test_place_grid_limit_order_param_contract(case: OrderParamCase):
    client = _make_client(case.client_cls)

    result = place_grid_limit_order(
        client,
        symbol="BTC/USDT",
        side=case.side,
        quantity=0.01,
        price=70000.0,
        market_type=case.market_type,
        exchange_config=case.exchange_config or {},
        pos_side=case.pos_side,
        reduce_only=case.reduce_only,
        client_order_id="coid-1",
        leverage=3.0,
        margin_mode="cross",
        post_only=True,
    )

    assert result.exchange_order_id == "oid-1"
    assert client.place_limit_order.call_args.kwargs == case.expected


def test_place_grid_limit_order_sets_leverage_for_contract_clients():
    client = _make_client(BitgetMixClient)

    place_grid_limit_order(
        client,
        symbol="BTC/USDT",
        side="buy",
        quantity=0.01,
        price=70000.0,
        market_type="swap",
        exchange_config={"product_type": "USDT-FUTURES", "margin_coin": "USDT"},
        pos_side="long",
        leverage=5.0,
        margin_mode="cross",
    )

    client.set_leverage.assert_called_once()
    assert client.set_leverage.call_args.kwargs["hold_side"] == "long"
    assert client.set_leverage.call_args.kwargs["product_type"] == "USDT-FUTURES"
