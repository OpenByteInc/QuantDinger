# ============================================================
# QuantDinger Strong Contrarian Zones
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Strong Contrarian Zones"
my_indicator_description = (
    "Scores contrarian swing zones from momentum, volatility, volume, wick, pressure, "
    "and divergence evidence, then tracks retests, sweeps, reclaims, breaks, flips, and decay."
)

# @param zone_side str Both Zone side values=Both,Bullish Only,Bearish Only
# @param show_labels bool false Show in-zone labels
# @param label_size str Normal Label size values=Tiny,Small,Normal,Large
# @param lookback_bars int 2500 Lookback bars, zero means all range=0:10000:50
# @param max_zones int 30 Maximum live zones range=2:120:1
# @param hide_overlap bool true Hide overlapping zones
# @param overlap_threshold int 25 Overlap threshold percent range=1:100:1
# @param pivot_left int 10 Pivot left bars range=1:100:1
# @param pivot_right int 3 Pivot right bars range=1:100:1
# @param max_life int 700 Maximum zone life range=10:5000:10
# @param normalize_height bool true Normalize zone height
# @param height_method str ATR_Based Zone height method values=ATR_Based,Fixed_Percentage
# @param height_atr float 0.6 Zone height ATR multiplier range=0.05:10.0:0.05
# @param height_percent float 0.3 Zone height percent range=0.01:5.0:0.05
# @param atr_length int 200 ATR length range=1:500:1
# @param min_conviction float 4.5 Minimum birth conviction range=0.0:10.0:0.5
# @param rsi_length int 14 RSI length range=2:100:1
# @param rsi_buy_reference int 42 Bullish RSI reference range=5:50:1
# @param rsi_sell_reference int 58 Bearish RSI reference range=50:95:1
# @param z_length int 50 Mean-reversion length range=5:400:1
# @param z_reference float 2.2 Stretch reference range=0.5:6.0:0.1
# @param bollinger_length int 20 Bollinger length range=2:200:1
# @param bollinger_deviation float 2.0 Bollinger deviation range=0.5:5.0:0.1
# @param band_reference float 0.5 Band-pierce ATR reference range=0.05:4.0:0.05
# @param volume_length int 20 Volume baseline length range=1:200:1
# @param volume_climax float 2.2 Volume climax reference range=1.1:8.0:0.1
# @param wick_reference float 0.45 Rejection wick reference range=0.1:1.0:0.05
# @param pressure_length int 20 Pressure lookback range=2:200:1
# @param pressure_reference float 18.0 Pressure reference range=2.0:50.0:1.0
# @param divergence_reference float 8.0 Divergence reference range=1.0:40.0:1.0
# @param weight_rsi float 1.0 RSI conviction weight range=0.0:3.0:0.1
# @param weight_divergence float 1.3 Divergence conviction weight range=0.0:3.0:0.1
# @param weight_wick float 1.0 Wick conviction weight range=0.0:3.0:0.1
# @param weight_volume float 1.0 Volume conviction weight range=0.0:3.0:0.1
# @param weight_stretch float 1.0 Stretch conviction weight range=0.0:3.0:0.1
# @param weight_pressure float 0.9 Pressure conviction weight range=0.0:3.0:0.1
# @param weight_band float 0.9 Band conviction weight range=0.0:3.0:0.1
# @param reclaim_alerts bool true Enable reclaim events
# @param minimum_sweep_depth float 0.15 Minimum sweep depth ATR range=0.02:4.0:0.01
# @param reclaim_window int 6 Maximum reclaim window range=1:40:1
# @param reclaim_confirmation str Border Reclaim confirmation values=Border,Midline,Far Border
# @param break_acceptance float 0.9 Break acceptance ATR range=0.1:8.0:0.1
# @param flip_broken bool true Flip broken zones
# @param only_strong_reclaims bool false Only strong reclaim events
# @param strong_reclaim_threshold float 6.5 Strong reclaim threshold range=0.0:10.0:0.5
# @param show_reclaim_triggers bool false Show reclaim triggers
# @param depth_reference float 1.0 Trigger depth reference range=0.2:6.0:0.1
# @param displacement_reference float 0.6 Trigger displacement reference range=0.1:4.0:0.1
# @param surge_reference float 2.0 Trigger volume reference range=1.1:8.0:0.1
# @param trigger_weight_depth float 1.0 Trigger depth weight range=0.0:3.0:0.1
# @param trigger_weight_speed float 1.0 Trigger speed weight range=0.0:3.0:0.1
# @param trigger_weight_wick float 1.0 Trigger wick weight range=0.0:3.0:0.1
# @param trigger_weight_volume float 1.0 Trigger volume weight range=0.0:3.0:0.1
# @param trigger_weight_conviction float 1.2 Trigger conviction weight range=0.0:3.0:0.1
# @param trigger_weight_displacement float 1.0 Trigger displacement weight range=0.0:3.0:0.1
# @param only_strong_zones bool false Show only strong zones
# @param strong_zone_threshold float 5.0 Strong zone threshold range=0.0:10.0:0.5
# @param full_retests float 4.0 Retests for full score range=1.0:20.0:0.5
# @param full_defends float 2.0 Defends for full score range=1.0:20.0:0.5
# @param score_weight_conviction float 0.35 Live conviction weight range=0.0:1.0:0.05
# @param score_weight_retests float 0.25 Live retest weight range=0.0:1.0:0.05
# @param score_weight_defends float 0.25 Live defense weight range=0.0:1.0:0.05
# @param score_weight_wick float 0.075 Live wick weight range=0.0:1.0:0.025
# @param score_weight_volume float 0.075 Live volume weight range=0.0:1.0:0.025
# @param use_decay bool true Decay idle zones
# @param decay_bars int 300 Full decay bars range=20:5000:10
# @param decay_floor float 0.65 Decay floor range=0.1:1.0:0.05
# @param bullish_color str #21C997 Bullish zone color
# @param bearish_color str #CC24E2 Bearish zone color
# @param gradient_fill bool true Use triple gradient fill
# @param fill_transparency int 84 Zone transparency range=50:96:1
# @param show_borders bool true Show zone borders
# @param border_transparency int 35 Border transparency range=0:100:1
# @param show_midline bool true Show zone midline
# @param label_placement str Outside_Right Label placement values=Outside_Right,Inside_Right,Inside_Center
# @param label_gap int 3 Label gap bars range=0:50:1
# @param max_labels int 4 Maximum visible zone labels range=0:30:1
# @param label_background_transparency int 22 Label background transparency range=0:100:1
# @param label_text_color str #F2F7FF Label text color
# @param proximity_percent float 0.1 Proximity percent range=0.0:5.0:0.01
# @param institutional_threshold float 7.0 Institutional score threshold range=0.0:10.0:0.5

def bounded_int(name, default, lower, upper):
    return min(upper, max(lower, int(params.get(name, default))))

def bounded_float(name, default, lower, upper):
    return min(upper, max(lower, float(params.get(name, default))))

zone_side = str(params.get("zone_side", "Both"))
show_labels = bool(params.get("show_labels", False))
label_size = str(params.get("label_size", "Normal"))
lookback_bars = bounded_int("lookback_bars", 2500, 0, 10000)
max_zones = bounded_int("max_zones", 30, 2, 120)
hide_overlap = bool(params.get("hide_overlap", True))
overlap_threshold = bounded_int("overlap_threshold", 25, 1, 100)
pivot_left = bounded_int("pivot_left", 10, 1, 100)
pivot_right = bounded_int("pivot_right", 3, 1, 100)
max_life = bounded_int("max_life", 700, 10, 5000)
normalize_height = bool(params.get("normalize_height", True))
height_method = str(params.get("height_method", "ATR_Based"))
height_atr = bounded_float("height_atr", 0.6, 0.05, 10.0)
height_percent = bounded_float("height_percent", 0.3, 0.01, 5.0)
atr_length = bounded_int("atr_length", 200, 1, 500)
min_conviction = bounded_float("min_conviction", 4.5, 0.0, 10.0)
rsi_length = bounded_int("rsi_length", 14, 2, 100)
rsi_buy_reference = bounded_int("rsi_buy_reference", 42, 5, 50)
rsi_sell_reference = bounded_int("rsi_sell_reference", 58, 50, 95)
z_length = bounded_int("z_length", 50, 5, 400)
z_reference = bounded_float("z_reference", 2.2, 0.5, 6.0)
bollinger_length = bounded_int("bollinger_length", 20, 2, 200)
bollinger_deviation = bounded_float("bollinger_deviation", 2.0, 0.5, 5.0)
band_reference = bounded_float("band_reference", 0.5, 0.05, 4.0)
volume_length = bounded_int("volume_length", 20, 1, 200)
volume_climax = bounded_float("volume_climax", 2.2, 1.1, 8.0)
wick_reference = bounded_float("wick_reference", 0.45, 0.1, 1.0)
pressure_length = bounded_int("pressure_length", 20, 2, 200)
pressure_reference = bounded_float("pressure_reference", 18.0, 2.0, 50.0)
divergence_reference = bounded_float("divergence_reference", 8.0, 1.0, 40.0)
weight_rsi = bounded_float("weight_rsi", 1.0, 0.0, 3.0)
weight_divergence = bounded_float("weight_divergence", 1.3, 0.0, 3.0)
weight_wick = bounded_float("weight_wick", 1.0, 0.0, 3.0)
weight_volume = bounded_float("weight_volume", 1.0, 0.0, 3.0)
weight_stretch = bounded_float("weight_stretch", 1.0, 0.0, 3.0)
weight_pressure = bounded_float("weight_pressure", 0.9, 0.0, 3.0)
weight_band = bounded_float("weight_band", 0.9, 0.0, 3.0)
reclaim_alerts = bool(params.get("reclaim_alerts", True))
minimum_sweep_depth = bounded_float("minimum_sweep_depth", 0.15, 0.02, 4.0)
reclaim_window = bounded_int("reclaim_window", 6, 1, 40)
reclaim_confirmation = str(params.get("reclaim_confirmation", "Border"))
break_acceptance = bounded_float("break_acceptance", 0.9, 0.1, 8.0)
flip_broken = bool(params.get("flip_broken", True))
only_strong_reclaims = bool(params.get("only_strong_reclaims", False))
strong_reclaim_threshold = bounded_float("strong_reclaim_threshold", 6.5, 0.0, 10.0)
show_reclaim_triggers = bool(params.get("show_reclaim_triggers", False))
depth_reference = bounded_float("depth_reference", 1.0, 0.2, 6.0)
displacement_reference = bounded_float("displacement_reference", 0.6, 0.1, 4.0)
surge_reference = bounded_float("surge_reference", 2.0, 1.1, 8.0)
trigger_weight_depth = bounded_float("trigger_weight_depth", 1.0, 0.0, 3.0)
trigger_weight_speed = bounded_float("trigger_weight_speed", 1.0, 0.0, 3.0)
trigger_weight_wick = bounded_float("trigger_weight_wick", 1.0, 0.0, 3.0)
trigger_weight_volume = bounded_float("trigger_weight_volume", 1.0, 0.0, 3.0)
trigger_weight_conviction = bounded_float("trigger_weight_conviction", 1.2, 0.0, 3.0)
trigger_weight_displacement = bounded_float("trigger_weight_displacement", 1.0, 0.0, 3.0)
only_strong_zones = bool(params.get("only_strong_zones", False))
strong_zone_threshold = bounded_float("strong_zone_threshold", 5.0, 0.0, 10.0)
full_retests = bounded_float("full_retests", 4.0, 1.0, 20.0)
full_defends = bounded_float("full_defends", 2.0, 1.0, 20.0)
score_weight_conviction = bounded_float("score_weight_conviction", 0.35, 0.0, 1.0)
score_weight_retests = bounded_float("score_weight_retests", 0.25, 0.0, 1.0)
score_weight_defends = bounded_float("score_weight_defends", 0.25, 0.0, 1.0)
score_weight_wick = bounded_float("score_weight_wick", 0.075, 0.0, 1.0)
score_weight_volume = bounded_float("score_weight_volume", 0.075, 0.0, 1.0)
use_decay = bool(params.get("use_decay", True))
decay_bars = bounded_int("decay_bars", 300, 20, 5000)
decay_floor = bounded_float("decay_floor", 0.65, 0.1, 1.0)
bullish_color = str(params.get("bullish_color", "#21C997"))
bearish_color = str(params.get("bearish_color", "#CC24E2"))
gradient_fill = bool(params.get("gradient_fill", True))
fill_transparency = bounded_int("fill_transparency", 84, 50, 96)
show_borders = bool(params.get("show_borders", True))
border_transparency = bounded_int("border_transparency", 35, 0, 100)
show_midline = bool(params.get("show_midline", True))
label_placement = str(params.get("label_placement", "Outside_Right"))
label_gap = bounded_int("label_gap", 3, 0, 50)
max_labels = bounded_int("max_labels", 4, 0, 30)
label_background_transparency = bounded_int("label_background_transparency", 22, 0, 100)
label_text_color = str(params.get("label_text_color", "#F2F7FF"))
proximity_percent = bounded_float("proximity_percent", 0.1, 0.0, 5.0)
institutional_threshold = bounded_float("institutional_threshold", 7.0, 0.0, 10.0)

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)
epsilon = 1e-10

def clamp01(value):
    return min(1.0, max(0.0, float(value)))

def color_with_opacity(color, opacity):
    value = str(color).strip()
    alpha = clamp01(opacity)
    if value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            red, green, blue = (int(value[position] * 2, 16) for position in range(1, 4))
        else:
            red, green, blue = int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{alpha:.3f})"
    if value.lower().startswith("rgba("):
        parts = value[value.find("(") + 1:value.rfind(")")].split(",")
        if len(parts) >= 3:
            return f"rgba({parts[0].strip()},{parts[1].strip()},{parts[2].strip()},{alpha:.3f})"
    return value

def rolling_mean(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    running = 0.0
    for index, value in enumerate(values):
        running += float(value)
        if index >= length:
            running -= float(values[index - length])
        if index >= length - 1:
            result[index] = running / length
    return result

def rolling_std(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        result[index] = float(np.std(values[index - length + 1:index + 1], ddof=0))
    return result

def rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    for index in range(length, len(values)):
        result[index] = (result[index - 1] * (length - 1) + float(values[index])) / length
    return result

true_range = np.zeros(n, dtype=float)
for index in range(n):
    true_range[index] = high[index] - low[index] if index == 0 else max(
        high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1])
    )
atr = rma(true_range, atr_length)

changes = np.zeros(n, dtype=float)
if n > 1:
    changes[1:] = close[1:] - close[:-1]
gains = rma(np.maximum(changes, 0.0), rsi_length)
losses = rma(np.maximum(-changes, 0.0), rsi_length)
rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.isfinite(gains[index]) and np.isfinite(losses[index]):
        if losses[index] <= epsilon:
            rsi[index] = 100.0 if gains[index] > epsilon else 50.0
        else:
            rsi[index] = 100.0 - 100.0 / (1.0 + gains[index] / losses[index])

z_basis = rolling_mean(close, z_length)
z_deviation = rolling_std(close, z_length)
z_score = np.full(n, np.nan, dtype=float)
valid_z = np.isfinite(z_basis) & np.isfinite(z_deviation)
z_score[valid_z] = (close[valid_z] - z_basis[valid_z]) / np.maximum(z_deviation[valid_z], epsilon)

band_basis = rolling_mean(close, bollinger_length)
band_dev = rolling_std(close, bollinger_length) * bollinger_deviation
band_upper = band_basis + band_dev
band_lower = band_basis - band_dev
volume_base = rolling_mean(volume, volume_length)
relative_volume = np.full(n, np.nan, dtype=float)
volume_valid = np.isfinite(volume_base) & (volume_base > epsilon)
relative_volume[volume_valid] = volume[volume_valid] / volume_base[volume_valid]

range_value = np.maximum(high - low, epsilon)
buy_volume = volume * (close - low) / range_value
sell_volume = volume * (high - close) / range_value
rolling_buy = rolling_mean(buy_volume, pressure_length) * pressure_length
rolling_sell = rolling_mean(sell_volume, pressure_length) * pressure_length
buy_percent = np.full(n, 50.0, dtype=float)
pressure_total = rolling_buy + rolling_sell
pressure_valid = np.isfinite(pressure_total) & (pressure_total > epsilon)
buy_percent[pressure_valid] = rolling_buy[pressure_valid] / pressure_total[pressure_valid] * 100.0

def pivot_at(values, confirmation_index, left, right, high_pivot):
    origin = confirmation_index - right
    if origin < left or confirmation_index >= len(values):
        return None
    window = values[origin - left:confirmation_index + 1]
    if len(window) != left + right + 1 or not np.all(np.isfinite(window)):
        return None
    candidate = float(values[origin])
    extreme = float(np.max(window) if high_pivot else np.min(window))
    if candidate != extreme:
        return None
    return origin, candidate

def overlap(first_top, first_bottom, second_top, second_bottom):
    overlap_size = min(first_top, second_top) - max(first_bottom, second_bottom)
    minimum_height = min(first_top - first_bottom, second_top - second_bottom)
    return overlap_size > 0.0 and minimum_height > 0.0 and overlap_size / minimum_height >= overlap_threshold / 100.0

def conviction_text(scores):
    names = ("RSI", "DIV", "WICK", "VOL", "STR", "PRS", "BB")
    active = [name for name, score in zip(names, scores) if score >= 0.5]
    return " ".join(active) if active else "-"

def trigger_score(depth_atr, bars_used, wick_fraction, volume_surge, conviction, displacement_atr):
    components = (
        clamp01(depth_atr / depth_reference),
        clamp01((reclaim_window - bars_used + 1.0) / reclaim_window),
        clamp01(wick_fraction),
        clamp01((volume_surge - 1.0) / max(surge_reference - 1.0, 0.1)),
        clamp01(conviction / 10.0),
        clamp01(displacement_atr / displacement_reference),
    )
    weights = (
        trigger_weight_depth,
        trigger_weight_speed,
        trigger_weight_wick,
        trigger_weight_volume,
        trigger_weight_conviction,
        trigger_weight_displacement,
    )
    total = sum(weights)
    return min(10.0, max(0.0, 10.0 * sum(weight * value for weight, value in zip(weights, components)) / total)) if total > 0.0 else 0.0

def zone_score(zone, index):
    retest_factor = min(zone["touches"] / full_retests, 1.0)
    defend_factor = min(zone["defends"] / full_defends, 1.0)
    atr_value = atr[index] if np.isfinite(atr[index]) else epsilon
    wick_factor = min((zone["wick_sum"] / zone["wick_count"]) / max(atr_value, epsilon), 1.0) if zone["wick_count"] else 0.0
    base_volume = volume_base[index] if np.isfinite(volume_base[index]) else 0.0
    volume_factor = min((zone["volume_sum"] / zone["volume_count"]) / base_volume, 1.0) if zone["volume_count"] and base_volume > epsilon else 0.0
    weights = (score_weight_conviction, score_weight_retests, score_weight_defends, score_weight_wick, score_weight_volume)
    factors = (zone["conviction"] / 10.0, retest_factor, defend_factor, wick_factor, volume_factor)
    total = sum(weights)
    raw = sum(weight * factor for weight, factor in zip(weights, factors)) / total if total > 0.0 else 0.0
    age_factor = 1.0
    if use_decay:
        age_factor = max(1.0 - (1.0 - decay_floor) * min((index - zone["last_bar"]) / max(decay_bars, 1), 1.0), decay_floor)
    return min(10.0, max(0.0, raw * 10.0 * age_factor))

zones = []
reclaim_bull = [None for _ in range(n)]
reclaim_bear = [None for _ in range(n)]
event_counts = {"touch": 0, "sweep": 0, "break": 0, "reclaim_bull": 0, "reclaim_bear": 0}
last_low_price = np.nan
last_low_rsi = np.nan
last_high_price = np.nan
last_high_rsi = np.nan

for index in range(n):
    atr_value = float(atr[index]) if np.isfinite(atr[index]) else max(float(high[index] - low[index]), epsilon)
    height_value = atr_value * height_atr if height_method in ("ATR_Based", "ATR Based") else float(close[index]) * height_percent / 100.0
    in_window = lookback_bars <= 0 or (n - 1 - index) <= lookback_bars

    for high_pivot in (False, True):
        pivot = pivot_at(high if high_pivot else low, index, pivot_left, pivot_right, high_pivot)
        if pivot is None:
            continue
        origin, pivot_price = pivot
        origin_rsi = float(rsi[origin]) if np.isfinite(rsi[origin]) else 50.0
        origin_range = max(float(high[origin] - low[origin]), epsilon)
        origin_rel_volume = float(relative_volume[origin]) if np.isfinite(relative_volume[origin]) else 1.0
        origin_z = float(z_score[origin]) if np.isfinite(z_score[origin]) else 0.0
        origin_buy_percent = float(buy_percent[origin])
        if not high_pivot:
            divergence = np.isfinite(last_low_price) and pivot_price < last_low_price and origin_rsi > last_low_rsi
            divergence_score = clamp01((origin_rsi - last_low_rsi) / divergence_reference) if divergence else 0.0
            rsi_score = clamp01((rsi_buy_reference - origin_rsi) / max(rsi_buy_reference, 1))
            stretch_score = clamp01(-origin_z / z_reference)
            volume_score = clamp01((origin_rel_volume - 1.0) / max(volume_climax - 1.0, 0.1))
            wick_size = max(float(min(open_price[origin], close[origin]) - low[origin]), 0.0)
            wick_score = clamp01((wick_size / origin_range) / wick_reference)
            pressure_score = clamp01((origin_buy_percent - 50.0) / pressure_reference)
            lower_band = float(band_lower[origin]) if np.isfinite(band_lower[origin]) else pivot_price
            band_score = clamp01((lower_band - pivot_price) / max(band_reference * (atr[origin] if np.isfinite(atr[origin]) else atr_value), epsilon))
            direction = 1
            allowed = zone_side in ("Both", "Bullish Only")
            last_low_price, last_low_rsi = pivot_price, origin_rsi
        else:
            divergence = np.isfinite(last_high_price) and pivot_price > last_high_price and origin_rsi < last_high_rsi
            divergence_score = clamp01((last_high_rsi - origin_rsi) / divergence_reference) if divergence else 0.0
            rsi_score = clamp01((origin_rsi - rsi_sell_reference) / max(100 - rsi_sell_reference, 1))
            stretch_score = clamp01(origin_z / z_reference)
            volume_score = clamp01((origin_rel_volume - 1.0) / max(volume_climax - 1.0, 0.1))
            wick_size = max(float(high[origin] - max(open_price[origin], close[origin])), 0.0)
            wick_score = clamp01((wick_size / origin_range) / wick_reference)
            pressure_score = clamp01((50.0 - origin_buy_percent) / pressure_reference)
            upper_band = float(band_upper[origin]) if np.isfinite(band_upper[origin]) else pivot_price
            band_score = clamp01((pivot_price - upper_band) / max(band_reference * (atr[origin] if np.isfinite(atr[origin]) else atr_value), epsilon))
            direction = -1
            allowed = zone_side in ("Both", "Bearish Only")
            last_high_price, last_high_rsi = pivot_price, origin_rsi

        scores = (rsi_score, divergence_score, wick_score, volume_score, stretch_score, pressure_score, band_score)
        weights = (weight_rsi, weight_divergence, weight_wick, weight_volume, weight_stretch, weight_pressure, weight_band)
        weight_total = sum(weights)
        conviction = 10.0 * sum(weight * score for weight, score in zip(weights, scores)) / weight_total if weight_total > 0.0 else 0.0
        if not allowed or not in_window or conviction < min_conviction:
            continue

        if normalize_height:
            top = pivot_price + height_value / 2.0
            bottom = pivot_price - height_value / 2.0
        elif direction == 1:
            top, bottom = pivot_price + height_value, pivot_price
        else:
            top, bottom = pivot_price, pivot_price - height_value
        if hide_overlap and any(overlap(top, bottom, zone["top"], zone["bottom"]) for zone in zones if not zone["dead"] and zone["phase"] != 1):
            continue
        zones.append(
            {
                "direction": direction,
                "top": float(top),
                "bottom": float(bottom),
                "mid": float((top + bottom) / 2.0),
                "anchor": float(pivot_price),
                "born_bar": int(origin),
                "last_bar": int(origin),
                "touches": 0,
                "defends": 0,
                "phase": 0,
                "sweep_bar": None,
                "sweep_extreme": None,
                "conviction": float(conviction),
                "conviction_text": conviction_text(scores),
                "volume_sum": float(volume[origin]),
                "volume_count": 1,
                "wick_sum": float(wick_size),
                "wick_count": 1,
                "trigger": 0.0,
                "state": 0,
                "score": float(conviction),
                "end_bar": int(index),
                "dead": False,
            }
        )
        while len([zone for zone in zones if not zone["dead"]]) > max_zones:
            oldest = next((zone for zone in zones if not zone["dead"]), None)
            if oldest is None:
                break
            oldest["dead"] = True

    reclaim_depth = 1.0 if reclaim_confirmation == "Far Border" else 0.5 if reclaim_confirmation == "Midline" else 0.0
    for zone in zones:
        if zone["dead"]:
            continue
        zone["end_bar"] = index
        if index - zone["born_bar"] >= max_life:
            zone["dead"] = True
            continue

        proximity = abs(zone["anchor"]) * proximity_percent / 100.0

        if zone["direction"] == 1:
            if zone["phase"] == 0:
                if low[index] <= zone["top"] + proximity and low[index] >= zone["bottom"] - proximity and close[index] > zone["bottom"] - proximity:
                    zone["touches"] += 1
                    zone["wick_sum"] += max(zone["top"] - float(low[index]), 0.0)
                    zone["wick_count"] += 1
                    zone["volume_sum"] += float(volume[index])
                    zone["volume_count"] += 1
                    zone["last_bar"] = index
                    zone["state"] = 0
                    event_counts["touch"] += 1
                elif low[index] < zone["bottom"] - minimum_sweep_depth * atr_value:
                    zone["phase"] = 1
                    zone["sweep_bar"] = index
                    zone["sweep_extreme"] = float(low[index])
                    zone["state"] = 1
                    event_counts["sweep"] += 1
            else:
                zone["sweep_extreme"] = min(zone["sweep_extreme"], float(low[index]))
                reclaim_level = zone["bottom"] + reclaim_depth * (zone["top"] - zone["bottom"])
                accepted = close[index] < zone["bottom"] - break_acceptance * atr_value or (index - zone["sweep_bar"] >= reclaim_window and close[index] < zone["bottom"])
                if close[index] > reclaim_level:
                    score = trigger_score(
                        (zone["bottom"] - zone["sweep_extreme"]) / atr_value,
                        index - zone["sweep_bar"],
                        (min(open_price[index], close[index]) - low[index]) / range_value[index],
                        volume[index] / max(volume_base[index] if np.isfinite(volume_base[index]) else volume[index], 1.0),
                        zone["conviction"],
                        (close[index] - zone["bottom"]) / atr_value,
                    )
                    if reclaim_alerts and (not only_strong_reclaims or score >= strong_reclaim_threshold):
                        reclaim_bull[index] = {"value": float(low[index]), "text": "RECLAIM", "color": bullish_color}
                        event_counts["reclaim_bull"] += 1
                    zone["defends"] += 1
                    zone["volume_sum"] += float(volume[index])
                    zone["volume_count"] += 1
                    zone["trigger"] = score
                    zone["last_bar"] = index
                    zone["phase"] = 0
                    zone["state"] = 2
                elif accepted:
                    event_counts["break"] += 1
                    if flip_broken:
                        zone.update({"direction": -1, "touches": 0, "defends": 0, "wick_sum": 0.0, "wick_count": 0, "phase": 0, "state": 3, "last_bar": index})
                    else:
                        zone["dead"] = True
        else:
            if zone["phase"] == 0:
                if high[index] >= zone["bottom"] - proximity and high[index] <= zone["top"] + proximity and close[index] < zone["top"] + proximity:
                    zone["touches"] += 1
                    zone["wick_sum"] += max(float(high[index]) - zone["bottom"], 0.0)
                    zone["wick_count"] += 1
                    zone["volume_sum"] += float(volume[index])
                    zone["volume_count"] += 1
                    zone["last_bar"] = index
                    zone["state"] = 0
                    event_counts["touch"] += 1
                elif high[index] > zone["top"] + minimum_sweep_depth * atr_value:
                    zone["phase"] = 1
                    zone["sweep_bar"] = index
                    zone["sweep_extreme"] = float(high[index])
                    zone["state"] = 1
                    event_counts["sweep"] += 1
            else:
                zone["sweep_extreme"] = max(zone["sweep_extreme"], float(high[index]))
                reclaim_level = zone["top"] - reclaim_depth * (zone["top"] - zone["bottom"])
                accepted = close[index] > zone["top"] + break_acceptance * atr_value or (index - zone["sweep_bar"] >= reclaim_window and close[index] > zone["top"])
                if close[index] < reclaim_level:
                    score = trigger_score(
                        (zone["sweep_extreme"] - zone["top"]) / atr_value,
                        index - zone["sweep_bar"],
                        (high[index] - max(open_price[index], close[index])) / range_value[index],
                        volume[index] / max(volume_base[index] if np.isfinite(volume_base[index]) else volume[index], 1.0),
                        zone["conviction"],
                        (zone["top"] - close[index]) / atr_value,
                    )
                    if reclaim_alerts and (not only_strong_reclaims or score >= strong_reclaim_threshold):
                        reclaim_bear[index] = {"value": float(high[index]), "text": "RECLAIM", "color": bearish_color}
                        event_counts["reclaim_bear"] += 1
                    zone["defends"] += 1
                    zone["volume_sum"] += float(volume[index])
                    zone["volume_count"] += 1
                    zone["trigger"] = score
                    zone["last_bar"] = index
                    zone["phase"] = 0
                    zone["state"] = 2
                elif accepted:
                    event_counts["break"] += 1
                    if flip_broken:
                        zone.update({"direction": 1, "touches": 0, "defends": 0, "wick_sum": 0.0, "wick_count": 0, "phase": 0, "state": 3, "last_bar": index})
                    else:
                        zone["dead"] = True
        if not zone["dead"]:
            zone["score"] = zone_score(zone, index)

layers = []
label_scale = {"Tiny": 9, "Small": 10, "Normal": 12, "Large": 14}.get(label_size, 12)
visible_zones = []
for zone in [item for item in zones if not item["dead"]]:
    visible = not only_strong_zones or zone["score"] >= strong_zone_threshold or zone["phase"] == 1 or zone["state"] == 2
    if not visible:
        continue
    visible_zones.append(zone)

label_candidates = sorted(
    visible_zones,
    key=lambda item: (item["state"] == 2, item["phase"] == 1, item["score"], item["last_bar"], item["born_bar"]),
    reverse=True,
)
latest_atr = float(atr[-1]) if n and np.isfinite(atr[-1]) else 0.0
minimum_label_gap = max(latest_atr * 2.5, abs(float(close[-1])) * 0.02) if n else 0.0
selected_label_zones = []
for candidate in label_candidates:
    if all(abs(candidate["mid"] - selected["mid"]) >= minimum_label_gap for selected in selected_label_zones):
        selected_label_zones.append(candidate)
    if len(selected_label_zones) >= max_labels:
        break
label_keys = {(zone["born_bar"], zone["direction"]) for zone in selected_label_zones}

for zone in visible_zones:
    color = bullish_color if zone["direction"] == 1 else bearish_color
    rounded_score = int(round(zone["score"]))
    outer_transparency = max(fill_transparency - 2, 62) if gradient_fill else fill_transparency
    mid_transparency = max(fill_transparency - 12 - rounded_score, 50) if gradient_fill else fill_transparency
    core_transparency = max(fill_transparency - 24 - rounded_score * 2, 36) if gradient_fill else fill_transparency
    common = {"startIndex": int(zone["born_bar"]), "endIndex": int(zone["end_bar"]), "extendRight": False}
    border_color = color_with_opacity(color, (100 - border_transparency) / 100.0) if show_borders else "rgba(0,0,0,0)"
    layers.append({"type": "zone", **common, "top": float(zone["top"]), "bottom": float(zone["bottom"]), "fillColor": color, "opacity": (100 - outer_transparency) / 100.0, "borderColor": border_color, "borderSize": 1})
    if gradient_fill:
        half = (zone["top"] - zone["mid"]) * 0.5
        core = (zone["top"] - zone["mid"]) * 0.22
        layers.append({"type": "zone", **common, "top": float(zone["mid"] + half), "bottom": float(zone["mid"] - half), "fillColor": color, "opacity": (100 - mid_transparency) / 100.0, "borderColor": "rgba(0,0,0,0)", "borderSize": 0})
        layers.append({"type": "zone", **common, "top": float(zone["mid"] + core), "bottom": float(zone["mid"] - core), "fillColor": color, "opacity": (100 - core_transparency) / 100.0, "borderColor": "rgba(0,0,0,0)", "borderSize": 0})
    if show_midline:
        layers.append({"type": "line", "startIndex": int(zone["born_bar"]), "endIndex": int(zone["end_bar"]), "y1": float(zone["mid"]), "y2": float(zone["mid"]), "color": color, "lineWidth": 1, "dashed": True})
    if show_labels and (zone["born_bar"], zone["direction"]) in label_keys:
        direction_text = "BUY-DIP" if zone["direction"] == 1 else "SELL-RIP"
        stars = "*" * min(5, max(0, int(zone["score"] // 1.5))) or "."
        tier = "INSTITUTIONAL" if zone["score"] >= institutional_threshold else "STRONG" if zone["score"] >= 6.5 else "PRIMED" if zone["score"] >= 5.0 else "WEAK" if zone["score"] >= 3.0 else "FORMING"
        state = " SWEEPING" if zone["state"] == 1 else f" RECLAIM {zone['trigger']:.1f}" if zone["state"] == 2 else " BROKEN" if zone["state"] == 3 else ""
        text_value = f"{direction_text} {stars} {zone['score']:.1f} {tier}{state} | C{zone['conviction']:.1f} R{zone['touches']}/D{zone['defends']} | {zone['anchor']:.6g}"
        if label_placement in ("Inside_Center", "Inside Center"):
            label_index = int((zone["born_bar"] + zone["end_bar"]) / 2)
        elif label_placement in ("Outside_Right", "Outside Right"):
            label_index = int(max(0, min(n - 1, zone["end_bar"] + label_gap) - max(6, label_gap + 4)))
        else:
            label_index = int(zone["end_bar"])
        layers.append({"type": "label", "index": label_index, "price": float(zone["mid"]), "text": text_value, "color": color, "textColor": label_text_color, "fontSize": label_scale, "opacity": (100 - label_background_transparency) / 100.0, "side": "center"})

anchor = [float(value) for value in close]
plots = [{"name": "Contrarian Zone Anchor", "title": False, "data": anchor, "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True}]
signals = [
    {"type": "buy", "name": "Bullish Reclaim", "text": "RECLAIM", "data": reclaim_bull if show_reclaim_triggers else [None for _ in range(n)], "color": bullish_color, "renderMode": "events"},
    {"type": "sell", "name": "Bearish Reclaim", "text": "RECLAIM", "data": reclaim_bear if show_reclaim_triggers else [None for _ in range(n)], "color": bearish_color, "renderMode": "events"},
]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers[-500:]}
