# ============================================================
# QuantDinger Kalman Trend Levels
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Kalman Trend Levels"
my_indicator_description = (
    "Compares short and long adaptive Kalman filters, projects the latest "
    "trend support and resistance zones, and marks reversals and retests."
)

# @param short_length int 50 Short Kalman length range=2:300:1
# @param long_length int 150 Long Kalman length range=3:600:1
# @param retest_signals bool false Show retests of the latest trend zones
# @param show_trend_zones bool true Show persistent support and resistance zones
# @param show_candle_regime bool true Shade each candle range by trend state
# @param up_color color #13BD6E Uptrend color
# @param down_color color #AF0D4B Downtrend color

short_length = min(300, max(1, int(params.get("short_length", 50))))
long_length = min(600, max(1, int(params.get("long_length", 150))))
retest_signals = bool(params.get("retest_signals", False))
show_trend_zones = bool(params.get("show_trend_zones", True))
show_candle_regime = bool(params.get("show_candle_regime", True))
up_color = str(params.get("up_color", "#13BD6E"))
down_color = str(params.get("down_color", "#AF0D4B"))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

def kalman_series(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < 2:
        return result

    estimate = float(values[0])
    error_estimate = 1.0
    measurement_error = 0.01 * float(length)
    process_noise = 0.1 / float(length)

    for index in range(1, len(values)):
        gain = error_estimate / (error_estimate + measurement_error)
        estimate = estimate + gain * (float(values[index]) - estimate)
        error_estimate = (1.0 - gain) * error_estimate + process_noise
        result[index] = estimate
    return result

def wilder_atr(high_values, low_values, close_values, length):
    result = np.full(len(close_values), np.nan, dtype=float)
    if len(close_values) == 0:
        return result

    true_range = np.zeros(len(close_values), dtype=float)
    true_range[0] = high_values[0] - low_values[0]
    for index in range(1, len(close_values)):
        true_range[index] = max(
            high_values[index] - low_values[index],
            abs(high_values[index] - close_values[index - 1]),
            abs(low_values[index] - close_values[index - 1]),
        )

    if len(close_values) >= length:
        result[length - 1] = float(np.mean(true_range[:length]))
        for index in range(length, len(close_values)):
            result[index] = (
                result[index - 1] * (length - 1) + true_range[index]
            ) / length
    return result

short_kalman = kalman_series(close, short_length)
long_kalman = kalman_series(close, long_length)
atr_band = wilder_atr(high, low, close, 200) * 0.5

trend_up = np.zeros(n, dtype=bool)
trend_valid = np.zeros(n, dtype=bool)
short_rising = np.zeros(n, dtype=bool)
trend_change_up = np.zeros(n, dtype=bool)
trend_change_down = np.zeros(n, dtype=bool)

for index in range(n):
    trend_valid[index] = np.isfinite(short_kalman[index]) and np.isfinite(long_kalman[index])
    if trend_valid[index]:
        trend_up[index] = short_kalman[index] > long_kalman[index]
    if index >= 2 and np.isfinite(short_kalman[index - 2]) and np.isfinite(short_kalman[index]):
        short_rising[index] = short_kalman[index] > short_kalman[index - 2]
    if index > 1 and trend_valid[index] and trend_valid[index - 1]:
        trend_change_up[index] = trend_up[index] and not trend_up[index - 1]
        trend_change_down[index] = not trend_up[index] and trend_up[index - 1]

trend_spread = []
candle_regime = []
bull_support = []
bear_resistance = []
up_marks = [None for _ in range(n)]
down_marks = [None for _ in range(n)]
bull_retests = [None for _ in range(n)]
bear_retests = [None for _ in range(n)]

active_support_top = None
active_support_bottom = None
active_resistance_top = None
active_resistance_bottom = None
support_records = []
resistance_records = []

for index in range(n):
    if not trend_valid[index]:
        trend_spread.append(None)
        candle_regime.append(None)
        bull_support.append(None)
        bear_resistance.append(None)
        continue

    trend_color = up_color if trend_up[index] else down_color
    trend_spread.append({
        "upper": float(max(short_kalman[index], long_kalman[index])),
        "lower": float(min(short_kalman[index], long_kalman[index])),
        "color": trend_color,
    })

    if show_candle_regime:
        if trend_up[index] and short_rising[index]:
            candle_color = "rgba(19, 189, 110, 0.13)"
        elif not trend_up[index] and not short_rising[index]:
            candle_color = "rgba(175, 13, 75, 0.13)"
        else:
            candle_color = "rgba(120, 123, 134, 0.08)"
        candle_regime.append({
            "upper": float(high[index]),
            "lower": float(low[index]),
            "color": candle_color,
        })
    else:
        candle_regime.append(None)

    if trend_change_up[index]:
        up_marks[index] = float(low[index])
        if np.isfinite(atr_band[index]):
            for old_index in range(len(bull_support)):
                bull_support[old_index] = None
            active_support_bottom = float(low[index])
            active_support_top = float(low[index] + atr_band[index])
            support_records.append({"start": index, "end": index, "top": active_support_top, "bottom": active_support_bottom})

    if trend_change_down[index]:
        down_marks[index] = float(high[index])
        if np.isfinite(atr_band[index]):
            for old_index in range(len(bear_resistance)):
                bear_resistance[old_index] = None
            active_resistance_top = float(high[index])
            active_resistance_bottom = float(high[index] - atr_band[index])
            resistance_records.append({"start": index, "end": index, "top": active_resistance_top, "bottom": active_resistance_bottom})

    if index > 0 and not (trend_up[index] != trend_up[index - 1]):
        if support_records:
            support_records[-1]["end"] = index
        if resistance_records:
            resistance_records[-1]["end"] = index

    if retest_signals and index > 0:
        if (
            active_resistance_bottom is not None
            and high[index] < active_resistance_bottom
            and high[index - 1] >= active_resistance_bottom
        ):
            bear_retests[index - 1] = float(high[index - 1])
        if (
            active_support_top is not None
            and low[index] > active_support_top
            and low[index - 1] <= active_support_top
        ):
            bull_retests[index - 1] = float(low[index - 1])

    if show_trend_zones and active_support_top is not None:
        bull_support.append({
            "upper": active_support_top,
            "lower": active_support_bottom,
            "color": "rgba(19, 189, 110, 0.26)",
        })
    else:
        bull_support.append(None)

    if show_trend_zones and active_resistance_top is not None:
        bear_resistance.append({
            "upper": active_resistance_top,
            "lower": active_resistance_bottom,
            "color": "rgba(175, 13, 75, 0.26)",
        })
    else:
        bear_resistance.append(None)

short_plot = []
long_plot = []
bar_colors = []
for index in range(n):
    if np.isfinite(short_kalman[index]):
        short_plot.append({"value": float(short_kalman[index]), "color": up_color if short_rising[index] else down_color})
    else:
        short_plot.append(None)
    if np.isfinite(long_kalman[index]):
        long_plot.append({"value": float(long_kalman[index]), "color": up_color if trend_up[index] else down_color})
    else:
        long_plot.append(None)
    if show_candle_regime and trend_valid[index]:
        if trend_up[index] and short_rising[index]:
            bar_colors.append(up_color)
        elif not trend_up[index] and index >= 2 and short_kalman[index] < short_kalman[index - 2]:
            bar_colors.append(down_color)
        else:
            bar_colors.append("#787B86")
    else:
        bar_colors.append(None)

layers = []
if show_trend_zones:
    for record in support_records:
        layers.append({
            "type": "zone", "role": "Kalman Support",
            "startIndex": record["start"], "endIndex": record["end"],
            "top": record["top"], "bottom": record["bottom"],
            "fillColor": up_color, "borderColor": "rgba(0,0,0,0)",
            "borderSize": 0, "opacity": 0.40,
        })
    for record in resistance_records:
        layers.append({
            "type": "zone", "role": "Kalman Resistance",
            "startIndex": record["start"], "endIndex": record["end"],
            "top": record["top"], "bottom": record["bottom"],
            "fillColor": down_color, "borderColor": "rgba(0,0,0,0)",
            "borderSize": 0, "opacity": 0.40,
        })

plots = [
    {"name": "Trend Spread", "title": False, "data": trend_spread, "color": up_color, "type": "cloud", "opacity": 0.20, "overlay": True},
    {"name": "Short Kalman", "data": short_plot, "color": up_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Long Kalman", "data": long_plot, "color": up_color, "type": "line", "lineWidth": 2, "overlay": True},
]

signals = [
    {"type": "buy", "text": "K+", "name": "Kalman Trend Up", "data": up_marks, "color": up_color, "renderMode": "events"},
    {"type": "sell", "text": "K-", "name": "Kalman Trend Down", "data": down_marks, "color": down_color, "renderMode": "events"},
    {"type": "buy", "text": "+", "name": "Support Retest", "data": bull_retests, "color": up_color, "renderMode": "events" if retest_signals else "hidden"},
    {"type": "sell", "text": "x", "name": "Resistance Retest", "data": bear_retests, "color": down_color, "renderMode": "events" if retest_signals else "hidden"},
]


def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": [None] * n,
    "calculatedVars": {
        "shortKalman": _finite_or_none(short_kalman), "longKalman": _finite_or_none(long_kalman),
        "atrBand": _finite_or_none(atr_band), "trendValid": trend_valid.tolist(),
        "trendUp": trend_up.tolist(), "shortRising": short_rising.tolist(),
        "trendChangeUp": trend_change_up.tolist(), "trendChangeDown": trend_change_down.tolist(),
        "supportRecords": support_records, "resistanceRecords": resistance_records,
        "upMarks": up_marks, "downMarks": down_marks,
        "bullRetests": bull_retests, "bearRetests": bear_retests,
    },
}
