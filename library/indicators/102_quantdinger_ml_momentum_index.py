# ============================================================
# QuantDinger ML Momentum Index
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger ML Momentum Index"
my_indicator_description = (
    "Classifies completed moving-average swings with nearest-neighbor momentum "
    "features and displays the vote total inside adaptive extreme zones."
)

# @param neighbors int 200 Prediction neighbors range=1:1000:1
# @param momentum_window int 20 Momentum smoothing length range=10:200:2
# @param show_extreme_fill bool true Show adaptive extreme fills

neighbors = min(1000, max(1, int(params.get("neighbors", 200))))
momentum_window = min(200, max(10, int(params.get("momentum_window", 20))))
show_extreme_fill = bool(params.get("show_extreme_fill", True))

df = df.copy()
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def rolling_wma(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(period - 1, len(source)):
        window = source[index - period + 1 : index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
    return result


def pine_rma(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    seed = []
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) < period:
                continue
            previous = float(np.mean(seed[-period:]))
        else:
            previous = (previous * (period - 1) + float(value)) / period
        result[index] = previous
    return result


def recursive_ema(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def rolling_std(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(period - 1, len(source)):
        window = source[index - period + 1 : index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.std(window, ddof=0))
    return result


def rolling_extreme(values, period, use_maximum):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(len(source)):
        start = max(0, index - period + 1)
        window = source[start : index + 1]
        finite = window[np.isfinite(window)]
        if finite.size:
            result[index] = float(np.max(finite) if use_maximum else np.min(finite))
    return result


def compute_rsi(values, period):
    source = np.asarray(values, dtype=float)
    gains = np.full(len(source), np.nan, dtype=float)
    losses = np.full(len(source), np.nan, dtype=float)
    for index in range(1, len(source)):
        change = source[index] - source[index - 1]
        gains[index] = max(float(change), 0.0)
        losses[index] = max(float(-change), 0.0)
    average_gain = pine_rma(gains, period)
    average_loss = pine_rma(losses, period)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(len(source)):
        if not np.isfinite(average_gain[index]) or not np.isfinite(average_loss[index]):
            continue
        if average_loss[index] == 0.0:
            result[index] = 100.0
        elif average_gain[index] == 0.0:
            result[index] = 0.0
        else:
            strength = average_gain[index] / average_loss[index]
            result[index] = 100.0 - 100.0 / (1.0 + strength)
    return result


quick_average = rolling_wma(close, 5)
slow_average = rolling_wma(close, 20)
quick_feature = rolling_wma(compute_rsi(close, 5), momentum_window)
slow_feature = rolling_wma(compute_rsi(close, 20), momentum_window)

parameter_one = [0.0]
parameter_two = [0.0]
entry_prices = [0.0]
trade_results = [0.0]
prediction = np.zeros(n, dtype=float)

for index in range(n):
    crossover = (
        index > 0
        and np.isfinite(quick_average[index])
        and np.isfinite(slow_average[index])
        and np.isfinite(quick_average[index - 1])
        and np.isfinite(slow_average[index - 1])
        and (
            (quick_average[index] > slow_average[index] and quick_average[index - 1] <= slow_average[index - 1])
            or (quick_average[index] < slow_average[index] and quick_average[index - 1] >= slow_average[index - 1])
        )
    )
    if crossover and np.isfinite(slow_feature[index]) and np.isfinite(quick_feature[index]):
        previous_entry = entry_prices[-1]
        parameter_one.append(parameter_one[-1])
        parameter_two.append(parameter_two[-1])
        entry_prices.append(previous_entry)
        trade_results.append(1.0 if close[index] >= previous_entry else -1.0)
        parameter_one[-1] = float(slow_feature[index])
        parameter_two[-1] = float(quick_feature[index])
        entry_prices[-1] = float(close[index])

    if not np.isfinite(slow_feature[index]) or not np.isfinite(quick_feature[index]):
        prediction[index] = prediction[index - 1] if index > 0 else 0.0
        continue

    distances = []
    for item_index in range(len(parameter_one)):
        delta_one = float(slow_feature[index]) - parameter_one[item_index]
        delta_two = float(quick_feature[index]) - parameter_two[item_index]
        distances.append(float((delta_one * delta_one + delta_two * delta_two) ** 0.5))
    sorted_distances = sorted(distances)
    selected_count = min(neighbors, len(sorted_distances))
    maximum_distance = sorted_distances[selected_count - 1]
    vote = 0.0
    for item_index, distance in enumerate(distances):
        if distance <= maximum_distance:
            vote += trade_results[item_index]
    prediction[index] = vote

prediction_average = rolling_wma(prediction, 20)
upper = rolling_extreme(prediction, 2000, True)
lower = rolling_extreme(prediction, 2000, False)
volatility = recursive_ema(rolling_std(prediction, 20), 20)
upper_threshold = upper - volatility
lower_threshold = lower + volatility

prediction_points = []
upper_channel = []
lower_channel = []
extreme_fill = []
for index in range(n):
    value = float(prediction[index])
    prediction_points.append(value)
    if np.isfinite(upper[index]):
        upper_channel.append({"upper": float(upper[index]), "lower": 0.0, "color": "rgba(86, 255, 113, 0.11)"})
    else:
        upper_channel.append(None)
    if np.isfinite(lower[index]):
        lower_channel.append({"upper": 0.0, "lower": float(lower[index]), "color": "rgba(255, 67, 67, 0.11)"})
    else:
        lower_channel.append(None)

    point = None
    if show_extreme_fill and np.isfinite(upper_threshold[index]) and value > upper_threshold[index]:
        span = max(1e-9, float(upper[index] - upper_threshold[index]))
        intensity = min(1.0, max(0.0, (value - float(upper_threshold[index])) / span))
        point = {"upper": max(value, 0.0), "lower": min(value, 0.0), "color": f"rgba(49, 255, 120, {0.12 + 0.48 * intensity:.3f})"}
    elif show_extreme_fill and np.isfinite(lower_threshold[index]) and value < lower_threshold[index]:
        span = max(1e-9, float(lower_threshold[index] - lower[index]))
        intensity = min(1.0, max(0.0, (float(lower_threshold[index]) - value) / span))
        point = {"upper": max(value, 0.0), "lower": min(value, 0.0), "color": f"rgba(255, 67, 67, {0.12 + 0.48 * intensity:.3f})"}
    extreme_fill.append(point)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "MLMI Prediction", "data": prediction_points, "color": "#426EFF", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Prediction WMA", "data": [float(value) if np.isfinite(value) else None for value in prediction_average], "color": "#31FFC8", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Mid Level", "data": [0.0 for _ in range(n)], "color": "rgba(148, 163, 184, 0.65)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Upper Adaptive Zone", "title": False, "data": upper_channel, "color": "#56FF71", "type": "cloud", "opacity": 0.11, "overlay": False},
        {"name": "Lower Adaptive Zone", "title": False, "data": lower_channel, "color": "#FF4343", "type": "cloud", "opacity": 0.11, "overlay": False},
        {"name": "Extreme Gradient", "title": False, "data": extreme_fill, "color": "#31FF78", "type": "cloud", "opacity": 0.42, "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
