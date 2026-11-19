# ============================================================
# QuantDinger Delta Reaction Zones
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Delta Reaction Zones"
my_indicator_description = (
    "Creates volatility-scaled support and resistance zones from confirmed pivots "
    "in smoothed cumulative signed volume, with merging and reclaim signals."
)

# @param delta_smooth int 3 Delta EMA length range=1:50:1
# @param pivot_length int 12 Pivot left/right length range=2:50:1
# @param pivot_source str Cumulative_Delta Pivot source values=Cumulative_Delta,Cumulative_Delta_RoC
# @param roc_length int 12 Delta RoC length range=2:100:1
# @param impulse_window int 100 Impulse statistics window range=5:500:1
# @param atr_length int 14 ATR length range=1:100:1
# @param atr_multiplier float 0.35 Zone half-width ATR multiplier range=0.01:5:0.05
# @param maximum_zones int 8 Maximum active zones range=5:50:1
# @param merge_zones bool true Merge overlapping zones of the same type
# @param merge_gap_ticks float 20 Merge tolerance in ticks range=0:200:10
# @param tick_size float 0.01 Instrument tick size range=0.00000001:100:0.01
# @param show_zone_boxes bool true Show zone fills
# @param show_signals bool true Show reclaim and re-enter signals
# @param extend_bars int 65 Visual extension metadata range=0:600:5
# @param lower_zone_color str #00D084 Support color
# @param upper_zone_color str #FF4D6D Resistance color

delta_smooth = min(50, max(1, int(params.get("delta_smooth", 3))))
pivot_length = min(50, max(2, int(params.get("pivot_length", 12))))
pivot_source = str(params.get("pivot_source", "Cumulative_Delta"))
roc_length = min(100, max(2, int(params.get("roc_length", 12))))
impulse_window = min(500, max(5, int(params.get("impulse_window", 100))))
atr_length = min(100, max(1, int(params.get("atr_length", 14))))
atr_multiplier = min(5.0, max(0.01, float(params.get("atr_multiplier", 0.35))))
maximum_zones = min(50, max(5, int(params.get("maximum_zones", 8))))
merge_zones = bool(params.get("merge_zones", True))
merge_gap_ticks = min(200.0, max(0.0, float(params.get("merge_gap_ticks", 20.0))))
tick_size = min(100.0, max(0.00000001, float(params.get("tick_size", 0.01))))
show_zone_boxes = bool(params.get("show_zone_boxes", True))
show_signals = bool(params.get("show_signals", True))
extend_bars = min(600, max(0, int(params.get("extend_bars", 65))))
lower_zone_color = str(params.get("lower_zone_color", "#00D084"))
upper_zone_color = str(params.get("upper_zone_color", "#FF4D6D"))

df = df.copy()
open_values = df["open"].astype(float).to_numpy(dtype=float)
high_values = df["high"].astype(float).to_numpy(dtype=float)
low_values = df["low"].astype(float).to_numpy(dtype=float)
close_values = df["close"].astype(float).to_numpy(dtype=float)
volume_values = df["volume"].astype(float).fillna(0.0).to_numpy(dtype=float)
n = len(df)


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def rgba(color, alpha):
    value = str(color).lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    try:
        red, green, blue = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        red, green, blue = 128, 128, 128
    return f"rgba({red},{green},{blue},{min(1.0, max(0.0, float(alpha))):.3f})"


def ema(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if not len(source):
        return result
    alpha = 2.0 / (length + 1.0)
    state = float(source[0])
    result[0] = state
    for index in range(1, len(source)):
        state = alpha * float(source[index]) + (1.0 - alpha) * state
        result[index] = state
    return result


def wilder(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < length:
        return result
    state = float(np.mean(source[:length]))
    result[length - 1] = state
    for index in range(length, len(source)):
        state = (state * (length - 1) + float(source[index])) / length
        result[index] = state
    return result


signed_position = np.sign(close_values - open_values)
raw_delta = signed_position * volume_values
smoothed_delta = raw_delta.copy() if delta_smooth == 1 else ema(raw_delta, delta_smooth)
cumulative_delta = np.cumsum(smoothed_delta)
delta_roc = np.full(n, np.nan, dtype=float)
if n > roc_length:
    delta_roc[roc_length:] = cumulative_delta[roc_length:] - cumulative_delta[:-roc_length]
pivot_input = delta_roc if pivot_source == "Cumulative_Delta_RoC" else cumulative_delta

previous_close = np.r_[np.nan, close_values[:-1]]
true_range = np.maximum(
    high_values - low_values,
    np.maximum(np.abs(high_values - previous_close), np.abs(low_values - previous_close)),
)
if n:
    true_range[0] = high_values[0] - low_values[0]
atr = wilder(true_range, atr_length)

positive_flow = np.maximum(smoothed_delta, 0.0)
negative_flow = np.maximum(-smoothed_delta, 0.0)
sum_positive = pd.Series(positive_flow).rolling(impulse_window, min_periods=1).sum().to_numpy(dtype=float)
sum_negative = pd.Series(negative_flow).rolling(impulse_window, min_periods=1).sum().to_numpy(dtype=float)
total_flow = sum_positive + sum_negative
positive_percentage = np.divide(100.0 * sum_positive, total_flow, out=np.full(n, 50.0), where=total_flow > 0.0)
negative_percentage = 100.0 - positive_percentage
net_impulse = pd.Series(smoothed_delta).rolling(impulse_window, min_periods=1).sum().to_numpy(dtype=float)

pivot_high_confirmation = np.zeros(n, dtype=bool)
pivot_low_confirmation = np.zeros(n, dtype=bool)
pivot_anchor_index = np.full(n, -1, dtype=int)
for confirmation in range(2 * pivot_length, n):
    anchor = confirmation - pivot_length
    window = pivot_input[anchor - pivot_length:anchor + pivot_length + 1]
    if len(window) != 2 * pivot_length + 1 or not np.all(np.isfinite(window)):
        continue
    if pivot_input[anchor] >= float(np.max(window)):
        pivot_high_confirmation[confirmation] = True
        pivot_anchor_index[confirmation] = anchor
    if pivot_input[anchor] <= float(np.min(window)):
        pivot_low_confirmation[confirmation] = True
        pivot_anchor_index[confirmation] = anchor

zones = []
created_zones = []
support_reclaim = np.zeros(n, dtype=bool)
resistance_reenter = np.zeros(n, dtype=bool)
active_zone_count = np.zeros(n, dtype=int)
gap_price = merge_gap_ticks * tick_size


def make_zone(is_lower, anchor, confirmation):
    half_width = float(atr[anchor]) * atr_multiplier if np.isfinite(atr[anchor]) else 0.0
    price = float(low_values[anchor] if is_lower else high_values[anchor])
    return {
        "is_lower": bool(is_lower), "top": price + half_width, "bottom": price - half_width,
        "mid": price, "positive": float(positive_percentage[anchor]),
        "negative": float(negative_percentage[anchor]), "impulse": float(net_impulse[anchor]),
        "creation": int(anchor), "confirmation": int(confirmation), "last_touch": int(anchor),
        "merged": 0,
    }


def overlap(first, second):
    return first["bottom"] - gap_price <= second["top"] + gap_price and first["top"] + gap_price >= second["bottom"] - gap_price


for index in range(n):
    for is_lower, condition in ((False, pivot_high_confirmation[index]), (True, pivot_low_confirmation[index])):
        if not condition:
            continue
        anchor = int(pivot_anchor_index[index])
        new_zone = make_zone(is_lower, anchor, index)
        merged = False
        if merge_zones:
            for existing in reversed(zones):
                if existing["is_lower"] == new_zone["is_lower"] and overlap(existing, new_zone):
                    old_weight = max(abs(existing["impulse"]), 1.0)
                    new_weight = max(abs(new_zone["impulse"]), 1.0)
                    existing["top"] = max(existing["top"], new_zone["top"])
                    existing["bottom"] = min(existing["bottom"], new_zone["bottom"])
                    existing["mid"] = (existing["top"] + existing["bottom"]) * 0.5
                    existing["positive"] = (existing["positive"] * old_weight + new_zone["positive"] * new_weight) / (old_weight + new_weight)
                    existing["negative"] = 100.0 - existing["positive"]
                    existing["impulse"] += new_zone["impulse"]
                    existing["creation"] = min(existing["creation"], new_zone["creation"])
                    existing["last_touch"] = max(existing["last_touch"], new_zone["last_touch"])
                    existing["merged"] += 1
                    merged = True
                    break
        if not merged:
            zones.append(new_zone)
            created_zones.append(new_zone)
    if len(zones) > maximum_zones:
        zones = zones[-maximum_zones:]

    survivors = []
    for zone in zones:
        if high_values[index] >= zone["bottom"] and low_values[index] <= zone["top"]:
            zone["last_touch"] = index
        breached = close_values[index] < zone["bottom"] if zone["is_lower"] else close_values[index] > zone["top"]
        if breached:
            continue
        if index > 0:
            if zone["is_lower"] and close_values[index - 1] < zone["mid"] <= close_values[index]:
                support_reclaim[index] = True
            if (not zone["is_lower"]) and close_values[index - 1] > zone["mid"] >= close_values[index]:
                resistance_reenter[index] = True
        survivors.append(zone)
    zones = survivors
    active_zone_count[index] = len(zones)

plots = [{
    "name": "Price Anchor", "title": False, "data": finite(close_values),
    "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True,
}]
layers = []
for number, zone in enumerate(zones[-maximum_zones:], start=1):
    color = lower_zone_color if zone["is_lower"] else upper_zone_color
    start = max(0, int(zone["creation"]))
    cloud = [None] * n
    midpoint = [None] * n
    for index in range(start, n):
        if show_zone_boxes:
            cloud[index] = {"upper": float(zone["top"]), "lower": float(zone["bottom"]), "color": rgba(color, 0.20)}
        midpoint[index] = float(zone["mid"])
    if show_zone_boxes:
        plots.append({"name": f"Zone {number}", "title": False, "data": cloud, "color": rgba(color, 0.20), "type": "cloud", "overlay": True})
    plots.append({"name": f"Zone Mid {number}", "title": False, "data": midpoint, "color": rgba(color, 0.70), "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True})
    layers.append({
        "type": "line", "startIndex": start, "endIndex": n - 1,
        "startPrice": float(zone["mid"]), "endPrice": float(zone["mid"]),
        "color": rgba(color, 0.72), "lineWidth": 1, "dashed": True,
    })

signals = []
if show_signals:
    signals = [
        {"type": "buy", "name": "Support Reclaim", "text": "RC", "color": lower_zone_color, "renderMode": "events", "data": [float(low_values[index]) if support_reclaim[index] else None for index in range(n)]},
        {"type": "sell", "name": "Resistance Re-enter", "text": "RE", "color": upper_zone_color, "renderMode": "events", "data": [float(high_values[index]) if resistance_reenter[index] else None for index in range(n)]},
    ]

tables = []
if zones:
    rows = []
    for zone in reversed(zones[-6:]):
        impulse_text = f"{zone['impulse'] / 1000.0:.1f}K" if abs(zone["impulse"]) >= 1000.0 else f"{zone['impulse']:.0f}"
        rows.append([
            "S" if zone["is_lower"] else "R",
            f"{zone['mid']:,.2f}",
            f"B{zone['positive']:.0f}/S{zone['negative']:.0f}",
            f"Δ{impulse_text}",
        ])
    tables.append({"title": "Delta Reaction Zones", "position": "top-right", "compact": True, "rows": rows})

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals,
    "layers": layers, "tables": tables, "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "rawDelta": finite(raw_delta), "smoothedDelta": finite(smoothed_delta),
        "cumulativeDelta": finite(cumulative_delta), "deltaRoc": finite(delta_roc),
        "pivotInput": finite(pivot_input), "pivotHighConfirmation": pivot_high_confirmation.tolist(),
        "pivotLowConfirmation": pivot_low_confirmation.tolist(), "pivotAnchorIndex": pivot_anchor_index.tolist(),
        "atr": finite(atr), "positivePercentage": finite(positive_percentage),
        "negativePercentage": finite(negative_percentage), "netImpulse": finite(net_impulse),
        "supportReclaim": support_reclaim.tolist(), "resistanceReenter": resistance_reenter.tolist(),
        "activeZoneCount": active_zone_count.tolist(), "activeZones": zones,
        "createdZoneCount": int(len(created_zones)), "extendBars": int(extend_bars),
    },
}
