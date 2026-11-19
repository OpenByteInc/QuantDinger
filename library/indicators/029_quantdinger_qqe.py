# ============================================================
# QuantDinger QQE
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger QQE"
my_indicator_description = (
    "A smoothed RSI and volatility-adjusted trailing line with trend highlighting and crossing events."
)

# @param rsi_period int 14 RSI period range=1:300:1
# @param smoothing_period int 5 RSI smoothing range=1:100:1
# @param show_signals bool true Show crossing signals
# @param highlight_state bool true Highlight trend state

rsi_period = max(1, int(params.get("rsi_period", 14)))
smoothing_period = max(1, int(params.get("smoothing_period", 5)))
show_signals = bool(params.get("show_signals", True))
highlight_state = bool(params.get("highlight_state", True))

df = df.copy()
close = df["close"].astype(float)
n = len(df)
change = close.diff()
gain = change.clip(lower=0.0)
loss = -change.clip(upper=0.0)
average_gain = gain.ewm(alpha=1.0 / rsi_period, adjust=False, min_periods=rsi_period).mean()
average_loss = loss.ewm(alpha=1.0 / rsi_period, adjust=False, min_periods=rsi_period).mean()
rsi = 100.0 - 100.0 / (1.0 + average_gain / average_loss.replace(0.0, np.nan))
rsi = rsi.where(average_loss != 0.0, 100.0).where(average_gain != 0.0, 0.0)
fast = rsi.ewm(span=smoothing_period, adjust=False, min_periods=smoothing_period).mean().to_numpy(dtype=float)

tr = np.full(n, np.nan, dtype=float)
wwma = np.zeros(n, dtype=float)
atr_rsi = np.zeros(n, dtype=float)
slow = np.zeros(n, dtype=float)
alpha = 1.0 / rsi_period
for index in range(n):
    if index > 0 and np.isfinite(fast[index]) and np.isfinite(fast[index - 1]):
        tr[index] = abs(fast[index] - fast[index - 1])
    current_tr = tr[index] if np.isfinite(tr[index]) else 0.0
    previous_wwma = wwma[index - 1] if index > 0 else 0.0
    previous_atr = atr_rsi[index - 1] if index > 0 else 0.0
    wwma[index] = alpha * current_tr + (1.0 - alpha) * previous_wwma
    atr_rsi[index] = alpha * wwma[index] + (1.0 - alpha) * previous_atr
    if not np.isfinite(fast[index]):
        slow[index] = slow[index - 1] if index > 0 else 0.0
        continue
    previous_slow = slow[index - 1] if index > 0 else 0.0
    previous_fast = fast[index - 1] if index > 0 and np.isfinite(fast[index - 1]) else fast[index]
    upper = fast[index] + atr_rsi[index] * 4.236
    lower = fast[index] - atr_rsi[index] * 4.236
    if upper < previous_slow:
        slow[index] = upper
    elif fast[index] > previous_slow and previous_fast < previous_slow:
        slow[index] = lower
    elif lower > previous_slow:
        slow[index] = lower
    elif fast[index] < previous_slow and previous_fast > previous_slow:
        slow[index] = upper
    else:
        slow[index] = previous_slow

fast_plot = [float(value) if np.isfinite(value) else None for value in fast]
slow_plot = [float(value) if np.isfinite(value) else None for value in slow]
cloud_plot = []
for index in range(n):
    if not highlight_state or not np.isfinite(fast[index]) or not np.isfinite(slow[index]):
        cloud_plot.append(None)
    else:
        color = "#008000" if fast[index] > slow[index] else "#FF0000"
        cloud_plot.append({"upper": float(max(fast[index], slow[index])), "lower": float(min(fast[index], slow[index])), "color": color})

buy_event = np.concatenate(([False], (fast[1:] > slow[1:]) & (fast[:-1] <= slow[:-1])))
sell_event = np.concatenate(([False], (fast[1:] < slow[1:]) & (fast[:-1] >= slow[:-1])))
buy_marks = [float(slow[index] * 0.995) if show_signals and buy_event[index] else None for index in range(n)]
sell_marks = [float(slow[index] * 1.005) if show_signals and sell_event[index] else None for index in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "QQE Fast", "data": fast_plot, "color": "#800000", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "QQE Slow", "data": slow_plot, "color": "#0000FF", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "QQE Midline", "data": [50.0 for _ in range(n)], "color": "#808080", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "QQE State", "title": False, "data": cloud_plot, "color": "#008000", "type": "cloud", "opacity": 0.18, "overlay": False},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "QQE Buy", "data": buy_marks, "color": "#008000", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "QQE Sell", "data": sell_marks, "color": "#FF0000", "renderMode": "events"},
    ],
    "layers": [],
}
