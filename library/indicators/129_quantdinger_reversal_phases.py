# ============================================================
# QuantDinger Reversal Phases
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Reversal Phases"
my_indicator_description = (
    "Tracks nine-count momentum phases, thirteen-count exhaustion phases, "
    "persistent support or risk levels, and optional phase-specific trade setups."
)

# @param momentum_display str Completed Momentum phase display values=Completed,Detailed,None
# @param show_support_resistance bool true Show momentum support and resistance
# @param show_momentum_risk bool false Show momentum risk levels
# @param exhaustion_display str Completed Exhaustion phase display values=Completed,Detailed,None
# @param show_exhaustion_risk bool false Show exhaustion risk levels
# @param show_exhaustion_target bool false Show exhaustion target levels
# @param trade_setup str None Phase-specific trade setup values=None,Momentum,Exhaustion,Qualified
# @param show_warnings bool false Show price-flip and stop warnings

momentum_display = str(params.get("momentum_display", "Completed"))
show_support_resistance = bool(params.get("show_support_resistance", True))
show_momentum_risk = bool(params.get("show_momentum_risk", False))
exhaustion_display = str(params.get("exhaustion_display", "Completed"))
show_exhaustion_risk = bool(params.get("show_exhaustion_risk", False))
show_exhaustion_target = bool(params.get("show_exhaustion_target", False))
trade_setup = str(params.get("trade_setup", "None"))
show_warnings = bool(params.get("show_warnings", False))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

bull_momentum = np.zeros(n, dtype=int)
bear_momentum = np.zeros(n, dtype=int)
bull_perfect = np.zeros(n, dtype=bool)
bear_perfect = np.zeros(n, dtype=bool)
bull_early = np.zeros(n, dtype=bool)
bear_early = np.zeros(n, dtype=bool)

resistance = np.zeros(n, dtype=float)
support = np.zeros(n, dtype=float)
bull_momentum_risk = np.zeros(n, dtype=float)
bear_momentum_risk = np.zeros(n, dtype=float)

bull_exhaustion = np.zeros(n, dtype=int)
bear_exhaustion = np.zeros(n, dtype=int)
bull_exhaustion_risk = np.zeros(n, dtype=float)
bull_exhaustion_target = np.zeros(n, dtype=float)
bear_exhaustion_risk = np.zeros(n, dtype=float)
bear_exhaustion_target = np.zeros(n, dtype=float)

bull_setup = np.zeros(n, dtype=bool)
bear_setup = np.zeros(n, dtype=bool)
bull_warning = np.zeros(n, dtype=bool)
bear_warning = np.zeros(n, dtype=bool)

bull_sl = np.nan
bull_sh = np.nan
bear_sh = np.nan
bear_sl = np.nan
bull_count8_close = np.nan
bear_count8_close = np.nan
bull_phase_active = False
bear_phase_active = False
bull_extreme_low = np.nan
bull_extreme_high = np.nan
bull_extreme_low_high = np.nan
bull_extreme_high_low = np.nan
bear_extreme_low = np.nan
bear_extreme_high = np.nan
bear_extreme_low_high = np.nan
bear_extreme_high_low = np.nan

bull_pending = False
bear_pending = False
long_trade_active = False
short_trade_active = False
bull_nines = []
bear_nines = []
bull_thirteens = []
bear_thirteens = []

for index in range(n):
    previous_bull = int(bull_momentum[index - 1]) if index > 0 else 0
    previous_bear = int(bear_momentum[index - 1]) if index > 0 else 0
    condition = index >= 4 and close[index] < close[index - 4]
    if condition:
        bull_momentum[index] = 1 if previous_bull == 9 else previous_bull + 1
        bear_momentum[index] = 0
    else:
        bear_momentum[index] = 1 if previous_bear == 9 else previous_bear + 1
        bull_momentum[index] = 0

    if index >= 3:
        bull_perfect[index] = (
            (low[index] <= low[index - 3] and low[index] <= low[index - 2])
            or (low[index - 1] <= low[index - 3] and low[index - 1] <= low[index - 2])
        )
        bear_perfect[index] = (
            (high[index] >= high[index - 3] and high[index] >= high[index - 2])
            or (high[index - 1] >= high[index - 3] and high[index - 1] >= high[index - 2])
        )

    bull_early[index] = index > 0 and previous_bull == 8 and bear_momentum[index] == 1
    bear_early[index] = index > 0 and previous_bear == 8 and bull_momentum[index] == 1

    highest_nine = float(np.max(high[max(0, index - 8):index + 1]))
    lowest_nine = float(np.min(low[max(0, index - 8):index + 1]))
    previous_resistance = resistance[index - 1] if index > 0 else 0.0
    previous_support = support[index - 1] if index > 0 else 0.0
    resistance[index] = (
        highest_nine
        if bull_momentum[index] == 9 or bull_early[index]
        else 0.0 if close[index] > previous_resistance else previous_resistance
    )
    support[index] = (
        lowest_nine
        if bear_momentum[index] == 9 or bear_early[index]
        else 0.0 if close[index] < previous_support else previous_support
    )

    if bull_momentum[index] == 1:
        bull_sl = low[index]
        bull_sh = high[index]
    elif bull_momentum[index] > 0:
        if not np.isfinite(bull_sl) or low[index] <= bull_sl:
            bull_sl = low[index]
            bull_sh = high[index]

    if bear_momentum[index] == 1:
        bear_sh = high[index]
        bear_sl = low[index]
    elif bear_momentum[index] > 0:
        if not np.isfinite(bear_sh) or high[index] >= bear_sh:
            bear_sh = high[index]
            bear_sl = low[index]

    previous_bull_risk = bull_momentum_risk[index - 1] if index > 0 else 0.0
    previous_bear_risk = bear_momentum_risk[index - 1] if index > 0 else 0.0
    if bull_momentum[index] == 9 and np.isfinite(bull_sl) and np.isfinite(bull_sh):
        bull_momentum_risk[index] = 2.0 * bull_sl - bull_sh
    elif close[index] < previous_bull_risk or bear_momentum[index] == 9:
        bull_momentum_risk[index] = 0.0
    else:
        bull_momentum_risk[index] = previous_bull_risk

    if bear_momentum[index] == 9 and np.isfinite(bear_sh) and np.isfinite(bear_sl):
        bear_momentum_risk[index] = 2.0 * bear_sh - bear_sl
    elif close[index] > previous_bear_risk or bull_momentum[index] == 9:
        bear_momentum_risk[index] = 0.0
    else:
        bear_momentum_risk[index] = previous_bear_risk

    previous_bull_exhaustion = int(bull_exhaustion[index - 1]) if index > 0 else 0
    previous_bear_exhaustion = int(bear_exhaustion[index - 1]) if index > 0 else 0
    bull_count_condition = index >= 2 and close[index] <= low[index - 2]
    bear_count_condition = index >= 2 and close[index] >= high[index - 2]
    bull_thirteen_condition = bull_count_condition and np.isfinite(bull_count8_close) and low[index] >= bull_count8_close
    bear_thirteen_condition = bear_count_condition and np.isfinite(bear_count8_close) and high[index] <= bear_count8_close

    if bull_momentum[index] == 9 and previous_bull_exhaustion == 0 and (bull_perfect[index] or (index > 0 and bull_perfect[index - 1])):
        bull_phase_active = True
    elif bear_momentum[index] == 9 or previous_bull_exhaustion == 13 or (resistance[index] > 0.0 and close[index] > resistance[index]):
        bull_phase_active = False

    if bear_momentum[index] == 9 and previous_bear_exhaustion == 0 and (bear_perfect[index] or (index > 0 and bear_perfect[index - 1])):
        bear_phase_active = True
    elif bull_momentum[index] == 9 or previous_bear_exhaustion == 13 or (support[index] > 0.0 and close[index] < support[index]):
        bear_phase_active = False

    if bull_phase_active:
        if bull_momentum[index] == 9:
            bull_exhaustion[index] = 1 if bull_count_condition else 0
        else:
            bull_exhaustion[index] = previous_bull_exhaustion + 1 if bull_count_condition else previous_bull_exhaustion
        if bull_exhaustion[index] == 13 and bull_thirteen_condition:
            bull_exhaustion[index] = 12

    if bear_phase_active:
        if bear_momentum[index] == 9:
            bear_exhaustion[index] = 1 if bear_count_condition else 0
        else:
            bear_exhaustion[index] = previous_bear_exhaustion + 1 if bear_count_condition else previous_bear_exhaustion
        if bear_exhaustion[index] == 13 and bear_thirteen_condition:
            bear_exhaustion[index] = 12

    if bull_exhaustion[index] == 8 and bull_exhaustion[index] != previous_bull_exhaustion:
        bull_count8_close = close[index]
    if bear_exhaustion[index] == 8 and bear_exhaustion[index] != previous_bear_exhaustion:
        bear_count8_close = close[index]

    if bull_exhaustion[index] == 1:
        bull_extreme_low = low[index]
        bull_extreme_high = high[index]
        bull_extreme_low_high = high[index]
        bull_extreme_high_low = low[index]
    elif bull_phase_active and bull_exhaustion[index] > 0:
        if high[index] >= bull_extreme_high:
            bull_extreme_high = high[index]
            bull_extreme_high_low = low[index]
        if low[index] <= bull_extreme_low:
            bull_extreme_low = low[index]
            bull_extreme_low_high = high[index]

    if bear_exhaustion[index] == 1:
        bear_extreme_low = low[index]
        bear_extreme_high = high[index]
        bear_extreme_low_high = high[index]
        bear_extreme_high_low = low[index]
    elif bear_phase_active and bear_exhaustion[index] > 0:
        if high[index] >= bear_extreme_high:
            bear_extreme_high = high[index]
            bear_extreme_high_low = low[index]
        if low[index] <= bear_extreme_low:
            bear_extreme_low = low[index]
            bear_extreme_low_high = high[index]

    previous_bull_target = bull_exhaustion_target[index - 1] if index > 0 else 0.0
    previous_bull_stop = bull_exhaustion_risk[index - 1] if index > 0 else 0.0
    previous_bear_stop = bear_exhaustion_risk[index - 1] if index > 0 else 0.0
    previous_bear_target = bear_exhaustion_target[index - 1] if index > 0 else 0.0

    if bull_exhaustion[index] == 13 and np.isfinite(bull_extreme_high) and np.isfinite(bull_extreme_high_low):
        bull_exhaustion_target[index] = 2.0 * bull_extreme_high - bull_extreme_high_low
        bull_exhaustion_risk[index] = 2.0 * bull_extreme_low - bull_extreme_low_high
    else:
        bull_exhaustion_target[index] = 0.0 if close[index] > previous_bull_target else previous_bull_target
        bull_exhaustion_risk[index] = 0.0 if close[index] < previous_bull_stop else previous_bull_stop

    if bear_exhaustion[index] == 13 and np.isfinite(bear_extreme_high) and np.isfinite(bear_extreme_low_high):
        bear_exhaustion_risk[index] = 2.0 * bear_extreme_high - bear_extreme_high_low
        bear_exhaustion_target[index] = 2.0 * bear_extreme_low - bear_extreme_low_high
    else:
        bear_exhaustion_risk[index] = 0.0 if close[index] > previous_bear_stop else previous_bear_stop
        bear_exhaustion_target[index] = 0.0 if close[index] < previous_bear_target else previous_bear_target

    if bull_momentum[index] == 9:
        bull_nines.append(index)
    if bear_momentum[index] == 9:
        bear_nines.append(index)
    if bull_exhaustion[index] == 13:
        bull_thirteens.append(index)
    if bear_exhaustion[index] == 13:
        bear_thirteens.append(index)

    if trade_setup != "None":
        bearish_offer = (
            bear_momentum[index] == 9 or bear_early[index]
            if trade_setup == "Momentum"
            else index >= 5 and bear_exhaustion[index - 5] == 13
            if trade_setup == "Exhaustion"
            else len(bear_nines) >= 2 and len(bear_thirteens) >= 1 and len(bull_nines) >= 1
            and bear_nines[-1] > bear_thirteens[-1] > bear_nines[-2] > bull_nines[-1]
        )
        bullish_offer = (
            bull_momentum[index] == 9 or bull_early[index]
            if trade_setup == "Momentum"
            else index >= 5 and bull_exhaustion[index - 5] == 13
            if trade_setup == "Exhaustion"
            else len(bull_nines) >= 2 and len(bull_thirteens) >= 1 and len(bear_nines) >= 1
            and bull_nines[-1] > bull_thirteens[-1] > bull_nines[-2] > bear_nines[-1]
        )
        if bearish_offer:
            bear_pending = True
        if bullish_offer:
            bull_pending = True
        bear_setup[index] = bear_pending and index >= 5 and close[index] < close[index - 4] and close[index - 1] > close[index - 5]
        bull_setup[index] = bull_pending and index >= 5 and close[index] > close[index - 4] and close[index - 1] < close[index - 5]
        if bear_setup[index]:
            bear_pending = False
            short_trade_active = True
            long_trade_active = False
        if bull_setup[index]:
            bull_pending = False
            long_trade_active = True
            short_trade_active = False

    if show_warnings and short_trade_active and close[index] > open_price[index] and bull_momentum[index] == 2:
        bull_warning[index] = True
        short_trade_active = False
    if show_warnings and long_trade_active and close[index] < open_price[index] and bear_momentum[index] == 2:
        bear_warning[index] = True
        long_trade_active = False

def visible_level(values, enabled):
    return [float(value) if enabled and value > 0.0 else None for value in values]

plots = [
    {"name": "Resistance", "data": visible_level(resistance, show_support_resistance), "color": "rgba(242, 54, 69, 0.50)", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Support", "data": visible_level(support, show_support_resistance), "color": "rgba(8, 153, 129, 0.50)", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Bull Momentum Risk", "data": visible_level(bull_momentum_risk, show_momentum_risk), "color": "#2962FF", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Bear Momentum Risk", "data": visible_level(bear_momentum_risk, show_momentum_risk), "color": "#2962FF", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Bull Exhaustion Target", "data": visible_level(bull_exhaustion_target, show_exhaustion_target), "color": "#787B86", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Bull Exhaustion Risk", "data": visible_level(bull_exhaustion_risk, show_exhaustion_risk), "color": "#00BCD4", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Bear Exhaustion Risk", "data": visible_level(bear_exhaustion_risk, show_exhaustion_risk), "color": "#00BCD4", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Bear Exhaustion Target", "data": visible_level(bear_exhaustion_target, show_exhaustion_target), "color": "#787B86", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Price Reference", "title": False, "data": [float(value) for value in close], "color": "rgba(255,255,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
]

def event_data(mask, bullish, text, color):
    data = [None for _ in range(n)]
    for index in range(n):
        if mask[index]:
            data[index] = {"value": float(low[index] if bullish else high[index]), "text": text, "color": color}
    return data

signals = []
if momentum_display != "None":
    completed_bull = (bull_momentum == 9) | bull_early
    completed_bear = (bear_momentum == 9) | bear_early
    signals.append({"type": "buy", "text": "M9", "name": "Bullish Momentum Phase", "data": event_data(completed_bull, True, "M9", "#089981"), "color": "#089981", "renderMode": "events"})
    signals.append({"type": "sell", "text": "M9", "name": "Bearish Momentum Phase", "data": event_data(completed_bear, False, "M9", "#F23645"), "color": "#F23645", "renderMode": "events"})
    if momentum_display == "Detailed":
        for count in range(1, 9):
            signals.append({"type": "buy", "text": str(count), "name": f"Bull Momentum {count}", "data": event_data(bull_momentum == count, False, str(count), "#089981"), "color": "#089981", "renderMode": "events"})
            signals.append({"type": "sell", "text": str(count), "name": f"Bear Momentum {count}", "data": event_data(bear_momentum == count, False, str(count), "#F23645"), "color": "#F23645", "renderMode": "events"})

if exhaustion_display != "None":
    signals.append({"type": "buy", "text": "E13", "name": "Bullish Exhaustion", "data": event_data(bull_exhaustion == 13, True, "E13", "#006400"), "color": "#006400", "renderMode": "events"})
    signals.append({"type": "sell", "text": "E13", "name": "Bearish Exhaustion", "data": event_data(bear_exhaustion == 13, False, "E13", "#910000"), "color": "#910000", "renderMode": "events"})
    if exhaustion_display == "Detailed":
        for count in range(1, 13):
            signals.append({"type": "buy", "text": str(count), "name": f"Bull Exhaustion {count}", "data": event_data(bull_exhaustion == count, True, str(count), "#089981"), "color": "#089981", "renderMode": "events"})
            signals.append({"type": "sell", "text": str(count), "name": f"Bear Exhaustion {count}", "data": event_data(bear_exhaustion == count, False, str(count), "#F23645"), "color": "#F23645", "renderMode": "events"})

signals.append({"type": "buy", "text": "L", "name": "Long Trade Setup", "data": event_data(bull_setup, True, "L", "#2962FF"), "color": "#2962FF", "renderMode": "events"})
signals.append({"type": "sell", "text": "S", "name": "Short Trade Setup", "data": event_data(bear_setup, False, "S", "#FACC15"), "color": "#FACC15", "renderMode": "events"})
signals.append({"type": "buy", "text": "Warning", "name": "Bullish Warning", "data": event_data(bull_warning, True, "Warning", "#FACC15"), "color": "#FACC15", "renderMode": "events"})
signals.append({"type": "sell", "text": "Warning", "name": "Bearish Warning", "data": event_data(bear_warning, False, "Warning", "#FACC15"), "color": "#FACC15", "renderMode": "events"})

plots = [
    plot
    for plot in plots
    if any(item is not None for item in plot.get("data", []))
]
signals = [
    signal
    for signal in signals
    if any(item is not None for item in signal.get("data", []))
]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": []}
