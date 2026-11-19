# ============================================================
# QuantDinger MTF Support Resistance
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger MTF Support Resistance"
my_indicator_description = (
    "Tracks rolling chart-timeframe extremes and aggregates the supplied bars "
    "into an automatically selected higher timeframe to form support zones."
)

# @param source str High/Low Level source values=High/Low,Close/Open
# @param higher_timeframe str Auto Higher timeframe values=Auto,15m,30m,1H,2H,3H,4H,12H,1D,1W,2W,1M,3M,6M,12M
# @param period int 10 Highest and lowest period range=1:100:1
# @param transparency int 80 Zone transparency percent range=0:100:5
# @param extend_lines bool false Extend the latest zone boundaries across history

source_name = str(params.get("source", "High/Low"))
higher_timeframe = str(params.get("higher_timeframe", "Auto"))
period = min(100, max(1, int(params.get("period", 10))))
transparency = min(100, max(0, int(params.get("transparency", 80))))
extend_lines = bool(params.get("extend_lines", False))

df = df.copy()
open_values = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)
if "time" in df.columns:
    raw_times = pd.to_numeric(df["time"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    nonzero_times = np.abs(raw_times[np.nonzero(raw_times)])
    timestamp_scale = float(np.median(nonzero_times)) if len(nonzero_times) else 0.0
    if timestamp_scale >= 1.0e17:
        times = (raw_times / 1.0e6).astype(np.int64)
    elif timestamp_scale >= 1.0e14:
        times = (raw_times / 1.0e3).astype(np.int64)
    elif timestamp_scale >= 1.0e11:
        times = raw_times.astype(np.int64)
    else:
        times = (raw_times * 1.0e3).astype(np.int64)
elif isinstance(df.index, pd.DatetimeIndex):
    times = (df.index.astype("int64") // 1_000_000).to_numpy(dtype=np.int64)
else:
    times = np.arange(n, dtype=np.int64) * 3_600_000

if source_name == "Close/Open":
    source_high = np.maximum(close, open_values)
    source_low = np.minimum(close, open_values)
else:
    source_high = high.copy()
    source_low = low.copy()

if n > 1:
    positive_deltas = np.diff(times)
    positive_deltas = positive_deltas[positive_deltas > 0]
    chart_minutes = float(np.median(positive_deltas)) / 60000.0 if len(positive_deltas) else 1440.0
else:
    chart_minutes = 1440.0

if higher_timeframe == "Auto":
    if chart_minutes <= 3.5:
        target_kind = "minutes"
        target_value = 15
    elif chart_minutes <= 15.5:
        target_kind = "minutes"
        target_value = 60
    elif chart_minutes <= 45.5:
        target_kind = "minutes"
        target_value = 120
    elif chart_minutes <= 60.5:
        target_kind = "minutes"
        target_value = 240
    elif chart_minutes <= 240.5:
        target_kind = "minutes"
        target_value = 1440
    elif chart_minutes <= 1440.5:
        target_kind = "weeks"
        target_value = 1
    elif chart_minutes <= 10080.5:
        target_kind = "months"
        target_value = 1
    else:
        target_kind = "months"
        target_value = 12
else:
    timeframe_map = {
        "15m": ("minutes", 15),
        "30m": ("minutes", 30),
        "1H": ("minutes", 60),
        "2H": ("minutes", 120),
        "3H": ("minutes", 180),
        "4H": ("minutes", 240),
        "12H": ("minutes", 720),
        "1D": ("minutes", 1440),
        "1W": ("weeks", 1),
        "2W": ("weeks", 2),
        "1M": ("months", 1),
        "3M": ("months", 3),
        "6M": ("months", 6),
        "12M": ("months", 12),
    }
    target_kind, target_value = timeframe_map.get(higher_timeframe, ("minutes", 1440))

if target_kind == "minutes":
    group_size_ms = int(target_value * 60000)
    group_keys = [int(value // group_size_ms) for value in times]
elif target_kind == "weeks":
    day_numbers = times // 86400000
    group_keys = [int((value - 4) // (7 * target_value)) for value in day_numbers]
else:
    date_index = pd.to_datetime(times, unit="ms", utc=True)
    month_numbers = date_index.year.to_numpy() * 12 + date_index.month.to_numpy() - 1
    group_keys = [int(value // target_value) for value in month_numbers]

chart_resistance = [None for _ in range(n)]
chart_support = [None for _ in range(n)]
higher_resistance = [None for _ in range(n)]
higher_support = [None for _ in range(n)]

current_chart_resistance = None
current_chart_support = None
group_highs = []
group_lows = []
previous_group = None
current_higher_resistance = None
current_higher_support = None

for index in range(n):
    local_start = max(0, index - period + 1)
    current_chart_resistance = float(np.max(source_high[local_start:index + 1]))
    current_chart_support = float(np.min(source_low[local_start:index + 1]))

    chart_resistance[index] = current_chart_resistance
    chart_support[index] = current_chart_support

    if previous_group is None or group_keys[index] != previous_group:
        group_highs.append(float(source_high[index]))
        group_lows.append(float(source_low[index]))
        previous_group = group_keys[index]
    else:
        group_highs[-1] = max(group_highs[-1], float(source_high[index]))
        group_lows[-1] = min(group_lows[-1], float(source_low[index]))

    higher_start = max(0, len(group_highs) - period)
    current_higher_resistance = float(max(group_highs[higher_start:]))
    current_higher_support = float(min(group_lows[higher_start:]))

    higher_resistance[index] = current_higher_resistance
    higher_support[index] = current_higher_support

zone_alpha = (100.0 - transparency) / 100.0
resistance_zone = []
support_zone = []
for index in range(n):
    if chart_resistance[index] is not None and higher_resistance[index] is not None:
        resistance_zone.append({
            "upper": float(max(chart_resistance[index], higher_resistance[index])),
            "lower": float(min(chart_resistance[index], higher_resistance[index])),
            "color": f"rgba(239, 83, 80, {zone_alpha:.2f})",
        })
    else:
        resistance_zone.append(None)

    if chart_support[index] is not None and higher_support[index] is not None:
        support_zone.append({
            "upper": float(max(chart_support[index], higher_support[index])),
            "lower": float(min(chart_support[index], higher_support[index])),
            "color": f"rgba(38, 166, 154, {zone_alpha:.2f})",
        })
    else:
        support_zone.append(None)

plots = [
    {
        "name": "Price Reference",
        "title": False,
        "data": [float(value) for value in close],
        "color": "rgba(255, 255, 255, 0.01)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    },
    {
        "name": "Resistance Zone",
        "title": False,
        "data": resistance_zone,
        "color": "#EF5350",
        "type": "cloud",
        "opacity": zone_alpha,
        "overlay": True,
    },
    {
        "name": "Support Zone",
        "title": False,
        "data": support_zone,
        "color": "#26A69A",
        "type": "cloud",
        "opacity": zone_alpha,
        "overlay": True,
    },
    {
        "name": "Chart Resistance",
        "data": chart_resistance,
        "color": "rgba(239, 83, 80, 0.82)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    },
    {
        "name": "Higher Resistance",
        "data": higher_resistance,
        "color": "#EF5350",
        "type": "line",
        "lineWidth": 2,
        "overlay": True,
    },
    {
        "name": "Chart Support",
        "data": chart_support,
        "color": "rgba(38, 166, 154, 0.82)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    },
    {
        "name": "Higher Support",
        "data": higher_support,
        "color": "#26A69A",
        "type": "line",
        "lineWidth": 2,
        "overlay": True,
    },
]

if extend_lines and n > 0:
    latest_levels = (
        ("Extended Chart Resistance", chart_resistance[-1], "rgba(239, 83, 80, 0.55)"),
        ("Extended Higher Resistance", higher_resistance[-1], "#EF5350"),
        ("Extended Chart Support", chart_support[-1], "rgba(38, 166, 154, 0.55)"),
        ("Extended Higher Support", higher_support[-1], "#26A69A"),
    )
    for name, value, color in latest_levels:
        if value is not None:
            plots.append({
                "name": name,
                "title": False,
                "data": [float(value) for _ in range(n)],
                "color": color,
                "type": "line",
                "lineWidth": 1,
                "overlay": True,
            })

output = {"name": my_indicator_name, "plots": plots, "signals": [], "layers": []}
