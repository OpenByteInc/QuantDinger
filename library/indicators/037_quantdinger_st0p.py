# ============================================================
# QuantDinger ST0P
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger ST0P"
my_indicator_description = (
    "A configurable long- or short-side percentage stop that ratchets with "
    "price and emits crossing events."
)

# @param percent float 3.0 Stop percent range=0.0:100.0:0.1
# @param side int 1 Long or short side range=1:2:1
# @param unit float 0.0 Absolute offset range=0.0:100000.0:0.1
# @param source_mode int 0 Price source range=0:3:1
# @param show_signals bool true Show crossing labels
# @param highlight_state bool true Highlight active stop side

percent = max(0.0, float(params.get("percent", 3.0)))
side = max(1, min(2, int(params.get("side", 1))))
unit = max(0.0, float(params.get("unit", 0.0)))
source_mode = max(0, min(3, int(params.get("source_mode", 0))))
show_signals = bool(params.get("show_signals", True))
highlight_state = bool(params.get("highlight_state", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
if source_mode == 1:
    source = (high + low) / 2.0
elif source_mode == 2:
    source = (high + low + close) / 3.0
elif source_mode == 3:
    source = (open_price + high + low + close) / 4.0
else:
    source = close.copy()
n = len(df)

short_candidate = source + unit + source * percent / 100.0
long_candidate = source - unit - source * percent / 100.0
short_stop = np.zeros(n, dtype=float)
long_stop = np.zeros(n, dtype=float)
for index in range(n):
    previous_short = short_stop[index - 1] if index > 0 else 0.0
    previous_long = long_stop[index - 1] if index > 0 else 0.0
    previous_source = source[index - 1] if index > 0 else np.nan
    short_stop[index] = short_candidate[index] if short_candidate[index] < previous_short or (np.isfinite(previous_source) and previous_source > previous_short) else previous_short
    long_stop[index] = long_candidate[index] if long_candidate[index] > previous_long or (np.isfinite(previous_source) and previous_source < previous_long) else previous_long

active_stop = long_stop if side == 1 else short_stop
cross_down = np.concatenate(([False], (source[1:] < active_stop[1:]) & (source[:-1] >= active_stop[:-1])))
cross_up = np.concatenate(([False], (source[1:] > active_stop[1:]) & (source[:-1] <= active_stop[:-1])))
stop_plot = []
state_cloud = []
mid_price = (open_price + high + low + close) / 4.0
for index in range(n):
    if index > 0 and active_stop[index] > active_stop[index - 1]:
        color = "#008000"
    elif index > 0 and active_stop[index] < active_stop[index - 1]:
        color = "#FF0000"
    else:
        color = "#001AE1"
    stop_plot.append({"value": float(active_stop[index]), "color": color})
    if highlight_state:
        state_cloud.append(
            {
                "upper": float(max(mid_price[index], active_stop[index])),
                "lower": float(min(mid_price[index], active_stop[index])),
                "color": "#008000" if side == 1 else "#FF0000",
            }
        )
    else:
        state_cloud.append(None)

buy_marks = [float(short_candidate[index]) if show_signals and side == 2 and cross_up[index] else None for index in range(n)]
sell_marks = [float(long_candidate[index]) if show_signals and side == 1 and cross_down[index] else None for index in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "ST0P", "data": stop_plot, "color": "#001AE1", "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Active Side", "title": False, "data": state_cloud, "color": "#008000", "type": "cloud", "opacity": 0.15, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "ST0P", "name": "Short Stop Buy", "data": buy_marks, "color": "#008000", "renderMode": "events"},
        {"type": "sell", "text": "ST0P", "name": "Long Stop Sell", "data": sell_marks, "color": "#FF0000", "renderMode": "events"},
    ],
    "layers": [],
}
