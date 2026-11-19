# ============================================================
# QuantDinger Momentum ZigZag
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Momentum ZigZag"
my_indicator_description = (
    "Builds a non-repainting swing path from MACD, moving-average turns, or "
    "QQE momentum changes and marks directional pivots."
)

# @param momentum_mode str QQE Momentum driver values=QQE,MACD,Moving Average
# @param color_force bool true Color segments that reverse after an RSI extreme
# @param macd_fast int 12 MACD fast length range=2:60:1
# @param macd_slow int 26 MACD slow length range=3:120:1
# @param macd_signal int 9 MACD signal length range=1:50:1
# @param macd_ma str EMA MACD oscillator average values=EMA,SMA
# @param signal_ma str EMA MACD signal average values=EMA,SMA
# @param average_type str SMA Moving-average driver values=SMA,EMA,WMA,VWMA,HMA,RMA,DEMA
# @param average_length int 20 Moving-average length range=2:200:1
# @param rsi_length int 14 QQE RSI length range=2:100:1
# @param qqe_factor float 4.238 QQE factor range=0.5:10:0.1
# @param rsi_smoothing int 5 QQE RSI smoothing range=1:50:1
# @param show_labels bool true Show long and short pivot labels

momentum_mode = str(params.get("momentum_mode", "QQE"))
color_force = bool(params.get("color_force", True))
macd_fast = min(60, max(2, int(params.get("macd_fast", 12))))
macd_slow = min(120, max(macd_fast + 1, int(params.get("macd_slow", 26))))
macd_signal = min(50, max(1, int(params.get("macd_signal", 9))))
macd_ma = str(params.get("macd_ma", "EMA"))
signal_ma = str(params.get("signal_ma", "EMA"))
average_type = str(params.get("average_type", "SMA"))
average_length = min(200, max(2, int(params.get("average_length", 20))))
rsi_length = min(100, max(2, int(params.get("rsi_length", 14))))
qqe_factor = min(10.0, max(0.5, float(params.get("qqe_factor", 4.238))))
rsi_smoothing = min(50, max(1, int(params.get("rsi_smoothing", 5))))
show_labels = bool(params.get("show_labels", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

def ema(values, length):
    result = np.full(n, np.nan, dtype=float)
    if n == 0:
        return result
    alpha = 2.0 / (length + 1.0)
    result[0] = float(values[0])
    for index in range(1, n):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result

def rma(values, length):
    result = np.full(n, np.nan, dtype=float)
    if n == 0:
        return result
    alpha = 1.0 / float(length)
    result[0] = float(values[0])
    for index in range(1, n):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result

def sma(values, length):
    return pd.Series(values, dtype=float).rolling(length, min_periods=1).mean().to_numpy(dtype=float)

def wma(values, length):
    weights = np.arange(1, length + 1, dtype=float)
    series = pd.Series(values, dtype=float)
    return series.rolling(length, min_periods=1).apply(
        lambda window: float(np.dot(window, weights[-len(window):]) / np.sum(weights[-len(window):])),
        raw=True,
    ).to_numpy(dtype=float)

def selected_average(values, length, average_name):
    if average_name == "SMA":
        return sma(values, length)
    if average_name == "WMA":
        return wma(values, length)
    if average_name == "VWMA":
        weighted = pd.Series(values * volume, dtype=float).rolling(length, min_periods=1).sum()
        volume_sum = pd.Series(volume, dtype=float).rolling(length, min_periods=1).sum()
        return (weighted / volume_sum.replace(0.0, np.nan)).fillna(method="ffill").fillna(0.0).to_numpy(dtype=float)
    if average_name == "HMA":
        half = max(1, length // 2)
        root = max(1, int(round(np.sqrt(length))))
        return wma(2.0 * wma(values, half) - wma(values, length), root)
    if average_name == "RMA":
        return rma(values, length)
    if average_name == "DEMA":
        first = ema(values, length)
        return 2.0 * first - ema(first, length)
    return ema(values, length)

fast_average = sma(close, macd_fast) if macd_ma == "SMA" else ema(close, macd_fast)
slow_average = sma(close, macd_slow) if macd_ma == "SMA" else ema(close, macd_slow)
macd_line = fast_average - slow_average
macd_signal_line = sma(macd_line, macd_signal) if signal_ma == "SMA" else ema(macd_line, macd_signal)

macd_up = np.zeros(n, dtype=bool)
macd_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    macd_up[index] = macd_line[index] > macd_signal_line[index] and macd_line[index - 1] <= macd_signal_line[index - 1]
    macd_down[index] = macd_line[index] < macd_signal_line[index] and macd_line[index - 1] >= macd_signal_line[index - 1]

driver_average = selected_average(close, average_length, average_type)
average_up = np.zeros(n, dtype=bool)
average_down = np.zeros(n, dtype=bool)
for index in range(2, n):
    average_up[index] = driver_average[index] > driver_average[index - 1] and driver_average[index - 2] > driver_average[index - 1]
    average_down[index] = driver_average[index] < driver_average[index - 1] and driver_average[index - 2] < driver_average[index - 1]

price_change = np.zeros(n, dtype=float)
if n > 1:
    price_change[1:] = np.diff(close)
gain = np.maximum(price_change, 0.0)
loss = np.maximum(-price_change, 0.0)
average_gain = rma(gain, rsi_length)
average_loss = rma(loss, rsi_length)
rsi = np.full(n, 50.0, dtype=float)
for index in range(n):
    if average_loss[index] == 0.0:
        rsi[index] = 100.0 if average_gain[index] > 0.0 else 50.0
    else:
        strength = average_gain[index] / average_loss[index]
        rsi[index] = 100.0 - 100.0 / (1.0 + strength)

rsi_average = ema(rsi, rsi_smoothing)
atr_rsi = np.zeros(n, dtype=float)
if n > 1:
    atr_rsi[1:] = np.abs(np.diff(rsi_average))
wilders_period = rsi_length * 2 - 1
smoothed_atr_rsi = ema(atr_rsi, wilders_period)
dar = ema(smoothed_atr_rsi, wilders_period) * qqe_factor

long_band = np.zeros(n, dtype=float)
short_band = np.zeros(n, dtype=float)
qqe_trend = np.ones(n, dtype=int)
last_qqe_high = np.zeros(n, dtype=float)
last_qqe_low = np.zeros(n, dtype=float)
if n > 0:
    long_band[0] = rsi_average[0] - dar[0]
    short_band[0] = rsi_average[0] + dar[0]
    last_qqe_high[0] = high[0]
    last_qqe_low[0] = low[0]

qqe_up = np.zeros(n, dtype=bool)
qqe_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    new_long = rsi_average[index] - dar[index]
    new_short = rsi_average[index] + dar[index]
    if rsi_average[index - 1] > long_band[index - 1] and rsi_average[index] > long_band[index - 1]:
        long_band[index] = max(long_band[index - 1], new_long)
    else:
        long_band[index] = new_long
    if rsi_average[index - 1] < short_band[index - 1] and rsi_average[index] < short_band[index - 1]:
        short_band[index] = min(short_band[index - 1], new_short)
    else:
        short_band[index] = new_short

    was_up = qqe_trend[index - 1] == 1
    was_down = qqe_trend[index - 1] == -1
    last_qqe_high[index] = max(last_qqe_high[index - 1], high[index]) if was_up else high[index]
    last_qqe_low[index] = min(last_qqe_low[index - 1], low[index]) if was_down else low[index]

    cross_rsi_up = rsi_average[index] > short_band[index - 1] and rsi_average[index - 1] <= short_band[index - 1]
    cross_rsi_down = rsi_average[index] < long_band[index - 1] and rsi_average[index - 1] >= long_band[index - 1]
    cross_price_up = high[index] > last_qqe_high[index - 1] and high[index - 1] <= last_qqe_high[index - 1]
    cross_price_down = low[index] < last_qqe_low[index - 1] and low[index - 1] >= last_qqe_low[index - 1]
    qqe_trend[index] = 1 if (cross_rsi_up or cross_price_up) else -1 if (cross_rsi_down or cross_price_down) else qqe_trend[index - 1]
    qqe_up[index] = qqe_trend[index] == 1 and qqe_trend[index - 1] == -1
    qqe_down[index] = qqe_trend[index] == -1 and qqe_trend[index - 1] == 1

if momentum_mode == "MACD":
    momentum_up = macd_up
    momentum_down = macd_down
elif momentum_mode == "Moving Average":
    momentum_up = average_up
    momentum_down = average_down
else:
    momentum_up = qqe_up
    momentum_down = qqe_down

momentum_direction = np.zeros(n, dtype=int)
for index in range(n):
    previous = momentum_direction[index - 1] if index > 0 else 0
    momentum_direction[index] = 1 if momentum_up[index] else -1 if momentum_down[index] else previous

rsi_fast_gain = rma(gain, 5)
rsi_fast_loss = rma(loss, 5)
rsi_fast = np.full(n, 50.0, dtype=float)
for index in range(n):
    if rsi_fast_loss[index] == 0.0:
        rsi_fast[index] = 100.0 if rsi_fast_gain[index] > 0.0 else 50.0
    else:
        ratio = rsi_fast_gain[index] / rsi_fast_loss[index]
        rsi_fast[index] = 100.0 - 100.0 / (1.0 + ratio)

def bars_since(condition):
    result = np.full(n, n + 1, dtype=int)
    count = n + 1
    for index in range(n):
        count = 0 if condition[index] else count + 1
        result[index] = count
    return result

since_up = bars_since(momentum_up)
since_down = bars_since(momentum_down)
since_overbought = bars_since(rsi_fast > 80.0)
since_oversold = bars_since(rsi_fast < 20.0)
force_up = np.zeros(n, dtype=bool)
force_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    force_up[index] = momentum_down[index] and since_up[index - 1] >= since_overbought[index - 1]
    force_down[index] = momentum_up[index] and since_down[index - 1] >= since_oversold[index - 1]

pivots = []
peak = float(high[0]) if n else 0.0
bottom = float(low[0]) if n else 0.0
for index in range(1, n):
    previous_direction = momentum_direction[index - 1]
    direction = momentum_direction[index]
    if direction == 1:
        if previous_direction == -1:
            pivots.append((index, float(bottom), "long", bool(force_down[index])))
            peak = float(high[index])
        else:
            peak = max(peak, float(high[index]))
    elif direction == -1:
        if previous_direction == 1:
            pivots.append((index, float(peak), "short", bool(force_up[index])))
            bottom = float(low[index])
        else:
            bottom = min(bottom, float(low[index]))

if not pivots and n > 0:
    pivots = [(0, float(close[0]), "long", False), (n - 1, float(close[-1]), "short", False)]

neutral_line = [None for _ in range(n)]
green_line = [None for _ in range(n)]
red_line = [None for _ in range(n)]

for pivot_number in range(1, len(pivots)):
    start_index, start_value, _, _ = pivots[pivot_number - 1]
    end_index, end_value, _, force_event = pivots[pivot_number]
    span = max(1, end_index - start_index)
    if color_force and force_event:
        target = green_line if pivots[pivot_number][2] == "short" else red_line
    else:
        target = neutral_line
    for index in range(start_index, end_index + 1):
        weight = (index - start_index) / float(span)
        target[index] = float(start_value + (end_value - start_value) * weight)

plots = [
    {
        "name": "Price Reference",
        "title": False,
        "data": [float(value) for value in close],
        "color": "rgba(255, 255, 255, 0.01)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    },
    {
        "name": "ZigZag",
        "data": neutral_line,
        "color": "#9CA3AF",
        "type": "line",
        "lineWidth": 3,
        "overlay": True,
    },
    {
        "name": "Force Up Segment",
        "title": False,
        "data": green_line,
        "color": "#7CFC00",
        "type": "line",
        "lineWidth": 4,
        "overlay": True,
    },
    {
        "name": "Force Down Segment",
        "title": False,
        "data": red_line,
        "color": "#EF4444",
        "type": "line",
        "lineWidth": 4,
        "overlay": True,
    },
]

signals = []
if show_labels:
    long_markers = [None for _ in range(n)]
    short_markers = [None for _ in range(n)]
    for pivot_index, pivot_value, direction, force_event in pivots:
        if direction == "long" and not force_event:
            long_markers[pivot_index] = {
                "value": float(pivot_value),
                "text": f"LONG {pivot_value:.2f}",
                "color": "#7CFC00",
            }
        if direction == "short" and not force_event:
            short_markers[pivot_index] = {
                "value": float(pivot_value),
                "text": f"SHORT {pivot_value:.2f}",
                "color": "#EF4444",
            }
    signals.extend([
        {
            "type": "buy",
            "text": "LONG",
            "name": "Long Pivot",
            "data": long_markers,
            "color": "#7CFC00",
            "renderMode": "events",
        },
        {
            "type": "sell",
            "text": "SHORT",
            "name": "Short Pivot",
            "data": short_markers,
            "color": "#EF4444",
            "renderMode": "events",
        },
    ])

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": []}
