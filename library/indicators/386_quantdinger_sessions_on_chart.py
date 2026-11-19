# ============================================================
# QuantDinger Sessions on Chart
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Sessions on Chart"
my_indicator_description = "Highlights London, New York, Tokyo and Sydney sessions in America/New_York time with DST-aware running ranges."

# @param high_low_view bool false Fill only each session high-low range
# @param resolution str D Reset resolution values=D,W,M
# @param london_hours str 0300-1200:1234567 London session
# @param new_york_hours str 0800-1700:1234567 New York session
# @param tokyo_hours str 2000-0400:1234567 Tokyo session
# @param sydney_hours str 1700-0200:1234567 Sydney session
# @param show_london bool true Show London session
# @param show_new_york bool true Show New York session
# @param show_tokyo bool true Show Tokyo session
# @param show_sydney bool false Show Sydney session

high_low_view = bool(params.get("high_low_view", False))
resolution = str(params.get("resolution", "D"))
resolution = resolution if resolution in ("D", "W", "M") else "D"
london_hours = str(params.get("london_hours", "0300-1200:1234567"))
new_york_hours = str(params.get("new_york_hours", "0800-1700:1234567"))
tokyo_hours = str(params.get("tokyo_hours", "2000-0400:1234567"))
sydney_hours = str(params.get("sydney_hours", "1700-0200:1234567"))
show_london = bool(params.get("show_london", True))
show_new_york = bool(params.get("show_new_york", True))
show_tokyo = bool(params.get("show_tokyo", True))
show_sydney = bool(params.get("show_sydney", False))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _parse_session(text):
    parts = str(text).split(":", 1)
    hours = parts[0].replace(":", "")
    days = parts[1] if len(parts) > 1 else "1234567"
    if len(hours) != 9 or hours[4] != "-" or not hours[:4].isdigit() or not hours[5:].isdigit():
        hours = "0000-0000"
    start = int(hours[:2]) * 60 + int(hours[2:4])
    end = int(hours[5:7]) * 60 + int(hours[7:9])
    allowed_days = [int(value) for value in days if value in "1234567"]
    return min(1439, max(0, start)), min(1440, max(0, end)), allowed_days


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
times = df["time"].to_numpy(dtype=float)
if len(times) and np.nanmax(np.abs(times)) > 10_000_000_000:
    times = times / 1000.0
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)
bar_seconds = float(np.median(np.diff(times))) if n > 1 else 3600.0
display_first_index = max(0, n - (240 if bar_seconds < 14400.0 else 60))
local_time = pd.to_datetime(times, unit="s", utc=True).tz_convert("America/New_York")
local_minutes = np.asarray(local_time.hour * 60 + local_time.minute, dtype=int)
pine_weekday = np.asarray(((local_time.dayofweek + 1) % 7) + 1, dtype=int)
year = np.asarray(local_time.year, dtype=int)
month = np.asarray(local_time.month, dtype=int)
day_of_year = np.asarray(local_time.dayofyear, dtype=int)
period_key = year * 400 + day_of_year if resolution == "D" else year * 60 + ((day_of_year + 6) // 7) if resolution == "W" else year * 12 + month


def _calculate(hours, enabled):
    start, end, allowed_days = _parse_session(hours)
    day_allowed = np.asarray([value in allowed_days for value in pine_weekday], dtype=bool)
    if start == end:
        active = day_allowed
    elif start < end:
        active = day_allowed & (local_minutes >= start) & (local_minutes < end)
    else:
        active = day_allowed & ((local_minutes >= start) | (local_minutes < end))
    active &= enabled
    previous_active = np.r_[False, active[:-1]]
    previous_key = np.r_[period_key[0] if n else 0, period_key[:-1]]
    new_bar = active & (~previous_active | (period_key != previous_key))
    running_low = np.full(n, np.nan, dtype=float)
    running_high = np.full(n, np.nan, dtype=float)
    state_low = state_high = np.nan
    records = []
    current = None
    for index in range(n):
        if new_bar[index]:
            state_low, state_high = float(low[index]), float(high[index])
            current = {"startIndex": index, "endIndex": index, "low": state_low, "high": state_high}
            records.append(current)
        elif active[index]:
            state_low = min(state_low, float(low[index])) if np.isfinite(state_low) else float(low[index])
            state_high = max(state_high, float(high[index])) if np.isfinite(state_high) else float(high[index])
            if current is not None:
                current["endIndex"], current["low"], current["high"] = index, state_low, state_high
        running_low[index], running_high[index] = state_low, state_high
    return {"active": active, "newBar": new_bar, "low": running_low, "high": running_high, "records": records}


sessions = [
    ("London", london_hours, show_london, "#008000"),
    ("New York", new_york_hours, show_new_york, "#FF0000"),
    ("Tokyo", tokyo_hours, show_tokyo, "#FFFF00"),
    ("Sydney", sydney_hours, show_sydney, "#0000FF"),
]
results = [_calculate(hours, enabled) for _, hours, enabled, _ in sessions]
plots = [{"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "overlay": True}]
layers = []
background_colors = [None] * n
chart_low = float(np.nanmin(low)) if n else 0.0
chart_high = float(np.nanmax(high)) if n else 1.0
calculated = {"localMinute": local_minutes.tolist(), "pineWeekday": pine_weekday.tolist(), "periodKey": period_key.astype(int).tolist()}
for (name, _, enabled, color), result in zip(sessions, results):
    cloud = [{"upper": float(result["high"][index]), "lower": float(result["low"][index]), "color": color, "opacity": 0.10} if high_low_view and enabled and result["active"][index] and np.all(np.isfinite([result["high"][index], result["low"][index]])) else None for index in range(n)]
    plots.append({"name": f"{name} Range", "title": False, "data": cloud, "color": color, "type": "cloud", "overlay": True})
    if enabled and not high_low_view:
        for index in range(n):
            if result["active"][index]:
                background_colors[index] = color + "1A"
    if enabled:
        for record in result["records"]:
            if record["endIndex"] < display_first_index:
                continue
            layers.append({
                "type": "zone",
                "role": f"{name} Session",
                "startIndex": max(display_first_index, record["startIndex"]),
                "endIndex": record["endIndex"],
                "top": record["high"] if high_low_view else chart_high,
                "bottom": record["low"] if high_low_view else chart_low,
                "fillColor": color,
                "opacity": 0.10 if high_low_view else 0.035,
                "borderColor": color,
                "borderSize": 1 if high_low_view else 0,
            })
    key = name.replace(" ", "")
    calculated[key + "Active"] = result["active"].tolist()
    calculated[key + "NewBar"] = result["newBar"].tolist()
    calculated[key + "Low"] = _finite(result["low"])
    calculated[key + "High"] = _finite(result["high"])
    calculated[key + "Records"] = result["records"]

output = {"schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": [], "layers": layers, "tables": [], "bar_colors": [None] * n, "background_colors": background_colors, "calculatedVars": calculated}
