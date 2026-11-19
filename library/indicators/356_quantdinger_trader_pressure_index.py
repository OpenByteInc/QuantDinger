# ============================================================
# QuantDinger Trader Pressure Index
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Trader Pressure Index"
my_indicator_description = "Measures bullish and bearish two-bar range pressure, smooths both sides, and plots their net control swing."

# @param average_length int 7 Pressure average length range=1:10000:1
# @param smoothing int 3 Net smoothing range=1:10000:1
# @param control_level int 30 Control level range=5:100:1
# @param pre_smoothing bool false Pre-smooth bull and bear pressure
# @param pre_smoothing_length int 3 Pre-smoothing length range=1:10000:1
# @param show_pressure_signal bool false Show dominant-pressure points
# @param pressure_signal_level int 70 Pressure signal height range=0:100:5

average_length = min(10000, max(1, int(params.get("average_length", 7))))
smoothing = min(10000, max(1, int(params.get("smoothing", 3))))
control_level = min(100, max(5, int(params.get("control_level", 30))))
pre_smoothing = bool(params.get("pre_smoothing", False))
pre_smoothing_length = min(10000, max(1, int(params.get("pre_smoothing_length", 3))))
show_pressure_signal = bool(params.get("show_pressure_signal", False))
pressure_signal_level = min(100, max(0, int(params.get("pressure_signal_level", 70))))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _wma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
n = len(df)

two_bar_range = np.empty(n, dtype=float)
high_change = np.full(n, np.nan, dtype=float)
low_change = np.full(n, np.nan, dtype=float)
for index in range(n):
    two_bar_range[index] = max(high[max(0, index - 1):index + 1]) - min(low[max(0, index - 1):index + 1])
if n > 1:
    high_change[1:] = np.diff(high)
    low_change[1:] = np.diff(low)
high_up = np.maximum(high_change, 0.0)
low_up = np.maximum(low_change, 0.0)
high_down = np.minimum(high_change, 0.0)
low_down = np.minimum(low_change, 0.0)
bulls = np.full(n, np.nan, dtype=float)
bears = np.full(n, np.nan, dtype=float)
valid_range = two_bar_range != 0.0
bulls[valid_range] = np.minimum((high_up[valid_range] + low_up[valid_range]) / two_bar_range[valid_range], 1.0) * 100.0
bears[valid_range] = np.maximum((high_down[valid_range] + low_down[valid_range]) / two_bar_range[valid_range], -1.0) * -100.0
bull_input = np.where(np.isfinite(bulls), bulls, 0.0)
bear_input = np.where(np.isfinite(bears), bears, 0.0)
average_bull = _wma(bull_input, average_length)
average_bear = _wma(bear_input, average_length)
smoothed_bull = _wma(average_bull, pre_smoothing_length) if pre_smoothing else average_bull.copy()
smoothed_bear = _wma(average_bear, pre_smoothing_length) if pre_smoothing else average_bear.copy()
net = smoothed_bull - smoothed_bear
pressure_index = _wma(net, smoothing)
bulls_in_control = smoothed_bull >= control_level
bears_in_control = smoothed_bear >= control_level
swing_up = np.asarray([index > 0 and np.all(np.isfinite([pressure_index[index], pressure_index[index - 1]])) and pressure_index[index] > 0.0 and pressure_index[index - 1] <= 0.0 for index in range(n)], dtype=bool)
swing_down = np.asarray([index > 0 and np.all(np.isfinite([pressure_index[index], pressure_index[index - 1]])) and pressure_index[index] < 0.0 and pressure_index[index - 1] >= 0.0 for index in range(n)], dtype=bool)
pressure_colors = ["#FFFFFF" if np.isfinite(value) and value > 0.0 else "#808080" for value in pressure_index]
bull_area = [{"upper": float(value), "lower": 0.0, "color": "#33FF00", "opacity": 0.40} if np.isfinite(value) else None for value in smoothed_bull]
bear_area = [{"upper": float(value), "lower": 0.0, "color": "#FF1111", "opacity": 0.60} if np.isfinite(value) else None for value in smoothed_bear]


def _events(mask, prices, text, color, event_type):
    return {"type": event_type, "name": text, "data": [{"active": True, "price": float(prices[index]), "text": text, "color": color} if active and np.isfinite(prices[index]) else None for index, active in enumerate(mask)], "color": color, "renderMode": "events"}


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Bull Pressure", "data": bull_area, "color": "#33FF00", "type": "cloud", "overlay": False},
        {"name": "Bear Pressure", "data": bear_area, "color": "#FF1111", "type": "cloud", "overlay": False},
        {"name": "Net Pressure", "data": _finite(pressure_index), "color": pressure_colors, "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Zero Line", "data": [0.0] * n, "color": "rgba(255,238,0,0.56)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Control Level", "data": [float(control_level)] * n, "color": "rgba(255,238,0,0.56)", "type": "line", "lineStyle": "dotted", "lineWidth": 2, "overlay": False},
    ],
    "signals": [
        _events(bulls_in_control & show_pressure_signal, np.full(n, pressure_signal_level), "Hot", "#008000", "buy"),
        _events(bears_in_control & show_pressure_signal, np.full(n, pressure_signal_level), "Cold", "#800000", "sell"),
        _events(swing_up, pressure_index, "Pressure Up", "#16A34A", "buy"),
        _events(swing_down, pressure_index, "Pressure Down", "#808080", "sell"),
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "twoBarRange": _finite(two_bar_range), "highChange": _finite(high_change), "lowChange": _finite(low_change),
        "highUp": _finite(high_up), "lowUp": _finite(low_up), "highDown": _finite(high_down), "lowDown": _finite(low_down),
        "bulls": _finite(bulls), "bears": _finite(bears), "averageBull": _finite(average_bull), "averageBear": _finite(average_bear),
        "smoothedBull": _finite(smoothed_bull), "smoothedBear": _finite(smoothed_bear), "net": _finite(net), "pressureIndex": _finite(pressure_index),
        "bullsInControl": bulls_in_control.tolist(), "bearsInControl": bears_in_control.tolist(), "swingUp": swing_up.tolist(), "swingDown": swing_down.tolist(),
    },
}
