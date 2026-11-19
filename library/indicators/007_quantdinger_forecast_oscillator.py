# ============================================================
# QuantDinger Forecast Oscillator
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Forecast Oscillator"
my_indicator_description = (
    "A normalized comparison between the selected source and the prior "
    "one-step linear-regression forecast, colored by oscillator direction."
)

# @param source str close Source values=open,high,low,close,hl2,hlc3,ohlc4
# @param length int 14 Regression length range=1:500:1

source_name = str(params.get("source", "close")).lower()
length = min(500, max(1, int(params.get("length", params.get("period", 14)))))

df = df.copy()
open_values = df["open"].astype(float)
high_values = df["high"].astype(float)
low_values = df["low"].astype(float)
close_values = df["close"].astype(float)
sources = {
    "open": open_values,
    "high": high_values,
    "low": low_values,
    "close": close_values,
    "hl2": (high_values + low_values) / 2.0,
    "hlc3": (high_values + low_values + close_values) / 3.0,
    "ohlc4": (open_values + high_values + low_values + close_values) / 4.0,
}
source = sources.get(source_name, close_values)


def rolling_regression(series, length, regression_offset):
    values = series.to_numpy(dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if length == 1:
        result[np.isfinite(values)] = values[np.isfinite(values)]
        return pd.Series(result, index=series.index)
    x_sum = length * (length - 1) / 2.0
    x_square_sum = length * (length - 1) * (2 * length - 1) / 6.0
    denominator = length * x_square_sum - x_sum * x_sum
    evaluation_x = length - 1 - regression_offset
    for index in range(length - 1, len(values)):
        window = values[index - length + 1 : index + 1]
        if not np.all(np.isfinite(window)):
            continue
        y_sum = float(np.sum(window))
        xy_sum = 0.0
        for window_index in range(length):
            xy_sum += window_index * float(window[window_index])
        slope = (length * xy_sum - x_sum * y_sum) / denominator
        intercept = (y_sum - slope * x_sum) / length
        result[index] = intercept + slope * evaluation_x
    return pd.Series(result, index=series.index)


regression_now = rolling_regression(source, length, 0)
regression_prior_point = rolling_regression(source, length, 1)
regression_slope = regression_now - regression_prior_point
forecast = regression_now + regression_slope
oscillator = 100.0 * (source - forecast.shift(1)) / source.replace(0.0, np.nan)

values = oscillator.to_numpy(dtype=float)
previous_values = np.concatenate(([np.nan], values[:-1]))
plot_data = []
for index in range(len(values)):
    value = values[index]
    if not np.isfinite(value):
        plot_data.append(None)
        continue
    previous = values[index - 1] if index > 0 else np.nan
    if np.isfinite(previous) and value > previous:
        color = "#21C400"
    elif np.isfinite(previous) and value < previous:
        color = "#960012"
    else:
        color = "#2962FF"
    plot_data.append({"value": float(value), "color": color})

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Forecast Difference",
            "data": plot_data,
            "color": "#2962FF",
            "type": "line",
            "lineWidth": 2,
            "overlay": False,
        },
        {
            "name": "Zero Reference",
            "data": [0.0 for _ in range(len(df))],
            "color": "#2962FF",
            "type": "line",
            "style": "dashed",
            "lineWidth": 1,
            "overlay": False,
        },
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None for _ in range(len(df))],
    "background_colors": [None for _ in range(len(df))],
    "calculatedVars": {
        "source": [float(value) if np.isfinite(value) else None for value in source.to_numpy(dtype=float)],
        "regressionNow": [float(value) if np.isfinite(value) else None for value in regression_now.to_numpy(dtype=float)],
        "regressionPriorPoint": [float(value) if np.isfinite(value) else None for value in regression_prior_point.to_numpy(dtype=float)],
        "regressionSlope": [float(value) if np.isfinite(value) else None for value in regression_slope.to_numpy(dtype=float)],
        "forecast": [float(value) if np.isfinite(value) else None for value in forecast.to_numpy(dtype=float)],
        "oscillator": [float(value) if np.isfinite(value) else None for value in values],
        "rising": [bool(np.isfinite(value) and np.isfinite(previous) and value > previous) for value, previous in zip(values, previous_values)],
        "falling": [bool(np.isfinite(value) and np.isfinite(previous) and value < previous) for value, previous in zip(values, previous_values)],
    },
}
