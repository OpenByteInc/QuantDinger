# ============================================================
# QuantDinger MA Ribbon Bar
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger MA Ribbon Bar"
my_indicator_description = (
    "Twenty stacked moving-average state bands that expose trend alignment, "
    "pullbacks, and reversal pressure from 5 through 100 periods."
)

# @param exponential bool true Use exponential moving averages

exponential = bool(params.get("exponential", True))
df = df.copy()
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def moving_average(values, period):
    if not exponential:
        return pd.Series(values).rolling(period, min_periods=period).mean().to_numpy(dtype=float)
    result = np.full(n, np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    seed = []
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) == period:
                previous = float(np.mean(seed))
                result[index] = previous
        else:
            previous = alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


periods = [5 * index for index in range(1, 21)]
averages = [moving_average(close, period) for period in periods]
leading = averages[0]
reference = averages[-1]
plots = []
for plot_index, period in enumerate(periods):
    average = averages[plot_index]
    data = []
    for index in range(n):
        if index == 0 or not np.isfinite(average[index]) or not np.isfinite(average[index - 1]) or not np.isfinite(leading[index]) or not np.isfinite(reference[index]):
            color = "#808080"
        else:
            change = average[index] - average[index - 1]
            if change >= 0.0 and leading[index] > reference[index]:
                color = "#00FF00"
            elif change < 0.0 and leading[index] > reference[index]:
                color = "#800000"
            elif change <= 0.0 and leading[index] < reference[index]:
                color = "#FF0000"
            elif change >= 0.0 and leading[index] < reference[index]:
                color = "#008000"
            else:
                color = "#808080"
        data.append({"value": float(period), "color": color})
    plots.append(
        {
            "name": "Ribbon %03d" % period,
            "data": data,
            "color": "#808080",
            "type": "column",
            "baseValue": float(period - 5),
            "lineWidth": 2,
            "overlay": False,
        }
    )

output = {
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
}
