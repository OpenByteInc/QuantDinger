# ============================================================
# QuantDinger Tillson T3
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Tillson T3"
my_indicator_description = (
    "Tillson's six-stage T3 moving average with direction coloring, an "
    "optional Fibonacci-factor companion, and slope reversal events."
)

# @param length int 8 T3 period range=1:100:1
# @param volume_factor float 0.7 Volume factor range=0.0:1.0:0.01
# @param show_fibonacci bool false Show Fibonacci T3
# @param fibonacci_length int 5 Fibonacci T3 period range=1:100:1
# @param fibonacci_factor float 0.618 Fibonacci factor range=0.0:1.0:0.001

length = max(1, int(params.get("length", 8)))
volume_factor = max(0.0, min(1.0, float(params.get("volume_factor", 0.7))))
show_fibonacci = bool(params.get("show_fibonacci", False))
fibonacci_length = max(1, int(params.get("fibonacci_length", 5)))
fibonacci_factor = max(0.0, min(1.0, float(params.get("fibonacci_factor", 0.618))))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)
source = (high + low + 2.0 * close) / 4.0


def exponential_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


def tillson(values, period, factor):
    stage1 = exponential_average(values, period)
    stage2 = exponential_average(stage1, period)
    stage3 = exponential_average(stage2, period)
    stage4 = exponential_average(stage3, period)
    stage5 = exponential_average(stage4, period)
    stage6 = exponential_average(stage5, period)
    coefficient1 = -(factor ** 3)
    coefficient2 = 3.0 * factor * factor + 3.0 * factor ** 3
    coefficient3 = -6.0 * factor * factor - 3.0 * factor - 3.0 * factor ** 3
    coefficient4 = 1.0 + 3.0 * factor + 3.0 * factor * factor + factor ** 3
    return coefficient1 * stage6 + coefficient2 * stage5 + coefficient3 * stage4 + coefficient4 * stage3


t3 = tillson(source, length, volume_factor)
fibonacci_t3 = tillson(source, fibonacci_length, fibonacci_factor)
t3_plot = []
fibonacci_plot = []
buy_event = np.zeros(n, dtype=bool)
sell_event = np.zeros(n, dtype=bool)
for index in range(n):
    direction = 0.0 if index == 0 else t3[index] - t3[index - 1]
    color = "#16A34A" if direction > 0.0 else "#EF4444" if direction < 0.0 else "#FACC15"
    t3_plot.append({"value": float(t3[index]), "color": color})
    fib_direction = 0.0 if index == 0 else fibonacci_t3[index] - fibonacci_t3[index - 1]
    fib_color = "#2962FF" if fib_direction > 0.0 else "#9333EA" if fib_direction < 0.0 else "#FACC15"
    fibonacci_plot.append({"value": float(fibonacci_t3[index]), "color": fib_color} if show_fibonacci else None)
    if index >= 2:
        previous_direction = t3[index - 1] - t3[index - 2]
        buy_event[index] = direction > 0.0 and previous_direction <= 0.0
        sell_event[index] = direction < 0.0 and previous_direction >= 0.0

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Tillson T3", "data": t3_plot, "color": "#16A34A", "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Fibonacci T3", "data": fibonacci_plot, "color": "#2962FF", "type": "line", "lineWidth": 2, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "T3 Slope Up", "data": [float(t3[i]) if buy_event[i] else None for i in range(n)], "color": "#16A34A", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "T3 Slope Down", "data": [float(t3[i]) if sell_event[i] else None for i in range(n)], "color": "#EF4444", "renderMode": "events"},
    ],
    "layers": [],
}
