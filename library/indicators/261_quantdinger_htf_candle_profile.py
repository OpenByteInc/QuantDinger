# ============================================================
# QuantDinger HTF Candle Profile
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger HTF Candle Profile"
my_indicator_description = (
    "Builds a daily, weekly, or monthly close-volume profile for the active "
    "higher-timeframe candle and projects its OHLC and point-of-control levels."
)

# @param timeframe str W Profile timeframe values=D,W,M
# @param offset int 5 Profile offset range=-40:100:1
# @param min_period int 50 Minimum profile period range=10:5000:1
# @param show_high bool true Show profile high
# @param show_low bool true Show profile low
# @param show_close bool true Show profile close
# @param show_open bool true Show profile open
# @param show_poc bool true Show point of control
# @param show_boundaries bool true Show timeframe boundaries
# @param bullish_color color #22A06B Bullish profile color
# @param bearish_color color #E5484D Bearish profile color

timeframe = str(params.get("timeframe", "W")).upper()
if timeframe not in ("D", "W", "M"):
    timeframe = "W"
offset = min(100, max(-40, int(params.get("offset", 5))))
min_period = min(5000, max(10, int(params.get("min_period", 50))))
show_high = bool(params.get("show_high", True))
show_low = bool(params.get("show_low", True))
show_close = bool(params.get("show_close", True))
show_open = bool(params.get("show_open", True))
show_poc = bool(params.get("show_poc", True))
show_boundaries = bool(params.get("show_boundaries", True))
bullish_color = str(params.get("bullish_color", "#22A06B"))
bearish_color = str(params.get("bearish_color", "#E5484D"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 3600.0
times = df["time"].to_numpy(dtype=float)
n = len(df)

if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0
timestamps = pd.to_datetime(times, unit="ms", utc=True) if n else []

period_keys = []
for timestamp in timestamps:
    if timeframe == "D":
        period_keys.append((int(timestamp.year), int(timestamp.dayofyear)))
    elif timeframe == "M":
        period_keys.append((int(timestamp.year), int(timestamp.month)))
    else:
        iso = timestamp.isocalendar()
        period_keys.append((int(iso.year), int(iso.week)))

timeframe_change = np.zeros(n, dtype=bool)
for index in range(1, n):
    timeframe_change[index] = period_keys[index] != period_keys[index - 1]
delayed_change = np.zeros(n, dtype=bool)
if n > 1:
    delayed_change[1:] = timeframe_change[:-1]

true_range = np.full(n, np.nan, dtype=float)
atr_200 = np.full(n, np.nan, dtype=float)
for index in range(n):
    previous_close = close[index - 1] if index else close[index]
    true_range[index] = max(
        high[index] - low[index],
        abs(high[index] - previous_close),
        abs(low[index] - previous_close),
    )
if n >= 200:
    atr_200[199] = float(np.mean(true_range[:200]))
    for index in range(200, n):
        atr_200[index] = (atr_200[index - 1] * 199.0 + true_range[index]) / 200.0

period_starts = []
period_opens = []
period_closes = []
period_history = np.zeros(n, dtype=int)
profile_open_history = np.full(n, np.nan, dtype=float)
profile_close_history = np.full(n, np.nan, dtype=float)
active_period = 0
profile_open = np.nan
profile_close = np.nan

for index in range(n):
    if delayed_change[index]:
        period_opens.append(float(open_price[index]))
        period_closes.append(float(close[index]))
        period_starts.append(index)
    if len(period_starts) > 1:
        if index - period_starts[-1] < min_period:
            active_period = index - period_starts[-2]
            profile_open = period_opens[-2]
            profile_close = period_closes[-1]
        else:
            active_period = index - period_starts[-1]
            profile_open = period_opens[-1]
            profile_close = float(close[index])
    period_history[index] = active_period
    profile_open_history[index] = profile_open
    profile_close_history[index] = profile_close

profile_available = bool(
    n and len(period_starts) > 1 and np.isfinite(atr_200[-1])
    and atr_200[-1] > 0.0 and active_period >= 0
)
window_start = max(0, n - 1 - active_period) if n else 0
profile_high = np.nan
profile_low = np.nan
profile_step = np.nan
bin_volumes = np.asarray([], dtype=float)
bin_widths = np.asarray([], dtype=int)
profile_color = bullish_color
layers = []

def _color_with_alpha(color, alpha):
    value = str(color).strip()
    if value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            red = int(value[1] * 2, 16)
            green = int(value[2] * 2, 16)
            blue = int(value[3] * 2, 16)
        else:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{max(0.0, min(1.0, float(alpha))):.3f})"
    return value

positive_intervals = np.diff(times)
positive_intervals = positive_intervals[np.isfinite(positive_intervals) & (positive_intervals > 0.0)]
bar_interval = float(np.median(positive_intervals)) if len(positive_intervals) else 3600000.0
last_time = float(times[-1]) if n else 0.0
profile_right_time = last_time + bar_interval * float(50 + offset)
candle_time = last_time + bar_interval * float(55 + offset)

if profile_available:
    window_high = high[window_start:n]
    window_low = low[window_start:n]
    profile_high = float(np.max(window_high))
    profile_low = float(np.min(window_low))
    profile_height = profile_high - profile_low
    atr_height = float(atr_200[-1] * 0.3)
    bin_count = int(np.ceil(profile_height / atr_height)) if profile_height > 0.0 else 0
    if bin_count > 0:
        profile_step = profile_height / float(bin_count)
        bin_volumes = np.zeros(bin_count, dtype=float)
        for source_index in range(window_start, n):
            source_close = float(close[source_index])
            for bin_index in range(bin_count):
                bin_low = profile_low + profile_step * bin_index
                midpoint = bin_low + profile_step * 0.5
                if abs(source_close - midpoint) <= profile_step:
                    bin_volumes[bin_index] += float(volume[source_index])
        maximum_volume = float(np.max(bin_volumes)) if len(bin_volumes) else 0.0
        if maximum_volume > 0.0:
            bin_widths = np.asarray([int(value / maximum_volume * 50.0) for value in bin_volumes], dtype=int)
        else:
            bin_widths = np.zeros(bin_count, dtype=int)

        profile_color = bullish_color if profile_close > profile_open else bearish_color
        retained_start = max(0, bin_count - 500)
        for bin_index in range(retained_start, bin_count):
            bottom = profile_low + profile_step * bin_index
            top = bottom + profile_step
            width = int(bin_widths[bin_index])
            left_time = profile_right_time - bar_interval * width
            strength = width / 50.0
            fill = _color_with_alpha(profile_color, 0.10 + strength * 0.90)
            layers.append({
                "type": "zone", "role": f"Profile Bin {bin_index}",
                "start": float(left_time), "end": float(profile_right_time),
                "top": float(top), "bottom": float(bottom),
                "color": fill, "fillColor": fill, "borderColor": "rgba(80,80,80,0.35)",
                "borderSize": 1, "opacity": 1.0,
            })
            if show_poc and width == 50:
                midpoint = float((top + bottom) * 0.5)
                layers.append({
                    "type": "line", "role": "Point of Control",
                    "start": float(times[window_start]), "end": float(left_time),
                    "y1": midpoint, "y2": midpoint, "color": profile_color, "lineWidth": 1,
                })
                layers.append({
                    "type": "label", "role": "Point of Control Volume",
                    "timestamp": float(times[window_start]), "price": midpoint,
                    "text": f"{bin_volumes[bin_index]:,.0f}", "anchor": "center",
                    "color": "rgba(0,0,0,0)", "borderColor": "rgba(0,0,0,0)",
                    "textColor": "#C8CCD4", "fontSize": 9,
                })

        layers.extend([
            {
                "type": "line", "role": "HTF Candle Wick", "start": candle_time, "end": candle_time,
                "y1": profile_high, "y2": profile_low, "color": profile_color, "lineWidth": 1,
            },
            {
                "type": "line", "role": "HTF Candle Body", "start": candle_time, "end": candle_time,
                "y1": float(profile_close), "y2": float(profile_open), "color": profile_color, "lineWidth": 10,
            },
        ])

        level_specs = [
            (show_high, "High", profile_high, True),
            (show_low, "Low", profile_low, True),
            (show_close, "Close", float(profile_close), False),
            (show_open, "Open", float(profile_open), False),
        ]
        for enabled, name, price, dashed in level_specs:
            if not enabled:
                continue
            layers.append({
                "type": "line", "role": f"Profile {name}",
                "start": float(times[window_start]), "end": candle_time,
                "y1": float(price), "y2": float(price), "color": profile_color,
                "lineWidth": 1, "dashed": bool(dashed),
            })
            layers.append({
                "type": "label", "role": f"Profile {name} Label",
                "timestamp": candle_time, "price": float(price), "text": f"{name}: {price:,.2f}",
                "anchor": "center", "color": "rgba(0,0,0,0)",
                "borderColor": "rgba(0,0,0,0)", "textColor": "#C8CCD4", "fontSize": 9,
            })

boundary_points = [
    {"value": float(low[index]), "color": "rgba(180,185,195,0.65)", "size": 3}
    if show_boundaries and delayed_change[index] else None
    for index in range(n)
]
background_colors = [
    "rgba(180,185,195,0.10)" if show_boundaries and delayed_change[index] else None
    for index in range(n)
]

def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "HTF Boundary", "title": False, "data": boundary_points, "color": "#AEB4C0", "type": "circle", "size": 3, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite_or_none(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [],
    "layers": layers,
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": background_colors,
    "calculatedVars": {
        "timeframeChange": timeframe_change.tolist(),
        "delayedChange": delayed_change.tolist(),
        "periodStarts": [int(value) for value in period_starts],
        "periodHistory": [int(value) for value in period_history],
        "profileOpenHistory": _finite_or_none(profile_open_history),
        "profileCloseHistory": _finite_or_none(profile_close_history),
        "atr200": _finite_or_none(atr_200),
        "profileAvailable": bool(profile_available and len(bin_widths)),
        "windowStart": int(window_start), "period": int(active_period),
        "profileOpen": float(profile_open) if np.isfinite(profile_open) else None,
        "profileClose": float(profile_close) if np.isfinite(profile_close) else None,
        "profileHigh": float(profile_high) if np.isfinite(profile_high) else None,
        "profileLow": float(profile_low) if np.isfinite(profile_low) else None,
        "profileStep": float(profile_step) if np.isfinite(profile_step) else None,
        "binVolumes": _finite_or_none(bin_volumes),
        "binWidths": [int(value) for value in bin_widths],
        "profileColor": profile_color,
        "profileRightTime": float(profile_right_time), "candleTime": float(candle_time),
        "barInterval": float(bar_interval),
    },
}
