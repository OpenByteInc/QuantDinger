# ============================================================
# QuantDinger Intraday Volume Swings
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Intraday Volume Swings"
my_indicator_description = (
    "Tracks three-bar increasing-volume high and low swings, retains each "
    "day's outermost regions, and projects the prior day's regions."
)

# @param mark_swings bool true Mark completed volume swings
# @param mark_new_day bool true Highlight the first bar of each day
# @param mark_greater_swings bool true Label higher highs and lower lows by day
# @param plot_current bool false Show current-day forming regions
# @param region_must_close bool true Require close entry for region alerts

mark_swings = bool(params.get("mark_swings", True))
mark_new_day = bool(params.get("mark_new_day", True))
mark_greater_swings = bool(params.get("mark_greater_swings", True))
plot_current = bool(params.get("plot_current", False))
region_must_close = bool(params.get("region_must_close", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
open_price = df["open"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 3600.0
times = df["time"].to_numpy(dtype=float)
n = len(df)
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0
timestamps = pd.to_datetime(times, unit="ms", utc=True) if n else []

new_day = np.zeros(n, dtype=bool)
for index in range(1, n):
    new_day[index] = timestamps[index].date() != timestamps[index - 1].date()

increasing_volume = np.r_[False, volume[1:] > volume[:-1]]
lower_low = np.r_[False, low[1:] < low[:-1]]
higher_high = np.r_[False, high[1:] > high[:-1]]
low_bar = increasing_volume & lower_low
high_bar = increasing_volume & higher_high
swing_low = np.zeros(n, dtype=bool)
swing_high = np.zeros(n, dtype=bool)
for index in range(2, n):
    swing_low[index] = low_bar[index] and low_bar[index - 1] and low_bar[index - 2]
    swing_high[index] = high_bar[index] and high_bar[index - 1] and high_bar[index - 2]
confirmed_swing_low = np.r_[False, (~swing_low[1:]) & swing_low[:-1]]
confirmed_swing_high = np.r_[False, (~swing_high[1:]) & swing_high[:-1]]

swing_low_top = np.full(n, np.nan, dtype=float)
swing_low_bottom = np.full(n, np.nan, dtype=float)
swing_high_top = np.full(n, np.nan, dtype=float)
swing_high_bottom = np.full(n, np.nan, dtype=float)
for index in range(2, n):
    if swing_low[index] and not swing_low[index - 1]:
        swing_low_top[index] = float(np.max(high[index - 2:index + 1]))
        swing_low_bottom[index] = float(np.min(low[index - 2:index + 1]))
    elif swing_low[index] and swing_low[index - 1]:
        swing_low_top[index] = max(swing_low_top[index - 1], high[index])
        swing_low_bottom[index] = min(swing_low_bottom[index - 1], low[index])
    if swing_high[index] and not swing_high[index - 1]:
        swing_high_top[index] = float(np.max(high[index - 2:index + 1]))
        swing_high_bottom[index] = float(np.min(low[index - 2:index + 1]))
    elif swing_high[index] and swing_high[index - 1]:
        swing_high_top[index] = max(swing_high_top[index - 1], high[index])
        swing_high_bottom[index] = min(swing_high_bottom[index - 1], low[index])

daily_low_top = np.full(n, np.nan, dtype=float)
daily_low_bottom = np.full(n, np.nan, dtype=float)
daily_high_top = np.full(n, np.nan, dtype=float)
daily_high_bottom = np.full(n, np.nan, dtype=float)
state_low_top = np.nan
state_low_bottom = np.nan
state_high_top = np.nan
state_high_bottom = np.nan

previous_low_top = np.full(n, np.nan, dtype=float)
previous_low_bottom = np.full(n, np.nan, dtype=float)
previous_high_top = np.full(n, np.nan, dtype=float)
previous_high_bottom = np.full(n, np.nan, dtype=float)
second_low_bottom = np.full(n, np.nan, dtype=float)
second_high_top = np.full(n, np.nan, dtype=float)
prev_low_record = (np.nan, np.nan)
prev_high_record = (np.nan, np.nan)
older_low_bottom = np.nan
older_high_top = np.nan

for index in range(n):
    if new_day[index]:
        prior_daily_low_top = daily_low_top[index - 1]
        prior_daily_low_bottom = daily_low_bottom[index - 1]
        prior_daily_high_top = daily_high_top[index - 1]
        prior_daily_high_bottom = daily_high_bottom[index - 1]
        older_low_bottom = prev_low_record[1]
        older_high_top = prev_high_record[0]
        prev_low_record = (prior_daily_low_top, prior_daily_low_bottom)
        prev_high_record = (prior_daily_high_top, prior_daily_high_bottom)

        if not swing_low[index] and not confirmed_swing_low[index]:
            state_low_top = np.nan
            state_low_bottom = np.nan
        if swing_low[index]:
            state_low_top = swing_low_top[index]
            state_low_bottom = swing_low_bottom[index]
        if confirmed_swing_low[index]:
            state_low_top = swing_low_top[index - 1]
            state_low_bottom = swing_low_bottom[index - 1]

        if not swing_high[index] and not confirmed_swing_high[index]:
            state_high_top = np.nan
            state_high_bottom = np.nan
        if swing_high[index]:
            state_high_top = swing_high_top[index]
            state_high_bottom = swing_high_bottom[index]
        if confirmed_swing_high[index]:
            state_high_top = swing_high_top[index - 1]
            state_high_bottom = swing_high_bottom[index - 1]
    else:
        if confirmed_swing_low[index] and not np.isfinite(state_low_bottom):
            state_low_top = swing_low_top[index - 1]
            state_low_bottom = swing_low_bottom[index - 1]
        if confirmed_swing_low[index] and np.isfinite(state_low_bottom) and swing_low_bottom[index - 1] < state_low_bottom:
            state_low_top = swing_low_top[index - 1]
            state_low_bottom = swing_low_bottom[index - 1]
        if confirmed_swing_high[index] and not np.isfinite(state_high_bottom):
            state_high_top = swing_high_top[index - 1]
            state_high_bottom = swing_high_bottom[index - 1]
        if confirmed_swing_high[index] and np.isfinite(state_high_top) and swing_high_top[index - 1] > state_high_top:
            state_high_top = swing_high_top[index - 1]
            state_high_bottom = swing_high_bottom[index - 1]

    daily_low_top[index] = state_low_top
    daily_low_bottom[index] = state_low_bottom
    daily_high_top[index] = state_high_top
    daily_high_bottom[index] = state_high_bottom
    previous_low_top[index], previous_low_bottom[index] = prev_low_record
    previous_high_top[index], previous_high_bottom[index] = prev_high_record
    second_low_bottom[index] = older_low_bottom
    second_high_top[index] = older_high_top

lower_swing_low = np.isfinite(previous_low_bottom) & np.isfinite(second_low_bottom) & (previous_low_bottom < second_low_bottom)
higher_swing_high = np.isfinite(previous_high_top) & np.isfinite(second_high_top) & (previous_high_top > second_high_top)

try:
    chart_timeframe = str(ctx.timeframe or "")
except Exception:
    chart_timeframe = ""
valid_chart = bool(chart_timeframe not in ("1D", "1W", "D", "W") and np.any(np.isfinite(volume)))


def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


low_cloud = [
    {"upper": float(previous_low_top[i]), "lower": float(previous_low_bottom[i]), "color": "#F44336"}
    if valid_chart and np.isfinite(previous_low_top[i]) and np.isfinite(previous_low_bottom[i]) else None
    for i in range(n)
]
high_cloud = [
    {"upper": float(previous_high_top[i]), "lower": float(previous_high_bottom[i]), "color": "#00E676"}
    if valid_chart and np.isfinite(previous_high_top[i]) and np.isfinite(previous_high_bottom[i]) else None
    for i in range(n)
]

plots = [
    {"name": "Previous Swing Low Region", "data": low_cloud, "color": "#F44336", "type": "cloud", "opacity": 0.30, "overlay": True},
    {"name": "Previous Swing High Region", "data": high_cloud, "color": "#00E676", "type": "cloud", "opacity": 0.30, "overlay": True},
]
if plot_current:
    plots.extend([
        {"name": "Current Low Top", "data": _finite_or_none(daily_low_top), "color": "rgba(128,0,0,0.50)", "type": "circle", "size": 2, "overlay": True},
        {"name": "Current Low Bottom", "data": _finite_or_none(daily_low_bottom), "color": "rgba(128,0,0,0.50)", "type": "circle", "size": 2, "overlay": True},
        {"name": "Current High Top", "data": _finite_or_none(daily_high_top), "color": "rgba(0,128,0,0.50)", "type": "circle", "size": 2, "overlay": True},
        {"name": "Current High Bottom", "data": _finite_or_none(daily_high_bottom), "color": "rgba(0,128,0,0.50)", "type": "circle", "size": 2, "overlay": True},
    ])

layers = []
if mark_greater_swings and n:
    if higher_swing_high[-1] and np.isfinite(previous_high_top[-1]) and np.isfinite(previous_high_bottom[-1]):
        layers.append({"type": "label", "role": "Higher Swing High", "timestamp": float(times[-1]), "price": float((previous_high_top[-1] + previous_high_bottom[-1]) * 0.5), "text": "Higher Swing High", "anchor": "right", "color": "#008000", "borderColor": "#008000", "textColor": "#FFFFFF", "fontSize": 10})
    if lower_swing_low[-1] and np.isfinite(previous_low_top[-1]) and np.isfinite(previous_low_bottom[-1]):
        layers.append({"type": "label", "role": "Lower Swing Low", "timestamp": float(times[-1]), "price": float((previous_low_top[-1] + previous_low_bottom[-1]) * 0.5), "text": "Lower Swing Low", "anchor": "right", "color": "#F44336", "borderColor": "#F44336", "textColor": "#FFFFFF", "fontSize": 10})

up_previous_alert = np.zeros(n, dtype=bool)
down_previous_alert = np.zeros(n, dtype=bool)
up_current_alert = np.zeros(n, dtype=bool)
down_current_alert = np.zeros(n, dtype=bool)
for index in range(1, n):
    if region_must_close:
        up_previous_alert[index] = np.isfinite(previous_high_bottom[index]) and open_price[index - 1] < previous_high_bottom[index] and close[index - 1] >= previous_high_bottom[index]
        down_previous_alert[index] = np.isfinite(previous_low_top[index]) and open_price[index - 1] > previous_low_top[index] and close[index - 1] <= previous_low_top[index]
        up_current_alert[index] = np.isfinite(daily_high_bottom[index]) and open_price[index - 1] < daily_high_bottom[index] and close[index - 1] >= daily_high_bottom[index]
        down_current_alert[index] = np.isfinite(daily_low_top[index]) and open_price[index - 1] > daily_low_top[index] and close[index - 1] <= daily_low_top[index]
    else:
        up_previous_alert[index] = np.isfinite(previous_high_bottom[index]) and open_price[index] < previous_high_bottom[index] and high[index] >= previous_high_bottom[index]
        down_previous_alert[index] = np.isfinite(previous_low_top[index]) and open_price[index] > previous_low_top[index] and low[index] <= previous_low_top[index]
        up_current_alert[index] = np.isfinite(daily_high_bottom[index]) and open_price[index] < daily_high_bottom[index] and high[index] >= daily_high_bottom[index]
        down_current_alert[index] = np.isfinite(daily_low_top[index]) and open_price[index] > daily_low_top[index] and low[index] <= daily_low_top[index]

signals = [
    {"type": "buy", "name": "Swing Low", "text": "▲", "data": [float(low[i]) if mark_swings and confirmed_swing_low[i] else None for i in range(n)], "color": "#F44336", "renderMode": "events" if mark_swings else "hidden"},
    {"type": "sell", "name": "Swing High", "text": "▼", "data": [float(high[i]) if mark_swings and confirmed_swing_high[i] else None for i in range(n)], "color": "#00E676", "renderMode": "events" if mark_swings else "hidden"},
    {"type": "buy", "name": "Previous High Region Entry", "text": "REGION", "data": [float(close[i]) if up_previous_alert[i] else None for i in range(n)], "color": "#00E676", "renderMode": "hidden"},
    {"type": "sell", "name": "Previous Low Region Entry", "text": "REGION", "data": [float(close[i]) if down_previous_alert[i] else None for i in range(n)], "color": "#F44336", "renderMode": "hidden"},
    {"type": "buy", "name": "Current High Region Entry", "text": "CURRENT", "data": [float(close[i]) if up_current_alert[i] else None for i in range(n)], "color": "#00E676", "renderMode": "hidden"},
    {"type": "sell", "name": "Current Low Region Entry", "text": "CURRENT", "data": [float(close[i]) if down_current_alert[i] else None for i in range(n)], "color": "#F44336", "renderMode": "hidden"},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": [] if valid_chart else [{"title": "Intraday Volume Swings", "position": "top-right", "rows": [["Warning", "Intraday timeframes with volume only"]], "compact": True}],
    "bar_colors": [None] * n,
    "background_colors": ["rgba(255,235,59,0.18)" if mark_new_day and new_day[i] else None for i in range(n)],
    "calculatedVars": {
        "newDay": new_day.tolist(), "increasingVolume": increasing_volume.tolist(),
        "lowBar": low_bar.tolist(), "highBar": high_bar.tolist(),
        "swingLow": swing_low.tolist(), "swingHigh": swing_high.tolist(),
        "confirmedSwingLow": confirmed_swing_low.tolist(), "confirmedSwingHigh": confirmed_swing_high.tolist(),
        "swingLowTop": _finite_or_none(swing_low_top), "swingLowBottom": _finite_or_none(swing_low_bottom),
        "swingHighTop": _finite_or_none(swing_high_top), "swingHighBottom": _finite_or_none(swing_high_bottom),
        "dailyLowTop": _finite_or_none(daily_low_top), "dailyLowBottom": _finite_or_none(daily_low_bottom),
        "dailyHighTop": _finite_or_none(daily_high_top), "dailyHighBottom": _finite_or_none(daily_high_bottom),
        "previousLowTop": _finite_or_none(previous_low_top), "previousLowBottom": _finite_or_none(previous_low_bottom),
        "previousHighTop": _finite_or_none(previous_high_top), "previousHighBottom": _finite_or_none(previous_high_bottom),
        "secondLowBottom": _finite_or_none(second_low_bottom), "secondHighTop": _finite_or_none(second_high_top),
        "lowerSwingLow": lower_swing_low.tolist(), "higherSwingHigh": higher_swing_high.tolist(),
        "upPreviousAlert": up_previous_alert.tolist(), "downPreviousAlert": down_previous_alert.tolist(),
        "upCurrentAlert": up_current_alert.tolist(), "downCurrentAlert": down_current_alert.tolist(),
        "validChart": valid_chart,
    },
}
