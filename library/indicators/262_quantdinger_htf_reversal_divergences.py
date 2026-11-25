# ============================================================
# QuantDinger HTF Reversal Divergences
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger HTF Reversal Divergences"
my_indicator_description = (
    "Combines completed higher-timeframe reversal candles, RSI divergences, "
    "and optional projected higher-timeframe candle tracking."
)

# @param htf str 15m Higher timeframe values=15m,30m,1H,2H,4H,6H,8H,12H,1D,1W
# @param show_engulfing bool true Show engulfing patterns
# @param show_pin_bars bool true Show hammer and shooting-star patterns
# @param bull_pattern_color color #089981 Bullish pattern color
# @param bear_pattern_color color #F23645 Bearish pattern color
# @param show_divergences bool true Show RSI divergences
# @param rsi_length int 14 RSI length range=1:200:1
# @param pivot_right int 5 Pivot right lookback range=1:50:1
# @param pivot_left int 5 Pivot left lookback range=1:50:1
# @param bull_div_color color #089981 Bullish divergence color
# @param bear_div_color color #F23645 Bearish divergence color
# @param show_po3 bool false Show projected HTF candles
# @param candle_count int 1 Projected candles range=1:10:1
# @param po3_offset int 15 Projection offset range=5:100:1
# @param show_po3_labels bool true Show projected candle labels
# @param show_po3_delta bool true Show projected candle delta
# @param alert_bull_div bool true Alert bullish divergence
# @param alert_bear_div bool true Alert bearish divergence
# @param alert_bull_engulfing bool true Alert bullish engulfing
# @param alert_bear_engulfing bool true Alert bearish engulfing
# @param alert_hammer bool true Alert hammer
# @param alert_shooting_star bool true Alert shooting star

indicator_timeframe_param = "htf"
indicator_timeframes = ["15m", "30m", "1H", "2H", "4H", "6H", "8H", "12H", "1D", "1W"]

htf = str(params.get("htf", "15m"))
if htf not in indicator_timeframes:
    htf = "15m"
show_engulfing = bool(params.get("show_engulfing", True))
show_pin_bars = bool(params.get("show_pin_bars", True))
bull_pattern_color = str(params.get("bull_pattern_color", "#089981"))
bear_pattern_color = str(params.get("bear_pattern_color", "#F23645"))
show_divergences = bool(params.get("show_divergences", True))
rsi_length = min(200, max(1, int(params.get("rsi_length", 14))))
pivot_right = min(50, max(1, int(params.get("pivot_right", 5))))
pivot_left = min(50, max(1, int(params.get("pivot_left", 5))))
bull_div_color = str(params.get("bull_div_color", "#089981"))
bear_div_color = str(params.get("bear_div_color", "#F23645"))
show_po3 = bool(params.get("show_po3", False))
candle_count = min(10, max(1, int(params.get("candle_count", 1))))
po3_offset = min(100, max(5, int(params.get("po3_offset", 15))))
show_po3_labels = bool(params.get("show_po3_labels", True))
show_po3_delta = bool(params.get("show_po3_delta", True))
alert_bull_div = bool(params.get("alert_bull_div", True))
alert_bear_div = bool(params.get("alert_bear_div", True))
alert_bull_engulfing = bool(params.get("alert_bull_engulfing", True))
alert_bear_engulfing = bool(params.get("alert_bear_engulfing", True))
alert_hammer = bool(params.get("alert_hammer", True))
alert_shooting_star = bool(params.get("alert_shooting_star", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 3600.0
times = df["time"].to_numpy(dtype=float)
n = len(df)
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0
timestamps = pd.to_datetime(times, unit="ms", utc=True) if n else []

def _color_with_alpha(color, alpha):
    value = str(color).strip()
    if value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            red = int(value[1] * 2, 16)
            green = int(value[2] * 2, 16)
            blue = int(value[3] * 2, 16)
        else:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{max(0.0, min(1.0, float(alpha))):.3f})"
    return value

timeframe_seconds = {
    "15m": 900, "30m": 1800, "1H": 3600, "2H": 7200,
    "4H": 14400, "6H": 21600, "8H": 28800, "12H": 43200,
    "1D": 86400, "1W": 604800,
}
requested_htf = htf
runtime_context = ctx
chart_timeframe = str(runtime_context.timeframe or "1H")
if chart_timeframe in timeframe_seconds and timeframe_seconds[htf] < timeframe_seconds[chart_timeframe]:
    htf = chart_timeframe
htf_seconds = timeframe_seconds[htf]

group_starts = np.zeros(n, dtype=float)
for index, timestamp in enumerate(timestamps):
    if htf == "1W":
        normalized = timestamp.normalize() - pd.Timedelta(days=int(timestamp.weekday()))
        group_starts[index] = float(normalized.value / 1000000)
    elif htf == "1D":
        group_starts[index] = float(timestamp.normalize().value / 1000000)
    else:
        group_starts[index] = float((int(times[index]) // (htf_seconds * 1000)) * (htf_seconds * 1000))

htf_new_bar = np.zeros(n, dtype=bool)
for index in range(1, n):
    htf_new_bar[index] = group_starts[index] != group_starts[index - 1]

requested = None
try:
    requested = request_series(htf, gaps="off", lookahead="off")
except Exception:
    requested = None

requested_valid = bool(
    requested is not None and hasattr(requested, "columns")
    and all(column in requested.columns for column in ("open", "high", "low", "close"))
    and len(requested) == n
)

pattern_events = []
completed_records = []
bull_engulfing_event = np.zeros(n, dtype=bool)
bear_engulfing_event = np.zeros(n, dtype=bool)
hammer_event = np.zeros(n, dtype=bool)
shooting_star_event = np.zeros(n, dtype=bool)

for index in range(1, n):
    if not htf_new_bar[index]:
        continue
    local_start = index - 1
    while local_start > 0 and group_starts[local_start - 1] == group_starts[index - 1]:
        local_start -= 1
    record = {
        "o": float(open_price[local_start]), "h": float(np.max(high[local_start:index])),
        "l": float(np.min(low[local_start:index])), "c": float(close[index - 1]),
        "start": float(group_starts[index - 1]), "end": float(group_starts[index]),
    }
    if requested_valid:
        values = [requested[column].iloc[index] for column in ("open", "high", "low", "close")]
        if all(np.isfinite(float(value)) for value in values):
            record["o"], record["h"], record["l"], record["c"] = [float(value) for value in values]

    previous = completed_records[-1] if completed_records else None
    if previous is not None:
        candle_range = record["h"] - record["l"]
        bull_engulfing = bool(
            show_engulfing and record["c"] > record["o"] and previous["c"] < previous["o"]
            and record["c"] >= previous["o"] and record["o"] <= previous["c"]
        )
        bear_engulfing = bool(
            show_engulfing and record["c"] < record["o"] and previous["c"] > previous["o"]
            and record["c"] <= previous["o"] and record["o"] >= previous["c"]
        )
        hammer = bool(
            show_pin_bars and candle_range > 0.0
            and min(record["o"], record["c"]) - record["l"] > candle_range * 0.6
            and abs(record["c"] - record["o"]) < candle_range * 0.3
        )
        shooting_star = bool(
            show_pin_bars and candle_range > 0.0
            and record["h"] - max(record["o"], record["c"]) > candle_range * 0.6
            and abs(record["c"] - record["o"]) < candle_range * 0.3
        )
        bull_engulfing_event[index] = bull_engulfing
        bear_engulfing_event[index] = bear_engulfing
        hammer_event[index] = hammer
        shooting_star_event[index] = shooting_star
        if bull_engulfing or hammer:
            pattern_events.append({
                **record, "index": index, "side": "bull",
                "name": "Bull Engulfing" if bull_engulfing else "Hammer",
            })
        if bear_engulfing or shooting_star:
            pattern_events.append({
                **record, "index": index, "side": "bear",
                "name": "Bear Engulfing" if bear_engulfing else "Shooting Star",
            })
    completed_records.append(record)

change = np.full(n, np.nan, dtype=float)
gain = np.full(n, np.nan, dtype=float)
loss = np.full(n, np.nan, dtype=float)
average_gain = np.full(n, np.nan, dtype=float)
average_loss = np.full(n, np.nan, dtype=float)
rsi = np.full(n, np.nan, dtype=float)
for index in range(1, n):
    change[index] = close[index] - close[index - 1]
    gain[index] = max(change[index], 0.0)
    loss[index] = max(-change[index], 0.0)
if n > rsi_length:
    average_gain[rsi_length] = float(np.mean(gain[1:rsi_length + 1]))
    average_loss[rsi_length] = float(np.mean(loss[1:rsi_length + 1]))
    for index in range(rsi_length + 1, n):
        average_gain[index] = (average_gain[index - 1] * (rsi_length - 1) + gain[index]) / rsi_length
        average_loss[index] = (average_loss[index - 1] * (rsi_length - 1) + loss[index]) / rsi_length
    for index in range(rsi_length, n):
        if average_gain[index] == 0.0 and average_loss[index] == 0.0:
            rsi[index] = 50.0
        elif average_loss[index] == 0.0:
            rsi[index] = 100.0
        elif average_gain[index] == 0.0:
            rsi[index] = 0.0
        else:
            ratio = average_gain[index] / average_loss[index]
            rsi[index] = 100.0 - 100.0 / (1.0 + ratio)

pivot_low_confirmed = np.zeros(n, dtype=bool)
pivot_high_confirmed = np.zeros(n, dtype=bool)
bull_div_confirmed = np.zeros(n, dtype=bool)
bear_div_confirmed = np.zeros(n, dtype=bool)
bull_divergences = []
bear_divergences = []
previous_low_pivot = None
previous_high_pivot = None

for confirmation in range(pivot_left + pivot_right, n):
    center = confirmation - pivot_right
    start = center - pivot_left
    end = center + pivot_right + 1
    window = rsi[start:end]
    if len(window) != pivot_left + pivot_right + 1 or not np.all(np.isfinite(window)):
        continue
    is_low = bool(rsi[center] <= float(np.min(window)))
    is_high = bool(rsi[center] >= float(np.max(window)))
    if is_low:
        pivot_low_confirmed[confirmation] = True
        if previous_low_pivot is not None and rsi[center] > previous_low_pivot[1] and low[center] < previous_low_pivot[2]:
            bull_div_confirmed[confirmation] = True
            bull_divergences.append({
                "fromIndex": int(previous_low_pivot[0]), "fromRsi": float(previous_low_pivot[1]),
                "fromPrice": float(previous_low_pivot[2]), "toIndex": int(center),
                "toRsi": float(rsi[center]), "toPrice": float(low[center]),
                "confirmation": int(confirmation),
            })
        previous_low_pivot = (center, float(rsi[center]), float(low[center]))
    if is_high:
        pivot_high_confirmed[confirmation] = True
        if previous_high_pivot is not None and rsi[center] < previous_high_pivot[1] and high[center] > previous_high_pivot[2]:
            bear_div_confirmed[confirmation] = True
            bear_divergences.append({
                "fromIndex": int(previous_high_pivot[0]), "fromRsi": float(previous_high_pivot[1]),
                "fromPrice": float(previous_high_pivot[2]), "toIndex": int(center),
                "toRsi": float(rsi[center]), "toPrice": float(high[center]),
                "confirmation": int(confirmation),
            })
        previous_high_pivot = (center, float(rsi[center]), float(high[center]))

bull_rsi_line = [None] * n
bear_rsi_line = [None] * n

def _draw_segment(target, start_index, end_index, start_value, end_value):
    if end_index <= start_index:
        return
    span = float(end_index - start_index)
    for draw_index in range(start_index, end_index + 1):
        ratio = (draw_index - start_index) / span
        target[draw_index] = float(start_value + (end_value - start_value) * ratio)

if show_divergences:
    for divergence in bull_divergences:
        _draw_segment(bull_rsi_line, divergence["fromIndex"], divergence["toIndex"], divergence["fromRsi"], divergence["toRsi"])
    for divergence in bear_divergences:
        _draw_segment(bear_rsi_line, divergence["fromIndex"], divergence["toIndex"], divergence["fromRsi"], divergence["toRsi"])

display_pattern_events = []
for event in reversed(pattern_events):
    if any(abs(int(event["index"]) - int(selected["index"])) < 5 for selected in display_pattern_events):
        continue
    display_pattern_events.append(event)
    if len(display_pattern_events) >= 12:
        break
display_pattern_events.sort(key=lambda event: int(event["index"]))

layers = []
for event in display_pattern_events:
    color = bull_pattern_color if event["side"] == "bull" else bear_pattern_color
    midpoint_time = float(round((event["start"] + event["end"]) * 0.5))
    inset = float(round((event["end"] - event["start"]) * 0.1))
    layers.extend([
        {
            "type": "zone", "role": f"{event['name']} Range", "start": event["start"], "end": event["end"],
            "top": event["h"], "bottom": event["l"], "fillColor": _color_with_alpha(color, 0.10),
            "borderColor": _color_with_alpha(color, 0.50), "color": _color_with_alpha(color, 0.10),
            "borderSize": 1, "opacity": 1.0,
        },
        {
            "type": "zone", "role": f"{event['name']} Body", "start": event["start"] + inset,
            "end": event["end"] - inset, "top": max(event["o"], event["c"]),
            "bottom": min(event["o"], event["c"]), "fillColor": _color_with_alpha(color, 0.40),
            "borderColor": _color_with_alpha(color, 0.80), "color": _color_with_alpha(color, 0.40),
            "borderSize": 1, "opacity": 1.0,
        },
        {
            "type": "line", "role": f"{event['name']} Wick", "start": midpoint_time, "end": midpoint_time,
            "y1": event["h"], "y2": event["l"], "color": _color_with_alpha(color, 0.80), "lineWidth": 1,
        },
        {
            "type": "label", "role": f"{event['name']} Label", "timestamp": midpoint_time,
            "price": event["l"] if event["side"] == "bull" else event["h"],
            "text": event["name"], "anchor": "below" if event["side"] == "bull" else "above",
            "color": "rgba(0,0,0,0)", "borderColor": "rgba(0,0,0,0)",
            "textColor": color, "fontSize": 9,
        },
    ])

if show_divergences:
    for divergence in bull_divergences:
        layers.extend([
            {"type": "line", "role": "Bullish Price Divergence", "startIndex": divergence["fromIndex"], "endIndex": divergence["toIndex"], "y1": divergence["fromPrice"], "y2": divergence["toPrice"], "color": bull_div_color, "lineWidth": 2},
            {"type": "label", "role": "Bullish Divergence Label", "index": divergence["toIndex"], "price": divergence["toPrice"], "text": "Bull Div", "anchor": "below", "color": "rgba(0,0,0,0)", "borderColor": "rgba(0,0,0,0)", "textColor": bull_div_color, "fontSize": 9},
        ])
    for divergence in bear_divergences:
        layers.extend([
            {"type": "line", "role": "Bearish Price Divergence", "startIndex": divergence["fromIndex"], "endIndex": divergence["toIndex"], "y1": divergence["fromPrice"], "y2": divergence["toPrice"], "color": bear_div_color, "lineWidth": 2},
            {"type": "label", "role": "Bearish Divergence Label", "index": divergence["toIndex"], "price": divergence["toPrice"], "text": "Bear Div", "anchor": "above", "color": "rgba(0,0,0,0)", "borderColor": "rgba(0,0,0,0)", "textColor": bear_div_color, "fontSize": 9},
        ])

po3_history = []
current_po3 = None
if show_po3:
    for index in range(n):
        signed_volume = float(volume[index]) if close[index] > open_price[index] else -float(volume[index]) if close[index] < open_price[index] else 0.0
        if htf_new_bar[index]:
            if current_po3 is not None:
                current_po3["c"] = float(close[index - 1])
                po3_history.insert(0, dict(current_po3))
                po3_history = po3_history[:10]
            current_po3 = {
                "o": float(open_price[index]), "h": float(high[index]), "l": float(low[index]), "c": float(close[index]),
                "oIdx": index, "hIdx": index, "lIdx": index, "delta": signed_volume, "start": float(times[index]),
            }
        elif current_po3 is not None:
            if high[index] > current_po3["h"]:
                current_po3["h"] = float(high[index])
                current_po3["hIdx"] = index
            if low[index] < current_po3["l"]:
                current_po3["l"] = float(low[index])
                current_po3["lIdx"] = index
            current_po3["c"] = float(close[index])
            current_po3["delta"] += signed_volume

positive_intervals = np.diff(times)
positive_intervals = positive_intervals[np.isfinite(positive_intervals) & (positive_intervals > 0.0)]
bar_interval = float(np.median(positive_intervals)) if len(positive_intervals) else 3600000.0
po3_records = []
if show_po3 and current_po3 is not None and n:
    for position in range(candle_count):
        is_live = position == candle_count - 1
        if is_live:
            data = dict(current_po3)
            data["c"] = float(close[-1])
        else:
            history_index = (candle_count - 2) - position
            data = dict(po3_history[history_index]) if history_index < len(po3_history) else None
        if data is None:
            continue
        start_time = float(times[-1] + bar_interval * (po3_offset + position * 16))
        end_time = float(start_time + bar_interval * 6)
        middle_time = float((start_time + end_time) * 0.5)
        base_color = bull_pattern_color if data["c"] >= data["o"] else bear_pattern_color
        wick_color = base_color if is_live else _color_with_alpha(base_color, 0.30)
        layers.extend([
            {"type": "line", "role": "PO3 Wick", "start": middle_time, "end": middle_time, "y1": data["h"], "y2": data["l"], "color": wick_color, "lineWidth": 2},
            {"type": "zone", "role": "PO3 Body", "start": start_time, "end": end_time, "top": max(data["o"], data["c"]), "bottom": min(data["o"], data["c"]), "color": _color_with_alpha(base_color, 0.80 if is_live else 0.15), "fillColor": _color_with_alpha(base_color, 0.80 if is_live else 0.15), "borderColor": wick_color, "borderSize": 1, "opacity": 1.0},
        ])
        if is_live:
            marker_specs = [
                (data["oIdx"], data["o"], start_time, "Open", "#787B86"),
                (data["hIdx"], data["h"], middle_time, "High", bull_pattern_color),
                (data["lIdx"], data["l"], middle_time, "Low", bear_pattern_color),
                (n - 1, data["c"], end_time, "Close", base_color),
            ]
            for source_index, price, target_time, name, color in marker_specs:
                layers.append({"type": "line", "role": f"PO3 {name} Marker", "startIndex": int(source_index), "end": target_time, "y1": float(price), "y2": float(price), "color": color, "lineWidth": 1, "dashed": True})
                if show_po3_labels:
                    layers.append({"type": "label", "role": f"PO3 {name} Label", "timestamp": end_time + bar_interval, "price": float(price), "text": f"{name}: {price:,.2f}", "anchor": "center", "color": "rgba(0,0,0,0)", "borderColor": "rgba(0,0,0,0)", "textColor": color, "fontSize": 9})
        time_text = htf
        if not is_live:
            time_text += "\n" + pd.to_datetime(data["start"], unit="ms", utc=True).strftime("%H:%M")
        layers.append({"type": "label", "role": "PO3 Time Label", "timestamp": middle_time, "price": data["h"], "text": time_text, "anchor": "above", "color": "rgba(0,0,0,0)", "borderColor": "rgba(0,0,0,0)", "textColor": "#787B86", "fontSize": 10})
        if show_po3_delta:
            delta = float(data["delta"])
            suffix = f"{delta / 1000000.0:+.1f}M" if abs(delta) >= 1000000 else f"{delta / 1000.0:+.1f}K" if abs(delta) >= 1000 else f"{delta:+.0f}"
            delta_color = bull_pattern_color if is_live and delta >= 0 else bear_pattern_color if is_live else "#787B86"
            layers.append({"type": "label", "role": "PO3 Delta Label", "timestamp": middle_time, "price": data["l"], "text": f"Delta: {suffix}", "anchor": "below", "color": "rgba(0,0,0,0)", "borderColor": "rgba(0,0,0,0)", "textColor": delta_color, "fontSize": 10})
        po3_records.append({**data, "position": position, "isLive": is_live, "startTime": start_time, "endTime": end_time})

def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]

rsi_points = [
    {"value": float(rsi[index]), "color": bull_div_color if rsi[index] > 50.0 else bear_div_color}
    if np.isfinite(rsi[index]) else None
    for index in range(n)
]
rsi_cloud = [
    {"upper": float(max(50.0, rsi[index])), "lower": float(min(50.0, rsi[index])),
     "color": _color_with_alpha(bull_div_color if rsi[index] > 50.0 else bear_div_color, 0.22)}
    if np.isfinite(rsi[index]) else None
    for index in range(n)
]

signals = [
    {"type": "buy", "name": "Bullish RSI Divergence", "text": "Bull Div", "data": [float(low[index]) if alert_bull_div and bull_div_confirmed[index] else None for index in range(n)], "color": bull_div_color, "renderMode": "hidden"},
    {"type": "sell", "name": "Bearish RSI Divergence", "text": "Bear Div", "data": [float(high[index]) if alert_bear_div and bear_div_confirmed[index] else None for index in range(n)], "color": bear_div_color, "renderMode": "hidden"},
    {"type": "buy", "name": "HTF Bullish Engulfing", "text": "Bull Eng", "data": [float(low[index]) if alert_bull_engulfing and bull_engulfing_event[index] else None for index in range(n)], "color": bull_pattern_color, "renderMode": "hidden"},
    {"type": "sell", "name": "HTF Bearish Engulfing", "text": "Bear Eng", "data": [float(high[index]) if alert_bear_engulfing and bear_engulfing_event[index] else None for index in range(n)], "color": bear_pattern_color, "renderMode": "hidden"},
    {"type": "buy", "name": "HTF Hammer", "text": "Hammer", "data": [float(low[index]) if alert_hammer and hammer_event[index] else None for index in range(n)], "color": bull_pattern_color, "renderMode": "hidden"},
    {"type": "sell", "name": "HTF Shooting Star", "text": "Star", "data": [float(high[index]) if alert_shooting_star and shooting_star_event[index] else None for index in range(n)], "color": bear_pattern_color, "renderMode": "hidden"},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "RSI", "data": rsi_points, "color": bull_div_color, "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "RSI Regime", "title": False, "data": rsi_cloud, "type": "cloud", "opacity": 0.22, "overlay": False},
        {"name": "Bull RSI Divergence", "title": False, "data": bull_rsi_line, "color": bull_div_color, "type": "line", "lineWidth": 2, "overlay": False, "connectNulls": False},
        {"name": "Bear RSI Divergence", "title": False, "data": bear_rsi_line, "color": bear_div_color, "type": "line", "lineWidth": 2, "overlay": False, "connectNulls": False},
        {"name": "Overbought", "data": [70.0] * n, "color": _color_with_alpha(bear_div_color, 0.50), "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": False},
        {"name": "Mid Line", "data": [50.0] * n, "color": "rgba(160,165,175,0.30)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Oversold", "data": [30.0] * n, "color": _color_with_alpha(bull_div_color, 0.50), "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": False},
    ],
    "signals": signals,
    "layers": layers,
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "htfNewBar": htf_new_bar.tolist(), "requestedSeriesUsed": bool(requested_valid),
        "requestedTimeframe": requested_htf, "resolvedTimeframe": htf,
        "completedRecords": completed_records, "patternEvents": pattern_events,
        "bullEngulfing": bull_engulfing_event.tolist(), "bearEngulfing": bear_engulfing_event.tolist(),
        "hammer": hammer_event.tolist(), "shootingStar": shooting_star_event.tolist(),
        "rsi": _finite_or_none(rsi), "averageGain": _finite_or_none(average_gain),
        "averageLoss": _finite_or_none(average_loss),
        "pivotLowConfirmed": pivot_low_confirmed.tolist(), "pivotHighConfirmed": pivot_high_confirmed.tolist(),
        "bullDivConfirmed": bull_div_confirmed.tolist(), "bearDivConfirmed": bear_div_confirmed.tolist(),
        "bullDivergences": bull_divergences, "bearDivergences": bear_divergences,
        "po3History": po3_history, "po3Records": po3_records,
        "barInterval": float(bar_interval), "htfSeconds": int(htf_seconds),
    },
}
