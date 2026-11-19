# ============================================================
# QuantDinger Consolidation Zones Live
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Consolidation Zones Live"
my_indicator_description = (
    "Tracks live price consolidation ranges from alternating rolling extremes "
    "and marks directional breaks beyond the completed zone."
)

indicator_timeframes = ["1H", "4H", "1D", "1W"]

# @param lookback int 10 Extreme lookback range=2:50:1
# @param min_consolidation int 5 Minimum consolidation length range=2:20:1
# @param paint_zone bool true Paint active consolidation area
# @param zone_color str rgba(41,98,255,0.30) Consolidation area color

lookback = max(2, int(params.get("lookback", 10)))
min_consolidation = max(2, int(params.get("min_consolidation", 5)))
paint_zone = bool(params.get("paint_zone", True))
zone_color = str(params.get("zone_color", "rgba(41,98,255,0.30)"))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

upper_boundary = [None for _ in range(n)]
lower_boundary = [None for _ in range(n)]
zone_cloud = [None for _ in range(n)]
breakout_up = np.zeros(n, dtype=bool)
breakout_down = np.zeros(n, dtype=bool)
direction_values = np.zeros(n, dtype=int)
pivot_values = np.full(n, np.nan, dtype=float)
consolidation_values = np.zeros(n, dtype=int)
condition_high_values = np.full(n, np.nan, dtype=float)
condition_low_values = np.full(n, np.nan, dtype=float)

direction = 0
pivot_price = np.nan
consolidation_count = 0
condition_high = np.nan
condition_low = np.nan

for index in range(n):
    window_start = max(0, index - lookback + 1)
    current_is_high = high[index] >= float(np.max(high[window_start : index + 1]))
    current_is_low = low[index] <= float(np.min(low[window_start : index + 1]))

    previous_direction = direction
    if current_is_high and not current_is_low:
        direction = 1
    elif current_is_low and not current_is_high:
        direction = -1

    turning_price = np.nan
    if current_is_high and current_is_low:
        turning_price = high[index] if direction == 1 else low[index]
    elif current_is_high:
        turning_price = high[index]
    elif current_is_low:
        turning_price = low[index]

    previous_pivot = pivot_price
    if direction != previous_direction and np.isfinite(turning_price):
        pivot_price = turning_price
    elif np.isfinite(turning_price):
        if not np.isfinite(pivot_price):
            pivot_price = turning_price
        elif direction == 1 and turning_price > pivot_price:
            pivot_price = turning_price
        elif direction == -1 and turning_price < pivot_price:
            pivot_price = turning_price

    pivot_changed = (
        np.isfinite(previous_pivot)
        and np.isfinite(pivot_price)
        and pivot_price != previous_pivot
    )

    if pivot_changed:
        if consolidation_count > min_consolidation:
            if np.isfinite(condition_high) and pivot_price > condition_high:
                breakout_up[index] = True
            if np.isfinite(condition_low) and pivot_price < condition_low:
                breakout_down[index] = True
        if (
            consolidation_count > 0
            and np.isfinite(condition_high)
            and np.isfinite(condition_low)
            and condition_low <= pivot_price <= condition_high
        ):
            consolidation_count += 1
        else:
            consolidation_count = 0
    else:
        consolidation_count += 1

    if consolidation_count >= min_consolidation:
        if consolidation_count == min_consolidation:
            range_start = max(0, index - min_consolidation + 1)
            condition_high = float(np.max(high[range_start : index + 1]))
            condition_low = float(np.min(low[range_start : index + 1]))
        else:
            condition_high = max(condition_high, float(high[index]))
            condition_low = min(condition_low, float(low[index]))

        line_start = max(0, index - consolidation_count)
        for line_index in range(line_start, index + 1):
            upper_boundary[line_index] = float(condition_high)
            lower_boundary[line_index] = float(condition_low)

        if paint_zone and consolidation_count > min_consolidation:
            zone_cloud[index] = {
                "upper": float(condition_high),
                "lower": float(condition_low),
                "color": zone_color,
            }

    direction_values[index] = int(direction)
    pivot_values[index] = float(pivot_price) if np.isfinite(pivot_price) else np.nan
    consolidation_values[index] = int(consolidation_count)
    condition_high_values[index] = float(condition_high) if np.isfinite(condition_high) else np.nan
    condition_low_values[index] = float(condition_low) if np.isfinite(condition_low) else np.nan

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Zone High",
            "data": upper_boundary,
            "color": "#EF4444",
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        },
        {
            "name": "Zone Low",
            "data": lower_boundary,
            "color": "#22C55E",
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        },
        {
            "name": "Consolidation Area",
            "title": False,
            "data": zone_cloud,
            "color": zone_color,
            "type": "cloud",
            "opacity": 0.28,
            "overlay": True,
        },
        {
            "name": "Event Anchor",
            "title": False,
            "data": [float(value) for value in close],
            "color": "rgba(0, 0, 0, 0)",
            "type": "line",
            "lineWidth": 1,
            "overlay": True,
        },
    ],
    "signals": [
        {
            "type": "buy",
            "text": "BREAK UP",
            "name": "Breakout Up",
            "data": [float(low[index]) if breakout_up[index] else None for index in range(n)],
            "color": "#22C55E",
            "renderMode": "events",
        },
        {
            "type": "sell",
            "text": "BREAK DOWN",
            "name": "Breakout Down",
            "data": [float(high[index]) if breakout_down[index] else None for index in range(n)],
            "color": "#EF4444",
            "renderMode": "events",
        },
    ],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "direction": [int(value) for value in direction_values],
        "pivotPrice": [float(value) if np.isfinite(value) else None for value in pivot_values],
        "consolidationCount": [int(value) for value in consolidation_values],
        "conditionHigh": [float(value) if np.isfinite(value) else None for value in condition_high_values],
        "conditionLow": [float(value) if np.isfinite(value) else None for value in condition_low_values],
        "breakoutUp": [bool(value) for value in breakout_up],
        "breakoutDown": [bool(value) for value in breakout_down],
        "alertBreakoutUp": [bool(value) for value in breakout_up],
        "alertBreakoutDown": [bool(value) for value in breakout_down],
    },
}
