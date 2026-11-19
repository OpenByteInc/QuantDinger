# ============================================================
# QuantDinger Pivot Trailing Extremes
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Pivot Trailing Extremes"
my_indicator_description = (
    "Resets trailing high and low rails at confirmed symmetric pivots, expands "
    "the rails with new extremes, and optionally backpaints confirmed history."
)

# @param pivot_length int 24 Pivot length range=2:500:1
# @param backpaint bool false Backpaint confirmed pivot information

pivot_length = min(500, max(2, int(params.get("pivot_length", 24))))
backpaint = bool(params.get("backpaint", False))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
n = len(df)

pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
for confirmation in range(pivot_length * 2, n):
    center = confirmation - pivot_length
    start = confirmation - pivot_length * 2
    high_window = high[start : confirmation + 1]
    low_window = low[start : confirmation + 1]
    if high[center] == np.max(high_window):
        pivot_high[confirmation] = float(high[center])
    if low[center] == np.min(low_window):
        pivot_low[confirmation] = float(low[center])

trail_high = np.full(n, np.nan, dtype=float)
trail_low = np.full(n, np.nan, dtype=float)
trail_average = np.full(n, np.nan, dtype=float)
fill_state = np.zeros(n, dtype=int)

current_high = np.nan
current_low = np.nan
current_fill = 0
offset = pivot_length if backpaint else 0

for index in range(n):
    high_event = np.isfinite(pivot_high[index])
    low_event = np.isfinite(pivot_low[index])
    if high_event or low_event:
        reset_index = index - pivot_length
        if reset_index >= 0:
            current_high = float(high[reset_index])
            current_low = float(low[reset_index])
        current_fill = -1 if high_event else 1

    sample_index = index - offset
    if sample_index >= 0 and np.isfinite(current_high) and np.isfinite(current_low):
        current_high = max(float(high[sample_index]), current_high)
        current_low = min(float(low[sample_index]), current_low)
        trail_high[index] = current_high
        trail_low[index] = current_low
        trail_average[index] = (current_high + current_low) / 2.0
    fill_state[index] = current_fill

bull_color = "#00897B"
bear_color = "#F23645"
average_color = "#FF5D00"

display_high = [None for _ in range(n)]
display_low = [None for _ in range(n)]
display_average = [None for _ in range(n)]
display_cloud = [None for _ in range(n)]

for index in range(n):
    target = index - offset
    pivot_event = np.isfinite(pivot_high[index]) or np.isfinite(pivot_low[index])
    if target < 0 or target >= n or pivot_event or not np.isfinite(trail_high[index]):
        continue
    display_high[target] = float(trail_high[index])
    display_low[target] = float(trail_low[index])
    display_average[target] = float(trail_average[index])
    if fill_state[index] != 0:
        color = "rgba(0, 137, 123, 0.20)" if fill_state[index] == 1 else "rgba(242, 54, 69, 0.20)"
        display_cloud[target] = {
            "upper": float(trail_high[index]),
            "lower": float(trail_low[index]),
            "color": color,
        }

if backpaint and n > pivot_length and np.isfinite(trail_high[-1]):
    rebuilt_high = float(trail_high[-1])
    rebuilt_low = float(trail_low[-1])
    final_fill = int(fill_state[-1])
    for step in range(pivot_length):
        source_index = n - pivot_length + step
        if source_index < 0 or source_index >= n:
            continue
        rebuilt_high = max(float(high[source_index]), rebuilt_high)
        rebuilt_low = min(float(low[source_index]), rebuilt_low)
        rebuilt_average = (rebuilt_high + rebuilt_low) / 2.0
        display_high[source_index] = rebuilt_high
        display_low[source_index] = rebuilt_low
        display_average[source_index] = rebuilt_average
        if final_fill != 0:
            color = "rgba(0, 137, 123, 0.20)" if final_fill == 1 else "rgba(242, 54, 69, 0.20)"
            display_cloud[source_index] = {
                "upper": rebuilt_high,
                "lower": rebuilt_low,
                "color": color,
            }

pivot_high_events = [None for _ in range(n)]
pivot_low_events = [None for _ in range(n)]
for confirmation in range(n):
    target = confirmation - offset
    if target < 0 or target >= n:
        continue
    if np.isfinite(pivot_high[confirmation]):
        pivot_high_events[target] = float(pivot_high[confirmation])
    if np.isfinite(pivot_low[confirmation]):
        pivot_low_events[target] = float(pivot_low[confirmation])

pivot_search_basis = [
    {"value": float((high[index] + low[index]) / 2.0), "color": "rgba(120, 123, 134, 0.12)"}
    if display_high[index] is None
    else None
    for index in range(n)
]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Trailing Upper Rail", "data": display_high, "color": bull_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Trailing Lower Rail", "data": display_low, "color": bear_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Rail Midpoint", "data": display_average, "color": average_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Pivot Regime Area", "title": False, "data": display_cloud, "color": bull_color, "type": "cloud", "opacity": 1.0, "overlay": True},
        {"name": "Pivot Search Basis", "title": False, "data": pivot_search_basis, "color": "rgba(120, 123, 134, 0.12)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "sell", "text": "▼", "name": "Pivot High", "data": pivot_high_events, "color": bear_color, "renderMode": "events"},
        {"type": "buy", "text": "▲", "name": "Pivot Low", "data": pivot_low_events, "color": bull_color, "renderMode": "events"},
    ],
    "layers": [],
}
