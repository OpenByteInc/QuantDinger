# ============================================================
# QuantDinger Algo Market Structure
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Algo Market Structure"
my_indicator_description = (
    "Tracks candle-reversal swing structure on the chart, 4H, or daily timeframe and renders "
    "HH/HL/LH/LL pivots, internal and target liquidity, BOS, efficiency levels, extremes, "
    "market-structure-update arrows, valid trading ranges, candle colors, and a status table."
)

indicator_timeframes = ["4H", "1D"]

# @param structure_timeframe str Chart Structure timeframe values=Chart,4H,1D
# @param minimum_timeframe str Chart Minimum chart timeframe values=Chart,1H,4H
# @param confirmation_bars int 1 Reversal confirmation bars range=1:5:1
# @param show_structure_labels bool false Show HH HL LH LL labels
# @param show_internal_liquidity bool true Show internal liquidity
# @param show_target_liquidity bool true Show target liquidity
# @param show_bos bool true Show break of structure
# @param show_efficiency bool true Show efficiency price action levels
# @param delete_unfilled_efficiency bool true Replace old unfilled efficiency levels
# @param show_msu bool true Show market structure update arrows
# @param show_extremes bool true Show extreme zones
# @param show_valid_ranges bool true Show valid trading ranges
# @param strict_valid_ranges bool false Require strict valid-range pattern
# @param delete_filled_ranges bool true Remove invalidated ranges
# @param color_price_bars bool true Color host candles by structure
# @param show_dashboard bool true Show structure dashboard
# @param dashboard_position str bottom-right Dashboard position values=top-left,top-right,bottom-left,bottom-right
# @param max_displayed_levels int 28 Maximum rendered line segments range=8:80:1
# @param max_displayed_zones int 4 Maximum rendered zones range=2:30:1
# @param structure_top_color str #4CAF50 Structure high marker color
# @param structure_bottom_color str #4CAF50 Structure low marker color
# @param bullish_color str #00E676 Bullish structure color
# @param bearish_color str #FF1744 Bearish structure color
# @param internal_liquidity_color str #FF9800 Internal liquidity color
# @param target_liquidity_color str #00E676 Target liquidity color
# @param efficiency_color str #2979FF Efficiency level color
# @param extreme_color str #9E9E9E Extreme zone color
# @param bullish_range_color str #00C853 Bullish valid-range color
# @param bearish_range_color str #F44336 Bearish valid-range color
# @param bullish_up_bar_color str #6BC2B3 Bullish up-candle color
# @param bullish_down_bar_color str #358D83 Bullish down-candle color
# @param bearish_up_bar_color str #F7868F Bearish up-candle color
# @param bearish_down_bar_color str #C1515F Bearish down-candle color

structure_timeframe = str(params.get("structure_timeframe", "Chart"))
if structure_timeframe not in ("Chart", "4H", "1D"):
    structure_timeframe = "Chart"
minimum_timeframe = str(params.get("minimum_timeframe", "Chart"))
if minimum_timeframe not in ("Chart", "1H", "4H"):
    minimum_timeframe = "Chart"
confirmation_bars = min(5, max(1, int(params.get("confirmation_bars", 1))))
show_structure_labels = bool(params.get("show_structure_labels", False))
show_internal_liquidity = bool(params.get("show_internal_liquidity", True))
show_target_liquidity = bool(params.get("show_target_liquidity", True))
show_bos = bool(params.get("show_bos", True))
show_efficiency = bool(params.get("show_efficiency", True))
delete_unfilled_efficiency = bool(params.get("delete_unfilled_efficiency", True))
show_msu = bool(params.get("show_msu", True))
show_extremes = bool(params.get("show_extremes", True))
show_valid_ranges = bool(params.get("show_valid_ranges", True))
strict_valid_ranges = bool(params.get("strict_valid_ranges", False))
delete_filled_ranges = bool(params.get("delete_filled_ranges", True))
color_price_bars = bool(params.get("color_price_bars", True))
show_dashboard = bool(params.get("show_dashboard", True))
dashboard_position = str(params.get("dashboard_position", "bottom-right"))
if dashboard_position not in ("top-left", "top-right", "bottom-left", "bottom-right"):
    dashboard_position = "bottom-right"
max_displayed_levels = min(80, max(8, int(params.get("max_displayed_levels", 28))))
max_displayed_zones = min(30, max(2, int(params.get("max_displayed_zones", 4))))
structure_top_color = str(params.get("structure_top_color", "#4CAF50"))
structure_bottom_color = str(params.get("structure_bottom_color", "#4CAF50"))
bullish_color = str(params.get("bullish_color", "#00E676"))
bearish_color = str(params.get("bearish_color", "#FF1744"))
internal_liquidity_color = str(params.get("internal_liquidity_color", "#FF9800"))
target_liquidity_color = str(params.get("target_liquidity_color", "#00E676"))
efficiency_color = str(params.get("efficiency_color", "#2979FF"))
extreme_color = str(params.get("extreme_color", "#9E9E9E"))
bullish_range_color = str(params.get("bullish_range_color", "#00C853"))
bearish_range_color = str(params.get("bearish_range_color", "#F44336"))
bullish_up_bar_color = str(params.get("bullish_up_bar_color", "#6BC2B3"))
bullish_down_bar_color = str(params.get("bullish_down_bar_color", "#358D83"))
bearish_up_bar_color = str(params.get("bearish_up_bar_color", "#F7868F"))
bearish_down_bar_color = str(params.get("bearish_down_bar_color", "#C1515F"))

timeframe_seconds = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600,
    "8H": 28800, "12H": 43200, "1D": 86400, "1W": 604800,
}

df = df.copy()
n = len(df)

def rgba(color, alpha):
    value = str(color).strip()
    if value.startswith("#") and len(value) == 7:
        try:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
            return f"rgba({red},{green},{blue},{alpha:.3f})"
        except ValueError:
            return value
    return value

def normalized_frame(frame):
    if frame is None or len(frame) == 0:
        frame = df.copy()
    result = frame.copy().reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
    times = result["time"].to_numpy(dtype=float)
    if len(times) and float(np.nanmax(np.abs(times))) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result

base_frame = normalized_frame(df)
base_time = base_frame["time"].to_numpy(dtype=float)
base_open = base_frame["open"].to_numpy(dtype=float)
base_high = base_frame["high"].to_numpy(dtype=float)
base_low = base_frame["low"].to_numpy(dtype=float)
base_close = base_frame["close"].to_numpy(dtype=float)

runtime_context = ctx
chart_timeframe = str(runtime_context.timeframe or "1H")
if chart_timeframe not in timeframe_seconds:
    chart_timeframe = "1H"
chart_seconds = timeframe_seconds[chart_timeframe]
selected_timeframe = chart_timeframe if structure_timeframe == "Chart" else structure_timeframe
selected_seconds = timeframe_seconds.get(selected_timeframe, chart_seconds)
minimum_selected = chart_timeframe if minimum_timeframe == "Chart" else minimum_timeframe
minimum_seconds = timeframe_seconds.get(minimum_selected, chart_seconds)
timeframe_enabled = chart_seconds <= selected_seconds and chart_seconds >= minimum_seconds

if selected_timeframe == chart_timeframe:
    structure_frame = base_frame.copy()
else:
    requested = runtime_context.raw_series(selected_timeframe) if runtime_context is not None and hasattr(runtime_context, "raw_series") else None
    if requested is None or len(requested) == 0:
        structure_frame = base_frame.copy()
        selected_timeframe = chart_timeframe
        selected_seconds = chart_seconds
    else:
        structure_frame = normalized_frame(requested)

source_open = structure_frame["open"].to_numpy(dtype=float)
source_high = structure_frame["high"].to_numpy(dtype=float)
source_low = structure_frame["low"].to_numpy(dtype=float)
source_close = structure_frame["close"].to_numpy(dtype=float)
source_time = structure_frame["time"].to_numpy(dtype=float)
source_count = len(structure_frame)

source_close_time = source_time + selected_seconds
base_close_time = base_time + chart_seconds

def base_index_for_source(source_index):
    if n == 0 or source_index < 0 or source_index >= source_count:
        return -1
    target = source_close_time[source_index]
    mapped = int(np.searchsorted(base_close_time, target, side="left"))
    return mapped if mapped < n else -1

def aligned_source_indices():
    result = np.full(n, -1, dtype=int)
    for index in range(n):
        result[index] = int(np.searchsorted(source_close_time, base_close_time[index], side="right") - 1)
    return result

source_for_base = aligned_source_indices()

pivot_highs = []
pivot_lows = []
pivot_events = []
bos_events = []
msu_up = np.zeros(source_count, dtype=bool)
msu_down = np.zeros(source_count, dtype=bool)
vta_up = np.zeros(source_count, dtype=bool)
vta_down = np.zeros(source_count, dtype=bool)
direction_history = np.ones(source_count, dtype=int)
efficiency_history = np.zeros(source_count, dtype=bool)

line_segments = []
bos_segments = []
extreme_zones = []
valid_ranges = []
active_ilq = None
active_tlq = None
active_epa = None
active_extreme = None
structure_direction = 1
price_efficient = False
used_vta_keys = set()

def close_segment(segment, end_index):
    if segment is not None and segment.get("end") is None:
        segment["end"] = max(segment["start"], int(end_index))

def add_line(kind, level, start, color, style="solid", width=1):
    segment = {
        "kind": kind,
        "level": float(level),
        "start": int(start),
        "end": None,
        "color": color,
        "style": style,
        "width": int(width),
    }
    line_segments.append(segment)
    return segment

def latest_values(items, count):
    if len(items) < count:
        return None
    return [float(items[-offset - 1]["value"]) for offset in range(count)]

def register_pivot(kind, pivot_index, confirmed_index):
    target = pivot_highs if kind == "high" else pivot_lows
    value = float(source_high[pivot_index] if kind == "high" else source_low[pivot_index])
    if target and int(target[-1]["index"]) == int(pivot_index):
        return None
    prior = float(target[-1]["value"]) if target else np.nan
    label = "HH" if kind == "high" and np.isfinite(prior) and value > prior else "LH" if kind == "high" and np.isfinite(prior) else "H"
    if kind == "low":
        label = "LL" if np.isfinite(prior) and value < prior else "HL" if np.isfinite(prior) else "L"
    item = {"kind": kind, "index": int(pivot_index), "confirmed": int(confirmed_index), "value": value, "label": label}
    target.append(item)
    pivot_events.append(item)
    return item

def create_structure_levels(index, valid_high, valid_low):
    global active_ilq, active_tlq, active_epa, active_extreme, price_efficient
    if not (valid_high or valid_low):
        return
    close_segment(active_ilq, index)
    close_segment(active_tlq, index)
    if delete_unfilled_efficiency:
        close_segment(active_epa, index)
    if active_extreme is not None and active_extreme.get("end") is None:
        active_extreme["end"] = index
    price_efficient = False

    if valid_high and pivot_lows:
        latest_low = pivot_lows[-1]
        recent_lows = pivot_lows[-2:] if len(pivot_lows) >= 2 else pivot_lows[-1:]
        target_low = min(recent_lows, key=lambda item: item["value"])
        if show_internal_liquidity:
            active_ilq = add_line("ILQ", latest_low["value"], index, internal_liquidity_color, "solid", 2)
            active_ilq["side"] = "bottom"
        if show_target_liquidity:
            active_tlq = add_line("TLQ", target_low["value"], index, target_liquidity_color, "solid", 1)
            active_tlq["side"] = "bottom"
        if show_efficiency and len(pivot_highs) >= 2:
            active_epa = add_line("EPA", pivot_highs[-2]["value"], index, efficiency_color, "dashed", 1)
            active_epa["side"] = "bull"
        if show_extremes:
            pivot_index = int(target_low["index"])
            body_top = float(min(source_open[pivot_index], source_close[pivot_index]))
            zone = {
                "kind": "Bottom Extreme", "start": index, "end": None,
                "upper": max(body_top, float(target_low["value"])),
                "lower": min(body_top, float(target_low["value"])),
                "color": extreme_color, "side": "bottom",
            }
            extreme_zones.append(zone)
            active_extreme = zone

    if valid_low and pivot_highs:
        latest_high = pivot_highs[-1]
        recent_highs = pivot_highs[-2:] if len(pivot_highs) >= 2 else pivot_highs[-1:]
        target_high = max(recent_highs, key=lambda item: item["value"])
        if show_internal_liquidity:
            active_ilq = add_line("ILQ", latest_high["value"], index, internal_liquidity_color, "solid", 2)
            active_ilq["side"] = "top"
        if show_target_liquidity:
            active_tlq = add_line("TLQ", target_high["value"], index, target_liquidity_color, "solid", 1)
            active_tlq["side"] = "top"
        if show_efficiency and len(pivot_lows) >= 2:
            active_epa = add_line("EPA", pivot_lows[-2]["value"], index, efficiency_color, "dashed", 1)
            active_epa["side"] = "bear"
        if show_extremes:
            pivot_index = int(target_high["index"])
            body_bottom = float(max(source_open[pivot_index], source_close[pivot_index]))
            zone = {
                "kind": "Top Extreme", "start": index, "end": None,
                "upper": max(body_bottom, float(target_high["value"])),
                "lower": min(body_bottom, float(target_high["value"])),
                "color": extreme_color, "side": "top",
            }
            extreme_zones.append(zone)
            active_extreme = zone

if source_count:
    initial_delta = source_close[0] - source_open[0]
    leg_direction = 1 if initial_delta >= 0 else -1
else:
    leg_direction = 1
leg_start = 0
opposite_run = 0

for index in range(source_count):
    if active_ilq is not None and active_ilq.get("end") is None:
        side = active_ilq.get("side")
        if (side == "bottom" and source_low[index] < active_ilq["level"]) or (side == "top" and source_high[index] > active_ilq["level"]):
            close_segment(active_ilq, index)
            active_ilq = None

    if active_tlq is not None and active_tlq.get("end") is None:
        broke_bottom = active_tlq.get("side") == "bottom" and source_low[index] < active_tlq["level"]
        broke_top = active_tlq.get("side") == "top" and source_high[index] > active_tlq["level"]
        if broke_bottom or broke_top:
            structure_direction = -1 if broke_bottom else 1
            close_segment(active_tlq, index)
            bos_segments.append({
                "level": float(active_tlq["level"]), "start": int(active_tlq["start"]),
                "end": index, "direction": structure_direction,
            })
            bos_events.append({"index": index, "value": float(active_tlq["level"]), "direction": structure_direction})
            active_tlq = None

    if active_epa is not None and active_epa.get("end") is None:
        filled = active_epa.get("side") == "bear" and source_high[index] > active_epa["level"]
        filled = filled or (active_epa.get("side") == "bull" and source_low[index] < active_epa["level"])
        if filled:
            price_efficient = True
            close_segment(active_epa, index)
            active_epa = None

    if active_extreme is not None and active_extreme.get("end") is None:
        invalid = active_extreme.get("side") == "bottom" and source_low[index] < active_extreme["lower"]
        invalid = invalid or (active_extreme.get("side") == "top" and source_high[index] > active_extreme["upper"])
        if invalid:
            active_extreme["end"] = index
            active_extreme = None

    for zone in valid_ranges:
        if zone.get("end") is not None:
            continue
        if zone["side"] == "top":
            if source_high[index] > zone["lower"] and source_close[index] < zone["upper"]:
                vta_down[index] = True
            if delete_filled_ranges and source_close[index] > zone["upper"]:
                zone["end"] = index
        else:
            if source_low[index] < zone["upper"] and source_close[index] > zone["lower"]:
                vta_up[index] = True
            if delete_filled_ranges and source_close[index] < zone["lower"]:
                zone["end"] = index

    candle_direction = 1 if source_close[index] > source_open[index] else -1 if source_close[index] < source_open[index] else leg_direction
    if candle_direction != leg_direction:
        opposite_run += 1
    else:
        opposite_run = 0

    valid_high = False
    valid_low = False
    if opposite_run >= confirmation_bars:
        reversal_start = max(0, index - confirmation_bars + 1)
        if leg_direction > 0:
            search_end = max(leg_start, reversal_start)
            pivot_index = leg_start + int(np.argmax(source_high[leg_start:search_end + 1]))
            pivot = register_pivot("high", pivot_index, index)
            if pivot is not None and len(pivot_highs) >= 3:
                values = latest_values(pivot_highs, 3)
                valid_high = values[0] > values[1] and values[0] > values[2]
        else:
            search_end = max(leg_start, reversal_start)
            pivot_index = leg_start + int(np.argmin(source_low[leg_start:search_end + 1]))
            pivot = register_pivot("low", pivot_index, index)
            if pivot is not None and len(pivot_lows) >= 3:
                values = latest_values(pivot_lows, 3)
                valid_low = values[0] < values[1] and values[0] < values[2]
        leg_direction = candle_direction
        leg_start = max(0, reversal_start - 1)
        opposite_run = 0
        create_structure_levels(index, valid_high, valid_low)

    ph = latest_values(pivot_highs, 4)
    pl = latest_values(pivot_lows, 4)
    if ph is not None and pl is not None:
        bearish_msu = ph[0] < ph[1] and pl[0] < pl[1] and source_high[index] > ph[0] and source_high[index] < ph[1]
        bullish_msu = pl[0] > pl[1] and ph[0] > ph[1] and source_low[index] < pl[0] and source_low[index] > pl[1]
        msu_down[index] = bearish_msu
        msu_up[index] = bullish_msu

        strict_bottom = pl[1] < pl[2] and pl[0] < pl[1] and ph[0] > ph[1] and source_close[index] > ph[0]
        strict_top = ph[1] > ph[2] and ph[0] > ph[1] and pl[0] < pl[1] and source_close[index] < pl[0]
        bottom_condition = strict_bottom if strict_valid_ranges else ph[0] > ph[1] and pl[0] < pl[1] and source_close[index] > ph[0]
        top_condition = strict_top if strict_valid_ranges else pl[0] < pl[1] and ph[0] > ph[1] and source_close[index] < pl[0]
        if show_valid_ranges and bottom_condition:
            key = ("bottom", int(pivot_highs[-1]["index"]))
            if key not in used_vta_keys:
                used_vta_keys.add(key)
                valid_ranges.append({
                    "kind": "Bullish Valid Range", "side": "bottom", "start": index, "end": None,
                    "upper": float(ph[0]), "lower": float(pl[0]), "color": bullish_range_color,
                })
        if show_valid_ranges and top_condition:
            key = ("top", int(pivot_lows[-1]["index"]))
            if key not in used_vta_keys:
                used_vta_keys.add(key)
                valid_ranges.append({
                    "kind": "Bearish Valid Range", "side": "top", "start": index, "end": None,
                    "upper": float(ph[0]), "lower": float(pl[0]), "color": bearish_range_color,
                })

    direction_history[index] = structure_direction
    efficiency_history[index] = price_efficient

for segment in line_segments:
    if segment.get("end") is None:
        segment["end"] = max(0, source_count - 1)
for zone in extreme_zones + valid_ranges:
    if zone.get("end") is None:
        zone["end"] = max(0, source_count - 1)

def mapped_range(start, end):
    left = base_index_for_source(int(start))
    right = base_index_for_source(int(end))
    if left < 0:
        return None
    if right < 0:
        right = n - 1
    return left, max(left, right)

def numeric_line(level, start, end):
    values = np.full(n, np.nan, dtype=float)
    mapped = mapped_range(start, end)
    if mapped is not None:
        left, right = mapped
        values[left:right + 1] = float(level)
    return [float(value) if np.isfinite(value) else None for value in values]

def cloud_data(zone):
    data = [None for _ in range(n)]
    mapped = mapped_range(zone["start"], zone["end"])
    if mapped is not None:
        left, right = mapped
        for index in range(left, right + 1):
            data[index] = {
                "upper": float(max(zone["upper"], zone["lower"])),
                "lower": float(min(zone["upper"], zone["lower"])),
                "color": rgba(zone["color"], 0.14),
            }
    return data

plots = [{
    "name": "Structure Price Anchor",
    "title": False,
    "data": [float(value) for value in base_close],
    "color": "rgba(0,0,0,0)",
    "type": "line",
    "lineWidth": 1,
    "overlay": True,
}]

visible_lines = sorted(line_segments, key=lambda item: item["start"])[-max_displayed_levels:]
for display_index, segment in enumerate(visible_lines, start=1):
    plots.append({
        "name": f"{segment['kind']} Level {display_index}",
        "title": False,
        "data": numeric_line(segment["level"], segment["start"], segment["end"]),
        "color": segment["color"],
        "type": "line",
        "lineStyle": segment["style"],
        "lineWidth": segment["width"],
        "overlay": True,
    })

if show_bos:
    for display_index, segment in enumerate(bos_segments[-max_displayed_levels:], start=1):
        color = bullish_color if segment["direction"] > 0 else bearish_color
        plots.append({
            "name": f"BOS Segment {display_index}",
            "title": False,
            "data": numeric_line(segment["level"], segment["start"], segment["end"]),
            "color": color,
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        })

recent_zone_start = max(0, n - 96)
recent_zone_low = float(np.nanmin(base_low[recent_zone_start:])) if n else 0.0
recent_zone_high = float(np.nanmax(base_high[recent_zone_start:])) if n else 0.0
recent_zone_span = max(recent_zone_high - recent_zone_low, abs(float(base_close[-1])) * 0.005 if n else 1.0)

def zone_near_recent_market(zone):
    padding = recent_zone_span * 0.5
    return float(zone["lower"]) <= recent_zone_high + padding and float(zone["upper"]) >= recent_zone_low - padding

visible_extreme_zones = [zone for zone in extreme_zones if zone_near_recent_market(zone)][-max_displayed_zones:]
visible_valid_ranges = [zone for zone in valid_ranges if zone_near_recent_market(zone)][-max_displayed_zones:]

for display_index, zone in enumerate(visible_extreme_zones, start=1):
    plots.append({"name": f"Extreme Zone {display_index}", "title": False, "data": cloud_data(zone), "type": "cloud", "overlay": True})
for display_index, zone in enumerate(visible_valid_ranges, start=1):
    plots.append({"name": f"Valid Trading Range {display_index}", "title": False, "data": cloud_data(zone), "type": "cloud", "overlay": True})

def event_data(events, direction=None, label=None, color=None):
    data = [None for _ in range(n)]
    for event in events:
        if direction is not None and int(event.get("direction", 0)) != int(direction):
            continue
        source_index = int(event.get("index", event.get("confirmed", -1)))
        mapped = base_index_for_source(source_index)
        if mapped < 0:
            continue
        text = label if label is not None else str(event.get("label", ""))
        value = float(event.get("value", source_close[source_index] if 0 <= source_index < source_count else 0.0))
        data[mapped] = {"value": value, "text": text, "color": color}
    return data

signals = []
if show_structure_labels:
    for pivot_label, signal_type, color in (
        ("HH", "sell", bullish_color), ("LH", "sell", bearish_color),
        ("HL", "buy", bullish_color), ("LL", "buy", bearish_color),
    ):
        filtered = [event for event in pivot_events if event["label"] == pivot_label]
        signals.append({
            "type": signal_type, "name": f"Structure {pivot_label}", "text": pivot_label,
            "color": color, "renderMode": "events", "data": event_data(filtered, label=pivot_label, color=color),
        })

if show_bos:
    signals.extend([
        {"type": "buy", "name": "Bullish BOS", "text": "BOS", "color": bullish_color, "renderMode": "events", "data": event_data(bos_events, 1, "BOS", bullish_color)},
        {"type": "sell", "name": "Bearish BOS", "text": "BOS", "color": bearish_color, "renderMode": "events", "data": event_data(bos_events, -1, "BOS", bearish_color)},
    ])

def mask_events(mask, values, text, color):
    data = [None for _ in range(n)]
    previous_source = -1
    for base_index, source_index in enumerate(source_for_base):
        if source_index < 0 or source_index == previous_source or source_index >= len(mask):
            previous_source = source_index
            continue
        if mask[source_index] and (source_index == 0 or not mask[source_index - 1]):
            data[base_index] = {"value": float(values[source_index]), "text": text, "color": color}
        previous_source = source_index
    return data

if show_msu:
    signals.extend([
        {"type": "buy", "name": "Bullish MSU", "text": "↑", "color": bullish_color, "renderMode": "events", "data": mask_events(msu_up, source_low, "↑", bullish_color)},
        {"type": "sell", "name": "Bearish MSU", "text": "↓", "color": bearish_color, "renderMode": "events", "data": mask_events(msu_down, source_high, "↓", bearish_color)},
    ])

bar_colors = [None for _ in range(n)]
if color_price_bars and timeframe_enabled:
    for index, source_index in enumerate(source_for_base):
        if source_index < 0 or source_index >= source_count:
            continue
        direction = direction_history[source_index]
        if direction > 0:
            bar_colors[index] = bullish_up_bar_color if base_close[index] >= base_open[index] else bullish_down_bar_color
        else:
            bar_colors[index] = bearish_up_bar_color if base_close[index] >= base_open[index] else bearish_down_bar_color

final_source = int(source_for_base[-1]) if n else -1
final_direction = int(direction_history[final_source]) if 0 <= final_source < source_count else 1
final_efficient = bool(efficiency_history[final_source]) if 0 <= final_source < source_count else False
final_msu = "Bullish" if 0 <= final_source < source_count and msu_up[final_source] else "Bearish" if 0 <= final_source < source_count and msu_down[final_source] else "—"
final_vta = "Bullish" if 0 <= final_source < source_count and vta_up[final_source] else "Bearish" if 0 <= final_source < source_count and vta_down[final_source] else "—"

tables = []
if show_dashboard:
    accent = bullish_color if final_direction > 0 else bearish_color
    tables.append({
        "title": "Algo Market Structure",
        "position": dashboard_position,
        "compact": True,
        "borderColor": accent,
        "accentColor": accent,
        "rows": [
            ["Timeframe", selected_timeframe],
            ["Structure", "Bullish" if final_direction > 0 else "Bearish"],
            ["Efficiency", "Efficient" if final_efficient else "Inefficient"],
            ["MSU", final_msu],
            ["VTA", final_vta],
        ],
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "tables": tables,
    "bar_colors": bar_colors,
}
