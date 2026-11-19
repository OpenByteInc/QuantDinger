# ============================================================
# QuantDinger Wick-Aware SuperTrend
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Wick-Aware SuperTrend"
my_indicator_description = (
    "A SuperTrend stop that can use candle wicks for reversals, with separate "
    "long and short trails, state highlighting, and reversal events."
)

# @param atr_length int 22 ATR period range=1:200:1
# @param atr_multiplier float 3.0 ATR multiplier range=0.1:20.0:0.1
# @param use_wicks bool true Use candle wicks
# @param show_labels bool true Show buy and sell labels
# @param highlight_state bool true Highlight state

atr_length = max(1, int(params.get("atr_length", 22)))
atr_multiplier = max(0.1, float(params.get("atr_multiplier", 3.0)))
use_wicks = bool(params.get("use_wicks", True))
show_labels = bool(params.get("show_labels", True))
highlight_state = bool(params.get("highlight_state", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

previous_close = np.concatenate(([close[0]], close[:-1]))
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close)))
atr = np.full(n, np.nan, dtype=float)
if n >= atr_length:
    seed = 0.0
    for index in range(atr_length):
        seed += true_range[index]
    atr[atr_length - 1] = seed / atr_length
    for index in range(atr_length, n):
        atr[index] = (atr[index - 1] * (atr_length - 1) + true_range[index]) / atr_length

source = (high + low) / 2.0
high_price = high if use_wicks else close
low_price = low if use_wicks else close
long_stop = np.full(n, np.nan, dtype=float)
short_stop = np.full(n, np.nan, dtype=float)
direction = np.ones(n, dtype=int)
for index in range(n):
    if not np.isfinite(atr[index]):
        direction[index] = direction[index - 1] if index > 0 else 1
        continue
    raw_long = source[index] - atr_multiplier * atr[index]
    raw_short = source[index] + atr_multiplier * atr[index]
    if index == 0 or not np.isfinite(long_stop[index - 1]):
        long_stop[index] = raw_long
        short_stop[index] = raw_short
        continue
    doji = open_price[index] == close[index] and open_price[index] == low[index] and open_price[index] == high[index]
    previous_long = long_stop[index - 1]
    previous_short = short_stop[index - 1]
    if doji:
        long_stop[index] = previous_long
        short_stop[index] = previous_short
    else:
        long_stop[index] = max(raw_long, previous_long) if low_price[index - 1] > previous_long and raw_long > 0.0 else raw_long if raw_long > 0.0 else previous_long
        short_stop[index] = min(raw_short, previous_short) if high_price[index - 1] < previous_short and raw_short > 0.0 else raw_short if raw_short > 0.0 else previous_short
    previous_direction = direction[index - 1]
    direction[index] = 1 if previous_direction == -1 and high_price[index] > previous_short else -1 if previous_direction == 1 and low_price[index] < previous_long else previous_direction

buy_event = np.concatenate(([False], (direction[1:] == 1) & (direction[:-1] == -1)))
sell_event = np.concatenate(([False], (direction[1:] == -1) & (direction[:-1] == 1)))
long_plot = []
short_plot = []
state_cloud = []
mid_price = (open_price + high + low + close) / 4.0
for index in range(n):
    long_plot.append(float(long_stop[index]) if direction[index] == 1 and np.isfinite(long_stop[index]) else None)
    short_plot.append(float(short_stop[index]) if direction[index] == -1 and np.isfinite(short_stop[index]) else None)
    active_stop = long_stop[index] if direction[index] == 1 else short_stop[index]
    if highlight_state and np.isfinite(active_stop):
        state_cloud.append({"upper": float(max(mid_price[index], active_stop)), "lower": float(min(mid_price[index], active_stop)), "color": "#00C853" if direction[index] == 1 else "#EF4444"})
    else:
        state_cloud.append(None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Long Stop", "data": long_plot, "color": "#00C853", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Short Stop", "data": short_plot, "color": "#EF4444", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "SuperTrend State", "title": False, "data": state_cloud, "color": "#00C853", "type": "cloud", "opacity": 0.15, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "SuperTrend Buy", "data": [float(long_stop[i]) if show_labels and buy_event[i] and np.isfinite(long_stop[i]) else None for i in range(n)], "color": "#00C853", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "SuperTrend Sell", "data": [float(short_stop[i]) if show_labels and sell_event[i] and np.isfinite(short_stop[i]) else None for i in range(n)], "color": "#EF4444", "renderMode": "events"},
    ],
    "layers": [],
}
