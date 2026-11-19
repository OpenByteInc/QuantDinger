# ============================================================
# QuantDinger Fibonacci Time-Price Zones
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Fibonacci Time-Price Zones"
my_indicator_description = (
    "Builds Fibonacci time-price zones from two configurable anchors using circular, "
    "orthogonal, slope, or horizontal geometry."
)

# @param a_bars_ago int 120 Anchor A bars ago range=2:1000:1
# @param b_bars_ago int 60 Anchor B bars ago range=1:999:1
# @param a_price float 0 Anchor A price (0 = close)
# @param b_price float 0 Anchor B price (0 = close)
# @param zone_type str Retracement Zone type values=Retracement,Extension,Both
# @param shape str Circular Shape values=Circular,Orthogonal,Slope,Horizontal
# @param fill_percent float 15 Fill percent range=0:30:1
# @param show_trend bool true Show A-B trendline
# @param show_extension bool false Show extension guide
# @param show_retracement bool false Show retracement guide
# @param show_points bool true Show A/B points
# @param show_end_points bool false Show C/D points
# @param show_level_table bool true Show level table
# @param enabled_0 bool true Enable level 0
# @param level_0 float 0.236 Level 0 range=0:5:0.001
# @param enabled_1 bool true Enable level 1
# @param level_1 float 0.382 Level 1 range=0:5:0.001
# @param enabled_2 bool true Enable level 2
# @param level_2 float 0.5 Level 2 range=0:5:0.001
# @param enabled_3 bool true Enable level 3
# @param level_3 float 0.618 Level 3 range=0:5:0.001
# @param enabled_4 bool true Enable level 4
# @param level_4 float 0.786 Level 4 range=0:5:0.001
# @param enabled_5 bool true Enable level 5
# @param level_5 float 1.0 Level 5 range=0:5:0.001
# @param enabled_6 bool false Enable level 6
# @param level_6 float 1.236 Level 6 range=0:5:0.001

a_bars_ago = min(1000, max(2, int(params.get("a_bars_ago", 120))))
b_bars_ago = min(999, max(1, int(params.get("b_bars_ago", 60))))
a_price_input = float(params.get("a_price", 0.0))
b_price_input = float(params.get("b_price", 0.0))
zone_type = str(params.get("zone_type", "Retracement")).strip().title()
shape = str(params.get("shape", "Circular")).strip().title()
fill_percent = min(30.0, max(0.0, float(params.get("fill_percent", 15.0))))
show_trend = bool(params.get("show_trend", True))
show_extension = bool(params.get("show_extension", False))
show_retracement = bool(params.get("show_retracement", False))
show_points = bool(params.get("show_points", True))
show_end_points = bool(params.get("show_end_points", False))
show_level_table = bool(params.get("show_level_table", True))

enabled = [bool(params.get(f"enabled_{index}", index < 6)) for index in range(7)]
default_levels = [0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.236]
levels = [max(0.0, float(params.get(f"level_{index}", default_levels[index]))) for index in range(7)]
level_colors = ["#87CEEB", "#4682B4", "#32CD32", "#FFEB3B", "#FF8C00", "#8B0000", "#6A5ACD"]


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


frame = df.copy().reset_index(drop=True)
n = len(frame)
close_values = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype=float)

if n < 3:
    plots = [{"name": "Price Anchor", "data": finite(close_values), "color": "rgba(0,0,0,0)", "type": "line", "overlay": True}]
    output = {
        "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": [],
        "layers": [], "tables": [], "bar_colors": [None for _ in range(n)],
        "background_colors": [None for _ in range(n)], "calculatedVars": {"insufficientData": True},
    }
else:
    a_index = max(0, n - 1 - a_bars_ago)
    b_index = max(0, n - 1 - b_bars_ago)
    if a_index == b_index:
        a_index = max(0, b_index - 1)
    if a_index > b_index:
        a_index, b_index = b_index, a_index
        a_price_input, b_price_input = b_price_input, a_price_input

    a_price = float(close_values[a_index] if a_price_input == 0.0 else a_price_input)
    b_price = float(close_values[b_index] if b_price_input == 0.0 else b_price_input)
    index_span = max(1, b_index - a_index)
    price_delta = b_price - a_price
    if abs(price_delta) < 1e-12:
        price_delta = max(abs(b_price), 1.0) * 1e-6
    trend = 1 if a_price < b_price else -1
    slope = price_delta / float(index_span)
    aspect_ratio = price_delta / float(index_span)
    radius = abs(price_delta) * np.sqrt(2.0)
    scale_factor = np.log(2.0) if shape == "Circular" else np.sqrt(2.0) / 2.0
    end_index = b_index + index_span
    c_price = b_price + price_delta
    d_price = b_price - price_delta

    draw_upper = zone_type == "Both" or (trend == 1 and zone_type == "Extension") or (trend == -1 and zone_type == "Retracement")
    draw_lower = zone_type == "Both" or (trend == 1 and zone_type == "Retracement") or (trend == -1 and zone_type == "Extension")

    upper_matrix = np.full((7, n), np.nan, dtype=float)
    lower_matrix = np.full((7, n), np.nan, dtype=float)
    initial_upper = np.full(7, np.nan, dtype=float)
    initial_lower = np.full(7, np.nan, dtype=float)
    terminal_indexes = np.full(7, -1, dtype=int)

    for level_index, level in enumerate(levels):
        if not enabled[level_index]:
            continue
        offset = price_delta * np.sqrt(2.0) * scale_factor * level
        initial_upper[level_index] = b_price + offset if trend == 1 else b_price - offset
        initial_lower[level_index] = b_price - offset if trend == 1 else b_price + offset
        level_radius = radius * level * scale_factor
        aspect_radius = abs(int(np.rint(level_radius / aspect_ratio)))
        terminal_indexes[level_index] = b_index + aspect_radius

        for index in range(a_index, n):
            if index < b_index:
                upper_matrix[level_index, index] = initial_upper[level_index]
                lower_matrix[level_index, index] = initial_lower[level_index]
                continue

            in_radius = index <= b_index + aspect_radius
            if shape == "Circular":
                adjusted_x = (index - a_index) * aspect_ratio
                center_x = index_span * aspect_ratio
                offset_sq = level_radius * level_radius - (adjusted_x - center_x) ** 2
                if offset_sq >= -1e-12:
                    y_offset = np.sqrt(max(0.0, offset_sq))
                    upper_matrix[level_index, index] = b_price + y_offset
                    lower_matrix[level_index, index] = b_price - y_offset
            elif shape == "Horizontal":
                upper_matrix[level_index, index] = initial_upper[level_index]
                lower_matrix[level_index, index] = initial_lower[level_index]
            elif shape == "Orthogonal" and in_radius:
                upper_matrix[level_index, index] = initial_upper[level_index]
                lower_matrix[level_index, index] = initial_lower[level_index]
            elif shape == "Slope" and in_radius:
                delta_index = index - b_index
                upper_matrix[level_index, index] = initial_upper[level_index] + trend * -slope * delta_index
                lower_matrix[level_index, index] = initial_lower[level_index] + trend * slope * delta_index

    plots = [
        {"name": "Price Anchor", "title": False, "data": finite(close_values), "color": "rgba(0,0,0,0)", "type": "line", "overlay": True}
    ]

    def append_side(side_name, matrix, should_draw):
        if not should_draw:
            return
        active_indexes = [index for index, value in enumerate(enabled) if value]
        for level_index in active_indexes:
            plots.append({
                "name": f"{side_name} {levels[level_index]:g}", "title": False,
                "data": finite(matrix[level_index]), "color": level_colors[level_index],
                "type": "line", "lineWidth": 2, "lineStyle": "linebr",
                "connectNulls": False, "overlay": True,
            })

        if fill_percent <= 0.0 or not active_indexes:
            return
        previous = np.full(n, np.nan, dtype=float)
        previous[a_index:] = b_price
        for level_index in active_indexes:
            current = matrix[level_index]
            cloud = []
            for first, second in zip(previous, current):
                if np.isfinite(first) and np.isfinite(second):
                    cloud.append({"upper": float(max(first, second)), "lower": float(min(first, second))})
                else:
                    cloud.append(None)
            plots.append({
                "name": f"{side_name} Fill {levels[level_index]:g}", "title": False,
                "data": cloud, "color": level_colors[level_index], "type": "cloud",
                "opacity": fill_percent / 100.0, "overlay": True,
            })
            previous = current

    append_side("Upper", upper_matrix, draw_upper)
    append_side("Lower", lower_matrix, draw_lower)

    layers = []
    if show_trend:
        layers.append({
            "type": "line", "role": "A-B Trend", "startIndex": int(a_index), "endIndex": int(b_index),
            "y1": a_price, "y2": b_price, "color": "rgba(0,255,255,0.55)", "lineWidth": 3, "dashed": True,
        })
    if shape == "Orthogonal":
        for level_index in range(7):
            terminal = int(terminal_indexes[level_index])
            if not enabled[level_index] or terminal < 0 or terminal >= n:
                continue
            if draw_upper:
                layers.append({
                    "type": "line", "role": f"Upper Turn {levels[level_index]:g}",
                    "startIndex": terminal, "endIndex": terminal,
                    "y1": float(initial_upper[level_index]), "y2": b_price,
                    "color": level_colors[level_index], "lineWidth": 2,
                })
            if draw_lower:
                layers.append({
                    "type": "line", "role": f"Lower Turn {levels[level_index]:g}",
                    "startIndex": terminal, "endIndex": terminal,
                    "y1": float(initial_lower[level_index]), "y2": b_price,
                    "color": level_colors[level_index], "lineWidth": 2,
                })

    if show_points:
        layers.extend([
            {"type": "label", "index": int(a_index), "price": a_price, "text": "A", "color": "rgba(0,0,0,0)", "textColor": "#22D3EE", "side": "center", "anchor": "top"},
            {"type": "label", "index": int(b_index), "price": b_price, "text": "B", "color": "rgba(0,0,0,0)", "textColor": "#22D3EE", "side": "center", "anchor": "bottom"},
        ])
    if show_end_points and end_index < n:
        layers.extend([
            {"type": "label", "index": int(end_index), "price": float(c_price), "text": "C", "color": "rgba(0,0,0,0)", "textColor": "#22D3EE", "side": "center", "anchor": "top"},
            {"type": "label", "index": int(end_index), "price": float(d_price), "text": "D", "color": "rgba(0,0,0,0)", "textColor": "#22D3EE", "side": "center", "anchor": "bottom"},
        ])

    guide_extension = np.full(n, np.nan, dtype=float)
    guide_retracement = np.full(n, np.nan, dtype=float)
    for index in range(b_index, min(n, end_index + 1)):
        slope_line = a_price + slope * (index - a_index)
        retrace_line = b_price - slope * (index - b_index)
        if index % 10 == 0 and show_extension:
            guide_extension[index] = slope_line
        if index % 10 == 0 and show_retracement:
            guide_retracement[index] = retrace_line
    if show_extension:
        plots.append({"name": "Extension Guide", "title": False, "data": finite(guide_extension), "color": "rgba(59,130,246,0.7)", "type": "scatter", "size": 3, "overlay": True})
    if show_retracement:
        plots.append({"name": "Retracement Guide", "title": False, "data": finite(guide_retracement), "color": "rgba(59,130,246,0.7)", "type": "scatter", "size": 3, "overlay": True})

    tables = []
    if show_level_table:
        rows = []
        for level_index in range(7):
            if not enabled[level_index]:
                continue
            candidates = []
            if draw_upper:
                candidates.append(upper_matrix[level_index])
            if draw_lower:
                candidates.append(lower_matrix[level_index])
            latest_value = None
            for candidate in candidates:
                finite_indexes = np.flatnonzero(np.isfinite(candidate))
                if finite_indexes.size:
                    latest_value = float(candidate[finite_indexes[-1]])
                    break
            rows.append([
                {"text": f"{levels[level_index]:g}", "color": level_colors[level_index]},
                {"text": "—" if latest_value is None else f"{latest_value:.2f}", "align": "right"},
            ])
        tables.append({"title": f"{shape} · {zone_type}", "position": "top-right", "compact": True, "columns": ["Fib", "Price"], "rows": rows})

    output = {
        "schema_version": 2,
        "name": my_indicator_name,
        "plots": plots,
        "signals": [],
        "layers": layers,
        "tables": tables,
        "bar_colors": [None for _ in range(n)],
        "background_colors": [None for _ in range(n)],
        "calculatedVars": {
            "aIndex": int(a_index), "bIndex": int(b_index), "endIndex": int(end_index),
            "aPrice": a_price, "bPrice": b_price, "cPrice": float(c_price), "dPrice": float(d_price),
            "trend": int(trend), "shape": shape, "zoneType": zone_type,
            "levels": levels, "enabled": enabled,
            "initialUpper": finite(initial_upper), "initialLower": finite(initial_lower),
            "terminalIndexes": terminal_indexes.tolist(),
            "upperMatrix": [finite(row) for row in upper_matrix],
            "lowerMatrix": [finite(row) for row in lower_matrix],
        },
    }
