# ============================================================
# QuantDinger AutoTrail
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only risk tool; stop events do not place orders.
# ============================================================

my_indicator_name = "QuantDinger AutoTrail"
my_indicator_description = (
    "Starts a one-direction trade trail from a configurable historical bar and "
    "ratchets the stop using fixed-price, ATR, percentage, or Parabolic SAR logic."
)

# @param start_bars_back int 120 Trail start bars back range=2:1000:1
# @param initial_stop float 0.0 Initial stop price (0 = automatic)
# @param direction str Auto Trade direction values=Auto,Long,Short
# @param trail_type str Price Trail type values=ATR,Percent,Price,SAR
# @param stop_source str High/Low Stop source values=High/Low,Close,Open
# @param atr_length int 14 ATR length range=1:200:1
# @param atr_multiplier float 1.0 ATR multiplier range=0.1:20.0:0.1
# @param percent_trail float 2.0 Percent trail range=0.1:50.0:0.1
# @param lookback int 5 Swing lookback range=1:200:1
# @param psar_start float 0.043 PSAR start range=0.001:1.0:0.001
# @param psar_step float 0.043 PSAR step range=0.001:1.0:0.001
# @param psar_max float 0.34 PSAR maximum range=0.01:2.0:0.01
# @param long_color str #64B5F6 Long stop color
# @param short_color str #EF5350 Short stop color
# @param line_width int 2 Stop line width range=1:6:1

start_bars_back = min(1000, max(2, int(params.get("start_bars_back", 120))))
initial_stop_input = max(0.0, float(params.get("initial_stop", 0.0)))
direction_name = str(params.get("direction", "Auto")).strip().title()
if direction_name not in ("Auto", "Long", "Short"):
    direction_name = "Auto"
trail_type = str(params.get("trail_type", "Price")).strip().upper()
if trail_type not in ("ATR", "PERCENT", "PRICE", "SAR"):
    trail_type = "PRICE"
stop_source = str(params.get("stop_source", "High/Low")).strip().upper()
if stop_source not in ("HIGH/LOW", "CLOSE", "OPEN"):
    stop_source = "HIGH/LOW"
atr_length = min(200, max(1, int(params.get("atr_length", 14))))
atr_multiplier = min(20.0, max(0.1, float(params.get("atr_multiplier", 1.0))))
percent_trail = min(50.0, max(0.1, float(params.get("percent_trail", 2.0))))
lookback = min(200, max(1, int(params.get("lookback", 5))))
psar_start = min(1.0, max(0.001, float(params.get("psar_start", 0.043))))
psar_step = min(1.0, max(0.001, float(params.get("psar_step", 0.043))))
psar_max = min(2.0, max(0.01, float(params.get("psar_max", 0.34))))
if psar_max < psar_start:
    psar_max = psar_start
long_color = str(params.get("long_color", "#64B5F6"))
short_color = str(params.get("short_color", "#EF5350"))
line_width = min(6, max(1, int(params.get("line_width", 2))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def wilder_atr(high_values, low_values, close_values, length):
    count = len(close_values)
    true_range = np.full(count, np.nan, dtype=float)
    result = np.full(count, np.nan, dtype=float)
    for index in range(count):
        if index == 0:
            true_range[index] = high_values[index] - low_values[index]
        else:
            true_range[index] = max(
                high_values[index] - low_values[index],
                abs(high_values[index] - close_values[index - 1]),
                abs(low_values[index] - close_values[index - 1]),
            )
    if count >= length:
        result[length - 1] = (
            sum(float(value) for value in true_range[:length]) / length
        )
        for index in range(length, count):
            result[index] = (
                result[index - 1] * (length - 1) + true_range[index]
            ) / length
    return result


def parabolic_sar(high_values, low_values, close_values, start_value, step_value, maximum_value):
    count = len(close_values)
    result = np.full(count, np.nan, dtype=float)
    bullish = np.zeros(count, dtype=bool)
    if count < 2:
        return result, bullish

    is_below = bool(close_values[1] > close_values[0])
    extreme = float(high_values[1] if is_below else low_values[1])
    sar_value = float(low_values[0] if is_below else high_values[0])
    acceleration = float(start_value)
    bullish[0] = is_below

    for index in range(1, count):
        first_trend_bar = False
        sar_value = sar_value + acceleration * (extreme - sar_value)
        raw_sar = sar_value

        if is_below:
            if sar_value > low_values[index]:
                first_trend_bar = True
                is_below = False
                sar_value = max(float(high_values[index]), extreme)
                extreme = float(low_values[index])
                acceleration = float(start_value)
        else:
            if sar_value < high_values[index]:
                first_trend_bar = True
                is_below = True
                sar_value = min(float(low_values[index]), extreme)
                extreme = float(high_values[index])
                acceleration = float(start_value)

        if not first_trend_bar:
            if is_below and high_values[index] > extreme:
                extreme = float(high_values[index])
                acceleration = min(acceleration + step_value, maximum_value)
            elif (not is_below) and low_values[index] < extreme:
                extreme = float(low_values[index])
                acceleration = min(acceleration + step_value, maximum_value)

        if is_below:
            sar_value = min(sar_value, float(low_values[index - 1]))
            if index > 1:
                sar_value = min(sar_value, float(low_values[index - 2]))
        else:
            sar_value = max(sar_value, float(high_values[index - 1]))
            if index > 1:
                sar_value = max(sar_value, float(high_values[index - 2]))

        result[index] = float(raw_sar if first_trend_bar else sar_value)
        bullish[index] = is_below

    return result, bullish


atr = wilder_atr(high, low, close, atr_length)
psar, psar_bullish = parabolic_sar(
    high, low, close, psar_start, psar_step, psar_max
)

trail = np.full(n, np.nan, dtype=float)
trail_direction = np.zeros(n, dtype=int)
entry_marker = np.full(n, np.nan, dtype=float)
long_stop_events = np.full(n, np.nan, dtype=float)
short_stop_events = np.full(n, np.nan, dtype=float)
start_index = max(1, n - start_bars_back) if n else 0
effective_direction = "Long"
effective_initial_stop = None
stop_index = None
active = False

if n >= 2:
    if direction_name == "Long":
        is_long = True
    elif direction_name == "Short":
        is_long = False
    elif initial_stop_input > 0.0:
        is_long = bool(initial_stop_input < close[start_index])
    elif trail_type == "SAR" and np.isfinite(psar[start_index]):
        is_long = bool(psar_bullish[start_index])
    else:
        baseline_index = max(0, start_index - lookback)
        is_long = bool(close[start_index] >= close[baseline_index])

    segment_start = start_index
    segment_number = 0
    active = True
    while segment_start < n:
        effective_direction = "Long" if is_long else "Short"
        base_atr = (
            float(atr[segment_start])
            if np.isfinite(atr[segment_start]) and atr[segment_start] > 0.0
            else max(
                float(high[segment_start] - low[segment_start]),
                abs(float(close[segment_start])) * 0.005,
                1e-9,
            )
        )
        safety_gap = max(base_atr * atr_multiplier, abs(float(close[segment_start])) * 1e-8)
        use_input_stop = segment_number == 0 and initial_stop_input > 0.0
        if use_input_stop:
            initial_stop = (
                min(initial_stop_input, float(low[segment_start]) - abs(float(close[segment_start])) * 1e-8)
                if is_long
                else max(initial_stop_input, float(high[segment_start]) + abs(float(close[segment_start])) * 1e-8)
            )
        else:
            initial_stop = (
                float(low[segment_start]) - safety_gap
                if is_long
                else float(high[segment_start]) + safety_gap
            )

        effective_initial_stop = float(initial_stop)
        entry_marker[segment_start] = float(initial_stop)
        if stop_source == "CLOSE":
            initial_source = float(close[segment_start])
        elif stop_source == "OPEN":
            initial_source = float(open_price[segment_start])
        else:
            initial_source = float(low[segment_start] if is_long else high[segment_start])
        fixed_offset = abs(initial_source - initial_stop)
        trail_price = 0.0
        next_segment = None

        for index in range(segment_start, n):
            left = max(segment_start, index - lookback + 1)
            if stop_source == "CLOSE":
                source_window = close[left:index + 1]
            elif stop_source == "OPEN":
                source_window = open_price[left:index + 1]
            else:
                source_window = low[left:index + 1] if is_long else high[left:index + 1]

            if trail_type == "PERCENT":
                offset = abs(float(close[index])) * percent_trail / 100.0
            elif trail_type == "ATR":
                offset = float(atr[index]) * atr_multiplier if np.isfinite(atr[index]) else safety_gap
            else:
                offset = fixed_offset

            swing_low = float(np.min(source_window)) - offset
            swing_high = float(np.max(source_window)) + offset
            if index == segment_start:
                candidate = initial_stop
            elif trail_type == "SAR" and np.isfinite(psar[index]) and (
                (is_long and psar_bullish[index]) or ((not is_long) and (not psar_bullish[index]))
            ):
                candidate = float(psar[index])
            else:
                candidate = swing_low if is_long else swing_high

            trail_price = max(trail_price, candidate) if is_long and trail_price != 0.0 else min(trail_price, candidate) if (not is_long) and trail_price != 0.0 else candidate
            trail[index] = float(trail_price)
            trail_direction[index] = 1 if is_long else -1

            sar_reversal = trail_type == "SAR" and index > segment_start and (
                (is_long and psar_bullish[index - 1] and (not psar_bullish[index]))
                or ((not is_long) and (not psar_bullish[index - 1]) and psar_bullish[index])
            )
            stop_hit = (
                (is_long and low[index] <= trail_price)
                or ((not is_long) and high[index] >= trail_price)
                or sar_reversal
            )
            if stop_hit and index > segment_start:
                stop_index = int(index)
                if is_long:
                    long_stop_events[index] = float(high[index])
                else:
                    short_stop_events[index] = float(low[index])
                if direction_name == "Auto" and index + 1 < n:
                    next_segment = index + 1
                    is_long = not is_long
                else:
                    active = False
                break

        if next_segment is None:
            break
        segment_start = next_segment
        segment_number += 1


def finite_points(values, color=None):
    points = []
    for value in values:
        if np.isfinite(value):
            if color is None:
                points.append(float(value))
            else:
                points.append({"value": float(value), "color": color})
        else:
            points.append(None)
    return points


trail_color = long_color if effective_direction == "Long" else short_color
colored_trail = [
    {"value": float(trail[index]), "color": long_color if trail_direction[index] > 0 else short_color}
    if np.isfinite(trail[index]) else None
    for index in range(n)
]
non_sar_data = colored_trail if trail_type != "SAR" else [None] * n
sar_data = colored_trail if trail_type == "SAR" else [None] * n
last_trail = None
for value in trail[::-1]:
    if np.isfinite(value):
        last_trail = float(value)
        break

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Runtime Anchor",
            "title": False,
            "data": [float(value) for value in close],
            "color": "rgba(0,0,0,0)",
            "opacity": 0.0,
            "type": "line",
            "lineWidth": 1,
            "overlay": True,
        },
        {
            "name": "Trailing Stop",
            "data": non_sar_data,
            "color": trail_color,
            "type": "line",
            "lineWidth": line_width,
            "overlay": True,
        },
        {
            "name": "SAR Trail",
            "data": sar_data,
            "color": trail_color,
            "type": "circle",
            "lineWidth": line_width,
            "overlay": True,
        },
        {
            "name": "Initial Stop",
            "data": finite_points(entry_marker),
            "color": trail_color,
            "type": "circle",
            "lineWidth": max(2, line_width),
            "overlay": True,
        },
    ],
    "signals": [
        {
            "type": "sell",
            "name": "Long Stop",
            "text": "Long Stop",
            "color": long_color,
            "renderMode": "events",
            "data": finite_points(long_stop_events),
        },
        {
            "type": "buy",
            "name": "Short Stop",
            "text": "Short Stop",
            "color": short_color,
            "renderMode": "events",
            "data": finite_points(short_stop_events),
        },
    ],
    "layers": [],
    "calculatedVars": {
        "direction": effective_direction,
        "trailType": trail_type,
        "startIndex": int(start_index) if n else None,
        "effectiveInitialStop": effective_initial_stop,
        "lastTrail": last_trail,
        "stopIndex": stop_index,
        "status": "active" if active else "stopped",
    },
}
