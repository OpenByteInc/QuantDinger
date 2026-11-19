# ============================================================
# QuantDinger Pivot Support Resistance
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Pivot Support Resistance"
my_indicator_description = (
    "Confirmed swing-high and swing-low levels projected as chart support and "
    "resistance segments."
)

# @param left_bars int 10 Pivot bars on the left range=1:100:1
# @param right_bars int 10 Pivot bars on the right range=1:100:1

left_bars = max(1, int(params.get("left_bars", 10)))
right_bars = max(1, int(params.get("right_bars", 10)))

df = df.copy()
high_values = df["high"].astype(float).to_numpy(dtype=float)
low_values = df["low"].astype(float).to_numpy(dtype=float)
n = len(df)

pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
for pivot_index in range(left_bars, n - right_bars):
    high_candidate = high_values[pivot_index]
    low_candidate = low_values[pivot_index]
    high_left = high_values[pivot_index - left_bars : pivot_index]
    high_right = high_values[pivot_index + 1 : pivot_index + right_bars + 1]
    low_left = low_values[pivot_index - left_bars : pivot_index]
    low_right = low_values[pivot_index + 1 : pivot_index + right_bars + 1]

    confirmation_index = pivot_index + right_bars
    if (
        np.isfinite(high_candidate)
        and np.all(high_candidate >= high_left)
        and np.all(high_candidate > high_right)
    ):
        pivot_high[confirmation_index] = high_candidate
    if (
        np.isfinite(low_candidate)
        and np.all(low_candidate <= low_left)
        and np.all(low_candidate < low_right)
    ):
        pivot_low[confirmation_index] = low_candidate

resistance = np.full(n, np.nan, dtype=float)
support = np.full(n, np.nan, dtype=float)
last_resistance = np.nan
last_support = np.nan
for index in range(n):
    if np.isfinite(pivot_high[index]):
        last_resistance = pivot_high[index]
    if np.isfinite(pivot_low[index]):
        last_support = pivot_low[index]
    resistance[index] = last_resistance
    support[index] = last_support

resistance_plot = [None] * n
support_plot = [None] * n
for index in range(n):
    target_index = index - left_bars
    if target_index < 0:
        continue

    resistance_changed = (
        index == 0
        or not np.isfinite(resistance[index - 1])
        or resistance[index] != resistance[index - 1]
    )
    if np.isfinite(resistance[index]) and not resistance_changed:
        resistance_plot[target_index] = float(resistance[index])

    support_changed = (
        index == 0
        or not np.isfinite(support[index - 1])
        or support[index] != support[index - 1]
    )
    if np.isfinite(support[index]) and not support_changed:
        support_plot[target_index] = float(support[index])

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Resistance",
            "data": resistance_plot,
            "color": "#00BCD4",
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        },
        {
            "name": "Support",
            "data": support_plot,
            "color": "#DC143C",
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        },
    ],
    "signals": [],
    "layers": [],
}
