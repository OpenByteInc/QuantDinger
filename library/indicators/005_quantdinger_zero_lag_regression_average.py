# ============================================================
# QuantDinger Zero-Lag Regression Average
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Zero-Lag Regression Average"
my_indicator_description = (
    "A zero-lag trend line formed by applying rolling linear regression twice "
    "and adding the regression error back to the first estimate."
)

# @param period int 32 Regression period range=2:300:1
# @param offset int 0 Regression offset range=-50:50:1

period = max(2, int(params.get("period", 32)))
offset = int(params.get("offset", 0))

df = df.copy()
close = df["close"].astype(float)


def rolling_regression(series, length, regression_offset):
    values = series.to_numpy(dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
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


first_regression = rolling_regression(close, period, offset)
second_regression = rolling_regression(first_regression, period, offset)
zero_lag = first_regression + (first_regression - second_regression)
plot_data = [float(value) if np.isfinite(value) else None for value in zero_lag]

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Zero-Lag Regression",
            "data": plot_data,
            "color": "#F4D03F",
            "type": "line",
            "lineWidth": 3,
            "overlay": True,
        }
    ],
    "signals": [],
    "layers": [],
}
