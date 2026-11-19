# ============================================================
# QuantDinger Protected Highs and Lows
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Protected Highs and Lows"
my_indicator_description = "Tracks pivot breaks, validates pullbacks, classifies MSS/BOS events, carries protected swing levels, and optionally draws retracement zones."

# @param pivot_strength int 1 Pivot strength range=1:100:1
# @param color_trend_candles bool true Color candles by structure trend
# @param show_structure_labels bool true Show MSS/BOS labels
# @param structure_label_type str MSS Structure label type values=MSS,BOS,All
# @param show_protected_levels bool true Show protected-high/low markers
# @param show_protected_trail bool true Show protected-level trail
# @param trail_width int 2 Trail width range=1:10:1
# @param require_valid_pullback bool true Require valid pullback before protected swing updates
# @param specific_pullback_candle bool false Search four pivot-side candles for the opposite candle
# @param protected_high_color str #F23645 Protected-high color
# @param protected_low_color str #0000FF Protected-low color
# @param bull_color str #008080 Bull structure color
# @param bear_color str #F23645 Bear structure color
# @param show_retracements bool false Show 30/50/70 percent retracements
# @param retracement_opacity int 85 Retracement transparency range=0:100:1
# @param retracement_line_width int 1 Retracement line width range=0:10:1
# @param retracement_line_style str Dotted Retracement line style values=Solid,Dashed,Dotted

pivot_strength = min(100, max(1, int(params.get("pivot_strength", 1))))
color_trend_candles = bool(params.get("color_trend_candles", True))
show_structure_labels = bool(params.get("show_structure_labels", True))
structure_label_type = str(params.get("structure_label_type", "MSS"))
if structure_label_type not in ("MSS", "BOS", "All"):
    structure_label_type = "MSS"
show_protected_levels = bool(params.get("show_protected_levels", True))
show_protected_trail = bool(params.get("show_protected_trail", True))
trail_width = min(10, max(1, int(params.get("trail_width", 2))))
require_valid_pullback = bool(params.get("require_valid_pullback", True))
specific_pullback_candle = bool(params.get("specific_pullback_candle", False))
protected_high_color = str(params.get("protected_high_color", "#F23645"))
protected_low_color = str(params.get("protected_low_color", "#0000FF"))
bull_color = str(params.get("bull_color", "#008080"))
bear_color = str(params.get("bear_color", "#F23645"))
show_retracements = bool(params.get("show_retracements", False))
retracement_opacity = min(100, max(0, int(params.get("retracement_opacity", 85))))
retracement_line_width = min(10, max(0, int(params.get("retracement_line_width", 1))))
retracement_line_style = str(params.get("retracement_line_style", "Dotted"))
if retracement_line_style not in ("Solid", "Dashed", "Dotted"):
    retracement_line_style = "Dotted"


def _normalize(frame):
    result = frame.copy().reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
    times = result["time"].to_numpy(dtype=float)
    if len(times) and np.nanmax(np.abs(times)) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _pivots(values, strength, high_side):
    result = np.full(len(values), np.nan, dtype=float)
    for confirmation in range(strength * 2, len(values)):
        center = confirmation - strength
        left = values[center - strength:center]
        right = values[center + 1:confirmation + 1]
        candidate = values[center]
        if high_side:
            valid = np.all(candidate >= left) and np.all(candidate > right)
        else:
            valid = np.all(candidate <= left) and np.all(candidate < right)
        if valid:
            result[confirmation] = float(candidate)
    return result


df = df.copy()
frame = _normalize(df)
time_values = frame["time"].to_numpy(dtype=float)
opened = frame["open"].to_numpy(dtype=float)
high = frame["high"].to_numpy(dtype=float)
low = frame["low"].to_numpy(dtype=float)
close = frame["close"].to_numpy(dtype=float)
n = len(frame)
pivot_high = _pivots(high, pivot_strength, True)
pivot_low = _pivots(low, pivot_strength, False)

queued_high = None
queued_low = None
queued_high_index = None
queued_low_index = None
last_high = None
last_low = None
last_high_index = None
last_low_index = None
tracked_high = None
tracked_low = None
tracked_high_index = None
tracked_low_index = None
pullback_from_high = {"price": None, "index": None, "valid": False, "bull": True}
pullback_from_low = {"price": None, "index": None, "valid": False, "bull": False}
bull_state = None
trail_price_state = None
trail_color_state = None
active_retracement = None
events = []
retracements = []
break_layers = []
protected_layers = []

bullish_bos = np.zeros(n, dtype=bool)
bearish_bos = np.zeros(n, dtype=bool)
bullish_mss = np.zeros(n, dtype=bool)
bearish_mss = np.zeros(n, dtype=bool)
change = np.zeros(n, dtype=bool)
pullback_high_valid = np.zeros(n, dtype=bool)
pullback_low_valid = np.zeros(n, dtype=bool)
tracked_high_series = np.full(n, np.nan, dtype=float)
tracked_low_series = np.full(n, np.nan, dtype=float)
queued_high_series = np.full(n, np.nan, dtype=float)
queued_low_series = np.full(n, np.nan, dtype=float)
trail_price = np.full(n, np.nan, dtype=float)
trend_state = np.zeros(n, dtype=int)


def _set_pullback(target, price, index):
    target["price"] = float(price)
    target["index"] = int(index)
    target["valid"] = False


for index in range(n):
    candidate_low = pivot_low[index]
    candidate_high = pivot_high[index]
    pivot_index = index - pivot_strength

    if np.isfinite(candidate_low) and queued_low is None:
        queued_low = float(candidate_low)
        queued_low_index = pivot_index
        chosen = None
        if specific_pullback_candle:
            for offset in range(4):
                candle_index = pivot_index - offset
                if candle_index >= 0 and close[candle_index] < opened[candle_index]:
                    chosen = high[candle_index]
                    break
        else:
            chosen = high[pivot_index]
        if chosen is not None:
            _set_pullback(pullback_from_low, chosen, index)
        if last_low is None or candidate_low < last_low:
            last_low = float(candidate_low)
            last_low_index = pivot_index

    if np.isfinite(candidate_high) and queued_high is None:
        queued_high = float(candidate_high)
        queued_high_index = pivot_index
        chosen = None
        if specific_pullback_candle:
            for offset in range(4):
                candle_index = pivot_index - offset
                if candle_index >= 0 and close[candle_index] > opened[candle_index]:
                    chosen = low[candle_index]
                    break
        else:
            chosen = low[pivot_index]
        if chosen is not None:
            _set_pullback(pullback_from_high, chosen, index)
        if last_high is None or candidate_high > last_high:
            last_high = float(candidate_high)
            last_high_index = pivot_index

    if pullback_from_high["price"] is not None and not pullback_from_high["valid"] and close[index] < pullback_from_high["price"]:
        pullback_from_high["valid"] = True
    if pullback_from_low["price"] is not None and not pullback_from_low["valid"] and close[index] > pullback_from_low["price"]:
        pullback_from_low["valid"] = True

    if np.isfinite(candidate_high):
        price_condition = tracked_high is None or candidate_high > tracked_high or (last_low_index is not None and tracked_high_index is not None and last_low_index >= tracked_high_index)
        pullback_condition = pullback_from_low["valid"] if require_valid_pullback else True
        if price_condition and pullback_condition:
            tracked_high = float(candidate_high)
            tracked_high_index = pivot_index
    if np.isfinite(candidate_low):
        price_condition = tracked_low is None or candidate_low < tracked_low or (last_high_index is not None and tracked_low_index is not None and last_high_index >= tracked_low_index)
        pullback_condition = pullback_from_high["valid"] if require_valid_pullback else True
        if price_condition and pullback_condition:
            tracked_low = float(candidate_low)
            tracked_low_index = pivot_index

    if queued_high is not None and close[index] > queued_high:
        previous_bull = bull_state
        event_type = "BOS" if previous_bull is True else "MSS"
        bullish_bos[index] = previous_bull is True
        bullish_mss[index] = previous_bull is not True
        should_label = show_structure_labels and (structure_label_type == "All" or structure_label_type == event_type) and previous_bull is not None
        event = {"index": index, "pivotIndex": queued_high_index, "price": queued_high, "direction": 1, "type": event_type, "labelShown": bool(should_label), "protectedPrice": tracked_low, "protectedIndex": tracked_low_index}
        events.append(event)
        break_layers.append({"type": "line", "role": f"Bullish {event_type}", "startIndex": queued_high_index, "endIndex": index, "y1": queued_high, "y2": queued_high, "color": bull_color, "dashed": True, "lineWidth": 1})
        if should_label:
            break_layers.append({"type": "label", "role": f"Bullish {event_type}", "index": int(np.floor((queued_high_index + index) / 2.0)), "price": queued_high, "text": event_type, "color": "rgba(0,0,0,0)", "textColor": bull_color, "direction": "down", "size": "small"})
        if show_retracements and (pullback_from_high["valid"] if require_valid_pullback else True) and tracked_low is not None:
            retracement = {"start": index, "end": index, "high": queued_high, "low": tracked_low, "direction": 1, "styled": False}
            retracements.append(retracement)
            active_retracement = retracement
        bull_state = True
        change[index] = True
        queued_high = queued_low = None
        queued_high_index = queued_low_index = None
        if tracked_low is not None:
            queued_low = tracked_low
            queued_low_index = tracked_low_index
            if show_protected_levels and tracked_low_index is not None:
                protected_layers.append({"type": "label", "role": "Protected Low", "index": tracked_low_index, "price": tracked_low, "text": "▲", "color": "rgba(0,0,0,0)", "textColor": protected_low_color, "direction": "up", "size": "small"})
            last_high = None
            last_high_index = None

    if queued_low is not None and close[index] < queued_low:
        previous_bull = bull_state
        event_type = "BOS" if previous_bull is False else "MSS"
        bearish_bos[index] = previous_bull is False
        bearish_mss[index] = previous_bull is not False
        should_label = show_structure_labels and (structure_label_type == "All" or structure_label_type == event_type) and previous_bull is not None
        event = {"index": index, "pivotIndex": queued_low_index, "price": queued_low, "direction": -1, "type": event_type, "labelShown": bool(should_label), "protectedPrice": tracked_high, "protectedIndex": tracked_high_index}
        events.append(event)
        break_layers.append({"type": "line", "role": f"Bearish {event_type}", "startIndex": queued_low_index, "endIndex": index, "y1": queued_low, "y2": queued_low, "color": bear_color, "dashed": True, "lineWidth": 1})
        if should_label:
            break_layers.append({"type": "label", "role": f"Bearish {event_type}", "index": int(np.floor((queued_low_index + index) / 2.0)), "price": queued_low, "text": event_type, "color": "rgba(0,0,0,0)", "textColor": bear_color, "direction": "up", "size": "small"})
        if show_retracements and (pullback_from_low["valid"] if require_valid_pullback else True) and tracked_high is not None:
            retracement = {"start": index, "end": index, "high": queued_low, "low": tracked_high, "direction": -1, "styled": False}
            retracements.append(retracement)
            active_retracement = retracement
        bull_state = False
        change[index] = True
        queued_high = queued_low = None
        queued_high_index = queued_low_index = None
        if tracked_high is not None:
            queued_high = tracked_high
            queued_high_index = tracked_high_index
            if show_protected_levels and tracked_high_index is not None:
                protected_layers.append({"type": "label", "role": "Protected High", "index": tracked_high_index, "price": tracked_high, "text": "▼", "color": "rgba(0,0,0,0)", "textColor": protected_high_color, "direction": "down", "size": "small"})
            last_low = None
            last_low_index = None

    if active_retracement is not None:
        active_retracement["end"] = index
    if index > 0 and change[index - 1]:
        if bearish_bos[index - 1] or bearish_mss[index - 1]:
            trail_price_state = tracked_high
            trail_color_state = protected_high_color
        elif bullish_bos[index - 1] or bullish_mss[index - 1]:
            trail_price_state = tracked_low
            trail_color_state = protected_low_color
        if active_retracement is not None:
            active_retracement["styled"] = True

    if tracked_high is not None:
        tracked_high_series[index] = tracked_high
    if tracked_low is not None:
        tracked_low_series[index] = tracked_low
    if queued_high is not None:
        queued_high_series[index] = queued_high
    if queued_low is not None:
        queued_low_series[index] = queued_low
    if trail_price_state is not None:
        trail_price[index] = trail_price_state
    pullback_high_valid[index] = pullback_from_high["valid"]
    pullback_low_valid[index] = pullback_from_low["valid"]
    trend_state[index] = 1 if bull_state is True else -1 if bull_state is False else 0

retracement_layers = []
for retracement in retracements[-120:]:
    first = retracement["high"]
    second = retracement["low"]
    levels = [second + (first - second) * ratio for ratio in (0.3, 0.5, 0.7)]
    color = bull_color if retracement["direction"] == 1 else bear_color
    top, bottom = max(first, second), min(first, second)
    retracement_layers.append({"type": "zone", "role": "Retracement", "startIndex": retracement["start"], "endIndex": retracement["end"], "top": float(top), "bottom": float(bottom), "fillColor": color, "opacity": (100 - retracement_opacity) / 100.0, "borderColor": "rgba(0,0,0,0)"})
    for ratio, level in zip((30, 50, 70), levels):
        retracement_layers.append({"type": "line", "role": f"Retracement {ratio}%", "startIndex": retracement["start"], "endIndex": retracement["end"], "y1": float(level), "y2": float(level), "color": color, "lineWidth": retracement_line_width, "dashed": retracement_line_style != "Solid", "dotted": retracement_line_style == "Dotted"})

bar_colors = []
for state in trend_state:
    bar_colors.append(bull_color if color_trend_candles and state == 1 else bear_color if color_trend_candles and state == -1 else None)

alert_bullish_bos = np.zeros(n, dtype=bool)
alert_bearish_bos = np.zeros(n, dtype=bool)
alert_bullish_mss = np.zeros(n, dtype=bool)
alert_bearish_mss = np.zeros(n, dtype=bool)
if n > 1:
    alert_bullish_bos[1:] = bullish_bos[:-1]
    alert_bearish_bos[1:] = bearish_bos[:-1]
    alert_bullish_mss[1:] = bullish_mss[:-1]
    alert_bearish_mss[1:] = bearish_mss[:-1]


def _event_signal(mask, side, text, color):
    prices = low if side == "buy" else high
    return {"type": side, "name": text, "data": [{"active": True, "price": float(prices[index]), "text": text, "color": color} if mask[index] else None for index in range(n)], "color": color, "renderMode": "events"}


trail_colors = []
previous_color = protected_low_color
for index in range(n):
    if change[index - 1] if index > 0 else False:
        previous_color = protected_low_color if bullish_bos[index - 1] or bullish_mss[index - 1] else protected_high_color
    trail_colors.append(previous_color)

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Protected Trail", "data": _finite(trail_price) if show_protected_trail else [None] * n, "color": trail_colors, "type": "step", "lineWidth": trail_width, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        _event_signal(alert_bullish_bos, "buy", "BOS Bull", bull_color),
        _event_signal(alert_bearish_bos, "sell", "BOS Bear", bear_color),
        _event_signal(alert_bullish_mss, "buy", "MSS Bull", bull_color),
        _event_signal(alert_bearish_mss, "sell", "MSS Bear", bear_color),
    ],
    "layers": (break_layers + protected_layers + retracement_layers)[-500:], "tables": [], "bar_colors": bar_colors, "background_colors": [None] * n,
    "calculatedVars": {
        "pivotHigh": _finite(pivot_high), "pivotLow": _finite(pivot_low), "trackedHigh": _finite(tracked_high_series), "trackedLow": _finite(tracked_low_series), "queuedHigh": _finite(queued_high_series), "queuedLow": _finite(queued_low_series),
        "pullbackFromHighValid": pullback_high_valid.tolist(), "pullbackFromLowValid": pullback_low_valid.tolist(), "bullishBos": bullish_bos.tolist(), "bearishBos": bearish_bos.tolist(), "bullishMss": bullish_mss.tolist(), "bearishMss": bearish_mss.tolist(), "change": change.tolist(),
        "alertBullishBos": alert_bullish_bos.tolist(), "alertBearishBos": alert_bearish_bos.tolist(), "alertBullishMss": alert_bullish_mss.tolist(), "alertBearishMss": alert_bearish_mss.tolist(),
        "trendState": [int(value) for value in trend_state], "trailPrice": _finite(trail_price), "events": events, "retracements": retracements,
        "pullbackFromHigh": pullback_from_high, "pullbackFromLow": pullback_from_low,
    },
}
