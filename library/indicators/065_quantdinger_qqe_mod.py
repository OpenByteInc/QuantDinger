# ============================================================
# QuantDinger QQE MOD
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger QQE MOD"
my_indicator_description = (
    "Two smoothed RSI trailing-band systems with a Bollinger envelope on the "
    "primary trail and highlighted agreement from the secondary oscillator."
)

# @param primary_rsi_length int 6 Primary RSI period range=2:100:1
# @param primary_smoothing int 5 Primary RSI smoothing range=1:100:1
# @param primary_factor float 3.0 Primary QQE factor range=0.1:10.0:0.1
# @param secondary_rsi_length int 6 Secondary RSI period range=2:100:1
# @param secondary_smoothing int 5 Secondary RSI smoothing range=1:100:1
# @param secondary_factor float 1.61 Secondary QQE factor range=0.1:10.0:0.01
# @param secondary_threshold float 3.0 Secondary threshold range=0.1:50.0:0.1
# @param bollinger_length int 50 Bollinger period range=2:200:1
# @param bollinger_multiplier float 0.35 Bollinger multiplier range=0.001:5.0:0.001

primary_rsi_length = max(2, int(params.get("primary_rsi_length", 6)))
primary_smoothing = max(1, int(params.get("primary_smoothing", 5)))
primary_factor = max(0.1, float(params.get("primary_factor", 3.0)))
secondary_rsi_length = max(2, int(params.get("secondary_rsi_length", 6)))
secondary_smoothing = max(1, int(params.get("secondary_smoothing", 5)))
secondary_factor = max(0.1, float(params.get("secondary_factor", 1.61)))
secondary_threshold = max(0.1, float(params.get("secondary_threshold", 3.0)))
bollinger_length = max(2, int(params.get("bollinger_length", 50)))
bollinger_multiplier = max(0.001, float(params.get("bollinger_multiplier", 0.35)))

df = df.copy()
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def exponential_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index in range(n):
        value = values[index]
        if np.isfinite(value):
            previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
            result[index] = previous
    return result


def relative_strength(values, period):
    result = np.full(n, np.nan, dtype=float)
    gains = np.zeros(n, dtype=float)
    losses = np.zeros(n, dtype=float)
    for index in range(1, n):
        change = values[index] - values[index - 1]
        gains[index] = max(change, 0.0)
        losses[index] = max(-change, 0.0)
    if n > period:
        average_gain = float(np.sum(gains[1 : period + 1])) / period
        average_loss = float(np.sum(losses[1 : period + 1])) / period
        result[period] = 100.0 if average_loss == 0.0 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
        for index in range(period + 1, n):
            average_gain = (average_gain * (period - 1) + gains[index]) / period
            average_loss = (average_loss * (period - 1) + losses[index]) / period
            result[index] = 100.0 if average_loss == 0.0 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


def calculate_qqe(rsi_period, smoothing, factor):
    rsi = relative_strength(close, rsi_period)
    smoothed = exponential_average(rsi, smoothing)
    rsi_range = np.full(n, np.nan, dtype=float)
    for index in range(1, n):
        if np.isfinite(smoothed[index]) and np.isfinite(smoothed[index - 1]):
            rsi_range[index] = abs(smoothed[index] - smoothed[index - 1])
    dynamic_range = exponential_average(rsi_range, rsi_period * 2 - 1) * factor
    long_band = np.zeros(n, dtype=float)
    short_band = np.zeros(n, dtype=float)
    trend = np.zeros(n, dtype=int)
    trail = np.full(n, np.nan, dtype=float)
    for index in range(n):
        if not np.isfinite(smoothed[index]) or not np.isfinite(dynamic_range[index]):
            continue
        new_long = smoothed[index] - dynamic_range[index]
        new_short = smoothed[index] + dynamic_range[index]
        if index > 0 and np.isfinite(smoothed[index - 1]):
            long_band[index] = max(long_band[index - 1], new_long) if smoothed[index - 1] > long_band[index - 1] and smoothed[index] > long_band[index - 1] else new_long
            short_band[index] = min(short_band[index - 1], new_short) if smoothed[index - 1] < short_band[index - 1] and smoothed[index] < short_band[index - 1] else new_short
            crossed_short = smoothed[index] > short_band[index - 1] and smoothed[index - 1] <= short_band[index - 1]
            crossed_long = smoothed[index] < long_band[index - 1] and smoothed[index - 1] >= long_band[index - 1]
            trend[index] = 1 if crossed_short else -1 if crossed_long else trend[index - 1]
        else:
            long_band[index] = new_long
            short_band[index] = new_short
        trail[index] = long_band[index] if trend[index] == 1 else short_band[index]
    return trail, smoothed


primary_trail, primary_rsi = calculate_qqe(primary_rsi_length, primary_smoothing, primary_factor)
secondary_trail, secondary_rsi = calculate_qqe(secondary_rsi_length, secondary_smoothing, secondary_factor)
primary_centered = primary_trail - 50.0
basis = pd.Series(primary_centered).rolling(bollinger_length, min_periods=bollinger_length).mean().to_numpy(dtype=float)
deviation = pd.Series(primary_centered).rolling(bollinger_length, min_periods=bollinger_length).std(ddof=0).to_numpy(dtype=float) * bollinger_multiplier
upper = basis + deviation
lower = basis - deviation
secondary_centered = secondary_rsi - 50.0

secondary_histogram = []
up_histogram = []
down_histogram = []
for index in range(n):
    value = secondary_centered[index]
    visible = np.isfinite(value) and (value > secondary_threshold or value < -secondary_threshold)
    secondary_histogram.append({"value": float(value), "color": "#707070CC"} if visible else None)
    up = visible and np.isfinite(primary_rsi[index]) and np.isfinite(upper[index]) and value > secondary_threshold and primary_rsi[index] - 50.0 > upper[index]
    down = visible and np.isfinite(primary_rsi[index]) and np.isfinite(lower[index]) and value < -secondary_threshold and primary_rsi[index] - 50.0 < lower[index]
    up_histogram.append(float(value) if up else None)
    down_histogram.append(float(value) if down else None)

cross_up = np.zeros(n, dtype=bool)
cross_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(primary_rsi[index]) and np.isfinite(primary_rsi[index - 1]):
        cross_up[index] = primary_rsi[index] > 50.0 and primary_rsi[index - 1] <= 50.0
        cross_down[index] = primary_rsi[index] < 50.0 and primary_rsi[index - 1] >= 50.0

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Secondary QQE Trend", "data": [float(value - 50.0) if np.isfinite(value) else None for value in secondary_trail], "color": "#FFFFFF", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Secondary RSI", "data": secondary_histogram, "color": "#707070", "type": "histogram", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
        {"name": "QQE Up", "data": up_histogram, "color": "#00C3FF", "type": "histogram", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
        {"name": "QQE Down", "data": down_histogram, "color": "#FF0062", "type": "histogram", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
        {"name": "Zero", "data": [0.0 for _ in range(n)], "color": "#FFFFFF", "type": "line", "lineWidth": 1, "overlay": False},
    ],
    "signals": [
        {"type": "buy", "text": "RSI+", "name": "Primary RSI Cross Up", "data": [0.0 if cross_up[i] else None for i in range(n)], "color": "#00C3FF", "renderMode": "events"},
        {"type": "sell", "text": "RSI-", "name": "Primary RSI Cross Down", "data": [0.0 if cross_down[i] else None for i in range(n)], "color": "#FF0062", "renderMode": "events"},
    ],
    "layers": [],
}
