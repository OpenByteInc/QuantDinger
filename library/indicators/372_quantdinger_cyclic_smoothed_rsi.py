# ============================================================
# QuantDinger Cyclic Smoothed RSI
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Cyclic Smoothed RSI"
my_indicator_description = "Applies a phased cyclic filter to RSI and derives adaptive percentile bands from two dominant cycles of memory."

# @param dominant_cycle int 20 Dominant cycle length range=10:1000:1
# @param oscillator_color str #FF00FF Cyclic RSI color
# @param band_color str #00FFFF Adaptive band color

dominant_cycle = min(1000, max(10, int(params.get("dominant_cycle", 20))))
oscillator_color = str(params.get("oscillator_color", "#FF00FF"))
band_color = str(params.get("band_color", "#00FFFF"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    seed = []
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) == length:
                previous = float(np.mean(seed))
                result[index] = previous
        else:
            previous = (previous * (length - 1.0) + float(value)) / length
            result[index] = previous
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
close = df["close"].to_numpy(dtype=float)
n = len(df)

cycle_length = max(1, int(dominant_cycle / 2))
vibration = 10
cyclic_memory = dominant_cycle * 2
torque = 2.0 / (vibration + 1.0)
phasing_lag = int((vibration - 1) / 2.0)
change = np.diff(close, prepend=np.nan)
up = _rma(np.where(np.isfinite(change), np.maximum(change, 0.0), np.nan), cycle_length)
down = _rma(np.where(np.isfinite(change), np.maximum(-change, 0.0), np.nan), cycle_length)
rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.all(np.isfinite([up[index], down[index]])):
        rsi[index] = 100.0 if down[index] == 0.0 else 0.0 if up[index] == 0.0 else 100.0 - 100.0 / (1.0 + up[index] / down[index])

cyclic_rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    lag_index = index - phasing_lag
    if lag_index >= 0 and np.all(np.isfinite([rsi[index], rsi[lag_index]])):
        previous = cyclic_rsi[index - 1] if index and np.isfinite(cyclic_rsi[index - 1]) else 0.0
        cyclic_rsi[index] = torque * (2.0 * rsi[index] - rsi[lag_index]) + (1.0 - torque) * previous

lower_band = np.zeros(n, dtype=float)
upper_band = np.zeros(n, dtype=float)
local_maximum = np.full(n, -999999.0, dtype=float)
local_minimum = np.full(n, 999999.0, dtype=float)
for index in range(n):
    maximum = -999999.0
    minimum = 999999.0
    start = max(0, index - cyclic_memory + 1)
    window = cyclic_rsi[start:index + 1][::-1]
    for value in window:
        max_candidate = float(value) if np.isfinite(value) else -999999.0
        if max_candidate > maximum:
            maximum = max_candidate
        else:
            min_candidate = float(value) if np.isfinite(value) else 999999.0
            if min_candidate < minimum:
                minimum = min_candidate
    local_maximum[index], local_minimum[index] = maximum, minimum
    step_size = (maximum - minimum) / 100.0
    if not np.isfinite(step_size):
        continue
    sorted_window = np.sort(np.asarray([value for value in window if np.isfinite(value)], dtype=float))
    for step in range(101):
        test_value = minimum + step_size * step
        below = int(np.searchsorted(sorted_window, test_value, side="left"))
        if below / float(cyclic_memory) >= 0.10:
            lower_band[index] = test_value
            break
    for step in range(101):
        test_value = maximum - step_size * step
        above = len(sorted_window) - int(np.searchsorted(sorted_window, test_value, side="left"))
        if above / float(cyclic_memory) >= 0.10:
            upper_band[index] = test_value
            break

adaptive_cloud = [
    {"upper": float(max(upper_band[index], lower_band[index])), "lower": float(min(upper_band[index], lower_band[index])), "color": "#808080", "opacity": 0.10}
    for index in range(n)
]
reference_cloud = [{"upper": 70.0, "lower": 30.0, "color": "#C0C0C0", "opacity": 0.10} for _ in range(n)]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Low Band", "title": True, "data": _finite(lower_band), "color": band_color, "type": "line", "overlay": False},
        {"name": "High Band", "title": True, "data": _finite(upper_band), "color": band_color, "type": "line", "overlay": False},
        {"name": "Adaptive Range", "title": False, "data": adaptive_cloud, "color": "#808080", "type": "cloud", "overlay": False},
        {"name": "Reference Range", "title": False, "data": reference_cloud, "color": "#C0C0C0", "type": "cloud", "overlay": False},
        {"name": "Cyclic RSI", "title": True, "data": _finite(cyclic_rsi), "color": oscillator_color, "type": "line", "lineWidth": 2, "overlay": False},
    ],
    "signals": [], "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "cycleLength": int(cycle_length), "cyclicMemory": int(cyclic_memory), "torque": float(torque), "phasingLag": int(phasing_lag),
        "up": _finite(up), "down": _finite(down), "rsi": _finite(rsi), "cyclicRsi": _finite(cyclic_rsi),
        "localMaximum": _finite(local_maximum), "localMinimum": _finite(local_minimum), "lowerBand": _finite(lower_band), "upperBand": _finite(upper_band),
    },
}
