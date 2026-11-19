# ============================================================
# QuantDinger Trend Direction Helper
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; alerts do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Trend Direction Helper"
my_indicator_description = (
    "Combines fast reversal prompts, recent pivot support and resistance, optional "
    "HH/HL/LH/LL markers, and a percent-reversal ZigZag on the price chart."
)

# @param risk int 14 Reversal sensitivity range=1:50:1
# @param label_size str Normal Signal label size values=Tiny,Normal,Large,Huge
# @param buy_text str B Buy label text
# @param sell_text str S Sell label text
# @param show_support_resistance bool true Show support and resistance
# @param left_pivot int 50 Left pivot bars range=2:200:1
# @param right_pivot int 25 Right pivot bars range=1:100:1
# @param quick_right_pivot int 5 Quick right pivot bars range=1:50:1
# @param show_structure_labels bool false Show HH HL LH LL labels
# @param show_zigzag bool true Show percent-reversal ZigZag
# @param reversal_percent float 1.75 ZigZag reversal percent range=0.1:20.0:0.25

risk = min(50, max(1, int(params.get("risk", 14))))
label_size = str(params.get("label_size", "Normal"))
buy_text = str(params.get("buy_text", "B"))
sell_text = str(params.get("sell_text", "S"))
show_support_resistance = bool(params.get("show_support_resistance", True))
left_pivot = min(200, max(2, int(params.get("left_pivot", 50))))
right_pivot = min(100, max(1, int(params.get("right_pivot", 25))))
quick_right_pivot = min(50, max(1, int(params.get("quick_right_pivot", 5))))
show_structure_labels = bool(params.get("show_structure_labels", False))
show_zigzag = bool(params.get("show_zigzag", True))
reversal_percent = min(20.0, max(0.1, float(params.get("reversal_percent", 1.75)))) / 100.0

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

upper_trigger = 60.0 + float(risk)
lower_trigger = 20.0 - float(risk)
location_value = np.full(n, np.nan, dtype=float)
for index in range(n):
    candle_range = high[index] - low[index]
    if candle_range > 0.0:
        location_value[index] = 100.0 - abs(100.0 * (close[index] - high[index]) / candle_range)

buy_events = [None for _ in range(n)]
sell_events = [None for _ in range(n)]
for index in range(n):
    if not np.isfinite(location_value[index]):
        continue
    prior_extreme = None
    for offset in range(1, 51):
        prior_index = index - offset
        if prior_index < 0:
            break
        value = location_value[prior_index]
        if np.isfinite(value) and (value < lower_trigger or value > upper_trigger):
            prior_extreme = float(value)
            break
    if prior_extreme is None:
        continue
    if location_value[index] > upper_trigger and prior_extreme < lower_trigger:
        buy_events[index] = float(low[index])
    elif location_value[index] < lower_trigger and prior_extreme > upper_trigger:
        sell_events[index] = float(high[index])

def confirmed_pivot(values, confirmation_index, left_count, right_count, high_pivot):
    center = confirmation_index - right_count
    if center < left_count or center < 0:
        return None
    start = center - left_count
    end = center + right_count
    if end >= n:
        return None
    window = values[start:end + 1]
    candidate = values[center]
    others = np.concatenate((window[:left_count], window[left_count + 1:]))
    if high_pivot:
        valid = candidate >= np.max(others) and np.any(candidate > others)
    else:
        valid = candidate <= np.min(others) and np.any(candidate < others)
    return center if valid else None

quick_highs = []
quick_lows = []
full_highs = []
full_lows = []
for index in range(n):
    qh = confirmed_pivot(high, index, left_pivot, quick_right_pivot, True)
    ql = confirmed_pivot(low, index, left_pivot, quick_right_pivot, False)
    fh = confirmed_pivot(high, index, left_pivot, right_pivot, True)
    fl = confirmed_pivot(low, index, left_pivot, right_pivot, False)
    if qh is not None:
        quick_highs.append((qh, float(high[qh])))
    if ql is not None:
        quick_lows.append((ql, float(low[ql])))
    if fh is not None:
        full_highs.append((fh, float(high[fh])))
    if fl is not None:
        full_lows.append((fl, float(low[fl])))

structure_high = [None for _ in range(n)]
structure_low = [None for _ in range(n)]
top_values = []
bottom_values = []
for index in range(4, n):
    top = high[index - 2]
    bottom = low[index - 2]
    is_top = high[index - 4] < top and high[index - 3] <= top and high[index - 1] <= top and high[index] < top
    is_bottom = low[index - 4] > bottom and low[index - 3] >= bottom and low[index - 1] >= bottom and low[index] > bottom
    if is_top:
        label = None
        if top_values:
            if top_values[-1] < top:
                label = "HH"
            elif top_values[-1] > top:
                label = "LH"
        top_values.append(float(top))
        if label is not None:
            structure_high[index - 2] = {"value": float(top), "text": label}
    if is_bottom:
        label = None
        if bottom_values:
            if bottom_values[-1] < bottom:
                label = "HL"
            elif bottom_values[-1] > bottom:
                label = "LL"
        bottom_values.append(float(bottom))
        if label is not None:
            structure_low[index - 2] = {"value": float(bottom), "text": label}

zigzag_events = []
if n > 1:
    direction_up = True
    tracked_high = float(max(high[0], high[1]))
    tracked_low = float(min(low[0], low[1]))
    tracked_high_index = 0 if high[0] >= high[1] else 1
    tracked_low_index = 0 if low[0] <= low[1] else 1
    pivot_value = float(close[0])
    for index in range(1, n):
        reverse_range = abs(pivot_value) * reversal_percent
        if direction_up:
            if high[index] >= tracked_high:
                tracked_high = float(high[index])
                tracked_high_index = index
            reverse_line = tracked_high - reverse_range
            if close[index] < reverse_line:
                pivot_value = tracked_high
                zigzag_events.append((tracked_high_index, float(pivot_value)))
                direction_up = False
                if low[index] <= low[index - 1]:
                    tracked_low = float(low[index])
                    tracked_low_index = index
                else:
                    tracked_low = float(low[index - 1])
                    tracked_low_index = index - 1
        else:
            if low[index] <= tracked_low:
                tracked_low = float(low[index])
                tracked_low_index = index
            reverse_line = tracked_low + reverse_range
            if close[index] > reverse_line:
                pivot_value = tracked_low
                zigzag_events.append((tracked_low_index, float(pivot_value)))
                direction_up = True
                if high[index] >= high[index - 1]:
                    tracked_high = float(high[index])
                    tracked_high_index = index
                else:
                    tracked_high = float(high[index - 1])
                    tracked_high_index = index - 1

layers = []
if show_support_resistance and n > 0:
    levels = []
    if quick_highs:
        levels.append(quick_highs[-1])
    if quick_lows:
        levels.append(quick_lows[-1])
    for offset in range(3):
        if len(full_highs) > offset:
            levels.append(full_highs[-1 - offset])
        if len(full_lows) > offset:
            levels.append(full_lows[-1 - offset])
    unique_levels = []
    seen_levels = set()
    for pivot_index, level in levels:
        level_key = round(float(level), 10)
        if level_key not in seen_levels:
            unique_levels.append((pivot_index, level))
            seen_levels.add(level_key)
    for pivot_index, level in unique_levels[:8]:
        layers.append(
            {
                "type": "line",
                "startIndex": int(pivot_index),
                "endIndex": max(0, n - 1),
                "y1": float(level),
                "y2": float(level),
                "color": "#16A34A" if close[-1] >= level else "#DC2626",
                "lineWidth": 2,
                "dashed": True,
                "extendRight": False,
            }
        )

if show_zigzag:
    recent = zigzag_events[-9:]
    for point_index in range(1, len(recent)):
        first = recent[point_index - 1]
        second = recent[point_index]
        layers.append(
            {
                "type": "line",
                "startIndex": int(first[0]),
                "endIndex": int(second[0]),
                "y1": float(first[1]),
                "y2": float(second[1]),
                "color": "rgba(109,139,13,0.56)" if second[1] > first[1] else "rgba(187,112,69,0.56)",
                "lineWidth": 5,
                "dashed": False,
                "extendRight": False,
            }
        )

size_map = {"Tiny": 8, "Normal": 10, "Large": 12, "Huge": 14}
marker_size = size_map.get(label_size, 10)
signals = [
    {
        "type": "buy",
        "text": buy_text,
        "name": "Bullish Reversal",
        "data": buy_events,
        "color": "rgba(0,255,255,0.80)",
        "size": marker_size,
        "renderMode": "events",
    },
    {
        "type": "sell",
        "text": sell_text,
        "name": "Bearish Reversal",
        "data": sell_events,
        "color": "rgba(255,0,0,0.80)",
        "size": marker_size,
        "renderMode": "events",
    },
]
if show_structure_labels:
    signals.extend(
        [
            {
                "type": "sell",
                "text": "Structure High",
                "name": "HH/LH",
                "data": structure_high,
                "color": "#800000",
                "renderMode": "events",
            },
            {
                "type": "buy",
                "text": "Structure Low",
                "name": "HL/LL",
                "data": structure_low,
                "color": "#16A34A",
                "renderMode": "events",
            },
        ]
    )

plots = [
    {
        "name": "Price Guide",
        "title": False,
        "data": [float(value) if np.isfinite(value) else None for value in close],
        "color": "rgba(0,0,0,0)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    }
]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers[-100:]}
