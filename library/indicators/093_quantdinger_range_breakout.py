# ============================================================
# QuantDinger Range Breakout
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Range Breakout"
my_indicator_description = (
    "Builds a persistent ATR-width price channel, resets it after confirmed "
    "breakouts or prolonged displacement, and marks break, re-entry, and "
    "optional fakeout events."
)

# @param channel_width float 4.0 Channel width range=0.1:10.0:0.1
# @param show_fakeouts bool false Show fakeout X signals
# @param filter_signals bool false Filter re-entry signals by channel trend
# @param gradient bool true Use gradient channel shading
# @param transparency int 80 Channel transparency range=0:100:1

channel_width = max(0.1, float(params.get("channel_width", 4.0)))
show_fakeouts = bool(params.get("show_fakeouts", False))
filter_signals = bool(params.get("filter_signals", False))
gradient = bool(params.get("gradient", True))
transparency = min(100, max(0, int(params.get("transparency", 80))))

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


def strict_sma(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(period - 1, len(source)):
        window = source[index - period + 1 : index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
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

atr_200 = pine_rma(true_range, 200)
channel_atr = strict_sma(atr_200, 100) * channel_width
mid_price = (high + low) / 2.0

center = np.full(n, np.nan, dtype=float)
upper = np.full(n, np.nan, dtype=float)
lower = np.full(n, np.nan, dtype=float)
upper_mid = np.full(n, np.nan, dtype=float)
lower_mid = np.full(n, np.nan, dtype=float)
trend = np.zeros(n, dtype=int)

bull_break = np.zeros(n, dtype=bool)
bear_break = np.zeros(n, dtype=bool)
buy_reentry = np.zeros(n, dtype=bool)
sell_reentry = np.zeros(n, dtype=bool)
up_fakeout = np.zeros(n, dtype=bool)
down_fakeout = np.zeros(n, dtype=bool)

current_center = np.nan
current_upper = np.nan
current_lower = np.nan
outside_count = 0
current_trend = 0

for index in range(n):
    if index == 301 and np.isfinite(channel_atr[index]):
        current_center = float(mid_price[index])
        current_upper = current_center + float(channel_atr[index])
        current_lower = current_center - float(channel_atr[index])

    if not np.isfinite(current_center):
        trend[index] = current_trend
        continue

    prior_upper = upper[index - 1] if index > 0 else np.nan
    prior_lower = lower[index - 1] if index > 0 else np.nan
    cross_upper = (
        index > 0
        and np.isfinite(prior_upper)
        and low[index] > current_upper
        and low[index - 1] <= prior_upper
    )
    cross_lower = (
        index > 0
        and np.isfinite(prior_lower)
        and high[index] < current_lower
        and high[index - 1] >= prior_lower
    )

    if low[index] > current_upper or high[index] < current_lower:
        outside_count += 1

    if cross_upper:
        current_trend = 1
        bull_break[index] = True
    if cross_lower:
        current_trend = -1
        bear_break[index] = True

    should_reset = cross_upper or cross_lower or outside_count == 100
    if should_reset and np.isfinite(channel_atr[index]):
        outside_count = 0
        current_center = float(mid_price[index])
        current_upper = current_center + float(channel_atr[index])
        current_lower = current_center - float(channel_atr[index])

    center[index] = current_center
    upper[index] = current_upper
    lower[index] = current_lower
    upper_mid[index] = (current_center + current_upper) / 2.0
    lower_mid[index] = (current_center + current_lower) / 2.0
    trend[index] = current_trend

    unchanged = index > 0 and np.isfinite(center[index - 1]) and center[index] == center[index - 1]
    if not unchanged:
        continue

    if index >= 10:
        buy_cross = low[index] > lower_mid[index] and low[index - 1] <= lower_mid[index - 1]
        sell_cross = high[index] < upper_mid[index] and high[index - 1] >= upper_mid[index - 1]
        buy_reentry[index] = buy_cross and low[index - 10] > lower_mid[index]
        sell_reentry[index] = sell_cross and high[index - 10] < upper_mid[index]

    up_fakeout[index] = (
        index > 0
        and high[index] < upper[index]
        and high[index - 1] >= upper[index - 1]
    )
    down_fakeout[index] = (
        index > 0
        and low[index] > lower[index]
        and low[index - 1] <= lower[index - 1]
    )

bull_color = "#1DAC70"
bear_color = "#DF3A79"
mid_color = "#D1D4DC"


def line_break_points(values, color, alternating=False):
    points = []
    for index, value in enumerate(values):
        visible = np.isfinite(value)
        if alternating:
            visible = visible and index % 2 == 0
        elif visible and index > 0 and np.isfinite(values[index - 1]):
            visible = value == values[index - 1]
        if visible:
            points.append({"value": float(value), "color": color})
        else:
            points.append(None)
    return points


upper_points = line_break_points(upper, bull_color)
center_points = line_break_points(center, mid_color, alternating=True)
lower_points = line_break_points(lower, bear_color)
upper_mid_points = line_break_points(upper_mid, "rgba(209, 212, 220, 0.5)")
lower_mid_points = line_break_points(lower_mid, "rgba(209, 212, 220, 0.5)")

slice_count = 6
upper_slices = [[None for _ in range(n)] for _ in range(slice_count)]
lower_slices = [[None for _ in range(n)] for _ in range(slice_count)]
base_alpha = 1.0 - transparency / 100.0
for index in range(n):
    unchanged = index > 0 and np.isfinite(center[index]) and center[index] == center[index - 1]
    if not unchanged:
        continue
    upper_span = upper[index] - upper_mid[index]
    lower_span = lower_mid[index] - lower[index]
    for slice_index in range(slice_count):
        ratio = (slice_index + 0.5) / slice_count
        upper_top = upper[index] - upper_span * slice_index / slice_count
        upper_bottom = upper[index] - upper_span * (slice_index + 1) / slice_count
        lower_top = lower_mid[index] - lower_span * slice_index / slice_count
        lower_bottom = lower_mid[index] - lower_span * (slice_index + 1) / slice_count
        upper_alpha = base_alpha * (1.0 - ratio) if gradient else base_alpha
        lower_alpha = base_alpha * ratio if gradient else base_alpha
        upper_slices[slice_index][index] = {
            "upper": float(upper_top),
            "lower": float(upper_bottom),
            "color": f"rgba(29, 172, 112, {upper_alpha:.3f})",
        }
        lower_slices[slice_index][index] = {
            "upper": float(lower_top),
            "lower": float(lower_bottom),
            "color": f"rgba(223, 58, 121, {lower_alpha:.3f})",
        }

warmup_basis = [
    {"value": float(mid_price[index]), "color": "rgba(209, 212, 220, 0.12)"}
    if not np.isfinite(center[index])
    else None
    for index in range(n)
]

plots = [
    {"name": "Upper Channel", "data": upper_points, "color": bull_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Channel Center", "data": center_points, "color": mid_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Lower Channel", "data": lower_points, "color": bear_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Upper Mid", "data": upper_mid_points, "color": mid_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Lower Mid", "data": lower_mid_points, "color": mid_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Warmup Basis", "title": False, "data": warmup_basis, "color": "rgba(209, 212, 220, 0.12)", "type": "line", "lineWidth": 1, "overlay": True},
]
for slice_index in range(slice_count):
    plots.append(
        {
            "name": f"Upper Gradient {slice_index + 1}",
            "title": False,
            "data": upper_slices[slice_index],
            "color": bull_color,
            "type": "cloud",
            "opacity": 1.0,
            "overlay": True,
        }
    )
    plots.append(
        {
            "name": f"Lower Gradient {slice_index + 1}",
            "title": False,
            "data": lower_slices[slice_index],
            "color": bear_color,
            "type": "cloud",
            "opacity": 1.0,
            "overlay": True,
        }
    )

break_up_data = [None for _ in range(n)]
break_down_data = [None for _ in range(n)]
buy_data = [None for _ in range(n)]
sell_data = [None for _ in range(n)]
fake_up_data = [None for _ in range(n)]
fake_down_data = [None for _ in range(n)]
for index in range(1, n):
    target = index - 1
    if bull_break[index] and np.isfinite(upper[target]):
        break_up_data[target] = float(upper[target])
    if bear_break[index] and np.isfinite(lower[target]):
        break_down_data[target] = float(lower[target])
    if buy_reentry[index] and (not filter_signals or trend[index] == 1):
        buy_data[target] = float(low[target])
    if sell_reentry[index] and (not filter_signals or trend[index] == -1):
        sell_data[target] = float(high[target])
    if show_fakeouts and up_fakeout[index]:
        fake_up_data[target] = float(high[target])
    if show_fakeouts and down_fakeout[index]:
        fake_down_data[target] = float(low[target])

output = {
    "name": my_indicator_name,
    "plots": plots,
    "signals": [
        {"type": "buy", "text": "●", "name": "Bullish Break", "data": break_up_data, "color": bull_color, "renderMode": "events"},
        {"type": "sell", "text": "●", "name": "Bearish Break", "data": break_down_data, "color": bear_color, "renderMode": "events"},
        {"type": "buy", "text": "▲", "name": "Bullish Re-entry", "data": buy_data, "color": bull_color, "renderMode": "events"},
        {"type": "sell", "text": "▼", "name": "Bearish Re-entry", "data": sell_data, "color": bear_color, "renderMode": "events"},
        {"type": "sell", "text": "X", "name": "Upper Fakeout", "data": fake_up_data, "color": bear_color, "renderMode": "events"},
        {"type": "buy", "text": "X", "name": "Lower Fakeout", "data": fake_down_data, "color": bull_color, "renderMode": "events"},
    ],
    "layers": [],
}
