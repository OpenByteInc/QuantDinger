# ============================================================
# QuantDinger Normalized Quantitative Qualitative Estimation
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Normalized QQE"
my_indicator_description = "Centers smoothed RSI and its recursive QQE trailing level around zero."

# @param source str Close Price source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param rsi_length int 14 RSI length range=1:500:1
# @param smoothing int 5 RSI smoothing factor range=1:500:1
# @param show_signals bool false Show crossing signals

source_name = str(params.get("source", "Close")).upper()
if source_name not in ("CLOSE", "OPEN", "HIGH", "LOW", "HL2", "HLC3", "OHLC4"):
    source_name = "CLOSE"
rsi_length = min(500, max(1, int(params.get("rsi_length", 14))))
smoothing = min(500, max(1, int(params.get("smoothing", 5))))
show_signals = bool(params.get("show_signals", False))


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
source = {"OPEN": opened, "HIGH": high, "LOW": low, "HL2": (high + low) / 2.0, "HLC3": (high + low + close) / 3.0, "OHLC4": (opened + high + low + close) / 4.0}.get(source_name, close)

rsi = _rsi(source, rsi_length)
qqe_fast = _ema(rsi, smoothing)
true_range_rsi = np.full(n, np.nan, dtype=float)
for index in range(1, n):
    if np.all(np.isfinite([qqe_fast[index], qqe_fast[index - 1]])):
        true_range_rsi[index] = abs(qqe_fast[index] - qqe_fast[index - 1])
alpha = 1.0 / rsi_length
wwma = np.full(n, np.nan, dtype=float)
atr_rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    previous_wwma = wwma[index - 1] if index >= 1 and np.isfinite(wwma[index - 1]) else 0.0
    if np.isfinite(true_range_rsi[index]):
        wwma[index] = alpha * true_range_rsi[index] + (1.0 - alpha) * previous_wwma
    previous_atr = atr_rsi[index - 1] if index >= 1 and np.isfinite(atr_rsi[index - 1]) else 0.0
    if np.isfinite(wwma[index]):
        atr_rsi[index] = alpha * wwma[index] + (1.0 - alpha) * previous_atr

upper_band = qqe_fast + atr_rsi * 4.236
lower_band = qqe_fast - atr_rsi * 4.236
qqe_slow = np.zeros(n, dtype=float)
for index in range(n):
    previous_slow = qqe_slow[index - 1] if index >= 1 and np.isfinite(qqe_slow[index - 1]) else 0.0
    previous_fast = qqe_fast[index - 1] if index >= 1 else np.nan
    if np.isfinite(upper_band[index]) and upper_band[index] < previous_slow:
        qqe_slow[index] = upper_band[index]
    elif np.isfinite(qqe_fast[index]) and np.isfinite(previous_fast) and qqe_fast[index] > previous_slow and previous_fast < previous_slow:
        qqe_slow[index] = lower_band[index] if np.isfinite(lower_band[index]) else previous_slow
    elif np.isfinite(lower_band[index]) and lower_band[index] > previous_slow:
        qqe_slow[index] = lower_band[index]
    elif np.isfinite(qqe_fast[index]) and np.isfinite(previous_fast) and qqe_fast[index] < previous_slow and previous_fast > previous_slow:
        qqe_slow[index] = upper_band[index] if np.isfinite(upper_band[index]) else previous_slow
    else:
        qqe_slow[index] = previous_slow

buy_cross = np.zeros(n, dtype=bool)
sell_cross = np.zeros(n, dtype=bool)
zero_up = np.zeros(n, dtype=bool)
zero_down = np.zeros(n, dtype=bool)
plus_ten_up = np.zeros(n, dtype=bool)
minus_ten_down = np.zeros(n, dtype=bool)
sideways = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.all(np.isfinite([qqe_fast[index], qqe_fast[index - 1], qqe_slow[index], qqe_slow[index - 1]])):
        buy_cross[index] = qqe_fast[index] > qqe_slow[index] and qqe_fast[index - 1] <= qqe_slow[index - 1]
        sell_cross[index] = qqe_fast[index] < qqe_slow[index] and qqe_fast[index - 1] >= qqe_slow[index - 1]
    if np.all(np.isfinite([qqe_fast[index], qqe_fast[index - 1]])):
        zero_up[index] = qqe_fast[index] > 50.0 and qqe_fast[index - 1] <= 50.0
        zero_down[index] = qqe_fast[index] < 50.0 and qqe_fast[index - 1] >= 50.0
        plus_ten_up[index] = qqe_fast[index] > 60.0 and qqe_fast[index - 1] <= 60.0
        minus_ten_down[index] = qqe_fast[index] < 40.0 and qqe_fast[index - 1] >= 40.0
        sideways[index] = (qqe_fast[index] < 60.0 and qqe_fast[index - 1] >= 60.0) or (qqe_fast[index] > 40.0 and qqe_fast[index - 1] <= 40.0)

fast_centered = qqe_fast - 50.0
slow_centered = qqe_slow - 50.0
fast_colors = ["#007002" if np.isfinite(value) and value > 10.0 else "#FF0000" if np.isfinite(value) and value < -10.0 else "#E8E81A" for value in fast_centered]
buy_points = [{"value": float(slow_centered[index] * 0.995), "color": "#16A34A", "size": 5} if show_signals and buy_cross[index] else None for index in range(n)]
sell_points = [{"value": float(slow_centered[index] * 1.005), "color": "#EF4444", "size": 5} if show_signals and sell_cross[index] else None for index in range(n)]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "FAST", "data": _finite(fast_centered), "color": "#800000", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "FAST State", "title": False, "data": _finite(fast_centered), "color": fast_colors, "type": "circle", "size": 3, "overlay": False},
        {"name": "SLOW", "data": _finite(slow_centered), "color": "#0007E1", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Upper Threshold", "data": [10.0] * n, "color": "#808080", "type": "line", "style": "dashed", "lineWidth": 1, "overlay": False},
        {"name": "Lower Threshold", "data": [-10.0] * n, "color": "#808080", "type": "line", "style": "dashed", "lineWidth": 1, "overlay": False},
        {"name": "Buy", "data": buy_points, "color": "#16A34A", "type": "circle", "size": 5, "overlay": False},
        {"name": "Sell", "data": sell_points, "color": "#EF4444", "type": "circle", "size": 5, "overlay": False},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "rsi": _finite(rsi), "qqeFast": _finite(qqe_fast), "rsiTrueRange": _finite(true_range_rsi), "wwma": _finite(wwma), "atrRsi": _finite(atr_rsi),
        "upperBand": _finite(upper_band), "lowerBand": _finite(lower_band), "qqeSlow": _finite(qqe_slow), "fastCentered": _finite(fast_centered), "slowCentered": _finite(slow_centered),
        "buyCross": buy_cross.tolist(), "sellCross": sell_cross.tolist(), "zeroCrossUp": zero_up.tolist(), "zeroCrossDown": zero_down.tolist(),
        "plusTenCrossUp": plus_ten_up.tolist(), "minusTenCrossDown": minus_ten_down.tolist(), "sidewaysEvent": sideways.tolist(), "source": source_name,
    },
}
