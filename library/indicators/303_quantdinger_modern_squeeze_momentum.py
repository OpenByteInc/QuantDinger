# ============================================================
# QuantDinger Modern Squeeze Momentum
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

import math

my_indicator_name = "QuantDinger Modern Squeeze Momentum"
my_indicator_description = "Adaptive squeeze grades, normalized momentum, qualified releases, closed higher-timeframe context and running statistics."
indicator_timeframes = ["4m", "12m", "20m", "1H", "2H", "4H", "8H", "12H", "1D", "2D", "4D", "1W", "4W"]

# @param classic_mode bool false Classic LazyBear mode
# @param adaptive_length bool true Adaptive cycle length
# @param minimum_length int 12 Minimum adaptive length range=1:500:1
# @param maximum_length int 40 Maximum adaptive length range=1:500:1
# @param fixed_length int 20 Fixed length range=1:500:1
# @param bb_multiplier float 2.0 Bollinger multiplier range=0:10:0.1
# @param kc_multiplier float 1.5 Keltner multiplier range=0:10:0.1
# @param percentile_ranking bool true Percentile squeeze grading
# @param rank_window int 150 Percentile window range=1:2000:1
# @param light_percentile int 30 Light squeeze percentile range=0:100:1
# @param medium_percentile int 15 Medium squeeze percentile range=0:100:1
# @param tight_percentile int 5 Tight squeeze percentile range=0:100:1
# @param minimum_squeeze_bars int 3 Minimum squeeze bars range=1:500:1
# @param normalize_momentum bool true Normalize momentum by Keltner range
# @param momentum_threshold float 0.5 Qualified momentum threshold range=0:100:0.1
# @param qualified_release bool true Filter release events
# @param require_range_break bool true Require squeeze-range break
# @param show_raw_release bool true Show unqualified releases
# @param enable_htf bool true Higher-timeframe wash
# @param htf_timeframe str Auto Higher timeframe values=Auto,15m,30m,1H,2H,4H,8H,12H,1D,2D,4D,1W
# @param show_table bool true Show statistics table
# @param statistics_horizon int 10 Statistics horizon range=1:500:1
# @param palette str GBB Palette values=GBB,LazyBear,Mono

classic_mode = bool(params.get("classic_mode", False))
adaptive_length = bool(params.get("adaptive_length", True))
minimum_length = min(500, max(1, int(params.get("minimum_length", 12))))
maximum_length = min(500, max(1, int(params.get("maximum_length", 40))))
fixed_length = min(500, max(1, int(params.get("fixed_length", 20))))
bb_multiplier = max(0.0, float(params.get("bb_multiplier", 2.0)))
kc_multiplier = max(0.0, float(params.get("kc_multiplier", 1.5)))
percentile_ranking = bool(params.get("percentile_ranking", True))
rank_window = min(2000, max(1, int(params.get("rank_window", 150))))
light_percentile = min(100, max(0, int(params.get("light_percentile", 30))))
medium_percentile = min(100, max(0, int(params.get("medium_percentile", 15))))
tight_percentile = min(100, max(0, int(params.get("tight_percentile", 5))))
minimum_squeeze_bars = min(500, max(1, int(params.get("minimum_squeeze_bars", 3))))
normalize_momentum = bool(params.get("normalize_momentum", True))
momentum_threshold = max(0.0, float(params.get("momentum_threshold", 0.5)))
qualified_release = bool(params.get("qualified_release", True))
require_range_break = bool(params.get("require_range_break", True))
show_raw_release = bool(params.get("show_raw_release", True))
enable_htf = bool(params.get("enable_htf", True))
htf_timeframe = str(params.get("htf_timeframe", "Auto")).strip()
show_table = bool(params.get("show_table", True))
statistics_horizon = min(500, max(1, int(params.get("statistics_horizon", 10))))
palette = str(params.get("palette", "GBB"))
if palette not in ("GBB", "LazyBear", "Mono"):
    palette = "GBB"

use_layer_1 = not classic_mode and adaptive_length
use_layer_2 = not classic_mode and percentile_ranking
use_layer_3 = not classic_mode and normalize_momentum
use_layer_4 = not classic_mode and qualified_release


def _normalize(source_frame):
    selected = df.copy() if source_frame is None or len(source_frame) == 0 else source_frame.copy()
    result = selected.reset_index(drop=True)
    if "time" not in result.columns:
        result["time"] = np.arange(len(result), dtype=float) * 3600.0
    if "volume" not in result.columns:
        result["volume"] = 0.0
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
    times = result["time"].to_numpy(dtype=float)
    if len(times) and max(abs(float(value)) for value in times) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result


def _tf_seconds(value):
    text = str(value or "1H").strip()
    aliases = {"1": 60, "3": 180, "5": 300, "15": 900, "30": 1800, "60": 3600, "120": 7200, "240": 14400, "D": 86400, "W": 604800, "M": 2629800}
    if text in aliases:
        return aliases[text]
    if text.endswith("m") and text[:-1].isdigit():
        return int(text[:-1]) * 60
    upper = text.upper()
    if upper.endswith("H") and upper[:-1].isdigit():
        return int(upper[:-1]) * 3600
    if upper.endswith("D") and upper[:-1].isdigit():
        return int(upper[:-1]) * 86400
    if upper.endswith("W") and upper[:-1].isdigit():
        return int(upper[:-1]) * 604800
    return 3600


def _tf_name(seconds):
    value = max(60, int(seconds))
    if value % 604800 == 0:
        return f"{value // 604800}W"
    if value % 86400 == 0:
        return f"{value // 86400}D"
    if value % 3600 == 0:
        return f"{value // 3600}H"
    return f"{max(1, value // 60)}m"


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _atr(values_high, values_low, values_close, period):
    count = len(values_close)
    true_range = np.full(count, np.nan, dtype=float)
    for index in range(count):
        if index == 0:
            true_range[index] = values_high[index] - values_low[index]
        else:
            true_range[index] = max(values_high[index] - values_low[index], abs(values_high[index] - values_close[index - 1]), abs(values_low[index] - values_close[index - 1]))
    result = np.full(count, np.nan, dtype=float)
    if count >= period:
        result[period - 1] = float(np.mean(true_range[:period]))
        for index in range(period, count):
            result[index] = (result[index - 1] * (period - 1) + true_range[index]) / period
    return result


def _state(source_frame):
    opened = source_frame["open"].to_numpy(dtype=float)
    high = source_frame["high"].to_numpy(dtype=float)
    low = source_frame["low"].to_numpy(dtype=float)
    close = source_frame["close"].to_numpy(dtype=float)
    count = len(source_frame)
    true_range = np.full(count, np.nan, dtype=float)
    for index in range(count):
        true_range[index] = high[index] - low[index] if index == 0 else max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))

    smooth = np.zeros(count, dtype=float)
    detrender = np.zeros(count, dtype=float)
    quadrature = np.zeros(count, dtype=float)
    in_phase = np.zeros(count, dtype=float)
    ji = np.zeros(count, dtype=float)
    jq = np.zeros(count, dtype=float)
    i2 = np.zeros(count, dtype=float)
    q2 = np.zeros(count, dtype=float)
    re = np.zeros(count, dtype=float)
    im = np.zeros(count, dtype=float)
    period_raw = np.zeros(count, dtype=float)
    period_smoothed = np.zeros(count, dtype=float)
    lengths = np.full(count, fixed_length, dtype=int)

    def lag(values, index, offset):
        return float(values[index - offset]) if index >= offset else 0.0

    for index in range(count):
        source = (high[index] + low[index]) / 2.0
        source_1 = (high[index - 1] + low[index - 1]) / 2.0 if index >= 1 else 0.0
        source_2 = (high[index - 2] + low[index - 2]) / 2.0 if index >= 2 else 0.0
        source_3 = (high[index - 3] + low[index - 3]) / 2.0 if index >= 3 else 0.0
        previous_period = period_raw[index - 1] if index >= 1 else 0.0
        factor = 0.075 * previous_period + 0.54
        smooth[index] = (4.0 * source + 3.0 * source_1 + 2.0 * source_2 + source_3) / 10.0
        detrender[index] = (0.0962 * smooth[index] + 0.5769 * lag(smooth, index, 2) - 0.5769 * lag(smooth, index, 4) - 0.0962 * lag(smooth, index, 6)) * factor
        quadrature[index] = (0.0962 * detrender[index] + 0.5769 * lag(detrender, index, 2) - 0.5769 * lag(detrender, index, 4) - 0.0962 * lag(detrender, index, 6)) * factor
        in_phase[index] = lag(detrender, index, 3)
        ji[index] = (0.0962 * in_phase[index] + 0.5769 * lag(in_phase, index, 2) - 0.5769 * lag(in_phase, index, 4) - 0.0962 * lag(in_phase, index, 6)) * factor
        jq[index] = (0.0962 * quadrature[index] + 0.5769 * lag(quadrature, index, 2) - 0.5769 * lag(quadrature, index, 4) - 0.0962 * lag(quadrature, index, 6)) * factor
        i2_previous = i2[index - 1] if index >= 1 else 0.0
        q2_previous = q2[index - 1] if index >= 1 else 0.0
        re_previous = re[index - 1] if index >= 1 else 0.0
        im_previous = im[index - 1] if index >= 1 else 0.0
        i2[index] = 0.2 * (in_phase[index] - jq[index]) + 0.8 * i2_previous
        q2[index] = 0.2 * (quadrature[index] + ji[index]) + 0.8 * q2_previous
        re[index] = 0.2 * (i2[index] * i2_previous + q2[index] * q2_previous) + 0.8 * re_previous
        im[index] = 0.2 * (i2[index] * q2_previous - q2[index] * i2_previous) + 0.8 * im_previous
        candidate = previous_period
        if im[index] != 0.0 and re[index] != 0.0:
            candidate = 2.0 * math.pi / math.atan(im[index] / re[index])
        candidate = min(candidate, 1.5 * previous_period)
        candidate = max(candidate, 0.67 * previous_period)
        candidate = min(50.0, max(6.0, candidate))
        period_raw[index] = 0.2 * candidate + 0.8 * previous_period
        period_smoothed[index] = period_raw[index] if index == 0 else 0.4 * period_raw[index] + 0.6 * period_smoothed[index - 1]
        cycle = min(max(period_smoothed[index], minimum_length), maximum_length)
        adaptive = max(1, int(math.floor(cycle + 0.5)))
        lengths[index] = adaptive if use_layer_1 else fixed_length

    basis = np.full(count, np.nan, dtype=float)
    upper_bb = np.full(count, np.nan, dtype=float)
    lower_bb = np.full(count, np.nan, dtype=float)
    keltner_range = np.full(count, np.nan, dtype=float)
    upper_kc = np.full(count, np.nan, dtype=float)
    lower_kc = np.full(count, np.nan, dtype=float)
    classic_squeeze = np.zeros(count, dtype=bool)
    midpoint = np.full(count, np.nan, dtype=float)
    for index in range(count):
        window_length = int(lengths[index])
        start = index - window_length + 1
        if start < 0:
            continue
        close_window = close[start:index + 1]
        basis[index] = float(np.mean(close_window))
        deviation = float(np.std(close_window, ddof=0)) * bb_multiplier
        upper_bb[index] = basis[index] + deviation
        lower_bb[index] = basis[index] - deviation
        keltner_range[index] = float(np.mean(true_range[start:index + 1]))
        upper_kc[index] = basis[index] + kc_multiplier * keltner_range[index]
        lower_kc[index] = basis[index] - kc_multiplier * keltner_range[index]
        classic_squeeze[index] = lower_bb[index] > lower_kc[index] and upper_bb[index] < upper_kc[index]
        midpoint[index] = ((float(np.max(high[start:index + 1])) + float(np.min(low[start:index + 1]))) / 2.0 + basis[index]) / 2.0

    centered = close - midpoint
    momentum_raw = np.full(count, np.nan, dtype=float)
    for index in range(count):
        window_length = int(lengths[index])
        start = index - window_length + 1
        if start < 0:
            continue
        window = centered[start:index + 1]
        if not np.all(np.isfinite(window)):
            continue
        if window_length == 1:
            momentum_raw[index] = float(window[-1])
            continue
        x_values = np.arange(window_length, dtype=float)
        x_mean = (window_length - 1.0) / 2.0
        denominator = float(np.sum((x_values - x_mean) ** 2))
        slope = float(np.sum((x_values - x_mean) * (window - float(np.mean(window)))) / denominator) if denominator > 0.0 else 0.0
        intercept = float(np.mean(window)) - slope * x_mean
        momentum_raw[index] = intercept + slope * (window_length - 1.0)

    ratio = np.full(count, np.nan, dtype=float)
    percent_rank = np.full(count, np.nan, dtype=float)
    grade = np.zeros(count, dtype=int)
    squeeze_on = np.zeros(count, dtype=bool)
    squeeze_bars = np.zeros(count, dtype=int)
    for index in range(count):
        denominator = upper_kc[index] - lower_kc[index]
        if np.isfinite(denominator) and denominator != 0.0:
            ratio[index] = (upper_bb[index] - lower_bb[index]) / denominator
        start = index - rank_window
        if start >= 0:
            rank_values = ratio[start:index]
            if np.isfinite(ratio[index]) and np.all(np.isfinite(rank_values)):
                percent_rank[index] = 100.0 * float(np.count_nonzero(rank_values <= ratio[index])) / rank_window
        if use_layer_2:
            value = percent_rank[index]
            if np.isfinite(value):
                grade[index] = 3 if value <= tight_percentile else 2 if value <= medium_percentile else 1 if value <= light_percentile else 0
        else:
            grade[index] = 1 if classic_squeeze[index] else 0
        squeeze_on[index] = grade[index] >= 1
        squeeze_bars[index] = (squeeze_bars[index - 1] if index >= 1 else 0) + 1 if squeeze_on[index] else 0

    momentum = momentum_raw.copy()
    if use_layer_3:
        for index in range(count):
            momentum[index] = momentum_raw[index] / keltner_range[index] if np.isfinite(momentum_raw[index]) and np.isfinite(keltner_range[index]) and keltner_range[index] != 0.0 else np.nan

    direction = np.zeros(count, dtype=int)
    raw_release = np.zeros(count, dtype=bool)
    qualified = np.zeros(count, dtype=bool)
    qualified_direction = np.zeros(count, dtype=int)
    squeeze_start = np.zeros(count, dtype=bool)
    for index in range(count):
        previous_on = squeeze_on[index - 1] if index >= 1 else False
        squeeze_start[index] = squeeze_on[index] and not previous_on
        raw_release[index] = previous_on and not squeeze_on[index]
        if np.isfinite(momentum[index]):
            direction[index] = 1 if momentum[index] > 0.0 else -1 if momentum[index] < 0.0 else 0
        bar_direction = 1 if close[index] > opened[index] else -1 if close[index] < opened[index] else 0
        duration = int(squeeze_bars[index - 1]) if index >= 1 else 0
        range_start = max(0, index - max(duration, 1))
        previous_high = float(np.max(high[range_start:index])) if index > range_start else np.nan
        previous_low = float(np.min(low[range_start:index])) if index > range_start else np.nan
        agrees = direction[index] != 0 and direction[index] == bar_direction
        strong = np.isfinite(momentum[index]) and abs(momentum[index]) >= momentum_threshold
        long_enough = duration >= minimum_squeeze_bars
        broke = not require_range_break or (direction[index] > 0 and np.isfinite(previous_high) and close[index] > previous_high) or (direction[index] < 0 and np.isfinite(previous_low) and close[index] < previous_low)
        qualified[index] = raw_release[index] and agrees and strong and long_enough and broke if use_layer_4 else raw_release[index]
        qualified_direction[index] = direction[index] if qualified[index] else 0

    return {
        "periodRaw": period_raw, "periodSmoothed": period_smoothed, "length": lengths,
        "basis": basis, "upperBB": upper_bb, "lowerBB": lower_bb, "keltnerRange": keltner_range,
        "upperKC": upper_kc, "lowerKC": lower_kc, "classicSqueeze": classic_squeeze,
        "midpoint": midpoint, "momentumRaw": momentum_raw, "ratio": ratio, "percentRank": percent_rank,
        "grade": grade, "squeezeOn": squeeze_on, "squeezeBars": squeeze_bars, "momentum": momentum,
        "rawRelease": raw_release, "qualifiedRelease": qualified, "qualifiedDirection": qualified_direction,
        "direction": direction, "squeezeStart": squeeze_start,
    }


df = df.copy()
base_frame = _normalize(df)
base_time = base_frame["time"].to_numpy(dtype=float)
opened = base_frame["open"].to_numpy(dtype=float)
high = base_frame["high"].to_numpy(dtype=float)
low = base_frame["low"].to_numpy(dtype=float)
close = base_frame["close"].to_numpy(dtype=float)
n = len(base_frame)
base = _state(base_frame)
runtime_ctx = ctx
chart_timeframe = str(runtime_ctx.timeframe or "1H")
chart_seconds = _tf_seconds(chart_timeframe)
selected_htf = _tf_name(chart_seconds * 4) if htf_timeframe in ("", "Auto") else htf_timeframe
selected_htf_seconds = _tf_seconds(selected_htf)
htf_active = enable_htf and selected_htf_seconds > chart_seconds
htf_available = False
htf_on = np.zeros(n, dtype=bool)
htf_grade = np.zeros(n, dtype=int)

if htf_active:
    raw_htf = runtime_ctx.raw_series(selected_htf) if runtime_ctx is not None and hasattr(runtime_ctx, "raw_series") else None
    if raw_htf is not None and len(raw_htf):
        htf_frame = _normalize(raw_htf)
        htf_state = _state(htf_frame)
        source_time = htf_frame["time"].to_numpy(dtype=float)
        source_close = source_time + selected_htf_seconds
        shifted_on = np.zeros(len(htf_frame), dtype=bool)
        shifted_grade = np.zeros(len(htf_frame), dtype=int)
        if len(htf_frame) > 1:
            shifted_on[1:] = htf_state["squeezeOn"][:-1]
            shifted_grade[1:] = htf_state["grade"][:-1]
        for index in range(n):
            current_close = base_time[index] + chart_seconds
            source_index = int(np.searchsorted(source_close, current_close, side="right") - 1)
            if source_index >= 0:
                htf_on[index] = bool(shifted_on[source_index])
                htf_grade[index] = int(shifted_grade[source_index])
        htf_available = True

if palette == "LazyBear":
    colors = {"pos_up": "#00FF00", "pos_down": "#008000", "neg_down": "#FF0000", "neg_up": "#800000", "dot0": "#0000FF", "dot1": "#000000", "dot2": "#000000", "dot3": "#000000", "long": "#00E676", "short": "#FF1744", "wash": "#FFB300"}
elif palette == "Mono":
    colors = {"pos_up": "#E0E0E0", "pos_down": "#9E9E9E", "neg_down": "#616161", "neg_up": "#424242", "dot0": "#757575", "dot1": "#BDBDBD", "dot2": "#E0E0E0", "dot3": "#FFFFFF", "long": "#FFFFFF", "short": "#BDBDBD", "wash": "#9E9E9E"}
else:
    colors = {"pos_up": "#26C6DA", "pos_down": "#00838F", "neg_down": "#FF5252", "neg_up": "#B71C1C", "dot0": "#546E7A", "dot1": "#FFD54F", "dot2": "#FF9800", "dot3": "#F44336", "long": "#00E676", "short": "#FF1744", "wash": "#FFB300"}

histogram_colors = []
for index in range(n):
    value = base["momentum"][index]
    if not np.isfinite(value):
        histogram_colors.append(None)
        continue
    previous = base["momentum"][index - 1] if index >= 1 and np.isfinite(base["momentum"][index - 1]) else 0.0
    rising = value > previous
    histogram_colors.append(colors["pos_up"] if value > 0.0 and rising else colors["pos_down"] if value > 0.0 else colors["neg_up"] if rising else colors["neg_down"])

background_colors = []
for index in range(n):
    if not htf_active or not htf_available or not htf_on[index]:
        background_colors.append(None)
    else:
        alpha = 0.25 if htf_grade[index] >= 3 else 0.18 if htf_grade[index] == 2 else 0.10
        rgb = (158, 158, 158) if palette == "Mono" else (255, 179, 0)
        background_colors.append(f"rgba({rgb[0]},{rgb[1]},{rgb[2]},{alpha:.2f})")

atr14 = _atr(high, low, close, 14)
n_squeezes = int(np.count_nonzero(base["squeezeStart"]))
n_raw = int(np.count_nonzero(base["rawRelease"]))
n_qualified = int(np.count_nonzero(base["qualifiedRelease"]))
n_scored = 0
n_hit = 0
sum_forward_all = 0.0
n_forward_all = 0
sum_forward_event = 0.0
n_forward_event = 0
forward_range = np.full(n, np.nan, dtype=float)
for index in range(statistics_horizon, n):
    atr_value = atr14[index - statistics_horizon]
    if np.isfinite(atr_value) and atr_value != 0.0:
        start = index - statistics_horizon + 1
        forward_range[index] = (float(np.max(high[start:index + 1])) - float(np.min(low[start:index + 1]))) / atr_value
        sum_forward_all += forward_range[index]
        n_forward_all += 1
    event_index = index - statistics_horizon
    if base["qualifiedRelease"][event_index]:
        n_scored += 1
        move_direction = 1 if close[index] > close[event_index] else -1 if close[index] < close[event_index] else 0
        if move_direction == base["qualifiedDirection"][event_index]:
            n_hit += 1
        if np.isfinite(forward_range[index]):
            sum_forward_event += forward_range[index]
            n_forward_event += 1

hit_rate = 100.0 * n_hit / n_scored if n_scored else np.nan
range_multiple = (sum_forward_event / n_forward_event) / (sum_forward_all / n_forward_all) if n_forward_event and n_forward_all and sum_forward_all > 0.0 else np.nan
if not htf_active:
    htf_status = "off"
elif not htf_available:
    htf_status = "unavailable"
elif n and htf_on[-1]:
    htf_status = f"squeeze {int(htf_grade[-1])}"
else:
    htf_status = "no squeeze"

plots = [
    {"name": "Momentum", "data": _finite(base["momentum"]), "color": histogram_colors, "type": "histogram", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
    {"name": "Squeeze None", "data": [0.0 if value == 0 else None for value in base["grade"]], "color": colors["dot0"], "type": "circle", "size": 2, "overlay": False},
    {"name": "Squeeze Light", "data": [0.0 if value == 1 else None for value in base["grade"]], "color": colors["dot1"], "type": "circle", "size": 4, "overlay": False},
    {"name": "Squeeze Medium", "data": [0.0 if value == 2 else None for value in base["grade"]], "color": colors["dot2"], "type": "circle", "size": 6, "overlay": False},
    {"name": "Squeeze Tight", "data": [0.0 if value == 3 else None for value in base["grade"]], "color": colors["dot3"], "type": "circle", "size": 8, "overlay": False},
    {"name": "Positive Reference 0.5", "data": [0.5 if use_layer_3 else None] * n, "color": "rgba(128,128,128,0.40)", "type": "line", "style": "dashed", "lineWidth": 1, "overlay": False},
    {"name": "Negative Reference 0.5", "data": [-0.5 if use_layer_3 else None] * n, "color": "rgba(128,128,128,0.40)", "type": "line", "style": "dashed", "lineWidth": 1, "overlay": False},
    {"name": "Positive Reference 1.0", "data": [1.0 if use_layer_3 else None] * n, "color": "rgba(128,128,128,0.25)", "type": "line", "style": "dotted", "lineWidth": 1, "overlay": False},
    {"name": "Negative Reference 1.0", "data": [-1.0 if use_layer_3 else None] * n, "color": "rgba(128,128,128,0.25)", "type": "line", "style": "dotted", "lineWidth": 1, "overlay": False},
]

signals = [
    {"type": "buy", "name": "Qualified Long", "text": "Q+", "data": [float(base["momentum"][index]) if base["qualifiedRelease"][index] and base["qualifiedDirection"][index] > 0 and np.isfinite(base["momentum"][index]) else None for index in range(n)], "color": colors["long"], "renderMode": "events"},
    {"type": "sell", "name": "Qualified Short", "text": "Q-", "data": [float(base["momentum"][index]) if base["qualifiedRelease"][index] and base["qualifiedDirection"][index] < 0 and np.isfinite(base["momentum"][index]) else None for index in range(n)], "color": colors["short"], "renderMode": "events"},
]
if show_raw_release:
    signals.append({"type": "buy", "name": "Unqualified Release", "text": "R", "data": [float(base["momentum"][index]) if base["rawRelease"][index] and not base["qualifiedRelease"][index] and np.isfinite(base["momentum"][index]) else None for index in range(n)], "color": "rgba(158,158,158,0.60)", "renderMode": "events"})

layers = []
if enable_htf and not htf_active and n:
    layers.append({"type": "label", "role": "HTF note", "time": float(base_time[-1] * 1000.0), "price": 0.0, "text": f"HTF wash off: {selected_htf} is not above {chart_timeframe}", "color": "rgba(128,128,128,0.70)", "textColor": "#FFFFFF"})

tables = []
if show_table:
    tables = [{"title": "Modern Squeeze", "position": "top-right", "compact": True, "rows": [
        [{"text": "Squeezes", "color": "#BDBDBD"}, {"text": str(n_squeezes), "color": "#FFFFFF"}],
        [{"text": "Raw / qualified", "color": "#BDBDBD"}, {"text": f"{n_raw} / {n_qualified}", "color": "#FFFFFF"}],
        [{"text": f"Hit rate @{statistics_horizon}", "color": "#BDBDBD"}, {"text": "-" if not np.isfinite(hit_rate) else f"{hit_rate:.1f}% ({n_scored})", "color": "#FFFFFF"}],
        [{"text": f"Range multiple @{statistics_horizon}", "color": "#BDBDBD"}, {"text": "-" if not np.isfinite(range_multiple) else f"{range_multiple:.2f}x", "color": "#FFFFFF"}],
        [{"text": f"HTF {selected_htf}", "color": "#BDBDBD"}, {"text": htf_status, "color": "#FFFFFF"}],
        [{"text": "Loaded-bar statistics · no costs", "color": "#9E9E9E"}, {"text": "", "color": "#9E9E9E"}],
    ]}]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": background_colors,
    "calculatedVars": {
        "periodRaw": _finite(base["periodRaw"]), "periodSmoothed": _finite(base["periodSmoothed"]), "adaptiveLength": base["length"].tolist(),
        "basis": _finite(base["basis"]), "upperBB": _finite(base["upperBB"]), "lowerBB": _finite(base["lowerBB"]),
        "keltnerRange": _finite(base["keltnerRange"]), "upperKC": _finite(base["upperKC"]), "lowerKC": _finite(base["lowerKC"]),
        "classicSqueeze": base["classicSqueeze"].tolist(), "midpoint": _finite(base["midpoint"]), "momentumRaw": _finite(base["momentumRaw"]),
        "compressionRatio": _finite(base["ratio"]), "percentRank": _finite(base["percentRank"]), "grade": base["grade"].tolist(),
        "squeezeOn": base["squeezeOn"].tolist(), "squeezeBars": base["squeezeBars"].tolist(), "momentum": _finite(base["momentum"]),
        "rawRelease": base["rawRelease"].tolist(), "qualifiedRelease": base["qualifiedRelease"].tolist(),
        "qualifiedDirection": base["qualifiedDirection"].tolist(), "momentumDirection": base["direction"].tolist(), "squeezeStart": base["squeezeStart"].tolist(),
        "htfTimeframe": selected_htf, "htfActive": bool(htf_active), "htfAvailable": bool(htf_available), "htfSqueezeOn": htf_on.tolist(), "htfGrade": htf_grade.tolist(),
        "atr14": _finite(atr14), "forwardRange": _finite(forward_range), "squeezeCount": n_squeezes, "rawReleaseCount": n_raw,
        "qualifiedReleaseCount": n_qualified, "scoredCount": n_scored, "hitCount": n_hit,
        "hitRate": float(hit_rate) if np.isfinite(hit_rate) else None, "rangeMultiple": float(range_multiple) if np.isfinite(range_multiple) else None,
        "classicMode": bool(classic_mode), "useAdaptiveLength": bool(use_layer_1), "usePercentileRanking": bool(use_layer_2),
        "useNormalizedMomentum": bool(use_layer_3), "useQualifiedRelease": bool(use_layer_4), "palette": palette,
    },
}
