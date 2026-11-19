# ============================================================
# QuantDinger Five MA Clouds
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Five MA Clouds"
my_indicator_description = "Displays five independently configurable moving-average clouds with optional line visibility and forward displacement."

# @param ma_type str EMA Moving average type values=EMA,SMA
# @param short_length_1 int 8 Cloud 1 short length range=1:10000:1
# @param long_length_1 int 9 Cloud 1 long length range=1:10000:1
# @param short_length_2 int 5 Cloud 2 short length range=1:10000:1
# @param long_length_2 int 12 Cloud 2 long length range=1:10000:1
# @param short_length_3 int 34 Cloud 3 short length range=1:10000:1
# @param long_length_3 int 50 Cloud 3 long length range=1:10000:1
# @param short_length_4 int 72 Cloud 4 short length range=1:10000:1
# @param long_length_4 int 89 Cloud 4 long length range=1:10000:1
# @param short_length_5 int 180 Cloud 5 short length range=1:10000:1
# @param long_length_5 int 200 Cloud 5 long length range=1:10000:1
# @param source str HL2 Price source values=HL2,Close,Open,High,Low,HLC3,OHLC4
# @param show_lines bool false Display moving-average boundary lines
# @param show_cloud_1 bool true Show cloud 1
# @param show_cloud_2 bool true Show cloud 2
# @param show_cloud_3 bool true Show cloud 3
# @param show_cloud_4 bool false Show cloud 4
# @param show_cloud_5 bool false Show cloud 5
# @param leading_period int 0 Forward cloud displacement range=0:10000:1

ma_type = str(params.get("ma_type", "EMA"))
ma_type = ma_type if ma_type in ("EMA", "SMA") else "EMA"
short_lengths = [
    min(10000, max(1, int(params.get("short_length_1", 8)))), min(10000, max(1, int(params.get("short_length_2", 5)))),
    min(10000, max(1, int(params.get("short_length_3", 34)))), min(10000, max(1, int(params.get("short_length_4", 72)))),
    min(10000, max(1, int(params.get("short_length_5", 180)))),
]
long_lengths = [
    min(10000, max(1, int(params.get("long_length_1", 9)))), min(10000, max(1, int(params.get("long_length_2", 12)))),
    min(10000, max(1, int(params.get("long_length_3", 50)))), min(10000, max(1, int(params.get("long_length_4", 89)))),
    min(10000, max(1, int(params.get("long_length_5", 200)))),
]
source_name = str(params.get("source", "HL2"))
source_name = source_name if source_name in ("HL2", "Close", "Open", "High", "Low", "HLC3", "OHLC4") else "HL2"
show_lines = bool(params.get("show_lines", False))
show_clouds = [
    bool(params.get("show_cloud_1", True)), bool(params.get("show_cloud_2", True)), bool(params.get("show_cloud_3", True)),
    bool(params.get("show_cloud_4", False)), bool(params.get("show_cloud_5", False)),
]
leading_period = min(10000, max(0, int(params.get("leading_period", 0))))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _shift(values, displacement):
    result = np.full(len(values), np.nan, dtype=float)
    if displacement < len(values):
        result[displacement:] = values[:len(values) - displacement] if displacement else values
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

if source_name == "Close":
    source = close
elif source_name == "Open":
    source = opened
elif source_name == "High":
    source = high
elif source_name == "Low":
    source = low
elif source_name == "HLC3":
    source = (high + low + close) / 3.0
elif source_name == "OHLC4":
    source = (opened + high + low + close) / 4.0
else:
    source = (high + low) / 2.0

average = _ema if ma_type == "EMA" else _sma
short_raw = [average(source, length) for length in short_lengths]
long_raw = [average(source, length) for length in long_lengths]
short_shifted = [_shift(values, leading_period) for values in short_raw]
long_shifted = [_shift(values, leading_period) for values in long_raw]
bull_colors = ("#036103", "#4CAF50", "#2196F3", "#009688", "#05BED5")
bear_colors = ("#880E4F", "#F44336", "#FFB74D", "#F06292", "#E65100")
opacities = (0.55, 0.35, 0.30, 0.35, 0.35)
plots = []
cloud_state = []
for cloud_index in range(5):
    short_values = short_shifted[cloud_index]
    long_values = long_shifted[cloud_index]
    enabled = show_clouds[cloud_index]
    state = np.isfinite(short_values) & np.isfinite(long_values) & (short_values >= long_values)
    cloud_state.append(state)
    colors = [bull_colors[cloud_index] if value else bear_colors[cloud_index] for value in state]
    short_slope = np.asarray([index > 0 and np.isfinite(short_values[index]) and np.isfinite(short_values[index - 1]) and short_values[index] >= short_values[index - 1] for index in range(n)], dtype=bool)
    long_slope = np.asarray([index > 0 and np.isfinite(long_values[index]) and np.isfinite(long_values[index - 1]) and long_values[index] >= long_values[index - 1] for index in range(n)], dtype=bool)
    cloud = [
        {"upper": float(max(short_values[index], long_values[index])), "lower": float(min(short_values[index], long_values[index])), "color": colors[index], "opacity": opacities[cloud_index]}
        if enabled and np.all(np.isfinite([short_values[index], long_values[index]])) else None for index in range(n)
    ]
    plots.extend([
        {"name": "Cloud " + str(cloud_index + 1) + " Short", "title": show_lines and enabled, "data": _finite(short_values) if show_lines and enabled else [None] * n, "color": ["#808000" if value else "#800000" for value in short_slope], "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Cloud " + str(cloud_index + 1) + " Long", "title": show_lines and enabled, "data": _finite(long_values) if show_lines and enabled else [None] * n, "color": ["#008000" if value else "#FF0000" for value in long_slope], "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "MA Cloud " + str(cloud_index + 1), "title": enabled, "data": cloud, "color": bull_colors[cloud_index], "type": "cloud", "overlay": True},
    ])

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [], "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "source": _finite(source),
        **{"short" + str(index + 1): _finite(short_raw[index]) for index in range(5)},
        **{"long" + str(index + 1): _finite(long_raw[index]) for index in range(5)},
        **{"shiftedShort" + str(index + 1): _finite(short_shifted[index]) for index in range(5)},
        **{"shiftedLong" + str(index + 1): _finite(long_shifted[index]) for index in range(5)},
        **{"cloudBull" + str(index + 1): cloud_state[index].tolist() for index in range(5)},
    },
}
