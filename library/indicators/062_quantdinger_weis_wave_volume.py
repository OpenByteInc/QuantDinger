# ============================================================
# QuantDinger Weis Wave Volume
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Weis Wave Volume"
my_indicator_description = (
    "Volume accumulated into price waves defined by ATR, a fixed reversal "
    "amount, or a fraction of price, with optional oscillation and normalization."
)

# @param assignment_method int 0 Assignment method range=0:2:1
# @param method_value float 14.0 Assignment value range=0.1:100.0:0.1
# @param price_source int 0 Price source range=0:2:1
# @param true_range_mode int 1 True range substitution range=0:2:1
# @param oscillating bool false Plot down waves below zero
# @param normalize bool false Normalize by bars in wave

assignment_method = max(0, min(2, int(params.get("assignment_method", 0))))
method_value = max(0.1, float(params.get("method_value", 14.0)))
price_source = max(0, min(2, int(params.get("price_source", 0))))
true_range_mode = max(0, min(2, int(params.get("true_range_mode", 1))))
oscillating = bool(params.get("oscillating", False))
normalize = bool(params.get("normalize", False))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

previous_close = np.concatenate(([close[0]], close[:-1]))
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close)))
atr_period = max(1, int(round(method_value)))
atr = np.full(n, np.nan, dtype=float)
if n >= atr_period:
    seed = 0.0
    for index in range(atr_period):
        seed += true_range[index]
    atr[atr_period - 1] = seed / atr_period
    for index in range(atr_period, n):
        atr[index] = (atr[index - 1] * (atr_period - 1) + true_range[index]) / atr_period

wave_input = true_range if true_range_mode == 0 else volume
wave_high = np.zeros(n, dtype=float)
wave_low = np.zeros(n, dtype=float)
for index in range(n):
    if price_source == 0:
        wave_high[index] = close[index]
        wave_low[index] = close[index]
    elif price_source == 1:
        wave_high[index] = max(open_price[index], close[index])
        wave_low[index] = min(open_price[index], close[index])
    else:
        wave_high[index] = high[index]
        wave_low[index] = low[index]

renko_close = np.zeros(n, dtype=float)
direction = np.zeros(n, dtype=int)
wave_total = np.zeros(n, dtype=float)
wave_bars = np.ones(n, dtype=int)
result = np.zeros(n, dtype=float)
for index in range(n):
    if assignment_method == 0:
        threshold = atr[index] if np.isfinite(atr[index]) else method_value
    elif assignment_method == 1:
        threshold = method_value
    else:
        threshold = close[index] / method_value if method_value != 0.0 else close[index]
    previous_wave_close = renko_close[index - 1] if index > 0 else 0.0
    previous_high = previous_wave_close + threshold
    previous_low = previous_wave_close - threshold
    renko_close[index] = wave_high[index] if wave_high[index] > previous_high else wave_low[index] if wave_low[index] < previous_low else previous_wave_close
    previous_direction = direction[index - 1] if index > 0 else 0
    direction[index] = 1 if renko_close[index] > previous_wave_close else -1 if renko_close[index] < previous_wave_close else previous_direction
    changed = index == 0 or direction[index] != previous_direction
    wave_bars[index] = 1 if changed else wave_bars[index - 1] + 1
    wave_total[index] = wave_input[index] if changed else wave_total[index - 1] + wave_input[index]
    value = wave_total[index] / wave_bars[index] if normalize and wave_bars[index] > 1 else wave_total[index]
    result[index] = -value if oscillating and direction[index] < 0 else value

wave_plot = []
for index, value in enumerate(result):
    wave_plot.append({"value": float(value), "color": "#16A34A" if direction[index] > 0 else "#EF4444"})

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Wave Volume", "data": wave_plot, "color": "#16A34A", "type": "histogram", "baseValue": 0.0, "lineWidth": 3, "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
