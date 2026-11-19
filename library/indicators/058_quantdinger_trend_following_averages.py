# ============================================================
# QuantDinger Trend Following Averages
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Trend Following Averages"
my_indicator_description = (
    "Twenty moving averages from 5 to 100 bars, colored by normalized trend "
    "strength after optional linear-regression smoothing."
)

# @param ma_type int 0 Moving average type range=0:4:1
# @param trend_period int 20 Trend check period range=5:100:1
# @param channel_rate float 1.0 Trend channel rate percent range=0.1:10.0:0.1
# @param use_regression bool true Use linear regression
# @param regression_period int 10 Regression period range=2:100:1

ma_type = max(0, min(4, int(params.get("ma_type", 0))))
trend_period = max(5, int(params.get("trend_period", 20)))
channel_rate = max(0.1, float(params.get("channel_rate", 1.0))) / 100.0
use_regression = bool(params.get("use_regression", True))
regression_period = max(2, int(params.get("regression_period", 10)))

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
    for index, value in enumerate(values):
        previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


def recursive_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    if n >= period:
        seed = 0.0
        for index in range(period):
            seed += values[index]
        result[period - 1] = seed / period
        for index in range(period, n):
            result[index] = (result[index - 1] * (period - 1) + values[index]) / period
    return result


def weighted_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    denominator = float(period * (period + 1)) / 2.0
    for index in range(period - 1, n):
        total = 0.0
        for offset in range(period):
            total += values[index - period + 1 + offset] * float(offset + 1)
        result[index] = total / denominator
    return result


def volume_weighted_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    for index in range(period - 1, n):
        numerator = 0.0
        denominator = 0.0
        for offset in range(period):
            position = index - period + 1 + offset
            numerator += values[position] * volume[position]
            denominator += volume[position]
        result[index] = numerator / denominator if denominator != 0.0 else values[index]
    return result


def moving_average(period):
    if ma_type == 0:
        return exponential_average(close, period)
    if ma_type == 1:
        return pd.Series(close).rolling(period, min_periods=period).mean().to_numpy(dtype=float)
    if ma_type == 2:
        return recursive_average(close, period)
    if ma_type == 3:
        return weighted_average(close, period)
    return volume_weighted_average(close, period)


def linear_regression(values, period):
    result = np.full(n, np.nan, dtype=float)
    x_sum = float(period * (period - 1)) / 2.0
    x2_sum = float(period * (period - 1) * (2 * period - 1)) / 6.0
    denominator = period * x2_sum - x_sum * x_sum
    for index in range(period - 1, n):
        y_sum = 0.0
        xy_sum = 0.0
        valid = True
        for position in range(period):
            value = values[index - period + 1 + position]
            if not np.isfinite(value):
                valid = False
                break
            y_sum += value
            xy_sum += float(position) * value
        if valid:
            slope = (period * xy_sum - x_sum * y_sum) / denominator if denominator != 0.0 else 0.0
            intercept = (y_sum - slope * x_sum) / period
            result[index] = intercept + slope * float(period - 1)
    return result


rolling_high = pd.Series(high).rolling(280, min_periods=1).max().to_numpy(dtype=float)
rolling_low = pd.Series(low).rolling(280, min_periods=1).min().to_numpy(dtype=float)
trend_channel = (rolling_high - rolling_low) * channel_rate
lengths = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100]
plots = []
palette_up = ["#00FF006F", "#00FF007F", "#00FF008F", "#00FF009F", "#00FF00AF", "#00FF00BF", "#00FF00CF", "#00FF00DF", "#00FF00EF", "#00FF00FF"]
palette_down = ["#FF00006F", "#FF00007F", "#FF00008F", "#FF00009F", "#FF0000AF", "#FF0000BF", "#FF0000CF", "#FF0000DF", "#FF0000EF", "#FF0000FF"]

for average_length in lengths:
    average = moving_average(average_length)
    trend_source = linear_regression(average, regression_period) if use_regression else average
    rolling_maximum = pd.Series(trend_source).rolling(trend_period, min_periods=trend_period).max().to_numpy(dtype=float)
    rolling_minimum = pd.Series(trend_source).rolling(trend_period, min_periods=trend_period).min().to_numpy(dtype=float)
    data = []
    for index in range(n):
        if not np.isfinite(average[index]) or not np.isfinite(rolling_maximum[index]) or not np.isfinite(rolling_minimum[index]) or trend_channel[index] <= 0.0:
            data.append(None)
            continue
        spread = abs(rolling_maximum[index] - rolling_minimum[index])
        trend = 0
        if spread > trend_channel[index]:
            trend = 1 if trend_source[index] > rolling_minimum[index] + trend_channel[index] else -1 if trend_source[index] < rolling_maximum[index] - trend_channel[index] else 0
        strength = trend * spread / trend_channel[index]
        if strength >= 1.0:
            color = palette_up[min(9, max(0, int(np.floor(strength)) - 1))]
            data.append({"value": float(average[index]), "color": color})
        elif strength <= -1.0:
            color = palette_down[min(9, max(0, int(np.floor(abs(strength))) - 1))]
            data.append({"value": float(average[index]), "color": color})
        else:
            data.append(None)
    plots.append({"name": "Trend MA " + str(average_length), "data": data, "color": "#808080", "type": "line", "lineWidth": 1, "overlay": True})

output = {
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
}
