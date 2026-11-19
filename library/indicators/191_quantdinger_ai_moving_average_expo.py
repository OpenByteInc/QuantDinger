# ============================================================
# QuantDinger AI Moving Average Expo
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger AI Moving Average Expo"
my_indicator_description = (
    "Combines a locally computed weighted-neighbor trend classifier, segmented regression "
    "slope, and nearest-history moving averages. No external AI or model service is required."
)

# @param show_classifier_average bool false Show classified moving average
# @param show_regression_slope bool true Show segmented regression slope
# @param average_type str SMA Moving average type values=SMA,EMA,WMA,RMA,HMA,VWMA
# @param average_length int 50 Moving average length range=1:500:1
# @param neighbors int 5 Weighted neighbors range=1:100:1
# @param data_points int 10 Classifier history points range=1:100:1
# @param show_price_neighbor_average bool false Show price-neighbor average
# @param show_average_neighbor_average bool true Show average-neighbor average
# @param closest_values int 15 Closest values range=2:200:1
# @param smoothing_period int 20 Neighbor target smoothing range=2:500:1
# @param classify_source str close Classification source values=close,open,high,low,hl2,hlc3,ohlc4
# @param average_source str close Moving average source values=close,open,high,low,hl2,hlc3,ohlc4
# @param show_trend_zones bool false Show classifier regime zones
# @param keep_historical_zones bool false Keep historical regime zones
# @param max_historical_zones int 20 Maximum historical zones range=1:100:1
# @param bullish_color str #00C853 Bullish color
# @param bearish_color str #FF1744 Bearish color
# @param neutral_color str #0088FF Neutral color
# @param slope_color str #FFA927 Zone midpoint color
# @param price_neighbor_color str #1FC7FF Price-neighbor average color
# @param average_neighbor_color str #FDD835 Average-neighbor color

show_classifier_average = bool(params.get("show_classifier_average", False))
show_regression_slope = bool(params.get("show_regression_slope", True))
average_type = str(params.get("average_type", "SMA")).upper()
if average_type not in ("SMA", "EMA", "WMA", "RMA", "HMA", "VWMA"):
    average_type = "SMA"
average_length = min(500, max(1, int(params.get("average_length", 50))))
neighbors = min(100, max(1, int(params.get("neighbors", 5))))
data_points = min(100, max(1, int(params.get("data_points", 10))))
show_price_neighbor_average = bool(params.get("show_price_neighbor_average", False))
show_average_neighbor_average = bool(params.get("show_average_neighbor_average", True))
closest_values = min(200, max(2, int(params.get("closest_values", 15))))
smoothing_period = min(500, max(2, int(params.get("smoothing_period", 20))))
classify_source_name = str(params.get("classify_source", "close")).lower()
average_source_name = str(params.get("average_source", "close")).lower()
show_trend_zones = bool(params.get("show_trend_zones", False))
keep_historical_zones = bool(params.get("keep_historical_zones", False))
max_historical_zones = min(100, max(1, int(params.get("max_historical_zones", 20))))
bullish_color = str(params.get("bullish_color", "#00C853"))
bearish_color = str(params.get("bearish_color", "#FF1744"))
neutral_color = str(params.get("neutral_color", "#0088FF"))
slope_color = str(params.get("slope_color", "#FFA927"))
price_neighbor_color = str(params.get("price_neighbor_color", "#1FC7FF"))
average_neighbor_color = str(params.get("average_neighbor_color", "#FDD835"))

df = df.copy()
open_values = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

def rgba(color, alpha):
    value = str(color).strip()
    if value.startswith("#") and len(value) == 7:
        try:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
            return f"rgba({red},{green},{blue},{alpha:.3f})"
        except ValueError:
            return value
    return value

def source_values(name):
    sources = {
        "close": close,
        "open": open_values,
        "high": high,
        "low": low,
        "hl2": (high + low) / 2.0,
        "hlc3": (high + low + close) / 3.0,
        "ohlc4": (open_values + high + low + close) / 4.0,
    }
    return sources.get(name, close)

def sma(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if length <= 0 or len(values) < length:
        return result
    sums = np.cumsum(np.nan_to_num(values, nan=0.0))
    finite = np.cumsum(np.isfinite(values).astype(int))
    for index in range(length - 1, len(values)):
        start = index - length
        window_sum = sums[index] - (sums[start] if start >= 0 else 0.0)
        count = finite[index] - (finite[start] if start >= 0 else 0)
        if count == length:
            result[index] = window_sum / length
    return result

def ema(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    seed = values[:length]
    if not np.all(np.isfinite(seed)):
        return result
    result[length - 1] = float(np.mean(seed))
    alpha = 2.0 / (length + 1.0)
    for index in range(length, len(values)):
        if np.isfinite(values[index]):
            result[index] = alpha * values[index] + (1.0 - alpha) * result[index - 1]
        else:
            result[index] = result[index - 1]
    return result

def rma(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    seed = values[:length]
    if not np.all(np.isfinite(seed)):
        return result
    result[length - 1] = float(np.mean(seed))
    for index in range(length, len(values)):
        if np.isfinite(values[index]):
            result[index] = (result[index - 1] * (length - 1) + values[index]) / length
        else:
            result[index] = result[index - 1]
    return result

def wma(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1.0, length + 1.0)
    divisor = float(np.sum(weights))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / divisor)
    return result

def hma(values, length):
    half_length = max(1, int(length / 2))
    root_length = max(1, int(np.sqrt(length)))
    half = wma(values, half_length)
    whole = wma(values, length)
    return wma(2.0 * half - whole, root_length)

def vwma(values, length):
    values = np.asarray(values, dtype=float)
    numerator = sma(values * volume, length) * length
    denominator = sma(volume, length) * length
    return np.divide(
        numerator,
        denominator,
        out=np.full(len(values), np.nan, dtype=float),
        where=np.isfinite(denominator) & (np.abs(denominator) > 1e-12),
    )

def moving_average(values, kind, length):
    if kind == "EMA":
        return ema(values, length)
    if kind == "WMA":
        return wma(values, length)
    if kind == "RMA":
        return rma(values, length)
    if kind == "HMA":
        return hma(values, length)
    if kind == "VWMA":
        return vwma(values, length)
    return sma(values, length)

def line_points(values, colors=None):
    points = []
    for index, value in enumerate(values):
        if not np.isfinite(value):
            points.append(None)
            continue
        point = {"value": float(value)}
        if colors is not None:
            point["color"] = colors[index]
        points.append(point)
    return points

classification_source = source_values(classify_source_name)
average_source = source_values(average_source_name)
base_average = moving_average(average_source, average_type, average_length)
history_size = max(neighbors, data_points)

classification = np.full(n, np.nan, dtype=float)
for index in range(history_size, n):
    sample_values = base_average[index - history_size + 1:index + 1]
    previous_values = base_average[index - history_size:index]
    sample_source = classification_source[index - history_size + 1:index + 1]
    if not (
        np.all(np.isfinite(sample_values))
        and np.all(np.isfinite(previous_values))
        and np.all(np.isfinite(sample_source))
        and np.isfinite(base_average[index])
    ):
        continue
    labels = ((sample_source > sample_values) & (sample_values > previous_values)).astype(float)
    distances = np.abs(sample_values - base_average[index])
    nearest = np.argsort(distances, kind="stable")[:neighbors]
    weights = 1.0 / (distances[nearest] + 1e-6)
    classification[index] = float(np.dot(weights, labels[nearest]) / np.sum(weights))

red_state = np.isfinite(classification) & (classification <= 1e-10)
green_state = np.isfinite(classification) & (classification >= 1.0 - 1e-10)
blue_state = np.isfinite(classification) & ~red_state & ~green_state
regime_colors = [
    bullish_color if green_state[index] else bearish_color if red_state[index] else neutral_color
    for index in range(n)
]

switch_state = np.zeros(n, dtype=bool)
for index in range(1, n):
    switch_state[index] = (
        (red_state[index] and (green_state[index - 1] or blue_state[index - 1]))
        or (green_state[index] and (red_state[index - 1] or blue_state[index - 1]))
    )

regression_line = np.full(n, np.nan, dtype=float)
regression_slope = np.full(n, np.nan, dtype=float)
segment_x = []
segment_y = []
for index in range(n):
    if switch_state[index]:
        segment_x = []
        segment_y = []
    if not np.isfinite(base_average[index]):
        continue
    segment_x.append(float(index))
    segment_y.append(float(base_average[index]))
    if len(segment_x) < 2:
        continue
    x_array = np.asarray(segment_x, dtype=float)
    y_array = np.asarray(segment_y, dtype=float)
    denominator = len(x_array) * float(np.dot(x_array, x_array)) - float(np.sum(x_array)) ** 2
    if abs(denominator) <= 1e-12:
        continue
    slope = (len(x_array) * float(np.dot(x_array, y_array)) - float(np.sum(x_array)) * float(np.sum(y_array))) / denominator
    intercept = (float(np.sum(y_array)) - slope * float(np.sum(x_array))) / len(x_array)
    regression_slope[index] = slope
    regression_line[index] = slope * index + intercept

target_average = sma(close, smoothing_period)
window_size = max(closest_values, 30)

def nearest_history_mean(values, targets):
    result = np.full(n, np.nan, dtype=float)
    for index in range(window_size, n):
        target = targets[index]
        if not np.isfinite(target):
            continue
        candidates = values[index - window_size:index]
        finite_candidates = candidates[np.isfinite(candidates)]
        if len(finite_candidates) < closest_values:
            continue
        distances = np.abs(finite_candidates - target)
        nearest = np.argsort(distances, kind="stable")[:closest_values]
        result[index] = float(np.mean(finite_candidates[nearest]))
    return result

price_neighbor_average = wma(nearest_history_mean(close, target_average), 5)
average_neighbor_average = wma(nearest_history_mean(target_average, target_average), 5)

plots = []
if show_classifier_average:
    bull_average = np.where(green_state, base_average, np.nan)
    bear_average = np.where(red_state, base_average, np.nan)
    neutral_average = np.where(blue_state, base_average, np.nan)
    plots.extend([
        {"name": "Classifier Bullish Average", "data": line_points(bull_average), "color": bullish_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Classifier Bearish Average", "data": line_points(bear_average), "color": bearish_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Classifier Neutral Average", "data": line_points(neutral_average), "color": neutral_color, "type": "line", "lineWidth": 2, "overlay": True},
    ])

if show_regression_slope:
    slope_colors = [bullish_color if np.isfinite(regression_slope[index]) and regression_slope[index] > 0.0 else bearish_color for index in range(n)]
    plots.append({
        "name": "Classifier Regression Slope",
        "data": line_points(regression_line, slope_colors),
        "color": bearish_color,
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    })

if show_price_neighbor_average:
    plots.append({"name": "Price Neighbor Average", "data": line_points(price_neighbor_average), "color": price_neighbor_color, "type": "line", "lineWidth": 2, "overlay": True})
if show_average_neighbor_average:
    plots.append({"name": "Average Neighbor Average", "data": line_points(average_neighbor_average), "color": average_neighbor_color, "type": "line", "lineWidth": 2, "overlay": True})

if show_trend_zones:
    segments = []
    active_start = None
    active_top = np.nan
    active_bottom = np.nan
    active_color = neutral_color
    for index in range(n):
        if switch_state[index]:
            if active_start is not None:
                segments.append((active_start, index - 1, active_top, active_bottom, active_color))
            start = max(0, index - 19)
            active_start = index
            active_top = float(np.max(high[start:index + 1]))
            active_bottom = float(np.min(low[start:index + 1]))
            active_color = regime_colors[index]
        elif active_start is not None:
            active_top = max(active_top, float(high[index]))
            active_bottom = min(active_bottom, float(low[index]))
    if active_start is not None:
        segments.append((active_start, n - 1, active_top, active_bottom, active_color))
    if not keep_historical_zones and segments:
        segments = segments[-1:]
    else:
        segments = segments[-max_historical_zones:]
    for zone_index, (start, end, top, bottom, color) in enumerate(segments, start=1):
        zone_data = [None for _ in range(n)]
        midpoint = (top + bottom) / 2.0
        midpoint_data = np.full(n, np.nan, dtype=float)
        for index in range(max(0, start), min(n - 1, end) + 1):
            zone_data[index] = {"upper": float(top), "lower": float(bottom), "color": rgba(color, 0.18)}
            midpoint_data[index] = midpoint
        plots.append({"name": f"Regime Zone {zone_index}", "title": False, "data": zone_data, "type": "cloud", "overlay": True})
        plots.append({"name": f"Regime Midpoint {zone_index}", "title": False, "data": line_points(midpoint_data), "color": slope_color, "type": "line", "lineStyle": "dotted", "lineWidth": 1, "overlay": True})

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "tables": [],
}
