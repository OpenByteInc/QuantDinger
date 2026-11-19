# ============================================================
# QuantDinger Madrid MA Ribbon Bar
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Madrid MA Ribbon Bar"
my_indicator_description = "Compresses twenty moving-average slopes and their relationship to the 100-period reference into a stacked ribbon bar."

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
if "time" not in frame.columns:
    frame["time"] = np.arange(len(frame), dtype=float) * 3600000.0
if "volume" not in frame.columns:
    frame["volume"] = 0.0
for column in ("time", "open", "high", "low", "close", "volume"):
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
frame = frame.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
close = frame["close"].to_numpy(dtype=float)
n = len(frame)
lengths = list(range(5, 101, 5))

moving_averages = {}
changes = {}
for length in lengths:
    if exponential:
        average = _ema(close, length)
    else:
        average = pd.Series(close, dtype=float).rolling(length, min_periods=length).mean().to_numpy(dtype=float)
    moving_averages[length] = average
    change = np.full(n, np.nan, dtype=float)
    if n > 1:
        change[1:] = average[1:] - average[:-1]
    changes[length] = change

ma_five = moving_averages[5]
ma_hundred = moving_averages[100]


def _colors(change):
    result = []
    for index in range(n):
        if np.isfinite(change[index]) and np.all(np.isfinite([ma_five[index], ma_hundred[index]])):
            if change[index] >= 0.0 and ma_five[index] > ma_hundred[index]:
                result.append("#00FF00")
            elif change[index] < 0.0 and ma_five[index] > ma_hundred[index]:
                result.append("#800000")
            elif change[index] <= 0.0 and ma_five[index] < ma_hundred[index]:
                result.append("#FF0000")
            elif change[index] >= 0.0 and ma_five[index] < ma_hundred[index]:
                result.append("#008000")
            else:
                result.append("#808080")
        else:
            result.append("#808080")
    return result


colors = {}
for length in lengths:
    color_length = 90 if length in (95, 100) else length
    colors[length] = _colors(changes[color_length])

plots = []
fixed_colors = ("#00FF00", "#800000", "#FF0000", "#008000", "#808080")
for position, length in enumerate(lengths):
    base_value = float(lengths[position - 1]) if position else 0.0
    for color in fixed_colors:
        plots.append({
            "name": f"MMA{length:02d} {color}", "title": False,
            "data": [float(length) if colors[length][index] == color else None for index in range(n)],
            "color": color, "type": "histogram", "baseValue": base_value,
            "lineWidth": 2, "overlay": False,
        })

calculated = {"ma05AboveMA100": (ma_five > ma_hundred).tolist()}
for length in lengths:
    calculated[f"ma{length:02d}"] = _finite(moving_averages[length])
    calculated[f"change{length:02d}"] = _finite(changes[length])
    calculated[f"color{length:02d}"] = colors[length]

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": [], "layers": [], "tables": [],
    "bar_colors": [None] * n, "background_colors": [None] * n, "calculatedVars": calculated,
}
