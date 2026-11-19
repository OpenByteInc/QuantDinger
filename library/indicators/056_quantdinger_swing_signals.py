# ============================================================
# QuantDinger Swing Signals
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Swing Signals"
my_indicator_description = (
    "Fast EMA and long SMA swing entries combined with RSI threshold exits "
    "and a trend-sensitive long-average color."
)

# @param fast_length int 5 Fast EMA period range=1:100:1
# @param slow_length int 50 Slow SMA period range=2:300:1
# @param rsi_length int 14 RSI period range=2:100:1
# @param overbought float 80.0 RSI overbought range=50.0:100.0:1.0
# @param oversold float 20.0 RSI oversold range=0.0:50.0:1.0

fast_length = max(1, int(params.get("fast_length", 5)))
slow_length = max(2, int(params.get("slow_length", 50)))
rsi_length = max(2, int(params.get("rsi_length", 14)))
overbought = float(params.get("overbought", 80.0))
oversold = float(params.get("oversold", 20.0))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def exponential_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


fast_average = exponential_average(close, fast_length)
slow_average = pd.Series(close).rolling(slow_length, min_periods=slow_length).mean().to_numpy(dtype=float)
upward = np.zeros(n, dtype=float)
downward = np.zeros(n, dtype=float)
for index in range(1, n):
    change = close[index] - close[index - 1]
    upward[index] = max(change, 0.0)
    downward[index] = max(-change, 0.0)

average_up = np.full(n, np.nan, dtype=float)
average_down = np.full(n, np.nan, dtype=float)
if n >= rsi_length:
    up_seed = 0.0
    down_seed = 0.0
    for index in range(rsi_length):
        up_seed += upward[index]
        down_seed += downward[index]
    average_up[rsi_length - 1] = up_seed / rsi_length
    average_down[rsi_length - 1] = down_seed / rsi_length
    for index in range(rsi_length, n):
        average_up[index] = (average_up[index - 1] * (rsi_length - 1) + upward[index]) / rsi_length
        average_down[index] = (average_down[index - 1] * (rsi_length - 1) + downward[index]) / rsi_length

rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.isfinite(average_up[index]) or not np.isfinite(average_down[index]):
        continue
    if average_down[index] == 0.0:
        rsi[index] = 100.0 if average_up[index] > 0.0 else 50.0
    else:
        ratio = average_up[index] / average_down[index]
        rsi[index] = 100.0 - 100.0 / (1.0 + ratio)

buy_exit = np.zeros(n, dtype=bool)
sell_exit = np.zeros(n, dtype=bool)
buy_entry = np.zeros(n, dtype=bool)
sell_entry = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(rsi[index]) and np.isfinite(rsi[index - 1]):
        buy_exit[index] = rsi[index] < overbought and rsi[index - 1] >= overbought
        sell_exit[index] = rsi[index] > oversold and rsi[index - 1] <= oversold
    if np.isfinite(slow_average[index]) and np.isfinite(slow_average[index - 1]):
        buy_entry[index] = slow_average[index] < fast_average[index] and slow_average[index - 1] >= fast_average[index - 1] and high[index] > slow_average[index]
        sell_entry[index] = slow_average[index] > fast_average[index] and slow_average[index - 1] <= fast_average[index - 1] and open_price[index] > close[index]

slow_plot = []
for index, value in enumerate(slow_average):
    if not np.isfinite(value):
        slow_plot.append(None)
    else:
        color = "#FACC15" if np.isfinite(rsi[index]) and (rsi[index] >= 85.0 or rsi[index] <= 15.0) else "#84CC16" if low[index] > value else "#EF4444" if high[index] < value else "#FACC15"
        slow_plot.append({"value": float(value), "color": color})

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Long Swing Average", "data": slow_plot, "color": "#FACC15", "type": "line", "lineWidth": 2, "overlay": True},
    ],
    "signals": [
        {"type": "sell", "text": "EXIT", "name": "RSI Bearish Reversal", "data": [float(high[i]) if buy_exit[i] else None for i in range(n)], "color": "#14B8A6", "renderMode": "events"},
        {"type": "buy", "text": "EXIT", "name": "RSI Bullish Reversal", "data": [float(low[i]) if sell_exit[i] else None for i in range(n)], "color": "#14B8A6", "renderMode": "events"},
        {"type": "buy", "text": "B", "name": "Swing Buy", "data": [float(low[i]) if buy_entry[i] else None for i in range(n)], "color": "#22D3EE", "renderMode": "events"},
        {"type": "sell", "text": "S", "name": "Swing Sell", "data": [float(high[i]) if sell_entry[i] else None for i in range(n)], "color": "#EF4444", "renderMode": "events"},
    ],
    "layers": [],
}
