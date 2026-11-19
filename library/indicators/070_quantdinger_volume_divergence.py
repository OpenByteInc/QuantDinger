# ============================================================
# QuantDinger Volume Divergence
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Volume Divergence"
my_indicator_description = (
    "A five-stage candle-signed weighted volume oscillator with regular and "
    "hidden pivot divergences against price."
)

# @param first_length int 5 First weighted period range=1:30:1
# @param second_length int 8 Second weighted period range=1:30:1
# @param pivot_left int 5 Pivot left bars range=1:30:1
# @param pivot_right int 5 Pivot right bars range=1:30:1
# @param range_min int 5 Minimum pivot distance range=1:100:1
# @param range_max int 60 Maximum pivot distance range=2:300:1
# @param regular_bullish bool true Show regular bullish divergence
# @param hidden_bullish bool false Show hidden bullish divergence
# @param regular_bearish bool true Show regular bearish divergence
# @param hidden_bearish bool false Show hidden bearish divergence

first_length = max(1, int(params.get("first_length", 5)))
second_length = max(1, int(params.get("second_length", 8)))
pivot_left = max(1, int(params.get("pivot_left", 5)))
pivot_right = max(1, int(params.get("pivot_right", 5)))
range_min = max(1, int(params.get("range_min", 5)))
range_max = max(range_min + 1, int(params.get("range_max", 60)))
regular_bullish = bool(params.get("regular_bullish", True))
hidden_bullish = bool(params.get("hidden_bullish", False))
regular_bearish = bool(params.get("regular_bearish", True))
hidden_bearish = bool(params.get("hidden_bearish", False))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)
candle_sign = np.where(close < open_price, -1.0, 1.0)


def signed_weighted_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    denominator = float(period * period * (period + 1)) / 2.0
    for index in range(period - 1, n):
        total = 0.0
        valid = True
        for offset in range(period):
            value = values[index - offset]
            if not np.isfinite(value):
                valid = False
                break
            weight = float((period - offset) * period)
            total += value * weight * candle_sign[index - offset]
        if valid:
            result[index] = total / denominator
    return result


third_length = first_length + second_length
fourth_length = second_length + third_length
fifth_length = third_length + fourth_length
stage_1 = signed_weighted_average(volume, first_length)
stage_2 = signed_weighted_average(stage_1, second_length)
stage_3 = signed_weighted_average(stage_2, third_length)
stage_4 = signed_weighted_average(stage_3, fourth_length)
oscillator = signed_weighted_average(stage_4, fifth_length)

pivot_low = np.zeros(n, dtype=bool)
pivot_high = np.zeros(n, dtype=bool)
window = pivot_left + pivot_right + 1
for confirmation in range(window - 1, n):
    pivot = confirmation - pivot_right
    segment = oscillator[pivot - pivot_left : pivot + pivot_right + 1]
    if np.all(np.isfinite(segment)):
        pivot_low[pivot] = oscillator[pivot] == np.min(segment)
        pivot_high[pivot] = oscillator[pivot] == np.max(segment)

regular_bull = np.zeros(n, dtype=bool)
hidden_bull = np.zeros(n, dtype=bool)
regular_bear = np.zeros(n, dtype=bool)
hidden_bear = np.zeros(n, dtype=bool)
previous_low = -1
previous_high = -1
for index in range(n):
    if pivot_low[index]:
        if previous_low >= 0:
            distance = index - previous_low
            regular_bull[index] = regular_bullish and range_min <= distance <= range_max and oscillator[index] > oscillator[previous_low] and low[index] < low[previous_low]
            hidden_bull[index] = hidden_bullish and range_min <= distance <= range_max and oscillator[index] < oscillator[previous_low] and low[index] > low[previous_low]
        previous_low = index
    if pivot_high[index]:
        if previous_high >= 0:
            distance = index - previous_high
            regular_bear[index] = regular_bearish and range_min <= distance <= range_max and oscillator[index] < oscillator[previous_high] and high[index] > high[previous_high]
            hidden_bear[index] = hidden_bearish and range_min <= distance <= range_max and oscillator[index] > oscillator[previous_high] and high[index] < high[previous_high]
        previous_high = index

oscillator_plot = []
regular_bull_plot = []
hidden_bull_plot = []
regular_bear_plot = []
hidden_bear_plot = []
for index in range(n):
    oscillator_plot.append({"value": float(oscillator[index]), "color": "#16A34A" if oscillator[index] > 0.0 else "#EF4444"} if np.isfinite(oscillator[index]) else None)
    regular_bull_plot.append(float(oscillator[index]) if regular_bull[index] else None)
    hidden_bull_plot.append(float(oscillator[index]) if hidden_bull[index] else None)
    regular_bear_plot.append(float(oscillator[index]) if regular_bear[index] else None)
    hidden_bear_plot.append(float(oscillator[index]) if hidden_bear[index] else None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Signed Volume", "data": oscillator_plot, "color": "#16A34A", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Zero", "data": [0.0 for _ in range(n)], "color": "#C0C0C0", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Regular Bullish", "data": regular_bull_plot, "color": "#16A34A", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Hidden Bullish", "data": hidden_bull_plot, "color": "#16A34ABF", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Regular Bearish", "data": regular_bear_plot, "color": "#EF4444", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Hidden Bearish", "data": hidden_bear_plot, "color": "#EF4444BF", "type": "line", "lineWidth": 2, "overlay": False},
    ],
    "signals": [
        {"type": "buy", "text": "Bull", "name": "Regular Bullish Divergence", "data": regular_bull_plot, "color": "#16A34A", "renderMode": "events"},
        {"type": "buy", "text": "H Bull", "name": "Hidden Bullish Divergence", "data": hidden_bull_plot, "color": "#16A34A", "renderMode": "events"},
        {"type": "sell", "text": "Bear", "name": "Regular Bearish Divergence", "data": regular_bear_plot, "color": "#EF4444", "renderMode": "events"},
        {"type": "sell", "text": "H Bear", "name": "Hidden Bearish Divergence", "data": hidden_bear_plot, "color": "#EF4444", "renderMode": "events"},
    ],
    "layers": [],
}
