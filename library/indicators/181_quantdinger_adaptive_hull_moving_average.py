# ============================================================
# QuantDinger Adaptive Hull Moving Average
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Adaptive Hull Moving Average"
my_indicator_description = (
    "Adapts a primary and a faster Hull moving average to volatility or volume expansion, "
    "with trend-state coloring, optional ATR distance zones, entries, exits, and warnings."
)

# @param charger str Volatility Adaptation source values=Volatility,Volume
# @param source str close Price source values=close,open,high,low,hl2,hlc3,ohlc4
# @param minimum_period int 172 Main minimum period range=2:500:1
# @param maximum_period int 233 Main maximum period range=2:800:1
# @param consolidation_slope float 17 Consolidation slope threshold range=1:90:1
# @param show_minor bool true Show minor adaptive HMA
# @param minor_minimum int 89 Minor minimum period range=2:500:1
# @param minor_maximum int 121 Minor maximum period range=2:800:1
# @param show_distance_zone bool false Show ATR distance zone
# @param distance_multiplier float 2.7 Distance multiplier range=0.1:20.0:0.1
# @param show_signals bool true Show possible signals
# @param use_background bool true Color low-energy background

charger = str(params.get("charger", "Volatility"))
if charger not in ("Volatility", "Volume"):
    charger = "Volatility"
source_name = str(params.get("source", "close"))
if source_name not in ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4"):
    source_name = "close"
minimum_period = min(500, max(2, int(params.get("minimum_period", 172))))
maximum_period = min(800, max(minimum_period, int(params.get("maximum_period", 233))))
consolidation_slope = min(90.0, max(1.0, float(params.get("consolidation_slope", 17))))
show_minor = bool(params.get("show_minor", True))
minor_minimum = min(500, max(2, int(params.get("minor_minimum", 89))))
minor_maximum = min(800, max(minor_minimum, int(params.get("minor_maximum", 121))))
show_distance_zone = bool(params.get("show_distance_zone", False))
distance_multiplier = min(20.0, max(0.1, float(params.get("distance_multiplier", 2.7))))
show_signals = bool(params.get("show_signals", True))
use_background = bool(params.get("use_background", True))
adaptation_rate = 0.03141

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

def select_source(name):
    if name == "open":
        return open_price.copy()
    if name == "high":
        return high.copy()
    if name == "low":
        return low.copy()
    if name == "hl2":
        return (high + low) / 2.0
    if name == "hlc3":
        return (high + low + close) / 3.0
    if name == "ohlc4":
        return (open_price + high + low + close) / 4.0
    return close.copy()

def rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    for index in range(length - 1, len(values)):
        if index == length - 1:
            seed = values[:length]
            if np.all(np.isfinite(seed)):
                result[index] = float(np.mean(seed))
        elif np.isfinite(result[index - 1]) and np.isfinite(values[index]):
            result[index] = (result[index - 1] * (length - 1) + values[index]) / length
    return result

def true_range_series():
    result = np.full(n, np.nan, dtype=float)
    for index in range(n):
        if index == 0:
            result[index] = high[index] - low[index]
        else:
            result[index] = max(
                high[index] - low[index],
                abs(high[index] - close[index - 1]),
                abs(low[index] - close[index - 1]),
            )
    return result

def rsi(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= length:
        return result
    changes = np.diff(values)
    gains = np.maximum(changes, 0.0)
    losses = np.maximum(-changes, 0.0)
    average_gain = float(np.mean(gains[:length]))
    average_loss = float(np.mean(losses[:length]))
    for index in range(length, len(values)):
        if index > length:
            average_gain = (average_gain * (length - 1) + gains[index - 1]) / length
            average_loss = (average_loss * (length - 1) + losses[index - 1]) / length
        if average_loss == 0.0:
            result[index] = 100.0 if average_gain > 0.0 else 50.0
        else:
            relative_strength = average_gain / average_loss
            result[index] = 100.0 - 100.0 / (1.0 + relative_strength)
    return result

def wma_at(values, index, length):
    period = max(1, int(length))
    start = index - period + 1
    if start < 0:
        return np.nan
    window = values[start:index + 1]
    if len(window) != period or not np.all(np.isfinite(window)):
        return np.nan
    weights = np.arange(1, period + 1, dtype=float)
    return float(np.dot(window, weights) / np.sum(weights))

def adaptive_hma(values, lengths):
    raw = np.full(len(values), np.nan, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        period = max(2, int(lengths[index]))
        half = wma_at(values, index, max(1, int(period / 2)))
        whole = wma_at(values, index, period)
        if np.isfinite(half) and np.isfinite(whole):
            raw[index] = 2.0 * half - whole
        result[index] = wma_at(raw, index, max(1, int(np.floor(np.sqrt(period)))))
    return result

source = select_source(source_name)
true_range = true_range_series()
atr_5 = rma(true_range, 5)
atr_14 = rma(true_range, 14)
atr_40 = rma(true_range, 40)
atr_46 = rma(true_range, 46)

volume_rsi = rsi(volume, 14)
volume_hma = adaptive_hma(volume_rsi, np.full(n, 10.0, dtype=float))
high_volatility = np.isfinite(atr_14) & np.isfinite(atr_46) & (atr_14 > atr_46)
volume_break = np.isfinite(volume_hma) & (volume_hma > 49.0)
plugged = volume_break if charger == "Volume" else high_volatility

dynamic_lengths = np.full(n, np.nan, dtype=float)
minor_lengths = np.full(n, np.nan, dtype=float)
dynamic_length = (minimum_period + maximum_period) / 2.0
minor_length = (minor_minimum + minor_maximum) / 2.0
for index in range(n):
    if plugged[index]:
        dynamic_length = max(minimum_period, dynamic_length * (1.0 - adaptation_rate))
        minor_length = max(minor_minimum, minor_length * (1.0 - adaptation_rate))
    else:
        dynamic_length = min(maximum_period, dynamic_length * (1.0 + adaptation_rate))
        minor_length = min(minor_maximum, minor_length * (1.0 + adaptation_rate))
    dynamic_lengths[index] = dynamic_length
    minor_lengths[index] = minor_length

dynamic_hma = adaptive_hma(source, dynamic_lengths)
minor_hma = adaptive_hma(source, minor_lengths)

slopes = np.full(n, np.nan, dtype=float)
slope_period = 34
for index in range(n):
    if index < max(slope_period - 1, 2) or not np.isfinite(dynamic_hma[index]) or not np.isfinite(dynamic_hma[index - 2]):
        continue
    highest_high = float(np.max(high[index - slope_period + 1:index + 1]))
    lowest_low = float(np.min(low[index - slope_period + 1:index + 1]))
    price_range = highest_high - lowest_low
    if price_range <= 0.0 or source[index] == 0.0:
        continue
    slope_range = 25.0 / price_range * lowest_low
    delta = (dynamic_hma[index - 2] - dynamic_hma[index]) / source[index] * slope_range
    angle = np.floor(180.0 * np.arccos(1.0 / np.sqrt(1.0 + delta * delta)) / np.pi + 0.5)
    slopes[index] = -float(angle) if delta > 0.0 else float(angle)

def trend_color(index):
    slope = slopes[index]
    if not np.isfinite(slope):
        return "#FDD835"
    if slope >= consolidation_slope:
        return "#6FBF73" if plugged[index] else "#C0F5AE"
    if slope <= -consolidation_slope:
        return "#EB4D5C" if plugged[index] else "#F2B1D4"
    return "#FDD835"

def colored_line(values, enabled=True):
    return [
        {"value": float(values[index]), "color": trend_color(index)}
        if enabled and np.isfinite(values[index]) else None
        for index in range(n)
    ]

upper_half = dynamic_hma + distance_multiplier * atr_40
lower_half = dynamic_hma - distance_multiplier * atr_40
upper_full = dynamic_hma + distance_multiplier * 2.0 * atr_40
lower_full = dynamic_hma - distance_multiplier * 2.0 * atr_40
upper_stop = dynamic_hma + distance_multiplier * 0.5 * atr_40
lower_stop = dynamic_hma - distance_multiplier * 0.5 * atr_40

def numeric_line(values, enabled):
    return [float(value) if enabled and np.isfinite(value) else None for value in values]

plots = [
    {"name": "Adaptive HMA", "data": colored_line(dynamic_hma), "color": "#FDD835", "type": "line", "lineWidth": 3, "overlay": True},
    {"name": "Minor Adaptive HMA", "data": colored_line(minor_hma, show_minor), "color": "#FDD835", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Price Anchor", "title": False, "data": [float(value) for value in close], "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
]

if show_distance_zone:
    plots.extend([
        {"name": "Upper Half Distance", "data": numeric_line(upper_half, True), "color": "rgba(76,175,80,0.45)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower Half Distance", "data": numeric_line(lower_half, True), "color": "rgba(244,67,54,0.45)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Upper Full Distance", "data": numeric_line(upper_full, True), "color": "rgba(158,158,158,0.45)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower Full Distance", "data": numeric_line(lower_full, True), "color": "rgba(158,158,158,0.45)", "type": "line", "lineWidth": 1, "overlay": True},
        {
            "name": "Adaptive Distance Core",
            "data": [
                {"upper": float(upper_stop[index]), "lower": float(lower_stop[index])}
                if np.isfinite(upper_stop[index]) and np.isfinite(lower_stop[index]) else None
                for index in range(n)
            ],
            "color": "rgba(156,39,176,0.16)",
            "fillColor": "rgba(156,39,176,0.16)",
            "type": "cloud",
            "overlay": True,
        },
    ])

up_state = np.isfinite(slopes) & (slopes >= consolidation_slope) & plugged
down_state = np.isfinite(slopes) & (slopes <= -consolidation_slope) & plugged
buy = up_state & ~np.concatenate(([False], up_state[:-1]))
sell = down_state & ~np.concatenate(([False], down_state[:-1]))
up_exit = np.isfinite(slopes) & (slopes >= consolidation_slope) & ~plugged & np.isfinite(minor_hma) & (close < minor_hma)
down_exit = np.isfinite(slopes) & (slopes <= -consolidation_slope) & ~plugged & np.isfinite(minor_hma) & (close > minor_hma)
fast_exit = (up_exit & ~np.concatenate(([False], up_exit[:-1]))) | (down_exit & ~np.concatenate(([False], down_exit[:-1])))
top_warning = np.isfinite(upper_full) & (high > upper_full)
bottom_warning = np.isfinite(lower_full) & (low < lower_full)
warning = (top_warning & ~np.concatenate(([False], top_warning[:-1]))) | (bottom_warning & ~np.concatenate(([False], bottom_warning[:-1])))

atr_position = 0.72 * atr_5

def event_points(mask, values, text, color):
    return [
        {"value": float(values[index]), "text": text, "color": color}
        if show_signals and mask[index] and np.isfinite(values[index]) else None
        for index in range(n)
    ]

buy_prices = dynamic_hma - atr_position
sell_prices = dynamic_hma + atr_position
exit_prices = np.where(up_exit, minor_hma + atr_position, minor_hma - atr_position)
warning_prices = np.where(top_warning, high + atr_position, low - atr_position)
signals = [
    {"type": "buy", "name": "Adaptive HMA Buy", "text": "⬆", "color": "#4CAF50", "data": event_points(buy, buy_prices, "⬆", "#4CAF50"), "renderMode": "events"},
    {"type": "sell", "name": "Adaptive HMA Sell", "text": "⬇", "color": "#F44336", "data": event_points(sell, sell_prices, "⬇", "#F44336"), "renderMode": "events"},
    {"type": "sell", "name": "Bull Trend Fast Exit", "text": "ⓧ", "color": "#4CAF50", "data": event_points(fast_exit & up_exit, exit_prices, "ⓧ", "#4CAF50"), "renderMode": "events"},
    {"type": "buy", "name": "Bear Trend Fast Exit", "text": "ⓧ", "color": "#F44336", "data": event_points(fast_exit & down_exit, exit_prices, "ⓧ", "#F44336"), "renderMode": "events"},
    {"type": "sell", "name": "Upper Distance Warning", "text": "⚠", "color": "#FF9800", "data": event_points(warning & top_warning, warning_prices, "⚠", "#FF9800"), "renderMode": "events"},
    {"type": "buy", "name": "Lower Distance Warning", "text": "⚠", "color": "#FF9800", "data": event_points(warning & bottom_warning, warning_prices, "⚠", "#FF9800"), "renderMode": "events"},
]

background_colors = []
for index in range(n):
    if not use_background or plugged[index] or not np.isfinite(slopes[index]):
        background_colors.append(None)
    elif -consolidation_slope < slopes[index] < consolidation_slope:
        background_colors.append("rgba(117,119,121,0.14)")
    else:
        background_colors.append("rgba(175,180,185,0.12)")

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "description": my_indicator_description,
    "plots": plots,
    "signals": signals,
    "background_colors": background_colors,
    "calculatedVars": {
        "charger": charger,
        "dynamic_length": float(dynamic_lengths[-1]) if n else None,
        "minor_length": float(minor_lengths[-1]) if n else None,
        "slope": float(slopes[-1]) if n and np.isfinite(slopes[-1]) else None,
    },
}
