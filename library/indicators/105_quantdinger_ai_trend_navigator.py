# ============================================================
# QuantDinger AI Trend Navigator
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger AI Trend Navigator"
my_indicator_description = (
    "Builds a nearest-neighbor price estimate, smooths its directional state, "
    "and overlays a color-coded trend line with an optional prediction cloud."
)

# @param price_value str hl2 Price feature values=hl2,VWAP,SMA,WMA,EMA,HMA
# @param price_length int 5 Price feature length range=2:200:1
# @param target_value str PriceAction Target feature values=PriceAction,VWAP,Volatility,SMA,WMA,EMA,HMA
# @param target_length int 5 Target feature length range=2:200:1
# @param neighbors int 3 Nearest values range=2:200:1
# @param smoothing_period int 50 Classifier smoothing range=2:500:1
# @param show_prediction_cloud bool false Show prediction color cloud

price_value = str(params.get("price_value", "hl2"))
price_length = min(200, max(2, int(params.get("price_length", 5))))
target_value = str(params.get("target_value", "PriceAction"))
target_length = min(200, max(2, int(params.get("target_length", 5))))
neighbors = min(200, max(2, int(params.get("neighbors", 3))))
smoothing_period = min(500, max(2, int(params.get("smoothing_period", 50))))
show_prediction_cloud = bool(params.get("show_prediction_cloud", False))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)


def rolling_sma(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(period - 1, len(source)):
        window = source[index - period + 1 : index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


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


def hull_average(values, period):
    half = max(1, int(round(period / 2.0)))
    root = max(1, int(round(period ** 0.5)))
    return rolling_wma(2.0 * rolling_wma(values, half) - rolling_wma(values, period), root)


def cumulative_vwap(values):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    numerator = 0.0
    denominator = 0.0
    for index, value in enumerate(source):
        if not np.isfinite(value) or not np.isfinite(volume[index]):
            continue
        numerator += float(value) * float(volume[index])
        denominator += float(volume[index])
        if denominator > 0.0:
            result[index] = numerator / denominator
    return result


def true_range():
    result = np.full(n, np.nan, dtype=float)
    for index in range(n):
        if index == 0:
            result[index] = high[index] - low[index]
        else:
            result[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
    return result


def feature(values, period, kind):
    source = np.asarray(values, dtype=float)
    if kind == "VWAP":
        shifted = np.full(n, np.nan, dtype=float)
        if period < n:
            shifted[period:] = source[:-period]
        return cumulative_vwap(shifted)
    if kind == "SMA":
        return rolling_sma(source, period)
    if kind == "WMA":
        return rolling_wma(source, period)
    if kind == "EMA":
        return recursive_ema(source, period)
    if kind == "HMA":
        return hull_average(source, period)
    return rolling_sma((high + low) / 2.0, period)


price_feature = feature(close, price_length, price_value)
if target_value == "PriceAction":
    target_feature = pine_rma(close, target_length)
elif target_value == "Volatility":
    target_feature = pine_rma(true_range(), 14)
else:
    target_feature = feature(close, target_length, target_value)

window_size = max(neighbors, 30)
knn_average = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.isfinite(target_feature[index]):
        continue
    closest_distances = [1e10 for _ in range(neighbors)]
    closest_values = [0.0 for _ in range(neighbors)]
    for lag in range(1, window_size + 1):
        prior = index - lag
        if prior < 0 or not np.isfinite(price_feature[prior]):
            continue
        distance = abs(float(target_feature[index]) - float(price_feature[prior]))
        maximum_index = 0
        maximum_distance = closest_distances[0]
        for candidate in range(1, neighbors):
            if closest_distances[candidate] > maximum_distance:
                maximum_index = candidate
                maximum_distance = closest_distances[candidate]
        if distance < maximum_distance:
            closest_distances[maximum_index] = distance
            closest_values[maximum_index] = float(price_feature[prior])
    knn_average[index] = float(sum(closest_values) / neighbors)

classifier_price = (knn_average + close) / 2.0
shifted_knn = np.full(n, np.nan, dtype=float)
if n > 1:
    shifted_knn[1:] = knn_average[:-1]
classifier_previous = pine_rma(shifted_knn, smoothing_period)
classifier_current = pine_rma(knn_average, smoothing_period)

prediction_raw = np.full(n, -1.0, dtype=float)
for index in range(n):
    if not np.isfinite(classifier_price[index]):
        continue
    positive_count = 0
    negative_count = 0
    minimum_distance = 1e11
    for lag in range(1, 11):
        prior = index - lag
        if prior < 0 or not np.isfinite(classifier_price[prior]):
            continue
        distance = abs(float(classifier_price[prior]) - float(classifier_price[index]))
        if distance < minimum_distance:
            minimum_distance = distance
            if np.isfinite(classifier_previous[prior]) and np.isfinite(classifier_current[prior]):
                if classifier_previous[prior] < classifier_current[prior]:
                    positive_count += 1
                elif classifier_previous[prior] > classifier_current[prior]:
                    negative_count += 1
    prediction_raw[index] = 1.0 if positive_count > negative_count else -1.0

prediction = rolling_wma(prediction_raw, 3)
knn_line = rolling_wma(knn_average, 5)
average_knn_line = pine_rma(knn_average, smoothing_period)

knn_plot = []
prediction_cloud = []
for index in range(n):
    value = knn_line[index]
    if np.isfinite(value):
        previous = knn_line[index - 1] if index > 0 else np.nan
        color = "#00FF00" if np.isfinite(previous) and value > previous else "#FF0000" if np.isfinite(previous) and value < previous else "#FFA500"
        knn_plot.append({"value": float(value), "color": color})
    else:
        knn_plot.append(None)

    if not show_prediction_cloud or not np.isfinite(prediction[index]):
        prediction_cloud.append(None)
        continue
    start = max(0, index - 99)
    upper = float(np.max(high[start : index + 1]))
    lower = float(np.min(low[start : index + 1]))
    color = "rgba(255, 0, 0, 0.12)" if prediction[index] < 0.5 else "rgba(0, 255, 0, 0.12)"
    prediction_cloud.append({"upper": upper, "lower": lower, "color": color})

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "KNN Classifier Line", "data": knn_plot, "color": "#00FF00", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Average KNN Classifier Line", "data": [float(value) if np.isfinite(value) else None for value in average_knn_line], "color": "#008080", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Prediction Cloud", "title": False, "data": prediction_cloud, "color": "#00FF00", "type": "cloud", "opacity": 0.12, "overlay": True},
    ],
    "signals": [],
    "layers": [],
}
