# ============================================================
# QuantDinger Adaptive Pivot Trend Levels
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Adaptive Pivot Trend Levels"
my_indicator_description = (
    "Builds an adaptive trend baseline from the ten most recent confirmed pivots, "
    "tracks unbroken pivot levels, and summarizes the current trend regime."
)

# @param pivot_length int 5 Pivot confirmation strength range=1:50:1
# @param show_active_levels bool true Show active pivot levels
# @param show_crossed_levels bool true Show crossed pivot levels
# @param show_trend_fill bool true Fill between price and trend baseline
# @param show_dashboard bool true Show trend dashboard
# @param dashboard_position str top-right Dashboard position values=top-left,top-right,bottom-left,bottom-right
# @param max_displayed_levels int 80 Maximum displayed pivot segments range=10:250:5
# @param up_color str #24E085 Uptrend color
# @param down_color str #E624A5 Downtrend color
# @param neutral_color str #8B93A1 Neutral color

pivot_length = min(50, max(1, int(params.get("pivot_length", 5))))
show_active_levels = bool(params.get("show_active_levels", True))
show_crossed_levels = bool(params.get("show_crossed_levels", True))
show_trend_fill = bool(params.get("show_trend_fill", True))
show_dashboard = bool(params.get("show_dashboard", True))
dashboard_position = str(params.get("dashboard_position", "top-right"))
if dashboard_position not in ("top-left", "top-right", "bottom-left", "bottom-right"):
    dashboard_position = "top-right"
max_displayed_levels = min(250, max(10, int(params.get("max_displayed_levels", 80))))
up_color = str(params.get("up_color", "#24E085"))
down_color = str(params.get("down_color", "#E624A5"))
neutral_color = str(params.get("neutral_color", "#8B93A1"))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
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

def line_points(values, colors=None, fallback=None):
    points = []
    for index, value in enumerate(values):
        if not np.isfinite(value):
            points.append(None)
            continue
        point = {"value": float(value)}
        if colors is not None:
            point["color"] = colors[index]
        elif fallback is not None:
            point["color"] = fallback
        points.append(point)
    return points

confirmed_high = np.zeros(n, dtype=bool)
confirmed_low = np.zeros(n, dtype=bool)
for confirmation_index in range(2 * pivot_length, n):
    pivot_index = confirmation_index - pivot_length
    start = pivot_index - pivot_length
    end = pivot_index + pivot_length + 1
    high_window = high[start:end]
    low_window = low[start:end]
    confirmed_high[confirmation_index] = high[pivot_index] >= float(np.max(high_window))
    confirmed_low[confirmation_index] = low[pivot_index] <= float(np.min(low_window))

trend_line = np.full(n, np.nan, dtype=float)
trend_state = np.zeros(n, dtype=int)
uptrend = np.zeros(n, dtype=bool)
downtrend = np.zeros(n, dtype=bool)
active_counts = np.zeros(n, dtype=int)
crossed_counts = np.zeros(n, dtype=int)

recent_pivots = []
segments = []
active_segment_ids = []
regime_start = 0
crossed_in_regime = 0

for index in range(n):
    pivot_index = index - pivot_length
    if confirmed_high[index]:
        recent_pivots.append(float(high[pivot_index]))
        segments.append({
            "level": float(high[pivot_index]),
            "start": int(pivot_index),
            "end": int(index),
            "below": False,
            "crossed": False,
            "deleted": False,
            "color": neutral_color,
        })
        active_segment_ids.append(len(segments) - 1)
    if confirmed_low[index]:
        recent_pivots.append(float(low[pivot_index]))
        segments.append({
            "level": float(low[pivot_index]),
            "start": int(pivot_index),
            "end": int(index),
            "below": True,
            "crossed": False,
            "deleted": False,
            "color": neutral_color,
        })
        active_segment_ids.append(len(segments) - 1)
    if len(recent_pivots) > 10:
        recent_pivots = recent_pivots[-10:]

    if recent_pivots:
        trend_line[index] = float(np.mean(recent_pivots))
        if low[index] >= trend_line[index]:
            trend_state[index] = 1
        elif high[index] <= trend_line[index]:
            trend_state[index] = -1

    if trend_state[index] == 1:
        uptrend[index] = True
    elif trend_state[index] == 0 and index >= 2:
        uptrend[index] = trend_state[index - 1] == 1 and trend_state[index - 2] == 1
    if trend_state[index] == -1:
        downtrend[index] = True
    elif trend_state[index] == 0 and index >= 2:
        downtrend[index] = trend_state[index - 1] == -1 and trend_state[index - 2] == -1

    trend_changed = False
    if index > 0:
        trend_changed = uptrend[index] != uptrend[index - 1] or downtrend[index] != downtrend[index - 1]
    current_color = up_color if uptrend[index] else down_color if downtrend[index] else neutral_color

    if trend_changed:
        crossed_in_regime = 0
        regime_start = index
    else:
        surviving_ids = []
        active_count = 0
        for segment_id in active_segment_ids:
            segment = segments[segment_id]
            if regime_start > segment["end"]:
                segment["deleted"] = True
                continue
            active_count += 1
            segment["end"] = index
            segment["color"] = current_color
            crossed = low[index] < segment["level"] if segment["below"] else high[index] > segment["level"]
            if crossed:
                segment["crossed"] = True
                crossed_in_regime += 1
            else:
                surviving_ids.append(segment_id)
        active_segment_ids = surviving_ids
        active_counts[index] = active_count
    crossed_counts[index] = crossed_in_regime

trend_colors = [
    up_color if uptrend[index] else down_color if downtrend[index] else neutral_color
    for index in range(n)
]

plots = [
    {
        "name": "Trend Line Shadow",
        "title": False,
        "data": line_points(trend_line, colors=[rgba(color, 0.28) for color in trend_colors]),
        "color": rgba(neutral_color, 0.28),
        "type": "line",
        "lineWidth": 6,
        "overlay": True,
    },
    {
        "name": "Trend Line",
        "data": line_points(trend_line, colors=trend_colors),
        "color": neutral_color,
        "type": "line",
        "lineWidth": 2,
        "overlay": True,
    },
]

if show_trend_fill:
    fill_data = []
    for index in range(n):
        if not np.isfinite(trend_line[index]):
            fill_data.append(None)
            continue
        fill_data.append({
            "upper": float(max(close[index], trend_line[index])),
            "lower": float(min(close[index], trend_line[index])),
            "color": rgba(trend_colors[index], 0.10),
        })
    plots.append({"name": "Trend Fill", "title": False, "data": fill_data, "type": "cloud", "overlay": True})

visible_segments = [segment for segment in segments if not segment["deleted"]]
visible_segments = visible_segments[-max_displayed_levels:]
for display_index, segment in enumerate(visible_segments, start=1):
    if segment["crossed"] and not show_crossed_levels:
        continue
    if not segment["crossed"] and not show_active_levels:
        continue
    values = np.full(n, np.nan, dtype=float)
    start = max(0, int(segment["start"]))
    end = min(n - 1, int(segment["end"]))
    if end >= start:
        values[start:end + 1] = float(segment["level"])
    segment_color = rgba(segment["color"], 0.48) if segment["crossed"] else segment["color"]
    plots.append({
        "name": f"Pivot Level {display_index}",
        "title": False,
        "data": line_points(values, fallback=segment_color),
        "color": segment_color,
        "type": "line",
        "lineWidth": 1,
        "lineStyle": "dotted" if segment["crossed"] else "dashed",
        "overlay": True,
    })

signals = []
trend_up_event = np.r_[False, uptrend[1:] & ~uptrend[:-1]] if n else np.zeros(0, dtype=bool)
trend_down_event = np.r_[False, downtrend[1:] & ~downtrend[:-1]] if n else np.zeros(0, dtype=bool)
signals.append({
    "type": "buy",
    "name": "Uptrend Start",
    "text": "UP",
    "color": up_color,
    "renderMode": "events",
    "data": [
        {"value": float(low[index]), "text": "UP", "color": up_color} if trend_up_event[index] else None
        for index in range(n)
    ],
})
signals.append({
    "type": "sell",
    "name": "Downtrend Start",
    "text": "DN",
    "color": down_color,
    "renderMode": "events",
    "data": [
        {"value": float(high[index]), "text": "DN", "color": down_color} if trend_down_event[index] else None
        for index in range(n)
    ],
})

tables = []
if show_dashboard:
    final_state = "Uptrend" if n and uptrend[-1] else "Downtrend" if n and downtrend[-1] else "Neutral"
    final_symbol = "UP" if n and uptrend[-1] else "DN" if n and downtrend[-1] else "—"
    final_color = trend_colors[-1] if n else neutral_color
    final_level = trend_line[-1] if n else np.nan
    tables.append({
        "title": "Adaptive Pivot Trend",
        "position": dashboard_position,
        "compact": True,
        "borderColor": final_color,
        "accentColor": final_color,
        "rows": [
            ["Trend", f"{final_symbol}  {final_state}"],
            ["Trend level", "—" if not np.isfinite(final_level) else f"{final_level:,.2f}"],
            ["Active levels", str(int(active_counts[-1]) if n else 0)],
            ["Crossed levels", str(int(crossed_counts[-1]) if n else 0)],
            ["Pivot strength", str(pivot_length)],
        ],
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "tables": tables,
}
