# ============================================================
# QuantDinger Pivot SuperTrend
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Pivot SuperTrend"
my_indicator_description = (
    "A weighted pivot center combined with ATR bands to produce a ratcheting "
    "SuperTrend trail, optional pivot levels, and reversal events."
)

# @param pivot_period int 2 Pivot period range=1:50:1
# @param atr_factor float 3.0 ATR factor range=0.1:20.0:0.1
# @param atr_period int 10 ATR period range=1:200:1
# @param show_pivots bool false Show pivot events
# @param show_labels bool true Show buy and sell events
# @param show_center bool false Show weighted pivot center
# @param show_levels bool false Show support and resistance

pivot_period = max(1, int(params.get("pivot_period", 2)))
atr_factor = max(0.1, float(params.get("atr_factor", 3.0)))
atr_period = max(1, int(params.get("atr_period", 10)))
show_pivots = bool(params.get("show_pivots", False))
show_labels = bool(params.get("show_labels", True))
show_center = bool(params.get("show_center", False))
show_levels = bool(params.get("show_levels", False))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

pivot_high = np.zeros(n, dtype=bool)
pivot_low = np.zeros(n, dtype=bool)
window = pivot_period * 2 + 1
for confirmation in range(window - 1, n):
    pivot = confirmation - pivot_period
    high_window = high[pivot - pivot_period:pivot + pivot_period + 1]
    low_window = low[pivot - pivot_period:pivot + pivot_period + 1]
    pivot_high[pivot] = high[pivot] == np.max(high_window)
    pivot_low[pivot] = low[pivot] == np.min(low_window)

center = np.full(n, np.nan, dtype=float)
support = np.full(n, np.nan, dtype=float)
resistance = np.full(n, np.nan, dtype=float)
current_center = np.nan
current_support = np.nan
current_resistance = np.nan
for index in range(n):
    pivot_value = high[index] if pivot_high[index] else low[index] if pivot_low[index] else np.nan
    if np.isfinite(pivot_value):
        current_center = pivot_value if not np.isfinite(current_center) else (current_center * 2.0 + pivot_value) / 3.0
    if pivot_low[index]:
        current_support = low[index]
    if pivot_high[index]:
        current_resistance = high[index]
    center[index] = current_center
    support[index] = current_support
    resistance[index] = current_resistance

previous_close = np.concatenate(([close[0]], close[:-1]))
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close)))
atr = pd.Series(true_range).ewm(alpha=1.0 / atr_period, adjust=False).mean().to_numpy(dtype=float)
upper_candidate = center - atr_factor * atr
lower_candidate = center + atr_factor * atr
upper_trail = np.full(n, np.nan, dtype=float)
lower_trail = np.full(n, np.nan, dtype=float)
trend = np.ones(n, dtype=int)
for index in range(n):
    if not np.isfinite(center[index]):
        trend[index] = trend[index - 1] if index > 0 else 1
        continue
    if index == 0 or not np.isfinite(upper_trail[index - 1]):
        upper_trail[index] = upper_candidate[index]
        lower_trail[index] = lower_candidate[index]
        trend[index] = 1
        continue
    upper_trail[index] = max(upper_candidate[index], upper_trail[index - 1]) if close[index - 1] > upper_trail[index - 1] else upper_candidate[index]
    lower_trail[index] = min(lower_candidate[index], lower_trail[index - 1]) if close[index - 1] < lower_trail[index - 1] else lower_candidate[index]
    previous_trend = trend[index - 1]
    if close[index] > lower_trail[index - 1]:
        trend[index] = 1
    elif close[index] < upper_trail[index - 1]:
        trend[index] = -1
    else:
        trend[index] = previous_trend

trailing_stop = np.where(trend == 1, upper_trail, lower_trail)
buy_event = np.concatenate(([False], (trend[1:] == 1) & (trend[:-1] == -1)))
sell_event = np.concatenate(([False], (trend[1:] == -1) & (trend[:-1] == 1)))
trail_plot = []
for index, value in enumerate(trailing_stop):
    if np.isfinite(value):
        trail_plot.append({"value": float(value), "color": "#00FF00" if trend[index] == 1 else "#FF0000"})
    else:
        trail_plot.append(None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Pivot SuperTrend", "data": trail_plot, "color": "#00FF00", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Pivot Center", "data": [float(value) if show_center and np.isfinite(value) else None for value in center], "color": "#2962FF", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Pivot Support", "data": [float(value) if show_levels and np.isfinite(value) else None for value in support], "color": "#00FF00", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Pivot Resistance", "data": [float(value) if show_levels and np.isfinite(value) else None for value in resistance], "color": "#FF0000", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "SuperTrend Buy", "data": [float(trailing_stop[i]) if show_labels and buy_event[i] and np.isfinite(trailing_stop[i]) else None for i in range(n)], "color": "#00FF00", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "SuperTrend Sell", "data": [float(trailing_stop[i]) if show_labels and sell_event[i] and np.isfinite(trailing_stop[i]) else None for i in range(n)], "color": "#FF0000", "renderMode": "events"},
        {"type": "sell", "text": "H", "name": "Pivot High", "data": [float(high[i]) if show_pivots and pivot_high[i] else None for i in range(n)], "color": "#FF0000", "renderMode": "events"},
        {"type": "buy", "text": "L", "name": "Pivot Low", "data": [float(low[i]) if show_pivots and pivot_low[i] else None for i in range(n)], "color": "#00FF00", "renderMode": "events"},
    ],
    "layers": [],
}
