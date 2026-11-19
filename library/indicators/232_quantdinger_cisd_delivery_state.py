# ============================================================
# QuantDinger CISD Delivery State
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger CISD Delivery State"
my_indicator_description = (
    "Tracks swing-liquidity mitigation and validates bullish or bearish changes "
    "in the state of delivery with origin quality and sweep confirmation."
)

# @param noise_filter float 0.7 Noise filter range=0.0:1.0:0.1
# @param swing_period int 12 Swing period range=1:100:1
# @param expiry_bars int 100 Liquidity level expiry range=5:1000:1
# @param liquidity_lookback int 10 Sweep lookback range=1:100:1
# @param bullish_color str #00FFBB Bullish color
# @param bearish_color str #FF1100 Bearish color
# @param candle_body_transparency int 90 Candle body transparency range=0:100:1
# @param candle_wick_transparency int 40 Candle wick transparency range=0:100:1
# @param hide_expired_levels bool true Hide expired liquidity levels
# @param hide_mitigated_levels bool false Hide mitigated liquidity levels
# @param max_visible_levels int 24 Maximum visible liquidity levels range=1:100:1
# @param max_cisd_lines int 20 Maximum visible CISD lines range=1:100:1

noise_filter = min(1.0, max(0.0, float(params.get("noise_filter", 0.7))))
swing_period = min(100, max(1, int(params.get("swing_period", 12))))
expiry_bars = min(1000, max(5, int(params.get("expiry_bars", 100))))
liquidity_lookback = min(100, max(1, int(params.get("liquidity_lookback", 10))))
bullish_color = str(params.get("bullish_color", "#00FFBB"))
bearish_color = str(params.get("bearish_color", "#FF1100"))
candle_body_transparency = min(100, max(0, int(params.get("candle_body_transparency", 90))))
candle_wick_transparency = min(100, max(0, int(params.get("candle_wick_transparency", 40))))
hide_expired_levels = bool(params.get("hide_expired_levels", True))
hide_mitigated_levels = bool(params.get("hide_mitigated_levels", False))
max_visible_levels = min(100, max(1, int(params.get("max_visible_levels", 24))))
max_cisd_lines = min(100, max(1, int(params.get("max_cisd_lines", 20))))

frame = df.copy().reset_index(drop=True)
n = len(frame)
open_values = pd.to_numeric(frame["open"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
high_values = pd.to_numeric(frame["high"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)


def unique_pivot(values, pivot_index, strength, mode):
    start = pivot_index - strength
    end = pivot_index + strength + 1
    if start < 0 or end > len(values):
        return False
    window = values[start:end]
    candidate = values[pivot_index]
    target = np.max(window) if mode == "high" else np.min(window)
    return candidate == target and np.count_nonzero(window == candidate) == 1


def rgba(hex_color, alpha):
    value = str(hex_color).lstrip("#")
    if len(value) != 6:
        return hex_color
    try:
        red_value = int(value[0:2], 16)
        green_value = int(value[2:4], 16)
        blue_value = int(value[4:6], 16)
    except ValueError:
        return hex_color
    return f"rgba({red_value},{green_value},{blue_value},{min(1.0, max(0.0, alpha)):.3f})"


pivot_high_confirmation = np.zeros(n, dtype=bool)
pivot_low_confirmation = np.zeros(n, dtype=bool)
pivot_high_marker = np.zeros(n, dtype=bool)
pivot_low_marker = np.zeros(n, dtype=bool)
wicked_high = np.zeros(n, dtype=bool)
wicked_low = np.zeros(n, dtype=bool)
wicked_high_level = np.full(n, np.nan, dtype=float)
wicked_low_level = np.full(n, np.nan, dtype=float)
cisd = np.zeros(n, dtype=int)
origin_level = np.full(n, np.nan, dtype=float)
origin_index = np.full(n, -1, dtype=int)
trend = np.zeros(n, dtype=int)
bearish_sweep = np.zeros(n, dtype=bool)
bullish_sweep = np.zeros(n, dtype=bool)

active_highs = []
active_lows = []
liquidity_lines = []
bear_candidates = []
bull_candidates = []
cisd_lines = []
last_wicked_high_level = np.nan
last_wicked_low_level = np.nan
last_wicked_high_index = None
last_wicked_low_index = None
current_trend = 0

for index in range(n):
    pivot_index = index - swing_period
    if pivot_index >= swing_period:
        if unique_pivot(high_values, pivot_index, swing_period, "high"):
            pivot_high_confirmation[index] = True
            pivot_high_marker[pivot_index] = True
            level = {"start": pivot_index, "end": index, "price": float(high_values[pivot_index]), "kind": "high", "deleted": False}
            active_highs.insert(0, level)
            liquidity_lines.append(level)
        if unique_pivot(low_values, pivot_index, swing_period, "low"):
            pivot_low_confirmation[index] = True
            pivot_low_marker[pivot_index] = True
            level = {"start": pivot_index, "end": index, "price": float(low_values[pivot_index]), "kind": "low", "deleted": False}
            active_lows.insert(0, level)
            liquidity_lines.append(level)

    remaining_highs = []
    for level in active_highs:
        age = index - level["start"]
        if age < expiry_bars:
            level["end"] = index
            if high_values[index] >= level["price"]:
                wicked_high[index] = True
                last_wicked_high_level = level["price"]
                last_wicked_high_index = index
                wicked_high_level[index] = level["price"]
                if hide_mitigated_levels:
                    level["deleted"] = True
            else:
                remaining_highs.append(level)
        else:
            if hide_expired_levels:
                level["deleted"] = True
    active_highs = remaining_highs[:100]
    for level in remaining_highs[100:]:
        level["deleted"] = True

    remaining_lows = []
    for level in active_lows:
        age = index - level["start"]
        if age < expiry_bars:
            level["end"] = index
            if low_values[index] <= level["price"]:
                wicked_low[index] = True
                last_wicked_low_level = level["price"]
                last_wicked_low_index = index
                wicked_low_level[index] = level["price"]
                if hide_mitigated_levels:
                    level["deleted"] = True
            else:
                remaining_lows.append(level)
        else:
            if hide_expired_levels:
                level["deleted"] = True
    active_lows = remaining_lows[:100]
    for level in remaining_lows[100:]:
        level["deleted"] = True

    if index > 0 and close_values[index - 1] < open_values[index - 1] and close_values[index] > open_values[index]:
        bear_candidates.insert(0, (float(open_values[index]), index))
    if index > 0 and close_values[index - 1] > open_values[index - 1] and close_values[index] < open_values[index]:
        bull_candidates.insert(0, (float(open_values[index]), index))

    bearish_origin = None
    while bear_candidates and close_values[index] < bear_candidates[0][0]:
        candidate_level, candidate_index = bear_candidates[0]
        highest_close = float(np.max(close_values[candidate_index:index + 1]))
        scan = candidate_index - 1
        top = 0.0
        while scan >= 0 and close_values[scan] < open_values[scan]:
            top = float(open_values[scan])
            scan -= 1
        denominator = top - candidate_level
        ratio = (highest_close - candidate_level) / denominator if denominator != 0.0 else np.nan
        if np.isfinite(ratio) and ratio > noise_filter:
            bearish_origin = (candidate_level, candidate_index)
            bear_candidates.clear()
            break
        bear_candidates.pop(0)

    bullish_origin = None
    while bull_candidates and close_values[index] > bull_candidates[0][0]:
        candidate_level, candidate_index = bull_candidates[0]
        lowest_close = float(np.min(close_values[candidate_index:index + 1]))
        scan = candidate_index - 1
        bottom = 0.0
        while scan >= 0 and close_values[scan] > open_values[scan]:
            bottom = float(open_values[scan])
            scan -= 1
        denominator = candidate_level - bottom
        ratio = (candidate_level - lowest_close) / denominator if denominator != 0.0 else np.nan
        if np.isfinite(ratio) and ratio > noise_filter:
            bullish_origin = (candidate_level, candidate_index)
            bull_candidates.clear()
            break
        bull_candidates.pop(0)

    if bearish_origin is not None:
        level, start = bearish_origin
        cisd[index] = -1
        origin_level[index] = level
        origin_index[index] = start
        current_trend = -1
        cisd_lines.append({"start": start, "end": index, "price": level, "trend": -1})
        if last_wicked_high_index is not None and index - last_wicked_high_index <= liquidity_lookback and close_values[index] < last_wicked_high_level:
            bearish_sweep[index] = True
    if bullish_origin is not None:
        level, start = bullish_origin
        cisd[index] = 1
        origin_level[index] = level
        origin_index[index] = start
        current_trend = 1
        cisd_lines.append({"start": start, "end": index, "price": level, "trend": 1})
        if last_wicked_low_index is not None and index - last_wicked_low_index <= liquidity_lookback and close_values[index] > last_wicked_low_level:
            bullish_sweep[index] = True
    trend[index] = current_trend

visible_liquidity = [level for level in liquidity_lines if not level["deleted"]][-max_visible_levels:]
layers = []
for level in visible_liquidity:
    layers.append({
        "type": "line",
        "startIndex": int(level["start"]),
        "endIndex": int(level["end"]),
        "startPrice": float(level["price"]),
        "endPrice": float(level["price"]),
        "color": "rgba(148,163,184,0.45)",
        "lineWidth": 1,
        "dashed": False,
    })
for line_value in cisd_lines[-max_cisd_lines:]:
    color_value = bullish_color if line_value["trend"] > 0 else bearish_color
    layers.append({
        "type": "line",
        "startIndex": int(line_value["start"]),
        "endIndex": int(line_value["end"]),
        "startPrice": float(line_value["price"]),
        "endPrice": float(line_value["price"]),
        "color": color_value,
        "lineWidth": 3,
        "dashed": False,
    })


def marker_points(mask, values, color, size):
    return [
        {"value": float(values[index]), "color": color, "size": float(size)} if mask[index] else None
        for index in range(n)
    ]


plots = [
    {"name": "CISD Anchor", "title": False, "data": [float(value) for value in close_values], "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Swing High", "title": False, "data": marker_points(pivot_high_marker, high_values, bearish_color, 3.5), "color": bearish_color, "type": "scatter", "overlay": True},
    {"name": "Swing Low", "title": False, "data": marker_points(pivot_low_marker, low_values, bullish_color, 3.5), "color": bullish_color, "type": "scatter", "overlay": True},
]

signals = [
    {"type": "sell", "text": "▼", "name": "Bearish CISD Sweep", "data": [float(high_values[index]) if bearish_sweep[index] else None for index in range(n)], "color": bearish_color, "renderMode": "events"},
    {"type": "buy", "text": "▲", "name": "Bullish CISD Sweep", "data": [float(low_values[index]) if bullish_sweep[index] else None for index in range(n)], "color": bullish_color, "renderMode": "events"},
]

body_opacity = max(0.0, min(1.0, (100.0 - candle_body_transparency) / 100.0))
bar_colors = [
    rgba(bullish_color if value > 0 else bearish_color, body_opacity)
    for value in trend
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": [None] * n,
    "calculatedVars": {
        "pivotHighConfirmation": [bool(value) for value in pivot_high_confirmation],
        "pivotLowConfirmation": [bool(value) for value in pivot_low_confirmation],
        "wickedHigh": [bool(value) for value in wicked_high],
        "wickedLow": [bool(value) for value in wicked_low],
        "wickedHighLevel": [float(value) if np.isfinite(value) else None for value in wicked_high_level],
        "wickedLowLevel": [float(value) if np.isfinite(value) else None for value in wicked_low_level],
        "cisd": [int(value) for value in cisd],
        "originLevel": [float(value) if np.isfinite(value) else None for value in origin_level],
        "originIndex": [int(value) for value in origin_index],
        "trend": [int(value) for value in trend],
        "bearishSweep": [bool(value) for value in bearish_sweep],
        "bullishSweep": [bool(value) for value in bullish_sweep],
        "liquidityLineCount": int(len(visible_liquidity)),
        "cisdLineCount": int(min(len(cisd_lines), max_cisd_lines)),
        "candleWickTransparency": int(candle_wick_transparency),
    },
}
