# ============================================================
# QuantDinger OBV MACD
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger OBV MACD"
my_indicator_description = (
    "A volume-pressure price transform smoothed by one of fourteen moving "
    "average families, compared with a slow EMA and filtered through a T-channel."
)

# @param obv_length int 1 OBV transform EMA period range=1:100:1
# @param average_type int 3 Moving average type range=0:13:1
# @param average_length int 9 Transform average period range=1:200:1
# @param macd_slow_length int 26 MACD slow EMA period range=1:300:1
# @param regression_length int 2 Regression projection period range=2:100:1
# @param show_signals bool false Show T-channel direction changes
# @param show_pivots bool false Show oscillator pivots
# @param pivot_period int 50 Pivot search period range=2:300:1

obv_length = max(1, int(params.get("obv_length", 1)))
average_type = max(0, min(13, int(params.get("average_type", 3))))
average_length = max(1, int(params.get("average_length", 9)))
macd_slow_length = max(1, int(params.get("macd_slow_length", 26)))
regression_length = max(2, int(params.get("regression_length", 2)))
show_signals = bool(params.get("show_signals", False))
show_pivots = bool(params.get("show_pivots", False))
pivot_period = max(2, int(params.get("pivot_period", 50)))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
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


def weighted_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    denominator = float(period * (period + 1)) / 2.0
    for index in range(period - 1, n):
        total = 0.0
        valid = True
        for position in range(period):
            value = values[index - period + 1 + position]
            if not np.isfinite(value):
                valid = False
                break
            total += value * float(position + 1)
        if valid:
            result[index] = total / denominator
    return result


def dema(values, period):
    first = exponential_average(values, period)
    second = exponential_average(first, period)
    return 2.0 * first - second


def tema(values, period):
    first = exponential_average(values, period)
    second = exponential_average(first, period)
    third = exponential_average(second, period)
    return 3.0 * (first - second) + third


def triple_dema(values, period):
    first = dema(values, period)
    second = dema(first, period)
    third = dema(second, period)
    return 3.0 * (first - second) + third


def triple_tema(values, period):
    first = tema(values, period)
    second = tema(first, period)
    third = tema(second, period)
    return 3.0 * (first - second) + third


def hull(values, period):
    half = weighted_average(values, max(1, int(period / 2)))
    full = weighted_average(values, period)
    return weighted_average(2.0 * half - full, max(1, int(round(np.sqrt(period)))))


def triple_hull(values, period):
    first = hull(values, period)
    second = hull(first, period)
    third = hull(second, period)
    return 3.0 * (first - second) + third


def shifted_source(values, period):
    lag = max(1, int(round((period - 1) / 2.0)))
    result = np.full(n, np.nan, dtype=float)
    for index in range(lag, n):
        if np.isfinite(values[index]) and np.isfinite(values[index - lag]):
            result[index] = values[index] + (values[index] - values[index - lag])
    return result


def zlema(values, period):
    return exponential_average(shifted_source(values, period), period)


def zldema(values, period):
    return dema(shifted_source(values, period), period)


def zltema(values, period):
    return tema(shifted_source(values, period), period)


def double_zlema(values, period):
    first = zlema(values, period)
    second = zlema(first, period)
    return 2.0 * first - second


def triple_zlema(values, period):
    first = zlema(values, period)
    second = zlema(first, period)
    third = zlema(second, period)
    return 3.0 * (first - second) + third


def low_lag_ema(values, period):
    prepared = np.full(n, np.nan, dtype=float)
    for index in range(2, n):
        if np.isfinite(values[index]) and np.isfinite(values[index - 1]) and np.isfinite(values[index - 2]):
            prepared[index] = 0.25 * values[index] + 0.5 * values[index - 1] + 0.25 * values[index - 2]
    return exponential_average(prepared, period)


def nonlinear_average(values, period, secondary_period):
    ratio = float(period) / float(secondary_period)
    denominator = float(period) - ratio
    coefficient = ratio * (period - 1.0) / denominator if denominator != 0.0 else 0.0
    first = exponential_average(values, period)
    second = exponential_average(first, secondary_period)
    return (1.0 + coefficient) * first - coefficient * second


def transform_average(values, period):
    if average_type == 0:
        return triple_dema(values, period)
    if average_type == 1:
        return triple_tema(values, period)
    if average_type == 2:
        return tema(values, period)
    if average_type == 3:
        return dema(values, period)
    if average_type == 4:
        return exponential_average(values, period)
    if average_type == 5:
        return (triple_tema(values, period) + triple_dema(values, period)) / 2.0
    if average_type == 6:
        return triple_hull(values, period)
    if average_type == 7:
        return zlema(values, period)
    if average_type == 8:
        return zldema(values, period)
    if average_type == 9:
        return zltema(values, period)
    if average_type == 10:
        return double_zlema(values, period)
    if average_type == 11:
        return triple_zlema(values, period)
    if average_type == 12:
        return low_lag_ema(values, period)
    return nonlinear_average(values, period, 26)


signed_volume = np.zeros(n, dtype=float)
for index in range(1, n):
    direction = 1.0 if close[index] > close[index - 1] else -1.0 if close[index] < close[index - 1] else 0.0
    signed_volume[index] = signed_volume[index - 1] + direction * volume[index]

volume_smooth = pd.Series(signed_volume).rolling(14, min_periods=14).mean().to_numpy(dtype=float)
price_spread = pd.Series(high - low).rolling(28, min_periods=28).std(ddof=0).to_numpy(dtype=float)
volume_residual = signed_volume - volume_smooth
volume_spread = pd.Series(volume_residual).rolling(28, min_periods=28).std(ddof=0).to_numpy(dtype=float)
shadow = np.divide(volume_residual, volume_spread, out=np.full(n, np.nan, dtype=float), where=np.isfinite(volume_spread) & (volume_spread != 0.0)) * price_spread
pressure_price = np.where(shadow > 0.0, high + shadow, low + shadow)
pressure_price[~np.isfinite(shadow)] = np.nan
pressure_ema = exponential_average(pressure_price, obv_length)
fast_average = transform_average(pressure_ema, average_length)
slow_average = exponential_average(close, macd_slow_length)
macd = fast_average - slow_average


def regression_projection(values, period):
    result = np.full(n, np.nan, dtype=float)
    x = np.arange(2.0, period + 2.0, dtype=float)
    x_sum = float(np.sum(x))
    x2_sum = float(np.sum(x * x))
    denominator = period * x2_sum - x_sum * x_sum
    for index in range(period - 1, n):
        segment = values[index - period + 1 : index + 1]
        if np.all(np.isfinite(segment)):
            y_sum = float(np.sum(segment))
            xy_sum = float(np.sum(segment * x))
            slope = (period * xy_sum - x_sum * y_sum) / denominator if denominator != 0.0 else 0.0
            average = y_sum / period
            intercept = average - slope * x_sum / period + slope
            result[index] = intercept + slope * period
    return result


projection = regression_projection(macd, regression_length)
channel = np.full(n, np.nan, dtype=float)
channel_direction = np.zeros(n, dtype=int)
cumulative_distance = 0.0
for index in range(n):
    if not np.isfinite(projection[index]):
        continue
    previous_channel = channel[index - 1] if index > 0 and np.isfinite(channel[index - 1]) else projection[index]
    cumulative_distance += abs(projection[index] - previous_channel)
    adaptive_step = cumulative_distance / float(index) if index > 0 else 0.0
    if projection[index] > previous_channel + adaptive_step or projection[index] < previous_channel - adaptive_step:
        channel[index] = projection[index]
    else:
        channel[index] = previous_channel
    if index > 0 and np.isfinite(channel[index - 1]):
        channel_direction[index] = 1 if channel[index] > channel[index - 1] else -1 if channel[index] < channel[index - 1] else channel_direction[index - 1]

direction_up = np.zeros(n, dtype=bool)
direction_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    direction_up[index] = channel_direction[index] == 1 and channel_direction[index - 1] != 1
    direction_down[index] = channel_direction[index] == -1 and channel_direction[index - 1] != -1

pivot_high = np.zeros(n, dtype=bool)
pivot_low = np.zeros(n, dtype=bool)
maximum_price = np.nan
maximum_oscillator = np.nan
minimum_price = np.nan
minimum_oscillator = np.nan
for index in range(n):
    start = max(0, index - pivot_period + 1)
    if np.isfinite(projection[index]):
        high_offset = int(np.nanargmax(projection[start : index + 1]))
        low_offset = int(np.nanargmin(projection[start : index + 1]))
        highest_now = start + high_offset == index
        lowest_now = start + low_offset == index
        if highest_now or not np.isfinite(maximum_oscillator):
            maximum_price = close[index]
            maximum_oscillator = projection[index]
        if lowest_now or not np.isfinite(minimum_oscillator):
            minimum_price = close[index]
            minimum_oscillator = projection[index]
        if close[index] > maximum_price:
            maximum_price = close[index]
        if projection[index] > maximum_oscillator:
            maximum_oscillator = projection[index]
        if close[index] < minimum_oscillator:
            minimum_oscillator = projection[index]
        if projection[index] < minimum_oscillator:
            minimum_oscillator = projection[index]
        if index >= 3:
            recent_max = projection[index - 2]
            prior_max = projection[index - 3]
            pivot_high[index] = np.isfinite(recent_max) and np.isfinite(prior_max) and maximum_oscillator == recent_max and recent_max != prior_max
            recent_min = projection[index - 2]
            prior_min = projection[index - 3]
            pivot_low[index] = np.isfinite(recent_min) and np.isfinite(prior_min) and minimum_oscillator == recent_min and recent_min != prior_min

channel_plot = []
signal_up_plot = [None for _ in range(n)]
signal_down_plot = [None for _ in range(n)]
pivot_high_plot = [None for _ in range(n)]
pivot_low_plot = [None for _ in range(n)]
for index in range(n):
    channel_plot.append({"value": float(channel[index]), "color": "#2962FF" if channel_direction[index] == 1 else "#EF4444"} if np.isfinite(channel[index]) else None)
    target = index - 1
    if show_signals and target >= 0 and np.isfinite(projection[index]):
        if direction_up[index]:
            signal_up_plot[target] = float(projection[index])
        if direction_down[index]:
            signal_down_plot[target] = float(projection[index])
    if show_pivots and np.isfinite(projection[index]):
        if pivot_high[index]:
            pivot_high_plot[index] = float(projection[index] + 2.0)
        if pivot_low[index]:
            pivot_low_plot[index] = float(projection[index] - 2.0)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "T Channel", "data": channel_plot, "color": "#2962FF", "type": "line", "lineWidth": 4, "overlay": False},
        {"name": "Zero", "data": [0.0 for _ in range(n)], "color": "#111827", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Up Change", "data": signal_up_plot, "color": "#2962FF", "type": "line", "lineWidth": 4, "overlay": False},
        {"name": "Down Change", "data": signal_down_plot, "color": "#EF4444", "type": "line", "lineWidth": 4, "overlay": False},
        {"name": "Pivot High", "data": pivot_high_plot, "color": "#EF4444", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Pivot Low", "data": pivot_low_plot, "color": "#2962FF", "type": "line", "lineWidth": 3, "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
