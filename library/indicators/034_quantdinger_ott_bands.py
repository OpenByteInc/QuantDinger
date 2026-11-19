# ============================================================
# QuantDinger OTT Bands
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger OTT Bands"
my_indicator_description = (
    "Optimized Trend Tracker bands around a selectable adaptive average, with "
    "half-width or Fibonacci subdivisions."
)

# @param average_length int 2 Average length range=1:300:1
# @param optimization_percent float 15.0 Optimization coefficient range=0.0:100.0:0.1
# @param upper_width float 0.1 Upper band coefficient range=0.0:1.0:0.001
# @param lower_width float 0.077 Lower band coefficient range=0.0:1.0:0.001
# @param show_fibonacci bool false Show Fibonacci subdivisions
# @param show_support bool false Show support average
# @param highlight_support bool false Color support by direction
# @param average_type int 4 Average type range=0:7:1
# @param source_mode int 0 Price source range=0:3:1

average_length = max(1, int(params.get("average_length", 2)))
optimization_percent = max(0.0, float(params.get("optimization_percent", 15.0)))
upper_width = max(0.0, float(params.get("upper_width", 0.1)))
lower_width = max(0.0, float(params.get("lower_width", 0.077)))
show_fibonacci = bool(params.get("show_fibonacci", False))
show_support = bool(params.get("show_support", False))
highlight_support = bool(params.get("highlight_support", False))
average_type = max(0, min(7, int(params.get("average_type", 4))))
source_mode = max(0, min(3, int(params.get("source_mode", 0))))

df = df.copy()
open_price = df["open"].astype(float)
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
n = len(df)
if source_mode == 1:
    source = (high + low) / 2.0
elif source_mode == 2:
    source = (high + low + close) / 3.0
elif source_mode == 3:
    source = (open_price + high + low + close) / 4.0
else:
    source = close.copy()


def weighted_average(series, period):
    weights = np.arange(1.0, period + 1.0)
    denominator = float(weights.sum())
    return series.rolling(window=period, min_periods=period).apply(
        lambda values: float(np.dot(values, weights) / denominator), raw=True
    )


def variable_average(series, period):
    values = series.to_numpy(dtype=float)
    upward = np.maximum(np.concatenate(([0.0], np.diff(values))), 0.0)
    downward = np.maximum(-np.concatenate(([0.0], np.diff(values))), 0.0)
    up_sum = pd.Series(upward).rolling(window=9, min_periods=9).sum().to_numpy(dtype=float)
    down_sum = pd.Series(downward).rolling(window=9, min_periods=9).sum().to_numpy(dtype=float)
    result = np.zeros(len(values), dtype=float)
    base_alpha = 2.0 / (period + 1.0)
    for index in range(len(values)):
        denominator = up_sum[index] + down_sum[index]
        cmo = (up_sum[index] - down_sum[index]) / denominator if np.isfinite(denominator) and denominator != 0.0 else 0.0
        coefficient = base_alpha * abs(cmo)
        previous = result[index - 1] if index > 0 else 0.0
        result[index] = coefficient * values[index] + (1.0 - coefficient) * previous
    return pd.Series(result, index=series.index)


def wilder_average(series, period):
    values = series.to_numpy(dtype=float)
    result = np.zeros(len(values), dtype=float)
    alpha = 1.0 / period
    for index, value in enumerate(values):
        previous = result[index - 1] if index > 0 else 0.0
        result[index] = alpha * value + (1.0 - alpha) * previous
    return pd.Series(result, index=series.index)


def regression_line(series, period, offset):
    values = series.to_numpy(dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    x_values = np.arange(period, dtype=float)
    x_mean = float(np.mean(x_values))
    denominator = float(np.sum((x_values - x_mean) ** 2))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1 : index + 1]
        y_mean = float(np.mean(window))
        slope = float(np.sum((x_values - x_mean) * (window - y_mean)) / denominator) if denominator > 0.0 else 0.0
        intercept = y_mean - slope * x_mean
        result[index] = intercept + slope * (period - 1 - offset)
    return pd.Series(result, index=series.index)


if average_type == 0:
    moving_average = source.rolling(window=average_length, min_periods=average_length).mean()
elif average_type == 1:
    moving_average = source.ewm(span=average_length, adjust=False, min_periods=average_length).mean()
elif average_type == 2:
    moving_average = weighted_average(source, average_length)
elif average_type == 3:
    first_length = int(np.ceil(average_length / 2.0))
    second_length = int(np.floor(average_length / 2.0) + 1)
    moving_average = source.rolling(window=first_length, min_periods=first_length).mean().rolling(window=second_length, min_periods=second_length).mean()
elif average_type == 4:
    moving_average = variable_average(source, average_length)
elif average_type == 5:
    moving_average = wilder_average(source, average_length)
elif average_type == 6:
    lag = int(average_length / 2.0) if average_length % 2 == 0 else int((average_length - 1) / 2.0)
    zero_lag_source = source + (source - source.shift(lag))
    moving_average = zero_lag_source.ewm(span=average_length, adjust=False, min_periods=average_length).mean()
else:
    regression_zero = regression_line(source, average_length, 0)
    regression_one = regression_line(source, average_length, 1)
    moving_average = regression_zero + (regression_zero - regression_one)

average_values = moving_average.to_numpy(dtype=float)
long_stop = np.full(n, np.nan, dtype=float)
short_stop = np.full(n, np.nan, dtype=float)
direction = np.ones(n, dtype=int)
tracker = np.full(n, np.nan, dtype=float)
percent_fraction = optimization_percent * 0.01
for index in range(n):
    current_average = average_values[index]
    if not np.isfinite(current_average):
        direction[index] = direction[index - 1] if index > 0 else 1
        continue
    long_candidate = current_average * (1.0 - percent_fraction)
    short_candidate = current_average * (1.0 + percent_fraction)
    previous_long = long_stop[index - 1] if index > 0 and np.isfinite(long_stop[index - 1]) else long_candidate
    previous_short = short_stop[index - 1] if index > 0 and np.isfinite(short_stop[index - 1]) else short_candidate
    long_stop[index] = max(long_candidate, previous_long) if current_average > previous_long else long_candidate
    short_stop[index] = min(short_candidate, previous_short) if current_average < previous_short else short_candidate
    previous_direction = direction[index - 1] if index > 0 else 1
    if previous_direction == -1 and current_average > previous_short:
        current_direction = 1
    elif previous_direction == 1 and current_average < previous_long:
        current_direction = -1
    else:
        current_direction = previous_direction
    direction[index] = current_direction
    middle_tracker = long_stop[index] if current_direction == 1 else short_stop[index]
    tracker[index] = middle_tracker * (200.0 + optimization_percent) / 200.0 if current_average > middle_tracker else middle_tracker * (200.0 - optimization_percent) / 200.0

delayed_tracker = np.concatenate((np.zeros(min(2, n)), tracker[: max(0, n - 2)]))
delayed_tracker = np.where(np.isfinite(delayed_tracker), delayed_tracker, 0.0)
upper = delayed_tracker * (1.0 + upper_width)
lower = delayed_tracker * (1.0 - lower_width)
if show_fibonacci:
    upper_inner = delayed_tracker * (1.0 + upper_width * 0.618)
    upper_fib = delayed_tracker * (1.0 + upper_width * 0.382)
    lower_inner = delayed_tracker * (1.0 - lower_width * 0.618)
    lower_fib = delayed_tracker * (1.0 - lower_width * 0.382)
else:
    upper_inner = delayed_tracker * (1.0 + upper_width * 0.5)
    upper_fib = np.full(n, np.nan, dtype=float)
    lower_inner = delayed_tracker * (1.0 - lower_width * 0.5)
    lower_fib = np.full(n, np.nan, dtype=float)


def finite_plot(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def cloud_between(first, second, color):
    result = []
    for index in range(n):
        if not np.isfinite(first[index]) or not np.isfinite(second[index]):
            result.append(None)
        else:
            result.append({"upper": float(max(first[index], second[index])), "lower": float(min(first[index], second[index])), "color": color})
    return result


support_plot = []
for index, value in enumerate(average_values):
    if not show_support or not np.isfinite(value):
        support_plot.append(None)
    else:
        previous = average_values[index - 1] if index > 0 and np.isfinite(average_values[index - 1]) else value
        color = "#008000" if highlight_support and value > previous else "#FF0000" if highlight_support else "#0585E1"
        support_plot.append({"value": float(value), "color": color})

plots = [
    {"name": "OTT Main", "data": finite_plot(delayed_tracker), "color": "#B800D9", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "OTT Upper", "data": finite_plot(upper), "color": "#4500C4", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "OTT Upper Inner", "data": finite_plot(upper_inner), "color": "#8400FF", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "OTT Upper Fibonacci", "data": finite_plot(upper_fib), "color": "#C830FF", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "OTT Lower", "data": finite_plot(lower), "color": "#00A6FF", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "OTT Lower Inner", "data": finite_plot(lower_inner), "color": "#007BBD", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "OTT Lower Fibonacci", "data": finite_plot(lower_fib), "color": "#024DE3", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Support Average", "data": support_plot, "color": "#0585E1", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Upper Outer Fill", "title": False, "data": cloud_between(upper, upper_inner, "#4500C4"), "color": "#4500C4", "type": "cloud", "opacity": 0.12, "overlay": True},
    {"name": "Upper Inner Fill", "title": False, "data": cloud_between(upper_inner, delayed_tracker, "#C810FF"), "color": "#C810FF", "type": "cloud", "opacity": 0.12, "overlay": True},
    {"name": "Lower Outer Fill", "title": False, "data": cloud_between(lower, lower_inner, "#00A6FF"), "color": "#00A6FF", "type": "cloud", "opacity": 0.12, "overlay": True},
    {"name": "Lower Inner Fill", "title": False, "data": cloud_between(lower_inner, delayed_tracker, "#024DE3"), "color": "#024DE3", "type": "cloud", "opacity": 0.12, "overlay": True},
]
if show_fibonacci:
    plots.extend(
        [
            {"name": "Upper Fibonacci Fill", "title": False, "data": cloud_between(upper_fib, upper_inner, "#8400FF"), "color": "#8400FF", "type": "cloud", "opacity": 0.12, "overlay": True},
            {"name": "Lower Fibonacci Fill", "title": False, "data": cloud_between(lower_fib, lower_inner, "#007BBD"), "color": "#007BBD", "type": "cloud", "opacity": 0.12, "overlay": True},
        ]
    )

output = {
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
}
