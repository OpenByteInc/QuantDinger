# ============================================================
# QuantDinger Multiple Divergences
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Multiple Divergences"
my_indicator_description = "Counts confirmed regular and hidden divergences across ten built-in oscillators."

# @param repaint_offset bool false Back-plot markers to pivot bars
# @param minimum_count int 2 Minimum divergence count range=1:10:1
# @param pivot_right int 1 Pivot lookback right range=1:50:1
# @param pivot_left int 3 Pivot lookback left range=1:50:1
# @param range_upper int 60 Maximum pivot-event spacing range=1:500:1
# @param range_lower int 1 Minimum pivot-event spacing range=0:100:1
# @param plot_bullish bool true Plot regular bullish
# @param plot_hidden_bullish bool true Plot hidden bullish
# @param plot_bearish bool true Plot regular bearish
# @param plot_hidden_bearish bool true Plot hidden bearish
# @param use_macd bool true Include MACD
# @param use_macd_histogram bool true Include MACD histogram
# @param use_rsi bool true Include RSI
# @param use_stochastic bool true Include stochastic
# @param use_cci bool true Include CCI
# @param use_momentum bool true Include momentum
# @param use_obv bool true Include OBV
# @param use_diosc bool true Include directional oscillator
# @param use_vwmacd bool true Include volume-weighted MACD
# @param use_cmf bool true Include Chaikin money flow

repaint_offset = bool(params.get("repaint_offset", False))
minimum_count = min(10, max(1, int(params.get("minimum_count", 2))))
pivot_right = min(50, max(1, int(params.get("pivot_right", 1))))
pivot_left = min(50, max(1, int(params.get("pivot_left", 3))))
range_upper = min(500, max(1, int(params.get("range_upper", 60))))
range_lower = min(100, max(0, int(params.get("range_lower", 1))))
plot_bullish = bool(params.get("plot_bullish", True))
plot_hidden_bullish = bool(params.get("plot_hidden_bullish", True))
plot_bearish = bool(params.get("plot_bearish", True))
plot_hidden_bearish = bool(params.get("plot_hidden_bearish", True))
enabled = {
    "macd": bool(params.get("use_macd", True)), "macdHistogram": bool(params.get("use_macd_histogram", True)),
    "rsi": bool(params.get("use_rsi", True)), "stochastic": bool(params.get("use_stochastic", True)),
    "cci": bool(params.get("use_cci", True)), "momentum": bool(params.get("use_momentum", True)),
    "obv": bool(params.get("use_obv", True)), "diosc": bool(params.get("use_diosc", True)),
    "vwmacd": bool(params.get("use_vwmacd", True)), "cmf": bool(params.get("use_cmf", True)),
}


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _rma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    finite_indices = np.flatnonzero(np.isfinite(values))
    if len(finite_indices) < period:
        return result
    seed_indices = finite_indices[:period]
    seed_index = int(seed_indices[-1])
    previous = float(np.mean(values[seed_indices]))
    result[seed_index] = previous
    for index in range(seed_index + 1, len(values)):
        if np.isfinite(values[index]):
            previous = (previous * (period - 1) + float(values[index])) / period
            result[index] = previous
    return result


def _rsi(values, period):
    gains = np.full(len(values), np.nan, dtype=float)
    losses = np.full(len(values), np.nan, dtype=float)
    for index in range(1, len(values)):
        change = values[index] - values[index - 1]
        gains[index], losses[index] = max(change, 0.0), max(-change, 0.0)
    average_gain, average_loss = _rma(gains, period), _rma(losses, period)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        if np.all(np.isfinite([average_gain[index], average_loss[index]])):
            result[index] = 100.0 if average_loss[index] == 0.0 else 0.0 if average_gain[index] == 0.0 else 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])
    return result


def _vwma(values, volumes, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        source_window = values[index - period + 1:index + 1]
        volume_window = volumes[index - period + 1:index + 1]
        denominator = float(np.sum(volume_window))
        if np.all(np.isfinite(source_window)) and np.all(np.isfinite(volume_window)) and abs(denominator) > 1e-12:
            result[index] = float(np.sum(source_window * volume_window) / denominator)
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].fillna(0.0).to_numpy(dtype=float)
n = len(df)

rsi = _rsi(close, 14)
macd = _ema(close, 12) - _ema(close, 26)
macd_signal = _ema(macd, 9)
macd_histogram = macd - macd_signal
momentum = np.full(n, np.nan, dtype=float)
if n > 10:
    momentum[10:] = close[10:] - close[:-10]
cci = np.full(n, np.nan, dtype=float)
for index in range(9, n):
    window = close[index - 9:index + 1]
    average = float(np.mean(window))
    deviation = float(np.mean(np.abs(window - average)))
    cci[index] = (close[index] - average) / (0.015 * deviation) if deviation > 1e-12 else 0.0
obv = np.zeros(n, dtype=float)
for index in range(1, n):
    obv[index] = obv[index - 1] + (volume[index] if close[index] > close[index - 1] else -volume[index] if close[index] < close[index - 1] else 0.0)
raw_stochastic = np.full(n, np.nan, dtype=float)
for index in range(13, n):
    highest = float(np.max(high[index - 13:index + 1]))
    lowest = float(np.min(low[index - 13:index + 1]))
    raw_stochastic[index] = 100.0 * (close[index] - lowest) / (highest - lowest) if highest > lowest else 0.0
stochastic = _sma(raw_stochastic, 3)
true_range = np.full(n, np.nan, dtype=float)
directional = np.full(n, np.nan, dtype=float)
for index in range(n):
    true_range[index] = high[index] - low[index] if index == 0 else max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
    if index >= 1:
        directional[index] = (high[index] - high[index - 1]) + (low[index] - low[index - 1])
smoothed_directional = _rma(directional, 14)
smoothed_range = _rma(true_range, 14)
diosc = np.full(n, np.nan, dtype=float)
last_diosc = np.nan
for index in range(n):
    candidate = 100.0 * smoothed_directional[index] / smoothed_range[index] if np.all(np.isfinite([smoothed_directional[index], smoothed_range[index]])) and abs(smoothed_range[index]) > 1e-12 else np.nan
    if np.isfinite(candidate):
        last_diosc = candidate
    diosc[index] = last_diosc
vwmacd = _vwma(close, volume, 12) - _vwma(close, volume, 26)
money_flow_multiplier = np.full(n, np.nan, dtype=float)
spread = high - low
valid_spread = np.abs(spread) > 1e-12
money_flow_multiplier[valid_spread] = ((close[valid_spread] - low[valid_spread]) - (high[valid_spread] - close[valid_spread])) / spread[valid_spread]
cmf = _sma(money_flow_multiplier * volume, 21) / _sma(volume, 21)

oscillators = {
    "rsi": rsi, "macd": macd, "macdHistogram": macd_histogram, "momentum": momentum, "cci": cci,
    "obv": obv, "stochastic": stochastic, "diosc": diosc, "vwmacd": vwmacd, "cmf": cmf,
}


def _divergences(values):
    regular_bull = np.zeros(n, dtype=bool)
    regular_bear = np.zeros(n, dtype=bool)
    hidden_bull = np.zeros(n, dtype=bool)
    hidden_bear = np.zeros(n, dtype=bool)
    previous_low_event = None
    previous_high_event = None
    previous_low_value = np.nan
    previous_high_value = np.nan
    previous_low_price = np.nan
    previous_high_price = np.nan
    for confirmation in range(pivot_left + pivot_right, n):
        center = confirmation - pivot_right
        window = values[center - pivot_left:center + pivot_right + 1]
        if not np.all(np.isfinite(window)):
            continue
        center_value = values[center]
        low_found = center_value == np.min(window) and np.count_nonzero(window == center_value) == 1
        high_found = center_value == np.max(window) and np.count_nonzero(window == center_value) == 1
        if low_found:
            if previous_low_event is not None:
                spacing = confirmation - previous_low_event - 1
                in_range = range_lower <= spacing <= range_upper
                regular_bull[confirmation] = in_range and center_value > previous_low_value and low[center] < previous_low_price
                hidden_bull[confirmation] = in_range and center_value < previous_low_value and low[center] > previous_low_price
            previous_low_event, previous_low_value, previous_low_price = confirmation, float(center_value), float(low[center])
        if high_found:
            if previous_high_event is not None:
                spacing = confirmation - previous_high_event - 1
                in_range = range_lower <= spacing <= range_upper
                regular_bear[confirmation] = in_range and center_value < previous_high_value and high[center] > previous_high_price
                hidden_bear[confirmation] = in_range and center_value > previous_high_value and high[center] < previous_high_price
            previous_high_event, previous_high_value, previous_high_price = confirmation, float(center_value), float(high[center])
    return regular_bull, regular_bear, hidden_bull, hidden_bear


regular_bull_count = np.zeros(n, dtype=int)
regular_bear_count = np.zeros(n, dtype=int)
hidden_bull_count = np.zeros(n, dtype=int)
hidden_bear_count = np.zeros(n, dtype=int)
per_oscillator = {}
for name, values in oscillators.items():
    divergence = _divergences(values)
    per_oscillator[name] = divergence
    if enabled[name]:
        regular_bull_count += divergence[0].astype(int)
        regular_bear_count += divergence[1].astype(int)
        hidden_bull_count += divergence[2].astype(int)
        hidden_bear_count += divergence[3].astype(int)


def _label(count):
    return "8+" if count > 8 else str(int(count))


def _signal_data(counts, bullish, visible, color):
    data = [None] * n
    if not visible:
        return data
    for confirmation, count in enumerate(counts):
        if int(count) < minimum_count:
            continue
        center = confirmation - pivot_right
        if center < 0:
            continue
        target = center if repaint_offset else confirmation
        price = low[center] if bullish else high[center]
        data[target] = {"active": True, "price": float(price), "text": _label(count), "color": color}
    return data


signals = [
    {"type": "buy", "name": "Regular Bullish Divergence", "data": _signal_data(regular_bull_count, True, plot_bullish, "#008080"), "color": "#008080", "renderMode": "events"},
    {"type": "sell", "name": "Regular Bearish Divergence", "data": _signal_data(regular_bear_count, False, plot_bearish, "#EF4444"), "color": "#EF4444", "renderMode": "events"},
    {"type": "buy", "name": "Hidden Bullish Divergence", "data": _signal_data(hidden_bull_count, True, plot_hidden_bullish, "#16A34A"), "color": "#16A34A", "renderMode": "events"},
    {"type": "sell", "name": "Hidden Bearish Divergence", "data": _signal_data(hidden_bear_count, False, plot_hidden_bearish, "#F59E0B"), "color": "#F59E0B", "renderMode": "events"},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [{"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True}],
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "rsi": _finite(rsi), "macd": _finite(macd), "macdSignal": _finite(macd_signal), "macdHistogram": _finite(macd_histogram),
        "momentum": _finite(momentum), "cci": _finite(cci), "obv": _finite(obv), "stochastic": _finite(stochastic),
        "diosc": _finite(diosc), "vwmacd": _finite(vwmacd), "cmf": _finite(cmf),
        "regularBullCount": regular_bull_count.tolist(), "regularBearCount": regular_bear_count.tolist(),
        "hiddenBullCount": hidden_bull_count.tolist(), "hiddenBearCount": hidden_bear_count.tolist(),
        "enabledOscillators": enabled, "repaintOffset": repaint_offset, "minimumCount": minimum_count,
        "perOscillator": {name: {"regularBull": values[0].tolist(), "regularBear": values[1].tolist(), "hiddenBull": values[2].tolist(), "hiddenBear": values[3].tolist()} for name, values in per_oscillator.items()},
    },
}
