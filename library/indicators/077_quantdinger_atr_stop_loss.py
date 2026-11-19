# ============================================================
# QuantDinger ATR Stop Loss Finder
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger ATR Stop Loss Finder"
my_indicator_description = (
    "Upper and lower stop references offset from configurable price sources "
    "by a selectable moving average of true range."
)

# @param length int 14 True-range length range=1:300:1
# @param smoothing int 0 Smoothing mode range=0:3:1
# @param multiplier float 1.5 ATR multiplier range=0.1:20.0:0.1
# @param upper_source int 0 Upper source range=0:3:1
# @param lower_source int 0 Lower source range=0:3:1

length = max(1, int(params.get("length", 14)))
smoothing = max(0, min(3, int(params.get("smoothing", 0))))
multiplier = max(0.0, float(params.get("multiplier", 1.5)))
upper_source = max(0, min(3, int(params.get("upper_source", 0))))
lower_source = max(0, min(3, int(params.get("lower_source", 0))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def select_upper(mode):
    if mode == 1:
        return close.copy()
    if mode == 2:
        return open_price.copy()
    if mode == 3:
        return (high + low) / 2.0
    return high.copy()


def select_lower(mode):
    if mode == 1:
        return close.copy()
    if mode == 2:
        return open_price.copy()
    if mode == 3:
        return (high + low) / 2.0
    return low.copy()


def simple_average(values, period):
    return pd.Series(values).rolling(period, min_periods=period).mean().to_numpy(dtype=float)


def exponential_average(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


def wilder_average(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < period:
        return result
    previous = float(np.mean(source[:period]))
    result[period - 1] = previous
    for index in range(period, len(source)):
        previous = (previous * (period - 1) + source[index]) / period
        result[index] = previous
    return result


def weighted_average(values, period):
    source = pd.Series(np.asarray(values, dtype=float))
    weights = np.arange(1, period + 1, dtype=float)
    denominator = float(np.sum(weights))
    return source.rolling(period, min_periods=period).apply(
        lambda window: float(np.dot(window, weights) / denominator), raw=True
    ).to_numpy(dtype=float)


previous_close = np.concatenate(([close[0]], close[:-1])) if n else np.array([], dtype=float)
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close)))
if smoothing == 1:
    average_range = simple_average(true_range, length)
elif smoothing == 2:
    average_range = exponential_average(true_range, length)
elif smoothing == 3:
    average_range = weighted_average(true_range, length)
else:
    average_range = wilder_average(true_range, length)

offset = average_range * multiplier
short_stop = select_upper(upper_source) + offset
long_stop = select_lower(lower_source) - offset

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "ATR Short Stop",
            "data": [float(value) if np.isfinite(value) else None for value in short_stop],
            "color": "#EF4444",
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        },
        {
            "name": "ATR Long Stop",
            "data": [float(value) if np.isfinite(value) else None for value in long_stop],
            "color": "#00897B",
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        },
    ],
    "signals": [],
    "layers": [],
}
