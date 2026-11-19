# ============================================================
# QuantDinger Liquidity Grabs
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Liquidity Grabs"
my_indicator_description = (
    "Tracks recent confirmed swing liquidity, removes invalidated levels, and "
    "marks wick rejections by side and wick-to-body strength."
)

# @param pivot_length int 25 Pivot length range=1:500:1
# @param wick_body_ratio float 0.5 Wick-to-body ratio unit range=0.0:10.0:0.1
# @param buyside_color color rgba(242,54,70,0.50) Buyside color
# @param sellside_color color rgba(8,153,129,0.50) Sellside color

pivot_length = min(500, max(1, int(params.get("pivot_length", 25))))
wick_body_ratio = min(10.0, max(0.0, float(params.get("wick_body_ratio", 0.5))))
buyside_color = str(params.get("buyside_color", "rgba(242,54,70,0.50)"))
sellside_color = str(params.get("sellside_color", "rgba(8,153,129,0.50)"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)
first_processed = max(0, n - 5000)

pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
for confirmation in range(pivot_length * 2, n):
    center = confirmation - pivot_length
    start = confirmation - pivot_length * 2
    if high[center] == np.max(high[start : confirmation + 1]):
        pivot_high[confirmation] = float(high[center])
    if low[center] == np.min(low[start : confirmation + 1]):
        pivot_low[confirmation] = float(low[center])

buyside_levels = []
sellside_levels = []
buyside_small = np.zeros(n, dtype=bool)
buyside_medium = np.zeros(n, dtype=bool)
buyside_large = np.zeros(n, dtype=bool)
sellside_small = np.zeros(n, dtype=bool)
sellside_medium = np.zeros(n, dtype=bool)
sellside_large = np.zeros(n, dtype=bool)
raw_buyside_small = np.zeros(n, dtype=bool)
raw_buyside_medium = np.zeros(n, dtype=bool)
raw_buyside_large = np.zeros(n, dtype=bool)
raw_sellside_small = np.zeros(n, dtype=bool)
raw_sellside_medium = np.zeros(n, dtype=bool)
raw_sellside_large = np.zeros(n, dtype=bool)
cooldown_ok_series = np.zeros(n, dtype=bool)
grab_found_series = np.zeros(n, dtype=bool)
grab_buyside_series = np.ones(n, dtype=bool)
grab_size_series = np.ones(n, dtype=int)

last_buyside_grab = 0
last_sellside_grab = 0

for index in range(first_processed, n):
    if np.isfinite(pivot_high[index]):
        buyside_levels.append(float(pivot_high[index]))
        if len(buyside_levels) > 5:
            buyside_levels.pop(0)
    if np.isfinite(pivot_low[index]):
        sellside_levels.append(float(pivot_low[index]))
        if len(sellside_levels) > 5:
            sellside_levels.pop(0)

    grab_found = False
    grab_buyside = True
    grab_size = 1
    levels_to_remove = []
    body_top = max(close[index], open_price[index])
    body_bottom = min(close[index], open_price[index])

    for level in buyside_levels:
        if high[index] > level and body_top < level:
            body_size = abs(close[index] - open_price[index])
            wick_size = high[index] - body_top
            current_ratio = wick_size / body_size if body_size > 0.0 else np.inf
            grab_found = True
            grab_buyside = True
            scaled_ratio = current_ratio / wick_body_ratio if wick_body_ratio != 0.0 else np.inf
            grab_size = int(np.floor(min(scaled_ratio, 3.0)))
            levels_to_remove.append(level)
            break
        if body_top > level:
            levels_to_remove.append(level)
            break

    if not grab_found:
        for level in sellside_levels:
            if low[index] < level and body_bottom > level:
                body_size = abs(close[index] - open_price[index])
                wick_size = body_bottom - low[index]
                current_ratio = wick_size / body_size if body_size > 0.0 else np.inf
                grab_found = True
                grab_buyside = False
                scaled_ratio = current_ratio / wick_body_ratio if wick_body_ratio != 0.0 else np.inf
                grab_size = int(np.floor(min(scaled_ratio, 3.0)))
                levels_to_remove.append(level)
                break
            if body_bottom < level:
                levels_to_remove.append(level)
                break

    for level in levels_to_remove:
        if level in buyside_levels:
            buyside_levels.remove(level)
        elif level in sellside_levels:
            sellside_levels.remove(level)

    cooldown_ok = (
        (grab_buyside and index - last_buyside_grab > 3)
        or ((not grab_buyside) and index - last_sellside_grab > 3)
    )
    cooldown_ok_series[index] = cooldown_ok
    grab_found_series[index] = grab_found
    grab_buyside_series[index] = grab_buyside
    grab_size_series[index] = grab_size
    if grab_found and grab_size > 0:
        visible = cooldown_ok
        if grab_buyside:
            raw_buyside_small[index] = grab_size == 1
            raw_buyside_medium[index] = grab_size == 2
            raw_buyside_large[index] = grab_size == 3
            buyside_small[index] = visible and grab_size == 1
            buyside_medium[index] = visible and grab_size == 2
            buyside_large[index] = visible and grab_size == 3
        else:
            raw_sellside_small[index] = grab_size == 1
            raw_sellside_medium[index] = grab_size == 2
            raw_sellside_large[index] = grab_size == 3
            sellside_small[index] = visible and grab_size == 1
            sellside_medium[index] = visible and grab_size == 2
            sellside_large[index] = visible and grab_size == 3
        if cooldown_ok:
            if grab_buyside:
                last_buyside_grab = index
            else:
                last_sellside_grab = index

grab_points = []
for index in range(n):
    point = None
    if buyside_small[index]:
        point = {"value": float(high[index]), "color": buyside_color, "size": 5}
    elif buyside_medium[index]:
        point = {"value": float(high[index]), "color": buyside_color, "size": 7}
    elif buyside_large[index]:
        point = {"value": float(high[index]), "color": buyside_color, "size": 10}
    elif sellside_small[index]:
        point = {"value": float(low[index]), "color": sellside_color, "size": 5}
    elif sellside_medium[index]:
        point = {"value": float(low[index]), "color": sellside_color, "size": 7}
    elif sellside_large[index]:
        point = {"value": float(low[index]), "color": sellside_color, "size": 10}
    grab_points.append(point)

scanner_basis = [
    {"value": float((high[index] + low[index]) / 2.0), "color": "rgba(120, 123, 134, 0.0)"}
    for index in range(n)
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Liquidity Grab", "data": grab_points, "color": buyside_color, "type": "circle", "size": 5, "overlay": True},
        {"name": "Scanner Basis", "title": False, "data": scanner_basis, "color": "rgba(120, 123, 134, 0.0)", "type": "line", "lineWidth": 1, "opacity": 0.0, "overlay": True},
    ],
    "signals": [
        {
            "type": "sell", "name": "Buyside Liquidity Grab", "text": "●",
            "data": [float(high[index]) if raw_buyside_small[index] or raw_buyside_medium[index] or raw_buyside_large[index] else None for index in range(n)],
            "color": buyside_color, "renderMode": "hidden",
        },
        {
            "type": "buy", "name": "Sellside Liquidity Grab", "text": "●",
            "data": [float(low[index]) if raw_sellside_small[index] or raw_sellside_medium[index] or raw_sellside_large[index] else None for index in range(n)],
            "color": sellside_color, "renderMode": "hidden",
        },
    ],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "pivotHigh": [float(value) if np.isfinite(value) else None for value in pivot_high],
        "pivotLow": [float(value) if np.isfinite(value) else None for value in pivot_low],
        "rawBuysideSmall": raw_buyside_small.tolist(), "rawBuysideMedium": raw_buyside_medium.tolist(), "rawBuysideLarge": raw_buyside_large.tolist(),
        "rawSellsideSmall": raw_sellside_small.tolist(), "rawSellsideMedium": raw_sellside_medium.tolist(), "rawSellsideLarge": raw_sellside_large.tolist(),
        "buysideSmall": buyside_small.tolist(), "buysideMedium": buyside_medium.tolist(), "buysideLarge": buyside_large.tolist(),
        "sellsideSmall": sellside_small.tolist(), "sellsideMedium": sellside_medium.tolist(), "sellsideLarge": sellside_large.tolist(),
        "cooldownOk": cooldown_ok_series.tolist(), "grabFound": grab_found_series.tolist(), "grabBuyside": grab_buyside_series.tolist(), "grabSize": grab_size_series.tolist(),
        "remainingBuysideLevels": [float(value) for value in buyside_levels], "remainingSellsideLevels": [float(value) for value in sellside_levels],
        "firstProcessed": int(first_processed),
    },
}
