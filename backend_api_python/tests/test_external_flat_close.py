"""Tests for exchange-native flat close trade backfill."""

from datetime import datetime, timezone
from contextlib import nullcontext

import pytest

from app.services.live_trading import external_flat_close as mod
from app.utils.trade_close_reason import EXCHANGE_NATIVE_CLOSE


@pytest.fixture(autouse=True)
def isolated_accounting(monkeypatch):
    monkeypatch.setattr(mod, "get_db_transaction", nullcontext)
    monkeypatch.setattr(mod, "lock_strategy_fills", lambda *_: None)
    monkeypatch.setattr(mod, "_pending_close_orders", lambda **_: [])
    monkeypatch.setattr(mod, "_already_recorded_fill", lambda *_: False)
    monkeypatch.setattr(mod, "_fetch_position", lambda *_: {})
    monkeypatch.setattr(mod, "_active_strategy_run_id", lambda *_: 0)


class _FakeOkx:
    def __init__(self, history=None, fills=None):
        self.history = history or []
        self.fills = fills or []

    def _signed_request(self, method, path, params=None):
        if path.endswith("/account/positions-history"):
            return {"data": self.history}
        if path.endswith("/trade/fills-history"):
            return {"data": self.fills}
        return {"data": []}


def test_resolve_okx_external_close_matches_entry_and_fee(monkeypatch):
    monkeypatch.setattr(mod, "OkxClient", _FakeOkx)
    opened = datetime(2026, 9, 12, 8, 0, 0, tzinfo=timezone.utc)
    closed_ms = int(datetime(2026, 9, 14, 2, 12, 23, tzinfo=timezone.utc).timestamp() * 1000)
    client = _FakeOkx(
        history=[
            {
                "posSide": "short",
                "direction": "long",
                "openAvgPx": "77240",
                "closeAvgPx": "77348.6",
                "uTime": str(closed_ms),
                "type": "2",
            }
        ],
        fills=[
            {
                "side": "buy",
                "posSide": "short",
                "fillPx": "77348.6",
                "fee": "-0.00773486",
                "feeCcy": "USDT",
                "tradeId": "2900408350",
                "ts": str(closed_ms),
            }
        ],
    )
    # isinstance checks OkxClient; patch the class used in isinstance.
    assert isinstance(client, _FakeOkx)
    monkeypatch.setattr(mod, "OkxClient", _FakeOkx)

    snap = mod.resolve_okx_external_close(
        client,
        symbol="BTC/USDT",
        side="short",
        entry_price=77240.0,
        opened_after=opened,
    )
    assert snap is not None
    assert abs(float(snap["price"]) - 77348.6) < 1e-9
    assert abs(float(snap["fee"]) - 0.00773486) < 1e-9
    assert snap["exchange_fill_id"] == "2900408350"
    assert snap["close_reason"] == EXCHANGE_NATIVE_CLOSE


def test_record_external_flat_close_writes_trade_and_clears_local(monkeypatch):
    recorded = {}
    applied = {}

    monkeypatch.setattr(mod, "_already_recorded_fill", lambda *a, **k: False)
    monkeypatch.setattr(
        mod,
        "_fetch_position",
        lambda strategy_id, symbol, side: {"size": 0.0002, "entry_price": 77240.0},
    )

    def _apply(**kwargs):
        applied.update(kwargs)
        return (-0.02172, None, 77240.0)

    monkeypatch.setattr(mod, "apply_fill_to_local_position", _apply)

    def _record(**kwargs):
        recorded.update(kwargs)
        return 71

    monkeypatch.setattr(mod, "record_trade", _record)
    monkeypatch.setattr(mod, "append_strategy_log", lambda *a, **k: None)

    trade_id = mod.record_external_flat_close(
        strategy_id=32,
        symbol="BTC/USDT",
        side="short",
        amount=0.0002,
        entry_price=77240.0,
        close_price=77348.6,
        commission=0.00773486,
        close_reason=EXCHANGE_NATIVE_CLOSE,
        exchange_fill_id="2900408350",
        exchange_order_id="close-order",
        clear_local_position=True,
    )
    assert trade_id == 71
    assert applied["signal_type"] == "close_short"
    assert recorded["trade_type"] == "close_short"
    assert abs(float(recorded["profit"]) - ((77240.0 - 77348.6) * 0.0002)) < 1e-9
    assert recorded["close_reason"] == EXCHANGE_NATIVE_CLOSE
    assert recorded["exchange_order_id"] == "close-order"


def test_reconcile_defers_without_exchange_snapshot(monkeypatch):
    monkeypatch.setattr(mod, "trade_side_net_qty", lambda *a, **k: 0.0002)
    monkeypatch.setattr(
        mod,
        "_last_open_trade",
        lambda *a, **k: {
            "price": 77240.0,
            "created_at": datetime(2026, 9, 12, 8, 0, 0),
            "credential_id": 5,
            "inst_id": "BTC-USDT-SWAP",
            "market_type": "swap",
        },
    )
    monkeypatch.setattr(mod, "_leg_owner_count", lambda **_kwargs: 1)
    monkeypatch.setattr(mod, "resolve_exchange_external_close", lambda *a, **k: None)
    monkeypatch.setattr(
        mod,
        "record_external_flat_close",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should defer")),
    )

    trade_id = mod.reconcile_external_flat_closes(
        strategy_id=32,
        symbol="BTC/USDT",
        side="short",
        client=None,
        fallback_price=0.0,
        clear_local_position=False,
    )
    assert trade_id == 0


def test_resolve_falls_back_to_fills_history(monkeypatch):
    monkeypatch.setattr(mod, "OkxClient", _FakeOkx)
    opened = datetime(2026, 9, 14, 16, 0, 0, tzinfo=timezone.utc)
    closed_ms = int(datetime(2026, 9, 14, 22, 4, 0, tzinfo=timezone.utc).timestamp() * 1000)
    client = _FakeOkx(
        history=[],
        fills=[
            {
                "side": "sell",
                "posSide": "long",
                "fillPx": "78657.8",
                "fee": "-0.00786578",
                "feeCcy": "USDT",
                "tradeId": "2919999999",
                "ts": str(closed_ms),
            }
        ],
    )
    snap = mod.resolve_okx_external_close(
        client,
        symbol="BTC/USDT",
        side="long",
        entry_price=78552.9,
        opened_after=opened,
    )
    assert snap is not None
    assert abs(float(snap["price"]) - 78657.8) < 1e-9
    assert snap["source"] == "fills_history"
    assert snap["close_reason"] == EXCHANGE_NATIVE_CLOSE


def test_reconcile_records_each_exact_fill_component(monkeypatch):
    monkeypatch.setattr(mod, "trade_side_net_qty", lambda *a, **k: 1.0)
    monkeypatch.setattr(
        mod,
        "_last_open_trade",
        lambda *a, **k: {
            "price": 100.0,
            "created_at": datetime(2026, 10, 15, 1, 0, 0),
            "credential_id": 5,
            "inst_id": "BTC-USDT-SWAP",
            "market_type": "swap",
        },
    )
    monkeypatch.setattr(mod, "_leg_owner_count", lambda **_kwargs: 1)
    closed_at = datetime(2026, 10, 15, 2, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        mod,
        "resolve_exchange_external_close",
        lambda *a, **k: {
            "components": [
                {"amount": 0.4, "price": 105.0, "fee": 0.04, "fee_ccy": "USDT", "exchange_fill_id": "f1", "closed_at": closed_at},
                {"amount": 0.6, "price": 106.0, "fee": 0.06, "fee_ccy": "USDT", "exchange_fill_id": "f2", "closed_at": closed_at},
            ]
        },
    )
    recorded = []
    monkeypatch.setattr(
        mod,
        "record_external_flat_close",
        lambda **kwargs: recorded.append(kwargs) or len(recorded),
    )

    trade_id = mod.reconcile_external_flat_closes(
        strategy_id=32,
        symbol="BTC/USDT",
        side="long",
        client=object(),
    )

    assert trade_id == 2
    assert [row["amount"] for row in recorded] == [0.4, 0.6]
    assert [row["exchange_fill_id"] for row in recorded] == ["f1", "f2"]


def test_reconcile_defers_multi_owner_account_leg(monkeypatch):
    monkeypatch.setattr(mod, "trade_side_net_qty", lambda *a, **k: 1.0)
    monkeypatch.setattr(
        mod,
        "_last_open_trade",
        lambda *a, **k: {"price": 100.0, "credential_id": 5, "market_type": "swap"},
    )
    monkeypatch.setattr(mod, "_leg_owner_count", lambda **_kwargs: 2)
    monkeypatch.setattr(
        mod,
        "resolve_exchange_external_close",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ambiguous leg must not query fills")),
    )

    assert mod.reconcile_external_flat_closes(
        strategy_id=32,
        symbol="BTC/USDT",
        side="long",
        client=object(),
    ) == 0


@pytest.mark.parametrize("status", ["sent", "syncing", "processing", "filled", "cancelled", "failed"])
def test_system_close_identity_stays_owned_by_pending_writer(status):
    orders = [{"status": status, "exchange_order_id": "broker-order", "client_order_id": "qd_1100_196924"}]
    assert mod._has_managed_close(orders, [{"exchange_order_id": "broker-order"}])


def test_unacknowledged_submission_blocks_external_backfill():
    assert mod._has_managed_close([{"status": "processing", "exchange_order_id": ""}], [])


def test_client_id_only_matches_without_conflicting_broker_identity():
    component = {"exchange_order_id": "old-broker-order", "client_order_id": "qd_1100_232107"}
    assert mod._has_managed_close([{"status": "filled", "client_order_id": component["client_order_id"]}], [component])
    assert not mod._has_managed_close([
        {"status": "filled", "client_order_id": component["client_order_id"], "exchange_order_id": "new-broker-order"}
    ], [component])
    assert not mod._has_managed_close([{"status": "filled", "id": 232107}], [component])


def test_completed_managed_fill_does_not_hide_remaining_external_close():
    from app.services.live_trading.exchange_close_history import _select_exact_close

    closed = datetime(2026, 11, 4, 14, 30, tzinfo=timezone.utc)
    managed = {"exchange_order_id": "system-order", "status": "filled"}
    rows = [
        {"amount": 0.6, "price": 160, "closed_at": closed, "exchange_fill_id": "activity-1", "exchange_order_id": "system-order"},
        {"amount": 0.4, "price": 161, "closed_at": closed, "exchange_fill_id": "activity-2", "exchange_order_id": "manual-order"},
    ]
    snapshot = _select_exact_close(
        rows, target_qty=0.4, opened_after=None, is_recorded=lambda _: False,
        is_managed_order=lambda row: mod._has_managed_close([managed], [row]),
    )
    assert snapshot["components"] == [rows[1]]


def test_managed_close_appearing_during_history_read_prevents_any_backfill(monkeypatch):
    monkeypatch.setattr(mod, "trade_side_net_qty", lambda *a, **k: 1.0)
    monkeypatch.setattr(mod, "_last_open_trade", lambda *a, **k: {"price": 100, "credential_id": 5})
    monkeypatch.setattr(mod, "_leg_owner_count", lambda **_: 1)
    snapshots = iter([[], [{"status": "filled", "exchange_order_id": "system-order"}]])
    monkeypatch.setattr(mod, "_pending_close_orders", lambda **_: next(snapshots))
    monkeypatch.setattr(mod, "resolve_exchange_external_close", lambda *a, **k: {"components": [
        {"amount": 0.4, "price": 105, "exchange_fill_id": "external-fill", "exchange_order_id": "manual-order"},
        {"amount": 0.6, "price": 106, "exchange_fill_id": "system-fill", "exchange_order_id": "system-order"},
    ]})
    monkeypatch.setattr(mod, "record_external_flat_close", lambda **_: pytest.fail("must defer the entire batch"))
    assert mod.reconcile_external_flat_closes(strategy_id=32, symbol="BTC/USDT", side="long") == 0
