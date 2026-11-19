# ============================================================
# QuantDinger SwingArm Fibonacci Trail
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger SwingArm Fibonacci Trail"
my_indicator_description = (
    "Builds a Wilder ATR trend trail, tracks the active trend extremum, and "
    "marks price crosses through three Fibonacci retracement zones."
)

# @param trail_type str modified True-range model values=modified,unmodified
# @param atr_period int 28 ATR period range=1:200:1
# @param atr_factor float 5.0 ATR factor range=0.1:20.0:0.1
# @param show_fib_entries bool true Show Fibonacci entry markers

trail_type = str(params.get("trail_type", "modified")).lower()
atr_period = min(200, max(1, int(params.get("atr_period", 28))))
atr_factor = min(20.0, max(0.1, float(params.get("atr_factor", 5.0))))
show_fib_entries = bool(params.get("show_fib_entries", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

range_values = high - low
range_average = pd.Series(range_values, dtype=float).rolling(atr_period, min_periods=1).mean().to_numpy(dtype=float)
true_range = np.zeros(n, dtype=float)
if n > 0:
    true_range[0] = range_values[0]
for index in range(1, n):
    if trail_type == "modified":
        high_low = min(range_values[index], 1.5 * range_average[index])
        high_reference = (
            high[index] - close[index - 1]
            if low[index] <= high[index - 1]
            else (high[index] - close[index - 1]) - 0.5 * (low[index] - high[index - 1])
        )
        low_reference = (
            close[index - 1] - low[index]
            if high[index] >= low[index - 1]
            else (close[index - 1] - low[index]) - 0.5 * (low[index - 1] - high[index])
        )
        true_range[index] = max(high_low, high_reference, low_reference)
    else:
        true_range[index] = max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )

wilder_range = np.zeros(n, dtype=float)
for index in range(n):
    previous = wilder_range[index - 1] if index > 0 else 0.0
    wilder_range[index] = previous + (true_range[index] - previous) / float(atr_period)

loss = atr_factor * wilder_range
up = close - loss
down = close + loss
trend_up = np.zeros(n, dtype=float)
trend_down = np.zeros(n, dtype=float)
trend = np.ones(n, dtype=int)
trail = np.zeros(n, dtype=float)
extremum = np.zeros(n, dtype=float)

if n > 0:
    trend_up[0] = up[0]
    trend_down[0] = down[0]
    trail[0] = trend_up[0]
    extremum[0] = high[0]

for index in range(1, n):
    trend_up[index] = max(up[index], trend_up[index - 1]) if close[index - 1] > trend_up[index - 1] else up[index]
    trend_down[index] = min(down[index], trend_down[index - 1]) if close[index - 1] < trend_down[index - 1] else down[index]

    if close[index] > trend_down[index - 1]:
        trend[index] = 1
    elif close[index] < trend_up[index - 1]:
        trend[index] = -1
    else:
        trend[index] = trend[index - 1]

    trail[index] = trend_up[index] if trend[index] == 1 else trend_down[index]
    if trend[index] > 0 and trend[index - 1] <= 0:
        extremum[index] = high[index]
    elif trend[index] < 0 and trend[index - 1] >= 0:
        extremum[index] = low[index]
    elif trend[index] == 1:
        extremum[index] = max(extremum[index - 1], high[index])
    else:
        extremum[index] = min(extremum[index - 1], low[index])

fib_1 = extremum + (trail - extremum) * 0.618
fib_2 = extremum + (trail - extremum) * 0.786
fib_3 = extremum + (trail - extremum) * 0.886

atr_14 = pd.Series(true_range, dtype=float).rolling(14, min_periods=1).mean().to_numpy(dtype=float)
long_crosses = [np.zeros(n, dtype=bool) for _ in range(3)]
short_crosses = [np.zeros(n, dtype=bool) for _ in range(3)]
fib_levels = (fib_1, fib_2, fib_3)
for index in range(2, n):
    for level_index, level in enumerate(fib_levels):
        long_crosses[level_index][index] = (
            trend[index - 1] == 1
            and close[index] < level[index - 1]
            and close[index - 1] >= level[index - 2]
        )
        short_crosses[level_index][index] = (
            trend[index - 1] == -1
            and close[index] > level[index - 1]
            and close[index - 1] <= level[index - 2]
        )

def colored_line(values, bullish_color, bearish_color):
    return [
        {
            "value": float(values[index]),
            "color": bullish_color if trend[index] == 1 else bearish_color,
        }
        for index in range(n)
    ]

plots = [
    {"name": "Trailing Stop", "data": colored_line(trail, "#008000", "#FF0000"), "color": "#008000", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Trend Extremum", "data": colored_line(extremum, "#00FF00", "#FF00FF"), "color": "#00FF00", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Fib 61.8", "data": colored_line(fib_1, "#22C55E", "#EF4444"), "color": "#22C55E", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Fib 78.6", "data": colored_line(fib_2, "#84CC16", "#F97316"), "color": "#84CC16", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Fib 88.6", "data": colored_line(fib_3, "#EAB308", "#EC4899"), "color": "#EAB308", "type": "line", "lineWidth": 1, "overlay": True},
]

signals = []
for level_index, multiplier in enumerate((1.0, 1.5, 2.0), start=1):
    long_data = [None for _ in range(n)]
    short_data = [None for _ in range(n)]
    if show_fib_entries:
        for index in range(n):
            if long_crosses[level_index - 1][index]:
                long_data[index] = {"value": float(low[index] - multiplier * atr_14[index]), "text": f"L{level_index}", "color": "#FACC15"}
            if short_crosses[level_index - 1][index]:
                short_data[index] = {"value": float(high[index] + multiplier * atr_14[index]), "text": f"S{level_index}", "color": "#A855F7"}
    signals.append({"type": "buy", "text": f"L{level_index}", "name": f"Long Fib {level_index}", "data": long_data, "color": "#FACC15", "renderMode": "events"})
    signals.append({"type": "sell", "text": f"S{level_index}", "name": f"Short Fib {level_index}", "data": short_data, "color": "#A855F7", "renderMode": "events"})

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": []}
