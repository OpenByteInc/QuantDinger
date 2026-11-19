# ============================================================
# QuantDinger Adaptive Convergence
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Adaptive Convergence"
my_indicator_description = (
    "A correlation-adaptive convergence oscillator with a signal average and "
    "direction-colored histogram."
)

# @param adaptation_period int 20 Correlation period range=2:300:1
# @param fast_period int 10 Fast period range=2:300:1
# @param slow_period int 20 Slow period range=2:300:1
# @param signal_period int 9 Signal period range=2:300:1

adaptation_period = max(2, int(params.get("adaptation_period", 20)))
fast_period = max(2, int(params.get("fast_period", 10)))
slow_period = max(2, int(params.get("slow_period", 20)))
signal_period = max(2, int(params.get("signal_period", 9)))

df = df.copy()
close_values = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)
bar_values = np.arange(n, dtype=float)
correlation = np.full(n, np.nan, dtype=float)
for index in range(adaptation_period - 1, n):
    price_window = close_values[index - adaptation_period + 1 : index + 1]
    bar_window = bar_values[index - adaptation_period + 1 : index + 1]
    if np.std(price_window) > 0.0:
        correlation[index] = float(np.corrcoef(price_window, bar_window)[0, 1])

a1 = 2.0 / (fast_period + 1.0)
a2 = 2.0 / (slow_period + 1.0)
macd_values = np.zeros(n, dtype=float)
for index in range(n):
    r2 = 0.5 * correlation[index] ** 2 + 0.5 if np.isfinite(correlation[index]) else np.nan
    if not np.isfinite(r2):
        macd_values[index] = 0.0
        continue
    coefficient = r2 * ((1.0 - a1) * (1.0 - a2)) + (
        1.0 - r2
    ) * ((1.0 - a1) / (1.0 - a2))
    previous_close = close_values[index - 1] if index > 0 else close_values[index]
    previous_macd = macd_values[index - 1] if index > 0 else 0.0
    second_previous_macd = macd_values[index - 2] if index > 1 else 0.0
    macd_values[index] = (
        (close_values[index] - previous_close) * (a1 - a2)
        + (-a2 - a1 + 2.0) * previous_macd
        - coefficient * second_previous_macd
    )

signal_values = pd.Series(macd_values).ewm(
    span=signal_period, adjust=False, min_periods=signal_period
).mean().to_numpy(dtype=float)
histogram_values = macd_values - signal_values

histogram_plot = []
for index, value in enumerate(histogram_values):
    if not np.isfinite(value):
        histogram_plot.append(None)
        continue
    previous_value = histogram_values[index - 1] if index > 0 else value
    rising = not np.isfinite(previous_value) or value >= previous_value
    if value >= 0.0:
        color = "#5B9CF6" if rising else "#9DC1F8"
    else:
        color = "#F77C80" if not rising else "#FBB9BB"
    histogram_plot.append({"value": float(value), "color": color})

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Histogram",
            "data": histogram_plot,
            "color": "#5B9CF6",
            "type": "column",
            "lineWidth": 1,
            "overlay": False,
        },
        {
            "name": "Adaptive Convergence",
            "data": [float(value) for value in macd_values],
            "color": "#D0D4DC",
            "type": "line",
            "lineWidth": 2,
            "overlay": False,
        },
        {
            "name": "Signal Average",
            "data": [float(value) if np.isfinite(value) else None for value in signal_values],
            "color": "#FF5D00",
            "type": "line",
            "lineWidth": 2,
            "overlay": False,
        },
    ],
    "signals": [],
    "layers": [],
}
