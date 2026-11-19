# ============================================================
# QuantDinger Market Structure Breakers
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Market Structure Breakers"
my_indicator_description = "Builds market-structure levels from confirmed swings, tracks breaker counts, and marks support and resistance retests."

# @param swing_period int 20 Swings period range=2:200:1
# @param maximum_breaks int 1 Maximum breaks range=1:20:1
# @param maximum_duration int 1000 Breaker maximum duration range=1:10000:1
# @param bullish_structure_color color #089981 Bullish structure color
# @param bullish_breaker_color color #F23645 Bullish breaker color
# @param bearish_structure_color color #F23645 Bearish structure color
# @param bearish_breaker_color color #089981 Bearish breaker color

swing_period = min(200, max(2, int(params.get("swing_period", 20))))
maximum_breaks = min(20, max(1, int(params.get("maximum_breaks", 1))))
maximum_duration = min(10000, max(1, int(params.get("maximum_duration", 1000))))
bullish_structure_color = str(params.get("bullish_structure_color", "#089981"))
bullish_breaker_color = str(params.get("bullish_breaker_color", "#F23645"))
bearish_structure_color = str(params.get("bearish_structure_color", "#F23645"))
bearish_breaker_color = str(params.get("bearish_breaker_color", "#089981"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


frame = df.copy().reset_index(drop=True)
if "time" not in frame.columns:
    frame["time"] = np.arange(len(frame), dtype=float) * 3600000.0
if "volume" not in frame.columns:
    frame["volume"] = 0.0
for column in ("time", "open", "high", "low", "close", "volume"):
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
frame = frame.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
times = frame["time"].to_numpy(dtype=float)
if len(times) and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0
open_price = frame["open"].to_numpy(dtype=float)
high = frame["high"].to_numpy(dtype=float)
low = frame["low"].to_numpy(dtype=float)
close = frame["close"].to_numpy(dtype=float)
n = len(frame)

confirmed_pivot_high = np.full(n, np.nan, dtype=float)
confirmed_pivot_low = np.full(n, np.nan, dtype=float)
pivot_high_index = np.full(n, -1, dtype=int)
pivot_low_index = np.full(n, -1, dtype=int)
last_high = np.nan
last_low = np.nan
last_high_index = 0
last_low_index = 0
for index in range(n):
    candidate_index = index - swing_period
    if candidate_index >= swing_period and candidate_index + swing_period < n:
        candidate_high = high[candidate_index]
        left_high = high[candidate_index - swing_period:candidate_index]
        right_high = high[candidate_index + 1:candidate_index + swing_period + 1]
        if candidate_high > np.max(left_high) and candidate_high >= np.max(right_high):
            last_high = candidate_high
            last_high_index = candidate_index
        candidate_low = low[candidate_index]
        left_low = low[candidate_index - swing_period:candidate_index]
        right_low = low[candidate_index + 1:candidate_index + swing_period + 1]
        if candidate_low < np.min(left_low) and candidate_low <= np.min(right_low):
            last_low = candidate_low
            last_low_index = candidate_index
    confirmed_pivot_high[index] = last_high
    confirmed_pivot_low[index] = last_low
    pivot_high_index[index] = last_high_index
    pivot_low_index[index] = last_low_index

bull_breakers = []
bear_breakers = []
completed_breakers = []
structure_layers = []
structure_labels = []
high_crossed = False
low_crossed = False
previous_high = np.nan
previous_low = np.nan
order_state = 0
bullish_structure = np.zeros(n, dtype=bool)
bearish_structure = np.zeros(n, dtype=bool)
break_down = np.zeros(n, dtype=bool)
break_up = np.zeros(n, dtype=bool)
support = np.zeros(n, dtype=bool)
resistance = np.zeros(n, dtype=bool)
active_bull_count = np.zeros(n, dtype=int)
active_bear_count = np.zeros(n, dtype=int)

for index in range(n):
    pivot_high = confirmed_pivot_high[index]
    pivot_low = confirmed_pivot_low[index]
    if np.isfinite(pivot_high) and (not np.isfinite(previous_high) or pivot_high != previous_high):
        high_crossed = False
        previous_high = pivot_high
    if np.isfinite(pivot_low) and (not np.isfinite(previous_low) or pivot_low != previous_low):
        low_crossed = False
        previous_low = pivot_low

    if np.isfinite(pivot_high) and close[index] > pivot_high and not high_crossed:
        bullish_structure[index] = True
        start_index = int(pivot_high_index[index])
        structure_layers.append((start_index, index, float(pivot_high), bullish_structure_color))
        structure_labels.append((int((start_index + index) / 2), float(pivot_high), "MSS" if order_state == -1 else "MSB", bullish_structure_color, "down"))
        bull_breakers.insert(0, {"start": index, "end": index, "level": float(pivot_high), "breaks": 0, "color": bullish_breaker_color})
        high_crossed = True
        if len(bull_breakers) > 100:
            completed_breakers.append(bull_breakers.pop())
        order_state = 1

    for breaker_index in range(len(bull_breakers) - 1, -1, -1):
        breaker = bull_breakers[breaker_index]
        breaker["end"] = index
        if close[index] < breaker["level"] and open_price[index] > breaker["level"]:
            breaker["breaks"] += 1
            if breaker["breaks"] == maximum_breaks:
                completed_breakers.append(bull_breakers.pop(breaker_index))
                break_down[index] = True
        elif index - breaker["start"] >= maximum_duration:
            bull_breakers.pop(breaker_index)
    if bull_breakers:
        newest_level = bull_breakers[0]["level"]
        support[index] = low[index] < newest_level and min(close[index], open_price[index]) > newest_level

    if np.isfinite(pivot_low) and close[index] < pivot_low and not low_crossed:
        bearish_structure[index] = True
        start_index = int(pivot_low_index[index])
        structure_layers.append((start_index, index, float(pivot_low), bearish_structure_color))
        structure_labels.append((int((start_index + index) / 2), float(pivot_low), "MSS" if order_state == 1 else "MSB", bearish_structure_color, "up"))
        bear_breakers.insert(0, {"start": index, "end": index, "level": float(pivot_low), "breaks": 0, "color": bearish_breaker_color})
        low_crossed = True
        if len(bear_breakers) > 100:
            completed_breakers.append(bear_breakers.pop())
        order_state = -1

    for breaker_index in range(len(bear_breakers) - 1, -1, -1):
        breaker = bear_breakers[breaker_index]
        breaker["end"] = index
        if close[index] > breaker["level"] and open_price[index] < breaker["level"]:
            breaker["breaks"] += 1
            if breaker["breaks"] == maximum_breaks:
                completed_breakers.append(bear_breakers.pop(breaker_index))
                break_up[index] = True
        elif index - breaker["start"] >= maximum_duration:
            bear_breakers.pop(breaker_index)
    if bear_breakers:
        newest_level = bear_breakers[0]["level"]
        resistance[index] = high[index] > newest_level and max(close[index], open_price[index]) < newest_level
    active_bull_count[index] = len(bull_breakers)
    active_bear_count[index] = len(bear_breakers)

layers = []
for start, end, level, color in structure_layers:
    layers.append({"type": "line", "role": "Market Structure", "start": float(times[start]), "end": float(times[end]), "y1": level, "y2": level, "color": color, "lineWidth": 1})
for index, price, text, color, direction in structure_labels:
    layers.append({"type": "label", "index": index, "price": price, "text": text, "textColor": color, "color": "rgba(0,0,0,0)", "direction": direction, "size": "small"})
for breaker in completed_breakers + bull_breakers + bear_breakers:
    layers.append({"type": "line", "role": "Breaker", "start": float(times[breaker["start"]]), "end": float(times[breaker["end"]]), "y1": breaker["level"], "y2": breaker["level"], "color": breaker["color"], "lineWidth": 1, "dashed": True})

plots = [{"name": "Structure State", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True}]
signals = [
    {"type": "buy", "name": "Bull MSB Breakout", "text": "▲", "data": [float(low[index]) if break_up[index] else None for index in range(n)], "color": bullish_structure_color, "renderMode": "events"},
    {"type": "sell", "name": "Bear MSB Breakout", "text": "▼", "data": [float(high[index]) if break_down[index] else None for index in range(n)], "color": bearish_structure_color, "renderMode": "events"},
    {"type": "buy", "name": "Support Event", "text": "", "data": [float(low[index]) if support[index] else None for index in range(n)], "color": bullish_structure_color, "renderMode": "events"},
    {"type": "sell", "name": "Resistance Event", "text": "", "data": [float(high[index]) if resistance[index] else None for index in range(n)], "color": bearish_structure_color, "renderMode": "events"},
]

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers, "tables": [],
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "pivotHigh": _finite(confirmed_pivot_high), "pivotLow": _finite(confirmed_pivot_low), "pivotHighIndex": pivot_high_index.tolist(), "pivotLowIndex": pivot_low_index.tolist(),
        "bullishStructure": bullish_structure.tolist(), "bearishStructure": bearish_structure.tolist(), "breakDown": break_down.tolist(), "breakUp": break_up.tolist(),
        "support": support.tolist(), "resistance": resistance.tolist(), "activeBullCount": active_bull_count.tolist(), "activeBearCount": active_bear_count.tolist(),
        "bullBreakers": bull_breakers, "bearBreakers": bear_breakers, "completedBreakers": completed_breakers,
    },
}
