# ============================================================
# QuantDinger Z-Score Range Breakout
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Z-Score Range Breakout"
my_indicator_description = (
    "Builds price ranges after extreme smoothed Z-Score crossings, extends the "
    "finalized top, midpoint and bottom levels, and marks confirmed breakouts."
)

# @param z_length int 20 Z-Score length range=2:500:1
# @param z_source str close Z-Score source values=close,open,high,low,hl2,hlc3,ohlc4
# @param smooth_length int 5 Z-Score smoothing length range=1:100:1
# @param oversold_threshold float -2.0 Oversold trigger range=-10.0:-0.1:0.1
# @param overbought_threshold float 2.0 Overbought trigger range=0.1:10.0:0.1
# @param box_period int 10 Developing range period range=1:200:1

z_length = min(500, max(2, int(params.get("z_length", 20))))
z_source = str(params.get("z_source", "close")).lower()
smooth_length = min(100, max(1, int(params.get("smooth_length", 5))))
oversold_threshold = float(params.get("oversold_threshold", -2.0))
overbought_threshold = float(params.get("overbought_threshold", 2.0))
box_period = min(200, max(1, int(params.get("box_period", 10))))

if oversold_threshold >= overbought_threshold:
    raise ValueError("Oversold threshold must be below overbought threshold")

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

source_map = {
    "close": close,
    "open": open_price,
    "high": high,
    "low": low,
    "hl2": (high + low) / 2.0,
    "hlc3": (high + low + close) / 3.0,
    "ohlc4": (open_price + high + low + close) / 4.0,
}
source = source_map.get(z_source, close)

source_series = pd.Series(source, dtype=float)
mean = source_series.rolling(z_length, min_periods=z_length).mean().to_numpy(dtype=float)
deviation = source_series.rolling(z_length, min_periods=z_length).std(ddof=0).to_numpy(dtype=float)
z_score = np.full(n, np.nan, dtype=float)
valid = np.isfinite(mean) & np.isfinite(deviation)
z_score[valid] = np.where(deviation[valid] != 0.0, (source[valid] - mean[valid]) / deviation[valid], 0.0)
z_smooth = pd.Series(z_score, dtype=float).rolling(smooth_length, min_periods=smooth_length).mean().to_numpy(dtype=float)

oversold_start = np.zeros(n, dtype=bool)
overbought_start = np.zeros(n, dtype=bool)
bullish_breakout = np.zeros(n, dtype=bool)
bearish_breakout = np.zeros(n, dtype=bool)
episodes = []
segments = []

active_episode = None
current_top = np.nan
current_bottom = np.nan
top_segment = None
mid_segment = None
bottom_segment = None

def crossed_under(values, index, level):
    return (
        index > 0
        and np.isfinite(values[index])
        and np.isfinite(values[index - 1])
        and values[index] < level
        and values[index - 1] >= level
    )

def crossed_over(values, index, level):
    return (
        index > 0
        and np.isfinite(values[index])
        and np.isfinite(values[index - 1])
        and values[index] > level
        and values[index - 1] <= level
    )

for index in range(n):
    start_oversold = crossed_under(z_smooth, index, oversold_threshold) and active_episode is None
    start_overbought = crossed_over(z_smooth, index, overbought_threshold) and active_episode is None

    if start_oversold or start_overbought:
        current_top = np.nan
        current_bottom = np.nan
        active_episode = {
            "kind": "bull" if start_oversold else "bear",
            "start": index,
            "top": float(high[index]),
            "bottom": float(low[index]),
            "z": float(z_smooth[index]),
        }
        episodes.append(active_episode)
        oversold_start[index] = start_oversold
        overbought_start[index] = start_overbought

    if active_episode is not None:
        elapsed = index - int(active_episode["start"])
        if elapsed <= box_period:
            active_episode["top"] = max(float(active_episode["top"]), float(high[index]))
            active_episode["bottom"] = min(float(active_episode["bottom"]), float(low[index]))
            if elapsed == box_period:
                current_top = float(active_episode["top"])
                current_bottom = float(active_episode["bottom"])
                midpoint = (current_top + current_bottom) / 2.0
                color = "#00FFFF" if active_episode["kind"] == "bull" else "#FF8352"
                top_segment = {"start": int(active_episode["start"]), "end": index, "value": current_top, "color": color}
                mid_segment = {"start": int(active_episode["start"]), "end": index, "value": midpoint, "color": color}
                bottom_segment = {"start": int(active_episode["start"]), "end": index, "value": current_bottom, "color": color}
                segments.extend([top_segment, mid_segment, bottom_segment])
        else:
            active_episode = None

    bull_break = crossed_over(close, index, current_top) if np.isfinite(current_top) else False
    bear_break = crossed_under(close, index, current_bottom) if np.isfinite(current_bottom) else False

    if bull_break:
        bullish_breakout[index] = True
        current_top = np.nan
        top_segment = None
        mid_segment = None
    if bear_break:
        bearish_breakout[index] = True
        current_bottom = np.nan
        bottom_segment = None
        mid_segment = None

    if active_episode is None and not start_oversold and not start_overbought:
        if top_segment is not None:
            top_segment["end"] = index
        if mid_segment is not None:
            mid_segment["end"] = index
        if bottom_segment is not None:
            bottom_segment["end"] = index

range_boxes = [None for _ in range(n)]
for episode in episodes:
    end = min(n - 1, int(episode["start"]) + box_period)
    color = "rgba(0, 255, 255, 0.15)" if episode["kind"] == "bull" else "rgba(255, 131, 82, 0.15)"
    for index in range(int(episode["start"]), end + 1):
        range_boxes[index] = {
            "upper": float(episode["top"]),
            "lower": float(episode["bottom"]),
            "color": color,
        }

top_data = [None for _ in range(n)]
mid_data = [None for _ in range(n)]
bottom_data = [None for _ in range(n)]
for segment_index in range(0, len(segments), 3):
    top = segments[segment_index]
    mid = segments[segment_index + 1]
    bottom = segments[segment_index + 2]
    for index in range(int(top["start"]), min(n - 1, int(top["end"])) + 1):
        top_data[index] = {"value": float(top["value"]), "color": top["color"]}
    for index in range(int(mid["start"]), min(n - 1, int(mid["end"])) + 1):
        mid_data[index] = {"value": float(mid["value"]), "color": mid["color"]}
    for index in range(int(bottom["start"]), min(n - 1, int(bottom["end"])) + 1):
        bottom_data[index] = {"value": float(bottom["value"]), "color": bottom["color"]}

def gradient_color(value):
    if not np.isfinite(value):
        return "rgba(148, 163, 184, 0.0)"
    ratio = min(1.0, max(0.0, (float(value) + 3.0) / 6.0))
    red = int(round(255.0 * ratio))
    green = int(round(255.0 + (131.0 - 255.0) * ratio))
    blue = int(round(255.0 + (82.0 - 255.0) * ratio))
    return f"rgb({red}, {green}, {blue})"

oscillator_data = [
    None if not np.isfinite(value) else {"value": float(value), "color": gradient_color(value)}
    for value in z_smooth
]
zero_data = [0.0 for _ in range(n)]
overbought_data = [float(overbought_threshold) for _ in range(n)]
oversold_data = [float(oversold_threshold) for _ in range(n)]
overbought_fill = []
oversold_fill = []
for value in z_smooth:
    if np.isfinite(value) and value > overbought_threshold:
        overbought_fill.append(
            {
                "upper": float(max(value, overbought_threshold)),
                "lower": float(min(value, overbought_threshold)),
                "color": "rgba(255, 131, 82, 0.28)",
            }
        )
    else:
        overbought_fill.append(None)
    if np.isfinite(value) and value < oversold_threshold:
        oversold_fill.append(
            {
                "upper": float(max(value, oversold_threshold)),
                "lower": float(min(value, oversold_threshold)),
                "color": "rgba(0, 255, 255, 0.28)",
            }
        )
    else:
        oversold_fill.append(None)

def boundary_lines(values):
    upper = []
    lower = []
    for item in values:
        if item is None:
            upper.append(None)
            lower.append(None)
            continue
        color = item.get("color", "#94A3B8")
        upper.append({"value": float(item["upper"]), "color": color})
        lower.append({"value": float(item["lower"]), "color": color})
    return upper, lower

overbought_fill_upper, overbought_fill_lower = boundary_lines(overbought_fill)
oversold_fill_upper, oversold_fill_lower = boundary_lines(oversold_fill)
range_box_upper, range_box_lower = boundary_lines(range_boxes)

plots = [
    {"name": "Smoothed Z-Score", "data": oscillator_data, "color": "#94A3B8", "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Zero Midline", "data": zero_data, "color": "rgba(148, 163, 184, 0.50)", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Overbought Level", "data": overbought_data, "color": "#FF8352", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": False},
    {"name": "Oversold Level", "data": oversold_data, "color": "#00FFFF", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": False},
    {"name": "Overbought Excursion Upper", "title": False, "data": overbought_fill_upper, "color": "#FF8352", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Overbought Excursion Lower", "title": False, "data": overbought_fill_lower, "color": "#FF8352", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Oversold Excursion Upper", "title": False, "data": oversold_fill_upper, "color": "#00FFFF", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Oversold Excursion Lower", "title": False, "data": oversold_fill_lower, "color": "#00FFFF", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Developing Range Upper", "title": False, "data": range_box_upper, "color": "#64748B", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Developing Range Lower", "title": False, "data": range_box_lower, "color": "#64748B", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Range Top", "data": top_data, "color": "#94A3B8", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Range Midpoint", "data": mid_data, "color": "#94A3B8", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
    {"name": "Range Bottom", "data": bottom_data, "color": "#94A3B8", "type": "line", "lineWidth": 1, "overlay": True},
]

plots = [plot for plot in plots if any(item is not None for item in plot["data"])]

oversold_data_events = [None for _ in range(n)]
overbought_data_events = [None for _ in range(n)]
bullish_breakout_data = [None for _ in range(n)]
bearish_breakout_data = [None for _ in range(n)]
for index in range(n):
    if oversold_start[index]:
        oversold_data_events[index] = {"value": float(low[index]), "text": f"Z {z_smooth[index]:.2f}", "color": "#00FFFF"}
    if overbought_start[index]:
        overbought_data_events[index] = {"value": float(high[index]), "text": f"Z {z_smooth[index]:.2f}", "color": "#FF8352"}
    if bullish_breakout[index]:
        bullish_breakout_data[index] = {"value": float(low[index]), "text": "BRK", "color": "#00FFFF"}
    if bearish_breakout[index]:
        bearish_breakout_data[index] = {"value": float(high[index]), "text": "BRK", "color": "#FF8352"}

signals = [
    {"type": "buy", "text": "Z", "name": "Oversold Range", "data": oversold_data_events, "color": "#00FFFF", "renderMode": "events"},
    {"type": "sell", "text": "Z", "name": "Overbought Range", "data": overbought_data_events, "color": "#FF8352", "renderMode": "events"},
    {"type": "buy", "text": "BRK", "name": "Bullish Breakout", "data": bullish_breakout_data, "color": "#00FFFF", "renderMode": "events"},
    {"type": "sell", "text": "BRK", "name": "Bearish Breakout", "data": bearish_breakout_data, "color": "#FF8352", "renderMode": "events"},
]

signals = [signal for signal in signals if any(item is not None for item in signal["data"])]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": []}
