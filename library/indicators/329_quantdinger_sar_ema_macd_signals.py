# ============================================================
# QuantDinger SAR EMA MACD Signals
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger SAR EMA MACD Signals"
my_indicator_description = "Combines standard parabolic SAR direction, a configurable EMA trend filter, and MACD histogram zero crosses."

# @param sar_start float 0.02 SAR initial acceleration range=0.001:1:0.001
# @param sar_increment float 0.02 SAR acceleration increment range=0.001:1:0.001
# @param sar_maximum float 0.2 SAR maximum acceleration range=0.001:2:0.001
# @param ema_length int 200 Trend EMA length range=1:1000:1
# @param ema_source str Close Trend EMA source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param ema_offset int 0 EMA plot offset range=-500:500:1
# @param macd_fast_length int 12 MACD fast length range=1:500:1
# @param macd_slow_length int 26 MACD slow length range=1:500:1
# @param macd_source str Close MACD source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param signal_length int 9 Signal smoothing range=1:50:1
# @param oscillator_ma_type str EMA Oscillator average type values=SMA,EMA
# @param signal_ma_type str EMA Signal average type values=SMA,EMA

sar_start = min(1.0, max(0.001, float(params.get("sar_start", 0.02))))
sar_increment = min(1.0, max(0.001, float(params.get("sar_increment", 0.02))))
sar_maximum = min(2.0, max(0.001, float(params.get("sar_maximum", 0.2))))
ema_length = min(1000, max(1, int(params.get("ema_length", 200))))
ema_source_name = str(params.get("ema_source", "Close")).upper()
macd_source_name = str(params.get("macd_source", "Close")).upper()
valid_sources = ("CLOSE", "OPEN", "HIGH", "LOW", "HL2", "HLC3", "OHLC4")
if ema_source_name not in valid_sources:
    ema_source_name = "CLOSE"
if macd_source_name not in valid_sources:
    macd_source_name = "CLOSE"
ema_offset = min(500, max(-500, int(params.get("ema_offset", 0))))
macd_fast_length = min(500, max(1, int(params.get("macd_fast_length", 12))))
macd_slow_length = min(500, max(1, int(params.get("macd_slow_length", 26))))
signal_length = min(50, max(1, int(params.get("signal_length", 9))))
oscillator_ma_type = str(params.get("oscillator_ma_type", "EMA")).upper()
signal_ma_type = str(params.get("signal_ma_type", "EMA")).upper()
if oscillator_ma_type not in ("SMA", "EMA"):
    oscillator_ma_type = "EMA"
if signal_ma_type not in ("SMA", "EMA"):
    signal_ma_type = "EMA"


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
n = len(df)
sources = {
    "OPEN": opened, "HIGH": high, "LOW": low, "CLOSE": close,
    "HL2": (high + low) / 2.0, "HLC3": (high + low + close) / 3.0,
    "OHLC4": (opened + high + low + close) / 4.0,
}
ema_source = sources[ema_source_name]
macd_source = sources[macd_source_name]

sar = np.full(n, np.nan, dtype=float)
sar_below = np.zeros(n, dtype=bool)
sar_acceleration = np.full(n, np.nan, dtype=float)
result = extreme = acceleration = np.nan
below = False
for index in range(n):
    first_trend_bar = False
    if index == 1:
        if close[index] > close[index - 1]:
            below, extreme, result = True, high[index], low[index - 1]
        else:
            below, extreme, result = False, low[index], high[index - 1]
        first_trend_bar, acceleration = True, sar_start
    if np.all(np.isfinite([result, acceleration, extreme])):
        result = result + acceleration * (extreme - result)
    if below and np.isfinite(result) and result > low[index]:
        first_trend_bar, below = True, False
        result, extreme, acceleration = max(high[index], extreme), low[index], sar_start
    elif (not below) and np.isfinite(result) and result < high[index]:
        first_trend_bar, below = True, True
        result, extreme, acceleration = min(low[index], extreme), high[index], sar_start
    if not first_trend_bar:
        if below and np.isfinite(extreme) and high[index] > extreme:
            extreme, acceleration = high[index], min(acceleration + sar_increment, sar_maximum)
        elif (not below) and np.isfinite(extreme) and low[index] < extreme:
            extreme, acceleration = low[index], min(acceleration + sar_increment, sar_maximum)
    if below and np.isfinite(result):
        if index >= 1:
            result = min(result, low[index - 1])
        if index > 1:
            result = min(result, low[index - 2])
    elif (not below) and np.isfinite(result):
        if index >= 1:
            result = max(result, high[index - 1])
        if index > 1:
            result = max(result, high[index - 2])
    sar[index], sar_below[index], sar_acceleration[index] = result, below, acceleration

trend_ema = _ema(ema_source, ema_length)
fast = _sma(macd_source, macd_fast_length) if oscillator_ma_type == "SMA" else _ema(macd_source, macd_fast_length)
slow = _sma(macd_source, macd_slow_length) if oscillator_ma_type == "SMA" else _ema(macd_source, macd_slow_length)
macd = fast - slow
signal = _sma(macd, signal_length) if signal_ma_type == "SMA" else _ema(macd, signal_length)
histogram = macd - signal
macd_long = np.zeros(n, dtype=bool)
macd_short = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.all(np.isfinite([histogram[index], histogram[index - 1]])):
        macd_long[index] = histogram[index] > 0.0 and histogram[index - 1] <= 0.0
        macd_short[index] = histogram[index] < 0.0 and histogram[index - 1] >= 0.0
ohlc4 = (opened + high + low + close) / 4.0
bull_trend = ohlc4 >= trend_ema
sar_long = sar < low
sar_short = sar > high
buy_signal = sar_long & macd_long & bull_trend
sell_signal = sar_short & macd_short & ~bull_trend

shifted_ema = np.full(n, np.nan, dtype=float)
for source_index, value in enumerate(trend_ema):
    target_index = source_index + ema_offset
    if 0 <= target_index < n:
        shifted_ema[target_index] = value

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Parabolic SAR", "data": _finite(sar), "color": "#2962FF", "type": "circle", "size": 3, "overlay": True},
        {"name": "EMA", "data": _finite(shifted_ema), "color": "#0000FF", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Buy", "text": "Buy", "data": [float(low[index]) if buy_signal[index] else None for index in range(n)], "color": "#008000", "renderMode": "events"},
        {"type": "sell", "name": "Sell", "text": "Sell", "data": [float(high[index]) if sell_signal[index] else None for index in range(n)], "color": "#FF0000", "renderMode": "events"},
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "sar": _finite(sar), "sarBelow": sar_below.tolist(), "sarAcceleration": _finite(sar_acceleration),
        "trendEma": _finite(trend_ema), "shiftedEma": _finite(shifted_ema),
        "fastMa": _finite(fast), "slowMa": _finite(slow), "macd": _finite(macd), "signal": _finite(signal), "histogram": _finite(histogram),
        "bullTrend": bull_trend.tolist(), "sarLong": sar_long.tolist(), "sarShort": sar_short.tolist(),
        "macdLong": macd_long.tolist(), "macdShort": macd_short.tolist(), "buySignal": buy_signal.tolist(), "sellSignal": sell_signal.tolist(),
        "emaSource": ema_source_name, "macdSource": macd_source_name, "oscillatorMaType": oscillator_ma_type, "signalMaType": signal_ma_type,
    },
}
