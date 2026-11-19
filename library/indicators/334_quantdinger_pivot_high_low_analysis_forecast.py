# ============================================================
# QuantDinger Pivot High/Low Analysis and Forecast
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Pivot High/Low Analysis and Forecast"
my_indicator_description = "Compares consecutive confirmed pivots, summarizes their average spacing and percentage change, and projects the next expected pivot windows."

# @param pivot_length int 50 Symmetric pivot length range=1:250:1
# @param show_forecast bool true Show forecasted high pivots
# @param dashboard_location str Bottom Dashboard location values=Top Right,Bottom Right,Top Left,Bottom Left
# @param dashboard_size str Normal Dashboard size values=Tiny,Small,Normal,Large

pivot_length = min(250, max(1, int(params.get("pivot_length", 50))))
show_forecast = bool(params.get("show_forecast", True))
dashboard_location = str(params.get("dashboard_location", "Bottom")).title()
if dashboard_location == "Bottom":
    dashboard_location = "Bottom Right"
if dashboard_location not in ("Top Right", "Bottom Right", "Top Left", "Bottom Left"):
    dashboard_location = "Bottom Right"
dashboard_size = str(params.get("dashboard_size", "Normal")).title()
if dashboard_size not in ("Tiny", "Small", "Normal", "Large"):
    dashboard_size = "Normal"


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

pivot_high_confirmation = np.zeros(n, dtype=bool)
pivot_low_confirmation = np.zeros(n, dtype=bool)
pivot_high_plot = np.full(n, np.nan, dtype=float)
pivot_low_plot = np.full(n, np.nan, dtype=float)
high_interval = np.zeros(n, dtype=int)
low_interval = np.zeros(n, dtype=int)
high_change = np.zeros(n, dtype=float)
low_change = np.zeros(n, dtype=float)
average_high_interval = np.full(n, np.nan, dtype=float)
average_low_interval = np.full(n, np.nan, dtype=float)
average_high_change = np.full(n, np.nan, dtype=float)
average_low_change = np.full(n, np.nan, dtype=float)
high_forecast_index = np.full(n, np.nan, dtype=float)
low_forecast_index = np.full(n, np.nan, dtype=float)

high_pivots = []
low_pivots = []
comparison_layers = []
high_forecast_layers = []
low_forecast_layers = []
sum_high_interval = 0.0
sum_low_interval = 0.0
count_high_interval = 0
count_low_interval = 0
sum_high_change = 0.0
sum_low_change = 0.0
count_high_change = 0
count_low_change = 0

for index in range(n):
    center = index - pivot_length
    if index >= pivot_length * 2:
        high_window = high[center - pivot_length:center + pivot_length + 1]
        low_window = low[center - pivot_length:center + pivot_length + 1]
        pivot_high_confirmation[index] = high[center] == np.max(high_window)
        pivot_low_confirmation[index] = low[center] == np.min(low_window)

    if pivot_high_confirmation[index]:
        price = float(high[center])
        pivot_high_plot[center] = price
        if high_pivots:
            previous_index, previous_price = high_pivots[-1]
            interval = int(center - previous_index)
            change = (price - previous_price) / previous_price * 100.0 if previous_price != 0.0 else np.nan
            high_interval[index] = interval
            high_change[index] = change if np.isfinite(change) else 0.0
            sum_high_interval += interval
            count_high_interval += 1 if interval != 0 else 0
            if np.isfinite(change):
                sum_high_change += change
                count_high_change += 1 if change != 0.0 else 0
            color = "#2157F3" if price > previous_price else "#FF1100"
            comparison_layers.extend([
                {"type": "line", "role": "Previous Pivot High", "startIndex": int(previous_index), "endIndex": int(center), "y1": previous_price, "y2": previous_price, "color": "#FF1100", "lineWidth": 1, "dotted": True},
                {"type": "line", "role": "Pivot High Change", "startIndex": int(center), "endIndex": int(center), "y1": price, "y2": previous_price, "color": color, "lineWidth": 1, "arrowStart": True, "arrowEnd": True},
                {"type": "label", "role": "Pivot High Statistics", "index": int(center), "price": max(price, previous_price), "text": f"Δ% : {change:.2f}\nΔt : {interval}", "color": "rgba(0,0,0,0)", "textColor": "#808080", "direction": "down", "size": "small"},
            ])
        high_pivots.append((int(center), price))

    if pivot_low_confirmation[index]:
        price = float(low[center])
        pivot_low_plot[center] = price
        if low_pivots:
            previous_index, previous_price = low_pivots[-1]
            interval = int(center - previous_index)
            change = (price - previous_price) / previous_price * 100.0 if previous_price != 0.0 else np.nan
            low_interval[index] = interval
            low_change[index] = change if np.isfinite(change) else 0.0
            sum_low_interval += interval
            count_low_interval += 1 if interval != 0 else 0
            if np.isfinite(change):
                sum_low_change += change
                count_low_change += 1 if change != 0.0 else 0
            color = "#2157F3" if price > previous_price else "#FF1100"
            comparison_layers.extend([
                {"type": "line", "role": "Previous Pivot Low", "startIndex": int(previous_index), "endIndex": int(center), "y1": previous_price, "y2": previous_price, "color": "#0CB51A", "lineWidth": 1, "dotted": True},
                {"type": "line", "role": "Pivot Low Change", "startIndex": int(center), "endIndex": int(center), "y1": price, "y2": previous_price, "color": color, "lineWidth": 1, "arrowStart": True, "arrowEnd": True},
                {"type": "label", "role": "Pivot Low Statistics", "index": int(center), "price": min(price, previous_price), "text": f"Δ% : {change:.2f}\nΔt : {interval}", "color": "rgba(0,0,0,0)", "textColor": "#808080", "direction": "up", "size": "small"},
            ])
        low_pivots.append((int(center), price))

    current_high_interval = sum_high_interval / count_high_interval if count_high_interval else np.nan
    current_low_interval = sum_low_interval / count_low_interval if count_low_interval else np.nan
    current_high_change = sum_high_change / count_high_change if count_high_change else np.nan
    current_low_change = sum_low_change / count_low_change if count_low_change else np.nan
    average_high_interval[index] = current_high_interval
    average_low_interval[index] = current_low_interval
    average_high_change[index] = current_high_change
    average_low_change[index] = current_low_change

    if show_forecast and pivot_high_confirmation[index] and np.isfinite(current_high_interval):
        forecast = int(index + min(int(current_high_interval - pivot_length), 500))
        high_forecast_index[index] = forecast
        high_forecast_layers = [
            {"type": "line", "role": "Forecast Pivot High Time", "startIndex": forecast, "endIndex": forecast, "y1": float(high[index]), "y2": float(low[index]), "color": "#FF1100", "lineWidth": 1, "dashed": True, "extendVertical": True},
            {"type": "line", "role": "Latest Pivot High", "startIndex": int(center), "endIndex": max(n - 1, forecast), "y1": float(high[center]), "y2": float(high[center]), "color": "#FF1100", "lineWidth": 1, "dotted": True},
        ]

    # The source keeps the low forecast outside its display toggle; preserve that behavior.
    if pivot_low_confirmation[index] and np.isfinite(current_low_interval):
        forecast = int(index + min(int(current_low_interval - pivot_length), 500))
        low_forecast_index[index] = forecast
        low_forecast_layers = [
            {"type": "line", "role": "Forecast Pivot Low Time", "startIndex": forecast, "endIndex": forecast, "y1": float(high[index]), "y2": float(low[index]), "color": "#0CB51A", "lineWidth": 1, "dashed": True, "extendVertical": True},
            {"type": "line", "role": "Latest Pivot Low", "startIndex": int(center), "endIndex": max(n - 1, forecast), "y1": float(low[center]), "y2": float(low[center]), "color": "#0CB51A", "lineWidth": 1, "dotted": True},
        ]

layers = comparison_layers + high_forecast_layers + low_forecast_layers
position = dashboard_location.lower().replace(" ", "-")
latest_high_interval = average_high_interval[-1] if n else np.nan
latest_low_interval = average_low_interval[-1] if n else np.nan
latest_high_change = average_high_change[-1] if n else np.nan
latest_low_change = average_low_change[-1] if n else np.nan


def _table_value(value):
    return f"{value:.2f}" if np.isfinite(value) else "—"


tables = [{
    "title": "Pivot Analysis", "position": position, "compact": dashboard_size in ("Tiny", "Small"),
    "rows": [
        [{"text": ""}, {"text": "Pivot High"}, {"text": "Pivot Low"}],
        [{"text": "E[Δt]"}, {"text": _table_value(latest_high_interval)}, {"text": _table_value(latest_low_interval)}],
        [{"text": "E[Δ%]"}, {"text": _table_value(latest_high_change)}, {"text": _table_value(latest_low_change)}],
    ],
}]

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Pivot High", "data": _finite(pivot_high_plot), "color": "#FF1100", "type": "scatter", "size": 4, "overlay": True},
        {"name": "Pivot Low", "data": _finite(pivot_low_plot), "color": "#0CB51A", "type": "scatter", "size": 4, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [], "layers": layers, "tables": tables,
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "pivotHighConfirmation": pivot_high_confirmation.tolist(), "pivotLowConfirmation": pivot_low_confirmation.tolist(),
        "pivotHigh": _finite(pivot_high_plot), "pivotLow": _finite(pivot_low_plot),
        "highInterval": high_interval.tolist(), "lowInterval": low_interval.tolist(),
        "highChange": _finite(high_change), "lowChange": _finite(low_change),
        "averageHighInterval": _finite(average_high_interval), "averageLowInterval": _finite(average_low_interval),
        "averageHighChange": _finite(average_high_change), "averageLowChange": _finite(average_low_change),
        "highForecastIndex": _finite(high_forecast_index), "lowForecastIndex": _finite(low_forecast_index),
        "highPivots": [{"index": item[0], "price": item[1]} for item in high_pivots],
        "lowPivots": [{"index": item[0], "price": item[1]} for item in low_pivots],
    },
}
