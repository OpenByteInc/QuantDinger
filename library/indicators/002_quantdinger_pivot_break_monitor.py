# ============================================================
# QuantDinger Pivot Break Monitor
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Pivot Break Monitor"
my_indicator_description = (
    "QuantDinger support/resistance monitor built from confirmed swing "
    "pivots, back-aligned level segments, volume-filtered breaks, and "
    "bullish/bearish wick events."
)

# @param show_breaks bool true Show break and wick labels
# @param left_bars int 15 Pivot bars on the left range=1:100:1
# @param right_bars int 15 Pivot bars on the right range=1:100:1
# @param volume_threshold float 20.0 Volume oscillator threshold range=-100.0:500.0:1.0

show_breaks = bool(params.get("show_breaks", True))
left_bars = max(1, int(params.get("left_bars", 15)))
right_bars = max(1, int(params.get("right_bars", 15)))
volume_threshold = float(params.get("volume_threshold", 20.0))

df = df.copy()
open_price = df["open"].astype(float)
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
volume = df["volume"].astype(float)

open_values = open_price.to_numpy(dtype=float)
high_values = high.to_numpy(dtype=float)
low_values = low.to_numpy(dtype=float)
close_values = close.to_numpy(dtype=float)
n = len(df)

# Pivot ties are asymmetric: equal values on the left are allowed, while an
# equal value on the right invalidates the older candidate.
pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
for pivot_index in range(left_bars, n - right_bars):
    high_candidate = high_values[pivot_index]
    low_candidate = low_values[pivot_index]

    high_left = high_values[pivot_index - left_bars : pivot_index]
    high_right = high_values[pivot_index + 1 : pivot_index + right_bars + 1]
    low_left = low_values[pivot_index - left_bars : pivot_index]
    low_right = low_values[pivot_index + 1 : pivot_index + right_bars + 1]

    if (
        not np.isnan(high_candidate)
        and np.all(high_candidate >= high_left)
        and np.all(high_candidate > high_right)
    ):
        confirmation_index = pivot_index + right_bars
        pivot_high[confirmation_index] = high_candidate

    if (
        not np.isnan(low_candidate)
        and np.all(low_candidate <= low_left)
        and np.all(low_candidate < low_right)
    ):
        confirmation_index = pivot_index + right_bars
        pivot_low[confirmation_index] = low_candidate

# Break logic receives each fully confirmed level one bar after confirmation.
resistance_current = np.full(n, np.nan, dtype=float)
support_current = np.full(n, np.nan, dtype=float)
last_resistance = np.nan
last_support = np.nan
for index in range(n):
    delayed_index = index - 1
    if delayed_index >= 0 and not np.isnan(pivot_high[delayed_index]):
        last_resistance = pivot_high[delayed_index]
    if delayed_index >= 0 and not np.isnan(pivot_low[delayed_index]):
        last_support = pivot_low[delayed_index]
    resistance_current[index] = last_resistance
    support_current[index] = last_support

# The display is aligned back to the originating pivot. Signal calculations
# continue to use confirmed, forward-filled levels without look-ahead.
display_offset = right_bars + 1
resistance_plot = [None] * n
support_plot = [None] * n
for index in range(n):
    target_index = index - display_offset
    if target_index < 0:
        continue

    resistance_value = resistance_current[index]
    resistance_changed = (
        index == 0
        or np.isnan(resistance_current[index - 1])
        or resistance_value != resistance_current[index - 1]
    )
    if not np.isnan(resistance_value) and not resistance_changed:
        resistance_plot[target_index] = float(resistance_value)

    support_value = support_current[index]
    support_changed = (
        index == 0
        or np.isnan(support_current[index - 1])
        or support_value != support_current[index - 1]
    )
    if not np.isnan(support_value) and not support_changed:
        support_plot[target_index] = float(support_value)

volume_ema_fast = volume.ewm(span=5, adjust=False, min_periods=1).mean()
volume_ema_slow = volume.ewm(span=10, adjust=False, min_periods=1).mean()
volume_oscillator = (
    100.0 * (volume_ema_fast - volume_ema_slow) / volume_ema_slow
).to_numpy(dtype=float)

previous_close = np.concatenate(([np.nan], close_values[:-1]))
previous_resistance = np.concatenate(([np.nan], resistance_current[:-1]))
previous_support = np.concatenate(([np.nan], support_current[:-1]))

cross_over_resistance = (
    (close_values > resistance_current)
    & (previous_close <= previous_resistance)
)
cross_under_support = (
    (close_values < support_current)
    & (previous_close >= previous_support)
)

bull_wick_condition = (open_values - low_values) > (
    close_values - open_values
)
bear_wick_condition = (open_values - close_values) < (
    high_values - open_values
)
volume_condition = volume_oscillator > volume_threshold

support_break_events = (
    show_breaks
    & cross_under_support
    & ~bear_wick_condition
    & volume_condition
)
resistance_break_events = (
    show_breaks
    & cross_over_resistance
    & ~bull_wick_condition
    & volume_condition
)
bull_wick_events = show_breaks & cross_over_resistance & bull_wick_condition
bear_wick_events = show_breaks & cross_under_support & bear_wick_condition

above_bar = np.where(high_values >= 0.0, high_values * 1.005, high_values * 0.995)
below_bar = np.where(low_values >= 0.0, low_values * 0.995, low_values * 1.005)

support_break_marks = [
    float(above_bar[index]) if support_break_events[index] else None
    for index in range(n)
]
resistance_break_marks = [
    float(below_bar[index]) if resistance_break_events[index] else None
    for index in range(n)
]
bull_wick_marks = [
    float(below_bar[index]) if bull_wick_events[index] else None
    for index in range(n)
]
bear_wick_marks = [
    float(above_bar[index]) if bear_wick_events[index] else None
    for index in range(n)
]

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Resistance",
            "data": resistance_plot,
            "color": "#FF0000",
            "type": "line",
            "lineWidth": 3,
            "overlay": True,
        },
        {
            "name": "Support",
            "data": support_plot,
            "color": "#233DEE",
            "type": "line",
            "lineWidth": 3,
            "overlay": True,
        },
    ],
    "signals": [
        {
            "type": "sell",
            "text": "B",
            "name": "Support Break",
            "data": support_break_marks,
            "color": "#FF0000",
            "renderMode": "events",
        },
        {
            "type": "buy",
            "text": "B",
            "name": "Resistance Break",
            "data": resistance_break_marks,
            "color": "#00A000",
            "renderMode": "events",
        },
        {
            "type": "buy",
            "text": "Bull Wick",
            "name": "Bull Wick",
            "data": bull_wick_marks,
            "color": "#00A000",
            "renderMode": "events",
        },
        {
            "type": "sell",
            "text": "Bear Wick",
            "name": "Bear Wick",
            "data": bear_wick_marks,
            "color": "#FF0000",
            "renderMode": "events",
        },
    ],
    "layers": [],
}
