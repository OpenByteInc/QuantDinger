# ============================================================
# QuantDinger Percent Reversal ZigZag
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Percent Reversal ZigZag"
my_indicator_description = (
    "Builds a close-confirmed percentage or ATR reversal ZigZag, annotates pivot prices "
    "and geometric patterns, and projects the next leg from observed price/time ranges."
)

# @param reversal_method str MANUAL Reversal method values=MANUAL,ATR005,ATR010,ATR020,ATR050,ATR100,ATR250
# @param manual_percent float 0.25 Manual reversal percent range=0:99:0.01
# @param atr_multiplier float 1.0 ATR multiplier range=0.01:20:0.01
# @param show_real_pivots bool true Anchor drawings at actual extrema
# @param show_zigzag_lines bool true Show ZigZag lines
# @param show_pivot_price bool false Show pivot prices
# @param show_triangle_rates bool false Show triangle rates
# @param show_angles bool false Show experimental angles
# @param show_patterns bool true Show pattern names
# @param show_harmonic bool true Detect harmonic triangles
# @param show_tap bool true Detect double, triple, and quadruple taps
# @param show_abcd bool true Detect AB=CD patterns
# @param show_bat bool true Detect Bat patterns
# @param show_butterfly bool true Detect Butterfly patterns
# @param show_gartley bool true Detect Gartley patterns
# @param show_crab bool true Detect Crab patterns
# @param show_shark bool true Detect Shark patterns
# @param show_5o bool true Detect 5-0 patterns
# @param show_wolfe bool true Detect Wolfe patterns
# @param show_contracting bool true Detect contracting triangles
# @param show_expanding bool true Detect expanding triangles
# @param show_head_shoulders bool true Detect head and shoulders
# @param show_projections bool true Show next-leg projection
# @param show_projection_box bool true Show projection bounds
# @param show_projection_rates bool false Show 0.618 and 1.618 targets
# @param stats_length int 10 Projection statistics smoothing range=2:100:1
# @param error_margin float 5.0 Pattern error percent range=0:50:0.5
# @param max_pivots int 120 Maximum retained pivots range=20:250:1

reversal_method = str(params.get("reversal_method", "MANUAL")).upper()
manual_percent = min(99.0, max(0.0, float(params.get("manual_percent", 0.25)))) / 100.0
atr_multiplier = min(20.0, max(0.01, float(params.get("atr_multiplier", 1.0))))
show_real_pivots = bool(params.get("show_real_pivots", True))
show_zigzag_lines = bool(params.get("show_zigzag_lines", True))
show_pivot_price = bool(params.get("show_pivot_price", False))
show_triangle_rates = bool(params.get("show_triangle_rates", False))
show_angles = bool(params.get("show_angles", False))
show_patterns = bool(params.get("show_patterns", True))
show_harmonic = bool(params.get("show_harmonic", True))
show_tap = bool(params.get("show_tap", True))
show_abcd = bool(params.get("show_abcd", True))
show_bat = bool(params.get("show_bat", True))
show_butterfly = bool(params.get("show_butterfly", True))
show_gartley = bool(params.get("show_gartley", True))
show_crab = bool(params.get("show_crab", True))
show_shark = bool(params.get("show_shark", True))
show_5o = bool(params.get("show_5o", True))
show_wolfe = bool(params.get("show_wolfe", True))
show_contracting = bool(params.get("show_contracting", True))
show_expanding = bool(params.get("show_expanding", True))
show_head_shoulders = bool(params.get("show_head_shoulders", True))
show_projections = bool(params.get("show_projections", True))
show_projection_box = bool(params.get("show_projection_box", True))
show_projection_rates = bool(params.get("show_projection_rates", False))
stats_length = min(100, max(2, int(params.get("stats_length", 10))))
error_margin = min(50.0, max(0.0, float(params.get("error_margin", 5.0)))) / 100.0
max_pivots = min(250, max(20, int(params.get("max_pivots", 120))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
time_values = df["time"].astype(float).to_numpy(dtype=float) if "time" in df.columns else np.arange(len(df), dtype=float)
n = len(df)
epsilon = 1e-12

def wilder_rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    seed = values[:length]
    if np.all(np.isfinite(seed)):
        result[length - 1] = float(np.mean(seed))
        for index in range(length, len(values)):
            result[index] = (result[index - 1] * (length - 1) + values[index]) / length
    return result

true_range = np.zeros(n, dtype=float)
for index in range(n):
    true_range[index] = high[index] - low[index] if index == 0 else max(
        high[index] - low[index],
        abs(high[index] - close[index - 1]),
        abs(low[index] - close[index - 1]),
    )

atr_length_map = {"ATR005": 5, "ATR010": 10, "ATR020": 20, "ATR050": 50, "ATR100": 100, "ATR250": 250}
atr_values = wilder_rma(true_range, atr_length_map.get(reversal_method, 5))
reversal_fraction = np.full(n, manual_percent, dtype=float)
if reversal_method in atr_length_map:
    valid = np.isfinite(atr_values) & (np.abs(open_price) > epsilon)
    reversal_fraction[:] = np.nan
    reversal_fraction[valid] = atr_values[valid] / np.abs(open_price[valid]) * atr_multiplier

direction_up = True
high_track = np.nan
low_track = np.nan
high_index = None
low_index = None
reverse_line = np.full(n, np.nan, dtype=float)
pivots = []

def append_pivot(price, real_index, confirmation_index):
    draw_index = real_index if show_real_pivots else confirmation_index
    if pivots and pivots[-1]["price"] == float(price) and pivots[-1]["real_index"] == int(real_index):
        return False
    pivots.append(
        {
            "price": float(price),
            "index": int(draw_index),
            "real_index": int(real_index),
            "confirmation_index": int(confirmation_index),
            "time": float(time_values[draw_index]),
        }
    )
    if len(pivots) > max_pivots:
        del pivots[0]
    return True

for index in range(1, n):
    fraction = reversal_fraction[index]
    if not np.isfinite(fraction):
        reverse_line[index] = reverse_line[index - 1] if index > 0 else np.nan
        continue
    if direction_up:
        low_track = np.nan
        low_index = index
        if not np.isfinite(high_track):
            if high[index] > high[index - 1]:
                high_track = float(high[index])
                high_index = index
            else:
                high_track = float(high[index - 1])
                high_index = index - 1
        elif high[index] > high_track:
            high_track = float(high[index])
            high_index = index
        reversal_range = abs(high_track) * fraction
        reverse_line[index] = high_track - reversal_range
        if close[index] <= reverse_line[index]:
            append_pivot(high_track, high_index, index)
            direction_up = False
            continue
    else:
        high_track = np.nan
        high_index = None
        if not np.isfinite(low_track):
            if low[index] < low[index - 1]:
                low_track = float(low[index])
                low_index = index
            else:
                low_track = float(low[index - 1])
                low_index = index - 1
        elif low[index] < low_track:
            low_track = float(low[index])
            low_index = index
        reversal_range = abs(low_track) * fraction
        reverse_line[index] = low_track + reversal_range
        if close[index] >= reverse_line[index]:
            append_pivot(low_track, low_index, index)
            direction_up = True

def safe_rate(c_value, b_value, a_value):
    denominator = b_value - c_value
    if not all(np.isfinite(value) for value in (c_value, b_value, a_value)) or abs(denominator) <= epsilon:
        return np.nan
    return float((a_value - b_value) / denominator)

def in_range(value, lower, upper):
    return bool(np.isfinite(value) and lower <= value <= upper)

def harmonic(value):
    return any(in_range(value, -(1.618 ** (-5 + step)) - error_margin, -(1.618 ** (-5 + step)) + error_margin) for step in range(1, 13))

def match4(values, ranges):
    return all(in_range(value, lower - error_margin, upper + error_margin) for value, (lower, upper) in zip(values, ranges))

def detect_patterns(last_seven):
    if len(last_seven) < 7:
        return [], {}
    a, b, c, d, e, f, g = [point["price"] for point in reversed(last_seven[-7:])]
    rates = {
        "gfc": safe_rate(g, f, c), "gfa": safe_rate(g, f, a),
        "gdc": safe_rate(g, d, c), "gda": safe_rate(g, d, a),
        "gfe": safe_rate(g, f, e), "gba": safe_rate(g, b, a),
        "fed": safe_rate(f, e, d), "feb": safe_rate(f, e, b),
        "fcb": safe_rate(f, c, b), "edc": safe_rate(e, d, c),
        "eda": safe_rate(e, d, a), "eba": safe_rate(e, b, a),
        "dcb": safe_rate(d, c, b), "cba": safe_rate(c, b, a),
    }
    r = rates
    valid_gfa = r["fed"] >= -1 and r["feb"] >= -1 and r["cba"] <= -1 and r["eda"] <= -1
    valid_gda = r["fed"] <= -1 and r["gfe"] >= -1 and r["cba"] <= -1 and r["dcb"] >= -1
    valid_gba = r["feb"] <= -1 and r["gfe"] >= -1 and r["cba"] <= -1 and r["dcb"] <= -1
    valid_eba = r["cba"] <= -1 and r["dcb"] <= -1
    valid_eda = r["cba"] <= -1 and r["dcb"] >= -1
    valid_fcb = r["fed"] >= -1 and r["edc"] <= -1
    valid_feb = r["edc"] >= -1 and r["dcb"] <= -1
    names = []
    if show_harmonic:
        for key, valid in (("gfa", valid_gfa), ("gda", valid_gda), ("gba", valid_gba), ("eba", valid_eba), ("eda", valid_eda), ("cba", True)):
            if valid and harmonic(r[key]):
                names.append("Harmonic Triangle " + key.upper())
    if show_tap:
        for key, valid in (("cba", True), ("eba", valid_eba), ("eda", valid_eda)):
            if valid and in_range(r[key], -1.0 - error_margin, -1.0 + error_margin):
                names.append("Double Tap " + key.upper())
        if all(in_range(r[key], -1.0 - error_margin, -1.0 + error_margin) for key in ("edc", "cba")):
            names.append("Triple Tap")
        if all(in_range(r[key], -1.0 - error_margin, -1.0 + error_margin) for key in ("gfe", "edc", "cba")):
            names.append("Quadruple Tap")
    if show_abcd:
        abcd_ranges = [(-1.618, -1.270), (-0.786, -0.618)]
        for values, valid, tag in (
            ((r["cba"], r["dcb"]), True, "CBA/DCB"),
            ((r["cba"], r["fcb"]), valid_fcb, "CBA/FCB"),
            ((r["eba"], r["feb"]), valid_feb, "EBA/FEB"),
            ((r["eda"], r["fed"]), valid_eda, "EDA/FED"),
        ):
            if valid and match4(values, abcd_ranges):
                names.append("AB=CD " + tag)
    pattern_specs = [
        ("Bat", show_bat, [(-0.500, -0.382), (-0.886, -0.382), (-2.618, -1.618), (-0.886, -0.886)]),
        ("Butterfly", show_butterfly, [(-0.786, -0.786), (-0.886, -0.382), (-2.618, -1.618), (-1.618, -1.270)]),
        ("Gartley", show_gartley, [(-0.618, -0.618), (-0.886, -0.382), (-2.618, -1.130), (-0.786, -0.786)]),
        ("Crab", show_crab, [(-0.886, -0.886), (-0.886, -0.382), (-3.618, -2.000), (-1.618, -1.618)]),
        ("Shark", show_shark, [(-0.886, -0.886), (-1.618, -1.130), (-2.240, -1.270), (-1.130, -0.886)]),
        ("5-0", show_5o, [(-1.618, -1.130), (-2.240, -1.618), (-0.500, -0.500), (-0.236, 0.236)]),
        ("Wolfe", show_wolfe, [(-1.618, -1.270), (-5.000, 0.000), (-1.618, -1.270), (-5.000, 0.000)]),
        ("Contracting Triangle", show_contracting, [(-0.886, -0.236)] * 4),
        ("Expanding Triangle", show_expanding, [(-2.618, -1.125)] * 4),
    ]
    variants = [
        ((r["edc"], r["dcb"], r["cba"], r["eda"]), True, "EDC/DCB/CBA/EDA"),
        ((r["gfe"], r["feb"], r["eba"], r["gfa"]), valid_eba, "GFE/FEB/EBA/GFA"),
        ((r["gfe"], r["fed"], r["eda"], r["gfa"]), valid_eda, "GFE/FED/EDA/GFA"),
    ]
    for pattern_name, enabled, ranges in pattern_specs:
        if not enabled:
            continue
        for values, valid, tag in variants:
            if valid and match4(values, ranges):
                names.append(pattern_name + " " + tag)
    if show_head_shoulders:
        hns_values = (r["fed"], r["feb"], r["dcb"], r["edc"], r["eda"], r["cba"])
        hns_ranges = [(-0.618, -0.090), (-0.886, -0.090), (-1.250, -0.750), (-9.999, -1.000), (-1.618, -0.090), (-0.886, -0.090)]
        if match4(hns_values, hns_ranges):
            names.append("Head and Shoulders")
    return names, rates

layers = []
price_average = None
time_average = None
price_pos_dev = 0.0
price_neg_dev = 0.0
time_pos_dev = 0.0
time_neg_dev = 0.0
weight = 2.0 / (stats_length + 1.0)
price_annotation_start = max(1, len(pivots) - 8)
pattern_annotation_start = max(1, len(pivots) - 3)

for pivot_number, pivot in enumerate(pivots):
    if pivot_number == 0:
        continue
    previous = pivots[pivot_number - 1]
    rising = pivot["price"] > previous["price"]
    if show_zigzag_lines:
        layers.append({"type": "line", "startIndex": int(previous["index"]), "endIndex": int(pivot["index"]), "y1": float(previous["price"]), "y2": float(pivot["price"]), "color": "#008080" if rising else "#FF9800", "lineWidth": 2, "dashed": False})

    price_range = abs(pivot["price"] - previous["price"])
    time_range = abs(pivot["time"] - previous["time"])
    if price_average is None:
        price_average = price_range
        time_average = time_range
    else:
        price_average = weight * price_range + (1.0 - weight) * price_average
        time_average = weight * time_range + (1.0 - weight) * time_average
    price_delta = price_range - price_average
    time_delta = time_range - time_average
    price_pos_dev = weight * max(0.0, price_delta) + (1.0 - weight) * price_pos_dev
    price_neg_dev = weight * min(0.0, price_delta) + (1.0 - weight) * price_neg_dev
    time_pos_dev = weight * max(0.0, time_delta) + (1.0 - weight) * time_pos_dev
    time_neg_dev = weight * min(0.0, time_delta) + (1.0 - weight) * time_neg_dev

    pattern_names, rates = detect_patterns(pivots[max(0, pivot_number - 6):pivot_number + 1]) if show_patterns or show_triangle_rates else ([], {})
    if pivot_number >= price_annotation_start or (pivot_number >= pattern_annotation_start and pattern_names):
        parts = []
        if show_pivot_price and pivot_number >= price_annotation_start:
            parts.append(("%.5f" % pivot["price"]).rstrip("0").rstrip("."))
        if pivot_number >= pattern_annotation_start:
            parts.extend(pattern_names)
        if parts:
            layers.append({"type": "label", "index": int(pivot["index"]), "price": float(pivot["price"]), "text": "\n".join(parts), "color": "#FF9800" if rising else "#008080", "textColor": "#000000", "side": "down" if rising else "up"})

    if show_triangle_rates and pivot_number == len(pivots) - 1 and len(pivots) >= 7 and rates:
        recent = list(reversed(pivots[pivot_number - 6:pivot_number + 1]))
        point_map = {name: recent[index] for index, name in enumerate(("a", "b", "c", "d", "e", "f", "g"))}
        rate_lines = [("cba", "a", "c"), ("dcb", "b", "d")]
        if rates["cba"] < rates["edc"]:
            rate_lines.extend([("eda", "a", "e"), ("eba", "a", "e")])
        if rates["cba"] < rates["edc"] < rates["gfe"]:
            rate_lines.extend([("gfa", "a", "g"), ("gda", "a", "g"), ("gba", "a", "g")])
        if rates["dcb"] < rates["fed"]:
            rate_lines.append(("feb", "b", "f"))
        for rate_key, first_key, second_key in rate_lines[:4]:
            rate_value = rates.get(rate_key)
            if not np.isfinite(rate_value):
                continue
            first = point_map[first_key]
            second = point_map[second_key]
            text = "Price: %.3f" % rate_value
            if show_angles:
                sin_angle = np.degrees(np.arcsin(rate_value)) if -1.0 <= rate_value <= 1.0 else 0.0
                cos_angle = np.degrees(np.arccos(rate_value)) if -1.0 <= rate_value <= 1.0 else 0.0
                tan_angle = np.degrees(np.arctan(rate_value))
                text += " (sin:%d°, cos:%d°, tan:%d°)" % (round(sin_angle), round(cos_angle), round(tan_angle))
            layers.append({"type": "line", "startIndex": int(first["index"]), "endIndex": int(second["index"]), "y1": float(first["price"]), "y2": float(second["price"]), "color": "#808080", "lineWidth": 1, "dashed": True})
            layers.append({"type": "label", "index": int(round((first["index"] + second["index"]) / 2.0)), "price": float((first["price"] + second["price"]) / 2.0), "text": text, "color": "#C0C0C0", "textColor": "#000000", "side": "center"})

positive_intervals = np.diff(time_values)
positive_intervals = positive_intervals[positive_intervals > 0]
bar_interval = float(np.median(positive_intervals)) if positive_intervals.size else 1.0

if show_projections and len(pivots) >= 2 and price_average is not None and time_average is not None:
    pivot = pivots[-1]
    previous = pivots[-2]
    falling_next = pivot["price"] > previous["price"]
    target_price = pivot["price"] - price_average if falling_next else pivot["price"] + price_average
    lower_price = pivot["price"] - price_average - price_pos_dev if falling_next else pivot["price"] + price_average + price_pos_dev
    upper_price = pivot["price"] - price_average - price_neg_dev if falling_next else pivot["price"] + price_average + price_neg_dev
    target_index = min(n - 1, int(pivot["index"] + max(1, round(time_average / bar_interval))))
    lower_time_index = min(n - 1, int(pivot["index"] + max(1, round((time_average + time_neg_dev) / bar_interval))))
    upper_time_index = min(n - 1, int(pivot["index"] + max(1, round((time_average + time_pos_dev) / bar_interval))))
    index_0618 = min(n - 1, int(pivot["index"] + max(1, round((time_average + time_neg_dev) * 0.618 / bar_interval))))
    index_1618 = min(n - 1, int(pivot["index"] + max(1, round((time_average + time_pos_dev) * 1.618 / bar_interval))))
    price_0618 = pivot["price"] - (price_average + price_neg_dev) * 0.618 if falling_next else pivot["price"] + (price_average + price_neg_dev) * 0.618
    price_1618 = pivot["price"] - (price_average + price_pos_dev) * 1.618 if falling_next else pivot["price"] + (price_average + price_pos_dev) * 1.618
    layers.append({"type": "line", "startIndex": int(pivot["index"]), "endIndex": target_index, "y1": float(pivot["price"]), "y2": float(target_price), "color": "#FF9800", "lineWidth": 1, "dashed": True, "extendRight": False})
    layers.append({"type": "line", "startIndex": target_index, "endIndex": target_index, "y1": float(lower_price), "y2": float(upper_price), "color": "#0066FF", "lineWidth": 1, "dashed": True})
    layers.append({"type": "line", "startIndex": lower_time_index, "endIndex": upper_time_index, "y1": float(target_price), "y2": float(target_price), "color": "#0066FF", "lineWidth": 1, "dashed": True})
    if show_projection_box:
        layers.append({"type": "zone", "startIndex": min(index_0618, index_1618), "endIndex": max(index_0618, index_1618), "top": float(max(price_0618, price_1618)), "bottom": float(min(price_0618, price_1618)), "fillColor": "rgba(128,128,128,0.04)", "borderColor": "#808080", "borderSize": 2, "dashed": True, "extendRight": False})
    if show_projection_rates:
        layers.append({"type": "label", "index": index_0618, "price": float(price_0618), "text": "Target 0.618 @ %.5f" % price_0618, "color": "rgba(192,192,192,0.80)", "textColor": "#000000", "side": "up" if falling_next else "down"})
        layers.append({"type": "label", "index": index_1618, "price": float(price_1618), "text": "Target 1.618 @ %.5f" % price_1618, "color": "rgba(192,192,192,0.80)", "textColor": "#000000", "side": "down" if falling_next else "up"})

reverse_plot = [float(value) if np.isfinite(value) else None for value in reverse_line]

plots = [
    {"name": "Reversal Line", "data": reverse_plot, "color": "rgba(128,128,128,0.60)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Price Anchor", "title": False, "data": [float(value) for value in close], "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
]

output = {"name": my_indicator_name, "plots": plots, "signals": [], "layers": layers[-500:]}
