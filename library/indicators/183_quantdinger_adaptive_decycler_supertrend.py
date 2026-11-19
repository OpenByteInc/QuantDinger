# ============================================================
# QuantDinger Adaptive Decycler Supertrend
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Adaptive Decycler Supertrend"
my_indicator_description = (
    "Combines an efficiency-adaptive decycler with residual RMS bands and a stateful Supertrend trail, "
    "including regime candle colors and reversal markers."
)

# @param source str close Price source values=close,open,high,low,hl2,hlc3,ohlc4
# @param minimum_cutoff int 15 Minimum cutoff range=6:500:1
# @param maximum_cutoff int 50 Maximum cutoff range=7:800:1
# @param efficiency_length int 10 Efficiency length range=2:500:1
# @param rms_length int 20 Residual RMS length range=2:500:1
# @param upper_multiplier float 2.0 Upper band multiplier range=0.1:20.0:0.1
# @param lower_multiplier float 2.6 Lower band multiplier range=0.1:20.0:0.1
# @param show_decycler bool true Show adaptive decycler
# @param show_envelope bool false Show RMS envelope
# @param show_fill bool true Fill RMS envelope
# @param show_signals bool true Show reversal signals
# @param color_candles bool true Color chart candles
# @param bullish_color str #39FF14 Bullish color
# @param bearish_color str #8A2BE2 Bearish color

source_name = str(params.get("source", "close"))
if source_name not in ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4"):
    source_name = "close"
minimum_cutoff = min(500, max(6, int(params.get("minimum_cutoff", 15))))
maximum_cutoff = min(800, max(7, int(params.get("maximum_cutoff", 50))))
efficiency_length = min(500, max(2, int(params.get("efficiency_length", 10))))
rms_length = min(500, max(2, int(params.get("rms_length", 20))))
upper_multiplier = min(20.0, max(0.1, float(params.get("upper_multiplier", 2.0))))
lower_multiplier = min(20.0, max(0.1, float(params.get("lower_multiplier", 2.6))))
show_decycler = bool(params.get("show_decycler", True))
show_envelope = bool(params.get("show_envelope", False))
show_fill = bool(params.get("show_fill", True))
show_signals = bool(params.get("show_signals", True))
color_candles = bool(params.get("color_candles", True))
bullish_color = str(params.get("bullish_color", "#39FF14"))
bearish_color = str(params.get("bearish_color", "#8A2BE2"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

def select_source(name):
    if name == "open":
        return open_price.copy()
    if name == "high":
        return high.copy()
    if name == "low":
        return low.copy()
    if name == "hl2":
        return (high + low) / 2.0
    if name == "hlc3":
        return (high + low + close) / 3.0
    if name == "ohlc4":
        return (open_price + high + low + close) / 4.0
    return close.copy()

def sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result

def rgba(color, alpha):
    raw = str(color).strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(char * 2 for char in raw)
    if len(raw) != 6:
        return color
    try:
        red = int(raw[0:2], 16)
        green = int(raw[2:4], 16)
        blue = int(raw[4:6], 16)
    except ValueError:
        return color
    return "rgba(%d,%d,%d,%.4f)" % (red, green, blue, alpha)

source = select_source(source_name)
fast_cutoff = min(minimum_cutoff, maximum_cutoff)
slow_cutoff = max(minimum_cutoff, maximum_cutoff)
efficiency = np.zeros(n, dtype=float)
adaptive_cutoff = np.full(n, float(slow_cutoff), dtype=float)
decycler = np.full(n, np.nan, dtype=float)

for index in range(n):
    reference = source[index - efficiency_length] if index >= efficiency_length else source[index]
    direction = abs(source[index] - reference)
    noise = 0.0
    for offset in range(efficiency_length):
        current_value = source[index - offset] if index - offset >= 0 else source[index]
        previous_value = source[index - offset - 1] if index - offset - 1 >= 0 else source[index]
        noise += abs(current_value - previous_value)
    efficiency[index] = min(1.0, max(0.0, direction / noise if noise > 0.0 else 0.0))
    adaptive_cutoff[index] = float(slow_cutoff) - efficiency[index] * float(slow_cutoff - fast_cutoff)
    angle = 2.0 * np.pi / adaptive_cutoff[index]
    cosine_value = np.cos(angle)
    alpha = (cosine_value + np.sin(angle) - 1.0) / cosine_value if abs(cosine_value) > 0.000001 else 1.0
    alpha = min(1.0, max(0.0, float(alpha)))
    if index == 0:
        decycler[index] = source[index]
    else:
        decycler[index] = alpha * 0.5 * (source[index] + source[index - 1]) + (1.0 - alpha) * decycler[index - 1]

residual = source - decycler
residual_energy = sma(residual * residual, rms_length)
residual_rms = np.sqrt(np.maximum(np.where(np.isfinite(residual_energy), residual_energy, 0.0), 0.0))
residual_rms = np.maximum(residual_rms, 1e-8)
upper_envelope = decycler + residual_rms * upper_multiplier
lower_envelope = decycler - residual_rms * lower_multiplier

state = np.ones(n, dtype=int)
trend_trail = np.full(n, np.nan, dtype=float)
long_signal = np.zeros(n, dtype=bool)
short_signal = np.zeros(n, dtype=bool)
for index in range(n):
    if index == 0:
        current_state = 1 if source[index] >= decycler[index] else -1
        previous_state = current_state
        previous_trail = lower_envelope[index] if previous_state == 1 else upper_envelope[index]
    else:
        previous_state = int(state[index - 1])
        previous_trail = trend_trail[index - 1]
        if not np.isfinite(previous_trail):
            previous_trail = lower_envelope[index] if previous_state == 1 else upper_envelope[index]

    turn_long = previous_state == -1 and source[index] > previous_trail
    turn_short = previous_state == 1 and source[index] < previous_trail
    if turn_long:
        current_state = 1
    elif turn_short:
        current_state = -1
    else:
        current_state = previous_state
    state[index] = current_state

    if current_state == 1:
        trend_trail[index] = lower_envelope[index] if turn_long else max(lower_envelope[index], previous_trail)
    else:
        trend_trail[index] = upper_envelope[index] if turn_short else min(upper_envelope[index], previous_trail)

    if index > 0:
        long_signal[index] = current_state == 1 and state[index - 1] != 1
        short_signal[index] = current_state == -1 and state[index - 1] != -1

regime_colors = [bullish_color if state[index] == 1 else bearish_color for index in range(n)]

def line_points(values, mask=None, colors=None):
    points = []
    for index in range(n):
        visible = np.isfinite(values[index]) and (mask is None or bool(mask[index]))
        if not visible:
            points.append(None)
            continue
        if colors is None:
            points.append(float(values[index]))
        else:
            points.append({"value": float(values[index]), "color": colors[index]})
    return points

plots = [
    {"name": "Bullish Supertrend", "data": line_points(trend_trail, state == 1), "color": bullish_color, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Bearish Supertrend", "data": line_points(trend_trail, state == -1), "color": bearish_color, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Adaptive Decycler", "data": line_points(decycler, np.full(n, show_decycler, dtype=bool), [rgba(color, 0.65) for color in regime_colors]), "color": rgba(bullish_color, 0.65), "type": "line", "lineWidth": 2, "overlay": True},
]

if show_envelope:
    plots.extend([
        {"name": "Upper RMS Band", "data": line_points(upper_envelope), "color": rgba(bearish_color, 0.30), "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower RMS Band", "data": line_points(lower_envelope), "color": rgba(bullish_color, 0.30), "type": "line", "lineWidth": 1, "overlay": True},
    ])
    if show_fill:
        plots.append({
            "name": "RMS Envelope Fill",
            "data": [
                {"upper": float(upper_envelope[index]), "lower": float(lower_envelope[index]), "color": rgba(regime_colors[index], 0.07)}
                for index in range(n)
            ],
            "color": rgba(bullish_color, 0.07),
            "fillColor": rgba(bullish_color, 0.07),
            "type": "cloud",
            "overlay": True,
        })

def event_points(mask, values, text, color):
    return [
        {"value": float(values[index]), "text": text, "color": color}
        if show_signals and mask[index] and np.isfinite(values[index]) else None
        for index in range(n)
    ]

signals = [
    {"type": "buy", "name": "Decycler Long", "text": "LONG", "color": bullish_color, "data": event_points(long_signal, low, "LONG", bullish_color), "renderMode": "events"},
    {"type": "sell", "name": "Decycler Short", "text": "SHORT", "color": bearish_color, "data": event_points(short_signal, high, "SHORT", bearish_color), "renderMode": "events"},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "description": my_indicator_description,
    "plots": plots,
    "signals": signals,
    "bar_colors": regime_colors if color_candles else [None] * n,
    "calculatedVars": {
        "efficiency": float(efficiency[-1]) if n else None,
        "adaptive_cutoff": float(adaptive_cutoff[-1]) if n else None,
        "residual": float(residual[-1]) if n else None,
        "residual_rms": float(residual_rms[-1]) if n else None,
        "state": int(state[-1]) if n else None,
    },
}
