# ============================================================
# QuantDinger Logarithmic Regression Channel Trend
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Logarithmic Regression Channel Trend"
my_indicator_description = "Tracks a rolling log-price regression, delayed trend changes, and the latest standard-deviation channel."

# @param lookback int 100 Logarithmic regression lookback range=2:2000:1
# @param channel_width float 1.5 Channel deviation width range=0:20:0.1
# @param channel_length int 100 Latest channel span range=1:1000:1
# @param show_mid_line bool true Show dashed channel midpoint
# @param fill_band bool true Fill short-term regression trend band
# @param up_color color #21DFAC Up-trend color
# @param down_color color #DF216D Down-trend color

lookback = min(2000, max(2, int(params.get("lookback", 100))))
channel_width = min(20.0, max(0.0, float(params.get("channel_width", 1.5))))
channel_length = min(1000, max(1, int(params.get("channel_length", 100))))
show_mid_line = bool(params.get("show_mid_line", True))
fill_band = bool(params.get("fill_band", True))
up_color = str(params.get("up_color", "#21DFAC"))
down_color = str(params.get("down_color", "#DF216D"))

df = df.copy()
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def _hex_rgb(color):
    value = str(color).strip().lstrip("#")
    if len(value) != 6:
        return (128, 128, 128)
    try:
        return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return (128, 128, 128)


def _rgba(color, opacity):
    red, green, blue = _hex_rgb(color)
    alpha = min(1.0, max(0.0, float(opacity)))
    return f"rgba({red},{green},{blue},{alpha:.3f})"


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _rolling_extreme(values, length, maximum):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        start_index = max(0, index - length + 1)
        window = values[start_index:index + 1]
        finite_window = window[np.isfinite(window)]
        if len(finite_window):
            result[index] = float(np.max(finite_window) if maximum else np.min(finite_window))
    return result


slope = np.full(n, np.nan, dtype=float)
intercept = np.full(n, np.nan, dtype=float)
regression_start = np.full(n, np.nan, dtype=float)
regression_end = np.full(n, np.nan, dtype=float)
deviation = np.full(n, np.nan, dtype=float)
x = np.arange(1, lookback + 1, dtype=float)
sum_x = float(np.sum(x))
sum_x_squared = float(np.sum(x * x))
denominator = lookback * sum_x_squared - sum_x * sum_x
for index in range(lookback - 1, n):
    price_window = close[index - lookback + 1:index + 1]
    if not np.all(np.isfinite(price_window)) or np.any(price_window <= 0.0):
        continue
    reverse_log = np.log(price_window[::-1])
    sum_y = float(np.sum(reverse_log))
    current_slope = float((lookback * np.sum(x * reverse_log) - sum_x * sum_y) / denominator)
    current_intercept = float(sum_y / lookback - current_slope * sum_x / lookback + current_slope)
    slope[index] = current_slope
    intercept[index] = current_intercept
    regression_start[index] = float(np.exp(current_intercept + current_slope * lookback))
    regression_end[index] = float(np.exp(current_intercept))
    deviation[index] = float(np.std(price_window, ddof=0))

delayed_end = np.full(n, np.nan, dtype=float)
if n > 3:
    delayed_end[3:] = regression_end[:-3]
trend_difference = regression_end - delayed_end
up_signal = np.zeros(n, dtype=bool)
down_signal = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(trend_difference[index]) and np.isfinite(trend_difference[index - 1]):
        up_signal[index] = trend_difference[index] > 0.0 and trend_difference[index - 1] <= 0.0
        down_signal[index] = trend_difference[index] < 0.0 and trend_difference[index - 1] >= 0.0

lowest_difference = _rolling_extreme(trend_difference, 200, False)
highest_difference = _rolling_extreme(trend_difference, 200, True)
trend_line_colors = []
trend_fill_colors = []
for index in range(n):
    difference = trend_difference[index]
    if not np.isfinite(difference):
        trend_line_colors.append(None)
        trend_fill_colors.append("rgba(0,0,0,0)")
        continue
    trend_line_colors.append(up_color if difference > 0.0 else down_color)
    low_value = lowest_difference[index]
    high_value = highest_difference[index]
    ratio = 0.5 if not np.all(np.isfinite([low_value, high_value])) or high_value == low_value else min(1.0, max(0.0, (difference - low_value) / (high_value - low_value)))
    trend_fill_colors.append(_rgba(up_color, ratio) if difference > 0.0 else _rgba(down_color, 1.0 - ratio))

channel_start = np.full(n, np.nan, dtype=float)
channel_end = np.full(n, np.nan, dtype=float)
channel_outer_upper = np.full(n, np.nan, dtype=float)
channel_inner_upper = np.full(n, np.nan, dtype=float)
channel_inner_lower = np.full(n, np.nan, dtype=float)
channel_outer_lower = np.full(n, np.nan, dtype=float)
channel_color = None
channel_label = None
channel_ready = n > channel_length and np.all(np.isfinite([regression_start[-1], regression_end[-1], deviation[-1]]))
if channel_ready:
    first_index = n - 1 - channel_length
    base_values = np.linspace(regression_start[-1], regression_end[-1], channel_length + 1)
    channel_start[first_index:] = base_values
    outer_offset = deviation[-1] * (channel_width + 0.3)
    inner_offset = deviation[-1] * channel_width
    channel_end[first_index:] = base_values
    channel_outer_upper[first_index:] = base_values + outer_offset
    channel_inner_upper[first_index:] = base_values + inner_offset
    channel_inner_lower[first_index:] = base_values - inner_offset
    channel_outer_lower[first_index:] = base_values - outer_offset
    channel_color = up_color if regression_end[-1] > regression_start[-1] else down_color
    channel_label = {
        "type": "label", "index": int(first_index), "price": float(channel_outer_lower[first_index]),
        "text": "UP" if regression_end[-1] > regression_start[-1] else "DN", "textColor": "#070F23",
        "color": channel_color, "side": "right", "direction": "down" if regression_end[-1] < regression_start[-1] else "up",
    }

transparent = ["rgba(0,0,0,0)"] * n
short_band_data = [
    {"upper": float(regression_end[index]), "lower": float(delayed_end[index]), "color": trend_fill_colors[index]}
    if fill_band and np.all(np.isfinite([regression_end[index], delayed_end[index]])) else None
    for index in range(n)
]
upper_channel_fill = [
    {"upper": float(channel_outer_upper[index]), "lower": float(channel_inner_upper[index]), "color": channel_color}
    if channel_ready and np.all(np.isfinite([channel_outer_upper[index], channel_inner_upper[index]])) else None
    for index in range(n)
]
lower_channel_fill = [
    {"upper": float(channel_inner_lower[index]), "lower": float(channel_outer_lower[index]), "color": channel_color}
    if channel_ready and np.all(np.isfinite([channel_inner_lower[index], channel_outer_lower[index]])) else None
    for index in range(n)
]

plots = [
    {"name": "Log Regression", "data": _finite(regression_end), "color": trend_line_colors, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Regression Trend Fill", "title": False, "data": short_band_data, "color": up_color, "type": "cloud", "opacity": 1.0, "overlay": True},
    {"name": "Channel Mid", "data": _finite(channel_start) if show_mid_line else [None] * n, "color": "#E5E7EB", "type": "line", "lineWidth": 1, "dashed": True, "overlay": True},
    {"name": "Channel Outer Upper", "data": _finite(channel_outer_upper), "color": channel_color or "#808080", "type": "line", "lineWidth": 3, "overlay": True},
    {"name": "Channel Outer Lower", "data": _finite(channel_outer_lower), "color": channel_color or "#808080", "type": "line", "lineWidth": 3, "overlay": True},
    {"name": "Channel Upper Fill", "title": False, "data": upper_channel_fill, "color": channel_color or "#808080", "type": "cloud", "opacity": 0.30, "overlay": True},
    {"name": "Channel Lower Fill", "title": False, "data": lower_channel_fill, "color": channel_color or "#808080", "type": "cloud", "opacity": 0.30, "overlay": True},
]
signals = [
    {"type": "buy", "name": "Log Regression Up", "text": "×", "data": [float(regression_end[index]) if up_signal[index] else None for index in range(n)], "color": "#E5E7EB", "renderMode": "events"},
    {"type": "sell", "name": "Log Regression Down", "text": "×", "data": [float(regression_end[index]) if down_signal[index] else None for index in range(n)], "color": "#E5E7EB", "renderMode": "events"},
]

latest_slope = float(slope[-1] * 100.0) if n and np.isfinite(slope[-1]) else np.nan
latest_channel_direction = "UP" if channel_ready and regression_end[-1] > regression_start[-1] else "DN" if channel_ready else "N/A"
latest_regression_direction = "UP" if n > 3 and np.isfinite(trend_difference[-1]) and trend_difference[-1] > 0.0 else "DN" if n > 3 and np.isfinite(trend_difference[-1]) else "N/A"
tables = [{
    "title": "Logarithmic Regression", "position": "top-right", "compact": True,
    "rows": [
        ["Slope", f"{latest_slope:,.3f}" if np.isfinite(latest_slope) else "N/A"],
        ["Channel", latest_channel_direction],
        ["Regression", latest_regression_direction],
    ],
}]

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals,
    "layers": [channel_label] if channel_label is not None else [], "tables": tables,
    "bar_colors": [None] * n, "background_colors": transparent,
    "calculatedVars": {
        "slope": _finite(slope), "intercept": _finite(intercept), "start": _finite(regression_start), "end": _finite(regression_end),
        "delayedEnd": _finite(delayed_end), "difference": _finite(trend_difference), "deviation": _finite(deviation),
        "lowestDifference": _finite(lowest_difference), "highestDifference": _finite(highest_difference),
        "upSignal": up_signal.tolist(), "downSignal": down_signal.tolist(), "trendLineColors": trend_line_colors, "trendFillColors": trend_fill_colors,
        "channelMid": _finite(channel_start), "channelOuterUpper": _finite(channel_outer_upper), "channelInnerUpper": _finite(channel_inner_upper),
        "channelInnerLower": _finite(channel_inner_lower), "channelOuterLower": _finite(channel_outer_lower), "channelColor": channel_color,
    },
}
