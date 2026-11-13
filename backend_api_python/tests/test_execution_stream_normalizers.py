from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.execution_streams.events import normalize_symbol
from app.services.execution_streams.normalizers import (
    parse_alpaca,
    parse_binance,
    parse_bitget,
    parse_bybit,
    parse_gate,
    parse_htx,
    parse_ibkr_execution,
    parse_okx,
)
from app.services.live_trading.capabilities import supported_crypto_exchange_ids
from app.services.execution_streams.repository import ExecutionEventRepository


def _fee_map(event) -> dict[str, float]:
    return {fee.currency: fee.amount for fee in event.fees}


def test_supported_private_stream_crypto_venues_are_exactly_the_six_supported_exchanges():
    assert supported_crypto_exchange_ids() == {
        "binance",
        "okx",
        "bitget",
        "bybit",
        "gate",
        "htx",
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("BTCUSDT", "BTC/USDT"),
        ("BTC_USDT", "BTC/USDT"),
        ("BTC-USDT-SWAP", "BTC/USDT"),
        ("ETH-USD-PERP", "ETH/USD"),
        ("AAPL", "AAPL"),
    ),
)
def test_normalize_symbol(raw: str, expected: str):
    assert normalize_symbol(raw) == expected


def test_binance_spot_execution_report_contains_actual_fee():
    event = parse_binance(
        {
            "e": "executionReport",
            "E": 1_700_000_000_000,
            "s": "BTCUSDT",
            "i": 10,
            "c": "qd-1",
            "t": 99,
            "S": "BUY",
            "X": "FILLED",
            "L": "60000",
            "l": "0.01",
            "z": "0.01",
            "N": "BNB",
            "n": "0.0001",
        },
        market_type="spot",
    )[0]
    assert (event.symbol, event.order_status, event.quantity) == ("BTC/USDT", "filled", 0.01)
    assert event.fee_status == "actual"
    assert _fee_map(event) == {"BNB": pytest.approx(0.0001)}


def test_binance_futures_order_trade_update_contains_realized_pnl_and_fee():
    event = parse_binance(
        {
            "e": "ORDER_TRADE_UPDATE",
            "E": 1_700_000_000_000,
            "o": {
                "s": "BTCUSDT",
                "i": 11,
                "c": "qd-2",
                "t": 100,
                "S": "SELL",
                "ps": "LONG",
                "X": "FILLED",
                "L": "61000",
                "l": "0.02",
                "z": "0.02",
                "N": "USDT",
                "n": "0.67",
                "rp": "12.5",
            },
        },
        market_type="swap",
    )[0]
    assert event.position_side == "long"
    assert event.realized_pnl == pytest.approx(12.5)
    assert _fee_map(event) == {"USDT": pytest.approx(0.67)}


def test_okx_negative_commission_becomes_cost_and_positive_rebate_becomes_credit():
    event = parse_okx(
        {
            "arg": {"channel": "orders", "instType": "SWAP"},
            "data": [{
                "instId": "BTC-USDT-SWAP",
                "ordId": "12",
                "clOrdId": "qd-3",
                "tradeId": "101",
                "fillSz": "0.01",
                "fillPx": "62000",
                "accFillSz": "0.01",
                "state": "filled",
                "fee": "-0.31",
                "feeCcy": "USDT",
                "rebate": "0.02",
                "rebateCcy": "USDT",
            }],
        }
    )[0]
    assert event.symbol == "BTC/USDT"
    assert sum(f.amount for f in event.fees) == pytest.approx(0.29)


def test_okx_algo_fill_preserves_parent_protection_identifiers():
    event = parse_okx(
        {
            "arg": {"channel": "orders", "instType": "SWAP"},
            "data": [{
                "instId": "BTC-USDT-SWAP",
                "ordId": "child-12",
                "tradeId": "child-fill-12",
                "fillSz": "0.01",
                "fillPx": "62000",
                "state": "filled",
                "algoId": "algo-12",
                "algoClOrdId": "qdprot12-sl",
            }],
        }
    )[0]
    assert {tuple(item.values()) for item in event.order_identifiers} == {
        ("algo_id", "algo-12"),
        ("algo_client_id", "qdprot12-sl"),
    }


def test_binding_identifier_extraction_keeps_primary_parent_and_child_ids():
    identifiers = ExecutionEventRepository._event_identifiers({
        "exchange_order_id": "child-1",
        "client_order_id": "child-client-1",
        "raw_json": {
            "_qd_execution": {
                "order_identifiers": [
                    {"type": "algo_id", "value": "algo-1"},
                    {"type": "algo_client_id", "value": "qdprot1-sl"},
                ],
            },
        },
    })
    assert identifiers == [
        ("exchange_order_id", "child-1"),
        ("client_order_id", "child-client-1"),
        ("algo_id", "algo-1"),
        ("algo_client_id", "qdprot1-sl"),
    ]


def test_bybit_execution_uses_leaves_quantity_for_terminal_status():
    event = parse_bybit(
        {
            "topic": "execution",
            "creationTime": 1_700_000_000_000,
            "data": [{
                "category": "linear",
                "symbol": "BTCUSDT",
                "orderId": "13",
                "orderLinkId": "qd-4",
                "execId": "102",
                "side": "Sell",
                "execPrice": "63000",
                "execQty": "0.01",
                "leavesQty": "0",
                "execFee": "0.3465",
                "feeCurrency": "USDT",
                "execPnl": "2.5",
            }],
        }
    )[0]
    assert event.order_status == "filled"
    assert event.realized_pnl == pytest.approx(2.5)
    assert _fee_map(event) == {"USDT": pytest.approx(0.3465)}


def test_bybit_execution_preserves_conditional_parent_identifiers():
    event = parse_bybit(
        {
            "topic": "execution",
            "data": [{
                "category": "linear",
                "symbol": "BTCUSDT",
                "orderId": "child-13",
                "execId": "fill-13",
                "side": "Sell",
                "execPrice": "63000",
                "execQty": "0.01",
                "parentOrderId": "parent-13",
                "parentOrderLinkId": "qdprot13-sl",
            }],
        }
    )[0]
    assert {tuple(item.values()) for item in event.order_identifiers} == {
        ("parent_client_order_id", "qdprot13-sl"),
        ("parent_order_id", "parent-13"),
    }


def test_bybit_execution_includes_exchange_reported_extra_fees():
    event = parse_bybit(
        {
            "topic": "execution",
            "data": [{
                "category": "linear",
                "symbol": "BTCUSDT",
                "orderId": "13-extra",
                "execId": "102-extra",
                "side": "Sell",
                "execPrice": "63000",
                "execQty": "0.01",
                "execFee": "0.3",
                "feeCurrency": "USDT",
                "extraFees": [{"feeCoin": "USDT", "fee": "0.02"}],
                "leavesQty": "0",
            }],
        }
    )[0]
    assert sum(f.amount for f in event.fees if f.currency == "USDT") == pytest.approx(0.32)


@pytest.mark.parametrize(
    ("inst_type", "fee_detail"),
    (
        ("SPOT", [{"feeCoin": "BTC", "fee": "-0.000001"}]),
        ("USDT-FUTURES", [{"feeCoin": "USDT", "totalFee": "-0.2"}]),
    ),
)
def test_bitget_signed_fee_is_normalized_to_positive_cost(inst_type, fee_detail):
    event = parse_bitget(
        {
            "arg": {"channel": "fill", "instType": inst_type, "instId": "default"},
            "data": [{
                "symbol": "BTCUSDT",
                "orderId": "14",
                "clientOid": "qd-5",
                "tradeId": "103",
                "side": "buy",
                "price": "64000",
                "baseVolume": "0.01",
                "feeDetail": fee_detail,
                "uTime": "1700000000000",
            }],
        }
    )[0]
    assert event.market_type == ("spot" if inst_type == "SPOT" else "swap")
    assert all(f.amount > 0 for f in event.fees)


def test_bitget_spot_fill_uses_official_size_price_and_time_fields():
    event = parse_bitget(
        {
            "arg": {"topic": "fill", "instType": "UTA"},
            "data": [{
                "category": "spot",
                "symbol": "BTCUSDT",
                "orderId": "111",
                "execId": "222",
                "side": "buy",
                "execPrice": "42740.41",
                "execQty": "0.0006",
                "tradeScope": "maker",
                "feeDetail": [{
                    "feeCoin": "USDT",
                    "fee": "0.01538655",
                }],
                "createdTime": "1703580202094",
            }],
        }
    )[0]
    assert event.market_type == "spot"
    assert event.quantity == pytest.approx(0.0006)
    assert event.cumulative_quantity == 0
    assert event.price == pytest.approx(42740.41)
    assert event.maker is True
    assert _fee_map(event) == {"USDT": pytest.approx(0.01538655)}


def test_bitget_uta_fill_accepts_exec_fee_without_fee_detail():
    event = parse_bitget(
        {
            "arg": {"topic": "fill", "instType": "UTA"},
            "data": [{
                "category": "usdt-futures",
                "symbol": "BTCUSDT",
                "orderId": "333",
                "execId": "444",
                "side": "sell",
                "execPrice": "62000",
                "execQty": "0.01",
                "feeCoin": "USDT",
                "execFee": "0.31",
                "createdTime": "1703580202094",
            }],
        }
    )[0]
    assert event.fee_status == "actual"
    assert _fee_map(event) == {"USDT": pytest.approx(0.31)}


def test_gate_spot_and_futures_fees_are_actual_and_futures_currency_is_inferred():
    spot = parse_gate(
        {
            "channel": "spot.usertrades",
            "result": [{
                "id": "104",
                "order_id": "15",
                "currency_pair": "BTC_USDT",
                "amount": "0.01",
                "price": "65000",
                "side": "buy",
                "fee": "0.00001",
                "fee_currency": "BTC",
            }],
        },
        market_type="spot",
    )[0]
    future = parse_gate(
        {
            "channel": "futures.usertrades",
            "result": [{
                "id": "105",
                "order_id": "16",
                "contract": "BTC_USDT",
                "size": "-2",
                "price": "65010",
                "fee": "0.12",
            }],
        },
        market_type="swap",
    )[0]
    assert _fee_map(spot) == {"BTC": pytest.approx(0.00001)}
    assert _fee_map(future) == {"USDT": pytest.approx(0.12)}


def test_gate_triggered_fill_preserves_parent_and_child_identifiers():
    event = parse_gate(
        {
            "channel": "futures.usertrades",
            "result": [{
                "id": "105",
                "order_id": "child-16",
                "contract": "BTC_USDT",
                "size": "-2",
                "price": "65010",
                "price_order_id": "trigger-16",
                "suborder_id": "child-16",
                "suborder_text": "t-qdprot16-sl",
            }],
        },
        market_type="swap",
    )[0]
    assert {tuple(item.values()) for item in event.order_identifiers} == {
        ("parent_order_id", "trigger-16"),
        ("child_order_id", "child-16"),
        ("child_client_order_id", "t-qdprot16-sl"),
    }


def test_htx_spot_and_swap_fees_are_parsed():
    spot = parse_htx(
        {
            "ch": "trade.clearing#btcusdt",
            "data": {
                "symbol": "btcusdt",
                "orderId": "17",
                "tradeId": "106",
                "orderSide": "buy",
                "tradePrice": "65100",
                "tradeVolume": "0.01",
                "transactFee": "0.00001",
                "feeCurrency": "btc",
            },
        },
        market_type="spot",
    )[0]
    swap = parse_htx(
        {
            "topic": "matchOrders.*",
            "data": {
                "contract_code": "BTC-USDT",
                "order_id": "18",
                "trade_id": "107",
                "direction": "sell",
                "trade_price": "65200",
                "trade_volume": "2",
                "trade_fee": "-0.15",
                "fee_asset": "USDT",
                "real_profit": "3.2",
            },
        },
        market_type="swap",
    )[0]
    assert _fee_map(spot) == {"BTC": pytest.approx(0.00001)}
    assert _fee_map(swap) == {"USDT": pytest.approx(0.15)}
    assert swap.realized_pnl == pytest.approx(3.2)


def test_alpaca_trade_update_and_ibkr_execution_are_normalized():
    alpaca = parse_alpaca(
        {
            "stream": "trade_updates",
            "data": {
                "event": "fill",
                "execution_id": "108",
                "qty": "3",
                "price": "192.5",
                "timestamp": "2026-07-24T01:02:03Z",
                "order": {
                    "id": "19",
                    "client_order_id": "qd-6",
                    "symbol": "AAPL",
                    "side": "buy",
                    "filled_qty": "3",
                },
            },
        }
    )[0]
    execution = SimpleNamespace(
        orderId=20,
        permId=21,
        orderRef="qd-7",
        execId="109",
        side="BOT",
        price=430.25,
        shares=2,
        cumQty=2,
        time=None,
    )
    ibkr = parse_ibkr_execution(execution, SimpleNamespace(symbol="MSFT"))
    assert (alpaca.exchange_id, alpaca.order_status, alpaca.quantity) == ("alpaca", "filled", 3)
    assert alpaca.fee_status == "pending"
    assert (ibkr.exchange_id, ibkr.symbol, ibkr.quantity) == ("ibkr", "MSFT", 2)
    assert ibkr.exchange_fill_id == "109"
