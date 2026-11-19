# ============================================================
# QuantDinger RSI Divergences
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger RSI Divergences"
my_indicator_description = (
    "Tracks RSI extremes and rolling price anchors to identify bearish and "
    "bullish divergence turns together with confirmed oscillator pivots."
)

# @param rsi_length int 14 RSI length range=1:100:1
# @param overbought float 70.0 Overbought level range=0.0:100.0:1.0
# @param oversold float 30.0 Oversold level range=0.0:100.0:1.0
# @param midline float 50.0 Midline level range=0.0:100.0:1.0
# @param hide_midline bool false Hide midline
# @param hide_pivots bool false Hide pivot markers
# @param short_labels bool false Use compact marker names
# @param hide_labels bool false Replace labels with event background bands
# @param lookback int 90 Divergence lookback range=1:300:1

rsi_length = max(1, int(params.get("rsi_length", 14)))
overbought = min(100.0, max(0.0, float(params.get("overbought", 70.0))))
oversold = min(100.0, max(0.0, float(params.get("oversold", 30.0))))
midline = min(100.0, max(0.0, float(params.get("midline", 50.0))))
hide_midline = bool(params.get("hide_midline", False))
hide_pivots = bool(params.get("hide_pivots", False))
short_labels = bool(params.get("short_labels", False))
hide_labels = bool(params.get("hide_labels", False))
lookback = max(1, int(params.get("lookback", 90)))

df = df.copy()
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def pine_rma(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    seed_values = []
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            if np.isfinite(previous):
                result[index] = previous
            continue
        if not np.isfinite(previous):
            seed_values.append(float(value))
            if len(seed_values) < period:
                continue
            previous = float(np.mean(seed_values[-period:]))
        else:
            previous = (previous * (period - 1) + float(value)) / period
        result[index] = previous
    return result


change = np.full(n, np.nan, dtype=float)
if n > 1:
    change[1:] = close[1:] - close[:-1]
gain = np.where(np.isfinite(change), np.maximum(change, 0.0), np.nan)
loss = np.where(np.isfinite(change), np.maximum(-change, 0.0), np.nan)
average_gain = pine_rma(gain, rsi_length)
average_loss = pine_rma(loss, rsi_length)
rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.isfinite(average_gain[index]) or not np.isfinite(average_loss[index]):
        continue
    if average_loss[index] == 0.0:
        rsi[index] = 100.0
    elif average_gain[index] == 0.0:
        rsi[index] = 0.0
    else:
        strength = average_gain[index] / average_loss[index]
        rsi[index] = 100.0 - 100.0 / (1.0 + strength)

rolling_high_is_current = np.zeros(n, dtype=bool)
rolling_low_is_current = np.zeros(n, dtype=bool)
for index in range(n):
    start = max(0, index - lookback + 1)
    window = rsi[start : index + 1]
    finite = window[np.isfinite(window)]
    if finite.size and np.isfinite(rsi[index]):
        rolling_high_is_current[index] = rsi[index] >= float(np.max(finite))
        rolling_low_is_current[index] = rsi[index] <= float(np.min(finite))

max_price = np.full(n, np.nan, dtype=float)
max_rsi = np.full(n, np.nan, dtype=float)
min_price = np.full(n, np.nan, dtype=float)
min_rsi = np.full(n, np.nan, dtype=float)
pivot_high = np.zeros(n, dtype=bool)
pivot_low = np.zeros(n, dtype=bool)
bearish_divergence = np.zeros(n, dtype=bool)
bullish_divergence = np.zeros(n, dtype=bool)

for index in range(n):
    previous_max_price = max_price[index - 1] if index > 0 else np.nan
    previous_max_rsi = max_rsi[index - 1] if index > 0 else np.nan
    previous_min_price = min_price[index - 1] if index > 0 else np.nan
    previous_min_rsi = min_rsi[index - 1] if index > 0 else np.nan

    max_price[index] = close[index] if rolling_high_is_current[index] or not np.isfinite(previous_max_price) else previous_max_price
    max_rsi[index] = rsi[index] if rolling_high_is_current[index] or not np.isfinite(previous_max_rsi) else previous_max_rsi
    min_price[index] = close[index] if rolling_low_is_current[index] or not np.isfinite(previous_min_price) else previous_min_price
    min_rsi[index] = rsi[index] if rolling_low_is_current[index] or not np.isfinite(previous_min_rsi) else previous_min_rsi

    if close[index] > max_price[index]:
        max_price[index] = close[index]
    if np.isfinite(rsi[index]) and (not np.isfinite(max_rsi[index]) or rsi[index] > max_rsi[index]):
        max_rsi[index] = rsi[index]
    if close[index] < min_price[index]:
        min_price[index] = close[index]
    if np.isfinite(rsi[index]) and (not np.isfinite(min_rsi[index]) or rsi[index] < min_rsi[index]):
        min_rsi[index] = rsi[index]

    if index >= 3 and np.isfinite(max_rsi[index]) and np.isfinite(max_rsi[index - 2]) and np.isfinite(max_rsi[index - 3]):
        pivot_high[index] = max_rsi[index] == max_rsi[index - 2] and max_rsi[index - 2] != max_rsi[index - 3]
    if index >= 3 and np.isfinite(min_rsi[index]) and np.isfinite(min_rsi[index - 2]) and np.isfinite(min_rsi[index - 3]):
        pivot_low[index] = min_rsi[index] == min_rsi[index - 2] and min_rsi[index - 2] != min_rsi[index - 3]

    if index >= 2 and all(np.isfinite(value) for value in (max_price[index - 1], max_price[index - 2], rsi[index - 1], max_rsi[index], rsi[index])):
        bearish_divergence[index] = max_price[index - 1] > max_price[index - 2] and rsi[index - 1] < max_rsi[index] and rsi[index] <= rsi[index - 1]
    if index >= 2 and all(np.isfinite(value) for value in (min_price[index - 1], min_price[index - 2], rsi[index - 1], min_rsi[index], rsi[index])):
        bullish_divergence[index] = min_price[index - 1] < min_price[index - 2] and rsi[index - 1] > min_rsi[index] and rsi[index] >= rsi[index - 1]

rsi_plot = []
for value in rsi:
    if not np.isfinite(value):
        rsi_plot.append(None)
    else:
        color = "#EF4444" if value > overbought or value < oversold else "#9CA3AF"
        rsi_plot.append({"value": float(value), "color": color})

bear_markers = [None for _ in range(n)]
bull_markers = [None for _ in range(n)]
high_pivot_markers = [None for _ in range(n)]
low_pivot_markers = [None for _ in range(n)]
event_background = [None for _ in range(n)]

for index in range(n):
    if bearish_divergence[index] and index >= 1:
        target = index - 1
        if not hide_labels and np.isfinite(rsi[target]):
            bear_markers[target] = float(rsi[target] + (0.4 if short_labels else 1.0))
        elif hide_labels:
            event_background[target] = {"upper": 100.0, "lower": 0.0, "color": "#EF4444"}
    if bullish_divergence[index] and index >= 1:
        target = index - 1
        if not hide_labels and np.isfinite(rsi[target]):
            bull_markers[target] = float(rsi[target] - (0.4 if short_labels else 1.0))
        elif hide_labels:
            event_background[target] = {"upper": 100.0, "lower": 0.0, "color": "#22C55E"}
    if pivot_high[index] and index >= 2 and not hide_pivots:
        target = index - 2
        if not hide_labels and np.isfinite(max_rsi[index]):
            high_pivot_markers[target] = float(max_rsi[index] + (0.4 if short_labels else 1.0))
        elif hide_labels:
            event_background[target] = {"upper": 100.0, "lower": 0.0, "color": "#3B82F6"}
    if pivot_low[index] and index >= 2 and not hide_pivots:
        target = index - 2
        if not hide_labels and np.isfinite(min_rsi[index]):
            low_pivot_markers[target] = float(min_rsi[index] - (0.4 if short_labels else 1.0))
        elif hide_labels:
            event_background[target] = {"upper": 100.0, "lower": 0.0, "color": "#3B82F6"}

upper_zone = [{"upper": float(overbought), "lower": float(midline), "color": "#22C55E"} for _ in range(n)]
lower_zone = [{"upper": float(midline), "lower": float(oversold), "color": "#EF4444"} for _ in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "RSI", "data": rsi_plot, "color": "#9CA3AF", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Upper Zone", "title": False, "data": upper_zone, "color": "#22C55E", "type": "cloud", "opacity": 0.06, "overlay": False},
        {"name": "Lower Zone", "title": False, "data": lower_zone, "color": "#EF4444", "type": "cloud", "opacity": 0.06, "overlay": False},
        {"name": "Overbought", "data": [float(overbought) for _ in range(n)], "color": "rgba(148, 163, 184, 0.55)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Oversold", "data": [float(oversold) for _ in range(n)], "color": "rgba(148, 163, 184, 0.55)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Midline", "data": [None if hide_midline else float(midline) for _ in range(n)], "color": "rgba(148, 163, 184, 0.35)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Bearish Divergence", "data": bear_markers, "color": "#EF4444", "type": "column", "lineWidth": 4, "overlay": False},
        {"name": "Bullish Divergence", "data": bull_markers, "color": "#22C55E", "type": "column", "lineWidth": 4, "overlay": False},
        {"name": "Pivot High", "data": high_pivot_markers, "color": "#3B82F6", "type": "column", "lineWidth": 3, "overlay": False},
        {"name": "Pivot Low", "data": low_pivot_markers, "color": "#3B82F6", "type": "column", "lineWidth": 3, "overlay": False},
        {"name": "Event Background", "title": False, "data": event_background, "color": "#3B82F6", "type": "cloud", "opacity": 0.50, "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
