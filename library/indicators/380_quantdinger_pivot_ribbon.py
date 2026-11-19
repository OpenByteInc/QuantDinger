# ============================================================
# QuantDinger Pivot Ribbon
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Pivot Ribbon"
my_indicator_description = "Builds fast/pivot/slow EMA clouds, conviction crosses and optional candle bias on a selectable time-warp series."
indicator_timeframe_param = "time_warp"
indicator_timeframes = ["1m", "2m", "3m", "4m", "5m", "10m", "15m", "20m", "30m", "1H", "2H", "4H", "1D", "1W", "1M", "12M"]

# @param time_warp str off Calculation timeframe values=off,1m,2m,3m,4m,5m,10m,15m,20m,30m,1h,2h,4h,D,W,M,Y
# @param fast_ema int 8 Fast EMA length range=1:500:1
# @param pivot_ema int 21 Pivot EMA length range=1:500:1
# @param slow_ema int 34 Slow EMA length range=1:500:1
# @param cloud_transparency int 60 Cloud transparency range=0:100:1
# @param show_fast_highlight bool false Show fast EMA line
# @param show_pivot_highlight bool false Show pivot EMA line
# @param show_slow_highlight bool false Show slow EMA line
# @param show_conviction_arrows bool true Show conviction arrows
# @param show_fast_conviction_ema bool true Show fast conviction EMA
# @param fast_conviction_ema int 13 Fast conviction EMA length range=1:500:1
# @param show_slow_conviction_ema bool true Show slow conviction EMA
# @param slow_conviction_ema int 48 Slow conviction EMA length range=1:500:1
# @param show_candle_bias bool false Show candle bias colors
# @param bias_ema int 21 Bias EMA length range=1:500:1
# @param bullish_fast_color str #008000 Bullish fast cloud color
# @param bearish_fast_color str #FF0000 Bearish fast cloud color
# @param bullish_slow_color str #00FFFF Bullish slow cloud color
# @param bearish_slow_color str #FFA500 Bearish slow cloud color

time_warp = str(params.get("time_warp", "off"))
valid_time_warps = ("off", "1m", "2m", "3m", "4m", "5m", "10m", "15m", "20m", "30m", "1h", "2h", "4h", "D", "W", "M", "Y")
time_warp = time_warp if time_warp in valid_time_warps else "off"
fast_length = min(500, max(1, int(params.get("fast_ema", 8))))
pivot_length = min(500, max(1, int(params.get("pivot_ema", 21))))
slow_length = min(500, max(1, int(params.get("slow_ema", 34))))
cloud_transparency = min(100, max(0, int(params.get("cloud_transparency", 60))))
show_fast_highlight = bool(params.get("show_fast_highlight", False))
show_pivot_highlight = bool(params.get("show_pivot_highlight", False))
show_slow_highlight = bool(params.get("show_slow_highlight", False))
show_conviction_arrows = bool(params.get("show_conviction_arrows", True))
show_fast_conviction = bool(params.get("show_fast_conviction_ema", True))
fast_conviction_length = min(500, max(1, int(params.get("fast_conviction_ema", 13))))
show_slow_conviction = bool(params.get("show_slow_conviction_ema", True))
slow_conviction_length = min(500, max(1, int(params.get("slow_conviction_ema", 48))))
show_candle_bias = bool(params.get("show_candle_bias", False))
bias_length = min(500, max(1, int(params.get("bias_ema", 21))))
bullish_fast_color = str(params.get("bullish_fast_color", "#008000"))
bearish_fast_color = str(params.get("bearish_fast_color", "#FF0000"))
bullish_slow_color = str(params.get("bullish_slow_color", "#00FFFF"))
bearish_slow_color = str(params.get("bearish_slow_color", "#FFA500"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _normalize(frame):
    result = frame.copy().reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = np.nan if column == "volume" else 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
    times = result["time"].to_numpy(dtype=float)
    if len(times) and np.nanmax(np.abs(times)) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    state = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            state = float(value) if not np.isfinite(state) else state + alpha * (float(value) - state)
            result[index] = state
    return result


def _tf_key(value):
    text = str(value or "1H").strip()
    aliases = {"1": "1m", "2": "2m", "3": "3m", "4": "4m", "5": "5m", "10": "10m", "15": "15m", "20": "20m", "30": "30m", "60": "1H", "120": "2H", "240": "4H", "D": "1D", "W": "1W", "M": "1M", "12M": "12M"}
    return aliases.get(text, text)


timeframe_seconds = {"1m": 60, "2m": 120, "3m": 180, "4m": 240, "5m": 300, "10m": 600, "15m": 900, "20m": 1200, "30m": 1800, "1H": 3600, "2H": 7200, "4H": 14400, "1D": 86400, "1W": 604800, "1M": 2629800, "12M": 31557600}
warp_mapping = {"1h": "1H", "2h": "2H", "4h": "4H", "D": "1D", "W": "1W", "M": "1M", "Y": "12M"}

df = df.copy()
base_frame = _normalize(df)
base_time = base_frame["time"].to_numpy(dtype=float)
opened = base_frame["open"].to_numpy(dtype=float)
high = base_frame["high"].to_numpy(dtype=float)
low = base_frame["low"].to_numpy(dtype=float)
close = base_frame["close"].to_numpy(dtype=float)
n = len(base_frame)
runtime_ctx = globals().get("ctx")
chart_timeframe = _tf_key(getattr(runtime_ctx, "timeframe", None) or "1H")
chart_seconds = timeframe_seconds.get(chart_timeframe, 3600)
selected_timeframe = chart_timeframe if time_warp == "off" else warp_mapping.get(time_warp, time_warp)
selected_seconds = timeframe_seconds.get(selected_timeframe, chart_seconds)

source_frame = None
source_kind = "chart"
timeframe_available = True
if selected_timeframe == chart_timeframe:
    source_frame = base_frame
else:
    raw = None
    if runtime_ctx is not None and hasattr(runtime_ctx, "raw_series"):
        try:
            raw = runtime_ctx.raw_series(selected_timeframe)
        except Exception:
            raw = None
    if raw is not None and len(raw) > 0:
        source_frame = _normalize(raw)
        source_kind = "context"
    else:
        source_frame = base_frame
        source_kind = "chart-fallback"
        timeframe_available = False

source_time = source_frame["time"].to_numpy(dtype=float)
source_close = source_frame["close"].to_numpy(dtype=float)
remote = {
    "fast": _ema(source_close, fast_length), "pivot": _ema(source_close, pivot_length), "slow": _ema(source_close, slow_length),
    "fastConviction": _ema(source_close, fast_conviction_length), "slowConviction": _ema(source_close, slow_conviction_length), "bias": _ema(source_close, bias_length),
}


def _align(values):
    if source_kind == "chart" and len(values) == n:
        return np.asarray(values, dtype=float).copy()
    result = np.full(n, np.nan, dtype=float)
    if not timeframe_available:
        return np.asarray(values, dtype=float).copy() if len(values) == n else result
    source_end = np.r_[source_time[1:], source_time[-1] + selected_seconds] if len(source_time) else np.asarray([])
    for index in range(n):
        base_end = base_time[index] + chart_seconds
        if selected_seconds > chart_seconds:
            source_index = int(np.searchsorted(source_end, base_end, side="right") - 1)
        elif selected_seconds < chart_seconds:
            source_index = int(np.searchsorted(source_time, base_end, side="left") - 1)
        else:
            source_index = int(np.searchsorted(source_time, base_time[index], side="right") - 1)
        if 0 <= source_index < len(values) and np.isfinite(values[source_index]):
            result[index] = float(values[source_index])
    return result


fast_ema = _align(remote["fast"])
pivot_ema = _align(remote["pivot"])
slow_ema = _align(remote["slow"])
fast_conviction = _align(remote["fastConviction"])
slow_conviction = _align(remote["slowConviction"])
bias_ema = _align(remote["bias"])
bullish_conviction = np.isfinite(fast_conviction) & np.isfinite(slow_conviction) & (fast_conviction >= slow_conviction)
bearish_conviction = np.isfinite(fast_conviction) & np.isfinite(slow_conviction) & (fast_conviction < slow_conviction)
bullish_confirmed = np.zeros(n, dtype=bool)
bearish_confirmed = np.zeros(n, dtype=bool)
for index in range(1, n):
    bullish_confirmed[index] = bullish_conviction[index] and not bullish_conviction[index - 1]
    bearish_confirmed[index] = bearish_conviction[index] and not bearish_conviction[index - 1]

opacity = (100.0 - cloud_transparency) / 100.0
fast_cloud = [{"upper": float(max(fast_ema[index], pivot_ema[index])), "lower": float(min(fast_ema[index], pivot_ema[index])), "color": bullish_fast_color if fast_ema[index] >= pivot_ema[index] else bearish_fast_color, "opacity": opacity * 0.10} if np.all(np.isfinite([fast_ema[index], pivot_ema[index]])) else None for index in range(n)]
slow_cloud = [{"upper": float(max(pivot_ema[index], slow_ema[index])), "lower": float(min(pivot_ema[index], slow_ema[index])), "color": bullish_slow_color if pivot_ema[index] >= slow_ema[index] else bearish_slow_color, "opacity": opacity * 0.10} if np.all(np.isfinite([pivot_ema[index], slow_ema[index]])) else None for index in range(n)]

above_bias = np.isfinite(bias_ema) & (close >= bias_ema)
up_candle = opened < close
down_candle = opened > close
doji = opened == close
bar_colors = []
for index in range(n):
    color = None
    if show_candle_bias:
        color = bullish_fast_color if above_bias[index] and up_candle[index] else bearish_slow_color if not above_bias[index] and up_candle[index] else bullish_slow_color if above_bias[index] and down_candle[index] else bearish_fast_color if not above_bias[index] and down_candle[index] else "#808080" if doji[index] else None
    bar_colors.append(color)

tables = []
if not timeframe_available:
    tables = [{"title": "Time Warp", "position": "top-right", "compact": True, "rows": [[{"text": f"{selected_timeframe} data unavailable", "color": "#FFA500"}, {"text": "Chart-timeframe fallback"}]]}]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Fast Cloud", "title": False, "data": fast_cloud, "color": bullish_fast_color, "type": "cloud", "overlay": True},
        {"name": "Slow Cloud", "title": False, "data": slow_cloud, "color": bullish_slow_color, "type": "cloud", "overlay": True},
        {"name": "Fast EMA", "title": True, "data": _finite(fast_ema) if show_fast_highlight else [None] * n, "color": "#FFFFFF", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Pivot EMA", "title": True, "data": _finite(pivot_ema) if show_pivot_highlight else [None] * n, "color": "#FFFFFF", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Slow EMA", "title": True, "data": _finite(slow_ema) if show_slow_highlight else [None] * n, "color": "#FFFFFF", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Fast Conviction EMA", "title": True, "data": _finite(fast_conviction) if show_fast_conviction else [None] * n, "color": "#C0C0C0", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Slow Conviction EMA", "title": True, "data": _finite(slow_conviction) if show_slow_conviction else [None] * n, "color": "#800080", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Bullish Conviction", "text": "▲", "data": [{"active": True, "price": float(low[index]), "text": "▲", "color": bullish_slow_color} if show_conviction_arrows and bullish_confirmed[index] else None for index in range(n)], "color": bullish_slow_color, "renderMode": "events", "overlay": True},
        {"type": "sell", "name": "Bearish Conviction", "text": "▼", "data": [{"active": True, "price": float(high[index]), "text": "▼", "color": "#FFFF00"} if show_conviction_arrows and bearish_confirmed[index] else None for index in range(n)], "color": "#FFFF00", "renderMode": "events", "overlay": True},
    ],
    "layers": [],
    "tables": tables,
    "bar_colors": bar_colors,
    "background_colors": [None] * n,
    "calculatedVars": {
        "selectedTimeframe": selected_timeframe, "sourceKind": source_kind, "timeframeAvailable": bool(timeframe_available),
        "fastEma": _finite(fast_ema), "pivotEma": _finite(pivot_ema), "slowEma": _finite(slow_ema),
        "fastConvictionEma": _finite(fast_conviction), "slowConvictionEma": _finite(slow_conviction), "biasEma": _finite(bias_ema),
        "bullishConviction": bullish_conviction.tolist(), "bearishConviction": bearish_conviction.tolist(), "bullishConfirmed": bullish_confirmed.tolist(), "bearishConfirmed": bearish_confirmed.tolist(),
        "aboveBias": above_bias.tolist(), "upCandle": up_candle.tolist(), "downCandle": down_candle.tolist(), "doji": doji.tolist(),
    },
}
