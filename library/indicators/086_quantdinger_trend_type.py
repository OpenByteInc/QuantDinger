# ============================================================
# QuantDinger Trend Type Regime
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Trend Type Regime"
my_indicator_description = (
    "Classifies each bar as an uptrend, sideways regime, or downtrend by "
    "combining ATR contraction with ADX and directional movement."
)

# @param use_atr bool true Use ATR contraction for sideways detection
# @param atr_length int 14 ATR length range=1:100:1
# @param atr_ma_type int 0 ATR average: 0 SMA, 1 EMA range=0:1:1
# @param atr_ma_length int 20 ATR average length range=1:200:1
# @param use_adx bool true Use ADX for sideways detection
# @param adx_length int 14 ADX smoothing range=1:50:1
# @param di_length int 14 Directional movement length range=1:100:1
# @param adx_limit float 25.0 Sideways ADX limit range=1.0:60.0:0.5
# @param smoothing int 3 Regime smoothing range=1:5:1
# @param lag int 8 Display lag range=0:15:1

use_atr = bool(params.get("use_atr", True))
atr_length = max(1, int(params.get("atr_length", 14)))
atr_ma_type = min(1, max(0, int(params.get("atr_ma_type", 0))))
atr_ma_length = max(1, int(params.get("atr_ma_length", 20)))
use_adx = bool(params.get("use_adx", True))
adx_length = max(1, int(params.get("adx_length", 14)))
di_length = max(1, int(params.get("di_length", 14)))
adx_limit = max(1.0, float(params.get("adx_limit", 25.0)))
smoothing = min(5, max(1, int(params.get("smoothing", 3))))
lag = min(15, max(0, int(params.get("lag", 8))))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def pine_rma(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    seed_values = []
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            if np.isfinite(previous):
                result[index] = previous
            continue
        if not np.isfinite(previous):
            seed_values.append(float(value))
            if len(seed_values) < period:
                continue
            previous = float(np.mean(seed_values[-period:]))
        else:
            previous = (previous * (period - 1) + float(value)) / period
        result[index] = previous
    return result


def pine_ema(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def rolling_mean(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(period - 1, len(source)):
        window = source[index - period + 1 : index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


previous_close = np.concatenate(([close[0]], close[:-1])) if n else np.array([], dtype=float)
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close)))
atr = pine_rma(true_range, atr_length)
atr_average = pine_ema(atr, atr_ma_length) if atr_ma_type == 1 else rolling_mean(atr, atr_ma_length)

up_move = np.full(n, np.nan, dtype=float)
down_move = np.full(n, np.nan, dtype=float)
if n > 1:
    up_move[1:] = high[1:] - high[:-1]
    down_move[1:] = low[:-1] - low[1:]

plus_dm = np.full(n, np.nan, dtype=float)
minus_dm = np.full(n, np.nan, dtype=float)
for index in range(1, n):
    plus_dm[index] = up_move[index] if up_move[index] > down_move[index] and up_move[index] > 0.0 else 0.0
    minus_dm[index] = down_move[index] if down_move[index] > up_move[index] and down_move[index] > 0.0 else 0.0

smoothed_tr = pine_rma(true_range, di_length)
smoothed_plus = pine_rma(plus_dm, di_length)
smoothed_minus = pine_rma(minus_dm, di_length)
plus_di = np.full(n, np.nan, dtype=float)
minus_di = np.full(n, np.nan, dtype=float)
valid_tr = np.isfinite(smoothed_tr) & (smoothed_tr != 0.0)
plus_di[valid_tr] = 100.0 * smoothed_plus[valid_tr] / smoothed_tr[valid_tr]
minus_di[valid_tr] = 100.0 * smoothed_minus[valid_tr] / smoothed_tr[valid_tr]

directional_ratio = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.isfinite(plus_di[index]) or not np.isfinite(minus_di[index]):
        continue
    denominator = plus_di[index] + minus_di[index]
    directional_ratio[index] = abs(plus_di[index] - minus_di[index]) / (denominator if denominator != 0.0 else 1.0)
adx = 100.0 * pine_rma(directional_ratio, adx_length)

regime = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not all(np.isfinite(value) for value in (atr[index], adx[index], plus_di[index], minus_di[index])):
        continue
    atr_sideways = use_atr and np.isfinite(atr_average[index]) and atr[index] <= atr_average[index]
    adx_sideways = use_adx and adx[index] <= adx_limit
    if atr_sideways or adx_sideways:
        regime[index] = 0.0
    else:
        regime[index] = 2.0 if plus_di[index] > minus_di[index] else -2.0

smoothed_regime = rolling_mean(regime, smoothing)
for index in range(n):
    if not np.isfinite(smoothed_regime[index]):
        continue
    scaled = smoothed_regime[index] / 2.0
    rounded = np.floor(scaled + 0.5) if scaled >= 0.0 else np.ceil(scaled - 0.5)
    smoothed_regime[index] = float(rounded * 2.0)

display_regime = np.full(n, np.nan, dtype=float)
for index in range(n):
    target = index - lag
    if target >= 0 and np.isfinite(smoothed_regime[index]):
        display_regime[target] = smoothed_regime[index]

regime_plot = []
for value in display_regime:
    if not np.isfinite(value):
        regime_plot.append(None)
    elif value > 0.0:
        regime_plot.append({"value": float(value), "color": "rgba(22, 163, 74, 0.72)"})
    elif value < 0.0:
        regime_plot.append({"value": float(value), "color": "rgba(220, 38, 38, 0.72)"})
    else:
        regime_plot.append({"value": 0.0, "color": "rgba(107, 114, 128, 0.80)"})

uptrend_zone = [{"upper": 3.0, "lower": 1.0, "color": "#16A34A"} for _ in range(n)]
sideways_zone = [{"upper": 1.0, "lower": -1.0, "color": "#9CA3AF"} for _ in range(n)]
downtrend_zone = [{"upper": -1.0, "lower": -3.0, "color": "#DC2626"} for _ in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Uptrend Area", "title": False, "data": uptrend_zone, "color": "#16A34A", "type": "cloud", "opacity": 0.12, "overlay": False},
        {"name": "Sideways Area", "title": False, "data": sideways_zone, "color": "#9CA3AF", "type": "cloud", "opacity": 0.10, "overlay": False},
        {"name": "Downtrend Area", "title": False, "data": downtrend_zone, "color": "#DC2626", "type": "cloud", "opacity": 0.12, "overlay": False},
        {"name": "Trend Type", "data": regime_plot, "color": "#6B7280", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Upper Boundary", "data": [1.0 for _ in range(n)], "color": "rgba(148, 163, 184, 0.45)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Lower Boundary", "data": [-1.0 for _ in range(n)], "color": "rgba(148, 163, 184, 0.45)", "type": "line", "lineWidth": 1, "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
