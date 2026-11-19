# ============================================================
# QuantDinger Support Resistance with Candle Patterns
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; alerts do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Support Resistance with Candle Patterns"
my_indicator_description = (
    "Confirms support and resistance zones from repeated swing tests, tracks breaks and retests, "
    "and annotates reversal candle patterns that occur inside active zones."
)

# @param swing_lookback int 10 Swing lookback range=5:100:1
# @param pivot_tests int 2 Pivot tests for valid zone values=2,3,4
# @param zone_expiry int 200 Zone expiry bars range=0:2000:1
# @param show_broken bool true Show broken zones
# @param show_patterns bool true Show candle patterns
# @param proximity_percent float 0.3 Proximity percent range=0.05:5.0:0.05
# @param show_pivots bool false Show confirmed pivot markers
# @param show_retests bool false Show retest labels
# @param zone_opacity int 80 Zone transparency percent range=0:100:1
# @param resistance_color str #FF0000 Resistance color
# @param support_color str #008000 Support color
# @param broken_color str rgba(128,128,128,0.40) Broken zone color

swing_lookback = min(100, max(5, int(params.get("swing_lookback", 10))))
pivot_tests = int(params.get("pivot_tests", 2))
if pivot_tests not in (2, 3, 4):
    pivot_tests = 2
zone_expiry = min(2000, max(0, int(params.get("zone_expiry", 200))))
show_broken = bool(params.get("show_broken", True))
show_patterns = bool(params.get("show_patterns", True))
proximity_percent = min(5.0, max(0.05, float(params.get("proximity_percent", 0.3))))
show_pivots = bool(params.get("show_pivots", False))
show_retests = bool(params.get("show_retests", False))
zone_opacity = min(100, max(0, int(params.get("zone_opacity", 80))))
resistance_color = str(params.get("resistance_color", "#FF0000"))
support_color = str(params.get("support_color", "#008000"))
broken_color = str(params.get("broken_color", "rgba(128,128,128,0.40)"))

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
        return f"rgba({red},{green},{blue},{min(1.0, max(0.0, opacity)):.3f})"
    return value

def is_pivot(values, confirmation_index, period, high_pivot):
    center = confirmation_index - period
    if center < period:
        return False
    window = values[center - period:confirmation_index + 1]
    if len(window) != period * 2 + 1 or not np.all(np.isfinite(window)):
        return False
    candidate = values[center]
    others = np.concatenate((window[:period], window[period + 1:]))
    if high_pivot:
        return bool(candidate >= np.max(others) and np.any(candidate > others))
    return bool(candidate <= np.min(others) and np.any(candidate < others))

high_pivots = []
low_pivots = []
resistance_zones = []
support_zones = []
annotation_layers = []

def add_annotation(index, price, text, color, side):
    annotation_layers.append(
        {
            "type": "label",
            "index": int(index),
            "price": float(price),
            "text": text,
            "color": color,
            "textColor": "#FFFFFF",
            "side": side,
        }
    )

for index in range(n):
    high_pivot = is_pivot(high, index, swing_lookback, True)
    low_pivot = is_pivot(low, index, swing_lookback, False)

    if high_pivot:
        origin = index - swing_lookback
        high_pivots.insert(
            0,
            {
                "origin": origin,
                "extreme": float(high[origin]),
                "body": float(max(open_price[origin], close[origin])),
            },
        )
        high_pivots = high_pivots[:4]
        if show_pivots:
            annotation_layers.append(
                {
                    "type": "label",
                    "index": int(origin),
                    "price": float(high[origin]),
                    "text": "×",
                    "color": "rgba(0,0,0,0)",
                    "textColor": resistance_color,
                    "side": "down",
                }
            )
        if len(high_pivots) >= pivot_tests:
            base = high_pivots[pivot_tests - 1]
            recent = high_pivots[:pivot_tests - 1]
            if all(item["extreme"] < base["extreme"] and item["extreme"] > base["body"] for item in recent):
                resistance_zones.append(
                    {
                        "origin": int(base["origin"]),
                        "start": index,
                        "end": index,
                        "top": float(base["extreme"]),
                        "bottom": float(base["body"]),
                        "active": True,
                        "broken": False,
                        "expired": False,
                        "was_away": True,
                    }
                )

    if low_pivot:
        origin = index - swing_lookback
        low_pivots.insert(
            0,
            {
                "origin": origin,
                "extreme": float(low[origin]),
                "body": float(min(open_price[origin], close[origin])),
            },
        )
        low_pivots = low_pivots[:4]
        if show_pivots:
            annotation_layers.append(
                {
                    "type": "label",
                    "index": int(origin),
                    "price": float(low[origin]),
                    "text": "×",
                    "color": "rgba(0,0,0,0)",
                    "textColor": support_color,
                    "side": "up",
                }
            )
        if len(low_pivots) >= pivot_tests:
            base = low_pivots[pivot_tests - 1]
            recent = low_pivots[:pivot_tests - 1]
            if all(item["extreme"] > base["extreme"] and item["extreme"] < base["body"] for item in recent):
                support_zones.append(
                    {
                        "origin": int(base["origin"]),
                        "start": index,
                        "end": index,
                        "top": float(base["body"]),
                        "bottom": float(base["extreme"]),
                        "active": True,
                        "broken": False,
                        "expired": False,
                        "was_away": True,
                    }
                )

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
        else:
            zone["end"] = index
            if close[index] < zone["bottom"]:
                zone["was_away"] = True
            if was_away and close[index] >= zone["bottom"] and close[index] <= zone["top"] and index - zone["start"] > 2:
                if show_retests:
                    add_annotation(index, high[index], "⟳ R Retest", resistance_color, "down")
                zone["was_away"] = False

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
        else:
            zone["end"] = index
            if close[index] > zone["top"]:
                zone["was_away"] = True
            if was_away and close[index] <= zone["top"] and close[index] >= zone["bottom"] and index - zone["start"] > 2:
                if show_retests:
                    add_annotation(index, low[index], "⟳ S Retest", support_color, "up")
                zone["was_away"] = False

    if show_patterns and index >= 2:
        candle_range = abs(high[index] - low[index])
        previous_range = abs(high[index - 1] - low[index - 1])
        older_range = abs(high[index - 2] - low[index - 2])
        body = abs(open_price[index] - close[index])
        previous_body = abs(open_price[index - 1] - close[index - 1])
        older_body = abs(open_price[index - 2] - close[index - 2])
        body_percent = body / candle_range * 100.0 if candle_range > 0.0 else 0.0
        previous_body_percent = previous_body / previous_range * 100.0 if previous_range > 0.0 else 0.0
        evening = body > candle_range * 0.6 and open_price[index] > close[index] and previous_body < previous_range * 0.3 and older_body > older_range * 0.6 and open_price[index - 2] < close[index - 2]
        morning = body > candle_range * 0.6 and open_price[index] < close[index] and previous_body < previous_range * 0.3 and older_body > older_range * 0.6 and open_price[index - 2] > close[index - 2]
        inside = high[index] < high[index - 1] and low[index] > low[index - 1] and previous_body > previous_range * 0.4
        five_percent = previous_range * 0.05
        sixty_percent = previous_range * 0.60
        tweezer_bottom = low[index] > low[index - 1] - five_percent and low[index] < low[index - 1] + five_percent and close[index] > low[index - 1] + sixty_percent and open_price[index - 1] > close[index - 1] and close[index - 1] - low[index - 1] < five_percent and open_price[index] - low[index] < five_percent and body_percent > 60.0 and previous_body_percent > 60.0
        tweezer_top = high[index] > high[index - 1] - five_percent and high[index] < high[index - 1] + five_percent and close[index] < high[index - 1] - sixty_percent and open_price[index - 1] < close[index - 1] and high[index - 1] - close[index - 1] < five_percent and high[index] - open_price[index] < five_percent and body_percent > 60.0 and previous_body_percent > 60.0

        active_resistance = [item for item in resistance_zones if item["active"]]
        active_support = [item for item in support_zones if item["active"]]
        offset = previous_range * 0.6

        if evening and any(zone["bottom"] < high[index - 1] < zone["top"] for zone in active_resistance):
            add_annotation(index - 1, high[index - 1] + offset, "Evening Star", "#FFA500", "down")
        if inside and any(zone["bottom"] < high[index - 1] < zone["top"] for zone in active_resistance):
            add_annotation(index - 1, high[index - 1] + offset, "Inside Bar", "#E04305", "down")
        if tweezer_top and any(zone["bottom"] < max(high[index], high[index - 1]) < zone["top"] for zone in active_resistance):
            add_annotation(index - 1, high[index - 1] + offset, "Tweezer Top", "#FF00D0", "down")
        if morning and any(zone["bottom"] < low[index - 1] < zone["top"] for zone in active_support):
            add_annotation(index - 1, low[index - 1] - offset, "Morning Star", "#FFA500", "up")
        if inside and any(zone["bottom"] < low[index - 1] < zone["top"] for zone in active_support):
            add_annotation(index - 1, low[index - 1] - offset, "Inside Bar", "#FF4800", "up")
        if tweezer_bottom and any(zone["bottom"] < min(low[index], low[index - 1]) < zone["top"] for zone in active_support):
            add_annotation(index - 1, low[index - 1] - offset, "Tweezer Bottom", "#FF00D0", "up")

layers = []
fill_opacity = 1.0 - zone_opacity / 100.0

def add_zone_layers(zone, color):
    line_color = broken_color if zone["broken"] and show_broken else color
    fill_color = broken_color if zone["broken"] and show_broken else rgba(color, fill_opacity)
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
                "color": line_color,
                "lineWidth": 1,
            }
        )

for zone in resistance_zones:
    if zone["broken"] and not show_broken:
        continue
    add_zone_layers(zone, resistance_color)
for zone in support_zones:
    if zone["broken"] and not show_broken:
        continue
    add_zone_layers(zone, support_color)
layers.extend(annotation_layers)

price_guide = [float(value) if np.isfinite(value) else None for value in close]
plots = [
    {"name": "Price Reference", "title": False, "data": price_guide, "color": "rgba(255,255,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True}
]

output = {"name": my_indicator_name, "plots": plots, "signals": [], "layers": layers[-500:]}
