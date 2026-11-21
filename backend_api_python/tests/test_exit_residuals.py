from dataclasses import replace

import pytest

from app.services.instrument_rules import InstrumentRules
from app.services.strategy_runtime.exit_residuals import evaluate_exit_residual


def _rules(*, step=0.001, minimum=0.001, notional=5.0):
    return InstrumentRules(
        key="binance:spot:DOGE/USDT",
        exchange_id="binance",
        market_type="spot",
        symbol="DOGE/USDT",
        amount_step=step,
        min_amount=minimum,
        min_notional=notional,
    )


def test_classifies_exchange_verified_quantity_below_minimum():
    decision = evaluate_exit_residual(
        local_size=0.0004,
        total_size=0.0004,
        available_size=0.0004,
        price=1.0,
        rules=_rules(),
    )
    assert decision.classified is True
    assert decision.reason == "below_min_amount"
    assert decision.tradable_size == 0


def test_classifies_exchange_verified_quantity_below_minimum_notional():
    decision = evaluate_exit_residual(
        local_size=10.0,
        total_size=10.0,
        available_size=10.0,
        price=0.1,
        rules=_rules(step=1.0, minimum=1.0, notional=5.0),
    )
    assert decision.classified is True
    assert decision.reason == "below_min_notional"
    assert decision.tradable_size == 10.0


def test_does_not_classify_locked_or_shared_inventory():
    locked = evaluate_exit_residual(
        local_size=10.0,
        total_size=10.0,
        available_size=9.0,
        price=1.0,
        rules=_rules(step=1.0, minimum=1.0, notional=1.0),
    )
    shared = evaluate_exit_residual(
        local_size=10.0,
        total_size=15.0,
        available_size=15.0,
        price=1.0,
        rules=_rules(step=1.0, minimum=1.0, notional=1.0),
    )
    assert locked.classified is False
    assert shared.classified is False


def test_does_not_classify_when_residual_is_still_tradable():
    decision = evaluate_exit_residual(
        local_size=10.0,
        total_size=10.0,
        available_size=10.0,
        price=1.0,
        rules=_rules(step=1.0, minimum=1.0, notional=5.0),
    )
    assert decision.classified is False
    assert decision.tradable_size == 10.0


@pytest.mark.parametrize("changes", [
    {"available_size": 0}, {"available_size": 0.0002}, {"available_size": 0.0006},
    {"total_size": 0.0005}, {"total_size": float("nan")},
    {"available_size": float("inf")}, {"price": float("nan")},
    {"available_size": None}, {"price": "unknown"},
    {"rules": _rules(step=float("inf"))},
    {"rules": replace(_rules(), market_type="swap")},
    {"rules": _rules(step=0, minimum=0, notional=0)},
])
def test_small_inventory_requires_consistent_snapshot_and_real_rules(changes):
    values = {"local_size": 0.0004, "total_size": 0.0004, "available_size": 0.0004,
              "price": 1, "rules": _rules()}
    values.update(changes)
    assert evaluate_exit_residual(**values).classified is False


def test_crypto_is_not_subject_to_a_fixed_share_threshold():
    decision = evaluate_exit_residual(
        local_size=0.001, total_size=0.001, available_size=0.001, price=60000,
        rules=_rules(step=0.00001, minimum=0.00001, notional=5),
    )
    assert decision.classified is False
