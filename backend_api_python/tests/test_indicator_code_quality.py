"""Tests for indicator_code_quality heuristics."""

from app.services.indicator_code_quality import analyze_indicator_code_quality
from app.services.indicator_params import StrategyConfigParser


def test_empty_code():
    hints = analyze_indicator_code_quality("")
    assert any(h["code"] == "EMPTY_CODE" for h in hints)


def test_minimal_valid_style():
    code = """
my_indicator_name = "T"
my_indicator_description = "D"
# @strategy stopLossPct 0.02
# @strategy takeProfitPct 0.04
df = df.copy()
df['buy'] = False
df['sell'] = False
output = {'name': 'T', 'plots': [], 'signals': []}
"""
    hints = analyze_indicator_code_quality(code)
    codes = {h["code"] for h in hints}
    assert "MISSING_OUTPUT" not in codes
    assert "NO_STOP_AND_TAKE_PROFIT" not in codes


def test_missing_stop_take_when_trading():
    code = """
my_indicator_name = "T"
my_indicator_description = "D"
df = df.copy()
df['open_long'] = True
df['close_long'] = False
df['open_short'] = True
df['close_short'] = False
output = {'name': 'T', 'plots': [], 'signals': []}
"""
    hints = analyze_indicator_code_quality(code)
    codes = [h["code"] for h in hints]
    assert "NO_STRATEGY_ANNOTATIONS" in codes


def test_partial_strategy_without_sl_tp():
    code = """
my_indicator_name = "T"
my_indicator_description = "D"
# @strategy tradeDirection long
# @strategy leverage 2
df = df.copy()
df['open_long'] = False
df['close_long'] = False
df['open_short'] = False
df['close_short'] = False
output = {'name': 'T', 'plots': [], 'signals': []}
"""
    hints = analyze_indicator_code_quality(code)
    assert any(h["code"] == "NO_STOP_AND_TAKE_PROFIT" for h in hints)


def test_strategy_parser_ignores_leverage_annotation():
    cfg = StrategyConfigParser.parse("# @strategy leverage 5\n# @strategy stopLossPct 0.02\n")
    assert "leverage" not in cfg
    assert cfg.get("stopLossPct") == 0.02


def test_legacy_leverage_line_not_flagged_unknown():
    code = """
my_indicator_name = "T"
my_indicator_description = "D"
# @strategy leverage 2
df = df.copy()
df['buy'] = False
df['sell'] = False
output = {'name': 'T', 'plots': [], 'signals': []}
"""
    hints = analyze_indicator_code_quality(code)
    assert not any(h["code"] == "UNKNOWN_STRATEGY_KEY" for h in hints)


def test_unknown_strategy_key():
    code = """
my_indicator_name = "T"
my_indicator_description = "D"
# @strategy signalTiming same_bar_close
df = df.copy()
df['buy'] = False
df['sell'] = False
output = {'name': 'T', 'plots': [], 'signals': []}
"""
    hints = analyze_indicator_code_quality(code)
    assert any(h["code"] == "UNKNOWN_STRATEGY_KEY" for h in hints)


def test_declared_params_must_be_read_via_params_get():
    code = """
my_indicator_name = "T"
my_indicator_description = "D"
# @param fast_period int 10 Fast MA
df = df.copy()
ma = df['close'].rolling(window=fast_period).mean()
df['buy'] = False
df['sell'] = False
output = {'name': 'T', 'plots': [], 'signals': []}
"""
    hints = analyze_indicator_code_quality(code)
    assert any(h["code"] == "DECLARED_PARAMS_NOT_READ_VIA_PARAMS_GET" for h in hints)


def test_declared_params_read_via_params_get_is_ok():
    code = """
my_indicator_name = "T"
my_indicator_description = "D"
# @param fast_period int 10 Fast MA
fast_period = params.get('fast_period', 10)
df = df.copy()
ma = df['close'].rolling(window=fast_period).mean()
df['buy'] = False
df['sell'] = False
output = {'name': 'T', 'plots': [], 'signals': []}
"""
    hints = analyze_indicator_code_quality(code)
    assert not any(h["code"] == "DECLARED_PARAMS_NOT_READ_VIA_PARAMS_GET" for h in hints)


def test_four_way_columns_no_missing_buy_sell_warn():
    code = """
my_indicator_name = "T"
my_indicator_description = "D"
# @strategy stopLossPct 0.02
# @strategy takeProfitPct 0.04
df = df.copy()
df['open_long'] = False
df['close_long'] = False
df['open_short'] = False
df['close_short'] = False
output = {'name': 'T', 'plots': [], 'signals': []}
"""
    hints = analyze_indicator_code_quality(code)
    assert not any(h["code"] == "MISSING_BUY_SELL_COLUMNS" for h in hints)


def test_where_none_signal_markers_warned():
    code = """
my_indicator_name = "T"
my_indicator_description = "D"
df = df.copy()
df['buy'] = False
df['sell'] = False
buy_prices = df['close'].where(df['buy'], None).tolist()
output = {'name': 'T', 'plots': [], 'signals': [{'type': 'buy', 'data': buy_prices}]}
"""
    hints = analyze_indicator_code_quality(code)
    assert any(h["code"] == "SIGNAL_MARKERS_USE_WHERE_NONE" for h in hints)
