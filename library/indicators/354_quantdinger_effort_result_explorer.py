# ============================================================
# QuantDinger Effort Result Explorer
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Effort Result Explorer"
my_indicator_description = "Compares normalized price result with normalized volume effort, then derives directional flow, bias, equalizer bands, and anomaly markers."

# @param flow_length int 10 Flow averaging length range=1:10000:1
# @param flow_average str WMA Flow average values=WMA,EMA,SMA,HMA,RMA
# @param smoothing int 3 Flow smoothing range=1:10000:1
# @param signal_length int 5 Signal length range=1:10000:1
# @param signal_average str WMA Signal average values=WMA,EMA,SMA,HMA,RMA
# @param lookback int 20 Normalization lookback range=1:10000:1
# @param lookback_averaging str Simple Lookback averaging values=Simple,Same-as-Flow
# @param show_bias bool false Show bias plot
# @param bias_length int 30 Bias length range=1:10000:1
# @param bias_average str WMA Bias average values=WMA,EMA,SMA,HMA,RMA
# @param show_bands bool true Show effort/result bands
# @param band_scale str 100 Band scale values=100,200,400
# @param band_option str Joint Band layout values=Joint,Separate
# @param show_markers bool true Show effort/result markers
# @param show_control_levels bool true Show control levels
# @param control_level float 25.0 Control level range=0:1000:1

flow_length = min(10000, max(1, int(params.get("flow_length", 10))))
flow_average = str(params.get("flow_average", "WMA"))
smoothing = min(10000, max(1, int(params.get("smoothing", 3))))
signal_length = min(10000, max(1, int(params.get("signal_length", 5))))
signal_average = str(params.get("signal_average", "WMA"))
lookback = min(10000, max(1, int(params.get("lookback", 20))))
lookback_averaging = str(params.get("lookback_averaging", "Simple")).replace("-", " ")
show_bias = bool(params.get("show_bias", False))
bias_length = min(10000, max(1, int(params.get("bias_length", 30))))
bias_average = str(params.get("bias_average", "WMA"))
show_bands = bool(params.get("show_bands", True))
band_scale = float(str(params.get("band_scale", "100")))
band_scale = 100.0 if band_scale not in (100.0, 200.0, 400.0) else band_scale
band_option = str(params.get("band_option", "Joint"))
show_markers = bool(params.get("show_markers", True))
show_control_levels = bool(params.get("show_control_levels", True))
control_level = min(1000.0, max(0.0, float(params.get("control_level", 25.0))))
valid_average_types = ("WMA", "EMA", "SMA", "HMA", "RMA")
flow_average = flow_average if flow_average in valid_average_types else "WMA"
signal_average = signal_average if signal_average in valid_average_types else "WMA"
bias_average = bias_average if bias_average in valid_average_types else "WMA"


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    seed = []
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) == length:
                previous = float(np.mean(seed))
                result[index] = previous
        else:
            previous = (previous * (length - 1.0) + float(value)) / length
            result[index] = previous
    return result


def _wma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
    return result


def _average(values, length, average_type):
    if average_type == "SMA":
        return _sma(values, length)
    if average_type == "EMA":
        return _ema(values, length)
    if average_type == "RMA":
        return _rma(values, length)
    if average_type == "WMA":
        return _wma(values, length)
    return _wma(2.0 * _wma(values, max(1, length // 2)) - _wma(values, length), max(1, int(np.sqrt(length))))


def _normalize(values, averages):
    result = np.full(len(values), np.nan, dtype=float)
    for index, (value, average) in enumerate(zip(values, averages)):
        if not np.all(np.isfinite([value, average])) or average == 0.0:
            continue
        ratio = value / average
        result[index] = 1.0 if ratio > 1.50 else 0.90 if ratio > 1.20 else 0.80 if ratio > 1.00 else 0.70 if ratio > 0.80 else 0.60 if ratio > 0.60 else 0.50 if ratio > 0.40 else 0.25 if ratio > 0.20 else 0.10
    return result


def _safe_divide(numerator, denominator):
    result = np.full(len(numerator), np.nan, dtype=float)
    valid = np.isfinite(numerator) & np.isfinite(denominator) & (denominator != 0.0)
    result[valid] = numerator[valid] / denominator[valid]
    positive_infinite = np.isfinite(numerator) & (numerator > 0.0) & (denominator == 0.0)
    result[positive_infinite] = np.inf
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].to_numpy(dtype=float)
n = len(df)

lookback_average_type = "SMA" if lookback_averaging == "Simple" else flow_average
effective_volume = np.where(np.isfinite(volume), volume, 1.0)
missing_volume = ~np.isfinite(volume)
volume_average = _average(effective_volume, lookback, lookback_average_type)
normalized_volume_pre = _normalize(effective_volume, volume_average) * 100.0
normalized_volume = np.where(missing_volume, 100.0, normalized_volume_pre)

bar_spread = close - opened
bar_range = high - low
two_bar_range = np.full(n, np.nan, dtype=float)
source_shift = np.full(n, np.nan, dtype=float)
if n > 1:
    two_bar_range[1:] = np.maximum(high[1:], high[:-1]) - np.minimum(low[1:], low[:-1])
    source_shift[1:] = np.diff(close)
spread_sign = np.sign(bar_spread)
shift_sign = np.sign(source_shift)
bar_closing = 2.0 * _safe_divide(close - low, bar_range) * 100.0 - 100.0
spread_to_range = _safe_divide(bar_spread, bar_range) * 100.0
absolute_spread = np.abs(bar_spread)
average_spread = _average(absolute_spread, lookback, lookback_average_type)
normalized_spread = _normalize(absolute_spread, average_spread) * 100.0 * spread_sign
two_bar_closing = 2.0 * _safe_divide(close - np.r_[np.nan, np.minimum(low[1:], low[:-1])], two_bar_range) * 100.0 - 100.0
shift_to_range = _safe_divide(source_shift, two_bar_range) * 100.0
absolute_shift = np.abs(source_shift)
average_shift = _average(absolute_shift, lookback, lookback_average_type)
normalized_shift = _normalize(absolute_shift, average_shift) * 100.0 * shift_sign
price_result = (bar_closing + spread_to_range + normalized_spread + two_bar_closing + shift_to_range + normalized_shift) / 6.0
bar_flow = price_result * normalized_volume / 100.0
bulls = np.maximum(bar_flow, 0.0)
bears = np.maximum(-bar_flow, 0.0)
bulls_average = _average(bulls, flow_length, flow_average)
bears_average = _average(bears, flow_length, flow_average)
flow_ratio = _safe_divide(bulls_average, bears_average)
raw_flow = np.full(n, np.nan, dtype=float)
finite_ratio = np.isfinite(flow_ratio)
raw_flow[finite_ratio] = 2.0 * (100.0 - 100.0 / (1.0 + flow_ratio[finite_ratio])) - 100.0
raw_flow[np.isposinf(flow_ratio)] = 100.0
smoothed_flow = _wma(raw_flow, smoothing)
signal = _average(smoothed_flow, signal_length, signal_average)

bias_bulls = _average(bulls, bias_length, bias_average)
bias_bears = _average(bears, bias_length, bias_average)
bias_ratio = _safe_divide(bias_bulls, bias_bears)
raw_bias = np.full(n, np.nan, dtype=float)
valid_bias = np.isfinite(bias_ratio)
raw_bias[valid_bias] = 2.0 * (100.0 - 100.0 / (1.0 + bias_ratio[valid_bias])) - 100.0
raw_bias[np.isposinf(bias_ratio)] = 100.0
smoothed_bias = _wma(raw_bias, smoothing)
smoothed_bulls = _wma(bulls_average, smoothing)
smoothed_bears = _wma(bears_average, smoothing)

normalized_price = np.clip(price_result, -100.0, 100.0)
normalized_volume_clipped = np.clip(normalized_volume, -100.0, 100.0)
volume_band_high = normalized_volume_clipped * band_scale / 200.0
price_band_low = volume_band_high if band_option == "Joint" else np.full(n, 0.5 * band_scale)
price_band_high = price_band_low + np.abs(normalized_price) * band_scale / 200.0
effort_result_ratio = _safe_divide(100.0 * np.abs(normalized_price), normalized_volume_clipped)
positive = normalized_price > 0.0
mega = (np.abs(normalized_price) >= 80.0) & (normalized_volume_clipped >= 80.0)
compression = effort_result_ratio <= 50.0
ease_of_move = (effort_result_ratio >= 120.0) & (~mega)


def _cross_above(values, level):
    return np.asarray([index > 0 and np.all(np.isfinite([values[index], values[index - 1]])) and values[index] > level and values[index - 1] <= level for index in range(len(values))], dtype=bool)


def _cross_below(values, level):
    return np.asarray([index > 0 and np.all(np.isfinite([values[index], values[index - 1]])) and values[index] < level and values[index - 1] >= level for index in range(len(values))], dtype=bool)


flow_up = _cross_above(smoothed_flow, 0.0)
flow_down = _cross_below(smoothed_flow, 0.0)
flow_colors = ["#00FFFF" if np.isfinite(value) and value >= 0.0 else "#FF9800" for value in smoothed_flow]
bias_area = [{"upper": float(max(0.0, value)), "lower": float(min(0.0, value)), "color": "#00AA00" if value >= 0.0 else "#FF180B", "opacity": 0.30} if show_bias and np.isfinite(value) else None for value in smoothed_bias]
volume_cloud = [{"upper": float(max(0.0, value)), "lower": float(min(0.0, value)), "color": "#26A69A" if bar_flow[index] > 0 else "#EF5350", "opacity": 0.25} if show_bands and np.isfinite(value) else None for index, value in enumerate(volume_band_high)]
price_cloud = [{"upper": float(max(price_band_low[index], price_band_high[index])), "lower": float(min(price_band_low[index], price_band_high[index])), "color": "#3ED73E" if bar_flow[index] > 0 else "#FF870A", "opacity": 0.25} if show_bands and np.all(np.isfinite([price_band_low[index], price_band_high[index]])) else None for index in range(n)]


def _events(mask, text, color, event_type):
    return {"type": event_type, "name": text, "data": [{"active": True, "price": 0.0, "text": text, "color": color} if show_markers and active else None for active in mask], "color": color, "renderMode": "events"}


def _limited_event_masks(masks, recent_bars=120, total_cap=16, minimum_gap=4):
    result = [np.zeros(n, dtype=bool) for _ in masks]
    candidates = []
    first_index = max(0, n - recent_bars)
    for mask_index, mask in enumerate(masks):
        for index in np.flatnonzero(np.asarray(mask, dtype=bool)):
            if index >= first_index:
                candidates.append((int(index), mask_index))
    selected_indices = []
    for index, mask_index in sorted(candidates, reverse=True):
        if len(selected_indices) >= total_cap:
            break
        if any(abs(index - selected) < minimum_gap for selected in selected_indices):
            continue
        result[mask_index][index] = True
        selected_indices.append(index)
    return result


display_eom_positive, display_eom_negative, display_compression_positive, display_compression_negative, display_mega_positive, display_mega_negative, display_flow_up, display_flow_down = _limited_event_masks([
    ease_of_move & positive,
    ease_of_move & (~positive),
    compression & positive,
    compression & (~positive),
    mega & positive,
    mega & (~positive),
    flow_up,
    flow_down,
])


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Signal Line", "data": _finite(signal), "color": "rgba(184,184,184,0.67)", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "RROF Smooth", "data": _finite(smoothed_flow), "color": flow_colors, "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Bias Sentiment", "title": show_bias, "data": bias_area, "color": "#00AA00", "type": "cloud", "overlay": False},
        {"name": "Volume Band", "title": show_bands, "data": volume_cloud, "color": "#26A69A", "type": "cloud", "overlay": False},
        {"name": "Price Band", "title": show_bands, "data": price_cloud, "color": "#3ED73E", "type": "cloud", "overlay": False},
        {"name": "Zero Line", "data": [0.0] * n, "color": "rgba(17,99,246,0.75)", "type": "line", "overlay": False},
        {"name": "Control High", "data": [control_level if show_control_levels else None] * n, "color": "rgba(255,238,88,0.70)", "type": "line", "lineStyle": "dashed", "overlay": False},
        {"name": "Control Low", "data": [-control_level if show_control_levels else None] * n, "color": "rgba(255,238,88,0.70)", "type": "line", "lineStyle": "dashed", "overlay": False},
    ],
    "signals": [
        _events(display_eom_positive, "EoM +", "#089981", "buy"), _events(display_eom_negative, "EoM -", "#F23645", "sell"),
        _events(display_compression_positive, "Compression +", "#089981", "buy"), _events(display_compression_negative, "Compression -", "#F23645", "sell"),
        _events(display_mega_positive, "Mega +", "#089981", "buy"), _events(display_mega_negative, "Mega -", "#F23645", "sell"),
        {"type": "buy", "name": "Flow Above Zero", "data": [{"active": True, "price": float(smoothed_flow[index]), "text": "▲", "color": "#00FFFF"} if active else None for index, active in enumerate(display_flow_up)], "color": "#00FFFF", "renderMode": "events"},
        {"type": "sell", "name": "Flow Below Zero", "data": [{"active": True, "price": float(smoothed_flow[index]), "text": "▼", "color": "#FF9800"} if active else None for index, active in enumerate(display_flow_down)], "color": "#FF9800", "renderMode": "events"},
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "effectiveVolume": _finite(effective_volume), "volumeAverage": _finite(volume_average), "normalizedVolume": _finite(normalized_volume),
        "barSpread": _finite(bar_spread), "barRange": _finite(bar_range), "twoBarRange": _finite(two_bar_range), "sourceShift": _finite(source_shift),
        "barClosing": _finite(bar_closing), "spreadToRange": _finite(spread_to_range), "normalizedSpread": _finite(normalized_spread),
        "twoBarClosing": _finite(two_bar_closing), "shiftToRange": _finite(shift_to_range), "normalizedShift": _finite(normalized_shift),
        "priceResult": _finite(price_result), "barFlow": _finite(bar_flow), "bulls": _finite(bulls), "bears": _finite(bears),
        "bullsAverage": _finite(bulls_average), "bearsAverage": _finite(bears_average), "rawFlow": _finite(raw_flow), "smoothedFlow": _finite(smoothed_flow), "signal": _finite(signal),
        "rawBias": _finite(raw_bias), "smoothedBias": _finite(smoothed_bias), "smoothedBulls": _finite(smoothed_bulls), "smoothedBears": _finite(smoothed_bears),
        "normalizedPrice": _finite(normalized_price), "normalizedVolumeClipped": _finite(normalized_volume_clipped), "volumeBandHigh": _finite(volume_band_high),
        "priceBandLow": _finite(price_band_low), "priceBandHigh": _finite(price_band_high), "effortResultRatio": _finite(effort_result_ratio),
        "mega": mega.tolist(), "compression": compression.tolist(), "easeOfMove": ease_of_move.tolist(), "positive": positive.tolist(),
        "flowUp": flow_up.tolist(), "flowDown": flow_down.tolist(),
    },
}
