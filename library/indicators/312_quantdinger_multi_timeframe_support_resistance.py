# ============================================================
# QuantDinger Multi-Timeframe Support and Resistance
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Multi-Timeframe Support and Resistance"
my_indicator_description = "Projects confirmed wick or body pivots from the chart and seven same-symbol timeframes."
indicator_timeframes = ["5m", "15m", "30m", "1H", "4H", "1D", "1W"]

# @param pivot_length int 15 Pivot range=1:100:1
# @param pivot_type str Body Pivot source values=Wick,Body
# @param show_current bool true Show current timeframe
# @param show_5m bool false Show 5m
# @param show_15m bool false Show 15m
# @param show_30m bool false Show 30m
# @param show_1h bool false Show 1H
# @param show_4h bool false Show 4H
# @param show_1d bool false Show 1D
# @param show_1w bool false Show 1W
# @param show_ama bool false Show adaptive moving average
# @param ama_length int 15 Adaptive moving average length range=1:500:1

pivot_length = min(100, max(1, int(params.get("pivot_length", 15))))
pivot_type = str(params.get("pivot_type", "Body"))
if pivot_type not in ("Wick", "Body"):
    pivot_type = "Body"
show_current = bool(params.get("show_current", True))
show_flags = {
    "5m": bool(params.get("show_5m", False)), "15m": bool(params.get("show_15m", False)),
    "30m": bool(params.get("show_30m", False)), "1H": bool(params.get("show_1h", False)),
    "4H": bool(params.get("show_4h", False)), "1D": bool(params.get("show_1d", False)),
    "1W": bool(params.get("show_1w", False)),
}
show_ama = bool(params.get("show_ama", False))
ama_length = min(500, max(1, int(params.get("ama_length", 15))))
timeframe_seconds = {"5m": 300, "15m": 900, "30m": 1800, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}


def _normalize(frame):
    result = frame.copy().reset_index(drop=True)
    if "time" not in result.columns:
        result["time"] = np.arange(len(result), dtype=float) * 60.0
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
    times = result["time"].to_numpy(dtype=float)
    if len(times) and max(abs(float(value)) for value in times) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result


def _confirmed_pivots(frame):
    high_source = frame["high"].to_numpy(dtype=float) if pivot_type == "Wick" else frame["close"].to_numpy(dtype=float)
    low_source = frame["low"].to_numpy(dtype=float) if pivot_type == "Wick" else frame["close"].to_numpy(dtype=float)
    count = len(frame)
    raw_high = np.full(count, np.nan, dtype=float)
    raw_low = np.full(count, np.nan, dtype=float)
    for confirmation in range(pivot_length * 2, count):
        center = confirmation - pivot_length
        high_window = high_source[center - pivot_length:center + pivot_length + 1]
        low_window = low_source[center - pivot_length:center + pivot_length + 1]
        if high_source[center] == np.max(high_window) and np.count_nonzero(high_window == high_source[center]) == 1:
            raw_high[confirmation] = float(high_source[center])
        if low_source[center] == np.min(low_window) and np.count_nonzero(low_window == low_source[center]) == 1:
            raw_low[confirmation] = float(low_source[center])
    delayed_high = np.full(count, np.nan, dtype=float)
    delayed_low = np.full(count, np.nan, dtype=float)
    if count > 1:
        delayed_high[1:] = raw_high[:-1]
        delayed_low[1:] = raw_low[:-1]
    carried_high = np.full(count, np.nan, dtype=float)
    carried_low = np.full(count, np.nan, dtype=float)
    last_high = np.nan
    last_low = np.nan
    for index in range(count):
        if np.isfinite(delayed_high[index]):
            last_high = delayed_high[index]
        if np.isfinite(delayed_low[index]):
            last_low = delayed_low[index]
        carried_high[index] = last_high
        carried_low[index] = last_low
    return carried_high, carried_low


def _timeframe(value, fallback="1H"):
    text = str(value or fallback).strip()
    aliases = {"5": "5m", "15": "15m", "30": "30m", "60": "1H", "240": "4H", "D": "1D", "W": "1W"}
    normalized = aliases.get(text, text)
    return normalized if normalized in timeframe_seconds else fallback


df = df.copy()
base_frame = _normalize(df)
base_time = base_frame["time"].to_numpy(dtype=float)
n = len(base_frame)
runtime_ctx = globals().get("ctx")
chart_timeframe = _timeframe(str(getattr(runtime_ctx, "timeframe", "") or "1H"))
chart_seconds = timeframe_seconds.get(chart_timeframe, 3600)


def _align_completed(source_frame, values, source_seconds, direct):
    if direct and len(values) == n:
        return np.asarray(values, dtype=float).copy()
    result = np.full(n, np.nan, dtype=float)
    if source_frame is None or len(source_frame) == 0:
        return result
    source_time = source_frame["time"].to_numpy(dtype=float)
    source_close = source_time + source_seconds
    for index in range(n):
        current_close = base_time[index] + chart_seconds
        source_index = int(np.searchsorted(source_close, current_close, side="right") - 1)
        if 0 <= source_index < len(values) and np.isfinite(values[source_index]):
            result[index] = float(values[source_index])
    return result


def _frame_result(timeframe):
    if timeframe == "current":
        frame, seconds, direct, available = base_frame, chart_seconds, True, True
    elif timeframe == chart_timeframe:
        frame, seconds, direct, available = base_frame, chart_seconds, True, True
    else:
        raw = runtime_ctx.raw_series(timeframe) if runtime_ctx is not None and hasattr(runtime_ctx, "raw_series") else None
        available = raw is not None and len(raw) > 0
        frame = _normalize(raw) if available else None
        seconds, direct = timeframe_seconds[timeframe], False
    if not available:
        return {"available": False, "high": np.full(n, np.nan), "low": np.full(n, np.nan)}
    pivot_high, pivot_low = _confirmed_pivots(frame)
    return {
        "available": True,
        "high": _align_completed(frame, pivot_high, seconds, direct),
        "low": _align_completed(frame, pivot_low, seconds, direct),
    }


results = {"current": _frame_result("current")}
for timeframe in indicator_timeframes:
    results[timeframe] = _frame_result(timeframe)


def _offset_display(values, visible):
    display = [None] * n
    if not visible:
        return display
    offset = pivot_length + 1
    for index, value in enumerate(values):
        target = index - offset
        if target < 0 or not np.isfinite(value):
            continue
        changed = index >= 1 and (not np.isfinite(values[index - 1]) or abs(float(value) - float(values[index - 1])) > 1e-12)
        display[target] = None if changed else float(value)
    return display


line_styles = {
    "5m": ("#00AEFF", "#FFBF00"), "15m": ("rgba(0,174,255,0.87)", "rgba(255,191,0,0.87)"),
    "30m": ("rgba(0,174,255,0.73)", "rgba(255,191,0,0.73)"), "1H": ("rgba(0,174,255,0.56)", "rgba(255,191,0,0.56)"),
    "4H": ("rgba(0,174,255,0.44)", "rgba(255,191,0,0.44)"), "1D": ("rgba(0,174,255,0.31)", "rgba(255,191,0,0.31)"),
    "1W": ("rgba(0,174,255,0.19)", "rgba(255,191,0,0.19)"), "current": ("#FF0040", "#6AFF00"),
}
names = {"5m": "M5", "15m": "M15", "30m": "M30", "1H": "H1", "4H": "H4", "1D": "Daily", "1W": "Weekly", "current": "Current Timeframe"}
plots = []
layers = []


def _level_layers(values, role, color):
    segments = []
    current_value = np.nan
    current_start = 0
    offset = pivot_length + 1
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(current_value) or abs(float(value) - float(current_value)) > 1e-12:
            display_start = max(0, index - offset)
            if np.isfinite(current_value):
                segments.append((current_start, max(current_start, display_start - 1), float(current_value)))
            current_start = display_start
            current_value = float(value)
    if np.isfinite(current_value):
        segments.append((current_start, n - 1, float(current_value)))
    return [
        {"type": "line", "role": f"{role} {segment_index + 1}", "startIndex": int(start), "endIndex": int(end), "y1": price, "y2": price, "color": color, "lineWidth": 2}
        for segment_index, (start, end, price) in enumerate(segments[-8:])
    ]


for timeframe in indicator_timeframes + ["current"]:
    visible = show_current if timeframe == "current" else show_flags[timeframe]
    if not visible:
        continue
    resistance_color, support_color = line_styles[timeframe]
    layers.extend(_level_layers(results[timeframe]["high"], f"Resistance {names[timeframe]}", resistance_color))
    layers.extend(_level_layers(results[timeframe]["low"], f"Support {names[timeframe]}", support_color))

close = base_frame["close"].to_numpy(dtype=float)
high = base_frame["high"].to_numpy(dtype=float)
low = base_frame["low"].to_numpy(dtype=float)
rolling_high = np.full(n, np.nan, dtype=float)
rolling_low = np.full(n, np.nan, dtype=float)
trigger = np.zeros(n, dtype=float)
ama = np.full(n, np.nan, dtype=float)
for index in range(n):
    start = max(0, index - ama_length + 1)
    rolling_high[index] = float(np.max(high[start:index + 1]))
    rolling_low[index] = float(np.min(low[start:index + 1]))
    if index >= 1:
        higher = 1.0 if rolling_high[index] > rolling_high[index - 1] else 0.0
        lower = 1.0 if rolling_low[index] < rolling_low[index - 1] else 0.0
        trigger[index] = 1.0 if higher or lower else 0.0
    if index >= ama_length - 1:
        coefficient = float(np.mean(trigger[index - ama_length + 1:index + 1]) ** 2)
    else:
        coefficient = np.nan
    previous = ama[index - 1] if index >= 1 else np.nan
    candidate = previous + coefficient * (close[index] - previous) if np.all(np.isfinite([previous, coefficient])) else np.nan
    ama[index] = candidate if np.isfinite(candidate) else close[index]
if show_ama:
    plots.append({"name": "Adaptive Moving Average", "data": [float(value) for value in ama], "color": "#FFFFFF", "type": "line", "lineWidth": 2, "overlay": True})
else:
    plots.append({"name": "Price Guide", "title": False, "data": [float(value) for value in close], "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True})


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": layers,
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "pivotLength": pivot_length, "pivotType": pivot_type, "chartTimeframe": chart_timeframe,
        "timeframeAvailable": {key: bool(value["available"]) for key, value in results.items()},
        "resistance": {key: _finite(value["high"]) for key, value in results.items()},
        "support": {key: _finite(value["low"]) for key, value in results.items()},
        "amaRollingHigh": _finite(rolling_high), "amaRollingLow": _finite(rolling_low), "amaTrigger": _finite(trigger), "adaptiveAverage": _finite(ama),
    },
}
