# ============================================================
# QuantDinger Triangular Momentum Divergence
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Triangular Momentum Divergence"
my_indicator_description = (
    "A double-smoothed momentum oscillator with real-time regular bullish "
    "and bearish divergence detection at oscillator turning points."
)

# @param length int 7 Triangular momentum length range=2:100:1
# @param show_lines bool true Show divergence connector lines
# @param correlation_color bool false Color by price and oscillator correlation

length = max(2, int(params.get("length", 7)))
show_lines = bool(params.get("show_lines", True))
correlation_color = bool(params.get("correlation_color", False))

df = df.copy()
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def rolling_mean(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(period - 1, len(source)):
        window = source[index - period + 1 : index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def rolling_extreme(values, period, mode):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(len(source)):
        start = max(0, index - period + 1)
        window = source[start : index + 1]
        result[index] = float(np.max(window) if mode == "high" else np.min(window))
    return result


first_smooth = rolling_mean(close, length)
second_smooth = rolling_mean(first_smooth, length)
oscillator = np.full(n, np.nan, dtype=float)
for index in range(length, n):
    if np.isfinite(second_smooth[index]) and np.isfinite(second_smooth[index - length]):
        oscillator[index] = float(second_smooth[index] - second_smooth[index - length])

rolling_high = rolling_extreme(close, length, "high")
rolling_low = rolling_extreme(close, length, "low")
momentum_change = np.full(n, np.nan, dtype=float)
for index in range(1, n):
    if np.isfinite(oscillator[index]) and np.isfinite(oscillator[index - 1]):
        momentum_change[index] = float(oscillator[index] - oscillator[index - 1])

peak_turn = np.zeros(n, dtype=bool)
trough_turn = np.zeros(n, dtype=bool)
for index in range(2, n):
    if np.isfinite(momentum_change[index]) and np.isfinite(momentum_change[index - 1]):
        peak_turn[index] = momentum_change[index] < 0.0 and momentum_change[index - 1] >= 0.0
        trough_turn[index] = momentum_change[index] > 0.0 and momentum_change[index - 1] <= 0.0

bullish = np.zeros(n, dtype=bool)
bearish = np.zeros(n, dtype=bool)
bullish_line = [None for _ in range(n)]
bearish_line = [None for _ in range(n)]
previous_peak = None
previous_trough = None

for index in range(n):
    if peak_turn[index]:
        current_peak = (index, float(oscillator[index]), float(rolling_high[index]))
        if previous_peak is not None:
            bearish[index] = (
                oscillator[index] > 0.0
                and current_peak[1] < previous_peak[1]
                and current_peak[2] > previous_peak[2]
            )
            if bearish[index] and show_lines:
                left_index, left_value, _ = previous_peak
                span = max(1, index - left_index)
                for plot_index in range(left_index, index + 1):
                    ratio = (plot_index - left_index) / span
                    bearish_line[plot_index] = float(left_value + ratio * (current_peak[1] - left_value))
        previous_peak = current_peak

    if trough_turn[index]:
        current_trough = (index, float(oscillator[index]), float(rolling_low[index]))
        if previous_trough is not None:
            bullish[index] = (
                oscillator[index] < 0.0
                and current_trough[1] > previous_trough[1]
                and current_trough[2] < previous_trough[2]
            )
            if bullish[index] and show_lines:
                left_index, left_value, _ = previous_trough
                span = max(1, index - left_index)
                for plot_index in range(left_index, index + 1):
                    ratio = (plot_index - left_index) / span
                    bullish_line[plot_index] = float(left_value + ratio * (current_trough[1] - left_value))
        previous_trough = current_trough


def interpolate_color(left, right, ratio):
    ratio = max(0.0, min(1.0, float(ratio)))
    channels = [int(round(left[channel] + ratio * (right[channel] - left[channel]))) for channel in range(3)]
    return "#%02X%02X%02X" % tuple(channels)


def correlation_gradient(value):
    normalized = max(0.0, min(1.0, 0.5 * float(value) + 0.5))
    if normalized <= 0.5:
        return interpolate_color((255, 17, 0), (255, 169, 0), normalized * 2.0)
    return interpolate_color((255, 169, 0), (48, 255, 133), (normalized - 0.5) * 2.0)


histogram = []
for index in range(n):
    if not np.isfinite(oscillator[index]):
        histogram.append(None)
        continue
    if correlation_color and index >= length - 1:
        osc_window = oscillator[index - length + 1 : index + 1]
        close_window = close[index - length + 1 : index + 1]
        if np.all(np.isfinite(osc_window)) and np.std(osc_window) > 0.0 and np.std(close_window) > 0.0:
            plot_color = correlation_gradient(np.corrcoef(osc_window, close_window)[0, 1])
        else:
            plot_color = "#808080"
    else:
        rising = index > 0 and np.isfinite(oscillator[index - 1]) and oscillator[index] > oscillator[index - 1]
        if oscillator[index] > 0.0:
            plot_color = "#00C42B" if rising else "#4EE567"
        else:
            plot_color = "#C03920" if rising else "#FF441F"
    histogram.append({"value": float(oscillator[index]), "color": plot_color})

bullish_events = [float(oscillator[index]) if bullish[index] else None for index in range(n)]
bearish_events = [float(oscillator[index]) if bearish[index] else None for index in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Triangular Momentum", "data": histogram, "color": "#808080", "type": "column", "lineWidth": 2, "overlay": False},
        {"name": "Bullish Divergence", "data": bullish_line, "color": "#00C42B", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Bearish Divergence", "data": bearish_line, "color": "#FF441F", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Bullish Event", "data": bullish_events, "color": "#00C42B", "type": "column", "lineWidth": 4, "overlay": False},
        {"name": "Bearish Event", "data": bearish_events, "color": "#FF441F", "type": "column", "lineWidth": 4, "overlay": False},
        {"name": "Zero", "data": [0.0 for _ in range(n)], "color": "rgba(148, 163, 184, 0.40)", "type": "line", "lineWidth": 1, "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
