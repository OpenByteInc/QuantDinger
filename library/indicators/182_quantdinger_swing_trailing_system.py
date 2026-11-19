# ============================================================
# QuantDinger Swing Trailing System
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Swing Trailing System"
my_indicator_description = (
    "Builds a stateful trailing level from recent swing highs and lows, colors the trail and host candles, "
    "and marks close crossovers as possible buy or sell events."
)

# @param swing_length int 3 Swing lookback range=1:200:1
# @param color_candles bool true Color chart candles
# @param color_background bool false Color chart background

swing_length = min(200, max(1, int(params.get("swing_length", 3))))
color_candles = bool(params.get("color_candles", True))
color_background = bool(params.get("color_background", False))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

rolling_high = np.full(n, np.nan, dtype=float)
rolling_low = np.full(n, np.nan, dtype=float)
for index in range(swing_length - 1, n):
    rolling_high[index] = float(np.max(high[index - swing_length + 1:index + 1]))
    rolling_low[index] = float(np.min(low[index - swing_length + 1:index + 1]))

direction = np.zeros(n, dtype=int)
active_direction = 0
trail = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index > 0 and np.isfinite(rolling_high[index - 1]) and close[index] > rolling_high[index - 1]:
        direction[index] = 1
    elif index > 0 and np.isfinite(rolling_low[index - 1]) and close[index] < rolling_low[index - 1]:
        direction[index] = -1
    if direction[index] != 0:
        active_direction = direction[index]
    if active_direction == 1 and np.isfinite(rolling_low[index]):
        trail[index] = rolling_low[index]
    elif np.isfinite(rolling_high[index]):
        trail[index] = rolling_high[index]

buy = np.zeros(n, dtype=bool)
sell = np.zeros(n, dtype=bool)
for index in range(1, n):
    if not np.isfinite(trail[index]) or not np.isfinite(trail[index - 1]):
        continue
    buy[index] = close[index] > trail[index] and close[index - 1] <= trail[index - 1]
    sell[index] = close[index] < trail[index] and close[index - 1] >= trail[index - 1]

trail_colors = []
bar_colors = []
background_colors = []
for index in range(n):
    if not np.isfinite(trail[index]):
        trail_colors.append(None)
        bar_colors.append(None)
        background_colors.append(None)
        continue
    color = "#22C55E" if close[index] >= trail[index] else "#EF4444"
    trail_colors.append(color)
    bar_colors.append(color if color_candles else None)
    if color_background:
        background_colors.append("rgba(34,197,94,0.10)" if close[index] >= trail[index] else "rgba(239,68,68,0.10)")
    else:
        background_colors.append(None)

trail_points = [
    {"value": float(trail[index]), "color": trail_colors[index]}
    if np.isfinite(trail[index]) else None
    for index in range(n)
]

def event_points(mask, values, text, color):
    return [
        {"value": float(values[index]), "text": text, "color": color}
        if mask[index] and np.isfinite(values[index]) else None
        for index in range(n)
    ]

signals = [
    {"type": "buy", "name": "Swing Buy", "text": "BUY", "color": "#22C55E", "data": event_points(buy, low, "BUY", "#22C55E"), "renderMode": "events"},
    {"type": "sell", "name": "Swing Sell", "text": "SELL", "color": "#EF4444", "data": event_points(sell, high, "SELL", "#EF4444"), "renderMode": "events"},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "description": my_indicator_description,
    "plots": [
        {"name": "Swing Trail", "data": trail_points, "color": "#22C55E", "type": "line", "lineWidth": 3, "overlay": True},
    ],
    "signals": signals,
    "bar_colors": bar_colors,
    "background_colors": background_colors,
    "calculatedVars": {
        "direction": int(active_direction),
        "trail": float(trail[-1]) if n and np.isfinite(trail[-1]) else None,
    },
}
