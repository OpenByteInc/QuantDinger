# ============================================================
# QuantDinger RSI T3 PMax
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger RSI T3 PMax"
my_indicator_description = (
    "A Wilder-style RSI smoothed by Tillson T3 and paired with an "
    "RSI-range volatility trail for momentum regime changes."
)

# @param rsi_length int 14 RSI period range=2:100:1
# @param t3_length int 8 T3 period range=1:100:1
# @param volume_factor float 0.7 T3 volume factor range=0.0:1.0:0.1
# @param atr_length int 10 RSI range period range=1:100:1
# @param atr_multiplier float 3.0 Trail multiplier range=0.1:10.0:0.1
# @param show_rsi bool true Show RSI
# @param show_support bool true Show T3 support
# @param show_signals bool true Show crossing signals
# @param highlight_state bool true Highlight momentum state

rsi_length = max(2, int(params.get("rsi_length", 14)))
t3_length = max(1, int(params.get("t3_length", 8)))
volume_factor = max(0.0, min(1.0, float(params.get("volume_factor", 0.7))))
atr_length = max(1, int(params.get("atr_length", 10)))
atr_multiplier = max(0.1, float(params.get("atr_multiplier", 3.0)))
show_rsi = bool(params.get("show_rsi", True))
show_support = bool(params.get("show_support", True))
show_signals = bool(params.get("show_signals", True))
highlight_state = bool(params.get("highlight_state", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def recursive_average(values, period):
    result = np.zeros(n, dtype=float)
    alpha = 1.0 / period
    for index, value in enumerate(values):
        previous = result[index - 1] if index > 0 else 0.0
        result[index] = alpha * value + (1.0 - alpha) * previous
    return result


def exponential_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


up_move = np.zeros(n, dtype=float)
down_move = np.zeros(n, dtype=float)
for index in range(1, n):
    change = close[index] - close[index - 1]
    up_move[index] = max(change, 0.0)
    down_move[index] = max(-change, 0.0)

average_up = recursive_average(up_move, rsi_length)
average_down = recursive_average(down_move, rsi_length)
rsi = np.full(n, 50.0, dtype=float)
for index in range(n):
    if average_down[index] == 0.0:
        rsi[index] = 100.0 if average_up[index] > 0.0 else 50.0
    else:
        ratio = average_up[index] / average_down[index]
        rsi[index] = 100.0 - 100.0 / (1.0 + ratio)

simple_up = pd.Series(up_move).rolling(rsi_length, min_periods=rsi_length).mean().to_numpy(dtype=float)
simple_down = pd.Series(down_move).rolling(rsi_length, min_periods=rsi_length).mean().to_numpy(dtype=float)
rsi_high = np.full(n, np.nan, dtype=float)
rsi_low = np.full(n, np.nan, dtype=float)
for index in range(1, n):
    if not np.isfinite(simple_up[index]) or not np.isfinite(simple_down[index]):
        continue
    high_up = max(high[index] - close[index - 1], 0.0)
    high_down = max(close[index - 1] - high[index], 0.0)
    low_up = max(low[index] - close[index - 1], 0.0)
    low_down = max(close[index - 1] - low[index], 0.0)
    adjusted_up_high = (simple_up[index] * (rsi_length - 1) + high_up) / rsi_length
    adjusted_down_high = (simple_down[index] * (rsi_length - 1) + high_down) / rsi_length
    adjusted_up_low = (simple_up[index] * (rsi_length - 1) + low_up) / rsi_length
    adjusted_down_low = (simple_down[index] * (rsi_length - 1) + low_down) / rsi_length
    rsi_high[index] = 100.0 if adjusted_down_high == 0.0 and adjusted_up_high > 0.0 else 50.0 if adjusted_down_high == 0.0 else 100.0 - 100.0 / (1.0 + adjusted_up_high / adjusted_down_high)
    rsi_low[index] = 100.0 if adjusted_down_low == 0.0 and adjusted_up_low > 0.0 else 50.0 if adjusted_down_low == 0.0 else 100.0 - 100.0 / (1.0 + adjusted_up_low / adjusted_down_low)

rsi_true_range = np.full(n, np.nan, dtype=float)
for index in range(1, n):
    if np.isfinite(rsi_high[index]) and np.isfinite(rsi_low[index]):
        rsi_true_range[index] = max(
            rsi_high[index] - rsi_low[index],
            abs(rsi_high[index] - rsi[index - 1]),
            abs(rsi_low[index] - rsi[index - 1]),
        )
rsi_atr = pd.Series(rsi_true_range).rolling(atr_length, min_periods=atr_length).mean().to_numpy(dtype=float)

stage1 = exponential_average(rsi, t3_length)
stage2 = exponential_average(stage1, t3_length)
stage3 = exponential_average(stage2, t3_length)
stage4 = exponential_average(stage3, t3_length)
stage5 = exponential_average(stage4, t3_length)
stage6 = exponential_average(stage5, t3_length)
factor = volume_factor
coefficient1 = -(factor ** 3)
coefficient2 = 3.0 * factor * factor + 3.0 * factor ** 3
coefficient3 = -6.0 * factor * factor - 3.0 * factor - 3.0 * factor ** 3
coefficient4 = 1.0 + 3.0 * factor + 3.0 * factor * factor + factor ** 3
t3 = coefficient1 * stage6 + coefficient2 * stage5 + coefficient3 * stage4 + coefficient4 * stage3

long_stop = np.full(n, np.nan, dtype=float)
short_stop = np.full(n, np.nan, dtype=float)
direction = np.ones(n, dtype=int)
trail = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.isfinite(t3[index]) or not np.isfinite(rsi_atr[index]):
        direction[index] = direction[index - 1] if index > 0 else 1
        continue
    raw_long = t3[index] - atr_multiplier * rsi_atr[index]
    raw_short = t3[index] + atr_multiplier * rsi_atr[index]
    if index == 0 or not np.isfinite(long_stop[index - 1]):
        long_stop[index] = raw_long
        short_stop[index] = raw_short
    else:
        previous_long = long_stop[index - 1]
        previous_short = short_stop[index - 1]
        long_stop[index] = max(raw_long, previous_long) if t3[index] > previous_long else raw_long
        short_stop[index] = min(raw_short, previous_short) if t3[index] < previous_short else raw_short
        previous_direction = direction[index - 1]
        direction[index] = 1 if previous_direction == -1 and t3[index] > previous_short else -1 if previous_direction == 1 and t3[index] < previous_long else previous_direction
    trail[index] = long_stop[index] if direction[index] == 1 else short_stop[index]

buy_event = np.zeros(n, dtype=bool)
sell_event = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(t3[index]) and np.isfinite(trail[index]) and np.isfinite(t3[index - 1]) and np.isfinite(trail[index - 1]):
        buy_event[index] = t3[index] > trail[index] and t3[index - 1] <= trail[index - 1]
        sell_event[index] = t3[index] < trail[index] and t3[index - 1] >= trail[index - 1]

background = [{"upper": 70.0, "lower": 30.0, "color": "#9915FF"} for _ in range(n)]
state_cloud = []
for index in range(n):
    if highlight_state and np.isfinite(trail[index]):
        state_cloud.append({"upper": float(max(rsi[index], trail[index])), "lower": float(min(rsi[index], trail[index])), "color": "#00C853" if t3[index] > trail[index] else "#FF1744"})
    else:
        state_cloud.append(None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "RSI", "data": [float(value) if show_rsi and np.isfinite(value) else None for value in rsi], "color": "#8E1599", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Upper Band", "data": [70.0 for _ in range(n)], "color": "#C0C0C0", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Lower Band", "data": [30.0 for _ in range(n)], "color": "#C0C0C0", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "RSI Zone", "title": False, "data": background, "color": "#9915FF", "type": "cloud", "opacity": 0.10, "overlay": False},
        {"name": "T3 Support", "data": [float(value) if show_support and np.isfinite(value) else None for value in t3], "color": "#111827", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "PMax Trail", "data": [float(value) if np.isfinite(value) else None for value in trail], "color": "#EF4444", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Momentum State", "title": False, "data": state_cloud, "color": "#00C853", "type": "cloud", "opacity": 0.16, "overlay": False},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "T3 Cross Up", "data": [float(trail[i] * 0.995) if show_signals and buy_event[i] else None for i in range(n)], "color": "#00C853", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "T3 Cross Down", "data": [float(trail[i] * 1.005) if show_signals and sell_event[i] else None for i in range(n)], "color": "#EF4444", "renderMode": "events"},
    ],
    "layers": [],
}
