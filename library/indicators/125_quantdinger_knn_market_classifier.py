# ============================================================
# QuantDinger kNN Market Classifier
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger kNN Market Classifier"
my_indicator_description = (
    "Classifies market direction from historical RSI, CCI, ROC, and volume "
    "feature pairs using the original rolling k-neighbour voting process."
)

# @param feature_mode str All Feature set values=All,RSI,ROC,CCI,Volume
# @param short_window int 14 Short feature period range=1:100:1
# @param long_window int 28 Long feature period range=2:200:1
# @param base_neighbors int 252 Base neighbour count range=5:1000:1
# @param volatility_filter bool false Require ATR 10 above ATR 40
# @param bar_threshold int 300 Maximum unchanged-signal bars range=2:5000:1
# @param start_year int 2000 Training window start year range=1970:2100:1
# @param end_year int 2100 Training window end year range=1970:2100:1

feature_mode = str(params.get("feature_mode", "All"))
short_window = min(100, max(1, int(params.get("short_window", 14))))
long_window = min(200, max(2, int(params.get("long_window", 28))))
base_neighbors = min(1000, max(5, int(params.get("base_neighbors", 252))))
volatility_filter = bool(params.get("volatility_filter", False))
bar_threshold = min(5000, max(2, int(params.get("bar_threshold", 300))))
start_year = min(2100, max(1970, int(params.get("start_year", 2000))))
end_year = min(2100, max(1970, int(params.get("end_year", 2100))))
if end_year < start_year:
    start_year, end_year = end_year, start_year

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

def rma(values, length):
    result = np.full(n, np.nan, dtype=float)
    if n == 0:
        return result
    result[0] = float(values[0])
    alpha = 1.0 / float(length)
    for index in range(1, n):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result

def rsi(values, length):
    changes = np.zeros(n, dtype=float)
    if n > 1:
        changes[1:] = np.diff(values)
    average_gain = rma(np.maximum(changes, 0.0), length)
    average_loss = rma(np.maximum(-changes, 0.0), length)
    result = np.full(n, 50.0, dtype=float)
    for index in range(n):
        if average_loss[index] == 0.0:
            result[index] = 100.0 if average_gain[index] > 0.0 else 50.0
        else:
            ratio = average_gain[index] / average_loss[index]
            result[index] = 100.0 - 100.0 / (1.0 + ratio)
    return result

def cci(values, length):
    result = np.zeros(n, dtype=float)
    for index in range(n):
        start = max(0, index - length + 1)
        window = values[start:index + 1]
        basis = float(np.mean(window))
        deviation = float(np.mean(np.abs(window - basis)))
        result[index] = (values[index] - basis) / (0.015 * deviation) if deviation > 0.0 else 0.0
    return result

def roc(values, length):
    result = np.zeros(n, dtype=float)
    for index in range(length, n):
        previous = float(values[index - length])
        result[index] = 100.0 * (float(values[index]) - previous) / previous if previous != 0.0 else 0.0
    return result

def rolling_minimax(values, length):
    result = np.full(n, 50.0, dtype=float)
    for index in range(n):
        start = max(0, index - length + 1)
        window = values[start:index + 1]
        lowest = float(np.min(window))
        highest = float(np.max(window))
        result[index] = 99.0 * (float(values[index]) - lowest) / (highest - lowest) if highest > lowest else 50.0
    return result

def true_range_values():
    result = np.zeros(n, dtype=float)
    if n == 0:
        return result
    result[0] = high[0] - low[0]
    for index in range(1, n):
        result[index] = max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )
    return result

rsi_long = rsi(close, long_window)
rsi_short = rsi(close, short_window)
cci_long = cci(close, long_window)
cci_short = cci(close, short_window)
roc_long = roc(close, long_window)
roc_short = roc(close, short_window)
volume_long = rolling_minimax(volume, long_window)
volume_short = rolling_minimax(volume, short_window)

mode_key = feature_mode.strip().upper()
if mode_key == "RSI":
    feature_long, feature_short = rsi_long, rsi_short
elif mode_key == "ROC":
    feature_long, feature_short = roc_long, roc_short
elif mode_key == "CCI":
    feature_long, feature_short = cci_long, cci_short
elif mode_key == "VOLUME":
    feature_long, feature_short = volume_long, volume_short
else:
    feature_long = (rsi_long + cci_long + roc_long + volume_long) / 4.0
    feature_short = (rsi_short + cci_short + roc_short + volume_short) / 4.0

atr_fast = rma(true_range_values(), 10)
atr_slow = rma(true_range_values(), 40)

if "time" in df.columns:
    raw_time = pd.to_numeric(df["time"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    nonzero_time = np.abs(raw_time[np.nonzero(raw_time)])
    timestamp_scale = float(np.median(nonzero_time)) if len(nonzero_time) else 0.0
    seconds_divisor = (
        1.0e9 if timestamp_scale >= 1.0e17
        else 1.0e6 if timestamp_scale >= 1.0e14
        else 1.0e3 if timestamp_scale >= 1.0e11
        else 1.0
    )
    epoch_seconds = np.floor(raw_time / seconds_divisor)
    years = epoch_seconds.astype("datetime64[s]").astype("datetime64[Y]").astype(int) + 1970
else:
    years = np.full(n, start_year, dtype=int)

training_long = []
training_short = []
training_directions = []
prediction_queue = []
prediction_score = np.zeros(n, dtype=float)
signal_state = np.zeros(n, dtype=int)
unchanged_bars = 0
previous_signal = 0
neighbour_count = max(1, int(np.floor(np.sqrt(float(base_neighbors)))))

for index in range(n):
    class_label = 0
    if index > 0:
        class_label = 1 if close[index - 1] > close[index] else -1 if close[index - 1] < close[index] else 0

    if start_year <= int(years[index]) <= end_year:
        training_long.append(float(feature_long[index]))
        training_short.append(float(feature_short[index]))
        training_directions.append(class_label)

    maximum_distance = -1.0
    for train_index in range(len(training_directions)):
        long_delta = float(feature_long[index]) - training_long[train_index]
        short_delta = float(feature_short[index]) - training_short[train_index]
        distance = float(np.sqrt(long_delta * long_delta + short_delta * short_delta))
        if distance > maximum_distance:
            maximum_distance = distance
            if len(prediction_queue) >= neighbour_count:
                prediction_queue.pop(0)
            prediction_queue.append(training_directions[train_index])

    score = float(sum(prediction_queue))
    prediction_score[index] = score
    filter_passed = (atr_fast[index] > atr_slow[index]) if volatility_filter else True
    current_signal = 1 if score > 0.0 and filter_passed else -1 if score < 0.0 and filter_passed else 0

    if unchanged_bars >= bar_threshold:
        current_signal = 0
        unchanged_bars = 0
    elif current_signal == previous_signal:
        unchanged_bars += 1
    else:
        unchanged_bars = 0

    signal_state[index] = current_signal
    previous_signal = current_signal

buy_markers = [None for _ in range(n)]
sell_markers = [None for _ in range(n)]
clear_markers = [None for _ in range(n)]
for index in range(n):
    previous = int(signal_state[index - 1]) if index > 0 else 0
    current = int(signal_state[index])
    if current == previous:
        continue
    if current == 1:
        buy_markers[index] = {"value": float(low[index]), "text": "Buy", "color": "#00B8D4"}
    elif current == -1:
        sell_markers[index] = {"value": float(high[index]), "text": "Sell", "color": "#FF4081"}
    else:
        clear_markers[index] = {"value": float(close[index]), "text": "Clear", "color": "#FACC15"}

plots = [
    {
        "name": "Price Reference",
        "title": False,
        "data": [float(value) for value in close],
        "color": "rgba(255, 255, 255, 0.01)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    }
]

signals = [
    {"type": "buy", "text": "Buy", "name": "kNN Buy", "data": buy_markers, "color": "#00B8D4", "renderMode": "events"},
    {"type": "sell", "text": "Sell", "name": "kNN Sell", "data": sell_markers, "color": "#FF4081", "renderMode": "events"},
    {"type": "exit", "text": "Clear", "name": "kNN Clear", "data": clear_markers, "color": "#FACC15", "renderMode": "events"},
]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": []}
