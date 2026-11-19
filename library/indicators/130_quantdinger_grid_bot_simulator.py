# ============================================================
# QuantDinger Grid Bot Simulator
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only simulator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Grid Bot Simulator"
my_indicator_description = (
    "Builds an automatically scaled or manually fixed price grid, tracks the last crossed level, highlights the "
    "adjacent trade zone, and emits direction-filtered grid crossing signals."
)

# @param auto_range bool true Fit the grid to the loaded price range
# @param auto_range_lookback int 120 Bars used to fit the automatic grid range range=20:1000:10
# @param upper_limit float 50000.0 Upper grid limit range=0.000001:1000000000.0:0.1
# @param lower_limit float 43000.0 Lower grid limit range=0.0:999999999.0:0.1
# @param grid_count int 10 Number of grid intervals range=2:50:1
# @param market_direction str neutral Direction filter values=up,neutral,down
# @param signal_size str small Signal marker size values=small,large
# @param show_grids bool true Show grid levels
# @param highlight_zones bool true Highlight the active trade zone boundaries
# @param show_current_only bool false Show only adjacent current levels
# @param use_extremes bool true Use high and low for crossings

auto_range = bool(params.get("auto_range", True))
auto_range_lookback = min(1000, max(20, int(params.get("auto_range_lookback", 120))))
upper_limit = float(params.get("upper_limit", 50000.0))
lower_limit = float(params.get("lower_limit", 43000.0))
grid_count = min(50, max(2, int(params.get("grid_count", 10))))
market_direction = str(params.get("market_direction", "neutral")).lower()
signal_size = str(params.get("signal_size", "small")).lower()
show_grids = bool(params.get("show_grids", True))
highlight_zones = bool(params.get("highlight_zones", True))
show_current_only = bool(params.get("show_current_only", False))
use_extremes = bool(params.get("use_extremes", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

if auto_range and n > 0:
    range_start = max(0, n - auto_range_lookback)
    data_low = float(np.min(low[range_start:]))
    data_high = float(np.max(high[range_start:]))
    data_span = max(data_high - data_low, abs(float(close[-1])) * 0.001)
    padding = data_span * 0.02
    lower_limit = max(0.0, data_low - padding)
    upper_limit = data_high + padding

if not np.isfinite(upper_limit) or not np.isfinite(lower_limit):
    raise ValueError("Grid limits must be finite")
if upper_limit <= lower_limit:
    raise ValueError("Upper grid limit must be greater than lower grid limit")

grid_interval = (upper_limit - lower_limit) / float(grid_count)
grid_levels = np.asarray(
    [lower_limit + grid_interval * index for index in range(grid_count + 1)],
    dtype=float,
)

buy = np.zeros(n, dtype=bool)
sell = np.zeros(n, dtype=bool)
last_signal = np.full(n, np.nan, dtype=float)
last_signal_index = np.full(n, np.nan, dtype=float)
signal_line = np.full(n, np.nan, dtype=float)
buy_line_index = np.zeros(n, dtype=int)
sell_line_index = np.zeros(n, dtype=int)

direction = 1 if market_direction == "up" else -1 if market_direction == "down" else 0

for index in range(1, n):
    buy_index = 0
    sell_index = 0
    for level_index in range(grid_count + 1):
        level = grid_levels[level_index]
        if use_extremes:
            if high[index - 1] > level and low[index] <= level:
                buy_index = level_index
            if low[index - 1] < level and high[index] >= level:
                sell_index = level_index
        else:
            if close[index - 1] > level and close[index] <= level:
                buy_index = level_index
            if close[index - 1] < level and close[index] >= level:
                sell_index = level_index

    buy_event = buy_index > 0
    sell_event = sell_index > 0
    buy_line_index[index] = buy_index
    sell_line_index[index] = sell_index
    previous_signal_line = signal_line[index - 1]

    if np.isfinite(previous_signal_line):
        if use_extremes:
            if low[index] >= previous_signal_line - grid_interval:
                buy_event = False
            if high[index] <= previous_signal_line + grid_interval:
                sell_event = False
        else:
            if close[index] >= previous_signal_line - grid_interval:
                buy_event = False
            if close[index] <= previous_signal_line + grid_interval:
                sell_event = False

    if close[index] >= upper_limit or close[index] < lower_limit:
        buy_event = False
    if close[index] <= lower_limit or close[index] > upper_limit:
        sell_event = False

    if np.isfinite(previous_signal_line):
        if direction == -1 and low[index] >= previous_signal_line - 2.0 * grid_interval:
            buy_event = False
        if direction == 1 and high[index] <= previous_signal_line + 2.0 * grid_interval:
            sell_event = False

    buy[index] = buy_event
    sell[index] = sell_event
    last_signal[index] = 1.0 if buy_event else -1.0 if sell_event else last_signal[index - 1]
    last_signal_index[index] = (
        buy_index if buy_event else sell_index if sell_event else last_signal_index[index - 1]
    )
    if np.isfinite(last_signal_index[index]):
        signal_line[index] = lower_limit + grid_interval * float(last_signal_index[index])

plots = []

if show_grids and not show_current_only:
    for level_index in range(1, grid_count + 1):
        level = float(grid_levels[level_index])
        level_data = []
        for index in range(n):
            if last_signal_index[index] == level_index:
                level_data.append(None)
            else:
                level_data.append(
                    {
                        "value": level,
                        "color": "rgba(255,0,0,0.50)" if level > close[index] else "rgba(0,128,0,0.60)",
                    }
                )
        plots.append(
            {
                "name": f"Grid {level_index}",
                "title": False,
                "data": level_data,
                "color": "#64748B",
                "type": "line",
                "lineWidth": 1,
                "overlay": True,
            }
        )

if show_grids and show_current_only:
    above_data = []
    below_data = []
    for index in range(n):
        if np.isfinite(signal_line[index]):
            above = signal_line[index] if signal_line[index] == upper_limit else signal_line[index] + grid_interval
            below = signal_line[index] if signal_line[index] == lower_limit else signal_line[index] - grid_interval
            above_data.append(float(above))
            below_data.append(float(below))
        else:
            above_data.append(None)
            below_data.append(None)
    plots.extend(
        [
            {"name": "Current Grid Above", "title": False, "data": above_data, "color": "#FF0000", "type": "line", "lineWidth": 1, "overlay": True},
            {"name": "Current Grid Below", "title": False, "data": below_data, "color": "#008000", "type": "line", "lineWidth": 1, "overlay": True},
        ]
    )

if show_grids and not show_current_only:
    upper_data = [
        {"value": upper_limit, "color": "#008000" if close[index] >= upper_limit else "#FF0000"}
        for index in range(n)
    ]
    lower_data = [
        {"value": lower_limit, "color": "#FF0000" if close[index] <= lower_limit else "#008000"}
        for index in range(n)
    ]
    plots.extend(
        [
            {"name": "Upper Limit", "title": False, "data": upper_data, "color": "#FF0000", "type": "line", "lineWidth": 3, "overlay": True},
            {"name": "Lower Limit", "title": False, "data": lower_data, "color": "#008000", "type": "line", "lineWidth": 3, "overlay": True},
        ]
    )

active_line_data = []
active_zone_data = []
for index in range(n):
    if np.isfinite(signal_line[index]):
        active_color = "rgba(0,128,0,0.05)" if last_signal[index] == 1 else "rgba(255,0,0,0.05)"
        active_line_data.append({"value": float(signal_line[index]), "color": active_color})
        if highlight_zones:
            upper = signal_line[index] if signal_line[index] == upper_limit else signal_line[index] + grid_interval
            lower = signal_line[index] if signal_line[index] == lower_limit else signal_line[index] - grid_interval
            zone_color = "rgba(0,128,0,0.10)" if last_signal[index] == -1 else "rgba(255,0,0,0.10)"
            active_zone_data.append({"upper": float(max(upper, lower)), "lower": float(min(upper, lower)), "color": zone_color})
        else:
            active_zone_data.append(None)
    else:
        active_line_data.append(None)
        active_zone_data.append(None)

plots.extend(
    [
        {"name": "Active Grid", "title": False, "data": active_line_data, "color": "#94A3B8", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Active Zone", "title": False, "data": active_zone_data, "type": "cloud", "overlay": True},
    ]
)
plots.append({
    "name": "Price Reference", "title": False, "data": [float(value) for value in close],
    "color": "rgba(255,255,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True,
})

buy_data = [None for _ in range(n)]
sell_data = [None for _ in range(n)]
marker_text = "▲"
sell_text = "▼"
for index in range(n):
    if buy[index]:
        buy_data[index] = {"value": float(low[index]), "text": marker_text, "color": "#32CD32"}
    if sell[index]:
        sell_data[index] = {"value": float(high[index]), "text": sell_text, "color": "#DC143C"}

signals = [
    {"type": "buy", "text": marker_text, "name": "Grid Buy", "data": buy_data, "color": "#32CD32", "renderMode": "hidden"},
    {"type": "sell", "text": sell_text, "name": "Grid Sell", "data": sell_data, "color": "#DC143C", "renderMode": "hidden"},
]

plots.extend([
    {
        "name": "Grid Buy Marks", "title": False,
        "data": [float(low[index]) if buy[index] else None for index in range(n)],
        "color": "#32CD32", "type": "scatter", "size": 6 if signal_size == "large" else 4, "overlay": True,
    },
    {
        "name": "Grid Sell Marks", "title": False,
        "data": [float(high[index]) if sell[index] else None for index in range(n)],
        "color": "#DC143C", "type": "scatter", "size": 6 if signal_size == "large" else 4, "overlay": True,
    },
])

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [plot for plot in plots if any(item is not None for item in plot.get("data", []))],
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "gridInterval": float(grid_interval), "gridLevels": grid_levels.tolist(),
        "buyLineIndex": buy_line_index.tolist(), "sellLineIndex": sell_line_index.tolist(),
        "buy": buy.tolist(), "sell": sell.tolist(),
        "lastSignal": [int(value) if np.isfinite(value) else None for value in last_signal],
        "lastSignalIndex": [int(value) if np.isfinite(value) else None for value in last_signal_index],
        "signalLine": [float(value) if np.isfinite(value) else None for value in signal_line],
    },
}
