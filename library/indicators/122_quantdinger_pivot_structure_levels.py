# ============================================================
# QuantDinger Pivot Structure Levels
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Pivot Structure Levels"
my_indicator_description = (
    "Confirms swing highs and lows, classifies them as HH, LH, HL, or LL, "
    "extends the selected pivot levels, and marks price breaks."
)

# @param high_source str high Pivot high source values=high,close,open
# @param high_left int 4 Pivot high left bars range=0:30:1
# @param high_right int 2 Pivot high right bars range=0:30:1
# @param low_source str low Pivot low source values=low,close,open
# @param low_left int 4 Pivot low left bars range=0:30:1
# @param low_right int 2 Pivot low right bars range=0:30:1
# @param show_hh bool true Show higher highs
# @param show_lh bool true Show lower highs
# @param show_hl bool true Show higher lows
# @param show_ll bool true Show lower lows
# @param show_markers bool true Show structure markers
# @param show_prices bool false Include pivot prices in marker text
# @param max_bars_back int 0 Maximum displayed bars, zero means all range=0:5000:50
# @param max_level_length int 0 Maximum level extension bars, zero means unlimited range=0:1000:10
# @param show_channel bool false Show levels as a fractal chaos channel
# @param show_average bool false Show the average of confirmed pivot levels
# @param show_breaks bool true Show pivot level break markers

high_source_name = str(params.get("high_source", "high"))
high_left = min(30, max(0, int(params.get("high_left", 4))))
high_right = min(30, max(0, int(params.get("high_right", 2))))
low_source_name = str(params.get("low_source", "low"))
low_left = min(30, max(0, int(params.get("low_left", 4))))
low_right = min(30, max(0, int(params.get("low_right", 2))))
show_hh = bool(params.get("show_hh", True))
show_lh = bool(params.get("show_lh", True))
show_hl = bool(params.get("show_hl", True))
show_ll = bool(params.get("show_ll", True))
show_markers = bool(params.get("show_markers", True))
show_prices = bool(params.get("show_prices", False))
max_bars_back = min(5000, max(0, int(params.get("max_bars_back", 0))))
max_level_length = min(1000, max(0, int(params.get("max_level_length", 0))))
show_channel = bool(params.get("show_channel", False))
show_average = bool(params.get("show_average", False))
show_breaks = bool(params.get("show_breaks", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

source_map = {
    "open": open_price,
    "high": high,
    "low": low,
    "close": close,
}
high_source = source_map.get(high_source_name, high)
low_source = source_map.get(low_source_name, low)

high_pivots = []
low_pivots = []
high_confirmation = [None for _ in range(n)]
low_confirmation = [None for _ in range(n)]

for confirmation in range(n):
    high_center = confirmation - high_right
    if high_center >= high_left and high_center >= 0:
        window_end = high_center + high_right
        window_start = high_center - high_left
        if window_end < n:
            center_value = float(high_source[high_center])
            window = high_source[window_start:window_end + 1]
            if center_value >= float(np.max(window)):
                prior_value = high_pivots[-1][1] if high_pivots else None
                kind = "HH" if prior_value is not None and center_value > prior_value else "LH"
                high_pivots.append((high_center, center_value, kind, confirmation))
                high_confirmation[confirmation] = center_value

    low_center = confirmation - low_right
    if low_center >= low_left and low_center >= 0:
        window_end = low_center + low_right
        window_start = low_center - low_left
        if window_end < n:
            center_value = float(low_source[low_center])
            window = low_source[window_start:window_end + 1]
            if center_value <= float(np.min(window)):
                prior_value = low_pivots[-1][1] if low_pivots else None
                kind = "HL" if prior_value is not None and center_value > prior_value else "LL"
                low_pivots.append((low_center, center_value, kind, confirmation))
                low_confirmation[confirmation] = center_value

display_start = max(0, n - max_bars_back) if max_bars_back > 0 else 0

def selected_pivots(items, first_enabled, second_enabled, first_kind, second_kind):
    selected = []
    for item in items:
        if (item[2] == first_kind and first_enabled) or (item[2] == second_kind and second_enabled):
            selected.append(item)
    return selected

selected_highs = selected_pivots(high_pivots, show_hh, show_lh, "HH", "LH")
selected_lows = selected_pivots(low_pivots, show_hl, show_ll, "HL", "LL")

def extended_levels(items):
    values = [None for _ in range(n)]
    for item_index, (pivot_index, pivot_value, _, _) in enumerate(items):
        next_index = items[item_index + 1][0] if item_index + 1 < len(items) else n
        end_index = next_index
        if max_level_length > 0:
            end_index = min(end_index, pivot_index + max_level_length)
        segment_start = pivot_index + (1 if item_index > 0 else 0)
        for index in range(max(display_start, segment_start), min(n, end_index)):
            values[index] = float(pivot_value)
    return values

high_levels = extended_levels(selected_highs)
low_levels = extended_levels(selected_lows)

confirmed_high = [None for _ in range(n)]
confirmed_low = [None for _ in range(n)]
current_high = None
current_low = None
for index in range(n):
    if high_confirmation[index] is not None:
        current_high = float(high_confirmation[index])
    if low_confirmation[index] is not None:
        current_low = float(low_confirmation[index])
    confirmed_high[index] = current_high
    confirmed_low[index] = current_low

average_level = [None for _ in range(n)]
average_bull = [None for _ in range(n)]
average_bear = [None for _ in range(n)]
for index in range(display_start, n):
    if confirmed_high[index] is not None and confirmed_low[index] is not None:
        average = (confirmed_high[index] + confirmed_low[index]) / 2.0
        average_level[index] = float(average)
        if close[index] > average:
            average_bull[index] = float(average)
        else:
            average_bear[index] = float(average)

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

if show_hh or show_lh:
    plots.append({
        "name": "High Chaos Channel" if show_channel else "Pivot High Levels",
        "data": high_levels,
        "color": "rgba(38, 166, 154, 0.78)",
        "type": "line",
        "lineWidth": 2 if show_channel else 1,
        "overlay": True,
    })

if show_hl or show_ll:
    plots.append({
        "name": "Low Chaos Channel" if show_channel else "Pivot Low Levels",
        "data": low_levels,
        "color": "rgba(239, 83, 80, 0.78)",
        "type": "line",
        "lineWidth": 2 if show_channel else 1,
        "overlay": True,
    })

if show_average:
    plots.extend([
        {
            "name": "Average Above",
            "data": average_bull,
            "color": "#26A69A",
            "type": "line",
            "lineWidth": 1,
            "overlay": True,
        },
        {
            "name": "Average Below",
            "data": average_bear,
            "color": "#EF5350",
            "type": "line",
            "lineWidth": 1,
            "overlay": True,
        },
    ])

signals = []

def append_structure_signal(kind, items, enabled):
    if not show_markers or not enabled:
        return
    marker = [None for _ in range(n)]
    for pivot_index, pivot_value, pivot_kind, _ in items:
        if pivot_kind == kind and pivot_index >= display_start:
            marker[pivot_index] = {
                "value": float(pivot_value),
                "text": f"{kind} {pivot_value:.2f}" if show_prices else kind,
                "color": "#26A69A" if kind in ("HH", "LH") else "#EF5350",
            }
    is_high = kind in ("HH", "LH")
    label = kind + (" Price" if show_prices else "")
    signals.append({
        "type": "sell" if is_high else "buy",
        "text": kind,
        "name": label,
        "data": marker,
        "color": "#26A69A" if is_high else "#EF5350",
        "renderMode": "events",
    })

append_structure_signal("HH", high_pivots, show_hh)
append_structure_signal("LH", high_pivots, show_lh)
append_structure_signal("HL", low_pivots, show_hl)
append_structure_signal("LL", low_pivots, show_ll)

if show_breaks:
    breakout = [None for _ in range(n)]
    breakdown = [None for _ in range(n)]
    for index in range(max(1, display_start), n):
        reference_high = confirmed_high[index]
        reference_low = confirmed_low[index]
        if reference_high is not None and close[index] > reference_high and open_price[index] <= reference_high:
            breakout[index] = float(low[index])
        if reference_low is not None and close[index] < reference_low and open_price[index] >= reference_low:
            breakdown[index] = float(high[index])
    signals.extend([
        {
            "type": "buy",
            "text": "Break+",
            "name": "Pivot Breakout",
            "data": breakout,
            "color": "#26A69A",
            "renderMode": "events",
        },
        {
            "type": "sell",
            "text": "Break-",
            "name": "Pivot Breakdown",
            "data": breakdown,
            "color": "#EF5350",
            "renderMode": "events",
        },
    ])

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": []}
