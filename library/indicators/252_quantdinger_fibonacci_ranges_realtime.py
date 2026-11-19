# ============================================================
# QuantDinger Fibonacci Ranges Real-Time
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Fibonacci Ranges Real-Time"
my_indicator_description = (
    "Builds live Fibonacci channels from confirmed alternating pivots, preserves "
    "historical channel shadows, detects persistent boundary breaks, and can show "
    "the latest pivot-to-pivot Fibonacci range."
)

# @param left_bars int 5 Pivot left bars range=1:100:1
# @param right_bars int 1 Pivot right bars range=1:100:1
# @param channel_mode str Channel_Shadows Fibonacci channel values=Channel,Channel_Shadows,None
# @param break_level str 0_to_1 Break level values=neg_0382_to_1382,0_to_1,0236_to_0764,0382_to_0618
# @param break_count int 2 Break count range=0:100:1
# @param show_breaks bool true Show breaks
# @param show_latest_fibonacci bool false Show latest pivot Fibonacci
# @param outer_upper_color str #08998164 Outer upper color
# @param upper_color str #08998180 Upper color
# @param middle_color str #1E42B070 Middle color
# @param lower_color str #F2364580 Lower color
# @param outer_lower_color str #F2364564 Outer lower color

left_bars = min(100, max(1, int(params.get("left_bars", 5))))
right_bars = min(100, max(1, int(params.get("right_bars", 1))))
channel_mode = str(params.get("channel_mode", "Channel_Shadows"))
break_level = str(params.get("break_level", "0_to_1"))
break_count = min(100, max(0, int(params.get("break_count", 2))))
show_breaks = bool(params.get("show_breaks", True))
show_latest_fibonacci = bool(params.get("show_latest_fibonacci", False))
outer_upper_color = str(params.get("outer_upper_color", "#08998164"))
upper_color = str(params.get("upper_color", "#08998180"))
middle_color = str(params.get("middle_color", "#1E42B070"))
lower_color = str(params.get("lower_color", "#F2364580"))
outer_lower_color = str(params.get("outer_lower_color", "#F2364564"))


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def confirmed_pivot(values, confirmation_index, left, right, is_high):
    anchor = confirmation_index - right
    if anchor - left < 0 or anchor < 0:
        return np.nan, -1
    window = values[anchor - left:anchor + right + 1]
    if len(window) != left + right + 1 or not np.all(np.isfinite(window)):
        return np.nan, -1
    candidate = float(values[anchor])
    extreme = float(np.max(window) if is_high else np.min(window))
    if candidate == extreme:
        return candidate, anchor
    return np.nan, -1


def color_with_alpha(color, opacity):
    raw = str(color).strip()
    if raw.startswith("#") and len(raw) in (7, 9):
        red = int(raw[1:3], 16)
        green = int(raw[3:5], 16)
        blue = int(raw[5:7], 16)
        return f"rgba({red},{green},{blue},{opacity:.3f})"
    return raw


frame = df.copy().reset_index(drop=True)
n = len(frame)
if "time" not in frame.columns:
    frame["time"] = np.arange(n, dtype=float) * 3600.0
time_values = pd.to_numeric(frame["time"], errors="coerce").to_numpy(dtype=float)
high_values = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype=float)

ratios = np.asarray([-0.382, 0.0, 0.236, 0.382, 0.5, 0.618, 0.764, 1.0, 1.382], dtype=float)
ratio_colors = [outer_upper_color, outer_upper_color, upper_color, upper_color, middle_color, lower_color, lower_color, outer_lower_color, outer_lower_color]
break_pairs = {
    "neg_0382_to_1382": (-0.382, 1.382),
    "0_to_1": (0.0, 1.0),
    "0236_to_0764": (0.236, 0.764),
    "0382_to_0618": (0.382, 0.618),
    "-0.382 - 1.382": (-0.382, 1.382),
    "0.000 - 1.000": (0.0, 1.0),
    "0.236 - 0.764": (0.236, 0.764),
    "0.382 - 0.618": (0.382, 0.618),
}
break_lower_ratio, break_upper_ratio = break_pairs.get(break_level, (0.0, 1.0))
normalized_channel_mode = channel_mode.lower().replace("_", " ").replace(" + ", " ")
channel_enabled = normalized_channel_mode != "none"
show_shadows = normalized_channel_mode in ("channel shadows", "channel shadow")

pivot_high_event = np.zeros(n, dtype=bool)
pivot_low_event = np.zeros(n, dtype=bool)
pivot_high_anchor = np.full(n, -1, dtype=int)
pivot_low_anchor = np.full(n, -1, dtype=int)
new_channel = np.zeros(n, dtype=bool)
channel_active = np.zeros(n, dtype=bool)
channel_count = np.zeros(n, dtype=int)
channel_id = np.full(n, -1, dtype=int)
break_up = np.zeros(n, dtype=bool)
break_down = np.zeros(n, dtype=bool)
level_series = [np.full(n, np.nan, dtype=float) for _ in ratios]

pivots = []
channel_records = []
active = False
active_id = -1
active_count = 0
active_levels = np.full(len(ratios), np.nan, dtype=float)
active_anchor = -1
active_previous_anchor = -1
active_base = np.nan
active_previous = np.nan
latest_pair = None


def start_channel(confirmation_index, anchor, price):
    previous = pivots[1]
    delta = float(price - previous["price"])
    levels_now = price - delta * ratios
    record = {
        "id": len(channel_records), "confirmation": int(confirmation_index),
        "anchor": int(anchor), "previousAnchor": int(previous["bar"]),
        "base": float(price), "previous": float(previous["price"]),
        "direction": 1 if delta > 0.0 else -1 if delta < 0.0 else 0,
        "end": None, "break": None,
    }
    channel_records.append(record)
    return record, levels_now


for index in range(n):
    pivot_high, high_anchor = confirmed_pivot(high_values, index, left_bars, right_bars, True)
    pivot_low, low_anchor = confirmed_pivot(low_values, index, left_bars, right_bars, False)

    if np.isfinite(pivot_high):
        pivot_high_event[index] = True
        pivot_high_anchor[index] = high_anchor
        if pivots:
            first = pivots[0]
            if first["direction"] > 0 and pivot_high > first["price"]:
                first["bar"] = int(high_anchor)
                first["price"] = float(pivot_high)
            if first["direction"] < 0 and pivot_high > first["price"]:
                pivots.insert(0, {"bar": int(high_anchor), "price": float(pivot_high), "direction": 1})
        else:
            pivots.insert(0, {"bar": int(high_anchor), "price": float(pivot_high), "direction": 1})

        if len(pivots) > 1 and not active and channel_enabled:
            record, levels_now = start_channel(index, high_anchor, pivot_high)
            active = True
            active_id = record["id"]
            active_count = 0
            active_levels = levels_now
            active_anchor = int(high_anchor)
            active_previous_anchor = int(record["previousAnchor"])
            active_base = float(pivot_high)
            active_previous = float(record["previous"])
            new_channel[index] = True

    if np.isfinite(pivot_low):
        pivot_low_event[index] = True
        pivot_low_anchor[index] = low_anchor
        if pivots:
            first = pivots[0]
            if first["direction"] < 0 and pivot_low < first["price"]:
                first["bar"] = int(low_anchor)
                first["price"] = float(pivot_low)
            if first["direction"] > 0 and pivot_low < first["price"]:
                pivots.insert(0, {"bar": int(low_anchor), "price": float(pivot_low), "direction": -1})
        else:
            pivots.insert(0, {"bar": int(low_anchor), "price": float(pivot_low), "direction": -1})

        if len(pivots) > 1 and not active and channel_enabled:
            record, levels_now = start_channel(index, low_anchor, pivot_low)
            active = True
            active_id = record["id"]
            active_count = 0
            active_levels = levels_now
            active_anchor = int(low_anchor)
            active_previous_anchor = int(record["previousAnchor"])
            active_base = float(pivot_low)
            active_previous = float(record["previous"])
            new_channel[index] = True

    if (np.isfinite(pivot_high) or np.isfinite(pivot_low)) and len(pivots) >= 2:
        latest_pair = (dict(pivots[0]), dict(pivots[1]))

    if active:
        lower_price = float(active_base - (active_base - active_previous) * break_lower_ratio)
        upper_price = float(active_base - (active_base - active_previous) * break_upper_ratio)
        boundary_min = min(lower_price, upper_price)
        boundary_max = max(lower_price, upper_price)
        if close_values[index] < boundary_max and close_values[index] > boundary_min:
            active_count = 0
        elif close_values[index] > boundary_max or close_values[index] < boundary_min:
            active_count += 1

        if active_count > break_count:
            if close_values[index] > boundary_max:
                break_up[index] = True
                channel_records[active_id]["break"] = "up"
            else:
                break_down[index] = True
                channel_records[active_id]["break"] = "down"
            channel_records[active_id]["end"] = int(index)
            active = False
        else:
            channel_active[index] = True
            channel_count[index] = active_count
            channel_id[index] = active_id
            for level_index in range(len(ratios)):
                level_series[level_index][index] = active_levels[level_index]

if active and active_id >= 0:
    channel_records[active_id]["end"] = int(n - 1) if n else None

plots = [{
    "name": "Price Anchor", "title": False, "data": finite(close_values),
    "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True,
}]
zone_pairs = [(0, 1, outer_upper_color), (2, 3, upper_color), (3, 5, middle_color), (5, 6, lower_color), (7, 8, outer_lower_color)]
if show_shadows:
    for zone_index, (first_index, second_index, zone_color) in enumerate(zone_pairs):
        cloud = []
        for index in range(n):
            first = level_series[first_index][index]
            second = level_series[second_index][index]
            if np.isfinite(first) and np.isfinite(second):
                cloud.append({
                    "upper": float(max(first, second)), "lower": float(min(first, second)),
                    "color": color_with_alpha(zone_color, 0.18),
                })
            else:
                cloud.append(None)
        plots.append({"name": f"Channel Shadow {zone_index + 1}", "title": False, "data": cloud, "type": "cloud", "overlay": True})

break_up_points = [
    {"value": float(high_values[index]), "color": "#089981", "size": 5}
    if show_breaks and break_up[index] else None for index in range(n)
]
break_down_points = [
    {"value": float(low_values[index]), "color": "#F23645", "size": 5}
    if show_breaks and break_down[index] else None for index in range(n)
]
plots.extend([
    {"name": "Break Up", "title": False, "data": break_up_points, "color": "#089981", "type": "scatter", "size": 5, "overlay": True},
    {"name": "Break Down", "title": False, "data": break_down_points, "color": "#F23645", "type": "scatter", "size": 5, "overlay": True},
])

layers = []
if n and active:
    for level_index, ratio in enumerate(ratios):
        price = float(active_levels[level_index])
        layers.append({
            "type": "line", "role": f"Active Fibonacci {ratio:g}",
            "startIndex": active_anchor, "endIndex": n - 1,
            "y1": price, "y2": price, "color": ratio_colors[level_index],
            "lineWidth": 1, "extendBars": 8,
        })
    layers.append({
        "type": "line", "role": "Active Fibonacci Diagonal",
        "startIndex": active_previous_anchor, "endIndex": active_anchor,
        "y1": active_previous, "y2": active_base,
        "color": "#C0C0C0", "lineWidth": 1, "dashed": True,
    })

if n and show_latest_fibonacci and latest_pair is not None:
    current_pivot, previous_pivot = latest_pair
    delta = float(current_pivot["price"] - previous_pivot["price"])
    latest_levels = current_pivot["price"] - delta * ratios
    time_scale = 1000.0 if np.nanmax(np.abs(time_values)) > 10000000000 else 1.0
    valid_intervals = np.diff(time_values)
    valid_intervals = valid_intervals[np.isfinite(valid_intervals) & (valid_intervals > 0.0)]
    bar_interval = float(valid_intervals[-1]) if len(valid_intervals) else time_scale
    latest_end = float(time_values[-1] + bar_interval * 8.0)
    latest_start = float(time_values[min(max(0, int(current_pivot["bar"])), n - 1)])
    for level_index, ratio in enumerate(ratios):
        price = float(latest_levels[level_index])
        layers.append({
            "type": "line", "role": f"Latest Fibonacci {ratio:g}",
            "start": latest_start, "end": latest_end,
            "y1": price, "y2": price, "color": ratio_colors[level_index], "lineWidth": 1,
        })
    layers.append({
        "type": "line", "role": "Latest Fibonacci Diagonal",
        "startIndex": int(previous_pivot["bar"]), "endIndex": int(current_pivot["bar"]),
        "y1": float(previous_pivot["price"]), "y2": float(current_pivot["price"]),
        "color": "#C0C0C0", "lineWidth": 1, "dashed": True,
    })
    for zone_index, (first_index, second_index, zone_color) in enumerate(zone_pairs):
        first = float(latest_levels[first_index])
        second = float(latest_levels[second_index])
        layers.append({
            "type": "box", "role": f"Latest Fibonacci Zone {zone_index + 1}",
            "start": latest_start, "end": latest_end,
            "top": max(first, second), "bottom": min(first, second),
            "color": color_with_alpha(zone_color, 0.14),
            "fillColor": color_with_alpha(zone_color, 0.14),
            "borderColor": color_with_alpha(zone_color, 0.20), "borderSize": 0, "opacity": 1.0,
        })

signals = [
    {"type": "buy", "name": "Break Up", "text": "UP", "color": "#089981", "renderMode": "hidden", "data": [float(high_values[index]) if break_up[index] else None for index in range(n)]},
    {"type": "sell", "name": "Break Down", "text": "DOWN", "color": "#F23645", "renderMode": "hidden", "data": [float(low_values[index]) if break_down[index] else None for index in range(n)]},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": [],
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "pivotHighEvent": pivot_high_event.tolist(), "pivotLowEvent": pivot_low_event.tolist(),
        "pivotHighAnchor": pivot_high_anchor.tolist(), "pivotLowAnchor": pivot_low_anchor.tolist(),
        "newChannel": new_channel.tolist(), "channelActive": channel_active.tolist(),
        "channelCount": channel_count.tolist(), "channelId": channel_id.tolist(),
        "breakUp": break_up.tolist(), "breakDown": break_down.tolist(),
        "ratios": ratios.tolist(), "levelSeries": [finite(values) for values in level_series],
        "pivots": pivots, "channels": channel_records,
        "finalActive": bool(active), "finalActiveId": int(active_id),
    },
}
