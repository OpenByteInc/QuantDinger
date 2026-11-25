"""Regression tests: order instructions are not execution evidence."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.fill_evidence import binance_execution_average
from app.services.live_trading.fill_recovery import try_recover_zero_fill
from app.services.grid.fill_units import extract_grid_fill_avg_price
from app.services.grid.exchange_orders import _parse_grid_order_fill
from app.services.grid.fill_handler import apply_grid_fill_to_local_state
from app.services.pending_orders.fill_records import _persist_strategy_fill
from app.utils.trade_execution import enrich_execution_reference


@pytest.mark.parametrize("average", [None, "", "0", "NaN", "Infinity"])
def test_order_limit_price_cannot_be_used_as_execution(average):
    raw = {"executedQty": "2", "avgPrice": average, "price": "100", "status": "FILLED"}
    assert binance_execution_average(raw) == 0
    assert extract_grid_fill_avg_price(None, data=raw, filled_base=2) == 0
    assert _parse_grid_order_fill(raw)[1] == 0


def test_binance_wait_returns_missing_average_then_actual_execution():
    client = BinanceFuturesClient(api_key="test", secret_key="test")
    raw = {"executedQty": "2", "price": "100", "status": "FILLED"}
    with patch.object(client, "get_order", side_effect=[raw, dict(raw, cumQuote="198")]), patch.object(
        client, "_fetch_commission_for_order", return_value=(0, "", {})
    ):
        missing = client.wait_for_fill(symbol="BTC/USDT", order_id="1", max_wait_sec=0)
        actual = client.wait_for_fill(symbol="BTC/USDT", order_id="1", max_wait_sec=0)
    assert (missing["filled"], missing["avg_price"]) == (2, 0)
    assert (actual["filled"], actual["avg_price"]) == (2, 99)


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf")])
def test_pending_fill_without_real_price_cannot_mutate_positions_or_ledger(price):
    with patch("app.services.pending_orders.fill_records.apply_fill_to_local_position") as position, patch(
        "app.services.pending_orders.fill_records.record_trade"
    ) as record:
        with pytest.raises(LiveTradingError, match="fillSnapshotNotReady"):
            _persist_strategy_fill(strategy_id=1, symbol="BTC/USDT", signal_type="open_long",
                                   filled=1, avg_price=price, exchange_config={}, market_type="spot")
    position.assert_not_called()
    record.assert_not_called()


@pytest.mark.parametrize("quantity,price", [(1, 0), (0, 100), (1, float("nan"))])
def test_grid_request_price_and_quantity_never_replace_missing_execution(quantity, price):
    order = SimpleNamespace(purpose="long_entry", price=100, quantity=1)
    with patch("app.services.grid.fill_handler.record_trade") as record:
        with pytest.raises(LiveTradingError, match="fillSnapshotNotReady"):
            apply_grid_fill_to_local_state(1, "BTC/USDT", order, quantity, price, {})
    record.assert_not_called()


def test_recovery_waits_for_price_instead_of_copying_reference():
    with patch("app.services.live_trading.fill_recovery.query_grid_order_fill", return_value=(1, 0, "filled")):
        result = try_recover_zero_fill(None, symbol="BTC/USDT", market_type="spot", exchange_config={},
            exchange_order_id="1", client_order_id="", requested_qty=1, signal_type="open_long",
            pos_side="long", pre_position_qty=0, ref_price=100)
    assert result == (0, 0, "")


@pytest.mark.parametrize("action,actual,expected", [
    ("open_long", 101, 1), ("close_long", 101, -1),
    ("open_short", 99, 1), ("close_short", 99, -1),
])
def test_reference_comparison_does_not_change_fill_price(action, actual, expected):
    result = enrich_execution_reference({"price": actual, "type": action,
        "request_payload": '{"ref_price":100,"limit_price":102,"secret":"not-exposed"}', "request_price":102})
    assert result["price"] == actual
    assert result["reference_price"] == 100
    assert result["reference_kind"] == "signal"
    assert result["price_deviation_pct"] == pytest.approx(expected)
    assert "request_payload" not in result
    assert "request_price" not in result


def test_grid_limit_reference_is_explicit_and_legacy_reference_stays_unknown():
    result = enrich_execution_reference({"price": 99, "type": "open_long", "grid_request_price": 100})
    assert result["reference_kind"] == "limit"
    assert result["price_deviation_pct"] == pytest.approx(-1)
    for payload in (None, "broken", "[]", '{"ref_price":"NaN"}'):
        result = enrich_execution_reference({"price": 99, "request_payload": payload})
        assert result["reference_price"] is None
        assert result["price_deviation_pct"] is None


@pytest.mark.parametrize("actual", [0, 99])
def test_alpaca_worker_keeps_reference_separate_from_execution(actual, equity_market_clock):
    from unittest.mock import MagicMock
    from app.services import pending_order_worker as module
    worker = object.__new__(module.PendingOrderWorker)
    worker._mark_sent = MagicMock()
    worker._mark_failed = MagicMock()
    client = MagicMock()
    client.place_market_order.return_value = SimpleNamespace(
        success=True, filled=1, avg_price=actual, order_id="alpaca-1", status="filled", raw={})
    with patch.object(module, "persist_strategy_fill", return_value=(None, None)) as persist, patch.object(
        module, "append_strategy_log"
    ), patch("app.services.live_trading.fee_quote.fee_to_quote", return_value=None):
        worker._execute_alpaca_order_locked(order_id=1, order_row={},
            payload={"signal_type":"open_long", "symbol":"AAPL", "amount":1, "ref_price":100},
            client=client, strategy_id=1, exchange_config={}, market_category="USStock",
            _notify_live_best_effort=MagicMock(), _console_print=MagicMock())
    worker._mark_failed.assert_not_called()
    sent = worker._mark_sent.call_args.kwargs
    assert sent["avg_price"] == actual
    assert sent["final_filled"] == (actual > 0)
    if actual:
        assert persist.call_args.kwargs["avg_price"] == actual
    else:
        persist.assert_not_called()


@pytest.mark.parametrize("placed,observed,expected", [
    ((1,100),(2,0),(2,0)), ((2,105),(1,100),(2,105)),
    ((1,100),(1,0),(1,100)), ((1,100),(2,105),(2,105)),
])
def test_execution_snapshots_cannot_mix_quantity_and_average(placed, observed, expected):
    from app.services.live_trading.executors import _execution_pair
    result = SimpleNamespace(filled=placed[0], avg_price=placed[1])
    snapshot = SimpleNamespace(filled_qty=observed[0], avg_price=observed[1])
    assert _execution_pair(result, snapshot) == expected


def test_composite_order_average_stays_unknown_if_one_executed_leg_has_no_price():
    from app.services.live_trading.executors import _weighted_avg
    assert _weighted_avg((1,100), (1,0)) == 0
    assert _weighted_avg((1,100), (0,0)) == 100
    assert _weighted_avg((1,100), (1,110)) == 105
