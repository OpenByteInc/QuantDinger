# ============================================================
# QuantDinger Multi Divergence Counter
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Multi Divergence Counter"
my_indicator_description = (
    "Confirms regular and hidden price divergences across ten internal "
    "oscillators and labels only bars that meet the requested vote count."
)

# @param repaint bool false Place markers on pivot bars before confirmation
# @param minimum_count int 2 Minimum divergence votes range=1:10:1
# @param pivot_right int 1 Pivot lookback right range=1:20:1
# @param pivot_left int 3 Pivot lookback left range=1:20:1
# @param range_upper int 60 Maximum bars between pivots range=2:200:1
# @param range_lower int 1 Minimum bars between pivots range=1:50:1
# @param regular_bullish bool true Show regular bullish divergences
# @param hidden_bullish bool true Show hidden bullish divergences
# @param regular_bearish bool true Show regular bearish divergences
# @param hidden_bearish bool true Show hidden bearish divergences
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

repaint = bool(params.get("repaint", False))
minimum_count = min(10, max(1, int(params.get("minimum_count", 2))))
pivot_right = min(20, max(1, int(params.get("pivot_right", 1))))
pivot_left = min(20, max(1, int(params.get("pivot_left", 3))))
range_upper = min(200, max(2, int(params.get("range_upper", 60))))
range_lower = min(50, max(1, int(params.get("range_lower", 1))))
regular_bullish = bool(params.get("regular_bullish", True))
hidden_bullish = bool(params.get("hidden_bullish", True))
regular_bearish = bool(params.get("regular_bearish", True))
hidden_bearish = bool(params.get("hidden_bearish", True))

enabled = {
    "MACD": bool(params.get("use_macd", True)),
    "MACD Histogram": bool(params.get("use_macd_histogram", True)),
    "RSI": bool(params.get("use_rsi", True)),
    "Stochastic": bool(params.get("use_stochastic", True)),
    "CCI": bool(params.get("use_cci", True)),
    "Momentum": bool(params.get("use_momentum", True)),
    "OBV": bool(params.get("use_obv", True)),
    "DIOSC": bool(params.get("use_diosc", True)),
    "VW MACD": bool(params.get("use_vwmacd", True)),
    "CMF": bool(params.get("use_cmf", True)),
}

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

def ema(values, length):
    result = np.full(n, np.nan, dtype=float)
    if n == 0:
        return result
    alpha = 2.0 / (length + 1.0)
    result[0] = float(values[0])
    for index in range(1, n):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result

def rma(values, length):
    result = np.full(n, np.nan, dtype=float)
    if n == 0:
        return result
    alpha = 1.0 / float(length)
    result[0] = float(values[0])
    for index in range(1, n):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result

def rolling_mean(values, length):
    return pd.Series(values, dtype=float).rolling(length, min_periods=1).mean().to_numpy(dtype=float)

def vwma(values, length):
    numerator = pd.Series(values * volume, dtype=float).rolling(length, min_periods=1).sum()
    denominator = pd.Series(volume, dtype=float).rolling(length, min_periods=1).sum()
    return (numerator / denominator.replace(0.0, np.nan)).ffill().fillna(0.0).to_numpy(dtype=float)

change = np.zeros(n, dtype=float)
if n > 1:
    change[1:] = np.diff(close)
gain = np.maximum(change, 0.0)
loss = np.maximum(-change, 0.0)
average_gain = rma(gain, 14)
average_loss = rma(loss, 14)
rsi = np.full(n, 50.0, dtype=float)
for index in range(n):
    if average_loss[index] == 0.0:
        rsi[index] = 100.0 if average_gain[index] > 0.0 else 50.0
    else:
        ratio = average_gain[index] / average_loss[index]
        rsi[index] = 100.0 - 100.0 / (1.0 + ratio)

macd = ema(close, 12) - ema(close, 26)
macd_signal = ema(macd, 9)
macd_histogram = macd - macd_signal

momentum = np.zeros(n, dtype=float)
for index in range(n):
    momentum[index] = close[index] - close[index - 10] if index >= 10 else 0.0

cci_basis = rolling_mean(close, 10)
cci = np.zeros(n, dtype=float)
for index in range(n):
    start = max(0, index - 9)
    mean_deviation = float(np.mean(np.abs(close[start:index + 1] - cci_basis[index])))
    cci[index] = (close[index] - cci_basis[index]) / (0.015 * mean_deviation) if mean_deviation > 0.0 else 0.0

obv = np.zeros(n, dtype=float)
for index in range(1, n):
    direction = 1.0 if close[index] > close[index - 1] else -1.0 if close[index] < close[index - 1] else 0.0
    obv[index] = obv[index - 1] + direction * volume[index]

stochastic_raw = np.full(n, 50.0, dtype=float)
for index in range(n):
    start = max(0, index - 13)
    lowest = float(np.min(low[start:index + 1]))
    highest = float(np.max(high[start:index + 1]))
    stochastic_raw[index] = 100.0 * (close[index] - lowest) / (highest - lowest) if highest > lowest else 50.0
stochastic = rolling_mean(stochastic_raw, 3)

high_change = np.zeros(n, dtype=float)
low_change = np.zeros(n, dtype=float)
if n > 1:
    high_change[1:] = np.diff(high)
    low_change[1:] = np.diff(low)
directional_input = high_change + low_change
true_range = np.zeros(n, dtype=float)
if n > 0:
    true_range[0] = high[0] - low[0]
for index in range(1, n):
    true_range[index] = max(
        high[index] - low[index],
        abs(high[index] - close[index - 1]),
        abs(low[index] - close[index - 1]),
    )
smoothed_true_range = rma(true_range, 14)
directional_oscillator = np.zeros(n, dtype=float)
smoothed_directional = rma(directional_input, 14)
for index in range(n):
    if smoothed_true_range[index] != 0.0:
        directional_oscillator[index] = 100.0 * smoothed_directional[index] / smoothed_true_range[index]

vw_macd = vwma(close, 12) - vwma(close, 26)

money_flow_multiplier = np.zeros(n, dtype=float)
for index in range(n):
    spread = high[index] - low[index]
    if spread > 0.0:
        money_flow_multiplier[index] = ((close[index] - low[index]) - (high[index] - close[index])) / spread
money_flow_volume = money_flow_multiplier * volume
cmf_numerator = rolling_mean(money_flow_volume, 21)
cmf_denominator = rolling_mean(volume, 21)
cmf = np.zeros(n, dtype=float)
for index in range(n):
    cmf[index] = cmf_numerator[index] / cmf_denominator[index] if cmf_denominator[index] != 0.0 else 0.0

oscillators = {
    "MACD": macd,
    "MACD Histogram": macd_histogram,
    "RSI": rsi,
    "Stochastic": stochastic,
    "CCI": cci,
    "Momentum": momentum,
    "OBV": obv,
    "DIOSC": directional_oscillator,
    "VW MACD": vw_macd,
    "CMF": cmf,
}

regular_bull_count = np.zeros(n, dtype=int)
regular_bear_count = np.zeros(n, dtype=int)
hidden_bull_count = np.zeros(n, dtype=int)
hidden_bear_count = np.zeros(n, dtype=int)

def pivot_flags(values):
    pivot_lows = np.zeros(n, dtype=bool)
    pivot_highs = np.zeros(n, dtype=bool)
    for confirmation in range(n):
        center = confirmation - pivot_right
        if center < pivot_left:
            continue
        window_start = center - pivot_left
        window_end = center + pivot_right
        if window_end >= n:
            continue
        window = values[window_start:window_end + 1]
        pivot_lows[confirmation] = values[center] <= float(np.min(window))
        pivot_highs[confirmation] = values[center] >= float(np.max(window))
    return pivot_lows, pivot_highs

for oscillator_name, values in oscillators.items():
    if not enabled[oscillator_name]:
        continue
    pivot_lows, pivot_highs = pivot_flags(values)
    previous_low = None
    previous_high = None

    for confirmation in range(n):
        center = confirmation - pivot_right
        if pivot_lows[confirmation]:
            if previous_low is not None:
                previous_center, previous_value = previous_low
                distance = center - previous_center
                if range_lower <= distance <= range_upper:
                    if regular_bullish and values[center] > previous_value and low[center] < low[previous_center]:
                        regular_bull_count[confirmation] += 1
                    if hidden_bullish and values[center] < previous_value and low[center] > low[previous_center]:
                        hidden_bull_count[confirmation] += 1
            previous_low = (center, float(values[center]))

        if pivot_highs[confirmation]:
            if previous_high is not None:
                previous_center, previous_value = previous_high
                distance = center - previous_center
                if range_lower <= distance <= range_upper:
                    if regular_bearish and values[center] < previous_value and high[center] > high[previous_center]:
                        regular_bear_count[confirmation] += 1
                    if hidden_bearish and values[center] > previous_value and high[center] < high[previous_center]:
                        hidden_bear_count[confirmation] += 1
            previous_high = (center, float(values[center]))

def divergence_markers(counts, bullish, prefix, color):
    markers = [None for _ in range(n)]
    for confirmation in range(n):
        count = int(counts[confirmation])
        if count < minimum_count:
            continue
        center = confirmation - pivot_right
        target_index = center if repaint else confirmation
        if target_index < 0 or target_index >= n or center < 0:
            continue
        anchor = float(low[center]) if bullish else float(high[center])
        markers[target_index] = {
            "value": anchor,
            "text": f"{prefix} {count if count <= 8 else '8+'}",
            "color": color,
        }
    return markers

signals = [
    {
        "type": "buy",
        "text": "RB",
        "name": "Regular Bullish Divergence Votes",
        "data": divergence_markers(regular_bull_count, True, "RB", "#00897B"),
        "color": "#00897B",
        "renderMode": "events",
    },
    {
        "type": "sell",
        "text": "RS",
        "name": "Regular Bearish Divergence Votes",
        "data": divergence_markers(regular_bear_count, False, "RS", "#EF4444"),
        "color": "#EF4444",
        "renderMode": "events",
    },
    {
        "type": "buy",
        "text": "HB",
        "name": "Hidden Bullish Divergence Votes",
        "data": divergence_markers(hidden_bull_count, True, "HB", "#22C55E"),
        "color": "#22C55E",
        "renderMode": "events",
    },
    {
        "type": "sell",
        "text": "HS",
        "name": "Hidden Bearish Divergence Votes",
        "data": divergence_markers(hidden_bear_count, False, "HS", "#F59E0B"),
        "color": "#F59E0B",
        "renderMode": "events",
    },
]

plots = [
    {
        "name": "Price Reference",
        "title": False,
        "data": [float(value) for value in close],
        "color": "rgba(255, 255, 255, 0.01)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    },
]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": []}
