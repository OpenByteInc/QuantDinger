# ============================================================
# QuantDinger Dynamic Support and Resistance
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Dynamic Support and Resistance"
my_indicator_description = (
    "Builds volatility-gated support and resistance areas, then extends the "
    "most recent confirmed zones across the active chart."
)

# @param multiplier float 8.0 Multiplicative factor range=0.5:20.0:0.5
# @param atr_length int 50 ATR length range=1:500:1
# @param extend_last int 4 Extend last zones range=0:12:1
# @param extend_levels bool true Extend level boundaries
# @param extend_areas bool true Extend support and resistance areas

multiplier = max(0.5, float(params.get("multiplier", 8.0)))
atr_length = max(1, int(params.get("atr_length", 50)))
extend_last = max(0, int(params.get("extend_last", 4)))
extend_levels = bool(params.get("extend_levels", True))
extend_areas = bool(params.get("extend_areas", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def pine_rma(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    seed = []
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) < period:
                continue
            previous = float(np.mean(seed[-period:]))
        else:
            previous = (previous * (period - 1) + float(value)) / period
        result[index] = previous
    return result


true_range = np.full(n, np.nan, dtype=float)
if n:
    true_range[0] = high[0] - low[0]
for index in range(1, n):
    true_range[index] = max(
        high[index] - low[index],
        abs(high[index] - close[index - 1]),
        abs(low[index] - close[index - 1]),
    )
atr = pine_rma(true_range, atr_length)

average = np.full(n, np.nan, dtype=float)
held_atr = np.zeros(n, dtype=float)
state = np.zeros(n, dtype=int)
upper_resistance = np.full(n, np.nan, dtype=float)
lower_resistance = np.full(n, np.nan, dtype=float)
upper_support = np.full(n, np.nan, dtype=float)
lower_support = np.full(n, np.nan, dtype=float)
changed = np.zeros(n, dtype=bool)
records = []

if n:
    average[0] = close[0]

for index in range(n):
    previous_average = average[index - 1] if index > 0 else close[index]
    previous_held = held_atr[index - 1] if index > 0 else 0.0
    previous_state = state[index - 1] if index > 0 else 0
    breakout_atr = (atr[index] if np.isfinite(atr[index]) else 0.0) * multiplier

    if abs(close[index] - previous_average) > breakout_atr:
        average[index] = close[index]
    else:
        average[index] = previous_average

    changed[index] = index == 0 or average[index] != previous_average
    held_atr[index] = breakout_atr if average[index] == close[index] else previous_held

    if index > 0 and average[index] > previous_average:
        state[index] = 1
    elif index > 0 and average[index] < previous_average:
        state[index] = 0
    else:
        state[index] = previous_state

    half_width = held_atr[index] / multiplier / 2.0
    full_width = held_atr[index] / multiplier
    if state[index] == 1:
        upper_support[index] = average[index] - half_width
        lower_support[index] = average[index] - full_width
    else:
        upper_resistance[index] = average[index] + full_width
        lower_resistance[index] = average[index] + half_width

    if average[index] == close[index]:
        if state[index] == 1:
            records.insert(
                0,
                {
                    "index": index,
                    "level": float(lower_support[index]),
                    "area": float(upper_support[index]),
                    "support": True,
                },
            )
        else:
            records.insert(
                0,
                {
                    "index": index,
                    "level": float(upper_resistance[index]),
                    "area": float(lower_resistance[index]),
                    "support": False,
                },
            )

support_color = "#089981"
resistance_color = "#F23645"

support_area = []
resistance_area = []
support_boundary = []
resistance_boundary = []
for index in range(n):
    if np.isfinite(upper_support[index]) and np.isfinite(lower_support[index]):
        support_area.append(
            {
                "upper": float(upper_support[index]),
                "lower": float(lower_support[index]),
                "color": support_color,
            }
        )
        support_boundary.append(
            None if changed[index] else {"value": float(lower_support[index]), "color": support_color}
        )
    else:
        support_area.append(None)
        support_boundary.append(None)

    if np.isfinite(upper_resistance[index]) and np.isfinite(lower_resistance[index]):
        resistance_area.append(
            {
                "upper": float(upper_resistance[index]),
                "lower": float(lower_resistance[index]),
                "color": resistance_color,
            }
        )
        resistance_boundary.append(
            None if changed[index] else {"value": float(upper_resistance[index]), "color": resistance_color}
        )
    else:
        resistance_area.append(None)
        resistance_boundary.append(None)

plots = [
    {"name": "Support Area", "title": False, "data": support_area, "color": support_color, "type": "cloud", "opacity": 0.20, "overlay": True},
    {"name": "Support Boundary", "data": support_boundary, "color": support_color, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Resistance Area", "title": False, "data": resistance_area, "color": resistance_color, "type": "cloud", "opacity": 0.20, "overlay": True},
    {"name": "Resistance Boundary", "data": resistance_boundary, "color": resistance_color, "type": "line", "lineWidth": 2, "overlay": True},
]

for record_index, record in enumerate(records[:extend_last]):
    if extend_areas:
        area_data = [None for _ in range(n)]
        for index in range(record["index"], n):
            area_data[index] = {
                "upper": float(max(record["level"], record["area"])),
                "lower": float(min(record["level"], record["area"])),
                "color": support_color if record["support"] else resistance_color,
            }
        plots.append(
            {
                "name": f"Extended Area {record_index + 1}",
                "title": False,
                "data": area_data,
                "color": support_color if record["support"] else resistance_color,
                "type": "cloud",
                "opacity": 0.20,
                "overlay": True,
            }
        )
    if extend_levels:
        level_data = [None for _ in range(n)]
        for index in range(record["index"], n):
            level_data[index] = {
                "value": float(record["level"]),
                "color": support_color if record["support"] else resistance_color,
            }
        plots.append(
            {
                "name": f"Extended Level {record_index + 1}",
                "data": level_data,
                "color": support_color if record["support"] else resistance_color,
                "type": "line",
                "lineWidth": 1,
                "overlay": True,
            }
        )

output = {
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
}
