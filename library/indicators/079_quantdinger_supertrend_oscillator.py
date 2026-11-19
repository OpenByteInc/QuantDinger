# ============================================================
# QuantDinger SuperTrend Oscillator
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger SuperTrend Oscillator"
my_indicator_description = (
    "A normalized SuperTrend distance oscillator with adaptive smoothing, "
    "strength-sensitive color opacity, crossover quality labels, and a final connector."
)

# @param length int 10 SuperTrend period range=1:300:1
# @param multiplier float 2.0 ATR multiplier range=1.0:20.0:0.1
# @param smoothing int 72 Histogram smoothing range=1:300:1
# @param fixed_opacity bool false Use fixed oscillator opacity
# @param show_connector bool true Show latest crossover connector
# @param show_labels bool true Show crossover quality labels
# @param show_false_rate bool false Show cumulative false-signal rate

length = max(1, int(params.get("length", 10)))
multiplier = max(1.0, float(params.get("multiplier", 2.0)))
smoothing = max(1, int(params.get("smoothing", 72)))
fixed_opacity = bool(params.get("fixed_opacity", False))
show_connector = bool(params.get("show_connector", True))
show_labels = bool(params.get("show_labels", True))
show_false_rate = bool(params.get("show_false_rate", False))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def wilder_average(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < period:
        return result
    previous = float(np.mean(source[:period]))
    result[period - 1] = previous
    for index in range(period, len(source)):
        previous = (previous * (period - 1) + source[index]) / period
        result[index] = previous
    return result


def pine_ema(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    previous = np.nan
    alpha = 2.0 / (period + 1.0)
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


previous_close = np.concatenate(([close[0]], close[:-1])) if n else np.array([], dtype=float)
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close)))
atr = wilder_average(true_range, length) * multiplier
midpoint = (high + low) / 2.0
raw_upper = midpoint + atr
raw_lower = midpoint - atr
upper = np.full(n, np.nan, dtype=float)
lower = np.full(n, np.nan, dtype=float)
trend = np.zeros(n, dtype=int)
supertrend = np.full(n, np.nan, dtype=float)

for index in range(n):
    if not np.isfinite(atr[index]):
        continue
    if index == 0 or not np.isfinite(upper[index - 1]):
        upper[index] = raw_upper[index]
        lower[index] = raw_lower[index]
        trend[index] = 0
    else:
        upper[index] = min(raw_upper[index], upper[index - 1]) if close[index - 1] < upper[index - 1] else raw_upper[index]
        lower[index] = max(raw_lower[index], lower[index - 1]) if close[index - 1] > lower[index - 1] else raw_lower[index]
        if close[index] > upper[index - 1]:
            trend[index] = 1
        elif close[index] < lower[index - 1]:
            trend[index] = 0
        else:
            trend[index] = trend[index - 1]
    supertrend[index] = lower[index] if trend[index] == 1 else upper[index]

oscillator = np.full(n, np.nan, dtype=float)
adaptive = np.full(n, np.nan, dtype=float)
for index in range(n):
    denominator = upper[index] - lower[index]
    if not np.isfinite(supertrend[index]) or not np.isfinite(denominator) or denominator == 0.0:
        continue
    oscillator[index] = max(-1.0, min(1.0, (close[index] - supertrend[index]) / denominator))
    alpha = oscillator[index] * oscillator[index] / length
    previous = adaptive[index - 1] if index > 0 else np.nan
    adaptive[index] = oscillator[index] if not np.isfinite(previous) else previous + alpha * (oscillator[index] - previous)

histogram = pine_ema(oscillator - adaptive, smoothing)
cross_up = np.zeros(n, dtype=bool)
cross_down = np.zeros(n, dtype=bool)
confirmed_buy = np.zeros(n, dtype=bool)
false_buy = np.zeros(n, dtype=bool)
confirmed_sell = np.zeros(n, dtype=bool)
false_sell = np.zeros(n, dtype=bool)
false_rate = np.zeros(n, dtype=float)
last_cross_bar = -1
last_crossover_price = np.nan
last_crossunder_price = np.nan
cross_count = 0
false_count = 0

for index in range(1, n):
    if not np.isfinite(supertrend[index]) or not np.isfinite(supertrend[index - 1]):
        false_rate[index] = false_rate[index - 1]
        continue
    cross_up[index] = close[index] > supertrend[index] and close[index - 1] <= supertrend[index - 1]
    cross_down[index] = close[index] < supertrend[index] and close[index - 1] >= supertrend[index - 1]
    if cross_up[index]:
        last_crossover_price = close[index]
        is_false = np.isfinite(last_crossunder_price) and last_crossover_price > last_crossunder_price
        false_sell[index] = is_false
        confirmed_buy[index] = not is_false
        last_cross_bar = index
    elif cross_down[index]:
        last_crossunder_price = close[index]
        is_false = np.isfinite(last_crossover_price) and last_crossunder_price < last_crossover_price
        false_buy[index] = is_false
        confirmed_sell[index] = not is_false
        last_cross_bar = index
    if cross_up[index] or cross_down[index]:
        cross_count += 1
        if false_buy[index] or false_sell[index]:
            false_count += 1
    false_rate[index] = 100.0 * false_count / cross_count if cross_count else 0.0

main_columns = []
fixed_area = []
histogram_area = []
signal_plot = []
for index in range(n):
    if not np.isfinite(oscillator[index]):
        main_columns.append(None)
        fixed_area.append(None)
        histogram_area.append(None)
        signal_plot.append(None)
        continue
    strength = min(0.99, abs(oscillator[index]) * 0.99)
    main_color = (
        "rgba(12, 181, 26, %.3f)" % strength
        if oscillator[index] > 0.0
        else "rgba(255, 17, 0, %.3f)" % strength
    )
    main_columns.append(None if fixed_opacity else {"value": float(oscillator[index] * 100.0), "color": main_color})
    fixed_area.append(
        None
        if not fixed_opacity
        else {
            "upper": float(max(oscillator[index] * 100.0, 0.0)),
            "lower": float(min(oscillator[index] * 100.0, 0.0)),
            "color": "#0CB51A" if oscillator[index] > 0.0 else "#FF1100",
        }
    )
    histogram_value = histogram[index] * 100.0 if np.isfinite(histogram[index]) else np.nan
    histogram_area.append(
        {
            "upper": float(max(histogram_value, 0.0)),
            "lower": float(min(histogram_value, 0.0)),
            "color": "#808080",
        }
        if np.isfinite(histogram_value)
        else None
    )
    signal_plot.append(
        {
            "value": float(adaptive[index] * 100.0),
            "color": "#2157F3" if adaptive[index] > 0.0 else "#673AB7",
        }
        if np.isfinite(adaptive[index])
        else None
    )

connector = [None for _ in range(n)]
if show_connector and last_cross_bar >= 0 and n > last_cross_bar and np.isfinite(oscillator[-1]):
    direction = 1.0 if oscillator[-1] >= 0.0 else -1.0
    start_value = -direction * 100.0
    end_value = float(oscillator[-1] * 100.0)
    span = max(1, n - 1 - last_cross_bar)
    connector_color = "#0CB51A" if oscillator[-1] >= 0.0 else "#FF1100"
    for index in range(last_cross_bar, n):
        ratio = (index - last_cross_bar) / span
        connector[index] = {"value": float(start_value + ratio * (end_value - start_value)), "color": connector_color}

zone_cloud = [{"upper": 80.0, "lower": -80.0, "color": "#2157F3"} for _ in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Strength Oscillator", "data": main_columns, "color": "#0CB51A", "type": "column", "lineWidth": 1, "overlay": False},
        {"name": "Fixed Oscillator Area", "title": False, "data": fixed_area, "color": "#0CB51A", "type": "cloud", "opacity": 0.85, "overlay": False},
        {"name": "Momentum Histogram", "title": False, "data": histogram_area, "color": "#808080", "type": "cloud", "opacity": 0.60, "overlay": False},
        {"name": "Adaptive Signal", "data": signal_plot, "color": "#2157F3", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Oscillator Zone", "title": False, "data": zone_cloud, "color": "#2157F3", "type": "cloud", "opacity": 0.10, "overlay": False},
        {"name": "Latest Cross Connector", "data": connector, "color": "#0CB51A", "type": "line", "lineWidth": 2, "overlay": False},
        {
            "name": "False Signal Rate",
            "data": [float(value) if show_false_rate else None for value in false_rate],
            "color": "#9CA3AF",
            "type": "line",
            "lineWidth": 1,
            "overlay": False,
        },
        {
            "name": "Confirmed Buy Events",
            "data": [-80.0 if show_labels and confirmed_buy[index] else None for index in range(n)],
            "color": "#0CB51A",
            "type": "column",
            "lineWidth": 3,
            "overlay": False,
        },
        {
            "name": "False Buy Events",
            "data": [-80.0 if show_labels and false_buy[index] else None for index in range(n)],
            "color": "#808080",
            "type": "column",
            "lineWidth": 3,
            "overlay": False,
        },
        {
            "name": "Confirmed Sell Events",
            "data": [80.0 if show_labels and confirmed_sell[index] else None for index in range(n)],
            "color": "#FF1100",
            "type": "column",
            "lineWidth": 3,
            "overlay": False,
        },
        {
            "name": "False Sell Events",
            "data": [80.0 if show_labels and false_sell[index] else None for index in range(n)],
            "color": "#808080",
            "type": "column",
            "lineWidth": 3,
            "overlay": False,
        },
    ],
    "signals": [],
    "layers": [],
}
