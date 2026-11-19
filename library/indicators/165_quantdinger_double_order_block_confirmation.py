# ============================================================
# QuantDinger Double Order Block Confirmation
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Double Order Block Confirmation"
my_indicator_description = (
    "Builds an alternating swing structure, detects the seven-swing double order-block "
    "confirmation pattern, and projects paired major and local supply/demand zones."
)

# @param pivot_period int 2 Pivot confirmation period range=2:50:1
# @param show_swing_labels bool false Show confirmed swing labels
# @param show_setup_labels bool true Show setup labels
# @param supply_major_color str rgba(197,200,202,0.32) Major supply zone color
# @param supply_local_color str rgba(252,231,48,0.32) Local supply zone color
# @param demand_major_color str rgba(197,200,202,0.32) Major demand zone color
# @param demand_local_color str rgba(48,252,242,0.32) Local demand zone color
# @param bearish_color str #B30202 Bearish setup color
# @param bullish_color str #2F9C26 Bullish setup color
# @param max_setups int 100 Maximum retained setups range=1:250:1

pivot_period = min(50, max(2, int(params.get("pivot_period", 2))))
show_swing_labels = bool(params.get("show_swing_labels", False))
show_setup_labels = bool(params.get("show_setup_labels", True))
supply_major_color = str(params.get("supply_major_color", "rgba(197,200,202,0.32)"))
supply_local_color = str(params.get("supply_local_color", "rgba(252,231,48,0.32)"))
demand_major_color = str(params.get("demand_major_color", "rgba(197,200,202,0.32)"))
demand_local_color = str(params.get("demand_local_color", "rgba(48,252,242,0.32)"))
bearish_color = str(params.get("bearish_color", "#B30202"))
bullish_color = str(params.get("bullish_color", "#2F9C26"))
max_setups = min(250, max(1, int(params.get("max_setups", 100))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

def confirmed_pivot(values, confirmation_index, period, high_pivot):
    center = confirmation_index - period
    if center < period or confirmation_index >= len(values):
        return None
    window = values[center - period:confirmation_index + 1]
    if len(window) != period * 2 + 1 or not np.all(np.isfinite(window)):
        return None
    candidate = float(values[center])
    if high_pivot:
        return center if candidate >= float(np.max(window)) else None
    return center if candidate <= float(np.min(window)) else None

def classify_swing(swings, kind, price):
    previous = None
    for swing in reversed(swings):
        if swing["kind"] == kind:
            previous = swing
            break
    if previous is None:
        return "H" if kind == "high" else "L"
    if kind == "high":
        return "HH" if price > previous["price"] else "LH"
    return "HL" if price > previous["price"] else "LL"

def add_swing(swings, kind, index, price):
    if not swings:
        swings.append({"kind": kind, "index": index, "price": price, "type": "H" if kind == "high" else "L"})
        return True
    last = swings[-1]
    if last["kind"] == kind:
        more_extreme = price > last["price"] if kind == "high" else price < last["price"]
        if not more_extreme:
            return False
        swings.pop()
        swing_type = classify_swing(swings, kind, price)
        swings.append({"kind": kind, "index": index, "price": price, "type": swing_type})
        return True
    swing_type = classify_swing(swings, kind, price)
    swings.append({"kind": kind, "index": index, "price": price, "type": swing_type})
    return True

swings = []
setups = []
bear_data = [None for _ in range(n)]
bull_data = [None for _ in range(n)]
last_pattern_key = None

for confirmation_index in range(n):
    high_index = confirmed_pivot(high, confirmation_index, pivot_period, True)
    low_index = confirmed_pivot(low, confirmation_index, pivot_period, False)
    candidates = []
    if high_index is not None:
        candidates.append(("high", high_index, float(high[high_index])))
    if low_index is not None:
        candidates.append(("low", low_index, float(low[low_index])))
    if len(candidates) == 2 and swings:
        candidates.sort(key=lambda item: item[0] == swings[-1]["kind"])
    changed = False
    for kind, pivot_index, price in candidates:
        if add_swing(swings, kind, pivot_index, price):
            changed = True
    if len(swings) > 300:
        swings = swings[-300:]
    if not changed or len(swings) < 7:
        continue

    points = list(reversed(swings[-7:]))
    types = [point["type"] for point in points]
    pattern_key = tuple((point["index"], point["type"]) for point in points)
    if pattern_key == last_pattern_key or points[0]["index"] != confirmation_index - pivot_period:
        continue

    bearish_pattern = (
        types == ["LL", "HH", "HL", "LH", "LL", "LH", "LL"]
        and points[0]["price"] > points[4]["price"]
        and points[5]["price"] > points[1]["price"]
    )
    bullish_pattern = (
        types == ["HH", "LL", "LH", "HL", "HH", "HL", "HH"]
        and points[0]["price"] < points[4]["price"]
        and points[5]["price"] < points[1]["price"]
    )
    if not bearish_pattern and not bullish_pattern:
        continue

    point_one = points[1]
    point_five = points[5]
    if bearish_pattern:
        overlap = low[point_five["index"]] <= high[point_one["index"]]
        if not overlap:
            continue
        signal_index = points[0]["index"]
        bear_data[signal_index] = {"value": float(low[signal_index]), "text": "Bear Setup", "color": bearish_color}
        setups.append(
            {
                "side": "supply",
                "signal": signal_index,
                "major_start": point_five["index"],
                "major_end": confirmation_index + (confirmation_index - point_five["index"]),
                "major_top": float(high[point_five["index"]]),
                "major_bottom": float(low[point_five["index"]]),
                "local_start": point_one["index"],
                "local_end": confirmation_index + 20,
                "local_top": float(high[point_one["index"]]),
                "local_bottom": float(low[point_one["index"]]),
            }
        )
    else:
        overlap = low[point_one["index"]] <= high[point_five["index"]]
        if not overlap:
            continue
        signal_index = points[0]["index"]
        bull_data[signal_index] = {"value": float(high[signal_index]), "text": "Bull Setup", "color": bullish_color}
        setups.append(
            {
                "side": "demand",
                "signal": signal_index,
                "major_start": point_five["index"],
                "major_end": confirmation_index + (confirmation_index - point_five["index"]),
                "major_top": float(high[point_five["index"]]),
                "major_bottom": float(low[point_five["index"]]),
                "local_start": point_one["index"],
                "local_end": confirmation_index + 20,
                "local_top": float(high[point_one["index"]]),
                "local_bottom": float(low[point_one["index"]]),
            }
        )
    setups = setups[-max_setups:]
    last_pattern_key = pattern_key

layers = []
for setup in setups:
    supply = setup["side"] == "supply"
    major_color = supply_major_color if supply else demand_major_color
    local_color = supply_local_color if supply else demand_local_color
    major_text = "Supply OB" if supply else "Demand OB"
    local_text = "Supply OB LTF" if supply else "Demand OB LTF"
    layers.append(
        {
            "type": "zone",
            "startIndex": int(setup["major_start"]),
            "endIndex": int(min(n - 1, setup["major_end"])),
            "top": float(setup["major_top"]),
            "bottom": float(setup["major_bottom"]),
            "fillColor": major_color,
            "borderColor": major_color,
            "borderSize": 1,
            "extendRight": False,
        }
    )
    layers.append(
        {
            "type": "zone",
            "startIndex": int(setup["local_start"]),
            "endIndex": int(min(n - 1, setup["local_end"])),
            "top": float(setup["local_top"]),
            "bottom": float(setup["local_bottom"]),
            "fillColor": local_color,
            "borderColor": local_color,
            "borderSize": 1,
            "extendRight": False,
        }
    )
    if show_setup_labels:
        label_color = bearish_color if supply else bullish_color
        layers.append({"type": "label", "index": int(setup["major_start"]), "price": float((setup["major_top"] + setup["major_bottom"]) / 2.0), "text": major_text, "color": "rgba(0,0,0,0)", "textColor": label_color, "side": "center"})
        layers.append({"type": "label", "index": int(setup["local_start"]), "price": float((setup["local_top"] + setup["local_bottom"]) / 2.0), "text": local_text, "color": "rgba(0,0,0,0)", "textColor": label_color, "side": "center"})

if show_swing_labels:
    for swing in swings[-250:]:
        layers.append(
            {
                "type": "label",
                "index": int(swing["index"]),
                "price": float(swing["price"]),
                "text": str(swing["type"]),
                "color": "rgba(0,0,0,0)",
                "textColor": bearish_color if swing["kind"] == "high" else bullish_color,
                "side": "down" if swing["kind"] == "high" else "up",
            }
        )

anchor = [float(value) for value in close]
plots = [
    {"name": "Structure Anchor", "title": False, "data": anchor, "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True}
]
signals = [
    {"type": "sell", "name": "Bear Setup04", "text": "Bear Setup", "data": bear_data, "color": bearish_color, "renderMode": "events"},
    {"type": "buy", "name": "Bull Setup04", "text": "Bull Setup", "data": bull_data, "color": bullish_color, "renderMode": "events"},
]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers[-500:]}
