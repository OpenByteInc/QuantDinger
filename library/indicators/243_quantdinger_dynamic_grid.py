# ============================================================
# QuantDinger Dynamic Grid
# Independent Python implementation for the QuantDinger runtime.
# ============================================================

my_indicator_name = "QuantDinger Dynamic Grid"
my_indicator_description = (
    "Builds a volatility-normalized grid around a Hull moving average, with "
    "proximity-aware channels, throttled reversal events, and a channel oscillator."
)

# @param hma_length int 150 HMA length range=1:1000:1
# @param atr_length int 100 ATR length range=1:1000:1
# @param atr_step float 1.6 ATR multiplier step range=0.1:10.0:0.1
# @param grid_levels int 3 Grid levels on each side range=1:5:1
# @param proximity float 0.5 Proximity sensitivity range=0.01:1.0:0.01
# @param minimum_signal_gap int 10 Minimum bars between signals range=1:500:1
# @param bullish_color str #1ECC75 Bullish color
# @param bearish_color str #C04020 Bearish color
# @param show_edge_labels bool true Show right-edge grid labels
# @param show_dashboard bool true Show position dashboard
# @param dashboard_position str top-right Dashboard position values=top-right,bottom-right

hma_length = min(1000, max(1, int(params.get("hma_length", 150))))
atr_length = min(1000, max(1, int(params.get("atr_length", 100))))
atr_step = min(10.0, max(0.1, float(params.get("atr_step", 1.6))))
grid_levels = min(5, max(1, int(params.get("grid_levels", 3))))
proximity = min(1.0, max(0.01, float(params.get("proximity", 0.5))))
minimum_signal_gap = min(500, max(1, int(params.get("minimum_signal_gap", 10))))
bullish_color = str(params.get("bullish_color", "#1ECC75"))
bearish_color = str(params.get("bearish_color", "#C04020"))
show_edge_labels = bool(params.get("show_edge_labels", True))
show_dashboard = bool(params.get("show_dashboard", True))
dashboard_position = str(params.get("dashboard_position", "top-right")).lower()

df = df.copy()
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 3600.0
open_values = pd.to_numeric(df["open"], errors="coerce").to_numpy(dtype=float)
high_values = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
time_values = pd.to_numeric(df["time"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
n = len(df)


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def rgba(color, alpha):
    value = str(color).strip().lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    try:
        red, green, blue = int(value[:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        red, green, blue = 128, 128, 128
    return f"rgba({red},{green},{blue},{min(1.0, max(0.0, float(alpha))):.3f})"


def weighted_average(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(np.sum(weights))
    if length == 1:
        return source.copy()
    for index in range(length - 1, len(source)):
        window = source[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
    return result


def hull_average(values, length):
    half_length = max(1, length // 2)
    root_length = max(1, int(round(np.sqrt(length))))
    full = weighted_average(values, length)
    half = weighted_average(values, half_length)
    raw = 2.0 * half - full
    return weighted_average(raw, root_length), raw, full, half


def wilder_average(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if not len(source):
        return result
    if length == 1:
        return source.copy()
    if len(source) >= length:
        state = float(np.mean(source[:length]))
        result[length - 1] = state
        for index in range(length, len(source)):
            state = (state * (length - 1) + float(source[index])) / float(length)
            result[index] = state
    return result


center_line, hull_raw, full_wma, half_wma = hull_average(close_values, hma_length)
previous_close = np.r_[np.nan, close_values[:-1]]
true_range = np.maximum(
    high_values - low_values,
    np.maximum(np.abs(high_values - previous_close), np.abs(low_values - previous_close)),
)
if n:
    true_range[0] = high_values[0] - low_values[0]
atr = wilder_average(true_range, atr_length)
grid_step = atr * atr_step
proximity_distance = grid_step * proximity
oscillator = np.divide(
    close_values - center_line,
    grid_step,
    out=np.full(n, np.nan, dtype=float),
    where=np.isfinite(grid_step) & (grid_step != 0.0),
)

upper_levels = [center_line + grid_step * level for level in range(1, 6)]
lower_levels = [center_line - grid_step * level for level in range(1, 6)]
hl2 = (high_values + low_values) * 0.5
hlc3 = (high_values + low_values + close_values) / 3.0
hlcc4 = (high_values + low_values + 2.0 * close_values) / 4.0


def point_series(values, level, upper):
    alpha = 0.15 + 0.65 * (float(level) / float(grid_levels))
    color = rgba(bullish_color if upper else bearish_color, alpha)
    result = []
    for index, value in enumerate(values):
        if not np.isfinite(value) or not np.isfinite(proximity_distance[index]):
            result.append(None)
            continue
        is_close = any(
            abs(source[index] - value) <= proximity_distance[index]
            for source in (close_values, open_values, hl2, hlc3, hlcc4)
        )
        result.append(None if is_close else {"value": float(value), "color": color})
    return result


trend_up = np.zeros(n, dtype=bool)
trend_colors = []
for index in range(n):
    rising = index > 0 and np.isfinite(center_line[index]) and np.isfinite(center_line[index - 1]) and center_line[index] > center_line[index - 1]
    trend_up[index] = rising
    trend_colors.append(bullish_color if rising else bearish_color)

cross_bear = [np.zeros(n, dtype=bool) for _ in range(5)]
cross_bull = [np.zeros(n, dtype=bool) for _ in range(5)]
for level in range(5):
    upper = upper_levels[level]
    lower = lower_levels[level]
    if n > 1:
        cross_bear[level][1:] = (
            np.isfinite(upper[1:]) & np.isfinite(upper[:-1])
            & (close_values[:-1] >= upper[:-1]) & (close_values[1:] < upper[1:])
        )
        cross_bull[level][1:] = (
            np.isfinite(lower[1:]) & np.isfinite(lower[:-1])
            & (close_values[:-1] <= lower[:-1]) & (close_values[1:] > lower[1:])
        )

bear_signal = np.zeros(n, dtype=bool)
bull_signal = np.zeros(n, dtype=bool)
bear_signal_level = np.zeros(n, dtype=int)
bull_signal_level = np.zeros(n, dtype=int)
last_signal_bar = -999
for index in range(n):
    raw_bear_levels = [level + 1 for level in range(grid_levels) if cross_bear[level][index]]
    raw_bull_levels = [level + 1 for level in range(grid_levels) if cross_bull[level][index]]
    if (raw_bear_levels or raw_bull_levels) and index - last_signal_bar >= minimum_signal_gap:
        if raw_bear_levels:
            bear_signal[index] = True
            bear_signal_level[index] = max(raw_bear_levels)
        if raw_bull_levels:
            bull_signal[index] = True
            bull_signal_level[index] = max(raw_bull_levels)
        last_signal_bar = index

plots = [
    {
        "name": "Central HMA",
        "data": [
            {"value": float(center_line[index]), "color": trend_colors[index]}
            if np.isfinite(center_line[index]) else None for index in range(n)
        ],
        "color": bullish_color, "type": "line", "lineWidth": 2, "overlay": True,
    },
    {
        "name": "Central HMA Glow", "title": False,
        "data": [
            {"value": float(center_line[index]), "color": rgba(trend_colors[index], 0.20)}
            if np.isfinite(center_line[index]) else None for index in range(n)
        ],
        "color": rgba(bullish_color, 0.20), "type": "line", "lineWidth": 8, "overlay": True,
    },
]
for level in range(1, grid_levels + 1):
    plots.append({
        "name": f"Upper {level}", "title": False,
        "data": point_series(upper_levels[level - 1], level, True),
        "color": bullish_color, "type": "line", "lineWidth": 1 if level < 3 else 2, "overlay": True,
    })
    plots.append({
        "name": f"Lower {level}", "title": False,
        "data": point_series(lower_levels[level - 1], level, False),
        "color": bearish_color, "type": "line", "lineWidth": 1 if level < 3 else 2, "overlay": True,
    })

zero_line = [0.0 for _ in range(n)]
plots.append({"name": "Oscillator Zero", "title": False, "data": zero_line, "color": "rgba(148,163,184,0.50)", "type": "line", "lineWidth": 1, "overlay": False})
for level in range(1, grid_levels + 1):
    osc_alpha = 0.20 + 0.80 * (float(level) / float(grid_levels))
    plots.append({"name": f"Osc Upper {level}", "title": False, "data": [float(level)] * n, "color": rgba(bullish_color, osc_alpha), "type": "line", "lineWidth": 1, "overlay": False})
    plots.append({"name": f"Osc Lower {level}", "title": False, "data": [float(-level)] * n, "color": rgba(bearish_color, osc_alpha), "type": "line", "lineWidth": 1, "overlay": False})

oscillator_points = []
oscillator_cloud = []
for index, value in enumerate(oscillator):
    if not np.isfinite(value):
        oscillator_points.append(None)
        oscillator_cloud.append(None)
        continue
    color = bullish_color if value >= 0.0 else bearish_color
    oscillator_points.append({"value": float(value), "color": color})
    oscillator_cloud.append({"upper": float(max(value, 0.0)), "lower": float(min(value, 0.0)), "color": rgba(color, 0.22)})
plots.append({"name": "Oscillator Fill", "title": False, "data": oscillator_cloud, "type": "cloud", "overlay": False})
plots.append({"name": "Channel Oscillator", "data": oscillator_points, "color": bullish_color, "type": "line", "lineWidth": 1, "overlay": False})

bull_pane = [
    {"value": float(oscillator[index]), "color": bullish_color, "size": 5}
    if bull_signal[index] and np.isfinite(oscillator[index]) else None for index in range(n)
]
bear_pane = [
    {"value": float(oscillator[index]), "color": bearish_color, "size": 5}
    if bear_signal[index] and np.isfinite(oscillator[index]) else None for index in range(n)
]
plots.extend([
    {"name": "Bull Grid Signal", "title": False, "data": bull_pane, "color": bullish_color, "type": "scatter", "lineWidth": 5, "overlay": False},
    {"name": "Bear Grid Signal", "title": False, "data": bear_pane, "color": bearish_color, "type": "scatter", "lineWidth": 5, "overlay": False},
])

signals = [
    {
        "type": "buy", "name": "Bullish Grid Re-entry", "text": "GRID+", "color": bullish_color, "renderMode": "events",
        "data": [
            {"value": float(low_values[index]), "text": f"+{bull_signal_level[index]}", "color": bullish_color}
            if bull_signal[index] else None for index in range(n)
        ],
    },
    {
        "type": "sell", "name": "Bearish Grid Re-entry", "text": "GRID-", "color": bearish_color, "renderMode": "events",
        "data": [
            {"value": float(high_values[index]), "text": f"-{bear_signal_level[index]}", "color": bearish_color}
            if bear_signal[index] else None for index in range(n)
        ],
    },
]

layers = []
if show_edge_labels and n:
    for level in range(1, grid_levels + 1):
        if np.isfinite(upper_levels[level - 1][-1]):
            layers.append({
                "type": "label", "index": n - 1, "price": float(upper_levels[level - 1][-1]),
                "text": f"+{level}", "color": "rgba(0,0,0,0)", "textColor": bullish_color, "side": "center",
            })
        if np.isfinite(lower_levels[level - 1][-1]):
            layers.append({
                "type": "label", "index": n - 1, "price": float(lower_levels[level - 1][-1]),
                "text": f"-{level}", "color": "rgba(0,0,0,0)", "textColor": bearish_color, "side": "center",
            })

tables = []
if show_dashboard and n and np.isfinite(oscillator[-1]):
    rows = []
    rounded_position = int(round(float(oscillator[-1])))
    for level in range(grid_levels, -grid_levels - 1, -1):
        level_text = f"+{level}" if level > 0 else str(level)
        level_color = bullish_color if level > 0 else bearish_color if level < 0 else "#94A3B8"
        current = rounded_position == level or (level == grid_levels and oscillator[-1] >= grid_levels) or (level == -grid_levels and oscillator[-1] <= -grid_levels)
        marker = f"◄ [{oscillator[-1]:.2f}]" if current else "│"
        marker_color = (bullish_color if oscillator[-1] >= 0.0 else bearish_color) if current else "rgba(148,163,184,0.40)"
        rows.append([
            {"text": level_text, "color": level_color},
            {"text": marker, "color": marker_color, "align": "right"},
        ])
    tables.append({
        "title": "Dynamic Grid", "position": dashboard_position, "compact": True,
        "columns": ["Level", "Scale"], "rows": rows,
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": tables,
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "centerLine": finite(center_line), "hullRaw": finite(hull_raw),
        "fullWma": finite(full_wma), "halfWma": finite(half_wma),
        "trueRange": finite(true_range), "atr": finite(atr), "gridStep": finite(grid_step),
        "proximityDistance": finite(proximity_distance), "oscillator": finite(oscillator),
        "upperLevels": [finite(values) for values in upper_levels],
        "lowerLevels": [finite(values) for values in lower_levels],
        "trendUp": trend_up.tolist(),
        "crossBear": [values.tolist() for values in cross_bear],
        "crossBull": [values.tolist() for values in cross_bull],
        "bearSignal": bear_signal.tolist(), "bullSignal": bull_signal.tolist(),
        "bearSignalLevel": bear_signal_level.tolist(), "bullSignalLevel": bull_signal_level.tolist(),
    },
}
