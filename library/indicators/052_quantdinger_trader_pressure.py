# ============================================================
# QuantDinger Trader Pressure Index
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Trader Pressure Index"
my_indicator_description = (
    "Bull and bear pressure derived from two-bar range expansion, with "
    "weighted smoothing, net pressure, and dominance levels."
)

# @param average_length int 7 Pressure average range=1:100:1
# @param smoothing int 3 Net smoothing range=1:50:1
# @param control_level int 30 Control level range=5:100:5
# @param pre_smooth bool false Pre-smooth pressure
# @param pre_smooth_length int 3 Pre-smoothing range=1:50:1
# @param show_pressure_signal bool false Show dominance markers
# @param signal_level int 70 Marker level range=0:100:5

average_length = max(1, int(params.get("average_length", 7)))
smoothing = max(1, int(params.get("smoothing", 3)))
control_level = max(5, min(100, int(params.get("control_level", 30))))
pre_smooth = bool(params.get("pre_smooth", False))
pre_smooth_length = max(1, int(params.get("pre_smooth_length", 3)))
show_pressure_signal = bool(params.get("show_pressure_signal", False))
signal_level = max(0, min(100, int(params.get("signal_level", 70))))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
n = len(df)


def weighted_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    denominator = float(period * (period + 1)) / 2.0
    for index in range(period - 1, n):
        total = 0.0
        valid = True
        for offset in range(period):
            value = values[index - period + 1 + offset]
            if not np.isfinite(value):
                valid = False
                break
            total += value * float(offset + 1)
        if valid:
            result[index] = total / denominator
    return result


bulls = np.zeros(n, dtype=float)
bears = np.zeros(n, dtype=float)
for index in range(1, n):
    two_bar_range = max(high[index], high[index - 1]) - min(low[index], low[index - 1])
    if two_bar_range == 0.0:
        continue
    high_up = max(high[index] - high[index - 1], 0.0)
    low_up = max(low[index] - low[index - 1], 0.0)
    high_down = min(high[index] - high[index - 1], 0.0)
    low_down = min(low[index] - low[index - 1], 0.0)
    bulls[index] = min((high_up + low_up) / two_bar_range, 1.0) * 100.0
    bears[index] = max((high_down + low_down) / two_bar_range, -1.0) * -100.0

average_bulls = weighted_average(bulls, average_length)
average_bears = weighted_average(bears, average_length)
smoothed_bulls = weighted_average(average_bulls, pre_smooth_length) if pre_smooth else average_bulls
smoothed_bears = weighted_average(average_bears, pre_smooth_length) if pre_smooth else average_bears
net_pressure = smoothed_bulls - smoothed_bears
tpx = weighted_average(net_pressure, smoothing)

bull_area = []
bear_area = []
net_plot = []
hot_plot = []
cold_plot = []
for index in range(n):
    bull_area.append({"upper": float(smoothed_bulls[index]), "lower": 0.0, "color": "#33FF00"} if np.isfinite(smoothed_bulls[index]) else None)
    bear_area.append({"upper": float(smoothed_bears[index]), "lower": 0.0, "color": "#FF1111"} if np.isfinite(smoothed_bears[index]) else None)
    net_plot.append({"value": float(tpx[index]), "color": "#FFFFFF" if tpx[index] > 0.0 else "#808080"} if np.isfinite(tpx[index]) else None)
    hot_plot.append(float(signal_level) if show_pressure_signal and np.isfinite(smoothed_bulls[index]) and smoothed_bulls[index] >= control_level else None)
    cold_plot.append(float(signal_level) if show_pressure_signal and np.isfinite(smoothed_bears[index]) and smoothed_bears[index] >= control_level else None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Bull Pressure", "data": bull_area, "color": "#33FF00", "type": "cloud", "opacity": 0.40, "overlay": False},
        {"name": "Bear Pressure", "data": bear_area, "color": "#FF1111", "type": "cloud", "opacity": 0.32, "overlay": False},
        {"name": "Net Pressure", "data": net_plot, "color": "#FFFFFF", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Zero Level", "data": [0.0 for _ in range(n)], "color": "#FFEE00", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Control Level", "data": [float(control_level) for _ in range(n)], "color": "#FFEE00", "type": "line", "lineWidth": 2, "lineStyle": "dotted", "overlay": False},
        {"name": "Bull Dominance", "data": hot_plot, "color": "#16A34A", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Bear Dominance", "data": cold_plot, "color": "#7F1D1D", "type": "line", "lineWidth": 3, "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
