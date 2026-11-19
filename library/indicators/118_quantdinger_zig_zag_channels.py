# ============================================================
# QuantDinger Zig Zag Channels
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Zig Zag Channels"
my_indicator_description = (
    "Builds confirmed alternating swing segments from delayed range breaks, "
    "then fits body-based upper and lower extremity channels to each leg."
)

# @param length int 20 Swing confirmation length range=5:250:1
# @param extend_last bool true Extend the active leg to the latest candle
# @param show_extremities bool true Show body-based channel extremities
# @param show_pivots bool true Mark confirmed swing highs and lows

length = min(250, max(5, int(params.get("length", 20))))
extend_last = bool(params.get("extend_last", True))
show_extremities = bool(params.get("show_extremities", True))
show_pivots = bool(params.get("show_pivots", True))

df = df.copy()
open_values = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

state = 0
pivots = []

for index in range(length, n):
    delayed_index = index - length
    recent_start = delayed_index + 1
    recent_high = float(np.max(close[recent_start:index + 1]))
    recent_low = float(np.min(close[recent_start:index + 1]))
    delayed_close = float(close[delayed_index])

    previous_state = state
    if delayed_close > recent_high:
        state = 0
    elif delayed_close < recent_low:
        state = 1

    if state == 1 and previous_state != 1:
        pivots.append((delayed_index, float(low[delayed_index]), False))
    elif state == 0 and previous_state != 0:
        pivots.append((delayed_index, float(high[delayed_index]), True))

center_line = [None for _ in range(n)]
upper_line = [None for _ in range(n)]
lower_line = [None for _ in range(n)]
top_marks = [None for _ in range(n)]
bottom_marks = [None for _ in range(n)]

def draw_leg(start_index, start_value, end_index, end_value):
    if end_index <= start_index:
        return

    span = end_index - start_index
    baselines = []
    upper_deviation = 0.0
    lower_deviation = 0.0

    for item in range(start_index, end_index + 1):
        progress = (item - start_index) / float(span)
        baseline = start_value + progress * (end_value - start_value)
        baselines.append(baseline)
        upper_deviation = max(
            upper_deviation,
            max(float(open_values[item]), float(close[item])) - baseline,
        )
        lower_deviation = max(
            lower_deviation,
            baseline - min(float(open_values[item]), float(close[item])),
        )

    for offset, item in enumerate(range(start_index, end_index + 1)):
        baseline = baselines[offset]
        center_line[item] = float(baseline)
        upper_line[item] = float(baseline + upper_deviation)
        lower_line[item] = float(baseline - lower_deviation)

for pivot_index, pivot_value, is_top in pivots:
    if is_top:
        top_marks[pivot_index] = pivot_value
    else:
        bottom_marks[pivot_index] = pivot_value

for pivot_number in range(1, len(pivots)):
    start_index, start_value, _ = pivots[pivot_number - 1]
    end_index, end_value, _ = pivots[pivot_number]
    draw_leg(start_index, start_value, end_index, end_value)

if extend_last and pivots and n > 0:
    active_index, active_value, _ = pivots[-1]
    if active_index < n - 1:
        draw_leg(active_index, active_value, n - 1, float(close[-1]))

plots = [
    {
        "name": "Price Reference",
        "title": False,
        "data": [float(value) for value in close],
        "color": "rgba(255, 255, 255, 0.01)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    },
]

if any(value is not None for value in center_line):
    plots.append({
        "name": "Zig Zag",
        "data": center_line,
        "color": "#FF5D00",
        "type": "line",
        "lineWidth": 2,
        "overlay": True,
    })
if show_extremities and any(value is not None for value in upper_line):
    plots.append({
        "name": "Upper Extremity",
        "data": upper_line,
        "color": "rgba(255, 17, 0, 0.78)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    })
    plots.append({
        "name": "Lower Extremity",
        "data": lower_line,
        "color": "rgba(33, 87, 243, 0.78)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    })

signals = []
if show_pivots and any(value is not None for value in top_marks):
    signals.append({
        "type": "sell",
        "text": "H",
        "name": "Confirmed Swing High",
        "data": top_marks,
        "color": "#FF1100",
        "renderMode": "events",
    })
if show_pivots and any(value is not None for value in bottom_marks):
    signals.append({
        "type": "buy",
        "text": "L",
        "name": "Confirmed Swing Low",
        "data": bottom_marks,
        "color": "#2157F3",
        "renderMode": "events",
    })

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": []}
