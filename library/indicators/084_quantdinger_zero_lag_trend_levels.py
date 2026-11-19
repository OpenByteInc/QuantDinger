# ============================================================
# QuantDinger Zero-Lag MA Trend Levels
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Zero-Lag MA Trend Levels"
my_indicator_description = (
    "Compares a zero-lag moving average with its base EMA, projects ATR-sized "
    "trend zones, and marks both trend shifts and later zone breaks."
)

# @param length int 15 Moving-average length range=2:200:1
# @param show_levels bool true Show ATR trend-level zones

length = max(2, int(params.get("length", 15)))
show_levels = bool(params.get("show_levels", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def pine_ema(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def wilder_average(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < period:
        return result
    previous = float(np.mean(source[:period]))
    result[period - 1] = previous
    for index in range(period, len(source)):
        previous = (previous * (period - 1) + source[index]) / period
        result[index] = previous
    return result


ema_value = pine_ema(close, length)
correction = close + (close - ema_value)
zero_lag = pine_ema(correction, length)
previous_close = np.concatenate(([close[0]], close[:-1])) if n else np.array([], dtype=float)
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close)))
atr = wilder_average(true_range, 200)

signal_up = np.zeros(n, dtype=bool)
signal_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if not all(np.isfinite(value) for value in (zero_lag[index], zero_lag[index - 1], ema_value[index], ema_value[index - 1])):
        continue
    signal_up[index] = zero_lag[index] > ema_value[index] and zero_lag[index - 1] <= ema_value[index - 1]
    signal_down[index] = zero_lag[index] < ema_value[index] and zero_lag[index - 1] >= ema_value[index - 1]

zero_lag_plot = []
ema_plot = []
average_cloud = []
for index in range(n):
    if not np.isfinite(zero_lag[index]) or not np.isfinite(ema_value[index]):
        zero_lag_plot.append(None)
        ema_plot.append(None)
        average_cloud.append(None)
        continue
    if index >= 3 and np.isfinite(zero_lag[index - 3]):
        zero_color = "#30D453" if zero_lag[index] > zero_lag[index - 3] else "#4043F1" if zero_lag[index] < zero_lag[index - 3] else "rgba(0, 0, 0, 0)"
    else:
        zero_color = "rgba(0, 0, 0, 0)"
    ema_color = "#30D453" if ema_value[index] < zero_lag[index] else "#4043F1"
    zero_lag_plot.append({"value": float(zero_lag[index]), "color": zero_color})
    ema_plot.append({"value": float(ema_value[index]), "color": ema_color})
    average_cloud.append(
        {
            "upper": float(max(zero_lag[index], ema_value[index])),
            "lower": float(min(zero_lag[index], ema_value[index])),
            "color": ema_color,
        }
    )

bull_zone = [None for _ in range(n)]
bear_zone = [None for _ in range(n)]
zone_break_up = np.zeros(n, dtype=bool)
zone_break_down = np.zeros(n, dtype=bool)
active_zone = None

for index in range(n):
    new_signal = bool(signal_up[index] or signal_down[index])
    if show_levels and new_signal and np.isfinite(atr[index]) and np.isfinite(zero_lag[index]):
        if active_zone is not None:
            previous_kind, previous_top, previous_bottom, previous_start = active_zone
            target = bull_zone if previous_kind == "bull" else bear_zone
            for extension_index in range(previous_start, min(n, index + 4)):
                if target[extension_index] is None:
                    target[extension_index] = {
                        "upper": float(previous_top),
                        "lower": float(previous_bottom),
                        "color": "#30D453" if previous_kind == "bull" else "#4043F1",
                    }
        if signal_up[index]:
            active_zone = ("bull", float(zero_lag[index]), float(zero_lag[index] - atr[index]), index)
        else:
            active_zone = ("bear", float(zero_lag[index] + atr[index]), float(zero_lag[index]), index)

    if show_levels and active_zone is not None:
        zone_kind, zone_top, zone_bottom, zone_start = active_zone
        target = bull_zone if zone_kind == "bull" else bear_zone
        target[index] = {
            "upper": float(zone_top),
            "lower": float(zone_bottom),
            "color": "#30D453" if zone_kind == "bull" else "#4043F1",
        }

        previous_signal = index > 0 and bool(signal_up[index - 1] or signal_down[index - 1])
        if index > 0 and not new_signal and not previous_signal:
            if ema_value[index] > zero_lag[index]:
                previous_high = high[index - 1]
                previous_bottom = zone_bottom
                zone_break_down[index] = high[index] < zone_bottom and previous_high >= previous_bottom
            if ema_value[index] < zero_lag[index]:
                previous_low = low[index - 1]
                previous_top = zone_top
                zone_break_up[index] = low[index] > zone_top and previous_low <= previous_top

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Zero-Lag MA", "data": zero_lag_plot, "color": "#30D453", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Base EMA", "data": ema_plot, "color": "#4043F1", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Average Spread", "title": False, "data": average_cloud, "color": "#30D453", "type": "cloud", "opacity": 0.18, "overlay": True},
        {"name": "Bull Trend Zone", "title": False, "data": bull_zone, "color": "#30D453", "type": "cloud", "opacity": 0.12, "overlay": True},
        {"name": "Bear Trend Zone", "title": False, "data": bear_zone, "color": "#4043F1", "type": "cloud", "opacity": 0.12, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "ZL UP", "name": "Bullish Shift", "data": [float(zero_lag[index]) if signal_up[index] else None for index in range(n)], "color": "#30D453", "renderMode": "events"},
        {"type": "sell", "text": "ZL DOWN", "name": "Bearish Shift", "data": [float(zero_lag[index]) if signal_down[index] else None for index in range(n)], "color": "#4043F1", "renderMode": "events"},
        {"type": "buy", "text": "LEVEL UP", "name": "Bull Zone Break", "data": [float(low[index - 1]) if index > 0 and zone_break_up[index] else None for index in range(n)], "color": "#30D453", "renderMode": "events"},
        {"type": "sell", "text": "LEVEL DOWN", "name": "Bear Zone Break", "data": [float(high[index - 1]) if index > 0 and zone_break_down[index] else None for index in range(n)], "color": "#4043F1", "renderMode": "events"},
    ],
    "layers": [],
}
