# ============================================================
# QuantDinger MACD Support Resistance
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger MACD Support Resistance"
my_indicator_description = (
    "Uses MACD signal crosses to capture nearby price pivots, keeps up to "
    "twenty unbroken support and resistance levels, and plots MACD context."
)

# @param fast_length int 12 Fast moving-average length range=1:1000:1
# @param slow_length int 26 Slow moving-average length range=1:1000:1
# @param signal_length int 9 Signal smoothing length range=1:50:1
# @param source str Close Price source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param oscillator_ma str EMA Oscillator average type values=EMA,SMA
# @param signal_ma str EMA Signal average type values=EMA,SMA
# @param show_levels bool true Show active price support and resistance
# @param max_levels int 8 Active level limit range=1:20:1
# @param show_crosses bool false Show MACD cross markers
# @param support_color color #2962FF Support and bullish MACD color
# @param resistance_color color #FF6D00 Resistance and bearish MACD color

fast_length = min(1000, max(1, int(params.get("fast_length", 12))))
slow_length = min(1000, max(1, int(params.get("slow_length", 26))))
signal_length = min(50, max(1, int(params.get("signal_length", 9))))
source_name = str(params.get("source", "Close"))
oscillator_ma = str(params.get("oscillator_ma", "EMA"))
signal_ma = str(params.get("signal_ma", "EMA"))
show_levels = bool(params.get("show_levels", True))
max_levels = min(20, max(1, int(params.get("max_levels", 8))))
show_crosses = bool(params.get("show_crosses", False))
support_color = str(params.get("support_color", "#2962FF"))
resistance_color = str(params.get("resistance_color", "#FF6D00"))

df = df.copy()
open_values = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

source = {
    "Open": open_values, "High": high, "Low": low, "HL2": (high + low) / 2.0,
    "HLC3": (high + low + close) / 3.0, "OHLC4": (open_values + high + low + close) / 4.0,
}.get(source_name, close).copy()

def sma_series(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result

def ema_series(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index in range(len(values)):
        if np.isfinite(values[index]):
            previous = float(values[index]) if not np.isfinite(previous) else alpha * float(values[index]) + (1.0 - alpha) * previous
            result[index] = previous
    return result

average_function = sma_series if oscillator_ma == "SMA" else ema_series
fast_average = average_function(source, fast_length)
slow_average = average_function(source, slow_length)
macd = fast_average - slow_average
signal_function = sma_series if signal_ma == "SMA" else ema_series
signal = signal_function(macd, signal_length)

cross_up = np.zeros(n, dtype=bool)
cross_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if not (
        np.isfinite(macd[index])
        and np.isfinite(signal[index])
        and np.isfinite(macd[index - 1])
        and np.isfinite(signal[index - 1])
    ):
        continue
    cross_up[index] = macd[index] > signal[index] and macd[index - 1] <= signal[index - 1]
    cross_down[index] = macd[index] < signal[index] and macd[index - 1] >= signal[index - 1]

active_levels = []
created_levels = []
removed_levels = []
active_level_history = []
for index in range(n):
    if index >= 5 and cross_down[index]:
        peak = float(np.max(high[index - 5:index + 1]))
        for offset in range(6):
            candidate_index = index - offset
            if float(high[candidate_index]) == peak:
                level = {
                    "support": False,
                    "value": peak,
                    "start": candidate_index,
                }
                active_levels.append(level)
                created_levels.append({"index": index, **level})

    if index >= 5 and cross_up[index]:
        trough = float(np.min(low[index - 5:index + 1]))
        for offset in range(6):
            candidate_index = index - offset
            if float(low[candidate_index]) == trough:
                level = {
                    "support": True,
                    "value": trough,
                    "start": candidate_index,
                }
                active_levels.append(level)
                created_levels.append({"index": index, **level})

    if len(active_levels) > max_levels:
        removed = active_levels.pop(0)
        removed_levels.append({"index": index, "reason": "capacity", **removed})

    level_index = 0
    while level_index < len(active_levels):
        level = active_levels[level_index]
        crossed = index > level["start"] and ((level["support"] and low[index] < level["value"]) or (not level["support"] and high[index] > level["value"]))
        if crossed:
            removed = active_levels.pop(level_index)
            removed_levels.append({"index": index, "reason": "cross", **removed})
        level_index += 1
    active_level_history.append([{**level} for level in active_levels])

macd_cloud = []
signal_plot = []
signal_colors = []
local_high = []
local_low = []
local_colors = []

for index in range(n):
    if np.isfinite(macd[index]) and np.isfinite(signal[index]):
        bullish = macd[index] > signal[index]
        color = support_color if bullish else resistance_color
        macd_cloud.append({
            "upper": float(max(macd[index], signal[index])),
            "lower": float(min(macd[index], signal[index])),
            "color": color,
        })
        signal_plot.append(float(signal[index]))
        signal_colors.append(color)
    else:
        macd_cloud.append(None)
        signal_plot.append(None)
        signal_colors.append(None)

    start = max(0, index - 99)
    finite_window = macd[start:index + 1]
    finite_window = finite_window[np.isfinite(finite_window)]
    if len(finite_window) > 0:
        local_high.append(float(np.max(finite_window)))
        local_low.append(float(np.min(finite_window)))
    else:
        local_high.append(None)
        local_low.append(None)
    local_colors.append(None if index % 5 == 0 or index % 4 == 0 or index % 3 == 0 else "rgba(120,123,134,0.70)")

plots = [
    {
        "name": "MACD Signal",
        "data": signal_plot,
        "color": signal_colors,
        "type": "line",
        "lineWidth": 2,
        "overlay": False,
    },
    {
        "name": "MACD Spread",
        "title": False,
        "data": macd_cloud,
        "color": "#787B86",
        "type": "cloud",
        "opacity": 0.30,
        "overlay": False,
    },
    {
        "name": "Zero Line",
        "title": False,
        "data": [0.0 for _ in range(n)],
        "color": "rgba(120, 123, 134, 0.48)",
        "type": "line",
        "lineWidth": 1,
        "overlay": False,
    },
    {
        "name": "Local MACD High",
        "title": False,
        "data": local_high,
        "color": local_colors,
        "type": "line",
        "lineWidth": 1,
        "overlay": False,
    },
    {
        "name": "Local MACD Low",
        "title": False,
        "data": local_low,
        "color": local_colors,
        "type": "line",
        "lineWidth": 1,
        "overlay": False,
    },
]

signals = []
if show_crosses:
    signals.extend([
        {"type": "buy", "text": "◆", "name": "MACD Up", "data": [float(signal[index]) if cross_up[index] else None for index in range(n)], "color": support_color, "renderMode": "events"},
        {"type": "sell", "text": "◆", "name": "MACD Down", "data": [float(signal[index]) if cross_down[index] else None for index in range(n)], "color": resistance_color, "renderMode": "events"},
    ])
layers = []
if show_levels:
    for number, level in enumerate(active_levels, start=1):
        level_data = [None for _ in range(n)]
        for index in range(level["start"], n):
            level_data[index] = float(level["value"])
        level_kind = "Support" if level["support"] else "Resistance"
        level_color = support_color if level["support"] else resistance_color
        plots.append({
            "name": f"{level_kind} {number}",
            "title": False,
            "data": level_data,
            "color": level_color,
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        })
        marker_data = [None for _ in range(n)]
        marker_data[level["start"]] = float(level["value"])
        signals.append({
            "type": "buy" if level["support"] else "sell",
            "text": "S" if level["support"] else "R",
            "name": f"MACD {level_kind}",
            "data": marker_data,
            "color": level_color,
            "renderMode": "events",
        })
        if n:
            layers.append({"type": "label", "index": n - 1, "price": float(level["value"]), "text": str(float(level["value"])), "color": level_color, "textColor": "#FFFFFF", "side": "left", "size": "small"})

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers, "tables": [],
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "source": [float(value) if np.isfinite(value) else None for value in source],
        "fastAverage": [float(value) if np.isfinite(value) else None for value in fast_average],
        "slowAverage": [float(value) if np.isfinite(value) else None for value in slow_average],
        "macd": [float(value) if np.isfinite(value) else None for value in macd],
        "signal": [float(value) if np.isfinite(value) else None for value in signal],
        "crossUp": cross_up.tolist(), "crossDown": cross_down.tolist(),
        "createdLevels": created_levels, "removedLevels": removed_levels, "activeLevelHistory": active_level_history,
        "activeLevels": active_levels, "localHigh": local_high, "localLow": local_low, "localColors": local_colors,
    },
}
