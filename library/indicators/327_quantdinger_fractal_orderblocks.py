# ============================================================
# QuantDinger Fractal Orderblocks
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Fractal Orderblocks"
my_indicator_description = "Finds order blocks after three- or five-bar fractal breaks, with optional nearby fair-value-gap qualification and fill removal."

# @param show_fractals bool false Show confirmed fractal markers
# @param fractal_filter str 3 Fractal width values=3,5
# @param break_source str Close Fractal break source values=Close,HL
# @param require_fvg bool true Require a nearby fair-value gap
# @param fvg_distance int 3 Maximum order-block to FVG distance range=1:6:1
# @param line_height str Body Order-block inner boundary values=Body,Wick
# @param delete_after_fill bool true Delete displayed levels after fill
# @param line_style str Solid Level style values=Solid,Dashed,Dotted
# @param line_length int 5 Projected level length in chart bars range=1:100:1
# @param line_width int 2 Level width range=1:4:1

show_fractals = bool(params.get("show_fractals", False))
fractal_filter = str(params.get("fractal_filter", "3"))
if fractal_filter not in ("3", "5"):
    fractal_filter = "3"
break_source = str(params.get("break_source", "Close")).upper()
if break_source not in ("CLOSE", "HL"):
    break_source = "CLOSE"
require_fvg = bool(params.get("require_fvg", True))
fvg_distance = min(6, max(1, int(params.get("fvg_distance", 3))))
line_height = str(params.get("line_height", "Body")).title()
if line_height not in ("Body", "Wick"):
    line_height = "Body"
delete_after_fill = bool(params.get("delete_after_fill", True))
line_style = str(params.get("line_style", "Solid")).title()
if line_style not in ("Solid", "Dashed", "Dotted"):
    line_style = "Solid"
line_length = min(100, max(1, int(params.get("line_length", 5))))
line_width = min(4, max(1, int(params.get("line_width", 2))))


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

fractal_high_event = np.zeros(n, dtype=bool)
fractal_low_event = np.zeros(n, dtype=bool)
fractal_high_value = np.full(n, np.nan, dtype=float)
fractal_low_value = np.full(n, np.nan, dtype=float)
bear_order_event = np.zeros(n, dtype=bool)
bull_order_event = np.zeros(n, dtype=bool)
pending_highs = []
pending_lows = []
active_bear = []
active_bull = []
archived_zones = []
created_zones = []
filled_zones = []

for index in range(n):
    if fractal_filter == "3":
        high_found = index >= 2 and high[index] < high[index - 1] and (
            high[index - 2] < high[index - 1] or
            (index >= 3 and high[index - 2] == high[index - 1] and high[index - 3] < high[index - 2])
        )
        low_found = index >= 2 and low[index] > low[index - 1] and (
            low[index - 2] > low[index - 1] or
            (index >= 3 and low[index - 2] == low[index - 1] and low[index - 3] > low[index - 2])
        )
        center = index - 1
    else:
        high_found = index >= 4 and high[index] < high[index - 2] and high[index - 1] < high[index - 2] and high[index - 3] < high[index - 2] and high[index - 4] < high[index - 2]
        low_found = index >= 4 and low[index] > low[index - 2] and low[index - 1] > low[index - 2] and low[index - 3] > low[index - 2] and low[index - 4] > low[index - 2]
        center = index - 2
    if high_found:
        fractal_high_event[center] = True
        fractal_high_value[center] = high[center]
        pending_highs.append({"index": int(center), "value": float(high[center])})
    if low_found:
        fractal_low_event[center] = True
        fractal_low_value[center] = low[center]
        pending_lows.append({"index": int(center), "value": float(low[center])})

    for pending_index in range(len(pending_lows) - 1, -1, -1):
        fractal = pending_lows[pending_index]
        break_value = close[index] if break_source == "CLOSE" else low[index]
        if break_value >= fractal["value"]:
            continue
        candidate_offset = 0
        current_max = float(low[index])
        gap_offset = 0
        first_position = max(fractal["index"], index - 500)
        for position in range(index, first_position - 1, -1):
            offset = index - position
            bearish_gap = position >= 2 and close[position - 1] < low[position - 2] and high[position] < low[position - 2]
            if close[position] > opened[position] and high[position] > current_max:
                candidate_offset = offset
                current_max = float(high[position])
            if bearish_gap and high[position] > current_max:
                gap_offset = offset + 2
        fvg_valid = (not require_fvg) or (gap_offset > 0 and candidate_offset - gap_offset >= 0 and candidate_offset - gap_offset <= fvg_distance)
        if candidate_offset != 0 and fvg_valid:
            source_index = index - candidate_offset
            zone = {
                "side": "bear", "sourceIndex": int(source_index), "formedIndex": int(index),
                "top": float(high[source_index]), "bottom": float(opened[source_index] if line_height == "Body" else low[source_index]),
                "endIndex": int(source_index + line_length), "candidateOffset": int(candidate_offset), "gapOffset": int(gap_offset),
                "filledIndex": None,
            }
            active_bear.append(zone)
            created_zones.append(zone.copy())
            bear_order_event[index] = True
        pending_lows.pop(pending_index)

    for pending_index in range(len(pending_highs) - 1, -1, -1):
        fractal = pending_highs[pending_index]
        break_value = close[index] if break_source == "CLOSE" else high[index]
        if break_value <= fractal["value"]:
            continue
        candidate_offset = 0
        current_min = float(low[index])
        gap_offset = 0
        first_position = max(fractal["index"], index - 500)
        for position in range(index, first_position - 1, -1):
            offset = index - position
            bullish_gap = position >= 2 and close[position - 1] > high[position - 2] and low[position] > high[position - 2]
            if close[position] < opened[position] and low[position] < current_min:
                candidate_offset = offset
                current_min = float(low[position])
            if bullish_gap:
                gap_offset = offset + 2
        fvg_valid = (not require_fvg) or (gap_offset > 0 and candidate_offset - gap_offset >= 0 and candidate_offset - gap_offset <= fvg_distance)
        if candidate_offset != 0 and fvg_valid:
            source_index = index - candidate_offset
            zone = {
                "side": "bull", "sourceIndex": int(source_index), "formedIndex": int(index),
                "top": float(opened[source_index] if line_height == "Body" else high[source_index]), "bottom": float(low[source_index]),
                "endIndex": int(source_index + line_length), "candidateOffset": int(candidate_offset), "gapOffset": int(gap_offset),
                "filledIndex": None,
            }
            active_bull.append(zone)
            created_zones.append(zone.copy())
            bull_order_event[index] = True
        pending_highs.pop(pending_index)

    kept = []
    for zone in active_bear:
        if high[index] >= zone["top"] and high[index] >= zone["bottom"]:
            item = zone.copy()
            item["filledIndex"] = int(index)
            filled_zones.append(item)
            if not delete_after_fill:
                archived_zones.append(item)
        else:
            kept.append(zone)
    active_bear = kept
    kept = []
    for zone in active_bull:
        if low[index] <= zone["bottom"] and low[index] <= zone["top"]:
            item = zone.copy()
            item["filledIndex"] = int(index)
            filled_zones.append(item)
            if not delete_after_fill:
                archived_zones.append(item)
        else:
            kept.append(zone)
    active_bull = kept

layers = []
display_zones = (archived_zones + active_bear + active_bull)[-50:]
for zone in display_zones:
    color = "#FF0000" if zone["side"] == "bear" else "#0000FF"
    for role, price in (("Outer", zone["top"] if zone["side"] == "bear" else zone["bottom"]), ("Inner", zone["bottom"] if zone["side"] == "bear" else zone["top"])):
        layers.append({
            "type": "line", "role": f"{zone['side'].title()} OB {role}",
            "startIndex": zone["sourceIndex"], "endIndex": zone["endIndex"], "y1": price, "y2": price,
            "color": color, "lineWidth": line_width, "dashed": line_style == "Dashed", "dotted": line_style == "Dotted",
        })


def _fractal_points(events, bullish):
    if not show_fractals:
        return [None] * n
    return [
        {"active": True, "price": float(low[index] if bullish else high[index]), "text": "Fractal", "color": "#0000FF" if bullish else "#FF0000"}
        if events[index] else None for index in range(n)
    ]


output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [{"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True}],
    "signals": [
        {"type": "sell", "name": "Fractal High", "data": _fractal_points(fractal_high_event, False), "color": "#FF0000", "renderMode": "events"},
        {"type": "buy", "name": "Fractal Low", "data": _fractal_points(fractal_low_event, True), "color": "#0000FF", "renderMode": "events"},
    ],
    "layers": layers, "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "fractalHighEvent": fractal_high_event.tolist(), "fractalLowEvent": fractal_low_event.tolist(),
        "fractalHighValue": _finite(fractal_high_value), "fractalLowValue": _finite(fractal_low_value),
        "bearOrderEvent": bear_order_event.tolist(), "bullOrderEvent": bull_order_event.tolist(),
        "pendingHighs": pending_highs, "pendingLows": pending_lows,
        "activeBear": active_bear, "activeBull": active_bull, "archivedZones": archived_zones,
        "createdZones": created_zones, "filledZones": filled_zones,
        "fractalFilter": fractal_filter, "breakSource": break_source, "requireFvg": require_fvg,
    },
}
