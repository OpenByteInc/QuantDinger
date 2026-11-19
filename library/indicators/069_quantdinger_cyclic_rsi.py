# ============================================================
# QuantDinger Cyclic RSI
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Cyclic RSI"
my_indicator_description = (
    "A phase-compensated RSI tuned to a dominant cycle, with adaptive upper "
    "and lower percentile bands calculated from two cycles of memory."
)

# @param dominant_cycle int 20 Dominant cycle length range=10:100:1
# @param vibration int 10 Vibration period range=2:30:1
# @param leveling float 10.0 Adaptive band percentile range=1.0:40.0:1.0

dominant_cycle = max(10, int(params.get("dominant_cycle", 20)))
vibration = max(2, int(params.get("vibration", 10)))
leveling = max(1.0, min(40.0, float(params.get("leveling", 10.0))))
cycle_length = max(1, int(dominant_cycle / 2))
cyclic_memory = dominant_cycle * 2
torque = 2.0 / (vibration + 1.0)
phasing_lag = max(1, int((vibration - 1) / 2.0))

df = df.copy()
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

changes = np.zeros(n, dtype=float)
changes[1:] = close[1:] - close[:-1]
gains = np.maximum(changes, 0.0)
losses = np.maximum(-changes, 0.0)


def recursive_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    if n >= period:
        result[period - 1] = float(np.sum(values[:period])) / period
        for index in range(period, n):
            result[index] = (result[index - 1] * (period - 1) + values[index]) / period
    return result


average_gain = recursive_average(gains, cycle_length)
average_loss = recursive_average(losses, cycle_length)
rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.isfinite(average_gain[index]) and np.isfinite(average_loss[index]):
        rsi[index] = 100.0 if average_loss[index] == 0.0 else 0.0 if average_gain[index] == 0.0 else 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])

cyclic_rsi = np.zeros(n, dtype=float)
for index in range(n):
    if not np.isfinite(rsi[index]):
        cyclic_rsi[index] = cyclic_rsi[index - 1] if index > 0 else 0.0
        continue
    lagged = rsi[index - phasing_lag] if index >= phasing_lag and np.isfinite(rsi[index - phasing_lag]) else 0.0
    previous = cyclic_rsi[index - 1] if index > 0 else 0.0
    cyclic_rsi[index] = torque * (2.0 * rsi[index] - lagged) + (1.0 - torque) * previous

lower_band = np.full(n, np.nan, dtype=float)
upper_band = np.full(n, np.nan, dtype=float)
percentile = leveling / 100.0
for index in range(n):
    start = max(0, index - cyclic_memory + 1)
    memory = cyclic_rsi[start : index + 1]
    if len(memory) == 0:
        continue
    local_maximum = float(np.max(memory))
    local_minimum = float(np.min(memory))
    step = (local_maximum - local_minimum) / 100.0
    lower_value = local_minimum
    upper_value = local_maximum
    for level in range(101):
        candidate = local_minimum + step * level
        if float(np.sum(memory < candidate)) / cyclic_memory >= percentile:
            lower_value = candidate
            break
    for level in range(101):
        candidate = local_maximum - step * level
        if float(np.sum(memory >= candidate)) / cyclic_memory >= percentile:
            upper_value = candidate
            break
    lower_band[index] = lower_value
    upper_band[index] = upper_value

fixed_zone = [{"upper": 70.0, "lower": 30.0, "color": "#C0C0C0"} for _ in range(n)]
adaptive_zone = []
for index in range(n):
    adaptive_zone.append({"upper": float(max(upper_band[index], lower_band[index])), "lower": float(min(upper_band[index], lower_band[index])), "color": "#808080"} if np.isfinite(upper_band[index]) and np.isfinite(lower_band[index]) else None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Lower Band", "data": [float(value) if np.isfinite(value) else None for value in lower_band], "color": "#00FFFF", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Upper Band", "data": [float(value) if np.isfinite(value) else None for value in upper_band], "color": "#00FFFF", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "CRSI", "data": [float(value) for value in cyclic_rsi], "color": "#FF00FF", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Fixed RSI Zone", "title": False, "data": fixed_zone, "color": "#C0C0C0", "type": "cloud", "opacity": 0.10, "overlay": False},
        {"name": "Adaptive RSI Zone", "title": False, "data": adaptive_zone, "color": "#808080", "type": "cloud", "opacity": 0.10, "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
