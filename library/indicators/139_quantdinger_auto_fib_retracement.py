# ============================================================
# QuantDinger Auto Fib Retracement
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Auto Fib Retracement"
my_indicator_description = (
    "Maintains the latest deviation-filtered swing, draws configurable Fibonacci "
    "retracement and extension levels, and marks the first target retest."
)

# @param deviation_threshold float 5.0 Pivot deviation percent range=0.0:100.0:0.5
# @param depth int 10 Pivot depth range=2:100:2
# @param extend_left bool false Extend levels left
# @param extend_right bool true Extend levels right
# @param retracement float 0.618 Alert retracement range=-5.0:5.0:0.001
# @param reverse bool false Reverse retracement direction
# @param show_prices bool false Show prices in labels
# @param show_levels bool false Show level values in labels
# @param levels_format str Values Level format values=Values,Percent
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
depth = min(100, max(2, int(params.get("depth", 10))))
extend_left = bool(params.get("extend_left", False))
extend_right = bool(params.get("extend_right", True))
retracement = min(5.0, max(-5.0, float(params.get("retracement", 0.618))))
reverse = bool(params.get("reverse", False))
show_prices = bool(params.get("show_prices", False))
show_levels = bool(params.get("show_levels", False))
levels_format = str(params.get("levels_format", "Values"))

level_specs = [
    (0.000, bool(params.get("show_level_0", True)), "#808080"),
    (0.236, bool(params.get("show_level_0236", True)), "#A61C00"),
    (0.382, bool(params.get("show_level_0382", True)), "#95CC28"),
    (0.500, bool(params.get("show_level_05", True)), "#28CC28"),
    (0.618, bool(params.get("show_level_0618", True)), "#28CC95"),
    (0.786, bool(params.get("show_level_0786", True)), "#2895CC"),
    (1.000, bool(params.get("show_level_1", True)), "#808080"),
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
n = len(df)

pivot_radius = max(1, depth // 2)
pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
pivot_high_index = np.full(n, -1, dtype=int)
pivot_low_index = np.full(n, -1, dtype=int)
for confirmation_index in range(2 * pivot_radius, n):
    candidate_index = confirmation_index - pivot_radius
    first_index = candidate_index - pivot_radius
    last_index = candidate_index + pivot_radius + 1
    if high[candidate_index] >= float(np.max(high[first_index:last_index])):
        pivot_high[confirmation_index] = float(high[candidate_index])
        pivot_high_index[confirmation_index] = candidate_index
    if low[candidate_index] <= float(np.min(low[first_index:last_index])):
        pivot_low[confirmation_index] = float(low[candidate_index])
        pivot_low_index[confirmation_index] = candidate_index

line_exists = False
line_start_index = 0
line_start_price = 0.0
line_end_index = 0
line_end_price = 0.0
last_pivot_index = 0
last_pivot_price = 0.0
last_pivot_high = False
has_pivot = False

start_series = np.full(n, np.nan, dtype=float)
end_series = np.full(n, np.nan, dtype=float)
target_series = np.full(n, np.nan, dtype=float)
buy_retrace = np.zeros(n, dtype=bool)
sell_retrace = np.zeros(n, dtype=bool)
buy_seen_since_change = False
sell_seen_since_change = False
previous_end = np.nan

for index in range(n):
    candidate_direction = 0
    candidate_index = -1
    candidate_price = np.nan
    if np.isfinite(pivot_high[index]):
        candidate_direction = 1
        candidate_index = int(pivot_high_index[index])
        candidate_price = float(pivot_high[index])
    elif np.isfinite(pivot_low[index]):
        candidate_direction = -1
        candidate_index = int(pivot_low_index[index])
        candidate_price = float(pivot_low[index])

    if candidate_direction != 0:
        candidate_is_high = candidate_direction == 1
        accepted = False
        if not has_pivot:
            last_pivot_index = candidate_index
            last_pivot_price = candidate_price
            last_pivot_high = candidate_is_high
            has_pivot = True
        elif last_pivot_high == candidate_is_high:
            more_extreme = candidate_price > last_pivot_price if candidate_is_high else candidate_price < last_pivot_price
            if more_extreme:
                if line_exists:
                    line_end_index = candidate_index
                    line_end_price = candidate_price
                accepted = True
        else:
            deviation = (
                100.0 * (candidate_price - last_pivot_price) / candidate_price
                if candidate_price != 0.0
                else 0.0
            )
            if abs(deviation) > deviation_threshold:
                line_start_index = int(last_pivot_index)
                line_start_price = float(last_pivot_price)
                line_end_index = candidate_index
                line_end_price = candidate_price
                line_exists = True
                accepted = True

        if accepted:
            last_pivot_index = candidate_index
            last_pivot_price = candidate_price
            last_pivot_high = candidate_is_high

    if line_exists:
        raw_start = float(last_pivot_price if reverse else line_start_price)
        raw_end = float(line_start_price if reverse else last_pivot_price)
        difference = raw_end - raw_start
        target = raw_start + difference * retracement
        start_series[index] = raw_start
        end_series[index] = raw_end
        target_series[index] = target

        end_changed = not np.isfinite(previous_end) or raw_end != previous_end
        if end_changed:
            buy_seen_since_change = False
            sell_seen_since_change = False

        is_buy_touch = raw_start < raw_end and low[index] <= target and high[index] > target
        is_sell_touch = raw_start > raw_end and high[index] >= target and low[index] < target
        if is_buy_touch and not buy_seen_since_change and not end_changed:
            buy_retrace[index] = True
        if is_sell_touch and not sell_seen_since_change and not end_changed:
            sell_retrace[index] = True
        if is_buy_touch:
            buy_seen_since_change = True
        if is_sell_touch:
            sell_seen_since_change = True
        previous_end = raw_end

start_plot = []
end_plot = []
target_plot = []
for index in range(n):
    start_changed = index == 0 or start_series[index] != start_series[index - 1]
    end_changed = index == 0 or end_series[index] != end_series[index - 1]
    target_changed = index == 0 or target_series[index] != target_series[index - 1]
    start_plot.append(None if not np.isfinite(start_series[index]) or start_changed else float(start_series[index]))
    end_plot.append(None if not np.isfinite(end_series[index]) or end_changed else float(end_series[index]))
    target_plot.append(None if not np.isfinite(target_series[index]) or target_changed else float(target_series[index]))

layers = []
if line_exists:
    layers.append(
        {
            "type": "line",
            "startIndex": int(line_start_index),
            "endIndex": int(line_end_index),
            "y1": float(line_start_price),
            "y2": float(line_end_price),
            "color": "#808080",
            "lineWidth": 1,
            "dashed": True,
        }
    )
    fib_start = float(last_pivot_price if reverse else line_start_price)
    fib_end = float(line_start_price if reverse else last_pivot_price)
    fib_difference = fib_end - fib_start
    level_start_index = 0 if extend_left else int(line_start_index)
    level_end_index = int(line_end_index)
    for level_value, enabled, color in level_specs:
        if not enabled:
            continue
        price = fib_start + fib_difference * level_value
        layers.append(
            {
                "type": "line",
                "startIndex": level_start_index,
                "endIndex": level_end_index,
                "y1": float(price),
                "y2": float(price),
                "color": color,
                "lineWidth": 1,
                "dashed": False,
                "extendRight": bool(extend_right),
            }
        )
        level_text = f"{level_value:g}" if levels_format == "Values" else f"{level_value * 100.0:g}%"
        price_text = f"({price:.2f})"
        text = (level_text if show_levels else "") + (price_text if show_prices else "")
        if text:
            layers.append(
                {
                    "type": "label",
                    "index": int(line_start_index),
                    "price": float(price),
                    "text": text,
                    "color": "rgba(0, 0, 0, 0.0)",
                    "textColor": color,
                    "side": "right",
                }
            )

buy_data = [None for _ in range(n)]
sell_data = [None for _ in range(n)]
for index in range(n):
    if buy_retrace[index]:
        buy_data[index] = {"value": float(low[index]), "text": "Buy Retrace", "color": "#00C853"}
    if sell_retrace[index]:
        sell_data[index] = {"value": float(high[index]), "text": "Sell Retrace", "color": "#F23645"}

plots = [
    {"name": "Start", "data": start_plot, "color": "#F23645", "type": "line", "lineWidth": 3, "overlay": True},
    {"name": "End", "data": end_plot, "color": "#00C853", "type": "line", "lineWidth": 3, "overlay": True},
    {"name": "Target", "data": target_plot, "color": "#9C27B0", "type": "line", "lineWidth": 3, "overlay": True},
]
plots = [plot for plot in plots if any(item is not None for item in plot["data"])]
signals = [
    {"type": "buy", "text": "Buy Retrace", "name": "Buy Retrace", "data": buy_data, "color": "#00C853", "renderMode": "events"},
    {"type": "sell", "text": "Sell Retrace", "name": "Sell Retrace", "data": sell_data, "color": "#F23645", "renderMode": "events"},
]

signals = [signal for signal in signals if any(item is not None for item in signal["data"])]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers}
