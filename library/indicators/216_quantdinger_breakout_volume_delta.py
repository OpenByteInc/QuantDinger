# ============================================================
# QuantDinger Breakout Volume Delta
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; breakout events do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Breakout Volume Delta"
my_indicator_description = (
    "Tracks confirmed swing support and resistance, validates breaks with lower-timeframe "
    "buy/sell volume, and splits breakout candle bodies by volume dominance."
)

indicator_timeframes = ["5m", "15m", "30m", "1H", "4H", "1D", "1W"]
indicator_lower_timeframes = ["1m", "3m", "5m", "15m", "30m", "1H"]

# @param swing_left int 10 Swing bars on the left range=1:100:1
# @param swing_right int 10 Swing bars on the right range=1:100:1
# @param swing_timeframe str Current Swing timeframe values=Current,5m,15m,30m,1H,4H,1D,1W
# @param volume_delta_timeframe str 1m Volume delta lower timeframe values=1m,3m,5m,15m,30m,1H
# @param breakout_mode str Close Breakout source values=Close,Wick
# @param max_levels int 5 Nearest levels per side range=1:50:1
# @param volume_filter bool false Require directional volume dominance
# @param volume_filter_percent float 60.0 Required directional volume percent range=0.0:100.0:0.5
# @param unbroken_style str Dotted Unbroken level style values=Solid,Dashed,Dotted
# @param unbroken_width int 1 Unbroken level width range=1:4:1
# @param unbroken_high_color str #00E676 Unbroken resistance color
# @param unbroken_low_color str #FF1744 Unbroken support color
# @param broken_style str Solid Broken level style values=Solid,Dashed,Dotted
# @param broken_width int 1 Broken level width range=1:4:1
# @param broken_high_color str #00E676 Broken resistance color
# @param broken_low_color str #FF1744 Broken support color
# @param bull_color str #00FF6A Bull volume color
# @param bear_color str #FF2D55 Bear volume color
# @param extend_bars int 5 Active level extension bars range=1:100:1
# @param extend_right bool false Extend active levels right
# @param show_delta_labels bool true Show breakout volume labels
# @param max_delta_labels int 4 Recent breakout volume labels range=1:50:1
# @param label_font_size int 8 Volume label font size range=8:16:1

swing_left = min(100, max(1, int(params.get("swing_left", 10))))
swing_right = min(100, max(1, int(params.get("swing_right", 10))))

valid_swing_timeframes = ("Current", "5m", "15m", "30m", "1H", "4H", "1D", "1W")
swing_timeframe = str(params.get("swing_timeframe", "Current"))
if swing_timeframe not in valid_swing_timeframes:
    swing_timeframe = "Current"

valid_lower_timeframes = ("1m", "3m", "5m", "15m", "30m", "1H")
volume_delta_timeframe = str(params.get("volume_delta_timeframe", "1m"))
if volume_delta_timeframe not in valid_lower_timeframes:
    volume_delta_timeframe = "1m"

breakout_mode = str(params.get("breakout_mode", "Close"))
if breakout_mode not in ("Close", "Wick"):
    breakout_mode = "Close"

max_levels = min(50, max(1, int(params.get("max_levels", 5))))
volume_filter = bool(params.get("volume_filter", False))
volume_filter_percent = min(100.0, max(0.0, float(params.get("volume_filter_percent", 60.0))))

valid_line_styles = ("Solid", "Dashed", "Dotted")
unbroken_style = str(params.get("unbroken_style", "Dotted"))
if unbroken_style not in valid_line_styles:
    unbroken_style = "Dotted"
broken_style = str(params.get("broken_style", "Solid"))
if broken_style not in valid_line_styles:
    broken_style = "Solid"

unbroken_width = min(4, max(1, int(params.get("unbroken_width", 1))))
broken_width = min(4, max(1, int(params.get("broken_width", 1))))
unbroken_high_color = str(params.get("unbroken_high_color", "#00E676"))
unbroken_low_color = str(params.get("unbroken_low_color", "#FF1744"))
broken_high_color = str(params.get("broken_high_color", "#00E676"))
broken_low_color = str(params.get("broken_low_color", "#FF1744"))
bull_color = str(params.get("bull_color", "#00FF6A"))
bear_color = str(params.get("bear_color", "#FF2D55"))
extend_bars = min(100, max(1, int(params.get("extend_bars", 5))))
extend_right = bool(params.get("extend_right", False))
show_delta_labels = bool(params.get("show_delta_labels", True))
max_delta_labels = min(50, max(1, int(params.get("max_delta_labels", 4))))
label_font_size = min(16, max(8, int(params.get("label_font_size", 8))))

timeframe_seconds = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "2H": 7200,
    "4H": 14400,
    "8H": 28800,
    "12H": 43200,
    "1D": 86400,
    "1W": 604800,
}


def normalized_frame(frame):
    if frame is None or len(frame) == 0:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    result = frame.copy().reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = np.arange(len(result), dtype=float) * 3600.0 if column == "time" else np.nan
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["time", "open", "high", "low", "close"]).copy()
    if len(result):
        times = result["time"].to_numpy(dtype=float)
        if np.nanmax(np.abs(times)) > 10_000_000_000:
            result["time"] = times / 1000.0
    return result.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


df = df.copy()
base_frame = normalized_frame(df)
n = len(base_frame)
time_values = base_frame["time"].to_numpy(dtype=float)
open_price = base_frame["open"].to_numpy(dtype=float)
high = base_frame["high"].to_numpy(dtype=float)
low = base_frame["low"].to_numpy(dtype=float)
close = base_frame["close"].to_numpy(dtype=float)
volume = base_frame["volume"].fillna(0.0).to_numpy(dtype=float)

runtime_context = globals().get("ctx")
chart_timeframe = str(getattr(runtime_context, "timeframe", "") or "")
if chart_timeframe in timeframe_seconds:
    chart_seconds = timeframe_seconds[chart_timeframe]
elif n > 1:
    positive_deltas = np.diff(time_values)
    positive_deltas = positive_deltas[positive_deltas > 0.0]
    chart_seconds = int(np.median(positive_deltas)) if len(positive_deltas) else 3600
else:
    chart_seconds = 3600

requested_swing_timeframe = chart_timeframe if swing_timeframe == "Current" else swing_timeframe
requested_swing_seconds = timeframe_seconds.get(requested_swing_timeframe, chart_seconds)
if requested_swing_seconds < chart_seconds:
    resolved_swing_timeframe = chart_timeframe or "Current"
    resolved_swing_seconds = chart_seconds
else:
    resolved_swing_timeframe = requested_swing_timeframe or "Current"
    resolved_swing_seconds = requested_swing_seconds


def aggregate_base(period_seconds):
    if not n:
        return normalized_frame(None)
    buckets = np.floor(time_values / float(period_seconds)).astype(np.int64)
    rows = []
    start = 0
    for index in range(1, n + 1):
        if index < n and buckets[index] == buckets[start]:
            continue
        segment = slice(start, index)
        rows.append({
            "time": float(buckets[start] * period_seconds),
            "open": float(open_price[start]),
            "high": float(np.max(high[segment])),
            "low": float(np.min(low[segment])),
            "close": float(close[index - 1]),
            "volume": float(np.sum(volume[segment])),
        })
        start = index
    return normalized_frame(pd.DataFrame(rows))


source_is_current = resolved_swing_seconds <= chart_seconds
if source_is_current:
    source_frame = base_frame.copy()
else:
    raw_source = (
        runtime_context.raw_series(resolved_swing_timeframe)
        if runtime_context is not None and hasattr(runtime_context, "raw_series")
        else None
    )
    source_frame = normalized_frame(raw_source)
    if len(source_frame) < swing_left + swing_right + 2:
        source_frame = aggregate_base(resolved_swing_seconds)

source_time = source_frame["time"].to_numpy(dtype=float) if len(source_frame) else np.zeros(0, dtype=float)
source_high = source_frame["high"].to_numpy(dtype=float) if len(source_frame) else np.zeros(0, dtype=float)
source_low = source_frame["low"].to_numpy(dtype=float) if len(source_frame) else np.zeros(0, dtype=float)
source_count = len(source_frame)

source_high_time = source_time.copy()
source_low_time = source_time.copy()
if n and source_count and not source_is_current:
    for source_index in range(source_count):
        start_time = source_time[source_index]
        end_time = start_time + resolved_swing_seconds
        left = int(np.searchsorted(time_values, start_time, side="left"))
        right = int(np.searchsorted(time_values, end_time, side="left"))
        if right <= left:
            continue
        high_offset = int(np.argmax(high[left:right]))
        low_offset = int(np.argmin(low[left:right]))
        source_high_time[source_index] = time_values[left + high_offset]
        source_low_time[source_index] = time_values[left + low_offset]

pivot_events = []
last_candidate = source_count - swing_right - 2
for pivot_index in range(swing_left, last_candidate + 1):
    candidate_high = source_high[pivot_index]
    candidate_low = source_low[pivot_index]
    left_high = source_high[pivot_index - swing_left:pivot_index]
    right_high = source_high[pivot_index + 1:pivot_index + swing_right + 1]
    left_low = source_low[pivot_index - swing_left:pivot_index]
    right_low = source_low[pivot_index + 1:pivot_index + swing_right + 1]
    confirmation_index = pivot_index + swing_right + 1
    available_time = source_time[confirmation_index]

    if (
        np.isfinite(candidate_high)
        and len(left_high) == swing_left
        and len(right_high) == swing_right
        and np.all(candidate_high > left_high)
        and np.all(candidate_high > right_high)
    ):
        pivot_events.append({
            "available_time": float(available_time),
            "pivot_time": float(source_high_time[pivot_index]),
            "side": "high",
            "price": float(candidate_high),
        })
    if (
        np.isfinite(candidate_low)
        and len(left_low) == swing_left
        and len(right_low) == swing_right
        and np.all(candidate_low < left_low)
        and np.all(candidate_low < right_low)
    ):
        pivot_events.append({
            "available_time": float(available_time),
            "pivot_time": float(source_low_time[pivot_index]),
            "side": "low",
            "price": float(candidate_low),
        })

pivot_events.sort(key=lambda event: (event["available_time"], 0 if event["side"] == "high" else 1))

lower_seconds = timeframe_seconds.get(volume_delta_timeframe, 60)
lower_request = globals().get("request_lower_tf")
can_request_lower = (
    chart_timeframe in timeframe_seconds
    and lower_seconds < chart_seconds
    and callable(lower_request)
)
if can_request_lower and n:
    intrabars = lower_request(volume_delta_timeframe, fields=["open", "close", "volume"])
    intrabar_open = intrabars.get("open", [[] for _ in range(n)])
    intrabar_close = intrabars.get("close", [[] for _ in range(n)])
    intrabar_volume = intrabars.get("volume", [[] for _ in range(n)])
else:
    intrabar_open = [[] for _ in range(n)]
    intrabar_close = [[] for _ in range(n)]
    intrabar_volume = [[] for _ in range(n)]

bull_volume = np.zeros(n, dtype=float)
bear_volume = np.zeros(n, dtype=float)
intrabar_count = np.zeros(n, dtype=int)
for index in range(n):
    available = min(
        len(intrabar_open[index]) if index < len(intrabar_open) else 0,
        len(intrabar_close[index]) if index < len(intrabar_close) else 0,
        len(intrabar_volume[index]) if index < len(intrabar_volume) else 0,
    )
    intrabar_count[index] = available
    if available:
        for cursor in range(available):
            current_open = float(intrabar_open[index][cursor])
            current_close = float(intrabar_close[index][cursor])
            current_volume = max(0.0, float(intrabar_volume[index][cursor]))
            if current_close > current_open:
                bull_volume[index] += current_volume
            elif current_close < current_open:
                bear_volume[index] += current_volume

total_directional_volume = bull_volume + bear_volume
bull_percent = np.full(n, 0.5, dtype=float)
valid_directional_volume = total_directional_volume > 0.0
bull_percent[valid_directional_volume] = (
    bull_volume[valid_directional_volume] / total_directional_volume[valid_directional_volume]
)
bull_percent = np.clip(bull_percent, 0.0, 1.0)
bear_percent = 1.0 - bull_percent


def data_index_for_time(timestamp):
    if not n:
        return 0
    index = int(np.searchsorted(time_values, timestamp, side="left"))
    return min(n - 1, max(0, index))


levels_high = []
levels_low = []
high_break = np.zeros(n, dtype=bool)
low_break = np.zeros(n, dtype=bool)
break_direction = np.zeros(n, dtype=int)
break_labels = []
event_cursor = 0
threshold = volume_filter_percent / 100.0


def add_level(event, created_index):
    collection = levels_high if event["side"] == "high" else levels_low
    collection.append({
        "side": event["side"],
        "price": float(event["price"]),
        "start": data_index_for_time(event["pivot_time"]),
        "created": int(created_index),
        "end": None,
        "broken": False,
        "deleted": False,
    })
    while len(collection) > max_levels:
        removed = collection.pop(0)
        removed["deleted"] = True


for index in range(n):
    while event_cursor < len(pivot_events) and pivot_events[event_cursor]["available_time"] <= time_values[index]:
        add_level(pivot_events[event_cursor], index)
        event_cursor += 1

    did_high_break = False
    did_low_break = False

    for level_index in range(len(levels_high) - 1, -1, -1):
        level = levels_high[level_index]
        if level["deleted"] or level["broken"]:
            continue
        broken = high[index] > level["price"] if breakout_mode == "Wick" else close[index] > level["price"]
        if not broken:
            continue
        volume_ok = (not volume_filter) or bull_percent[index] > threshold
        if not volume_ok:
            level["deleted"] = True
            levels_high.pop(level_index)
        else:
            level["broken"] = True
            level["end"] = index
            did_high_break = True

    for level_index in range(len(levels_low) - 1, -1, -1):
        level = levels_low[level_index]
        if level["deleted"] or level["broken"]:
            continue
        broken = low[index] < level["price"] if breakout_mode == "Wick" else close[index] < level["price"]
        if not broken:
            continue
        volume_ok = (not volume_filter) or bear_percent[index] > threshold
        if not volume_ok:
            level["deleted"] = True
            levels_low.pop(level_index)
        else:
            level["broken"] = True
            level["end"] = index
            did_low_break = True

    high_break[index] = did_high_break
    low_break[index] = did_low_break
    if did_high_break or did_low_break:
        direction = 1 if did_high_break else -1
        break_direction[index] = direction
        break_labels.append({
            "index": index,
            "direction": direction,
            "bull_volume": float(bull_volume[index]),
            "bear_volume": float(bear_volume[index]),
            "bull_percent": float(bull_percent[index]),
            "bear_percent": float(bear_percent[index]),
        })


def rgba(color_value, opacity):
    value = str(color_value).strip()
    alpha = min(1.0, max(0.0, float(opacity)))
    if value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            red, green, blue = (int(value[position] * 2, 16) for position in range(1, 4))
        else:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{alpha:.3f})"
    return value


plots = [{
    "name": "Breakout Alignment Anchor",
    "title": False,
    "data": [float(value) for value in close],
    "color": "rgba(0,0,0,0)",
    "type": "line",
    "lineWidth": 1,
    "overlay": True,
}]
layers = []
visible_levels = [level for level in levels_high + levels_low if not level["deleted"]]
visible_levels.sort(key=lambda level: (level["created"], 0 if level["side"] == "high" else 1))
for display_index, level in enumerate(visible_levels, start=1):
    is_high = level["side"] == "high"
    if level["broken"]:
        level_color = broken_high_color if is_high else broken_low_color
        level_style = broken_style.lower()
        level_width = broken_width
        state_name = "Broken"
    else:
        level_color = unbroken_high_color if is_high else unbroken_low_color
        level_style = unbroken_style.lower()
        level_width = unbroken_width
        state_name = "Active"
    start_index = max(0, min(n - 1, int(level["start"]))) if n else 0
    end_index = n - 1 if level["end"] is None else min(n - 1, max(0, int(level["end"])))
    if n and end_index >= start_index:
        layers.append({
            "type": "line",
            "role": f"{state_name} {'Resistance' if is_high else 'Support'} {display_index}",
            "start": float(time_values[start_index]),
            "end": float(time_values[end_index]),
            "price": float(level["price"]),
            "color": level_color,
            "lineWidth": level_width,
            "lineStyle": level_style,
            "dashed": level_style != "solid",
            "extendBars": extend_bars if not level["broken"] and not extend_right else 0,
            "extendRight": bool(extend_right and not level["broken"]),
        })

visible_break_indices = sorted({
    int(level["end"])
    for level in visible_levels
    if level["broken"] and level["end"] is not None
})
for index in visible_break_indices:
    direction = int(break_direction[index])
    if not direction:
        continue
    body_top = float(max(open_price[index], close[index]))
    body_bottom = float(min(open_price[index], close[index]))
    body_range = body_top - body_bottom
    if body_range <= 0.0:
        padding = max(float(high[index] - low[index]), 1e-12) * 0.35
        body_top = float(close[index] + padding)
        body_bottom = float(close[index] - padding)
        body_range = body_top - body_bottom

    if direction > 0:
        cut = body_bottom + body_range * bull_percent[index]
        dominant_open, dominant_close = body_bottom, cut
        opposite_open, opposite_close = cut, body_top
        dominant_color, opposite_color = bull_color, bear_color
        outline_color = bull_color
    else:
        cut = body_top - body_range * bear_percent[index]
        dominant_open, dominant_close = cut, body_top
        opposite_open, opposite_close = body_bottom, cut
        dominant_color, opposite_color = bear_color, bull_color
        outline_color = bear_color

    center_time = float(time_values[index])
    half_width = max(1.0, float(chart_seconds) * 0.28)
    start_time = center_time - half_width
    end_time = center_time + half_width
    layers.extend([
        {
            "type": "box",
            "role": "Breakout Delta Dominant Segment",
            "start": start_time,
            "end": end_time,
            "top": float(max(dominant_open, dominant_close)),
            "bottom": float(min(dominant_open, dominant_close)),
            "color": dominant_color,
            "fillColor": dominant_color,
            "borderColor": dominant_color,
            "borderSize": 0,
            "opacity": 0.92,
        },
        {
            "type": "box",
            "role": "Breakout Delta Opposite Segment",
            "start": start_time,
            "end": end_time,
            "top": float(max(opposite_open, opposite_close)),
            "bottom": float(min(opposite_open, opposite_close)),
            "color": opposite_color,
            "fillColor": opposite_color,
            "borderColor": opposite_color,
            "borderSize": 0,
            "opacity": 0.92,
        },
        {
            "type": "box",
            "role": "Breakout Candle Outline",
            "start": start_time,
            "end": end_time,
            "top": body_top,
            "bottom": body_bottom,
            "color": rgba(outline_color, 0.0),
            "fillColor": rgba(outline_color, 0.0),
            "borderColor": outline_color,
            "borderSize": 1,
            "opacity": 1.0,
        },
        {
            "type": "line",
            "role": "Breakout Candle Wick",
            "start": center_time,
            "end": center_time,
            "y1": float(low[index]),
            "y2": float(high[index]),
            "color": outline_color,
            "lineWidth": 1,
        },
    ])


def compact_volume(value):
    absolute = abs(float(value))
    if absolute >= 1_000_000_000.0:
        divisor, suffix = 1_000_000_000.0, "B"
    elif absolute >= 1_000_000.0:
        divisor, suffix = 1_000_000.0, "M"
    elif absolute >= 1_000.0:
        divisor, suffix = 1_000.0, "K"
    else:
        divisor, suffix = 1.0, ""
    precision = 2 if absolute >= 1_000.0 else 0
    text = f"{float(value) / divisor:.{precision}f}"
    if precision:
        text = text.rstrip("0").rstrip(".")
    return text + suffix


if show_delta_labels:
    covered_break_labels = [
        event for event in break_labels
        if intrabar_count[int(event["index"])] > 0
    ]
    for event in covered_break_labels[-max_delta_labels:]:
        index = int(event["index"])
        direction = int(event["direction"])
        padding = max(float(high[index] - low[index]), abs(float(close[index])) * 0.0005, 1e-12) * 0.25
        label_price = float(high[index] + padding) if direction > 0 else float(low[index] - padding)
        label_color = bull_color if direction > 0 else bear_color
        label_text = (
            f"B {compact_volume(event['bull_volume'])} {event['bull_percent'] * 100.0:.1f}%\n"
            f"S {compact_volume(event['bear_volume'])} {event['bear_percent'] * 100.0:.1f}%"
        )
        layers.append({
            "type": "label",
            "timestamp": float(time_values[index]),
            "price": label_price,
            "text": label_text,
            "color": "rgba(0,0,0,0)",
            "fillColor": "rgba(0,0,0,0)",
            "borderColor": "rgba(0,0,0,0)",
            "textColor": label_color,
            "fontSize": label_font_size,
            "side": "above" if direction > 0 else "below",
        })

coverage = int(np.sum(intrabar_count > 0))
latest_index = n - 1 if n else 0
output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": layers,
    "tables": [],
    "calculatedVars": {
        "chartTimeframe": chart_timeframe or "inferred",
        "resolvedSwingTimeframe": resolved_swing_timeframe,
        "volumeDeltaTimeframe": volume_delta_timeframe,
        "volumeCoverageBars": coverage,
        "bullVolume": [float(value) for value in bull_volume],
        "bearVolume": [float(value) for value in bear_volume],
        "bullPercent": [float(value) for value in bull_percent],
        "bearPercent": [float(value) for value in bear_percent],
        "intrabarCount": [int(value) for value in intrabar_count],
        "highBreak": [bool(value) for value in high_break],
        "lowBreak": [bool(value) for value in low_break],
        "breakDirection": [int(value) for value in break_direction],
        "activeResistanceCount": int(sum(not level["broken"] for level in levels_high)),
        "activeSupportCount": int(sum(not level["broken"] for level in levels_low)),
        "latestBullPercent": float(bull_percent[latest_index]) if n else 0.5,
        "latestBearPercent": float(bear_percent[latest_index]) if n else 0.5,
    },
}
