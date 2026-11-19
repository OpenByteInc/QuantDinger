# ============================================================
# QuantDinger FVG Instantaneous Mitigation
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger FVG Instantaneous Mitigation"
my_indicator_description = (
    "Detects instantaneous bullish and bearish fair value gap reversals, "
    "tracks mitigation levels, and manages optional target, stop, and trailing zones."
)

# @param filter_width float 0.0 Minimum gap width in ATR units range=0.0:20.0:0.1
# @param show_tp bool false Show take-profit areas
# @param tp_multiplier float 4.0 Take-profit ATR multiplier range=0.0:20.0:0.25
# @param tp_color str #5B9CF6 Take-profit color
# @param show_sl bool false Show stop-loss areas
# @param sl_multiplier float 2.0 Stop-loss ATR multiplier range=0.0:20.0:0.25
# @param sl_color str #808080 Stop-loss color
# @param trailing_reset str every_signals Trailing-stop reset values=every_signals,inverse_signals
# @param trailing_multiplier float 3.0 Trailing-stop ATR multiplier range=0.0:20.0:0.25
# @param show_bull bool true Show bullish gaps
# @param bull_color str #089981 Bullish color
# @param bull_average bool true Show bullish gap averages
# @param show_bear bool true Show bearish gaps
# @param bear_color str #F23645 Bearish color
# @param bear_average bool true Show bearish gap averages


def clamp(value, lower, upper):
    return min(upper, max(lower, value))


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def rgba(color, opacity):
    value = str(color).strip().lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    try:
        red = int(value[0:2], 16)
        green = int(value[2:4], 16)
        blue = int(value[4:6], 16)
    except (ValueError, IndexError):
        red, green, blue = 128, 128, 128
    return f"rgba({red},{green},{blue},{clamp(float(opacity), 0.0, 1.0):.3f})"


def pine_rma(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    seed = []
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) < length:
                continue
            previous = float(np.mean(seed[-length:]))
        else:
            previous = (previous * (length - 1.0) + float(value)) / length
        result[index] = previous
    return result


def cloud_slots(records, count, slot_count, color, top_key="top", bottom_key="bottom"):
    slots = [[None for _ in range(count)] for _ in range(slot_count)]
    for record_number, record in enumerate(records[-120:]):
        slot = record_number % slot_count
        left = max(0, int(record["left"]))
        right = min(count - 1, int(record["right"]))
        for index in range(left, right + 1):
            slots[slot][index] = {
                "upper": float(max(record[top_key], record[bottom_key])),
                "lower": float(min(record[top_key], record[bottom_key])),
                "color": color,
            }
    return slots


def line_slots(records, count, slot_count, color):
    slots = [[None for _ in range(count)] for _ in range(slot_count)]
    for record_number, record in enumerate(records[-120:]):
        slot = record_number % slot_count
        left = max(0, int(record["start"]))
        right = min(count - 1, int(record["right"]))
        for index in range(left, right + 1):
            slots[slot][index] = {"value": float(record["value"]), "color": color}
    return slots


filter_width = clamp(float(params.get("filter_width", 0.0)), 0.0, 20.0)
show_tp = bool(params.get("show_tp", False))
tp_multiplier = clamp(float(params.get("tp_multiplier", 4.0)), 0.0, 20.0)
tp_color = str(params.get("tp_color", "#5B9CF6"))
show_sl = bool(params.get("show_sl", False))
sl_multiplier = clamp(float(params.get("sl_multiplier", 2.0)), 0.0, 20.0)
sl_color = str(params.get("sl_color", "#808080"))
trailing_reset = str(params.get("trailing_reset", "every_signals")).lower()
trailing_multiplier = clamp(float(params.get("trailing_multiplier", 3.0)), 0.0, 20.0)
show_bull = bool(params.get("show_bull", True))
bull_color = str(params.get("bull_color", "#089981"))
bull_average = bool(params.get("bull_average", True))
show_bear = bool(params.get("show_bear", True))
bear_color = str(params.get("bear_color", "#F23645"))
bear_average = bool(params.get("bear_average", True))
if trailing_reset not in ("every_signals", "inverse_signals"):
    trailing_reset = "every_signals"

df = df.copy()
high = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
low = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
close = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
n = len(df)

true_range = np.full(n, np.nan, dtype=float)
if n:
    true_range[0] = high[0] - low[0]
for index in range(1, n):
    true_range[index] = max(
        high[index] - low[index],
        abs(high[index] - close[index - 1]),
        abs(low[index] - close[index - 1]),
    )
atr_raw = pine_rma(true_range, 200)
atr = np.where(np.isfinite(atr_raw), atr_raw, 0.0)

bull = np.zeros(n, dtype=bool)
bear = np.zeros(n, dtype=bool)
bull_level = np.full(n, np.nan, dtype=float)
bear_level = np.full(n, np.nan, dtype=float)
for index in range(3, n):
    bull[index] = bool(
        show_bull
        and low[index - 3] > high[index - 1]
        and close[index - 2] < low[index - 3]
        and close[index] > low[index - 3]
        and low[index - 3] - high[index - 1] > atr[index] * filter_width
    )
    bear[index] = bool(
        show_bear
        and low[index - 1] > high[index - 3]
        and close[index - 2] > high[index - 3]
        and close[index] < high[index - 3]
        and low[index - 1] - high[index - 3] > atr[index] * filter_width
    )
    if bull[index]:
        bull_level[index] = (low[index - 3] + high[index - 1]) * 0.5
    if bear[index]:
        bear_level[index] = (low[index - 1] + high[index - 3]) * 0.5

trend = np.zeros(n, dtype=int)
trailing_stop = np.full(n, np.nan, dtype=float)
trailing_reached = np.zeros(n, dtype=bool)
bull_level_reached = np.zeros(n, dtype=bool)
bear_level_reached = np.zeros(n, dtype=bool)
bull_trade_reached = np.zeros(n, dtype=bool)
bear_trade_reached = np.zeros(n, dtype=bool)

bull_gap_records = []
bear_gap_records = []
bull_average_records = []
bear_average_records = []
long_tp_records = []
long_sl_records = []
short_tp_records = []
short_sl_records = []

current_trend = 0
current_trailing_stop = np.nan
current_trailing_reached = False
active_bull_level = np.nan
active_bear_level = np.nan
active_bull_average_record = None
active_bear_average_record = None
active_bull_level_reached = True
active_bear_level_reached = True
active_bull_trade_reached = True
active_bear_trade_reached = True
active_long_records = []
active_short_records = []
active_long_tp = None
active_long_sl = None
active_short_tp = None
active_short_sl = None

for index in range(n):
    previous_trend = current_trend
    if bull[index]:
        current_trend = 1
        active_bull_level = float(bull_level[index])
        active_bull_level_reached = False
        bull_gap_records.append({
            "left": index - 3, "right": index,
            "top": float(low[index - 3]), "bottom": float(high[index - 1]),
        })
        active_bull_average_record = {
            "start": index, "right": index, "value": active_bull_level,
        }
        bull_average_records.append(active_bull_average_record)
        active_bull_trade_reached = False
        active_bear_trade_reached = True
        active_long_records = []
        active_long_tp = None
        active_long_sl = None
        if show_tp:
            record = {
                "left": index, "right": index,
                "top": active_bull_level + atr[index] * tp_multiplier,
                "bottom": active_bull_level,
            }
            long_tp_records.append(record)
            active_long_records.append(record)
            active_long_tp = record
        if show_sl:
            record = {
                "left": index, "right": index,
                "top": active_bull_level,
                "bottom": active_bull_level - atr[index] * sl_multiplier,
            }
            long_sl_records.append(record)
            active_long_records.append(record)
            active_long_sl = record

    if bear[index]:
        current_trend = 0
        active_bear_level = float(bear_level[index])
        active_bear_level_reached = False
        bear_gap_records.append({
            "left": index - 3, "right": index,
            "top": float(low[index - 1]), "bottom": float(high[index - 3]),
        })
        active_bear_average_record = {
            "start": index, "right": index, "value": active_bear_level,
        }
        bear_average_records.append(active_bear_average_record)
        active_bear_trade_reached = False
        active_bull_trade_reached = True
        active_short_records = []
        active_short_tp = None
        active_short_sl = None
        if show_tp:
            record = {
                "left": index, "right": index,
                "top": active_bear_level,
                "bottom": active_bear_level - atr[index] * tp_multiplier,
            }
            short_tp_records.append(record)
            active_short_records.append(record)
            active_short_tp = record
        if show_sl:
            record = {
                "left": index, "right": index,
                "top": active_bear_level + atr[index] * sl_multiplier,
                "bottom": active_bear_level,
            }
            short_sl_records.append(record)
            active_short_records.append(record)
            active_short_sl = record

    if not active_bull_level_reached and active_bull_average_record is not None:
        active_bull_average_record["right"] = index
        if close[index] < active_bull_level:
            active_bull_level_reached = True
    if not active_bear_level_reached and active_bear_average_record is not None:
        active_bear_average_record["right"] = index
        if close[index] > active_bear_level:
            active_bear_level_reached = True

    if not active_bull_trade_reached:
        for record in active_long_records:
            record["right"] = index
        long_tp_hit = bool(active_long_tp is not None and high[index] > active_long_tp["top"])
        long_sl_hit = bool(active_long_sl is not None and low[index] < active_long_sl["bottom"])
        if long_tp_hit or long_sl_hit:
            active_bull_trade_reached = True
    if not active_bear_trade_reached:
        for record in active_short_records:
            record["right"] = index
        short_tp_hit = bool(active_short_tp is not None and low[index] < active_short_tp["bottom"])
        short_sl_hit = bool(active_short_sl is not None and high[index] > active_short_sl["top"])
        if short_tp_hit or short_sl_hit:
            active_bear_trade_reached = True

    trigger = bool(bull[index] or bear[index]) if trailing_reset == "every_signals" else current_trend != previous_trend
    if trigger:
        current_trailing_stop = close[index] - atr[index] * trailing_multiplier if current_trend == 1 else close[index] + atr[index] * trailing_multiplier
        current_trailing_reached = False
    elif np.isfinite(current_trailing_stop):
        if current_trend == 1 and close[index] - current_trailing_stop > atr[index] * trailing_multiplier:
            current_trailing_stop = close[index] - atr[index] * trailing_multiplier
        elif current_trend == 0 and current_trailing_stop - close[index] > atr[index] * trailing_multiplier:
            current_trailing_stop = close[index] + atr[index] * trailing_multiplier
        if current_trend == 1 and close[index] < current_trailing_stop:
            current_trailing_reached = True
        elif current_trend == 0 and close[index] > current_trailing_stop:
            current_trailing_reached = True

    trend[index] = current_trend
    trailing_stop[index] = current_trailing_stop
    trailing_reached[index] = current_trailing_reached
    bull_level_reached[index] = active_bull_level_reached
    bear_level_reached[index] = active_bear_level_reached
    bull_trade_reached[index] = active_bull_trade_reached
    bear_trade_reached[index] = active_bear_trade_reached

trailing_plot = []
bar_colors = []
for index in range(n):
    if np.isfinite(trailing_stop[index]) and not trailing_reached[index] and not bull[index] and not bear[index]:
        trailing_plot.append({
            "value": float(trailing_stop[index]),
            "color": bull_color if trend[index] == 1 else bear_color,
        })
    else:
        trailing_plot.append(None)
    if trailing_reached[index]:
        bar_colors.append(None)
    elif trend[index] == 1 and not bull_trade_reached[index]:
        bar_colors.append(bull_color)
    elif trend[index] == 0 and not bear_trade_reached[index]:
        bar_colors.append(bear_color)
    else:
        bar_colors.append(None)

plots = [
    {
        "name": "Trailing Stop", "data": trailing_plot, "color": bull_color,
        "type": "line", "lineWidth": 2, "style": "linebr", "connectNulls": False, "overlay": True,
    }
]
plots.append({
    "name": "Price Reference", "title": False, "data": finite(close),
    "color": "rgba(255,255,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True,
})
for number, slot in enumerate(cloud_slots(bull_gap_records, n, 8, rgba(bull_color, 0.50))):
    plots.append({"name": f"Bull Gap {number + 1}", "title": False, "data": slot, "type": "cloud", "overlay": True})
for number, slot in enumerate(cloud_slots(bear_gap_records, n, 8, rgba(bear_color, 0.50))):
    plots.append({"name": f"Bear Gap {number + 1}", "title": False, "data": slot, "type": "cloud", "overlay": True})
if bull_average:
    for number, slot in enumerate(line_slots(bull_average_records, n, 8, bull_color)):
        plots.append({
            "name": f"Bull Average {number + 1}", "title": False, "data": slot,
            "color": bull_color, "type": "line", "style": "dashed", "lineWidth": 1,
            "connectNulls": False, "overlay": True,
        })
if bear_average:
    for number, slot in enumerate(line_slots(bear_average_records, n, 8, bear_color)):
        plots.append({
            "name": f"Bear Average {number + 1}", "title": False, "data": slot,
            "color": bear_color, "type": "line", "style": "dashed", "lineWidth": 1,
            "connectNulls": False, "overlay": True,
        })
for name, records, color in (
    ("Long Target", long_tp_records, rgba(tp_color, 0.20)),
    ("Long Stop", long_sl_records, rgba(sl_color, 0.20)),
    ("Short Target", short_tp_records, rgba(tp_color, 0.20)),
    ("Short Stop", short_sl_records, rgba(sl_color, 0.20)),
):
    for number, slot in enumerate(cloud_slots(records, n, 4, color)):
        plots.append({"name": f"{name} {number + 1}", "title": False, "data": slot, "type": "cloud", "overlay": True})

signals = [
    {
        "type": "buy", "name": "Bullish Instant Mitigation", "text": "▲",
        "color": bull_color, "renderMode": "events",
        "data": [float(low[index]) if bull[index] else None for index in range(n)],
    },
    {
        "type": "sell", "name": "Bearish Instant Mitigation", "text": "▼",
        "color": bear_color, "renderMode": "events",
        "data": [float(high[index]) if bear[index] else None for index in range(n)],
    },
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [plot for plot in plots if any(item is not None for item in plot.get("data", []))],
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "trueRange": finite(true_range), "atrRaw": finite(atr_raw), "atr": finite(atr),
        "bull": bull.tolist(), "bear": bear.tolist(),
        "bullLevel": finite(bull_level), "bearLevel": finite(bear_level),
        "trend": trend.tolist(), "trailingStop": finite(trailing_stop),
        "trailingReached": trailing_reached.tolist(),
        "bullLevelReached": bull_level_reached.tolist(), "bearLevelReached": bear_level_reached.tolist(),
        "bullTradeReached": bull_trade_reached.tolist(), "bearTradeReached": bear_trade_reached.tolist(),
        "bullGapRecords": bull_gap_records, "bearGapRecords": bear_gap_records,
        "bullAverageRecords": bull_average_records, "bearAverageRecords": bear_average_records,
        "longTargetRecords": long_tp_records, "longStopRecords": long_sl_records,
        "shortTargetRecords": short_tp_records, "shortStopRecords": short_sl_records,
    },
}
