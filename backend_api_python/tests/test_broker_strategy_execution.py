from types import SimpleNamespace
from contextlib import nullcontext

import pytest

from app.services import pending_order_worker as worker_module
from app.services.live_trading.base import LiveTradingError


pytestmark = pytest.mark.usefixtures("equity_market_clock")


def _worker():
    worker = object.__new__(worker_module.PendingOrderWorker)
    worker.sent = []
    worker.failed = []
    worker._mark_sent = lambda **kwargs: worker.sent.append(kwargs)
    worker._mark_failed = lambda **kwargs: worker.failed.append(kwargs)
    return worker


def _result(**raw):
    return SimpleNamespace(
        success=True,
        filled=0.0,
        avg_price=0.0,
        order_id="broker-1",
        status="Submitted",
        message="",
        raw=raw,
    )


def test_broker_order_type_is_fail_closed_for_maker_then_market():
    with pytest.raises(LiveTradingError, match="maker_then_market"):
        worker_module._broker_order_type({"order_type": "maker_then_market"}, 100)


def test_ibkr_strategy_limit_order_is_not_downgraded_to_market(monkeypatch):
    calls = []

    class Client:
        def place_limit_order(self, **kwargs):
            calls.append(kwargs)
            return _result()

    worker = _worker()
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)
    worker._execute_ibkr_order(
        order_id=10,
        order_row={},
        payload={
            "signal_type": "open_short",
            "symbol": "AAPL",
            "amount": 2,
            "order_type": "limit",
            "limit_price": 201.25,
        },
        client=Client(),
        strategy_id=3,
        exchange_config={},
        _notify_live_best_effort=lambda **kwargs: None,
        _console_print=lambda message: None,
    )

    assert not worker.failed
    assert calls[0]["side"] == "sell"
    assert calls[0]["price"] == 201.25
    assert worker.sent[0]["exchange_id"] == "ibkr"


def test_ibkr_strategy_entry_uses_native_bracket(monkeypatch):
    calls = []

    class Client:
        def place_bracket_order(self, **kwargs):
            calls.append(kwargs)
            return _result()

    worker = _worker()
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)
    worker._execute_ibkr_order(
        order_id=11,
        order_row={},
        payload={
            "signal_type": "open_long",
            "symbol": "AAPL",
            "amount": 2,
            "ref_price": 100,
            "protection": {"stop_loss_pct": 0.02, "take_profit_pct": 0.05},
        },
        client=Client(),
        strategy_id=3,
        exchange_config={"native_stock_protection": True},
        _notify_live_best_effort=lambda **kwargs: None,
        _console_print=lambda message: None,
    )

    assert calls[0]["stop_loss_price"] == pytest.approx(98)
    assert calls[0]["take_profit_price"] == pytest.approx(105)


def test_ibkr_fractional_entry_keeps_runtime_protection(monkeypatch):
    calls = []

    class Client:
        def place_market_order(self, **kwargs):
            calls.append(kwargs)
            return _result()

        def place_bracket_order(self, **_kwargs):
            pytest.fail("fractional stock quantity must not use a native bracket")

    worker = _worker()
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)
    worker._execute_ibkr_order(
        order_id=14,
        order_row={},
        payload={
            "signal_type": "open_long",
            "symbol": "AAPL",
            "amount": 2.5,
            "ref_price": 100,
            "protection": {"stop_loss_pct": 0.02},
        },
        client=Client(),
        strategy_id=3,
        exchange_config={},
        _notify_live_best_effort=lambda **kwargs: None,
        _console_print=lambda message: None,
    )

    assert calls[0]["quantity"] == pytest.approx(2.5)


def test_ibkr_stock_entry_defaults_to_runtime_protection(monkeypatch):
    calls = []

    class Client:
        def place_market_order(self, **kwargs):
            calls.append(kwargs)
            return _result()

        def place_bracket_order(self, **_kwargs):
            pytest.fail("native stock protection must be explicitly enabled")

    worker = _worker()
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)
    worker._execute_ibkr_order(
        order_id=16,
        order_row={},
        payload={
            "signal_type": "open_long",
            "symbol": "AAPL",
            "amount": 2,
            "ref_price": 100,
            "protection": {"stop_loss_pct": 0.02},
        },
        client=Client(),
        strategy_id=3,
        exchange_config={},
        _notify_live_best_effort=lambda **kwargs: None,
        _console_print=lambda message: None,
    )

    assert calls[0]["quantity"] == pytest.approx(2)


def test_alpaca_fractional_entry_keeps_runtime_protection(monkeypatch):
    from app.services.live_trading import alpaca_ownership, records
    from app.services.live_trading.position_ownership import calculate_position_ownership

    calls = []
    monkeypatch.setattr(alpaca_ownership, "alpaca_account_lock", lambda credential: nullcontext())
    monkeypatch.setattr(alpaca_ownership, "ensure_alpaca_settled", lambda **kwargs: None)
    monkeypatch.setattr(alpaca_ownership, "list_strategy_allocations_for_account", lambda **kwargs: [])
    monkeypatch.setattr(records, "_get_user_id_from_strategy", lambda sid: 7)
    monkeypatch.setattr(
        alpaca_ownership,
        "evaluate_and_record_ownership",
        lambda **kwargs: calculate_position_ownership(
            **{key: kwargs[key] for key in ("symbol", "side", "account_qty", "strategy_qty")}
        ),
    )

    class Client:
        def get_positions(self, *, raise_on_error):
            return []

        def get_orders(self, *, status, limit, raise_on_error):
            return []

        def place_market_order(self, **kwargs):
            calls.append(kwargs)
            return _result()

    worker = _worker()
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)
    worker._execute_alpaca_order(
        order_id=15,
        order_row={},
        payload={
            "signal_type": "open_long",
            "symbol": "AAPL",
            "amount": 2.5,
            "ref_price": 100,
            "protection": {"stop_loss_pct": 0.02},
        },
        client=Client(),
        strategy_id=3,
        exchange_config={"exchange_id": "alpaca", "credential_id": 33},
        market_category="USStock",
        _notify_live_best_effort=lambda **kwargs: None,
        _console_print=lambda message: None,
    )

    assert calls[0]["quantity"] == pytest.approx(2.5)
    assert "stop_loss_price" not in calls[0]


def test_alpaca_equity_short_limit_is_supported(monkeypatch):
    from app.services.live_trading import alpaca_ownership, records
    from app.services.live_trading.position_ownership import calculate_position_ownership

    calls = []
    snapshots = []
    monkeypatch.setattr(alpaca_ownership, "alpaca_account_lock", lambda credential: nullcontext())
    monkeypatch.setattr(alpaca_ownership, "ensure_alpaca_settled", lambda **kwargs: None)
    monkeypatch.setattr(alpaca_ownership, "list_strategy_allocations_for_account", lambda **kwargs: [])
    monkeypatch.setattr(records, "_get_user_id_from_strategy", lambda sid: 7)
    monkeypatch.setattr(
        alpaca_ownership,
        "evaluate_and_record_ownership",
        lambda **kwargs: calculate_position_ownership(
            **{key: kwargs[key] for key in ("symbol", "side", "account_qty", "strategy_qty")}
        ),
    )

    class Client:
        def get_positions(self, *, raise_on_error):
            assert raise_on_error
            snapshots.append("positions")
            return []

        def get_orders(self, *, status, limit, raise_on_error):
            assert status == "open" and raise_on_error
            snapshots.append("orders")
            return []

        def place_limit_order(self, **kwargs):
            assert snapshots == ["positions", "orders"]
            calls.append(kwargs)
            return _result()

    worker = _worker()
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)
    worker._execute_alpaca_order(
        order_id=12,
        order_row={},
        payload={
            "signal_type": "open_short",
            "symbol": "AAPL",
            "amount": 2,
            "order_type": "limit",
            "limit_price": 200,
        },
        client=Client(),
        strategy_id=3,
        exchange_config={"exchange_id": "alpaca", "credential_id": 33},
        market_category="USStock",
        _notify_live_best_effort=lambda **kwargs: None,
        _console_print=lambda message: None,
    )

    assert not worker.failed
    assert calls[0]["side"] == "sell"
    assert calls[0]["market_type"] == "USStock"


def test_alpaca_crypto_short_is_rejected_before_submission(monkeypatch):
    class Client:
        def place_market_order(self, **kwargs):
            pytest.fail("crypto short must not be submitted")

    worker = _worker()
    monkeypatch.setattr(worker_module, "append_strategy_log", lambda *args, **kwargs: None)
    worker._execute_alpaca_order(
        order_id=13,
        order_row={},
        payload={"signal_type": "open_short", "symbol": "BTC/USD", "amount": 0.1},
        client=Client(),
        strategy_id=3,
        exchange_config={},
        market_category="Crypto",
        _notify_live_best_effort=lambda **kwargs: None,
        _console_print=lambda message: None,
    )

    assert worker.failed[0]["error"] == "alpaca_crypto_short_not_supported"
