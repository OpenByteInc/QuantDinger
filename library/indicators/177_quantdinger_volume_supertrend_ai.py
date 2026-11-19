# ============================================================
# QuantDinger Volume SuperTrend AI
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Volume SuperTrend AI"
my_indicator_description = (
    "Builds a volume-aware SuperTrend, classifies it with weighted nearest neighbors, "
    "and marks AI trend starts and direction-confirmed SuperTrend flips."
)

# @param neighbors int 3 KNN neighbors range=1:100:1
# @param data_points int 10 KNN history points range=1:100:1
# @param price_trend_length int 20 Price WMA length range=2:500:1
# @param prediction_trend_length int 100 SuperTrend WMA length range=2:500:1
# @param show_ai_signals bool true Show AI trend signals
# @param bullish_signal_color str #00FF00 Bullish signal color
# @param bearish_signal_color str #FF0000 Bearish signal color
# @param supertrend_length int 10 SuperTrend length range=1:500:1
# @param factor float 3.0 ATR multiplier range=0.1:20.0:0.1
# @param average_type str WMA Volume average type values=SMA,EMA,WMA,RMA,VWMA
# @param bullish_color str #00FF00 Bullish trend color
# @param bearish_color str #FF0000 Bearish trend color
# @param neutral_color str #2962FF Neutral trend color
# @param show_cloud bool true Show cloud reference layers
# @param cloud_bull_color str #00FF00 Bull cloud color
# @param cloud_bear_color str #FF0000 Bear cloud color
# @param cloud_transparency int 78 Cloud transparency range=0:95:1
# @param cloud_reference_length int 10 Cloud reference EMA length range=1:500:1
# @param cloud_line_smoothing int 2 Cloud active SMA length range=1:100:1
# @param cloud_layers int 3 Cloud layer count range=1:3:1

neighbors = min(100, max(1, int(params.get("neighbors", 3))))
data_points = min(100, max(1, int(params.get("data_points", 10))))
knn_count = max(neighbors, data_points)
price_trend_length = min(500, max(2, int(params.get("price_trend_length", 20))))
prediction_trend_length = min(500, max(2, int(params.get("prediction_trend_length", 100))))
show_ai_signals = bool(params.get("show_ai_signals", True))
bullish_signal_color = str(params.get("bullish_signal_color", "#00FF00"))
bearish_signal_color = str(params.get("bearish_signal_color", "#FF0000"))
supertrend_length = min(500, max(1, int(params.get("supertrend_length", 10))))
factor = min(20.0, max(0.1, float(params.get("factor", 3.0))))
average_type = str(params.get("average_type", "WMA"))
bullish_color = str(params.get("bullish_color", "#00FF00"))
bearish_color = str(params.get("bearish_color", "#FF0000"))
neutral_color = str(params.get("neutral_color", "#2962FF"))
show_cloud = bool(params.get("show_cloud", True))
cloud_bull_color = str(params.get("cloud_bull_color", "#00FF00"))
cloud_bear_color = str(params.get("cloud_bear_color", "#FF0000"))
cloud_transparency = min(95, max(0, int(params.get("cloud_transparency", 78))))
cloud_reference_length = min(500, max(1, int(params.get("cloud_reference_length", 10))))
cloud_line_smoothing = min(100, max(1, int(params.get("cloud_line_smoothing", 2))))
cloud_layers = min(3, max(1, int(params.get("cloud_layers", 3))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

def color_with_opacity(color, opacity):
    value = str(color).strip()
    alpha = min(1.0, max(0.0, float(opacity)))
    if value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            red, green, blue = (int(value[position] * 2, 16) for position in range(1, 4))
        else:
            red, green, blue = int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{alpha:.3f})"
    return value

def sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window_values = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window_values)):
            result[index] = float(np.mean(window_values))
    return result

def ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    seed = values[:length]
    if not np.all(np.isfinite(seed)):
        first_valid = next((index for index in range(length - 1, len(values)) if np.all(np.isfinite(values[index - length + 1:index + 1]))), None)
        if first_valid is None:
            return result
        result[first_valid] = float(np.mean(values[first_valid - length + 1:first_valid + 1]))
        start = first_valid + 1
    else:
        result[length - 1] = float(np.mean(seed))
        start = length
    alpha = 2.0 / (length + 1.0)
    for index in range(start, len(values)):
        if np.isfinite(values[index]):
            result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
        else:
            result[index] = result[index - 1]
    return result

def rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    for index in range(length, len(values)):
        result[index] = (result[index - 1] * (length - 1) + float(values[index])) / length
    return result

def wma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(length - 1, len(values)):
        window_values = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window_values)):
            result[index] = float(np.dot(window_values, weights) / denominator)
    return result

def ratio_average(price_values, volume_values, length, mode):
    weighted_price = price_values * volume_values
    if mode == "SMA":
        numerator, denominator = sma(weighted_price, length), sma(volume_values, length)
    elif mode == "EMA":
        numerator, denominator = ema(weighted_price, length), ema(volume_values, length)
    elif mode == "RMA":
        numerator, denominator = rma(weighted_price, length), rma(volume_values, length)
    elif mode == "VWMA":
        squared_volume = volume_values * volume_values
        numerator, denominator = sma(price_values * squared_volume, length), sma(squared_volume, length)
    else:
        numerator, denominator = wma(weighted_price, length), wma(volume_values, length)
    result = np.full(len(price_values), np.nan, dtype=float)
    valid = np.isfinite(numerator) & np.isfinite(denominator) & (np.abs(denominator) > 1e-12)
    result[valid] = numerator[valid] / denominator[valid]
    return result

volume_average = ratio_average(close, volume, supertrend_length, average_type)
true_range = np.zeros(n, dtype=float)
for index in range(n):
    true_range[index] = high[index] - low[index] if index == 0 else max(
        high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1])
    )
atr = rma(true_range, supertrend_length)
raw_upper = volume_average + factor * atr
raw_lower = volume_average - factor * atr
upper = np.full(n, np.nan, dtype=float)
lower = np.full(n, np.nan, dtype=float)
supertrend = np.full(n, np.nan, dtype=float)
direction = np.zeros(n, dtype=int)
for index in range(n):
    if not (np.isfinite(raw_upper[index]) and np.isfinite(raw_lower[index])):
        direction[index] = 1
        continue
    previous_lower = lower[index - 1] if index > 0 and np.isfinite(lower[index - 1]) else 0.0
    previous_upper = upper[index - 1] if index > 0 and np.isfinite(upper[index - 1]) else 0.0
    lower[index] = raw_lower[index] if raw_lower[index] > previous_lower or (index > 0 and close[index - 1] < previous_lower) else previous_lower
    upper[index] = raw_upper[index] if raw_upper[index] < previous_upper or (index > 0 and close[index - 1] > previous_upper) else previous_upper
    if index == 0 or not np.isfinite(atr[index - 1]):
        direction[index] = 1
    elif np.isfinite(supertrend[index - 1]) and abs(supertrend[index - 1] - previous_upper) <= 1e-9:
        direction[index] = -1 if close[index] > upper[index] else 1
    else:
        direction[index] = 1 if close[index] < lower[index] else -1
    supertrend[index] = lower[index] if direction[index] == -1 else upper[index]

price_trend = wma(close, price_trend_length)
prediction_trend = wma(supertrend, prediction_trend_length)
classification = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index < knn_count - 1 or not np.isfinite(supertrend[index]):
        continue
    history_indices = np.arange(index, index - knn_count, -1)
    data = supertrend[history_indices]
    label_values = (price_trend[history_indices] > prediction_trend[history_indices]).astype(float)
    if not (np.all(np.isfinite(data)) and np.all(np.isfinite(price_trend[history_indices])) and np.all(np.isfinite(prediction_trend[history_indices]))):
        continue
    distances = np.abs(float(supertrend[index]) - data)
    order = np.argsort(distances)[:neighbors]
    weights = 1.0 / (distances[order] + 1e-6)
    classification[index] = float(np.sum(weights * label_values[order]) / np.sum(weights))

state = np.full(n, -1, dtype=int)
for index in range(n):
    if not np.isfinite(classification[index]):
        continue
    if classification[index] >= 1.0 - 1e-12:
        state[index] = 1
    elif classification[index] <= 1e-12:
        state[index] = 0

bull_line = [None for _ in range(n)]
bear_line = [None for _ in range(n)]
neutral_line = [None for _ in range(n)]
start_bull = [None for _ in range(n)]
start_bear = [None for _ in range(n)]
trend_up = [None for _ in range(n)]
trend_down = [None for _ in range(n)]
for index in range(n):
    if not np.isfinite(supertrend[index]):
        continue
    target = bull_line if state[index] == 1 else bear_line if state[index] == 0 else neutral_line
    target[index] = float(supertrend[index])
    if index > 0 and state[index] != state[index - 1]:
        previous_target = bull_line if state[index - 1] == 1 else bear_line if state[index - 1] == 0 else neutral_line
        if np.isfinite(supertrend[index - 1]):
            target[index - 1] = float(supertrend[index - 1])
        previous_target[index] = float(supertrend[index])
    if show_ai_signals and state[index] == 1 and (index == 0 or state[index - 1] != 1):
        start_bull[index] = float(supertrend[index])
    if show_ai_signals and state[index] == 0 and (index == 0 or state[index - 1] != 0):
        start_bear[index] = float(supertrend[index])
    if show_ai_signals and index > 0 and direction[index] == -1 and direction[index - 1] == 1 and state[index] == 1:
        trend_up[index] = {"value": float(supertrend[index]), "text": "AI UP", "color": bullish_signal_color}
    if show_ai_signals and index > 0 and direction[index] == 1 and direction[index - 1] == -1 and state[index] == 0:
        trend_down[index] = {"value": float(supertrend[index]), "text": "AI DN", "color": bearish_signal_color}

plots = [
    {"name": "Bullish Volume SuperTrend AI", "data": bull_line, "color": bullish_color, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Bearish Volume SuperTrend AI", "data": bear_line, "color": bearish_color, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Neutral Volume SuperTrend AI", "data": neutral_line, "color": neutral_color, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "AI Bullish Trend Start", "title": False, "data": start_bull, "color": bullish_signal_color, "type": "circle", "size": 4, "overlay": True},
    {"name": "AI Bearish Trend Start", "title": False, "data": start_bear, "color": bearish_signal_color, "type": "circle", "size": 4, "overlay": True},
]

if show_cloud:
    cloud_active = sma(supertrend, cloud_line_smoothing)
    cloud_reference = ema((open_price + close) / 2.0, cloud_reference_length)
    fractions = (0.25, 0.50, 0.75)[:cloud_layers]
    cloud_alpha = max(0.08, (100 - cloud_transparency) / 100.0)
    cloud_bull_render = color_with_opacity(cloud_bull_color, cloud_alpha)
    cloud_bear_render = color_with_opacity(cloud_bear_color, cloud_alpha)
    for layer_index, fraction in enumerate(fractions, 1):
        layer_values = cloud_active + (cloud_reference - cloud_active) * fraction
        bull_cloud = [float(layer_values[index]) if state[index] == 1 and np.isfinite(layer_values[index]) else None for index in range(n)]
        bear_cloud = [float(layer_values[index]) if state[index] == 0 and np.isfinite(layer_values[index]) else None for index in range(n)]
        plots.append({"name": f"Bull Cloud Layer {layer_index}", "title": False, "data": bull_cloud, "color": cloud_bull_render, "type": "line", "lineWidth": 1, "overlay": True})
        plots.append({"name": f"Bear Cloud Layer {layer_index}", "title": False, "data": bear_cloud, "color": cloud_bear_render, "type": "line", "lineWidth": 1, "overlay": True})

signals = [
    {"type": "buy", "name": "AI Bullish Trend Signal", "title": False, "text": "AI UP", "data": trend_up, "color": bullish_signal_color, "renderMode": "events"},
    {"type": "sell", "name": "AI Bearish Trend Signal", "title": False, "text": "AI DN", "data": trend_down, "color": bearish_signal_color, "renderMode": "events"},
]
output = {"name": my_indicator_name, "plots": plots, "signals": signals}
