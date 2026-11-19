# ============================================================
# QuantDinger Stochastic Optimized Trend Tracker
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Stochastic Optimized Trend Tracker"
my_indicator_description = (
    "A long-window stochastic smoothed by an adaptive average and followed "
    "by a delayed optimized trend trail around the 1000 baseline."
)

# @param stochastic_length int 500 Stochastic period range=1:1000:1
# @param stochastic_smoothing int 200 Stochastic smoothing range=1:500:1
# @param tracker_length int 2 Tracker period range=1:100:1
# @param tracker_percent float 0.5 Tracker percent range=0.0:10.0:0.1
# @param show_support bool false Show support line
# @param show_signals bool false Show crossing signals

stochastic_length = max(1, int(params.get("stochastic_length", 500)))
stochastic_smoothing = max(1, int(params.get("stochastic_smoothing", 200)))
tracker_length = max(1, int(params.get("tracker_length", 2)))
tracker_percent = max(0.0, float(params.get("tracker_percent", 0.5)))
show_support = bool(params.get("show_support", False))
show_signals = bool(params.get("show_signals", False))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def adaptive_average(values, period):
    upward = np.zeros(n, dtype=float)
    downward = np.zeros(n, dtype=float)
    for index in range(1, n):
        change = values[index] - values[index - 1]
        upward[index] = max(change, 0.0)
        downward[index] = max(-change, 0.0)
    upward_sum = pd.Series(upward).rolling(9, min_periods=1).sum().to_numpy(dtype=float)
    downward_sum = pd.Series(downward).rolling(9, min_periods=1).sum().to_numpy(dtype=float)
    result = np.zeros(n, dtype=float)
    alpha = 2.0 / (period + 1.0)
    for index in range(n):
        denominator = upward_sum[index] + downward_sum[index]
        cmo = (upward_sum[index] - downward_sum[index]) / denominator if denominator != 0.0 else 0.0
        previous = result[index - 1] if index > 0 else 0.0
        result[index] = alpha * abs(cmo) * values[index] + (1.0 - alpha * abs(cmo)) * previous
    return result


rolling_high = pd.Series(high).rolling(stochastic_length, min_periods=1).max().to_numpy(dtype=float)
rolling_low = pd.Series(low).rolling(stochastic_length, min_periods=1).min().to_numpy(dtype=float)
raw_stochastic = np.full(n, 50.0, dtype=float)
for index in range(n):
    span = rolling_high[index] - rolling_low[index]
    raw_stochastic[index] = 100.0 * (close[index] - rolling_low[index]) / span if span != 0.0 else 50.0

smoothed_stochastic = adaptive_average(raw_stochastic, stochastic_smoothing)
source = smoothed_stochastic + 1000.0
support = adaptive_average(source, tracker_length)
long_stop = np.zeros(n, dtype=float)
short_stop = np.zeros(n, dtype=float)
direction = np.ones(n, dtype=int)
tracker = np.zeros(n, dtype=float)
for index in range(n):
    distance = support[index] * tracker_percent / 100.0
    raw_long = support[index] - distance
    raw_short = support[index] + distance
    if index == 0:
        long_stop[index] = raw_long
        short_stop[index] = raw_short
    else:
        previous_long = long_stop[index - 1]
        previous_short = short_stop[index - 1]
        long_stop[index] = max(raw_long, previous_long) if support[index] > previous_long else raw_long
        short_stop[index] = min(raw_short, previous_short) if support[index] < previous_short else raw_short
        previous_direction = direction[index - 1]
        direction[index] = 1 if previous_direction == -1 and support[index] > previous_short else -1 if previous_direction == 1 and support[index] < previous_long else previous_direction
    market_trail = long_stop[index] if direction[index] == 1 else short_stop[index]
    tracker[index] = market_trail * (200.0 + tracker_percent) / 200.0 if support[index] > market_trail else market_trail * (200.0 - tracker_percent) / 200.0

delayed = np.concatenate((np.full(2, np.nan), tracker[:-2])) if n > 2 else np.full(n, np.nan)
buy_event = np.zeros(n, dtype=bool)
sell_event = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(delayed[index]) and np.isfinite(delayed[index - 1]):
        buy_event[index] = source[index] > delayed[index] and source[index - 1] <= delayed[index - 1]
        sell_event[index] = source[index] < delayed[index] and source[index - 1] >= delayed[index - 1]

zone = [{"upper": 1080.0, "lower": 1020.0, "color": "#9915FF"} for _ in range(n)]
output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Upper Band", "data": [1080.0 for _ in range(n)], "color": "#606060", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Lower Band", "data": [1020.0 for _ in range(n)], "color": "#606060", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Stochastic Zone", "title": False, "data": zone, "color": "#9915FF", "type": "cloud", "opacity": 0.20, "overlay": False},
        {"name": "Stochastic K", "data": [float(value) for value in source], "color": "#0094FF", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Support Line", "data": [float(value) if show_support else None for value in support], "color": "#0585E1", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Optimized Trail", "data": [float(value) if np.isfinite(value) else None for value in delayed], "color": "#B800D9", "type": "line", "lineWidth": 2, "overlay": False},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "Stochastic Cross Up", "data": [float(tracker[i] * 0.995) if show_signals and buy_event[i] else None for i in range(n)], "color": "#00C853", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Stochastic Cross Down", "data": [float(tracker[i] * 1.005) if show_signals and sell_event[i] else None for i in range(n)], "color": "#EF4444", "renderMode": "events"},
    ],
    "layers": [],
}
