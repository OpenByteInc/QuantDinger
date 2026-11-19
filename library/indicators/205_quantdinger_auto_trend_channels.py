# ============================================================
# QuantDinger Auto Trend Channels
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Auto Trend Channels"
my_indicator_description = (
    "Builds volatility-qualified three-pivot trend channels, accepts candidates only after a "
    "confirmed close inside, and tracks break, retouch, return, expiry, and retained history states."
)

# @param swing_length int 8 Swing length range=2:30:1
# @param longer_channel bool false Enable longer swing channel
# @param longer_multiple int 3 Longer swing multiple range=2:4:1
# @param pivot_pool int 10 Pivots searched range=3:12:1
# @param horizon int 600 Search and age horizon range=100:2000:1
# @param volatility_window int 14 Simple true range window range=5:100:1
# @param containment_allowance float 0.1 Containment allowance in ranges range=0.0:0.5:0.05
# @param minimum_width float 0.5 Minimum width in ranges range=0.1:5.0:0.1
# @param maximum_width float 8.0 Maximum width in ranges range=1.0:20.0:0.5
# @param close_allowance float 0.0 Close allowance in ranges range=0.0:1.0:0.05
# @param return_watch int 20 Return watch bars range=1:50:1
# @param retained_channels int 3 Retained finished channels range=0:10:1
# @param show_midpoint bool true Show midpoint
# @param show_quarters bool false Show quarter lines
# @param show_labels bool false Show channel labels
# @param show_anchors bool false Show A B C anchors
# @param show_forming bool false Show best forming candidate
# @param show_table bool true Show corner table
# @param panel_mode str Off Status panel values=Full,Disclosure_Only,Off
# @param panel_position str Top_Right Panel position values=Top_Right,Top_Left,Bottom_Right,Bottom_Left
# @param boundary_width int 2 Boundary width range=1:4:1
# @param show_fill bool true Fill active channels
# @param fill_transparency int 80 Fill transparency range=0:100:1
# @param theme str Signature Color theme values=Signature,High_Contrast,Paper,Muted,Colourblind_Safe,Monochrome,Warm,Cool,Custom
# @param rising_color str #26A69A Custom rising color
# @param falling_color str #EF5350 Custom falling color
# @param history_color str #9AA0A6 Custom history color
# @param label_position str On_Line Label position values=On_Line,Right_Of_Line
# @param label_size str Tiny Label size values=Tiny,Small,Normal
# @param formation_alert bool true Emit formation events
# @param outside_alert bool true Emit close-outside events
# @param retest_alert bool false Emit boundary-retouch events
# @param return_alert bool false Emit return-inside events

swing_length = min(30, max(2, int(params.get("swing_length", 8))))
longer_channel = bool(params.get("longer_channel", False))
longer_multiple = min(4, max(2, int(params.get("longer_multiple", 3))))
pivot_pool = min(12, max(3, int(params.get("pivot_pool", 10))))
horizon = min(2000, max(100, int(params.get("horizon", 600))))
volatility_window = min(100, max(5, int(params.get("volatility_window", 14))))
containment_allowance = min(0.5, max(0.0, float(params.get("containment_allowance", 0.1))))
minimum_width = min(5.0, max(0.1, float(params.get("minimum_width", 0.5))))
maximum_width = min(20.0, max(1.0, float(params.get("maximum_width", 8.0))))
close_allowance = min(1.0, max(0.0, float(params.get("close_allowance", 0.0))))
return_watch = min(50, max(1, int(params.get("return_watch", 20))))
retained_channels = min(10, max(0, int(params.get("retained_channels", 3))))
show_midpoint = bool(params.get("show_midpoint", True))
show_quarters = bool(params.get("show_quarters", False))
show_labels = bool(params.get("show_labels", False))
show_anchors = bool(params.get("show_anchors", False))
show_forming = bool(params.get("show_forming", False))
show_table = bool(params.get("show_table", True))
panel_mode = str(params.get("panel_mode", "Off"))
if panel_mode not in ("Full", "Disclosure_Only", "Off"):
    panel_mode = "Off"
panel_position = str(params.get("panel_position", "Top_Right"))
boundary_width = min(4, max(1, int(params.get("boundary_width", 2))))
show_fill = bool(params.get("show_fill", True))
fill_transparency = min(100, max(0, int(params.get("fill_transparency", 80))))
theme = str(params.get("theme", "Signature"))
rising_color_input = str(params.get("rising_color", "#26A69A"))
falling_color_input = str(params.get("falling_color", "#EF5350"))
history_color_input = str(params.get("history_color", "#9AA0A6"))
label_position = str(params.get("label_position", "On_Line"))
label_size = str(params.get("label_size", "Tiny"))
formation_alert = bool(params.get("formation_alert", True))
outside_alert = bool(params.get("outside_alert", True))
retest_alert = bool(params.get("retest_alert", False))
return_alert = bool(params.get("return_alert", False))

PENDING = 0
ACTIVE = 1
WATCH = 2
RETURNED = 3
ENDED = 4
AGED = 5
FORMED = 0
ABOVE = 1
BELOW = 2
RETURN = 3
RETEST = 4
PENDING_CAP = 32
PENDING_WAIT = 20

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
raw_time = df["time"].astype(float).to_numpy(dtype=float) if "time" in df.columns else np.arange(len(df), dtype=float)
n = len(df)


def rgba(color, alpha):
    value = str(color).strip()
    if len(value) == 7 and value.startswith("#"):
        try:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
            return f"rgba({red},{green},{blue},{min(1.0, max(0.0, float(alpha))):.3f})"
        except ValueError:
            pass
    return value


theme_colors = {
    "Signature": ("#26A69A", "#EF5350", "#9AA0A6"),
    "High_Contrast": ("#00E676", "#FF1744", "#FFFFFF"),
    "Paper": ("#00695C", "#B71C1C", "#546E7A"),
    "Muted": ("#4C9A8F", "#B0645F", "#78808A"),
    "Colourblind_Safe": ("#0072B2", "#D55E00", "#999999"),
    "Monochrome": ("#FFFFFF", "#9E9E9E", "#5F6A6A"),
    "Warm": ("#F9A825", "#C62828", "#8D6E63"),
    "Cool": ("#26C6DA", "#5C6BC0", "#78909C"),
    "Custom": (rising_color_input, falling_color_input, history_color_input),
}
rising_color, falling_color, history_color = theme_colors.get(theme, theme_colors["Signature"])


def lower_at(channel, index):
    baseline = channel["a"]["price"] + channel["slope"] * (index - channel["a"]["index"])
    return baseline if channel["direction"] == 1 else baseline - channel["width"]


def upper_at(channel, index):
    baseline = channel["a"]["price"] + channel["slope"] * (index - channel["a"]["index"])
    return baseline + channel["width"] if channel["direction"] == 1 else baseline


def is_inside(channel, bar, buffer_value=0.0):
    return lower_at(channel, bar["index"]) - buffer_value <= bar["close"] <= upper_at(channel, bar["index"]) + buffer_value


def channel_key(direction, a, b, c):
    return f"{direction}:{a['stamp']}:{b['stamp']}:{c['stamp']}"


def strict_pivot(bars, now, strength):
    center_index = now - strength
    if center_index - strength < 0:
        return None, None
    center = bars[center_index]
    if not center["valid"]:
        return None, None
    high_pivot = True
    low_pivot = True
    for index in range(center_index - strength, now + 1):
        neighbor = bars[index]
        if not neighbor["valid"]:
            high_pivot = False
            low_pivot = False
            break
        if index != center_index:
            high_pivot = high_pivot and center["high"] > neighbor["high"]
            low_pivot = low_pivot and center["low"] < neighbor["low"]
    if high_pivot == low_pivot:
        return None, None
    pivot = {"index": center_index, "stamp": center["stamp"], "price": center["high"] if high_pivot else center["low"]}
    return (pivot, None) if high_pivot else (None, pivot)


def construct_channel(bars, a, c, opposites, direction, layer, strength, now, volatility):
    if not np.isfinite(volatility) or volatility <= 0.0:
        return None
    if c["index"] - a["index"] < 2 * strength or now - a["index"] > horizon or direction * (c["price"] - a["price"]) <= 0.0:
        return None
    slope = (c["price"] - a["price"]) / (c["index"] - a["index"])
    opposite = None
    width = 0.0
    for pivot in opposites:
        if a["index"] < pivot["index"] < c["index"]:
            residual = direction * (pivot["price"] - (a["price"] + slope * (pivot["index"] - a["index"])))
            if residual > width:
                width = residual
                opposite = pivot
    if opposite is None or width < minimum_width * volatility or width > maximum_width * volatility:
        return None
    channel = {
        "a": a, "b": opposite, "c": c, "direction": direction, "layer": layer,
        "slope": float(slope), "width": float(width), "volatility": float(volatility),
        "containment": containment_allowance * volatility, "allowance": close_allowance * volatility,
        "constructed": now, "key": channel_key(direction, a, opposite, c),
        "available": -1, "state": PENDING, "break_bar": -1, "break_side": 0,
        "return_bar": -1, "retest_bar": -1, "end_bar": -1, "retired": -1,
    }
    for index in range(a["index"], c["index"] + 1):
        bar = bars[index]
        if not bar["valid"] or bar["high"] > upper_at(channel, index) + channel["containment"] or bar["low"] < lower_at(channel, index) - channel["containment"]:
            return None
    return channel


def active_channel(records, layer):
    for channel in records:
        if channel["layer"] == layer and channel["state"] == ACTIVE:
            return channel
    return None


def better_candidate(candidate, incumbent, bar):
    candidate_distance = abs(bar["close"] - (lower_at(candidate, bar["index"]) + candidate["width"] * 0.5)) / candidate["width"]
    incumbent_distance = abs(bar["close"] - (lower_at(incumbent, bar["index"]) + incumbent["width"] * 0.5)) / incumbent["width"]
    if candidate["c"]["index"] != incumbent["c"]["index"]:
        return candidate["c"]["index"] > incumbent["c"]["index"]
    if abs(candidate_distance - incumbent_distance) > 1e-12:
        return candidate_distance < incumbent_distance
    candidate_span = candidate["c"]["index"] - candidate["a"]["index"]
    incumbent_span = incumbent["c"]["index"] - incumbent["a"]["index"]
    if candidate_span != incumbent_span:
        return candidate_span > incumbent_span
    if candidate["b"]["index"] != incumbent["b"]["index"]:
        return candidate["b"]["index"] < incumbent["b"]["index"]
    return candidate["direction"] > incumbent["direction"]


bars = []
true_ranges = []
layers_state = [
    {"highs": [], "lows": [], "pending": []},
    {"highs": [], "lows": [], "pending": []},
]
records = []
used_keys = {}
event_data = [[np.full(n, np.nan, dtype=float) for _ in range(5)] for _ in range(2)]
volatility_history = np.full(n, np.nan, dtype=float)


def record_event(kind, channel, bar):
    if kind == FORMED:
        price = bar["close"]
    elif kind == ABOVE:
        price = upper_at(channel, bar["index"])
    elif kind == BELOW:
        price = lower_at(channel, bar["index"])
    elif kind == RETURN:
        price = bar["close"]
    else:
        price = upper_at(channel, bar["index"]) if channel["break_side"] == 1 else lower_at(channel, bar["index"])
    event_data[channel["layer"]][kind][bar["index"]] = float(price)


def advance_channel(channel, bar):
    if bar["index"] - channel["a"]["index"] > horizon:
        channel["state"] = AGED
        channel["end_bar"] = bar["index"]
        if channel["retired"] < 0:
            channel["retired"] = bar["index"]
    elif channel["state"] == ACTIVE and bar["index"] > channel["available"] and bar["valid"]:
        side = 1 if bar["close"] > upper_at(channel, bar["index"]) + channel["allowance"] else -1 if bar["close"] < lower_at(channel, bar["index"]) - channel["allowance"] else 0
        if side:
            channel["state"] = WATCH
            channel["break_bar"] = bar["index"]
            channel["break_side"] = side
            channel["retired"] = bar["index"]
            record_event(ABOVE if side == 1 else BELOW, channel, bar)
    elif channel["state"] == WATCH and bar["index"] > channel["break_bar"]:
        if bar["index"] <= channel["break_bar"] + return_watch and bar["valid"] and is_inside(channel, bar):
            channel["state"] = RETURNED
            channel["return_bar"] = bar["index"]
            channel["end_bar"] = bar["index"]
            record_event(RETURN, channel, bar)
        else:
            if channel["retest_bar"] < 0 and bar["valid"]:
                reached = bar["low"] <= upper_at(channel, bar["index"]) if channel["break_side"] == 1 else bar["high"] >= lower_at(channel, bar["index"])
                if reached:
                    channel["retest_bar"] = bar["index"]
                    record_event(RETEST, channel, bar)
            if bar["index"] >= channel["break_bar"] + return_watch:
                channel["state"] = ENDED
                channel["end_bar"] = bar["index"]


def ingest_layer(layer_data, layer, strength, bar, volatility):
    high_pivot, low_pivot = strict_pivot(bars, bar["index"], strength)
    if high_pivot is not None:
        layer_data["highs"].append(high_pivot)
    if low_pivot is not None:
        layer_data["lows"].append(low_pivot)
    for pool in (layer_data["highs"], layer_data["lows"]):
        while pool and (bar["index"] - pool[0]["index"] > horizon or len(pool) > pivot_pool):
            pool.pop(0)

    for pivot, same_pool, opposite_pool, direction in (
        (high_pivot, layer_data["highs"], layer_data["lows"], -1),
        (low_pivot, layer_data["lows"], layer_data["highs"], 1),
    ):
        if pivot is not None and len(same_pool) > 1:
            for anchor in same_pool[:-1]:
                candidate = construct_channel(bars, anchor, pivot, opposite_pool, direction, layer, strength, bar["index"], volatility)
                if candidate is not None:
                    layer_data["pending"].append(candidate)
    layer_data["pending"] = [
        channel for channel in layer_data["pending"]
        if bar["index"] - channel["a"]["index"] <= horizon and bar["valid"] and bar["index"] - channel["constructed"] <= PENDING_WAIT
    ][-PENDING_CAP:]

    if active_channel(records, layer) is None and bar["valid"]:
        best = None
        best_index = -1
        for index, candidate in enumerate(layer_data["pending"]):
            if candidate["key"] not in used_keys and is_inside(candidate, bar):
                if best is None or better_candidate(candidate, best, bar):
                    best = candidate
                    best_index = index
        if best is not None:
            layer_data["pending"].pop(best_index)
            best["state"] = ACTIVE
            best["available"] = bar["index"]
            records.append(best)
            used_keys[best["key"]] = best["a"]["index"]
            record_event(FORMED, best, bar)


longer_enabled = longer_channel and horizon >= 3 * swing_length * longer_multiple
configuration_valid = minimum_width <= maximum_width
for index in range(n):
    valid = np.isfinite(high[index]) and np.isfinite(low[index]) and np.isfinite(close[index]) and low[index] <= close[index] <= high[index]
    bar = {"index": index, "stamp": float(raw_time[index]), "high": float(high[index]), "low": float(low[index]), "close": float(close[index]), "valid": bool(valid)}
    bars.append(bar)
    if valid:
        if index == 0 or not bars[index - 1]["valid"]:
            true_range = high[index] - low[index]
        else:
            true_range = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
    else:
        true_range = np.nan
    true_ranges.append(float(true_range))
    if len(true_ranges) >= volatility_window:
        window = np.asarray(true_ranges[-volatility_window:], dtype=float)
        if np.all(np.isfinite(window)):
            volatility_history[index] = float(np.mean(window))

    if not configuration_valid:
        continue
    used_keys = {key: anchor for key, anchor in used_keys.items() if index - anchor <= horizon}
    for channel in records:
        advance_channel(channel, bar)
    records = [channel for channel in records if channel["state"] != AGED]
    ingest_layer(layers_state[0], 0, swing_length, bar, volatility_history[index])
    if longer_enabled:
        ingest_layer(layers_state[1], 1, swing_length * longer_multiple, bar, volatility_history[index])
    terminal = [channel for channel in records if channel["state"] in (RETURNED, ENDED)]
    while len(terminal) > 10:
        oldest = min(terminal, key=lambda channel: (channel["retired"], channel["available"], channel["layer"]))
        records.remove(oldest)
        terminal.remove(oldest)


active_channels = [channel for channel in records if channel["state"] == ACTIVE]
non_active = [channel for channel in records if channel["state"] != ACTIVE]
non_active.sort(key=lambda channel: (channel["retired"], channel["available"], channel["layer"]), reverse=True)
displayed_channels = active_channels + non_active[:retained_channels]


def state_name(channel):
    if channel["state"] == ACTIVE:
        return "Active"
    if channel["state"] == WATCH:
        return "Watching above" if channel["break_side"] == 1 else "Watching below"
    if channel["state"] == RETURNED:
        return "Returned inside"
    return "Watch ended"


def channel_tint(channel):
    if channel["state"] == ACTIVE:
        selected = rising_color if channel["direction"] == 1 else falling_color
    else:
        selected = history_color
    return rgba(selected, 0.85) if channel["layer"] == 1 else selected


layers = []
plots = [
    {"name": "Runtime Anchor", "title": False, "data": [float(value) for value in close], "color": "rgba(0,0,0,0)", "opacity": 0.0, "type": "line", "lineWidth": 1, "overlay": True},
]
font_size = 9 if label_size == "Tiny" else 11 if label_size == "Small" else 13
for channel_index, channel in enumerate(displayed_channels):
    running = channel["state"] in (ACTIVE, WATCH)
    right = n - 1 if running else max(channel["a"]["index"], channel["end_bar"])
    tint = channel_tint(channel)
    weight = boundary_width if channel["layer"] == 0 else min(4, boundary_width + 1)
    dashed = channel["state"] != ACTIVE
    lower_left = lower_at(channel, channel["a"]["index"])
    lower_right = lower_at(channel, right)
    upper_left = upper_at(channel, channel["a"]["index"])
    upper_right = upper_at(channel, right)
    for name, left_price, right_price, color, line_weight, line_dashed in (
        ("Lower", lower_left, lower_right, tint, weight, dashed),
        ("Upper", upper_left, upper_right, tint, weight, dashed),
        ("Mid", lower_left + channel["width"] * 0.5, lower_right + channel["width"] * 0.5, rgba(tint, 0.80), 1, True),
        ("Quarter", lower_left + channel["width"] * 0.25, lower_right + channel["width"] * 0.25, rgba(tint, 0.65), 1, True),
        ("Three Quarter", lower_left + channel["width"] * 0.75, lower_right + channel["width"] * 0.75, rgba(tint, 0.65), 1, True),
    ):
        if name == "Mid" and not show_midpoint:
            continue
        if name in ("Quarter", "Three Quarter") and not show_quarters:
            continue
        layers.append({
            "type": "line", "startIndex": channel["a"]["index"], "endIndex": right,
            "startPrice": float(left_price), "endPrice": float(right_price),
            "color": color, "lineWidth": line_weight, "dashed": line_dashed,
        })
    layers.append({
        "type": "line", "startIndex": channel["available"], "endIndex": channel["available"],
        "startPrice": float(lower_at(channel, channel["available"])), "endPrice": float(upper_at(channel, channel["available"])),
        "color": tint, "lineWidth": 1, "dashed": True,
    })
    if channel["state"] == ACTIVE and show_fill:
        cloud = [None for _ in range(n)]
        fill_opacity = min(0.30, max(0.02, (100.0 - fill_transparency) / 100.0))
        for index in range(channel["a"]["index"], n):
            cloud[index] = {"upper": float(upper_at(channel, index)), "lower": float(lower_at(channel, index)), "color": rgba(tint, fill_opacity)}
        plots.append({"name": f"Active Channel Fill {channel_index + 1}", "title": False, "data": cloud, "color": tint, "type": "cloud", "opacity": fill_opacity, "overlay": True})
    if show_labels:
        clearance = channel["volatility"] * 0.35
        layers.append({
            "type": "label", "index": channel["available"], "price": float(upper_at(channel, channel["available"]) + clearance),
            "text": "Known", "color": tint, "textColor": tint, "side": "above", "fontSize": font_size,
        })
        direction_text = "Rising" if channel["direction"] == 1 else "Falling"
        endpoint_text = ("" if channel["layer"] == 0 else "Longer swing · ") + direction_text + "\n" + state_name(channel)
        endpoint_index = right
        layers.append({
            "type": "label", "index": endpoint_index, "price": float(lower_at(channel, endpoint_index) + channel["width"] * 0.5),
            "text": endpoint_text, "color": tint, "textColor": tint, "side": "center", "fontSize": font_size,
        })
        if show_anchors:
            for name, pivot, side in (("A", channel["a"], "below"), ("B", channel["b"], "above"), ("C", channel["c"], "below")):
                layers.append({"type": "label", "index": pivot["index"], "price": float(pivot["price"]), "text": name, "color": tint, "textColor": tint, "side": side, "fontSize": font_size})
        for field, text_value in (("break_bar", "Close above" if channel["break_side"] == 1 else "Close below"), ("retest_bar", "Boundary retouch"), ("return_bar", "Returned inside")):
            event_index = channel[field]
            if event_index >= 0:
                side = channel["break_side"] or 1
                event_price = max(upper_at(channel, event_index), high[event_index]) + clearance if side == 1 else min(lower_at(channel, event_index), low[event_index]) - clearance
                layers.append({"type": "label", "index": event_index, "price": float(event_price), "text": text_value, "color": tint, "textColor": tint, "side": "above" if side == 1 else "below", "fontSize": font_size})

if show_forming:
    for layer_index in range(2 if longer_enabled else 1):
        if active_channel(records, layer_index) is not None:
            continue
        candidates = [channel for channel in layers_state[layer_index]["pending"] if channel["key"] not in used_keys]
        if not candidates or not bars:
            continue
        best = candidates[0]
        for candidate in candidates[1:]:
            if better_candidate(candidate, best, bars[-1]):
                best = candidate
        forming_color = rgba(history_color, 0.60)
        for boundary in ("lower", "upper"):
            start_price = lower_at(best, best["a"]["index"]) if boundary == "lower" else upper_at(best, best["a"]["index"])
            end_price = lower_at(best, n - 1) if boundary == "lower" else upper_at(best, n - 1)
            layers.append({"type": "line", "startIndex": best["a"]["index"], "endIndex": n - 1, "startPrice": float(start_price), "endPrice": float(end_price), "color": forming_color, "lineWidth": 1, "dashed": True})
        if show_labels:
            layers.append({"type": "label", "index": n - 1, "price": float(max(upper_at(best, n - 1), high[-1]) + best["volatility"] * 0.35), "text": "Forming · not accepted", "color": forming_color, "textColor": forming_color, "side": "above", "fontSize": font_size})


def event_values(layer, kind, enabled):
    return [float(value) if enabled and np.isfinite(value) else None for value in event_data[layer][kind]]


signals = []
signal_specs = [
    (FORMED, "Channel Formed", formation_alert, "buy"),
    (ABOVE, "Close Above", outside_alert, "buy"),
    (BELOW, "Close Below", outside_alert, "sell"),
    (RETURN, "Returned Inside", return_alert, "buy"),
    (RETEST, "Boundary Retouch", retest_alert, "sell"),
]
for layer_index, layer_name in ((0, "Swing"), (1, "Longer Swing")):
    if layer_index == 1 and not longer_enabled:
        continue
    for kind, name, enabled, signal_type in signal_specs:
        signals.append({
            "type": signal_type, "name": f"{layer_name} {name}", "text": name,
            "color": rising_color if signal_type == "buy" else falling_color,
            "renderMode": "events", "data": event_values(layer_index, kind, enabled),
        })

position_map = {"Top_Right": "top-right", "Top_Left": "top-left", "Bottom_Right": "bottom-right", "Bottom_Left": "bottom-left"}
tables = []
if show_table:
    swing_active = active_channel(records, 0)
    longer_active = active_channel(records, 1) if longer_enabled else None
    swing_watches = sum(1 for channel in records if channel["layer"] == 0 and channel["state"] == WATCH)
    longer_watches = sum(1 for channel in records if channel["layer"] == 1 and channel["state"] == WATCH)
    rows = []
    if panel_mode == "Full":
        def channel_rows(name, channel, watches, inactive_text):
            if channel is None:
                return [[name, inactive_text], ["Upper", ""], ["Mid", ""], ["Lower", ""], ["Watches", str(watches)]]
            return [
                [name, ("Rising" if channel["direction"] == 1 else "Falling") + " · Active"],
                ["Upper", f"{upper_at(channel, n - 1):.2f}"],
                ["Mid", f"{lower_at(channel, n - 1) + channel['width'] * 0.5:.2f}"],
                ["Lower", f"{lower_at(channel, n - 1):.2f}"],
                ["Watches", str(watches)],
            ]
        rows.extend(channel_rows("Swing", swing_active, swing_watches, "No active channel"))
        rows.extend(channel_rows("Longer swing", longer_active, longer_watches, "No active channel" if longer_enabled else "Off"))
        rows.append(["Drawn", f"{len(displayed_channels)} of {retained_channels + 1 + (1 if longer_enabled else 0)} sets"])
    notice = "Linear-price geometry · confirmed-pivot delay"
    notice += "\nAnchor history, not a forecast"
    if not configuration_valid:
        notice += "\nInvalid settings: minimum width exceeds maximum width."
    elif longer_channel and not longer_enabled:
        notice += "\nLonger swing disabled: horizon is too short."
    elif not active_channels:
        enough_pivots = len(layers_state[0]["highs"]) >= 2 or len(layers_state[0]["lows"]) >= 2
        notice += "\nNo active valid channel." if enough_pivots else "\nInsufficient confirmed pivots."
    rows.append(["Notice", notice])
    tables.append({
        "title": "AUTO TREND CHANNELS" if panel_mode != "Off" else "CHANNEL DISCLOSURE",
        "position": position_map.get(panel_position, "top-right"),
        "compact": True,
        "borderColor": history_color,
        "accentColor": rising_color,
        "rows": rows,
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": tables,
}
