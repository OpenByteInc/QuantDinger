# ============================================================
# QuantDinger Candle Range
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Candle Range"
my_indicator_description = (
    "Tracks execution and context candle ranges, breakout resets, range sweeps, "
    "midpoint transitions, and confirmed transition statistics."
)

indicator_timeframes = ["1H", "4H", "1D", "1W"]

# @param execution_timeframe str CHART Execution range timeframe values=CHART,1H,4H,1D,1W
# @param context_timeframe str 1H Context range timeframe values=1H,4H,1D,1W
# @param color_mode str trend Color mode values=trend,single
# @param single_color str #E2E8F0 Single or initial color
# @param bullish_color str #22C55E Bullish reset color
# @param bearish_color str #EF4444 Bearish reset color
# @param border_width int 2 Grid line width range=1:4:1
# @param midpoint_width int 1 Midpoint width range=1:4:1
# @param show_fill bool false Fill execution range
# @param fill_opacity float 0.08 Execution fill opacity range=0.0:0.35:0.01
# @param show_context_range bool true Show context range
# @param context_width int 1 Context line width range=1:4:1
# @param context_opacity float 0.42 Context opacity range=0.05:1.0:0.05
# @param show_context_fill bool false Fill context range
# @param context_fill_opacity float 0.04 Context fill opacity range=0.0:0.25:0.01
# @param show_quarter_levels bool true Show execution quarter levels
# @param quarter_width int 1 Quarter line width range=1:4:1
# @param show_context_midpoint bool true Show context midpoint
# @param show_execution_labels bool true Show execution labels
# @param show_quarter_labels bool true Show quarter labels
# @param show_context_labels bool true Show context labels
# @param show_sweep_markers bool true Show execution range sweeps
# @param show_midpoint_markers bool false Show midpoint reclaim and loss
# @param show_dashboard bool true Show range dashboard
# @param show_statistics bool true Show transition statistics
# @param minimum_samples int 30 Minimum statistical samples range=1:500:1
# @param show_reset_markers bool false Show execution reset markers
# @param max_markers int 6 Recent event markers range=1:50:1
# @param marker_spacing int 6 Minimum bars between identical markers range=0:50:1

requested_execution = str(params.get("execution_timeframe", "CHART")).upper()
requested_context = str(params.get("context_timeframe", "1H")).upper()
color_mode = str(params.get("color_mode", "trend")).lower()
single_color = str(params.get("single_color", "#E2E8F0"))
bullish_color = str(params.get("bullish_color", "#22C55E"))
bearish_color = str(params.get("bearish_color", "#EF4444"))
border_width = min(4, max(1, int(params.get("border_width", 2))))
midpoint_width = min(4, max(1, int(params.get("midpoint_width", 1))))
show_fill = bool(params.get("show_fill", False))
fill_opacity = min(0.35, max(0.0, float(params.get("fill_opacity", 0.08))))
show_context_range = bool(params.get("show_context_range", True))
context_width = min(4, max(1, int(params.get("context_width", 1))))
context_opacity = min(1.0, max(0.05, float(params.get("context_opacity", 0.42))))
show_context_fill = bool(params.get("show_context_fill", False))
context_fill_opacity = min(0.25, max(0.0, float(params.get("context_fill_opacity", 0.04))))
show_quarter_levels = bool(params.get("show_quarter_levels", True))
quarter_width = min(4, max(1, int(params.get("quarter_width", 1))))
show_context_midpoint = bool(params.get("show_context_midpoint", True))
show_execution_labels = bool(params.get("show_execution_labels", True))
show_quarter_labels = bool(params.get("show_quarter_labels", True))
show_context_labels = bool(params.get("show_context_labels", True))
show_sweep_markers = bool(params.get("show_sweep_markers", True))
show_midpoint_markers = bool(params.get("show_midpoint_markers", False))
show_dashboard = bool(params.get("show_dashboard", True))
show_statistics = bool(params.get("show_statistics", True))
minimum_samples = min(500, max(1, int(params.get("minimum_samples", 30))))
show_reset_markers = bool(params.get("show_reset_markers", False))
max_markers = min(50, max(1, int(params.get("max_markers", 6))))
marker_spacing = min(50, max(0, int(params.get("marker_spacing", 6))))

frame = df.copy()
frame = frame.reset_index(drop=True)
n = len(frame)
if "time" not in frame.columns:
    frame["time"] = np.arange(n, dtype=float) * 3600.0
time_values = pd.to_numeric(frame["time"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
base_time_seconds = time_values.copy()
if n and np.nanmax(np.abs(base_time_seconds)) > 10000000000:
    base_time_seconds = base_time_seconds / 1000.0
open_values = pd.to_numeric(frame["open"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
high_values = pd.to_numeric(frame["high"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)

timeframe_seconds = {
    "1M": 60,
    "5M": 300,
    "15M": 900,
    "30M": 1800,
    "1H": 3600,
    "4H": 14400,
    "1D": 86400,
    "1W": 604800,
}
ordered_timeframes = ("1M", "5M", "15M", "30M", "1H", "4H", "1D", "1W")
runtime_context = globals().get("ctx")
chart_timeframe = str(getattr(runtime_context, "timeframe", "1H") or "1H").upper()
chart_seconds = timeframe_seconds.get(chart_timeframe, 3600)

resolved_execution = chart_timeframe if requested_execution == "CHART" else requested_execution
if resolved_execution not in timeframe_seconds or timeframe_seconds[resolved_execution] < chart_seconds:
    resolved_execution = chart_timeframe
execution_seconds = timeframe_seconds.get(resolved_execution, chart_seconds)

resolved_context = requested_context if requested_context in timeframe_seconds else resolved_execution
if timeframe_seconds.get(resolved_context, 0) < execution_seconds:
    resolved_context = next(
        (name for name in ordered_timeframes if timeframe_seconds[name] > execution_seconds),
        resolved_execution,
    )


def normalize_ohlc(raw):
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=["time", "high", "low", "close"])
    result = raw.copy().reset_index(drop=True)
    if "time" not in result.columns:
        result["time"] = np.arange(len(result), dtype=float) * 3600.0
    for column in ("time", "high", "low", "close"):
        result[column] = pd.to_numeric(result.get(column), errors="coerce")
    result = result.dropna(subset=["time", "high", "low", "close"])
    if len(result) and np.nanmax(np.abs(result["time"].to_numpy(dtype=float))) > 10000000000:
        result["time"] = result["time"] / 1000.0
    return result.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


def calculate_range(times, highs, lows, closes):
    count = len(times)
    range_high = np.full(count, np.nan, dtype=float)
    range_low = np.full(count, np.nan, dtype=float)
    direction = np.zeros(count, dtype=int)
    seed_time = np.full(count, np.nan, dtype=float)
    break_strength = np.full(count, np.nan, dtype=float)
    state_high = np.nan
    state_low = np.nan
    state_direction = 0
    state_seed = np.nan
    state_strength = np.nan
    for index in range(count):
        current_high = float(highs[index])
        current_low = float(lows[index])
        current_close = float(closes[index])
        if not np.isfinite(state_high):
            state_high = current_high
            state_low = current_low
            state_seed = float(times[index])
        else:
            closes_above = current_close > state_high
            closes_below = current_close < state_low
            if closes_above or closes_below:
                previous_size = state_high - state_low
                distance = current_close - state_high if closes_above else state_low - current_close
                state_strength = distance / previous_size * 100.0 if previous_size > 0.0 else np.nan
                state_high = current_high
                state_low = current_low
                state_direction = 1 if closes_above else -1
                state_seed = float(times[index])
        range_high[index] = state_high
        range_low[index] = state_low
        direction[index] = state_direction
        seed_time[index] = state_seed
        break_strength[index] = state_strength
    return range_high, range_low, direction, seed_time, break_strength


chart_state = calculate_range(base_time_seconds, high_values, low_values, close_values)
state_cache = {chart_timeframe: chart_state}
raw_count_by_timeframe = {chart_timeframe: int(n)}


def aligned_state(timeframe_name):
    if timeframe_name == chart_timeframe:
        return chart_state, np.arange(n, dtype=int)
    raw_source = (
        runtime_context.raw_series(timeframe_name)
        if runtime_context is not None and hasattr(runtime_context, "raw_series")
        else None
    )
    raw = normalize_ohlc(raw_source)
    raw_count_by_timeframe[timeframe_name] = int(len(raw))
    if not len(raw):
        return chart_state, np.arange(n, dtype=int)
    raw_time = raw["time"].to_numpy(dtype=float)
    if timeframe_name not in state_cache:
        state_cache[timeframe_name] = calculate_range(
            raw_time,
            raw["high"].to_numpy(dtype=float),
            raw["low"].to_numpy(dtype=float),
            raw["close"].to_numpy(dtype=float),
        )
    raw_state = state_cache[timeframe_name]
    mapped = [np.full(n, np.nan, dtype=float), np.full(n, np.nan, dtype=float), np.zeros(n, dtype=int), np.full(n, np.nan, dtype=float), np.full(n, np.nan, dtype=float)]
    source_index = np.full(n, -1, dtype=int)
    for index in range(n):
        current_index = int(np.searchsorted(raw_time, base_time_seconds[index], side="right") - 1)
        confirmed_index = current_index - 1
        if confirmed_index < 0:
            continue
        source_index[index] = confirmed_index
        mapped[0][index] = raw_state[0][confirmed_index]
        mapped[1][index] = raw_state[1][confirmed_index]
        mapped[2][index] = raw_state[2][confirmed_index]
        mapped[3][index] = raw_state[3][confirmed_index]
        mapped[4][index] = raw_state[4][confirmed_index]
    return tuple(mapped), source_index


(execution_high, execution_low, execution_direction, execution_seed_time, execution_break_strength), execution_source_index = aligned_state(resolved_execution)
(context_high, context_low, context_direction, context_seed_time, context_break_strength), context_source_index = aligned_state(resolved_context)

execution_size = execution_high - execution_low
execution_q25 = execution_low + execution_size * 0.25
execution_mid = execution_low + execution_size * 0.50
execution_q75 = execution_low + execution_size * 0.75
context_size = context_high - context_low
context_q25 = context_low + context_size * 0.25
context_mid = context_low + context_size * 0.50
context_q75 = context_low + context_size * 0.75

execution_ready = np.isfinite(execution_high) & np.isfinite(execution_low) & np.isfinite(execution_seed_time)
context_ready = np.isfinite(context_high) & np.isfinite(context_low) & np.isfinite(context_seed_time)
new_execution_grid = np.zeros(n, dtype=bool)
new_context_grid = np.zeros(n, dtype=bool)
for index in range(n):
    new_execution_grid[index] = bool(execution_ready[index] and (index == 0 or not np.isfinite(execution_seed_time[index - 1]) or execution_seed_time[index] != execution_seed_time[index - 1]))
    new_context_grid[index] = bool(context_ready[index] and (index == 0 or not np.isfinite(context_seed_time[index - 1]) or context_seed_time[index] != context_seed_time[index - 1]))

bullish_execution_reset = new_execution_grid & (execution_direction > 0)
bearish_execution_reset = new_execution_grid & (execution_direction < 0)
bullish_context_reset = new_context_grid & (context_direction > 0)
bearish_context_reset = new_context_grid & (context_direction < 0)

execution_position = np.full(n, np.nan, dtype=float)
context_position = np.full(n, np.nan, dtype=float)
valid_execution_size = execution_ready & (execution_size > 0.0)
valid_context_size = context_ready & (context_size > 0.0)
execution_position[valid_execution_size] = (close_values[valid_execution_size] - execution_low[valid_execution_size]) / execution_size[valid_execution_size] * 100.0
context_position[valid_context_size] = (close_values[valid_context_size] - context_low[valid_context_size]) / context_size[valid_context_size] * 100.0

lower_sweep = execution_ready & ~new_execution_grid & (low_values < execution_low) & (close_values >= execution_low)
upper_sweep = execution_ready & ~new_execution_grid & (high_values > execution_high) & (close_values <= execution_high)
mid_reclaim = np.zeros(n, dtype=bool)
mid_loss = np.zeros(n, dtype=bool)
for index in range(1, n):
    if not execution_ready[index] or new_execution_grid[index] or not execution_ready[index - 1]:
        continue
    mid_reclaim[index] = close_values[index] > execution_mid[index] and close_values[index - 1] <= execution_mid[index - 1]
    mid_loss[index] = close_values[index] < execution_mid[index] and close_values[index - 1] >= execution_mid[index - 1]

execution_age = np.zeros(n, dtype=int)
context_age = np.zeros(n, dtype=int)
for index in range(1, n):
    execution_age[index] = 0 if new_execution_grid[index] else execution_age[index - 1] + 1
    context_age[index] = 0 if new_context_grid[index] else context_age[index - 1] + 1

exec_bull_samples = 0
exec_bull_successes = 0
exec_bear_samples = 0
exec_bear_successes = 0
aligned_bull_samples = 0
aligned_bull_successes = 0
aligned_bear_samples = 0
aligned_bear_successes = 0
bull_pullback_samples = 0
bull_pullback_recoveries = 0
bear_pullback_samples = 0
bear_pullback_recoveries = 0
context_bull_samples = 0
context_bull_successes = 0
context_bear_samples = 0
context_bear_successes = 0
tracked_execution_direction = 0
tracked_execution_context_direction = 0
tracked_context_direction = 0

for index in range(n):
    current_execution_direction = int(execution_direction[index])
    current_context_direction = int(context_direction[index])
    if new_execution_grid[index] and current_execution_direction != 0:
        if tracked_execution_direction != 0:
            if tracked_execution_direction > 0:
                exec_bull_samples += 1
                exec_bull_successes += int(current_execution_direction > 0)
            else:
                exec_bear_samples += 1
                exec_bear_successes += int(current_execution_direction < 0)
            if tracked_execution_context_direction > 0 and tracked_execution_direction > 0:
                aligned_bull_samples += 1
                aligned_bull_successes += int(current_execution_direction > 0)
            elif tracked_execution_context_direction < 0 and tracked_execution_direction < 0:
                aligned_bear_samples += 1
                aligned_bear_successes += int(current_execution_direction < 0)
            elif tracked_execution_context_direction > 0 and tracked_execution_direction < 0:
                bull_pullback_samples += 1
                bull_pullback_recoveries += int(current_execution_direction > 0)
            elif tracked_execution_context_direction < 0 and tracked_execution_direction > 0:
                bear_pullback_samples += 1
                bear_pullback_recoveries += int(current_execution_direction < 0)
        tracked_execution_direction = current_execution_direction
        tracked_execution_context_direction = current_context_direction if context_ready[index] else 0
    if new_context_grid[index] and current_context_direction != 0:
        if tracked_context_direction != 0:
            if tracked_context_direction > 0:
                context_bull_samples += 1
                context_bull_successes += int(current_context_direction > 0)
            else:
                context_bear_samples += 1
                context_bear_successes += int(current_context_direction < 0)
        tracked_context_direction = current_context_direction


def direction_text(direction):
    return "Bullish" if direction > 0 else "Bearish" if direction < 0 else "Initial"


def zone_text(price, bottom, q25, midpoint, q75, top):
    if not all(np.isfinite(value) for value in (price, bottom, q25, midpoint, q75, top)):
        return "N/A"
    if price > top:
        return "Above range"
    if price < bottom:
        return "Below range"
    if price >= q75:
        return "75-100%"
    if price >= midpoint:
        return "50-75%"
    if price >= q25:
        return "25-50%"
    return "0-25%"


def alignment_text(context_dir, execution_dir, price, context_bottom, context_middle, context_top, execution_middle):
    if price > context_top:
        return "Context break UP"
    if price < context_bottom:
        return "Context break DOWN"
    if context_dir > 0 and execution_dir > 0:
        return "STRONG BULL" if price >= context_middle and price >= execution_middle else "Bull aligned"
    if context_dir < 0 and execution_dir < 0:
        return "STRONG BEAR" if price <= context_middle and price <= execution_middle else "Bear aligned"
    if context_dir > 0 and execution_dir < 0:
        return "BULL PULLBACK" if price >= context_middle else "Deep bull pullback"
    if context_dir < 0 and execution_dir > 0:
        return "BEAR PULLBACK" if price <= context_middle else "Deep bear pullback"
    if context_dir > 0:
        return "Bull context"
    if context_dir < 0:
        return "Bear context"
    return "Neutral"


def confirmation_text(context_dir, execution_dir, price, execution_middle):
    if context_dir > 0:
        if execution_dir > 0:
            return "Long-side structure" if price >= execution_middle else "Wait for Exec Mid reclaim"
        return "Wait for bullish Exec shift" if execution_dir < 0 else "Wait for Exec confirmation"
    if context_dir < 0:
        if execution_dir < 0:
            return "Short-side structure" if price <= execution_middle else "Wait for Exec Mid loss"
        return "Wait for bearish Exec shift" if execution_dir > 0 else "Wait for Exec confirmation"
    return "Wait"


def format_stat(outcome, successes, samples):
    if samples <= 0:
        return f"{outcome} N/A"
    warning = " LOW N" if samples < minimum_samples else ""
    return f"{outcome} {successes * 100.0 / samples:.1f}% n={samples}{warning}"


def rgba(hex_color, alpha):
    value = str(hex_color).lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    if len(value) != 6:
        return str(hex_color)
    try:
        red = int(value[0:2], 16)
        green = int(value[2:4], 16)
        blue = int(value[4:6], 16)
        return f"rgba({red},{green},{blue},{alpha:.3f})"
    except ValueError:
        return str(hex_color)


def range_color(direction, alpha=1.0):
    base_color = single_color if color_mode == "single" else bullish_color if direction > 0 else bearish_color if direction < 0 else single_color
    return base_color if alpha >= 0.999 else rgba(base_color, alpha)


def colored_line(values, directions, alpha=1.0):
    result = []
    for index, value in enumerate(values):
        if not np.isfinite(value):
            result.append(None)
        else:
            result.append({"value": float(value), "color": range_color(int(directions[index]), alpha)})
    return result


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


plots = [
    {"name": "Execution High", "data": colored_line(execution_high, execution_direction), "type": "line", "lineWidth": border_width, "overlay": True},
    {"name": "Execution Mid", "data": colored_line(execution_mid, execution_direction), "type": "line", "lineWidth": midpoint_width, "lineStyle": "dashed", "overlay": True},
    {"name": "Execution Low", "data": colored_line(execution_low, execution_direction), "type": "line", "lineWidth": border_width, "overlay": True},
]
if show_quarter_levels:
    plots.extend([
        {"name": "Execution 75%", "data": colored_line(execution_q75, execution_direction, 0.62), "type": "line", "lineWidth": quarter_width, "lineStyle": "dotted", "overlay": True},
        {"name": "Execution 25%", "data": colored_line(execution_q25, execution_direction, 0.62), "type": "line", "lineWidth": quarter_width, "lineStyle": "dotted", "overlay": True},
    ])
if show_fill:
    execution_cloud = []
    for index in range(n):
        execution_cloud.append({"upper": float(execution_high[index]), "lower": float(execution_low[index]), "color": range_color(int(execution_direction[index]))} if execution_ready[index] else None)
    plots.append({"name": "Execution Range Fill", "title": False, "data": execution_cloud, "type": "cloud", "color": single_color, "opacity": fill_opacity, "overlay": True})
if show_context_range:
    plots.extend([
        {"name": "Context High", "data": colored_line(context_high, context_direction, context_opacity), "type": "line", "lineWidth": context_width, "overlay": True},
        {"name": "Context Low", "data": colored_line(context_low, context_direction, context_opacity), "type": "line", "lineWidth": context_width, "overlay": True},
    ])
    if show_context_midpoint:
        plots.append({"name": "Context Mid", "data": colored_line(context_mid, context_direction, context_opacity), "type": "line", "lineWidth": context_width, "lineStyle": "dashed", "overlay": True})
    if show_context_fill:
        context_cloud = []
        for index in range(n):
            context_cloud.append({"upper": float(context_high[index]), "lower": float(context_low[index]), "color": range_color(int(context_direction[index]))} if context_ready[index] else None)
        plots.append({"name": "Context Range Fill", "title": False, "data": context_cloud, "type": "cloud", "color": single_color, "opacity": context_fill_opacity, "overlay": True})

event_candidates = []
for index in range(n):
    if show_sweep_markers and lower_sweep[index]:
        event_candidates.append((index, "SWP", bullish_color, low_values[index], "below"))
    if show_sweep_markers and upper_sweep[index]:
        event_candidates.append((index, "SWP", bearish_color, high_values[index], "above"))
    if show_midpoint_markers and mid_reclaim[index]:
        event_candidates.append((index, "MID+", bullish_color, low_values[index], "below"))
    if show_midpoint_markers and mid_loss[index]:
        event_candidates.append((index, "MID-", bearish_color, high_values[index], "above"))
    if show_reset_markers and bullish_execution_reset[index]:
        event_candidates.append((index, "RST+", bullish_color, low_values[index], "below"))
    if show_reset_markers and bearish_execution_reset[index]:
        event_candidates.append((index, "RST-", bearish_color, high_values[index], "above"))
spaced_events = []
for event in event_candidates:
    if spaced_events and event[1] == spaced_events[-1][1] and event[0] - spaced_events[-1][0] <= marker_spacing:
        spaced_events[-1] = event
    else:
        spaced_events.append(event)
event_candidates = spaced_events[-max_markers:]
layers = []
for index, text_value, color_value, price_value, side_value in event_candidates:
    layers.append({
        "type": "label",
        "timestamp": float(time_values[index]),
        "price": float(price_value),
        "text": text_value,
        "color": color_value,
        "fillColor": color_value,
        "borderColor": color_value,
        "textColor": "#FFFFFF",
        "fontSize": 8,
        "side": side_value,
    })

last_index = n - 1
current_level_labels = []
if n and execution_ready[last_index] and show_execution_labels:
    execution_label_color = range_color(int(execution_direction[last_index]), 0.74)
    current_level_labels.extend([
        [float(execution_high[last_index]), "E:H", execution_label_color],
        [float(execution_mid[last_index]), "E:M", execution_label_color],
        [float(execution_low[last_index]), "E:L", execution_label_color],
    ])
    if show_quarter_labels:
        current_level_labels.extend([
            [float(execution_q75[last_index]), "E:75", execution_label_color],
            [float(execution_q25[last_index]), "E:25", execution_label_color],
        ])
if n and context_ready[last_index] and show_context_range and show_context_labels:
    context_label_color = range_color(int(context_direction[last_index]), 0.46)
    current_level_labels.extend([
        [float(context_high[last_index]), "C:H", context_label_color],
        [float(context_mid[last_index]), "C:M", context_label_color],
        [float(context_low[last_index]), "C:L", context_label_color],
    ])
if current_level_labels:
    visible_start = max(0, n - 200)
    visible_span = float(np.max(high_values[visible_start:]) - np.min(low_values[visible_start:]))
    merge_distance = max(1e-9, visible_span * 0.04)
    grouped_labels = []
    for price_value, text_value, color_value in sorted(current_level_labels, key=lambda item: item[0], reverse=True):
        if grouped_labels and abs(price_value - grouped_labels[-1][0]) <= merge_distance:
            group = grouped_labels[-1]
            group[0] = (group[0] * group[3] + price_value) / float(group[3] + 1)
            group[1] = group[1] + " | " + text_value
            group[3] += 1
        else:
            grouped_labels.append([price_value, text_value, color_value, 1])
    for price_value, text_value, color_value, _ in grouped_labels:
        layers.append({"type": "label", "timestamp": float(time_values[last_index]), "price": float(price_value), "text": text_value, "color": color_value, "fillColor": color_value, "borderColor": color_value, "textColor": "#FFFFFF", "fontSize": 7, "side": "above"})

tables = []
if show_dashboard and n and execution_ready[last_index] and context_ready[last_index]:
    execution_dir = int(execution_direction[last_index])
    context_dir = int(context_direction[last_index])
    execution_zone = zone_text(close_values[last_index], execution_low[last_index], execution_q25[last_index], execution_mid[last_index], execution_q75[last_index], execution_high[last_index])
    context_zone = zone_text(close_values[last_index], context_low[last_index], context_q25[last_index], context_mid[last_index], context_q75[last_index], context_high[last_index])
    alignment = alignment_text(context_dir, execution_dir, close_values[last_index], context_low[last_index], context_mid[last_index], context_high[last_index], execution_mid[last_index])
    confirmation = confirmation_text(context_dir, execution_dir, close_values[last_index], execution_mid[last_index])
    if execution_dir > 0:
        execution_stat = format_stat("Next Bull", exec_bull_successes, exec_bull_samples)
    elif execution_dir < 0:
        execution_stat = format_stat("Next Bear", exec_bear_successes, exec_bear_samples)
    else:
        execution_stat = "N/A"
    if context_dir > 0 and execution_dir > 0:
        state_stat = format_stat("Next Bull", aligned_bull_successes, aligned_bull_samples)
    elif context_dir < 0 and execution_dir < 0:
        state_stat = format_stat("Next Bear", aligned_bear_successes, aligned_bear_samples)
    elif context_dir > 0 and execution_dir < 0:
        state_stat = format_stat("Recover Bull", bull_pullback_recoveries, bull_pullback_samples)
    elif context_dir < 0 and execution_dir > 0:
        state_stat = format_stat("Recover Bear", bear_pullback_recoveries, bear_pullback_samples)
    else:
        state_stat = "N/A"
    context_stat = format_stat("Next Bull", context_bull_successes, context_bull_samples) if context_dir > 0 else format_stat("Next Bear", context_bear_successes, context_bear_samples) if context_dir < 0 else "N/A"
    execution_position_text = f"{execution_position[last_index]:.0f}% | {execution_zone}" if np.isfinite(execution_position[last_index]) else "N/A"
    context_position_text = f"{context_position[last_index]:.0f}% | {context_zone}" if np.isfinite(context_position[last_index]) else "N/A"
    execution_break_text = f"{execution_break_strength[last_index]:.1f}%" if np.isfinite(execution_break_strength[last_index]) else "N/A"
    context_break_text = f"{context_break_strength[last_index]:.1f}%" if np.isfinite(context_break_strength[last_index]) else "N/A"
    tables.append({
        "position": "top-right",
        "backgroundColor": "rgba(15,23,42,0.92)",
        "borderColor": range_color(context_dir),
        "textColor": "#F8FAFC",
        "accentColor": range_color(execution_dir),
        "rows": [
            ["Context", f"{resolved_context} {direction_text(context_dir)}"],
            ["Execution", f"{resolved_execution} {direction_text(execution_dir)}"],
            ["Alignment", alignment],
            ["Read", confirmation],
            ["Exec Stat", execution_stat if show_statistics else "Off"],
            ["State Stat", state_stat if show_statistics else "Off"],
            ["Context Stat", context_stat if show_statistics else "Off"],
            ["Context Pos", context_position_text],
            ["Exec Pos", execution_position_text],
            ["Break", f"C {context_break_text} | E {execution_break_text}"],
            ["Age", f"C {context_age[last_index]} | E {execution_age[last_index]}"],
        ],
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": layers,
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "resolvedExecutionTimeframe": resolved_execution,
        "resolvedContextTimeframe": resolved_context,
        "rawCountByTimeframe": raw_count_by_timeframe,
        "executionSourceIndex": [int(value) for value in execution_source_index],
        "contextSourceIndex": [int(value) for value in context_source_index],
        "executionHigh": finite(execution_high),
        "executionQ75": finite(execution_q75),
        "executionMid": finite(execution_mid),
        "executionQ25": finite(execution_q25),
        "executionLow": finite(execution_low),
        "executionDirection": [int(value) for value in execution_direction],
        "executionSeedTime": finite(execution_seed_time),
        "executionBreakStrength": finite(execution_break_strength),
        "executionPosition": finite(execution_position),
        "contextHigh": finite(context_high),
        "contextMid": finite(context_mid),
        "contextLow": finite(context_low),
        "contextDirection": [int(value) for value in context_direction],
        "contextSeedTime": finite(context_seed_time),
        "contextBreakStrength": finite(context_break_strength),
        "contextPosition": finite(context_position),
        "newExecutionGrid": [bool(value) for value in new_execution_grid],
        "newContextGrid": [bool(value) for value in new_context_grid],
        "bullishExecutionReset": [bool(value) for value in bullish_execution_reset],
        "bearishExecutionReset": [bool(value) for value in bearish_execution_reset],
        "bullishContextReset": [bool(value) for value in bullish_context_reset],
        "bearishContextReset": [bool(value) for value in bearish_context_reset],
        "lowerSweep": [bool(value) for value in lower_sweep],
        "upperSweep": [bool(value) for value in upper_sweep],
        "midReclaim": [bool(value) for value in mid_reclaim],
        "midLoss": [bool(value) for value in mid_loss],
        "executionAge": [int(value) for value in execution_age],
        "contextAge": [int(value) for value in context_age],
        "statistics": {
            "execBull": [int(exec_bull_successes), int(exec_bull_samples)],
            "execBear": [int(exec_bear_successes), int(exec_bear_samples)],
            "alignedBull": [int(aligned_bull_successes), int(aligned_bull_samples)],
            "alignedBear": [int(aligned_bear_successes), int(aligned_bear_samples)],
            "bullPullback": [int(bull_pullback_recoveries), int(bull_pullback_samples)],
            "bearPullback": [int(bear_pullback_recoveries), int(bear_pullback_samples)],
            "contextBull": [int(context_bull_successes), int(context_bull_samples)],
            "contextBear": [int(context_bear_successes), int(context_bear_samples)],
        },
    },
}
