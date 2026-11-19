# ============================================================
# QuantDinger Machine Learning Momentum Index
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Machine Learning Momentum Index"
my_indicator_description = "Classifies moving-average swing outcomes with a two-feature nearest-neighbor momentum model."

# @param num_neighbors int 200 Prediction neighbors range=1:2000:1
# @param momentum_window int 20 Trend length range=10:200:2
# @param show_gradients bool true Show oscillator gradient channels

num_neighbors = min(2000, max(1, int(params.get("num_neighbors", 200))))
momentum_window = min(200, max(10, int(params.get("momentum_window", 20))))
show_gradients = bool(params.get("show_gradients", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _wma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    divisor = float(np.sum(weights))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / divisor)
    return result


def _rsi(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= period:
        return result
    changes = np.diff(values)
    gains = np.maximum(changes, 0.0)
    losses = np.maximum(-changes, 0.0)
    average_gain = float(np.mean(gains[:period]))
    average_loss = float(np.mean(losses[:period]))
    for index in range(period, len(values)):
        if index > period:
            average_gain = (average_gain * (period - 1.0) + gains[index - 1]) / period
            average_loss = (average_loss * (period - 1.0) + losses[index - 1]) / period
        if average_gain == 0.0 and average_loss == 0.0:
            result[index] = 50.0
        elif average_loss == 0.0:
            result[index] = 100.0
        elif average_gain == 0.0:
            result[index] = 0.0
        else:
            result[index] = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _cross(first, second):
    over = np.zeros(len(first), dtype=bool)
    under = np.zeros(len(first), dtype=bool)
    for index in range(1, len(first)):
        current = (first[index], second[index])
        previous = (first[index - 1], second[index - 1])
        if np.all(np.isfinite(current + previous)):
            over[index] = first[index] > second[index] and first[index - 1] <= second[index - 1]
            under[index] = first[index] < second[index] and first[index - 1] >= second[index - 1]
    return over, under


frame = df.copy().reset_index(drop=True)
if "time" not in frame.columns:
    frame["time"] = np.arange(len(frame), dtype=float) * 3600000.0
if "volume" not in frame.columns:
    frame["volume"] = 0.0
for column in ("time", "open", "high", "low", "close", "volume"):
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
frame = frame.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
close = frame["close"].to_numpy(dtype=float)
n = len(frame)

ma_quick = _wma(close, 5)
ma_slow = _wma(close, 20)
rsi_quick = _wma(_rsi(close, 5), momentum_window)
rsi_slow = _wma(_rsi(close, 20), momentum_window)
positive_cross, negative_cross = _cross(ma_quick, ma_slow)
trade_event = positive_cross | negative_cross

parameter_one = [0.0]
parameter_two = [0.0]
price_records = [0.0]
result_records = [0.0]
prediction = np.zeros(n, dtype=float)
record_count = np.ones(n, dtype=int)

for index in range(n):
    if trade_event[index]:
        previous_parameter_one = parameter_one[-1]
        previous_parameter_two = parameter_two[-1]
        previous_price = price_records[-1]
        parameter_one.append(previous_parameter_one)
        parameter_two.append(previous_parameter_two)
        price_records.append(previous_price)
        result_records.append(1.0 if close[index] >= previous_price else -1.0)
        parameter_one[-1] = float(rsi_slow[index]) if np.isfinite(rsi_slow[index]) else np.nan
        parameter_two[-1] = float(rsi_quick[index]) if np.isfinite(rsi_quick[index]) else np.nan
        price_records[-1] = float(close[index])

    p1 = rsi_slow[index]
    p2 = rsi_quick[index]
    distances = np.sqrt((p1 - np.asarray(parameter_one, dtype=float)) ** 2 + (p2 - np.asarray(parameter_two, dtype=float)) ** 2)
    finite = np.isfinite(distances)
    if finite.any():
        sorted_distances = np.sort(distances[finite])
        maximum_distance = sorted_distances[min(num_neighbors, len(sorted_distances)) - 1]
        neighbor_mask = finite & (distances <= maximum_distance)
        prediction[index] = float(np.sum(np.asarray(result_records, dtype=float)[neighbor_mask]))
    else:
        prediction[index] = 0.0
    record_count[index] = len(parameter_one)

prediction_ma = _wma(prediction, 20)
upper = pd.Series(prediction, dtype=float).rolling(2000, min_periods=1).max().to_numpy(dtype=float)
lower = pd.Series(prediction, dtype=float).rolling(2000, min_periods=1).min().to_numpy(dtype=float)
prediction_deviation = pd.Series(prediction, dtype=float).rolling(20, min_periods=20).std(ddof=0).to_numpy(dtype=float)
smoothed_deviation = _ema(prediction_deviation, 20)
upper_trigger = upper - smoothed_deviation
lower_trigger = lower + smoothed_deviation

over_upper, under_upper = _cross(prediction, upper_trigger)
over_lower, under_lower = _cross(prediction, lower_trigger)
over_mid, under_mid = _cross(prediction, np.zeros(n, dtype=float))
over_average, under_average = _cross(prediction, prediction_ma)

channel_up = []
channel_down = []
extreme_up = []
extreme_down = []
for index in range(n):
    if show_gradients:
        channel_up.append({"upper": float(max(0.0, upper[index])), "lower": 0.0, "topColor": "rgba(0,255,0,0.25)", "bottomColor": "rgba(0,255,0,0.00)"})
        channel_down.append({"upper": 0.0, "lower": float(min(0.0, lower[index])), "topColor": "rgba(255,0,0,0.00)", "bottomColor": "rgba(255,0,0,0.25)"})
        extreme_up.append({"upper": float(max(prediction[index], 0.0)), "lower": float(min(prediction[index], 0.0)), "topColor": "rgba(0,255,0,1.00)", "bottomColor": "rgba(0,128,0,0.00)"} if np.isfinite(upper_trigger[index]) and prediction[index] >= upper_trigger[index] else None)
        extreme_down.append({"upper": float(max(prediction[index], 0.0)), "lower": float(min(prediction[index], 0.0)), "topColor": "rgba(255,0,0,0.00)", "bottomColor": "rgba(255,0,0,1.00)"} if np.isfinite(lower_trigger[index]) and prediction[index] <= lower_trigger[index] else None)
    else:
        channel_up.append(None); channel_down.append(None); extreme_up.append(None); extreme_down.append(None)

plots = [
    {"name": "MLMI Prediction", "data": _finite(prediction), "color": "#426EFF", "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "WMA of MLMI Prediction", "data": _finite(prediction_ma), "color": "#31FFC8", "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Mid Level", "data": [0.0] * n, "color": "rgba(150,150,150,0.65)", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Positive Channel", "title": False, "data": channel_up, "color": "#00FF00", "type": "cloud", "overlay": False},
    {"name": "Negative Channel", "title": False, "data": channel_down, "color": "#FF0000", "type": "cloud", "overlay": False},
    {"name": "Overbought Gradient", "title": False, "data": extreme_up, "color": "#00FF00", "type": "cloud", "overlay": False},
    {"name": "Oversold Gradient", "title": False, "data": extreme_down, "color": "#FF0000", "type": "cloud", "overlay": False},
]

signals = [
    {"type": "buy", "name": "MLMI Crossover OB", "text": "", "data": [float(prediction[index]) if over_upper[index] else None for index in range(n)], "color": "rgba(0,0,0,0)", "renderMode": "hidden"},
    {"type": "sell", "name": "MLMI Crossunder OB", "text": "", "data": [float(prediction[index]) if under_upper[index] else None for index in range(n)], "color": "rgba(0,0,0,0)", "renderMode": "hidden"},
    {"type": "buy", "name": "MLMI Crossover OS", "text": "", "data": [float(prediction[index]) if over_lower[index] else None for index in range(n)], "color": "rgba(0,0,0,0)", "renderMode": "hidden"},
    {"type": "sell", "name": "MLMI Crossunder OS", "text": "", "data": [float(prediction[index]) if under_lower[index] else None for index in range(n)], "color": "rgba(0,0,0,0)", "renderMode": "hidden"},
    {"type": "buy", "name": "MLMI Crossover Mid", "text": "", "data": [float(prediction[index]) if over_mid[index] else None for index in range(n)], "color": "rgba(0,0,0,0)", "renderMode": "hidden"},
    {"type": "sell", "name": "MLMI Crossunder Mid", "text": "", "data": [float(prediction[index]) if under_mid[index] else None for index in range(n)], "color": "rgba(0,0,0,0)", "renderMode": "hidden"},
    {"type": "buy", "name": "MLMI Crossover MA", "text": "", "data": [float(prediction[index]) if over_average[index] else None for index in range(n)], "color": "rgba(0,0,0,0)", "renderMode": "hidden"},
    {"type": "sell", "name": "MLMI Crossunder MA", "text": "", "data": [float(prediction[index]) if under_average[index] else None for index in range(n)], "color": "rgba(0,0,0,0)", "renderMode": "hidden"},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "maQuick": _finite(ma_quick), "maSlow": _finite(ma_slow), "rsiQuick": _finite(rsi_quick), "rsiSlow": _finite(rsi_slow),
        "positiveCross": positive_cross.tolist(), "negativeCross": negative_cross.tolist(), "tradeEvent": trade_event.tolist(),
        "recordCount": record_count.tolist(), "prediction": _finite(prediction), "predictionMA": _finite(prediction_ma),
        "upper": _finite(upper), "lower": _finite(lower), "deviation": _finite(prediction_deviation), "smoothedDeviation": _finite(smoothed_deviation),
        "upperTrigger": _finite(upper_trigger), "lowerTrigger": _finite(lower_trigger),
        "overUpper": over_upper.tolist(), "underUpper": under_upper.tolist(), "overLower": over_lower.tolist(), "underLower": under_lower.tolist(),
        "overMid": over_mid.tolist(), "underMid": under_mid.tolist(), "overAverage": over_average.tolist(), "underAverage": under_average.tolist(),
        "storedParameterOne": _finite(np.asarray(parameter_one, dtype=float)), "storedParameterTwo": _finite(np.asarray(parameter_two, dtype=float)),
        "storedPrice": _finite(np.asarray(price_records, dtype=float)), "storedResult": _finite(np.asarray(result_records, dtype=float)),
    },
}
