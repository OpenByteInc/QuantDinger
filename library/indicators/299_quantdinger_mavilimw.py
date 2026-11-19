# ============================================================
# QuantDinger MavilimW
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger MavilimW"
my_indicator_description = "Builds a six-stage weighted moving-average cascade and colors the result by its current slope."

# @param show_previous bool false Show the fixed 3-5-8-13-21-34 previous version
# @param first_length int 3 First moving-average length range=1:100:1
# @param second_length int 5 Second moving-average length range=1:100:1

show_previous = bool(params.get("show_previous", False))
first_length = min(100, max(1, int(params.get("first_length", 3))))
second_length = min(100, max(1, int(params.get("second_length", 5))))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _weighted_average(values, period):
    series = pd.Series(values, dtype=float)
    denominator = float(period * (period + 1) / 2)

    def _weighted_window(window):
        total = 0.0
        for index in range(len(window)):
            total += float(window[index]) * float(index + 1)
        return total / denominator

    return series.rolling(period, min_periods=period).apply(
        _weighted_window, raw=True
    ).to_numpy(dtype=float)


def _cascade(values, periods):
    result = np.asarray(values, dtype=float)
    stages = []
    for period in periods:
        result = _weighted_average(result, period)
        stages.append(result.copy())
    return result, stages


df = df.copy()
frame = df.reset_index(drop=True)
if "time" not in frame.columns:
    frame["time"] = np.arange(len(frame), dtype=float) * 3600000.0
if "volume" not in frame.columns:
    frame["volume"] = 0.0
for column in ("time", "open", "high", "low", "close", "volume"):
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
frame = frame.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
close = frame["close"].to_numpy(dtype=float)
n = len(frame)

third_length = first_length + second_length
fourth_length = second_length + third_length
fifth_length = third_length + fourth_length
sixth_length = fourth_length + fifth_length
periods = [first_length, second_length, third_length, fourth_length, fifth_length, sixth_length]

trend, stages = _cascade(close, periods)
previous, previous_stages = _cascade(close, [3, 5, 8, 13, 21, 34])

trend_colors = []
for index, value in enumerate(trend):
    prior = trend[index - 1] if index else np.nan
    if np.isfinite(value) and np.isfinite(prior) and value > prior:
        trend_colors.append("#0000FF")
    elif np.isfinite(value) and np.isfinite(prior) and value < prior:
        trend_colors.append("#FF0000")
    else:
        trend_colors.append("#FFFF00")

slope_up = np.zeros(n, dtype=bool)
slope_down = np.zeros(n, dtype=bool)
for index in range(2, n):
    if np.all(np.isfinite([trend[index], trend[index - 1], trend[index - 2]])):
        slope_up[index] = trend[index] > trend[index - 1] and trend[index - 1] <= trend[index - 2]
        slope_down[index] = trend[index] < trend[index - 1] and trend[index - 1] >= trend[index - 2]

calculated = {
    "periods": periods,
    "trend": _finite(trend),
    "previousTrend": _finite(previous),
    "trendColors": trend_colors,
    "slopeTurnsUp": slope_up.tolist(),
    "slopeTurnsDown": slope_down.tolist(),
    "slopeChanges": np.logical_or(slope_up, slope_down).tolist(),
}
for index, stage in enumerate(stages, start=1):
    calculated[f"stage{index}"] = _finite(stage)
for index, stage in enumerate(previous_stages, start=1):
    calculated[f"previousStage{index}"] = _finite(stage)

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "MAVW", "data": _finite(trend), "color": trend_colors, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "MAVW Previous", "data": _finite(previous) if show_previous else [None] * n, "color": "#0000FF", "type": "line", "lineWidth": 2, "overlay": True},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": calculated,
}
