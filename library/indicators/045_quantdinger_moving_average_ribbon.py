# ============================================================
# QuantDinger Moving Average Ribbon
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Moving Average Ribbon"
my_indicator_description = "Displays eighteen moving averages colored by slope and position relative to the 100-period trend reference."

# @param exponential bool true Use exponential moving averages

exponential = bool(params.get("exponential", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


frame = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
frame = frame.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
close = frame["close"].to_numpy(dtype=float)
n = len(frame)
periods = list(range(5, 91, 5))


def _moving_average(period):
    if exponential:
        return _ema(close, period)
    return pd.Series(close, dtype=float).rolling(period, min_periods=period).mean().to_numpy(dtype=float)


averages = {period: _moving_average(period) for period in periods}
reference = _moving_average(100)
changes = {}
colors = {}
for period in periods:
    average = averages[period]
    change = np.full(n, np.nan, dtype=float)
    if n > 1:
        change[1:] = average[1:] - average[:-1]
    changes[period] = change
    series_colors = []
    for index in range(n):
        if np.isfinite(change[index]) and np.all(np.isfinite([average[index], reference[index]])):
            if change[index] >= 0.0 and average[index] > reference[index]:
                series_colors.append("#00FF00")
            elif change[index] < 0.0 and average[index] > reference[index]:
                series_colors.append("#800000")
            elif change[index] <= 0.0 and average[index] < reference[index]:
                series_colors.append("#FF0000")
            elif change[index] >= 0.0 and average[index] < reference[index]:
                series_colors.append("#008000")
            else:
                series_colors.append("#808080")
        else:
            series_colors.append("#808080")
    colors[period] = series_colors

plots = [
    {
        "name": f"MMA{period:02d}", "data": _finite(averages[period]), "color": colors[period],
        "type": "line", "lineWidth": 3 if period in (5, 90) else 1, "overlay": True,
    }
    for period in periods
]

calculated = {"ma100Reference": _finite(reference)}
for period in periods:
    calculated[f"ma{period:02d}"] = _finite(averages[period])
    calculated[f"change{period:02d}"] = _finite(changes[period])
    calculated[f"color{period:02d}"] = colors[period]

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": [], "layers": [], "tables": [],
    "bar_colors": [None] * n, "background_colors": [None] * n, "calculatedVars": calculated,
}
