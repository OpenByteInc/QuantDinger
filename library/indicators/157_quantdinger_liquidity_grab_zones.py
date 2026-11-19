# ============================================================
# QuantDinger Liquidity Grab Zones
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; alerts do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Liquidity Grab Zones"
my_indicator_description = (
    "Finds wick-dominant grabs of separated pivot liquidity, builds and combines grab zones, "
    "and tracks invalidation, inverse zones, retests, and optional break labels."
)

# @param max_distance str Normal Max distance to last bar values=Normal,Low
# @param pivot_length int 25 Pivot length range=2:100:1
# @param wick_body_ratio float 0.5 Wick-body ratio range=0.1:5.0:0.1
# @param invalidation str Close Zone invalidation values=Close,Wick
# @param show_retests bool true Show retests
# @param show_breaks bool false Show breaks
# @param inverse_zones bool true Create inverse zones
# @param show_historic bool true Show historic zones
# @param bullish_color str rgba(8,153,129,0.50) Bullish color
# @param bearish_color str rgba(242,54,70,0.50) Bearish color

max_distance = str(params.get("max_distance", "Normal"))
pivot_length = min(100, max(2, int(params.get("pivot_length", 25))))
wick_body_ratio = min(5.0, max(0.1, float(params.get("wick_body_ratio", 0.5))))
invalidation = str(params.get("invalidation", "Close"))
show_retests = bool(params.get("show_retests", True))
show_breaks = bool(params.get("show_breaks", False))
inverse_zones = bool(params.get("inverse_zones", True))
show_historic = bool(params.get("show_historic", True))
bullish_color = str(params.get("bullish_color", "rgba(8,153,129,0.50)"))
bearish_color = str(params.get("bearish_color", "rgba(242,54,70,0.50)"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

distance_limit = 150 if max_distance == "Low" else 500
first_processed = max(0, n - distance_limit)

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))

atr = np.full(n, np.nan, dtype=float)
if n >= 5:
    atr[4] = float(np.mean(true_range[:5]))
    for index in range(5, n):
        atr[index] = (atr[index - 1] * 4.0 + true_range[index]) / 5.0

def is_pivot(values, confirmation_index, period, high_pivot):
    center = confirmation_index - period
    if center < period:
        return False
    window = values[center - period:confirmation_index + 1]
    if len(window) != period * 2 + 1:
        return False
    candidate = values[center]
    others = np.concatenate((window[:period], window[period + 1:]))
    if high_pivot:
        return bool(candidate >= np.max(others) and np.any(candidate > others))
    return bool(candidate <= np.min(others) and np.any(candidate < others))

liquidities = []
zones = []
retest_bull = [None for _ in range(n)]
retest_bear = [None for _ in range(n)]
break_bull = [None for _ in range(n)]
break_bear = [None for _ in range(n)]
grab_high_5 = np.zeros(n, dtype=bool)
grab_high_10 = np.zeros(n, dtype=bool)
grab_high_15 = np.zeros(n, dtype=bool)
grab_low_5 = np.zeros(n, dtype=bool)
grab_low_10 = np.zeros(n, dtype=bool)
grab_low_15 = np.zeros(n, dtype=bool)
high_circle_price = np.full(n, np.nan, dtype=float)
low_circle_price = np.full(n, np.nan, dtype=float)
last_high_grab = 0
last_low_grab = 0
last_retest = 0

def zones_touch(first, second, current_index):
    first_end = current_index if first["end"] is None else first["end"]
    second_end = current_index if second["end"] is None else second["end"]
    time_overlap = max(0.0, min(first_end, second_end) - max(first["start"], second["start"]))
    price_overlap = max(0.0, min(first["top"], second["top"]) - max(first["bottom"], second["bottom"]))
    intersection = time_overlap * price_overlap
    first_area_end = first["start"] + 15 if first["end"] is None else first["end"]
    second_area_end = second["start"] + 15 if second["end"] is None else second["end"]
    first_area = abs(first_area_end - first["start"]) * abs(first["top"] - first["bottom"])
    second_area = abs(second_area_end - second["start"]) * abs(second["top"] - second["bottom"])
    union = first_area + second_area - intersection
    overlap_percentage = intersection / union * 100.0 if union != 0.0 else (np.inf if intersection > 0.0 else 0.0)
    return bool(overlap_percentage > 0.0)

def combine_zones(current_index):
    while True:
        pair = None
        for first_index in range(len(zones)):
            first = zones[first_index]
            if first["disabled"]:
                continue
            for second_index in range(first_index + 1, len(zones)):
                second = zones[second_index]
                if second["disabled"] or first["kind"] != second["kind"]:
                    continue
                if zones_touch(first, second, current_index):
                    pair = (first, second)
                    break
            if pair is not None:
                break
        if pair is None:
            return
        first, second = pair
        first["disabled"] = True
        second["disabled"] = True
        merged_end_value = max(0 if first["end"] is None else first["end"], 0 if second["end"] is None else second["end"])
        merged_end = None if merged_end_value == 0 else merged_end_value
        zones.insert(
            0,
            {
                "start": min(first["start"], second["start"]),
                "end": merged_end,
                "top": max(first["top"], second["top"]),
                "bottom": min(first["bottom"], second["bottom"]),
                "kind": first["kind"],
                "disabled": False,
                "ephemeral": False,
                "retest_ready": False,
            },
        )

for index in range(n):
    if index < first_processed:
        continue

    for liquidity in liquidities:
        if liquidity["end"] is None:
            crossed = close[index] > liquidity["price"] if liquidity["kind"] == "Buyside" else close[index] < liquidity["price"]
            if crossed:
                liquidity["end"] = index

    if is_pivot(high, index, pivot_length, True):
        origin = index - pivot_length
        price = float(high[origin])
        separated = True
        if np.isfinite(atr[index]):
            separated = all(
                item["end"] is not None or item["kind"] != "Buyside" or abs(price - item["price"]) >= atr[index]
                for item in liquidities
            )
        if separated:
            liquidities.insert(0, {"origin": origin, "end": None, "price": price, "kind": "Buyside", "grabs": 0})
            liquidities = liquidities[:500]

    if is_pivot(low, index, pivot_length, False):
        origin = index - pivot_length
        price = float(low[origin])
        separated = True
        if np.isfinite(atr[index]):
            separated = all(
                item["end"] is not None or item["kind"] != "Sellside" or abs(price - item["price"]) >= atr[index]
                for item in liquidities
            )
        if separated:
            liquidities.insert(0, {"origin": origin, "end": None, "price": price, "kind": "Sellside", "grabs": 0})
            liquidities = liquidities[:500]

    high_grab = False
    low_grab = False
    body_size = abs(close[index] - open_price[index])
    for liquidity in liquidities:
        if liquidity["end"] is not None or liquidity["grabs"] >= 1:
            continue
        if liquidity["kind"] == "Buyside" and high[index] >= liquidity["price"] and index - last_high_grab > 0:
            wick_size = high[index] - max(close[index], open_price[index])
            ratio = wick_size / body_size if body_size > 0.0 else np.inf
            last_high_grab = index
            liquidity["grabs"] += 1
            if ratio >= wick_body_ratio * 3.0:
                grab_high_15[index] = True
                high_grab = True
            elif ratio >= wick_body_ratio * 2.0:
                grab_high_10[index] = True
                high_grab = True
            elif ratio >= wick_body_ratio:
                grab_high_5[index] = True
                high_grab = True
            if np.isfinite(atr[index]):
                high_circle_price[index] = high[index] + atr[index] * 0.20
        elif liquidity["kind"] == "Sellside" and low[index] <= liquidity["price"] and index - last_low_grab > 0:
            wick_size = min(close[index], open_price[index]) - low[index]
            ratio = wick_size / body_size if body_size > 0.0 else np.inf
            last_low_grab = index
            liquidity["grabs"] += 1
            if ratio >= wick_body_ratio * 3.0:
                grab_low_15[index] = True
                low_grab = True
            elif ratio >= wick_body_ratio * 2.0:
                grab_low_10[index] = True
                low_grab = True
            elif ratio >= wick_body_ratio:
                grab_low_5[index] = True
                low_grab = True
            if np.isfinite(atr[index]):
                low_circle_price[index] = low[index] - atr[index] * 0.20

    if high_grab:
        zones.insert(
            0,
            {
                "start": index,
                "end": None,
                "top": float(high[index]),
                "bottom": float(max(close[index], open_price[index])),
                "kind": "Buyside",
                "disabled": False,
                "ephemeral": False,
                "retest_ready": False,
            },
        )
        zones = zones[:100]
    elif low_grab:
        zones.insert(
            0,
            {
                "start": index,
                "end": None,
                "top": float(min(close[index], open_price[index])),
                "bottom": float(low[index]),
                "kind": "Sellside",
                "disabled": False,
                "ephemeral": False,
                "retest_ready": False,
            },
        )
        zones = zones[:100]

    inverse_additions = []
    for zone in list(zones):
        if zone["disabled"] or zone["end"] is not None or index <= zone["start"]:
            continue
        if zone["kind"] == "Buyside":
            value = close[index] if invalidation == "Close" else high[index]
            invalidated = value > zone["top"]
            if invalidated:
                zone["end"] = index
                if index - zone["start"] > 3:
                    break_bull[index] = float(zone["bottom"])
                    if inverse_zones and not zone["ephemeral"]:
                        inverse_additions.append(
                            {"start": index, "end": None, "top": zone["top"], "bottom": zone["bottom"], "kind": "Sellside", "disabled": False, "ephemeral": True, "retest_ready": False}
                        )
        else:
            value = close[index] if invalidation == "Close" else low[index]
            invalidated = value < zone["bottom"]
            if invalidated:
                zone["end"] = index
                if index - zone["start"] > 3:
                    break_bear[index] = float(zone["top"])
                    if inverse_zones and not zone["ephemeral"]:
                        inverse_additions.append(
                            {"start": index, "end": None, "top": zone["top"], "bottom": zone["bottom"], "kind": "Buyside", "disabled": False, "ephemeral": True, "retest_ready": False}
                        )
    for item in inverse_additions:
        zones.insert(0, item)

    if index - last_retest > 5:
        for zone in zones:
            if zone["disabled"] or zone["end"] is not None or index - zone["start"] < 3:
                continue
            middle = (zone["top"] + zone["bottom"]) * 0.5
            if zone["kind"] == "Buyside" and high[index] > middle:
                retest_bull[index] = float(zone["top"])
                last_retest = index
            elif zone["kind"] == "Sellside" and low[index] < middle:
                retest_bear[index] = float(zone["bottom"])
                last_retest = index

    combine_zones(index)

layers = []
for zone in zones:
    if zone["disabled"]:
        continue
    if zone["end"] is not None and (not show_historic or zone["end"] - zone["start"] <= 3):
        continue
    end_index = max(0, n - 1) if zone["end"] is None else zone["end"]
    color = bearish_color if zone["kind"] == "Buyside" else bullish_color
    layers.append(
        {
            "type": "zone",
            "startIndex": int(zone["start"]),
            "endIndex": int(end_index),
            "top": float(zone["top"]),
            "bottom": float(zone["bottom"]),
            "fillColor": color,
            "borderColor": "rgba(0,0,0,0)",
            "borderSize": 0,
            "extendRight": zone["end"] is None,
        }
    )
    middle = (zone["top"] + zone["bottom"]) / 2.0
    layers.append(
        {
            "type": "line",
            "startIndex": int(zone["start"]),
            "endIndex": int(end_index),
            "y1": float(middle),
            "y2": float(middle),
            "color": "#FFFFFF",
            "lineWidth": 1,
            "dashed": True,
            "extendRight": zone["end"] is None,
        }
    )

price_guide = [float(value) if np.isfinite(value) else None for value in close]
plots = [
    {"name": "Price Reference", "title": False, "data": price_guide, "color": "rgba(255,255,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True}
]
signals = [
    {"type": "sell", "text": "●", "name": "Buyside Grab", "data": [float(high_circle_price[i]) if grab_high_5[i] or grab_high_10[i] or grab_high_15[i] else None for i in range(n)], "color": bearish_color, "renderMode": "hidden"},
    {"type": "buy", "text": "●", "name": "Sellside Grab", "data": [float(low_circle_price[i]) if grab_low_5[i] or grab_low_10[i] or grab_low_15[i] else None for i in range(n)], "color": bullish_color, "renderMode": "hidden"},
    {"type": "sell", "text": "R", "name": "Buyside Retest", "data": retest_bull, "color": bearish_color, "renderMode": "events" if show_retests else "hidden"},
    {"type": "buy", "text": "R", "name": "Sellside Retest", "data": retest_bear, "color": bullish_color, "renderMode": "events" if show_retests else "hidden"},
    {"type": "buy", "text": "B", "name": "Buyside Break", "data": break_bull, "color": "#2962FF", "renderMode": "events" if show_breaks else "hidden"},
    {"type": "sell", "text": "B", "name": "Sellside Break", "data": break_bear, "color": "#2962FF", "renderMode": "events" if show_breaks else "hidden"},
]


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "trueRange": finite(true_range), "atr": finite(atr), "firstProcessed": int(first_processed),
        "grabHigh5": grab_high_5.tolist(), "grabHigh10": grab_high_10.tolist(), "grabHigh15": grab_high_15.tolist(),
        "grabLow5": grab_low_5.tolist(), "grabLow10": grab_low_10.tolist(), "grabLow15": grab_low_15.tolist(),
        "highCirclePrice": finite(high_circle_price), "lowCirclePrice": finite(low_circle_price),
        "retestBull": retest_bull, "retestBear": retest_bear, "breakBull": break_bull, "breakBear": break_bear,
        "liquidities": liquidities, "zones": zones,
    },
}
