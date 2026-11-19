# ============================================================
# QuantDinger Horns Pattern
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Horns Pattern"
my_indicator_description = (
    "Finds bullish and inverted three-bar horn structures, projects their "
    "confirmation and measured-move levels, and marks confirmed breakouts."
)

# @param threshold float 0.05 Normalized horn threshold range=0:2:0.01
# @param bull_color color #0CB51A Bullish horn color
# @param bear_color color #FF1100 Bearish horn color
# @param show_patterns bool true Show horn connectors
# @param show_levels bool true Show confirmation and target levels
# @param show_signals bool true Show confirmed breakout markers
# @param pattern_width int 2 Connector thickness range=1:5:1

threshold = max(0.0, float(params.get("threshold", 0.05)))
bull_color = str(params.get("bull_color", "#0CB51A"))
bear_color = str(params.get("bear_color", "#FF1100"))
show_patterns = bool(params.get("show_patterns", True))
show_levels = bool(params.get("show_levels", True))
show_signals = bool(params.get("show_signals", True))
pattern_width = min(5, max(1, int(params.get("pattern_width", 2))))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

top_distance = np.full(n, np.nan, dtype=float)
bottom_distance = np.full(n, np.nan, dtype=float)
rolling_high = np.full(n, np.nan, dtype=float)
rolling_low = np.full(n, np.nan, dtype=float)

for index in range(2, n):
    window_high = float(max(high[index], high[index - 1], high[index - 2]))
    window_low = float(min(low[index], low[index - 1], low[index - 2]))
    span = window_high - window_low
    rolling_high[index] = window_high
    rolling_low[index] = window_low
    if np.isfinite(span) and span != 0.0:
        top_distance[index] = abs(high[index] - high[index - 2]) / span
        bottom_distance[index] = abs(low[index] - low[index - 2]) / span

events = []
bull_connector = [None] * n
bear_connector = [None] * n
buy_marks = [None] * n
sell_marks = [None] * n
level_history = np.zeros(n, dtype=float)
side_history = np.zeros(n, dtype=int)
armed_history = np.zeros(n, dtype=int)

active_level = 0.0
active_side = 0
armed = 0

for index in range(n):
    previous_level = active_level

    if index >= 3:
        inverted_horn = (
            np.isfinite(top_distance[index])
            and np.isfinite(top_distance[index - 1])
            and top_distance[index] < threshold
            and top_distance[index - 1] >= threshold
            and high[index - 1] < min(high[index], high[index - 2])
        )
        bullish_horn = (
            np.isfinite(bottom_distance[index])
            and np.isfinite(bottom_distance[index - 1])
            and bottom_distance[index] < threshold
            and bottom_distance[index - 1] >= threshold
            and low[index - 1] > max(low[index], low[index - 2])
        )

        if inverted_horn:
            active_side = -1
            active_level = float(rolling_low[index])
            target = active_level - float(rolling_high[index] - rolling_low[index])
            events.append({
                "index": index, "side": "bear", "level": active_level,
                "target": target, "high": float(rolling_high[index]),
                "low": float(rolling_low[index]),
            })
            if show_patterns:
                bear_connector[index - 2] = float(high[index - 2])
                bear_connector[index - 1] = float((high[index - 2] + high[index]) / 2.0)
                bear_connector[index] = float(high[index])

        if bullish_horn:
            active_side = 1
            active_level = float(rolling_high[index])
            target = active_level + float(rolling_high[index] - rolling_low[index])
            events.append({
                "index": index, "side": "bull", "level": active_level,
                "target": target, "high": float(rolling_high[index]),
                "low": float(rolling_low[index]),
            })
            if show_patterns:
                bull_connector[index - 2] = float(low[index - 2])
                bull_connector[index - 1] = float((low[index - 2] + low[index]) / 2.0)
                bull_connector[index] = float(low[index])

    buy = bool(
        index > 0 and active_side == 1 and armed == 1
        and close[index] > active_level and close[index - 1] <= previous_level
    )
    sell = bool(
        index > 0 and active_side == -1 and armed == 1
        and close[index] < active_level and close[index - 1] >= previous_level
    )
    if buy:
        buy_marks[index] = float(low[index])
    if sell:
        sell_marks[index] = float(high[index])

    if active_level != previous_level:
        armed = 1
    elif buy or sell:
        armed = 0

    level_history[index] = active_level
    side_history[index] = active_side
    armed_history[index] = armed

bull_level = [None] * n
bull_target = [None] * n
bear_level = [None] * n
bear_target = [None] * n
if show_levels:
    for event_index, event in enumerate(events):
        start = int(event["index"])
        end = n - 1
        if event_index + 1 < len(events):
            end = int(events[event_index + 1]["index"]) - 2
        if end < start:
            continue
        level_output = bull_level if event["side"] == "bull" else bear_level
        target_output = bull_target if event["side"] == "bull" else bear_target
        for output_index in range(start, end + 1):
            level_output[output_index] = float(event["level"])
            target_output[output_index] = float(event["target"])

def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]

audit_events = [
    {
        "index": int(event["index"]), "side": event["side"],
        "level": float(event["level"]), "target": float(event["target"]),
        "high": float(event["high"]), "low": float(event["low"]),
    }
    for event in events
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Bullish Horn", "title": False, "data": bull_connector, "color": bull_color, "type": "line", "lineWidth": pattern_width, "overlay": True, "connectNulls": False},
        {"name": "Inverted Horn", "title": False, "data": bear_connector, "color": bear_color, "type": "line", "lineWidth": pattern_width, "overlay": True, "connectNulls": False},
        {"name": "Bull Confirmation", "data": bull_level, "color": bull_color, "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": True, "connectNulls": False},
        {"name": "Bull Target", "data": bull_target, "color": bull_color, "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True, "connectNulls": False},
        {"name": "Bear Confirmation", "data": bear_level, "color": bear_color, "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": True, "connectNulls": False},
        {"name": "Bear Target", "data": bear_target, "color": bear_color, "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True, "connectNulls": False},
        {"name": "Price Guide", "title": False, "data": _finite_or_none(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": ([
        {"type": "buy", "text": "B", "name": "Bull Horn Breakout", "data": buy_marks, "color": bull_color, "renderMode": "events"},
        {"type": "sell", "text": "S", "name": "Bear Horn Breakout", "data": sell_marks, "color": bear_color, "renderMode": "events"},
    ] if show_signals else []),
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "topDistance": _finite_or_none(top_distance),
        "bottomDistance": _finite_or_none(bottom_distance),
        "rollingHigh": _finite_or_none(rolling_high),
        "rollingLow": _finite_or_none(rolling_low),
        "level": _finite_or_none(level_history),
        "side": [int(value) for value in side_history],
        "armed": [int(value) for value in armed_history],
        "events": audit_events,
        "buyBars": [index for index, value in enumerate(buy_marks) if value is not None],
        "sellBars": [index for index, value in enumerate(sell_marks) if value is not None],
    },
}
