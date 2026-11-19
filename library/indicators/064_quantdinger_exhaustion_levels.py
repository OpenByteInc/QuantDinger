# ============================================================
# QuantDinger Exhaustion Levels
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Exhaustion Levels"
my_indicator_description = (
    "Persistent support and resistance levels created by counted four-bar "
    "price advances or declines that terminate at a rolling extreme."
)

# @param show_points bool true Show exhaustion events
# @param show_levels bool true Show exhaustion levels
# @param swing_length int 40 Swing lookback range=2:200:1
# @param bar_count int 10 Exhaustion count range=1:100:1
# @param line_width int 2 Level line width range=1:5:1

show_points = bool(params.get("show_points", True))
show_levels = bool(params.get("show_levels", True))
swing_length = max(2, int(params.get("swing_length", 40)))
bar_count = max(1, int(params.get("bar_count", 10)))
line_width = max(1, min(5, int(params.get("line_width", 2))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

rolling_high = pd.Series(high).rolling(swing_length, min_periods=1).max().to_numpy(dtype=float)
rolling_low = pd.Series(low).rolling(swing_length, min_periods=1).min().to_numpy(dtype=float)
events = np.zeros(n, dtype=int)
resistance = np.full(n, np.nan, dtype=float)
support = np.full(n, np.nan, dtype=float)
advance_count = 0
decline_count = 0
current_resistance = np.nan
current_support = np.nan

for index in range(n):
    if index >= 4 and close[index] > close[index - 4]:
        advance_count += 1
    if index >= 4 and close[index] < close[index - 4]:
        decline_count += 1
    if advance_count > bar_count and close[index] < open_price[index] and high[index] >= rolling_high[index]:
        advance_count = 0
        events[index] = -1
        current_resistance = high[index]
    elif decline_count > bar_count and close[index] > open_price[index] and low[index] <= rolling_low[index]:
        decline_count = 0
        events[index] = 1
        current_support = low[index]
    resistance[index] = current_resistance
    support[index] = current_support

resistance_plot = []
support_plot = []
for index in range(n):
    changed_resistance = index == 0 or not np.isfinite(resistance[index - 1]) or resistance[index] != resistance[index - 1]
    changed_support = index == 0 or not np.isfinite(support[index - 1]) or support[index] != support[index - 1]
    resistance_plot.append(float(resistance[index]) if show_levels and np.isfinite(resistance[index]) and not changed_resistance else None)
    support_plot.append(float(support[index]) if show_levels and np.isfinite(support[index]) and not changed_support else None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Resistance Level", "data": resistance_plot, "color": "#FF0000", "type": "line", "lineWidth": line_width, "overlay": True},
        {"name": "Support Level", "data": support_plot, "color": "#138484", "type": "line", "lineWidth": line_width, "overlay": True},
        {"name": "Event Anchor", "title": False, "data": [float(value) for value in close], "color": "rgba(0, 0, 0, 0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "Up", "name": "Bullish Exhaustion", "data": [float(low[i]) if show_points and events[i] == 1 else None for i in range(n)], "color": "#00FF00", "renderMode": "events"},
        {"type": "sell", "text": "Down", "name": "Bearish Exhaustion", "data": [float(high[i]) if show_points and events[i] == -1 else None for i in range(n)], "color": "#FF0000", "renderMode": "events"},
    ],
    "layers": [],
}
