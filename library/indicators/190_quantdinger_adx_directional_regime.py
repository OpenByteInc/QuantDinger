# ============================================================
# QuantDinger ADX Directional Regime
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger ADX Directional Regime"
my_indicator_description = (
    "Classifies ranging, directional, and strong directional regimes from Wilder-smoothed "
    "ADX and DI values, with state transitions, pane markers, and optional price-bar coloring."
)

# @param adx_smoothing int 14 ADX smoothing length range=2:500:1
# @param di_length int 14 DI length range=2:500:1
# @param range_level float 20.0 Ranging threshold range=0.0:100.0:1.0
# @param trend_level float 35.0 Strong trend threshold range=0.0:100.0:1.0
# @param show_alert_markers bool true Show regime transition markers
# @param color_price_bars bool true Color price bars by regime
# @param bullish_color str #006400 Bullish rising color
# @param bullish_soft_color str #388E3C Bullish easing color
# @param bearish_color str #8B0000 Bearish rising color
# @param bearish_soft_color str #B71C1C Bearish easing color
# @param range_color str #FF9800 Range color

adx_smoothing = min(500, max(2, int(params.get("adx_smoothing", 14))))
di_length = min(500, max(2, int(params.get("di_length", 14))))
range_level = min(100.0, max(0.0, float(params.get("range_level", 20.0))))
trend_level = min(100.0, max(0.0, float(params.get("trend_level", 35.0))))
show_alert_markers = bool(params.get("show_alert_markers", True))
color_price_bars = bool(params.get("color_price_bars", True))
bullish_color = str(params.get("bullish_color", "#006400"))
bullish_soft_color = str(params.get("bullish_soft_color", "#388E3C"))
bearish_color = str(params.get("bearish_color", "#8B0000"))
bearish_soft_color = str(params.get("bearish_soft_color", "#B71C1C"))
range_color = str(params.get("range_color", "#FF9800"))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
n = len(df)

def rgba(color, alpha):
    value = str(color).strip()
    if value.startswith("#") and len(value) == 7:
        try:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
            return f"rgba({red},{green},{blue},{alpha:.3f})"
        except ValueError:
            return value
    return value

def rma(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < length:
        return result
    seed = source[:length]
    if not np.all(np.isfinite(seed)):
        seed = np.nan_to_num(seed, nan=0.0)
    result[length - 1] = float(np.mean(seed))
    for index in range(length, len(source)):
        value = float(source[index]) if np.isfinite(source[index]) else 0.0
        result[index] = (result[index - 1] * (length - 1) + value) / length
    return result

upward_move = np.zeros(n, dtype=float)
downward_move = np.zeros(n, dtype=float)
true_range = np.zeros(n, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
        continue
    upward_move[index] = high[index] - high[index - 1]
    downward_move[index] = low[index - 1] - low[index]
    true_range[index] = max(
        high[index] - low[index],
        abs(high[index] - df["close"].iloc[index - 1]),
        abs(low[index] - df["close"].iloc[index - 1]),
    )

plus_dm = np.where((upward_move > downward_move) & (upward_move > 0.0), upward_move, 0.0)
minus_dm = np.where((downward_move > upward_move) & (downward_move > 0.0), downward_move, 0.0)
smoothed_range = rma(true_range, di_length)
smoothed_plus = rma(plus_dm, di_length)
smoothed_minus = rma(minus_dm, di_length)

di_plus = np.divide(
    100.0 * smoothed_plus,
    smoothed_range,
    out=np.zeros(n, dtype=float),
    where=np.isfinite(smoothed_range) & (np.abs(smoothed_range) > 1e-12),
)
di_minus = np.divide(
    100.0 * smoothed_minus,
    smoothed_range,
    out=np.zeros(n, dtype=float),
    where=np.isfinite(smoothed_range) & (np.abs(smoothed_range) > 1e-12),
)
directional_sum = di_plus + di_minus
directional_ratio = np.divide(
    np.abs(di_plus - di_minus),
    directional_sum,
    out=np.zeros(n, dtype=float),
    where=np.abs(directional_sum) > 1e-12,
)
adx = 100.0 * np.nan_to_num(rma(directional_ratio, adx_smoothing), nan=0.0)

in_range = adx <= range_level
bullish = di_plus >= di_minus
bearish = di_minus > di_plus
strong_bullish = di_plus >= trend_level
strong_bearish = di_minus > trend_level
adx_rising = np.r_[False, adx[1:] > adx[:-1]] if n else np.zeros(0, dtype=bool)
di_cross = np.r_[False, ((di_plus[1:] >= di_minus[1:]) != (di_plus[:-1] >= di_minus[:-1]))] if n else np.zeros(0, dtype=bool)

weak_long_entry = np.zeros(n, dtype=bool)
weak_short_entry = np.zeros(n, dtype=bool)
strong_long_entry = (~in_range) & bullish & adx_rising & strong_bullish
strong_short_entry = (~in_range) & bearish & adx_rising & strong_bearish
exit_long = np.zeros(n, dtype=bool)
exit_short = np.zeros(n, dtype=bool)
for index in range(1, n):
    weak_long_entry[index] = (
        (not in_range[index] and bullish[index] and adx_rising[index] and not bullish[index - 1])
        or (not in_range[index] and bullish[index] and adx_rising[index] and adx[index] > range_level and in_range[index - 1])
    )
    weak_short_entry[index] = (
        (not in_range[index] and bearish[index] and adx_rising[index] and not bearish[index - 1])
        or (not in_range[index] and bearish[index] and adx_rising[index] and adx[index] > range_level and in_range[index - 1])
    )
    entered_range = in_range[index] and not in_range[index - 1]
    exit_long[index] = (di_cross[index] and bullish[index - 1]) or entered_range
    exit_short[index] = (di_cross[index] and bearish[index - 1]) or entered_range

condition = np.zeros(n, dtype=float)
for index in range(1, n):
    prior = condition[index - 1]
    if prior != 1.0 and strong_long_entry[index]:
        condition[index] = 1.0
    elif prior != -1.0 and strong_short_entry[index]:
        condition[index] = -1.0
    elif prior != 0.5 and weak_long_entry[index]:
        condition[index] = 0.5
    elif prior != -0.5 and weak_short_entry[index]:
        condition[index] = -0.5
    elif prior != 0.0 and (exit_long[index] or exit_short[index]):
        condition[index] = 0.0
    else:
        condition[index] = prior

long_weak = np.r_[False, (condition[1:] == 0.5) & (condition[:-1] != 0.5)] if n else np.zeros(0, dtype=bool)
short_weak = np.r_[False, (condition[1:] == -0.5) & (condition[:-1] != -0.5)] if n else np.zeros(0, dtype=bool)
long_strong = np.r_[False, (condition[1:] == 1.0) & (condition[:-1] != 1.0)] if n else np.zeros(0, dtype=bool)
short_strong = np.r_[False, (condition[1:] == -1.0) & (condition[:-1] != -1.0)] if n else np.zeros(0, dtype=bool)
trend_end = np.r_[False, (condition[1:] == 0.0) & (condition[:-1] != 0.0)] if n else np.zeros(0, dtype=bool)

adx_colors = []
for index in range(n):
    if in_range[index]:
        adx_colors.append(range_color)
    elif bullish[index]:
        adx_colors.append(bullish_color if adx_rising[index] else bullish_soft_color)
    else:
        adx_colors.append(bearish_color if adx_rising[index] else bearish_soft_color)

def points(values, colors):
    return [
        {"value": float(value), "color": colors[index]} if np.isfinite(value) else None
        for index, value in enumerate(values)
    ]

fill_data = []
for index in range(n):
    fill_color = range_color
    if not in_range[index] and bullish[index]:
        fill_color = bullish_color if strong_bullish[index] else bullish_soft_color
    elif not in_range[index] and bearish[index]:
        fill_color = bearish_color if strong_bearish[index] else bearish_soft_color
    fill_data.append({
        "upper": float(max(di_plus[index], di_minus[index])),
        "lower": float(min(di_plus[index], di_minus[index])),
        "color": rgba(fill_color, 0.10),
    })

marker_value = trend_level + 10.0
marker_specs = [
    ("Weak Bull", long_weak, bullish_color, 3),
    ("Weak Bear", short_weak, bearish_color, 3),
    ("Strong Bull", long_strong, bullish_color, 5),
    ("Strong Bear", short_strong, bearish_color, 5),
    ("Trend End", trend_end, range_color, 5),
]

plots = [
    {"name": "ADX", "data": points(adx, adx_colors), "color": range_color, "type": "line", "lineWidth": 3, "overlay": False},
    {"name": "Positive DI", "data": points(di_plus, [bullish_color] * n), "color": bullish_color, "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Negative DI", "data": points(di_minus, [bearish_color] * n), "color": bearish_color, "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Directional Fill", "title": False, "data": fill_data, "type": "cloud", "overlay": False},
    {"name": "Range Level", "title": False, "data": points(np.full(n, range_level), ["#8B93A1"] * n), "color": "#8B93A1", "type": "line", "lineStyle": "dotted", "lineWidth": 1, "overlay": False},
    {"name": "Trend Level", "title": False, "data": points(np.full(n, trend_level), ["#667085"] * n), "color": "#667085", "type": "line", "lineStyle": "dotted", "lineWidth": 1, "overlay": False},
]
if show_alert_markers:
    for name, mask, color, size in marker_specs:
        plots.append({
            "name": name,
            "title": False,
            "data": [float(marker_value) if mask[index] else None for index in range(n)],
            "color": color,
            "type": "circle",
            "size": size,
            "overlay": False,
        })

signals = []
if show_alert_markers:
    event_specs = [
        ("buy", "Bullish Trend", "B", long_weak, bullish_color),
        ("sell", "Bearish Trend", "S", short_weak, bearish_color),
        ("buy", "Strong Bullish Trend", "B+", long_strong, bullish_color),
        ("sell", "Strong Bearish Trend", "S+", short_strong, bearish_color),
        ("exit", "Trend End", "X", trend_end, range_color),
    ]
    for signal_type, name, text, mask, color in event_specs:
        signals.append({
            "type": signal_type,
            "name": name,
            "text": text,
            "color": color,
            "renderMode": "events",
            "data": [
                {"value": float(marker_value), "text": text, "color": color} if mask[index] else None
                for index in range(n)
            ],
        })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "tables": [],
    "bar_colors": adx_colors if color_price_bars else [None for _ in range(n)],
}
