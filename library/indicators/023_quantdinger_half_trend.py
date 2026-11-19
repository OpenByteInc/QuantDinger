# ============================================================
# QuantDinger Half Trend
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Half Trend"
my_indicator_description = (
    "A stateful swing trend line with ATR channels and reversal markers."
)

# @param amplitude int 2 Swing amplitude range=1:100:1
# @param channel_deviation float 2.0 Channel deviation range=0.0:20.0:0.1
# @param show_arrows bool true Show reversal arrows
# @param show_channels bool true Show ATR channels
# @param show_labels bool true Show reversal labels

amplitude = max(1, int(params.get("amplitude", 2)))
channel_deviation = max(0.0, float(params.get("channel_deviation", 2.0)))
show_arrows = bool(params.get("show_arrows", True))
show_channels = bool(params.get("show_channels", True))
show_labels = bool(params.get("show_labels", True))

df = df.copy()
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
n = len(df)

previous_close = close.shift(1)
true_range = pd.concat(
    [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
).max(axis=1, skipna=True)
tr_values = true_range.to_numpy(dtype=float)
atr = np.full(n, np.nan, dtype=float)
atr_period = 100
if n >= atr_period:
    atr[atr_period - 1] = float(np.mean(tr_values[:atr_period]))
    for index in range(atr_period, n):
        atr[index] = (atr[index - 1] * (atr_period - 1) + tr_values[index]) / atr_period
atr2 = atr / 2.0

rolling_high = high.rolling(window=amplitude, min_periods=1).max().to_numpy(dtype=float)
rolling_low = low.rolling(window=amplitude, min_periods=1).min().to_numpy(dtype=float)
high_ma = high.rolling(window=amplitude, min_periods=amplitude).mean().to_numpy(dtype=float)
low_ma = low.rolling(window=amplitude, min_periods=amplitude).mean().to_numpy(dtype=float)
high_values = high.to_numpy(dtype=float)
low_values = low.to_numpy(dtype=float)
close_values = close.to_numpy(dtype=float)

trend_values = np.zeros(n, dtype=int)
half_trend = np.full(n, np.nan, dtype=float)
atr_high = np.full(n, np.nan, dtype=float)
atr_low = np.full(n, np.nan, dtype=float)
buy_event = np.zeros(n, dtype=bool)
sell_event = np.zeros(n, dtype=bool)

trend = 0
next_trend = 0
max_low_price = low_values[0] if n else 0.0
min_high_price = high_values[0] if n else 0.0
up_value = 0.0
down_value = 0.0

for index in range(n):
    previous_trend = trend_values[index - 1] if index > 0 else trend
    previous_low = low_values[index - 1] if index > 0 else low_values[index]
    previous_high = high_values[index - 1] if index > 0 else high_values[index]
    if next_trend == 1:
        max_low_price = max(rolling_low[index], max_low_price)
        if np.isfinite(high_ma[index]) and high_ma[index] < max_low_price and close_values[index] < previous_low:
            trend = 1
            next_trend = 0
            min_high_price = rolling_high[index]
    else:
        min_high_price = min(rolling_high[index], min_high_price)
        if np.isfinite(low_ma[index]) and low_ma[index] > min_high_price and close_values[index] > previous_high:
            trend = 0
            next_trend = 1
            max_low_price = rolling_low[index]

    if trend == 0:
        if index > 0 and previous_trend != 0:
            up_value = down_value
            buy_event[index] = True
        else:
            up_value = max(max_low_price, up_value)
        current = up_value
    else:
        if index > 0 and previous_trend != 1:
            down_value = up_value
            sell_event[index] = True
        else:
            down_value = min(min_high_price, down_value) if down_value != 0.0 else min_high_price
        current = down_value

    trend_values[index] = trend
    half_trend[index] = current
    if np.isfinite(atr2[index]):
        deviation = channel_deviation * atr2[index]
        atr_high[index] = current + deviation
        atr_low[index] = current - deviation

line_plot = [
    {"value": float(half_trend[index]), "color": "#0000FF" if trend_values[index] == 0 else "#FF0000"}
    if np.isfinite(half_trend[index])
    else None
    for index in range(n)
]
high_plot = [float(value) if show_channels and np.isfinite(value) else None for value in atr_high]
low_plot = [float(value) if show_channels and np.isfinite(value) else None for value in atr_low]
upper_cloud = []
lower_cloud = []
for index in range(n):
    if high_plot[index] is None or low_plot[index] is None or not np.isfinite(half_trend[index]):
        upper_cloud.append(None)
        lower_cloud.append(None)
    else:
        upper_cloud.append({"upper": high_plot[index], "lower": float(half_trend[index])})
        lower_cloud.append({"upper": float(half_trend[index]), "lower": low_plot[index]})

buy_marks = [float(atr_low[index]) if buy_event[index] and np.isfinite(atr_low[index]) else None for index in range(n)]
sell_marks = [float(atr_high[index]) if sell_event[index] and np.isfinite(atr_high[index]) else None for index in range(n)]
buy_arrows = [value if show_arrows else None for value in buy_marks]
sell_arrows = [value if show_arrows else None for value in sell_marks]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Half Trend", "data": line_plot, "color": "#0000FF", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "ATR High", "data": high_plot, "color": "#FF0000", "type": "scatter", "size": 2, "overlay": True},
        {"name": "ATR Low", "data": low_plot, "color": "#0000FF", "type": "scatter", "size": 2, "overlay": True},
        {"name": "Upper Channel", "title": False, "data": upper_cloud, "color": "#FF0000", "type": "cloud", "opacity": 0.15, "overlay": True},
        {"name": "Lower Channel", "title": False, "data": lower_cloud, "color": "#0000FF", "type": "cloud", "opacity": 0.15, "overlay": True},
        {"name": "Buy Arrow", "title": False, "data": buy_arrows, "color": "#0000FF", "type": "scatter", "size": 4, "overlay": True},
        {"name": "Sell Arrow", "title": False, "data": sell_arrows, "color": "#FF0000", "type": "scatter", "size": 4, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "Buy", "name": "Half Trend Buy", "data": buy_marks, "color": "#0000FF", "renderMode": "events" if show_labels else "hidden"},
        {"type": "sell", "text": "Sell", "name": "Half Trend Sell", "data": sell_marks, "color": "#FF0000", "renderMode": "events" if show_labels else "hidden"},
    ],
    "layers": [],
    "tables": [],
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "trueRange": [float(value) if np.isfinite(value) else None for value in tr_values],
        "atr": [float(value) if np.isfinite(value) else None for value in atr],
        "atrHalf": [float(value) if np.isfinite(value) else None for value in atr2],
        "rollingHigh": [float(value) if np.isfinite(value) else None for value in rolling_high],
        "rollingLow": [float(value) if np.isfinite(value) else None for value in rolling_low],
        "highAverage": [float(value) if np.isfinite(value) else None for value in high_ma],
        "lowAverage": [float(value) if np.isfinite(value) else None for value in low_ma],
        "trend": trend_values.tolist(),
        "halfTrend": [float(value) if np.isfinite(value) else None for value in half_trend],
        "atrHigh": [float(value) if np.isfinite(value) else None for value in atr_high],
        "atrLow": [float(value) if np.isfinite(value) else None for value in atr_low],
        "buySignal": buy_event.tolist(), "sellSignal": sell_event.tolist(),
    },
}
