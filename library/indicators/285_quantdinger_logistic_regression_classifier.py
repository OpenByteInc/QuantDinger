# ============================================================
# QuantDinger Logistic Regression Classifier
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Logistic Regression Classifier"
my_indicator_description = "Retrains the source's rolling logistic classifier, normalizes its curves to price, and tracks holding-period trade events."

# @param price_type str Close Price source values=Open,High,Low,Close,HL2,OC2,OHL3,HLC3,OHLC4
# @param resolution str Current Calculation timeframe values=Current,1m,5m,15m,30m,1H,2H,4H,1D,1W
# @param lookback int 3 Logistic lookback range=2:1000:1
# @param normalization_lookback int 2 Normalization lookback range=2:240:1
# @param learning_rate float 0.0009 Learning rate range=0.0001:0.01:0.0001
# @param training_iterations int 1000 Training iterations range=50:20000:1
# @param filter_type str None Signal filter values=Volatility,Volume,Both,None
# @param show_curves bool false Show normalized loss and prediction curves
# @param optional_calculation bool true Use timestamp feature and price target
# @param use_price_signal bool true Compare price with normalized loss
# @param holding_period int 5 Holding period range=1:5000:1
# @param lot_size float 0.01 Backtest lot size
# @param show_info bool true Show statistics table

price_type = str(params.get("price_type", "Close"))
resolution = str(params.get("resolution", "Current"))
lookback = min(1000, max(2, int(params.get("lookback", 3))))
normalization_lookback = min(240, max(2, int(params.get("normalization_lookback", 2))))
learning_rate = min(0.01, max(0.0001, float(params.get("learning_rate", 0.0009))))
training_iterations = min(20000, max(50, int(params.get("training_iterations", 1000))))
filter_type = str(params.get("filter_type", "None"))
show_curves = bool(params.get("show_curves", False))
optional_calculation = bool(params.get("optional_calculation", True))
use_price_signal = bool(params.get("use_price_signal", True))
holding_period = min(5000, max(1, int(params.get("holding_period", 5))))
lot_size = max(0.0, float(params.get("lot_size", 0.01)))
show_info = bool(params.get("show_info", True))

timeframe_seconds = {"1M": 60, "5M": 300, "15M": 900, "30M": 1800, "1H": 3600, "2H": 7200, "4H": 14400, "1D": 86400, "1W": 604800}


def _normalize_frame(raw):
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    frame = raw.copy().reset_index(drop=True)
    if "time" not in frame.columns:
        frame["time"] = np.arange(len(frame), dtype=float) * 3600000.0
    if "volume" not in frame.columns:
        frame["volume"] = 0.0
    for column in ("time", "open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["volume"] = frame["volume"].fillna(0.0)
    frame = frame.dropna(subset=["time", "open", "high", "low", "close"])
    if len(frame) and np.nanmax(np.abs(frame["time"].to_numpy(dtype=float))) < 100000000000.0:
        frame["time"] = frame["time"] * 1000.0
    return frame.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


def _source(frame, name):
    o = frame["open"].to_numpy(dtype=float); h = frame["high"].to_numpy(dtype=float); l = frame["low"].to_numpy(dtype=float); c = frame["close"].to_numpy(dtype=float)
    if name == "Open": return o
    if name == "High": return h
    if name == "Low": return l
    if name == "HL2": return (h + l) * 0.5
    if name == "OC2": return (o + c) * 0.5
    if name == "OHL3": return (o + h + l) / 3.0
    if name == "HLC3": return (h + l + c) / 3.0
    if name == "OHLC4": return (o + h + l + c) * 0.25
    return c


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _rolling_sum(values, period):
    return pd.Series(values, dtype=float).rolling(period, min_periods=period).sum().to_numpy(dtype=float)


def _rolling_min(values, period):
    return pd.Series(values, dtype=float).rolling(period, min_periods=period).min().to_numpy(dtype=float)


def _rolling_max(values, period):
    return pd.Series(values, dtype=float).rolling(period, min_periods=period).max().to_numpy(dtype=float)


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float); alpha = 2.0 / (period + 1.0); previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _rma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < period: return result
    previous = float(np.mean(values[:period])); result[period - 1] = previous
    for index in range(period, len(values)):
        previous = (previous * (period - 1.0) + values[index]) / period; result[index] = previous
    return result


def _wma(values, period):
    result = np.full(len(values), np.nan, dtype=float); weights = np.arange(1, period + 1, dtype=float); divisor = float(np.sum(weights))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)): result[index] = float(np.dot(window, weights) / divisor)
    return result


def _hma(values, period):
    return _wma(2.0 * _wma(values, max(1, period // 2)) - _wma(values, period), max(1, int(round(np.sqrt(period)))))


def _rsi(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= period: return result
    changes = np.diff(values); gains = np.maximum(changes, 0.0); losses = np.maximum(-changes, 0.0); average_gain = float(np.mean(gains[:period])); average_loss = float(np.mean(losses[:period]))
    for index in range(period, len(values)):
        if index > period:
            average_gain = (average_gain * (period - 1.0) + gains[index - 1]) / period; average_loss = (average_loss * (period - 1.0) + losses[index - 1]) / period
        result[index] = 50.0 if average_gain == 0.0 and average_loss == 0.0 else 100.0 if average_loss == 0.0 else 0.0 if average_gain == 0.0 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


def _cross(first, second):
    over = np.zeros(len(first), dtype=bool); under = np.zeros(len(first), dtype=bool)
    for index in range(1, len(first)):
        if np.all(np.isfinite([first[index], second[index], first[index - 1], second[index - 1]])):
            over[index] = first[index] > second[index] and first[index - 1] <= second[index - 1]
            under[index] = first[index] < second[index] and first[index - 1] >= second[index - 1]
    return over, under


base_frame = _normalize_frame(df)
base_times = base_frame["time"].to_numpy(dtype=float)
open_price = base_frame["open"].to_numpy(dtype=float); high = base_frame["high"].to_numpy(dtype=float); low = base_frame["low"].to_numpy(dtype=float); close = base_frame["close"].to_numpy(dtype=float); volume = base_frame["volume"].to_numpy(dtype=float)
n = len(base_frame)
runtime_ctx = globals().get("ctx")
chart_key = str(getattr(runtime_ctx, "timeframe", "1H") or "1H").upper()
chart_seconds = timeframe_seconds.get(chart_key, 3600)
requested_key = chart_key if resolution.lower() in ("", "current") else resolution.upper()
calculation_frame = base_frame
resolved_resolution = chart_key
if requested_key != chart_key and requested_key in timeframe_seconds:
    raw_series = runtime_ctx.raw_series(resolution) if runtime_ctx is not None and hasattr(runtime_ctx, "raw_series") else None
    candidate = _normalize_frame(raw_series)
    if len(candidate): calculation_frame = candidate; resolved_resolution = requested_key

calculation_source = _source(calculation_frame, price_type)
if resolved_resolution == chart_key:
    base_source = calculation_source.copy()
else:
    target_times = calculation_frame["time"].to_numpy(dtype=float); target_ends = target_times + timeframe_seconds[resolved_resolution] * 1000.0; chart_ends = base_times + chart_seconds * 1000.0; source_indexes = np.searchsorted(target_ends, chart_ends, side="right") - 1; base_source = np.full(n, np.nan, dtype=float); valid = (source_indexes >= 0) & (source_indexes < len(calculation_source)); base_source[valid] = calculation_source[source_indexes[valid]]

synthetic_source = np.log(np.abs(base_source * base_source - 1.0) + 0.5)
feature = base_times.copy() if optional_calculation else base_source.copy()
target = base_source.copy() if optional_calculation else synthetic_source.copy()
hypothesis = 0.5
gradient = _rolling_sum(feature * (hypothesis - target), lookback) / lookback
weight = -learning_rate * training_iterations * gradient
prediction_argument = _rolling_sum(feature * weight, lookback)
prediction = np.full(n, np.nan, dtype=float)
finite_argument = np.isfinite(prediction_argument)
positive_argument = finite_argument & (prediction_argument >= 0.0)
negative_argument = finite_argument & (prediction_argument < 0.0)
prediction[positive_argument] = 1.0 / (1.0 + np.exp(-np.minimum(prediction_argument[positive_argument], 700.0)))
negative_exponent = np.exp(np.maximum(prediction_argument[negative_argument], -700.0))
prediction[negative_argument] = negative_exponent / (1.0 + negative_exponent)
inner_loss = _rolling_sum(target * (np.log(hypothesis) + (1.0 - target)), lookback)
loss = -_rolling_sum(inner_loss * np.log(1.0 - hypothesis), lookback) / lookback


def _minimax(values, period, price_values):
    values_high = _rolling_max(values, period); values_low = _rolling_min(values, period); price_high = _rolling_max(price_values, period); price_low = _rolling_min(price_values, period); result = np.full(len(values), np.nan, dtype=float); denominator = values_high - values_low; valid = np.isfinite(denominator) & (denominator != 0.0) & np.isfinite(price_high) & np.isfinite(price_low); result[valid] = (price_high[valid] - price_low[valid]) * (values[valid] - values_low[valid]) / denominator[valid] + price_low[valid]; return result


scaled_loss = _minimax(loss, normalization_lookback, base_source)
scaled_prediction = _minimax(prediction, normalization_lookback, base_source)

true_range = np.full(n, np.nan, dtype=float)
if n: true_range[0] = high[0] - low[0]
for index in range(1, n): true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
atr_one = true_range.copy(); atr_ten = _rma(true_range, 10)
volume_rsi = _rsi(volume, 14); volume_oscillator = _hma(volume_rsi, 10)
filter_allowed = np.ones(n, dtype=bool)
if filter_type in ("Volatility", "Both"):
    filter_allowed &= np.isfinite(atr_one) & np.isfinite(atr_ten) & (atr_one > atr_ten)
if filter_type in ("Volume", "Both"):
    filter_allowed &= np.isfinite(volume_oscillator) & (volume_oscillator > 49.0)

loss_over_prediction, loss_under_prediction = _cross(scaled_loss, scaled_prediction)
signal = np.zeros(n, dtype=int)
for index in range(n):
    previous_signal = signal[index - 1] if index > 0 else 0
    if use_price_signal:
        signal[index] = -1 if filter_allowed[index] and np.all(np.isfinite([base_source[index], scaled_loss[index]])) and base_source[index] < scaled_loss[index] else 1 if filter_allowed[index] and np.all(np.isfinite([base_source[index], scaled_loss[index]])) and base_source[index] > scaled_loss[index] else previous_signal
    else:
        signal[index] = -1 if filter_allowed[index] and loss_under_prediction[index] else 1 if filter_allowed[index] and loss_over_prediction[index] else previous_signal

changed = np.zeros(n, dtype=bool); holding_counter = np.zeros(n, dtype=int); start_long = np.zeros(n, dtype=bool); start_short = np.zeros(n, dtype=bool); end_long = np.zeros(n, dtype=bool); end_short = np.zeros(n, dtype=bool)
counter = 0
for index in range(n):
    changed[index] = index > 0 and signal[index] != signal[index - 1]
    counter = 0 if changed[index] else counter + 1; holding_counter[index] = counter
    start_long[index] = changed[index] and signal[index] == 1; start_short[index] = changed[index] and signal[index] == -1
    end_long[index] = (signal[index] == 1 and counter == holding_period and not changed[index]) or (changed[index] and signal[index] == -1)
    end_short[index] = (signal[index] == -1 and counter == holding_period and not changed[index]) or (changed[index] and signal[index] == 1)

ohl_three = (open_price + high + low) / 3.0
start_long_price = 0.0; start_short_price = 0.0; long_trade = 0.0; short_trade = 0.0; trade_flag = 0; win_flag = 0; cumulative_return = np.zeros(n, dtype=float); total_trades = np.zeros(n, dtype=int); total_wins = np.zeros(n, dtype=int); running_return = 0.0; running_trades = 0; running_wins = 0
for index in range(n):
    if start_long[index]: start_long_price = float(ohl_three[index])
    if end_long[index]:
        trade_flag = 1; difference = float(ohl_three[index] - start_long_price); win_flag = 1 if difference > 0.0 else 0; long_trade = difference * lot_size
    if start_short[index]: start_short_price = float(ohl_three[index])
    if end_short[index]:
        trade_flag = 1; difference = float(start_short_price - ohl_three[index]); win_flag = 1 if difference > 0.0 else 0; short_trade = difference * lot_size
    running_return += long_trade + short_trade; running_trades += trade_flag; running_wins += win_flag; cumulative_return[index] = running_return; total_trades[index] = running_trades; total_wins[index] = running_wins

plots = [
    {"name": "Loss Curve", "data": _finite(scaled_loss) if show_curves else [None] * n, "color": "#0000FF", "type": "line", "lineWidth": 3, "overlay": True},
    {"name": "Prediction Curve", "data": _finite(scaled_prediction) if show_curves else [None] * n, "color": "#00FF00", "type": "line", "lineWidth": 3, "overlay": True},
    {"name": "Long Exit", "data": [float(high[index]) if end_long[index] else None for index in range(n)], "color": "rgba(0,128,255,0.70)", "type": "cross", "size": 2, "overlay": True},
    {"name": "Short Exit", "data": [float(low[index]) if end_short[index] else None for index in range(n)], "color": "rgba(255,0,128,0.70)", "type": "cross", "size": 2, "overlay": True},
    {"name": "Classifier State", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
]
signals = [
    {"type": "buy", "name": "Buy", "text": "BUY", "data": [float(low[index]) if start_long[index] else None for index in range(n)], "color": "#0080FF", "renderMode": "events"},
    {"type": "sell", "name": "Sell", "text": "SELL", "data": [float(high[index]) if start_short[index] else None for index in range(n)], "color": "#FF0080", "renderMode": "events"},
]
tables = []
if show_info and n:
    losses = int(total_trades[-1] - total_wins[-1]); losses_for_ratio = 1 if losses == 0 else losses; trades_for_rate = max(1, int(total_trades[-1])); bar_seconds = (base_times[-1] - base_times[-2]) / 1000.0 if n > 1 else 0.0
    tables.append({"title": "Logistic Classifier", "position": "top-right", "compact": True, "rows": [["CR", f"{cumulative_return[-1]:.1f}"], ["Trades", str(int(total_trades[-1]))], ["Win/Loss", f"{total_wins[-1] / losses_for_ratio:.2f}"], ["Winrate", f"{total_wins[-1] / trades_for_rate * 100.0:.1f}%"], ["Bar Time", f"{bar_seconds:.0f}s"]]})

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": [], "tables": tables,
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "baseSource": _finite(base_source), "syntheticSource": _finite(synthetic_source), "feature": _finite(feature), "target": _finite(target),
        "gradient": _finite(gradient), "weight": _finite(weight), "loss": _finite(loss), "prediction": _finite(prediction),
        "scaledLoss": _finite(scaled_loss), "scaledPrediction": _finite(scaled_prediction), "volumeOscillator": _finite(volume_oscillator),
        "filterAllowed": filter_allowed.tolist(), "signal": signal.tolist(), "changed": changed.tolist(), "holdingCounter": holding_counter.tolist(),
        "startLong": start_long.tolist(), "startShort": start_short.tolist(), "endLong": end_long.tolist(), "endShort": end_short.tolist(),
        "cumulativeReturn": cumulative_return.tolist(), "totalTrades": total_trades.tolist(), "totalWins": total_wins.tolist(), "resolvedResolution": resolved_resolution,
    },
}
