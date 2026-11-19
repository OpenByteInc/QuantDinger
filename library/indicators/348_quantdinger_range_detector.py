# ============================================================
# QuantDinger Range Detector
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Range Detector"
my_indicator_description = "Detects low-dispersion price ranges around a moving average, merges overlapping ranges, and recolors them after upward or downward breaks."

# @param minimum_range_length int 20 Minimum range length range=2:1000:1
# @param range_width float 1.0 ATR range-width multiplier range=0:100:0.1
# @param atr_length int 500 ATR length range=1:5000:1
# @param upward_break_color str #089981 Broken-upward color
# @param downward_break_color str #F23645 Broken-downward color
# @param unbroken_color str #2157F3 Unbroken range color

minimum_range_length = min(1000, max(2, int(params.get("minimum_range_length", 20))))
range_width = min(100.0, max(0.0, float(params.get("range_width", 1.0))))
atr_length = min(5000, max(1, int(params.get("atr_length", 500))))
upward_break_color = str(params.get("upward_break_color", "#089981"))
downward_break_color = str(params.get("downward_break_color", "#F23645"))
unbroken_color = str(params.get("unbroken_color", "#2157F3"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    running = 0.0
    for index, value in enumerate(values):
        running += float(value)
        if index >= length:
            running -= float(values[index - length])
        if index >= length - 1:
            result[index] = running / length
    return result


def _rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if not len(values):
        return result
    result[0] = float(values[0])
    for index in range(1, len(values)):
        result[index] = (result[index - 1] * (length - 1.0) + values[index]) / length
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    true_range[index] = high[index] - low[index] if index == 0 else max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
atr = _rma(true_range, atr_length) * range_width
average = _sma(close, minimum_range_length)
deviation_count = np.zeros(n, dtype=int)
range_top = np.full(n, np.nan, dtype=float)
range_bottom = np.full(n, np.nan, dtype=float)
range_state = np.zeros(n, dtype=int)
detection = np.zeros(n, dtype=bool)
overlap_event = np.zeros(n, dtype=bool)
new_range_event = np.zeros(n, dtype=bool)
upward_break = np.zeros(n, dtype=bool)
downward_break = np.zeros(n, dtype=bool)
ranges = []
active_range = None
top_state = np.nan
bottom_state = np.nan
state = 0
previous_count = 0

for index in range(n):
    count = 0
    for offset in range(minimum_range_length):
        source_index = index - offset
        if source_index >= 0 and np.all(np.isfinite([average[index], atr[index]])) and abs(close[source_index] - average[index]) > atr[index]:
            count += 1
    deviation_count[index] = count
    if count == 0 and previous_count != count:
        left = max(0, index - minimum_range_length + 1)
        if active_range is not None and left <= active_range["right"]:
            active_range["top"] = max(float(average[index] + atr[index]), active_range["top"])
            active_range["bottom"] = min(float(average[index] - atr[index]), active_range["bottom"])
            active_range["right"] = index
            active_range["state"] = 0
            active_range["mid"] = (active_range["top"] + active_range["bottom"]) / 2.0
            top_state, bottom_state = active_range["top"], active_range["bottom"]
            overlap_event[index] = True
        else:
            top_state = float(average[index] + atr[index])
            bottom_state = float(average[index] - atr[index])
            active_range = {"left": left, "right": index, "top": top_state, "bottom": bottom_state, "mid": float(average[index]), "state": 0}
            ranges.append(active_range)
            ranges = ranges[-500:]
            state = 0
            detection[index] = True
            new_range_event[index] = True
    elif count == 0 and active_range is not None:
        active_range["right"] = index

    if active_range is not None:
        prior_state = active_range["state"]
        if close[index] > active_range["top"]:
            active_range["state"] = 1
            state = 1
            upward_break[index] = prior_state != 1
        elif close[index] < active_range["bottom"]:
            active_range["state"] = -1
            state = -1
            downward_break[index] = prior_state != -1
        top_state, bottom_state = active_range["top"], active_range["bottom"]
    range_top[index] = top_state
    range_bottom[index] = bottom_state
    range_state[index] = state
    previous_count = count

top_display = range_top.copy()
bottom_display = range_bottom.copy()
for index in range(n):
    if index == 0 or (np.isfinite(range_top[index]) and (not np.isfinite(range_top[index - 1]) or range_top[index] != range_top[index - 1])):
        top_display[index] = np.nan
    if index == 0 or (np.isfinite(range_bottom[index]) and (not np.isfinite(range_bottom[index - 1]) or range_bottom[index] != range_bottom[index - 1])):
        bottom_display[index] = np.nan

line_colors = [unbroken_color if value == 0 else upward_break_color if value == 1 else downward_break_color for value in range_state]
layers = []
for item in ranges:
    color = unbroken_color if item["state"] == 0 else upward_break_color if item["state"] == 1 else downward_break_color
    layers.append({"type": "zone", "role": "Detected Range", "startIndex": item["left"], "endIndex": item["right"], "top": item["top"], "bottom": item["bottom"], "fillColor": color, "opacity": 0.20, "borderColor": "rgba(0,0,0,0)"})
    layers.append({"type": "line", "role": "Range Mid", "startIndex": item["left"], "endIndex": item["right"], "y1": item["mid"], "y2": item["mid"], "color": color, "dotted": True, "lineWidth": 1})

background_colors = ["rgba(128,128,128,0.20)" if value else None for value in detection]
output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Range Top", "data": _finite(top_display), "color": line_colors, "type": "step", "lineWidth": 1, "overlay": True},
        {"name": "Range Bottom", "data": _finite(bottom_display), "color": line_colors, "type": "step", "lineWidth": 1, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [], "layers": layers, "tables": [], "bar_colors": [None] * n, "background_colors": background_colors,
    "calculatedVars": {
        "trueRange": _finite(true_range), "atrWidth": _finite(atr), "average": _finite(average), "deviationCount": [int(value) for value in deviation_count],
        "rangeTop": _finite(range_top), "rangeBottom": _finite(range_bottom), "rangeState": [int(value) for value in range_state], "detection": detection.tolist(), "overlapEvent": overlap_event.tolist(), "newRangeEvent": new_range_event.tolist(), "upwardBreak": upward_break.tolist(), "downwardBreak": downward_break.tolist(), "ranges": ranges,
    },
}
