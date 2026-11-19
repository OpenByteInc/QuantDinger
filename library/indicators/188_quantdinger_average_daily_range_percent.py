# ============================================================
# QuantDinger Average Daily Range Percent
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Average Daily Range Percent"
my_indicator_description = (
    "Measures the moving average of each daily high-to-low range as a percentage, "
    "then aligns completed daily values to the active chart timeframe."
)

indicator_timeframes = ["1D"]

# @param length int 20 Daily averaging length range=1:500:1
# @param line_color str #FF9800 ADR line color

length = min(500, max(1, int(params.get("length", 20))))
line_color = str(params.get("line_color", "#FF9800"))

df = df.copy()
n = len(df)

timeframe_seconds = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "2H": 7200,
    "4H": 14400,
    "6H": 21600,
    "8H": 28800,
    "12H": 43200,
    "1D": 86400,
    "1W": 604800,
}

def normalized_frame(frame):
    if frame is None or len(frame) == 0:
        return None
    result = frame.copy().reset_index(drop=True)
    for column in ("time", "high", "low"):
        if column not in result.columns:
            return None
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["time", "high", "low"]).reset_index(drop=True)
    if len(result) == 0:
        return None
    times = result["time"].to_numpy(dtype=float)
    if np.nanmax(np.abs(times)) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result

base_frame = normalized_frame(df)
base_time = base_frame["time"].to_numpy(dtype=float) if base_frame is not None else np.arange(n, dtype=float)
chart_timeframe = str(ctx.timeframe or "1D")
chart_seconds = timeframe_seconds.get(chart_timeframe, 86400)

daily_frame = base_frame if chart_timeframe == "1D" else normalized_frame(ctx.raw_series("1D"))
if daily_frame is None and base_frame is not None:
    buckets = np.floor(base_time / 86400.0).astype(np.int64)
    rows = []
    for bucket in np.unique(buckets):
        mask = buckets == bucket
        rows.append({
            "time": float(bucket * 86400),
            "high": float(np.max(base_frame.loc[mask, "high"].to_numpy(dtype=float))),
            "low": float(np.min(base_frame.loc[mask, "low"].to_numpy(dtype=float))),
        })
    daily_frame = pd.DataFrame(rows)

daily_time = daily_frame["time"].to_numpy(dtype=float)
daily_high = daily_frame["high"].to_numpy(dtype=float)
daily_low = daily_frame["low"].to_numpy(dtype=float)
daily_range_ratio = np.divide(
    daily_high,
    daily_low,
    out=np.full(len(daily_frame), np.nan, dtype=float),
    where=np.abs(daily_low) > 1e-12,
)

daily_adr = np.full(len(daily_frame), np.nan, dtype=float)
for index in range(length - 1, len(daily_frame)):
    window = daily_range_ratio[index - length + 1:index + 1]
    if np.all(np.isfinite(window)):
        daily_adr[index] = 100.0 * (float(np.mean(window)) - 1.0)

adr = np.full(n, np.nan, dtype=float)
if chart_timeframe == "1D" and len(daily_adr) == n:
    adr[:] = daily_adr
else:
    source_close_time = daily_time + 86400.0
    base_close_time = base_time + chart_seconds
    for index in range(n):
        source_index = int(np.searchsorted(source_close_time, base_close_time[index], side="right") - 1)
        if 0 <= source_index < len(daily_adr) and np.isfinite(daily_adr[source_index]):
            adr[index] = float(daily_adr[source_index])

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {
            "name": "ADR %",
            "data": [
                {"value": float(value), "color": line_color} if np.isfinite(value) else None
                for value in adr
            ],
            "color": line_color,
            "type": "line",
            "lineWidth": 2,
            "overlay": False,
        }
    ],
    "signals": [],
    "tables": [],
}
