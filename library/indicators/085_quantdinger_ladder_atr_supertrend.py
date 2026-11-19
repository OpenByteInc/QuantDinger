# ============================================================
# QuantDinger Ladder ATR SuperTrend
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Ladder ATR SuperTrend"
my_indicator_description = (
    "Builds separate true-range ladders from bullish and bearish candles, "
    "then drives a directional trailing stop with optional sticky confirmation."
)

# @param ma_length int 7 Ladder moving-average length range=2:100:1
# @param ma_type int 4 MA type: 0 SMA, 1 EMA, 2 RMA, 3 WMA, 4 HMA range=0:4:1
# @param multiplier int 4 ATR multiplier range=1:20:1
# @param wait_for_close bool false Confirm reversals with close
# @param delayed bool false Use delayed sticky stop updates

ma_length = max(2, int(params.get("ma_length", 7)))
ma_type = min(4, max(0, int(params.get("ma_type", 4))))
multiplier = max(1, int(params.get("multiplier", 4)))
wait_for_close = bool(params.get("wait_for_close", False))
delayed = bool(params.get("delayed", False))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

previous_close = np.concatenate(([close[0]], close[:-1])) if n else np.array([], dtype=float)
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close)))


def wma_series(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    weights = np.arange(1.0, period + 1.0)
    denominator = float(np.sum(weights))
    for index in range(period - 1, len(source)):
        window = source[index - period + 1 : index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
    return result


def ladder_average(values, average_type, period):
    if len(values) < period:
        return np.nan
    source = np.asarray(values, dtype=float)
    if average_type == 0:
        return float(np.mean(source[-period:]))
    if average_type == 1:
        alpha = 2.0 / (period + 1.0)
        result = float(source[0])
        for value in source[1:]:
            result = alpha * float(value) + (1.0 - alpha) * result
        return result
    if average_type == 2:
        result = float(np.mean(source[:period]))
        for value in source[period:]:
            result = (result * (period - 1) + float(value)) / period
        return result
    if average_type == 3:
        return float(wma_series(source, period)[-1])
    half_period = max(1, period // 2)
    root_period = max(1, int(round(np.sqrt(period))))
    half = wma_series(source, half_period)
    full = wma_series(source, period)
    difference = 2.0 * half - full
    hull = wma_series(difference, root_period)
    return float(hull[-1]) if np.isfinite(hull[-1]) else np.nan


positive_ranges = []
negative_ranges = []
positive_atr = np.full(n, np.nan, dtype=float)
negative_atr = np.full(n, np.nan, dtype=float)

for index in range(n):
    if open_price[index] < close[index]:
        positive_ranges.append(float(true_range[index]))
    else:
        negative_ranges.append(float(true_range[index]))
    positive_atr[index] = ladder_average(positive_ranges, ma_type, ma_length)
    negative_atr[index] = ladder_average(negative_ranges, ma_type, ma_length)

direction = np.ones(n, dtype=int)
buy_stop_diff = np.full(n, np.nan, dtype=float)
sell_stop_diff = np.full(n, np.nan, dtype=float)
buy_stop = np.full(n, np.nan, dtype=float)
sell_stop = np.full(n, np.nan, dtype=float)
supertrend = np.full(n, np.nan, dtype=float)

for index in range(n):
    if not np.isfinite(positive_atr[index]) or not np.isfinite(negative_atr[index]):
        if index > 0:
            direction[index] = direction[index - 1]
        continue

    current_direction = direction[index - 1] if index > 0 else 1
    raw_buy_diff = float(negative_atr[index] * multiplier)
    raw_sell_diff = float(positive_atr[index] * multiplier)
    previous_buy_diff = buy_stop_diff[index - 1] if index > 0 else np.nan
    previous_sell_diff = sell_stop_diff[index - 1] if index > 0 else np.nan
    buy_stop_diff[index] = min(raw_buy_diff, previous_buy_diff) if current_direction == 1 and np.isfinite(previous_buy_diff) else raw_buy_diff
    sell_stop_diff[index] = min(raw_sell_diff, previous_sell_diff) if current_direction == -1 and np.isfinite(previous_sell_diff) else raw_sell_diff

    buy_current = float(low[index] - buy_stop_diff[index])
    sell_current = float(high[index] + sell_stop_diff[index])
    buy_inverse = float(low[index] - buy_stop_diff[index] / 2.0)
    sell_inverse = float(high[index] + sell_stop_diff[index] / 2.0)

    previous_buy_stop = buy_stop[index - 1] if index > 0 else buy_current
    previous_sell_stop = sell_stop[index - 1] if index > 0 else sell_current
    if not np.isfinite(previous_buy_stop):
        previous_buy_stop = buy_current
    if not np.isfinite(previous_sell_stop):
        previous_sell_stop = sell_current

    if index > 0:
        previous_high_confirmation = close[index - 1] if wait_for_close else high[index - 1]
        previous_low_confirmation = close[index - 1] if wait_for_close else low[index - 1]
        if current_direction == 1 and previous_low_confirmation < previous_buy_stop:
            current_direction = -1
        elif current_direction == -1 and previous_high_confirmation > previous_sell_stop:
            current_direction = 1
        target_reached = (
            (current_direction == 1 and previous_high_confirmation >= previous_sell_stop)
            or (current_direction == -1 and previous_low_confirmation <= previous_buy_stop)
            or not delayed
        )
    else:
        target_reached = not delayed

    direction[index] = current_direction
    if current_direction == 1:
        buy_stop[index] = max(previous_buy_stop, buy_current) if target_reached else previous_buy_stop
    else:
        buy_stop[index] = buy_current if target_reached else max(previous_buy_stop, buy_inverse)

    if current_direction == -1:
        sell_stop[index] = min(previous_sell_stop, sell_current) if target_reached else previous_sell_stop
    else:
        sell_stop[index] = sell_current if target_reached else min(previous_sell_stop, sell_inverse)

    supertrend[index] = buy_stop[index] if current_direction > 0 else sell_stop[index]

direction_change_up = np.zeros(n, dtype=bool)
direction_change_down = np.zeros(n, dtype=bool)
trend_plot = []
for index in range(n):
    if index > 0:
        direction_change_up[index] = direction[index] > direction[index - 1]
        direction_change_down[index] = direction[index] < direction[index - 1]
    trend_plot.append(
        {"value": float(supertrend[index]), "color": "#53C3BD" if direction[index] > 0 else "#FF00FF"}
        if np.isfinite(supertrend[index])
        else None
    )

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Ladder SuperTrend Stop", "data": trend_plot, "color": "#53C3BD", "type": "line", "lineWidth": 2, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "LADDER UP", "name": "Bullish Reversal", "data": [float(low[index]) if direction_change_up[index] else None for index in range(n)], "color": "#53C3BD", "renderMode": "events"},
        {"type": "sell", "text": "LADDER DOWN", "name": "Bearish Reversal", "data": [float(high[index]) if direction_change_down[index] else None for index in range(n)], "color": "#FF00FF", "renderMode": "events"},
    ],
    "layers": [],
}
