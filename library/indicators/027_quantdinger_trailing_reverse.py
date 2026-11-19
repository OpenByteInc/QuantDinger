# ============================================================
# QuantDinger Trailing Reverse
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Trailing Reverse"
my_indicator_description = (
    "A percentage trailing reversal line that switches between long and short states at confirmed closes."
)

# @param correction_percent float 2.0 Correction percent range=0.0:100.0:0.1
# @param show_labels bool true Show reversal labels
# @param highlight_state bool true Highlight trend state

correction_percent = max(0.0, min(100.0, float(params.get("correction_percent", 2.0)))) / 100.0
show_labels = bool(params.get("show_labels", True))
highlight_state = bool(params.get("highlight_state", True))

df = df.copy()
open_price = df["open"].astype(float)
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
mid_price = (open_price + high + low + close) / 4.0
values = close.to_numpy(dtype=float)
mid_values = mid_price.to_numpy(dtype=float)
n = len(df)

trend_values = np.zeros(n, dtype=int)
reverse_line = np.full(n, np.nan, dtype=float)
trend = 0
high_point = values[0] if n else 0.0
low_point = values[0] if n else 0.0
for index in range(n):
    price = values[index]
    if trend >= 0:
        if price > high_point:
            high_point = price
        current = high_point * (1.0 - correction_percent)
        if price <= current:
            trend = -1
            low_point = price
            current = low_point * (1.0 + correction_percent)
    else:
        if price < low_point:
            low_point = price
        current = low_point * (1.0 + correction_percent)
        if price > current:
            trend = 1
            high_point = price
            current = high_point * (1.0 - correction_percent)
    trend_values[index] = trend
    reverse_line[index] = current

buy_event = np.concatenate(([False], (trend_values[:-1] == -1) & (trend_values[1:] == 1)))
sell_event = np.concatenate(([False], (trend_values[:-1] == 1) & (trend_values[1:] == -1)))
long_plot = [float(reverse_line[index]) if trend_values[index] == 1 else None for index in range(n)]
short_plot = [float(reverse_line[index]) if trend_values[index] != 1 else None for index in range(n)]
state_cloud = []
for index in range(n):
    if not highlight_state:
        state_cloud.append(None)
    else:
        state_cloud.append({"upper": float(max(mid_values[index], reverse_line[index])), "lower": float(min(mid_values[index], reverse_line[index])), "color": "#008000" if trend_values[index] == 1 else "#FF0000"})

buy_marks = [float(reverse_line[index]) if show_labels and buy_event[index] else None for index in range(n)]
sell_marks = [float(reverse_line[index]) if show_labels and sell_event[index] else None for index in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Long Stop", "data": long_plot, "color": "#008000", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Short Stop", "data": short_plot, "color": "#FF0000", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "State Cloud", "title": False, "data": state_cloud, "color": "#008000", "type": "cloud", "opacity": 0.15, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "Trailing Reverse Buy", "data": buy_marks, "color": "#008000", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Trailing Reverse Sell", "data": sell_marks, "color": "#FF0000", "renderMode": "events"},
    ],
    "layers": [],
}
