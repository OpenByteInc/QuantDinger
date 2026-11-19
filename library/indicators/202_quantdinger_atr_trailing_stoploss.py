# ============================================================
# QuantDinger ATR Trailing Stoploss
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger ATR Trailing Stoploss"
my_indicator_description = (
    "Tracks the rolling highest ATR-offset high after a short initialization period, colors the "
    "trail and host candles by price position, and emits close/trail crossover events."
)

# @param atr_period int 5 ATR period range=1:500:1
# @param highest_period int 10 Rolling highest period range=1:500:1
# @param multiplier float 2.5 ATR multiplier range=0.1:20.0:0.1
# @param color_candles bool true Color candles by trail state
# @param signal_cooldown_bars int 8 Minimum bars between displayed signals range=0:100:1

atr_period = min(500, max(1, int(params.get("atr_period", 5))))
highest_period = min(500, max(1, int(params.get("highest_period", 10))))
multiplier = min(20.0, max(0.1, float(params.get("multiplier", 2.5))))
color_candles = bool(params.get("color_candles", True))
signal_cooldown_bars = min(100, max(0, int(params.get("signal_cooldown_bars", 8))))

bullish_color = "#00C853"
bearish_color = "#F44336"
neutral_color = "#212121"

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def wilder_average(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < length:
        return result
    result[length - 1] = float(np.mean(source[:length]))
    for index in range(length, len(source)):
        result[index] = (result[index - 1] * (length - 1) + source[index]) / length
    return result


def rolling_highest(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(length - 1, len(source)):
        window = source[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.max(window))
    return result


true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )

atr = wilder_average(true_range, atr_period)
highest_offset = rolling_highest(high - multiplier * atr, highest_period)
trail = np.full(n, np.nan, dtype=float)
for index in range(n):
    trail[index] = close[index] if index + 1 < 16 else highest_offset[index]

trail_colors = []
bar_colors = []
for index in range(n):
    if not np.isfinite(trail[index]):
        trail_colors.append(neutral_color)
        bar_colors.append(None)
    elif close[index] > trail[index]:
        trail_colors.append(bullish_color)
        bar_colors.append(bullish_color if color_candles else None)
    elif close[index] < trail[index]:
        trail_colors.append(bearish_color)
        bar_colors.append(bearish_color if color_candles else None)
    else:
        trail_colors.append(neutral_color)
        bar_colors.append(neutral_color if color_candles else None)

buy = np.zeros(n, dtype=bool)
sell = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(trail[index]) and np.isfinite(trail[index - 1]):
        buy[index] = close[index] > trail[index] and close[index - 1] <= trail[index - 1]
        sell[index] = close[index] < trail[index] and close[index - 1] >= trail[index - 1]

display_buy = np.zeros(n, dtype=bool)
display_sell = np.zeros(n, dtype=bool)
last_displayed_signal = -signal_cooldown_bars - 1
for index in range(n):
    if (buy[index] or sell[index]) and index - last_displayed_signal >= signal_cooldown_bars:
        display_buy[index] = buy[index]
        display_sell[index] = sell[index]
        last_displayed_signal = index

trail_points = [
    {"value": float(trail[index]), "color": trail_colors[index]} if np.isfinite(trail[index]) else None
    for index in range(n)
]
buy_values = [float(low[index]) if display_buy[index] else None for index in range(n)]
sell_values = [float(high[index]) if display_sell[index] else None for index in range(n)]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "ATR Trailing Stoploss", "data": trail_points, "color": neutral_color, "type": "line", "lineWidth": 3, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Buy Signal", "text": "BUY", "color": bullish_color, "renderMode": "events", "data": buy_values},
        {"type": "sell", "name": "Sell Signal", "text": "SELL", "color": bearish_color, "renderMode": "events", "data": sell_values},
    ],
    "bar_colors": bar_colors,
}
