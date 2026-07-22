from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.live_trading.adapters import LiveOrderPhaseAdapter
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.contracts import OrderIntent
from app.services.live_trading.gate import GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.okx import OkxClient
from app.services.live_trading.fee_quote import fee_to_quote
from app.services.pending_orders.fee_reconciliation import (
    backfill_zero_commission_trades,
    fee_breakdown_snapshot,
    incremental_fees,
)


def test_fee_reconciliation_reads_saved_phase_and_only_charges_delta():
    saved = {
        "phases": {
            "fee_breakdown": {"USDT": "0.03", "BNB": "0.001"},
        }
    }
    previous = fee_breakdown_snapshot(saved)
    delta = incremental_fees(
        {"USDT": 0.05, "BNB": 0.001},
        previous,
    )

    assert previous == {"USDT": 0.03, "BNB": 0.001}
    assert delta == pytest.approx({"USDT": 0.02})


def test_fee_backfill_repairs_missing_quote_value_without_overwriting_native_fee(monkeypatch):
    statements = []

    class Cursor:
        def execute(self, sql, params):
            statements.append((sql, params))

        def fetchall(self):
            return [{"id": 1, "value": 100, "amount": 1}]

        def close(self):
            return None

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            return None

    monkeypatch.setattr(
        "app.services.pending_orders.fee_reconciliation.get_db_connection",
        lambda: Database(),
    )

    count = backfill_zero_commission_trades(
        order_id=9,
        fees_by_ccy={"BNB": 0.00003},
        commission_quote=0.024,
    )

    assert count == 1
    assert "COALESCE(commission_quote, 0) = 0" in statements[0][0]
    update_sql, update_params = statements[1]
    assert "CASE WHEN COALESCE(commission, 0) = 0" in update_sql
    assert "CASE WHEN COALESCE(commission_quote, 0) = 0" in update_sql
    assert update_params[0] == pytest.approx(0.00003)
    assert update_params[1] == "BNB"
    assert update_params[2] == pytest.approx(0.024)
    assert update_params[3] == 1


def test_adapter_preserves_multi_currency_fee_breakdown():
    adapter = LiveOrderPhaseAdapter(
        client=object(),
        exchange_id="test",
        payload={},
        exchange_config={},
    )
    with patch(
        "app.services.live_trading.adapters.wait_live_order_fill",
        return_value={
            "filled": 1,
            "avg_price": 100,
            "status": "filled",
            "fees_by_ccy": {"usdt": "0.05", "bnb": "0.001"},
        },
    ):
        fill = adapter.wait_for_fill(OrderIntent(symbol="BTC/USDT", side="buy", quantity=1))

    assert fill.fees_by_ccy == {"USDT": 0.05, "BNB": 0.001}


def test_binance_spot_uses_spot_trade_history_and_converts_bnb_fee():
    client = BinanceSpotClient(api_key="key", secret_key="secret")
    trades = [
        {"orderId": 12, "commission": "0.00001", "commissionAsset": "BNB"},
        {"orderId": 12, "commission": "0.00002", "commissionAsset": "BNB"},
    ]
    with patch.object(client, "_signed_request", return_value=trades) as signed:
        fee, currency, breakdown = client._fetch_commission_for_order(
            symbol="BTC/USDT", order_id="12", filled=0.1, avg_price=60000,
        )

    assert fee == pytest.approx(0.00003)
    assert currency == "BNB"
    assert breakdown == pytest.approx({"BNB": 0.00003})
    assert signed.call_args.args[:2] == ("GET", "/api/v3/myTrades")
    assert signed.call_args.kwargs["params"]["orderId"] == "12"

    with patch.object(client, "get_ticker", return_value={"price": "800"}) as ticker:
        quote_fee = fee_to_quote(
            client, symbol="BTC/USDT", fee=fee, fee_ccy=currency, fill_price=60000,
        )
    assert quote_fee == pytest.approx(0.024)
    ticker.assert_called_once_with(symbol="BNB/USDT")


def test_binance_futures_uses_futures_trade_history():
    client = BinanceFuturesClient(api_key="key", secret_key="secret")
    with patch.object(
        client,
        "_signed_request",
        return_value=[{"commission": "0.03", "commissionAsset": "USDT"}],
    ) as signed:
        fee, currency, breakdown = client._fetch_commission_for_order(
            symbol="BTC/USDT", order_id="34", filled=0.1, avg_price=60000,
        )

    assert fee == pytest.approx(0.03)
    assert currency == "USDT"
    assert breakdown == pytest.approx({"USDT": 0.03})
    assert signed.call_args.args[:2] == ("GET", "/fapi/v1/userTrades")
    assert signed.call_args.kwargs["params"]["orderId"] == "34"


@pytest.mark.parametrize(
    "client",
    [
        BinanceSpotClient(api_key="key", secret_key="secret"),
        BinanceFuturesClient(api_key="key", secret_key="secret"),
    ],
)
def test_binance_missing_fill_fee_is_retried_instead_of_estimated(client):
    history_method = (
        "get_my_trades"
        if isinstance(client, BinanceSpotClient)
        else "get_user_trades"
    )
    with patch.object(client, history_method, return_value=[]), patch.object(
        client,
        "get_fee_rate",
    ) as get_fee_rate, patch("time.sleep"):
        fee, currency, breakdown = client._fetch_commission_for_order(
            symbol="BTC/USDT",
            order_id="not-visible-yet",
            filled=0.1,
            avg_price=60000,
        )

    assert fee == 0
    assert currency == ""
    assert breakdown == {}
    get_fee_rate.assert_not_called()


def test_okx_fee_reconciliation_falls_back_to_fills_history():
    client = OkxClient(api_key="key", secret_key="secret", passphrase="pass")
    history = {"data": [{"ordId": "o-1", "fee": "-0.02", "feeCcy": "USDT"}]}
    with patch.object(client, "_signed_request", side_effect=[{"data": []}, history]) as signed:
        result = client.get_order_fills(
            inst_id="BTC-USDT", ord_id="o-1", inst_type="SPOT",
        )

    assert result == history
    assert [call.args[1] for call in signed.call_args_list] == [
        "/api/v5/trade/fills",
        "/api/v5/trade/fills-history",
    ]
    assert signed.call_args_list[-1].kwargs["params"]["instType"] == "SPOT"


def test_okx_spot_fee_currency_can_be_converted_to_quote():
    client = OkxClient(api_key="key", secret_key="secret", passphrase="pass")
    with patch.object(
        client,
        "_public_request",
        return_value={"data": [{"instId": "OKB-USDT", "last": "50"}]},
    ) as ticker:
        quote_fee = fee_to_quote(
            client, symbol="BTC/USDT", fee=0.01, fee_ccy="OKB", fill_price=60000,
        )

    assert quote_fee == pytest.approx(0.5)
    ticker.assert_called_once_with(
        "GET", "/api/v5/market/ticker", params={"instId": "OKB-USDT"},
    )


def test_bybit_wait_for_fill_sums_authoritative_executions():
    client = BybitClient(api_key="key", secret_key="secret", category="linear")
    order = {"orderStatus": "Filled", "cumExecQty": "2", "avgPrice": "100"}
    executions = {
        "result": {
            "list": [
                {"orderId": "o-1", "execFee": "0.03", "feeCurrency": "USDT"},
                {"orderId": "o-1", "execFee": "0.02", "feeCurrency": "USDT"},
            ]
        }
    }
    with patch.object(client, "get_order", return_value=order), patch.object(
        client, "get_executions", return_value=executions
    ) as get_executions:
        result = client.wait_for_fill(symbol="BTC/USDT", order_id="o-1", max_wait_sec=0)

    assert result["fee"] == pytest.approx(0.05)
    assert result["fee_ccy"] == "USDT"
    assert result["fees_by_ccy"] == pytest.approx({"USDT": 0.05})
    get_executions.assert_called_once()


def test_htx_spot_wait_for_fill_uses_match_results_fee():
    client = HtxClient(api_key="key", secret_key="secret", market_type="spot")
    order = {
        "state": "filled",
        "field-amount": "0.1",
        "field-cash-amount": "10",
    }
    matches = {
        "status": "ok",
        "data": [
            {"filled-fees": "0.00004", "fee-currency": "BTC"},
            {"filled-fees": "0.00006", "fee-currency": "BTC"},
        ],
    }
    with patch.object(client, "get_order", return_value=order), patch.object(
        client, "get_order_match_results", return_value=matches
    ) as get_matches:
        result = client.wait_for_fill(symbol="BTC/USDT", order_id="o-2", max_wait_sec=0)

    assert result["filled"] == pytest.approx(0.1)
    assert result["avg_price"] == pytest.approx(100)
    assert result["fee"] == pytest.approx(0.0001)
    assert result["fee_ccy"] == "BTC"
    assert result["fees_by_ccy"] == pytest.approx({"BTC": 0.0001})
    get_matches.assert_called_once()


def test_htx_swap_fee_parser_prefers_nested_trades_without_double_counting():
    raw = {
        "code": 200,
        "data": {
            "details": [
                {
                    "fee": "-0.08",
                    "fee_asset": "USDT",
                    "trades": [
                        {"trade_fee": "-0.03", "fee_asset": "USDT"},
                        {"trade_fee": "-0.05", "fee_asset": "USDT"},
                    ],
                }
            ]
        },
    }

    fees = HtxClient._match_fee_breakdown(raw, default_ccy="USDT")

    assert fees == pytest.approx({"USDT": 0.08})


@pytest.mark.parametrize(
    ("margin_mode", "endpoint"),
    [
        ("isolated", "/linear-swap-api/v1/swap_order_detail"),
        ("cross", "/linear-swap-api/v1/swap_cross_order_detail"),
    ],
)
def test_htx_swap_fee_query_falls_back_to_v1_order_detail(
    margin_mode,
    endpoint,
):
    client = HtxClient(
        api_key="key",
        secret_key="secret",
        market_type="swap",
        margin_mode=margin_mode,
    )
    expected = {
        "status": "ok",
        "data": {"trades": [{"trade_fee": "-0.03", "fee_asset": "USDT"}]},
    }
    with patch.object(
        client,
        "_swap_v5_request",
        side_effect=LiveTradingError("V5 unavailable"),
    ), patch.object(
        client,
        "_swap_private_request_raw",
        return_value=expected,
    ) as v1_request:
        result = client.get_order_match_results(
            symbol="BTC/USDT",
            order_id="123",
        )

    assert result == expected
    v1_request.assert_called_once_with(
        "POST",
        endpoint,
        json_body={"contract_code": "BTC-USDT", "order_id": "123"},
    )


@pytest.mark.parametrize(
    ("client", "response", "expected"),
    [
        (BinanceFuturesClient(api_key="key", secret_key="secret"),
         {"raw": [{"tranId": 1, "symbol": "BTCUSDT", "income": "-0.25", "asset": "USDT", "time": 10}]}, -0.25),
        (OkxClient(api_key="key", secret_key="secret", passphrase="pass"),
         {"data": [{"billId": "1", "instId": "BTC-USDT-SWAP", "balChg": "0.15", "ccy": "USDT", "ts": "10"}]}, 0.15),
        (BitgetMixClient(api_key="key", secret_key="secret", passphrase="pass"),
         {"data": {"bills": [{"billId": "1", "symbol": "BTCUSDT", "amount": "-0.35", "coin": "USDT", "cTime": "10"}]}}, -0.35),
        (BybitClient(api_key="key", secret_key="secret"),
         {"result": {"list": [{"id": "1", "symbol": "BTCUSDT", "funding": "0.45", "currency": "USDT", "transactionTime": "10"}]}}, 0.45),
        (GateUsdtFuturesClient(api_key="key", secret_key="secret"),
         [{"id": "1", "contract": "BTC_USDT", "type": "fund", "change": "-0.55", "time": 10}], -0.55),
        (HtxClient(api_key="key", secret_key="secret", market_type="swap"),
         {"status": "ok", "data": {"financial_record": [{"id": "1", "contract": "BTC-USDT", "type": 30, "amount": "0.65", "created_at": 10}]}}, 0.65),
    ],
)
def test_exchange_funding_payments_use_signed_cash_flow(client, response, expected):
    method = "_swap_private_request_raw" if isinstance(client, HtxClient) else "_signed_request"
    with patch.object(client, method, return_value=response):
        rows = client.get_funding_payments(
            symbol="BTC/USDT", start_time_ms=1, end_time_ms=100, limit=100,
        )

    assert len(rows) == 1
    assert rows[0]["amount"] == pytest.approx(expected)
    assert rows[0]["asset"] == "USDT"
