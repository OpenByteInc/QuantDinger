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
