# ============================================================
# QuantDinger Fibonacci Levels MTF
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Fibonacci Levels MTF"
my_indicator_description = (
    "Projects configurable Fibonacci prices from the current or last completed "
    "higher-timeframe candle, with optional price labels and a live HTF candle overlay."
)

indicator_timeframes = ["1H", "4H", "1D", "1W"]

# @param timeframe str 1D Higher timeframe values=1H,4H,1D,1W
# @param candle_mode str Last Higher-timeframe candle values=Current,Last
# @param show_price_labels bool true Show price labels
# @param show_htf_candle bool false Show current HTF candle
# @param up_color str rgba(0,255,0,0.25) Up HTF candle color
# @param down_color str rgba(255,0,0,0.25) Down HTF candle color
# @param wick_color str rgba(128,128,128,0.25) HTF wick color
# @param enable_0 bool true Enable level 0
# @param level_0 float 0.0 Level 0 ratio range=-10.0:10.0:0.001
# @param color_0 str #2962FF Level 0 color
# @param enable_1 bool true Enable level 1
# @param level_1 float 0.236 Level 1 ratio range=-10.0:10.0:0.001
# @param color_1 str #00E676 Level 1 color
# @param enable_2 bool true Enable level 2
# @param level_2 float 0.382 Level 2 ratio range=-10.0:10.0:0.001
# @param color_2 str #F23645 Level 2 color
# @param enable_3 bool true Enable level 3
# @param level_3 float 0.5 Level 3 ratio range=-10.0:10.0:0.001
# @param color_3 str #FF9800 Level 3 color
# @param enable_4 bool true Enable level 4
# @param level_4 float 0.618 Level 4 ratio range=-10.0:10.0:0.001
# @param color_4 str #089981 Level 4 color
# @param enable_5 bool true Enable level 5
# @param level_5 float 0.786 Level 5 ratio range=-10.0:10.0:0.001
# @param color_5 str #1E3A8A Level 5 color
# @param enable_6 bool true Enable level 6
# @param level_6 float 1.0 Level 6 ratio range=-10.0:10.0:0.001
# @param color_6 str #2962FF Level 6 color
# @param enable_7 bool false Enable level 7
# @param level_7 float -0.382 Level 7 ratio range=-10.0:10.0:0.001
# @param color_7 str #787B86 Level 7 color
# @param enable_8 bool false Enable level 8
# @param level_8 float 1.236 Level 8 ratio range=-10.0:10.0:0.001
# @param color_8 str #787B86 Level 8 color

selected_timeframe = str(params.get("timeframe", "1D")).upper()
candle_mode = str(params.get("candle_mode", "Last")).lower()
show_price_labels = bool(params.get("show_price_labels", True))
show_htf_candle = bool(params.get("show_htf_candle", False))
up_color = str(params.get("up_color", "rgba(0,255,0,0.25)"))
down_color = str(params.get("down_color", "rgba(255,0,0,0.25)"))
wick_color = str(params.get("wick_color", "rgba(128,128,128,0.25)"))

levels = [
    float(params.get("level_0", 0.0)), float(params.get("level_1", 0.236)),
    float(params.get("level_2", 0.382)), float(params.get("level_3", 0.5)),
    float(params.get("level_4", 0.618)), float(params.get("level_5", 0.786)),
    float(params.get("level_6", 1.0)), float(params.get("level_7", -0.382)),
    float(params.get("level_8", 1.236)),
]
colors = [
    str(params.get("color_0", "#2962FF")), str(params.get("color_1", "#00E676")),
    str(params.get("color_2", "#F23645")), str(params.get("color_3", "#FF9800")),
    str(params.get("color_4", "#089981")), str(params.get("color_5", "#1E3A8A")),
    str(params.get("color_6", "#2962FF")), str(params.get("color_7", "#787B86")),
    str(params.get("color_8", "#787B86")),
]
enabled = [
    bool(params.get("enable_0", True)), bool(params.get("enable_1", True)),
    bool(params.get("enable_2", True)), bool(params.get("enable_3", True)),
    bool(params.get("enable_4", True)), bool(params.get("enable_5", True)),
    bool(params.get("enable_6", True)), bool(params.get("enable_7", False)),
    bool(params.get("enable_8", False)),
]

timeframe_seconds = {"1M": 60, "5M": 300, "15M": 900, "30M": 1800, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
runtime_context = globals().get("ctx")
chart_timeframe = str(getattr(runtime_context, "timeframe", "1H") or "1H").upper()
chart_seconds = int(timeframe_seconds.get(chart_timeframe, 3600))
requested_seconds = int(timeframe_seconds.get(selected_timeframe, 86400))
timeframe_clamped = requested_seconds < chart_seconds
resolved_timeframe = chart_timeframe if timeframe_clamped else selected_timeframe
resolved_seconds = max(chart_seconds, requested_seconds)


def normalize_ohlc(raw):
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    result = raw.copy().reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = np.arange(len(result), dtype=float) * float(chart_seconds) if column == "time" else np.nan
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["volume"] = result["volume"].fillna(0.0)
    result = result.dropna(subset=["time", "open", "high", "low", "close"])
    return result.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


def seconds(values):
    values = np.asarray(values, dtype=float)
    if len(values) and np.nanmax(np.abs(values)) > 10000000000:
        return values / 1000.0
    return values.copy()


def group_starts(values, timeframe_name, duration):
    values = np.asarray(values, dtype=float)
    if timeframe_name == "1W":
        monday_epoch = 4.0 * 86400.0
        return np.floor((values - monday_epoch) / float(duration)) * float(duration) + monday_epoch
    return np.floor(values / float(duration)) * float(duration)


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


base_input = df.copy()
frame = normalize_ohlc(base_input)
n = len(frame)
time_values = frame["time"].to_numpy(dtype=float)
time_seconds = seconds(time_values)
open_values = frame["open"].to_numpy(dtype=float)
high_values = frame["high"].to_numpy(dtype=float)
low_values = frame["low"].to_numpy(dtype=float)
close_values = frame["close"].to_numpy(dtype=float)

groups = group_starts(time_seconds, resolved_timeframe, resolved_seconds)
new_group = np.zeros(n, dtype=bool)
if n:
    new_group[0] = True
if n > 1:
    new_group[1:] = groups[1:] != groups[:-1]

current_open = np.full(n, np.nan, dtype=float)
current_high = np.full(n, np.nan, dtype=float)
current_low = np.full(n, np.nan, dtype=float)
current_close = close_values.copy()
group_start_index = np.zeros(n, dtype=int)
active_start = 0
for index in range(n):
    if index == 0 or new_group[index]:
        active_start = index
        current_open[index] = open_values[index]
        current_high[index] = high_values[index]
        current_low[index] = low_values[index]
    else:
        current_open[index] = current_open[index - 1]
        current_high[index] = max(current_high[index - 1], high_values[index])
        current_low[index] = min(current_low[index - 1], low_values[index])
    group_start_index[index] = active_start

reference_open = current_open.copy()
reference_high = current_high.copy()
reference_low = current_low.copy()
reference_close = current_close.copy()
reference_index = np.full(n, -1, dtype=int)

if candle_mode == "last":
    raw_higher = (
        runtime_context.raw_series(resolved_timeframe)
        if runtime_context is not None and hasattr(runtime_context, "raw_series")
        else None
    )
    higher = normalize_ohlc(raw_higher)
    if len(higher):
        higher_times = seconds(higher["time"].to_numpy(dtype=float))
        completed_at = higher_times + float(resolved_seconds)
        reference_index = np.searchsorted(completed_at, time_seconds + 1e-9, side="right") - 1
        higher_open = higher["open"].to_numpy(dtype=float)
        higher_high = higher["high"].to_numpy(dtype=float)
        higher_low = higher["low"].to_numpy(dtype=float)
        higher_close = higher["close"].to_numpy(dtype=float)
        reference_open[:] = np.nan
        reference_high[:] = np.nan
        reference_low[:] = np.nan
        reference_close[:] = np.nan
        valid = (reference_index >= 0) & (reference_index < len(higher))
        reference_open[valid] = higher_open[reference_index[valid]]
        reference_high[valid] = higher_high[reference_index[valid]]
        reference_low[valid] = higher_low[reference_index[valid]]
        reference_close[valid] = higher_close[reference_index[valid]]
    else:
        last_summary = None
        current_group = groups[0] if n else np.nan
        for index in range(n):
            if index > 0 and groups[index] != current_group:
                last_summary = (
                    current_open[index - 1], current_high[index - 1],
                    current_low[index - 1], current_close[index - 1],
                )
                current_group = groups[index]
            if last_summary is None:
                reference_open[index] = np.nan
                reference_high[index] = np.nan
                reference_low[index] = np.nan
                reference_close[index] = np.nan
            else:
                reference_open[index], reference_high[index], reference_low[index], reference_close[index] = last_summary

level_values = [np.full(n, np.nan, dtype=float) for _ in range(9)]
for index in range(n):
    if new_group[index]:
        continue
    values = (reference_open[index], reference_high[index], reference_low[index], reference_close[index])
    if not all(np.isfinite(value) for value in values):
        continue
    span = reference_high[index] - reference_low[index]
    bullish = reference_close[index] >= reference_open[index]
    for level_index, ratio in enumerate(levels):
        if enabled[level_index]:
            level_values[level_index][index] = (
                reference_high[index] - span * ratio
                if bullish else
                reference_low[index] + span * ratio
            )

plots = []
for index in range(9):
    if enabled[index]:
        plots.append({
            "name": f"Fibonacci {index + 1} {levels[index]:g}", "title": False,
            "data": finite(level_values[index]), "color": colors[index],
            "type": "line", "lineWidth": 1, "lineStyle": "solid",
            "connectNulls": False, "overlay": True,
        })

layers = []
tables = []
if n and show_price_labels:
    visible = [index for index in range(9) if enabled[index] and np.isfinite(level_values[index][-1])]
    label_rows = []
    active_start = int(group_start_index[-1])
    for index in visible:
        price = float(level_values[index][-1])
        layers.append({
            "type": "line", "role": f"Fibonacci {levels[index]:g}",
            "startIndex": active_start, "endIndex": n - 1,
            "y1": price, "y2": price, "color": colors[index],
            "lineWidth": 1, "dashed": False,
        })
        label_rows.append([
            {"text": f"{levels[index]:.3f}", "color": colors[index]},
            {"text": f"{price:.2f}", "align": "right"},
        ])
    if label_rows:
        tables.append({
            "title": f"Fibonacci · {resolved_timeframe} · {candle_mode.title()}",
            "position": "top-right", "compact": True,
            "columns": ["Ratio", "Price"], "rows": label_rows,
        })

if n and show_htf_candle:
    start_index = int(group_start_index[-1])
    start_time = float(time_values[start_index])
    end_time = float(time_values[-1] + chart_seconds * (1000.0 if np.nanmax(np.abs(time_values)) > 10000000000 else 1.0))
    body_top = float(max(current_open[-1], current_close[-1]))
    body_bottom = float(min(current_open[-1], current_close[-1]))
    candle_color = up_color if current_close[-1] >= current_open[-1] else down_color
    layers.extend([
        {"type": "box", "role": "HTF Candle Body", "start": start_time, "end": end_time, "top": body_top, "bottom": body_bottom, "color": candle_color, "fillColor": candle_color, "borderColor": candle_color, "borderSize": 0, "opacity": 1.0},
        {"type": "box", "role": "HTF Upper Wick", "start": start_time, "end": end_time, "top": float(current_high[-1]), "bottom": body_top, "color": wick_color, "fillColor": wick_color, "borderColor": wick_color, "borderSize": 0, "opacity": 1.0},
        {"type": "box", "role": "HTF Lower Wick", "start": start_time, "end": end_time, "top": body_bottom, "bottom": float(current_low[-1]), "color": wick_color, "fillColor": wick_color, "borderColor": wick_color, "borderSize": 0, "opacity": 1.0},
    ])

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": layers,
    "tables": tables,
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "requestedTimeframe": selected_timeframe, "resolvedTimeframe": resolved_timeframe,
        "timeframeClamped": bool(timeframe_clamped), "candleMode": candle_mode,
        "groupStart": finite(groups), "newGroup": new_group.tolist(),
        "groupStartIndex": group_start_index.tolist(), "referenceIndex": reference_index.tolist(),
        "currentOpen": finite(current_open), "currentHigh": finite(current_high),
        "currentLow": finite(current_low), "currentClose": finite(current_close),
        "referenceOpen": finite(reference_open), "referenceHigh": finite(reference_high),
        "referenceLow": finite(reference_low), "referenceClose": finite(reference_close),
        "levels": [finite(values) for values in level_values],
    },
}
