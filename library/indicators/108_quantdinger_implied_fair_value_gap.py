# ============================================================
# QuantDinger Implied Fair Value Gap
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Implied Fair Value Gap"
my_indicator_description = (
    "Detects three-candle implied fair value gaps from body dominance and "
    "shadow proportions, then projects each zone and midpoint independently."
)

# @param shadow_threshold float 30.0 Shadow threshold percent range=0:100:1
# @param extension int 8 Zone extension bars range=1:100:1
# @param extend_averages bool false Extend latest averages after each zone
# @param show_bullish bool true Show bullish gaps
# @param bullish_color color #3179F5 Bullish color
# @param show_bearish bool true Show bearish gaps
# @param bearish_color color #FF5D00 Bearish color

shadow_threshold = min(100.0, max(0.0, float(params.get("shadow_threshold", 30.0)))) / 100.0
extension = min(100, max(1, int(params.get("extension", 8))))
extend_averages = bool(params.get("extend_averages", False))
show_bullish = bool(params.get("show_bullish", True))
bullish_color = str(params.get("bullish_color", "#3179F5"))
show_bearish = bool(params.get("show_bearish", True))
bearish_color = str(params.get("bearish_color", "#FF5D00"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
times = df["time"].to_numpy(dtype=float)
n = len(df)
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0

positive_intervals = np.diff(times)
positive_intervals = positive_intervals[np.isfinite(positive_intervals) & (positive_intervals > 0.0)]
bar_interval = float(np.median(positive_intervals)) if len(positive_intervals) else 3600000.0


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


body = np.abs(close - open_price)
bullish_events = []
bearish_events = []
bullish_event_flags = np.zeros(n, dtype=bool)
bearish_event_flags = np.zeros(n, dtype=bool)

for index in range(2, n):
    candle_range = high[index] - low[index]
    if not np.isfinite(candle_range) or candle_range <= 0.0:
        continue
    if not (body[index - 1] > max(body[index], body[index - 2])):
        continue

    bull_top = (min(close[index], open_price[index]) + low[index]) * 0.5
    bull_bottom = (max(close[index - 2], open_price[index - 2]) + high[index - 2]) * 0.5
    bull_current_shadow = (min(close[index], open_price[index]) - low[index]) / candle_range
    bull_prior_shadow = (high[index - 2] - max(close[index - 2], open_price[index - 2])) / candle_range
    bullish = bool(
        low[index] < high[index - 2]
        and bull_current_shadow > shadow_threshold
        and bull_prior_shadow > shadow_threshold
        and bull_top > bull_bottom
    )
    bullish_event_flags[index] = bullish
    if bullish and show_bullish:
        bullish_events.append({
            "index": index,
            "top": float(bull_top),
            "bottom": float(bull_bottom),
            "average": float((bull_top + bull_bottom) * 0.5),
        })

    bear_top = (min(close[index - 2], open_price[index - 2]) + low[index - 2]) * 0.5
    bear_bottom = (max(close[index], open_price[index]) + high[index]) * 0.5
    bear_current_shadow = (high[index] - max(close[index], open_price[index])) / candle_range
    bear_prior_shadow = (min(close[index - 2], open_price[index - 2]) - low[index - 2]) / candle_range
    bearish = bool(
        high[index] > low[index - 2]
        and bear_current_shadow > shadow_threshold
        and bear_prior_shadow > shadow_threshold
        and bear_top > bear_bottom
    )
    bearish_event_flags[index] = bearish
    if bearish and show_bearish:
        bearish_events.append({
            "index": index,
            "top": float(bear_top),
            "bottom": float(bear_bottom),
            "average": float((bear_top + bear_bottom) * 0.5),
        })


all_events = []
for event in bullish_events:
    all_events.append((event["index"], "Bullish", event, bullish_color))
for event in bearish_events:
    all_events.append((event["index"], "Bearish", event, bearish_color))
all_events.sort(key=lambda item: item[0])
all_events = all_events[-500:]

layers = []
for _, side, event, color in all_events:
    detected_at = event["index"]
    start_time = float(times[max(0, detected_at - 2)])
    end_time = float(times[detected_at] + bar_interval * extension)
    fill_color = _color_with_alpha(color, 0.50)
    layers.extend([
        {
            "type": "zone", "role": f"{side} IFVG",
            "start": start_time, "end": end_time,
            "top": event["top"], "bottom": event["bottom"],
            "color": fill_color, "fillColor": fill_color,
            "borderColor": color, "borderSize": 1, "opacity": 1.0,
        },
        {
            "type": "line", "role": f"{side} IFVG Average",
            "start": start_time, "end": end_time,
            "y1": event["average"], "y2": event["average"],
            "color": color, "lineWidth": 1,
        },
    ])


def _extended_average(events):
    result = [None] * n
    if not extend_averages:
        return result
    for event_index, event in enumerate(events):
        start = event["index"] + extension + 1
        stop = events[event_index + 1]["index"] if event_index + 1 < len(events) else n
        for index in range(max(0, start), min(n, stop)):
            result[index] = event["average"]
    return result


bull_average = _extended_average(bullish_events)
bear_average = _extended_average(bearish_events)


def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Bullish Extended Average", "data": bull_average, "color": bullish_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Bearish Extended Average", "data": bear_average, "color": bearish_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite_or_none(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [],
    "layers": layers,
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "body": _finite_or_none(body),
        "bullishEvent": bullish_event_flags.tolist(),
        "bearishEvent": bearish_event_flags.tolist(),
        "bullishEvents": bullish_events,
        "bearishEvents": bearish_events,
        "barInterval": bar_interval,
    },
}
