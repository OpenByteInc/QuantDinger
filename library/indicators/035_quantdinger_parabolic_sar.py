# ============================================================
# QuantDinger Parabolic SAR
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Parabolic SAR"
my_indicator_description = (
    "Wilder's accelerating stop-and-reverse trail with direction changes, "
    "entry labels, and optional state highlighting."
)

# @param start_factor float 0.02 Start factor range=0.001:1.0:0.001
# @param increment_factor float 0.02 Increment factor range=0.001:1.0:0.001
# @param maximum_factor float 0.2 Maximum factor range=0.01:2.0:0.01
# @param point_width int 2 Point width range=1:8:1
# @param show_labels bool true Show buy and sell labels
# @param highlight_start bool true Highlight reversal points
# @param highlight_state bool true Highlight trend state

start_factor = max(0.001, float(params.get("start_factor", 0.02)))
increment_factor = max(0.001, float(params.get("increment_factor", 0.02)))
maximum_factor = max(start_factor, float(params.get("maximum_factor", 0.2)))
point_width = max(1, int(params.get("point_width", 2)))
show_labels = bool(params.get("show_labels", True))
highlight_start = bool(params.get("highlight_start", True))
highlight_state = bool(params.get("highlight_state", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

sar = np.full(n, np.nan, dtype=float)
direction = np.zeros(n, dtype=int)
if n == 1:
    sar[0] = low[0]
    direction[0] = 1
elif n > 1:
    bullish = close[1] > close[0]
    extreme = high[1] if bullish else low[1]
    current_sar = low[0] if bullish else high[0]
    acceleration = start_factor
    sar[0] = current_sar
    direction[0] = 1 if bullish else -1
    for index in range(1, n):
        candidate = current_sar + acceleration * (extreme - current_sar)
        if bullish:
            if candidate > low[index]:
                bullish = False
                candidate = extreme
                extreme = low[index]
                acceleration = start_factor
            else:
                if high[index] > extreme:
                    extreme = high[index]
                    acceleration = min(maximum_factor, acceleration + increment_factor)
                candidate = min(candidate, low[index - 1])
                if index > 1:
                    candidate = min(candidate, low[index - 2])
        else:
            if candidate < high[index]:
                bullish = True
                candidate = extreme
                extreme = high[index]
                acceleration = start_factor
            else:
                if low[index] < extreme:
                    extreme = low[index]
                    acceleration = min(maximum_factor, acceleration + increment_factor)
                candidate = max(candidate, high[index - 1])
                if index > 1:
                    candidate = max(candidate, high[index - 2])
        current_sar = candidate
        sar[index] = current_sar
        direction[index] = 1 if current_sar < close[index] else -1

buy_event = np.concatenate(([False], (direction[1:] == 1) & (direction[:-1] == -1)))
sell_event = np.concatenate(([False], (direction[1:] == -1) & (direction[:-1] == 1)))
sar_plot = []
state_cloud = []
mid_price = (open_price + high + low + close) / 4.0
for index in range(n):
    color = "#3388BB" if direction[index] == 1 else "#FDCC02"
    sar_plot.append({"value": float(sar[index]), "color": color} if np.isfinite(sar[index]) else None)
    if highlight_state and np.isfinite(sar[index]):
        state_cloud.append(
            {
                "upper": float(max(mid_price[index], sar[index])),
                "lower": float(min(mid_price[index], sar[index])),
                "color": "#008000" if direction[index] == 1 else "#FF0000",
            }
        )
    else:
        state_cloud.append(None)

buy_marks = [float(sar[index]) if show_labels and buy_event[index] else None for index in range(n)]
sell_marks = [float(sar[index]) if show_labels and sell_event[index] else None for index in range(n)]
start_marks = [float(sar[index]) if highlight_start and (buy_event[index] or sell_event[index]) else None for index in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "PSAR", "data": sar_plot, "color": "#3388BB", "type": "line", "lineWidth": point_width, "overlay": True},
        {"name": "Trade State", "title": False, "data": state_cloud, "color": "#008000", "type": "cloud", "opacity": 0.15, "overlay": True},
        {"name": "Reversal Points", "title": False, "data": start_marks, "color": "#FFFFFF", "type": "line", "lineWidth": max(2, point_width), "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "PSAR Buy", "data": buy_marks, "color": "#008000", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "PSAR Sell", "data": sell_marks, "color": "#FF0000", "renderMode": "events"},
    ],
    "layers": [],
}
