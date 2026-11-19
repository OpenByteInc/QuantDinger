# ============================================================
# QuantDinger kNN Market Classifier
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger kNN Market Classifier"
my_indicator_description = "Reproduces a persistent record-distance neighbor classifier over RSI, ROC, CCI and normalized volume features."

# @param start_time float 946684800000 Training start timestamp in milliseconds
# @param stop_time float 4102444800000 Training stop timestamp in milliseconds
# @param indicator str All Feature family values=RSI,ROC,CCI,Volume,All
# @param short_period int 14 Short feature period range=1:1000:1
# @param long_period int 28 Long feature period range=2:2000:1
# @param base_neighbors int 252 Base neighbor count range=5:100000:1
# @param volatility_filter bool false Require ATR10 above ATR40
# @param bar_threshold int 300 Signal duration counter threshold range=2:5000:1

start_time = float(params.get("start_time", 946684800000.0))
stop_time = float(params.get("stop_time", 4102444800000.0))
indicator = str(params.get("indicator", "All"))
short_period = min(1000, max(1, int(params.get("short_period", 14))))
long_period = min(2000, max(2, int(params.get("long_period", 28))))
base_neighbors = min(100000, max(5, int(params.get("base_neighbors", 252))))
use_volatility_filter = bool(params.get("volatility_filter", False))
bar_threshold = min(5000, max(2, int(params.get("bar_threshold", 300))))
neighbor_count = max(1, int(np.floor(np.sqrt(base_neighbors))))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].fillna(0.0).astype(float).to_numpy(dtype=float)
n = len(df)
times = df["time"].to_numpy(dtype=float) if "time" in df.columns else np.arange(n, dtype=float) * 3600000.0
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _rsi(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= period:
        return result
    changes = np.diff(values); gains = np.maximum(changes, 0.0); losses = np.maximum(-changes, 0.0)
    average_gain = float(np.mean(gains[:period])); average_loss = float(np.mean(losses[:period]))
    for index in range(period, len(values)):
        if index > period:
            average_gain = (average_gain * (period - 1.0) + gains[index - 1]) / period
            average_loss = (average_loss * (period - 1.0) + losses[index - 1]) / period
        result[index] = 50.0 if average_gain == 0.0 and average_loss == 0.0 else 100.0 if average_loss == 0.0 else 0.0 if average_gain == 0.0 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


def _roc(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) > period:
        previous = values[:-period]
        valid = np.isfinite(previous) & (previous != 0.0)
        target = result[period:]
        target[valid] = (values[period:][valid] - previous[valid]) / previous[valid] * 100.0
    return result


def _cci(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        average = float(np.mean(window)); deviation = float(np.mean(np.abs(window - average)))
        result[index] = (values[index] - average) / (0.015 * deviation) if deviation != 0.0 else 0.0
    return result


def _minimax(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        window = values[max(0, index - period + 1):index + 1]
        highest = float(np.max(window)); lowest = float(np.min(window))
        if highest != lowest:
            result[index] = 99.0 * (values[index] - lowest) / (highest - lowest)
    return result


def _rma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < period:
        return result
    previous = float(np.mean(values[:period])); result[period - 1] = previous
    for index in range(period, len(values)):
        previous = (previous * (period - 1.0) + values[index]) / period
        result[index] = previous
    return result


rsi_long = _rsi(close, long_period); rsi_short = _rsi(close, short_period)
cci_long = _cci(close, long_period); cci_short = _cci(close, short_period)
roc_long = _roc(close, long_period); roc_short = _roc(close, short_period)
volume_long = _minimax(volume, long_period); volume_short = _minimax(volume, short_period)
if indicator == "RSI":
    feature_one = rsi_long; feature_two = rsi_short
elif indicator == "CCI":
    feature_one = cci_long; feature_two = cci_short
elif indicator == "ROC":
    feature_one = roc_long; feature_two = roc_short
elif indicator == "Volume":
    feature_one = volume_long; feature_two = volume_short
else:
    feature_one = (rsi_long + cci_long + roc_long + volume_long) / 4.0
    feature_two = (rsi_short + cci_short + roc_short + volume_short) / 4.0

true_range = np.full(n, np.nan, dtype=float)
if n:
    true_range[0] = high[0] - low[0]
for index in range(1, n):
    true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
atr_ten = _rma(true_range, 10)
atr_forty = _rma(true_range, 40)

training_feature_one = []
training_feature_two = []
training_directions = []
predictions = []
prediction_history = np.zeros(n, dtype=float)
signal = np.zeros(n, dtype=int)
bars_history = np.zeros(n, dtype=int)
training_size = np.zeros(n, dtype=int)
prediction_queue_history = []
counter = 0

for index in range(n):
    class_label = int(np.sign(close[index - 1] - close[index])) if index > 0 else 0
    in_window = start_time <= times[index] <= stop_time
    if in_window:
        training_feature_one.append(float(feature_one[index]) if np.isfinite(feature_one[index]) else np.nan)
        training_feature_two.append(float(feature_two[index]) if np.isfinite(feature_two[index]) else np.nan)
        training_directions.append(class_label)
    maximum_distance = -999.0
    if np.all(np.isfinite([feature_one[index], feature_two[index]])):
        for training_index in range(len(training_directions)):
            first = training_feature_one[training_index]; second = training_feature_two[training_index]
            if not np.all(np.isfinite([first, second])):
                continue
            distance = float(np.hypot(feature_one[index] - first, feature_two[index] - second))
            if distance > maximum_distance:
                maximum_distance = distance
                if len(predictions) >= neighbor_count:
                    predictions.pop(0)
                predictions.append(int(training_directions[training_index]))
    prediction = float(np.sum(predictions)) if predictions else 0.0
    prediction_history[index] = prediction
    volatility_allowed = bool(not use_volatility_filter or (np.isfinite(atr_ten[index]) and np.isfinite(atr_forty[index]) and atr_ten[index] > atr_forty[index]))
    long_condition = prediction > 0.0 and volatility_allowed
    short_condition = prediction < 0.0 and volatility_allowed
    if counter == bar_threshold:
        counter = 0
    else:
        counter += 1
    signal[index] = 1 if long_condition else -1 if short_condition else 0
    bars_history[index] = counter
    training_size[index] = len(training_directions)
    prediction_queue_history.append(list(predictions))

changed = np.zeros(n, dtype=bool)
start_long = np.zeros(n, dtype=bool)
start_short = np.zeros(n, dtype=bool)
clear_event = np.zeros(n, dtype=bool)
for index in range(1, n):
    changed[index] = signal[index] != signal[index - 1]
    start_long[index] = changed[index] and signal[index] == 1
    start_short[index] = changed[index] and signal[index] == -1
    clear_event[index] = changed[index] and signal[index] == 0

rolling_high = np.full(n, np.nan, dtype=float)
rolling_low = np.full(n, np.nan, dtype=float)
for index in range(n):
    start = max(0, index - 9)
    rolling_high[index] = float(np.max(high[start:index + 1])); rolling_low[index] = float(np.min(low[start:index + 1]))


def _marker_color(base, strength):
    alpha_steps = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00]
    slot = min(9, max(0, int(strength)))
    red, green, blue = base
    return f"rgba({red},{green},{blue},{alpha_steps[slot]:.2f})"


buy_colors = [_marker_color((0, 128, 255), int(prediction_history[index] * 5)) for index in range(n)]
sell_colors = [_marker_color((255, 0, 128), int(-prediction_history[index] * 5)) for index in range(n)]
signals = [
    {"type": "buy", "name": "Buy", "text": "BUY", "data": [float(rolling_low[index]) if start_long[index] else None for index in range(n)], "color": "#0080FF", "renderMode": "events"},
    {"type": "sell", "name": "Sell", "text": "SELL", "data": [float(rolling_high[index]) if start_short[index] else None for index in range(n)], "color": "#FF0080", "renderMode": "events"},
]
plots = [
    {"name": "Clear Position", "data": [float(close[index]) if clear_event[index] else None for index in range(n)], "color": "#FFFF00", "type": "cross", "size": 4, "overlay": True},
    {"name": "Classifier State", "title": False, "data": [float(close[index]) for index in range(n)], "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
]

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": [], "tables": [],
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "rsiLong": _finite(rsi_long), "rsiShort": _finite(rsi_short), "cciLong": _finite(cci_long), "cciShort": _finite(cci_short),
        "rocLong": _finite(roc_long), "rocShort": _finite(roc_short), "volumeLong": _finite(volume_long), "volumeShort": _finite(volume_short),
        "featureOne": _finite(feature_one), "featureTwo": _finite(feature_two), "atrTen": _finite(atr_ten), "atrForty": _finite(atr_forty),
        "prediction": prediction_history.tolist(), "signal": signal.tolist(), "bars": bars_history.tolist(), "trainingSize": training_size.tolist(),
        "predictionQueues": prediction_queue_history, "startLong": start_long.tolist(), "startShort": start_short.tolist(), "clear": clear_event.tolist(),
        "rollingHigh": rolling_high.tolist(), "rollingLow": rolling_low.tolist(), "neighborCount": int(neighbor_count),
        "buyColors": buy_colors, "sellColors": sell_colors,
        "finalTrainingFeatureOne": training_feature_one, "finalTrainingFeatureTwo": training_feature_two, "finalTrainingDirections": training_directions,
    },
}
