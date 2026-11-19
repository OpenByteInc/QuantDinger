# ============================================================
# QuantDinger Auto Fib Retracement Alerts
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Auto Fib Retracement Alerts"
my_indicator_description = (
    "Finds confirmed deviation-filtered swing segments, draws configurable Fibonacci levels "
    "for the current segment, and emits the first qualifying retracement touch after each update."
)

# @param deviation_threshold float 5.0 Minimum swing deviation percent range=0.0:100.0:0.1
# @param depth int 10 Pivot depth range=2:500:1
# @param extend_left bool false Extend Fibonacci levels left
# @param extend_right bool true Extend Fibonacci levels right
# @param retracement float 0.618 Alert retracement level range=0.0:10.0:0.001
# @param reverse bool false Reverse current swing anchors
# @param show_prices bool false Include prices in level labels
# @param show_levels bool true Include level values in labels
# @param levels_format str Values Level label format values=Values,Percent
# @param show_level_0 bool true Show level 0
# @param show_level_0236 bool true Show level 0.236
# @param show_level_0382 bool true Show level 0.382
# @param show_level_05 bool true Show level 0.5
# @param show_level_0618 bool true Show level 0.618
# @param show_level_0786 bool true Show level 0.786
# @param show_level_1 bool true Show level 1
# @param show_level_1272 bool true Show level 1.272
# @param show_level_1414 bool true Show level 1.414
# @param show_level_1618 bool true Show level 1.618
# @param show_level_2618 bool false Show level 2.618
# @param show_level_3618 bool false Show level 3.618
# @param show_level_4236 bool false Show level 4.236

deviation_threshold = min(100.0, max(0.0, float(params.get("deviation_threshold", 5.0))))
depth = min(500, max(2, int(params.get("depth", 10))))
extend_left = bool(params.get("extend_left", False))
extend_right = bool(params.get("extend_right", True))
retracement = min(10.0, max(0.0, float(params.get("retracement", 0.618))))
reverse = bool(params.get("reverse", False))
show_prices = bool(params.get("show_prices", False))
show_levels = bool(params.get("show_levels", True))
levels_format = str(params.get("levels_format", "Values"))
if levels_format not in ("Values", "Percent"):
    levels_format = "Values"

level_settings = [
    (0.0, bool(params.get("show_level_0", True)), "#808080"),
    (0.236, bool(params.get("show_level_0236", True)), "#A61C00"),
    (0.382, bool(params.get("show_level_0382", True)), "#95CC28"),
    (0.5, bool(params.get("show_level_05", True)), "#28CC28"),
    (0.618, bool(params.get("show_level_0618", True)), "#28CC95"),
    (0.786, bool(params.get("show_level_0786", True)), "#2895CC"),
    (1.0, bool(params.get("show_level_1", True)), "#808080"),
    (1.272, bool(params.get("show_level_1272", True)), "#82CA89"),
    (1.414, bool(params.get("show_level_1414", True)), "#F32C42"),
    (1.618, bool(params.get("show_level_1618", True)), "#2796ED"),
    (2.618, bool(params.get("show_level_2618", False)), "#A61C00"),
    (3.618, bool(params.get("show_level_3618", False)), "#9B03AE"),
    (4.236, bool(params.get("show_level_4236", False)), "#E80065"),
]

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)
half_depth = max(1, int(depth / 2))


def confirmed_pivot(values, confirmation_index, is_high):
    pivot_index = confirmation_index - half_depth
    start = pivot_index - half_depth
    end = pivot_index + half_depth + 1
    if start < 0 or end > len(values):
        return None
    window = values[start:end]
    candidate = float(values[pivot_index])
    if is_high and candidate >= float(np.max(window)):
        return pivot_index, candidate
    if not is_high and candidate <= float(np.min(window)):
        return pivot_index, candidate
    return None


def deviation(base_price, price):
    if price == 0.0:
        return 0.0
    return 100.0 * (price - base_price) / price


def label_text(level, price):
    parts = []
    if show_levels:
        parts.append(f"{level:g}" if levels_format == "Values" else f"{level * 100.0:g}%")
    if show_prices:
        parts.append(f"({price:.2f})")
    return " ".join(parts)


segment_exists = False
segment_start_index = 0
segment_start_price = 0.0
segment_end_index = 0
segment_end_price = 0.0
last_pivot_index = 0
last_pivot_price = 0.0
last_pivot_is_high = False

p1_series = np.full(n, np.nan, dtype=float)
p2_series = np.full(n, np.nan, dtype=float)
target_series = np.full(n, np.nan, dtype=float)
segment_version = np.zeros(n, dtype=int)
version = 0

for confirmation_index in range(n):
    pivot = confirmed_pivot(high, confirmation_index, True)
    pivot_is_high = True
    if pivot is None:
        pivot = confirmed_pivot(low, confirmation_index, False)
        pivot_is_high = False

    if pivot is not None:
        pivot_index, pivot_price = pivot
        accepted = False
        if segment_exists and last_pivot_is_high == pivot_is_high:
            more_extreme = pivot_price > last_pivot_price if pivot_is_high else pivot_price < last_pivot_price
            if more_extreme:
                segment_end_index = pivot_index
                segment_end_price = pivot_price
                last_pivot_index = pivot_index
                last_pivot_price = pivot_price
                accepted = True
        elif abs(deviation(last_pivot_price, pivot_price)) > deviation_threshold:
            segment_start_index = last_pivot_index
            segment_start_price = last_pivot_price
            segment_end_index = pivot_index
            segment_end_price = pivot_price
            last_pivot_index = pivot_index
            last_pivot_price = pivot_price
            last_pivot_is_high = pivot_is_high
            segment_exists = True
            accepted = True
        if accepted:
            version += 1

    if segment_exists:
        first_price = segment_end_price if reverse else segment_start_price
        second_price = segment_start_price if reverse else segment_end_price
        p1_series[confirmation_index] = first_price
        p2_series[confirmation_index] = second_price
        target_series[confirmation_index] = first_price + (second_price - first_price) * retracement
        segment_version[confirmation_index] = version

buy_alert = np.zeros(n, dtype=bool)
sell_alert = np.zeros(n, dtype=bool)
signaled_version = -1
for index in range(n):
    if not (np.isfinite(p1_series[index]) and np.isfinite(p2_series[index]) and np.isfinite(target_series[index])):
        continue
    current_version = int(segment_version[index])
    if current_version != signaled_version:
        if p1_series[index] < p2_series[index] and low[index] <= target_series[index] < high[index]:
            buy_alert[index] = True
            signaled_version = current_version
        elif p1_series[index] > p2_series[index] and low[index] < target_series[index] <= high[index]:
            sell_alert[index] = True
            signaled_version = current_version


def current_segment_plot(values):
    points = []
    for index, value in enumerate(values):
        if version > 0 and int(segment_version[index]) == version and np.isfinite(value):
            points.append(float(value))
        else:
            points.append(None)
    return points


layers = []
if segment_exists:
    layers.append({
        "type": "line",
        "startIndex": max(0, segment_start_index),
        "endIndex": max(0, segment_end_index),
        "startPrice": float(segment_start_price),
        "endPrice": float(segment_end_price),
        "color": "#808080",
        "lineWidth": 1,
        "dashed": True,
        "text": "Swing",
    })
    first_price = segment_end_price if reverse else segment_start_price
    second_price = segment_start_price if reverse else segment_end_price
    level_start_index = 0 if extend_left else max(0, min(segment_start_index, segment_end_index))
    level_end_index = max(0, max(segment_start_index, segment_end_index))
    for level, visible, color in level_settings:
        if not visible:
            continue
        price = first_price + (second_price - first_price) * level
        layers.append({
            "type": "line",
            "startIndex": level_start_index,
            "endIndex": level_end_index,
            "price": float(price),
            "text": label_text(level, price),
            "textPosition": "start",
            "color": color,
            "lineWidth": 1,
            "extendRight": extend_right,
        })

buy_values = [float(low[index]) if buy_alert[index] else None for index in range(n)]
sell_values = [float(high[index]) if sell_alert[index] else None for index in range(n)]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Start", "data": current_segment_plot(p1_series), "color": "#F44336", "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "End", "data": current_segment_plot(p2_series), "color": "#7CFC00", "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Target", "data": current_segment_plot(target_series), "color": "#9C27B0", "type": "line", "lineWidth": 3, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Buy Retrace", "text": "Buy Retrace", "color": "#7CFC00", "renderMode": "events", "data": buy_values},
        {"type": "sell", "name": "Sell Retrace", "text": "Sell Retrace", "color": "#F44336", "renderMode": "events", "data": sell_values},
    ],
    "layers": layers,
}
