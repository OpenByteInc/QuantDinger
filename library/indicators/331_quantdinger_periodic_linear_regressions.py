# ============================================================
# QuantDinger Periodic Linear Regressions
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

import math

my_indicator_name = "QuantDinger Periodic Linear Regressions"
my_indicator_description = "Calculates rolling or static least-squares regressions around configurable calendar anchors with residual-deviation bands."

# @param method str Rolling Regression rendering method values=Static,Rolling
# @param anchor_type str Periodic Anchor reset behavior values=Periodic,First Bar
# @param anchor_period str 1W Anchor period values=4H,1D,1W,1M,3M,12M
# @param source str Close Regression source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param deviation_multiple float 1 Residual deviation multiple range=0.1:10:0.1
# @param show_bands bool true Show extremity bands
# @param bicolor bool true Color by correlation or slope
# @param gradient_fill bool true Show rolling gradient fill
# @param gradient_grade int 15 Gradient opacity range=0:50:1
# @param show_dashboard bool true Show anchor dashboard
# @param calendar_timezone_offset_minutes int 0 Calendar offset from UTC range=-840:840:15

method = str(params.get("method", "Rolling")).title()
if method not in ("Static", "Rolling"):
    method = "Rolling"
anchor_type = str(params.get("anchor_type", "Periodic")).title()
if anchor_type not in ("Periodic", "First Bar"):
    anchor_type = "Periodic"
anchor_period = str(params.get("anchor_period", "1W")).upper()
if anchor_period not in ("4H", "1D", "1W", "1M", "3M", "12M"):
    anchor_period = "1W"
source_name = str(params.get("source", "Close")).upper()
if source_name not in ("CLOSE", "OPEN", "HIGH", "LOW", "HL2", "HLC3", "OHLC4"):
    source_name = "CLOSE"
deviation_multiple = min(10.0, max(0.1, float(params.get("deviation_multiple", 1.0))))
show_bands = bool(params.get("show_bands", True))
bicolor = bool(params.get("bicolor", True))
gradient_fill = bool(params.get("gradient_fill", True))
gradient_grade = min(50, max(0, int(params.get("gradient_grade", 15))))
show_dashboard = bool(params.get("show_dashboard", True))
calendar_timezone_offset_minutes = min(840, max(-840, int(params.get("calendar_timezone_offset_minutes", 0))))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _gradient(value):
    if not np.isfinite(value):
        return "rgba(0,0,0,0)"
    ratio = min(1.0, max(0.0, (value + 1.0) / 2.0))
    red = int(round(242 + (8 - 242) * ratio))
    green = int(round(54 + (153 - 54) * ratio))
    blue = int(round(69 + (129 - 69) * ratio))
    return f"#{red:02X}{green:02X}{blue:02X}"


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
time_raw = df["time"].to_numpy(dtype=float)
time_seconds = time_raw / 1000.0 if len(time_raw) and np.nanmax(np.abs(time_raw)) > 1.0e11 else time_raw.copy()
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)
source = {
    "OPEN": opened, "HIGH": high, "LOW": low, "CLOSE": close,
    "HL2": (high + low) / 2.0, "HLC3": (high + low + close) / 3.0,
    "OHLC4": (opened + high + low + close) / 4.0,
}[source_name]

local_seconds = time_seconds + calendar_timezone_offset_minutes * 60.0
calendar = pd.to_datetime(local_seconds, unit="s", utc=True) if n else pd.DatetimeIndex([])
if anchor_period == "4H":
    period_key = np.floor(local_seconds / 14400.0).astype(np.int64) if n else np.asarray([], dtype=np.int64)
elif anchor_period == "1D":
    period_key = np.floor(local_seconds / 86400.0).astype(np.int64) if n else np.asarray([], dtype=np.int64)
elif anchor_period == "1W":
    period_key = np.floor((local_seconds + 3.0 * 86400.0) / 604800.0).astype(np.int64) if n else np.asarray([], dtype=np.int64)
else:
    month_index = calendar.year.to_numpy(dtype=np.int64) * 12 + calendar.month.to_numpy(dtype=np.int64) - 1 if n else np.asarray([], dtype=np.int64)
    period_key = month_index // (1 if anchor_period == "1M" else 3 if anchor_period == "3M" else 12)
period_change = np.asarray([index == 0 or period_key[index] != period_key[index - 1] for index in range(n)], dtype=bool)

regression = np.full(n, np.nan, dtype=float)
upper = np.full(n, np.nan, dtype=float)
lower = np.full(n, np.nan, dtype=float)
correlation = np.full(n, np.nan, dtype=float)
slope_series = np.full(n, np.nan, dtype=float)
intercept_series = np.full(n, np.nan, dtype=float)
distance_series = np.full(n, np.nan, dtype=float)
sample_count = np.zeros(n, dtype=int)
sum_x = sum_y = sum_x2 = sum_xy = 0.0
population = 0
x_values, y_values = [], []
static_lines = []
current_line = None

for index in range(n):
    sum_x += index
    sum_y += source[index]
    sum_x2 += index * index
    sum_xy += index * source[index]
    population += 1
    x_values.insert(0, float(index))
    y_values.insert(0, float(source[index]))

    if len(x_values) >= 2:
        x_array = np.asarray(x_values, dtype=float)
        y_array = np.asarray(y_values, dtype=float)
        x_variance = float(np.var(x_array))
        y_variance = float(np.var(y_array))
        covariance = float(np.mean((x_array - np.mean(x_array)) * (y_array - np.mean(y_array))))
        x_deviation = math.sqrt(x_variance)
        y_deviation = math.sqrt(y_variance)
        array_slope = covariance / x_variance if x_variance > 0.0 else np.nan
        r_value = covariance / (x_deviation * y_deviation) if x_deviation > 0.0 and y_deviation > 0.0 else np.nan
        residual_variance = max(0.0, y_variance * (1.0 - r_value * r_value)) if np.isfinite(r_value) else np.nan
        distance = math.sqrt(residual_variance) * deviation_multiple if np.isfinite(residual_variance) else np.nan
    else:
        array_slope = r_value = distance = np.nan

    denominator = population * sum_x2 - sum_x * sum_x
    slope = (population * sum_xy - sum_x * sum_y) / denominator if abs(denominator) > 1e-12 else np.nan
    intercept = (sum_y - slope * sum_x) / population if population > 0 and np.isfinite(slope) else np.nan
    fitted = slope * index + intercept if np.all(np.isfinite([slope, intercept])) else np.nan
    regression[index], slope_series[index], intercept_series[index], distance_series[index] = fitted, slope, intercept, distance
    upper[index] = fitted + distance if show_bands and np.all(np.isfinite([fitted, distance])) else np.nan
    lower[index] = fitted - distance if show_bands and np.all(np.isfinite([fitted, distance])) else np.nan
    correlation[index] = r_value
    sample_count[index] = population

    if method == "Static" and current_line is not None:
        previous_up = current_line["y1"] is not None and current_line["y2"] is not None and current_line["y2"] > current_line["y1"]
        current_line["color"] = "#089981" if bicolor and previous_up else "#F23645" if bicolor else "#FF5D00"
        current_line["endIndex"] = int(index)
        current_line["y2"] = float(fitted) if np.isfinite(fitted) else None
        start_value = slope * current_line["startIndex"] + intercept if np.all(np.isfinite([slope, intercept])) else np.nan
        current_line["y1"] = float(start_value) if np.isfinite(start_value) else None
        if show_bands:
            current_line["upper1"] = float(start_value + distance) if np.all(np.isfinite([start_value, distance])) else None
            current_line["upper2"] = float(fitted + distance) if np.all(np.isfinite([fitted, distance])) else None
            current_line["lower1"] = float(start_value - distance) if np.all(np.isfinite([start_value, distance])) else None
            current_line["lower2"] = float(fitted - distance) if np.all(np.isfinite([fitted, distance])) else None

    if period_change[index]:
        if anchor_type == "Periodic" or method == "Static":
            sum_x = sum_y = sum_x2 = sum_xy = 0.0
            x_values, y_values = [], []
            correlation[index] = np.nan
        population = 0
        if method == "Static":
            current_line = {
                "startIndex": int(index), "endIndex": int(index), "y1": float(source[index]), "y2": float(source[index]),
                "upper1": float(source[index] + distance) if show_bands and np.isfinite(distance) else None,
                "upper2": float(source[index] + distance) if show_bands and np.isfinite(distance) else None,
                "lower1": float(source[index] - distance) if show_bands and np.isfinite(distance) else None,
                "lower2": float(source[index] - distance) if show_bands and np.isfinite(distance) else None,
                "color": "#FF5D00",
            }
            static_lines.append(current_line)

rolling_visible = method == "Rolling"
rolling_colors = [_gradient(correlation[index]) if bicolor else "#FF5D00" for index in range(n)]
upper_fill, lower_fill = [], []
for index in range(n):
    if rolling_visible and show_bands and gradient_fill and np.all(np.isfinite([regression[index], upper[index], lower[index]])):
        color = _gradient(correlation[index])
        opacity = gradient_grade / 100.0
        upper_fill.append({"upper": float(upper[index]), "lower": float(regression[index]), "color": color, "opacity": opacity})
        lower_fill.append({"upper": float(regression[index]), "lower": float(lower[index]), "color": color, "opacity": opacity})
    else:
        upper_fill.append(None)
        lower_fill.append(None)

layers = []
if method == "Static":
    for line in static_lines[-166:]:
        if line["y1"] is None or line["y2"] is None:
            continue
        layers.append({"type": "line", "role": "Static Regression", "startIndex": line["startIndex"], "endIndex": line["endIndex"], "y1": line["y1"], "y2": line["y2"], "color": line["color"], "lineWidth": 1})
        if show_bands and line["upper1"] is not None and line["upper2"] is not None:
            layers.append({"type": "line", "role": "Static Upper", "startIndex": line["startIndex"], "endIndex": line["endIndex"], "y1": line["upper1"], "y2": line["upper2"], "color": line["color"], "lineWidth": 1})
            layers.append({"type": "line", "role": "Static Lower", "startIndex": line["startIndex"], "endIndex": line["endIndex"], "y1": line["lower1"], "y2": line["lower2"], "color": line["color"], "lineWidth": 1})

tables = []
if show_dashboard:
    tables = [{"title": "Periodic Regression", "position": "top-right", "compact": True, "rows": [[{"text": "HTF"}, {"text": anchor_period}]]}]

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Regression", "data": _finite(regression) if rolling_visible else [None] * n, "color": rolling_colors, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Upper Band", "data": _finite(upper) if rolling_visible and show_bands else [None] * n, "color": "rgba(0,0,0,0)" if bicolor else "#FF5D00", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower Band", "data": _finite(lower) if rolling_visible and show_bands else [None] * n, "color": "rgba(0,0,0,0)" if bicolor else "#FF5D00", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Upper Gradient", "title": False, "data": upper_fill, "color": "#089981", "type": "cloud", "overlay": True},
        {"name": "Lower Gradient", "title": False, "data": lower_fill, "color": "#F23645", "type": "cloud", "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [], "layers": layers, "tables": tables, "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "regression": _finite(regression), "upper": _finite(upper), "lower": _finite(lower), "correlation": _finite(correlation),
        "slope": _finite(slope_series), "intercept": _finite(intercept_series), "distance": _finite(distance_series),
        "sampleCount": sample_count.tolist(), "periodKey": period_key.tolist(), "periodChange": period_change.tolist(),
        "staticLines": static_lines, "method": method, "anchorType": anchor_type, "anchorPeriod": anchor_period, "source": source_name,
    },
}
