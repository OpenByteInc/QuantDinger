from __future__ import annotations

from app.services.live_trading.base import LiveTradingError
from app.services.pending_orders import live_order_phases
from app.services.pending_orders.live_order_support import (
    FillAccumulator,
    LiveOrderRejected,
    build_live_order_context,
    make_client_order_id,
    signal_to_side_pos_reduce,
)


def test_make_client_order_id_okx_is_compact_alphanumeric():
    oid = make_client_order_id(exchange_id="okx", strategy_id=123456789, order_id=987654321, phase="lmt")

    assert oid.isalnum()
    assert len(oid) <= 32
    assert oid.startswith("qd")


def test_make_client_order_id_default_keeps_phase():
    assert make_client_order_id(exchange_id="bitget", strategy_id=12, order_id=34, phase="mkt") == "qd_12_34_mkt"


def test_signal_to_side_pos_reduce_exit_aliases():
    assert signal_to_side_pos_reduce("close_long_trailing") == ("sell", "long", True)
    assert signal_to_side_pos_reduce("close_short_profit") == ("buy", "short", True)
    assert signal_to_side_pos_reduce("open_short") == ("sell", "short", False)


def test_signal_to_side_pos_reduce_rejects_unknown():
    try:
        signal_to_side_pos_reduce("flip_sideways")
    except LiveTradingError as exc:
        assert "Unsupported signal_type" in str(exc)
    else:
        raise AssertionError("expected LiveTradingError")


def test_fill_accumulator_tracks_weighted_average_and_fee():
    fills = FillAccumulator()

    fills.apply_fill(0.1, 100)
    fills.apply_fill(0.2, 200)
    fills.apply_fee("-0.003", "USDT")
    fills.apply_fee(0.002, "BTC")

    assert round(fills.total_base, 8) == 0.3
    assert round(fills.total_quote, 8) == 50
    assert round(fills.avg_price(), 8) == round(50 / 0.3, 8)
    assert round(fills.total_fee, 8) == 0.005
    assert fills.fee_ccy == "USDT"


def test_maker_limit_price_offsets_buy_and_sell():
    assert live_order_phases.maker_limit_price(ref_price=100, side="buy", maker_offset=0.01) == 99
    assert live_order_phases.maker_limit_price(ref_price=100, side="sell", maker_offset=0.01) == 101

    try:
        live_order_phases.maker_limit_price(ref_price=0, side="buy", maker_offset=0.01)
    except LiveTradingError as exc:
        assert "missing_ref_price" in str(exc)
    else:
        raise AssertionError("expected LiveTradingError")

    try:
        live_order_phases.maker_limit_price(ref_price=100, side="hold", maker_offset=0.01)
    except LiveTradingError as exc:
        assert "unsupported_order_side" in str(exc)
    else:
        raise AssertionError("expected LiveTradingError")


def test_place_live_market_order_spot_buy_uses_quote_amount(monkeypatch):
    class FakeBitgetSpot:
        def place_market_order(self, **kwargs):
            self.kwargs = kwargs
            return type("Result", (), {"exchange_order_id": "1", "raw": kwargs})()

    monkeypatch.setattr(live_order_phases, "BitgetSpotClient", FakeBitgetSpot)
    client = FakeBitgetSpot()

    live_order_phases.place_live_market_order(
        client=client,
        symbol="BTC/USDT",
        side="buy",
        amount=0.01,
        reduce_only=False,
        pos_side="long",
        client_order_id="oid",
        market_type="spot",
        payload={},
        exchange_config={},
        leverage=1,
        ref_price=100,
        spot_quote_amt=25,
        spot_market_buy_uses_quote=True,
    )

    assert client.kwargs["size"] == 25
    assert client.kwargs["client_order_id"] == "oid"


def test_wait_live_order_fill_enforces_slow_exchange_limit_wait(monkeypatch):
    class FakeGateSpot:
        def wait_for_fill(self, **kwargs):
            self.kwargs = kwargs
            return {"filled": 1, "avg_price": 10, "fee": 0, "fee_ccy": "USDT"}

    monkeypatch.setattr(live_order_phases, "GateSpotClient", FakeGateSpot)
    client = FakeGateSpot()

    snapshot = live_order_phases.wait_live_order_fill(
        client=client,
        symbol="BTC/USDT",
        order_id="ex-1",
        client_order_id="client-1",
        market_type="spot",
        exchange_config={},
        max_wait_sec=1,
        phase="limit",
    )

    assert snapshot["filled"] == 1
    assert client.kwargs["max_wait_sec"] == 8.0


def test_wait_live_order_fill_rejects_unknown_client():
    try:
        live_order_phases.wait_live_order_fill(
            client=object(),
            symbol="BTC/USDT",
            order_id="ex-1",
            client_order_id="client-1",
            market_type="spot",
            exchange_config={},
            max_wait_sec=1,
            phase="limit",
        )
    except LiveTradingError as exc:
        assert "Unsupported client type" in str(exc)
    else:
        raise AssertionError("expected LiveTradingError")


def test_build_live_order_context_normalizes_and_validates():
    ctx = build_live_order_context(
        order_id=99,
        order_row={"strategy_id": 12, "symbol": "BTC/USDT", "signal_type": "open_long", "market_type": "futures"},
        payload={"amount": 0.01},
        load_strategy_configs=lambda strategy_id: {
            "user_id": 7,
            "market_category": "Crypto",
            "market_type": "futures",
            "trading_config": {"trade_direction": "long", "bot_type": "trend"},
            "exchange_config": {"exchange_id": "binance"},
        },
        resolve_exchange_config=lambda cfg, user_id: dict(cfg, credential_user_id=user_id),
        safe_exchange_config_for_log=lambda cfg: {"exchange_id": cfg.get("exchange_id")},
    )

    assert ctx.strategy_id == 12
    assert ctx.strategy_user_id == 7
    assert ctx.exchange_id == "binance"
    assert ctx.market_type == "swap"
    assert ctx.safe_exchange_config == {"exchange_id": "binance"}


def test_build_live_order_context_rejects_missing_symbol():
    try:
        build_live_order_context(
            order_id=99,
            order_row={"strategy_id": 12, "signal_type": "open_long"},
            payload={"amount": 0.01},
            load_strategy_configs=lambda strategy_id: {},
            resolve_exchange_config=lambda cfg, user_id: {},
            safe_exchange_config_for_log=lambda cfg: {},
        )
    except LiveOrderRejected as exc:
        assert exc.error == "missing_symbol_or_signal_type"
        assert exc.strategy_id == 12
        assert "missing symbol" in exc.console_message
    else:
        raise AssertionError("expected LiveOrderRejected")


def test_build_live_order_context_rejects_policy_violation():
    try:
        build_live_order_context(
            order_id=99,
            order_row={"strategy_id": 12, "symbol": "BTC/USDT", "signal_type": "open_long", "market_type": "swap"},
            payload={"amount": 0.01},
            load_strategy_configs=lambda strategy_id: {
                "user_id": 7,
                "market_category": "Crypto",
                "market_type": "swap",
                "trading_config": {"trade_direction": "long", "bot_type": "trend"},
                "exchange_config": {"exchange_id": "coinbaseexchange"},
            },
            resolve_exchange_config=lambda cfg, user_id: cfg,
            safe_exchange_config_for_log=lambda cfg: cfg,
        )
    except LiveOrderRejected as exc:
        assert exc.error.startswith("policy_violation:")
        assert exc.strategy_id == 12
    else:
        raise AssertionError("expected LiveOrderRejected")

