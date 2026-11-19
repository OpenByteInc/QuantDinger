# ============================================================
# QuantDinger Liquidity Sweep Filter
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Liquidity Sweep Filter"
my_indicator_description = "Tracks trend-qualified swing liquidity, aggregates swept volume strength, and renders the active trend segment's overlap volume profile."

# @param length int 12 Trend length range=1:500:1
# @param multiplier float 2.0 Deviation multiplier range=0.1:20.0:0.1
# @param source str close Source values=close,open,high,low,hl2,hlc3,ohlc4
# @param major_sweep_threshold float 50.0 Major sweep threshold range=0:100:1
# @param show_major bool true Show major liquidation labels
# @param show_minor bool true Show minor liquidation labels
# @param show_profile bool true Show current trend volume profile
# @param show_bounds bool true Show profile bounding box
# @param resolution int 40 Profile resolution range=1:200:1
# @param scale int 30 Profile horizontal scale range=1:200:1
# @param dismiss_no_volume bool false Dismiss no-volume warning
# @param bullish_color color #00FFBB Bullish color
# @param bearish_color color #FF1100 Bearish color
# @param profile_color color #808080 Profile color

length = min(500, max(1, int(params.get("length", 12))))
multiplier = min(20.0, max(0.1, float(params.get("multiplier", 2.0))))
source_name = str(params.get("source", "close"))
if source_name not in ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4"):
    source_name = "close"
major_threshold = min(100.0, max(0.0, float(params.get("major_sweep_threshold", 50.0))))
show_major = bool(params.get("show_major", True))
show_minor = bool(params.get("show_minor", True))
show_profile = bool(params.get("show_profile", True))
show_bounds = bool(params.get("show_bounds", True))
resolution = min(200, max(1, int(params.get("resolution", 40))))
scale = min(200, max(1, int(params.get("scale", 30))))
dismiss_no_volume = bool(params.get("dismiss_no_volume", False))
bullish_color = str(params.get("bullish_color", "#00FFBB"))
bearish_color = str(params.get("bearish_color", "#FF1100"))
profile_color = str(params.get("profile_color", "#808080"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].fillna(0.0).astype(float).to_numpy(dtype=float)
n = len(df)
times = df["time"].to_numpy(dtype=float) if "time" in df.columns else np.arange(n, dtype=float) * 3600000.0
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0

source_map = {"close": close, "open": open_price, "high": high, "low": low, "hl2": (high + low) * 0.5, "hlc3": (high + low + close) / 3.0, "ohlc4": (open_price + high + low + close) * 0.25}
source = source_map[source_name]


def ema(values, period):
    result = np.full(len(values), np.nan, dtype=float); alpha = 2.0 / (period + 1.0); state = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value): state = value if not np.isfinite(state) else alpha * value + (1.0 - alpha) * state; result[index] = state
    return result


def rma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) >= period:
        state = float(np.mean(values[:period])); result[period - 1] = state
        for index in range(period, len(values)): state = (state * (period - 1.0) + values[index]) / period; result[index] = state
    return result


ema_source = ema(source, length)
basis = pd.Series(ema_source).rolling(length, min_periods=length).mean().to_numpy(dtype=float)
deviation = ema(np.abs(close - basis), length * 3) * multiplier
upper = basis + deviation
lower = basis - deviation

volume_window = max(1, int(length * multiplier))
volume_low = pd.Series(volume).rolling(volume_window, min_periods=volume_window).min().to_numpy(dtype=float)
volume_high = pd.Series(volume).rolling(volume_window, min_periods=volume_window).max().to_numpy(dtype=float)
normalized_volume = np.divide((volume - volume_low) * 100.0, volume_high - volume_low, out=np.full(n, np.nan), where=np.isfinite(volume_high - volume_low) & ((volume_high - volume_low) != 0.0))

trend = np.zeros(n, dtype=float)
state = 0.0
for index in range(n):
    if np.isfinite(upper[index]) and close[index] > upper[index]: state = 1.0
    if np.isfinite(lower[index]) and close[index] < lower[index]: state = -1.0
    trend[index] = state

trend_cross_up = np.zeros(n, dtype=bool); trend_cross_down = np.zeros(n, dtype=bool); trend_cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    trend_cross_up[index] = trend[index] > 0.0 and trend[index - 1] <= 0.0
    trend_cross_down[index] = trend[index] < 0.0 and trend[index - 1] >= 0.0
    trend_cross[index] = trend_cross_up[index] or trend_cross_down[index]

active_peaks = []
active_valleys = []
bull_liquidation = np.zeros(n, dtype=float); bear_liquidation = np.zeros(n, dtype=float); normalized_bull_liquidation = np.zeros(n, dtype=float); normalized_bear_liquidation = np.zeros(n, dtype=float)
liquidation_labels = []
for index in range(n):
    peak = index >= 2 and high[index - 1] > high[index] and high[index - 2] < high[index - 1]
    valley = index >= 2 and low[index - 1] < low[index] and low[index - 2] > low[index - 1]
    if trend[index] < 0.0:
        if peak and np.isfinite(upper[index]) and high[index - 1] < upper[index]:
            active_peaks.append({"startIndex": index - 1, "endIndex": index, "price": float(high[index - 1]), "volume": float(volume[index - 1]), "normalized": float(normalized_volume[index - 1]), "colorAlpha": float(normalized_volume[index - 1] / 100.0) if np.isfinite(normalized_volume[index - 1]) else 0.0})
    elif trend[index] > 0.0 and np.isfinite(lower[index]) and low[index - 1] > lower[index] and valley:
        active_valleys.append({"startIndex": index - 1, "endIndex": index, "price": float(low[index - 1]), "volume": float(volume[index - 1]), "normalized": float(normalized_volume[index - 1]), "colorAlpha": float(normalized_volume[index - 1] / 100.0) if np.isfinite(normalized_volume[index - 1]) else 0.0})

    peak_survivors = []
    for line in active_peaks:
        if high[index] > line["price"] or (np.isfinite(upper[index]) and upper[index] < line["price"]):
            if high[index] > line["price"]:
                bear_liquidation[index] += line["volume"]
                normalized_bear_liquidation[index] += line["normalized"]
        else:
            line["endIndex"] = index
            peak_survivors.append(line)
    active_peaks = peak_survivors[-500:]

    valley_survivors = []
    for line in active_valleys:
        if low[index] < line["price"] or (np.isfinite(lower[index]) and lower[index] > line["price"]):
            if low[index] < line["price"]:
                bull_liquidation[index] += line["volume"]
                normalized_bull_liquidation[index] += line["normalized"]
        else:
            line["endIndex"] = index
            valley_survivors.append(line)
    active_valleys = valley_survivors[-500:]

    if bull_liquidation[index] > 0.0:
        normalized_value = normalized_bull_liquidation[index]
        visible = (normalized_value < major_threshold and show_minor) or (normalized_value > major_threshold and show_major)
        if visible and np.isfinite(lower[index]): liquidation_labels.append({"type": "label", "index": index, "price": float(lower[index]), "text": f"{bull_liquidation[index]:,.0f}", "color": bullish_color, "textColor": "#FFFFFF", "side": "below"})
    if bear_liquidation[index] > 0.0:
        normalized_value = normalized_bear_liquidation[index]
        visible = (normalized_value < major_threshold and show_minor) or (normalized_value > major_threshold and show_major)
        if visible and np.isfinite(upper[index]): liquidation_labels.append({"type": "label", "index": index, "price": float(upper[index]), "text": f"{bear_liquidation[index]:,.0f}", "color": bearish_color, "textColor": "#FFFFFF", "side": "above"})

true_range = np.zeros(n, dtype=float)
if n:
    true_range[0] = high[0] - low[0]
for index in range(1, n): true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
atr = rma(true_range, 14)
volatility = atr * 0.5


def color_with_alpha(color, alpha):
    value = str(color).strip()
    if value.startswith("#") and len(value) == 7:
        red = int(value[1:3], 16); green = int(value[3:5], 16); blue = int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{max(0.0, min(1.0, float(alpha))):.3f})"
    return value


def gradient(first, second, ratio):
    def rgb(value):
        value = str(value).lstrip("#")
        return np.array([int(value[i:i + 2], 16) for i in (0, 2, 4)], dtype=float) if len(value) == 6 else np.array([128.0, 128.0, 128.0])
    ratio = min(1.0, max(0.0, float(ratio))); mixed = np.rint(rgb(first) + (rgb(second) - rgb(first)) * ratio).astype(int)
    return f"#{mixed[0]:02X}{mixed[1]:02X}{mixed[2]:02X}"


# Replace label colors after the helper is available.
for layer in liquidation_labels:
    layer["color"] = color_with_alpha(bullish_color if layer["side"] == "below" else bearish_color, 0.30)


def interval_ms():
    if n < 2: return 60000.0
    differences = np.diff(times); differences = differences[np.isfinite(differences) & (differences > 0.0)]
    return float(np.median(differences)) if len(differences) else 60000.0


timer = np.full(n, np.nan, dtype=float)
last_cross = None
for index in range(n):
    if trend_cross[index]: last_cross = index
    if last_cross is not None: timer[index] = index - last_cross

latest_timer = int(timer[-1]) if n and np.isfinite(timer[-1]) else 0
profile_start_index = max(0, n - 1 - latest_timer + (1 if latest_timer > 0 else 0)) if n else 0
profile_high = float(np.max(high[profile_start_index:])) if n else np.nan
profile_low = float(np.min(low[profile_start_index:])) if n else np.nan
profile_step = (profile_high - profile_low) / resolution if n else np.nan
profile_bins = np.zeros(resolution, dtype=float)
profile_tops = np.zeros(resolution, dtype=float)
profile_bottoms = np.zeros(resolution, dtype=float)
if n and np.isfinite(profile_step) and profile_step > 0.0:
    for bin_index in range(resolution):
        bottom = profile_low + bin_index * profile_step; top = profile_low + (bin_index + 1) * profile_step
        profile_bottoms[bin_index] = bottom; profile_tops[bin_index] = top
        overlaps = ~((low[profile_start_index:] > top) | (high[profile_start_index:] < bottom))
        profile_bins[bin_index] = float(np.sum(volume[profile_start_index:][overlaps]))

visible_sweep_indexes = set(
    np.flatnonzero((bull_liquidation > 0.0) | (bear_liquidation > 0.0))[-16:].tolist()
)
layers = [layer for layer in liquidation_labels if layer["index"] in visible_sweep_indexes][-16:]
interval = interval_ms()
for line in active_peaks:
    layers.append({"type": "line", "role": "Pending Peak Liquidity", "start": float(times[line["startIndex"]]), "end": float(times[line["endIndex"]]), "y1": line["price"], "y2": line["price"], "color": color_with_alpha(bearish_color, line["colorAlpha"]), "lineWidth": 2})
for line in active_valleys:
    layers.append({"type": "line", "role": "Pending Valley Liquidity", "start": float(times[line["startIndex"]]), "end": float(times[line["endIndex"]]), "y1": line["price"], "y2": line["price"], "color": color_with_alpha(bullish_color, line["colorAlpha"]), "lineWidth": 2})

maximum_profile = float(np.max(profile_bins)) if len(profile_bins) else 0.0
right_time = float(times[-1] + (7 + scale) * interval) if n else 0.0
if show_profile and maximum_profile > 0.0 and float(np.sum(volume)) != 0.0:
    rounded_maximum = max(1.0, float(np.round(maximum_profile)))
    trend_color = bullish_color if n and trend[-1] > 0.0 else bearish_color
    for bin_index in range(resolution):
        rounded_value = float(np.round(profile_bins[bin_index])); width_ratio = rounded_value / rounded_maximum
        left_time = right_time - width_ratio * scale * interval
        color_ratio = float(np.sin(profile_bins[bin_index] * np.pi / maximum_profile))
        box_color = gradient(trend_color, profile_color, color_ratio)
        layers.append({"type": "zone", "role": "Trend Volume Profile", "start": left_time, "end": right_time, "top": float(profile_tops[bin_index]), "bottom": float(profile_bottoms[bin_index]), "fillColor": color_with_alpha(box_color, 0.90), "borderColor": box_color, "borderSize": 1})
if show_bounds and n and float(np.sum(volume)) != 0.0:
    layers.append({"type": "zone", "role": "Trend Profile Bounds", "start": float(times[profile_start_index]), "end": right_time, "top": profile_high, "bottom": profile_low, "fillColor": "rgba(229,231,235,0.05)", "borderColor": "rgba(229,231,235,0.10)", "borderSize": 1})

tables = []
if n and float(np.sum(volume)) == 0.0 and not dismiss_no_volume:
    tables.append({"title": "Volume Warning", "position": "middle-center", "rows": [["Warning", "This symbol has no volume data"]]})


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


signals = [
    {"type": "buy", "name": "Strong Bullish Sweep", "text": "▲", "data": [float(low[i] - volatility[i]) if i in visible_sweep_indexes and bull_liquidation[i] != 0.0 and normalized_bull_liquidation[i] > major_threshold and np.isfinite(volatility[i]) else None for i in range(n)], "color": bullish_color, "renderMode": "events"},
    {"type": "sell", "name": "Strong Bearish Sweep", "text": "▼", "data": [float(high[i] + volatility[i]) if i in visible_sweep_indexes and bear_liquidation[i] != 0.0 and normalized_bear_liquidation[i] > major_threshold and np.isfinite(volatility[i]) else None for i in range(n)], "color": bearish_color, "renderMode": "events"},
    {"type": "buy", "name": "Bullish Sweep", "text": "△", "data": [float(low[i] - volatility[i]) if i in visible_sweep_indexes and bull_liquidation[i] != 0.0 and 0.0 < normalized_bull_liquidation[i] <= major_threshold and np.isfinite(volatility[i]) else None for i in range(n)], "color": bullish_color, "renderMode": "events"},
    {"type": "sell", "name": "Bearish Sweep", "text": "▽", "data": [float(high[i] + volatility[i]) if i in visible_sweep_indexes and bear_liquidation[i] != 0.0 and 0.0 < normalized_bear_liquidation[i] <= major_threshold and np.isfinite(volatility[i]) else None for i in range(n)], "color": bearish_color, "renderMode": "events"},
    {"type": "buy", "name": "Bullish Trend", "text": "▲", "data": [float(lower[i]) if trend_cross_up[i] and np.isfinite(lower[i]) else None for i in range(n)], "color": bullish_color, "renderMode": "events"},
    {"type": "sell", "name": "Bearish Trend", "text": "▼", "data": [float(upper[i]) if trend_cross_down[i] and np.isfinite(upper[i]) else None for i in range(n)], "color": bearish_color, "renderMode": "events"},
]

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Downtrend Line", "data": [float(upper[i]) if trend[i] < 0.0 and np.isfinite(upper[i]) else None for i in range(n)], "color": color_with_alpha(bearish_color, 0.70), "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Uptrend Line", "data": [float(lower[i]) if trend[i] > 0.0 and np.isfinite(lower[i]) else None for i in range(n)], "color": color_with_alpha(bullish_color, 0.70), "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Price Guide", "title": False, "data": finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": signals, "layers": layers, "tables": tables, "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "emaSource": finite(ema_source), "basis": finite(basis), "deviation": finite(deviation), "upper": finite(upper), "lower": finite(lower),
        "volumeLow": finite(volume_low), "volumeHigh": finite(volume_high), "normalizedVolume": finite(normalized_volume), "trend": trend.tolist(),
        "trendCrossUp": trend_cross_up.tolist(), "trendCrossDown": trend_cross_down.tolist(), "timer": finite(timer),
        "bullLiquidation": bull_liquidation.tolist(), "bearLiquidation": bear_liquidation.tolist(), "normalizedBullLiquidation": finite(normalized_bull_liquidation), "normalizedBearLiquidation": finite(normalized_bear_liquidation),
        "activePeaks": active_peaks, "activeValleys": active_valleys, "trueRange": true_range.tolist(), "atr": finite(atr),
        "profileStartIndex": int(profile_start_index), "profileHigh": profile_high if np.isfinite(profile_high) else None, "profileLow": profile_low if np.isfinite(profile_low) else None,
        "profileTops": profile_tops.tolist(), "profileBottoms": profile_bottoms.tolist(), "profileBins": profile_bins.tolist(), "barInterval": interval,
    },
}
