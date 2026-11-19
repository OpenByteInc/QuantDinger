# ============================================================
# QuantDinger MACD ReLoaded
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger MACD ReLoaded"
my_indicator_description = (
    "A MACD whose fast, slow, and trigger averages share one of eleven "
    "selectable smoothing methods, including adaptive, zero-lag, Hull, and T3."
)

# @param source str Close Source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param short_length int 12 Short average period range=1:1000:1
# @param long_length int 26 Long average period range=1:1000:1
# @param trigger_length int 9 Trigger period range=1:1000:1
# @param t3_factor float 0.7 Tillson T3 factor range=-10.0:10.0:0.1
# @param show_histogram bool true Show MACD histogram
# @param average_type str VAR Moving average type values=SMA,EMA,WMA,DEMA,TMA,VAR,WWMA,ZLEMA,TSF,HULL,TILL

source_name = str(params.get("source", "Close"))
short_length = min(1000, max(1, int(params.get("short_length", 12))))
long_length = min(1000, max(1, int(params.get("long_length", 26))))
trigger_length = min(1000, max(1, int(params.get("trigger_length", 9))))
t3_factor = min(10.0, max(-10.0, float(params.get("t3_factor", 0.7))))
show_histogram = bool(params.get("show_histogram", True))
average_value = params.get("average_type", "VAR")
average_names = ["SMA", "EMA", "WMA", "DEMA", "TMA", "VAR", "WWMA", "ZLEMA", "TSF", "HULL", "TILL"]
if isinstance(average_value, (int, float)):
    average_type = average_names[max(0, min(10, int(average_value)))]
else:
    average_type = str(average_value).upper()
    if average_type not in average_names:
        average_type = "VAR"

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
source = {
    "Open": open_price, "High": high, "Low": low, "HL2": (high + low) * 0.5,
    "HLC3": (high + low + close) / 3.0, "OHLC4": (open_price + high + low + close) / 4.0,
}.get(source_name, close)
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


def simple_average(values, period):
    return pd.Series(values).rolling(period, min_periods=period).mean().to_numpy(dtype=float)


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


def variable_average(values, period):
    result = np.zeros(n, dtype=float)
    alpha = 2.0 / (period + 1.0)
    upward = np.zeros(n, dtype=float)
    downward = np.zeros(n, dtype=float)
    for index in range(1, n):
        if np.isfinite(values[index]) and np.isfinite(values[index - 1]):
            change = values[index] - values[index - 1]
            upward[index] = max(change, 0.0)
            downward[index] = max(-change, 0.0)
    up_sum = pd.Series(upward).rolling(9, min_periods=1).sum().to_numpy(dtype=float)
    down_sum = pd.Series(downward).rolling(9, min_periods=1).sum().to_numpy(dtype=float)
    for index in range(n):
        denominator = up_sum[index] + down_sum[index]
        cmo = (up_sum[index] - down_sum[index]) / denominator if denominator != 0.0 else 0.0
        weight = alpha * abs(cmo)
        value = values[index] if np.isfinite(values[index]) else 0.0
        previous = result[index - 1] if index > 0 else 0.0
        result[index] = weight * value + (1.0 - weight) * previous
    return result


def wilders_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    alpha = 1.0 / period
    previous = 0.0
    for index in range(n):
        value = values[index]
        if np.isfinite(value):
            previous = alpha * value + (1.0 - alpha) * previous
            result[index] = previous
    return result


def linear_regression(values, period):
    endpoint = np.full(n, np.nan, dtype=float)
    slope_result = np.full(n, np.nan, dtype=float)
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
            slope_result[index] = slope
            endpoint[index] = intercept + slope * float(period - 1)
    return endpoint, slope_result


def shifted_source(values, lag):
    result = np.full(n, np.nan, dtype=float)
    for index in range(lag, n):
        if np.isfinite(values[index]) and np.isfinite(values[index - lag]):
            result[index] = values[index] + (values[index] - values[index - lag])
    return result


def tillson_t3(values, period):
    first = exponential_average(values, period)
    second = exponential_average(first, period)
    third = exponential_average(second, period)
    fourth = exponential_average(third, period)
    fifth = exponential_average(fourth, period)
    sixth = exponential_average(fifth, period)
    factor = t3_factor
    c1 = -(factor ** 3)
    c2 = 3.0 * factor * factor + 3.0 * factor ** 3
    c3 = -6.0 * factor * factor - 3.0 * factor - 3.0 * factor ** 3
    c4 = 1.0 + 3.0 * factor + factor ** 3 + 3.0 * factor * factor
    return c1 * sixth + c2 * fifth + c3 * fourth + c4 * third


def moving_average(values, period):
    if average_type == "SMA":
        return simple_average(values, period)
    if average_type == "EMA":
        return exponential_average(values, period)
    if average_type == "WMA":
        return weighted_average(values, period)
    if average_type == "DEMA":
        first = exponential_average(values, period)
        return 2.0 * first - exponential_average(first, period)
    if average_type == "TMA":
        return simple_average(simple_average(values, max(1, int(np.ceil(period / 2.0)))), max(1, int(np.floor(period / 2.0)) + 1))
    if average_type == "VAR":
        return variable_average(values, period)
    if average_type == "WWMA":
        return wilders_average(values, period)
    if average_type == "ZLEMA":
        return exponential_average(shifted_source(values, int(period / 2)), period)
    if average_type == "TSF":
        endpoint, slope = linear_regression(values, period)
        return endpoint + slope
    if average_type == "HULL":
        half = weighted_average(values, max(1, int(period / 2)))
        full = weighted_average(values, period)
        return weighted_average(2.0 * half - full, max(1, int(round(np.sqrt(period)))))
    return tillson_t3(values, period)


fast_average = moving_average(source, short_length)
slow_average = moving_average(source, long_length)
macd = fast_average - slow_average
trigger = moving_average(macd, trigger_length)
histogram = macd - trigger

histogram_plot = []
for index in range(n):
    if not show_histogram or not np.isfinite(histogram[index]):
        histogram_plot.append(None)
        continue
    previous = histogram[index - 1] if index > 0 and np.isfinite(histogram[index - 1]) else histogram[index]
    color = "#00FF00" if histogram[index] >= 0.0 and histogram[index] > previous else "#B2DFDB" if histogram[index] >= 0.0 else "#FFCDD2" if histogram[index] > previous else "#EF5350"
    histogram_plot.append({"value": float(histogram[index]), "color": color})

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Histogram", "data": histogram_plot, "color": "#00FF00", "type": "histogram", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
        {"name": "MACD ReLoaded", "data": [float(value) if np.isfinite(value) else None for value in macd], "color": "#426BE6", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Trigger", "data": [float(value) if np.isfinite(value) else None for value in trigger], "color": "#E60000", "type": "line", "lineWidth": 2, "overlay": False},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "source": [float(value) if np.isfinite(value) else None for value in source],
        "fastAverage": [float(value) if np.isfinite(value) else None for value in fast_average],
        "slowAverage": [float(value) if np.isfinite(value) else None for value in slow_average],
        "macd": [float(value) if np.isfinite(value) else None for value in macd],
        "trigger": [float(value) if np.isfinite(value) else None for value in trigger],
        "histogram": [float(value) if np.isfinite(value) else None for value in histogram],
        "averageType": average_type,
    },
}
