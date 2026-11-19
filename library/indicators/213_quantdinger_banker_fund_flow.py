# ============================================================
# QuantDinger Banker Fund Flow
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: analytical signals only; no orders are submitted.
# ============================================================

my_indicator_name = "QuantDinger Banker Fund Flow"
my_indicator_description = (
    "Banker fund-flow oscillator with governed long/short events, optional "
    "higher-timeframe confirmation, ATR risk levels, and a status dashboard."
)

indicator_timeframes = ["1H", "4H", "1D", "1W"]

# @param raw_length int 27 Raw stochastic length range=2:300:1
# @param fast_length int 5 Fast smoothing length range=1:100:1
# @param slow_length int 3 Slow smoothing length range=1:100:1
# @param smooth_weight float 1.0 Smoothing weight range=0.0:20.0:0.5
# @param band_length int 34 Bull/bear band length range=2:300:1
# @param bull_bear_ema int 13 Bull/bear EMA length range=1:200:1
# @param decline_ratio float 0.95 Pullback ratio range=0.5:1.0:0.01
# @param entry_zone float 25.0 Entry zone range=0:100:1
# @param exit_zone float 75.0 Exit zone range=0:100:1
# @param overbought float 80.0 Overbought level range=50:100:1
# @param strong_level float 90.0 Strong level range=50:100:1
# @param oversold float 20.0 Oversold level range=0:50:1
# @param weak_level float 10.0 Weak level range=0:50:1
# @param use_exit_signal bool true Enable governed exit
# @param use_short_entry bool true Enable short entries
# @param allow_reverse bool true Allow stop-and-reverse
# @param cooldown_bars int 3 Signal cooldown bars range=0:100:1
# @param use_zone_entry bool true Enable zone breakout entry
# @param use_os_recovery bool true Enable oversold recovery
# @param use_ob_roll_exit bool true Enable overbought rollover
# @param use_flip_exit bool false Enable trend-line flip exit
# @param use_volume_filter bool true Require volume confirmation
# @param volume_length int 20 Volume average length range=1:300:1
# @param volume_multiplier float 1.0 Volume multiplier range=0.1:10.0:0.1
# @param use_strength_filter bool true Require trend alignment
# @param use_htf_filter bool false Use higher-timeframe filter
# @param htf_timeframe str 4H Higher timeframe values=1H,4H,1D,1W
# @param htf_length int 50 Higher-timeframe EMA length range=1:500:1
# @param use_noise_filter bool false Skip wide-range bars
# @param max_range_atr float 2.5 Maximum range x ATR range=0.5:20.0:0.5
# @param hysteresis float 0.0 Cross hysteresis range=0:25:0.5
# @param noise_smooth_length int 1 Extra smoothing length range=1:100:1
# @param use_atr_stops bool true Enable ATR risk levels
# @param atr_length int 14 ATR length range=1:300:1
# @param take_profit_atr float 3.0 Take-profit x ATR range=0.5:20.0:0.5
# @param stop_loss_atr float 1.5 Stop-loss x ATR range=0.5:20.0:0.5
# @param show_dashboard bool true Show status dashboard
# @param show_background bool true Show bull/bear background
# @param show_labels bool true Show governed events
# @param show_trend_line bool true Show fund-flow trend line
# @param show_risk_levels bool true Show entry, TP, and SL
# @param show_signal_labels bool true Show signal markers

raw_length = min(300, max(2, int(params.get("raw_length", 27))))
fast_length = min(100, max(1, int(params.get("fast_length", 5))))
slow_length = min(100, max(1, int(params.get("slow_length", 3))))
smooth_weight = min(20.0, max(0.0, float(params.get("smooth_weight", 1.0))))
band_length = min(300, max(2, int(params.get("band_length", 34))))
bull_bear_ema = min(200, max(1, int(params.get("bull_bear_ema", 13))))
decline_ratio = min(1.0, max(0.5, float(params.get("decline_ratio", 0.95))))
entry_zone = min(100.0, max(0.0, float(params.get("entry_zone", 25.0))))
exit_zone = min(100.0, max(0.0, float(params.get("exit_zone", 75.0))))
overbought = min(100.0, max(50.0, float(params.get("overbought", 80.0))))
strong_level = min(100.0, max(50.0, float(params.get("strong_level", 90.0))))
oversold = min(50.0, max(0.0, float(params.get("oversold", 20.0))))
weak_level = min(50.0, max(0.0, float(params.get("weak_level", 10.0))))
use_exit_signal = bool(params.get("use_exit_signal", True))
use_short_entry = bool(params.get("use_short_entry", True))
allow_reverse = bool(params.get("allow_reverse", True))
cooldown_bars = min(100, max(0, int(params.get("cooldown_bars", 3))))
use_zone_entry = bool(params.get("use_zone_entry", True))
use_os_recovery = bool(params.get("use_os_recovery", True))
use_ob_roll_exit = bool(params.get("use_ob_roll_exit", True))
use_flip_exit = bool(params.get("use_flip_exit", False))
use_volume_filter = bool(params.get("use_volume_filter", True))
volume_length = min(300, max(1, int(params.get("volume_length", 20))))
volume_multiplier = min(10.0, max(0.1, float(params.get("volume_multiplier", 1.0))))
use_strength_filter = bool(params.get("use_strength_filter", True))
use_htf_filter = bool(params.get("use_htf_filter", False))
htf_timeframe = str(params.get("htf_timeframe", "4H"))
if htf_timeframe not in ("1H", "4H", "1D", "1W"):
    htf_timeframe = "4H"
htf_length = min(500, max(1, int(params.get("htf_length", 50))))
use_noise_filter = bool(params.get("use_noise_filter", False))
max_range_atr = min(20.0, max(0.5, float(params.get("max_range_atr", 2.5))))
hysteresis = min(25.0, max(0.0, float(params.get("hysteresis", 0.0))))
noise_smooth_length = min(100, max(1, int(params.get("noise_smooth_length", 1))))
use_atr_stops = bool(params.get("use_atr_stops", True))
atr_length = min(300, max(1, int(params.get("atr_length", 14))))
take_profit_atr = min(20.0, max(0.5, float(params.get("take_profit_atr", 3.0))))
stop_loss_atr = min(20.0, max(0.5, float(params.get("stop_loss_atr", 1.5))))
show_dashboard = bool(params.get("show_dashboard", True))
show_background = bool(params.get("show_background", True))
show_labels = bool(params.get("show_labels", True))
show_trend_line = bool(params.get("show_trend_line", True))
show_risk_levels = bool(params.get("show_risk_levels", True))
show_signal_labels = bool(params.get("show_signal_labels", True))

timeframe_seconds = {"1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}

df = df.copy()


def normalized_frame(frame):
    source = df.copy() if frame is None or len(frame) == 0 else frame.copy()
    result = source.reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = np.arange(len(result), dtype=float) * 3600.0 if column == "time" else 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
    times = result["time"].to_numpy(dtype=float)
    if len(times) and max(abs(float(value)) for value in times) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result


base_frame = normalized_frame(df)
n = len(base_frame)
base_time = base_frame["time"].to_numpy(dtype=float)
open_values = base_frame["open"].to_numpy(dtype=float)
high_values = base_frame["high"].to_numpy(dtype=float)
low_values = base_frame["low"].to_numpy(dtype=float)
close_values = base_frame["close"].to_numpy(dtype=float)
volume_values = base_frame["volume"].to_numpy(dtype=float)

runtime_context = globals().get("ctx")
chart_timeframe = str(getattr(runtime_context, "timeframe", "") or "")
if chart_timeframe in timeframe_seconds:
    chart_seconds = timeframe_seconds[chart_timeframe]
elif n > 1:
    deltas = sorted(
        float(base_time[index] - base_time[index - 1])
        for index in range(1, n)
        if base_time[index] > base_time[index - 1]
    )
    chart_seconds = int(deltas[len(deltas) // 2]) if deltas else 3600
else:
    chart_seconds = 3600


def finite_window(values, left, right):
    return all(np.isfinite(float(values[index])) for index in range(left, right))


def rolling_mean(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        left = index - length + 1
        if finite_window(values, left, index + 1):
            result[index] = sum(float(values[cursor]) for cursor in range(left, index + 1)) / length
    return result


def rolling_low(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        left = index - length + 1
        if finite_window(values, left, index + 1):
            result[index] = min(float(values[cursor]) for cursor in range(left, index + 1))
    return result


def rolling_high(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        left = index - length + 1
        if finite_window(values, left, index + 1):
            result[index] = max(float(values[cursor]) for cursor in range(left, index + 1))
    return result


def ema(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    seed_index = -1
    for index in range(length - 1, len(source)):
        left = index - length + 1
        if finite_window(source, left, index + 1):
            result[index] = sum(float(source[cursor]) for cursor in range(left, index + 1)) / length
            seed_index = index
            break
    if seed_index < 0:
        return result
    alpha = 2.0 / (length + 1.0)
    for index in range(seed_index + 1, len(source)):
        if np.isfinite(source[index]):
            result[index] = alpha * source[index] + (1.0 - alpha) * result[index - 1]
        else:
            result[index] = result[index - 1]
    return result


def weighted_smooth(values, length, weight):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    seed_index = -1
    for index in range(length - 1, len(source)):
        left = index - length + 1
        if finite_window(source, left, index + 1):
            result[index] = sum(float(source[cursor]) for cursor in range(left, index + 1)) / length
            seed_index = index
            break
    if seed_index < 0:
        return result
    safe_weight = min(float(length), max(0.0, float(weight)))
    for index in range(seed_index + 1, len(source)):
        if np.isfinite(source[index]):
            result[index] = (
                source[index] * safe_weight + result[index - 1] * (length - safe_weight)
            ) / length
        else:
            result[index] = result[index - 1]
    return result


def true_range_average(high_data, low_data, close_data, length):
    ranges = np.full(len(close_data), np.nan, dtype=float)
    for index in range(len(close_data)):
        if index == 0:
            ranges[index] = float(high_data[index] - low_data[index])
        else:
            ranges[index] = max(
                float(high_data[index] - low_data[index]),
                abs(float(high_data[index] - close_data[index - 1])),
                abs(float(low_data[index] - close_data[index - 1])),
            )
    return weighted_smooth(ranges, length, 1.0)


def cross_up(left, right):
    left_values = np.asarray(left, dtype=float)
    right_values = np.full(len(left_values), float(right), dtype=float) if np.isscalar(right) else np.asarray(right, dtype=float)
    result = np.zeros(len(left_values), dtype=bool)
    for index in range(1, len(left_values)):
        if all(np.isfinite(value) for value in (left_values[index], left_values[index - 1], right_values[index], right_values[index - 1])):
            result[index] = left_values[index] > right_values[index] and left_values[index - 1] <= right_values[index - 1]
    return result


def cross_down(left, right):
    left_values = np.asarray(left, dtype=float)
    right_values = np.full(len(left_values), float(right), dtype=float) if np.isscalar(right) else np.asarray(right, dtype=float)
    result = np.zeros(len(left_values), dtype=bool)
    for index in range(1, len(left_values)):
        if all(np.isfinite(value) for value in (left_values[index], left_values[index - 1], right_values[index], right_values[index - 1])):
            result[index] = left_values[index] < right_values[index] and left_values[index - 1] >= right_values[index - 1]
    return result


def align_completed(source_frame, values, source_seconds):
    result = np.full(n, np.nan, dtype=float)
    source_time = source_frame["time"].to_numpy(dtype=float)
    source_close_time = source_time + source_seconds
    for index in range(n):
        base_close_time = base_time[index] + chart_seconds
        source_index = int(np.searchsorted(source_close_time, base_close_time, side="right") - 1)
        if 0 <= source_index < len(values) and np.isfinite(values[source_index]):
            result[index] = float(values[source_index])
    return result


raw_low = rolling_low(low_values, raw_length)
raw_high = rolling_high(high_values, raw_length)
raw_range = raw_high - raw_low
raw_stochastic = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.isfinite(raw_range[index]) and abs(float(raw_range[index])) > 1e-12:
        raw_stochastic[index] = (close_values[index] - raw_low[index]) / raw_range[index] * 100.0

fast_smooth = weighted_smooth(raw_stochastic, fast_length, smooth_weight)
slow_smooth = weighted_smooth(fast_smooth, slow_length, smooth_weight)
fund_flow = (3.0 * fast_smooth - 2.0 * slow_smooth - 50.0) * 1.032 + 50.0
if noise_smooth_length > 1:
    fund_flow = ema(fund_flow, noise_smooth_length)

band_low = rolling_low(low_values, band_length)
band_high = rolling_high(high_values, band_length)
typical_price = (2.0 * close_values + high_values + low_values + open_values) / 5.0
band_position = np.full(n, np.nan, dtype=float)
for index in range(n):
    band_range = band_high[index] - band_low[index]
    if np.isfinite(band_range) and abs(float(band_range)) > 1e-12:
        band_position[index] = (typical_price[index] - band_low[index]) / band_range * 100.0
bull_bear = ema(band_position, bull_bear_ema)

atr_values = true_range_average(high_values, low_values, close_values, atr_length)
volume_average = rolling_mean(volume_values, volume_length)
cross_level = bull_bear + hysteresis

htf_bullish = np.zeros(n, dtype=bool)
if use_htf_filter:
    if htf_timeframe == chart_timeframe:
        htf_close = np.full(n, np.nan, dtype=float)
        htf_ema = np.full(n, np.nan, dtype=float)
        source_ema = ema(close_values, htf_length)
        if n > 1:
            htf_close[1:] = close_values[:-1]
            htf_ema[1:] = source_ema[:-1]
    else:
        raw_htf = (
            runtime_context.raw_series(htf_timeframe)
            if runtime_context is not None and hasattr(runtime_context, "raw_series")
            else None
        )
        htf_frame = normalized_frame(raw_htf) if raw_htf is not None and len(raw_htf) else base_frame.copy()
        htf_close_source = htf_frame["close"].to_numpy(dtype=float)
        htf_ema_source = ema(htf_close_source, htf_length)
        prior_close = np.full(len(htf_close_source), np.nan, dtype=float)
        prior_ema = np.full(len(htf_close_source), np.nan, dtype=float)
        if len(htf_close_source) > 1:
            prior_close[1:] = htf_close_source[:-1]
            prior_ema[1:] = htf_ema_source[:-1]
        source_seconds = timeframe_seconds.get(htf_timeframe, chart_seconds)
        htf_close = align_completed(htf_frame, prior_close, source_seconds)
        htf_ema = align_completed(htf_frame, prior_ema, source_seconds)
    for index in range(n):
        htf_bullish[index] = np.isfinite(htf_close[index]) and np.isfinite(htf_ema[index]) and htf_close[index] > htf_ema[index]

banker_entry = cross_up(fund_flow, bull_bear) & (bull_bear < entry_zone)
banker_exit = cross_down(fund_flow, bull_bear) & (bull_bear > exit_zone)
cross_entry = cross_up(fund_flow, cross_level) & (bull_bear < entry_zone)
zone_entry_cross = cross_up(bull_bear, entry_zone)
oversold_recovery_cross = cross_up(fund_flow, oversold)
mirror_exit_cross = cross_down(fund_flow, cross_level)
overbought_roll_cross = cross_down(fund_flow, overbought)
flip_cross = cross_down(fund_flow, bull_bear)
zone_short_cross = cross_down(bull_bear, exit_zone)

zone_entry = zone_entry_cross & use_zone_entry
oversold_recovery = oversold_recovery_cross & use_os_recovery
any_long_entry = cross_entry | zone_entry | oversold_recovery
mirror_exit = mirror_exit_cross & (bull_bear > exit_zone) & use_exit_signal
overbought_exit = overbought_roll_cross & use_ob_roll_exit
flip_exit = flip_cross & use_flip_exit
any_long_exit = mirror_exit | overbought_exit | flip_exit
short_cross = mirror_exit_cross & (bull_bear > exit_zone)
short_zone = zone_short_cross & use_zone_entry
short_overbought = overbought_roll_cross & use_ob_roll_exit
short_flip = flip_cross & use_flip_exit
any_short_entry = (short_cross | short_zone | short_overbought | short_flip) & use_short_entry

volume_ok = np.ones(n, dtype=bool)
strength_long_ok = np.ones(n, dtype=bool)
strength_short_ok = np.ones(n, dtype=bool)
noise_ok = np.ones(n, dtype=bool)
htf_long_ok = np.ones(n, dtype=bool)
htf_short_ok = np.ones(n, dtype=bool)
for index in range(n):
    if use_volume_filter:
        volume_ok[index] = np.isfinite(volume_average[index]) and volume_values[index] > volume_average[index] * volume_multiplier
    if use_strength_filter:
        strength_long_ok[index] = index > 0 and np.isfinite(fund_flow[index]) and np.isfinite(fund_flow[index - 1]) and fund_flow[index] > fund_flow[index - 1]
        strength_short_ok[index] = index > 0 and np.isfinite(fund_flow[index]) and np.isfinite(fund_flow[index - 1]) and fund_flow[index] < fund_flow[index - 1]
    if use_noise_filter:
        noise_ok[index] = np.isfinite(atr_values[index]) and high_values[index] - low_values[index] < atr_values[index] * max_range_atr
    if use_htf_filter:
        htf_long_ok[index] = htf_bullish[index]
        htf_short_ok[index] = not htf_bullish[index]

long_gate = volume_ok & strength_long_ok & noise_ok & htf_long_ok
short_gate = volume_ok & strength_short_ok & noise_ok & htf_short_ok

governed_long_entry = np.zeros(n, dtype=bool)
governed_long_exit = np.zeros(n, dtype=bool)
governed_short_entry = np.zeros(n, dtype=bool)
governed_short_exit = np.zeros(n, dtype=bool)
reenter_long = np.zeros(n, dtype=bool)
reenter_short = np.zeros(n, dtype=bool)
take_profit_hit = np.zeros(n, dtype=bool)
stop_loss_hit = np.zeros(n, dtype=bool)
position_series = np.zeros(n, dtype=float)
entry_line = np.full(n, np.nan, dtype=float)
take_profit_line = np.full(n, np.nan, dtype=float)
stop_loss_line = np.full(n, np.nan, dtype=float)

position = 0
last_signal_index = -1
entry_count = 0
exit_count = 0
relong_count = 0
reshort_count = 0
active_entry = np.nan
active_take_profit = np.nan
active_stop_loss = np.nan

for index in range(n):
    cooldown_ok = last_signal_index < 0 or index - last_signal_index > cooldown_bars
    long_event = bool(any_long_entry[index] and long_gate[index] and cooldown_ok)
    short_event = bool(any_short_entry[index] and short_gate[index] and cooldown_ok)
    upward_exit_event = bool(any_long_entry[index] and cooldown_ok)
    downward_exit_event = bool((any_long_exit[index] or any_short_entry[index]) and cooldown_ok)
    reverse_long = False
    reverse_short = False

    if cooldown_ok:
        if position == 0:
            if long_event:
                governed_long_entry[index] = True
            elif short_event:
                governed_short_entry[index] = True
        elif position == 1:
            if long_event:
                reenter_long[index] = True
            elif downward_exit_event:
                if allow_reverse and bool(any_short_entry[index] and short_gate[index]):
                    governed_short_entry[index] = True
                    reverse_short = True
                else:
                    governed_long_exit[index] = True
        else:
            if short_event:
                reenter_short[index] = True
            elif upward_exit_event:
                if allow_reverse and bool(any_long_entry[index] and long_gate[index]):
                    governed_long_entry[index] = True
                    reverse_long = True
                else:
                    governed_short_exit[index] = True

    if governed_long_entry[index]:
        position = 1
        last_signal_index = index
        entry_count += 1
        if reverse_long:
            exit_count += 1
        if use_atr_stops and np.isfinite(atr_values[index]):
            active_entry = close_values[index]
            active_take_profit = close_values[index] + atr_values[index] * take_profit_atr
            active_stop_loss = close_values[index] - atr_values[index] * stop_loss_atr
    elif governed_short_entry[index]:
        position = -1
        last_signal_index = index
        entry_count += 1
        if reverse_short:
            exit_count += 1
        if use_atr_stops and np.isfinite(atr_values[index]):
            active_entry = close_values[index]
            active_take_profit = close_values[index] - atr_values[index] * take_profit_atr
            active_stop_loss = close_values[index] + atr_values[index] * stop_loss_atr
    elif reenter_long[index]:
        last_signal_index = index
        relong_count += 1
        if use_atr_stops and np.isfinite(atr_values[index]):
            active_entry = close_values[index]
            active_take_profit = close_values[index] + atr_values[index] * take_profit_atr
            active_stop_loss = close_values[index] - atr_values[index] * stop_loss_atr
    elif reenter_short[index]:
        last_signal_index = index
        reshort_count += 1
        if use_atr_stops and np.isfinite(atr_values[index]):
            active_entry = close_values[index]
            active_take_profit = close_values[index] - atr_values[index] * take_profit_atr
            active_stop_loss = close_values[index] + atr_values[index] * stop_loss_atr
    elif governed_long_exit[index] or governed_short_exit[index]:
        position = 0
        last_signal_index = index
        exit_count += 1
        active_entry = np.nan
        active_take_profit = np.nan
        active_stop_loss = np.nan

    if use_atr_stops and position != 0 and np.isfinite(active_take_profit) and np.isfinite(active_stop_loss):
        take_profit_hit[index] = (position == 1 and high_values[index] >= active_take_profit) or (position == -1 and low_values[index] <= active_take_profit)
        stop_loss_hit[index] = (position == 1 and low_values[index] <= active_stop_loss) or (position == -1 and high_values[index] >= active_stop_loss)
        if take_profit_hit[index] or stop_loss_hit[index]:
            position = 0
            last_signal_index = index
            exit_count += 1
            active_entry = np.nan
            active_take_profit = np.nan
            active_stop_loss = np.nan

    position_series[index] = float(position)
    if position != 0 and np.isfinite(active_entry):
        entry_line[index] = active_entry
        take_profit_line[index] = active_take_profit
        stop_loss_line[index] = active_stop_loss

pullback_reference = np.full(n, np.nan, dtype=float)
if n > 1:
    pullback_reference[1:] = fund_flow[:-1] * decline_ratio

state_names = []
state_colors = []
for index in range(n):
    if banker_entry[index]:
        state_names.append("Banker Entry")
        state_colors.append("#FDD835")
    elif np.isfinite(fund_flow[index]) and np.isfinite(bull_bear[index]) and fund_flow[index] > bull_bear[index]:
        state_names.append("Increase")
        state_colors.append("#22C55E")
    elif np.isfinite(fund_flow[index]) and np.isfinite(pullback_reference[index]) and fund_flow[index] < pullback_reference[index]:
        state_names.append("Decrease")
        state_colors.append("#E5E7EB")
    elif np.isfinite(fund_flow[index]) and np.isfinite(bull_bear[index]) and fund_flow[index] < bull_bear[index] and np.isfinite(pullback_reference[index]) and fund_flow[index] > pullback_reference[index]:
        state_names.append("Rebound")
        state_colors.append("#42A5F5")
    elif np.isfinite(fund_flow[index]) and np.isfinite(bull_bear[index]) and fund_flow[index] < bull_bear[index]:
        state_names.append("Exit")
        state_colors.append("#EF5350")
    else:
        state_names.append("Neutral")
        state_colors.append("#94A3B8")


def plain_line(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def state_segment(state_name):
    result = [None] * n
    for index in range(n):
        if state_names[index] == state_name and np.isfinite(fund_flow[index]):
            result[index] = float(fund_flow[index])
            if index > 0 and state_names[index - 1] != state_name and np.isfinite(fund_flow[index - 1]):
                result[index - 1] = float(fund_flow[index - 1])
    return result


def state_cloud(state_name):
    return [
        {"upper": float(max(fund_flow[index], bull_bear[index])), "lower": float(min(fund_flow[index], bull_bear[index]))}
        if state_names[index] == state_name and np.isfinite(fund_flow[index]) and np.isfinite(bull_bear[index]) else None
        for index in range(n)
    ]


def fixed_cloud(lower_value, upper_value, mask=None):
    result = []
    for index in range(n):
        visible = True if mask is None else bool(mask[index])
        result.append({"upper": float(upper_value), "lower": float(lower_value)} if visible else None)
    return result


def marker_points(mask, values, color, size):
    return [
        {"value": float(values[index]), "color": color, "size": float(size)}
        if bool(mask[index]) and np.isfinite(values[index]) else None
        for index in range(n)
    ]


plots = []
for state_name, color in (
    ("Banker Entry", "#FDD835"),
    ("Increase", "#22C55E"),
    ("Decrease", "#E5E7EB"),
    ("Exit", "#EF5350"),
    ("Rebound", "#42A5F5"),
):
    plots.append({"name": state_name + " Flow", "title": False, "data": state_cloud(state_name), "color": color, "type": "cloud", "opacity": 0.55, "overlay": False})
    if show_trend_line:
        plots.append({"name": state_name + " Trend", "title": False, "data": state_segment(state_name), "color": color, "type": "line", "lineWidth": 2, "overlay": False})

plots.append({"name": "Bull Bear Line", "data": plain_line(bull_bear), "color": "#94A3B8", "type": "line", "lineWidth": 2, "overlay": False})
plots.extend([
    {"name": "Weak", "title": False, "data": [weak_level] * n, "color": "#84CC16", "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": False},
    {"name": "Oversold", "title": False, "data": [oversold] * n, "color": "#FDD835", "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": False},
    {"name": "Entry Zone", "title": False, "data": [entry_zone] * n, "color": "#F59E0B", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": False},
    {"name": "Midline", "title": False, "data": [50.0] * n, "color": "#64748B", "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": False},
    {"name": "Exit Zone", "title": False, "data": [exit_zone] * n, "color": "#A855F7", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": False},
    {"name": "Overbought", "title": False, "data": [overbought] * n, "color": "#EF5350", "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": False},
    {"name": "Strong", "title": False, "data": [strong_level] * n, "color": "#D946EF", "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": False},
    {"name": "Lower Extreme Zone", "title": False, "data": fixed_cloud(weak_level, oversold), "color": "#FDD835", "type": "cloud", "opacity": 0.12, "overlay": False},
    {"name": "Upper Extreme Zone", "title": False, "data": fixed_cloud(overbought, strong_level), "color": "#D946EF", "type": "cloud", "opacity": 0.12, "overlay": False},
])

if show_background:
    increase_mask = np.asarray([name == "Increase" for name in state_names], dtype=bool)
    bearish_mask = np.asarray([name in ("Exit", "Decrease") for name in state_names], dtype=bool)
    plots.extend([
        {"name": "Bull Flow Background", "title": False, "data": fixed_cloud(0.0, 100.0, increase_mask), "color": "#22C55E", "type": "cloud", "opacity": 0.04, "overlay": False},
        {"name": "Bear Flow Background", "title": False, "data": fixed_cloud(0.0, 100.0, bearish_mask), "color": "#EF5350", "type": "cloud", "opacity": 0.04, "overlay": False},
    ])

if show_risk_levels and use_atr_stops:
    plots.extend([
        {"name": "Active Entry", "data": plain_line(entry_line), "color": "#9CA3AF", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
        {"name": "Active Take Profit", "data": plain_line(take_profit_line), "color": "#14B8A6", "type": "line", "lineWidth": 2, "lineStyle": "dashed", "overlay": True},
        {"name": "Active Stop Loss", "data": plain_line(stop_loss_line), "color": "#B91C1C", "type": "line", "lineWidth": 2, "lineStyle": "dashed", "overlay": True},
    ])

if show_labels:
    plots.append({"name": "Banker In", "data": marker_points(banker_entry, fund_flow, "#FDD835", 7.0), "color": "#FDD835", "type": "scatter", "overlay": False})

if show_signal_labels:
    event_specs = [
        ("Governed Long", governed_long_entry, fund_flow - 4.0, "#84CC16"),
        ("Governed Long Exit", governed_long_exit, fund_flow + 4.0, "#F97316"),
        ("Governed Short", governed_short_entry, fund_flow + 4.0, "#D946EF"),
        ("Governed Short Exit", governed_short_exit, fund_flow - 4.0, "#3B82F6"),
        ("Re-enter Long", reenter_long, fund_flow - 7.0, "#14B8A6"),
        ("Re-enter Short", reenter_short, fund_flow + 7.0, "#A855F7"),
    ]
    for event_name, event_mask, event_values, event_color in event_specs:
        plots.append({"name": event_name, "data": marker_points(event_mask, event_values, event_color, 6.0), "color": event_color, "type": "scatter", "overlay": False})

signals = []
if show_signal_labels:
    signals.extend([
        {"type": "sell", "name": "Take Profit", "text": "TP", "color": "#14B8A6", "renderMode": "events", "data": [float(high_values[index]) if take_profit_hit[index] else None for index in range(n)]},
        {"type": "sell", "name": "Stop Loss", "text": "SL", "color": "#B91C1C", "renderMode": "events", "data": [float(high_values[index]) if stop_loss_hit[index] else None for index in range(n)]},
    ])

last_index = n - 1
last_state = state_names[last_index] if n else "Neutral"
last_position = int(position_series[last_index]) if n else 0
position_text = "LONG" if last_position > 0 else "SHORT" if last_position < 0 else "FLAT"
last_entry = float(entry_line[last_index]) if n and np.isfinite(entry_line[last_index]) else None
last_take_profit = float(take_profit_line[last_index]) if n and np.isfinite(take_profit_line[last_index]) else None
last_stop_loss = float(stop_loss_line[last_index]) if n and np.isfinite(stop_loss_line[last_index]) else None
filter_primary_text = "-"
filter_secondary_text = "-"
if n:
    vol_status = "+" if volume_ok[last_index] else "-"
    strength_long_status = "+" if strength_long_ok[last_index] else "-"
    strength_short_status = "+" if strength_short_ok[last_index] else "-"
    range_status = "+" if noise_ok[last_index] else "-"
    htf_status = "+" if (not use_htf_filter or htf_bullish[last_index]) else "-"
    filter_primary_text = f"Vol {vol_status} | StrL {strength_long_status} | StrS {strength_short_status}"
    filter_secondary_text = f"Range {range_status} | HTF {htf_status}"

tables = []
if show_dashboard:
    tables.append({
        "position": "top-right",
        "backgroundColor": "rgba(15,23,42,0.90)",
        "borderColor": state_colors[last_index] if n else "#64748B",
        "textColor": "#F8FAFC",
        "accentColor": state_colors[last_index] if n else "#64748B",
        "rows": [
            ["BANKER FUND FLOW", last_state],
            ["Trend / BullBear", f"{float(fund_flow[last_index]):.2f} / {float(bull_bear[last_index]):.2f}" if n and np.isfinite(fund_flow[last_index]) and np.isfinite(bull_bear[last_index]) else "-"],
            ["Position", position_text],
            ["Entry", f"{last_entry:.4f}" if last_entry is not None else "-"],
            ["TP / SL", f"{last_take_profit:.4f} / {last_stop_loss:.4f}" if last_take_profit is not None and last_stop_loss is not None else "-"],
            ["Filters", filter_primary_text],
            ["Range / HTF", filter_secondary_text],
            ["Signals", f"In {entry_count} Out {exit_count} | RL {relong_count} RS {reshort_count}"],
            ["HTF", htf_timeframe if use_htf_filter else "Off"],
        ],
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "layers": [],
    "calculatedVars": {
        "state": last_state,
        "position": position_text,
        "entries": entry_count,
        "exits": exit_count,
        "reentriesLong": relong_count,
        "reentriesShort": reshort_count,
        "fundFlow": float(fund_flow[last_index]) if n and np.isfinite(fund_flow[last_index]) else None,
        "bullBear": float(bull_bear[last_index]) if n and np.isfinite(bull_bear[last_index]) else None,
    },
}
