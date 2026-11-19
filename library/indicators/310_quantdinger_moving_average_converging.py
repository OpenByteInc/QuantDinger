# ============================================================
# QuantDinger Moving Average Converging
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Moving Average Converging"
my_indicator_description = "A range-accelerated converging average paired with an adaptive fast envelope."

# @param length int 100 Converging length range=1:2000:1
# @param increment float 10.0 Alpha increment divisor range=0.01:1000:0.01
# @param fast_length float 10.0 Fast convergence divisor range=0.01:1000:0.01
# @param source str Close Price source values=Close,Open,High,Low,HL2,HLC3,OHLC4

length = min(2000, max(1, int(params.get("length", 100))))
increment = min(1000.0, max(0.01, float(params.get("increment", 10.0))))
fast_length = min(1000.0, max(0.01, float(params.get("fast_length", 10.0))))
source_name = str(params.get("source", "Close")).upper()
if source_name not in ("CLOSE", "OPEN", "HIGH", "LOW", "HL2", "HLC3", "OHLC4"):
    source_name = "CLOSE"


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)
source = {"OPEN": opened, "HIGH": high, "LOW": low, "HL2": (high + low) / 2.0, "HLC3": (high + low + close) / 3.0, "OHLC4": (opened + high + low + close) / 4.0}.get(source_name, close)

upper = np.full(n, np.nan, dtype=float)
lower = np.full(n, np.nan, dtype=float)
initial_average = np.full(n, np.nan, dtype=float)
alpha = np.zeros(n, dtype=float)
converging = np.full(n, np.nan, dtype=float)
fast = np.full(n, np.nan, dtype=float)
cross = np.zeros(n, dtype=bool)
increment_step = 1.0 / increment
for index in range(n):
    start = max(0, index - length + 1)
    upper[index] = float(np.max(source[start:index + 1]))
    lower[index] = float(np.min(source[start:index + 1]))
    if index >= length - 1:
        initial_average[index] = float(np.mean(source[index - length + 1:index + 1]))
    previous_ma = converging[index - 1] if index >= 1 else np.nan
    previous_alpha = alpha[index - 1] if index >= 1 else 0.0
    if index >= 1 and np.all(np.isfinite([source[index], source[index - 1], previous_ma, converging[index - 2] if index >= 2 else np.nan])):
        cross[index] = (source[index] > previous_ma and source[index - 1] <= converging[index - 2]) or (source[index] < previous_ma and source[index - 1] >= converging[index - 2])
    if cross[index]:
        alpha[index] = 2.0 / (length + 1.0)
    elif np.isfinite(previous_ma) and source[index] > previous_ma and index >= 1 and upper[index] > upper[index - 1]:
        alpha[index] = previous_alpha + increment_step
    elif np.isfinite(previous_ma) and source[index] < previous_ma and index >= 1 and lower[index] < lower[index - 1]:
        alpha[index] = previous_alpha + increment_step
    else:
        alpha[index] = previous_alpha
    candidate = previous_ma + previous_alpha * (source[index] - previous_ma) if np.isfinite(previous_ma) else np.nan
    converging[index] = candidate if np.isfinite(candidate) else initial_average[index]
    previous_fast = fast[index - 1] if index >= 1 else np.nan
    if cross[index] and np.isfinite(previous_fast):
        fast_candidate = (source[index] + previous_fast) / 2.0
    elif np.isfinite(converging[index]) and source[index] > converging[index] and np.isfinite(previous_fast):
        fast_candidate = max(source[index], previous_fast) + (source[index] - previous_fast) / fast_length
    elif np.isfinite(previous_fast):
        fast_candidate = min(source[index], previous_fast) + (source[index] - previous_fast) / fast_length
    else:
        fast_candidate = np.nan
    fast[index] = fast_candidate if np.isfinite(fast_candidate) else source[index]

colors = ["#008080" if np.isfinite(converging[index]) and fast[index] > converging[index] else "#FF0000" for index in range(n)]
cloud = []
for index in range(n):
    if np.all(np.isfinite([fast[index], converging[index]])):
        cloud.append({"upper": float(max(fast[index], converging[index])), "lower": float(min(fast[index], converging[index])), "color": "rgba(0,128,128,0.20)" if fast[index] > converging[index] else "rgba(255,0,0,0.20)"})
    else:
        cloud.append(None)

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Fast Average", "title": False, "data": _finite(fast), "color": "rgba(255,93,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Converging Average", "data": _finite(converging), "color": colors, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Convergence Fill", "title": False, "data": cloud, "color": "#008080", "type": "cloud", "overlay": True},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "upper": _finite(upper), "lower": _finite(lower), "initialAverage": _finite(initial_average), "alpha": _finite(alpha),
        "cross": cross.tolist(), "convergingAverage": _finite(converging), "fastAverage": _finite(fast), "trendColors": colors,
        "source": source_name,
    },
}
