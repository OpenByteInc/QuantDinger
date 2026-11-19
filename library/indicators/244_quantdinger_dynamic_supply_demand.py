# ============================================================
# QuantDinger Dynamic Supply and Demand
# Independent Python implementation for the QuantDinger runtime.
# ============================================================

my_indicator_name = "QuantDinger Dynamic Supply and Demand"
my_indicator_description = (
    "Builds a candle-overlap volume profile from the latest confirmed major pivot "
    "and marks low-participation price bins as dynamic supply and demand zones."
)

# @param dynamic_lookback bool true Use latest confirmed major pivot
# @param fixed_lookback int 100 Fixed lookback range=10:4999:1
# @param pivot_period int 200 Pivot confirmation period range=2:1000:1
# @param profile_resolution int 40 Profile resolution range=5:200:1
# @param profile_width int 33 Profile horizontal width range=1:100:1
# @param zone_tolerance float 0.93 Zone tolerance range=0.01:1.0:0.01
# @param show_profile bool true Show right-side volume profile
# @param show_background bool true Show lookback background
# @param show_zone_labels bool true Show zone strength labels
# @param level_color str #808080 Neutral level color
# @param bullish_color str #00FFBB Bullish zone color
# @param bearish_color str #FF1100 Bearish zone color

dynamic_lookback = bool(params.get("dynamic_lookback", True))
fixed_lookback = min(4999, max(10, int(params.get("fixed_lookback", 100))))
pivot_period = min(1000, max(2, int(params.get("pivot_period", 200))))
profile_resolution = min(200, max(5, int(params.get("profile_resolution", 40))))
profile_width = min(100, max(1, int(params.get("profile_width", 33))))
zone_tolerance = min(1.0, max(0.01, float(params.get("zone_tolerance", 0.93))))
show_profile = bool(params.get("show_profile", True))
show_background = bool(params.get("show_background", True))
show_zone_labels = bool(params.get("show_zone_labels", True))
level_color = str(params.get("level_color", "#808080"))
bullish_color = str(params.get("bullish_color", "#00FFBB"))
bearish_color = str(params.get("bearish_color", "#FF1100"))

df = df.copy()
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 3600.0
high_values = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
volume_values = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
time_values = pd.to_numeric(df["time"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
n = len(df)


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def rgba(color, alpha):
    value = str(color).strip().lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    try:
        red, green, blue = int(value[:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        red, green, blue = 128, 128, 128
    return f"rgba({red},{green},{blue},{min(1.0, max(0.0, float(alpha))):.3f})"


pivot_high_confirmation = np.zeros(n, dtype=bool)
pivot_low_confirmation = np.zeros(n, dtype=bool)
pivot_high_anchor = np.zeros(n, dtype=bool)
pivot_low_anchor = np.zeros(n, dtype=bool)
pivot_anchor_at_confirmation = np.full(n, -1, dtype=int)
latest_pivot_index = 0
for confirmation in range(2 * pivot_period, n):
    anchor = confirmation - pivot_period
    start = anchor - pivot_period
    end = anchor + pivot_period + 1
    high_window = high_values[start:end]
    low_window = low_values[start:end]
    high_pivot = np.isfinite(high_values[anchor]) and high_values[anchor] >= float(np.nanmax(high_window))
    low_pivot = np.isfinite(low_values[anchor]) and low_values[anchor] <= float(np.nanmin(low_window))
    if high_pivot:
        pivot_high_confirmation[confirmation] = True
        pivot_high_anchor[anchor] = True
    if low_pivot:
        pivot_low_confirmation[confirmation] = True
        pivot_low_anchor[anchor] = True
    if high_pivot or low_pivot:
        latest_pivot_index = anchor
        pivot_anchor_at_confirmation[confirmation] = anchor

if n:
    window_start = latest_pivot_index if dynamic_lookback else max(0, n - 1 - fixed_lookback)
    window_start = min(n - 1, max(0, int(window_start)))
else:
    window_start = 0
window_length = n - window_start

bottom_boundaries = np.full(profile_resolution, np.nan, dtype=float)
top_boundaries = np.full(profile_resolution, np.nan, dtype=float)
bin_volume = np.zeros(profile_resolution, dtype=float)
zone_mask = np.zeros(profile_resolution, dtype=bool)
profile_ratio = np.zeros(profile_resolution, dtype=float)
window_high = np.nan
window_low = np.nan
bin_step = np.nan

if n and window_length > 0:
    window_high = float(np.nanmax(high_values[window_start:]))
    window_low = float(np.nanmin(low_values[window_start:]))
    bin_step = (window_high - window_low) / float(profile_resolution)
    if not np.isfinite(bin_step) or bin_step <= 0.0:
        bin_step = max(abs(window_high) * 1e-9, 1e-9)
    for index in range(profile_resolution):
        bottom = window_low + index * bin_step
        top = window_low + (index + 1) * bin_step
        bottom_boundaries[index] = bottom
        top_boundaries[index] = top
        overlaps = ~((low_values[window_start:] > top) | (high_values[window_start:] < bottom))
        bin_volume[index] = float(np.sum(volume_values[window_start:][overlaps]))
    maximum_volume = float(np.max(bin_volume)) if len(bin_volume) else 0.0
    if maximum_volume > 0.0:
        profile_ratio = bin_volume / maximum_volume
    for index in range(profile_resolution):
        lower_neighbor = True if index == 0 else bin_volume[index] < bin_volume[index - 1] * zone_tolerance
        upper_neighbor = True if index == profile_resolution - 1 else bin_volume[index] < bin_volume[index + 1] * zone_tolerance
        zone_mask[index] = lower_neighbor and upper_neighbor

if n > 1:
    valid_intervals = np.diff(time_values)
    valid_intervals = valid_intervals[np.isfinite(valid_intervals) & (valid_intervals > 0.0)]
    bar_interval = float(np.median(valid_intervals)) if len(valid_intervals) else 1.0
else:
    bar_interval = 1.0

layers = []
zone_records = []
if n and np.isfinite(window_high) and np.isfinite(window_low):
    if show_background:
        layers.append({
            "type": "box", "role": "Profile Lookback",
            "start": float(time_values[window_start]), "end": float(time_values[-1]),
            "top": window_high, "bottom": window_low,
            "color": rgba("#2962FF", 0.22), "fillColor": rgba("#2962FF", 0.10),
            "borderColor": rgba("#2962FF", 0.30), "borderSize": 1, "opacity": 1.0,
        })

    for index in range(profile_resolution):
        top = float(top_boundaries[index])
        bottom = float(bottom_boundaries[index])
        if close_values[-1] > top:
            active_color = bullish_color
        elif close_values[-1] < bottom:
            active_color = bearish_color
        else:
            active_color = level_color

        if zone_mask[index]:
            depth = (1.0 - profile_ratio[index]) * 100.0
            zone_records.append({
                "index": int(index), "top": top, "bottom": bottom,
                "volume": float(bin_volume[index]), "ratio": float(profile_ratio[index]),
                "depth": float(depth), "side": "demand" if close_values[-1] > top else "supply" if close_values[-1] < bottom else "active",
            })
            layers.append({
                "type": "box", "role": "Low Participation Zone",
                "start": float(time_values[window_start]), "end": float(time_values[-1]),
                "top": top, "bottom": bottom,
                "color": rgba(active_color, 0.40), "fillColor": rgba(active_color, 0.26),
                "borderColor": rgba(active_color, 0.55), "borderSize": 1, "opacity": 1.0,
            })
            if show_zone_labels:
                layers.append({
                    "type": "label", "index": int(window_start), "price": float((top + bottom) * 0.5),
                    "text": f"{depth:.0f}%", "color": rgba(active_color, 0.80),
                    "textColor": "#FFFFFF", "side": "center",
                })

        if show_profile and profile_ratio[index] > 0.0:
            right_time = float(time_values[-1] + bar_interval)
            width_bars = float(round(bin_volume[index])) / max(1.0, float(round(np.max(bin_volume)))) * profile_width
            left_time = right_time - bar_interval * width_bars
            layers.append({
                "type": "box", "role": "Volume Profile Bin",
                "start": float(left_time), "end": right_time,
                "top": top, "bottom": bottom,
                "color": rgba(active_color, 0.42), "fillColor": rgba(active_color, 0.36),
                "borderColor": rgba("#000000", 0.75), "borderSize": 1, "opacity": 1.0,
            })

pivot_high_points = [
    {"value": float(high_values[index]), "color": "#E2E8F0", "size": 3}
    if pivot_high_anchor[index] else None for index in range(n)
]
pivot_low_points = [
    {"value": float(low_values[index]), "color": "#E2E8F0", "size": 3}
    if pivot_low_anchor[index] else None for index in range(n)
]
plots = [
    {"name": "Price Anchor", "title": False, "data": finite(close_values), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Confirmed Pivot High", "title": False, "data": pivot_high_points, "color": "#E2E8F0", "type": "scatter", "lineWidth": 3, "overlay": True},
    {"name": "Confirmed Pivot Low", "title": False, "data": pivot_low_points, "color": "#E2E8F0", "type": "scatter", "lineWidth": 3, "overlay": True},
]

signals = [{
    "type": "buy", "name": "New Major Pivot", "text": "PIVOT", "color": "#94A3B8", "renderMode": "hidden",
    "data": [float(close_values[index]) if pivot_high_confirmation[index] or pivot_low_confirmation[index] else None for index in range(n)],
}]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": [],
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "pivotHighConfirmation": pivot_high_confirmation.tolist(),
        "pivotLowConfirmation": pivot_low_confirmation.tolist(),
        "pivotHighAnchor": pivot_high_anchor.tolist(),
        "pivotLowAnchor": pivot_low_anchor.tolist(),
        "pivotAnchorAtConfirmation": pivot_anchor_at_confirmation.tolist(),
        "latestPivotIndex": int(latest_pivot_index),
        "windowStart": int(window_start), "windowLength": int(window_length),
        "windowHigh": float(window_high) if np.isfinite(window_high) else None,
        "windowLow": float(window_low) if np.isfinite(window_low) else None,
        "binStep": float(bin_step) if np.isfinite(bin_step) else None,
        "bottomBoundaries": finite(bottom_boundaries), "topBoundaries": finite(top_boundaries),
        "binVolume": finite(bin_volume), "profileRatio": finite(profile_ratio),
        "zoneMask": zone_mask.tolist(), "zones": zone_records,
    },
}
