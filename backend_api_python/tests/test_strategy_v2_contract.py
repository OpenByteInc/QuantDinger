import pytest

from app.services.strategy_v2 import StrategyV2ContractError, compile_strategy_v2, parse_instrument


def test_dataframe_result_cannot_be_used_as_a_boolean_condition():
    code = '''
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1m")

def handle_data(context, data):
    bars = get_history(10, "1m", "close", "USStock:AAPL")
    if not bars:
        return
'''
    with pytest.raises(StrategyV2ContractError, match="strategyV2.dataframeTruthAmbiguous"):
        compile_strategy_v2(code)


def test_dataframe_result_explicit_length_check_is_allowed():
    code = '''
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1m")

def handle_data(context, data):
    bars = get_history(10, "1m", "close", "USStock:AAPL")
    if len(bars) == 0:
        return
'''
    assert compile_strategy_v2(code).manifest.primary_frequency == "1m"


def test_contract_rejects_symbol_in_get_history_count_position():
    code = '''
def initialize(context):
    context.set_universe(["Crypto:ZEC/USDT@swap"])
    context.subscribe(frequency="30m")

def handle_data(context, data):
    symbol = "Crypto:ZEC/USDT@swap"
    get_history(symbol, "30m", ["close"], [symbol])
'''
    with pytest.raises(StrategyV2ContractError, match="strategyV2.apiCallInvalid:get_history:expectedCountFirst"):
        compile_strategy_v2(code)


def test_contract_rejects_reversed_data_history_arguments():
    code = '''
def initialize(context):
    context.set_universe(["Crypto:ZEC/USDT@spot"])
    context.subscribe(frequency="30m")

def handle_data(context, data):
    symbol = "Crypto:ZEC/USDT@spot"
    data.history(200, symbol, ["close", "high", "low"])
'''
    with pytest.raises(StrategyV2ContractError, match="strategyV2.apiCallInvalid:data.history:expectedSymbolsThenCount"):
        compile_strategy_v2(code)


def test_contract_rejects_context_passed_to_global_order_helper():
    code = '''
def initialize(context):
    context.set_universe(["Crypto:ZEC/USDT@spot"])
    context.subscribe(frequency="30m")

def handle_data(context, data):
    symbol = "Crypto:ZEC/USDT@spot"
    order_target_percent(context, 1.0, symbol)
'''
    with pytest.raises(StrategyV2ContractError, match="strategyV2.apiCallInvalid:order_target_percent:expectedSymbolAndValue"):
        compile_strategy_v2(code)


def test_contract_rejects_symbol_index_on_single_history_dataframe():
    code = '''
def initialize(context):
    context.set_universe(["Crypto:ZEC/USDT@spot"])
    context.subscribe(frequency="30m")

def handle_data(context, data):
    symbol = "Crypto:ZEC/USDT@spot"
    history_data = get_history(
        count=200,
        frequency="30m",
        field=["close", "high", "low"],
        security_list=[symbol],
    )
    close = history_data[symbol]["close"]
'''
    with pytest.raises(StrategyV2ContractError, match="strategyV2.apiCallInvalid:history:singleSymbolResultIsDataFrame"):
        compile_strategy_v2(code)


def test_contract_rejects_plural_fields_keyword_for_get_history():
    code = '''
def initialize(context):
    context.set_universe(["Crypto:ZEC/USDT@spot"])
    context.subscribe(frequency="30m")

def handle_data(context, data):
    get_history(
        count=200,
        frequency="30m",
        fields=["close", "high", "low"],
        security_list=["Crypto:ZEC/USDT@spot"],
    )
'''
    with pytest.raises(StrategyV2ContractError, match="strategyV2.apiCallInvalid:get_history:unsupportedArgument:fields"):
        compile_strategy_v2(code)


def test_contract_rejects_chained_symbol_index_on_single_history_dataframe():
    code = '''
def initialize(context):
    g.symbol = "Crypto:ZEC/USDT@spot"
    context.set_universe([g.symbol])
    context.subscribe(frequency="30m")

def handle_data(context, data):
    bars = get_history(
        80,
        frequency="30m",
        field=None,
        security_list=[g.symbol],
    )[g.symbol]
'''
    with pytest.raises(StrategyV2ContractError, match="strategyV2.apiCallInvalid:history:singleSymbolResultIsDataFrame"):
        compile_strategy_v2(code)


def test_contract_accepts_canonical_history_and_order_calls():
    code = '''
def initialize(context):
    g.symbol = "Crypto:ZEC/USDT@spot"
    context.set_universe([g.symbol])
    context.subscribe(frequency="30m")

def handle_data(context, data):
    bars = data.history(g.symbol, count=200, fields=["close", "high", "low"])
    if len(bars) < 20:
        return
    order_target_percent(g.symbol, 1.0, reason="entry")
'''
    assert compile_strategy_v2(code).manifest.primary_frequency == "30m"


@pytest.mark.parametrize(
    "invalid_access",
    [
        '"amount" in position',
        'position["amount"] > 0',
        'position.get("amount", 0) > 0',
    ],
)
def test_contract_rejects_dictionary_access_on_position_object(invalid_access):
    code = f'''
def initialize(context):
    context.set_universe(["Crypto:ZEC/USDT@spot"])
    context.subscribe(frequency="30m")

def handle_data(context, data):
    position = get_position("Crypto:ZEC/USDT@spot")
    if {invalid_access}:
        return
'''
    with pytest.raises(StrategyV2ContractError, match="strategyV2.apiCallInvalid:get_position:returnsPositionObject"):
        compile_strategy_v2(code)


def test_contract_accepts_position_object_attributes():
    code = '''
def initialize(context):
    context.set_universe(["Crypto:ZEC/USDT@spot"])
    context.subscribe(frequency="30m")

def handle_data(context, data):
    position = get_position("Crypto:ZEC/USDT@spot")
    if float(position.amount or 0.0) > 0:
        order_target_percent("Crypto:ZEC/USDT@spot", 0.0)
'''
    assert compile_strategy_v2(code).manifest.primary_frequency == "30m"


def test_instrument_parser_normalizes_ptrade_and_crypto_symbols():
    assert parse_instrument("600519.XSHG").key == "CNStock:600519.SH"
    assert parse_instrument("USStock:MSFT").key == "USStock:MSFT"
    assert parse_instrument("Crypto:BTCUSDT@okx:swap").key == "Crypto:BTC/USDT@okx:swap"
    assert parse_instrument("Crypto:BTC/USDT@swap").key == "Crypto:BTC/USDT@swap"


def test_manifest_discovers_static_multi_asset_strategy_and_schedule():
    code = """
def initialize(context):
    g.sec_dict = {
        "000063.XSHE": {"amount": 10000},
        "600519.XSHG": {"amount": 20000},
    }
    context.set_universe(list(g.sec_dict.keys()))
    context.subscribe(frequency="1d")
    context.set_warmup(60)
    run_daily(rebalance, time="09:35")

def rebalance(context, data=None):
    pass
"""
    compiled = compile_strategy_v2(code)
    manifest = compiled.manifest

    assert manifest.api_version == 2
    assert manifest.strategy_type == "portfolio"
    assert [item.symbol for item in manifest.universe.instruments] == ["000063.SZ", "600519.SH"]
    assert manifest.primary_frequency == "1d"
    assert manifest.warmup_bars == 60
    assert manifest.schedules[0].callback == "rebalance"
    assert manifest.schedules[0].time == "09:35"


def test_manifest_discovers_dynamic_index_universe_and_dependencies():
    code = """
def initialize(context):
    context.set_universe(index="000300.XBHS")
    context.subscribe(frequency="1d")
    run_weekly(rebalance, weekday=1, time="09:40")

def rebalance(context, data):
    scores = factor(["RSI", "ROE"])
    fundamentals = get_fundamentals(["PE", "PB"])
"""
    manifest = compile_strategy_v2(code).manifest

    assert manifest.strategy_type == "portfolio"
    assert manifest.universe.kind == "dynamic"
    assert manifest.universe.reference == "CNStock:000300.SH"
    assert manifest.factor_dependencies == ("ROE", "RSI")
    assert manifest.fundamental_dependencies == ("PB", "PE")
    assert manifest.schedules[0].frequency == "weekly"


def test_manifest_discovers_named_universe_pool():
    code = """
def initialize(context):
    context.set_universe(pool="sp500")
    context.subscribe(frequency="1d")
    run_weekly(rebalance)

def rebalance(context, data):
    for symbol in get_universe_stocks():
        order_target_percent(symbol, 0.0)
"""
    manifest = compile_strategy_v2(code).manifest

    assert manifest.strategy_type == "portfolio"
    assert manifest.universe.kind == "dynamic"
    assert manifest.universe.reference == "POOL:sp500"


def test_manifest_declares_contract_leverage_policy():
    code = """
def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@okx:swap"])
    context.subscribe(frequency="1h")
    context.allow_leverage(5)

def handle_data(context, data):
    pass
"""
    manifest = compile_strategy_v2(code).manifest

    assert manifest.strategy_type == "cta"
    assert manifest.leverage_allowed is True
    assert manifest.max_leverage == 5
    assert manifest.primary_frequency == "1h"


def test_manifest_declares_direction_capability_from_metadata():
    code = """
def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@okx:swap"])
    context.subscribe(frequency="1h")
    context.set_metadata(direction_mode="both")

def handle_data(context, data):
    pass
"""
    manifest = compile_strategy_v2(code).manifest

    assert manifest.direction_mode == "both"
    assert manifest.metadata()["directionMode"] == "both"


@pytest.mark.parametrize(
    "strategy_body,expected",
    [
        ("DIRECTION = 1.0", "long_only"),
        ("DIRECTION = -1.0", "short_only"),
        (
            '''
def trade():
    order_target_value("Crypto:BTC/USDT@okx:swap", 10, position_side="long")
    order_target_value("Crypto:BTC/USDT@okx:swap", -10, position_side="short")
''',
            "both",
        ),
    ],
)
def test_manifest_infers_legacy_direction_capability(strategy_body, expected):
    code = f"""
{strategy_body}

def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@okx:swap"])
    context.subscribe(frequency="1h")

def handle_data(context, data):
    pass
"""

    assert compile_strategy_v2(code).manifest.direction_mode == expected


def test_manifest_rejects_invalid_direction_capability():
    code = """
def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@okx:swap"])
    context.subscribe(frequency="1h")
    context.set_metadata(direction_mode="sideways")

def handle_data(context, data):
    pass
"""

    with pytest.raises(StrategyV2ContractError, match="strategyV2.directionModeInvalid"):
        compile_strategy_v2(code)


def test_manifest_allows_exchange_agnostic_crypto_swap_leverage():
    code = """
def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@swap"])
    context.subscribe(frequency="4h")
    context.allow_leverage(max_leverage=20)

def handle_data(context, data):
    pass
"""
    manifest = compile_strategy_v2(code).manifest

    assert manifest.leverage_allowed is True
    assert manifest.max_leverage == 20
    assert manifest.universe.instruments[0].exchange_id == ""
    assert manifest.universe.instruments[0].market_type == "swap"


def test_manifest_rejects_leverage_for_non_crypto_swap_instruments():
    for instrument in ("USStock:SPY", "Crypto:BTC/USDT@spot"):
        code = f"""
def initialize(context):
    context.set_universe(["{instrument}"])
    context.subscribe(frequency="1d")
    context.allow_leverage(2)

def handle_data(context, data):
    pass
"""
        try:
            compile_strategy_v2(code)
        except ValueError as exc:
            assert str(exc) == "strategyV2.leverageCryptoSwapOnly"
        else:
            raise AssertionError(f"leverage should be rejected for {instrument}")


def test_manifest_classifies_known_fundamental_factor_by_required_columns():
    code = """
def initialize(context):
    context.set_universe(index="INDEX:SP500")
    context.subscribe(frequency="1d")
    run_weekly(rebalance)

def rebalance(context, data):
    get_factors(get_index_stocks("INDEX:SP500"), "market_cap")
"""
    manifest = compile_strategy_v2(code).manifest

    assert manifest.factor_dependencies == ()
    assert manifest.fundamental_dependencies == ("MARKET_CAP",)
