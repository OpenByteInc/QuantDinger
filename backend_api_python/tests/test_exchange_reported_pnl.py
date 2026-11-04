"""Venue P&L must remain independent of strategy P&L and fee calculations."""
from unittest.mock import MagicMock
from types import SimpleNamespace
import pytest

from app.services.execution_streams.reported_pnl import fill_report, fetch_order_report, websocket_report
from app.services.execution_streams import pnl_reconciliation as reconciliation


def test_report_preserves_negative_zero_and_deduplicates_fills_without_deducting_fees():
    rows = [dict(id="a", pnl="-3.123456", quantity=".1"), dict(id="b", pnl="0", quantity=".2")]
    result = fill_report(rows + rows, expected=.3, source="test", currency="USDT")
    assert result["amount"] == -3.123456
    assert result["fill_ids"] == ["a", "b"]
    assert result["fee_basis"] == "excluding_fees"
    assert fill_report([dict(id="a", pnl=0, quantity=1)], expected=1, source="test", currency="USDT")["amount"] == 0


@pytest.mark.parametrize("pnl", [None, "", "NaN", "Infinity", "not-a-number", "1.7976931348623157e308"])
def test_missing_invalid_and_ibkr_unset_sentinel_are_never_zero(pnl):
    assert fill_report([dict(id="a", pnl=pnl, quantity=1)], expected=1, source="test", currency="USD") is None


def test_partial_overlapping_and_conflicting_reports_are_not_complete():
    row = dict(id="a", pnl=1, quantity=.5)
    assert fill_report([row], expected=1, source="test", currency="USDT") is None
    assert fill_report([row], expected=.1, source="test", currency="USDT") is None
    assert fill_report([row, dict(row, pnl=2)], expected=.5, source="test", currency="USDT") is None


def test_ws_uses_raw_venue_field_and_rejects_defaulted_zero():
    event = dict(exchange_fill_id="a", quantity=1, realized_pnl=0, raw_json={"o": {"rp": ""}})
    assert websocket_report([event], expected=1, exchange="binance", currency="USDT") is None
    event["raw_json"]["o"]["rp"] = "-7"
    result = websocket_report([event, event], expected=1, exchange="binance", currency="USDT")
    assert result["amount"] == -7
    assert result["source"] == "websocket:rp"


@pytest.fixture
def venue(monkeypatch):
    monkeypatch.setattr("app.services.live_trading.fill_accounting.contract_multiplier", lambda *a: .1)
    return MagicMock()


def fetch(client, exchange, **kwargs):
    return fetch_order_report(client, exchange=exchange, symbol="BTC/USDT", order_id="o1", expected=1,
                              timestamp=1700000000, config={}, **kwargs)


def test_gate_joins_order_fills_to_pnl_bills_by_contract_and_trade_id(venue):
    venue._order_trade_rows.return_value = [dict(id="f1", order_id="o1", contract="BTC_USDT", size="10", create_time=1700000000)]
    bill = dict(id="bill1", trade_id="f1", contract="BTC_USDT", type="pnl", change="-5")
    venue._signed_request.return_value = [bill, bill, dict(bill, id="fee", type="fee", change="-.1"),
        dict(bill, id="other", contract="ETH_USDT", change="999"), dict(bill, id="otherfill", trade_id="f2", change="100")]
    report = fetch(venue, "gate")
    assert report["amount"] == -5
    assert report["quantity"] == 1
    assert report["fee_basis"] == "excluding_fees"
    assert venue._order_trade_rows.call_args.args[1]["order"] == "o1"
    venue._signed_request.return_value = [dict(bill, trade_id="unknown")]
    assert fetch(venue, "gate") is None


def test_gate_truncated_book_does_not_claim_complete_pnl(venue):
    venue._order_trade_rows.return_value = [dict(id="f1", order_id="o1", contract="BTC_USDT", size="10", create_time=1700000000)]
    venue._signed_request.return_value = [{}] * 1000
    assert fetch(venue, "gate") is None


@pytest.mark.parametrize("exchange", ["binance", "okx", "bitget", "htx"])
def test_rest_fields_preserved_and_other_orders_excluded(venue, exchange):
    if exchange == "binance":
        venue.get_user_trades.return_value = [dict(id="f1", orderId="o1", qty=1, realizedPnl="-7"),
                                             dict(id="other", orderId="o2", qty=1, realizedPnl="99")]
    elif exchange == "okx":
        venue.get_order_fills.return_value = {"data": [dict(tradeId="f1", ordId="o1", fillSz=10, fillPnl="-7")]}
    elif exchange == "bitget":
        venue.get_order_fills.return_value = {"data": {"fillList": [dict(tradeId="f1", orderId="o1", baseVolume=1, profit="-7")]}}
    else:
        venue.get_order_match_results.return_value = {"data": {"order_id_str": "o1", "trades": [dict(id="f1", trade_volume=10, real_profit="-7")]}}
    assert fetch(venue, exchange)["amount"] == -7


def test_bybit_closed_order_pnl_is_not_charged_fees_twice(venue):
    venue._signed_request.side_effect = [
        {"result": {"list": [], "nextPageCursor": "next"}},
        {"result": {"list": [dict(orderId="o1", closedSize=1, closedPnl="-10", openFee="2", closeFee="3")]}}]
    result = fetch(venue, "bybit")
    assert result["amount"] == -10
    assert result["source"] == "rest:closed-pnl.closedPnl"
    assert venue._signed_request.call_args.kwargs["params"]["cursor"] == "next"


def row(id=1, **kwargs):
    return dict(id=id, credential_id=1, exchange_id="binance", market_type="swap", symbol="BTC/USDT",
                exchange_order_id="o1", amount=.5, type="close_long", profit=999, created_at=1700000000, **kwargs)


def test_ws_total_once_per_order_and_never_overwrites_grid_return(monkeypatch):
    rows = [row(), row(2)]
    key = reconciliation.order_key(rows[0])
    event = dict(exchange_fill_id="f1", quantity=1, raw_json={"o": {"rp": "-5"}})
    monkeypatch.setattr(reconciliation, "_load", lambda keys: ({}, {key: [event, event]}))
    monkeypatch.setattr(reconciliation, "_claim", lambda *a: pytest.fail("Complete WS data should not query REST"))
    result = reconciliation.enrich_reported_order_pnl(rows, user_id=1, trading_config={})
    assert result[0]["exchange_pnl"]["status"] == "order_total_elsewhere"
    assert result[1]["exchange_pnl"]["amount"] == -5
    assert result[1]["profit"] == 999


def test_account_symbol_market_and_order_are_isolated(monkeypatch):
    rows = [row()]
    event = dict(exchange_fill_id="f1", quantity=.5, raw_json={"o": {"rp": "999"}})
    other = (2, "binance", "swap", "BTC/USDT", "o1")
    monkeypatch.setattr(reconciliation, "_load", lambda keys: ({}, {other: [event]}))
    monkeypatch.setattr(reconciliation, "_claim", lambda *a: False)
    result = reconciliation.enrich_reported_order_pnl(rows, user_id=1, trading_config={})
    assert result[0]["exchange_pnl"] == {"status": "pending"}


def test_spot_and_missing_credentials_do_not_manufacture_realized_pnl(monkeypatch):
    rows = [row(), row(2)]
    rows[0]["market_type"] = "spot"
    rows[1]["credential_id"] = 0
    monkeypatch.setattr(reconciliation, "_load", lambda keys: pytest.fail("No eligible reports"))
    result = reconciliation.enrich_reported_order_pnl(rows, user_id=1, trading_config={})
    assert result[0]["exchange_pnl"] == {"status": "not_applicable_spot"}
    assert result[1]["exchange_pnl"] == {"status": "unavailable"}


def test_unsupported_venue_is_explicit_and_never_queried(monkeypatch):
    rows = [dict(row(), exchange_id="alpaca", market_type="usstock")]
    monkeypatch.setattr(reconciliation, "_load", lambda keys: pytest.fail("Unsupported venues must not be queried"))

    result = reconciliation.enrich_reported_order_pnl(rows, user_id=1, trading_config={})

    assert result[0]["exchange_pnl"] == {"status": "unsupported"}
    assert reconciliation.supports_reported_order_pnl("alpaca", "usstock") is False
    assert reconciliation.supports_reported_order_pnl("webull", "usstock") is False
    assert reconciliation.supports_reported_order_pnl("ibkr", "usstock") is True
    assert reconciliation.supports_reported_order_pnl("tiger", "usstock") is True
    assert reconciliation.supports_reported_order_pnl("tiger", "hkstock") is True


def test_tiger_reads_order_level_realized_pnl_for_us_and_hk_stocks():
    client = MagicMock()
    client.get_order_status.return_value = SimpleNamespace(raw={
        "filled_qty": "2",
        "realized_pnl": "-3.25",
        "currency": "HKD",
    })

    result = fetch_order_report(
        client,
        exchange="tiger",
        symbol="00700",
        order_id="123",
        expected=2,
        timestamp=1700000000,
        config={},
    )

    assert result["amount"] == -3.25
    assert result["currency"] == "HKD"
    assert result["source"] == "rest:order.realized_pnl"
    assert result["fee_basis"] == "venue_defined"


def test_tiger_account_without_order_pnl_is_explicitly_unsupported():
    client = MagicMock()
    client.get_order_status.return_value = SimpleNamespace(raw={
        "filled_qty": 2,
        "realized_pnl": None,
        "currency": "USD",
    })

    result = fetch_order_report(
        client,
        exchange="tiger",
        symbol="AAPL",
        order_id="123",
        expected=2,
        timestamp=1700000000,
        config={},
    )

    assert result == {"status": "unsupported", "reason": "venue_order_pnl_not_provided"}


@pytest.mark.parametrize("exchange", ["binance", "okx", "bybit", "bitget", "gate", "htx"])
def test_crypto_spot_never_claims_venue_reported_position_pnl(monkeypatch, exchange):
    rows = [dict(row(), exchange_id=exchange, market_type="spot")]
    monkeypatch.setattr(reconciliation, "_load", lambda keys: pytest.fail("Spot rows are not eligible"))

    result = reconciliation.enrich_reported_order_pnl(rows, user_id=1, trading_config={})

    assert result[0]["exchange_pnl"] == {"status": "not_applicable_spot"}


@pytest.mark.parametrize("exchange", ["binance", "okx", "bybit", "bitget", "gate", "htx"])
def test_supported_derivatives_keep_venue_pnl_pending_until_evidence_is_complete(monkeypatch, exchange):
    rows = [dict(row(), exchange_id=exchange)]
    monkeypatch.setattr(reconciliation, "_load", lambda keys: ({}, {}))
    monkeypatch.setattr(reconciliation, "_claim", lambda *args: False)

    result = reconciliation.enrich_reported_order_pnl(rows, user_id=1, trading_config={})

    assert result[0]["exchange_pnl"] == {"status": "pending"}


def test_rest_budget_and_failures_leave_reports_pending(monkeypatch):
    rows = [dict(row(i), exchange_order_id=str(i)) for i in range(1, 6)]
    monkeypatch.setattr(reconciliation, "_load", lambda keys: ({}, {}))
    monkeypatch.setattr(reconciliation, "_claim", lambda *a: True)
    make_client = MagicMock(side_effect=RuntimeError("offline"))
    monkeypatch.setattr(reconciliation, "_client", make_client)
    reconciliation.enrich_reported_order_pnl(rows, user_id=1, trading_config={})
    assert make_client.call_count == 2
    assert all(r["exchange_pnl"]["status"] == "pending" for r in rows)


@pytest.mark.parametrize("pnl,expected", [("-5.2", -5.2), ("0", 0), (None, None), ("1.7976931348623157e308", None)])
def test_ibkr_joins_late_commission_report_to_execution_and_preserves_unknown(pnl, expected):
    events = [dict(exchange_fill_id="exec1", quantity=1, raw_json={}),
              dict(exchange_fill_id="exec1:commission", quantity=0, raw_json={"realizedPNL": pnl, "currency": "USD", "commission": 1})]
    report = websocket_report(events, expected=1, exchange="ibkr", currency="")
    assert (report["amount"] if report else None) == expected


def test_htx_order_total_must_not_be_inherited_by_each_fill():
    from app.services.execution_streams.normalizers import parse_htx
    payload = dict(topic="matchOrders.BTC-USDT", contract_code="BTC-USDT", order_id_str="o1", real_profit="10",
                   trade=[dict(id="f1", trade_volume=5, trade_price=100), dict(id="f2", trade_volume=5, trade_price=101)])
    parsed = parse_htx(payload, market_type="swap")
    assert len(parsed) == 2
    assert all(e.realized_pnl is None for e in parsed)
    legacy = [dict(exchange_fill_id="f1", quantity=1, raw_json=payload, realized_pnl=10)]
    assert websocket_report(legacy, expected=1, exchange="htx", currency="USDT") is None


@pytest.mark.parametrize("value", [None, 1.7976931348623157e308, float("nan"), 0, -2])
def test_ibkr_adapter_preserves_unset_vs_zero(value):
    from types import SimpleNamespace
    from app.services.execution_streams.events import ExecutionEvent
    from app.services.execution_streams.adapters import IBKRExecutionAdapter
    adapter = object.__new__(IBKRExecutionAdapter)
    adapter._events = {"exec1": ExecutionEvent(exchange_id="ibkr", market_type="usstock", symbol="AAPL", quantity=1)}
    adapter.credential_id, adapter.user_id = 1, 1
    received = []
    adapter.on_event = received.append
    adapter._on_commission(None, None, SimpleNamespace(execId="exec1", realizedPNL=value, commission=.1, currency="USD"))
    expected = value if value in (0, -2) else None
    assert received[0].realized_pnl == expected
    assert received[0].raw["realizedPNL"] == expected


def test_htx_v5_missing_pnl_uses_documented_order_detail(venue):
    venue.get_order_match_results.return_value = {"data": {"order_id": "o1", "trades": [{"id": "f1"}]}}
    venue.margin_mode = "isolated"
    venue._swap_private_request_raw.return_value = {"data": {"order_id": "o1", "trades": [dict(id="f1", trade_volume=10, real_profit="-7")]}}
    assert fetch(venue, "htx")["amount"] == -7
    assert venue._swap_private_request_raw.call_args.args == ("POST", "/linear-swap-api/v1/swap_order_detail")
