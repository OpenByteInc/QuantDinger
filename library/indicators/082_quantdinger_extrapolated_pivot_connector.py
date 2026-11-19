# ============================================================
# QuantDinger Extrapolated Pivot Connector
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Extrapolated Pivot Connector"
my_indicator_description = (
    "Connects selected confirmed swing highs and lows, then projects each "
    "pivot slope forward as a live support or resistance guide."
)

# @param length int 20 Pivot radius range=1:1000:1
# @param high_a_position int 1 A-high occurrence range=0:20:1
# @param high_b_position int 0 B-high occurrence range=0:20:1
# @param low_a_position int 1 A-low occurrence range=0:20:1
# @param low_b_position int 0 B-low occurrence range=0:20:1
# @param use_custom_source bool false Use custom source for both pivot directions
# @param custom_source str close Custom source values=open,high,low,close,hl2,hlc3,ohlc4

length = min(1000, max(1, int(params.get("length", 20))))
high_a_position = min(50, max(0, int(params.get("high_a_position", 1))))
high_b_position = min(50, max(0, int(params.get("high_b_position", 0))))
low_a_position = min(50, max(0, int(params.get("low_a_position", 1))))
low_b_position = min(50, max(0, int(params.get("low_b_position", 0))))
use_custom_source = bool(params.get("use_custom_source", params.get("use_close_source", False)))
custom_source_name = str(params.get("custom_source", "close")).lower()

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

sources = {
    "open": df["open"].astype(float).to_numpy(dtype=float),
    "high": high,
    "low": low,
    "close": close,
    "hl2": (high + low) * 0.5,
    "hlc3": (high + low + close) / 3.0,
    "ohlc4": (df["open"].astype(float).to_numpy(dtype=float) + high + low + close) * 0.25,
}
custom_source = sources.get(custom_source_name, close)
high_source = custom_source if use_custom_source else high
low_source = custom_source if use_custom_source else low
pivot_high = [None for _ in range(n)]
pivot_low = [None for _ in range(n)]
high_positions = []
high_values = []
low_positions = []
low_values = []

for confirmation_index in range(2 * length, n):
    center_index = confirmation_index - length
    window_start = center_index - length
    window_end = center_index + length + 1
    high_value = float(high_source[center_index])
    low_value = float(low_source[center_index])
    if high_value >= float(np.max(high_source[window_start:window_end])):
        pivot_high[center_index] = high_value
        high_positions.append(center_index)
        high_values.append(high_value)
    if low_value <= float(np.min(low_source[window_start:window_end])):
        pivot_low[center_index] = low_value
        low_positions.append(center_index)
        low_values.append(low_value)


def joined_pivot_path(positions, values):
    path = [None for _ in range(n)]
    if len(positions) == 1:
        path[positions[0]] = float(values[0])
        return path
    for segment_index in range(1, len(positions)):
        left_index = positions[segment_index - 1]
        right_index = positions[segment_index]
        left_value = float(values[segment_index - 1])
        right_value = float(values[segment_index])
        span = max(1, right_index - left_index)
        for plot_index in range(left_index, right_index + 1):
            ratio = (plot_index - left_index) / span
            path[plot_index] = float(left_value + ratio * (right_value - left_value))
    return path


def selected_pivot(positions, values, occurrence):
    selected_index = len(positions) - 1 - occurrence
    if selected_index < 0:
        return None
    return positions[selected_index], float(values[selected_index])


def projected_connector(first_point, second_point):
    connector = [None for _ in range(n)]
    if first_point is None or second_point is None:
        return connector
    first_index, first_value = first_point
    second_index, second_value = second_point
    delta_index = second_index - first_index
    slope = 0.0 if delta_index == 0 else (second_value - first_value) / delta_index
    start_index = min(first_index, second_index)
    for plot_index in range(start_index, n):
        connector[plot_index] = float(first_value + slope * (plot_index - first_index))
    return connector


high_a = selected_pivot(high_positions, high_values, high_a_position)
high_b = selected_pivot(high_positions, high_values, high_b_position)
low_a = selected_pivot(low_positions, low_values, low_a_position)
low_b = selected_pivot(low_positions, low_values, low_b_position)
upper_connector = projected_connector(high_a, high_b)
lower_connector = projected_connector(low_a, low_b)
high_path = joined_pivot_path(high_positions, high_values)
low_path = joined_pivot_path(low_positions, low_values)


def marker_data(point):
    values = [None for _ in range(n)]
    if point is not None:
        values[point[0]] = float(point[1])
    return values


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Upper Projection",
            "data": upper_connector,
            "color": "#2962FF",
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        },
        {
            "name": "Lower Projection",
            "data": lower_connector,
            "color": "#F59E0B",
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        },
        {
            "name": "Pivot High Path",
            "data": high_path,
            "color": "rgba(41, 98, 255, 0.45)",
            "type": "line",
            "lineWidth": 1,
            "overlay": True,
        },
        {
            "name": "Pivot Low Path",
            "data": low_path,
            "color": "rgba(245, 158, 11, 0.45)",
            "type": "line",
            "lineWidth": 1,
            "overlay": True,
        },
        {
            "name": "Calculation Anchor",
            "title": False,
            "data": [float(value) for value in close],
            "color": "rgba(0, 0, 0, 0)",
            "type": "line",
            "lineWidth": 1,
            "overlay": True,
        },
    ],
    "signals": [
        {"type": "sell", "text": "A-High", "name": "A High", "data": marker_data(high_a), "color": "#2962FF", "renderMode": "events"},
        {"type": "sell", "text": "B-High", "name": "B High", "data": marker_data(high_b), "color": "#2962FF", "renderMode": "events"},
        {"type": "buy", "text": "A-Low", "name": "A Low", "data": marker_data(low_a), "color": "#F59E0B", "renderMode": "events"},
        {"type": "buy", "text": "B-Low", "name": "B Low", "data": marker_data(low_b), "color": "#F59E0B", "renderMode": "events"},
    ],
    "layers": [],
    "tables": [],
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "length": int(length),
        "useCustomSource": bool(use_custom_source),
        "customSource": custom_source_name,
        "highAPosition": int(high_a_position),
        "highBPosition": int(high_b_position),
        "lowAPosition": int(low_a_position),
        "lowBPosition": int(low_b_position),
        "highSource": [float(value) for value in high_source],
        "lowSource": [float(value) for value in low_source],
        "pivotHigh": pivot_high,
        "pivotLow": pivot_low,
        "highPositions": high_positions,
        "highValues": high_values,
        "lowPositions": low_positions,
        "lowValues": low_values,
        "highA": list(high_a) if high_a is not None else None,
        "highB": list(high_b) if high_b is not None else None,
        "lowA": list(low_a) if low_a is not None else None,
        "lowB": list(low_b) if low_b is not None else None,
        "upperConnector": upper_connector,
        "lowerConnector": lower_connector,
        "highPath": high_path,
        "lowPath": low_path,
    },
}
