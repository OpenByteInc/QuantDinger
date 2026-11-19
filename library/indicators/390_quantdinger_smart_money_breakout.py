# ============================================================
# QuantDinger Smart Money Breakout
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Smart Money Breakout"
my_indicator_description = "Detects pivot-based breaks of structure, distinguishes change-of-character events, and tracks three range-derived objectives."

# @param swing_size int 25 Market structure time horizon range=1:500:1
# @param confirmation str Close Breakout confirmation values=Close,Wicks
# @param show_choch bool true Show change-of-character labels
# @param bullish_color str #00FFBB Bullish color
# @param bearish_color str #FF1100 Bearish color
# @param show_statistics bool true Show breakout statistics

swing_size = min(500, max(1, int(params.get("swing_size", 25))))
confirmation = str(params.get("confirmation", "Close")); show_choch = bool(params.get("show_choch", True)); bullish_color = str(params.get("bullish_color", "#00FFBB")); bearish_color = str(params.get("bearish_color", "#FF1100")); show_statistics = bool(params.get("show_statistics", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _confirmed_pivots(values, length, high_pivot):
    result = np.full(len(values), np.nan)
    for confirmed in range(length * 2, len(values)):
        center = confirmed - length; window = values[center-length:center+length+1]; value = values[center]
        if value == (np.max(window) if high_pivot else np.min(window)): result[confirmed] = value
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns: df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened, high, low, close = (df[key].to_numpy(float) for key in ("open", "high", "low", "close")); n = len(df)
pivot_high = _confirmed_pivots(high, swing_size, True); pivot_low = _confirmed_pivots(low, swing_size, False)
higher_high = np.zeros(n, bool); lower_high = np.zeros(n, bool); higher_low = np.zeros(n, bool); lower_low = np.zeros(n, bool); bullish_break = np.zeros(n, bool); bearish_break = np.zeros(n, bool)
previous_high = previous_low = np.nan; previous_high_index = previous_low_index = -1; high_active = low_active = False; previous_break_direction = 0
structure_high = np.full(n, np.nan); structure_low = np.full(n, np.nan); structure_high_index = np.full(n, -1, int); structure_low_index = np.full(n, -1, int); breakout_label = [None] * n
active_state = np.zeros(n, int); target_full = np.full(n, np.nan); target_two_thirds = np.full(n, np.nan); target_one_third = np.full(n, np.nan); stop_level = np.full(n, np.nan); distance_history = np.full(n, np.nan)
active = active_mid = active_near = 0; target = target_mid = target_near = stop = np.nan; total = full_hits = mid_hits = near_hits = 0; risk_records = []; active_record = None; layers = []; previous_trigger = False

for index in range(n):
    if np.isfinite(pivot_high[index]):
        if np.isfinite(previous_high) and pivot_high[index] >= previous_high: higher_high[index] = True
        else: lower_high[index] = True
        previous_high = float(pivot_high[index]); previous_high_index = index - swing_size; high_active = True
    if np.isfinite(pivot_low[index]):
        if np.isfinite(previous_low) and pivot_low[index] >= previous_low: higher_low[index] = True
        else: lower_low[index] = True
        previous_low = float(pivot_low[index]); previous_low_index = index - swing_size; low_active = True
    use_close = confirmation in ("Close", "Candle Close"); high_source = close[index] if use_close else high[index]; low_source = close[index] if use_close else low[index]
    bullish_break[index] = bool(high_active and np.isfinite(previous_high) and high_source > previous_high)
    bearish_break[index] = bool(low_active and np.isfinite(previous_low) and low_source < previous_low)
    if bullish_break[index]: high_active = False
    if bearish_break[index]: low_active = False
    structure_high[index] = previous_high; structure_low[index] = previous_low; structure_high_index[index] = previous_high_index; structure_low_index[index] = previous_low_index
    length = 0
    if bullish_break[index]: length = index - previous_high_index
    if bearish_break[index]: length = index - previous_low_index
    width = max(1, int(length)); first = max(0, index - width + 1); distance = float(np.max(high[first:index+1]) - np.min(low[first:index+1])) / 3.0; distance_history[index] = distance
    trigger = bool(bullish_break[index] or bearish_break[index])
    if bullish_break[index]:
        label = "CHoCH" if previous_break_direction == -1 and show_choch else "BOS"; breakout_label[index] = label; midpoint = int(np.floor(index - (index - previous_high_index) / 2.0)); layers.append({"type": "line", "role": "Bullish Structure Break", "startIndex": int(previous_high_index), "endIndex": int(index), "y1": float(previous_high), "y2": float(previous_high), "color": bullish_color, "lineWidth": 1}); layers.append({"type": "label", "role": label, "index": midpoint, "price": float(previous_high), "text": label, "color": "rgba(0,0,0,0)", "textColor": bullish_color, "size": "tiny"}); previous_break_direction = 1
    if bearish_break[index]:
        label = "CHoCH" if previous_break_direction == 1 and show_choch else "BOS"; breakout_label[index] = label; midpoint = int(np.floor(index - (index - previous_low_index) / 2.0)); layers.append({"type": "line", "role": "Bearish Structure Break", "startIndex": int(previous_low_index), "endIndex": int(index), "y1": float(previous_low), "y2": float(previous_low), "color": bearish_color, "lineWidth": 1}); layers.append({"type": "label", "role": label, "index": midpoint, "price": float(previous_low), "text": label, "color": "rgba(0,0,0,0)", "textColor": bearish_color, "size": "tiny"}); previous_break_direction = -1
    if bullish_break[index]:
        if active_record is not None and active_record["endIndex"] is None: active_record["endIndex"] = max(active_record["startIndex"], index - 1)
        active = active_mid = active_near = 1; total += 1; target = previous_high + distance; target_mid = previous_high + distance * 2.0 / 3.0; target_near = previous_high + distance / 3.0; stop = previous_high - distance; active_record = {"direction": 1, "originIndex": int(previous_high_index), "startIndex": int(index), "endIndex": None, "full": float(target), "mid": float(target_mid), "near": float(target_near), "stop": float(stop)}; risk_records.append(active_record)
    if bearish_break[index]:
        if active_record is not None and active_record["endIndex"] is None: active_record["endIndex"] = max(active_record["startIndex"], index - 1)
        active = active_mid = active_near = -1; total += 1; target = previous_low - distance; target_mid = previous_low - distance * 2.0 / 3.0; target_near = previous_low - distance / 3.0; stop = previous_low + distance; active_record = {"direction": -1, "originIndex": int(previous_low_index), "startIndex": int(index), "endIndex": None, "full": float(target), "mid": float(target_mid), "near": float(target_near), "stop": float(stop)}; risk_records.append(active_record)
    if active == 1 and high[index] > target: full_hits += 1
    if active_mid == 1 and high[index] > target_mid: mid_hits += 1; active_mid = 0
    if active_near == 1 and high[index] > target_near: near_hits += 1; active_near = 0
    if active == -1 and low[index] < target: full_hits += 1
    if active_mid == -1 and low[index] < target_mid: mid_hits += 1; active_mid = 0
    if active_near == -1 and low[index] < target_near: near_hits += 1; active_near = 0
    terminal = (active == 1 and (high[index] > target or low[index] < stop)) or (active == -1 and (low[index] < target or high[index] > stop))
    if terminal and not (trigger and not previous_trigger):
        active = 0
        if active_record is not None and active_record["endIndex"] is None: active_record["endIndex"] = index
    active_state[index] = active
    if active != 0:
        target_full[index] = target; target_two_thirds[index] = target_mid; target_one_third[index] = target_near; stop_level[index] = stop
    previous_trigger = trigger

if active_record is not None and active_record["endIndex"] is None: active_record["endIndex"] = n - 1
for record in risk_records:
    end_index = int(record["endIndex"]); color = bullish_color if record["direction"] == 1 else bearish_color
    for role, value, opacity in (("Target 3", record["full"], 0.30), ("Target 2", record["mid"], 0.50), ("Target 1", record["near"], 0.70)):
        layers.append({"type": "line", "role": role, "startIndex": int(record["originIndex"]), "endIndex": end_index, "y1": value, "y2": value, "color": color, "opacity": opacity, "lineWidth": 1, "lineStyle": "dashed"})
    layers.append({"type": "zone", "role": "Target Zone", "startIndex": int(record["originIndex"]), "endIndex": end_index, "top": float(max(record["full"], record["mid"])), "bottom": float(min(record["full"], record["mid"])), "fillColor": color, "borderColor": color, "opacity": 0.10})

bar_colors = ["rgba(128,128,128,0.60)" if state == 0 else bullish_color if state == 1 else bearish_color for state in active_state]
tables = []
if show_statistics:
    def _rate(count): return "No Trades" if total == 0 else f"{count / total * 100.0:.2f}%"
    tables.append({"title": "Smart Money Breakout Stats", "position": "top-right", "compact": True, "rows": [[{"text": "Total Signals"}, {"text": str(total)}], [{"text": "TP1 Win Rate"}, {"text": _rate(near_hits)}], [{"text": "TP2 Win Rate"}, {"text": _rate(mid_hits)}], [{"text": "TP3 Win Rate"}, {"text": _rate(full_hits)}]]})

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [{"name": "Target 3 Active", "title": False, "data": _finite(target_full), "color": bullish_color, "type": "line", "lineWidth": 1, "dashed": True, "overlay": True}, {"name": "Target 2 Active", "title": False, "data": _finite(target_two_thirds), "color": bullish_color, "type": "line", "lineWidth": 1, "dashed": True, "overlay": True}, {"name": "Target 1 Active", "title": False, "data": _finite(target_one_third), "color": bullish_color, "type": "line", "lineWidth": 1, "dashed": True, "overlay": True}, {"name": "Stop Active", "title": False, "data": _finite(stop_level), "color": bearish_color, "type": "line", "lineWidth": 1, "dashed": True, "overlay": True}, {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "overlay": True}],
    "signals": [{"type": "buy", "name": "Bullish Breakout", "text": "BOS", "data": [{"active": True, "price": float(low[index]), "text": "BOS", "color": bullish_color} if bullish_break[index] else None for index in range(n)], "color": bullish_color, "renderMode": "events", "overlay": True}, {"type": "sell", "name": "Bearish Breakout", "text": "BOS", "data": [{"active": True, "price": float(high[index]), "text": "BOS", "color": bearish_color} if bearish_break[index] else None for index in range(n)], "color": bearish_color, "renderMode": "events", "overlay": True}],
    "layers": layers, "tables": tables, "bar_colors": bar_colors, "background_colors": [None] * n,
    "calculatedVars": {"pivotHigh": _finite(pivot_high), "pivotLow": _finite(pivot_low), "higherHigh": higher_high.tolist(), "lowerHigh": lower_high.tolist(), "higherLow": higher_low.tolist(), "lowerLow": lower_low.tolist(), "bullishBreak": bullish_break.tolist(), "bearishBreak": bearish_break.tolist(), "previousHigh": _finite(structure_high), "previousLow": _finite(structure_low), "previousHighIndex": structure_high_index.tolist(), "previousLowIndex": structure_low_index.tolist(), "breakoutLabel": breakout_label, "distance": _finite(distance_history), "activeState": active_state.tolist(), "targetFull": _finite(target_full), "targetTwoThirds": _finite(target_two_thirds), "targetOneThird": _finite(target_one_third), "stopLevel": _finite(stop_level), "totalSignals": int(total), "targetOneHits": int(near_hits), "targetTwoHits": int(mid_hits), "targetThreeHits": int(full_hits), "riskRecords": risk_records},
}
