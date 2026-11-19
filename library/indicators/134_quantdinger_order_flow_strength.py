# ============================================================
# QuantDinger Order Flow Strength
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Order Flow Strength"
my_indicator_description = (
    "Detects structure-break order blocks, scores displacement and relative "
    "volume, filters mitigated or overlapping zones, and tracks the strongest block."
)

# @param pivot_lookback int 5 Pivot lookback range=2:50:1
# @param max_unmitigated int 5 Maximum displayed unmitigated blocks range=1:50:1
# @param hide_overlapped bool true Hide overlapping blocks
# @param show_strength_labels bool true Show strength labels
# @param show_strongest bool true Show strongest order-block plot
# @param strongest_buffer int 5 Strongest-block scan depth range=1:100:1
# @param bullish_color color #089981 Bullish order-block color
# @param bearish_color color #F23645 Bearish order-block color

pivot_lookback = min(50, max(2, int(params.get("pivot_lookback", 5))))
max_unmitigated = min(50, max(1, int(params.get("max_unmitigated", 5))))
hide_overlapped = bool(params.get("hide_overlapped", True))
show_strength_labels = bool(params.get("show_strength_labels", True))
show_strongest = bool(params.get("show_strongest", True))
strongest_buffer = min(100, max(1, int(params.get("strongest_buffer", 5))))
bullish_color = str(params.get("bullish_color", "#089981"))
bearish_color = str(params.get("bearish_color", "#F23645"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

volume_sma = pd.Series(volume, dtype=float).rolling(20, min_periods=20).mean().to_numpy(dtype=float)
pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
for confirmation_index in range(2 * pivot_lookback, n):
    pivot_index = confirmation_index - pivot_lookback
    left = pivot_index - pivot_lookback
    right = pivot_index + pivot_lookback + 1
    if high[pivot_index] >= float(np.max(high[left:right])):
        pivot_high[confirmation_index] = float(high[pivot_index])
    if low[pivot_index] <= float(np.min(low[left:right])):
        pivot_low[confirmation_index] = float(low[pivot_index])

blocks = []
strongest_top = np.full(n, np.nan, dtype=float)
strongest_bottom = np.full(n, np.nan, dtype=float)
strongest_median = np.full(n, np.nan, dtype=float)
strongest_bullish = np.zeros(n, dtype=bool)
strongest_changed = np.zeros(n, dtype=bool)
strongest_strength = np.full(n, np.nan, dtype=float)
bullish_break_event = np.zeros(n, dtype=bool)
bearish_break_event = np.zeros(n, dtype=bool)

last_high = np.nan
last_low = np.nan
last_high_index = None
last_low_index = None

for index in range(n):
    if np.isfinite(pivot_high[index]):
        last_high = float(pivot_high[index])
        last_high_index = index - pivot_lookback
    if np.isfinite(pivot_low[index]):
        last_low = float(pivot_low[index])
        last_low_index = index - pivot_lookback

    bullish_break = (
        index > 0
        and np.isfinite(last_high)
        and close[index] > last_high
        and close[index - 1] <= last_high
    )
    bearish_break = (
        index > 0
        and np.isfinite(last_low)
        and close[index] < last_low
        and close[index - 1] >= last_low
    )
    bullish_break_event[index] = bullish_break
    bearish_break_event[index] = bearish_break

    if bullish_break or bearish_break:
        pivot_index = last_high_index if bullish_break else last_low_index
        if pivot_index is not None:
            search_length = max(1, index - int(pivot_index))
            selected_offset = None
            selected_metric = np.nan
            for offset in range(1, search_length + 1):
                candidate = index - offset
                if candidate < 0:
                    break
                if bullish_break and close[candidate] < open_price[candidate]:
                    if selected_offset is None or low[candidate] < selected_metric:
                        selected_offset = offset
                        selected_metric = float(low[candidate])
                if bearish_break and close[candidate] > open_price[candidate]:
                    if selected_offset is None or high[candidate] > selected_metric:
                        selected_offset = offset
                        selected_metric = float(high[candidate])

            if selected_offset is not None:
                block_index = index - selected_offset
                block_top = float(high[block_index])
                block_bottom = float(low[block_index])
                block_height = block_top - block_bottom
                breakout_distance = (
                    close[index] - block_top
                    if bullish_break
                    else block_bottom - close[index]
                )
                average_volume = volume_sma[block_index]
                relative_volume = (
                    float(volume[block_index] / average_volume)
                    if np.isfinite(average_volume) and average_volume != 0.0
                    else 0.0
                )
                displacement_factor = min(max(breakout_distance / max(block_height * 5.0, 1e-12), 0.0), 1.0)
                volume_factor = min(max(relative_volume / 2.0, 0.0), 1.0)
                strength = round((displacement_factor * 0.6 + volume_factor * 0.4) * 100.0, 1)
                blocks.insert(
                    0,
                    {
                        "top": block_top,
                        "bottom": block_bottom,
                        "start": block_index,
                        "strength": strength,
                        "bullish": bool(bullish_break),
                        "mitigated": False,
                    },
                )

        if bullish_break:
            last_high = np.nan
            last_high_index = None
        else:
            last_low = np.nan
            last_low_index = None

    for block in blocks:
        if not block["mitigated"]:
            block["mitigated"] = (
                low[index] < block["bottom"]
                if block["bullish"]
                else high[index] > block["top"]
            )

    active_checked = 0
    strongest = None
    for block in blocks:
        if block["mitigated"]:
            continue
        if strongest is None or block["strength"] > strongest["strength"]:
            strongest = block
        active_checked += 1
        if active_checked >= strongest_buffer:
            break

    if strongest is not None:
        strongest_top[index] = float(strongest["top"])
        strongest_bottom[index] = float(strongest["bottom"])
        strongest_median[index] = (strongest["top"] + strongest["bottom"]) / 2.0
        strongest_bullish[index] = bool(strongest["bullish"])
        strongest_strength[index] = float(strongest["strength"])
        strongest_changed[index] = bool(
            index > 0 and np.isfinite(strongest_median[index - 1])
            and strongest_median[index] != strongest_median[index - 1]
        )

    if len(blocks) > 100:
        retained = []
        for block in blocks:
            if not block["mitigated"] or len(retained) < 50:
                retained.append(block)
            if len(retained) >= 100:
                break
        blocks = retained

top_data = []
bottom_data = []
median_data = []
strongest_cloud = []
for index in range(n):
    visible = show_strongest and np.isfinite(strongest_median[index]) and not strongest_changed[index]
    color = bullish_color if strongest_bullish[index] else bearish_color
    if visible:
        top_data.append({"value": float(strongest_top[index]), "color": color})
        bottom_data.append({"value": float(strongest_bottom[index]), "color": color})
        median_data.append({"value": float(strongest_median[index]), "color": color})
        strongest_cloud.append({"upper": float(strongest_top[index]), "lower": float(strongest_bottom[index]), "color": color})
    else:
        top_data.append(None)
        bottom_data.append(None)
        median_data.append(None)
        strongest_cloud.append(None)

layers = []
occupied = []
active_count = 0
for block in blocks:
    if block["mitigated"]:
        continue
    overlapped = False
    if hide_overlapped:
        for other_top, other_bottom in occupied:
            if (
                other_bottom <= block["top"] <= other_top
                or other_bottom <= block["bottom"] <= other_top
                or (block["top"] >= other_top and block["bottom"] <= other_bottom)
            ):
                overlapped = True
                break
    if overlapped:
        continue

    color = bullish_color if block["bullish"] else bearish_color
    fill = color
    layers.append(
        {
            "type": "zone",
            "startIndex": int(block["start"]),
            "endIndex": max(0, n - 1),
            "top": float(block["top"]),
            "bottom": float(block["bottom"]),
            "fillColor": fill,
            "borderColor": color,
            "borderSize": 1,
            "dashed": True,
            "opacity": 0.20,
        }
    )
    if show_strength_labels:
        layers.append(
            {
                "type": "label",
                "index": max(0, n - 1),
                "price": float((block["top"] + block["bottom"]) / 2.0),
                "text": f"{block['strength']:.1f}%",
                "color": "rgba(0, 0, 0, 0.0)",
                "textColor": color,
                "side": "right",
            }
        )
    occupied.append((float(block["top"]), float(block["bottom"])))
    active_count += 1
    if active_count >= max_unmitigated:
        break

plots = [
    {"name": "Strongest OB Top", "data": top_data, "color": bullish_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Strongest OB Bottom", "data": bottom_data, "color": bullish_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Strongest OB Zone", "title": False, "data": strongest_cloud, "color": bullish_color, "type": "cloud", "opacity": 0.10, "overlay": True},
    {"name": "Strongest OB Median", "data": median_data, "color": bullish_color, "type": "line", "lineWidth": 2, "overlay": True},
]

plots = [plot for plot in plots if any(item is not None for item in plot["data"])]
plots.append(
    {
        "name": "Price Reference",
        "title": False,
        "data": [float(value) for value in close],
        "color": "rgba(255, 255, 255, 0.01)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    }
)

def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": layers,
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "volumeSma": _finite_or_none(volume_sma),
        "pivotHigh": _finite_or_none(pivot_high),
        "pivotLow": _finite_or_none(pivot_low),
        "bullishBreak": bullish_break_event.tolist(),
        "bearishBreak": bearish_break_event.tolist(),
        "strongestTop": _finite_or_none(strongest_top),
        "strongestBottom": _finite_or_none(strongest_bottom),
        "strongestMedian": _finite_or_none(strongest_median),
        "strongestBullish": strongest_bullish.tolist(),
        "strongestStrength": _finite_or_none(strongest_strength),
        "strongestChanged": strongest_changed.tolist(),
        "blocks": blocks,
    },
}
