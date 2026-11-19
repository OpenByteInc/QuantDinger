# ============================================================
# QuantDinger Trading ABC
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Trading ABC"
my_indicator_description = (
    "Combines a six-average trend cloud with a rolling ZigZag, Fibonacci retracement window, "
    "ABC pattern annotations, and moving-average bounce signals."
)

# @param zigzag_period int 8 ZigZag period range=2:50:1
# @param fibonacci_max float 0.618 Fibonacci maximum range=0.0:2.0:0.001
# @param fibonacci_min float 0.382 Fibonacci minimum range=0.0:2.0:0.001
# @param error_rate float 5.0 Pattern error rate percent range=0.0:30.0:0.5
# @param show_abc bool true Show ABC patterns
# @param keep_old_abc bool false Keep old ABC patterns
# @param show_cloud bool true Show trend cloud
# @param show_zigzag bool true Show ZigZag and Fibonacci
# @param ma_source str close Moving-average source values=close,open,high,low,hl2,hlc3,ohlc4
# @param sma_one int 50 SMA one length range=1:500:1
# @param sma_two int 100 SMA two length range=1:500:1
# @param sma_three int 150 SMA three length range=1:500:1
# @param sma_four int 200 SMA four length range=1:500:1
# @param ema_one int 20 EMA one length range=1:500:1
# @param ema_two int 40 EMA two length range=1:500:1
# @param bullish_color str #00FF00 Bullish color
# @param bearish_color str #FF0000 Bearish color

zigzag_period = min(50, max(2, int(params.get("zigzag_period", 8))))
fibonacci_max = min(2.0, max(0.0, float(params.get("fibonacci_max", 0.618))))
fibonacci_min = min(2.0, max(0.0, float(params.get("fibonacci_min", 0.382))))
error_rate = min(30.0, max(0.0, float(params.get("error_rate", 5.0)))) / 100.0
show_abc = bool(params.get("show_abc", True))
keep_old_abc = bool(params.get("keep_old_abc", False))
show_cloud = bool(params.get("show_cloud", True))
show_zigzag = bool(params.get("show_zigzag", True))
ma_source = str(params.get("ma_source", "close"))
sma_one = min(500, max(1, int(params.get("sma_one", 50))))
sma_two = min(500, max(1, int(params.get("sma_two", 100))))
sma_three = min(500, max(1, int(params.get("sma_three", 150))))
sma_four = min(500, max(1, int(params.get("sma_four", 200))))
ema_one = min(500, max(1, int(params.get("ema_one", 20))))
ema_two = min(500, max(1, int(params.get("ema_two", 40))))
bullish_color = str(params.get("bullish_color", "#00FF00"))
bearish_color = str(params.get("bearish_color", "#FF0000"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

source_map = {
    "close": close,
    "open": open_price,
    "high": high,
    "low": low,
    "hl2": (high + low) / 2.0,
    "hlc3": (high + low + close) / 3.0,
    "ohlc4": (open_price + high + low + close) / 4.0,
}
ma_values = source_map.get(ma_source, close)

def sma(values, length):
    return pd.Series(values).rolling(length, min_periods=length).mean().to_numpy(dtype=float)

def pine_ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    alpha = 2.0 / (length + 1.0)
    for index in range(length, len(values)):
        result[index] = alpha * values[index] + (1.0 - alpha) * result[index - 1]
    return result

averages = [
    sma(ma_values, sma_one),
    sma(ma_values, sma_two),
    sma(ma_values, sma_three),
    sma(ma_values, sma_four),
    pine_ema(ma_values, ema_one),
    pine_ema(ma_values, ema_two),
]

upper_max = np.full(n, np.nan, dtype=float)
upper_min = np.full(n, np.nan, dtype=float)
lower_max = np.full(n, np.nan, dtype=float)
lower_min = np.full(n, np.nan, dtype=float)
trend = np.zeros(n, dtype=int)
upper_condition = np.zeros(n, dtype=bool)
lower_condition = np.zeros(n, dtype=bool)

for index in range(n):
    upper_items = [item[index] for item in averages if np.isfinite(item[index]) and item[index] >= max(open_price[index], close[index])]
    lower_items = [item[index] for item in averages if np.isfinite(item[index]) and item[index] <= min(open_price[index], close[index])]
    if upper_items:
        upper_max[index] = max(upper_items)
        upper_min[index] = min(upper_items)
    if lower_items:
        lower_max[index] = max(lower_items)
        lower_min[index] = min(lower_items)
    upper_condition[index] = bool(upper_items) and not lower_items
    lower_condition[index] = bool(lower_items) and not upper_items
    previous = trend[index - 1] if index else 0
    if index and lower_condition[index] and lower_condition[index - 1]:
        trend[index] = 1
    elif index and upper_condition[index] and upper_condition[index - 1]:
        trend[index] = -1
    else:
        trend[index] = previous

up_cloud_upper = []
up_cloud_lower = []
down_cloud_upper = []
down_cloud_lower = []
for index in range(n):
    if show_cloud and trend[index] == 1 and np.isfinite(lower_max[index]) and np.isfinite(lower_min[index]):
        up_cloud_upper.append(float(lower_max[index]))
        up_cloud_lower.append(float(lower_min[index]))
    else:
        up_cloud_upper.append(None)
        up_cloud_lower.append(None)
    if show_cloud and trend[index] == -1 and np.isfinite(upper_max[index]) and np.isfinite(upper_min[index]):
        down_cloud_upper.append(float(upper_max[index]))
        down_cloud_lower.append(float(upper_min[index]))
    else:
        down_cloud_upper.append(None)
        down_cloud_lower.append(None)

rolling_high = pd.Series(high).rolling(zigzag_period, min_periods=1).max().to_numpy(dtype=float)
rolling_low = pd.Series(low).rolling(zigzag_period, min_periods=1).min().to_numpy(dtype=float)
direction = 0
zigzag = []
abc_layers = []
last_zigzag_value = np.nan
last_abc_point = 0.0
bars_since_abc = 1000000
long_signals = [None for _ in range(n)]
short_signals = [None for _ in range(n)]

def add_zigzag_point(value, index):
    zigzag.insert(0, {"value": float(value), "index": int(index)})
    if len(zigzag) > 5:
        zigzag.pop()

def update_zigzag_point(value, index):
    if not zigzag:
        add_zigzag_point(value, index)
    elif (direction == 1 and value > zigzag[0]["value"]) or (direction == -1 and value < zigzag[0]["value"]):
        zigzag[0] = {"value": float(value), "index": int(index)}

def add_pattern_layer(layer_list, start, start_price, end, end_price, color):
    layer_list.append(
        {
            "type": "line",
            "startIndex": int(start),
            "endIndex": int(end),
            "y1": float(start_price),
            "y2": float(end_price),
            "color": color,
            "lineWidth": 2,
        }
    )

for index in range(n):
    is_high = bool(np.isfinite(rolling_high[index]) and high[index] >= rolling_high[index])
    is_low = bool(np.isfinite(rolling_low[index]) and low[index] <= rolling_low[index])
    previous_direction = direction
    if is_high and not is_low:
        direction = 1
    elif is_low and not is_high:
        direction = -1

    if is_high or is_low:
        value = high[index] if direction == 1 else low[index]
        if direction != previous_direction:
            add_zigzag_point(value, index)
        else:
            update_zigzag_point(value, index)

    current_zigzag_value = zigzag[0]["value"] if zigzag else 0.0
    zigzag_changed = not np.isfinite(last_zigzag_value) or current_zigzag_value != last_zigzag_value
    pattern = None
    if zigzag_changed and len(zigzag) >= 3:
        valid_turn = (
            is_low
            and trend[index] == 1
            and direction == -1
            and any(np.isfinite(item[index]) and low[index] < item[index] for item in averages)
        ) or (
            is_high
            and trend[index] == -1
            and direction == 1
            and any(np.isfinite(item[index]) and high[index] > item[index] for item in averages)
        )
        if valid_turn:
            a = zigzag[0]["value"]
            b = zigzag[1]["value"]
            c = zigzag[2]["value"]
            denominator = c - b
            rate = (a - b) / denominator if denominator != 0.0 else np.nan
            if np.isfinite(rate) and rate >= fibonacci_min * (1.0 - error_rate) and rate <= fibonacci_max * (1.0 + error_rate):
                pattern = {"b": zigzag[1], "c": zigzag[2]}

    if pattern is not None:
        last_abc_point = float(zigzag[0]["value"])
        bars_since_abc = 0
        if show_abc:
            current_layers = []
            first_color = bullish_color if direction == 1 else bearish_color
            second_color = bearish_color if direction == 1 else bullish_color
            add_pattern_layer(current_layers, index, zigzag[0]["value"], pattern["b"]["index"], pattern["b"]["value"], first_color)
            add_pattern_layer(current_layers, pattern["b"]["index"], pattern["b"]["value"], pattern["c"]["index"], pattern["c"]["value"], second_color)
            label_points = [
                (index, zigzag[0]["value"], "C", "down" if direction == 1 else "up"),
                (pattern["b"]["index"], pattern["b"]["value"], "B", "down" if direction == -1 else "up"),
                (pattern["c"]["index"], pattern["c"]["value"], "A", "down" if direction == 1 else "up"),
            ]
            for point_index, price, text, side in label_points:
                current_layers.append(
                    {
                        "type": "label",
                        "index": int(point_index),
                        "price": float(price),
                        "text": text,
                        "color": "rgba(0,0,0,0)",
                        "textColor": "#2962FF",
                        "side": side,
                    }
                )
            if keep_old_abc:
                abc_layers.extend(current_layers)
            else:
                abc_layers = current_layers
    else:
        bars_since_abc += 1

    bounce_long = False
    bounce_short = False
    if index > 0:
        for average in averages:
            value = average[index]
            if not np.isfinite(value):
                continue
            if min(low[index], low[index - 1]) <= value and close[index] > value and close[index] > open_price[index]:
                bounce_long = True
            if max(high[index], high[index - 1]) >= value and close[index] < value and close[index] < open_price[index]:
                bounce_short = True
    window_start = max(0, index - bars_since_abc)
    recent_low = float(np.min(low[window_start:index + 1]))
    recent_high = float(np.max(high[window_start:index + 1]))
    if trend[index] == 1 and bars_since_abc <= 6 and bounce_long and recent_low >= last_abc_point:
        long_signals[index] = float(low[index])
    if trend[index] == -1 and bars_since_abc <= 6 and bounce_short and recent_high <= last_abc_point:
        short_signals[index] = float(high[index])
    last_zigzag_value = current_zigzag_value

layers = list(abc_layers)
if show_zigzag and len(zigzag) >= 3:
    first_color = bullish_color if direction == 1 else bearish_color
    second_color = bullish_color if direction == -1 else bearish_color
    for first, second, color in ((zigzag[0], zigzag[1], first_color), (zigzag[1], zigzag[2], second_color)):
        layers.append(
            {
                "type": "line",
                "startIndex": int(first["index"]),
                "endIndex": int(second["index"]),
                "y1": float(first["value"]),
                "y2": float(second["value"]),
                "color": color,
                "lineWidth": 2,
                "dashed": True,
            }
        )
    zigzag_length = abs(zigzag[1]["value"] - zigzag[2]["value"])
    ratio_lower = fibonacci_min * (1.0 - error_rate)
    ratio_upper = fibonacci_max * (1.0 + error_rate)
    if direction == 1:
        fibonacci_low = zigzag[1]["value"] + zigzag_length * ratio_lower
        fibonacci_high = zigzag[1]["value"] + zigzag_length * ratio_upper
    else:
        fibonacci_low = zigzag[2]["value"] + zigzag_length * (1.0 - ratio_upper)
        fibonacci_high = zigzag[2]["value"] + zigzag_length * (1.0 - ratio_lower)
    for price in (fibonacci_low, fibonacci_high):
        layers.append(
            {
                "type": "line",
                "startIndex": int(zigzag[1]["index"]),
                "endIndex": max(0, n - 1),
                "y1": float(price),
                "y2": float(price),
                "color": "#2962FF",
                "lineWidth": 1,
                "dashed": True,
                "extendRight": True,
            }
        )

price_guide = [float(value) if np.isfinite(value) else None for value in close]
plots = [
    {"name": "Price Reference", "title": False, "data": price_guide, "color": "rgba(255,255,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Bullish Cloud Upper", "title": False, "data": up_cloud_upper, "color": "rgba(0,255,0,0.35)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Bullish Cloud Lower", "title": False, "data": up_cloud_lower, "color": "rgba(0,255,0,0.18)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Bearish Cloud Upper", "title": False, "data": down_cloud_upper, "color": "rgba(255,0,0,0.35)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Bearish Cloud Lower", "title": False, "data": down_cloud_lower, "color": "rgba(255,0,0,0.18)", "type": "line", "lineWidth": 1, "overlay": True},
]
plots = [plot for plot in plots if any(item is not None for item in plot["data"])]
signals = []
if any(item is not None for item in long_signals):
    signals.append({"type": "buy", "text": "ABC", "name": "ABC Long", "data": long_signals, "color": bullish_color, "renderMode": "events"})
if any(item is not None for item in short_signals):
    signals.append({"type": "sell", "text": "ABC", "name": "ABC Short", "data": short_signals, "color": bearish_color, "renderMode": "events"})

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers[-500:]}
