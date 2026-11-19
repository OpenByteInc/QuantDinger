# ============================================================
# QuantDinger Liquidation Levels
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Liquidation Levels"
my_indicator_description = "Estimates leverage liquidation levels from volume or volatility events and extends each untouched level until price trades through it."

# @param reference_price str open Reference price values=open,close,oc2,hl2,hlc3,ohlc4,hlcc4
# @param volume_threshold float 1.7 Volume threshold range=1.0:5.0:0.1
# @param volatility_threshold int 10 Volatility threshold range=0:100:5
# @param leverage_1_enabled bool true Enable first leverage
# @param leverage_1 int 25 First leverage range=1:500:1
# @param leverage_1_long_color color rgba(91,156,246,0.50) First long color
# @param leverage_1_short_color color rgba(240,98,146,0.50) First short color
# @param leverage_2_enabled bool true Enable second leverage
# @param leverage_2 int 50 Second leverage range=1:500:1
# @param leverage_2_long_color color rgba(77,208,225,0.50) Second long color
# @param leverage_2_short_color color rgba(186,104,200,0.50) Second short color
# @param leverage_3_enabled bool true Enable third leverage
# @param leverage_3 int 100 Third leverage range=1:500:1
# @param leverage_3_long_color color rgba(66,189,168,0.50) Third long color
# @param leverage_3_short_color color rgba(149,117,205,0.50) Third short color
# @param hide_bubbles bool false Hide liquidation bubbles
# @param hide_levels bool false Hide liquidation levels

reference_price = str(params.get("reference_price", "open")).lower()
if reference_price not in ("open", "close", "oc2", "hl2", "hlc3", "ohlc4", "hlcc4"):
    reference_price = "open"
volume_threshold = min(5.0, max(1.0, float(params.get("volume_threshold", 1.7))))
volatility_threshold = min(100, max(0, int(params.get("volatility_threshold", 10))))
hide_bubbles = bool(params.get("hide_bubbles", False))
hide_levels = bool(params.get("hide_levels", False))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].fillna(0.0).astype(float).to_numpy(dtype=float)
times = df["time"].to_numpy(dtype=float)
n = len(df)
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0

reference_sources = {
    "open": open_price, "close": close, "oc2": (open_price + close) * 0.5, "hl2": (high + low) * 0.5,
    "hlc3": (high + low + close) / 3.0, "ohlc4": (open_price + high + low + close) * 0.25,
    "hlcc4": (high + low + close * 2.0) * 0.25,
}
reference = reference_sources[reference_price]
volume_average = pd.Series(volume).rolling(13, min_periods=13).mean().to_numpy(dtype=float)
volume_tier_0 = np.isfinite(volume_average) & (volume > volume_average * volume_threshold)
volume_tier_1 = np.isfinite(volume_average) & (volume > volume_average * (1.0 + volume_threshold))
volume_tier_2 = np.isfinite(volume_average) & (volume > volume_average * (2.0 + volume_threshold))

liquidation_trigger = np.zeros(n, dtype=bool)
excursion = np.zeros(n, dtype=bool)
for index in range(n):
    ref = reference[index]
    if not np.isfinite(ref):
        continue
    lower_event = low[index] != ref and ref / (ref - low[index]) <= volatility_threshold
    upper_event = high[index] != ref and ref / (high[index] - ref) <= volatility_threshold
    liquidation_trigger[index] = bool(lower_event or upper_event)
    excursion[index] = bool(ref * (1.0 + 1.0 / 333.0) < high[index] or ref * (1.0 - 1.0 / 333.0) > low[index])

specs = [
    {"enabled": bool(params.get("leverage_1_enabled", True)), "value": min(500, max(1, int(params.get("leverage_1", 25)))), "long": str(params.get("leverage_1_long_color", "rgba(91,156,246,0.50)")), "short": str(params.get("leverage_1_short_color", "rgba(240,98,146,0.50)"))},
    {"enabled": bool(params.get("leverage_2_enabled", True)), "value": min(500, max(1, int(params.get("leverage_2", 50)))), "long": str(params.get("leverage_2_long_color", "rgba(77,208,225,0.50)")), "short": str(params.get("leverage_2_short_color", "rgba(186,104,200,0.50)"))},
    {"enabled": bool(params.get("leverage_3_enabled", True)), "value": min(500, max(1, int(params.get("leverage_3", 100)))), "long": str(params.get("leverage_3_long_color", "rgba(66,189,168,0.50)")), "short": str(params.get("leverage_3_short_color", "rgba(149,117,205,0.50)"))},
]

signal_buffers = []
level_events = []
for number, spec in enumerate(specs, start=1):
    upper_price = reference * (1.0 + 1.0 / spec["value"])
    lower_price = reference * (1.0 - 1.0 / spec["value"])
    enabled = spec["enabled"] and not hide_bubbles
    tiny = enabled & excursion & (volume_tier_0 | liquidation_trigger)
    small = enabled & excursion & volume_tier_1
    normal = enabled & excursion & volume_tier_2
    for side, prices, color, signal_type in (("Long", upper_price, spec["long"], "sell"), ("Short", lower_price, spec["short"], "buy")):
        for tier_name, events, marker_size in (("Tiny", tiny, 3), ("Small", small, 5), ("Normal", normal, 7)):
            signal_buffers.append({
                "type": signal_type, "name": f"L{number} {side} Bubble {tier_name}", "text": "●",
                "data": [float(prices[index]) if events[index] else None for index in range(n)],
                "color": color, "markerSize": marker_size, "renderMode": "events",
            })
    levels_visible = spec["enabled"] and not hide_levels
    create_upper = excursion & (liquidation_trigger | volume_tier_0) & (upper_price > high) if levels_visible else np.zeros(n, dtype=bool)
    create_lower = excursion & (liquidation_trigger | volume_tier_0) & (lower_price < low) if levels_visible else np.zeros(n, dtype=bool)
    level_events.append({"number": number, "upperPrice": upper_price, "lowerPrice": lower_price, "createUpper": create_upper, "createLower": create_lower, "longColor": spec["long"], "shortColor": spec["short"]})

active_lines = []
created_records = []
for index in range(n):
    survivors = []
    for line in active_lines:
        if high[index] > line["price"] and low[index] < line["price"]:
            continue
        line["endIndex"] = index + 1
        survivors.append(line)
    active_lines = survivors
    width = 3 if volume_tier_2[index] else 2 if volume_tier_1[index] else 1
    for event in level_events:
        if event["createUpper"][index]:
            record = {"startIndex": index, "endIndex": index + 1, "price": float(event["upperPrice"][index]), "color": event["longColor"], "width": width, "leverage": event["number"], "side": "upper"}
            active_lines.append(record); created_records.append(dict(record))
        if event["createLower"][index]:
            record = {"startIndex": index, "endIndex": index + 1, "price": float(event["lowerPrice"][index]), "color": event["shortColor"], "width": width, "leverage": event["number"], "side": "lower"}
            active_lines.append(record); created_records.append(dict(record))
    if len(active_lines) > 500:
        active_lines = active_lines[-500:]


def _interval():
    if n < 2: return 60000.0
    differences = np.diff(times); differences = differences[np.isfinite(differences) & (differences > 0.0)]
    return float(np.median(differences)) if len(differences) else 60000.0


interval = _interval()
layers = []
for line in active_lines:
    start_index = int(line["startIndex"]); end_index = int(line["endIndex"])
    layers.append({
        "type": "line", "role": f"L{line['leverage']} {line['side'].title()} Liquidation",
        "start": float(times[start_index]), "end": float(times[min(n - 1, end_index)] + (interval if end_index >= n else 0.0)),
        "y1": float(line["price"]), "y2": float(line["price"]), "color": line["color"], "lineWidth": int(line["width"]),
    })


def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


audit_levels = []
for event in level_events:
    audit_levels.append({
        "number": event["number"], "upperPrice": _finite_or_none(event["upperPrice"]), "lowerPrice": _finite_or_none(event["lowerPrice"]),
        "createUpper": event["createUpper"].tolist(), "createLower": event["createLower"].tolist(),
    })

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [{"name": "Price Guide", "title": False, "data": _finite_or_none(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True}],
    "signals": signal_buffers, "layers": layers, "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "reference": _finite_or_none(reference), "volumeAverage": _finite_or_none(volume_average),
        "volumeTier0": volume_tier_0.tolist(), "volumeTier1": volume_tier_1.tolist(), "volumeTier2": volume_tier_2.tolist(),
        "liquidationTrigger": liquidation_trigger.tolist(), "excursion": excursion.tolist(), "levels": audit_levels,
        "activeLines": active_lines, "createdRecords": created_records, "barInterval": interval,
    },
}
