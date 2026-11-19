# ============================================================
# QuantDinger Adaptive Ehlers Percentile
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Adaptive Ehlers Percentile"
my_indicator_description = (
    "Filters a volatility-adaptive moving average with nonlinear Ehlers momentum weights, then builds "
    "nearest-rank percentile bands for regime changes, candle colors, and reversal signals."
)

# @param source str close Price source values=close,open,high,low,hl2,hlc3,ohlc4
# @param minimum_ma_period int 3 Minimum adaptive period range=1:500:1
# @param maximum_ma_period int 30 Maximum adaptive period range=2:800:1
# @param volatility_period int 9 Volatility period range=2:500:1
# @param filter_length int 9 Ehlers filter length range=2:500:1
# @param momentum_length int 15 Ehlers momentum length range=1:500:1
# @param percentile_length int 50 Percentile length range=2:1000:1
# @param percentile_level float 75 Percentile level range=1:99:1
# @param upper_multiplier float 0.7 Upper band multiplier range=0.1:20.0:0.1
# @param lower_multiplier float 0.7 Lower band multiplier range=0.1:20.0:0.1
# @param show_filter bool true Show adaptive Ehlers filter
# @param show_bands bool true Show percentile bands
# @param show_fill bool true Fill percentile bands
# @param show_signals bool true Show regime signals
# @param color_candles bool true Color chart candles
# @param bullish_color str #39FF14 Bullish color
# @param bearish_color str #8A2BE2 Bearish color

source_name = str(params.get("source", "close"))
if source_name not in ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4"):
    source_name = "close"
minimum_ma_period = min(500, max(1, int(params.get("minimum_ma_period", 3))))
maximum_ma_period = min(800, max(2, int(params.get("maximum_ma_period", 30))))
volatility_period = min(500, max(2, int(params.get("volatility_period", 9))))
filter_length = min(500, max(2, int(params.get("filter_length", 9))))
momentum_length = min(500, max(1, int(params.get("momentum_length", 15))))
percentile_length = min(1000, max(2, int(params.get("percentile_length", 50))))
percentile_level = min(99.0, max(1.0, float(params.get("percentile_level", 75.0))))
upper_multiplier = min(20.0, max(0.1, float(params.get("upper_multiplier", 0.7))))
lower_multiplier = min(20.0, max(0.1, float(params.get("lower_multiplier", 0.7))))
show_filter = bool(params.get("show_filter", True))
show_bands = bool(params.get("show_bands", True))
show_fill = bool(params.get("show_fill", True))
show_signals = bool(params.get("show_signals", True))
color_candles = bool(params.get("color_candles", True))
bullish_color = str(params.get("bullish_color", "#39FF14"))
bearish_color = str(params.get("bearish_color", "#8A2BE2"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
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

def sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result

def rolling_stdev(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.std(window, ddof=0))
    return result

def nearest_rank(values, length, level):
    result = np.full(len(values), np.nan, dtype=float)
    rank_index = max(0, min(length - 1, int(np.ceil(level / 100.0 * length)) - 1))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.sort(window)[rank_index])
    return result

def rgba(color, alpha):
    raw = str(color).strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(char * 2 for char in raw)
    if len(raw) != 6:
        return color
    try:
        red = int(raw[0:2], 16)
        green = int(raw[2:4], 16)
        blue = int(raw[4:6], 16)
    except ValueError:
        return color
    return "rgba(%d,%d,%d,%.4f)" % (red, green, blue, alpha)

source = select_source(source_name)
delta = np.full(n, np.nan, dtype=float)
if n > 1:
    delta[1:] = np.diff(source)
volatility = rolling_stdev(delta, volatility_period)
average_volatility = sma(volatility, volatility_period)
adaptive_length = np.full(n, float(maximum_ma_period), dtype=float)
adaptive_average = np.full(n, np.nan, dtype=float)

for index in range(n):
    current_volatility = volatility[index]
    current_average = average_volatility[index]
    if np.isfinite(current_volatility) and np.isfinite(current_average):
        minimum_volatility = current_average * 0.25
        maximum_volatility = current_average * 1.75
        volatility_range = maximum_volatility - minimum_volatility
        if volatility_range > 0.0:
            if current_volatility <= minimum_volatility:
                adaptive_length[index] = float(maximum_ma_period)
            elif current_volatility >= maximum_volatility:
                adaptive_length[index] = float(minimum_ma_period)
            else:
                adaptive_length[index] = float(maximum_ma_period) - float(maximum_ma_period - minimum_ma_period) * (current_volatility - minimum_volatility) / volatility_range
    alpha = 2.0 / (adaptive_length[index] + 1.0)
    if index == 0:
        adaptive_average[index] = source[index]
    else:
        adaptive_average[index] = adaptive_average[index - 1] + alpha * (source[index] - adaptive_average[index - 1])

filter_fallback = sma(adaptive_average, filter_length)
trend_filter = np.full(n, np.nan, dtype=float)
for index in range(n):
    numerator = 0.0
    coefficient_sum = 0.0
    for offset in range(filter_length):
        current_index = index - offset
        reference_index = current_index - momentum_length
        if current_index < 0 or reference_index < 0:
            continue
        current_sample = adaptive_average[current_index]
        reference_sample = adaptive_average[reference_index]
        if not np.isfinite(current_sample) or not np.isfinite(reference_sample):
            continue
        coefficient = abs(current_sample - reference_sample)
        numerator += coefficient * current_sample
        coefficient_sum += coefficient
    if coefficient_sum > 0.0:
        trend_filter[index] = numerator / coefficient_sum
    elif np.isfinite(filter_fallback[index]):
        trend_filter[index] = filter_fallback[index]

deviation = np.abs(source - trend_filter)
percentile_distance = nearest_rank(deviation, percentile_length, percentile_level)
upper_band = trend_filter + percentile_distance * upper_multiplier
lower_band = trend_filter - percentile_distance * lower_multiplier

state = np.zeros(n, dtype=int)
long_signal = np.zeros(n, dtype=bool)
short_signal = np.zeros(n, dtype=bool)
for index in range(n):
    previous_state = int(state[index - 1]) if index > 0 else 0
    current_state = previous_state
    if np.isfinite(upper_band[index]) and source[index] > upper_band[index]:
        current_state = 1
    elif np.isfinite(lower_band[index]) and source[index] < lower_band[index]:
        current_state = -1
    state[index] = current_state
    if index > 0:
        long_signal[index] = current_state == 1 and previous_state != 1
        short_signal[index] = current_state == -1 and previous_state != -1

regime_colors = [bullish_color if state[index] == 1 else bearish_color for index in range(n)]

def line_points(values, enabled=True, colors=None):
    points = []
    for index in range(n):
        if not enabled or not np.isfinite(values[index]):
            points.append(None)
        elif colors is None:
            points.append(float(values[index]))
        else:
            points.append({"value": float(values[index]), "color": colors[index]})
    return points

plots = [
    {"name": "Adaptive Ehlers Filter", "data": line_points(trend_filter, show_filter, regime_colors), "color": bullish_color, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Upper Percentile Band", "data": line_points(upper_band, show_bands), "color": rgba(bullish_color, 0.65), "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Lower Percentile Band", "data": line_points(lower_band, show_bands), "color": rgba(bearish_color, 0.65), "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Price Anchor", "title": False, "data": [float(value) for value in close], "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
]

if show_bands and show_fill:
    plots.append({
        "name": "Percentile Band Fill",
        "data": [
            {"upper": float(upper_band[index]), "lower": float(lower_band[index]), "color": rgba(regime_colors[index], 0.08)}
            if np.isfinite(upper_band[index]) and np.isfinite(lower_band[index]) else None
            for index in range(n)
        ],
        "color": rgba(bullish_color, 0.08),
        "fillColor": rgba(bullish_color, 0.08),
        "type": "cloud",
        "overlay": True,
    })

def event_points(mask, values, text, color):
    return [
        {"value": float(values[index]), "text": text, "color": color}
        if show_signals and mask[index] and np.isfinite(values[index]) else None
        for index in range(n)
    ]

signals = [
    {"type": "buy", "name": "Ehlers Long", "text": "LONG", "color": bullish_color, "data": event_points(long_signal, low, "LONG", bullish_color), "renderMode": "events"},
    {"type": "sell", "name": "Ehlers Short", "text": "SHORT", "color": bearish_color, "data": event_points(short_signal, high, "SHORT", bearish_color), "renderMode": "events"},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "description": my_indicator_description,
    "plots": plots,
    "signals": signals,
    "bar_colors": regime_colors if color_candles else [None] * n,
    "calculatedVars": {
        "adaptive_length": float(adaptive_length[-1]) if n else None,
        "percentile_distance": float(percentile_distance[-1]) if n and np.isfinite(percentile_distance[-1]) else None,
        "state": int(state[-1]) if n else None,
    },
}
