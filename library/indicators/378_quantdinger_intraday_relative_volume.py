# ============================================================
# QuantDinger Intraday Relative Volume
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Intraday Relative Volume"
my_indicator_description = "Compares the current session's cumulative volume with the same clock slot across a rolling set of prior sessions."

# @param comparison_days int 5 Number of prior sessions range=1:55:1
# @param daylight_shift int 0 Latest-week daylight shift range=-1:1:1
# @param bullish_color str #008200 Bullish column color
# @param bearish_color str #820000 Bearish column color

comparison_days = min(55, max(1, int(params.get("comparison_days", 5))))
daylight_shift = min(1, max(-1, int(params.get("daylight_shift", 0))))
bullish_color = str(params.get("bullish_color", "#008200"))
bearish_color = str(params.get("bearish_color", "#820000"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _seconds(values):
    result = np.asarray(values, dtype=float).copy()
    if len(result) and np.nanmax(np.abs(result)) > 10_000_000_000:
        result /= 1000.0
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
time_seconds = _seconds(df["time"].to_numpy(dtype=float))
opened = df["open"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].fillna(0.0).to_numpy(dtype=float)
n = len(df)

if n > 1:
    positive_steps = np.diff(time_seconds)
    positive_steps = positive_steps[positive_steps > 0.0]
    median_step = float(np.median(positive_steps)) if len(positive_steps) else np.nan
else:
    median_step = np.nan
timeframe_valid = not np.isfinite(median_step) or 60.0 <= median_step < 86400.0

day_identifier = np.floor(time_seconds / 86400.0).astype(np.int64) if n else np.asarray([], dtype=np.int64)
week_identifier = np.floor((day_identifier + 3) / 7).astype(np.int64) if n else np.asarray([], dtype=np.int64)
latest_week = int(np.max(week_identifier)) if n else 0
new_day = np.zeros(n, dtype=bool)
if n > 1:
    new_day[1:] = day_identifier[1:] != day_identifier[:-1]
base_minute = np.zeros(n, dtype=int)
for index in range(n):
    minute = int(np.floor(time_seconds[index] / 60.0) % 1440)
    base_minute[index] = minute + (daylight_shift * 60 if week_identifier[index] == latest_week else 0)

extended_slot = np.zeros(n, dtype=int)
plus_day = np.zeros(n, dtype=int)
current_cumulative = np.zeros(n, dtype=float)
historical_average = np.zeros(n, dtype=float)
relative_volume = np.zeros(n, dtype=float)
history = [np.zeros(2880, dtype=float) for _ in range(comparison_days)]
current_session = np.zeros(2880, dtype=float)
plus_state = 0
previous_slot = 0

for index in range(n):
    if new_day[index]:
        history.pop(0)
        history.append(current_session)
        current_session = np.zeros(2880, dtype=float)
        plus_state = 0
    if index > 0 and base_minute[index] < base_minute[index - 1] and not new_day[index]:
        plus_state = 1440
    slot = base_minute[index] + plus_state
    slot = min(2879, max(0, slot))
    if index == 0 or new_day[index]:
        cumulative = float(volume[index])
    else:
        cumulative = float(current_session[previous_slot] + volume[index])
    current_session[slot] = cumulative
    baseline = 0.0
    for prior_session in history:
        prior_value = float(prior_session[slot])
        if prior_value == 0.0:
            nonzero = np.flatnonzero(prior_session[:slot + 1])
            prior_value = float(prior_session[nonzero[-1]]) if len(nonzero) else 0.0
        baseline += prior_value / comparison_days
    extended_slot[index] = slot
    plus_day[index] = plus_state
    current_cumulative[index] = cumulative
    historical_average[index] = baseline
    relative_volume[index] = 0.0 if baseline == 0.0 else cumulative / baseline
    previous_slot = slot

layers = []
for index in np.flatnonzero(new_day):
    layers.append({"type": "line", "role": "Session Start", "startIndex": int(index), "endIndex": int(index), "y1": 0.0, "y2": max(1.0, float(relative_volume[index])), "color": "#808080", "lineWidth": 1, "lineStyle": "dashed"})

bull_columns = [float(relative_volume[index]) if close[index] >= opened[index] else None for index in range(n)]
bear_columns = [float(relative_volume[index]) if close[index] < opened[index] else None for index in range(n)]
tables = []
if not timeframe_valid:
    tables = [{"title": "Timeframe", "position": "top-right", "compact": True, "rows": [[{"text": "Requires 1 minute to under 1 day", "color": "#FF0000"}, {"text": f"Median: {median_step:.0f}s" if np.isfinite(median_step) else "Unavailable"}]]}]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Bullish Relative Volume", "title": True, "data": bull_columns, "color": bullish_color, "type": "histogram", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
        {"name": "Bearish Relative Volume", "title": True, "data": bear_columns, "color": bearish_color, "type": "histogram", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
        {"name": "Zero", "title": True, "data": [0.0] * n, "color": "#808080", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "0.5", "title": True, "data": [0.5] * n, "color": "rgba(128,128,128,0.55)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "1.0", "title": True, "data": [1.0] * n, "color": "#808080", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "1.5", "title": True, "data": [1.5] * n, "color": "rgba(128,128,128,0.55)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "2.0", "title": True, "data": [2.0] * n, "color": "#808080", "type": "line", "lineWidth": 1, "overlay": False},
    ],
    "signals": [],
    "layers": layers,
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "timeSeconds": _finite(time_seconds), "dayIdentifier": day_identifier.tolist(), "weekIdentifier": week_identifier.tolist(), "latestWeek": int(latest_week),
        "newDay": new_day.tolist(), "baseMinute": base_minute.tolist(), "plusDay": plus_day.tolist(), "extendedSlot": extended_slot.tolist(),
        "currentCumulative": _finite(current_cumulative), "historicalAverage": _finite(historical_average), "relativeVolume": _finite(relative_volume),
        "timeframeValid": bool(timeframe_valid), "medianStepSeconds": None if not np.isfinite(median_step) else float(median_step),
    },
}
