# ============================================================
# QuantDinger Trend Impulse Channels
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Trend Impulse Channels"
my_indicator_description = (
    "Advances a stateful trend line after ATR-confirmed direction flips, "
    "wraps it in volatility channels, and marks directional channel retests."
)

# @param trigger_threshold float 2.86 Trigger threshold range=0.0:10.0:0.01
# @param max_step_atr float -0.034 Maximum step size in ATR range=-1.0:1.0:0.001
# @param band_multiplier float 2.02 Band multiplier range=0.0:10.0:0.01
# @param trend_hold int 0 Trend hold bars range=0:200:1
# @param show_fill bool true Show channel fill
# @param show_retests bool true Show channel retest signals
# @param filter_retests bool true Filter retests by trend direction
# @param show_steps bool false Show trend step signals

trigger_threshold = max(0.0, float(params.get("trigger_threshold", 2.86)))
max_step_atr = float(params.get("max_step_atr", -0.034))
band_multiplier = max(0.0, float(params.get("band_multiplier", 2.02)))
trend_hold = max(0, int(params.get("trend_hold", 0)))
show_fill = bool(params.get("show_fill", True))
show_retests = bool(params.get("show_retests", True))
filter_retests = bool(params.get("filter_retests", True))
show_steps = bool(params.get("show_steps", False))

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
atr = pine_rma(true_range, 200)

trend_line = np.full(n, np.nan, dtype=float)
upper = np.full(n, np.nan, dtype=float)
lower = np.full(n, np.nan, dtype=float)
direction = np.zeros(n, dtype=int)
trend_step = np.zeros(n, dtype=bool)
lower_retest = np.zeros(n, dtype=bool)
upper_retest = np.zeros(n, dtype=bool)

current_trend = np.nan
current_direction = 0
bars_in_trend = 0
hold_extension = 0

for index in range(n):
    if not np.isfinite(current_trend):
        current_trend = float(close[index])
        current_direction = 0
        bars_in_trend = 0
        hold_extension = 0
    else:
        trigger = atr[index] * trigger_threshold if np.isfinite(atr[index]) else np.nan
        start_long = np.isfinite(trigger) and close[index] > current_trend + trigger
        start_short = np.isfinite(trigger) and close[index] < current_trend - trigger
        flip = (start_long or start_short) and bars_in_trend >= 0
        if flip and hold_extension <= 0:
            current_trend = float(close[index])
            current_direction = 1 if start_long else -1
            bars_in_trend = 1
            hold_extension = trend_hold
        else:
            if np.isfinite(atr[index]):
                step_base = atr[index] * 2.52
                maximum_step = atr[index] * max_step_atr
                step_size = min(step_base + 0.0093 * bars_in_trend * atr[index], maximum_step)
                if current_direction == 1:
                    current_trend += step_size
                elif current_direction == -1:
                    current_trend -= step_size
            bars_in_trend += 1
            hold_extension = max(hold_extension - 1, 0)

    trend_line[index] = current_trend
    direction[index] = current_direction
    if np.isfinite(atr[index]):
        upper[index] = current_trend + atr[index] * band_multiplier
        lower[index] = current_trend - atr[index] * band_multiplier

    if index > 0 and current_direction != 0 and np.isfinite(trend_line[index - 1]):
        trend_step[index] = (
            (current_trend > trend_line[index - 1] and current_direction == 1)
            or (current_trend < trend_line[index - 1] and current_direction == -1)
        )

    if index > 0 and np.isfinite(lower[index]) and np.isfinite(lower[index - 1]):
        crossed_lower = low[index] < lower[index] and low[index - 1] >= lower[index - 1]
        crossed_upper = high[index] > upper[index] and high[index - 1] <= upper[index - 1]
        lower_retest[index] = crossed_lower and (not filter_retests or current_direction == 1)
        upper_retest[index] = crossed_upper and (not filter_retests or current_direction == -1)

bull_color = "#00E676"
bear_color = "#F23645"
neutral_color = "#787B86"

trend_points = []
for index, value in enumerate(trend_line):
    if not np.isfinite(value):
        trend_points.append(None)
        continue
    color = bull_color if direction[index] == 1 else bear_color if direction[index] == -1 else neutral_color
    trend_points.append({"value": float(value), "color": color})

slice_count = 6
upper_slices = [[None for _ in range(n)] for _ in range(slice_count)]
lower_slices = [[None for _ in range(n)] for _ in range(slice_count)]
if show_fill:
    for index in range(n):
        if not np.isfinite(upper[index]) or not np.isfinite(lower[index]):
            continue
        color_rgb = (0, 230, 118) if direction[index] == 1 else (242, 54, 69) if direction[index] == -1 else (120, 123, 134)
        upper_span = upper[index] - trend_line[index]
        lower_span = trend_line[index] - lower[index]
        for slice_index in range(slice_count):
            ratio = (slice_index + 0.5) / slice_count
            upper_top = upper[index] - upper_span * slice_index / slice_count
            upper_bottom = upper[index] - upper_span * (slice_index + 1) / slice_count
            lower_top = trend_line[index] - lower_span * slice_index / slice_count
            lower_bottom = trend_line[index] - lower_span * (slice_index + 1) / slice_count
            upper_alpha = 0.40 * (1.0 - ratio)
            lower_alpha = 0.40 * ratio
            upper_slices[slice_index][index] = {
                "upper": float(upper_top),
                "lower": float(upper_bottom),
                "color": f"rgba({color_rgb[0]}, {color_rgb[1]}, {color_rgb[2]}, {upper_alpha:.3f})",
            }
            lower_slices[slice_index][index] = {
                "upper": float(lower_top),
                "lower": float(lower_bottom),
                "color": f"rgba({color_rgb[0]}, {color_rgb[1]}, {color_rgb[2]}, {lower_alpha:.3f})",
            }

plots = [
    {"name": "Impulse Guide", "data": trend_points, "color": neutral_color, "type": "line", "lineWidth": 2, "overlay": True},
]
for slice_index in range(slice_count):
    plots.append(
        {
            "name": f"Upper Channel Fill {slice_index + 1}",
            "title": False,
            "data": upper_slices[slice_index],
            "color": neutral_color,
            "type": "cloud",
            "opacity": 1.0,
            "overlay": True,
        }
    )
    plots.append(
        {
            "name": f"Lower Channel Fill {slice_index + 1}",
            "title": False,
            "data": lower_slices[slice_index],
            "color": neutral_color,
            "type": "cloud",
            "opacity": 1.0,
            "overlay": True,
        }
    )

output = {
    "name": my_indicator_name,
    "plots": plots,
    "signals": [
        {
            "type": "buy",
            "text": "▲",
            "name": "Lower Channel Retest",
            "data": [float(low[index]) if show_retests and lower_retest[index] else None for index in range(n)],
            "color": bull_color,
            "renderMode": "events",
        },
        {
            "type": "sell",
            "text": "▼",
            "name": "Upper Channel Retest",
            "data": [float(high[index]) if show_retests and upper_retest[index] else None for index in range(n)],
            "color": bear_color,
            "renderMode": "events",
        },
        {
            "type": "buy",
            "text": "●",
            "name": "Bullish Trend Step",
            "data": [float(high[index]) if show_steps and trend_step[index] and direction[index] == 1 else None for index in range(n)],
            "color": bull_color,
            "renderMode": "events",
        },
        {
            "type": "sell",
            "text": "●",
            "name": "Bearish Trend Step",
            "data": [float(low[index]) if show_steps and trend_step[index] and direction[index] == -1 else None for index in range(n)],
            "color": bear_color,
            "renderMode": "events",
        },
    ],
    "layers": [],
}
