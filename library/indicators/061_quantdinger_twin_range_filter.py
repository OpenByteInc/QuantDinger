# ============================================================
# QuantDinger Twin Range Filter
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Twin Range Filter"
my_indicator_description = (
    "Fast and slow double-smoothed ranges drive a recursive price filter and "
    "trailing regime line with crossover events and directional highlighting."
)

# @param fast_period int 27 Fast period range=1:200:1
# @param fast_multiplier float 1.6 Fast range multiplier range=0.1:10.0:0.1
# @param slow_period int 55 Slow period range=1:300:1
# @param slow_multiplier float 2.0 Slow range multiplier range=0.1:10.0:0.1
# @param show_signals bool true Show buy and sell signals
# @param highlight_state bool true Highlight filter state

fast_period = max(1, int(params.get("fast_period", 27)))
fast_multiplier = max(0.1, float(params.get("fast_multiplier", 1.6)))
slow_period = max(1, int(params.get("slow_period", 55)))
slow_multiplier = max(0.1, float(params.get("slow_multiplier", 2.0)))
show_signals = bool(params.get("show_signals", True))
highlight_state = bool(params.get("highlight_state", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def exponential_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


absolute_change = np.zeros(n, dtype=float)
for index in range(1, n):
    absolute_change[index] = abs(close[index] - close[index - 1])
fast_range = exponential_average(exponential_average(absolute_change, fast_period), fast_period * 2 - 1) * fast_multiplier
slow_range = exponential_average(exponential_average(absolute_change, slow_period), slow_period * 2 - 1) * slow_multiplier
smooth_range = (fast_range + slow_range) / 2.0

price_filter = np.zeros(n, dtype=float)
upper_band = np.zeros(n, dtype=float)
lower_band = np.zeros(n, dtype=float)
final_upper = np.zeros(n, dtype=float)
final_lower = np.zeros(n, dtype=float)
trail = np.zeros(n, dtype=float)
for index in range(n):
    if index == 0:
        price_filter[index] = close[index]
    elif close[index] > price_filter[index - 1]:
        price_filter[index] = price_filter[index - 1] if close[index] - smooth_range[index] < price_filter[index - 1] else close[index] - smooth_range[index]
    else:
        price_filter[index] = price_filter[index - 1] if close[index] + smooth_range[index] > price_filter[index - 1] else close[index] + smooth_range[index]
    upper_band[index] = price_filter[index] + smooth_range[index]
    lower_band[index] = price_filter[index] - smooth_range[index]
    if index == 0:
        final_upper[index] = upper_band[index]
        final_lower[index] = lower_band[index]
        trail[index] = upper_band[index] if close[index] <= upper_band[index] else lower_band[index]
    else:
        final_upper[index] = upper_band[index] if upper_band[index] < final_upper[index - 1] or close[index - 1] > final_upper[index - 1] else final_upper[index - 1]
        final_lower[index] = lower_band[index] if lower_band[index] > final_lower[index - 1] or close[index - 1] < final_lower[index - 1] else final_lower[index - 1]
        if trail[index - 1] == final_upper[index - 1]:
            trail[index] = final_upper[index] if close[index] <= final_upper[index] else final_lower[index]
        else:
            trail[index] = final_lower[index] if close[index] >= final_lower[index] else final_upper[index]

long_event = np.zeros(n, dtype=bool)
short_event = np.zeros(n, dtype=bool)
state_cloud = []
mid_price = (open_price + high + low + close) / 4.0
for index in range(n):
    if index > 0:
        long_event[index] = close[index] > trail[index] and close[index - 1] <= trail[index - 1]
        short_event[index] = close[index] < trail[index] and close[index - 1] >= trail[index - 1]
    if highlight_state:
        state_cloud.append({"upper": float(max(mid_price[index], trail[index])), "lower": float(min(mid_price[index], trail[index])), "color": "#16A34A" if close[index] > trail[index] else "#EF4444"})
    else:
        state_cloud.append(None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Twin Range Trail", "data": [float(value) for value in trail], "color": "#2962FF", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Twin Range State", "title": False, "data": state_cloud, "color": "#16A34A", "type": "cloud", "opacity": 0.10, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "Twin Range Buy", "data": [float(low[i]) if show_signals and long_event[i] else None for i in range(n)], "color": "#0013E6", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Twin Range Sell", "data": [float(high[i]) if show_signals and short_event[i] else None for i in range(n)], "color": "#0013E6", "renderMode": "events"},
    ],
    "layers": [],
}
