# ============================================================
# QuantDinger Momentum ZigZag
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Momentum ZigZag"
my_indicator_description = "Non-repainting momentum-turn ZigZag using MACD, moving-average reversals or QQE, with force filtering and stop references."

# @param color_force_direction bool true Color force direction
# @param momentum_indicator str QQE Momentum engine values=MACD,MovingAverage,QQE
# @param macd_fast_length int 12 MACD fast length range=1:500:1
# @param macd_slow_length int 26 MACD slow length range=1:500:1
# @param macd_source str Close MACD source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param macd_signal_length int 9 MACD signal length range=1:50:1
# @param macd_oscillator_ma str EMA MACD oscillator MA values=SMA,EMA
# @param macd_signal_ma str EMA MACD signal MA values=SMA,EMA
# @param average_type str SMA Reversal average values=EMA,SMA,WMA,VWMA,HMA,RMA,DEMA
# @param average_length int 20 Reversal average length range=1:500:1
# @param rsi_length int 14 QQE RSI length range=1:500:1
# @param qqe_factor float 4.238 QQE factor range=0:100:0.001
# @param rsi_smoothing int 5 QQE RSI smoothing range=1:500:1
# @param threshold int 10 QQE threshold range=0:100:1
# @param take_profit_level int 200 Alert take-profit level range=0:1000000:1

color_force_direction = bool(params.get("color_force_direction", True))
momentum_indicator = str(params.get("momentum_indicator", "QQE"))
if momentum_indicator not in ("MACD", "MovingAverage", "QQE"):
    momentum_indicator = "QQE"
macd_fast_length = min(500, max(1, int(params.get("macd_fast_length", 12))))
macd_slow_length = min(500, max(1, int(params.get("macd_slow_length", 26))))
macd_source = str(params.get("macd_source", "Close")).upper()
if macd_source not in ("CLOSE", "OPEN", "HIGH", "LOW", "HL2", "HLC3", "OHLC4"):
    macd_source = "CLOSE"
macd_signal_length = min(50, max(1, int(params.get("macd_signal_length", 9))))
macd_oscillator_ma = str(params.get("macd_oscillator_ma", "EMA")).upper()
if macd_oscillator_ma not in ("SMA", "EMA"):
    macd_oscillator_ma = "EMA"
macd_signal_ma = str(params.get("macd_signal_ma", "EMA")).upper()
if macd_signal_ma not in ("SMA", "EMA"):
    macd_signal_ma = "EMA"
average_type = str(params.get("average_type", "SMA")).upper()
if average_type not in ("EMA", "SMA", "WMA", "VWMA", "HMA", "RMA", "DEMA"):
    average_type = "SMA"
average_length = min(500, max(1, int(params.get("average_length", 20))))
rsi_length = min(500, max(1, int(params.get("rsi_length", 14))))
qqe_factor = max(0.0, float(params.get("qqe_factor", 4.238)))
rsi_smoothing = min(500, max(1, int(params.get("rsi_smoothing", 5))))
threshold = min(100, max(0, int(params.get("threshold", 10))))
take_profit_level = min(1_000_000, max(0, int(params.get("take_profit_level", 200))))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _sma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _wma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.sum(window * weights) / denominator)
    return result


def _vwma(values, volume_values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        source_window = values[index - period + 1:index + 1]
        volume_window = volume_values[index - period + 1:index + 1]
        denominator = float(np.sum(volume_window))
        if np.all(np.isfinite(source_window)) and denominator != 0.0:
            result[index] = float(np.sum(source_window * volume_window) / denominator)
    return result


def _rma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    finite_indices = [index for index, value in enumerate(values) if np.isfinite(value)]
    if len(finite_indices) < period:
        return result
    seed_indices = finite_indices[:period]
    seed_index = seed_indices[-1]
    result[seed_index] = float(np.mean(values[seed_indices]))
    previous = result[seed_index]
    for index in range(seed_index + 1, len(values)):
        if np.isfinite(values[index]):
            previous = (previous * (period - 1) + float(values[index])) / period
            result[index] = previous
    return result


def _moving_average(values, volume_values, period, kind):
    if kind == "SMA":
        return _sma(values, period)
    if kind == "WMA":
        return _wma(values, period)
    if kind == "VWMA":
        return _vwma(values, volume_values, period)
    if kind == "RMA":
        return _rma(values, period)
    if kind == "HMA":
        half = max(1, int(period / 2))
        root = max(1, int(math.floor(math.sqrt(period) + 0.5)))
        return _wma(2.0 * _wma(values, half) - _wma(values, period), root)
    first = _ema(values, period)
    if kind == "DEMA":
        return 2.0 * first - _ema(first, period)
    return first


def _rsi(values, period):
    change = np.full(len(values), np.nan, dtype=float)
    gain = np.full(len(values), np.nan, dtype=float)
    loss = np.full(len(values), np.nan, dtype=float)
    for index in range(1, len(values)):
        change[index] = values[index] - values[index - 1]
        gain[index] = max(change[index], 0.0)
        loss[index] = max(-change[index], 0.0)
    average_gain = _rma(gain, period)
    average_loss = _rma(loss, period)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        if not np.isfinite(average_gain[index]) or not np.isfinite(average_loss[index]):
            continue
        if average_loss[index] == 0.0:
            result[index] = 100.0 if average_gain[index] > 0.0 else 50.0
        elif average_gain[index] == 0.0:
            result[index] = 0.0
        else:
            result[index] = 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])
    return result


def _cross_over(first, second, index):
    return index >= 1 and np.all(np.isfinite([first[index], first[index - 1], second[index], second[index - 1]])) and first[index] > second[index] and first[index - 1] <= second[index - 1]


def _cross_under(first, second, index):
    return index >= 1 and np.all(np.isfinite([first[index], first[index - 1], second[index], second[index - 1]])) and first[index] < second[index] and first[index - 1] >= second[index - 1]


def _barssince(condition):
    result = np.full(len(condition), np.nan, dtype=float)
    last = -1
    for index, value in enumerate(condition):
        if value:
            last = index
        if last >= 0:
            result[index] = index - last
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close", "volume"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].to_numpy(dtype=float)
n = len(df)

source = {"OPEN": opened, "HIGH": high, "LOW": low, "HL2": (high + low) / 2.0, "HLC3": (high + low + close) / 3.0, "OHLC4": (opened + high + low + close) / 4.0}.get(macd_source, close)
fast_ma = _sma(source, macd_fast_length) if macd_oscillator_ma == "SMA" else _ema(source, macd_fast_length)
slow_ma = _sma(source, macd_slow_length) if macd_oscillator_ma == "SMA" else _ema(source, macd_slow_length)
macd = fast_ma - slow_ma
macd_signal = _sma(macd, macd_signal_length) if macd_signal_ma == "SMA" else _ema(macd, macd_signal_length)
macd_up = np.zeros(n, dtype=bool)
macd_down = np.zeros(n, dtype=bool)
for index in range(n):
    macd_up[index] = _cross_over(macd, macd_signal, index)
    macd_down[index] = _cross_under(macd, macd_signal, index)

moving_average = _moving_average(close, volume, average_length, average_type)
ma_up = np.zeros(n, dtype=bool)
ma_down = np.zeros(n, dtype=bool)
for index in range(2, n):
    if np.all(np.isfinite(moving_average[index - 2:index + 1])):
        ma_up[index] = moving_average[index] > moving_average[index - 1] and moving_average[index - 2] > moving_average[index - 1]
        ma_down[index] = moving_average[index] < moving_average[index - 1] and moving_average[index - 2] < moving_average[index - 1]

rsi = _rsi(close, rsi_length)
rsi_ma = _ema(rsi, rsi_smoothing)
atr_rsi = np.full(n, np.nan, dtype=float)
for index in range(1, n):
    if np.isfinite(rsi_ma[index]) and np.isfinite(rsi_ma[index - 1]):
        atr_rsi[index] = abs(rsi_ma[index - 1] - rsi_ma[index])
wilders_period = rsi_length * 2 - 1
ma_atr_rsi = _ema(atr_rsi, wilders_period)
dar = _ema(ma_atr_rsi, wilders_period) * qqe_factor
long_band = np.zeros(n, dtype=float)
short_band = np.zeros(n, dtype=float)
trend = np.ones(n, dtype=int)
last_qqe_high = np.zeros(n, dtype=float)
last_qqe_low = np.zeros(n, dtype=float)
qqe_long_count = np.zeros(n, dtype=int)
qqe_short_count = np.zeros(n, dtype=int)
qqe_up = np.zeros(n, dtype=bool)
qqe_down = np.zeros(n, dtype=bool)
qqe_line = np.full(n, np.nan, dtype=float)
for index in range(n):
    previous_long_band = long_band[index - 1] if index else 0.0
    previous_short_band = short_band[index - 1] if index else 0.0
    new_long = rsi_ma[index] - dar[index] if np.all(np.isfinite([rsi_ma[index], dar[index]])) else np.nan
    new_short = rsi_ma[index] + dar[index] if np.all(np.isfinite([rsi_ma[index], dar[index]])) else np.nan
    if index >= 1 and np.all(np.isfinite([rsi_ma[index - 1], rsi_ma[index], new_long])) and rsi_ma[index - 1] > previous_long_band and rsi_ma[index] > previous_long_band:
        long_band[index] = max(previous_long_band, new_long)
    else:
        long_band[index] = new_long if np.isfinite(new_long) else 0.0
    if index >= 1 and np.all(np.isfinite([rsi_ma[index - 1], rsi_ma[index], new_short])) and rsi_ma[index - 1] < previous_short_band and rsi_ma[index] < previous_short_band:
        short_band[index] = min(previous_short_band, new_short)
    else:
        short_band[index] = new_short if np.isfinite(new_short) else 0.0

    seen_long = np.flatnonzero(qqe_long_count[:index] == 1)
    seen_short = np.flatnonzero(qqe_short_count[:index] == 1)
    bars_long = index - 1 - seen_long[-1] if len(seen_long) else np.nan
    bars_short = index - 1 - seen_short[-1] if len(seen_short) else np.nan
    going_up = np.isfinite(bars_long) and np.isfinite(bars_short) and bars_long < bars_short
    going_down = np.isfinite(bars_long) and np.isfinite(bars_short) and bars_long > bars_short
    previous_going_up = False
    previous_going_down = False
    if index >= 1:
        prior_long_events = np.flatnonzero(qqe_long_count[:index - 1] == 1)
        prior_short_events = np.flatnonzero(qqe_short_count[:index - 1] == 1)
        if len(prior_long_events) and len(prior_short_events):
            prior_long_distance = index - 2 - prior_long_events[-1]
            prior_short_distance = index - 2 - prior_short_events[-1]
            previous_going_up = prior_long_distance < prior_short_distance
            previous_going_down = prior_long_distance > prior_short_distance
    previous_high = last_qqe_high[index - 1] if index else np.nan
    previous_low = last_qqe_low[index - 1] if index else np.nan
    last_qqe_high[index] = high[index] if ((np.isfinite(previous_high) and high[index] > previous_high and going_up) or (previous_going_down and going_up)) else (previous_high if np.isfinite(previous_high) else 0.0)
    last_qqe_low[index] = low[index] if ((np.isfinite(previous_low) and low[index] < previous_low and going_down) or (previous_going_up and going_down)) else (previous_low if np.isfinite(previous_low) else 0.0)

    cross_up_band = index >= 1 and np.all(np.isfinite([rsi_ma[index], rsi_ma[index - 1], short_band[index - 1], short_band[index - 2] if index >= 2 else np.nan])) and rsi_ma[index] > short_band[index - 1] and rsi_ma[index - 1] <= short_band[index - 2]
    cross_down_band = index >= 1 and np.all(np.isfinite([rsi_ma[index], rsi_ma[index - 1], long_band[index - 1], long_band[index - 2] if index >= 2 else np.nan])) and rsi_ma[index] < long_band[index - 1] and rsi_ma[index - 1] >= long_band[index - 2]
    cross_high = index >= 1 and high[index] > last_qqe_high[index] and high[index - 1] <= last_qqe_high[index - 1]
    cross_low = index >= 1 and low[index] < last_qqe_low[index] and low[index - 1] >= last_qqe_low[index - 1]
    previous_trend = trend[index - 1] if index else 1
    trend[index] = 1 if cross_up_band or cross_high else -1 if cross_down_band or cross_low else previous_trend
    qqe_line[index] = long_band[index] if trend[index] == 1 else short_band[index]
    qqe_long_count[index] = 1 if trend[index] == 1 and index >= 1 and trend[index - 1] == -1 else 0
    qqe_short_count[index] = 1 if trend[index] == -1 and index >= 1 and trend[index - 1] == 1 else 0
    previous_line = qqe_line[index - 1] - 50.0 if index >= 1 and np.isfinite(qqe_line[index - 1]) else np.nan
    qqe_up[index] = qqe_long_count[index] == 1 and np.isfinite(previous_line) and previous_line != 0.0
    qqe_down[index] = qqe_short_count[index] == 1 and np.isfinite(previous_line) and previous_line != 0.0

momentum_up = macd_up if momentum_indicator == "MACD" else ma_up if momentum_indicator == "MovingAverage" else qqe_up
momentum_down = macd_down if momentum_indicator == "MACD" else ma_down if momentum_indicator == "MovingAverage" else qqe_down
momentum_direction = np.zeros(n, dtype=int)
for index in range(n):
    momentum_direction[index] = 1 if momentum_up[index] else -1 if momentum_down[index] else momentum_direction[index - 1] if index else 0

rsi5 = _rsi(close, 5)
bars_up = _barssince(momentum_up)
bars_down = _barssince(momentum_down)
bars_overbought = _barssince(rsi5 > 80.0)
bars_oversold = _barssince(rsi5 < 20.0)
force_up = np.zeros(n, dtype=bool)
force_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    force_up[index] = momentum_down[index] and np.isfinite(bars_up[index - 1]) and np.isfinite(bars_overbought[index - 1]) and bars_up[index - 1] >= bars_overbought[index - 1]
    force_down[index] = momentum_up[index] and np.isfinite(bars_down[index - 1]) and np.isfinite(bars_oversold[index - 1]) and bars_down[index - 1] >= bars_oversold[index - 1]

peak = np.zeros(n, dtype=float)
bottom = np.zeros(n, dtype=float)
zigzag = np.full(n, np.nan, dtype=float)
for index in range(n):
    going_up = momentum_direction[index] == 1
    going_down = momentum_direction[index] == -1
    previous_up = index >= 1 and momentum_direction[index - 1] == 1
    previous_down = index >= 1 and momentum_direction[index - 1] == -1
    previous_peak = peak[index - 1] if index else np.nan
    previous_bottom = bottom[index - 1] if index else np.nan
    peak[index] = high[index] if ((np.isfinite(previous_peak) and high[index] > previous_peak and going_up) or (previous_down and going_up)) else (previous_peak if np.isfinite(previous_peak) else 0.0)
    bottom[index] = low[index] if ((np.isfinite(previous_bottom) and low[index] < previous_bottom and going_down) or (previous_up and going_down)) else (previous_bottom if np.isfinite(previous_bottom) else 0.0)
    if going_up and previous_down:
        zigzag[index] = previous_bottom
    elif previous_up and going_down:
        zigzag[index] = previous_peak

go_short = momentum_down & ~force_up
go_long = momentum_up & ~force_down
last_pivot_low = np.full(n, np.nan, dtype=float)
last_pivot_high = np.full(n, np.nan, dtype=float)
stop_long = np.full(n, np.nan, dtype=float)
stop_short = np.full(n, np.nan, dtype=float)
stored_low = low[0] if n else np.nan
stored_high = high[0] if n else np.nan
current_pl = np.nan
current_ph = np.nan
for index in range(n):
    if momentum_up[index] and np.isfinite(zigzag[index]):
        current_pl = zigzag[index]
    if momentum_down[index] and np.isfinite(zigzag[index]):
        current_ph = zigzag[index]
    last_pivot_low[index] = current_pl
    last_pivot_high[index] = current_ph
    if go_long[index]:
        stored_low = low[index] if np.isfinite(current_pl) and low[index] < current_pl else current_pl
    if go_short[index]:
        stored_high = high[index] if np.isfinite(current_ph) and high[index] > current_ph else current_ph
    stop_long[index] = stored_low
    stop_short[index] = stored_high

zigzag_colors = []
for index in range(n):
    if not color_force_direction:
        zigzag_colors.append("#000000")
    elif force_up[index]:
        zigzag_colors.append("#00FF00")
    elif force_down[index]:
        zigzag_colors.append("#FF0000")
    else:
        zigzag_colors.append("#000000")

signals = [
    {"type": "buy", "name": "Long Pivot", "text": "LONG", "data": [float(zigzag[index]) if go_long[index] and np.isfinite(zigzag[index]) else None for index in range(n)], "color": "#00FF00", "renderMode": "events"},
    {"type": "sell", "name": "Short Pivot", "text": "SHORT", "data": [float(zigzag[index]) if go_short[index] and np.isfinite(zigzag[index]) else None for index in range(n)], "color": "#FF0000", "renderMode": "events"},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Momentum ZigZag", "data": _finite(zigzag), "color": zigzag_colors, "type": "line", "lineWidth": 5, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "fastMA": _finite(fast_ma), "slowMA": _finite(slow_ma), "macd": _finite(macd), "macdSignal": _finite(macd_signal), "macdUp": macd_up.tolist(), "macdDown": macd_down.tolist(),
        "movingAverage": _finite(moving_average), "maUp": ma_up.tolist(), "maDown": ma_down.tolist(),
        "rsi": _finite(rsi), "rsiMA": _finite(rsi_ma), "atrRSI": _finite(atr_rsi), "maAtrRSI": _finite(ma_atr_rsi), "dar": _finite(dar),
        "longBand": _finite(long_band), "shortBand": _finite(short_band), "qqeTrend": trend.tolist(), "qqeLine": _finite(qqe_line), "qqeUp": qqe_up.tolist(), "qqeDown": qqe_down.tolist(),
        "momentumUp": momentum_up.tolist(), "momentumDown": momentum_down.tolist(), "momentumDirection": momentum_direction.tolist(),
        "rsi5": _finite(rsi5), "forceUp": force_up.tolist(), "forceDown": force_down.tolist(), "peak": _finite(peak), "bottom": _finite(bottom), "zigzag": _finite(zigzag),
        "goLong": go_long.tolist(), "goShort": go_short.tolist(), "lastPivotLow": _finite(last_pivot_low), "lastPivotHigh": _finite(last_pivot_high),
        "stoplossLong": _finite(stop_long), "stoplossShort": _finite(stop_short), "takeProfitLevel": int(take_profit_level), "momentumIndicator": momentum_indicator,
    },
}
