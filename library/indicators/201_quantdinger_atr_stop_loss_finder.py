# ============================================================
# QuantDinger ATR Stop Loss Finder
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger ATR Stop Loss Finder"
my_indicator_description = (
    "Calculates configurable smoothed true range and places upper short-stop and lower long-stop "
    "levels around selected price sources, with current values shown in a compact table."
)

# @param length int 14 ATR smoothing length range=1:500:1
# @param smoothing str RMA Smoothing method values=RMA,SMA,EMA,WMA
# @param multiplier float 1.5 ATR multiplier range=0.01:20.0:0.05
# @param upper_source str High Upper stop source values=High,Low,Open,Close,HL2,HLC3
# @param lower_source str Low Lower stop source values=High,Low,Open,Close,HL2,HLC3
# @param show_price_lines bool true Extend current stop prices to the right
# @param atr_text_color str #2196F3 ATR table color
# @param low_text_color str #009688 Long stop table color
# @param high_text_color str #F44336 Short stop table color
# @param long_line_color str #009688 Long stop line color
# @param short_line_color str #F44336 Short stop line color

length = min(500, max(1, int(params.get("length", 14))))
smoothing = str(params.get("smoothing", "RMA"))
if smoothing not in ("RMA", "SMA", "EMA", "WMA"):
    smoothing = "RMA"
multiplier = min(20.0, max(0.01, float(params.get("multiplier", 1.5))))
upper_source = str(params.get("upper_source", "High"))
lower_source = str(params.get("lower_source", "Low"))
source_names = ("High", "Low", "Open", "Close", "HL2", "HLC3")
if upper_source not in source_names:
    upper_source = "High"
if lower_source not in source_names:
    lower_source = "Low"
show_price_lines = bool(params.get("show_price_lines", True))
atr_text_color = str(params.get("atr_text_color", "#2196F3"))
low_text_color = str(params.get("low_text_color", "#009688"))
high_text_color = str(params.get("high_text_color", "#F44336"))
long_line_color = str(params.get("long_line_color", "#009688"))
short_line_color = str(params.get("short_line_color", "#F44336"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def simple_average(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(period - 1, len(source)):
        window = source[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def exponential_average(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < period:
        return result
    result[period - 1] = float(np.mean(source[:period]))
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(source)):
        result[index] = alpha * source[index] + (1.0 - alpha) * result[index - 1]
    return result


def weighted_average(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    weights = np.arange(1.0, period + 1.0)
    divisor = float(np.sum(weights))
    for index in range(period - 1, len(source)):
        window = source[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / divisor)
    return result


def wilder_average(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < period:
        return result
    result[period - 1] = float(np.mean(source[:period]))
    for index in range(period, len(source)):
        result[index] = (result[index - 1] * (period - 1) + source[index]) / period
    return result


def selected_source(name):
    if name == "Low":
        return low
    if name == "Open":
        return open_price
    if name == "Close":
        return close
    if name == "HL2":
        return (high + low) * 0.5
    if name == "HLC3":
        return (high + low + close) / 3.0
    return high


def finite_line(values):
    return [float(value) if np.isfinite(value) else None for value in values]


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

if smoothing == "SMA":
    smoothed_range = simple_average(true_range, length)
elif smoothing == "EMA":
    smoothed_range = exponential_average(true_range, length)
elif smoothing == "WMA":
    smoothed_range = weighted_average(true_range, length)
else:
    smoothed_range = wilder_average(true_range, length)

atr_distance = smoothed_range * multiplier
short_stop = selected_source(upper_source) + atr_distance
long_stop = selected_source(lower_source) - atr_distance

plots = [
    {"name": "ATR Short Stop Loss", "data": finite_line(short_stop), "color": short_line_color, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "ATR Long Stop Loss", "data": finite_line(long_stop), "color": long_line_color, "type": "line", "lineWidth": 2, "overlay": True},
]

layers = []
if show_price_lines and n >= 2 and np.isfinite(short_stop[-1]) and np.isfinite(long_stop[-1]):
    layers.extend([
        {
            "type": "line",
            "startIndex": n - 2,
            "endIndex": n - 1,
            "price": float(short_stop[-1]),
            "text": "Short Stop",
            "color": short_line_color,
            "lineWidth": 1,
            "dashed": True,
            "extendRight": True,
        },
        {
            "type": "line",
            "startIndex": n - 2,
            "endIndex": n - 1,
            "price": float(long_stop[-1]),
            "text": "Long Stop",
            "color": long_line_color,
            "lineWidth": 1,
            "dashed": True,
            "extendRight": True,
        },
    ])

tables = []
finite_indexes = np.flatnonzero(np.isfinite(atr_distance) & np.isfinite(short_stop) & np.isfinite(long_stop))
if len(finite_indexes):
    final_index = int(finite_indexes[-1])
    tables.append({
        "title": "ATR STOP LOSS",
        "position": "bottom-right",
        "compact": True,
        "borderColor": atr_text_color,
        "accentColor": atr_text_color,
        "rows": [[
            {"text": f"ATR: {atr_distance[final_index]:.1f}", "color": atr_text_color},
            {"text": f"H: {short_stop[final_index]:.1f}", "color": high_text_color},
            {"text": f"L: {long_stop[final_index]:.1f}", "color": low_text_color},
        ]],
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": layers,
    "tables": tables,
}
