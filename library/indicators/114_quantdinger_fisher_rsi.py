# ============================================================
# QuantDinger Fisher RSI
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Fisher RSI"
my_indicator_description = (
    "Applies a recursive Fisher transform to smoothed Wilder RSI, displays "
    "theme-aware regime zones, and marks extreme-zone Fisher turns."
)

# @param source str Close RSI source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param rsi_length int 14 RSI length range=2:200:1
# @param rsi_smoothing int 1 RSI SMA smoothing range=1:10:1
# @param oversold_level int 30 Oversold boundary range=1:49:1
# @param overbought_level int 70 Overbought boundary range=51:99:1
# @param fisher_length int 10 Fisher normalization length range=2:100:1
# @param enable_fisher_transform bool true Show Fisher and signal lines
# @param show_fisher_arrows bool true Show extreme-zone Fisher turns
# @param show_chart_highlight bool true Highlight gated crosses on price chart
# @param chart_highlight_transparency int 85 Gated highlight transparency range=0:100:1
# @param show_any_cross_highlight bool false Highlight every Fisher cross
# @param any_cross_highlight_transparency int 93 Any-cross highlight transparency range=0:100:1
# @param show_rsi bool false Show smoothed RSI
# @param show_regime_zones bool true Show regime background
# @param regime_basis str Fisher_Transform Regime basis values=Fisher_Transform,RSI
# @param color_theme str Vaporwave Color theme values=Modern,Neon,Classic,Gold Standard,Ultraviolet,Infrared,Arctic,Vaporwave,Matrix,Terminal
# @param use_custom_colors bool false Override theme colors
# @param custom_bearish_color str #00D4AA Custom bearish color
# @param custom_bullish_color str #FF073A Custom bullish color
# @param custom_neutral_color str #9D4EDD Custom neutral color
# @param custom_accent_color str #FFC107 Custom signal color
# @param enable_visual_effects bool true Enable gradients and fills
# @param show_glow bool false Show line glow
# @param gradient_lower_bound float 25 Gradient lower bound range=0:49:1
# @param gradient_upper_bound float 75 Gradient upper bound range=51:100:1

source_name = str(params.get("source", "Close")).strip().upper()
rsi_length = min(200, max(2, int(params.get("rsi_length", 14))))
rsi_smoothing = min(10, max(1, int(params.get("rsi_smoothing", 1))))
oversold_level = min(49, max(1, int(params.get("oversold_level", 30))))
overbought_level = min(99, max(51, int(params.get("overbought_level", 70))))
fisher_length = min(100, max(2, int(params.get("fisher_length", 10))))
enable_fisher_transform = bool(params.get("enable_fisher_transform", True))
show_fisher_arrows = bool(params.get("show_fisher_arrows", True))
show_chart_highlight = bool(params.get("show_chart_highlight", True))
chart_highlight_transparency = min(100, max(0, int(params.get("chart_highlight_transparency", 85))))
show_any_cross_highlight = bool(params.get("show_any_cross_highlight", False))
any_cross_highlight_transparency = min(100, max(0, int(params.get("any_cross_highlight_transparency", 93))))
show_rsi = bool(params.get("show_rsi", False))
show_regime_zones = bool(params.get("show_regime_zones", True))
regime_basis = str(params.get("regime_basis", "Fisher_Transform")).replace("_", " ").strip()
color_theme = str(params.get("color_theme", "Vaporwave")).strip()
use_custom_colors = bool(params.get("use_custom_colors", False))
enable_visual_effects = bool(params.get("enable_visual_effects", True))
show_glow = bool(params.get("show_glow", False))
gradient_lower_bound = min(49.0, max(0.0, float(params.get("gradient_lower_bound", 25.0))))
gradient_upper_bound = min(100.0, max(51.0, float(params.get("gradient_upper_bound", 75.0))))

theme_colors = {
    "Modern": ("#00D4AA", "#FF073A", "#9D4EDD", "#FFC107"),
    "Neon": ("#00FFFF", "#FF00FF", "#9932CC", "#FFFF00"),
    "Classic": ("#00C853", "#FF1744", "#888888", "#FFB300"),
    "Gold Standard": ("#FFD700", "#B0B0B0", "#CD7F32", "#FFFFFF"),
    "Ultraviolet": ("#BF00FF", "#6600AA", "#5C0080", "#00FF00"),
    "Infrared": ("#FFFFFF", "#CC0000", "#882200", "#FF6600"),
    "Arctic": ("#A8D8FF", "#4488CC", "#3A5A8A", "#F0F8FF"),
    "Vaporwave": ("#FF6B9D", "#9B30FF", "#6622AA", "#00FFFF"),
    "Matrix": ("#00FF41", "#007A1F", "#003D0F", "#FFFFFF"),
    "Terminal": ("#00FF00", "#FF6000", "#707070", "#FFFFFF"),
}
theme_bearish, theme_bullish, theme_neutral, theme_accent = theme_colors.get(
    color_theme, theme_colors["Vaporwave"]
)
if use_custom_colors:
    theme_bearish = str(params.get("custom_bearish_color", "#00D4AA"))
    theme_bullish = str(params.get("custom_bullish_color", "#FF073A"))
    theme_neutral = str(params.get("custom_neutral_color", "#9D4EDD"))
    theme_accent = str(params.get("custom_accent_color", "#FFC107"))


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def color_tuple(value, fallback):
    text = str(value).strip().lstrip("#")
    if len(text) >= 6:
        try:
            return tuple(int(text[offset:offset + 2], 16) for offset in (0, 2, 4))
        except ValueError:
            pass
    fallback_text = fallback.lstrip("#")
    return tuple(int(fallback_text[offset:offset + 2], 16) for offset in (0, 2, 4))


def mix_color(first, second, weight):
    weight = min(1.0, max(0.0, float(weight)))
    first_rgb = color_tuple(first, "#808080")
    second_rgb = color_tuple(second, "#808080")
    mixed = tuple(int(round(a + (b - a) * weight)) for a, b in zip(first_rgb, second_rgb))
    return f"rgb({mixed[0]},{mixed[1]},{mixed[2]})"


def rgba_color(color, opacity):
    red, green, blue = color_tuple(color, "#808080")
    return f"rgba({red},{green},{blue},{min(1.0, max(0.0, float(opacity))):.4f})"


moderate_bearish_threshold = (oversold_level + 50.0) / 2.0
moderate_bullish_threshold = (overbought_level + 50.0) / 2.0
theme_moderate_bearish = mix_color(theme_bearish, theme_neutral, 0.5)
theme_moderate_bullish = mix_color(theme_neutral, theme_bullish, 0.5)


def line_color(value):
    if not np.isfinite(value):
        return theme_neutral
    if enable_visual_effects:
        clamped = min(gradient_upper_bound, max(gradient_lower_bound, float(value)))
        if clamped <= 50.0:
            denominator = max(1e-12, 50.0 - gradient_lower_bound)
            return mix_color(theme_bearish, theme_neutral, (clamped - gradient_lower_bound) / denominator)
        denominator = max(1e-12, gradient_upper_bound - 50.0)
        return mix_color(theme_neutral, theme_bullish, (clamped - 50.0) / denominator)
    if value <= oversold_level:
        return theme_bearish
    if value <= moderate_bearish_threshold:
        return theme_moderate_bearish
    if value < moderate_bullish_threshold:
        return theme_neutral
    if value < overbought_level:
        return theme_moderate_bullish
    return theme_bullish


frame = df.copy().reset_index(drop=True)
n = len(frame)
open_values = pd.to_numeric(frame["open"], errors="coerce").to_numpy(dtype=float)
high_values = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype=float)
source_map = {
    "OPEN": open_values,
    "HIGH": high_values,
    "LOW": low_values,
    "HL2": (high_values + low_values) / 2.0,
    "HLC3": (high_values + low_values + close_values) / 3.0,
    "OHLC4": (open_values + high_values + low_values + close_values) / 4.0,
}
source_values = source_map.get(source_name, close_values)

change = np.diff(source_values, prepend=np.nan)
gain = np.where(np.isfinite(change), np.maximum(change, 0.0), np.nan)
loss = np.where(np.isfinite(change), np.maximum(-change, 0.0), np.nan)
average_gain = np.full(n, np.nan, dtype=float)
average_loss = np.full(n, np.nan, dtype=float)
if n > rsi_length:
    average_gain[rsi_length] = float(np.mean(gain[1:rsi_length + 1]))
    average_loss[rsi_length] = float(np.mean(loss[1:rsi_length + 1]))
    for index in range(rsi_length + 1, n):
        average_gain[index] = (average_gain[index - 1] * (rsi_length - 1) + gain[index]) / rsi_length
        average_loss[index] = (average_loss[index - 1] * (rsi_length - 1) + loss[index]) / rsi_length

rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not (np.isfinite(average_gain[index]) and np.isfinite(average_loss[index])):
        continue
    if average_loss[index] == 0.0:
        rsi[index] = 100.0 if average_gain[index] > 0.0 else 50.0
    else:
        ratio = average_gain[index] / average_loss[index]
        rsi[index] = 100.0 - 100.0 / (1.0 + ratio)

score = pd.Series(rsi).rolling(rsi_smoothing, min_periods=rsi_smoothing).mean().to_numpy(dtype=float)
fisher_highest = pd.Series(score).rolling(fisher_length, min_periods=1).max().to_numpy(dtype=float)
fisher_lowest = pd.Series(score).rolling(fisher_length, min_periods=1).min().to_numpy(dtype=float)
fisher_transform = np.full(n, np.nan, dtype=float)
fisher_signal = np.full(n, np.nan, dtype=float)
fisher_value_scaled = np.full(n, np.nan, dtype=float)
fisher_signal_scaled = np.full(n, np.nan, dtype=float)
fisher_value1 = np.full(n, np.nan, dtype=float)
previous_value = 0.0
previous_fisher = 0.0
for index in range(n):
    if not np.isfinite(score[index]):
        continue
    span = fisher_highest[index] - fisher_lowest[index]
    position = (score[index] - fisher_lowest[index]) / span - 0.5 if span != 0.0 else 0.0
    current_value = min(0.999, max(-0.999, 0.66 * position + 0.67 * previous_value))
    current_fisher = 0.5 * np.log((1.0 + current_value) / (1.0 - current_value)) + 0.5 * previous_fisher
    fisher_value1[index] = current_value
    fisher_transform[index] = current_fisher
    fisher_signal[index] = previous_fisher
    fisher_value_scaled[index] = 50.0 + current_fisher * 10.0
    fisher_signal_scaled[index] = 50.0 + previous_fisher * 10.0
    previous_value = current_value
    previous_fisher = current_fisher

any_cross_up = np.zeros(n, dtype=bool)
any_cross_down = np.zeros(n, dtype=bool)
for index in range(2, n):
    if np.all(np.isfinite(fisher_transform[index - 2:index + 1])):
        any_cross_up[index] = fisher_transform[index] > fisher_transform[index - 1] and fisher_transform[index - 1] <= fisher_transform[index - 2]
        any_cross_down[index] = fisher_transform[index] < fisher_transform[index - 1] and fisher_transform[index - 1] >= fisher_transform[index - 2]

bull_zone = (score <= oversold_level) | (fisher_value_scaled <= oversold_level)
bear_zone = (score >= overbought_level) | (fisher_value_scaled >= overbought_level)
fisher_cross_up = show_fisher_arrows & any_cross_up & bull_zone
fisher_cross_down = show_fisher_arrows & any_cross_down & bear_zone
rsi_enter_overbought = np.zeros(n, dtype=bool)
rsi_enter_oversold = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(score[index]) and np.isfinite(score[index - 1]):
        rsi_enter_overbought[index] = score[index] > overbought_level and score[index - 1] <= overbought_level
        rsi_enter_oversold[index] = score[index] < oversold_level and score[index - 1] >= oversold_level

rsi_colors = [line_color(value) if np.isfinite(value) else theme_neutral for value in score]
fisher_colors = [line_color(value) if np.isfinite(value) else theme_neutral for value in fisher_value_scaled]
rsi_plot = [{"value": float(value), "color": rsi_colors[index]} if show_rsi and np.isfinite(value) else None for index, value in enumerate(score)]
fisher_plot = [{"value": float(value), "color": fisher_colors[index]} if enable_fisher_transform and np.isfinite(value) else None for index, value in enumerate(fisher_value_scaled)]
signal_plot = [float(value) if enable_fisher_transform and np.isfinite(value) else None for value in fisher_signal_scaled]

plots = [
    {
        "name": "Regime", "title": False,
        "data": [
            {
                "upper": 100.0, "lower": 0.0,
                "color": rgba_color(
                    rsi_colors[index] if regime_basis.upper() == "RSI" else fisher_colors[index],
                    0.10,
                ),
            }
            if show_regime_zones and np.isfinite(score[index] if regime_basis.upper() == "RSI" else fisher_value_scaled[index])
            else None
            for index in range(n)
        ],
        "color": theme_neutral, "type": "cloud", "opacity": 0.10, "overlay": False,
    },
    {
        "name": "OB/OS Band", "title": False,
        "data": [{"upper": float(overbought_level), "lower": float(oversold_level)} for _ in range(n)] if enable_visual_effects else [None for _ in range(n)],
        "color": theme_neutral, "type": "cloud", "opacity": 0.08, "overlay": False,
    },
    {
        "name": "Overbought Overshoot", "title": False,
        "data": [
            {"upper": float(value), "lower": float(overbought_level), "color": rgba_color(theme_bullish, 0.25)}
            if enable_visual_effects and np.isfinite(value) and value > overbought_level else None
            for value in fisher_value_scaled
        ],
        "color": theme_bullish, "type": "cloud", "opacity": 0.25, "overlay": False,
    },
    {
        "name": "Oversold Overshoot", "title": False,
        "data": [
            {"upper": float(oversold_level), "lower": float(value), "color": rgba_color(theme_bearish, 0.25)}
            if enable_visual_effects and np.isfinite(value) and value < oversold_level else None
            for value in fisher_value_scaled
        ],
        "color": theme_bearish, "type": "cloud", "opacity": 0.25, "overlay": False,
    },
    {"name": "Oversold", "data": [float(oversold_level) for _ in range(n)], "color": rgba_color(theme_bearish, 0.60), "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Neutral", "data": [50.0 for _ in range(n)], "color": rgba_color(theme_neutral, 0.50), "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Overbought", "data": [float(overbought_level) for _ in range(n)], "color": rgba_color(theme_bullish, 0.60), "type": "line", "lineWidth": 1, "overlay": False},
]
if show_glow and show_rsi:
    plots.extend([
        {"name": "RSI Glow Outer", "title": False, "data": rsi_plot, "color": theme_neutral, "type": "line", "lineWidth": 8, "opacity": 0.20, "overlay": False},
        {"name": "RSI Glow Inner", "title": False, "data": rsi_plot, "color": theme_neutral, "type": "line", "lineWidth": 5, "opacity": 0.40, "overlay": False},
    ])
if show_rsi:
    plots.append({"name": "RSI", "data": rsi_plot, "color": theme_neutral, "type": "line", "lineWidth": 3, "lineStyle": "linebr", "connectNulls": False, "overlay": False})
if show_glow and enable_fisher_transform:
    plots.extend([
        {"name": "Fisher Glow Outer", "title": False, "data": fisher_plot, "color": theme_neutral, "type": "line", "lineWidth": 8, "opacity": 0.20, "overlay": False},
        {"name": "Fisher Glow Inner", "title": False, "data": fisher_plot, "color": theme_neutral, "type": "line", "lineWidth": 5, "opacity": 0.40, "overlay": False},
    ])
if enable_fisher_transform:
    plots.extend([
        {"name": "Fisher Signal", "data": signal_plot, "color": theme_accent, "type": "line", "lineWidth": 1, "lineStyle": "linebr", "connectNulls": False, "overlay": False},
        {"name": "Fisher RSI", "data": fisher_plot, "color": theme_neutral, "type": "line", "lineWidth": 2, "lineStyle": "linebr", "connectNulls": False, "overlay": False},
    ])
plots.extend([
    {
        "name": "Bullish Fisher Turn", "title": False,
        "data": [{"value": float(fisher_value_scaled[index]), "color": theme_bearish, "size": 5} if fisher_cross_up[index] else None for index in range(n)],
        "color": theme_bearish, "type": "scatter", "size": 5, "overlay": False,
    },
    {
        "name": "Bearish Fisher Turn", "title": False,
        "data": [{"value": float(fisher_value_scaled[index]), "color": theme_bullish, "size": 5} if fisher_cross_down[index] else None for index in range(n)],
        "color": theme_bullish, "type": "scatter", "size": 5, "overlay": False,
    },
])

background_colors = []
for index in range(n):
    if show_chart_highlight and fisher_cross_up[index]:
        background_colors.append(rgba_color(theme_bearish, 1.0 - chart_highlight_transparency / 100.0))
    elif show_chart_highlight and fisher_cross_down[index]:
        background_colors.append(rgba_color(theme_bullish, 1.0 - chart_highlight_transparency / 100.0))
    elif show_any_cross_highlight and any_cross_up[index]:
        background_colors.append(rgba_color(theme_bearish, 1.0 - any_cross_highlight_transparency / 100.0))
    elif show_any_cross_highlight and any_cross_down[index]:
        background_colors.append(rgba_color(theme_bullish, 1.0 - any_cross_highlight_transparency / 100.0))
    else:
        background_colors.append(None)

signals = [
    {"type": "buy", "name": "Fisher Arrow Bullish", "text": "FRSI", "color": theme_bearish, "renderMode": "hidden", "data": [float(fisher_value_scaled[index]) if fisher_cross_up[index] else None for index in range(n)]},
    {"type": "sell", "name": "Fisher Arrow Bearish", "text": "FRSI", "color": theme_bullish, "renderMode": "hidden", "data": [float(fisher_value_scaled[index]) if fisher_cross_down[index] else None for index in range(n)]},
    {"type": "buy", "name": "Any Fisher Bullish Cross", "text": "TURN", "color": theme_bearish, "renderMode": "hidden", "data": [float(fisher_value_scaled[index]) if any_cross_up[index] else None for index in range(n)]},
    {"type": "sell", "name": "Any Fisher Bearish Cross", "text": "TURN", "color": theme_bullish, "renderMode": "hidden", "data": [float(fisher_value_scaled[index]) if any_cross_down[index] else None for index in range(n)]},
    {"type": "sell", "name": "RSI Overbought", "text": "RSI OB", "color": theme_bullish, "renderMode": "hidden", "data": [float(score[index]) if rsi_enter_overbought[index] else None for index in range(n)]},
    {"type": "buy", "name": "RSI Oversold", "text": "RSI OS", "color": theme_bearish, "renderMode": "hidden", "data": [float(score[index]) if rsi_enter_oversold[index] else None for index in range(n)]},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": [None for _ in range(n)],
    "background_colors": background_colors,
    "calculatedVars": {
        "source": source_name, "averageGain": finite(average_gain), "averageLoss": finite(average_loss),
        "rsi": finite(rsi), "score": finite(score), "fisherHighest": finite(fisher_highest),
        "fisherLowest": finite(fisher_lowest), "fisherValue1": finite(fisher_value1),
        "fisherTransform": finite(fisher_transform), "fisherSignal": finite(fisher_signal),
        "fisherValueScaled": finite(fisher_value_scaled), "fisherSignalScaled": finite(fisher_signal_scaled),
        "bullZone": bull_zone.tolist(), "bearZone": bear_zone.tolist(),
        "anyCrossUp": any_cross_up.tolist(), "anyCrossDown": any_cross_down.tolist(),
        "fisherCrossUp": fisher_cross_up.tolist(), "fisherCrossDown": fisher_cross_down.tolist(),
        "rsiEnterOverbought": rsi_enter_overbought.tolist(), "rsiEnterOversold": rsi_enter_oversold.tolist(),
        "rsiColors": rsi_colors, "fisherColors": fisher_colors,
        "themeColors": [theme_bearish, theme_bullish, theme_neutral, theme_accent],
    },
}
