from datetime import datetime, timezone

import pytest

from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.exchange_close_history import resolve_exchange_external_close
from app.services.live_trading.gate import GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.okx import OkxClient
from app.services.alpaca_trading import AlpacaClient
from app.services.ibkr_trading import IBKRClient


OPENED = datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc)
TS1 = int(datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc).timestamp() * 1000)
TS2 = int(datetime(2026, 10, 15, 2, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _resolve(client, qty, *, side="long", market_type="swap", recorded=None):
    recorded = set(recorded or [])
    return resolve_exchange_external_close(
        client,
        symbol="BTC/USDT",
        side=side,
        target_qty=qty,
        opened_after=OPENED,
        market_type=market_type,
        is_recorded=lambda fill_id: fill_id in recorded,
    )


class _Binance(BinanceFuturesClient):
    def __init__(self, rows, force_orders=None):
        self.rows = rows
        self.force_orders = force_orders or {}

    def get_user_trades(self, **_kwargs):
        return self.rows

    def _signed_request(self, _method, _path, *, params):
        return self.force_orders.get(str(params.get("autoCloseType") or ""), [])


class _Bybit(BybitClient):
    def __init__(self, rows):
        self.rows = rows

    def get_executions(self, **_kwargs):
        return {"result": {"list": self.rows}}


class _Bitget(BitgetMixClient):
    def __init__(self, rows):
        self.rows = rows

    def _signed_request(self, *_args, **_kwargs):
        return {"data": {"list": self.rows, "cursor": ""}}


class _Gate(GateUsdtFuturesClient):
    def __init__(self, rows):
        self.rows = rows

    def _signed_request(self, *_args, **_kwargs):
        return self.rows

    def get_contract(self, **_kwargs):
        return {"quanto_multiplier": "0.001"}


class _Okx(OkxClient):
    def __init__(self, rows, history=None):
        self.rows = rows
        self.history = history or []

    def _signed_request(self, _method, path, **_kwargs):
        if path.endswith("positions-history"):
            return {"data": self.history}
        return {"data": self.rows}

    def get_instrument(self, **_kwargs):
        return {"ctVal": "0.01"}


class _Htx(HtxClient):
    def __init__(self, rows):
        self.rows = rows
        self.market_type = "swap"
        self.margin_mode = "cross"

    def _swap_private_request_raw(self, *_args, **_kwargs):
        return {"data": {"trades": self.rows}}

    def get_contract_info(self, **_kwargs):
        return {"contract_size": "0.001"}


class _Alpaca(AlpacaClient):
    def __init__(self, rows):
        self.rows = rows

    def get_account_activities(self, **_kwargs):
        return self.rows


class _Ibkr(IBKRClient):
    def __init__(self, rows):
        self.rows = rows

    def get_executions(self, **_kwargs):
        return self.rows


def test_binance_combines_exact_partial_close_fills():
    snap = _resolve(
        _Binance(
            [
                {"id": 1, "buyer": False, "positionSide": "LONG", "qty": "0.4", "price": "100", "commission": "0.04", "commissionAsset": "USDT", "time": TS1},
                {"id": 2, "buyer": False, "positionSide": "LONG", "qty": "0.6", "price": "110", "commission": "0.06", "commissionAsset": "USDT", "time": TS2},
                {"id": 3, "buyer": True, "positionSide": "LONG", "qty": "1", "price": "90", "time": TS2},
            ]
        ),
        1.0,
    )
    assert snap is not None
    assert snap["price"] == pytest.approx(106.0)
    assert snap["fee"] == pytest.approx(0.1)
    assert [row["exchange_fill_id"] for row in snap["components"]] == ["1", "2"]


def test_binance_preserves_force_order_liquidation_reason():
    snap = _resolve(
        _Binance(
            [
                {
                    "id": 7,
                    "orderId": 9001,
                    "buyer": False,
                    "positionSide": "LONG",
                    "qty": "1",
                    "price": "80",
                    "time": TS1,
                }
            ],
            force_orders={"LIQUIDATION": [{"orderId": 9001}]},
        ),
        1.0,
    )

    assert snap is not None
    assert snap["components"][0]["close_reason"] == "exchange_liquidation"


def test_close_history_requires_exact_unrecorded_quantity():
    client = _Binance(
        [{"id": 1, "buyer": False, "positionSide": "LONG", "qty": "0.4", "price": "100", "time": TS1}]
    )
    assert _resolve(client, 1.0) is None
    assert _resolve(client, 0.4, recorded={"1"}) is None


def test_close_history_treats_recorded_exchange_order_as_duplicate():
    client = _Binance(
        [{"id": 1, "orderId": 99, "buyer": False, "positionSide": "LONG", "qty": "0.4", "price": "100", "time": TS1}]
    )
    assert _resolve(client, 0.4, recorded={"99"}) is None


def test_bybit_uses_closed_size_and_position_lane():
    snap = _resolve(
        _Bybit(
            [{"execId": "b1", "side": "Buy", "positionIdx": 2, "closedSize": "0.25", "execQty": "1", "execPrice": "99", "execFee": "0.02", "execTime": TS1}]
        ),
        0.25,
        side="short",
    )
    assert snap is not None
    assert snap["amount"] == pytest.approx(0.25)


@pytest.mark.parametrize(
    ("exec_type", "expected"),
    [("BustTrade", "exchange_liquidation"), ("AdlTrade", "exchange_adl")],
)
def test_bybit_preserves_forced_close_reason(exec_type, expected):
    snap = _resolve(
        _Bybit(
            [
                {
                    "execId": "b-force",
                    "side": "Sell",
                    "positionIdx": 1,
                    "closedSize": "0.25",
                    "execPrice": "80",
                    "execTime": TS1,
                    "execType": exec_type,
                }
            ]
        ),
        0.25,
    )

    assert snap is not None
    assert snap["components"][0]["close_reason"] == expected


def test_bitget_rejects_open_fills_and_parses_fee_detail():
    snap = _resolve(
        _Bitget(
            [
                {"execId": "g0", "side": "sell", "tradeSide": "open", "execQty": "0.5", "execPrice": "90", "createdTime": TS1},
                {"execId": "g1", "side": "sell", "tradeSide": "close", "execQty": "0.5", "execPrice": "101", "feeDetail": [{"feeCoin": "USDT", "fee": "0.03"}], "createdTime": TS2},
            ]
        ),
        0.5,
    )
    assert snap is not None
    assert snap["fee"] == pytest.approx(0.03)
    assert snap["fee_ccy"] == "USDT"


def test_bitget_preserves_liquidation_reason():
    snap = _resolve(
        _Bitget(
            [{
                "execId": "g-force",
                "side": "sell",
                "tradeSide": "close",
                "execQty": "0.5",
                "execPrice": "80",
                "reason": "liquidation",
                "createdTime": TS1,
            }]
        ),
        0.5,
    )
    assert snap is not None
    assert snap["components"][0]["close_reason"] == "exchange_liquidation"


def test_gate_converts_contracts_to_base_quantity():
    snap = _resolve(
        _Gate([{"id": "gate1", "size": "-25", "price": "105", "fee": "-0.02", "create_time_ms": TS1}]),
        0.025,
    )
    assert snap is not None
    assert snap["amount"] == pytest.approx(0.025)


def test_gate_preserves_adl_reason():
    snap = _resolve(
        _Gate([{
            "id": "gate-adl",
            "size": "-25",
            "price": "80",
            "text": "adl_auto_close",
            "create_time_ms": TS1,
        }]),
        0.025,
    )
    assert snap is not None
    assert snap["components"][0]["close_reason"] == "exchange_adl"


def test_okx_converts_contracts_and_preserves_liquidation_reason():
    snap = _resolve(
        _Okx(
            [{"tradeId": "o1", "side": "sell", "posSide": "long", "fillSz": "2", "fillPx": "95", "fee": "-0.01", "feeCcy": "USDT", "ts": TS1}],
            history=[{"posSide": "long", "type": "3", "uTime": TS1}],
        ),
        0.02,
    )
    assert snap is not None
    assert snap["components"][0]["close_reason"] == "exchange_liquidation"


def test_okx_preserves_adl_reason():
    snap = _resolve(
        _Okx(
            [{"tradeId": "o-adl", "side": "sell", "posSide": "long", "fillSz": "2", "fillPx": "95", "ts": TS1}],
            history=[{"posSide": "long", "type": "5", "uTime": TS1}],
        ),
        0.02,
    )
    assert snap is not None
    assert snap["components"][0]["close_reason"] == "exchange_adl"


def test_okx_ignores_forced_close_history_from_before_current_position():
    stale_ts = int(datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc).timestamp() * 1000)
    snap = _resolve(
        _Okx(
            [{"tradeId": "o2", "side": "sell", "posSide": "long", "fillSz": "2", "fillPx": "105", "ts": TS1}],
            history=[{"posSide": "long", "type": "3", "uTime": stale_ts}],
        ),
        0.02,
    )

    assert snap is not None
    assert snap["components"][0]["close_reason"] == "exchange_native_close"


def test_htx_converts_contract_volume_to_base_quantity():
    snap = _resolve(
        _Htx([{"trade_id": "h1", "direction": "sell", "offset": "close", "trade_volume": "20", "trade_price": "102", "trade_fee": "-0.01", "created_at": TS1}]),
        0.02,
    )
    assert snap is not None
    assert snap["amount"] == pytest.approx(0.02)


def test_htx_preserves_liquidation_reason():
    snap = _resolve(
        _Htx([{
            "trade_id": "h-force",
            "direction": "sell",
            "offset": "close",
            "trade_volume": "20",
            "trade_price": "80",
            "reason": "forced_liquidation",
            "created_at": TS1,
        }]),
        0.02,
    )
    assert snap is not None
    assert snap["components"][0]["close_reason"] == "exchange_liquidation"


def test_alpaca_uses_fill_activities_and_preserves_order_id():
    client = _Alpaca(
        [
            {
                "id": "fill-a1",
                "order_id": "order-a1",
                "symbol": "AAPL",
                "side": "sell",
                "qty": "2",
                "price": "205.5",
                "transaction_time": datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc).isoformat(),
            }
        ]
    )
    snap = resolve_exchange_external_close(
        client,
        symbol="AAPL",
        side="long",
        target_qty=2.0,
        opened_after=OPENED,
        market_type="spot",
        is_recorded=lambda _value: False,
    )
    assert snap is not None
    assert snap["amount"] == pytest.approx(2.0)
    assert snap["price"] == pytest.approx(205.5)
    assert snap["exchange_order_id"] == "order-a1"


def test_ibkr_uses_account_executions_for_native_protection_close():
    snap = resolve_exchange_external_close(
        _Ibkr(
            [{
                "execution_id": "ib-fill-1",
                "order_id": 901,
                "client_order_id": "qd_7_3",
                "symbol": "AAPL",
                "side": "SLD",
                "quantity": 2,
                "price": 203.25,
                "executed_at": datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc),
                "commission": 0.35,
                "commission_ccy": "USD",
            }]
        ),
        symbol="AAPL",
        side="long",
        target_qty=2.0,
        opened_after=OPENED,
        market_type="spot",
        is_recorded=lambda _value: False,
    )
    assert snap is not None
    assert snap["price"] == pytest.approx(203.25)
    assert snap["fee"] == pytest.approx(0.35)
    assert snap["exchange_order_id"] == "901"
