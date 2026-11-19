# ============================================================
# QuantDinger Pivot Structure Levels
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Pivot Structure Levels"
my_indicator_description = "Classifies independently configured high/low pivots as HH, HL, LH, or LL and selectively projects their levels, prices, channels, and breaks."

# @param high_source str high High-pivot source values=open,high,low,close,hl2,hlc3,ohlc4
# @param high_left int 4 High-pivot left length range=0:250:1
# @param high_right int 2 High-pivot right length range=0:250:1
# @param low_source str low Low-pivot source values=open,high,low,close,hl2,hlc3,ohlc4
# @param low_left int 4 Low-pivot left length range=0:250:1
# @param low_right int 2 Low-pivot right length range=0:250:1
# @param level_hh bool true Project higher highs
# @param level_lh bool true Project lower highs
# @param level_hl bool true Project higher lows
# @param level_ll bool true Project lower lows
# @param marker_hh bool true Mark higher highs
# @param marker_lh bool true Mark lower highs
# @param marker_hl bool true Mark higher lows
# @param marker_ll bool true Mark lower lows
# @param price_hh bool true Label higher-high prices
# @param price_lh bool true Label lower-high prices
# @param price_hl bool true Label higher-low prices
# @param price_ll bool true Label lower-low prices
# @param maximum_bars int 0 Maximum recent bars; zero shows all range=0:10000:1
# @param maximum_level_length int 0 Maximum level extension; zero is unlimited range=0:10000:1
# @param show_channel bool false Show fractal chaos channel
# @param show_center bool false Show average pivot center
# @param show_breaks bool true Show fractal break symbols

high_source_name = str(params.get("high_source", "high")).lower()
high_left = min(250, max(0, int(params.get("high_left", 4))))
high_right = min(250, max(0, int(params.get("high_right", 2))))
low_source_name = str(params.get("low_source", "low")).lower()
low_left = min(250, max(0, int(params.get("low_left", 4))))
low_right = min(250, max(0, int(params.get("low_right", 2))))
level_hh = bool(params.get("level_hh", True))
level_lh = bool(params.get("level_lh", True))
level_hl = bool(params.get("level_hl", True))
level_ll = bool(params.get("level_ll", True))
marker_hh = bool(params.get("marker_hh", True))
marker_lh = bool(params.get("marker_lh", True))
marker_hl = bool(params.get("marker_hl", True))
marker_ll = bool(params.get("marker_ll", True))
price_hh = bool(params.get("price_hh", True))
price_lh = bool(params.get("price_lh", True))
price_hl = bool(params.get("price_hl", True))
price_ll = bool(params.get("price_ll", True))
maximum_bars = max(0, int(params.get("maximum_bars", 0)))
maximum_level_length = max(0, int(params.get("maximum_level_length", 0)))
show_channel = bool(params.get("show_channel", False))
show_center = bool(params.get("show_center", False))
show_breaks = bool(params.get("show_breaks", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)
sources = {
    "open": opened, "high": high, "low": low, "close": close,
    "hl2": (high + low) / 2.0, "hlc3": (high + low + close) / 3.0,
    "ohlc4": (opened + high + low + close) / 4.0,
}
high_source = sources.get(high_source_name, high)
low_source = sources.get(low_source_name, low)

pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
higher_high = np.zeros(n, dtype=bool)
lower_high = np.zeros(n, dtype=bool)
higher_low = np.zeros(n, dtype=bool)
lower_low = np.zeros(n, dtype=bool)
high_history = []
low_history = []

for index in range(n):
    high_center = index - high_right
    if high_center >= high_left and index >= high_right:
        high_match = all(high_source[high_center] >= high_source[position] for position in range(high_center - high_left, high_center))
        high_match = high_match and all(high_source[high_center] > high_source[position] for position in range(high_center + 1, index + 1))
        if high_match:
            pivot_high[index] = high_source[high_center]
            if high_history:
                higher_high[index] = high_history[-1] < high_source[high_center]
                lower_high[index] = high_history[-1] > high_source[high_center]
            high_history.append(float(high_source[high_center]))
    low_center = index - low_right
    if low_center >= low_left and index >= low_right:
        low_match = all(low_source[low_center] <= low_source[position] for position in range(low_center - low_left, low_center))
        low_match = low_match and all(low_source[low_center] < low_source[position] for position in range(low_center + 1, index + 1))
        if low_match:
            pivot_low[index] = low_source[low_center]
            if low_history:
                higher_low[index] = low_history[-1] < low_source[low_center]
                lower_low[index] = low_history[-1] > low_source[low_center]
            low_history.append(float(low_source[low_center]))

count_high = 0
count_low = 0
current_high = np.nan
current_low = np.nan
selected_high = np.nan
selected_low = np.nan
fixed_high = np.full(n, np.nan, dtype=float)
fixed_low = np.full(n, np.nan, dtype=float)
pivot_average = np.full(n, np.nan, dtype=float)
high_levels = np.full(n, np.nan, dtype=float)
low_levels = np.full(n, np.nan, dtype=float)
buy_break = np.zeros(n, dtype=bool)
sell_break = np.zeros(n, dtype=bool)
label_layers = []

for index in range(n):
    in_display_range = maximum_bars == 0 or index > (n - 1 - maximum_bars)
    high_event = np.isfinite(pivot_high[index])
    low_event = np.isfinite(pivot_low[index])
    previous_selected_high = selected_high
    previous_selected_low = selected_low
    if high_event:
        count_high = 0
        current_high = float(high_source[index - high_right])
    else:
        count_high += 1
    if low_event:
        count_low = 0
        current_low = float(low_source[index - low_right])
    else:
        count_low += 1
    fixed_high[index] = current_high
    fixed_low[index] = current_low
    pivot_average[index] = (current_high + current_low) / 2.0 if np.all(np.isfinite([current_high, current_low])) else np.nan

    if level_hh and level_lh:
        selected_high = current_high
    elif level_hh:
        if higher_high[index]:
            selected_high = current_high
    elif level_lh:
        if lower_high[index]:
            selected_high = current_high
    else:
        selected_high = np.nan
    if level_hl and level_ll:
        selected_low = current_low
    elif level_hl:
        if higher_low[index]:
            selected_low = current_low
    elif level_ll:
        if lower_low[index]:
            selected_low = current_low
    else:
        selected_low = np.nan

    high_changed = np.isfinite(previous_selected_high) and np.isfinite(selected_high) and selected_high != previous_selected_high
    low_changed = np.isfinite(previous_selected_low) and np.isfinite(selected_low) and selected_low != previous_selected_low
    high_target = index - high_right
    low_target = index - low_right
    if in_display_range and (level_hh or level_lh) and 0 <= high_target < n:
        if show_channel:
            high_levels[high_target] = selected_high
        elif (maximum_level_length == 0 or count_high < maximum_level_length) and not high_changed:
            high_levels[high_target] = selected_high
    if in_display_range and (level_hl or level_ll) and 0 <= low_target < n:
        if show_channel:
            low_levels[low_target] = selected_low
        elif (maximum_level_length == 0 or count_low < maximum_level_length) and not low_changed:
            low_levels[low_target] = selected_low

    buy_break[index] = np.isfinite(current_high) and close[index] > current_high and opened[index] <= current_high
    sell_break[index] = np.isfinite(current_low) and close[index] < current_low and opened[index] >= current_low

    if in_display_range:
        label_specs = (
            (higher_high[index] and price_hh, "HH", pivot_high[index], True),
            (higher_low[index] and price_hl, "HL", pivot_low[index], False),
            (lower_high[index] and price_lh, "LH", pivot_high[index], True),
            (lower_low[index] and price_ll, "LL", pivot_low[index], False),
        )
        for enabled, role, price, high_side in label_specs:
            if enabled and np.isfinite(price):
                # All four source price labels use the high-pivot right offset.
                label_index = index - high_right
                if 0 <= label_index < n:
                    label_layers.append({
                        "type": "label", "role": f"{role} Price", "index": int(label_index), "price": float(price),
                        "text": f"[{price:.6g}]", "color": "rgba(0,0,0,0)",
                        "textColor": "rgba(0,128,128,0.50)" if high_side else "rgba(255,0,0,0.50)",
                        "direction": "down" if high_side else "up", "size": "small",
                    })

center_display = np.full(n, np.nan, dtype=float)
if show_center:
    for index in range(n):
        if maximum_bars == 0 or index > (n - 1 - maximum_bars):
            center_display[index] = pivot_average[index]
center_colors = ["rgba(0,128,128,0.50)" if np.isfinite(pivot_average[index]) and close[index] > pivot_average[index] else "rgba(255,0,0,0.50)" for index in range(n)]


def _structure_points(events, role, enabled, offset, high_side):
    result = [None] * n
    if not enabled:
        return result
    for confirmation in np.flatnonzero(events):
        if maximum_bars != 0 and confirmation <= n - 1 - maximum_bars:
            continue
        target = confirmation - offset
        if 0 <= target < n:
            result[target] = {
                "active": True, "price": float(pivot_high[confirmation] if high_side else pivot_low[confirmation]),
                "text": role, "color": "rgba(0,128,128,0.50)" if high_side else "rgba(255,0,0,0.50)",
            }
    return result


signals = [
    {"type": "sell", "name": "Higher High", "data": _structure_points(higher_high, "HH", marker_hh, high_right, True), "color": "rgba(0,128,128,0.50)", "renderMode": "events"},
    # The source places HL with the high offset and LH with the low offset.
    {"type": "buy", "name": "Higher Low", "data": _structure_points(higher_low, "HL", marker_hl, high_right, False), "color": "rgba(255,0,0,0.50)", "renderMode": "events"},
    {"type": "sell", "name": "Lower High", "data": _structure_points(lower_high, "LH", marker_lh, low_right, True), "color": "rgba(0,128,128,0.50)", "renderMode": "events"},
    {"type": "buy", "name": "Lower Low", "data": _structure_points(lower_low, "LL", marker_ll, low_right, False), "color": "rgba(255,0,0,0.50)", "renderMode": "events"},
    {"type": "buy", "name": "Breakout", "data": [{"active": True, "price": float(low[index]), "text": "↑↑", "color": "rgba(0,128,128,0.50)"} if show_breaks and buy_break[index] and (maximum_bars == 0 or index > n - 1 - maximum_bars) else None for index in range(n)], "color": "rgba(0,128,128,0.50)", "renderMode": "events"},
    {"type": "sell", "name": "Breakdown", "data": [{"active": True, "price": float(high[index]), "text": "↓↓", "color": "rgba(255,0,0,0.50)"} if show_breaks and sell_break[index] and (maximum_bars == 0 or index > n - 1 - maximum_bars) else None for index in range(n)], "color": "rgba(255,0,0,0.50)", "renderMode": "events"},
]

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Selected High Levels", "data": _finite(high_levels), "color": "rgba(0,128,128,0.50)", "type": "step" if show_channel else "scatter", "size": 2, "lineWidth": 1, "overlay": True},
        {"name": "Selected Low Levels", "data": _finite(low_levels), "color": "rgba(255,0,0,0.50)", "type": "step" if show_channel else "scatter", "size": 2, "lineWidth": 1, "overlay": True},
        {"name": "Pivot Center", "data": _finite(center_display), "color": center_colors, "type": "step", "lineWidth": 1, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": signals, "layers": label_layers, "tables": [],
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "pivotHigh": _finite(pivot_high), "pivotLow": _finite(pivot_low),
        "higherHigh": higher_high.tolist(), "lowerHigh": lower_high.tolist(),
        "higherLow": higher_low.tolist(), "lowerLow": lower_low.tolist(),
        "fixedHigh": _finite(fixed_high), "fixedLow": _finite(fixed_low), "pivotAverage": _finite(pivot_average),
        "selectedHighLevels": _finite(high_levels), "selectedLowLevels": _finite(low_levels),
        "buyBreak": buy_break.tolist(), "sellBreak": sell_break.tolist(),
        "highHistory": high_history, "lowHistory": low_history,
    },
}
