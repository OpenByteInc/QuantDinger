# ============================================================
# QuantDinger MTF Trend Dashboard
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger MTF Trend Dashboard"
my_indicator_description = "Combines EMA and RSI trend states across five same-symbol timeframes."
indicator_timeframes = ["1m", "5m", "15m", "30m", "1H", "4H", "1D", "1W"]

# @param fast_length int 20 Fast EMA range=1:500:1
# @param slow_length int 50 Slow EMA range=1:1000:1
# @param rsi_length int 14 RSI length range=1:500:1
# @param show_signals bool true Show EMA cross signals
# @param timeframe_1 str 5m Timeframe 1 values=1m,5m,15m,30m,1H,4H,1D,1W
# @param timeframe_2 str 15m Timeframe 2 values=1m,5m,15m,30m,1H,4H,1D,1W
# @param timeframe_3 str 1H Timeframe 3 values=1m,5m,15m,30m,1H,4H,1D,1W
# @param timeframe_4 str 4H Timeframe 4 values=1m,5m,15m,30m,1H,4H,1D,1W
# @param timeframe_5 str 1D Timeframe 5 values=1m,5m,15m,30m,1H,4H,1D,1W
# @param table_position str Top_Right Dashboard position values=Top_Right,Top_Left,Bottom_Right,Bottom_Left
# @param table_size str Small Text size values=Tiny,Small,Normal

fast_length = min(500, max(1, int(params.get("fast_length", 20))))
slow_length = min(1000, max(1, int(params.get("slow_length", 50))))
rsi_length = min(500, max(1, int(params.get("rsi_length", 14))))
show_signals = bool(params.get("show_signals", True))


def _timeframe(value, fallback):
    text = str(value if value is not None else fallback).strip()
    aliases = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1H", "240": "4H", "D": "1D", "W": "1W"}
    normalized = aliases.get(text, text)
    return normalized if normalized in indicator_timeframes else fallback


selected_timeframes = [
    _timeframe(params.get("timeframe_1", "5m"), "5m"),
    _timeframe(params.get("timeframe_2", "15m"), "15m"),
    _timeframe(params.get("timeframe_3", "1H"), "1H"),
    _timeframe(params.get("timeframe_4", "4H"), "4H"),
    _timeframe(params.get("timeframe_5", "1D"), "1D"),
]
table_position_name = str(params.get("table_position", "Top_Right"))
position_map = {"Top_Right": "top-right", "Top_Left": "top-left", "Bottom_Right": "bottom-right", "Bottom_Left": "bottom-left", "Top Right": "top-right", "Top Left": "top-left", "Bottom Right": "bottom-right", "Bottom Left": "bottom-left"}
table_position = position_map.get(table_position_name, "top-right")
table_size = str(params.get("table_size", "Small"))
if table_size not in ("Tiny", "Small", "Normal"):
    table_size = "Small"

timeframe_seconds = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}


def _normalize(frame):
    result = frame.copy().reset_index(drop=True)
    if "time" not in result.columns:
        result["time"] = np.arange(len(result), dtype=float) * 60.0
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
    times = result["time"].to_numpy(dtype=float)
    if len(times) and max(abs(float(value)) for value in times) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _rma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    finite_indices = np.flatnonzero(np.isfinite(values))
    if len(finite_indices) < period:
        return result
    seed_indices = finite_indices[:period]
    seed_index = int(seed_indices[-1])
    previous = float(np.mean(values[seed_indices]))
    result[seed_index] = previous
    for index in range(seed_index + 1, len(values)):
        if np.isfinite(values[index]):
            previous = (previous * (period - 1) + float(values[index])) / period
            result[index] = previous
    return result


def _rsi(values, period):
    gains = np.full(len(values), np.nan, dtype=float)
    losses = np.full(len(values), np.nan, dtype=float)
    for index in range(1, len(values)):
        change = float(values[index] - values[index - 1])
        gains[index], losses[index] = max(change, 0.0), max(-change, 0.0)
    average_gain = _rma(gains, period)
    average_loss = _rma(losses, period)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        if np.all(np.isfinite([average_gain[index], average_loss[index]])):
            result[index] = 100.0 if average_loss[index] == 0.0 else 0.0 if average_gain[index] == 0.0 else 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])
    return result


def _trend(frame):
    close_values = frame["close"].to_numpy(dtype=float)
    fast_values = _ema(close_values, fast_length)
    slow_values = _ema(close_values, slow_length)
    rsi_values = _rsi(close_values, rsi_length)
    states = np.zeros(len(frame), dtype=int)
    for index in range(len(frame)):
        if not np.all(np.isfinite([fast_values[index], slow_values[index], rsi_values[index]])):
            continue
        if close_values[index] > fast_values[index] and fast_values[index] > slow_values[index] and rsi_values[index] > 50.0:
            states[index] = 1
        elif close_values[index] < fast_values[index] and fast_values[index] < slow_values[index] and rsi_values[index] < 50.0:
            states[index] = -1
    return {"fast": fast_values, "slow": slow_values, "rsi": rsi_values, "state": states}


df = df.copy()
base_frame = _normalize(df)
base_time = base_frame["time"].to_numpy(dtype=float)
n = len(base_frame)
runtime_ctx = globals().get("ctx")
chart_timeframe = _timeframe(str(getattr(runtime_ctx, "timeframe", "") or "1H"), "1H")
chart_seconds = timeframe_seconds.get(chart_timeframe, 3600)
base_values = _trend(base_frame)


def _requested(timeframe):
    if timeframe == chart_timeframe:
        return base_frame, chart_seconds, True, True
    raw = runtime_ctx.raw_series(timeframe) if runtime_ctx is not None and hasattr(runtime_ctx, "raw_series") else None
    if raw is None or len(raw) == 0:
        return None, timeframe_seconds.get(timeframe, chart_seconds), False, False
    return _normalize(raw), timeframe_seconds.get(timeframe, chart_seconds), False, True


def _align_completed(source_frame, values, source_seconds, direct, integer=False):
    fill_value = 0 if integer else np.nan
    dtype = int if integer else float
    result = np.full(n, fill_value, dtype=dtype)
    if direct and len(values) == n:
        return np.asarray(values, dtype=dtype).copy()
    if source_frame is None or len(source_frame) == 0:
        return result
    source_time = source_frame["time"].to_numpy(dtype=float)
    source_close = source_time + source_seconds
    for index in range(n):
        current_close = base_time[index] + chart_seconds
        source_index = int(np.searchsorted(source_close, current_close, side="right") - 1)
        if 0 <= source_index < len(values):
            value = values[source_index]
            if integer or np.isfinite(value):
                result[index] = int(value) if integer else float(value)
    return result


timeframe_results = []
for timeframe in selected_timeframes:
    requested_frame, requested_seconds, direct, available = _requested(timeframe)
    raw_values = _trend(requested_frame) if available else {"rsi": np.array([], dtype=float), "state": np.array([], dtype=int)}
    timeframe_results.append({
        "timeframe": timeframe,
        "available": available,
        "state": _align_completed(requested_frame, raw_values["state"], requested_seconds, direct, integer=True),
        "rsi": _align_completed(requested_frame, raw_values["rsi"], requested_seconds, direct),
    })

score = np.zeros(n, dtype=int)
for item in timeframe_results:
    score += item["state"]
all_bull = score == 5
all_bear = score == -5
all_bull_event = np.zeros(n, dtype=bool)
all_bear_event = np.zeros(n, dtype=bool)
for index in range(n):
    all_bull_event[index] = bool(all_bull[index] and (index == 0 or not all_bull[index - 1]))
    all_bear_event[index] = bool(all_bear[index] and (index == 0 or not all_bear[index - 1]))

fast_ema = base_values["fast"]
slow_ema = base_values["slow"]
trend_state = base_values["state"]
bull_cross = np.zeros(n, dtype=bool)
bear_cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.all(np.isfinite([fast_ema[index], slow_ema[index], fast_ema[index - 1], slow_ema[index - 1]])):
        bull_cross[index] = fast_ema[index] > slow_ema[index] and fast_ema[index - 1] <= slow_ema[index - 1]
        bear_cross[index] = fast_ema[index] < slow_ema[index] and fast_ema[index - 1] >= slow_ema[index - 1]

trend_colors = ["#008080" if state == 1 else "#EF4444" if state == -1 else "#808080" for state in trend_state]
slow_colors = ["rgba(0,128,128,0.60)" if state == 1 else "rgba(239,68,68,0.60)" if state == -1 else "rgba(128,128,128,0.60)" for state in trend_state]
cloud = []
for index in range(n):
    if np.all(np.isfinite([fast_ema[index], slow_ema[index]])):
        cloud.append({"upper": float(max(fast_ema[index], slow_ema[index])), "lower": float(min(fast_ema[index], slow_ema[index])), "color": "rgba(0,128,128,0.15)" if trend_state[index] == 1 else "rgba(239,68,68,0.15)" if trend_state[index] == -1 else "rgba(128,128,128,0.15)"})
    else:
        cloud.append(None)


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


signals = []
if show_signals:
    low_values = base_frame["low"].to_numpy(dtype=float)
    high_values = base_frame["high"].to_numpy(dtype=float)
    signals = [
        {"type": "buy", "name": "Bullish Cross", "text": "▲", "data": [float(low_values[index]) if bull_cross[index] else None for index in range(n)], "color": "#008080", "renderMode": "events"},
        {"type": "sell", "name": "Bearish Cross", "text": "▼", "data": [float(high_values[index]) if bear_cross[index] else None for index in range(n)], "color": "#EF4444", "renderMode": "events"},
    ]

rows = []
for item in timeframe_results:
    if n and item["available"]:
        state = int(item["state"][-1])
        state_text = "▲ Bull" if state == 1 else "▼ Bear" if state == -1 else "• Neutral"
        state_color = "#14B8A6" if state == 1 else "#EF4444" if state == -1 else "#9CA3AF"
        rsi_text = f"{item['rsi'][-1]:.1f}" if np.isfinite(item["rsi"][-1]) else "-"
    else:
        state_text, state_color, rsi_text = "Unavailable", "#9CA3AF", "-"
    rows.append([
        {"text": item["timeframe"], "color": "#FFFFFF"},
        {"text": state_text, "color": state_color},
        {"text": rsi_text, "color": "#FFFFFF"},
    ])
latest_score = int(score[-1]) if n else 0
bias = "Bullish bias" if latest_score >= 3 else "Bearish bias" if latest_score <= -3 else "Mixed"
bias_color = "#14B8A6" if latest_score >= 3 else "#EF4444" if latest_score <= -3 else "#9CA3AF"
rows.append([
    {"text": f"Score {latest_score}/5", "color": "#FFFFFF"},
    {"text": bias, "color": bias_color},
    {"text": "", "color": "#FFFFFF"},
])

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Fast EMA", "data": _finite(fast_ema), "color": trend_colors, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Slow EMA", "data": _finite(slow_ema), "color": slow_colors, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "EMA Cloud", "title": False, "data": cloud, "color": "#008080", "type": "cloud", "overlay": True},
    ],
    "signals": signals,
    "layers": [],
    "tables": [{"title": "MTF Trend", "position": table_position, "compact": table_size != "Normal", "columns": [{"text": "TF", "color": "#C0C0C0"}, {"text": "Trend", "color": "#C0C0C0"}, {"text": "RSI", "color": "#C0C0C0"}], "rows": rows}],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "fastEMA": _finite(fast_ema), "slowEMA": _finite(slow_ema), "rsi": _finite(base_values["rsi"]), "trendState": trend_state.tolist(),
        "bullCross": bull_cross.tolist(), "bearCross": bear_cross.tolist(), "score": score.tolist(),
        "allBull": all_bull.tolist(), "allBear": all_bear.tolist(), "allBullEvent": all_bull_event.tolist(), "allBearEvent": all_bear_event.tolist(),
        "selectedTimeframes": selected_timeframes, "timeframeAvailable": [bool(item["available"]) for item in timeframe_results],
        "timeframeStates": [item["state"].tolist() for item in timeframe_results], "timeframeRsi": [_finite(item["rsi"]) for item in timeframe_results],
    },
}
