# ============================================================
# QuantDinger Profit Maximizer
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Profit Maximizer"
my_indicator_description = (
    "A selectable moving-average baseline with a ratcheting ATR trail, "
    "trend highlighting, and baseline or price crossover events."
)

# @param atr_length int 10 ATR period range=1:100:1
# @param atr_multiplier float 3.0 ATR multiplier range=0.1:10.0:0.1
# @param ma_type int 1 Moving average type range=0:7:1
# @param ma_length int 10 Moving average period range=1:100:1
# @param use_wilder_atr bool true Use Wilder ATR
# @param normalize_atr bool false Normalize ATR by close
# @param show_support bool true Show moving average
# @param show_support_signals bool true Show average crossing signals
# @param show_price_signals bool false Show price crossing signals
# @param highlight_state bool true Highlight trend state

atr_length = max(1, int(params.get("atr_length", 10)))
atr_multiplier = max(0.1, float(params.get("atr_multiplier", 3.0)))
ma_type = max(0, min(7, int(params.get("ma_type", 1))))
ma_length = max(1, int(params.get("ma_length", 10)))
use_wilder_atr = bool(params.get("use_wilder_atr", True))
normalize_atr = bool(params.get("normalize_atr", False))
show_support = bool(params.get("show_support", True))
show_support_signals = bool(params.get("show_support_signals", True))
show_price_signals = bool(params.get("show_price_signals", False))
highlight_state = bool(params.get("highlight_state", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
source = (high + low) / 2.0
n = len(df)


def simple_average(values, period):
    return pd.Series(values).rolling(period, min_periods=period).mean().to_numpy(dtype=float)


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


def weighted_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    denominator = float(period * (period + 1)) / 2.0
    for index in range(period - 1, n):
        total = 0.0
        valid = True
        for offset in range(period):
            value = values[index - period + 1 + offset]
            if not np.isfinite(value):
                valid = False
                break
            total += value * float(offset + 1)
        if valid:
            result[index] = total / denominator
    return result


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


def linear_regression(values, period, offset):
    result = np.full(n, np.nan, dtype=float)
    x_sum = float(period * (period - 1)) / 2.0
    x2_sum = float(period * (period - 1) * (2 * period - 1)) / 6.0
    denominator = period * x2_sum - x_sum * x_sum
    for index in range(period - 1, n):
        y_sum = 0.0
        xy_sum = 0.0
        for position in range(period):
            value = values[index - period + 1 + position]
            y_sum += value
            xy_sum += float(position) * value
        slope = (period * xy_sum - x_sum * y_sum) / denominator if denominator != 0.0 else 0.0
        intercept = (y_sum - slope * x_sum) / period
        result[index] = intercept + slope * float(period - 1 - offset)
    return result


if ma_type == 0:
    average = simple_average(source, ma_length)
elif ma_type == 1:
    average = exponential_average(source, ma_length)
elif ma_type == 2:
    average = weighted_average(source, ma_length)
elif ma_type == 3:
    first_period = max(1, int(np.ceil(ma_length / 2.0)))
    second_period = max(1, int(np.floor(ma_length / 2.0)) + 1)
    average = simple_average(simple_average(source, first_period), second_period)
elif ma_type == 4:
    average = adaptive_average(source, ma_length)
elif ma_type == 5:
    average = np.zeros(n, dtype=float)
    alpha = 1.0 / ma_length
    for index, value in enumerate(source):
        previous = average[index - 1] if index > 0 else 0.0
        average[index] = alpha * value + (1.0 - alpha) * previous
elif ma_type == 6:
    lag = ma_length // 2 if ma_length % 2 == 0 else (ma_length - 1) // 2
    adjusted = np.full(n, np.nan, dtype=float)
    for index in range(lag, n):
        adjusted[index] = source[index] + source[index] - source[index - lag]
    average = exponential_average(adjusted, ma_length)
else:
    regression_now = linear_regression(source, ma_length, 0)
    regression_prior = linear_regression(source, ma_length, 1)
    average = regression_now + (regression_now - regression_prior)

previous_close = np.concatenate(([close[0]], close[:-1]))
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close)))
simple_atr = simple_average(true_range, atr_length)
wilder_atr = np.full(n, np.nan, dtype=float)
if n >= atr_length:
    seed = 0.0
    for index in range(atr_length):
        seed += true_range[index]
    wilder_atr[atr_length - 1] = seed / atr_length
    for index in range(atr_length, n):
        wilder_atr[index] = (wilder_atr[index - 1] * (atr_length - 1) + true_range[index]) / atr_length
atr = wilder_atr if use_wilder_atr else simple_atr

long_stop = np.full(n, np.nan, dtype=float)
short_stop = np.full(n, np.nan, dtype=float)
direction = np.ones(n, dtype=int)
trail = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.isfinite(average[index]) or not np.isfinite(atr[index]):
        direction[index] = direction[index - 1] if index > 0 else 1
        continue
    distance = atr_multiplier * atr[index] / close[index] if normalize_atr and close[index] != 0.0 else atr_multiplier * atr[index]
    raw_long = average[index] - distance
    raw_short = average[index] + distance
    if index == 0 or not np.isfinite(long_stop[index - 1]):
        long_stop[index] = raw_long
        short_stop[index] = raw_short
    else:
        previous_long = long_stop[index - 1]
        previous_short = short_stop[index - 1]
        long_stop[index] = max(raw_long, previous_long) if average[index] > previous_long else raw_long
        short_stop[index] = min(raw_short, previous_short) if average[index] < previous_short else raw_short
        previous_direction = direction[index - 1]
        direction[index] = 1 if previous_direction == -1 and average[index] > previous_short else -1 if previous_direction == 1 and average[index] < previous_long else previous_direction
    trail[index] = long_stop[index] if direction[index] == 1 else short_stop[index]

support_up = np.zeros(n, dtype=bool)
support_down = np.zeros(n, dtype=bool)
price_up = np.zeros(n, dtype=bool)
price_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(trail[index]) and np.isfinite(trail[index - 1]) and np.isfinite(average[index]) and np.isfinite(average[index - 1]):
        support_up[index] = average[index] > trail[index] and average[index - 1] <= trail[index - 1]
        support_down[index] = average[index] < trail[index] and average[index - 1] >= trail[index - 1]
        price_up[index] = source[index] > trail[index] and source[index - 1] <= trail[index - 1]
        price_down[index] = source[index] < trail[index] and source[index - 1] >= trail[index - 1]

state_cloud = []
mid_price = (open_price + high + low + close) / 4.0
for index in range(n):
    if highlight_state and np.isfinite(trail[index]):
        state_cloud.append({"upper": float(max(mid_price[index], trail[index])), "lower": float(min(mid_price[index], trail[index])), "color": "#00C853" if average[index] > trail[index] else "#EF4444"})
    else:
        state_cloud.append(None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Moving Average", "data": [float(value) if show_support and np.isfinite(value) else None for value in average], "color": "#0585E1", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "PMax Trail", "data": [float(value) if np.isfinite(value) else None for value in trail], "color": "#EF4444", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "PMax State", "title": False, "data": state_cloud, "color": "#00C853", "type": "cloud", "opacity": 0.14, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "Average Cross Up", "data": [float(trail[i] * 0.995) if show_support_signals and support_up[i] else None for i in range(n)], "color": "#00C853", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Average Cross Down", "data": [float(trail[i] * 1.005) if show_support_signals and support_down[i] else None for i in range(n)], "color": "#EF4444", "renderMode": "events"},
        {"type": "buy", "text": "BUY", "name": "Price Cross Up", "data": [float(trail[i] * 0.995) if show_price_signals and price_up[i] else None for i in range(n)], "color": "#0F18BF", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Price Cross Down", "data": [float(trail[i] * 1.005) if show_price_signals and price_down[i] else None for i in range(n)], "color": "#0F18BF", "renderMode": "events"},
    ],
    "layers": [],
}
