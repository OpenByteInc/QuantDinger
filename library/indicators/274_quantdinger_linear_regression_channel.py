# ============================================================
# QuantDinger Linear Regression Channel
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Linear Regression Channel"
my_indicator_description = "Combines rolling regression bands, a latest-window channel, bounded future projection, heat-map candles, and pivot breakouts."

# @param show_bands bool true Plot rolling regression bands
# @param band_length int 150 Rolling band length range=2:1000:1
# @param band_deviation float 3.0 Band range multiplier range=0:20:0.1
# @param show_channel bool false Plot latest regression channel
# @param channel_length int 150 Channel length range=2:1000:1
# @param channel_deviation float 1.0 Channel deviation multiplier range=0:20:0.1
# @param show_projection bool false Plot bounded future regression projection
# @param show_direction bool false Show latest direction arrow
# @param projection_length int 50 Projection regression length range=2:1000:1
# @param projection_deviation float 1.0 Projection deviation multiplier range=0:20:0.1
# @param projection_bars int 50 Visible future projection bars range=1:500:1

show_bands = bool(params.get("show_bands", True))
band_length = min(1000, max(2, int(params.get("band_length", 150))))
band_deviation = min(20.0, max(0.0, float(params.get("band_deviation", 3.0))))
show_channel = bool(params.get("show_channel", False))
channel_length = min(1000, max(2, int(params.get("channel_length", 150))))
channel_deviation = min(20.0, max(0.0, float(params.get("channel_deviation", 1.0))))
show_projection = bool(params.get("show_projection", False))
show_direction = bool(params.get("show_direction", False))
projection_length = min(1000, max(2, int(params.get("projection_length", 50))))
projection_deviation = min(20.0, max(0.0, float(params.get("projection_deviation", 1.0))))
projection_bars = min(500, max(1, int(params.get("projection_bars", 50))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)
times = df["time"].to_numpy(dtype=float) if "time" in df.columns else np.arange(n, dtype=float) * 3600000.0
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0


def _regression(values, length):
    intercepts = np.full(len(values), np.nan, dtype=float)
    old_ends = np.full(len(values), np.nan, dtype=float)
    deviations = np.full(len(values), np.nan, dtype=float)
    slopes = np.full(len(values), np.nan, dtype=float)
    x = np.arange(1, length + 1, dtype=float)
    sx = float(np.sum(x)); sx2 = float(np.sum(x * x)); denominator = length * sx2 - sx * sx
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1][::-1]
        if not np.all(np.isfinite(window)):
            continue
        sy = float(np.sum(window)); slope = float((length * np.sum(x * window) - sx * sy) / denominator); intercept = float((sy - slope * sx) / length)
        fitted_for_deviation = slope * (length - np.arange(length, dtype=float)) + intercept
        intercepts[index] = intercept
        old_ends[index] = intercept + slope * (length - 1)
        deviations[index] = float(np.sqrt(np.mean((window - fitted_for_deviation) ** 2)))
        slopes[index] = slope
    return intercepts, old_ends, deviations, slopes


def _rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    seed = float(np.mean(values[:length]))
    result[length - 1] = seed
    for index in range(length, len(values)):
        seed = (seed * (length - 1.0) + values[index]) / length
        result[index] = seed
    return result


band_intercept, band_old, band_dev, band_slope = _regression(close, band_length)
band_mid = band_intercept + band_slope
average_range = _rma(high - low, band_length)
upper = band_mid + average_range * band_deviation
lower = band_mid - average_range * band_deviation

channel_mid = np.full(n, np.nan, dtype=float)
channel_upper = np.full(n, np.nan, dtype=float)
channel_lower = np.full(n, np.nan, dtype=float)
channel_values = None
if n >= channel_length:
    ci, co, cd, cs = _regression(close, channel_length)
    end = n - 1; start = n - channel_length
    current = float(ci[end]); oldest = float(co[end]); deviation = float(cd[end])
    if np.all(np.isfinite([current, oldest, deviation])):
        values = np.linspace(oldest, current, channel_length)
        channel_mid[start:] = values
        channel_upper[start:] = values + deviation * channel_deviation
        channel_lower[start:] = values - deviation * channel_deviation
        channel_values = {"start": start, "end": end, "intercept": current, "oldEnd": oldest, "deviation": deviation, "slope": float(cs[end])}


def _interval():
    if n < 2:
        return 60000.0
    diffs = np.diff(times); diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    return float(np.median(diffs)) if len(diffs) else 60000.0


layers = []
projection_values = None
if n >= projection_length and (show_projection or show_direction):
    pi, po, pdv, ps = _regression(close, projection_length)
    end = n - 1; start = n - projection_length; interval = _interval()
    current = float(pi[end]); oldest = float(po[end]); deviation = float(pdv[end]); slope = float(ps[end])
    projection_values = {"start": start, "end": end, "intercept": current, "oldEnd": oldest, "deviation": deviation, "slope": slope}
    if show_projection and np.all(np.isfinite([current, oldest, deviation, slope])):
        future_time = float(times[-1] + projection_bars * interval)
        for offset, role, dashed in ((-1.0, "Projection Lower", True), (0.0, "Projection Mid", False), (1.0, "Projection Upper", True)):
            delta = deviation * projection_deviation * offset
            layers.append({
                "type": "line", "role": role, "start": float(times[start]), "end": future_time,
                "y1": float(oldest + delta), "y2": float(current - slope * projection_bars + delta),
                "color": "#808080", "lineWidth": 1, "dashed": dashed,
            })
    if show_direction and np.isfinite(current):
        midpoint = (high[-1] + low[-1]) * 0.5
        direction = "⇗" if midpoint > current else "⇘" if midpoint < current else "⇒"
        color = "#00FFFF" if midpoint > current else "#F01313" if midpoint < current else "#808080"
        layers.append({"type": "label", "index": n - 1, "price": float(high[-1] if midpoint > current else low[-1]), "text": direction, "textColor": color, "color": "rgba(0,0,0,0)", "size": "large"})


def _rgb(hex_color):
    value = hex_color.lstrip("#")
    return np.array([int(value[index:index + 2], 16) for index in (0, 2, 4)], dtype=float)


def _gradient(value, low_value, high_value, low_color, high_color):
    ratio = 0.0 if high_value == low_value else min(1.0, max(0.0, (value - low_value) / (high_value - low_value)))
    rgb = np.rint(_rgb(low_color) + (_rgb(high_color) - _rgb(low_color)) * ratio).astype(int)
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


position = np.full(n, np.nan, dtype=float)
span = upper - lower
valid_span = np.isfinite(span) & (span != 0.0)
position[valid_span] = np.clip((close[valid_span] - lower[valid_span]) / span[valid_span], 0.0, 1.0)
bar_colors = []
for index, value in enumerate(position):
    if not np.isfinite(value) or (show_channel and index < n - channel_length):
        bar_colors.append(None)
    elif show_channel:
        bar_colors.append(_gradient(value, 0.0, 0.5, "#00FFFF", "#FFFF00") if value <= 0.5 else _gradient(value, 0.5, 1.0, "#FFFF00", "#F01313"))
    else:
        bar_colors.append(_gradient(value, 0.0, 0.5, "#F01313", "#FFFF00") if value < 0.5 else _gradient(value, 0.5, 1.0, "#FFFF00", "#00FFFF"))

pivot_low = np.zeros(n, dtype=bool); pivot_high = np.zeros(n, dtype=bool)
for index in range(6, n):
    center = index - 3; left = center - 3; right = center + 4
    pivot_low[index] = low[center] <= np.min(low[left:right])
    pivot_high[index] = high[center] >= np.max(high[left:right])
breakouts_visible = show_bands and not show_channel
bull_break = pivot_low & np.isfinite(lower) & (close < lower) if breakouts_visible else np.zeros(n, dtype=bool)
bear_break = pivot_high & np.isfinite(upper) & (close > upper) if breakouts_visible else np.zeros(n, dtype=bool)


def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


band_visible = show_bands and not show_channel
plots = [
    {"name": "Band Upper", "data": _finite_or_none(upper) if band_visible else [None] * n, "color": "#808080", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Band Mid", "data": _finite_or_none(band_mid) if band_visible else [None] * n, "color": "#808080", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Band Lower", "data": _finite_or_none(lower) if band_visible else [None] * n, "color": "#808080", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Band Cloud", "title": False, "data": [{"upper": float(upper[i]), "lower": float(lower[i]), "color": "#00FFFF" if close[i] >= band_mid[i] else "#F01313"} if band_visible and np.all(np.isfinite([upper[i], lower[i], band_mid[i]])) else None for i in range(n)], "color": "#808080", "type": "cloud", "opacity": 0.10, "overlay": True},
    {"name": "Channel Upper", "data": _finite_or_none(channel_upper) if show_channel else [None] * n, "color": "#808080", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Channel Mid", "data": _finite_or_none(channel_mid) if show_channel else [None] * n, "color": "#808080", "type": "line", "lineWidth": 2, "dashed": True, "overlay": True},
    {"name": "Channel Lower", "data": _finite_or_none(channel_lower) if show_channel else [None] * n, "color": "#808080", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Channel Cloud", "title": False, "data": [{"upper": float(channel_upper[i]), "lower": float(channel_lower[i]), "color": "#808080"} if show_channel and np.all(np.isfinite([channel_upper[i], channel_lower[i]])) else None for i in range(n)], "color": "#808080", "type": "cloud", "opacity": 0.10, "overlay": True},
]
signals = [
    {"type": "buy", "name": "Lower Band Pivot Break", "text": "◆", "data": [float(low[i]) if bull_break[i] else None for i in range(n)], "color": "#00FFFF", "renderMode": "events"},
    {"type": "sell", "name": "Upper Band Pivot Break", "text": "◆", "data": [float(high[i]) if bear_break[i] else None for i in range(n)], "color": "#F01313", "renderMode": "events"},
]

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers, "tables": [],
    "bar_colors": bar_colors, "background_colors": [None] * n,
    "calculatedVars": {
        "bandIntercept": _finite_or_none(band_intercept), "bandOldEnd": _finite_or_none(band_old), "bandDeviation": _finite_or_none(band_dev), "bandSlope": _finite_or_none(band_slope),
        "averageRange": _finite_or_none(average_range), "bandMid": _finite_or_none(band_mid), "upper": _finite_or_none(upper), "lower": _finite_or_none(lower), "position": _finite_or_none(position),
        "channelMid": _finite_or_none(channel_mid), "channelUpper": _finite_or_none(channel_upper), "channelLower": _finite_or_none(channel_lower), "channelValues": channel_values, "projectionValues": projection_values,
        "pivotLow": pivot_low.tolist(), "pivotHigh": pivot_high.tolist(), "bullBreak": bull_break.tolist(), "bearBreak": bear_break.tolist(), "barInterval": _interval(),
    },
}
