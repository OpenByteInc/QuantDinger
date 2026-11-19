# ============================================================
# QuantDinger AI Trend Navigator
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger AI Trend Navigator"
my_indicator_description = (
    "Builds a locally computed nearest-history trend line, smooths it into a baseline, "
    "and derives trend direction, prediction shading, and crossover events."
)

# @param price_method str hl2 Price feature values=hl2,VWAP,SMA,WMA,EMA,HMA
# @param price_length int 5 Price feature length range=2:200:1
# @param target_method str Price_Action Target feature values=Price_Action,VWAP,Volatility,SMA,WMA,EMA,HMA
# @param target_length int 5 Target feature length range=2:200:1
# @param closest_values int 3 Closest history values range=2:200:1
# @param smoothing_period int 50 Baseline smoothing range=2:500:1
# @param show_prediction_background bool false Show prediction background
# @param show_event_markers bool false Show crossover and turn markers
# @param rising_color str #00C853 Rising line color
# @param falling_color str #FF1744 Falling line color
# @param neutral_color str #FF9800 Neutral line color
# @param baseline_color str #00897B Baseline color
# @param positive_background_color str #00C853 Positive prediction background
# @param negative_background_color str #FF1744 Negative prediction background

price_method = str(params.get("price_method", "hl2"))
if price_method not in ("hl2", "VWAP", "SMA", "WMA", "EMA", "HMA"):
    price_method = "hl2"
price_length = min(200, max(2, int(params.get("price_length", 5))))
target_method = str(params.get("target_method", "Price_Action"))
if target_method not in ("Price_Action", "VWAP", "Volatility", "SMA", "WMA", "EMA", "HMA"):
    target_method = "Price_Action"
target_length = min(200, max(2, int(params.get("target_length", 5))))
closest_values = min(200, max(2, int(params.get("closest_values", 3))))
smoothing_period = min(500, max(2, int(params.get("smoothing_period", 50))))
show_prediction_background = bool(params.get("show_prediction_background", False))
show_event_markers = bool(params.get("show_event_markers", False))
rising_color = str(params.get("rising_color", "#00C853"))
falling_color = str(params.get("falling_color", "#FF1744"))
neutral_color = str(params.get("neutral_color", "#FF9800"))
baseline_color = str(params.get("baseline_color", "#00897B"))
positive_background_color = str(params.get("positive_background_color", "#00C853"))
negative_background_color = str(params.get("negative_background_color", "#FF1744"))

df = df.copy()
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

def sma(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(length - 1, len(source)):
        window = source[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result

def ema(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < length or not np.all(np.isfinite(source[:length])):
        return result
    result[length - 1] = float(np.mean(source[:length]))
    alpha = 2.0 / (length + 1.0)
    for index in range(length, len(source)):
        if np.isfinite(source[index]):
            result[index] = alpha * source[index] + (1.0 - alpha) * result[index - 1]
        else:
            result[index] = result[index - 1]
    return result

def rma(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    finite_indices = np.flatnonzero(np.isfinite(source))
    if len(finite_indices) < length:
        return result
    seed_end = int(finite_indices[length - 1])
    seed = source[finite_indices[:length]]
    result[seed_end] = float(np.mean(seed))
    for index in range(seed_end + 1, len(source)):
        if np.isfinite(source[index]):
            result[index] = (result[index - 1] * (length - 1) + source[index]) / length
        else:
            result[index] = result[index - 1]
    return result

def wma(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    weights = np.arange(1.0, length + 1.0)
    divisor = float(np.sum(weights))
    for index in range(length - 1, len(source)):
        window = source[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / divisor)
    return result

def hma(values, length):
    half = wma(values, max(1, int(length / 2)))
    whole = wma(values, length)
    return wma(2.0 * half - whole, max(1, int(np.sqrt(length))))

def vwap(values):
    source = np.asarray(values, dtype=float)
    weighted_sum = np.cumsum(np.nan_to_num(source * volume, nan=0.0))
    volume_sum = np.cumsum(np.nan_to_num(volume, nan=0.0))
    return np.divide(
        weighted_sum,
        volume_sum,
        out=np.full(len(source), np.nan, dtype=float),
        where=np.abs(volume_sum) > 1e-12,
    )

def moving_average(values, method, length):
    if method == "WMA":
        return wma(values, length)
    if method == "EMA":
        return ema(values, length)
    if method == "HMA":
        return hma(values, length)
    return sma(values, length)

def atr(length):
    true_range = np.zeros(n, dtype=float)
    for index in range(n):
        if index == 0:
            true_range[index] = high[index] - low[index]
        else:
            true_range[index] = max(
                high[index] - low[index],
                abs(high[index] - close[index - 1]),
                abs(low[index] - close[index - 1]),
            )
    return rma(true_range, length)

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

hl2 = (high + low) / 2.0
if price_method == "hl2":
    price_feature = sma(hl2, price_length)
elif price_method == "VWAP":
    delayed_close = np.r_[np.full(price_length, np.nan), close[:-price_length]]
    price_feature = vwap(delayed_close)
else:
    price_feature = moving_average(close, price_method, price_length)

if target_method == "Price_Action":
    target_feature = rma(close, target_length)
elif target_method == "VWAP":
    delayed_close = np.r_[np.full(target_length, np.nan), close[:-target_length]]
    target_feature = vwap(delayed_close)
elif target_method == "Volatility":
    target_feature = atr(14)
else:
    target_feature = moving_average(close, target_method, target_length)

window_size = max(closest_values, 30)
neighbor_average = np.full(n, np.nan, dtype=float)
for index in range(window_size, n):
    target = target_feature[index]
    if not np.isfinite(target):
        continue
    history = price_feature[index - window_size:index]
    history = history[np.isfinite(history)]
    if len(history) < closest_values:
        continue
    distances = np.abs(history - target)
    nearest = np.argsort(distances, kind="stable")[:closest_values]
    neighbor_average[index] = float(np.mean(history[nearest]))

classifier_line = wma(neighbor_average, 5)
baseline = rma(neighbor_average, smoothing_period)
line_colors = []
for index in range(n):
    if index == 0 or not np.isfinite(classifier_line[index]) or not np.isfinite(classifier_line[index - 1]):
        line_colors.append(neutral_color)
    elif classifier_line[index] > classifier_line[index - 1]:
        line_colors.append(rising_color)
    elif classifier_line[index] < classifier_line[index - 1]:
        line_colors.append(falling_color)
    else:
        line_colors.append(neutral_color)

shifted_neighbor = np.r_[np.nan, neighbor_average[:-1]] if n else np.zeros(0, dtype=float)
prediction_close = rma(shifted_neighbor, smoothing_period)
prediction_open = baseline
prediction_price = (neighbor_average + close) / 2.0
raw_prediction = np.full(n, np.nan, dtype=float)
for index in range(10, n):
    if not np.isfinite(prediction_price[index]):
        continue
    minimum_distance = float("inf")
    positive_count = 0
    negative_count = 0
    for lookback in range(1, 11):
        candidate = prediction_price[index - lookback]
        if not np.isfinite(candidate):
            continue
        distance = abs(candidate - prediction_price[index])
        if distance < minimum_distance:
            minimum_distance = distance
            if np.isfinite(prediction_close[index - lookback]) and np.isfinite(prediction_open[index - lookback]):
                if prediction_close[index - lookback] < prediction_open[index - lookback]:
                    positive_count += 1
                elif prediction_close[index - lookback] > prediction_open[index - lookback]:
                    negative_count += 1
    raw_prediction[index] = 1.0 if positive_count > negative_count else -1.0

smoothed_prediction = wma(raw_prediction, 3)
down_prediction = np.isfinite(smoothed_prediction) & (smoothed_prediction < 0.5)
up_prediction = np.isfinite(smoothed_prediction) & (smoothed_prediction > -0.5)
background_colors = [None for _ in range(n)]
if show_prediction_background:
    for index in range(n):
        if down_prediction[index]:
            background_colors[index] = rgba(negative_background_color, 0.20)
        elif up_prediction[index]:
            background_colors[index] = rgba(positive_background_color, 0.20)

cross_over_baseline = np.zeros(n, dtype=bool)
cross_under_baseline = np.zeros(n, dtype=bool)
cross_over_close = np.zeros(n, dtype=bool)
cross_under_close = np.zeros(n, dtype=bool)
switch_up = np.zeros(n, dtype=bool)
switch_down = np.zeros(n, dtype=bool)
neutral_turn = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(classifier_line[index]) and np.isfinite(classifier_line[index - 1]):
        if np.isfinite(baseline[index]) and np.isfinite(baseline[index - 1]):
            cross_over_baseline[index] = classifier_line[index] > baseline[index] and classifier_line[index - 1] <= baseline[index - 1]
            cross_under_baseline[index] = classifier_line[index] < baseline[index] and classifier_line[index - 1] >= baseline[index - 1]
        cross_over_close[index] = classifier_line[index] > close[index] and classifier_line[index - 1] <= close[index - 1]
        cross_under_close[index] = classifier_line[index] < close[index] and classifier_line[index - 1] >= close[index - 1]
        neutral_turn[index] = line_colors[index] == neutral_color and line_colors[index - 1] != neutral_color
    if index >= 2 and np.all(np.isfinite(classifier_line[index - 2:index + 1])):
        switch_up[index] = classifier_line[index - 1] < classifier_line[index] and classifier_line[index - 1] <= classifier_line[index - 2]
        switch_down[index] = classifier_line[index - 1] > classifier_line[index] and classifier_line[index - 1] >= classifier_line[index - 2]

plots = [
    {"name": "KNN Classifier Line", "data": line_points(classifier_line, line_colors), "color": neutral_color, "type": "line", "lineWidth": 3, "overlay": True},
    {"name": "Average KNN Classifier Line", "data": line_points(baseline), "color": baseline_color, "type": "line", "lineWidth": 2, "overlay": True},
]

signals = []
if show_event_markers:
    event_specs = [
        ("buy", "KNN Above Baseline", "KNN UP", cross_over_baseline, rising_color, low),
        ("sell", "KNN Below Baseline", "KNN DN", cross_under_baseline, falling_color, high),
        ("buy", "KNN Above Price", "PRICE UP", cross_over_close, rising_color, low),
        ("sell", "KNN Below Price", "PRICE DN", cross_under_close, falling_color, high),
        ("buy", "KNN Turn Up", "TURN UP", switch_up, rising_color, low),
        ("sell", "KNN Turn Down", "TURN DN", switch_down, falling_color, high),
        ("exit", "KNN Neutral", "NEUTRAL", neutral_turn, neutral_color, close),
    ]
    for signal_type, name, text, mask, color, values in event_specs:
        signals.append({
            "type": signal_type,
            "name": name,
            "text": text,
            "color": color,
            "renderMode": "events",
            "data": [
                {"value": float(values[index]), "text": text, "color": color} if mask[index] else None
                for index in range(n)
            ],
        })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "tables": [],
    "background_colors": background_colors,
}
