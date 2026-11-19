# ============================================================
# QuantDinger MACD Price Forecasting
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger MACD Price Forecasting"
my_indicator_description = "Learns price paths by MACD trend age and projects percentile envelopes when a new trend begins."

# @param fast_length int 12 MACD fast length range=2:1000:1
# @param slow_length int 26 MACD slow length range=2:1000:1
# @param signal_length int 9 MACD signal length range=2:1000:1
# @param trend_determination str MACD-Signal Trend determination values=MACD,MACD - Signal
# @param maximum_memory int 50 Samples retained per trend age range=2:1000:1
# @param forecasting_length int 100 Forecast horizon range=1:1000:1
# @param top_percentile float 80 Top percentile range=0:100:1
# @param average_percentile float 50 Middle percentile range=0:100:1
# @param bottom_percentile float 20 Bottom percentile range=0:100:1
# @param bullish_line_color color #3179F5 Bullish forecast line
# @param bearish_line_color color #FF5D00 Bearish forecast line
# @param bullish_area_color color #3179F5 Bullish forecast area
# @param bearish_area_color color #FF5D00 Bearish forecast area
# @param show_signal_area bool true Shade price against trend reference
# @param bullish_signal_color color #089981 Bullish reference area
# @param bearish_signal_color color #F23645 Bearish reference area

fast_length = min(1000, max(2, int(params.get("fast_length", 12))))
slow_length = min(1000, max(2, int(params.get("slow_length", 26))))
signal_length = min(1000, max(2, int(params.get("signal_length", 9))))
trend_mode = str(params.get("trend_determination", "MACD" + " - Signal"))
maximum_memory = min(1000, max(2, int(params.get("maximum_memory", 50))))
forecasting_length = min(1000, max(1, int(params.get("forecasting_length", 100))))
top_percentile = min(100.0, max(0.0, float(params.get("top_percentile", 80.0))))
average_percentile = min(100.0, max(0.0, float(params.get("average_percentile", 50.0))))
bottom_percentile = min(100.0, max(0.0, float(params.get("bottom_percentile", 20.0))))
bullish_line_color = str(params.get("bullish_line_color", "#3179F5"))
bearish_line_color = str(params.get("bearish_line_color", "#FF5D00"))
bullish_area_color = str(params.get("bullish_area_color", "#3179F5"))
bearish_area_color = str(params.get("bearish_area_color", "#FF5D00"))
show_signal_area = bool(params.get("show_signal_area", True))
bullish_signal_color = str(params.get("bullish_signal_color", "#089981"))
bearish_signal_color = str(params.get("bearish_signal_color", "#F23645"))

df = df.copy()
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)
times = df["time"].to_numpy(dtype=float) if "time" in df.columns else np.arange(n, dtype=float) * 3600000.0
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def _cross(first, second):
    result = np.zeros(len(first), dtype=bool)
    for index in range(1, len(first)):
        if np.all(np.isfinite([first[index], second[index], first[index - 1], second[index - 1]])):
            result[index] = ((first[index] > second[index] and first[index - 1] <= second[index - 1]) or
                             (first[index] < second[index] and first[index - 1] >= second[index - 1]))
    return result


def _interval():
    if n < 2:
        return 60000.0
    differences = np.diff(times)
    differences = differences[np.isfinite(differences) & (differences > 0.0)]
    return float(np.median(differences)) if len(differences) else 60000.0


fast_ema = _ema(close, fast_length)
slow_ema = _ema(close, slow_length)
macd = fast_ema - slow_ema
signal = _ema(macd, signal_length)
zero = np.zeros(n, dtype=float)
comparison = zero if trend_mode == "MACD" else signal
trigger = _cross(macd, comparison)
uptrend = np.isfinite(macd) & np.isfinite(comparison) & (macd > comparison)
downtrend = np.isfinite(macd) & np.isfinite(comparison) & (macd < comparison)

up_reference = np.full(n, np.nan, dtype=float)
down_reference = np.full(n, np.nan, dtype=float)
reference = np.full(n, np.nan, dtype=float)
up_index_history = np.zeros(n, dtype=int)
down_index_history = np.zeros(n, dtype=int)
up_memory = []
down_memory = []
up_index = 0
down_index = 0
up_initial = float(close[0]) if n else np.nan
down_initial = float(close[0]) if n else np.nan
forecast_events = []

for index in range(n):
    previous_up = bool(uptrend[index - 1]) if index > 0 else False
    previous_down = bool(downtrend[index - 1]) if index > 0 else False
    if uptrend[index] and not previous_up:
        up_initial = float(close[index])
    if downtrend[index] and not previous_down:
        down_initial = float(close[index])
    up_reference[index] = up_initial
    down_reference[index] = down_initial
    reference[index] = up_initial if uptrend[index] else down_initial

    if uptrend[index]:
        while len(up_memory) <= up_index:
            up_memory.append([])
        up_memory[up_index].insert(0, float(close[index] - up_initial))
        if len(up_memory[up_index]) > maximum_memory:
            up_memory[up_index].pop()
    if downtrend[index]:
        while len(down_memory) <= down_index:
            down_memory.append([])
        down_memory[down_index].insert(0, float(close[index] - down_initial))
        if len(down_memory[down_index]) > maximum_memory:
            down_memory[down_index].pop()

    up_index = up_index + 1 if uptrend[index] else 0
    down_index = down_index + 1 if downtrend[index] else 0
    up_index_history[index] = up_index
    down_index_history[index] = down_index

    if trigger[index]:
        memory = up_memory if uptrend[index] else down_memory
        trend_index = up_index if uptrend[index] else down_index
        initial_price = up_initial if uptrend[index] else down_initial
        maximum_horizon = len(memory)
        upper_values = []
        middle_values = []
        lower_values = []
        start_step = trend_index - 1
        end_step = min(trend_index + forecasting_length, maximum_horizon - 1)
        if end_step >= start_step:
            for step in range(start_step, end_step + 1):
                samples = np.asarray(memory[step], dtype=float)
                upper_values.append(float(initial_price + np.percentile(samples, top_percentile, method="linear")))
                middle_values.append(float(initial_price + np.percentile(samples, average_percentile, method="linear")))
                lower_values.append(float(initial_price + np.percentile(samples, bottom_percentile, method="linear")))
        forecast_events.append({
            "index": int(index), "direction": 1 if uptrend[index] else -1, "trendIndex": int(trend_index),
            "initialPrice": float(initial_price), "upper": upper_values, "middle": middle_values, "lower": lower_values,
        })

interval = _interval()
layers = []
latest_forecast = forecast_events[-1] if forecast_events else None
if latest_forecast is not None and latest_forecast["middle"]:
    event_index = latest_forecast["index"]
    middle_points = [{"time": float(times[event_index] + offset * interval), "price": value} for offset, value in enumerate(latest_forecast["middle"])]
    upper_points = [{"time": float(times[event_index] + offset * interval), "price": value} for offset, value in enumerate(latest_forecast["upper"])]
    lower_points = [{"time": float(times[event_index] + offset * interval), "price": value} for offset, value in enumerate(latest_forecast["lower"])]
    direction_up = latest_forecast["direction"] == 1
    line_color = bullish_line_color if direction_up else bearish_line_color
    area_color = bullish_area_color if direction_up else bearish_area_color
    layers = [
        {"type": "polyline", "role": "Forecast Median", "points": middle_points, "curved": False, "closed": False, "color": line_color, "lineWidth": 1},
        {"type": "polyline", "role": "Forecast Percentile Area", "points": upper_points + list(reversed(lower_points)), "curved": False, "closed": True, "color": "rgba(0,0,0,0)", "fillColor": area_color, "fillOpacity": 0.20, "lineWidth": 0},
    ]

reference_colors = []
reference_cloud = []
for index in range(n):
    changed = index == 0 or not np.isfinite(reference[index - 1]) or reference[index] != reference[index - 1]
    reference_colors.append(None if changed else "#808080")
    if show_signal_area and np.all(np.isfinite([close[index], reference[index]])):
        direction_up = bool(uptrend[index])
        reference_cloud.append({
            "upper": float(max(close[index], reference[index])), "lower": float(min(close[index], reference[index])),
            "color": bullish_signal_color if direction_up else bearish_signal_color,
            "topColor": bullish_signal_color if direction_up else "rgba(0,0,0,0)",
            "bottomColor": "rgba(0,0,0,0)" if direction_up else bearish_signal_color,
        })
    else:
        reference_cloud.append(None)

plots = [
    {"name": "Reference Price", "data": _finite(reference), "color": reference_colors, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Price to Reference Area", "title": False, "data": reference_cloud, "color": bullish_signal_color, "type": "cloud", "opacity": 0.50, "overlay": True},
]

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": [], "layers": layers, "tables": [],
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "fastEma": _finite(fast_ema), "slowEma": _finite(slow_ema), "macd": _finite(macd), "signal": _finite(signal),
        "trigger": trigger.tolist(), "uptrend": uptrend.tolist(), "downtrend": downtrend.tolist(),
        "upReference": _finite(up_reference), "downReference": _finite(down_reference), "reference": _finite(reference),
        "upIndex": up_index_history.tolist(), "downIndex": down_index_history.tolist(),
        "upMemory": up_memory, "downMemory": down_memory, "forecastEvents": forecast_events, "barInterval": float(interval),
    },
}
