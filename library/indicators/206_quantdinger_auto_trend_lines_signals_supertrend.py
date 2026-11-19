# ============================================================
# QuantDinger Auto Trend Lines Signals Supertrend
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Auto Trend Lines Signals Supertrend"
my_indicator_description = (
    "Builds confirmed macro and micro pivot trend lines, emits close-crossing signals from the "
    "latest valid lines, and overlays a stateful ATR Supertrend with flip markers."
)

# @param macro_on bool true Show macro trend lines
# @param macro_left int 10 Macro pivot left bars range=1:100:1
# @param macro_right int 5 Macro pivot right bars range=1:100:1
# @param macro_extension int 10 Macro line extension bars range=0:400:1
# @param macro_wicks bool true Build macro pivots from candle wicks
# @param macro_filter bool true Keep only falling highs and rising lows
# @param macro_log bool true Use logarithmic macro projection
# @param macro_bull_color str #26A69A Macro rising-low color
# @param macro_bear_color str #EF5350 Macro falling-high color
# @param macro_style str Solid Macro line style values=Solid,Dashed,Dotted
# @param macro_width int 1 Macro line width range=1:4:1
# @param micro_on bool true Show micro trend lines
# @param micro_left int 4 Micro pivot left bars range=1:100:1
# @param micro_right int 2 Micro pivot right bars range=1:100:1
# @param micro_extension int 4 Micro line extension bars range=0:400:1
# @param micro_wicks bool true Build micro pivots from candle wicks
# @param micro_filter bool true Keep only falling highs and rising lows
# @param micro_log bool true Use logarithmic micro projection
# @param micro_bull_color str #26A69A Micro rising-low color
# @param micro_bear_color str #EF5350 Micro falling-high color
# @param micro_style str Dotted Micro line style values=Solid,Dashed,Dotted
# @param micro_width int 1 Micro line width range=1:4:1
# @param show_signals bool true Show trend-line crossing signals
# @param strong_buy_color str #00E5FF Strong buy color
# @param strong_sell_color str #FF1744 Strong sell color
# @param weak_buy_color str #69F0AE Weak buy color
# @param weak_sell_color str #FF6D00 Weak sell color
# @param supertrend_on bool true Show Supertrend
# @param supertrend_atr int 10 Supertrend ATR period range=1:500:1
# @param supertrend_multiplier float 3.0 Supertrend multiplier range=0.1:20.0:0.1
# @param supertrend_bull_color str #00E5FF Supertrend bull color
# @param supertrend_bear_color str #FF1744 Supertrend bear color
# @param supertrend_width int 2 Supertrend line width range=1:4:1
# @param supertrend_signals bool true Show Supertrend flip signals
# @param supertrend_buy_color str #00E5FF Supertrend buy color
# @param supertrend_sell_color str #FF1744 Supertrend sell color

macro_on = bool(params.get("macro_on", True))
macro_left = min(100, max(1, int(params.get("macro_left", 10))))
macro_right = min(100, max(1, int(params.get("macro_right", 5))))
macro_extension = min(400, max(0, int(params.get("macro_extension", 10))))
macro_wicks = bool(params.get("macro_wicks", True))
macro_filter = bool(params.get("macro_filter", True))
macro_log = bool(params.get("macro_log", True))
macro_bull_color = str(params.get("macro_bull_color", "#26A69A"))
macro_bear_color = str(params.get("macro_bear_color", "#EF5350"))
macro_style = str(params.get("macro_style", "Solid"))
if macro_style not in ("Solid", "Dashed", "Dotted"):
    macro_style = "Solid"
macro_width = min(4, max(1, int(params.get("macro_width", 1))))

micro_on = bool(params.get("micro_on", True))
micro_left = min(100, max(1, int(params.get("micro_left", 4))))
micro_right = min(100, max(1, int(params.get("micro_right", 2))))
micro_extension = min(400, max(0, int(params.get("micro_extension", 4))))
micro_wicks = bool(params.get("micro_wicks", True))
micro_filter = bool(params.get("micro_filter", True))
micro_log = bool(params.get("micro_log", True))
micro_bull_color = str(params.get("micro_bull_color", "#26A69A"))
micro_bear_color = str(params.get("micro_bear_color", "#EF5350"))
micro_style = str(params.get("micro_style", "Dotted"))
if micro_style not in ("Solid", "Dashed", "Dotted"):
    micro_style = "Dotted"
micro_width = min(4, max(1, int(params.get("micro_width", 1))))

show_signals = bool(params.get("show_signals", True))
strong_buy_color = str(params.get("strong_buy_color", "#00E5FF"))
strong_sell_color = str(params.get("strong_sell_color", "#FF1744"))
weak_buy_color = str(params.get("weak_buy_color", "#69F0AE"))
weak_sell_color = str(params.get("weak_sell_color", "#FF6D00"))

supertrend_on = bool(params.get("supertrend_on", True))
supertrend_atr = min(500, max(1, int(params.get("supertrend_atr", 10))))
supertrend_multiplier = min(20.0, max(0.1, float(params.get("supertrend_multiplier", 3.0))))
supertrend_bull_color = str(params.get("supertrend_bull_color", "#00E5FF"))
supertrend_bear_color = str(params.get("supertrend_bear_color", "#FF1744"))
supertrend_width = min(4, max(1, int(params.get("supertrend_width", 2))))
supertrend_signals = bool(params.get("supertrend_signals", True))
supertrend_buy_color = str(params.get("supertrend_buy_color", "#00E5FF"))
supertrend_sell_color = str(params.get("supertrend_sell_color", "#FF1744"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)
body_high = np.maximum(open_price, close)
body_low = np.minimum(open_price, close)


def wilder_average(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < length:
        return result
    result[length - 1] = float(np.mean(source[:length]))
    for index in range(length, len(source)):
        result[index] = (result[index - 1] * (length - 1) + source[index]) / length
    return result


def confirmed_pivots(values, left, right, is_high):
    events = {}
    source = np.asarray(values, dtype=float)
    for center in range(left, len(source) - right):
        value = source[center]
        left_values = source[center - left:center]
        right_values = source[center + 1:center + right + 1]
        if not np.isfinite(value) or not np.all(np.isfinite(left_values)) or not np.all(np.isfinite(right_values)):
            continue
        if is_high:
            accepted = value > float(np.max(left_values)) and value >= float(np.max(right_values))
        else:
            accepted = value < float(np.min(left_values)) and value <= float(np.min(right_values))
        if accepted:
            events[center + right] = {"bar": center, "price": float(value)}
    return events


def project_price(first_price, second_price, first_bar, second_bar, target_bar, use_log):
    if second_bar == first_bar:
        return float(second_price)
    safe_first = max(float(first_price), 1e-12)
    safe_second = max(float(second_price), 1e-12)
    first_value = np.log(safe_first) if use_log else float(first_price)
    second_value = np.log(safe_second) if use_log else float(second_price)
    slope = (second_value - first_value) / float(second_bar - first_bar)
    projected = second_value + slope * float(target_bar - second_bar)
    return float(np.exp(projected) if use_log else projected)


def trend_line_state(values, left, right, extension, use_log, filter_lines, is_high, enabled, color, style, width):
    active_values = np.full(n, np.nan, dtype=float)
    layers = []
    if not enabled:
        return active_values, layers
    events = confirmed_pivots(values, left, right, is_high)
    previous_pivot = None
    active_line = None
    for index in range(n):
        current_pivot = events.get(index)
        if current_pivot is not None:
            if previous_pivot is not None:
                direction_ok = (
                    current_pivot["price"] < previous_pivot["price"]
                    if is_high
                    else current_pivot["price"] > previous_pivot["price"]
                )
                if not filter_lines or direction_ok:
                    active_line = {
                        "first_bar": previous_pivot["bar"],
                        "first_price": previous_pivot["price"],
                        "second_bar": current_pivot["bar"],
                        "second_price": current_pivot["price"],
                        "log": use_log,
                    }
                    requested_end = current_pivot["bar"] + extension
                    visible_end = min(n - 1, requested_end)
                    end_price = project_price(
                        previous_pivot["price"], current_pivot["price"],
                        previous_pivot["bar"], current_pivot["bar"], visible_end, use_log,
                    )
                    layer = {
                        "type": "line",
                        "startIndex": previous_pivot["bar"],
                        "endIndex": visible_end,
                        "startPrice": previous_pivot["price"],
                        "endPrice": end_price,
                        "color": color,
                        "lineWidth": width,
                        "dashed": style in ("Dashed", "Dotted"),
                    }
                    if requested_end > n - 1:
                        layer["extendBars"] = requested_end - (n - 1)
                    layers.append(layer)
            previous_pivot = current_pivot
        if active_line is not None:
            active_values[index] = project_price(
                active_line["first_price"], active_line["second_price"],
                active_line["first_bar"], active_line["second_bar"], index, active_line["log"],
            )
    return active_values, layers[-500:]


macro_high_source = high if macro_wicks else body_high
macro_low_source = low if macro_wicks else body_low
micro_high_source = high if micro_wicks else body_high
micro_low_source = low if micro_wicks else body_low

macro_high_value, macro_high_layers = trend_line_state(
    macro_high_source, macro_left, macro_right, macro_extension, macro_log,
    macro_filter, True, macro_on, macro_bear_color, macro_style, macro_width,
)
macro_low_value, macro_low_layers = trend_line_state(
    macro_low_source, macro_left, macro_right, macro_extension, macro_log,
    macro_filter, False, macro_on, macro_bull_color, macro_style, macro_width,
)
micro_high_value, micro_high_layers = trend_line_state(
    micro_high_source, micro_left, micro_right, micro_extension, micro_log,
    micro_filter, True, micro_on, micro_bear_color, micro_style, micro_width,
)
micro_low_value, micro_low_layers = trend_line_state(
    micro_low_source, micro_left, micro_right, micro_extension, micro_log,
    micro_filter, False, micro_on, micro_bull_color, micro_style, micro_width,
)


def crosses_over(source, boundary):
    result = np.zeros(n, dtype=bool)
    for index in range(1, n):
        if np.isfinite(boundary[index]) and np.isfinite(boundary[index - 1]):
            result[index] = source[index] > boundary[index] and source[index - 1] <= boundary[index - 1]
    return result


def crosses_under(source, boundary):
    result = np.zeros(n, dtype=bool)
    for index in range(1, n):
        if np.isfinite(boundary[index]) and np.isfinite(boundary[index - 1]):
            result[index] = source[index] < boundary[index] and source[index - 1] >= boundary[index - 1]
    return result


strong_buy = crosses_over(close, macro_high_value) if show_signals and macro_on else np.zeros(n, dtype=bool)
strong_sell = crosses_under(close, macro_low_value) if show_signals and macro_on else np.zeros(n, dtype=bool)
weak_buy = crosses_over(close, micro_high_value) if show_signals and micro_on else np.zeros(n, dtype=bool)
weak_sell = crosses_under(close, micro_low_value) if show_signals and micro_on else np.zeros(n, dtype=bool)

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )
atr = wilder_average(true_range, supertrend_atr)
midpoint = (high + low) * 0.5
upper_basic = midpoint + supertrend_multiplier * atr
lower_basic = midpoint - supertrend_multiplier * atr
upper_band = np.full(n, np.nan, dtype=float)
lower_band = np.full(n, np.nan, dtype=float)
supertrend_direction = np.ones(n, dtype=int)
supertrend_line = np.full(n, np.nan, dtype=float)

for index in range(n):
    if not np.isfinite(atr[index]):
        if index > 0:
            supertrend_direction[index] = supertrend_direction[index - 1]
        continue
    if index == 0 or not np.isfinite(upper_band[index - 1]):
        upper_band[index] = upper_basic[index]
        lower_band[index] = lower_basic[index]
        supertrend_direction[index] = 1
    else:
        upper_band[index] = (
            upper_basic[index]
            if upper_basic[index] < upper_band[index - 1] or close[index - 1] > upper_band[index - 1]
            else upper_band[index - 1]
        )
        lower_band[index] = (
            lower_basic[index]
            if lower_basic[index] > lower_band[index - 1] or close[index - 1] < lower_band[index - 1]
            else lower_band[index - 1]
        )
        if supertrend_direction[index - 1] == -1:
            supertrend_direction[index] = 1 if close[index] > upper_band[index - 1] else -1
        else:
            supertrend_direction[index] = -1 if close[index] < lower_band[index - 1] else 1
    supertrend_line[index] = lower_band[index] if supertrend_direction[index] == 1 else upper_band[index]

supertrend_buy = np.zeros(n, dtype=bool)
supertrend_sell = np.zeros(n, dtype=bool)
if supertrend_on and supertrend_signals:
    for index in range(1, n):
        supertrend_buy[index] = supertrend_direction[index] == 1 and supertrend_direction[index - 1] == -1
        supertrend_sell[index] = supertrend_direction[index] == -1 and supertrend_direction[index - 1] == 1


def numeric_line(values, enabled=True):
    return [float(value) if enabled and np.isfinite(value) else None for value in values]


def event_line(mask, buy_side):
    data = []
    for index, active in enumerate(mask):
        if not active:
            data.append(None)
            continue
        clearance = atr[index] * 0.30 if np.isfinite(atr[index]) else max(high[index] - low[index], 1e-9) * 0.50
        data.append(float(low[index] - clearance if buy_side else high[index] + clearance))
    return data


bull_supertrend = np.where(supertrend_direction == 1, supertrend_line, np.nan)
bear_supertrend = np.where(supertrend_direction == -1, supertrend_line, np.nan)
plots = [
    {
        "name": "Runtime Anchor", "title": False,
        "data": numeric_line(close), "color": "rgba(0,0,0,0)", "opacity": 0.0,
        "type": "line", "lineWidth": 1, "overlay": True,
    },
    {
        "name": "Supertrend Bull", "data": numeric_line(bull_supertrend, supertrend_on),
        "color": supertrend_bull_color, "type": "line", "lineWidth": supertrend_width,
        "overlay": True,
    },
    {
        "name": "Supertrend Bear", "data": numeric_line(bear_supertrend, supertrend_on),
        "color": supertrend_bear_color, "type": "line", "lineWidth": supertrend_width,
        "overlay": True,
    },
]

signals = [
    {"type": "buy", "name": "Strong Buy", "text": "B", "color": strong_buy_color, "renderMode": "events", "data": event_line(strong_buy, True)},
    {"type": "sell", "name": "Strong Sell", "text": "S", "color": strong_sell_color, "renderMode": "events", "data": event_line(strong_sell, False)},
    {"type": "buy", "name": "Weak Buy", "text": "b", "color": weak_buy_color, "renderMode": "events", "data": event_line(weak_buy, True)},
    {"type": "sell", "name": "Weak Sell", "text": "s", "color": weak_sell_color, "renderMode": "events", "data": event_line(weak_sell, False)},
    {"type": "buy", "name": "Supertrend Buy", "text": "ST", "color": supertrend_buy_color, "renderMode": "events", "data": event_line(supertrend_buy, True)},
    {"type": "sell", "name": "Supertrend Sell", "text": "ST", "color": supertrend_sell_color, "renderMode": "events", "data": event_line(supertrend_sell, False)},
]

layers = (macro_high_layers + macro_low_layers + micro_high_layers + micro_low_layers)[-500:]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
}
