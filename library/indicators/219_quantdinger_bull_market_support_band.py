# ============================================================
# QuantDinger Bull Market Support Band
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Bull Market Support Band"
my_indicator_description = (
    "Projects the weekly 20-period SMA and 21-period EMA onto the active chart "
    "and fills the support band between them."
)

indicator_timeframes = ["1W"]

# @param sma_length int 20 Weekly SMA length range=1:200:1
# @param ema_length int 21 Weekly EMA length range=1:200:1
# @param sma_color str #EF4444 SMA color
# @param ema_color str #22C55E EMA color
# @param band_color str #F59E0B Band color
# @param band_opacity float 0.20 Band opacity range=0.02:0.45:0.01

sma_length = min(200, max(1, int(params.get("sma_length", 20))))
ema_length = min(200, max(1, int(params.get("ema_length", 21))))
sma_color = str(params.get("sma_color", "#EF4444"))
ema_color = str(params.get("ema_color", "#22C55E"))
band_color = str(params.get("band_color", "#F59E0B"))
band_opacity = min(0.45, max(0.02, float(params.get("band_opacity", 0.20))))

base = df.copy()
base = base.reset_index(drop=True)
n = len(base)
if "time" not in base.columns:
    base["time"] = np.arange(n, dtype=float) * 3600.0
base_time = pd.to_numeric(base["time"], errors="coerce").to_numpy(dtype=float)
if n and np.nanmax(np.abs(base_time)) > 10_000_000_000:
    base_time = base_time / 1000.0


def normalize_weekly(frame):
    if frame is None or len(frame) == 0:
        return pd.DataFrame(columns=["time", "close"])
    result = frame.copy()
    result = result.reset_index(drop=True)
    if "time" not in result.columns:
        result["time"] = np.arange(len(result), dtype=float) * 604800.0
    if "close" not in result.columns:
        return pd.DataFrame(columns=["time", "close"])
    result["time"] = pd.to_numeric(result.get("time"), errors="coerce")
    result["close"] = pd.to_numeric(result.get("close"), errors="coerce")
    result = result.dropna(subset=["time", "close"])
    if len(result) and np.nanmax(np.abs(result["time"].to_numpy(dtype=float))) > 10_000_000_000:
        result["time"] = result["time"] / 1000.0
    return result.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


runtime_context = globals().get("ctx")
chart_timeframe = str(getattr(runtime_context, "timeframe", "") or "")
if chart_timeframe == "1W":
    weekly = normalize_weekly(base)
else:
    weekly_source = (
        runtime_context.raw_series("1W")
        if runtime_context is not None and hasattr(runtime_context, "raw_series")
        else None
    )
    weekly = normalize_weekly(weekly_source)
    if len(weekly) == 0:
        weekly = normalize_weekly(base)

weekly_count = len(weekly)
weekly_time = weekly["time"].to_numpy(dtype=float) if weekly_count else np.zeros(0, dtype=float)
weekly_close = weekly["close"].to_numpy(dtype=float) if weekly_count else np.zeros(0, dtype=float)
weekly_sma = (
    pd.Series(weekly_close, dtype=float).rolling(sma_length, min_periods=sma_length).mean().to_numpy(dtype=float)
    if weekly_count else np.zeros(0, dtype=float)
)
weekly_ema = np.full(weekly_count, np.nan, dtype=float)
if weekly_count:
    alpha = 2.0 / (float(ema_length) + 1.0)
    weekly_ema[0] = float(weekly_close[0])
    for index in range(1, weekly_count):
        weekly_ema[index] = alpha * float(weekly_close[index]) + (1.0 - alpha) * weekly_ema[index - 1]

aligned_sma = np.full(n, np.nan, dtype=float)
aligned_ema = np.full(n, np.nan, dtype=float)
weekly_source_index = np.full(n, -1, dtype=int)
if weekly_count:
    for index in range(n):
        source_index = int(np.searchsorted(weekly_time, base_time[index], side="right") - 1)
        if source_index < 0:
            continue
        weekly_source_index[index] = source_index
        if np.isfinite(weekly_sma[source_index]):
            aligned_sma[index] = float(weekly_sma[source_index])
        if np.isfinite(weekly_ema[source_index]):
            aligned_ema[index] = float(weekly_ema[source_index])


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


band_cloud = []
for index in range(n):
    if np.isfinite(aligned_sma[index]) and np.isfinite(aligned_ema[index]):
        band_cloud.append({
            "upper": float(max(aligned_sma[index], aligned_ema[index])),
            "lower": float(min(aligned_sma[index], aligned_ema[index])),
            "color": band_color,
        })
    else:
        band_cloud.append(None)

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "20W SMA" if sma_length == 20 else f"{sma_length}W SMA", "data": finite(aligned_sma), "color": sma_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "21W EMA" if ema_length == 21 else f"{ema_length}W EMA", "data": finite(aligned_ema), "color": ema_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Weekly Support Band", "title": False, "data": band_cloud, "color": band_color, "type": "cloud", "opacity": band_opacity, "overlay": True},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "weeklyBarCount": int(weekly_count),
        "weeklyTime": [float(value) for value in weekly_time],
        "weeklyClose": [float(value) for value in weekly_close],
        "weeklySma": finite(weekly_sma),
        "weeklyEma": finite(weekly_ema),
        "alignedSma": finite(aligned_sma),
        "alignedEma": finite(aligned_ema),
        "weeklySourceIndex": [int(value) for value in weekly_source_index],
    },
}
