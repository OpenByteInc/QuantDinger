# ============================================================
# QuantDinger Ichimoku Oscillator
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Ichimoku Oscillator"
my_indicator_description = (
    "A four-layer Ichimoku oscillator that scores cloud, lagging, conversion, "
    "and base-line agreement with entry and profit-taking events."
)

# @param conversion_length int 8 Conversion length range=1:200:1
# @param base_length int 13 Base length range=1:300:1
# @param span_b_length int 26 Span B length range=1:500:1
# @param displacement int 13 Displacement range=1:200:1
# @param use_atr_filter bool true Use ATR whipsaw filter
# @param atr_length int 9 ATR length range=1:200:1
# @param atr_multiplier float 2.0 ATR multiplier range=0.1:10.0:0.1
# @param bounce_off bool false Enable support/resistance bounce entries
# @param show_ema bool true Show oscillator EMA
# @param ema_length int 9 Oscillator EMA length range=1:200:1
# @param ema_color color #D1C4E9 Oscillator EMA color
# @param ema_width int 1 Oscillator EMA width range=1:5:1
# @param ema_take_profit bool true Use EMA partial-profit events
# @param show_signals bool true Show entry and exit events
# @param long_color color #FFFFFF Long event color
# @param short_color color #FFFF00 Short event color
# @param colored_background bool true Color trend background
# @param up_background str Green Uptrend background values=Green,Red,Blue
# @param down_background str Blue Downtrend background values=Green,Red,Blue
# @param background_transparency int 60 Background transparency range=0:100:10
# @param up_color_1 color #00FA00 Uptrend layer 1
# @param up_color_2 color #00BE00 Uptrend layer 2
# @param up_color_3 color #008200 Uptrend layer 3
# @param up_color_4 color #004600 Uptrend layer 4
# @param down_color_1 color #FA0000 Downtrend layer 1
# @param down_color_2 color #BE0000 Downtrend layer 2
# @param down_color_3 color #820000 Downtrend layer 3
# @param down_color_4 color #460000 Downtrend layer 4
# @param zone_color color #D1D4DC Support/resistance zone color
# @param main_alert bool true Enable main-entry alerts
# @param additional_alert bool true Enable additional-entry alerts
# @param close_alert bool true Enable close alerts
# @param partial_alert bool true Enable partial-profit alerts

conversion_length = max(1, int(params.get("conversion_length", 8)))
base_length = max(1, int(params.get("base_length", 13)))
span_b_length = max(1, int(params.get("span_b_length", 26)))
displacement = max(1, int(params.get("displacement", 13)))
use_atr_filter = bool(params.get("use_atr_filter", True))
atr_length = max(1, int(params.get("atr_length", 9)))
atr_multiplier = max(0.1, float(params.get("atr_multiplier", 2.0)))
bounce_off = bool(params.get("bounce_off", False))
show_ema = bool(params.get("show_ema", True))
ema_length = max(1, int(params.get("ema_length", 9)))
ema_color = str(params.get("ema_color", "#D1C4E9"))
ema_width = min(5, max(1, int(params.get("ema_width", 1))))
ema_take_profit = bool(params.get("ema_take_profit", True))
show_signals = bool(params.get("show_signals", True))
long_color = str(params.get("long_color", "#FFFFFF"))
short_color = str(params.get("short_color", "#FFFF00"))
colored_background = bool(params.get("colored_background", True))
up_background = str(params.get("up_background", "Green"))
down_background = str(params.get("down_background", "Blue"))
background_transparency = min(100, max(0, int(params.get("background_transparency", 60))))
up_color_1 = str(params.get("up_color_1", "#00FA00"))
up_color_2 = str(params.get("up_color_2", "#00BE00"))
up_color_3 = str(params.get("up_color_3", "#008200"))
up_color_4 = str(params.get("up_color_4", "#004600"))
down_color_1 = str(params.get("down_color_1", "#FA0000"))
down_color_2 = str(params.get("down_color_2", "#BE0000"))
down_color_3 = str(params.get("down_color_3", "#820000"))
down_color_4 = str(params.get("down_color_4", "#460000"))
zone_color = str(params.get("zone_color", "#D1D4DC"))
main_alert = bool(params.get("main_alert", True))
additional_alert = bool(params.get("additional_alert", True))
close_alert = bool(params.get("close_alert", True))
partial_alert = bool(params.get("partial_alert", True))

df = df.copy()
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def ema(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


def shifted(values, periods):
    result = np.full(len(values), np.nan, dtype=float)
    if periods <= 0:
        return np.asarray(values, dtype=float).copy()
    if periods < len(values):
        result[periods:] = np.asarray(values, dtype=float)[:-periods]
    return result


conversion = (
    high.rolling(conversion_length, min_periods=conversion_length).max()
    + low.rolling(conversion_length, min_periods=conversion_length).min()
).to_numpy(dtype=float) / 2.0
base = (
    high.rolling(base_length, min_periods=base_length).max()
    + low.rolling(base_length, min_periods=base_length).min()
).to_numpy(dtype=float) / 2.0
span_a = (conversion + base) / 2.0
span_b = (
    high.rolling(span_b_length, min_periods=span_b_length).max()
    + low.rolling(span_b_length, min_periods=span_b_length).min()
).to_numpy(dtype=float) / 2.0

lag = displacement - 1
cloud_a = shifted(span_a, lag)
cloud_b = shifted(span_b, lag)
cloud_min = np.minimum(cloud_a, cloud_b)
cloud_max = np.maximum(cloud_a, cloud_b)
inthecloud = np.isfinite(cloud_min) & np.isfinite(cloud_max) & (close >= cloud_min) & (close <= cloud_max)
mtrend = np.zeros(n, dtype=int)
osc_line = np.full(n, np.nan, dtype=float)
for index in range(n):
    previous = mtrend[index - 1] if index > 0 else 0
    if np.isfinite(cloud_max[index]) and close[index] > cloud_max[index]:
        mtrend[index] = 1
    elif np.isfinite(cloud_min[index]) and close[index] < cloud_min[index]:
        mtrend[index] = -1
    else:
        mtrend[index] = previous
    boundary = cloud_min[index] if mtrend[index] == 1 else cloud_max[index]
    if np.isfinite(boundary):
        osc_line[index] = close[index] - boundary

older_min = shifted(cloud_min, lag)
older_max = shifted(cloud_max, lag)
lagging = osc_line.copy()
for index in range(n):
    if not np.isfinite(osc_line[index]):
        continue
    if mtrend[index] == 1 and np.isfinite(older_max[index]):
        lagging[index] += max(close[index] - older_max[index], 0.0)
    elif mtrend[index] == -1 and np.isfinite(older_min[index]):
        lagging[index] += min(close[index] - older_min[index], 0.0)

conversion_layer = lagging.copy()
valid_pair = np.isfinite(conversion) & np.isfinite(base)
conversion_layer[valid_pair & (mtrend == 1)] += np.maximum(conversion[valid_pair & (mtrend == 1)] - base[valid_pair & (mtrend == 1)], 0.0)
conversion_layer[valid_pair & (mtrend == -1)] += np.minimum(conversion[valid_pair & (mtrend == -1)] - base[valid_pair & (mtrend == -1)], 0.0)
cloud_layer = conversion_layer.copy()
valid_cloud = np.isfinite(span_a) & np.isfinite(span_b)
cloud_layer[valid_cloud & (mtrend == 1)] += np.maximum(span_a[valid_cloud & (mtrend == 1)] - span_b[valid_cloud & (mtrend == 1)], 0.0)
cloud_layer[valid_cloud & (mtrend == -1)] += np.minimum(span_a[valid_cloud & (mtrend == -1)] - span_b[valid_cloud & (mtrend == -1)], 0.0)

previous_close = np.concatenate(([close[0]], close[:-1]))
true_range = np.maximum(
    high.to_numpy(dtype=float) - low.to_numpy(dtype=float),
    np.maximum(np.abs(high.to_numpy(dtype=float) - previous_close), np.abs(low.to_numpy(dtype=float) - previous_close)),
)
atr = np.full(n, np.nan, dtype=float)
if n >= atr_length:
    atr[atr_length - 1] = float(np.mean(true_range[:atr_length]))
    for index in range(atr_length, n):
        atr[index] = (atr[index - 1] * (atr_length - 1) + true_range[index]) / atr_length
tolerance = atr * atr_multiplier if use_atr_filter else np.zeros(n, dtype=float)
trend = np.zeros(n, dtype=int)
entry_level = np.full(n, np.nan, dtype=float)
for index in range(n):
    previous_trend = trend[index - 1] if index > 0 else 0
    previous_mtrend = mtrend[index - 1] if index > 0 else 0
    current = previous_trend
    if mtrend[index] == 1:
        if previous_mtrend == -1:
            current = 0
        if current < 4 and np.isfinite(cloud_max[index]) and close[index] > cloud_max[index]:
            current = 1
            current += int(np.isfinite(lagging[index]) and np.isfinite(osc_line[index]) and lagging[index] > osc_line[index])
            current += int(valid_pair[index] and conversion[index] >= base[index] and (index == 0 or conversion[index] > conversion[index - 1] or base[index] > base[index - 1]))
            current += int(valid_cloud[index] and span_a[index] >= span_b[index])
        elif valid_pair[index] and np.isfinite(tolerance[index]) and conversion[index] < base[index] - tolerance[index]:
            current = 0
    elif mtrend[index] == -1:
        if previous_mtrend == 1:
            current = 0
        if current > -4 and np.isfinite(cloud_min[index]) and close[index] < cloud_min[index]:
            current = -1
            current -= int(np.isfinite(lagging[index]) and np.isfinite(osc_line[index]) and lagging[index] < osc_line[index])
            current -= int(valid_pair[index] and conversion[index] <= base[index] and (index == 0 or conversion[index] < conversion[index - 1] or base[index] < base[index - 1]))
            current -= int(valid_cloud[index] and span_a[index] <= span_b[index])
        elif valid_pair[index] and np.isfinite(tolerance[index]) and conversion[index] > base[index] + tolerance[index]:
            current = 0
    trend[index] = current
    entry_level[index] = close[index] if abs(current) == 4 and abs(previous_trend) != 4 else (entry_level[index - 1] if index > 0 else np.nan)

osc_ema = ema(cloud_layer, ema_length)
buy_event = np.concatenate(([False], (trend[1:] == 4) & (trend[:-1] != 4)))
sell_event = np.concatenate(([False], (trend[1:] == -4) & (trend[:-1] != -4)))
close_event = np.concatenate(([False], (trend[1:] != trend[:-1]) & (np.abs(trend[:-1]) == 4)))
bounce_long = np.zeros(n, dtype=bool)
bounce_short = np.zeros(n, dtype=bool)
partial_event = np.zeros(n, dtype=bool)
partial_alert_event = np.zeros(n, dtype=bool)
for index in range(1, n):
    bounce_long[index] = bool(bounce_off and trend[index] == 4 and close[index] > cloud_max[index] and inthecloud[index - 1]) if np.isfinite(cloud_max[index]) else False
    bounce_short[index] = bool(bounce_off and trend[index] == -4 and close[index] < cloud_min[index] and inthecloud[index - 1]) if np.isfinite(cloud_min[index]) else False
    profitable = (trend[index] == 4 and np.isfinite(entry_level[index]) and close[index] > entry_level[index]) or (trend[index] == -4 and np.isfinite(entry_level[index]) and close[index] < entry_level[index])
    conversion_cross = valid_pair[index] and valid_pair[index - 1] and ((trend[index] == 4 and conversion[index] < base[index] and conversion[index - 1] >= base[index - 1]) or (trend[index] == -4 and conversion[index] > base[index] and conversion[index - 1] <= base[index - 1]))
    ema_cross = ema_take_profit and np.isfinite(osc_ema[index]) and np.isfinite(osc_ema[index - 1]) and np.isfinite(cloud_layer[index]) and np.isfinite(cloud_layer[index - 1]) and ((trend[index] == 4 and osc_ema[index] > cloud_layer[index] and osc_ema[index - 1] <= cloud_layer[index - 1]) or (trend[index] == -4 and cloud_layer[index] > osc_ema[index] and cloud_layer[index - 1] <= osc_ema[index - 1]))
    partial_alert_event[index] = conversion_cross or ema_cross
    partial_event[index] = profitable and partial_alert_event[index]


def finite_line(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def layer_cloud(lower_values, upper_values, colors):
    result = []
    for index in range(n):
        lower = lower_values[index]
        upper = upper_values[index]
        if np.isfinite(lower) and np.isfinite(upper):
            result.append({"upper": float(max(lower, upper)), "lower": float(min(lower, upper)), "color": colors[index]})
        else:
            result.append(None)
    return result


def color_with_alpha(color, alpha):
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


regime_colors = [up_color_1 if value > 0 else down_color_1 if value < 0 else zone_color for value in mtrend]
zero = np.zeros(n, dtype=float)
base_area = layer_cloud(zero, osc_line, regime_colors)
lagging_area = layer_cloud(osc_line, lagging, regime_colors)
conversion_area = layer_cloud(lagging, conversion_layer, [up_color_3 if value >= 0 else down_color_3 for value in conversion_layer])
cloud_area = layer_cloud(conversion_layer, cloud_layer, [up_color_4 if value >= 0 else down_color_4 for value in cloud_layer])
signal_level = [float(cloud_layer[index]) if np.isfinite(cloud_layer[index]) else 0.0 for index in range(n)]

background_colors = []
for value in trend:
    if not colored_background:
        background_colors.append(None)
        continue
    if value > 0:
        intensity = min(255, max(0, int(159 + int(value) ** 3)))
        channels = {"Red": (intensity, 0, 0), "Green": (0, intensity, 0), "Blue": (0, 0, intensity)}
        red, green, blue = channels.get(up_background, channels["Green"])
        background_colors.append(f"rgba({red},{green},{blue},{1.0 - background_transparency / 100.0:.3f})")
    elif value < 0:
        intensity = min(255, max(0, int(159 - int(value) ** 3)))
        channels = {"Red": (intensity, 0, 0), "Green": (0, intensity, 0), "Blue": (0, 0, intensity)}
        red, green, blue = channels.get(down_background, channels["Blue"])
        background_colors.append(f"rgba({red},{green},{blue},{1.0 - background_transparency / 100.0:.3f})")
    else:
        background_colors.append(color_with_alpha(zone_color, 0.08))

signal_mode = "events" if show_signals else "hidden"

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Cloud Strength", "data": finite_line(cloud_layer), "color": up_color_4, "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Oscillator EMA", "data": finite_line(osc_ema) if show_ema else [None] * n, "color": ema_color, "type": "line", "lineWidth": ema_width, "overlay": False},
        {"name": "Price Cloud Layer", "title": False, "data": base_area, "color": up_color_1, "type": "cloud", "opacity": 0.18, "overlay": False},
        {"name": "Lagging Layer", "title": False, "data": lagging_area, "color": up_color_2, "type": "cloud", "opacity": 0.24, "overlay": False},
        {"name": "Conversion Layer", "title": False, "data": conversion_area, "color": up_color_3, "type": "cloud", "opacity": 0.30, "overlay": False},
        {"name": "Span Layer", "title": False, "data": cloud_area, "color": up_color_4, "type": "cloud", "opacity": 0.30, "overlay": False},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "Main Long", "data": [signal_level[i] if (show_signals or main_alert) and buy_event[i] else None for i in range(n)], "color": long_color, "renderMode": signal_mode},
        {"type": "sell", "text": "SELL", "name": "Main Short", "data": [signal_level[i] if (show_signals or main_alert) and sell_event[i] else None for i in range(n)], "color": short_color, "renderMode": signal_mode},
        {"type": "buy", "text": "ADD", "name": "Additional Long", "data": [signal_level[i] if (show_signals or additional_alert) and bounce_long[i] else None for i in range(n)], "color": long_color, "renderMode": signal_mode},
        {"type": "sell", "text": "ADD", "name": "Additional Short", "data": [signal_level[i] if (show_signals or additional_alert) and bounce_short[i] else None for i in range(n)], "color": short_color, "renderMode": signal_mode},
        {"type": "sell", "text": "EXIT", "name": "Trend Exit", "data": [signal_level[i] if (show_signals or close_alert) and close_event[i] else None for i in range(n)], "color": "#64748B", "renderMode": signal_mode},
        {"type": "sell", "text": "TP", "name": "Partial Profit", "data": [signal_level[i] if (show_signals or partial_alert) and partial_event[i] else None for i in range(n)], "color": ema_color, "renderMode": signal_mode},
    ],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": background_colors,
    "calculatedVars": {
        "conversion": finite_line(conversion), "base": finite_line(base),
        "spanA": finite_line(span_a), "spanB": finite_line(span_b),
        "cloudMin": finite_line(cloud_min), "cloudMax": finite_line(cloud_max),
        "inCloud": inthecloud.tolist(), "marketTrend": mtrend.tolist(),
        "oscillator": finite_line(osc_line), "lagging": finite_line(lagging),
        "conversionLayer": finite_line(conversion_layer), "cloudLayer": finite_line(cloud_layer),
        "atr": finite_line(atr), "tolerance": finite_line(tolerance),
        "trend": trend.tolist(), "entryLevel": finite_line(entry_level),
        "oscillatorEma": finite_line(osc_ema), "buyEvent": buy_event.tolist(),
        "sellEvent": sell_event.tolist(), "closeEvent": close_event.tolist(),
        "bounceLong": bounce_long.tolist(), "bounceShort": bounce_short.tolist(),
        "partialEvent": partial_event.tolist(), "partialAlertEvent": partial_alert_event.tolist(),
    },
}
