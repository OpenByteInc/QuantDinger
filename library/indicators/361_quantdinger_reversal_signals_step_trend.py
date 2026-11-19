# ============================================================
# QuantDinger Reversal Signals with Step Trend
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Reversal Signals with Step Trend"
my_indicator_description = "Confirms extreme-candle reversals with optional volume and overlays a direction-aware stepped trend average."

# @param candle_lookback int 12 Candle lookback range=1:10000:1
# @param confirm_within int 3 Confirmation window range=0:10000:1
# @param use_volume_confirmation bool true Require volume above its 20-bar average
# @param trend_ma_period int 50 Trend average period range=1:10000:1
# @param trend_ma_type str EMA Trend average values=SMA,EMA,WMA,VWMA
# @param ma_step_period int 33 Step update period range=1:10000:1
# @param bull_color str #00FFBB Bullish color
# @param bear_color str #FF1100 Bearish color
# @param show_trend_fill bool true Show trend fill
# @param show_ma_steps bool true Show stepped average

candle_lookback = min(10000, max(1, int(params.get("candle_lookback", 12))))
confirm_within = min(10000, max(0, int(params.get("confirm_within", 3))))
use_volume_confirmation = bool(params.get("use_volume_confirmation", True))
trend_ma_period = min(10000, max(1, int(params.get("trend_ma_period", 50))))
trend_ma_type = str(params.get("trend_ma_type", "EMA"))
trend_ma_type = trend_ma_type if trend_ma_type in ("SMA", "EMA", "WMA", "VWMA") else "EMA"
ma_step_period = min(10000, max(1, int(params.get("ma_step_period", 33))))
bull_color = str(params.get("bull_color", "#00FFBB"))
bear_color = str(params.get("bear_color", "#FF1100"))
show_trend_fill = bool(params.get("show_trend_fill", True))
show_ma_steps = bool(params.get("show_ma_steps", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _wma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
    return result


def _vwma(values, volume, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        price_window = values[index - length + 1:index + 1]
        volume_window = volume[index - length + 1:index + 1]
        denominator = float(np.sum(volume_window))
        if np.all(np.isfinite(price_window)) and np.all(np.isfinite(volume_window)) and denominator != 0.0:
            result[index] = float(np.sum(price_window * volume_window) / denominator)
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].to_numpy(dtype=float)
n = len(df)

volume_average = _sma(volume, 20)
volume_high = np.isfinite(volume) & np.isfinite(volume_average) & (volume > volume_average)
if trend_ma_type == "SMA":
    current_ma = _sma(close, trend_ma_period)
elif trend_ma_type == "WMA":
    current_ma = _wma(close, trend_ma_period)
elif trend_ma_type == "VWMA":
    current_ma = _vwma(close, volume, trend_ma_period)
else:
    current_ma = _ema(close, trend_ma_period)

stepped_ma = np.full(n, np.nan, dtype=float)
ma_direction = np.ones(n, dtype=int)
ma_last_update = np.zeros(n, dtype=int)
step_value = np.nan
last_update = 0
direction = 1
for index in range(n):
    if not np.isfinite(step_value):
        step_value = current_ma[index]
        last_update = index
    elif direction == 1:
        if close[index] < step_value:
            direction = -1
            step_value = current_ma[index]
            last_update = index
        elif index - last_update >= ma_step_period:
            if np.isfinite(current_ma[index]):
                step_value = max(step_value, current_ma[index])
            last_update = index
    else:
        if close[index] > step_value:
            direction = 1
            step_value = current_ma[index]
            last_update = index
        elif index - last_update >= ma_step_period:
            if np.isfinite(current_ma[index]):
                step_value = min(step_value, current_ma[index])
            last_update = index
    stepped_ma[index] = step_value
    ma_direction[index] = direction
    ma_last_update[index] = last_update

bull_score = np.zeros(n, dtype=int)
bear_score = np.zeros(n, dtype=int)
bull_candidate = np.zeros(n, dtype=bool)
bear_candidate = np.zeros(n, dtype=bool)
bull_low = np.zeros(n, dtype=float)
bull_high = np.zeros(n, dtype=float)
bear_low = np.zeros(n, dtype=float)
bear_high = np.zeros(n, dtype=float)
bull_confirmed = np.zeros(n, dtype=bool)
bear_confirmed = np.zeros(n, dtype=bool)
bull_count = np.zeros(n, dtype=int)
bear_count = np.zeros(n, dtype=int)
bull_condition = np.zeros(n, dtype=bool)
bear_condition = np.zeros(n, dtype=bool)
bull_signal = np.zeros(n, dtype=bool)
bear_signal = np.zeros(n, dtype=bool)

for index in range(n):
    if index:
        bull_candidate[index], bear_candidate[index] = bull_candidate[index - 1], bear_candidate[index - 1]
        bull_low[index], bull_high[index] = bull_low[index - 1], bull_high[index - 1]
        bear_low[index], bear_high[index] = bear_low[index - 1], bear_high[index - 1]
        bull_confirmed[index], bear_confirmed[index] = bull_confirmed[index - 1], bear_confirmed[index - 1]
        bull_count[index], bear_count[index] = bull_count[index - 1], bear_count[index - 1]
    if index >= candle_lookback - 1:
        start = index - candle_lookback + 1
        bull_score[index] = int(np.sum(close[index] < low[start:index]))
        bear_score[index] = int(np.sum(close[index] > high[start:index]))
    if bear_score[index] == candle_lookback - 1:
        bear_candidate[index] = True
        bear_low[index], bear_high[index] = low[index], high[index]
        bear_confirmed[index] = False
        bear_count[index] = 0
    if bear_candidate[index]:
        bear_count[index] += 1
        if close[index] > bear_high[index]:
            bear_candidate[index] = False
    if bear_candidate[index] and close[index] < bear_low[index] and not bear_confirmed[index] and bear_count[index] <= confirm_within + 1:
        bear_confirmed[index] = True
        bear_condition[index] = True
    if bear_condition[index] and ((not use_volume_confirmation) or volume_high[index]):
        bear_signal[index] = True
    if bull_score[index] == candle_lookback - 1:
        bull_candidate[index] = True
        bull_low[index], bull_high[index] = low[index], high[index]
        bull_confirmed[index] = False
        bull_count[index] = 0
    if bull_candidate[index]:
        bull_count[index] += 1
        if close[index] < bull_low[index]:
            bull_candidate[index] = False
    if bull_candidate[index] and close[index] > bull_high[index] and not bull_confirmed[index] and bull_count[index] <= confirm_within + 1:
        bull_confirmed[index] = True
        bull_condition[index] = True
    if bull_condition[index] and ((not use_volume_confirmation) or volume_high[index]):
        bull_signal[index] = True

trend_up = close > stepped_ma
step_colors = [bull_color if value else bear_color for value in trend_up]
center = (opened + close) / 2.0
trend_cloud = []
for index in range(n):
    if show_trend_fill and np.all(np.isfinite([stepped_ma[index], center[index]])):
        trend_cloud.append({"upper": float(max(stepped_ma[index], center[index])), "lower": float(min(stepped_ma[index], center[index])), "color": bull_color if trend_up[index] else bear_color, "opacity": 0.30})
    else:
        trend_cloud.append(None)


def _cross_above(first, second):
    return np.asarray([index > 0 and np.all(np.isfinite([first[index], first[index - 1], second[index], second[index - 1]])) and first[index] > second[index] and first[index - 1] <= second[index - 1] for index in range(n)], dtype=bool)


def _cross_below(first, second):
    return np.asarray([index > 0 and np.all(np.isfinite([first[index], first[index - 1], second[index], second[index - 1]])) and first[index] < second[index] and first[index - 1] >= second[index - 1] for index in range(n)], dtype=bool)


trend_bull = _cross_above(close, stepped_ma)
trend_bear = _cross_below(close, stepped_ma)


def _events(mask, prices, text, color, event_type):
    return {"type": event_type, "name": text, "data": [{"active": True, "price": float(prices[index]), "text": text, "color": color} if active else None for index, active in enumerate(mask)], "color": color, "renderMode": "events"}


def _limited_event_masks(masks, recent_bars=120, total_cap=10, minimum_gap=5):
    result = [np.zeros(n, dtype=bool) for _ in masks]
    candidates = []
    first_index = max(0, n - recent_bars)
    for mask_index, mask in enumerate(masks):
        for index in np.flatnonzero(np.asarray(mask, dtype=bool)):
            if index >= first_index:
                candidates.append((int(index), mask_index))
    selected_indices = []
    for index, mask_index in sorted(candidates, reverse=True):
        if len(selected_indices) >= total_cap:
            break
        if any(abs(index - selected) < minimum_gap for selected in selected_indices):
            continue
        result[mask_index][index] = True
        selected_indices.append(index)
    return result


display_bull_signal, display_bear_signal, display_trend_bull, display_trend_bear = _limited_event_masks([
    bull_signal,
    bear_signal,
    trend_bull,
    trend_bear,
])


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Stepped MA", "title": show_ma_steps, "data": _finite(stepped_ma) if show_ma_steps else [None] * n, "color": step_colors, "type": "step", "lineWidth": 2, "overlay": True},
        {"name": "Trend Fill", "title": False, "data": trend_cloud, "color": bull_color, "type": "cloud", "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "overlay": True},
    ],
    "signals": [
        _events(display_bull_signal, low, "𝓡", bull_color, "buy"), _events(display_bear_signal, high, "𝓡", bear_color, "sell"),
        _events(display_trend_bull, close, "Trend Up", bull_color, "buy"), _events(display_trend_bear, close, "Trend Down", bear_color, "sell"),
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "volumeAverage": _finite(volume_average), "volumeHigh": volume_high.tolist(), "currentMa": _finite(current_ma), "steppedMa": _finite(stepped_ma),
        "maDirection": [int(value) for value in ma_direction], "maLastUpdate": [int(value) for value in ma_last_update],
        "bullScore": [int(value) for value in bull_score], "bearScore": [int(value) for value in bear_score], "bullCandidate": bull_candidate.tolist(), "bearCandidate": bear_candidate.tolist(),
        "bullLow": _finite(bull_low), "bullHigh": _finite(bull_high), "bearLow": _finite(bear_low), "bearHigh": _finite(bear_high),
        "bullConfirmed": bull_confirmed.tolist(), "bearConfirmed": bear_confirmed.tolist(), "bullCount": [int(value) for value in bull_count], "bearCount": [int(value) for value in bear_count],
        "bullCondition": bull_condition.tolist(), "bearCondition": bear_condition.tolist(), "bullSignal": bull_signal.tolist(), "bearSignal": bear_signal.tolist(),
        "trendBull": trend_bull.tolist(), "trendBear": trend_bear.tolist(),
    },
}
