# ============================================================
# QuantDinger Dual-Timeframe Volume-Accelerated RSI
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Dual-Timeframe Volume-Accelerated RSI"
my_indicator_description = "Compares a volume-accelerated RSI on the chart timeframe with a live or last-closed locally aggregated context timeframe."

# @param rsi_length int 14 RSI length range=1:10000:1
# @param averaging str RMA RSI averaging values=EMA,HMA,RMA,SMA,WMA
# @param volume_weighted bool true Weight price changes by volume
# @param smoothing_length int 3 Current RSI smoothing range=1:10000:1
# @param smoothing_type str WMA Smoothing average values=EMA,HMA,RMA,SMA,WMA
# @param signal_length int 5 Signal length range=1:10000:1
# @param signal_type str EMA Signal average values=EMA,HMA,RMA,SMA,WMA
# @param show_context bool true Show context RSI
# @param context_multiplier int 5 Context timeframe multiplier range=2:60:1
# @param context_update str Live Context update values=Live,Last-Closed
# @param show_alignment bool true Show direction alignment markers
# @param current_bull_color str #00FFFF Current bullish color
# @param current_bear_color str #F57F17 Current bearish color
# @param context_bull_color str #33AA00 Context bullish color
# @param context_bear_color str #FF1111 Context bearish color
# @param signal_color str #2962FF Signal color

rsi_length = min(10000, max(1, int(params.get("rsi_length", 14))))
averaging = str(params.get("averaging", "RMA"))
volume_weighted = bool(params.get("volume_weighted", True))
smoothing_length = min(10000, max(1, int(params.get("smoothing_length", 3))))
smoothing_type = str(params.get("smoothing_type", "WMA"))
signal_length = min(10000, max(1, int(params.get("signal_length", 5))))
signal_type = str(params.get("signal_type", "EMA"))
show_context = bool(params.get("show_context", True))
context_multiplier = min(60, max(2, int(params.get("context_multiplier", 5))))
context_update = str(params.get("context_update", "Live")).replace("_", "-")
if context_update not in ("Live", "Last-Closed"):
    context_update = "Live"
show_alignment = bool(params.get("show_alignment", True))
current_bull_color = str(params.get("current_bull_color", "#00FFFF"))
current_bear_color = str(params.get("current_bear_color", "#F57F17"))
context_bull_color = str(params.get("context_bull_color", "#33AA00"))
context_bear_color = str(params.get("context_bear_color", "#FF1111"))
signal_color = str(params.get("signal_color", "#2962FF"))
valid_average_types = ("EMA", "HMA", "RMA", "SMA", "WMA")
averaging = averaging if averaging in valid_average_types else "RMA"
smoothing_type = smoothing_type if smoothing_type in valid_average_types else "WMA"
signal_type = signal_type if signal_type in valid_average_types else "EMA"


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
        if not np.isfinite(value):
            continue
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


def _ma(values, length, average_type):
    if average_type == "SMA":
        return _sma(values, length)
    if average_type == "EMA":
        return _ema(values, length)
    if average_type == "RMA":
        return _rma(values, length)
    if average_type == "WMA":
        return _wma(values, length)
    half = max(1, length // 2)
    root = max(1, int(np.sqrt(length)))
    return _wma(2.0 * _wma(values, half) - _wma(values, length), root)


def _volume_rsi(close_values, volume_values, length, average_type, weighted):
    change = np.full(len(close_values), np.nan, dtype=float)
    if len(close_values) > 1:
        weights = np.where(np.isfinite(volume_values[1:]), volume_values[1:], 1.0) if weighted else np.ones(len(close_values) - 1)
        change[1:] = np.diff(close_values) * weights
    upward = _ma(np.maximum(change, 0.0), length, average_type)
    downward = _ma(np.maximum(-change, 0.0), length, average_type)
    result = np.full(len(close_values), np.nan, dtype=float)
    for index in range(len(close_values)):
        if not np.all(np.isfinite([upward[index], downward[index]])):
            continue
        result[index] = 100.0 if downward[index] == 0.0 else 0.0 if upward[index] == 0.0 else 100.0 - 100.0 / (1.0 + upward[index] / downward[index])
    return result


def _timeframe_seconds(value):
    text = str(value or "1H").strip()
    aliases = {"1": 60, "3": 180, "5": 300, "15": 900, "30": 1800, "60": 3600, "120": 7200, "240": 14400, "D": 86400, "W": 604800}
    if text in aliases:
        return aliases[text]
    if text.endswith("m"):
        try:
            return int(float(text[:-1]) * 60)
        except ValueError:
            return 3600
    upper = text.upper()
    try:
        if upper.endswith("H"):
            return int(float(upper[:-1]) * 3600)
        if upper.endswith("D"):
            return int(float(upper[:-1]) * 86400)
        if upper.endswith("W"):
            return int(float(upper[:-1]) * 604800)
    except ValueError:
        pass
    return 3600


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
times = df["time"].to_numpy(dtype=float)
if len(times) and np.nanmax(np.abs(times)) > 10_000_000_000:
    times = times / 1000.0
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
n = len(df)

current_raw = _volume_rsi(close, volume, rsi_length, averaging, volume_weighted)
current_rsi = _ma(current_raw, smoothing_length, smoothing_type) if smoothing_length > 1 else current_raw.copy()
signal_rsi = _ma(current_rsi, signal_length, signal_type) if signal_length > 1 else current_rsi.copy()

runtime_ctx = globals().get("ctx")
chart_seconds = _timeframe_seconds(getattr(runtime_ctx, "timeframe", None) or "1H")
context_seconds = chart_seconds * context_multiplier
context_live = np.full(n, np.nan, dtype=float)
context_closed = np.full(n, np.nan, dtype=float)
bucket_closes = []
bucket_volumes = []
current_bucket = None
current_bucket_volume = 0.0
current_bucket_has_volume = False
last_closed_value = np.nan
for index in range(n):
    bucket = int(np.floor(times[index] / context_seconds)) if context_seconds else index
    if current_bucket is None or bucket != current_bucket:
        if current_bucket is not None:
            bucket_closes.append(previous_close)
            bucket_volumes.append(current_bucket_volume if current_bucket_has_volume else np.nan)
        current_bucket = bucket
        current_bucket_volume = 0.0
        current_bucket_has_volume = False
    if np.isfinite(volume[index]):
        current_bucket_volume += float(volume[index])
        current_bucket_has_volume = True
    previous_close = float(close[index])
    partial_close = np.asarray(bucket_closes + [previous_close], dtype=float)
    partial_volume = np.asarray(bucket_volumes + [current_bucket_volume if current_bucket_has_volume else np.nan], dtype=float)
    partial_rsi = _volume_rsi(partial_close, partial_volume, rsi_length, averaging, volume_weighted)
    if len(partial_rsi) and np.isfinite(partial_rsi[-1]):
        context_live[index] = partial_rsi[-1]
    bar_closes_bucket = times[index] + chart_seconds >= (bucket + 1) * context_seconds - max(1.0, chart_seconds * 0.001)
    if bar_closes_bucket and np.isfinite(context_live[index]):
        last_closed_value = context_live[index]
    context_closed[index] = last_closed_value

if not show_context:
    context_rsi = np.full(n, np.nan, dtype=float)
elif context_update == "Last-Closed":
    context_rsi = context_closed
else:
    context_rsi = context_live

current_up = current_rsi > 50.0
context_up = context_rsi > 50.0
aligned_up = show_context & current_up & context_up
aligned_down = show_context & (~current_up) & (~context_up) & np.isfinite(current_rsi) & np.isfinite(context_rsi)
bull_alignment = aligned_up & np.r_[True, ~aligned_up[:-1]]
bear_alignment = aligned_down & np.r_[True, ~aligned_down[:-1]]


def _cross_above(values, level):
    return np.asarray([index > 0 and np.all(np.isfinite([values[index], values[index - 1]])) and values[index] > level and values[index - 1] <= level for index in range(len(values))], dtype=bool)


def _cross_below(values, level):
    return np.asarray([index > 0 and np.all(np.isfinite([values[index], values[index - 1]])) and values[index] < level and values[index - 1] >= level for index in range(len(values))], dtype=bool)


mid_up = _cross_above(current_rsi, 50.0)
mid_down = _cross_below(current_rsi, 50.0)
overbought_entry = _cross_above(current_rsi, 70.0)
oversold_entry = _cross_below(current_rsi, 30.0)
current_colors = [current_bull_color if value else current_bear_color for value in current_up]
context_colors = [context_bull_color if value else context_bear_color for value in context_up]
context_cloud = []
for value in context_rsi:
    if not np.isfinite(value):
        context_cloud.append(None)
    elif value >= 50.0:
        context_cloud.append({"upper": float(value), "lower": 50.0, "color": context_bull_color, "opacity": 0.35})
    else:
        context_cloud.append({"upper": 50.0, "lower": float(value), "color": context_bear_color, "opacity": 0.35})


def _events(mask, price_values, text, color, event_type):
    return {"type": event_type, "name": text, "data": [{"active": True, "price": float(price_values[index]), "text": text, "color": color} if active and np.isfinite(price_values[index]) else None for index, active in enumerate(mask)], "color": color, "renderMode": "events"}


def _limited_event_masks(masks, recent_bars=96, total_cap=10, minimum_gap=5):
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


display_bull_alignment, display_bear_alignment, display_mid_up, display_mid_down, display_overbought, display_oversold = _limited_event_masks([
    bull_alignment if show_alignment else np.zeros(n, bool),
    bear_alignment if show_alignment else np.zeros(n, bool),
    mid_up,
    mid_down,
    overbought_entry,
    oversold_entry,
])


context_label = f"{context_seconds // 3600}H" if context_seconds % 3600 == 0 and context_seconds < 86400 else f"{context_seconds // 86400}D" if context_seconds % 86400 == 0 else f"{context_seconds // 60}m"
output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Current RSI", "data": _finite(current_rsi), "color": current_colors, "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "RSI Signal", "data": _finite(signal_rsi), "color": signal_color, "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Context RSI", "data": _finite(context_rsi), "color": context_colors, "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Context Sentiment", "title": False, "data": context_cloud, "color": context_bull_color, "type": "cloud", "overlay": False},
        {"name": "Midline", "data": [50.0] * n, "color": "rgba(255,235,59,0.50)", "type": "line", "lineStyle": "dotted", "overlay": False},
        {"name": "Overbought", "data": [70.0] * n, "color": "rgba(8,153,129,0.70)", "type": "line", "lineStyle": "dashed", "overlay": False},
        {"name": "Oversold", "data": [30.0] * n, "color": "rgba(242,54,69,0.70)", "type": "line", "lineStyle": "dashed", "overlay": False},
    ],
    "signals": [
        _events(display_bull_alignment, np.zeros(n), "Bull Alignment", context_bull_color, "buy"),
        _events(display_bear_alignment, np.full(n, 100.0), "Bear Alignment", context_bear_color, "sell"),
        _events(display_mid_up, current_rsi, "RSI Above 50", current_bull_color, "buy"),
        _events(display_mid_down, current_rsi, "RSI Below 50", current_bear_color, "sell"),
        _events(display_overbought, current_rsi, "Overbought", current_bull_color, "buy"),
        _events(display_oversold, current_rsi, "Oversold", current_bear_color, "sell"),
    ],
    "layers": [],
    "tables": [{"title": "Context", "position": "top-right", "compact": True, "columns": [{"text": "Timeframe"}, {"text": "Update"}], "rows": [[{"text": context_label}, {"text": context_update}]]}],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "currentRaw": _finite(current_raw), "currentRsi": _finite(current_rsi), "signalRsi": _finite(signal_rsi),
        "contextLive": _finite(context_live), "contextClosed": _finite(context_closed), "contextRsi": _finite(context_rsi),
        "currentUp": current_up.tolist(), "contextUp": context_up.tolist(), "alignedUp": aligned_up.tolist(), "alignedDown": aligned_down.tolist(),
        "bullAlignment": bull_alignment.tolist(), "bearAlignment": bear_alignment.tolist(), "midUp": mid_up.tolist(), "midDown": mid_down.tolist(),
        "overboughtEntry": overbought_entry.tolist(), "oversoldEntry": oversold_entry.tolist(),
        "chartSeconds": int(chart_seconds), "contextSeconds": int(context_seconds), "contextLabel": context_label,
    },
}
