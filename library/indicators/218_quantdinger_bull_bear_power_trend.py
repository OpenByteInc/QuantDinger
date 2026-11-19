# ============================================================
# QuantDinger Bull Bear Power Trend
# Independent Python implementation for the QuantDinger indicator runtime.
# Formula-equivalent reuse of the previously validated indicator 015.
# ============================================================

my_indicator_name = "QuantDinger Bull Bear Power Trend"
my_indicator_description = (
    "ATR-normalized bullish and bearish range pressure with threshold zones, "
    "shortfall columns, and an optional rolling regression trend."
)

# @param regression_mode bool false Use rolling regression trend
# @param regression_period int 8 Regression period range=1:200:1
# @param bull_color str #16A34A Bull pressure color
# @param bear_color str #EF4444 Bear pressure color
# @param trend_color str #E5E7EB Trend color
# @param zone_opacity float 0.18 Pressure-zone opacity range=0.02:0.45:0.01

regression_mode = bool(params.get("regression_mode", False))
regression_period = min(200, max(1, int(params.get("regression_period", 8))))
bull_color = str(params.get("bull_color", "#16A34A"))
bear_color = str(params.get("bear_color", "#EF4444"))
trend_color = str(params.get("trend_color", "#E5E7EB"))
zone_opacity = min(0.45, max(0.02, float(params.get("zone_opacity", 0.18))))

frame = df.copy()
high_values = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype=float)
n = len(frame)

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high_values[index] - low_values[index]
    else:
        true_range[index] = max(
            high_values[index] - low_values[index],
            abs(high_values[index] - close_values[index - 1]),
            abs(low_values[index] - close_values[index - 1]),
        )

atr = np.full(n, np.nan, dtype=float)
if n >= 5:
    atr[4] = float(np.mean(true_range[:5]))
    for index in range(5, n):
        atr[index] = (atr[index - 1] * 4.0 + true_range[index]) / 5.0

rolling_low = pd.Series(low_values, dtype=float).rolling(50, min_periods=50).min().to_numpy(dtype=float)
rolling_high = pd.Series(high_values, dtype=float).rolling(50, min_periods=50).max().to_numpy(dtype=float)
bull_power = np.full(n, np.nan, dtype=float)
bear_power = np.full(n, np.nan, dtype=float)
valid = np.isfinite(atr) & (atr != 0.0) & np.isfinite(rolling_low) & np.isfinite(rolling_high)
bull_power[valid] = (close_values[valid] - rolling_low[valid]) / atr[valid]
bear_power[valid] = -(rolling_high[valid] - close_values[valid]) / atr[valid]
power_trend = bull_power + bear_power

regression_trend = np.full(n, np.nan, dtype=float)
if regression_period == 1:
    regression_trend[:] = power_trend
else:
    local_x = np.arange(regression_period, dtype=float)
    x_centered = local_x - float(np.mean(local_x))
    x_variance = float(np.dot(x_centered, x_centered))
    for index in range(regression_period - 1, n):
        window = power_trend[index - regression_period + 1:index + 1]
        if not np.all(np.isfinite(window)):
            continue
        y_mean = float(np.mean(window))
        slope = float(np.dot(x_centered, window - y_mean) / x_variance)
        regression_trend[index] = y_mean + slope * float(regression_period - 1) / 2.0

display_trend = regression_trend if regression_mode else power_trend
bull_shortfall = np.where(np.isfinite(bull_power) & (bull_power < 2.0), bull_power - 2.0, 0.0)
bear_shortfall = np.where(np.isfinite(bear_power) & (bear_power > -2.0), bear_power + 2.0, 0.0)


def finite_series(values):
    return [float(value) if np.isfinite(value) else None for value in values]


bull_cloud = []
bear_cloud = []
center_cloud = []
for index in range(n):
    if np.isfinite(bull_power[index]):
        bull_cloud.append({"upper": float(max(bull_power[index], 2.0)), "lower": float(min(bull_power[index], 2.0)), "color": bull_color})
    else:
        bull_cloud.append(None)
    if np.isfinite(bear_power[index]):
        bear_cloud.append({"upper": float(max(bear_power[index], -2.0)), "lower": float(min(bear_power[index], -2.0)), "color": bear_color})
    else:
        bear_cloud.append(None)
    center_cloud.append({"upper": 2.0, "lower": -2.0, "color": "#94A3B8"})

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Bull Power", "data": finite_series(bull_power), "color": bull_color, "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Bear Power", "data": finite_series(bear_power), "color": bear_color, "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Power Trend", "data": finite_series(display_trend), "color": trend_color, "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Upper Guide", "data": [2.0] * n, "color": bull_color, "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Lower Guide", "data": [-2.0] * n, "color": bear_color, "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Zero Guide", "data": [0.0] * n, "color": "#64748B", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Bull Zone", "title": False, "data": bull_cloud, "color": bull_color, "type": "cloud", "opacity": zone_opacity, "overlay": False},
        {"name": "Bear Zone", "title": False, "data": bear_cloud, "color": bear_color, "type": "cloud", "opacity": zone_opacity, "overlay": False},
        {"name": "Center Zone", "title": False, "data": center_cloud, "color": "#94A3B8", "type": "cloud", "opacity": 0.08, "overlay": False},
        {"name": "Bull Shortfall", "data": finite_series(bull_shortfall), "color": bear_color, "type": "column", "overlay": False},
        {"name": "Bear Shortfall", "data": finite_series(bear_shortfall), "color": bull_color, "type": "column", "overlay": False},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "trueRange": finite_series(true_range),
        "atr": finite_series(atr),
        "rollingLow": finite_series(rolling_low),
        "rollingHigh": finite_series(rolling_high),
        "bullPower": finite_series(bull_power),
        "bearPower": finite_series(bear_power),
        "powerTrend": finite_series(power_trend),
        "regressionTrend": finite_series(regression_trend),
        "bullShortfall": finite_series(bull_shortfall),
        "bearShortfall": finite_series(bear_shortfall),
    },
}
