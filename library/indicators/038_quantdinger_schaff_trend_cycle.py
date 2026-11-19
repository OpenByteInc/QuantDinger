# ============================================================
# QuantDinger Schaff Trend Cycle
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Schaff Trend Cycle"
my_indicator_description = (
    "A double-stochastic cycle transform of MACD with recursive smoothing and "
    "25/75 regime guides."
)

# @param cycle_length int 12 Cycle length range=2:300:1
# @param fast_length int 26 Fast EMA length range=1:300:1
# @param slow_length int 50 Slow EMA length range=2:500:1
# @param smoothing_factor float 0.5 Smoothing factor range=0.0:1.0:0.05

cycle_length = max(2, int(params.get("cycle_length", 12)))
fast_length = max(1, int(params.get("fast_length", 26)))
slow_length = max(2, int(params.get("slow_length", 50)))
smoothing_factor = max(0.0, min(1.0, float(params.get("smoothing_factor", 0.5))))

df = df.copy()
close = df["close"].astype(float)
n = len(df)


def pine_ema(series, period):
    values = np.asarray(series, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    seed_values = []
    seeded = False
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not seeded:
            seed_values.append(float(value))
            if len(seed_values) == period:
                previous = float(np.mean(seed_values))
                result[index] = previous
                seeded = True
        else:
            previous = alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


close_values = close.to_numpy(dtype=float)
macd = pine_ema(close_values, fast_length) - pine_ema(close_values, slow_length)
macd_series = pd.Series(macd)
macd_low = macd_series.rolling(window=cycle_length, min_periods=cycle_length).min().to_numpy(dtype=float)
macd_range = (
    macd_series.rolling(window=cycle_length, min_periods=cycle_length).max()
    - macd_series.rolling(window=cycle_length, min_periods=cycle_length).min()
).to_numpy(dtype=float)
first_stochastic = np.zeros(n, dtype=float)
first_smoothed = np.zeros(n, dtype=float)
for index in range(n):
    previous_stochastic = first_stochastic[index - 1] if index > 0 else 0.0
    if np.isfinite(macd_range[index]) and macd_range[index] > 0.0:
        first_stochastic[index] = (macd[index] - macd_low[index]) / macd_range[index] * 100.0
    else:
        first_stochastic[index] = previous_stochastic
    previous_smoothed = first_smoothed[index - 1] if index > 0 else first_stochastic[index]
    first_smoothed[index] = previous_smoothed + smoothing_factor * (first_stochastic[index] - previous_smoothed)

first_series = pd.Series(first_smoothed)
first_low = first_series.rolling(window=cycle_length, min_periods=cycle_length).min().to_numpy(dtype=float)
first_range = (
    first_series.rolling(window=cycle_length, min_periods=cycle_length).max()
    - first_series.rolling(window=cycle_length, min_periods=cycle_length).min()
).to_numpy(dtype=float)
second_stochastic = np.zeros(n, dtype=float)
cycle = np.zeros(n, dtype=float)
for index in range(n):
    previous_stochastic = second_stochastic[index - 1] if index > 0 else 0.0
    if np.isfinite(first_range[index]) and first_range[index] > 0.0:
        second_stochastic[index] = (first_smoothed[index] - first_low[index]) / first_range[index] * 100.0
    else:
        second_stochastic[index] = previous_stochastic
    previous_cycle = cycle[index - 1] if index > 0 else second_stochastic[index]
    cycle[index] = previous_cycle + smoothing_factor * (second_stochastic[index] - previous_cycle)

cycle_plot = []
for index, value in enumerate(cycle):
    previous = cycle[index - 1] if index > 0 else value
    cycle_plot.append({"value": float(value), "color": "#008000" if value > previous else "#FF0000"})

regime_cloud = [
    {"upper": 75.0, "lower": 25.0, "color": "#808080"}
    for _ in range(n)
]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Schaff Cycle", "data": cycle_plot, "color": "#008000", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Lower Guide", "data": [25.0 for _ in range(n)], "color": "#808080", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Upper Guide", "data": [75.0 for _ in range(n)], "color": "#808080", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Cycle Regime", "title": False, "data": regime_cloud, "color": "#808080", "type": "cloud", "opacity": 0.04, "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
