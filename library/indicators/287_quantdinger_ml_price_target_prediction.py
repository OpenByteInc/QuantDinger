# ============================================================
# QuantDinger ML Price Target Prediction
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger ML Price Target Prediction"
my_indicator_description = "Learns trend-segment move sizes from eight market features and projects adaptive target and risk zones."

# @param trend_method str EMA Trend engine values=SuperTrend,HMA,EMA
# @param hma_length int 93 Hull moving-average length range=2:500:1
# @param ema_fast int 50 Fast EMA length range=1:500:1
# @param ema_slow int 200 Slow EMA length range=2:1000:1
# @param supertrend_factor float 3.0 SuperTrend factor range=0.1:20.0:0.1
# @param atr_length int 10 SuperTrend ATR length range=1:200:1
# @param bandwidth float 5.0 Kernel-regression bandwidth range=2.0:100.0:0.5
# @param hide_invalid_signals bool true Hide signals without a finite prediction
# @param bullish_color color #00FFBB Bullish color
# @param bearish_color color #FF1100 Bearish color
# @param show_metrics bool true Show model metrics

trend_method = str(params.get("trend_method", "EMA"))
hma_length = min(500, max(2, int(params.get("hma_length", 93))))
ema_fast = min(500, max(1, int(params.get("ema_fast", 50))))
ema_slow = min(1000, max(2, int(params.get("ema_slow", 200))))
supertrend_factor = min(20.0, max(0.1, float(params.get("supertrend_factor", 3.0))))
atr_length = min(200, max(1, int(params.get("atr_length", 10))))
bandwidth = min(100.0, max(2.0, float(params.get("bandwidth", 5.0))))
hide_invalid_signals = bool(params.get("hide_invalid_signals", True))
bullish_color = str(params.get("bullish_color", "#00FFBB"))
bearish_color = str(params.get("bearish_color", "#FF1100"))
show_metrics = bool(params.get("show_metrics", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _rgba(color, transparency):
    text = color.lstrip("#")
    if len(text) == 6:
        red, green, blue = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
        return f"rgba({red},{green},{blue},{max(0.0, min(1.0, 1.0 - transparency / 100.0)):.3f})"
    return color


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _wma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    divisor = float(np.sum(weights))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / divisor)
    return result


def _hma(values, period):
    return _wma(2.0 * _wma(values, max(1, period // 2)) - _wma(values, period), max(1, int(round(np.sqrt(period)))))


def _rma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if not len(values):
        return result
    if period == 1:
        return values.astype(float).copy()
    if len(values) < period:
        return result
    previous = float(np.mean(values[:period]))
    result[period - 1] = previous
    for index in range(period, len(values)):
        previous = (previous * (period - 1.0) + values[index]) / period
        result[index] = previous
    return result


def _rsi(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= period:
        return result
    change = np.diff(values)
    gains = np.maximum(change, 0.0)
    losses = np.maximum(-change, 0.0)
    average_gain = float(np.mean(gains[:period]))
    average_loss = float(np.mean(losses[:period]))
    for index in range(period, len(values)):
        if index > period:
            average_gain = (average_gain * (period - 1.0) + gains[index - 1]) / period
            average_loss = (average_loss * (period - 1.0) + losses[index - 1]) / period
        result[index] = 50.0 if average_gain == 0.0 and average_loss == 0.0 else 100.0 if average_loss == 0.0 else 0.0 if average_gain == 0.0 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


def _rolling_extreme(values, period, mode):
    rolling = pd.Series(values, dtype=float).rolling(period, min_periods=1)
    return (rolling.max() if mode == "max" else rolling.min()).to_numpy(dtype=float)


frame = df.copy().reset_index(drop=True)
if "time" not in frame.columns:
    frame["time"] = np.arange(len(frame), dtype=float) * 3600000.0
if "volume" not in frame.columns:
    frame["volume"] = 0.0
for column in ("time", "open", "high", "low", "close", "volume"):
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
frame["volume"] = frame["volume"].fillna(0.0)
frame = frame.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
times = frame["time"].to_numpy(dtype=float)
if len(times) and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0
open_price = frame["open"].to_numpy(dtype=float)
high = frame["high"].to_numpy(dtype=float)
low = frame["low"].to_numpy(dtype=float)
close = frame["close"].to_numpy(dtype=float)
volume = frame["volume"].to_numpy(dtype=float)
n = len(frame)

true_range = np.full(n, np.nan, dtype=float)
if n:
    true_range[0] = high[0] - low[0]
for index in range(1, n):
    true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))

supertrend_atr = _rma(true_range, atr_length)
supertrend = np.full(n, np.nan, dtype=float)
supertrend_direction = np.ones(n, dtype=int)
upper_band = np.full(n, np.nan, dtype=float)
lower_band = np.full(n, np.nan, dtype=float)
midpoint = (high + low) * 0.5
for index in range(n):
    if not np.isfinite(supertrend_atr[index]):
        continue
    raw_upper = midpoint[index] + supertrend_factor * supertrend_atr[index]
    raw_lower = midpoint[index] - supertrend_factor * supertrend_atr[index]
    previous_lower = lower_band[index - 1] if index > 0 and np.isfinite(lower_band[index - 1]) else 0.0
    previous_upper = upper_band[index - 1] if index > 0 and np.isfinite(upper_band[index - 1]) else 0.0
    previous_close = close[index - 1] if index > 0 else np.nan
    lower_band[index] = raw_lower if raw_lower > previous_lower or (np.isfinite(previous_close) and previous_close < previous_lower) else previous_lower
    upper_band[index] = raw_upper if raw_upper < previous_upper or (np.isfinite(previous_close) and previous_close > previous_upper) else previous_upper
    if index == 0 or not np.isfinite(supertrend_atr[index - 1]):
        supertrend_direction[index] = 1
    elif supertrend[index - 1] == previous_upper:
        supertrend_direction[index] = -1 if close[index] > upper_band[index] else 1
    else:
        supertrend_direction[index] = 1 if close[index] < lower_band[index] else -1
    supertrend[index] = lower_band[index] if supertrend_direction[index] == -1 else upper_band[index]

hull = _hma(close, hma_length)
hma_signal = np.full(n, -1, dtype=int)
for index in range(1, n):
    if np.all(np.isfinite([hull[index], hull[index - 1]])) and hull[index] > hull[index - 1]:
        hma_signal[index] = 1
ema_one = _ema(close, ema_fast)
ema_two = _ema(close, ema_slow)
ema_signal = np.full(n, -1, dtype=int)
ema_signal[ema_one > ema_two] = 1
if trend_method == "SuperTrend":
    trend_signal = -supertrend_direction
elif trend_method in ("HMA", "HMA (Increasing or Decreasing)"):
    trend_signal = hma_signal
else:
    trend_signal = ema_signal

trend_cross = np.zeros(n, dtype=bool)
bullish_cross = np.zeros(n, dtype=bool)
bearish_cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    bullish_cross[index] = trend_signal[index] > 0 and trend_signal[index - 1] <= 0
    bearish_cross[index] = trend_signal[index] < 0 and trend_signal[index - 1] >= 0
    trend_cross[index] = bullish_cross[index] or bearish_cross[index]

highest_fourteen = _rolling_extreme(high, 14, "max")
lowest_fourteen = _rolling_extreme(low, 14, "min")
dump_ratio = np.divide(highest_fourteen - close, highest_fourteen, out=np.full(n, np.nan), where=highest_fourteen != 0.0)
pump_ratio = np.divide(close - lowest_fourteen, lowest_fourteen, out=np.full(n, np.nan), where=lowest_fourteen != 0.0)
feature_one = _rsi(dump_ratio, 14)
feature_two = _rsi(pump_ratio, 14)
range_average = pd.Series(high - low, dtype=float).rolling(40, min_periods=40).mean().to_numpy(dtype=float)
range_baseline = _ema(range_average, 20)
range_change = range_average - range_baseline
range_change_low = _rolling_extreme(range_change, 1000, "min")
range_change_high = _rolling_extreme(range_change, 1000, "max")
range_change_span = range_change_high - range_change_low
feature_three = np.divide(range_change - range_change_low, range_change_span, out=np.full(n, np.nan), where=range_change_span != 0.0) * 100.0
range_change_baseline = _ema(range_change, 10)
range_acceleration = range_change - range_change_baseline
acceleration_low = _rolling_extreme(range_acceleration, 1000, "min")
acceleration_high = _rolling_extreme(range_acceleration, 1000, "max")
acceleration_span = acceleration_high - acceleration_low
feature_four = np.divide(range_acceleration - acceleration_low, acceleration_span, out=np.full(n, np.nan), where=acceleration_span != 0.0) * 100.0
volume_short = _ema(volume, 7)
volume_long = _ema(volume, 14)
feature_five = np.divide(100.0 * (volume_short - volume_long), volume_long, out=np.full(n, np.nan), where=volume_long != 0.0)
true_range_sum = pd.Series(true_range, dtype=float).rolling(14, min_periods=14).sum().to_numpy(dtype=float)
price_span = highest_fourteen - lowest_fourteen
choppiness_ratio = np.divide(true_range_sum, price_span, out=np.full(n, np.nan), where=price_span > 0.0)
feature_six = np.where(choppiness_ratio > 0.0, 100.0 * np.log10(choppiness_ratio) / np.log10(14.0), np.nan)
feature_seven = _rsi(close, 14)
feature_eight = np.where(trend_signal == 1, 1.0, 0.0)
features = [feature_one, feature_two, feature_three, feature_four, feature_five, feature_six, feature_seven, feature_eight]

duration = np.full(n, -1, dtype=int)
signal_move_up = np.full(n, np.nan, dtype=float)
signal_move_down = np.full(n, np.nan, dtype=float)
runup = np.full(n, np.nan, dtype=float)
drawdown = np.full(n, np.nan, dtype=float)
absolute_move = np.full(n, np.nan, dtype=float)
last_cross = -1
for index in range(n):
    if trend_cross[index]:
        last_cross = index
    if last_cross < 0:
        continue
    duration[index] = index - last_cross
    anchor = close[last_cross]
    if anchor == 0.0:
        continue
    signal_move_up[index] = (high[index] - anchor) / anchor
    signal_move_down[index] = (low[index] - anchor) / anchor
    length = max(1, duration[index])
    start = max(0, index - length + 1)
    runup[index] = float(np.nanmax(signal_move_up[start:index + 1]))
    drawdown[index] = float(np.nanmin(signal_move_down[start:index + 1]))
    absolute_move[index] = abs(runup[index] if trend_signal[index] > 0 else drawdown[index])

training_targets = []
training_features = [[] for _ in range(8)]
current_features = None
prediction = np.full(n, np.nan, dtype=float)
training_size = np.zeros(n, dtype=int)
total_predictions = np.zeros(n, dtype=int)
correct_predictions = np.zeros(n, dtype=int)
running_total = 0
running_correct = 0
for index in range(n):
    if trend_cross[index]:
        lookback = duration[index - 1] + 1 if index > 0 and duration[index - 1] >= 0 else -1
        source_index = index - lookback if lookback > 0 else -1
        if source_index >= 0 and index > 0:
            candidates = [absolute_move[index - 1]] + [values[source_index] for values in features[:7]]
            if np.all(np.isfinite(candidates)):
                training_targets.insert(0, float(absolute_move[index - 1]))
                for feature_index in range(8):
                    training_features[feature_index].insert(0, float(features[feature_index][source_index]))
        current_features = np.asarray([values[index] for values in features], dtype=float)

    if current_features is not None and len(training_targets) and np.all(np.isfinite(current_features)):
        feature_matrix = np.asarray(training_features, dtype=float).T
        distances = np.sqrt(np.sum((feature_matrix - current_features) ** 2, axis=1))
        kernels = np.exp(-((distances / bandwidth) ** 2))
        kernel_sum = float(np.sum(kernels))
        if kernel_sum != 0.0 and np.isfinite(kernel_sum):
            prediction[index] = float(np.dot(kernels, np.asarray(training_targets, dtype=float)) / kernel_sum)

    if trend_cross[index] and index > 0 and np.isfinite(prediction[index - 1]):
        running_total += 1
        if np.isfinite(absolute_move[index - 1]) and absolute_move[index - 1] > prediction[index - 1]:
            running_correct += 1
    training_size[index] = len(training_targets)
    total_predictions[index] = running_total
    correct_predictions[index] = running_correct

win_rate = np.divide(correct_predictions, total_predictions, out=np.full(n, np.nan), where=total_predictions != 0)
recommended_rr = np.full(n, np.nan, dtype=float)
positive_win_rate = np.isfinite(win_rate) & (win_rate > 0.0)
recommended_rr[positive_win_rate] = 1.0 / win_rate[positive_win_rate] - 1.0
recommended_rr[np.isfinite(win_rate) & (win_rate == 0.0)] = np.inf

take_profit_level = np.full(n, np.nan, dtype=float)
stop_loss_level = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.isfinite(prediction[index]):
        continue
    direction = 1.0 if trend_signal[index] > 0 else -1.0
    take_profit_level[index] = close[index] + direction * close[index] * prediction[index]
    rr = recommended_rr[index]
    if np.isfinite(rr) and rr != 0.0:
        stop_loss_level[index] = close[index] - direction * close[index] * prediction[index] / rr
    elif np.isinf(rr):
        stop_loss_level[index] = close[index]

atr_fourteen = _rma(true_range, 14)
valid_entry = trend_cross & (np.isfinite(prediction) if hide_invalid_signals else np.ones(n, dtype=bool))
entry_bullish = bullish_cross & valid_entry
entry_bearish = bearish_cross & valid_entry
tp_zone = [None] * n
sl_zone = [None] * n
in_trade = np.zeros(n, dtype=int)
tp_hit_event = np.zeros(n, dtype=bool)
sl_hit_event = np.zeros(n, dtype=bool)
tp_hit_state = np.zeros(n, dtype=bool)
sl_hit_state = np.zeros(n, dtype=bool)
active_tp = None
active_sl = None
trade_state = 0
persistent_tp_hit = False
persistent_sl_hit = False
for index in range(n):
    same_direction = index > 0 and trend_signal[index] == trend_signal[index - 1]
    if valid_entry[index]:
        active_tp = None
        active_sl = None
        if np.isfinite(take_profit_level[index]):
            active_tp = (float(max(close[index], take_profit_level[index])), float(min(close[index], take_profit_level[index])))
        if np.isfinite(stop_loss_level[index]):
            active_sl = (float(max(close[index], stop_loss_level[index])), float(min(close[index], stop_loss_level[index])))
        trade_state = 1

    if active_tp is not None:
        tp_zone[index] = {"upper": active_tp[0], "lower": active_tp[1], "color": _rgba(bullish_color, 80)}
    if active_sl is not None:
        sl_zone[index] = {"upper": active_sl[0], "lower": active_sl[1], "color": _rgba(bearish_color, 80)}

    if active_tp is not None:
        target_hit = (trend_signal[index] > 0 and high[index] >= active_tp[0]) or (trend_signal[index] < 0 and low[index] <= active_tp[1])
        if same_direction and target_hit:
            persistent_tp_hit = True
            tp_hit_event[index] = True
            active_tp = None
            active_sl = None
            trade_state = 0

    if active_sl is not None:
        stop_hit = (trend_signal[index] > 0 and low[index] <= active_sl[1]) or (trend_signal[index] < 0 and high[index] >= active_sl[0])
        if same_direction and stop_hit:
            persistent_sl_hit = True
            sl_hit_event[index] = True
            active_tp = None
            active_sl = None
            trade_state = 0
    in_trade[index] = trade_state
    tp_hit_state[index] = persistent_tp_hit
    sl_hit_state[index] = persistent_sl_hit

plots = [
    {"name": "Take Profit Zone", "title": False, "data": tp_zone, "color": bullish_color, "type": "cloud", "overlay": True},
    {"name": "Stop Loss Zone", "title": False, "data": sl_zone, "color": bearish_color, "type": "cloud", "overlay": True},
    {"name": "Model State", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
]
signals = [
    {"type": "buy", "name": "Bullish Entry Signal", "text": "▲", "data": [float(stop_loss_level[index] - atr_fourteen[index]) if entry_bullish[index] and np.all(np.isfinite([stop_loss_level[index], atr_fourteen[index]])) else None for index in range(n)], "color": bullish_color, "renderMode": "events"},
    {"type": "sell", "name": "Bearish Entry Signal", "text": "▼", "data": [float(stop_loss_level[index] + atr_fourteen[index]) if entry_bearish[index] and np.all(np.isfinite([stop_loss_level[index], atr_fourteen[index]])) else None for index in range(n)], "color": bearish_color, "renderMode": "events"},
    {"type": "sell", "name": "Take Profit Hit", "text": "", "data": [float(close[index]) if tp_hit_state[index] else None for index in range(n)], "color": "rgba(0,0,0,0)", "renderMode": "hidden"},
    {"type": "sell", "name": "Stop Loss Hit", "text": "", "data": [float(close[index]) if sl_hit_state[index] else None for index in range(n)], "color": "rgba(0,0,0,0)", "renderMode": "hidden"},
]
bar_colors = ["#808080" if in_trade[index] == 0 else bullish_color if trend_signal[index] > 0 else bearish_color for index in range(n)]
tables = []
if show_metrics:
    latest_prediction = prediction[-1] if n else np.nan
    latest_win_rate = win_rate[-1] if n else np.nan
    latest_rr = recommended_rr[-1] if n else np.nan
    tables.append({"title": "Indicator Metrics", "position": "top-right", "compact": True, "rows": [
        ["Metric", "Value"], ["Training Data Size", str(int(training_size[-1])) if n else "0"],
        ["Predicted Move Size", f"{latest_prediction * 100.0:.2f}%" if np.isfinite(latest_prediction) else "N/A"],
        ["Success Rate", f"{latest_win_rate * 100.0:.2f}%" if np.isfinite(latest_win_rate) else "N/A"],
        ["Recommended Reward/Risk", f"{latest_rr:.2f}" if np.isfinite(latest_rr) else "N/A"],
        ["In Trade", "Yes" if n and in_trade[-1] else "No"],
    ]})

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": [], "tables": tables,
    "bar_colors": bar_colors, "background_colors": [None] * n,
    "calculatedVars": {
        "trueRange": _finite(true_range), "supertrendATR": _finite(supertrend_atr), "supertrend": _finite(supertrend), "supertrendDirection": supertrend_direction.tolist(),
        "hull": _finite(hull), "hmaSignal": hma_signal.tolist(), "emaFast": _finite(ema_one), "emaSlow": _finite(ema_two), "emaSignal": ema_signal.tolist(),
        "trendSignal": trend_signal.tolist(), "trendCross": trend_cross.tolist(), "bullishCross": bullish_cross.tolist(), "bearishCross": bearish_cross.tolist(),
        "feature1": _finite(feature_one), "feature2": _finite(feature_two), "feature3": _finite(feature_three), "feature4": _finite(feature_four),
        "feature5": _finite(feature_five), "feature6": _finite(feature_six), "feature7": _finite(feature_seven), "feature8": _finite(feature_eight),
        "duration": duration.tolist(), "signalMoveUp": _finite(signal_move_up), "signalMoveDown": _finite(signal_move_down), "runup": _finite(runup), "drawdown": _finite(drawdown), "absoluteMove": _finite(absolute_move),
        "trainingSize": training_size.tolist(), "prediction": _finite(prediction), "totalPredictions": total_predictions.tolist(), "correctPredictions": correct_predictions.tolist(),
        "winRate": _finite(win_rate), "recommendedRR": _finite(recommended_rr), "takeProfitLevel": _finite(take_profit_level), "stopLossLevel": _finite(stop_loss_level),
        "validEntry": valid_entry.tolist(), "inTrade": in_trade.tolist(), "tpHitEvent": tp_hit_event.tolist(), "slHitEvent": sl_hit_event.tolist(),
        "tpHitState": tp_hit_state.tolist(), "slHitState": sl_hit_state.tolist(),
        "trainingTarget": _finite(np.asarray(training_targets, dtype=float)),
        "trainingFeature1": _finite(np.asarray(training_features[0], dtype=float)), "trainingFeature2": _finite(np.asarray(training_features[1], dtype=float)),
        "trainingFeature3": _finite(np.asarray(training_features[2], dtype=float)), "trainingFeature4": _finite(np.asarray(training_features[3], dtype=float)),
        "trainingFeature5": _finite(np.asarray(training_features[4], dtype=float)), "trainingFeature6": _finite(np.asarray(training_features[5], dtype=float)),
        "trainingFeature7": _finite(np.asarray(training_features[6], dtype=float)), "trainingFeature8": _finite(np.asarray(training_features[7], dtype=float)),
    },
}
