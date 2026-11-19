# ============================================================
# QuantDinger Fisher Williams %R Divergence
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Fisher Williams %R Divergence"
my_indicator_description = (
    "Transforms Williams %R through a recursive Fisher filter, adds filtered trigger "
    "crosses, and detects confirmed regular or hidden price divergences."
)

# @param williams_length int 14 Williams %R length range=1:200:1
# @param fisher_length int 10 Fisher normalization length range=1:200:1
# @param source str Close Source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param overbought float 1.5 Overbought level range=-10:10:0.1
# @param oversold float -1.5 Oversold level range=-10:10:0.1
# @param pivot_left int 5 Pivot left lookback range=1:50:1
# @param pivot_right int 5 Pivot right lookback range=1:50:1
# @param range_upper int 60 Maximum pivot range range=1:300:1
# @param range_lower int 5 Minimum pivot range range=1:100:1
# @param min_bars_between int 0 Same-type signal spacing range=0:300:1
# @param min_fisher_difference float 0.1 Minimum Fisher difference range=0:5:0.05
# @param show_regular bool true Show regular divergence
# @param show_hidden bool true Show hidden divergence
# @param zone_filter bool false Require OB/OS divergence zone
# @param show_cross bool true Show Fisher/trigger crosses
# @param trigger_length int 3 Trigger EMA length range=1:100:1
# @param zone_gate bool true Require recent cross extreme
# @param extreme_lookback int 8 Extreme lookback bars range=1:200:1
# @param zero_side bool true Buy below / sell above zero
# @param alternate_only bool true Alternate cross signals
# @param cross_min_bars int 3 Minimum bars between crosses range=0:300:1
# @param max_divergence_lines int 80 Maximum visible divergence lines range=10:300:10

williams_length = min(200, max(1, int(params.get("williams_length", 14))))
fisher_length = min(200, max(1, int(params.get("fisher_length", 10))))
source_name = str(params.get("source", "Close")).strip().upper()
overbought = float(params.get("overbought", 1.5))
oversold = float(params.get("oversold", -1.5))
pivot_left = min(50, max(1, int(params.get("pivot_left", 5))))
pivot_right = min(50, max(1, int(params.get("pivot_right", 5))))
range_lower = min(100, max(1, int(params.get("range_lower", 5))))
range_upper = min(300, max(range_lower, int(params.get("range_upper", 60))))
min_bars_between = min(300, max(0, int(params.get("min_bars_between", 0))))
min_fisher_difference = min(5.0, max(0.0, float(params.get("min_fisher_difference", 0.10))))
show_regular = bool(params.get("show_regular", True))
show_hidden = bool(params.get("show_hidden", True))
zone_filter = bool(params.get("zone_filter", False))
show_cross = bool(params.get("show_cross", True))
trigger_length = min(100, max(1, int(params.get("trigger_length", 3))))
zone_gate = bool(params.get("zone_gate", True))
extreme_lookback = min(200, max(1, int(params.get("extreme_lookback", 8))))
zero_side = bool(params.get("zero_side", True))
alternate_only = bool(params.get("alternate_only", True))
cross_min_bars = min(300, max(0, int(params.get("cross_min_bars", 3))))
max_divergence_lines = min(300, max(10, int(params.get("max_divergence_lines", 80))))


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def ema_first(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


frame = df.copy().reset_index(drop=True)
n = len(frame)
open_values = pd.to_numeric(frame["open"], errors="coerce").to_numpy(dtype=float)
high_values = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype=float)

source_map = {
    "OPEN": open_values,
    "HIGH": high_values,
    "LOW": low_values,
    "HL2": (high_values + low_values) / 2.0,
    "HLC3": (high_values + low_values + close_values) / 3.0,
    "OHLC4": (open_values + high_values + low_values + close_values) / 4.0,
}
source_values = source_map.get(source_name, close_values)

highest_high = pd.Series(high_values).rolling(williams_length, min_periods=1).max().to_numpy(dtype=float)
lowest_low = pd.Series(low_values).rolling(williams_length, min_periods=1).min().to_numpy(dtype=float)
price_range = highest_high - lowest_low
percent_r = np.full(n, -50.0, dtype=float)
valid_price_range = np.isfinite(price_range) & (price_range != 0.0)
np.divide(
    100.0 * (source_values - highest_high),
    price_range,
    out=percent_r,
    where=valid_price_range,
)
highest_percent_r = pd.Series(percent_r).rolling(fisher_length, min_periods=1).max().to_numpy(dtype=float)
lowest_percent_r = pd.Series(percent_r).rolling(fisher_length, min_periods=1).min().to_numpy(dtype=float)
percent_range = highest_percent_r - lowest_percent_r
raw_value = np.full(n, 0.5, dtype=float)
np.divide(
    percent_r - lowest_percent_r,
    percent_range,
    out=raw_value,
    where=percent_range != 0.0,
)
normalized = 2.0 * (raw_value - 0.5)

smoothed_value = np.zeros(n, dtype=float)
fisher = np.zeros(n, dtype=float)
for index in range(n):
    previous_value = smoothed_value[index - 1] if index > 0 else 0.0
    previous_fisher = fisher[index - 1] if index > 0 else 0.0
    smoothed_value[index] = min(0.999, max(-0.999, 0.33 * normalized[index] + 0.67 * previous_value))
    fisher[index] = 0.5 * np.log((1.0 + smoothed_value[index]) / (1.0 - smoothed_value[index])) + 0.5 * previous_fisher

ema_trigger = ema_first(fisher, trigger_length)
trigger = np.roll(fisher, 1) if trigger_length == 1 else ema_trigger.copy()
if trigger_length == 1 and n:
    trigger[0] = np.nan

cross_up = np.zeros(n, dtype=bool)
cross_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(trigger[index]) and np.isfinite(trigger[index - 1]):
        cross_up[index] = fisher[index] > trigger[index] and fisher[index - 1] <= trigger[index - 1]
        cross_down[index] = fisher[index] < trigger[index] and fisher[index - 1] >= trigger[index - 1]

recent_low = pd.Series(fisher).rolling(extreme_lookback, min_periods=1).min().to_numpy(dtype=float)
recent_high = pd.Series(fisher).rolling(extreme_lookback, min_periods=1).max().to_numpy(dtype=float)
raw_buy = cross_up & ((recent_low <= oversold) if zone_gate else True) & ((trigger < 0.0) if zero_side else True)
raw_sell = cross_down & ((recent_high >= overbought) if zone_gate else True) & ((trigger > 0.0) if zero_side else True)

buy_signal = np.zeros(n, dtype=bool)
sell_signal = np.zeros(n, dtype=bool)
last_cross_direction = 0
last_cross_bar = -1
for index in range(n):
    cooldown_ok = last_cross_bar < 0 or index - last_cross_bar >= cross_min_bars
    if show_cross and raw_buy[index] and cooldown_ok and (not alternate_only or last_cross_direction != 1):
        buy_signal[index] = True
        last_cross_direction = 1
        last_cross_bar = index
    elif show_cross and raw_sell[index] and cooldown_ok and (not alternate_only or last_cross_direction != -1):
        sell_signal[index] = True
        last_cross_direction = -1
        last_cross_bar = index

pivot_low_confirmation = np.full(n, np.nan, dtype=float)
pivot_high_confirmation = np.full(n, np.nan, dtype=float)
pivot_low_anchor = np.full(n, -1, dtype=int)
pivot_high_anchor = np.full(n, -1, dtype=int)
window_size = pivot_left + pivot_right + 1
for confirmation in range(window_size - 1, n):
    anchor = confirmation - pivot_right
    window = fisher[anchor - pivot_left:anchor + pivot_right + 1]
    if len(window) != window_size or not np.all(np.isfinite(window)):
        continue
    if fisher[anchor] == np.min(window):
        pivot_low_confirmation[confirmation] = fisher[anchor]
        pivot_low_anchor[confirmation] = anchor
    if fisher[anchor] == np.max(window):
        pivot_high_confirmation[confirmation] = fisher[anchor]
        pivot_high_anchor[confirmation] = anchor

regular_bull = np.zeros(n, dtype=bool)
hidden_bull = np.zeros(n, dtype=bool)
regular_bear = np.zeros(n, dtype=bool)
hidden_bear = np.zeros(n, dtype=bool)
regular_bull_anchor = np.zeros(n, dtype=bool)
hidden_bull_anchor = np.zeros(n, dtype=bool)
regular_bear_anchor = np.zeros(n, dtype=bool)
hidden_bear_anchor = np.zeros(n, dtype=bool)
divergences = []
last_bull_anchor = -1
last_bear_anchor = -1

for confirmation in range(n):
    if np.isfinite(pivot_low_confirmation[confirmation]):
        current_anchor = int(pivot_low_anchor[confirmation])
        previous_confirmation = -1
        for distance in range(range_lower, range_upper + 1):
            candidate = confirmation - distance
            if candidate >= 0 and np.isfinite(pivot_low_confirmation[candidate]):
                previous_confirmation = candidate
                break
        if previous_confirmation >= 0:
            previous_anchor = int(pivot_low_anchor[previous_confirmation])
            current_fisher = float(fisher[current_anchor])
            previous_fisher = float(fisher[previous_anchor])
            in_zone = not zone_filter or current_fisher <= oversold
            spacing_ok = last_bull_anchor < 0 or current_anchor - last_bull_anchor >= min_bars_between
            difference_ok = abs(current_fisher - previous_fisher) >= min_fisher_difference
            kind = None
            if show_regular and in_zone and spacing_ok and difference_ok and low_values[current_anchor] < low_values[previous_anchor] and current_fisher > previous_fisher:
                regular_bull[confirmation] = True
                regular_bull_anchor[current_anchor] = True
                kind = "regular_bull"
            elif show_hidden and in_zone and spacing_ok and difference_ok and low_values[current_anchor] > low_values[previous_anchor] and current_fisher < previous_fisher:
                hidden_bull[confirmation] = True
                hidden_bull_anchor[current_anchor] = True
                kind = "hidden_bull"
            if kind is not None:
                last_bull_anchor = current_anchor
                divergences.append({
                    "kind": kind, "confirmation": int(confirmation),
                    "start": previous_anchor, "end": current_anchor,
                    "y1": previous_fisher, "y2": current_fisher,
                })

    if np.isfinite(pivot_high_confirmation[confirmation]):
        current_anchor = int(pivot_high_anchor[confirmation])
        previous_confirmation = -1
        for distance in range(range_lower, range_upper + 1):
            candidate = confirmation - distance
            if candidate >= 0 and np.isfinite(pivot_high_confirmation[candidate]):
                previous_confirmation = candidate
                break
        if previous_confirmation >= 0:
            previous_anchor = int(pivot_high_anchor[previous_confirmation])
            current_fisher = float(fisher[current_anchor])
            previous_fisher = float(fisher[previous_anchor])
            in_zone = not zone_filter or current_fisher >= overbought
            spacing_ok = last_bear_anchor < 0 or current_anchor - last_bear_anchor >= min_bars_between
            difference_ok = abs(current_fisher - previous_fisher) >= min_fisher_difference
            kind = None
            if show_regular and in_zone and spacing_ok and difference_ok and high_values[current_anchor] > high_values[previous_anchor] and current_fisher < previous_fisher:
                regular_bear[confirmation] = True
                regular_bear_anchor[current_anchor] = True
                kind = "regular_bear"
            elif show_hidden and in_zone and spacing_ok and difference_ok and high_values[current_anchor] < high_values[previous_anchor] and current_fisher > previous_fisher:
                hidden_bear[confirmation] = True
                hidden_bear_anchor[current_anchor] = True
                kind = "hidden_bear"
            if kind is not None:
                last_bear_anchor = current_anchor
                divergences.append({
                    "kind": kind, "confirmation": int(confirmation),
                    "start": previous_anchor, "end": current_anchor,
                    "y1": previous_fisher, "y2": current_fisher,
                })

plots = [
    {"name": "Fisher %R", "data": finite(fisher), "color": "#2962FF", "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Trigger", "data": finite(trigger), "color": "#FF6D00", "type": "line", "lineWidth": 1, "lineStyle": "linebr", "connectNulls": False, "overlay": False},
    {"name": "OB/OS Zone", "title": False, "data": [{"upper": overbought, "lower": oversold} for _ in range(n)], "color": "#2962FF", "type": "cloud", "opacity": 0.07, "overlay": False},
    {"name": "Overbought", "data": [overbought for _ in range(n)], "color": "rgba(156,163,175,0.75)", "type": "line", "lineWidth": 1, "style": "dashed", "overlay": False},
    {"name": "Zero", "data": [0.0 for _ in range(n)], "color": "rgba(156,163,175,0.45)", "type": "line", "lineWidth": 1, "style": "dotted", "overlay": False},
    {"name": "Oversold", "data": [oversold for _ in range(n)], "color": "rgba(156,163,175,0.75)", "type": "line", "lineWidth": 1, "style": "dashed", "overlay": False},
]

marker_specs = [
    ("Buy Cross", buy_signal, "#00E676"), ("Sell Cross", sell_signal, "#FF5252"),
    ("Regular Bullish", regular_bull_anchor, "#00E676"), ("Hidden Bullish", hidden_bull_anchor, "rgba(0,230,118,0.60)"),
    ("Regular Bearish", regular_bear_anchor, "#FF5252"), ("Hidden Bearish", hidden_bear_anchor, "rgba(255,82,82,0.60)"),
]
for name, events, color in marker_specs:
    plots.append({
        "name": name, "title": False,
        "data": [{"value": float(fisher[index]), "color": color, "size": 5} if events[index] else None for index in range(n)],
        "color": color, "type": "scatter", "size": 5, "overlay": False,
    })

divergence_colors = {
    "regular_bull": ("#00E676", False), "hidden_bull": ("rgba(0,230,118,0.60)", True),
    "regular_bear": ("#FF5252", False), "hidden_bear": ("rgba(255,82,82,0.60)", True),
}
for display_index, divergence in enumerate(divergences[-max_divergence_lines:]):
    segment = np.full(n, np.nan, dtype=float)
    start = divergence["start"]
    end = divergence["end"]
    if end > start:
        segment[start:end + 1] = np.linspace(divergence["y1"], divergence["y2"], end - start + 1)
    color, dashed = divergence_colors[divergence["kind"]]
    plots.append({
        "name": f"Divergence {display_index + 1}", "title": False, "data": finite(segment),
        "color": color, "type": "line", "lineWidth": 2,
        "lineStyle": "dashed" if dashed else "linebr", "connectNulls": False, "overlay": False,
    })

signals = [
    {"type": "buy", "name": "Fisher Buy Cross", "text": "BUY", "color": "#00E676", "renderMode": "hidden", "data": [float(fisher[index]) if buy_signal[index] else None for index in range(n)]},
    {"type": "sell", "name": "Fisher Sell Cross", "text": "SELL", "color": "#FF5252", "renderMode": "hidden", "data": [float(fisher[index]) if sell_signal[index] else None for index in range(n)]},
    {"type": "buy", "name": "Regular Bullish Divergence", "text": "REG BULL", "color": "#00E676", "renderMode": "hidden", "data": [float(fisher[index]) if regular_bull[index] else None for index in range(n)]},
    {"type": "buy", "name": "Hidden Bullish Divergence", "text": "HDN BULL", "color": "#00E676", "renderMode": "hidden", "data": [float(fisher[index]) if hidden_bull[index] else None for index in range(n)]},
    {"type": "sell", "name": "Regular Bearish Divergence", "text": "REG BEAR", "color": "#FF5252", "renderMode": "hidden", "data": [float(fisher[index]) if regular_bear[index] else None for index in range(n)]},
    {"type": "sell", "name": "Hidden Bearish Divergence", "text": "HDN BEAR", "color": "#FF5252", "renderMode": "hidden", "data": [float(fisher[index]) if hidden_bear[index] else None for index in range(n)]},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "source": source_name, "highestHigh": finite(highest_high), "lowestLow": finite(lowest_low),
        "percentR": finite(percent_r), "highestPercentR": finite(highest_percent_r),
        "lowestPercentR": finite(lowest_percent_r), "rawValue": finite(raw_value),
        "normalized": finite(normalized), "smoothedValue": finite(smoothed_value),
        "fisher": finite(fisher), "emaTrigger": finite(ema_trigger), "trigger": finite(trigger),
        "crossUp": cross_up.tolist(), "crossDown": cross_down.tolist(),
        "recentLow": finite(recent_low), "recentHigh": finite(recent_high),
        "rawBuy": raw_buy.tolist(), "rawSell": raw_sell.tolist(),
        "buySignal": buy_signal.tolist(), "sellSignal": sell_signal.tolist(),
        "pivotLowConfirmation": finite(pivot_low_confirmation), "pivotHighConfirmation": finite(pivot_high_confirmation),
        "pivotLowAnchor": pivot_low_anchor.tolist(), "pivotHighAnchor": pivot_high_anchor.tolist(),
        "regularBull": regular_bull.tolist(), "hiddenBull": hidden_bull.tolist(),
        "regularBear": regular_bear.tolist(), "hiddenBear": hidden_bear.tolist(),
        "regularBullAnchor": regular_bull_anchor.tolist(), "hiddenBullAnchor": hidden_bull_anchor.tolist(),
        "regularBearAnchor": regular_bear_anchor.tolist(), "hiddenBearAnchor": hidden_bear_anchor.tolist(),
        "divergences": divergences,
    },
}
