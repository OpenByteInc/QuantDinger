"""Tests for exchange-native flat close trade backfill."""

from datetime import datetime, timezone

from app.services.live_trading import external_flat_close as mod
from app.utils.trade_close_reason import EXCHANGE_NATIVE_CLOSE


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
        clear_local_position=True,
    )
    assert trade_id == 71
    assert applied["signal_type"] == "close_short"
    assert recorded["trade_type"] == "close_short"
    assert abs(float(recorded["profit"]) - ((77240.0 - 77348.6) * 0.0002)) < 1e-9
    assert recorded["close_reason"] == EXCHANGE_NATIVE_CLOSE


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
    monkeypatch.setattr(mod, "resolve_okx_external_close", lambda *a, **k: None)
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
