# ============================================================
# QuantDinger Higher-Timeframe Fibonacci Levels
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Higher-Timeframe Fibonacci Levels"
my_indicator_description = (
    "Aggregates a higher-timeframe candle from the chart history and projects "
    "direction-aware Fibonacci levels from either the current or last completed candle."
)

# @param higher_timeframe str 1D Higher timeframe values=15m,1H,4H,1D,1W,1M
# @param candle_selection str Last Higher-timeframe candle values=Last,Current
# @param show_price_labels bool true Show level and price labels
# @param show_htf_candle bool false Show the current higher-timeframe candle
# @param level_0_enabled bool true Enable level 0.000
# @param level_0 float 0.000 Level 0 value range=-5.0:5.0:0.001
# @param level_0236_enabled bool true Enable level 0.236
# @param level_0236 float 0.236 Level 1 value range=-5.0:5.0:0.001
# @param level_0382_enabled bool true Enable level 0.382
# @param level_0382 float 0.382 Level 2 value range=-5.0:5.0:0.001
# @param level_05_enabled bool true Enable level 0.500
# @param level_05 float 0.500 Level 3 value range=-5.0:5.0:0.001
# @param level_0618_enabled bool true Enable level 0.618
# @param level_0618 float 0.618 Level 4 value range=-5.0:5.0:0.001
# @param level_0786_enabled bool true Enable level 0.786
# @param level_0786 float 0.786 Level 5 value range=-5.0:5.0:0.001
# @param level_1_enabled bool true Enable level 1.000
# @param level_1 float 1.000 Level 6 value range=-5.0:5.0:0.001
# @param level_neg0382_enabled bool false Enable level -0.382
# @param level_neg0382 float -0.382 Level 7 value range=-5.0:5.0:0.001
# @param level_1236_enabled bool false Enable level 1.236
# @param level_1236 float 1.236 Level 8 value range=-5.0:5.0:0.001

higher_timeframe = str(params.get("higher_timeframe", "1D"))
candle_selection = str(params.get("candle_selection", "Last"))
show_price_labels = bool(params.get("show_price_labels", True))
show_htf_candle = bool(params.get("show_htf_candle", False))

level_0_enabled = bool(params.get("level_0_enabled", True))
level_0 = float(params.get("level_0", 0.000))
level_0236_enabled = bool(params.get("level_0236_enabled", True))
level_0236 = float(params.get("level_0236", 0.236))
level_0382_enabled = bool(params.get("level_0382_enabled", True))
level_0382 = float(params.get("level_0382", 0.382))
level_05_enabled = bool(params.get("level_05_enabled", True))
level_05 = float(params.get("level_05", 0.500))
level_0618_enabled = bool(params.get("level_0618_enabled", True))
level_0618 = float(params.get("level_0618", 0.618))
level_0786_enabled = bool(params.get("level_0786_enabled", True))
level_0786 = float(params.get("level_0786", 0.786))
level_1_enabled = bool(params.get("level_1_enabled", True))
level_1 = float(params.get("level_1", 1.000))
level_neg0382_enabled = bool(params.get("level_neg0382_enabled", False))
level_neg0382 = float(params.get("level_neg0382", -0.382))
level_1236_enabled = bool(params.get("level_1236_enabled", False))
level_1236 = float(params.get("level_1236", 1.236))

level_specs = [
    (level_1_enabled, level_1, "#2962FF"),
    (level_1236_enabled, level_1236, "#808080"),
    (level_neg0382_enabled, level_neg0382, "#808080"),
    (level_0786_enabled, level_0786, "#4F6BFF"),
    (level_0618_enabled, level_0618, "#008080"),
    (level_05_enabled, level_05, "#FF9800"),
    (level_0382_enabled, level_0382, "#F23645"),
    (level_0236_enabled, level_0236, "#00C853"),
    (level_0_enabled, level_0, "#2962FF"),
]

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
times = df["time"].to_numpy(dtype=float)
n = len(df)

if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0
timestamps = pd.to_datetime(times, unit="ms", utc=True)

def timeframe_keys(values, timeframe_name):
    normalized = timeframe_name.strip().upper()
    if normalized.endswith("M") and normalized != "1M":
        minutes = max(1, int(normalized[:-1]))
        return (times // float(minutes * 60000)).astype(np.int64)
    if normalized.endswith("H"):
        hours = max(1, int(normalized[:-1]))
        return (times // float(hours * 3600000)).astype(np.int64)
    if normalized in ("D", "1D"):
        return (times // 86400000.0).astype(np.int64)
    if normalized in ("W", "1W"):
        day_number = (times // 86400000.0).astype(np.int64)
        return (day_number + 3) // 7
    if normalized in ("M", "1M"):
        return np.asarray([int(value.year * 12 + value.month) for value in values], dtype=np.int64)
    try:
        minutes = max(1, int(normalized))
    except (TypeError, ValueError):
        minutes = 1440
    return (times // float(minutes * 60000)).astype(np.int64)

bucket_keys = timeframe_keys(timestamps, higher_timeframe) if n else np.asarray([], dtype=np.int64)
levels = []
for enabled, value, color in level_specs:
    levels.append(
        {
            "enabled": bool(enabled),
            "value": float(value),
            "color": color,
            "data": [None for _ in range(n)],
        }
    )

previous_candle = None
current_candle = None
current_start = 0
latest_values = None
for index in range(n):
    new_bucket = index == 0 or bucket_keys[index] != bucket_keys[index - 1]
    if new_bucket:
        previous_candle = current_candle
        current_start = index
        current_candle = {
            "open": float(open_price[index]),
            "high": float(high[index]),
            "low": float(low[index]),
            "close": float(close[index]),
        }
    else:
        current_candle["high"] = float(max(current_candle["high"], high[index]))
        current_candle["low"] = float(min(current_candle["low"], low[index]))
        current_candle["close"] = float(close[index])

    selected = previous_candle if candle_selection == "Last" else current_candle
    latest_values = None
    if selected is not None:
        candle_range = selected["high"] - selected["low"]
        rising = selected["close"] >= selected["open"]
        latest_values = []
        for level in levels:
            value = selected["high"] - candle_range * level["value"] if rising else selected["low"] + candle_range * level["value"]
            latest_values.append(float(value))
        for level_index, level in enumerate(levels):
            if level["enabled"]:
                level["data"][index] = latest_values[level_index]

layers = []
if show_htf_candle and current_candle is not None and n:
    body_top = max(current_candle["open"], current_candle["close"])
    body_bottom = min(current_candle["open"], current_candle["close"])
    candle_color = "rgba(0,255,0,0.30)" if current_candle["close"] >= current_candle["open"] else "rgba(255,0,0,0.30)"
    layers.append(
        {
            "type": "zone",
            "startIndex": int(current_start),
            "endIndex": int(n - 1),
            "top": float(body_top),
            "bottom": float(body_bottom),
            "fillColor": candle_color,
            "borderColor": candle_color,
            "borderSize": 1,
        }
    )
    if current_candle["high"] > body_top:
        layers.append(
            {
                "type": "zone",
                "startIndex": int(current_start),
                "endIndex": int(n - 1),
                "top": float(current_candle["high"]),
                "bottom": float(body_top),
                "fillColor": "rgba(128,128,128,0.25)",
                "borderColor": "rgba(128,128,128,0.25)",
                "borderSize": 0,
            }
        )
    if body_bottom > current_candle["low"]:
        layers.append(
            {
                "type": "zone",
                "startIndex": int(current_start),
                "endIndex": int(n - 1),
                "top": float(body_bottom),
                "bottom": float(current_candle["low"]),
                "fillColor": "rgba(128,128,128,0.25)",
                "borderColor": "rgba(128,128,128,0.25)",
                "borderSize": 0,
            }
        )

if show_price_labels and latest_values is not None and n:
    interval = float(np.median(np.diff(times))) if n > 1 else 3600000.0
    label_time = float(times[-1] + max(interval, 1.0))
    for level_index, level in enumerate(levels):
        if not level["enabled"]:
            continue
        price = latest_values[level_index]
        layers.append(
            {
                "type": "label",
                "timestamp": label_time,
                "price": float(price),
                "text": f"{level['value']:.3f} ({price:.4f})",
                "color": level["color"],
                "textColor": "#FFFFFF",
                "side": "right",
            }
        )

plots = []
for level_index, level in enumerate(levels):
    if level["enabled"] and any(value is not None for value in level["data"]):
        plots.append(
            {
                "name": f"Fibonacci Level {level_index + 1} · {level['value']:.3f}",
                "data": level["data"],
                "color": level["color"],
                "type": "line",
                "lineWidth": 1,
                "overlay": True,
            }
        )

output = {"name": my_indicator_name, "plots": plots, "signals": [], "layers": layers}
