# ============================================================
# QuantDinger Turtle Trade Channels
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Turtle Trade Channels"
my_indicator_description = (
    "Classic Turtle entry and exit channels with breakout state, trend and "
    "exit lines, directional channel fills, and entry or exit events."
)

# @param entry_length int 20 Entry channel period range=1:200:1
# @param exit_length int 10 Exit channel period range=1:200:1
# @param show_signals bool true Show entry and exit signals
# @param highlight_state bool true Highlight channel state

entry_length = max(1, int(params.get("entry_length", 20)))
exit_length = max(1, int(params.get("exit_length", 10)))
show_signals = bool(params.get("show_signals", True))
highlight_state = bool(params.get("highlight_state", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

upper = pd.Series(high).rolling(entry_length, min_periods=entry_length).max().to_numpy(dtype=float)
lower = pd.Series(low).rolling(entry_length, min_periods=entry_length).min().to_numpy(dtype=float)
exit_upper = pd.Series(high).rolling(exit_length, min_periods=exit_length).max().to_numpy(dtype=float)
exit_lower = pd.Series(low).rolling(exit_length, min_periods=exit_length).min().to_numpy(dtype=float)

high_break = np.zeros(n, dtype=bool)
low_break = np.zeros(n, dtype=bool)
long_exit_event = np.zeros(n, dtype=bool)
short_exit_event = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(upper[index - 1]):
        high_break[index] = high[index] >= upper[index - 1]
    if np.isfinite(lower[index - 1]):
        low_break[index] = low[index] <= lower[index - 1]
    if np.isfinite(exit_lower[index - 1]):
        long_exit_event[index] = low[index] <= exit_lower[index - 1]
    if np.isfinite(exit_upper[index - 1]):
        short_exit_event[index] = high[index] >= exit_upper[index - 1]

high_since = np.full(n, 1000000, dtype=int)
low_since = np.full(n, 1000000, dtype=int)
long_exit_since = np.full(n, 1000000, dtype=int)
short_exit_since = np.full(n, 1000000, dtype=int)
for index in range(n):
    high_since[index] = 0 if high_break[index] else (high_since[index - 1] + 1 if index > 0 else 1000000)
    low_since[index] = 0 if low_break[index] else (low_since[index - 1] + 1 if index > 0 else 1000000)
    long_exit_since[index] = 0 if long_exit_event[index] else (long_exit_since[index - 1] + 1 if index > 0 else 1000000)
    short_exit_since[index] = 0 if short_exit_event[index] else (short_exit_since[index - 1] + 1 if index > 0 else 1000000)

trend_line = np.full(n, np.nan, dtype=float)
exit_line = np.full(n, np.nan, dtype=float)
long_entry = np.zeros(n, dtype=bool)
short_entry = np.zeros(n, dtype=bool)
long_exit = np.zeros(n, dtype=bool)
short_exit = np.zeros(n, dtype=bool)
green_area = []
red_area = []
for index in range(n):
    bullish_state = high_since[index] <= low_since[index]
    if np.isfinite(upper[index]) and np.isfinite(lower[index]):
        trend_line[index] = lower[index] if bullish_state else upper[index]
    if np.isfinite(exit_upper[index]) and np.isfinite(exit_lower[index]):
        exit_line[index] = exit_lower[index] if bullish_state else exit_upper[index]
    prior_high_since = high_since[index - 1] if index > 0 else 1000000
    prior_low_since = low_since[index - 1] if index > 0 else 1000000
    prior_long_exit_since = long_exit_since[index - 1] if index > 0 else 1000000
    prior_short_exit_since = short_exit_since[index - 1] if index > 0 else 1000000
    long_entry[index] = high_break[index] and long_exit_since[index] < prior_high_since
    short_entry[index] = low_break[index] and short_exit_since[index] < prior_low_since
    long_exit[index] = long_exit_event[index] and high_since[index] < prior_long_exit_since
    short_exit[index] = short_exit_event[index] and low_since[index] < prior_short_exit_since
    if highlight_state and np.isfinite(exit_line[index]):
        green_area.append({"upper": float(max(upper[index], exit_line[index])), "lower": float(min(upper[index], exit_line[index])), "color": "#16A34A"} if high_since[index] <= min(low_since[index], long_exit_since[index]) and np.isfinite(upper[index]) else None)
        red_area.append({"upper": float(max(lower[index], exit_line[index])), "lower": float(min(lower[index], exit_line[index])), "color": "#EF4444"} if low_since[index] <= min(high_since[index], short_exit_since[index]) and np.isfinite(lower[index]) else None)
    else:
        green_area.append(None)
        red_area.append(None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Upper Entry Channel", "data": [float(value) if np.isfinite(value) else None for value in upper], "color": "#0094FF", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower Entry Channel", "data": [float(value) if np.isfinite(value) else None for value in lower], "color": "#0094FF", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Turtle Trend Line", "data": [float(value) if np.isfinite(value) else None for value in trend_line], "color": "#EF4444", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Turtle Exit Line", "data": [float(value) if np.isfinite(value) else None for value in exit_line], "color": "#2962FF", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Long Channel State", "title": False, "data": green_area, "color": "#16A34A", "type": "cloud", "opacity": 0.12, "overlay": True},
        {"name": "Short Channel State", "title": False, "data": red_area, "color": "#EF4444", "type": "cloud", "opacity": 0.12, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "LONG", "name": "Long Entry", "data": [float(lower[i]) if show_signals and long_entry[i] and np.isfinite(lower[i]) else None for i in range(n)], "color": "#16A34A", "renderMode": "events"},
        {"type": "sell", "text": "SHORT", "name": "Short Entry", "data": [float(upper[i]) if show_signals and short_entry[i] and np.isfinite(upper[i]) else None for i in range(n)], "color": "#EF4444", "renderMode": "events"},
        {"type": "sell", "text": "EXIT", "name": "Long Exit", "data": [float(upper[i]) if show_signals and long_exit[i] and np.isfinite(upper[i]) else None for i in range(n)], "color": "#2962FF", "renderMode": "events"},
        {"type": "buy", "text": "EXIT", "name": "Short Exit", "data": [float(lower[i]) if show_signals and short_exit[i] and np.isfinite(lower[i]) else None for i in range(n)], "color": "#2962FF", "renderMode": "events"},
    ],
    "layers": [],
}
