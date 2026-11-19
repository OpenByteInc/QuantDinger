# ============================================================
# QuantDinger Range Detector
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Range Detector"
my_indicator_description = (
    "Finds low-dispersion price ranges around a moving average, preserves "
    "overlapping zones, and colors completed ranges by their latest breakout."
)

# @param length int 20 Minimum range length range=2:200:1
# @param width float 1.0 Range width range=0.0:10.0:0.1
# @param atr_length int 100 ATR length range=1:1000:1

length = max(2, int(params.get("length", 20)))
width = max(0.0, float(params.get("width", 1.0)))
atr_length = max(1, int(params.get("atr_length", 100)))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def pine_rma(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    seed = []
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) < period:
                continue
            previous = float(np.mean(seed[-period:]))
        else:
            previous = (previous * (period - 1) + float(value)) / period
        result[index] = previous
    return result


def rolling_mean(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    running_sum = 0.0
    for index, value in enumerate(values):
        running_sum += float(value)
        if index >= period:
            running_sum -= float(values[index - period])
        if index >= period - 1:
            result[index] = running_sum / period
    return result


true_range = np.full(n, np.nan, dtype=float)
if n:
    true_range[0] = high[0] - low[0]
for index in range(1, n):
    true_range[index] = max(
        high[index] - low[index],
        abs(high[index] - close[index - 1]),
        abs(low[index] - close[index - 1]),
    )

atr = pine_rma(true_range, atr_length) * width
average = rolling_mean(close, length)
outside_count = np.full(n, np.nan, dtype=float)
for index in range(length - 1, n):
    if not np.isfinite(average[index]) or not np.isfinite(atr[index]):
        continue
    count = 0
    for offset in range(length):
        if abs(close[index - offset] - average[index]) > atr[index]:
            count += 1
    outside_count[index] = float(count)

ranges = []
current_range = None
active_top = np.full(n, np.nan, dtype=float)
active_bottom = np.full(n, np.nan, dtype=float)
active_state = np.zeros(n, dtype=int)
detection = np.zeros(n, dtype=bool)
state = 0
top_value = np.nan
bottom_value = np.nan

for index in range(n):
    count = outside_count[index]
    previous_count = outside_count[index - 1] if index > 0 else np.nan
    new_range = (
        np.isfinite(count)
        and count == 0.0
        and np.isfinite(previous_count)
        and previous_count != count
    )

    if new_range:
        candidate_top = average[index] + atr[index]
        candidate_bottom = average[index] - atr[index]
        candidate_start = max(0, index - length)
        if current_range is not None and candidate_start <= current_range["end"]:
            top_value = max(candidate_top, current_range["top"])
            bottom_value = min(candidate_bottom, current_range["bottom"])
            current_range["top"] = float(top_value)
            current_range["bottom"] = float(bottom_value)
            current_range["middle"] = float((top_value + bottom_value) / 2.0)
            current_range["end"] = index
            current_range["state"] = 0
        else:
            current_range = {
                "start": candidate_start,
                "end": index,
                "top": float(candidate_top),
                "bottom": float(candidate_bottom),
                "middle": float(average[index]),
                "state": 0,
            }
            ranges.append(current_range)
            top_value = float(candidate_top)
            bottom_value = float(candidate_bottom)
            state = 0
            detection[index] = True
    elif np.isfinite(count) and count == 0.0 and current_range is not None:
        current_range["end"] = index

    if current_range is not None:
        if close[index] > current_range["top"]:
            state = 1
            current_range["state"] = 1
        elif close[index] < current_range["bottom"]:
            state = -1
            current_range["state"] = -1

    if np.isfinite(top_value) and np.isfinite(bottom_value):
        active_top[index] = top_value
        active_bottom[index] = bottom_value
        active_state[index] = state

up_color = "#089981"
down_color = "#F23645"
range_color = "#2157F3"


def state_color(value):
    if value > 0:
        return up_color
    if value < 0:
        return down_color
    return range_color


top_plot = []
bottom_plot = []
for index in range(n):
    changed = (
        index == 0
        or not np.isfinite(active_top[index])
        or not np.isfinite(active_bottom[index])
        or not np.isfinite(active_top[index - 1])
        or not np.isfinite(active_bottom[index - 1])
        or active_top[index] != active_top[index - 1]
        or active_bottom[index] != active_bottom[index - 1]
    )
    if changed:
        top_plot.append(None)
        bottom_plot.append(None)
    else:
        color = state_color(active_state[index])
        top_plot.append({"value": float(active_top[index]), "color": color})
        bottom_plot.append({"value": float(active_bottom[index]), "color": color})

zone_data = [None for _ in range(n)]
middle_data = [None for _ in range(n)]
for item in ranges:
    color = state_color(item["state"])
    start = max(0, int(item["start"]))
    end = min(n - 1, int(item["end"]))
    for index in range(start, end + 1):
        zone_data[index] = {
            "upper": float(item["top"]),
            "lower": float(item["bottom"]),
            "color": color,
        }
        middle_data[index] = {"value": float(item["middle"]), "color": color}

detection_data = []
for index in range(n):
    if detection[index] and np.isfinite(active_top[index]) and np.isfinite(active_bottom[index]):
        detection_data.append(
            {
                "upper": float(active_top[index]),
                "lower": float(active_bottom[index]),
                "color": "#9CA3AF",
            }
        )
    else:
        detection_data.append(None)

detection_basis = [float(value) if np.isfinite(value) else None for value in average]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Range Zone", "title": False, "data": zone_data, "color": range_color, "type": "cloud", "opacity": 0.20, "overlay": True},
        {"name": "Range Midpoint", "data": middle_data, "color": range_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Range Top", "data": top_plot, "color": range_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Range Bottom", "data": bottom_plot, "color": range_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "New Range", "title": False, "data": detection_data, "color": "#9CA3AF", "type": "cloud", "opacity": 0.20, "overlay": True},
        {"name": "Detection Basis", "title": False, "data": detection_basis, "color": "rgba(148, 163, 184, 0.18)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [],
    "layers": [],
}
