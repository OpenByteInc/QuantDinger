# ============================================================
# QuantDinger Market Structure Incremental Trailing Stop
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Market Structure Incremental Trailing Stop"
my_indicator_description = "Resets a structure stop on configured breaks and advances it by a percentage of new trend extremes."

# @param pivot_lookback int 14 Pivot lookback range=2:200:1
# @param increment_factor float 100.0 Increment factor percent range=0.0:500.0:1.0
# @param reset_on str CHoCH Reset stop on values=CHoCH,All
# @param show_structures bool true Show structure lines
# @param bullish_color color #008080 Bullish structure color
# @param bearish_color color #FF0000 Bearish structure color
# @param retracement_color color #FF5D00 Retracement color
# @param area_transparency int 80 Area transparency range=0:100:1

pivot_lookback = min(200, max(2, int(params.get("pivot_lookback", 14))))
increment_factor = min(500.0, max(0.0, float(params.get("increment_factor", 100.0))))
reset_on = str(params.get("reset_on", "CHoCH"))
show_structures = bool(params.get("show_structures", True))
bullish_color = str(params.get("bullish_color", "#008080"))
bearish_color = str(params.get("bearish_color", "#FF0000"))
retracement_color = str(params.get("retracement_color", "#FF5D00"))
area_transparency = min(100, max(0, int(params.get("area_transparency", 80))))


def _finite(values): return [float(value) if np.isfinite(value) else None for value in values]


def _rgba(color, transparency):
    text = color.lstrip("#")
    if len(text) == 6:
        red, green, blue = int(text[:2], 16), int(text[2:4], 16), int(text[4:6], 16)
        return f"rgba({red},{green},{blue},{1.0 - transparency / 100.0:.3f})"
    return color


frame = df.copy().reset_index(drop=True)
if "time" not in frame.columns: frame["time"] = np.arange(len(frame), dtype=float) * 3600000.0
if "volume" not in frame.columns: frame["volume"] = 0.0
for column in ("time", "open", "high", "low", "close", "volume"): frame[column] = pd.to_numeric(frame[column], errors="coerce")
frame = frame.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
times = frame["time"].to_numpy(dtype=float)
if len(times) and np.nanmax(np.abs(times)) < 100000000000.0: times = times * 1000.0
high = frame["high"].to_numpy(dtype=float); low = frame["low"].to_numpy(dtype=float); close = frame["close"].to_numpy(dtype=float); n = len(frame)

pivot_high = np.full(n, np.nan); pivot_low = np.full(n, np.nan)
for index in range(2 * pivot_lookback, n):
    candidate = index - pivot_lookback
    if high[candidate] > np.max(high[candidate - pivot_lookback:candidate]) and high[candidate] >= np.max(high[candidate + 1:candidate + pivot_lookback + 1]): pivot_high[index] = high[candidate]
    if low[candidate] < np.min(low[candidate - pivot_lookback:candidate]) and low[candidate] <= np.min(low[candidate + 1:candidate + pivot_lookback + 1]): pivot_low[index] = low[candidate]

high_level = np.nan; low_level = np.nan; high_index = -1; low_index = -1; high_crossed = False; low_crossed = False; order_state = 0; running_max = np.nan; running_min = np.nan; trailing_state = np.nan
market_structure = np.zeros(n, dtype=int); order_history = np.zeros(n, dtype=int); local_bottom = np.full(n, np.nan); local_top = np.full(n, np.nan); running_max_history = np.full(n, np.nan); running_min_history = np.full(n, np.nan); trailing_stop = np.full(n, np.nan); bull_break = np.zeros(n, dtype=bool); bear_break = np.zeros(n, dtype=bool)
structure_lines = []
for index in range(n):
    if np.isfinite(pivot_high[index]): high_level = float(pivot_high[index]); high_index = index - pivot_lookback; high_crossed = False
    if np.isfinite(pivot_low[index]): low_level = float(pivot_low[index]); low_index = index - pivot_lookback; low_crossed = False
    ms = 0
    if np.isfinite(high_level) and close[index] > high_level and not high_crossed:
        ms = 1 if reset_on == "All" or order_state == -1 else 0; bull_break[index] = True; high_crossed = True
        if show_structures: structure_lines.append((high_index, index, high_level, bullish_color, order_state == -1))
        order_state = 1; start = min(index, high_index + 1); local_bottom[index] = float(np.min(low[start:index + 1]))
    if np.isfinite(low_level) and close[index] < low_level and not low_crossed:
        ms = -1 if reset_on == "All" or order_state == 1 else 0; bear_break[index] = True; low_crossed = True
        if show_structures: structure_lines.append((low_index, index, low_level, bearish_color, order_state == 1))
        order_state = -1; start = min(index, low_index + 1); local_top[index] = float(np.max(high[start:index + 1]))
    market_structure[index] = ms; order_history[index] = order_state
    previous_max = running_max; previous_min = running_min
    if ms == 1: running_max = close[index]
    elif ms == -1: running_min = close[index]
    else:
        running_max = max(close[index], running_max) if np.isfinite(running_max) else np.nan
        running_min = min(close[index], running_min) if np.isfinite(running_min) else np.nan
    if ms == 1: trailing_state = local_bottom[index]
    elif ms == -1: trailing_state = local_top[index]
    elif order_state == 1 and np.all(np.isfinite([trailing_state, running_max, previous_max])): trailing_state += (running_max - previous_max) * increment_factor / 100.0
    elif order_state == -1 and np.all(np.isfinite([trailing_state, running_min, previous_min])): trailing_state += (running_min - previous_min) * increment_factor / 100.0
    running_max_history[index] = running_max; running_min_history[index] = running_min; trailing_stop[index] = trailing_state

line_data = [None if market_structure[index] != 0 or not np.isfinite(trailing_stop[index]) else float(trailing_stop[index]) for index in range(n)]
line_colors = [bullish_color if order_history[index] == 1 else bearish_color for index in range(n)]
cloud = []
for index in range(n):
    if market_structure[index] == 0 and np.isfinite(trailing_stop[index]):
        color = retracement_color if (close[index] - trailing_stop[index]) * order_history[index] < 0 else line_colors[index]
        cloud.append({"upper": float(max(close[index], trailing_stop[index])), "lower": float(min(close[index], trailing_stop[index])), "color": _rgba(color, area_transparency)})
    else: cloud.append(None)
layers = [{"type": "line", "role": "Market Structure", "start": float(times[start]), "end": float(times[end]), "y1": level, "y2": level, "color": color, "lineWidth": 1, "dashed": dashed} for start, end, level, color, dashed in structure_lines]
plots = [
    {"name": "Trailing Stop", "data": line_data, "color": line_colors, "type": "line", "style": "linebr", "lineWidth": 1, "overlay": True},
    {"name": "Price/Stop Area", "title": False, "data": cloud, "color": bullish_color, "type": "cloud", "overlay": True},
    {"name": "Structure State", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
]
output = {"schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": [], "layers": layers, "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
          "calculatedVars": {"pivotHigh": _finite(pivot_high), "pivotLow": _finite(pivot_low), "bullBreak": bull_break.tolist(), "bearBreak": bear_break.tolist(), "marketStructure": market_structure.tolist(), "orderState": order_history.tolist(), "localBottom": _finite(local_bottom), "localTop": _finite(local_top), "runningMax": _finite(running_max_history), "runningMin": _finite(running_min_history), "trailingStop": _finite(trailing_stop)}}
