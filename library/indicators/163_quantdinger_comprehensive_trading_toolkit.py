# ============================================================
# QuantDinger Comprehensive Trading Toolkit
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; alerts do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Comprehensive Trading Toolkit"
my_indicator_description = (
    "Combines large-candle pivot support and resistance zones, RSI regime highlights, "
    "optional price/RSI divergence segments, and short-lived correction boxes."
)

indicator_timeframes = ["1H", "4H", "1D", "1W"]

# @param show_support_resistance bool true Show support and resistance zones
# @param pivot_length int 30 Support and resistance pivot length range=2:100:1
# @param bullish_zone_color str rgba(0,230,119,0.30) Bullish zone color
# @param bearish_zone_color str rgba(255,82,82,0.30) Bearish zone color
# @param rsi_length int 24 RSI length range=2:200:1
# @param oversold int 30 Oversold level range=1:49:1
# @param overbought int 70 Overbought level range=51:99:1
# @param show_divergence bool false Show regular RSI divergences
# @param divergence_length int 10 Divergence pivot length range=2:50:1
# @param bullish_divergence_color str rgba(0,230,211,0.80) Bullish divergence color
# @param bearish_divergence_color str rgba(255,82,82,0.80) Bearish divergence color
# @param show_corrections bool false Show correction boxes
# @param correction_percent int 5 Correction threshold range=1:30:1
# @param correction_color str rgba(33,149,243,0.18) Correction box color

show_support_resistance = bool(params.get("show_support_resistance", True))
pivot_length = min(100, max(2, int(params.get("pivot_length", 30))))
bullish_zone_color = str(params.get("bullish_zone_color", "rgba(0,230,119,0.30)"))
bearish_zone_color = str(params.get("bearish_zone_color", "rgba(255,82,82,0.30)"))
rsi_length = min(200, max(2, int(params.get("rsi_length", 24))))
oversold = min(49, max(1, int(params.get("oversold", 30))))
overbought = min(99, max(51, int(params.get("overbought", 70))))
show_divergence = bool(params.get("show_divergence", False))
divergence_length = min(50, max(2, int(params.get("divergence_length", 10))))
bullish_divergence_color = str(params.get("bullish_divergence_color", "rgba(0,230,211,0.80)"))
bearish_divergence_color = str(params.get("bearish_divergence_color", "rgba(255,82,82,0.80)"))
show_corrections = bool(params.get("show_corrections", False))
correction_percent = min(30, max(1, int(params.get("correction_percent", 5))))
correction_color = str(params.get("correction_color", "rgba(33,149,243,0.18)"))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

def wilder_rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    seed = values[:length]
    if not np.all(np.isfinite(seed)):
        return result
    result[length - 1] = float(np.mean(seed))
    for index in range(length, len(values)):
        if np.isfinite(values[index]):
            result[index] = (result[index - 1] * (length - 1) + values[index]) / length
    return result

changes = np.full(n, np.nan, dtype=float)
if n > 1:
    changes[1:] = close[1:] - close[:-1]
gains = np.where(np.isfinite(changes), np.maximum(changes, 0.0), np.nan)
losses = np.where(np.isfinite(changes), np.maximum(-changes, 0.0), np.nan)
rsi = np.full(n, np.nan, dtype=float)
if n > rsi_length:
    gain_seed = gains[1:rsi_length + 1]
    loss_seed = losses[1:rsi_length + 1]
    if np.all(np.isfinite(gain_seed)) and np.all(np.isfinite(loss_seed)):
        avg_gain = float(np.mean(gain_seed))
        avg_loss = float(np.mean(loss_seed))
        rsi[rsi_length] = 100.0 if avg_loss == 0.0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
        for index in range(rsi_length + 1, n):
            avg_gain = (avg_gain * (rsi_length - 1) + gains[index]) / rsi_length
            avg_loss = (avg_loss * (rsi_length - 1) + losses[index]) / rsi_length
            rsi[index] = 100.0 if avg_loss == 0.0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )
atr_200 = wilder_rma(true_range, 200)

candle_range = high - low
range_stdev = np.full(n, np.nan, dtype=float)
for index in range(199, n):
    range_stdev[index] = float(np.std(candle_range[index - 199:index + 1], ddof=0))
normalized_size = np.divide(
    candle_range,
    range_stdev,
    out=np.full(n, np.nan, dtype=float),
    where=np.isfinite(range_stdev) & (range_stdev > 0.0),
)

def confirmed_pivot(values, confirmation_index, period, high_pivot):
    center = confirmation_index - period
    if center < period or confirmation_index >= len(values):
        return None
    window = values[center - period:confirmation_index + 1]
    if len(window) != period * 2 + 1 or not np.all(np.isfinite(window)):
        return None
    candidate = values[center]
    others = np.concatenate((window[:period], window[period + 1:]))
    if high_pivot:
        valid = candidate >= np.max(others) and np.any(candidate > others)
    else:
        valid = candidate <= np.min(others) and np.any(candidate < others)
    return center if valid else None

levels = []
for index in range(n):
    high_pivot = confirmed_pivot(high, index, pivot_length, True)
    low_pivot = confirmed_pivot(low, index, pivot_length, False)
    atr_width = float(atr_200[index] * 0.3) if np.isfinite(atr_200[index]) else None
    if atr_width is not None:
        if high_pivot is not None and np.isfinite(normalized_size[high_pivot]) and normalized_size[high_pivot] > 1.0:
            levels.append(
                {
                    "origin": high_pivot,
                    "price": float(high[high_pivot]),
                    "top": float(high[high_pivot] + atr_width),
                    "bottom": float(high[high_pivot] - atr_width),
                    "zone_start": high_pivot,
                    "color": bearish_zone_color,
                    "dashed": normalized_size[high_pivot] <= 1.5,
                }
            )
        if low_pivot is not None and np.isfinite(normalized_size[low_pivot]) and normalized_size[low_pivot] > 1.0:
            levels.append(
                {
                    "origin": low_pivot,
                    "price": float(low[low_pivot]),
                    "top": float(low[low_pivot] + atr_width),
                    "bottom": float(low[low_pivot] - atr_width),
                    "zone_start": low_pivot,
                    "color": bullish_zone_color,
                    "dashed": normalized_size[low_pivot] <= 1.5,
                }
            )
        if len(levels) > 6:
            levels = levels[-6:]
        for level in levels:
            if index > 0 and high[index - 1] > level["price"] and high[index] < level["price"]:
                level["zone_start"] = index
                level["top"] = float(level["price"] + atr_width)
                level["bottom"] = float(level["price"] - atr_width)
                level["color"] = bearish_zone_color
            if index > 0 and low[index - 1] < level["price"] and low[index] > level["price"]:
                level["zone_start"] = index
                level["top"] = float(level["price"] + atr_width)
                level["bottom"] = float(level["price"] - atr_width)
                level["color"] = bullish_zone_color

correction_boxes = []
roc = np.full(n, np.nan, dtype=float)
for index in range(15, n):
    if close[index - 15] != 0.0:
        roc[index] = (close[index] / close[index - 15] - 1.0) * 100.0
last_zero_cross_index = None
last_zero_cross_price = None
for index in range(1, n):
    if not np.isfinite(roc[index]) or not np.isfinite(roc[index - 1]):
        continue
    crossed_zero = (roc[index - 1] <= 0.0 < roc[index]) or (roc[index - 1] >= 0.0 > roc[index])
    if crossed_zero:
        last_zero_cross_index = index
        last_zero_cross_price = float(high[index])
    crossed_correction = roc[index - 1] >= -correction_percent and roc[index] < -correction_percent
    if crossed_correction and last_zero_cross_index is not None:
        correction_boxes = [box for box in correction_boxes if box["start"] != last_zero_cross_index]
        correction_boxes.append(
            {
                "start": int(last_zero_cross_index),
                "top": float(last_zero_cross_price),
                "bottom": float(low[index]),
                "created": index,
            }
        )
    correction_boxes = [box for box in correction_boxes if index - box["created"] < 10]

bull_rsi_segment = [None for _ in range(n)]
bear_rsi_segment = [None for _ in range(n)]
bull_price_segment = [None for _ in range(n)]
bear_price_segment = [None for _ in range(n)]

def draw_segment(target, start_index, start_value, end_index, end_value, color):
    span = end_index - start_index
    if span <= 0:
        return
    for index in range(start_index, end_index + 1):
        ratio = (index - start_index) / span
        target[index] = {
            "value": float(start_value + ratio * (end_value - start_value)),
            "color": color,
        }

previous_rsi_low = None
previous_rsi_high = None
for index in range(n):
    low_pivot = confirmed_pivot(rsi, index, divergence_length, False)
    high_pivot = confirmed_pivot(rsi, index, divergence_length, True)
    if low_pivot is not None:
        current = {"index": low_pivot, "rsi": float(rsi[low_pivot]), "price": float(low[low_pivot])}
        if previous_rsi_low is not None:
            distance = current["index"] - previous_rsi_low["index"]
            if 5 <= distance <= 60 and current["rsi"] > previous_rsi_low["rsi"] and current["price"] < previous_rsi_low["price"]:
                draw_segment(
                    bull_rsi_segment,
                    previous_rsi_low["index"],
                    previous_rsi_low["rsi"],
                    current["index"],
                    current["rsi"],
                    bullish_divergence_color,
                )
                draw_segment(
                    bull_price_segment,
                    previous_rsi_low["index"],
                    previous_rsi_low["price"],
                    current["index"],
                    current["price"],
                    bullish_divergence_color,
                )
        previous_rsi_low = current
    if high_pivot is not None:
        current = {"index": high_pivot, "rsi": float(rsi[high_pivot]), "price": float(high[high_pivot])}
        if previous_rsi_high is not None:
            distance = current["index"] - previous_rsi_high["index"]
            if 5 <= distance <= 60 and current["rsi"] < previous_rsi_high["rsi"] and current["price"] > previous_rsi_high["price"]:
                draw_segment(
                    bear_rsi_segment,
                    previous_rsi_high["index"],
                    previous_rsi_high["rsi"],
                    current["index"],
                    current["rsi"],
                    bearish_divergence_color,
                )
                draw_segment(
                    bear_price_segment,
                    previous_rsi_high["index"],
                    previous_rsi_high["price"],
                    current["index"],
                    current["price"],
                    bearish_divergence_color,
                )
        previous_rsi_high = current

def finite_list(values):
    return [float(value) if np.isfinite(value) else None for value in values]

plots = [
    {
        "name": "RSI",
        "data": finite_list(rsi),
        "color": "#D1D5DB",
        "type": "line",
        "lineWidth": 1,
        "overlay": False,
    },
    {"name": "Overbought", "data": [float(overbought) for _ in range(n)], "color": "#D1D5DB", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "RSI Midline", "data": [50.0 for _ in range(n)], "color": "rgba(209,213,219,0.50)", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Oversold", "data": [float(oversold) for _ in range(n)], "color": "#D1D5DB", "type": "line", "lineWidth": 1, "overlay": False},
    {
        "name": "Oversold Pressure",
        "title": False,
        "data": [
            float(rsi[index])
            if np.isfinite(rsi[index]) and rsi[index] < oversold
            else None
            for index in range(n)
        ],
        "color": "#EF4444",
        "type": "line",
        "lineWidth": 3,
        "overlay": False,
    },
    {
        "name": "Overbought Pressure",
        "title": False,
        "data": [
            float(rsi[index])
            if np.isfinite(rsi[index]) and rsi[index] > overbought
            else None
            for index in range(n)
        ],
        "color": "#22C55E",
        "type": "line",
        "lineWidth": 3,
        "overlay": False,
    },
]

if show_divergence:
    plots.extend(
        [
            {"name": "Bullish RSI Divergence", "data": bull_rsi_segment, "color": bullish_divergence_color, "type": "line", "lineWidth": 2, "overlay": False},
            {"name": "Bearish RSI Divergence", "data": bear_rsi_segment, "color": bearish_divergence_color, "type": "line", "lineWidth": 2, "overlay": False},
            {"name": "Bullish Price Divergence", "data": bull_price_segment, "color": bullish_divergence_color, "type": "line", "lineWidth": 1, "overlay": True},
            {"name": "Bearish Price Divergence", "data": bear_price_segment, "color": bearish_divergence_color, "type": "line", "lineWidth": 1, "overlay": True},
        ]
    )

layers = []
if show_support_resistance and n > 0:
    for level in levels:
        layers.append(
            {
                "type": "line",
                "startIndex": int(level["origin"]),
                "endIndex": max(0, n - 1),
                "y1": float(level["price"]),
                "y2": float(level["price"]),
                "color": "#D1D5DB",
                "lineWidth": 1,
                "dashed": bool(level["dashed"]),
                "extendRight": True,
            }
        )
        layers.append(
            {
                "type": "zone",
                "startIndex": int(level["zone_start"]),
                "endIndex": int(n - 1),
                "top": float(level["top"]),
                "bottom": float(level["bottom"]),
                "fillColor": level["color"],
                "borderColor": "rgba(0,0,0,0)",
                "borderSize": 0,
                "extendRight": False,
            }
        )

if show_corrections:
    for box in correction_boxes:
        layers.append(
            {
                "type": "zone",
                "startIndex": int(box["start"]),
                "endIndex": int(min(n - 1, box["created"] + 1)),
                "top": float(max(box["top"], box["bottom"])),
                "bottom": float(min(box["top"], box["bottom"])),
                "fillColor": correction_color,
                "borderColor": "rgba(0,0,0,0)",
                "borderSize": 0,
                "extendRight": False,
            }
        )

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": layers[-500:],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "rsi": finite_list(rsi),
        "atr200": finite_list(atr_200),
        "normalizedCandleSize": finite_list(normalized_size),
        "roc15": finite_list(roc),
        "supportResistanceLevels": [
            {
                "origin": int(level["origin"]),
                "price": float(level["price"]),
                "top": float(level["top"]),
                "bottom": float(level["bottom"]),
                "zoneStart": int(level["zone_start"]),
                "color": str(level["color"]),
                "dashed": bool(level["dashed"]),
            }
            for level in levels
        ],
        "correctionBoxes": [
            {
                "start": int(box["start"]),
                "created": int(box["created"]),
                "top": float(box["top"]),
                "bottom": float(box["bottom"]),
            }
            for box in correction_boxes
        ],
        "bullishRsiDivergence": bull_rsi_segment,
        "bearishRsiDivergence": bear_rsi_segment,
        "bullishPriceDivergence": bull_price_segment,
        "bearishPriceDivergence": bear_price_segment,
    },
}
