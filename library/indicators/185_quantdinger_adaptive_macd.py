# ============================================================
# QuantDinger Adaptive MACD
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Adaptive MACD"
my_indicator_description = (
    "Uses rolling price-to-time fit to adapt a two-speed recursive MACD, then displays its "
    "histogram, adaptive line, signal line, and optional crossover markers."
)

# @param r2_period int 20 Trend-fit period range=2:500:1
# @param fast_length int 10 Fast response length range=2:500:1
# @param slow_length int 20 Slow response length range=2:500:1
# @param signal_length int 9 Signal smoothing length range=2:500:1
# @param rising_positive_color str #5B9CF6 Rising positive histogram color
# @param falling_positive_color str #8DBAF8 Falling positive histogram color
# @param falling_negative_color str #F77C80 Falling negative histogram color
# @param rising_negative_color str #FBAEB1 Rising negative histogram color
# @param macd_color str #E5E7EB Adaptive MACD line color
# @param signal_color str #FF5D00 Signal line color
# @param zero_color str #6B7280 Zero line color
# @param show_cross_signals bool false Show line crossover markers

r2_period = min(500, max(2, int(params.get("r2_period", 20))))
fast_length = min(500, max(2, int(params.get("fast_length", 10))))
slow_length = min(500, max(2, int(params.get("slow_length", 20))))
signal_length = min(500, max(2, int(params.get("signal_length", 9))))
rising_positive_color = str(params.get("rising_positive_color", "#5B9CF6"))
falling_positive_color = str(params.get("falling_positive_color", "#8DBAF8"))
falling_negative_color = str(params.get("falling_negative_color", "#F77C80"))
rising_negative_color = str(params.get("rising_negative_color", "#FBAEB1"))
macd_color = str(params.get("macd_color", "#E5E7EB"))
signal_color = str(params.get("signal_color", "#FF5D00"))
zero_color = str(params.get("zero_color", "#6B7280"))
show_cross_signals = bool(params.get("show_cross_signals", False))

df = df.copy()
close = df["close"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
n = len(df)

def ema(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) == 0:
        return result
    alpha = 2.0 / (length + 1.0)
    state = float(source[0]) if np.isfinite(source[0]) else 0.0
    result[0] = state
    for index in range(1, len(source)):
        value = float(source[index]) if np.isfinite(source[index]) else state
        state = alpha * value + (1.0 - alpha) * state
        result[index] = state
    return result

fit = np.full(n, 0.5, dtype=float)
time_axis = np.arange(n, dtype=float)
for index in range(r2_period - 1, n):
    start = index - r2_period + 1
    price_window = close[start:index + 1]
    time_window = time_axis[start:index + 1]
    price_deviation = float(np.std(price_window, ddof=0))
    if price_deviation <= 1e-12:
        correlation = 0.0
    else:
        correlation = float(np.corrcoef(price_window, time_window)[0, 1])
        if not np.isfinite(correlation):
            correlation = 0.0
    fit[index] = 0.5 * correlation * correlation + 0.5

fast_alpha = 2.0 / (fast_length + 1.0)
slow_alpha = 2.0 / (slow_length + 1.0)
adaptive = np.zeros(n, dtype=float)
for index in range(1, n):
    feedback = (
        fit[index] * (1.0 - fast_alpha) * (1.0 - slow_alpha)
        + (1.0 - fit[index]) * ((1.0 - fast_alpha) / max(1e-12, 1.0 - slow_alpha))
    )
    previous = adaptive[index - 1]
    two_bars_back = adaptive[index - 2] if index > 1 else 0.0
    adaptive[index] = (
        (close[index] - close[index - 1]) * (fast_alpha - slow_alpha)
        + (-slow_alpha - fast_alpha + 2.0) * previous
        - feedback * two_bars_back
    )

signal_line = ema(adaptive, signal_length)
histogram = adaptive - signal_line
histogram_colors = []
for index, value in enumerate(histogram):
    previous = histogram[index - 1] if index else value
    if value > 0.0:
        histogram_colors.append(rising_positive_color if value >= previous else falling_positive_color)
    else:
        histogram_colors.append(falling_negative_color if value <= previous else rising_negative_color)

def point_series(values, color=None, colors=None):
    result = []
    for index, value in enumerate(values):
        if not np.isfinite(value):
            result.append(None)
            continue
        point = {"value": float(value)}
        if colors is not None:
            point["color"] = colors[index]
        elif color is not None:
            point["color"] = color
        result.append(point)
    return result

cross_up = np.r_[False, (adaptive[1:] > signal_line[1:]) & (adaptive[:-1] <= signal_line[:-1])]
cross_down = np.r_[False, (adaptive[1:] < signal_line[1:]) & (adaptive[:-1] >= signal_line[:-1])]
signals = []
if show_cross_signals:
    signals = [
        {
            "type": "buy",
            "name": "Adaptive MACD Bull Cross",
            "text": "MACD UP",
            "color": rising_positive_color,
            "renderMode": "events",
            "data": [
                {"value": float(low[index]), "text": "MACD UP", "color": rising_positive_color}
                if cross_up[index] else None
                for index in range(n)
            ],
        },
        {
            "type": "sell",
            "name": "Adaptive MACD Bear Cross",
            "text": "MACD DN",
            "color": falling_negative_color,
            "renderMode": "events",
            "data": [
                {"value": float(high[index]), "text": "MACD DN", "color": falling_negative_color}
                if cross_down[index] else None
                for index in range(n)
            ],
        },
    ]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Histogram",
            "data": point_series(histogram, colors=histogram_colors),
            "color": rising_positive_color,
            "type": "bar",
            "overlay": False,
        },
        {
            "name": "Adaptive MACD",
            "data": point_series(adaptive, macd_color),
            "color": macd_color,
            "type": "line",
            "lineWidth": 2,
            "overlay": False,
        },
        {
            "name": "Signal",
            "data": point_series(signal_line, signal_color),
            "color": signal_color,
            "type": "line",
            "lineWidth": 2,
            "overlay": False,
        },
        {
            "name": "Zero",
            "title": False,
            "data": point_series(np.zeros(n, dtype=float), zero_color),
            "color": zero_color,
            "type": "line",
            "lineWidth": 1,
            "overlay": False,
        },
    ],
    "signals": signals,
    "tables": [],
}
