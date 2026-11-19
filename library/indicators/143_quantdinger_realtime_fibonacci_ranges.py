# ============================================================
# QuantDinger Real-Time Fibonacci Ranges
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Real-Time Fibonacci Ranges"
my_indicator_description = (
    "Builds an active Fibonacci channel from alternating swing pivots, removes it "
    "after configurable boundary breaks, and optionally projects the latest swing range."
)

# @param swing_left int 5 Swing left bars range=1:100:1
# @param swing_right int 1 Swing right bars range=1:100:1
# @param channel_mode str Channel_+_Shadows Channel display values=Channel,Channel_+_Shadows,None
# @param break_level str 0.000_to_1.000 Break boundary values=Minus_0.382_to_1.382,0.000_to_1.000,0.236_to_0.764,0.382_to_0.618
# @param break_count int 2 Consecutive break count range=0:100:1
# @param show_breaks bool true Show channel breaks
# @param show_latest_fibonacci bool false Show latest Fibonacci

swing_left = min(100, max(1, int(params.get("swing_left", 5))))
swing_right = min(100, max(1, int(params.get("swing_right", 1))))
channel_mode = str(params.get("channel_mode", "Channel_+_Shadows")).replace("_", " ")
break_level = str(params.get("break_level", "0.000_to_1.000"))
break_count = min(100, max(0, int(params.get("break_count", 2))))
show_breaks = bool(params.get("show_breaks", True))
show_latest_fibonacci = bool(params.get("show_latest_fibonacci", False))

if channel_mode not in ("Channel", "Channel + Shadows", "None"):
    channel_mode = "Channel + Shadows"
break_ranges = {
    "Minus_0.382_to_1.382": (-0.382, 1.382),
    "0.000_to_1.000": (0.0, 1.0),
    "0.236_to_0.764": (0.236, 0.764),
    "0.382_to_0.618": (0.382, 0.618),
}
break_minimum, break_maximum = break_ranges.get(break_level, (0.0, 1.0))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
time_values = df["time"].astype(float).to_numpy(dtype=float)
n = len(df)

positive_intervals = np.diff(time_values)
positive_intervals = positive_intervals[positive_intervals > 0.0]
interval = float(np.median(positive_intervals)) if positive_intervals.size else 0.0

def time_for_index(index):
    if n == 0:
        return 0.0
    if index < n:
        return float(time_values[max(0, index)])
    return float(time_values[-1] + interval * (index - (n - 1)))

def line_layer(start_index, end_index, price, color, width=1, dashed=False):
    layer = {
        "type": "line",
        "startIndex": int(max(0, start_index)),
        "y1": float(price),
        "y2": float(price),
        "color": color,
        "lineWidth": int(width),
        "dashed": bool(dashed),
    }
    if end_index < n:
        layer["endIndex"] = int(max(0, end_index))
    else:
        layer["end"] = time_for_index(end_index)
    return layer

def zone_layer(start_index, end_index, first_price, second_price, color):
    layer = {
        "type": "zone",
        "startIndex": int(max(0, start_index)),
        "top": float(max(first_price, second_price)),
        "bottom": float(min(first_price, second_price)),
        "fillColor": color,
        "borderColor": "rgba(0,0,0,0.0)",
        "borderSize": 0,
    }
    if end_index < n:
        layer["endIndex"] = int(max(0, end_index))
    else:
        layer["end"] = time_for_index(end_index)
    return layer

pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
pivot_high_index = np.full(n, -1, dtype=int)
pivot_low_index = np.full(n, -1, dtype=int)
for confirmation_index in range(swing_left + swing_right, n):
    candidate_index = confirmation_index - swing_right
    first_index = candidate_index - swing_left
    last_index = candidate_index + swing_right + 1
    if high[candidate_index] >= float(np.max(high[first_index:last_index])):
        pivot_high[confirmation_index] = float(high[candidate_index])
        pivot_high_index[confirmation_index] = candidate_index
    if low[candidate_index] <= float(np.min(low[first_index:last_index])):
        pivot_low[confirmation_index] = float(low[candidate_index])
        pivot_low_index[confirmation_index] = candidate_index

pivots = []
active_channel = None
channel_count = 0
break_up = np.zeros(n, dtype=bool)
break_down = np.zeros(n, dtype=bool)

def create_channel(current_index, current_price, previous_pivot, sign, current_bar):
    difference = abs(float(current_price - previous_pivot["price"]))
    prices = {
        -0.382: float(current_price + sign * difference * 0.382),
        0.0: float(current_price),
        0.236: float(current_price - sign * difference * 0.236),
        0.382: float(current_price - sign * difference * 0.382),
        0.5: float(current_price - sign * difference * 0.5),
        0.618: float(current_price - sign * difference * 0.618),
        0.764: float(current_price - sign * difference * 0.764),
        1.0: float(previous_pivot["price"]),
        1.382: float(previous_pivot["price"] - sign * difference * 0.382),
    }
    return {
        "start": int(current_index),
        "end": int(current_bar + 8),
        "previous_index": int(previous_pivot["index"]),
        "previous_price": float(previous_pivot["price"]),
        "current_price": float(current_price),
        "prices": prices,
    }

for index in range(n):
    if np.isfinite(pivot_high[index]):
        current_index = int(pivot_high_index[index])
        current_price = float(pivot_high[index])
        if pivots:
            first = pivots[0]
            if first["direction"] > 0 and current_price > first["price"]:
                first["index"] = current_index
                first["price"] = current_price
            elif first["direction"] < 0 and current_price > first["price"]:
                pivots.insert(0, {"index": current_index, "price": current_price, "direction": 1})
        else:
            pivots.insert(0, {"index": current_index, "price": current_price, "direction": 1})
        if len(pivots) > 1 and active_channel is None and channel_mode != "None":
            active_channel = create_channel(current_index, current_price, pivots[1], 1.0, index)
            channel_count = 0

    if np.isfinite(pivot_low[index]):
        current_index = int(pivot_low_index[index])
        current_price = float(pivot_low[index])
        if pivots:
            first = pivots[0]
            if first["direction"] < 0 and current_price < first["price"]:
                first["index"] = current_index
                first["price"] = current_price
            elif first["direction"] > 0 and current_price < first["price"]:
                pivots.insert(0, {"index": current_index, "price": current_price, "direction": -1})
        else:
            pivots.insert(0, {"index": current_index, "price": current_price, "direction": -1})
        if len(pivots) > 1 and active_channel is None and channel_mode != "None":
            active_channel = create_channel(current_index, current_price, pivots[1], -1.0, index)
            channel_count = 0

    if len(pivots) > 200:
        pivots = pivots[:200]

    if active_channel is not None:
        lower_boundary = active_channel["prices"][break_minimum]
        upper_boundary = active_channel["prices"][break_maximum]
        channel_low = min(lower_boundary, upper_boundary)
        channel_high = max(lower_boundary, upper_boundary)
        if channel_low < close[index] < channel_high:
            channel_count = 0
        elif close[index] > channel_high or close[index] < channel_low:
            channel_count += 1
        if channel_count > break_count:
            if close[index] > channel_high:
                break_up[index] = True
            else:
                break_down[index] = True
            active_channel = None
        else:
            active_channel["end"] = int(index + 8)

layers = []
channel_colors = {
    -0.382: "rgba(8,153,129,0.39)",
    0.0: "rgba(8,153,129,0.39)",
    0.236: "rgba(8,153,129,0.50)",
    0.382: "rgba(8,153,129,0.50)",
    0.5: "rgba(30,66,176,0.44)",
    0.618: "rgba(242,54,69,0.50)",
    0.764: "rgba(242,54,69,0.50)",
    1.0: "rgba(242,54,69,0.39)",
    1.382: "rgba(242,54,69,0.39)",
}
ordered_levels = [-0.382, 0.0, 0.236, 0.382, 0.5, 0.618, 0.764, 1.0, 1.382]

if active_channel is not None:
    for level_value in ordered_levels:
        layers.append(
            line_layer(
                active_channel["start"],
                active_channel["end"],
                active_channel["prices"][level_value],
                channel_colors[level_value],
                1,
                False,
            )
        )
    diagonal = {
        "type": "line",
        "startIndex": int(active_channel["previous_index"]),
        "endIndex": int(active_channel["start"]),
        "y1": float(active_channel["previous_price"]),
        "y2": float(active_channel["current_price"]),
        "color": "#B0B0B0",
        "lineWidth": 1,
        "dashed": True,
    }
    layers.append(diagonal)
    boundary_end_time = time_for_index(active_channel["end"])
    for boundary_value in (break_minimum, break_maximum):
        layers.append(
            {
                "type": "label",
                "timestamp": boundary_end_time,
                "price": float(active_channel["prices"][boundary_value]),
                "text": "●",
                "color": "#808080",
                "textColor": "#FFFFFF",
                "side": "center",
            }
        )
    if channel_mode == "Channel + Shadows":
        shadow_pairs = [
            (-0.382, 0.0, "rgba(8,153,129,0.14)"),
            (0.236, 0.382, "rgba(8,153,129,0.14)"),
            (0.382, 0.5, "rgba(30,66,176,0.11)"),
            (0.5, 0.618, "rgba(30,66,176,0.11)"),
            (0.618, 0.764, "rgba(242,54,69,0.14)"),
            (1.0, 1.382, "rgba(242,54,69,0.14)"),
        ]
        for first_level, second_level, color in shadow_pairs:
            layers.append(
                zone_layer(
                    active_channel["start"],
                    active_channel["end"],
                    active_channel["prices"][first_level],
                    active_channel["prices"][second_level],
                    color,
                )
            )

if show_latest_fibonacci and len(pivots) >= 2:
    current_pivot = pivots[0]
    previous_pivot = pivots[1]
    difference = abs(current_pivot["price"] - previous_pivot["price"])
    signed_difference = -difference if current_pivot["price"] < previous_pivot["price"] else difference
    latest_prices = {
        -0.382: float(current_pivot["price"] + signed_difference * 0.382),
        0.0: float(current_pivot["price"]),
        0.236: float(current_pivot["price"] - signed_difference * 0.236),
        0.382: float(current_pivot["price"] - signed_difference * 0.382),
        0.5: float(current_pivot["price"] - signed_difference * 0.5),
        0.618: float(current_pivot["price"] - signed_difference * 0.618),
        0.764: float(current_pivot["price"] - signed_difference * 0.764),
        1.0: float(current_pivot["price"] - signed_difference),
        1.382: float(previous_pivot["price"] - signed_difference * 0.382),
    }
    latest_end = n - 1 + 8
    for level_value in ordered_levels:
        layers.append(
            line_layer(
                current_pivot["index"],
                latest_end,
                latest_prices[level_value],
                "#2962FF" if level_value == 0.5 else channel_colors[level_value],
                1,
                False,
            )
        )
    layers.append(
        {
            "type": "line",
            "startIndex": int(current_pivot["index"]),
            "endIndex": int(previous_pivot["index"]),
            "y1": float(current_pivot["price"]),
            "y2": float(previous_pivot["price"]),
            "color": "#B0B0B0",
            "lineWidth": 1,
            "dashed": True,
        }
    )
    latest_fill_pairs = [
        (-0.382, 0.0, "rgba(8,153,129,0.08)"),
        (0.236, 0.382, "rgba(8,153,129,0.08)"),
        (0.618, 0.764, "rgba(242,54,69,0.08)"),
        (1.0, 1.382, "rgba(242,54,69,0.08)"),
    ]
    for first_level, second_level, color in latest_fill_pairs:
        layers.append(
            zone_layer(
                current_pivot["index"],
                latest_end,
                latest_prices[first_level],
                latest_prices[second_level],
                color,
            )
        )
    layers.append(
        {
            "type": "label",
            "index": int(current_pivot["index"]),
            "price": float(current_pivot["price"]),
            "text": "0",
            "color": "#808080",
            "textColor": "#FFFFFF",
            "side": "below" if previous_pivot["price"] > current_pivot["price"] else "above",
        }
    )

break_up_data = [None for _ in range(n)]
break_down_data = [None for _ in range(n)]
for index in range(n):
    if show_breaks and break_up[index]:
        break_up_data[index] = {"value": float(high[index]), "text": "Break Up", "color": "#089981"}
    if show_breaks and break_down[index]:
        break_down_data[index] = {"value": float(low[index]), "text": "Break Down", "color": "#F23645"}

plots = [
    {"name": "Price Reference", "title": False, "data": [float(value) for value in close], "color": "rgba(255,255,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True}
]
signals = [
    {"type": "buy", "text": "Break Up", "name": "Break Up", "data": break_up_data, "color": "#089981", "renderMode": "events"},
    {"type": "sell", "text": "Break Down", "name": "Break Down", "data": break_down_data, "color": "#F23645", "renderMode": "events"},
]

signals = [signal for signal in signals if any(item is not None for item in signal["data"])]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers}
