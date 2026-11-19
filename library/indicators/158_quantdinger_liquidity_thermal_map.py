# ============================================================
# QuantDinger Liquidity Thermal Map
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Liquidity Thermal Map"
my_indicator_description = "Builds 31 rolling close-volume heat bands and a right-projected liquidity ladder with buy/sell balance statistics."

# @param profile_depth str Shallow Profile depth values=Shallow,Balanced,Deep
# @param thermal_sensitivity str Balanced Thermal sensitivity values=High Contrast,Balanced,Smooth
# @param render_lookback int 60 Historical heat-map bars range=20:500:10
# @param upper_liquidity_color color #BD2B2B Upper liquidity color
# @param lower_liquidity_color color #29B453 Lower liquidity color
# @param hide_active_candle_color bool true Hide heat around active candle

profile_depth = str(params.get("profile_depth", "Shallow"))
if profile_depth in ("Shallow", "Shallow Profile"): period = 60
elif profile_depth in ("Deep", "Deep Profile"): period = 600
else: period = 300
thermal_sensitivity = str(params.get("thermal_sensitivity", "Balanced"))
minimum_intensity = 0.5 if thermal_sensitivity == "High Contrast" else 0.0 if thermal_sensitivity == "Smooth" else 0.25
render_lookback = min(500, max(20, int(params.get("render_lookback", 60))))
upper_color = str(params.get("upper_liquidity_color", "#BD2B2B"))
lower_color = str(params.get("lower_liquidity_color", "#29B453"))
hide_active = bool(params.get("hide_active_candle_color", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].fillna(0.0).astype(float).to_numpy(dtype=float)
n = len(df)
times = df["time"].to_numpy(dtype=float) if "time" in df.columns else np.arange(n, dtype=float) * 60000.0
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0


def rgba(color, opacity):
    value = str(color).strip()
    if value.startswith("#") and len(value) == 7:
        red = int(value[1:3], 16); green = int(value[3:5], 16); blue = int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{max(0.0, min(1.0, float(opacity))):.3f})"
    return value


def interval_ms():
    if n < 2: return 60000.0
    differences = np.diff(times); differences = differences[np.isfinite(differences) & (differences > 0.0)]
    return float(np.median(differences)) if len(differences) else 60000.0


cloud_data = [[None for _ in range(n)] for _ in range(31)]
highest_series = np.full(n, np.nan, dtype=float)
lowest_series = np.full(n, np.nan, dtype=float)
step_series = np.full(n, np.nan, dtype=float)
latest = None
first_calculated = max(period - 1, n - 1500)
render_start = max(first_calculated, n - render_lookback)
for index in range(first_calculated, n):
    start = index - period + 1
    if start < 0:
        continue
    upper = float(np.max(high[start:index + 1])); lower = float(np.min(low[start:index + 1])); step = (upper - lower) / 31.0
    highest_series[index] = upper; lowest_series[index] = lower; step_series[index] = step
    if not np.isfinite(step) or step <= 0.0:
        continue
    levels = lower + step * np.arange(31, dtype=float)
    mids = levels + step * 0.5
    window_close = close[start:index + 1]; window_volume = volume[start:index + 1]
    bins = np.zeros(31, dtype=float)
    for bin_index in range(31):
        bins[bin_index] = float(np.sum(window_volume[np.abs(window_close - mids[bin_index]) < step]))
    maximum = float(np.max(bins))
    current_values = (open_price[index], close[index], (high[index] + low[index]) * 0.5, (high[index] + low[index] + close[index]) / 3.0)
    for bin_index in range(31):
        low_boundary = levels[bin_index]
        upper_boundary = levels[29] if bin_index + 1 > 29 else levels[bin_index + 1]
        base_color = upper_color if close[index] <= low_boundary else lower_color
        active = hide_active and any(low_boundary <= value <= upper_boundary for value in current_values)
        if active:
            color = "rgba(68,68,68,0.000)"
        elif bins[bin_index] == maximum:
            color = rgba(base_color, 0.80)
        else:
            ratio = bins[bin_index] / maximum if maximum > 0.0 else 0.0
            blend = min(1.0, max(0.0, (ratio - minimum_intensity) / max(1.0 - minimum_intensity, 1e-12)))
            color = rgba(base_color, 0.50 * blend)
        band_upper = upper if bin_index == 30 else levels[bin_index + 1]
        if index >= render_start:
            cloud_data[bin_index][index] = {"upper": float(band_upper), "lower": float(levels[bin_index]), "color": color}
    if index == n - 1:
        latest = {"upper": upper, "lower": lower, "step": step, "levels": levels, "bins": bins, "maximum": maximum}

layers = []
buy_volume = sell_volume = buy_percent = sell_percent = 0.0
imbalance = np.nan
split_index = 0
if latest is not None:
    upper = latest["upper"]; lower = latest["lower"]; step = latest["step"]; levels = latest["levels"]; bins = latest["bins"]; maximum = latest["maximum"]
    split_index = int(np.floor((close[-1] - lower) / step)); split_index = max(0, min(30, split_index)); maximum_distance = max(split_index, 30 - split_index); interval = interval_ms()
    box_start = float(times[-1] + 25 * interval); box_end = float(times[-1] + 35 * interval); maximum_start = float(times[-1] + 10 * interval)
    for bin_index in range(31):
        midpoint = lower + step * bin_index + step * 0.5; value = float(bins[bin_index])
        if midpoint < close[-1]: buy_volume += value
        else: sell_volume += value
        distance = abs(bin_index - split_index); fade = distance / maximum_distance if maximum_distance > 0 else 0.0; transparency = int(10.0 + fade * 85.0); base_color = lower_color if close[-1] > midpoint else upper_color
        is_maximum = value == maximum
        layers.append({
            "type": "zone", "role": "Thermal Ladder Bin", "start": maximum_start if is_maximum else box_start, "end": box_end,
            "top": float(lower + step * (bin_index + 1)), "bottom": float(lower + step * bin_index),
            "fillColor": rgba(base_color, 1.0 if is_maximum else 1.0 - transparency / 100.0), "borderColor": "rgba(0,0,0,0)", "borderSize": 0,
            "text": f">{maximum:.0f}<" if is_maximum else "", "textColor": "#FFFFFF",
        })
    total = buy_volume + sell_volume; buy_percent = buy_volume / total * 100.0 if total > 0.0 else 0.0; sell_percent = sell_volume / total * 100.0 if total > 0.0 else 0.0; imbalance = buy_volume - sell_volume if sell_volume > 0.0 else np.nan
    label_time = float(times[-1] + 40 * interval)
    layers.extend([
        {"type": "label", "time": label_time, "price": float(lower + (close[-1] - lower) * 0.5), "text": f"BUY LIQ\n{buy_volume:,.0f}\n{buy_percent:.1f}%", "color": "rgba(0,0,0,0)", "textColor": lower_color, "side": "left"},
        {"type": "label", "time": label_time, "price": float(close[-1] + (upper - close[-1]) * 0.5), "text": f"SELL LIQ\n{sell_volume:,.0f}\n{sell_percent:.1f}%", "color": "rgba(0,0,0,0)", "textColor": upper_color, "side": "left"},
        {"type": "label", "time": label_time, "price": float(upper + step), "text": f"Imbalance: {imbalance:,.0f}" if np.isfinite(imbalance) else "Imbalance: na", "color": "rgba(0,0,0,0)", "textColor": "#E5E7EB", "side": "left"},
    ])


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


plots = [{"name": f"Thermal Band {index + 1}", "title": False, "data": cloud_data[index], "color": lower_color, "type": "cloud", "opacity": 1.0, "overlay": True} for index in range(31)]
plots.append({"name": "Price Guide", "title": False, "data": finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True})

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": [], "layers": layers, "tables": [],
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "period": int(period), "firstCalculated": int(first_calculated), "highest": finite(highest_series), "lowest": finite(lowest_series), "step": finite(step_series),
        "latestLevels": latest["levels"].tolist() if latest is not None else [], "latestBins": latest["bins"].tolist() if latest is not None else [], "maximumVolume": latest["maximum"] if latest is not None else None,
        "splitIndex": int(split_index), "buyVolume": float(buy_volume), "sellVolume": float(sell_volume), "buyPercent": float(buy_percent), "sellPercent": float(sell_percent),
        "imbalance": float(imbalance) if np.isfinite(imbalance) else None, "barInterval": interval_ms(),
    },
}
