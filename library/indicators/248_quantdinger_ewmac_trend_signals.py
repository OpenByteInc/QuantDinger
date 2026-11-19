# ============================================================
# QuantDinger EWMAC Trend Signals
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger EWMAC Trend Signals"
my_indicator_description = (
    "Blends six volatility-normalized EMA crossover rules into a capped trend "
    "score with adaptive scaling, hysteresis, and a weighted trend line."
)

# @param preset str Default Configuration values=Default,Fast Response,Smooth Trend
# @param source str close Price source values=open,high,low,close,hl2,hlc3,ohlc4
# @param rule_1_enabled bool false Enable rule 1
# @param rule_1_length int 2 Rule 1 fast span range=1:500:1
# @param rule_1_weight float 1.0 Rule 1 weight range=0.0:10.0:0.1
# @param rule_2_enabled bool true Enable rule 2
# @param rule_2_length int 4 Rule 2 fast span range=1:500:1
# @param rule_2_weight float 1.0 Rule 2 weight range=0.0:10.0:0.1
# @param rule_3_enabled bool true Enable rule 3
# @param rule_3_length int 8 Rule 3 fast span range=1:500:1
# @param rule_3_weight float 1.0 Rule 3 weight range=0.0:10.0:0.1
# @param rule_4_enabled bool true Enable rule 4
# @param rule_4_length int 16 Rule 4 fast span range=1:500:1
# @param rule_4_weight float 1.0 Rule 4 weight range=0.0:10.0:0.1
# @param rule_5_enabled bool true Enable rule 5
# @param rule_5_length int 32 Rule 5 fast span range=1:500:1
# @param rule_5_weight float 1.0 Rule 5 weight range=0.0:10.0:0.1
# @param rule_6_enabled bool false Enable rule 6
# @param rule_6_length int 64 Rule 6 fast span range=1:500:1
# @param rule_6_weight float 1.0 Rule 6 weight range=0.0:10.0:0.1
# @param volatility_length int 32 Volatility length range=5:500:1
# @param long_run_weight float 0.3 Long-run volatility weight range=0.0:1.0:0.05
# @param long_run_length int 500 Long-run volatility length range=50:5000:1
# @param scaling_method str Adaptive Scaling method values=Adaptive,Fixed
# @param adaptive_length int 250 Adaptive scaling length range=20:2000:1
# @param diversification_multiplier float 0.0 Diversification multiplier range=0.0:3.0:0.01
# @param score_cap float 20.0 Score cap range=5.0:100.0:1.0
# @param entry_threshold float 4.0 Entry threshold range=0.5:20.0:0.5
# @param use_neutral_state bool true Use neutral state
# @param exit_fraction float 0.5 Exit fraction range=0.0:1.0:0.05
# @param color_preset str Custom Color preset values=Custom,Classic,Aqua,Cosmic,Cyber,Neon
# @param bullish_color str #00FFAA Bullish color
# @param bearish_color str #FF0000 Bearish color
# @param neutral_color str #808080 Neutral color
# @param line_width int 3 Trend line width range=1:8:1
# @param show_glow bool true Show trend-line glow
# @param show_readout bool true Show current signal readout
# @param color_bars bool true Color price bars
# @param bar_transparency int 0 Bar color transparency range=0:100:1
# @param color_background bool false Color chart background
# @param background_transparency int 90 Background transparency range=0:100:1


def clamp(value, lower, upper):
    return min(upper, max(lower, value))


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def pine_ema(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    alpha = 2.0 / (float(length) + 1.0)
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def parse_hex(color):
    value = str(color).strip().lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    try:
        return int(value[:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        return 128, 128, 128


def rgba(color, opacity):
    red, green, blue = parse_hex(color)
    return f"rgba({red},{green},{blue},{clamp(float(opacity), 0.0, 1.0):.3f})"


preset = str(params.get("preset", "Default"))
source_name = str(params.get("source", "close")).lower()
rule_enabled = [
    bool(params.get("rule_1_enabled", False)), bool(params.get("rule_2_enabled", True)),
    bool(params.get("rule_3_enabled", True)), bool(params.get("rule_4_enabled", True)),
    bool(params.get("rule_5_enabled", True)), bool(params.get("rule_6_enabled", False)),
]
rule_lengths = [
    clamp(int(params.get("rule_1_length", 2)), 1, 500),
    clamp(int(params.get("rule_2_length", 4)), 1, 500),
    clamp(int(params.get("rule_3_length", 8)), 1, 500),
    clamp(int(params.get("rule_4_length", 16)), 1, 500),
    clamp(int(params.get("rule_5_length", 32)), 1, 500),
    clamp(int(params.get("rule_6_length", 64)), 1, 500),
]
rule_weights = [
    clamp(float(params.get("rule_1_weight", 1.0)), 0.0, 10.0),
    clamp(float(params.get("rule_2_weight", 1.0)), 0.0, 10.0),
    clamp(float(params.get("rule_3_weight", 1.0)), 0.0, 10.0),
    clamp(float(params.get("rule_4_weight", 1.0)), 0.0, 10.0),
    clamp(float(params.get("rule_5_weight", 1.0)), 0.0, 10.0),
    clamp(float(params.get("rule_6_weight", 1.0)), 0.0, 10.0),
]
volatility_length = clamp(int(params.get("volatility_length", 32)), 5, 500)
long_run_weight = clamp(float(params.get("long_run_weight", 0.3)), 0.0, 1.0)
long_run_length = clamp(int(params.get("long_run_length", 500)), 50, 5000)
scaling_method = str(params.get("scaling_method", "Adaptive"))
adaptive_length = clamp(int(params.get("adaptive_length", 250)), 20, 2000)
diversification_multiplier = clamp(float(params.get("diversification_multiplier", 0.0)), 0.0, 3.0)
score_cap = clamp(float(params.get("score_cap", 20.0)), 5.0, 100.0)
entry_threshold = clamp(float(params.get("entry_threshold", 4.0)), 0.5, 20.0)
use_neutral_state = bool(params.get("use_neutral_state", True))
exit_fraction = clamp(float(params.get("exit_fraction", 0.5)), 0.0, 1.0)
color_preset = str(params.get("color_preset", "Custom"))
bullish_color = str(params.get("bullish_color", "#00FFAA"))
bearish_color = str(params.get("bearish_color", "#FF0000"))
neutral_color = str(params.get("neutral_color", "#808080"))
line_width = clamp(int(params.get("line_width", 3)), 1, 8)
show_glow = bool(params.get("show_glow", True))
show_readout = bool(params.get("show_readout", True))
color_bars = bool(params.get("color_bars", True))
bar_transparency = clamp(int(params.get("bar_transparency", 0)), 0, 100)
color_background = bool(params.get("color_background", False))
background_transparency = clamp(int(params.get("background_transparency", 90)), 0, 100)

if preset == "Fast Response":
    rule_enabled = [True, True, True, True, False, False]
    volatility_length, long_run_weight, long_run_length = 16, 0.15, 250
    adaptive_length, entry_threshold, exit_fraction = 150, 2.5, 0.70
elif preset == "Smooth Trend":
    rule_enabled = [False, False, False, True, True, True]
    volatility_length, long_run_weight, long_run_length = 64, 0.40, 750
    adaptive_length, entry_threshold, exit_fraction = 400, 6.0, 0.35

palette = {
    "Classic": ("#00FF00", "#FF0000", "#808080"),
    "Aqua": ("#00D4FF", "#FF8C00", "#808080"),
    "Cosmic": ("#49FFCE", "#9932CC", "#808080"),
    "Cyber": ("#00CCCC", "#FF6600", "#808080"),
    "Neon": ("#FFFF00", "#FF00FF", "#808080"),
}
if color_preset in palette:
    bullish_color, bearish_color, neutral_color = palette[color_preset]

df = df.copy()
open_values = pd.to_numeric(df["open"], errors="coerce").to_numpy(dtype=float)
high_values = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
sources = {
    "open": open_values,
    "high": high_values,
    "low": low_values,
    "close": close_values,
    "hl2": (high_values + low_values) * 0.5,
    "hlc3": (high_values + low_values + close_values) / 3.0,
    "ohlc4": (open_values + high_values + low_values + close_values) * 0.25,
}
source = np.maximum(sources.get(source_name, close_values), 1e-10)
n = len(source)

returns = np.zeros(n, dtype=float)
if n > 1:
    returns[1:] = np.log(source[1:] / np.maximum(source[:-1], 1e-10))
volatility_short = pd.Series(returns).rolling(volatility_length, min_periods=volatility_length).std(ddof=0).to_numpy(dtype=float)
volatility_long = pd.Series(volatility_short).rolling(long_run_length, min_periods=long_run_length).mean().to_numpy(dtype=float)
volatility_percent = np.where(
    np.isfinite(volatility_long),
    volatility_short * (1.0 - long_run_weight) + volatility_long * long_run_weight,
    volatility_short,
)
volatility_price = np.maximum(volatility_percent * source, 1e-10)

rule_scores = []
rule_midpoints = []
rule_raw = []
for length in rule_lengths:
    fast = pine_ema(source, length)
    slow = pine_ema(source, length * 4)
    raw = (fast - slow) / volatility_price
    average_absolute = pd.Series(np.abs(raw)).rolling(adaptive_length, min_periods=adaptive_length).mean().to_numpy(dtype=float)
    fixed_scale = 15.0 / np.sqrt(float(length))
    if scaling_method == "Adaptive":
        scale = np.where(np.isfinite(average_absolute), 10.0 / np.maximum(average_absolute, 1e-10), fixed_scale)
    else:
        scale = np.full(n, fixed_scale, dtype=float)
    rule_raw.append(raw)
    rule_scores.append(np.clip(raw * scale, -score_cap, score_cap))
    rule_midpoints.append((fast + slow) * 0.5)

weight_sum = sum(weight for enabled, weight in zip(rule_enabled, rule_weights) if enabled and weight > 0.0)
active_count = sum(1 for enabled, weight in zip(rule_enabled, rule_weights) if enabled and weight > 0.0)
score_sum = np.zeros(n, dtype=float)
line_sum = np.zeros(n, dtype=float)
if weight_sum > 0.0:
    for enabled, weight, score, midpoint in zip(rule_enabled, rule_weights, rule_scores, rule_midpoints):
        if enabled and weight > 0.0:
            score_sum += score * weight
            line_sum += midpoint * weight
    multiplier_table = (1.0, 1.0, 1.1, 1.2, 1.27, 1.32, 1.36)[min(6, active_count)]
    applied_multiplier = diversification_multiplier if diversification_multiplier > 0.0 else multiplier_table
    trend_score = np.clip(score_sum / weight_sum * applied_multiplier, -score_cap, score_cap)
    trend_line = line_sum / weight_sum
else:
    applied_multiplier = 1.0
    trend_score = np.zeros(n, dtype=float)
    trend_line = source.copy()

exit_threshold = entry_threshold * exit_fraction
signal_state = np.zeros(n, dtype=int)
turned_bull = np.zeros(n, dtype=bool)
turned_bear = np.zeros(n, dtype=bool)
turned_neutral = np.zeros(n, dtype=bool)
previous_state = 0
for index, score in enumerate(trend_score):
    state = previous_state
    if np.isfinite(score):
        if score > entry_threshold and state != 1:
            state = 1
        elif score < -entry_threshold and state != -1:
            state = -1
        elif use_neutral_state and state == 1 and score < exit_threshold:
            state = 0
        elif use_neutral_state and state == -1 and score > -exit_threshold:
            state = 0
    signal_state[index] = state
    turned_bull[index] = state == 1 and previous_state != 1
    turned_bear[index] = state == -1 and previous_state != -1
    turned_neutral[index] = index > 0 and state == 0 and previous_state != 0
    previous_state = state

state_colors = [bullish_color if state == 1 else bearish_color if state == -1 else neutral_color for state in signal_state]
trend_points = [
    {"value": float(value), "color": state_colors[index]} if np.isfinite(value) else None
    for index, value in enumerate(trend_line)
]
outer_glow = [
    {"value": float(value), "color": rgba(state_colors[index], 0.12)} if show_glow and np.isfinite(value) else None
    for index, value in enumerate(trend_line)
]
inner_glow = [
    {"value": float(value), "color": rgba(state_colors[index], 0.25)} if show_glow and np.isfinite(value) else None
    for index, value in enumerate(trend_line)
]

bar_opacity = 1.0 - bar_transparency / 100.0
background_opacity = 1.0 - background_transparency / 100.0
bar_colors = [rgba(color, bar_opacity) if color_bars else None for color in state_colors]
background_colors = [rgba(color, background_opacity) if color_background else None for color in state_colors]

layers = []
if show_readout and n and np.isfinite(trend_line[-1]):
    state = int(signal_state[-1])
    text = "Bullish" if state == 1 else "Bearish" if state == -1 else "Neutral"
    red, green, blue = parse_hex(state_colors[-1])
    brightness = (red * 299.0 + green * 587.0 + blue * 114.0) / 1000.0
    layers.append({
        "type": "label",
        "index": n - 1,
        "price": float(trend_line[-1]),
        "text": text,
        "color": state_colors[-1],
        "textColor": "#000000" if brightness >= 128.0 else "#FFFFFF",
        "side": "left",
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Trend Line Glow Outer", "title": False, "data": outer_glow, "color": neutral_color, "type": "line", "lineWidth": 20, "overlay": True},
        {"name": "Trend Line Glow Inner", "title": False, "data": inner_glow, "color": neutral_color, "type": "line", "lineWidth": 10, "overlay": True},
        {"name": "Trend Line", "data": trend_points, "color": neutral_color, "type": "line", "lineWidth": line_width, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Bullish Trend Signal", "text": "BULL", "color": bullish_color, "renderMode": "hidden", "data": [float(trend_line[index]) if turned_bull[index] else None for index in range(n)]},
        {"type": "sell", "name": "Bearish Trend Signal", "text": "BEAR", "color": bearish_color, "renderMode": "hidden", "data": [float(trend_line[index]) if turned_bear[index] else None for index in range(n)]},
        {"type": "info", "name": "Neutral Trend Signal", "text": "NEUTRAL", "color": neutral_color, "renderMode": "hidden", "data": [float(trend_line[index]) if turned_neutral[index] else None for index in range(n)]},
    ],
    "layers": layers,
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": background_colors,
    "calculatedVars": {
        "source": finite(source),
        "returns": finite(returns),
        "volatilityShort": finite(volatility_short),
        "volatilityLong": finite(volatility_long),
        "volatilityPercent": finite(volatility_percent),
        "volatilityPrice": finite(volatility_price),
        "ruleEnabled": rule_enabled,
        "ruleLengths": rule_lengths,
        "ruleWeights": rule_weights,
        "volatilityLength": int(volatility_length),
        "longRunWeight": float(long_run_weight),
        "longRunLength": int(long_run_length),
        "scalingMethod": scaling_method,
        "adaptiveLength": int(adaptive_length),
        "scoreCap": float(score_cap),
        "useNeutralState": bool(use_neutral_state),
        "exitFraction": float(exit_fraction),
        "bullishColor": bullish_color,
        "bearishColor": bearish_color,
        "neutralColor": neutral_color,
        "ruleRaw": [finite(values) for values in rule_raw],
        "ruleScores": [finite(values) for values in rule_scores],
        "ruleMidpoints": [finite(values) for values in rule_midpoints],
        "activeCount": int(active_count),
        "weightSum": float(weight_sum),
        "appliedMultiplier": float(applied_multiplier),
        "entryThreshold": float(entry_threshold),
        "exitThreshold": float(exit_threshold),
        "trendScore": finite(trend_score),
        "trendLine": finite(trend_line),
        "signalState": signal_state.tolist(),
        "turnedBull": turned_bull.tolist(),
        "turnedBear": turned_bear.tolist(),
        "turnedNeutral": turned_neutral.tolist(),
        "stateColors": state_colors,
    },
}
