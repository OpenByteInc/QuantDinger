# ============================================================
# QuantDinger Bull Bear Power
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Bull Bear Power"
my_indicator_description = (
    "ATR-normalized bullish and bearish range pressure with threshold zones, "
    "shortfall columns, and an optional regression trend line."
)

# @param regression_mode bool false Use regression trend line
# @param regression_period int 8 Regression period range=1:200:1

regression_mode = bool(params.get("regression_mode", False))
regression_period = max(1, int(params.get("regression_period", 8)))

df = df.copy()
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
n = len(df)
previous_close = close.shift(1)
true_range = pd.concat(
    [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
).max(axis=1, skipna=True)

atr_values = np.full(n, np.nan, dtype=float)
tr_values = true_range.to_numpy(dtype=float)
if n >= 5:
    atr_values[4] = float(np.mean(tr_values[:5]))
    for index in range(5, n):
        atr_values[index] = (atr_values[index - 1] * 4.0 + tr_values[index]) / 5.0

rolling_low = low.rolling(window=50, min_periods=50).min().to_numpy(dtype=float)
rolling_high = high.rolling(window=50, min_periods=50).max().to_numpy(dtype=float)
close_values = close.to_numpy(dtype=float)
bull = (close_values - rolling_low) / atr_values
bear = -1.0 * (rolling_high - close_values) / atr_values
trend = bull + bear

regression = np.full(n, np.nan, dtype=float)
for index in range(regression_period - 1, n):
    window = trend[index - regression_period + 1 : index + 1]
    if np.all(np.isfinite(window)):
        x_values = np.arange(index - regression_period + 1, index + 1, dtype=float)
        slope, intercept = np.polyfit(x_values, window, 1)
        regression[index] = slope * index + intercept

trend_source = regression if regression_mode else trend
bull_histogram = np.where(np.isfinite(bull) & (bull < 2.0), bull - 2.0, np.nan)
bear_histogram = np.where(np.isfinite(bear) & (bear > -2.0), bear + 2.0, np.nan)

def finite_plot(values):
    return [float(value) if np.isfinite(value) else None for value in values]

bull_cloud = []
bear_cloud = []
center_cloud = []
for index in range(n):
    bull_cloud.append(
        {"upper": max(float(bull[index]), 2.0), "lower": min(float(bull[index]), 2.0), "color": "#008000"}
        if np.isfinite(bull[index]) else None
    )
    bear_cloud.append(
        {"upper": max(float(bear[index]), -2.0), "lower": min(float(bear[index]), -2.0), "color": "#FF0000"}
        if np.isfinite(bear[index]) else None
    )
    center_cloud.append({"upper": 2.0, "lower": -2.0, "color": "#A0A0A0"})

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Bull Power", "data": finite_plot(bull), "color": "#008000", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Bear Power", "data": finite_plot(bear), "color": "#FF0000", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Power Trend", "data": finite_plot(trend_source), "color": "#202020", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Upper Guide", "data": [2.0] * n, "color": "#008000", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Lower Guide", "data": [-2.0] * n, "color": "#FF0000", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Zero Guide", "data": [0.0] * n, "color": "#505050", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Bull Zone", "title": False, "data": bull_cloud, "color": "#008000", "type": "cloud", "opacity": 0.18, "overlay": False},
        {"name": "Bear Zone", "title": False, "data": bear_cloud, "color": "#FF0000", "type": "cloud", "opacity": 0.18, "overlay": False},
        {"name": "Center Zone", "title": False, "data": center_cloud, "color": "#A0A0A0", "type": "cloud", "opacity": 0.08, "overlay": False},
        {"name": "Bull Shortfall", "data": finite_plot(bull_histogram), "color": "#FF0000", "type": "column", "overlay": False},
        {"name": "Bear Shortfall", "data": finite_plot(bear_histogram), "color": "#008000", "type": "column", "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
