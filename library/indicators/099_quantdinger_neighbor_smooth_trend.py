# ============================================================
# QuantDinger Neighbor-Smooth Trend
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Neighbor-Smooth Trend"
my_indicator_description = (
    "Combines a direction-classified moving average, a segment regression slope, "
    "and two nearest-history smoothing curves."
)

# @param show_primary bool false Show classified moving average
# @param show_slope bool true Show classified trend slope
# @param ma_type str SMA Moving average type values=SMA,EMA,WMA,RMA,HMA,VWMA
# @param ma_length int 50 Moving average length range=1:500:1
# @param neighbors int 5 Classification neighbors range=1:100:1
# @param data_points int 10 Classification history range=1:100:1
# @param show_neighbor_price bool false Show price-neighbor average
# @param show_neighbor_smooth bool true Show smooth-neighbor average
# @param closest_values int 15 Closest history values range=2:200:1
# @param smoothing_period int 20 Base smoothing period range=2:500:1

show_primary = bool(params.get("show_primary", False))
show_slope = bool(params.get("show_slope", True))
ma_type = str(params.get("ma_type", "SMA")).upper()
ma_length = min(500, max(1, int(params.get("ma_length", 50))))
neighbors = min(100, max(1, int(params.get("neighbors", 5))))
data_points = min(100, max(1, int(params.get("data_points", 10))))
show_neighbor_price = bool(params.get("show_neighbor_price", False))
show_neighbor_smooth = bool(params.get("show_neighbor_smooth", True))
closest_values = min(200, max(2, int(params.get("closest_values", 15))))
smoothing_period = min(500, max(2, int(params.get("smoothing_period", 20))))

df = df.copy()
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)


def rolling_mean(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if period <= 0:
        return result
    running = 0.0
    finite_count = 0
    for index, value in enumerate(source):
        if np.isfinite(value):
            running += float(value)
            finite_count += 1
        if index >= period:
            old_value = source[index - period]
            if np.isfinite(old_value):
                running -= float(old_value)
                finite_count -= 1
        if index >= period - 1 and finite_count == period:
            result[index] = running / period
    return result


def rolling_wma(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if period <= 0:
        return result
    weights = np.arange(1, period + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(period - 1, len(source)):
        window = source[index - period + 1 : index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
    return result


def recursive_average(values, period, alpha):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    seed_values = []
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed_values.append(float(value))
            if len(seed_values) < period:
                continue
            previous = float(np.mean(seed_values[-period:]))
        else:
            previous = alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def hull_average(values, period):
    half_period = max(1, period // 2)
    root_period = max(1, int(round(period ** 0.5)))
    half = rolling_wma(values, half_period)
    full = rolling_wma(values, period)
    difference = 2.0 * half - full
    return rolling_wma(difference, root_period)


def volume_weighted_average(values, weights, period):
    source = np.asarray(values, dtype=float)
    weight_source = np.asarray(weights, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(period - 1, len(source)):
        value_window = source[index - period + 1 : index + 1]
        weight_window = weight_source[index - period + 1 : index + 1]
        denominator = float(np.sum(weight_window))
        if denominator > 0.0 and np.all(np.isfinite(value_window)) and np.all(np.isfinite(weight_window)):
            result[index] = float(np.dot(value_window, weight_window) / denominator)
    return result


if ma_type == "EMA":
    selected_ma = recursive_average(close, ma_length, 2.0 / (ma_length + 1.0))
elif ma_type == "WMA":
    selected_ma = rolling_wma(close, ma_length)
elif ma_type == "RMA":
    selected_ma = recursive_average(close, ma_length, 1.0 / ma_length)
elif ma_type == "HMA":
    selected_ma = hull_average(close, ma_length)
elif ma_type == "VWMA":
    selected_ma = volume_weighted_average(close, volume, ma_length)
else:
    selected_ma = rolling_mean(close, ma_length)

history_size = max(neighbors, data_points)
classification = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.isfinite(selected_ma[index]):
        continue
    start = max(1, index - history_size + 1)
    candidates = []
    for history_index in range(start, index + 1):
        current_ma = selected_ma[history_index]
        previous_ma = selected_ma[history_index - 1]
        if not np.isfinite(current_ma) or not np.isfinite(previous_ma):
            continue
        label = 1.0 if close[history_index] > current_ma and current_ma > previous_ma else 0.0
        distance = abs(float(selected_ma[index] - current_ma))
        candidates.append((distance, label))
    if not candidates:
        continue
    candidates.sort(key=lambda item: item[0])
    weighted_sum = 0.0
    total_weight = 0.0
    for distance, label in candidates[: min(neighbors, len(candidates))]:
        weight = 1.0 / (distance + 1e-6)
        weighted_sum += label * weight
        total_weight += weight
    classification[index] = weighted_sum / total_weight if total_weight > 0.0 else 0.0

trend_is_up = np.zeros(n, dtype=bool)
previous_state = False
for index in range(n):
    if np.isfinite(classification[index]):
        previous_state = classification[index] >= 0.5
    trend_is_up[index] = previous_state

slope_line = np.full(n, np.nan, dtype=float)
segment_start = 0
sum_x = 0.0
sum_y = 0.0
sum_xy = 0.0
sum_x2 = 0.0
count = 0
for index in range(n):
    if index > 0 and trend_is_up[index] != trend_is_up[index - 1]:
        segment_start = index
        sum_x = 0.0
        sum_y = 0.0
        sum_xy = 0.0
        sum_x2 = 0.0
        count = 0
    value = selected_ma[index]
    if not np.isfinite(value):
        continue
    x_value = float(index - segment_start)
    sum_x += x_value
    sum_y += float(value)
    sum_xy += x_value * float(value)
    sum_x2 += x_value * x_value
    count += 1
    denominator = count * sum_x2 - sum_x * sum_x
    if count > 1 and abs(denominator) > 1e-12:
        slope = (count * sum_xy - sum_x * sum_y) / denominator
        intercept = (sum_y - slope * sum_x) / count
        slope_line[index] = slope * x_value + intercept

smooth_base = rolling_mean(close, smoothing_period)
search_window = max(closest_values, 30)
neighbor_price = np.full(n, np.nan, dtype=float)
neighbor_smooth = np.full(n, np.nan, dtype=float)
for index in range(n):
    target = smooth_base[index]
    if not np.isfinite(target):
        continue
    start = max(0, index - search_window)
    price_candidates = []
    smooth_candidates = []
    for history_index in range(start, index):
        price_value = close[history_index]
        smooth_value = smooth_base[history_index]
        if np.isfinite(price_value):
            price_candidates.append((abs(float(target - price_value)), float(price_value)))
        if np.isfinite(smooth_value):
            smooth_candidates.append((abs(float(target - smooth_value)), float(smooth_value)))
    if price_candidates:
        price_candidates.sort(key=lambda item: item[0])
        selected = price_candidates[: min(closest_values, len(price_candidates))]
        neighbor_price[index] = float(np.mean([item[1] for item in selected]))
    if smooth_candidates:
        smooth_candidates.sort(key=lambda item: item[0])
        selected = smooth_candidates[: min(closest_values, len(smooth_candidates))]
        neighbor_smooth[index] = float(np.mean([item[1] for item in selected]))

neighbor_price_line = rolling_wma(neighbor_price, 5)
neighbor_smooth_line = rolling_wma(neighbor_smooth, 5)

primary_points = []
slope_points = []
for index in range(n):
    trend_color = "#39D98A" if trend_is_up[index] else "#FF5B69"
    primary_points.append(
        {"value": float(selected_ma[index]), "color": trend_color}
        if show_primary and np.isfinite(selected_ma[index])
        else None
    )
    slope_points.append(
        {"value": float(slope_line[index]), "color": trend_color}
        if show_slope and np.isfinite(slope_line[index])
        else None
    )

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Classified Average", "data": primary_points, "color": "#39D98A", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Segment Slope", "data": slope_points, "color": "#39D98A", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Price Neighbor", "data": [float(value) if show_neighbor_price and np.isfinite(value) else None for value in neighbor_price_line], "color": "#1FC7FF", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Smooth Neighbor", "data": [float(value) if show_neighbor_smooth and np.isfinite(value) else None for value in neighbor_smooth_line], "color": "#F2D14C", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [],
    "layers": [],
}
