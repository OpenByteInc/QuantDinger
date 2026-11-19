import pytest

from app.services.strategy_marketplace_contract import (
    adapt_parameterized_source,
    compatibility_for_target,
    derive_strategy_contract,
)


PARAMETERIZED = '''
INSTRUMENT = "USStock:SPY"

def initialize(context):
    context.set_universe([INSTRUMENT])
    context.subscribe(frequency="1d")
    context.set_metadata(direction_mode="long_only")

def handle_data(context, data):
    bars = get_history(20, "1d", "close", INSTRUMENT)
    if len(bars):
        order_target_percent(INSTRUMENT, 1.0)
'''


def test_parameterized_binding_is_structurally_detected_and_adapted():
    contract = derive_strategy_contract(PARAMETERIZED)
    assert contract["binding_mode"] == "parameterized"
    assert contract["bound_instruments"] == ["USStock:SPY"]
    assert contract["direction_mode"] == "long_only"
    assert contract["execution_mode"] == "bar"
    assert contract["execution_frequency"] == "1d"
    assert contract["confirmation_frequencies"] == []

    compatibility = compatibility_for_target(contract, target_instrument="USStock:MSFT")
    assert compatibility["compatible"] is True
    assert compatibility["requires_rebacktest"] is True

    adapted = adapt_parameterized_source(PARAMETERIZED, contract, "USStock:MSFT")
    assert 'INSTRUMENT = \'USStock:MSFT\'' in adapted
    assert derive_strategy_contract(adapted)["bound_instruments"] == ["USStock:MSFT"]


def test_broker_metadata_does_not_change_marketplace_equity_identity(monkeypatch):
    from app.services import strategy_marketplace_contract as contract_module

    original_compile = contract_module.inspect_strategy_v2

    class _Program:
        manifest = type("_Manifest", (), {"metadata": lambda self: {
            **original_compile(PARAMETERIZED).manifest.metadata(),
            "universe": {
                "kind": "static",
                "reference": "",
                "instruments": [{
                    "market": "USStock",
                    "symbol": "SPY",
                    "exchange_id": "NASDAQ",
                    "market_type": "spot",
                }],
            },
        }})()

    monkeypatch.setattr(contract_module, "inspect_strategy_v2", lambda _code: _Program())

    contract = contract_module.derive_strategy_contract(PARAMETERIZED)

    assert contract["bound_instruments"] == ["USStock:SPY"]
    assert contract["binding_mode"] == "parameterized"


def test_literal_strategy_remains_fixed_and_cannot_claim_other_instrument():
    code = '''
def initialize(context):
    context.set_universe(["USStock:SPY"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    order_target_percent("USStock:SPY", 1.0)
'''
    contract = derive_strategy_contract(code)
    assert contract["binding_mode"] == "fixed"
    result = compatibility_for_target(contract, target_instrument="USStock:MSFT")
    assert result["compatible"] is False
    assert result["reason_codes"] == ["fixed_instrument_mismatch"]


def test_parameterized_contract_rejects_cross_market_and_market_type_changes():
    contract = derive_strategy_contract(PARAMETERIZED)
    result = compatibility_for_target(contract, target_instrument="Crypto:BTC/USDT@spot")
    assert result["compatible"] is False
    assert "market_mismatch" in result["reason_codes"]

    swap_code = PARAMETERIZED.replace("USStock:SPY", "Crypto:BTC/USDT@swap")
    swap_contract = derive_strategy_contract(swap_code)
    result = compatibility_for_target(swap_contract, target_instrument="Crypto:ETH/USDT@spot")
    assert result["compatible"] is False
    assert "market_type_mismatch" in result["reason_codes"]


def test_dynamic_universe_does_not_accept_an_individual_replacement():
    code = '''
def initialize(context):
    context.set_universe(pool="sp500")
    context.subscribe(frequency="1d")
    run_weekly(rebalance)

def rebalance(context, data):
    pass
'''
    contract = derive_strategy_contract(code)
    assert contract["binding_mode"] == "universe"
    assert contract["execution_mode"] == "scheduled"
    result = compatibility_for_target(contract, target_instrument="USStock:MSFT")
    assert result["compatible"] is False
    assert result["reason_codes"] == ["individual_target_not_supported"]


def test_adaptation_rejects_non_parameterized_source():
    fixed_code = '''
def initialize(context):
    context.set_universe(["USStock:SPY"])
    context.subscribe(frequency="1d")
def handle_data(context, data):
    pass
'''
    fixed = derive_strategy_contract(fixed_code)
    with pytest.raises(ValueError, match="strategyNotParameterized"):
        adapt_parameterized_source(PARAMETERIZED, fixed, "USStock:MSFT")


def test_multitimeframe_contract_separates_execution_and_confirmation_periods():
    code = '''
def initialize(context):
    context.set_universe(["Crypto:BTC/USDT"])
    context.subscribe(frequency="1h")
    context.subscribe(frequency="4h")
    context.subscribe(frequency="1d")

def handle_data(context, data):
    pass
'''
    contract = derive_strategy_contract(code)
    assert contract["execution_frequency"] == "1h"
    assert contract["confirmation_frequencies"] == ["4h", "1d"]
    # Strategy V2 compatibility aliases remain read-only and unchanged.
    assert contract["primary_frequency"] == "1h"
    assert contract["driving_frequency"] == "1h"
