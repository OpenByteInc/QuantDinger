# ============================================================
# QuantDinger Fibonacci Price Action
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Fibonacci Price Action"
my_indicator_description = (
    "Builds pivot-qualified Fibonacci retracements, support/resistance zones and "
    "trendlines, with selectable long and short price-test alerts."
)

# @param find_long bool true Find long entries
# @param find_short bool false Find short entries
# @param left_bars int 10 Pivot left bars range=1:100:1
# @param right_bars int 5 Pivot right bars range=1:100:1
# @param fibonacci_width int 2 Fibonacci line width range=1:5:1
# @param zone_width int 2 Support/resistance width range=1:5:1
# @param trendline_width int 2 Trendline width range=1:5:1
# @param fibonacci_extension int 5 Fibonacci extension bars range=1:100:1
# @param zone_extension int 5 Zone extension bars range=1:100:1
# @param long_alert str Fibo_0.5 Long alert test values=Fibo_0.382,Fibo_0.5,Fibo_0.618,Fibo_0.786,Support_Zone,Trendline
# @param short_alert str Fibo_0.5 Short alert test values=Fibo_0.382,Fibo_0.5,Fibo_0.618,Fibo_0.786,Resistant_Zone,Trendline

find_long = bool(params.get("find_long", True))
find_short = bool(params.get("find_short", False))
left_bars = min(100, max(1, int(params.get("left_bars", 10))))
right_bars = min(100, max(1, int(params.get("right_bars", 5))))
fibonacci_width = min(5, max(1, int(params.get("fibonacci_width", 2))))
zone_width = min(5, max(1, int(params.get("zone_width", 2))))
trendline_width = min(5, max(1, int(params.get("trendline_width", 2))))
fibonacci_extension = min(100, max(1, int(params.get("fibonacci_extension", 5))))
zone_extension = min(100, max(1, int(params.get("zone_extension", 5))))
long_alert = str(params.get("long_alert", "Fibo_0.5")).replace("_", " ")
short_alert = str(params.get("short_alert", "Fibo_0.5")).replace("_", " ")

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
time_values = df["time"].astype(float).to_numpy(dtype=float)
n = len(df)

positive_intervals = np.diff(time_values)
positive_intervals = positive_intervals[positive_intervals > 0.0]
interval = float(np.median(positive_intervals)) if positive_intervals.size else 0.0

def time_for_index(index):
    if n == 0:
        return 0.0
    if index < n:
        return float(time_values[max(0, index)])
    return float(time_values[-1] + interval * (index - (n - 1)))

def make_line(start_index, start_price, end_index, end_price, color, width, dashed=False):
    layer = {
        "type": "line",
        "y1": float(start_price),
        "y2": float(end_price),
        "color": color,
        "lineWidth": int(width),
        "dashed": bool(dashed),
    }
    if start_index < n:
        layer["startIndex"] = int(max(0, start_index))
    else:
        layer["start"] = time_for_index(start_index)
    if end_index < n:
        layer["endIndex"] = int(max(0, end_index))
    else:
        layer["end"] = time_for_index(end_index)
    return layer

def append_dotted(target, start_index, start_price, end_index, end_price, color, width):
    span = max(1, int(end_index - start_index))
    segment_start = int(start_index)
    while segment_start <= end_index:
        segment_end = min(segment_start + 1, end_index)
        start_fraction = (segment_start - start_index) / span
        end_fraction = (segment_end - start_index) / span
        y1 = start_price + (end_price - start_price) * start_fraction
        y2 = start_price + (end_price - start_price) * end_fraction
        target.append(make_line(segment_start, y1, segment_end, y2, color, width, False))
        segment_start += 3

pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
pivot_high_index = np.full(n, -1, dtype=int)
pivot_low_index = np.full(n, -1, dtype=int)
for confirmation_index in range(left_bars + right_bars, n):
    pivot_index = confirmation_index - right_bars
    first_index = pivot_index - left_bars
    last_index = pivot_index + right_bars + 1
    if high[pivot_index] >= float(np.max(high[first_index:last_index])):
        pivot_high[confirmation_index] = float(high[pivot_index])
        pivot_high_index[confirmation_index] = pivot_index
    if low[pivot_index] <= float(np.min(low[first_index:last_index])):
        pivot_low[confirmation_index] = float(low[pivot_index])
        pivot_low_index[confirmation_index] = pivot_index

high_pivots = []
low_pivots = []
drawing_layers = []
latest_long_labels = []
latest_short_labels = []
long_alert_events = np.zeros(n, dtype=bool)
short_alert_events = np.zeros(n, dtype=bool)

fib_ratios = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
fib_colors = ["rgba(192,192,192,0.40)", "rgba(242,54,69,0.40)", "rgba(129,199,132,0.40)", "rgba(0,128,0,0.40)", "rgba(8,153,129,0.40)", "rgba(0,188,212,0.40)", "rgba(192,192,192,0.40)"]

for index in range(n):
    if np.isfinite(pivot_high[index]):
        pivot_index = int(pivot_high_index[index])
        body_low = float(max(open_price[pivot_index], close[pivot_index]))
        high_pivots.append({"index": pivot_index, "price": float(pivot_high[index]), "body": body_low})

        if find_long and len(high_pivots) >= 2 and len(low_pivots) >= 1:
            current_high = high_pivots[-1]
            previous_high = high_pivots[-2]
            latest_low = low_pivots[-1]
            if current_high["price"] > previous_high["price"]:
                start_index = int(latest_low["index"])
                end_index = int(current_high["index"])
                extension_end = end_index + right_bars + fibonacci_extension
                difference = current_high["price"] - latest_low["price"]
                drawing_layers.append(make_line(start_index, latest_low["price"], end_index, current_high["price"], "#00C853", 2, False))
                drawing_layers.append(
                    {
                        "type": "label",
                        "index": end_index,
                        "price": float(current_high["price"]),
                        "text": "▶",
                        "color": "rgba(0,0,0,0.0)",
                        "textColor": "#00C853",
                        "side": "right",
                    }
                )
                current_levels = []
                for ratio, color in zip(fib_ratios, fib_colors):
                    level_price = current_high["price"] - difference * ratio
                    current_levels.append(level_price)
                    drawing_layers.append(make_line(start_index, level_price, extension_end, level_price, color, fibonacci_width, False))

                zone_end = end_index + right_bars + zone_extension
                zone_top = float(previous_high["price"])
                zone_bottom = float(previous_high["body"])
                zone = {
                    "type": "zone",
                    "startIndex": int(previous_high["index"]),
                    "top": max(zone_top, zone_bottom),
                    "bottom": min(zone_top, zone_bottom),
                    "fillColor": "rgba(0, 200, 83, 0.15)",
                    "borderColor": "rgba(0, 200, 83, 0.80)",
                    "borderSize": int(zone_width),
                }
                if zone_end < n:
                    zone["endIndex"] = int(zone_end)
                else:
                    zone["end"] = time_for_index(zone_end)
                drawing_layers.append(zone)

                if len(high_pivots) >= 3:
                    older_high = high_pivots[-3]
                    if previous_high["price"] < older_high["price"] and previous_high["index"] != older_high["index"]:
                        slope = (previous_high["price"] - older_high["price"]) / (previous_high["index"] - older_high["index"])
                        projected = previous_high["price"] + slope * (index - previous_high["index"])
                        append_dotted(drawing_layers, older_high["index"], older_high["price"], previous_high["index"], previous_high["price"], "#F23645", trendline_width)
                        append_dotted(drawing_layers, previous_high["index"], previous_high["price"], index, projected, "#F23645", trendline_width)
                    else:
                        projected = np.nan
                else:
                    projected = np.nan

                selected_ratio = 0.382 if long_alert == "Fibo 0.382" else 0.5 if long_alert == "Fibo 0.5" else 0.618 if long_alert == "Fibo 0.618" else 0.786
                selected_level = current_high["price"] - difference * selected_ratio
                long_alert_events[index] = (
                    (long_alert.startswith("Fibo") and close[index] < selected_level)
                    or (long_alert == "Support Zone" and close[index] < previous_high["price"])
                    or (long_alert == "Trendline" and np.isfinite(projected) and close[index] < projected * 1.005)
                )
                label_time = time_for_index(n)
                latest_long_labels = [
                    {
                        "type": "label",
                        "timestamp": label_time,
                        "price": float(level_price),
                        "text": f"{ratio:g}",
                        "color": "rgba(0,0,0,0.0)",
                        "textColor": "#FFD700",
                        "side": "right",
                    }
                    for ratio, level_price in zip(fib_ratios, current_levels)
                ]

    if np.isfinite(pivot_low[index]):
        pivot_index = int(pivot_low_index[index])
        body_high = float(min(open_price[pivot_index], close[pivot_index]))
        low_pivots.append({"index": pivot_index, "price": float(pivot_low[index]), "body": body_high})

        if find_short and len(low_pivots) >= 2 and len(high_pivots) >= 1:
            current_low = low_pivots[-1]
            previous_low = low_pivots[-2]
            latest_high = high_pivots[-1]
            if current_low["price"] < previous_low["price"]:
                start_index = int(latest_high["index"])
                end_index = int(current_low["index"])
                extension_end = end_index + right_bars + fibonacci_extension
                difference = latest_high["price"] - current_low["price"]
                drawing_layers.append(make_line(start_index, latest_high["price"], end_index, current_low["price"], "#F23645", 2, False))
                drawing_layers.append(
                    {
                        "type": "label",
                        "index": end_index,
                        "price": float(current_low["price"]),
                        "text": "▶",
                        "color": "rgba(0,0,0,0.0)",
                        "textColor": "#F23645",
                        "side": "right",
                    }
                )
                current_levels = []
                for ratio, color in zip(fib_ratios, fib_colors):
                    level_price = current_low["price"] + difference * ratio
                    current_levels.append(level_price)
                    drawing_layers.append(make_line(start_index, level_price, extension_end, level_price, color, fibonacci_width, False))

                zone_end = end_index + right_bars + zone_extension
                zone_top = float(previous_low["body"])
                zone_bottom = float(previous_low["price"])
                zone = {
                    "type": "zone",
                    "startIndex": int(previous_low["index"]),
                    "top": max(zone_top, zone_bottom),
                    "bottom": min(zone_top, zone_bottom),
                    "fillColor": "rgba(242, 54, 69, 0.15)",
                    "borderColor": "rgba(242, 54, 69, 0.80)",
                    "borderSize": int(zone_width),
                }
                if zone_end < n:
                    zone["endIndex"] = int(zone_end)
                else:
                    zone["end"] = time_for_index(zone_end)
                drawing_layers.append(zone)

                if len(low_pivots) >= 3:
                    older_low = low_pivots[-3]
                    if previous_low["price"] > older_low["price"] and previous_low["index"] != older_low["index"]:
                        slope = (previous_low["price"] - older_low["price"]) / (previous_low["index"] - older_low["index"])
                        projected = previous_low["price"] + slope * (index - previous_low["index"])
                        append_dotted(drawing_layers, older_low["index"], older_low["price"], previous_low["index"], previous_low["price"], "#00C853", trendline_width)
                        append_dotted(drawing_layers, previous_low["index"], previous_low["price"], index, projected, "#00C853", trendline_width)
                    else:
                        projected = np.nan
                else:
                    projected = np.nan

                selected_ratio = 0.382 if short_alert == "Fibo 0.382" else 0.5 if short_alert == "Fibo 0.5" else 0.618 if short_alert == "Fibo 0.618" else 0.786
                selected_level = current_low["price"] + difference * selected_ratio
                short_alert_events[index] = (
                    (short_alert.startswith("Fibo") and close[index] > selected_level)
                    or (short_alert == "Resistant Zone" and close[index] > previous_low["price"])
                    or (short_alert == "Trendline" and np.isfinite(projected) and close[index] > projected * 0.995)
                )
                label_time = time_for_index(n)
                latest_short_labels = [
                    {
                        "type": "label",
                        "timestamp": label_time,
                        "price": float(level_price),
                        "text": f"{ratio:g}",
                        "color": "rgba(0,0,0,0.0)",
                        "textColor": "#FFD700",
                        "side": "right",
                    }
                    for ratio, level_price in zip(fib_ratios, current_levels)
                ]

layers = drawing_layers[-500:] + latest_long_labels + latest_short_labels
pivot_high_data = [None for _ in range(n)]
pivot_low_data = [None for _ in range(n)]
long_alert_data = [None for _ in range(n)]
short_alert_data = [None for _ in range(n)]
for record in high_pivots:
    pivot_high_data[record["index"]] = {"value": record["price"], "text": "◆", "color": "#F23645"}
for record in low_pivots:
    pivot_low_data[record["index"]] = {"value": record["price"], "text": "◆", "color": "#00C853"}
for index in range(n):
    if long_alert_events[index]:
        long_alert_data[index] = {"value": float(low[index]), "text": long_alert, "color": "#00C853"}
    if short_alert_events[index]:
        short_alert_data[index] = {"value": float(high[index]), "text": short_alert, "color": "#F23645"}

plots = [
    {"name": "Price Reference", "title": False, "data": [float(value) for value in close], "color": "rgba(255,255,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True}
]
signals = [
    {"type": "sell", "text": "◆", "name": "Pivot High", "data": pivot_high_data, "color": "#F23645", "renderMode": "events"},
    {"type": "buy", "text": "◆", "name": "Pivot Low", "data": pivot_low_data, "color": "#00C853", "renderMode": "events"},
    {"type": "buy", "text": "Alert", "name": "Long Price Test", "data": long_alert_data, "color": "#00C853", "renderMode": "events"},
    {"type": "sell", "text": "Alert", "name": "Short Price Test", "data": short_alert_data, "color": "#F23645", "renderMode": "events"},
]

signals = [signal for signal in signals if any(item is not None for item in signal["data"])]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers}
