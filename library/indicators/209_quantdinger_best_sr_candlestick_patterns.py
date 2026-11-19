# ============================================================
# QuantDinger Best S/R Candlestick Patterns
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; alerts do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Best S/R Candlestick Patterns"
my_indicator_description = (
    "Builds support and resistance zones from repeated swing tests, tracks "
    "breaks and retests, and annotates reversal patterns inside active zones."
)

# @param swing_lookback int 10 Swing lookback range=5:100:1
# @param pivot_tests int 2 Pivot tests for valid zone values=2,3,4
# @param zone_expiry int 200 Zone expiry bars range=0:2000:1
# @param resistance_color str #F23645 Resistance color
# @param support_color str #089981 Support color
# @param broken_color str rgba(128,128,128,0.40) Broken zone color
# @param zone_transparency int 80 Zone transparency percent range=0:100:1
# @param show_broken bool true Show broken zones
# @param show_patterns bool true Show candle pattern labels
# @param proximity_percent float 0.3 Proximity percent range=0.05:5.0:0.05
# @param show_retests bool true Show retest labels
# @param show_pivots bool true Show confirmed pivot points
# @param label_spacing int 4 Minimum bars between labels on the same side range=0:20:1
# @param label_history int 120 Label history bars range=50:1000:10

swing_lookback = min(100, max(5, int(params.get("swing_lookback", 10))))
pivot_tests = int(params.get("pivot_tests", 2))
if pivot_tests not in (2, 3, 4):
    pivot_tests = 2
zone_expiry = min(2000, max(0, int(params.get("zone_expiry", 200))))
resistance_color = str(params.get("resistance_color", "#F23645"))
support_color = str(params.get("support_color", "#089981"))
broken_color = str(params.get("broken_color", "rgba(128,128,128,0.40)"))
zone_transparency = min(100, max(0, int(params.get("zone_transparency", 80))))
show_broken = bool(params.get("show_broken", True))
show_patterns = bool(params.get("show_patterns", True))
proximity_percent = min(
    5.0, max(0.05, float(params.get("proximity_percent", 0.3)))
)
show_retests = bool(params.get("show_retests", True))
show_pivots = bool(params.get("show_pivots", True))
label_spacing = min(20, max(0, int(params.get("label_spacing", 4))))
label_history = min(1000, max(50, int(params.get("label_history", 120))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def rgba(color, opacity):
    value = str(color).strip()
    if value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            red = int(value[1] * 2, 16)
            green = int(value[2] * 2, 16)
            blue = int(value[3] * 2, 16)
        else:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
        alpha = min(1.0, max(0.0, float(opacity)))
        return f"rgba({red},{green},{blue},{alpha:.3f})"
    return value


def is_pivot(values, confirmation_index, period, high_pivot):
    center = confirmation_index - period
    if center < period:
        return False
    window = values[center - period:confirmation_index + 1]
    if len(window) != period * 2 + 1:
        return False
    if not all(np.isfinite(float(value)) for value in window):
        return False
    candidate = float(values[center])
    others = [float(value) for offset, value in enumerate(window) if offset != period]
    if high_pivot:
        return candidate >= max(others) and any(candidate > value for value in others)
    return candidate <= min(others) and any(candidate < value for value in others)


last_label_index = {"up": -100000, "down": -100000}


def append_label(target, index, price, text, color, side):
    side_key = "up" if side == "up" else "down"
    if int(index) - last_label_index[side_key] < label_spacing:
        return
    target.append(
        {
            "type": "label",
            "index": int(index),
            "price": float(price),
            "text": text,
            "color": color,
            "textColor": "#FFFFFF",
            "side": side,
            "size": "small",
        }
    )
    last_label_index[side_key] = int(index)


high_pivots = []
low_pivots = []
resistance_zones = []
support_zones = []
annotations = []
pivot_high_plot = [None] * n
pivot_low_plot = [None] * n
new_zone_events = [False] * n
near_resistance_events = [False] * n
near_support_events = [False] * n

for index in range(n):
    confirmed_high = is_pivot(high, index, swing_lookback, True)
    confirmed_low = is_pivot(low, index, swing_lookback, False)

    if confirmed_high:
        origin = index - swing_lookback
        pivot_high_plot[origin] = float(high[origin]) if show_pivots else None
        high_pivots.insert(
            0,
            {
                "origin": int(origin),
                "extreme": float(high[origin]),
                "body": float(max(open_price[origin], close[origin])),
            },
        )
        high_pivots = high_pivots[:4]
        if len(high_pivots) >= pivot_tests:
            base = high_pivots[pivot_tests - 1]
            recent = high_pivots[:pivot_tests - 1]
            valid = all(
                item["extreme"] < base["extreme"]
                and item["extreme"] > base["body"]
                for item in recent
            )
            if valid:
                resistance_zones.append(
                    {
                        "origin": base["origin"],
                        "start": index,
                        "end": index,
                        "top": base["extreme"],
                        "bottom": base["body"],
                        "active": True,
                        "broken": False,
                        "expired": False,
                        "was_away": True,
                    }
                )
                new_zone_events[index] = True

    if confirmed_low:
        origin = index - swing_lookback
        pivot_low_plot[origin] = float(low[origin]) if show_pivots else None
        low_pivots.insert(
            0,
            {
                "origin": int(origin),
                "extreme": float(low[origin]),
                "body": float(min(open_price[origin], close[origin])),
            },
        )
        low_pivots = low_pivots[:4]
        if len(low_pivots) >= pivot_tests:
            base = low_pivots[pivot_tests - 1]
            recent = low_pivots[:pivot_tests - 1]
            valid = all(
                item["extreme"] > base["extreme"]
                and item["extreme"] < base["body"]
                for item in recent
            )
            if valid:
                support_zones.append(
                    {
                        "origin": base["origin"],
                        "start": index,
                        "end": index,
                        "top": base["body"],
                        "bottom": base["extreme"],
                        "active": True,
                        "broken": False,
                        "expired": False,
                        "was_away": True,
                    }
                )
                new_zone_events[index] = True

    near_resistance = False
    for zone in resistance_zones:
        if not zone["active"]:
            continue
        was_away = zone["was_away"]
        broken = close[index] > zone["top"]
        expired = zone_expiry > 0 and index - zone["start"] > zone_expiry
        if broken or expired:
            zone["active"] = False
            zone["broken"] = bool(broken)
            zone["expired"] = bool(expired)
            zone["end"] = max(zone["start"], index - 1)
            continue
        zone["end"] = index
        buffer = zone["top"] * proximity_percent / 100.0
        if zone["bottom"] - buffer < close[index] < zone["top"] + buffer:
            near_resistance = True
        if close[index] < zone["bottom"]:
            zone["was_away"] = True
        if (
            was_away
            and zone["bottom"] <= close[index] <= zone["top"]
            and index - zone["start"] > 2
        ):
            if show_retests:
                append_label(
                    annotations,
                    index,
                    zone["bottom"],
                    "R Retest",
                    resistance_color,
                    "down",
                )
            zone["was_away"] = False
    near_resistance_events[index] = near_resistance

    near_support = False
    for zone in support_zones:
        if not zone["active"]:
            continue
        was_away = zone["was_away"]
        broken = close[index] < zone["bottom"]
        expired = zone_expiry > 0 and index - zone["start"] > zone_expiry
        if broken or expired:
            zone["active"] = False
            zone["broken"] = bool(broken)
            zone["expired"] = bool(expired)
            zone["end"] = max(zone["start"], index - 1)
            continue
        zone["end"] = index
        buffer = zone["top"] * proximity_percent / 100.0
        if zone["bottom"] - buffer < close[index] < zone["top"] + buffer:
            near_support = True
        if close[index] > zone["top"]:
            zone["was_away"] = True
        if (
            was_away
            and zone["bottom"] <= close[index] <= zone["top"]
            and index - zone["start"] > 2
        ):
            if show_retests:
                append_label(
                    annotations,
                    index,
                    zone["top"],
                    "S Retest",
                    support_color,
                    "up",
                )
            zone["was_away"] = False
    near_support_events[index] = near_support

    if not show_patterns or index < 2:
        continue
    candle_range = abs(high[index] - low[index])
    previous_range = abs(high[index - 1] - low[index - 1])
    older_range = abs(high[index - 2] - low[index - 2])
    body = abs(open_price[index] - close[index])
    previous_body = abs(open_price[index - 1] - close[index - 1])
    older_body = abs(open_price[index - 2] - close[index - 2])
    body_percent = body / candle_range * 100.0 if candle_range > 0.0 else 0.0
    previous_body_percent = (
        previous_body / previous_range * 100.0 if previous_range > 0.0 else 0.0
    )
    evening = (
        body > candle_range * 0.6
        and open_price[index] > close[index]
        and previous_body < previous_range * 0.3
        and older_body > older_range * 0.6
        and open_price[index - 2] < close[index - 2]
    )
    morning = (
        body > candle_range * 0.6
        and open_price[index] < close[index]
        and previous_body < previous_range * 0.3
        and older_body > older_range * 0.6
        and open_price[index - 2] > close[index - 2]
    )
    inside = (
        high[index] < high[index - 1]
        and low[index] > low[index - 1]
        and previous_body > previous_range * 0.4
    )
    five_percent = previous_range * 0.05
    sixty_percent = previous_range * 0.60
    tweezer_bottom = (
        low[index - 1] - five_percent < low[index] < low[index - 1] + five_percent
        and close[index] > low[index - 1] + sixty_percent
        and open_price[index - 1] > close[index - 1]
        and close[index - 1] - low[index - 1] < five_percent
        and open_price[index] - low[index] < five_percent
        and body_percent > 60.0
        and previous_body_percent > 60.0
    )
    tweezer_top = (
        high[index - 1] - five_percent < high[index] < high[index - 1] + five_percent
        and close[index] < high[index - 1] - sixty_percent
        and open_price[index - 1] < close[index - 1]
        and high[index - 1] - close[index - 1] < five_percent
        and high[index] - open_price[index] < five_percent
        and body_percent > 60.0
        and previous_body_percent > 60.0
    )
    active_resistance = [zone for zone in resistance_zones if zone["active"]]
    active_support = [zone for zone in support_zones if zone["active"]]
    offset = previous_range * 0.6

    if evening and any(
        zone["bottom"] < high[index - 1] < zone["top"]
        for zone in active_resistance
    ):
        append_label(
            annotations,
            index - 1,
            high[index - 1] + offset,
            "Evening Star",
            "#F59E0B",
            "down",
        )
    if inside and any(
        zone["bottom"] < high[index - 1] < zone["top"]
        for zone in active_resistance
    ):
        append_label(
            annotations,
            index - 1,
            high[index - 1] + offset,
            "Inside Bar",
            "#E04305",
            "down",
        )
    if tweezer_top and any(
        zone["bottom"] < max(high[index], high[index - 1]) < zone["top"]
        for zone in active_resistance
    ):
        append_label(
            annotations,
            index - 1,
            high[index - 1] + offset,
            "Tweezer Top",
            "#FF00D0",
            "down",
        )
    if morning and any(
        zone["bottom"] < low[index - 1] < zone["top"]
        for zone in active_support
    ):
        append_label(
            annotations,
            index - 1,
            low[index - 1] - offset,
            "Morning Star",
            "#F59E0B",
            "up",
        )
    if inside and any(
        zone["bottom"] < low[index - 1] < zone["top"]
        for zone in active_support
    ):
        append_label(
            annotations,
            index - 1,
            low[index - 1] - offset,
            "Inside Bar",
            "#FF4800",
            "up",
        )
    if tweezer_bottom and any(
        zone["bottom"] < min(low[index], low[index - 1]) < zone["top"]
        for zone in active_support
    ):
        append_label(
            annotations,
            index - 1,
            low[index - 1] - offset,
            "Tweezer Bottom",
            "#FF00D0",
            "up",
        )

layers = []
fill_opacity = 1.0 - zone_transparency / 100.0


def append_zone_layers(zone, color):
    rendered_color = broken_color if zone["broken"] and show_broken else color
    fill_color = (
        broken_color
        if zone["broken"] and show_broken
        else rgba(color, fill_opacity)
    )
    layers.append(
        {
            "type": "zone",
            "startIndex": int(zone["origin"]),
            "endIndex": int(zone["end"]),
            "top": float(zone["top"]),
            "bottom": float(zone["bottom"]),
            "fillColor": fill_color,
            "borderColor": "rgba(0,0,0,0)",
            "borderSize": 0,
        }
    )
    for price in (zone["top"], zone["bottom"]):
        layers.append(
            {
                "type": "line",
                "startIndex": int(zone["origin"]),
                "endIndex": int(zone["end"]),
                "y1": float(price),
                "y2": float(price),
                "color": rendered_color,
                "lineWidth": 1,
            }
        )


for zone in resistance_zones:
    if not (zone["broken"] and not show_broken):
        append_zone_layers(zone, resistance_color)
for zone in support_zones:
    if not (zone["broken"] and not show_broken):
        append_zone_layers(zone, support_color)
label_cutoff = max(0, n - label_history)
layers.extend(
    annotation
    for annotation in annotations
    if int(annotation.get("index", 0)) >= label_cutoff
)

active_resistance_count = sum(1 for zone in resistance_zones if zone["active"])
active_support_count = sum(1 for zone in support_zones if zone["active"])

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Resistance Pivots",
            "data": pivot_high_plot,
            "color": resistance_color,
            "type": "circle",
            "lineWidth": 3,
            "overlay": True,
        },
        {
            "name": "Support Pivots",
            "data": pivot_low_plot,
            "color": support_color,
            "type": "circle",
            "lineWidth": 3,
            "overlay": True,
        },
        {
            "name": "Runtime Anchor",
            "title": False,
            "data": [float(value) if np.isfinite(value) else None for value in close],
            "color": "rgba(255,255,255,0.01)",
            "type": "line",
            "lineWidth": 1,
            "overlay": True,
        },
    ],
    "signals": [],
    "layers": layers[-500:],
    "calculatedVars": {
        "activeResistanceZones": active_resistance_count,
        "activeSupportZones": active_support_count,
        "newZoneOnLastBar": bool(new_zone_events[-1]) if n else False,
        "nearResistance": bool(near_resistance_events[-1]) if n else False,
        "nearSupport": bool(near_support_events[-1]) if n else False,
    },
}
