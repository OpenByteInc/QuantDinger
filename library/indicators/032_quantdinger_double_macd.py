# ============================================================
# QuantDinger Double MACD
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Double MACD"
my_indicator_description = (
    "Dual-speed MACD momentum with volatility and choppiness zones, regular "
    "or hidden divergences, and trend or counter-trend entries."
)

# @param trend_entries bool true Show trend entries
# @param countertrend_entries bool false Show counter-trend entries
# @param atlas_zone bool true Show low-bandwidth zone
# @param choppiness_zone bool true Show choppiness zone
# @param choppiness_level float 50.0 Choppiness level range=1.0:99.0:1.0
# @param regular_bullish bool true Show regular bullish divergences
# @param regular_bearish bool true Show regular bearish divergences
# @param hidden_bullish bool false Show hidden bullish divergences
# @param hidden_bearish bool false Show hidden bearish divergences
# @param divergence_labels bool true Show divergence labels

trend_entries = bool(params.get("trend_entries", True))
countertrend_entries = bool(params.get("countertrend_entries", False))
atlas_zone = bool(params.get("atlas_zone", True))
choppiness_zone = bool(params.get("choppiness_zone", True))
choppiness_level = max(1.0, min(99.0, float(params.get("choppiness_level", 50.0))))
regular_bullish = bool(params.get("regular_bullish", True))
regular_bearish = bool(params.get("regular_bearish", True))
hidden_bullish = bool(params.get("hidden_bullish", False))
hidden_bearish = bool(params.get("hidden_bearish", False))
divergence_labels = bool(params.get("divergence_labels", True))

df = df.copy()
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
n = len(df)


def pine_ema(series, period):
    values = np.asarray(series, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


close_values = close.to_numpy(dtype=float)
macd_slow = pine_ema(close_values, 12) - pine_ema(close_values, 26)
macd_fast = pine_ema(close_values, 5) - pine_ema(close_values, 15)

basis = close.rolling(window=20, min_periods=20).mean().to_numpy(dtype=float)
deviation = close.rolling(window=20, min_periods=20).std(ddof=0).to_numpy(dtype=float) * 2.0
upper_band = basis + deviation
lower_band = basis - deviation
bandwidth = np.full(n, np.nan, dtype=float)
valid_upper = np.isfinite(upper_band) & (upper_band > 0.0)
bandwidth[valid_upper] = np.sqrt(np.maximum((upper_band[valid_upper] - lower_band[valid_upper]) / upper_band[valid_upper], 0.0)) * 20.0
bandwidth_average = pine_ema(bandwidth, 120)
atlas_active = np.where(np.isfinite(bandwidth_average), bandwidth <= bandwidth_average * 0.8, True)

previous_close = close.shift(1)
true_range = pd.concat(
    [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
).max(axis=1, skipna=True)
range_sum = true_range.rolling(window=14, min_periods=14).sum()
range_low = pd.concat([low, previous_close], axis=1).min(axis=1).rolling(window=14, min_periods=14).min()
range_high = pd.concat([high, previous_close], axis=1).max(axis=1).rolling(window=14, min_periods=14).max()
height = range_high - range_low
choppiness = 100.0 * np.log10(range_sum / height.replace(0.0, np.nan)) / np.log10(14.0)
choppy_active = choppiness.to_numpy(dtype=float) >= choppiness_level

top_fractal = np.zeros(n, dtype=bool)
bottom_fractal = np.zeros(n, dtype=bool)
for index in range(4, n):
    window = macd_fast[index - 4 : index + 1]
    if np.all(np.isfinite(window)):
        top_fractal[index] = window[0] < window[2] and window[1] < window[2] and window[2] > window[3] and window[2] > window[4]
        bottom_fractal[index] = window[0] > window[2] and window[1] > window[2] and window[2] < window[3] and window[2] < window[4]

high_values = high.to_numpy(dtype=float)
low_values = low.to_numpy(dtype=float)
regular_bear_event = np.zeros(n, dtype=bool)
hidden_bear_event = np.zeros(n, dtype=bool)
regular_bull_event = np.zeros(n, dtype=bool)
hidden_bull_event = np.zeros(n, dtype=bool)
top_history = []
bottom_history = []
for index in range(n):
    if top_fractal[index]:
        eligible = [item for item in top_history if item[0] <= index - 2]
        if eligible:
            _, previous_value, previous_price = eligible[-1]
            pivot_index = index - 2
            regular_bear_event[index] = high_values[pivot_index] > previous_price and macd_fast[pivot_index] < previous_value
            hidden_bear_event[index] = high_values[pivot_index] < previous_price and macd_fast[pivot_index] > previous_value
        top_history.append((index, float(macd_fast[index - 2]), float(high_values[index - 2])))
    if bottom_fractal[index]:
        eligible = [item for item in bottom_history if item[0] <= index - 2]
        if eligible:
            _, previous_value, previous_price = eligible[-1]
            pivot_index = index - 2
            regular_bull_event[index] = low_values[pivot_index] < previous_price and macd_fast[pivot_index] > previous_value
            hidden_bull_event[index] = low_values[pivot_index] > previous_price and macd_fast[pivot_index] < previous_value
        bottom_history.append((index, float(macd_fast[index - 2]), float(low_values[index - 2])))

cross_zero_up = np.concatenate(([False], (macd_fast[1:] > 0.0) & (macd_fast[:-1] <= 0.0)))
cross_zero_down = np.concatenate(([False], (macd_fast[1:] < 0.0) & (macd_fast[:-1] >= 0.0)))
cross_pair_up = np.concatenate(([False], (macd_fast[1:] > macd_slow[1:]) & (macd_fast[:-1] <= macd_slow[:-1])))
cross_pair_down = np.concatenate(([False], (macd_fast[1:] < macd_slow[1:]) & (macd_fast[:-1] >= macd_slow[:-1])))
slow_rising = np.concatenate(([False], macd_slow[1:] > macd_slow[:-1]))
slow_falling = np.concatenate(([False], macd_slow[1:] < macd_slow[:-1]))
trend_long = trend_entries & cross_zero_up & (macd_slow < macd_fast) & slow_rising & (macd_slow < 0.0)
trend_short = trend_entries & cross_zero_down & (macd_slow > macd_fast) & slow_falling & (macd_slow > 0.0)
counter_long = countertrend_entries & cross_pair_up & (macd_fast < 0.0)
counter_short = countertrend_entries & cross_pair_down & (macd_fast > 0.0)

rolling_scale = pd.Series(macd_fast).rolling(window=100, min_periods=1).max().to_numpy(dtype=float) * 1.5
rolling_scale = np.where(np.isfinite(rolling_scale), rolling_scale, 0.0)
atlas_cloud = []
choppy_cloud = []
for index in range(n):
    bounds = {"upper": float(rolling_scale[index]), "lower": float(-rolling_scale[index]), "color": "#FF0000"}
    atlas_cloud.append(bounds if atlas_zone and bool(atlas_active[index]) else None)
    choppy_cloud.append(bounds if choppiness_zone and bool(choppy_active[index]) else None)

slow_line = [float(value) if np.isfinite(value) else None for value in macd_slow]
fast_line = [float(value) if np.isfinite(value) else None for value in macd_fast]
slow_histogram = [{"value": float(value), "color": "#C0C0C0"} if np.isfinite(value) else None for value in macd_slow]
fast_histogram = [{"value": float(value), "color": "#FFFF00"} if np.isfinite(value) else None for value in macd_fast]
bear_divergence_plot = [None for _ in range(n)]
bull_divergence_plot = [None for _ in range(n)]
regular_bear_marks = [None for _ in range(n)]
hidden_bear_marks = [None for _ in range(n)]
regular_bull_marks = [None for _ in range(n)]
hidden_bull_marks = [None for _ in range(n)]
for index in range(n):
    bear_visible = (regular_bearish and regular_bear_event[index]) or (hidden_bearish and hidden_bear_event[index])
    bull_visible = (regular_bullish and regular_bull_event[index]) or (hidden_bullish and hidden_bull_event[index])
    if bear_visible and index >= 2:
        pivot_index = index - 2
        bear_divergence_plot[pivot_index] = float(macd_fast[pivot_index])
        if divergence_labels:
            marker_value = float(macd_fast[index - 1] + rolling_scale[index] / 8.0)
            if regular_bearish and regular_bear_event[index]:
                regular_bear_marks[pivot_index] = marker_value
            if hidden_bearish and hidden_bear_event[index]:
                hidden_bear_marks[pivot_index] = marker_value
    if bull_visible and index >= 2:
        pivot_index = index - 2
        bull_divergence_plot[pivot_index] = float(macd_fast[pivot_index])
        if divergence_labels:
            marker_value = float(macd_fast[index - 1] - rolling_scale[index] / 8.0)
            if regular_bullish and regular_bull_event[index]:
                regular_bull_marks[pivot_index] = marker_value
            if hidden_bullish and hidden_bull_event[index]:
                hidden_bull_marks[pivot_index] = marker_value

long_marks = [float(macd_fast[index] - rolling_scale[index] * 0.1875) if trend_long[index] or counter_long[index] else None for index in range(n)]
short_marks = [float(macd_fast[index] + rolling_scale[index] * 0.1875) if trend_short[index] or counter_short[index] else None for index in range(n)]


def marker_points(values, color, size=6.0):
    return [
        {"value": float(value), "color": color, "size": float(size)}
        if value is not None and np.isfinite(value)
        else None
        for value in values
    ]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Zero Line", "title": False, "data": [0.0 for _ in range(n)], "color": "#000000", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "MACD Slow Line", "data": slow_line, "color": "#C0C0C0", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "MACD Slow Histogram", "title": False, "data": slow_histogram, "color": "#C0C0C0", "type": "histogram", "baseValue": 0.0, "lineWidth": 1, "overlay": False},
        {"name": "MACD Fast Line", "data": fast_line, "color": "#FFFF00", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "MACD Fast Histogram", "title": False, "data": fast_histogram, "color": "#FFFF00", "type": "histogram", "baseValue": 0.0, "lineWidth": 1, "overlay": False},
        {"name": "Atlas Zone", "title": False, "data": atlas_cloud, "color": "#FF0000", "type": "cloud", "opacity": 0.10, "overlay": False},
        {"name": "Choppiness Zone", "title": False, "data": choppy_cloud, "color": "#FF0000", "type": "cloud", "opacity": 0.10, "overlay": False},
        {"name": "Bearish Divergence", "title": False, "data": bear_divergence_plot, "color": "#FF0000", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Bullish Divergence", "title": False, "data": bull_divergence_plot, "color": "#0AAC00", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Long Entry Marker", "title": False, "data": marker_points(long_marks, "#0AAC00", 6.0), "color": "#0AAC00", "type": "scatter", "overlay": False},
        {"name": "Short Entry Marker", "title": False, "data": marker_points(short_marks, "#FF0000", 6.0), "color": "#FF0000", "type": "scatter", "overlay": False},
        {"name": "Regular Bull Marker", "title": False, "data": marker_points(regular_bull_marks, "#0AAC00", 7.0), "color": "#0AAC00", "type": "scatter", "overlay": False},
        {"name": "Hidden Bull Marker", "title": False, "data": marker_points(hidden_bull_marks, "rgba(10,172,0,0.55)", 6.0), "color": "#0AAC00", "type": "scatter", "overlay": False},
        {"name": "Regular Bear Marker", "title": False, "data": marker_points(regular_bear_marks, "#FF0000", 7.0), "color": "#FF0000", "type": "scatter", "overlay": False},
        {"name": "Hidden Bear Marker", "title": False, "data": marker_points(hidden_bear_marks, "rgba(255,0,0,0.55)", 6.0), "color": "#FF0000", "type": "scatter", "overlay": False},
    ],
    "signals": [
        {"type": "buy", "text": "LONG", "name": "Long Entry", "data": long_marks, "color": "#0AAC00", "renderMode": "hidden"},
        {"type": "sell", "text": "SHORT", "name": "Short Entry", "data": short_marks, "color": "#FF0000", "renderMode": "hidden"},
        {"type": "buy", "text": "BULL R", "name": "Regular Bullish Divergence", "data": regular_bull_marks, "color": "#0AAC00", "renderMode": "hidden"},
        {"type": "buy", "text": "BULL H", "name": "Hidden Bullish Divergence", "data": hidden_bull_marks, "color": "#0AAC00", "renderMode": "hidden"},
        {"type": "sell", "text": "BEAR R", "name": "Regular Bearish Divergence", "data": regular_bear_marks, "color": "#FF0000", "renderMode": "hidden"},
        {"type": "sell", "text": "BEAR H", "name": "Hidden Bearish Divergence", "data": hidden_bear_marks, "color": "#FF0000", "renderMode": "hidden"},
    ],
    "layers": [],
    "tables": [],
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "macdSlow": [float(value) if np.isfinite(value) else None for value in macd_slow],
        "macdFast": [float(value) if np.isfinite(value) else None for value in macd_fast],
        "bandwidth": [float(value) if np.isfinite(value) else None for value in bandwidth],
        "bandwidthAverage": [float(value) if np.isfinite(value) else None for value in bandwidth_average],
        "atlasActive": [bool(value) for value in atlas_active],
        "choppiness": [float(value) if np.isfinite(value) else None for value in choppiness.to_numpy(dtype=float)],
        "choppyActive": [bool(value) for value in choppy_active],
        "regularBearish": [bool(value) for value in regular_bear_event],
        "hiddenBearish": [bool(value) for value in hidden_bear_event],
        "regularBullish": [bool(value) for value in regular_bull_event],
        "hiddenBullish": [bool(value) for value in hidden_bull_event],
        "trendLong": [bool(value) for value in trend_long],
        "trendShort": [bool(value) for value in trend_short],
        "counterLong": [bool(value) for value in counter_long],
        "counterShort": [bool(value) for value in counter_short],
        "rollingScale": [float(value) for value in rolling_scale],
    },
}
