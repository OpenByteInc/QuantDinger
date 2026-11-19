# ============================================================
# QuantDinger Impulse Retention Map
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Impulse Retention Map"
my_indicator_description = (
    "Maps efficient ATR-sized close displacement, then tracks how much of the "
    "impulse remains through half-back, unwind, recovery, replacement, and expiry."
)

# @param impulse_span int 3 Impulse window range=1:12:1
# @param minimum_move float 2.0 Minimum move in pre-impulse ATR range=0.5:8.0:0.1
# @param minimum_efficiency float 0.75 Minimum path efficiency range=0.3:1.0:0.05
# @param atr_length int 20 ATR baseline range=5:100:1
# @param maximum_life int 60 Maximum tracking bars range=5:250:1
# @param maps_to_keep int 6 Maps to keep range=1:20:1
# @param show_outcomes bool true Show outcome labels
# @param show_panel bool true Show status panel
# @param panel_position str Bottom_left Panel position values=Bottom_left,Top_right,Bottom_right
# @param show_rails bool true Show latest map price labels
# @param up_color color #26C6AD Up impulse color
# @param down_color color #EA7884 Down impulse color
# @param half_color color #D8AE63 Half-return color

impulse_span = min(12, max(1, int(params.get("impulse_span", 3))))
minimum_move = min(8.0, max(0.5, float(params.get("minimum_move", 2.0))))
minimum_efficiency = min(1.0, max(0.3, float(params.get("minimum_efficiency", 0.75))))
atr_length = min(100, max(5, int(params.get("atr_length", 20))))
maximum_life = min(250, max(5, int(params.get("maximum_life", 60))))
maps_to_keep = min(20, max(1, int(params.get("maps_to_keep", 6))))
show_outcomes = bool(params.get("show_outcomes", True))
show_panel = bool(params.get("show_panel", True))
panel_position_raw = str(params.get("panel_position", "Bottom_left"))
panel_position_aliases = {
    "Bottom_left": "Bottom left", "Top_right": "Top right", "Bottom_right": "Bottom right",
    "Bottom left": "Bottom left", "Top right": "Top right", "Bottom right": "Bottom right",
}
panel_position = panel_position_aliases.get(panel_position_raw, "Bottom left")
show_rails = bool(params.get("show_rails", True))
up_color = str(params.get("up_color", "#26C6AD"))
down_color = str(params.get("down_color", "#EA7884"))
half_color = str(params.get("half_color", "#D8AE63"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 3600.0
times = df["time"].to_numpy(dtype=float)
n = len(df)
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0
positive_intervals = np.diff(times)
positive_intervals = positive_intervals[np.isfinite(positive_intervals) & (positive_intervals > 0.0)]
bar_interval = float(np.median(positive_intervals)) if len(positive_intervals) else 3600000.0


def _color_with_alpha(color, alpha):
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
        return f"rgba({red},{green},{blue},{max(0.0, min(1.0, float(alpha))):.3f})"
    return value


true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )

atr = np.full(n, np.nan, dtype=float)
if n >= atr_length:
    atr[atr_length - 1] = float(np.mean(true_range[:atr_length]))
    for index in range(atr_length, n):
        atr[index] = (atr[index - 1] * (atr_length - 1) + true_range[index]) / atr_length

base_atr = np.full(n, np.nan, dtype=float)
delta = np.full(n, np.nan, dtype=float)
path = np.full(n, np.nan, dtype=float)
efficiency = np.zeros(n, dtype=float)
location = np.zeros(n, dtype=float)
candidate = np.zeros(n, dtype=int)

for index in range(impulse_span, n):
    base_atr[index] = atr[index - impulse_span]
    delta[index] = close[index] - close[index - impulse_span]
    changes = np.abs(np.diff(close[index - impulse_span:index + 1]))
    path[index] = float(np.sum(changes))
    window_high = float(np.max(high[index - impulse_span + 1:index + 1]))
    window_low = float(np.min(low[index - impulse_span + 1:index + 1]))
    window_range = window_high - window_low
    ready = np.isfinite(base_atr[index]) and base_atr[index] > 0.0 and window_range > 0.0
    if not ready:
        continue
    efficiency[index] = abs(delta[index]) / path[index] if path[index] > 0.0 else 0.0
    direction = 1 if delta[index] > 0.0 else -1 if delta[index] < 0.0 else 0
    location[index] = (
        (close[index] - window_low) / window_range
        if direction > 0 else (window_high - close[index]) / window_range
    )
    qualified = (
        direction != 0
        and abs(delta[index]) >= minimum_move * base_atr[index]
        and efficiency[index] >= minimum_efficiency
        and location[index] >= 0.7
    )
    candidate[index] = direction if qualified else 0

maps = []
event_records = []
last_born = None
prior_candidate = 0

new_up = np.zeros(n, dtype=bool)
new_down = np.zeros(n, dtype=bool)
half_event = np.zeros(n, dtype=bool)
unwind_event = np.zeros(n, dtype=bool)
recover_event = np.zeros(n, dtype=bool)
expire_event = np.zeros(n, dtype=bool)
replace_event = np.zeros(n, dtype=bool)
latest_origin = np.full(n, np.nan, dtype=float)
latest_tip = np.full(n, np.nan, dtype=float)
latest_retained = np.full(n, np.nan, dtype=float)
latest_worst = np.full(n, np.nan, dtype=float)
latest_age = np.full(n, -1, dtype=int)
latest_status = [None] * n

for index in range(n):
    if maps and maps[-1]["active"]:
        current = maps[-1]
        current["age"] = index - current["born"]
        current["end"] = index
        denominator = current["tip"] - current["origin"]
        if denominator != 0.0 and index >= impulse_span:
            current["retained"] = (close[index] - current["origin"]) / denominator
            current["worst"] = min(current["worst"], current["retained"])
            if current["retained"] <= 0.0:
                current["status"] = "UNWOUND"
                current["active"] = False
                unwind_event[index] = True
                event_records.append({"index": index, "text": "UNWOUND", "price": float(close[index]), "side": "above" if current["dir"] > 0 else "below", "color": down_color})
            elif current["half_seen"] and current["retained"] >= 1.0:
                current["status"] = "RECOVERED"
                current["active"] = False
                recover_event[index] = True
                event_records.append({"index": index, "text": "RECOVERED", "price": float(close[index]), "side": "above" if current["dir"] < 0 else "below", "color": up_color})
            elif not current["half_seen"] and current["retained"] <= 0.5:
                current["half_seen"] = True
                half_event[index] = True
                event_records.append({"index": index, "text": "HALF BACK", "price": float(close[index]), "side": "above" if current["dir"] > 0 else "below", "color": half_color})
            if current["active"]:
                if current["retained"] <= 0.5:
                    current["status"] = "DEEP RETRACE"
                elif current["half_seen"]:
                    current["status"] = "REBUILDING"
                elif current["retained"] >= 1.0:
                    current["status"] = "EXTENDED"
                else:
                    current["status"] = "HOLDING"
        if current["active"] and current["age"] >= maximum_life:
            current["status"] = "EXPIRED"
            current["active"] = False
            expire_event[index] = True

    fresh = candidate[index] != 0 and candidate[index] != prior_candidate
    spaced = last_born is None or index - last_born >= impulse_span
    if fresh and spaced:
        if maps and maps[-1]["active"]:
            maps[-1]["active"] = False
            maps[-1]["status"] = "REPLACED"
            maps[-1]["end"] = index
            replace_event[index] = True
        origin = float(close[index - impulse_span])
        tip = float(close[index])
        direction = int(candidate[index])
        maps.append({
            "born": index, "end": index, "dir": direction,
            "origin": origin, "tip": tip,
            "atr_size": float(abs(delta[index]) / base_atr[index]),
            "retained": 1.0, "worst": 1.0, "age": 0,
            "half_seen": False, "active": True, "status": "NEW IMPULSE",
        })
        if len(maps) > maps_to_keep:
            maps.pop(0)
            if maps:
                oldest = maps[0]["born"]
                event_records = [record for record in event_records if record["index"] >= oldest]
        last_born = index
        new_up[index] = direction > 0
        new_down[index] = direction < 0
    prior_candidate = int(candidate[index])

    if maps:
        latest = maps[-1]
        latest_origin[index] = latest["origin"]
        latest_tip[index] = latest["tip"]
        latest_retained[index] = latest["retained"] * 100.0
        latest_worst[index] = latest["worst"] * 100.0
        latest_age[index] = latest["age"]
        latest_status[index] = latest["status"]

event_records = event_records[-60:]
layers = []
last_index = max(0, n - 1)
for map_index, record in enumerate(maps):
    direction_color = up_color if record["dir"] > 0 else down_color
    middle = (record["origin"] + record["tip"]) * 0.5
    end_index = last_index if record["active"] else record["end"]
    active = record["active"]
    layers.extend([
        {
            "type": "zone", "role": f"Impulse Strong {map_index + 1}",
            "startIndex": record["born"], "endIndex": end_index,
            "top": max(record["tip"], middle), "bottom": min(record["tip"], middle),
            "fillColor": _color_with_alpha(direction_color, 0.14 if active else 0.06),
            "borderColor": _color_with_alpha(direction_color, 0.65 if active else 0.25),
            "borderSize": 1,
        },
        {
            "type": "zone", "role": f"Impulse Weak {map_index + 1}",
            "startIndex": record["born"], "endIndex": end_index,
            "top": max(record["origin"], middle), "bottom": min(record["origin"], middle),
            "fillColor": _color_with_alpha(half_color, 0.06 if active else 0.04),
            "borderColor": _color_with_alpha(half_color, 0.45 if active else 0.18),
            "borderSize": 1,
        },
        {
            "type": "line", "role": f"Impulse Midpoint {map_index + 1}",
            "startIndex": record["born"], "endIndex": end_index,
            "y1": middle, "y2": middle,
            "color": _color_with_alpha(half_color, 0.80 if active else 0.28),
            "lineWidth": 1, "dashed": True,
        },
        {
            "type": "label", "role": f"Impulse Tag {map_index + 1}",
            "index": record["born"], "price": record["tip"],
            "text": ("UP " if record["dir"] > 0 else "DOWN ") + f"{record['atr_size']:.1f} ATR",
            "anchor": "above" if record["dir"] > 0 else "below",
            "color": _color_with_alpha(direction_color, 0.88),
            "borderColor": direction_color, "textColor": "#101722", "fontSize": 10,
        },
    ])

if show_outcomes:
    for record in event_records:
        layers.append({
            "type": "label", "role": record["text"],
            "index": record["index"], "price": record["price"], "text": record["text"],
            "anchor": record["side"], "color": "rgba(16,23,34,0.92)",
            "borderColor": record["color"], "textColor": record["color"], "fontSize": 10,
        })

if maps and maps[-1]["active"] and show_rails and n:
    latest = maps[-1]
    future_time = float(times[-1] + bar_interval * 3.0)
    rail_specs = [
        (latest["tip"], "100%  Impulse close", up_color if latest["dir"] > 0 else down_color),
        ((latest["tip"] + latest["origin"]) * 0.5, "50%  Half back", half_color),
        (latest["origin"], "0%  Origin", "#8794A8"),
    ]
    for price, text, color in rail_specs:
        layers.append({
            "type": "label", "role": "Impulse Rail", "timestamp": future_time,
            "price": float(price), "text": text, "anchor": "right",
            "color": "rgba(16,23,34,0.90)", "borderColor": color,
            "textColor": color, "fontSize": 10,
        })

tables = []
if show_panel:
    position_map = {"Bottom left": "bottom-left", "Top right": "top-right", "Bottom right": "bottom-right"}
    if maps:
        latest = maps[-1]
        tables.append({
            "title": "Impulse Retention",
            "position": position_map[panel_position],
            "rows": [
                ["Direction", "UP" if latest["dir"] > 0 else "DOWN"],
                ["Status", latest["status"]],
                ["Move retained", f"{latest['retained'] * 100.0:.1f}%"],
                ["Worst close", f"{latest['worst'] * 100.0:.1f}%"],
                [f"Original {impulse_span}-bar move", f"{abs(latest['tip'] - latest['origin']):.4f} / {latest['atr_size']:.1f} ATR"],
                ["Bars tracked", f"{latest['age']} / {maximum_life}"],
            ],
            "compact": True,
        })
    else:
        warmup = atr_length + impulse_span
        tables.append({
            "title": "Impulse Retention",
            "position": position_map[panel_position],
            "rows": [["Status", "WARMING UP" if n < warmup else "WAITING FOR IMPULSE"], ["Threshold", f">= {minimum_move:.1f} ATR"]],
            "compact": True,
        })


def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


signals = [
    {"type": "buy", "name": "New Up Impulse", "text": "UP", "data": [float(low[i]) if new_up[i] else None for i in range(n)], "color": up_color, "renderMode": "hidden"},
    {"type": "sell", "name": "New Down Impulse", "text": "DOWN", "data": [float(high[i]) if new_down[i] else None for i in range(n)], "color": down_color, "renderMode": "hidden"},
    {"type": "info", "name": "Half of Impulse Returned", "text": "HALF", "data": [float(close[i]) if half_event[i] else None for i in range(n)], "color": half_color, "renderMode": "hidden"},
    {"type": "sell", "name": "Impulse Fully Unwound", "text": "UNWOUND", "data": [float(close[i]) if unwind_event[i] else None for i in range(n)], "color": down_color, "renderMode": "hidden"},
    {"type": "buy", "name": "Impulse Recovered", "text": "RECOVERED", "data": [float(close[i]) if recover_event[i] else None for i in range(n)], "color": up_color, "renderMode": "hidden"},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [{"name": "Price Guide", "title": False, "data": _finite_or_none(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True}],
    "signals": signals,
    "layers": layers,
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "trueRange": _finite_or_none(true_range), "atr": _finite_or_none(atr),
        "baseAtr": _finite_or_none(base_atr), "delta": _finite_or_none(delta),
        "path": _finite_or_none(path), "efficiency": _finite_or_none(efficiency),
        "location": _finite_or_none(location), "candidate": candidate.tolist(),
        "newUp": new_up.tolist(), "newDown": new_down.tolist(),
        "halfEvent": half_event.tolist(), "unwindEvent": unwind_event.tolist(),
        "recoverEvent": recover_event.tolist(), "expireEvent": expire_event.tolist(),
        "replaceEvent": replace_event.tolist(),
        "latestOrigin": _finite_or_none(latest_origin), "latestTip": _finite_or_none(latest_tip),
        "latestRetained": _finite_or_none(latest_retained), "latestWorst": _finite_or_none(latest_worst),
        "latestAge": latest_age.tolist(), "latestStatus": latest_status,
        "maps": maps, "events": event_records, "barInterval": bar_interval,
    },
}
