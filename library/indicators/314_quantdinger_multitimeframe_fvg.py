# ============================================================
# QuantDinger Multitimeframe Fair Value Gap
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

import math

my_indicator_name = "QuantDinger Multitimeframe Fair Value Gap"
my_indicator_description = "Tracks body-qualified fair value gaps, mitigation and a filtered gap-direction trend on a selected timeframe."
indicator_timeframe_param = "timeframe"
indicator_timeframes = ["1m", "5m", "15m", "30m", "1H", "4H", "1D", "1W"]

# @param timeframe str Chart FVG timeframe values=Chart,1m,5m,15m,30m,1H,4H,1D,1W
# @param body_sensitivity float 1.0 Body sensitivity range=0:10:0.1
# @param mitigate_midpoint bool true Mitigate at midpoint
# @param show_active_table bool false Show active FVG table
# @param extend_bars int 10 Extend boxes range=1:200:1
# @param show_trend bool true Show FVG trend
# @param ripple_db float 10.0 Passband ripple range=0.1:100:0.1
# @param cutoff_frequency float 0.1 Cutoff frequency range=0.001:0.5:0.001


def _tf_key(value):
    text = str(value or "Chart").strip()
    aliases = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1H", "240": "4H", "D": "1D", "W": "1W"}
    normalized = aliases.get(text, text)
    return normalized if normalized in indicator_timeframes else "Chart"


selected_timeframe = _tf_key(params.get("timeframe", "Chart"))
body_sensitivity = min(10.0, max(0.0, float(params.get("body_sensitivity", 1.0))))
mitigate_midpoint = bool(params.get("mitigate_midpoint", True))
show_active_table = bool(params.get("show_active_table", False))
extend_bars = min(200, max(1, int(params.get("extend_bars", 10))))
show_trend = bool(params.get("show_trend", True))
ripple_db = min(100.0, max(0.1, float(params.get("ripple_db", 10.0))))
cutoff_frequency = min(0.5, max(0.001, float(params.get("cutoff_frequency", 0.1))))
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


df = df.copy()
base_frame = _normalize(df)
base_time = base_frame["time"].to_numpy(dtype=float)
base_open = base_frame["open"].to_numpy(dtype=float)
base_high = base_frame["high"].to_numpy(dtype=float)
base_low = base_frame["low"].to_numpy(dtype=float)
base_close = base_frame["close"].to_numpy(dtype=float)
n = len(base_frame)
runtime_ctx = globals().get("ctx")
chart_timeframe = _tf_key(str(getattr(runtime_ctx, "timeframe", "") or "1H"))
if chart_timeframe == "Chart":
    chart_timeframe = "1H"
chart_seconds = timeframe_seconds.get(chart_timeframe, 3600)

if selected_timeframe == "Chart" or selected_timeframe == chart_timeframe:
    source_frame = base_frame
    source_timeframe = chart_timeframe
    timeframe_available = True
    direct_source = True
else:
    raw_source = runtime_ctx.raw_series(selected_timeframe) if runtime_ctx is not None and hasattr(runtime_ctx, "raw_series") else None
    timeframe_available = raw_source is not None and len(raw_source) > 0
    source_frame = _normalize(raw_source) if timeframe_available else None
    source_timeframe = selected_timeframe
    direct_source = False
source_seconds = timeframe_seconds.get(source_timeframe, chart_seconds)


def _source_values(frame):
    if frame is None:
        return {}
    opened = frame["open"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    volume = frame["volume"].fillna(0.0).to_numpy(dtype=float)
    count = len(frame)
    high_two = np.full(count, np.nan, dtype=float)
    low_two = np.full(count, np.nan, dtype=float)
    if count > 2:
        high_two[2:] = high[:-2]
        low_two[2:] = low[:-2]
    body = np.abs(close - opened)
    average_body = np.full(count, np.nan, dtype=float)
    running_sum = 0.0
    running_count = 0
    for index, value in enumerate(body):
        if np.isfinite(value):
            running_sum += float(value)
            running_count += 1
        average_body[index] = running_sum / running_count if running_count else np.nan
    return {"open": opened, "high": high, "low": low, "close": close, "volume": volume, "high2": high_two, "low2": low_two, "body": body, "averageBody": average_body}


def _align_lookahead(values):
    result = np.full(n, np.nan, dtype=float)
    if not timeframe_available or source_frame is None:
        return result
    if direct_source and len(values) == n:
        return np.asarray(values, dtype=float).copy()
    source_time = source_frame["time"].to_numpy(dtype=float)
    for index in range(n):
        if source_seconds < chart_seconds:
            source_index = int(np.searchsorted(source_time, base_time[index], side="left"))
            if source_index >= len(source_time) or source_time[source_index] >= base_time[index] + chart_seconds:
                continue
        else:
            source_index = int(np.searchsorted(source_time, base_time[index], side="right") - 1)
        if 0 <= source_index < len(values) and np.isfinite(values[source_index]):
            result[index] = float(values[source_index])
    return result


raw_values = _source_values(source_frame)
aligned = {key: _align_lookahead(values) for key, values in raw_values.items()} if raw_values else {key: np.full(n, np.nan) for key in ("open", "high", "low", "close", "volume", "high2", "low2", "body", "averageBody")}


def _bars_back(value, series, index):
    if not np.isfinite(value):
        return float(base_time[index])
    lower = max(0, index - 4999)
    for search_index in range(index, lower - 1, -1):
        if np.isfinite(series[search_index]) and abs(float(value) - float(series[search_index])) <= 1e-10:
            return float(base_time[search_index])
    return float(base_time[index])


def _volume_power(volume_value, close_value, low_value, high_value, buy_side):
    spread = high_value - low_value
    if not np.all(np.isfinite([volume_value, close_value, low_value, high_value])) or abs(spread) <= 1e-12 or abs(volume_value) <= 1e-12:
        return 0.0
    ratio = (close_value - low_value) / spread if buy_side else (high_value - close_value) / spread
    return float(round(round(volume_value * ratio) / volume_value * 100.0))


active_bull = []
active_bear = []
created_gaps = []
trend_seed = np.zeros(n, dtype=float)
bull_event = np.zeros(n, dtype=bool)
bear_event = np.zeros(n, dtype=bool)
previous_bull_condition = False
previous_bear_condition = False
previous_trend = 0.0
for index in range(n):
    values = [aligned[key][index] for key in ("low", "low2", "high", "high2", "close", "body", "averageBody")]
    if np.all(np.isfinite(values)):
        low_now, low_two, high_now, high_two, close_now, body_now, average_body = values
        bull_condition = low_now > high_two and close_now > high_two
        bear_condition = high_now < low_two and close_now < low_two
        bull_event[index] = bull_condition and not previous_bull_condition and body_now >= average_body * body_sensitivity
        bear_event[index] = bear_condition and not previous_bear_condition and body_now >= average_body * body_sensitivity
        previous_bull_condition = bool(bull_condition)
        previous_bear_condition = bool(bear_condition)
    if bull_event[index] or bear_event[index]:
        chart_ohlc4 = (base_open[index] + base_high[index] + base_low[index] + base_close[index]) / 4.0
        previous_trend = (chart_ohlc4 + previous_trend) / 2.0
    trend_seed[index] = previous_trend
    if bull_event[index]:
        top, bottom = float(aligned["low"][index]), float(aligned["high2"][index])
        left = _bars_back(aligned["high2"][index], base_high, index)
        anchor = _bars_back(aligned["high"][index], base_high, index)
        end = anchor + extend_bars * chart_seconds
        buy = _volume_power(aligned["volume"][index], aligned["close"][index], aligned["low"][index], aligned["high"][index], True)
        gap = {"side": "bull", "top": top, "bottom": bottom, "mid": (top + bottom) / 2.0, "start": left, "end": end, "buy": buy, "sell": 100.0 - buy, "created": index}
        active_bull.insert(0, gap)
        created_gaps.append(gap.copy())
    if bear_event[index]:
        top, bottom = float(aligned["low2"][index]), float(aligned["high"][index])
        left = _bars_back(aligned["low2"][index], base_low, index)
        anchor = _bars_back(aligned["low"][index], base_low, index)
        end = anchor + extend_bars * chart_seconds
        buy = _volume_power(aligned["volume"][index], aligned["close"][index], aligned["low"][index], aligned["high"][index], True)
        gap = {"side": "bear", "top": top, "bottom": bottom, "mid": (top + bottom) / 2.0, "start": left, "end": end, "buy": buy, "sell": 100.0 - buy, "created": index}
        active_bear.insert(0, gap)
        created_gaps.append(gap.copy())
    active_bull = [gap for gap in active_bull if not (base_low[index] <= (gap["mid"] if mitigate_midpoint else gap["bottom"]))]
    active_bear = [gap for gap in active_bear if not (base_high[index] >= (gap["mid"] if mitigate_midpoint else gap["top"]))]
    active_bull = active_bull[:120]
    active_bear = active_bear[:120]

epsilon = math.sqrt(10.0 ** (ripple_db / 10.0) - 1.0)
damping = math.sqrt(1.0 + epsilon * epsilon)
coefficient_c = 1.0 / math.tan(math.pi * cutoff_frequency)
normalizer = 1.0 / (1.0 + damping * coefficient_c + coefficient_c * coefficient_c)
b0, b1, b2 = normalizer, 2.0 * normalizer, normalizer
a1 = 2.0 * normalizer * (1.0 - coefficient_c * coefficient_c)
a2 = normalizer * (1.0 - damping * coefficient_c + coefficient_c * coefficient_c)
trend_source = (aligned["high"] + aligned["low"] + trend_seed) / 3.0
trend_filter = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index < 2:
        trend_filter[index] = trend_source[index]
    elif np.all(np.isfinite([trend_source[index], trend_source[index - 1], trend_source[index - 2]])):
        previous_one = trend_filter[index - 1] if np.isfinite(trend_filter[index - 1]) else 0.0
        previous_two = trend_filter[index - 2] if np.isfinite(trend_filter[index - 2]) else 0.0
        trend_filter[index] = b0 * trend_source[index] + b1 * trend_source[index - 1] + b2 * trend_source[index - 2] - a1 * previous_one - a2 * previous_two


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


visual_average = _ema(trend_seed, 10)
trend_colors = []
previous_color = "#808080"
for index, value in enumerate(trend_seed):
    if index >= 1 and value > trend_seed[index - 1]:
        previous_color = "#00FF00"
    elif index >= 1 and value < trend_seed[index - 1]:
        previous_color = "#FF0000"
    trend_colors.append(previous_color)
trend_cloud = []
for index in range(n):
    if show_trend and np.all(np.isfinite([trend_filter[index], visual_average[index]])):
        trend_cloud.append({"upper": float(max(trend_filter[index], visual_average[index])), "lower": float(min(trend_filter[index], visual_average[index])), "color": "rgba(0,255,0,0.30)" if trend_colors[index] == "#00FF00" else "rgba(255,0,0,0.30)" if trend_colors[index] == "#FF0000" else "rgba(128,128,128,0.30)"})
    else:
        trend_cloud.append(None)

layers = []
for gap in list(reversed(active_bull + active_bear))[-120:]:
    start_ms, end_ms = gap["start"] * 1000.0, gap["end"] * 1000.0
    duration = max(0.0, end_ms - start_ms)
    layers.append({"type": "zone", "role": f"{gap['side']} FVG", "start": start_ms, "end": end_ms, "top": gap["top"], "bottom": gap["bottom"], "fillColor": "rgba(128,128,128,0.15)", "borderColor": "rgba(128,128,128,0.15)"})
    layers.append({"type": "line", "role": f"{gap['side']} midpoint", "start": start_ms, "end": end_ms, "y1": gap["mid"], "y2": gap["mid"], "color": "#FFFFFF", "dashed": True, "lineWidth": 1})
    layers.append({"type": "zone", "role": f"{gap['side']} buy volume", "start": start_ms, "end": start_ms + duration * gap["buy"] / 100.0, "top": gap["top"], "bottom": gap["mid"], "fillColor": "rgba(0,255,187,0.50)", "borderColor": "rgba(0,255,187,0.50)"})
    layers.append({"type": "zone", "role": f"{gap['side']} sell volume", "start": start_ms, "end": start_ms + duration * gap["sell"] / 100.0, "top": gap["mid"], "bottom": gap["bottom"], "fillColor": "rgba(255,17,0,0.50)", "borderColor": "rgba(255,17,0,0.50)"})

tables = []
if show_active_table:
    rows = []
    for gap in (active_bull + active_bear)[:20]:
        rows.append([{"text": gap["side"].upper(), "color": "#14B8A6" if gap["side"] == "bull" else "#EF4444"}, {"text": f"{gap['bottom']:.6g} – {gap['top']:.6g}", "color": "#FFFFFF"}, {"text": f"B {gap['buy']:.0f}%", "color": "#FFFFFF"}])
    tables = [{"title": "Active FVG", "position": "top-right", "compact": True, "columns": [{"text": "Side"}, {"text": "Range"}, {"text": "Volume"}], "rows": rows or [[{"text": "—"}, {"text": "No active gaps"}, {"text": ""}]]}]


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "FVG Trend Filter", "data": _finite(trend_filter) if show_trend else [None] * n, "color": trend_colors, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Visual Trend Average", "title": False, "data": _finite(visual_average), "color": "rgba(0,0,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "FVG Trend Fill", "title": False, "data": trend_cloud, "color": "#808080", "type": "cloud", "overlay": True},
    ],
    "signals": [],
    "layers": layers,
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "selectedTimeframe": selected_timeframe, "timeframeAvailable": bool(timeframe_available), "lookaheadMode": True,
        "alignedHigh": _finite(aligned["high"]), "alignedLow": _finite(aligned["low"]), "alignedHigh2": _finite(aligned["high2"]), "alignedLow2": _finite(aligned["low2"]),
        "body": _finite(aligned["body"]), "averageBody": _finite(aligned["averageBody"]), "bullEvent": bull_event.tolist(), "bearEvent": bear_event.tolist(),
        "trendSeed": _finite(trend_seed), "trendSource": _finite(trend_source), "trendFilter": _finite(trend_filter), "visualAverage": _finite(visual_average),
        "activeBullGaps": active_bull, "activeBearGaps": active_bear, "createdGaps": created_gaps,
        "filterCoefficients": [b0, b1, b2, a1, a2],
    },
}
