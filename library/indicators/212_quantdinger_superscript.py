# ============================================================
# QuantDinger SuperScript
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only toolkit; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger SuperScript"
my_indicator_description = (
    "Combines T3 reversal, three-average trend, TSI/RSI momentum, RSI heat colors, "
    "Parabolic SAR, Heikin-Ashi overlay, and a nine-factor market rating."
)

# @param strategy str Bj_Reversal Active strategy values=Bj_Reversal,MA,TRM,RSI_Color
# @param source str Close Price source values=Close,HL2,HLC3,OHLC4
# @param t3_alpha float 0.7 T3 alpha range=0.1:1.0:0.05
# @param t3_fast_length int 5 T3 fast length range=1:100:1
# @param t3_slow_length int 8 T3 slow length range=1:100:1
# @param show_t3 bool false Always show T3 ribbon
# @param show_ma bool false Always show MA ribbon
# @param show_psar bool false Show Parabolic SAR
# @param show_arrows bool false Show TSI curl arrows
# @param show_rating bool false Show nine-factor rating
# @param rating_position str bottom-right Rating position values=top-left,top-right,bottom-left,bottom-right
# @param disable_strategy_lines bool false Hide active strategy lines
# @param trm_speed str Fast TRM speed values=Fast,Slow
# @param rsi_speed str Slow RSI speed values=Fast,Slow
# @param ha_overlay bool false Show Heikin-Ashi overlay
# @param ha_type str Candles Heikin-Ashi style values=Hollow,Bars,Candles
# @param ma1_type str HEMA MA 1 type values=SMA,EMA,HEMA,T3,DEMA,TEMA,HMA,WMA,VWMA,SWMA,VWAP
# @param ma1_length int 5 MA 1 length range=1:300:1
# @param ma2_type str HEMA MA 2 type values=SMA,EMA,HEMA,T3,DEMA,TEMA,HMA,WMA,VWMA,SWMA,VWAP
# @param ma2_length int 9 MA 2 length range=1:300:1
# @param ma3_type str HEMA MA 3 type values=SMA,EMA,HEMA,T3,DEMA,TEMA,HMA,WMA,VWMA,SWMA,VWAP
# @param ma3_length int 21 MA 3 length range=1:300:1
# @param bull_color str #64B5F6 Bullish color
# @param bear_color str #EF5350 Bearish color
# @param reversal_color str #FFF176 Reversal color
# @param hold_color str #B2B5BE Hold color
# @param trm_buy_color str #3BB3E4 TRM buy color
# @param trm_sell_color str #FFEB3B TRM sell color
# @param psar_start float 0.043 PSAR start range=0.001:1.0:0.001
# @param psar_step float 0.043 PSAR step range=0.001:1.0:0.001
# @param psar_max float 0.34 PSAR maximum range=0.01:2.0:0.01
# @param show_signal_markers bool false Show strategy signal markers

strategy = str(params.get("strategy", "Bj_Reversal"))
if strategy not in ("Bj_Reversal", "MA", "TRM", "RSI_Color"):
    strategy = "Bj_Reversal"
source_name = str(params.get("source", "Close")).upper()
if source_name not in ("CLOSE", "HL2", "HLC3", "OHLC4"):
    source_name = "CLOSE"
t3_alpha = min(1.0, max(0.1, float(params.get("t3_alpha", 0.7))))
t3_fast_length = min(100, max(1, int(params.get("t3_fast_length", 5))))
t3_slow_length = min(100, max(1, int(params.get("t3_slow_length", 8))))
show_t3 = bool(params.get("show_t3", False))
show_ma = bool(params.get("show_ma", False))
show_psar = bool(params.get("show_psar", False))
show_arrows = bool(params.get("show_arrows", False))
show_rating = bool(params.get("show_rating", False))
rating_position = str(params.get("rating_position", "bottom-right"))
if rating_position not in ("top-left", "top-right", "bottom-left", "bottom-right"):
    rating_position = "bottom-right"
disable_strategy_lines = bool(params.get("disable_strategy_lines", False))
trm_speed = str(params.get("trm_speed", "Fast"))
if trm_speed not in ("Fast", "Slow"):
    trm_speed = "Fast"
rsi_speed = str(params.get("rsi_speed", "Slow"))
if rsi_speed not in ("Fast", "Slow"):
    rsi_speed = "Slow"
ha_overlay = bool(params.get("ha_overlay", False))
ha_type = str(params.get("ha_type", "Candles"))
if ha_type not in ("Hollow", "Bars", "Candles"):
    ha_type = "Candles"
valid_ma_types = ("SMA", "EMA", "HEMA", "T3", "DEMA", "TEMA", "HMA", "WMA", "VWMA", "SWMA", "VWAP")
ma_types = [
    str(params.get("ma1_type", "HEMA")).upper(),
    str(params.get("ma2_type", "HEMA")).upper(),
    str(params.get("ma3_type", "HEMA")).upper(),
]
ma_types = [value if value in valid_ma_types else "HEMA" for value in ma_types]
ma_lengths = [
    min(300, max(1, int(params.get("ma1_length", 5)))),
    min(300, max(1, int(params.get("ma2_length", 9)))),
    min(300, max(1, int(params.get("ma3_length", 21)))),
]
bull_color = str(params.get("bull_color", "#64B5F6"))
bear_color = str(params.get("bear_color", "#EF5350"))
reversal_color = str(params.get("reversal_color", "#FFF176"))
hold_color = str(params.get("hold_color", "#B2B5BE"))
trm_buy_color = str(params.get("trm_buy_color", "#3BB3E4"))
trm_sell_color = str(params.get("trm_sell_color", "#FFEB3B"))
psar_start = min(1.0, max(0.001, float(params.get("psar_start", 0.043))))
psar_step = min(1.0, max(0.001, float(params.get("psar_step", 0.043))))
psar_max = min(2.0, max(0.01, float(params.get("psar_max", 0.34))))
if psar_max < psar_start:
    psar_max = psar_start
show_signal_markers = bool(params.get("show_signal_markers", False))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
raw_time = df["time"].astype(float).to_numpy(dtype=float) if "time" in df.columns else np.arange(len(df), dtype=float)
time_seconds = raw_time / 1000.0 if len(raw_time) and max(abs(float(value)) for value in raw_time) > 10_000_000_000 else raw_time
n = len(df)

if source_name == "HL2":
    source = (high + low) / 2.0
elif source_name == "HLC3":
    source = (high + low + close) / 3.0
elif source_name == "OHLC4":
    source = (open_price + high + low + close) / 4.0
else:
    source = close.copy()


def average(values):
    return sum(float(value) for value in values) / len(values) if len(values) else np.nan


def ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    seed_values = []
    seeded = False
    weight = 2.0 / (length + 1.0)
    last_value = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not seeded:
            seed_values.append(float(value))
            if len(seed_values) > length:
                seed_values.pop(0)
            if len(seed_values) == length:
                last_value = average(seed_values)
                result[index] = last_value
                seeded = True
        else:
            last_value = weight * float(value) + (1.0 - weight) * last_value
            result[index] = last_value
    return result


def sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if all(np.isfinite(float(value)) for value in window):
            result[index] = average(window)
    return result


def wma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    denominator = length * (length + 1) / 2.0
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if all(np.isfinite(float(value)) for value in window):
            result[index] = sum(
                float(value) * (offset + 1) for offset, value in enumerate(window)
            ) / denominator
    return result


def vwma(values, weights, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        left = index - length + 1
        denominator = sum(float(value) for value in weights[left:index + 1])
        if denominator > 1e-12:
            result[index] = sum(
                float(values[cursor]) * float(weights[cursor])
                for cursor in range(left, index + 1)
            ) / denominator
    return result


def heikin_ashi_values():
    ha_close = (open_price + high + low + close) / 4.0
    ha_open = np.full(n, np.nan, dtype=float)
    ha_high = np.full(n, np.nan, dtype=float)
    ha_low = np.full(n, np.nan, dtype=float)
    for index in range(n):
        if index == 0:
            ha_open[index] = (open_price[index] + close[index]) / 2.0
        else:
            ha_open[index] = (ha_open[index - 1] + ha_close[index - 1]) / 2.0
        ha_high[index] = max(high[index], ha_open[index], ha_close[index])
        ha_low[index] = min(low[index], ha_open[index], ha_close[index])
    return ha_open, ha_high, ha_low, ha_close


ha_open, ha_high, ha_low, ha_close = heikin_ashi_values()


def t3(values, length, alpha):
    def generalized_dema(input_values):
        first = ema(input_values, length)
        second = ema(first, length)
        return first * (1.0 + alpha) - second * alpha
    return generalized_dema(generalized_dema(generalized_dema(values)))


def session_vwap(values):
    result = np.full(n, np.nan, dtype=float)
    current_day = None
    total_value = 0.0
    total_volume = 0.0
    for index in range(n):
        day = int(time_seconds[index] // 86400)
        if current_day is None or day != current_day:
            current_day = day
            total_value = 0.0
            total_volume = 0.0
        total_value += float(values[index]) * volume[index]
        total_volume += volume[index]
        if total_volume > 1e-12:
            result[index] = total_value / total_volume
    return result


def moving_average(kind, length):
    if kind == "SMA":
        return sma(source, length)
    if kind == "HEMA":
        return ema(ha_open, length)
    if kind == "T3":
        return t3(source, length, t3_alpha)
    if kind == "DEMA":
        first = ema(source, length)
        return 2.0 * first - ema(first, length)
    if kind == "TEMA":
        first = ema(source, length)
        second = ema(first, length)
        third = ema(second, length)
        return 3.0 * (first - second) + third
    if kind == "HMA":
        return wma(2.0 * wma(source, max(1, length // 2)) - wma(source, length), max(1, int(round(length ** 0.5))))
    if kind == "WMA":
        return wma(source, length)
    if kind == "VWMA":
        return vwma(source, volume, length)
    if kind == "SWMA":
        result = np.full(n, np.nan, dtype=float)
        for index in range(3, n):
            result[index] = (
                source[index - 3] + 2.0 * source[index - 2]
                + 2.0 * source[index - 1] + source[index]
            ) / 6.0
        return result
    if kind == "VWAP":
        return session_vwap(source)
    return ema(source, length)


def rsi(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= length:
        return result
    gains = np.zeros(len(values), dtype=float)
    losses = np.zeros(len(values), dtype=float)
    for index in range(1, len(values)):
        change = float(values[index] - values[index - 1])
        gains[index] = max(change, 0.0)
        losses[index] = max(-change, 0.0)
    average_gain = average(gains[1:length + 1])
    average_loss = average(losses[1:length + 1])
    result[length] = 100.0 if average_loss <= 1e-12 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    for index in range(length + 1, len(values)):
        average_gain = (average_gain * (length - 1) + gains[index]) / length
        average_loss = (average_loss * (length - 1) + losses[index]) / length
        result[index] = 100.0 if average_loss <= 1e-12 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


def tsi(values, short_length, long_length):
    momentum = np.zeros(len(values), dtype=float)
    for index in range(1, len(values)):
        momentum[index] = values[index] - values[index - 1]
    numerator = ema(ema(momentum, short_length), long_length)
    denominator = ema(ema(np.abs(momentum), short_length), long_length)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        if np.isfinite(numerator[index]) and np.isfinite(denominator[index]) and denominator[index] > 1e-12:
            result[index] = numerator[index] / denominator[index]
    return result


def parabolic_sar():
    result = np.full(n, np.nan, dtype=float)
    bullish = np.zeros(n, dtype=bool)
    if n < 2:
        return result, bullish
    is_below = bool(close[1] > close[0])
    extreme = float(high[1] if is_below else low[1])
    sar_value = float(low[0] if is_below else high[0])
    acceleration = psar_start
    bullish[0] = is_below
    for index in range(1, n):
        first_trend_bar = False
        sar_value = sar_value + acceleration * (extreme - sar_value)
        if is_below and sar_value > low[index]:
            first_trend_bar = True
            is_below = False
            sar_value = max(float(high[index]), extreme)
            extreme = float(low[index])
            acceleration = psar_start
        elif (not is_below) and sar_value < high[index]:
            first_trend_bar = True
            is_below = True
            sar_value = min(float(low[index]), extreme)
            extreme = float(high[index])
            acceleration = psar_start
        if not first_trend_bar:
            if is_below and high[index] > extreme:
                extreme = float(high[index])
                acceleration = min(acceleration + psar_step, psar_max)
            elif (not is_below) and low[index] < extreme:
                extreme = float(low[index])
                acceleration = min(acceleration + psar_step, psar_max)
        if is_below:
            sar_value = min(sar_value, float(low[index - 1]))
            if index > 1:
                sar_value = min(sar_value, float(low[index - 2]))
        else:
            sar_value = max(sar_value, float(high[index - 1]))
            if index > 1:
                sar_value = max(sar_value, float(high[index - 2]))
        result[index] = sar_value
        bullish[index] = is_below
    return result, bullish


t3_fast = t3(source, t3_fast_length, t3_alpha)
t3_slow = t3(source, t3_slow_length, t3_alpha)
averages = [moving_average(ma_types[index], ma_lengths[index]) for index in range(3)]
ha_fast = (high + low) / 2.0

if trm_speed == "Fast":
    tsi_short, tsi_long, tsi_signal_length = 5, 25, 14
else:
    tsi_short, tsi_long, tsi_signal_length = 13, 25, 13
rsi_length = 5 if rsi_speed == "Fast" else 14
tsi_value = tsi(close, tsi_short, tsi_long)
tsi_signal = ema(tsi_value, tsi_signal_length)
rsi_value = rsi(close, rsi_length)
psar, psar_bullish = parabolic_sar()


def crosses(left, right, upward):
    result = np.zeros(n, dtype=bool)
    for index in range(1, n):
        if not (
            np.isfinite(left[index]) and np.isfinite(right[index])
            and np.isfinite(left[index - 1]) and np.isfinite(right[index - 1])
        ):
            continue
        if upward:
            result[index] = left[index] > right[index] and left[index - 1] <= right[index - 1]
        else:
            result[index] = left[index] < right[index] and left[index - 1] >= right[index - 1]
    return result


trm_buy = crosses(tsi_value, tsi_signal, True) & (rsi_value > 50.0)
trm_buy = trm_buy | (crosses(rsi_value, np.full(n, 50.0), True) & (tsi_value > tsi_signal))
trm_sell = crosses(tsi_value, tsi_signal, False) & (rsi_value < 50.0)
trm_sell = trm_sell | (crosses(rsi_value, np.full(n, 50.0), False) & (tsi_value < tsi_signal))
reversal_up = crosses(close, t3_slow, True)
reversal_down = crosses(close, t3_slow, False)
ma_cross_up = (crosses(close, averages[0], True) & (close > averages[1])) | (crosses(close, averages[1], True) & (close > averages[0]))
ma_cross_down = (crosses(close, averages[0], False) & (close < averages[1])) | (crosses(close, averages[1], False) & (close < averages[0]))
rsi_up = crosses(rsi_value, np.full(n, 50.0), True)
rsi_down = crosses(rsi_value, np.full(n, 50.0), False)
psar_up = crosses(close, psar, True)
psar_down = crosses(close, psar, False)

trm_bullish = (tsi_value > tsi_signal) & (rsi_value > 50.0)
trm_bearish = (tsi_value < tsi_signal) & (rsi_value < 50.0)
t3_bullish = (t3_fast >= t3_slow) & (close > t3_slow)
t3_bearish = (t3_fast <= t3_slow) & (close < t3_slow)
ma_bullish = (close > averages[0]) & (close > averages[1])
ma_bearish = (close < averages[0]) & (close < averages[1])


def rsi_heat_color(value):
    if not np.isfinite(value):
        return None
    if value <= 10.0:
        return "#17FF00"
    if value <= 50.0:
        return "#FFFFFF"
    if value <= 80.0:
        return "#2196F3"
    if value <= 90.0:
        return "#9C27B0"
    return "#FF0000"


bar_colors = [None] * n
for index in range(n):
    if strategy == "TRM":
        bar_colors[index] = trm_buy_color if trm_bullish[index] else trm_sell_color if trm_bearish[index] else hold_color
    elif strategy == "MA":
        bar_colors[index] = bull_color if ma_bullish[index] else bear_color if ma_bearish[index] else hold_color
    elif strategy == "RSI_Color":
        bar_colors[index] = rsi_heat_color(rsi_value[index])
    else:
        bar_colors[index] = bull_color if t3_bullish[index] else bear_color if t3_bearish[index] else reversal_color


def line_segment(values, mask):
    result = [None] * n
    for index, value in enumerate(values):
        if not (np.isfinite(value) and bool(mask[index])):
            continue
        result[index] = float(value)
        if index > 0 and np.isfinite(values[index - 1]) and not bool(mask[index - 1]):
            result[index - 1] = float(values[index - 1])
    return result


def cloud_segment(upper_values, lower_values, mask):
    return [
        {
            "upper": float(max(upper_values[index], lower_values[index])),
            "lower": float(min(upper_values[index], lower_values[index])),
        }
        if bool(mask[index]) and np.isfinite(upper_values[index]) and np.isfinite(lower_values[index])
        else None
        for index in range(n)
    ]


plots = [
    {
        "name": "Runtime Anchor",
        "title": False,
        "data": [float(value) for value in close],
        "color": "rgba(0,0,0,0)",
        "opacity": 0.0,
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    }
]

show_t3_lines = show_t3 or (strategy == "Bj_Reversal" and not disable_strategy_lines)
if show_t3_lines:
    fast_rising = np.zeros(n, dtype=bool)
    slow_rising = np.zeros(n, dtype=bool)
    for index in range(1, n):
        fast_rising[index] = np.isfinite(t3_fast[index]) and np.isfinite(t3_fast[index - 1]) and t3_fast[index] >= t3_fast[index - 1]
        slow_rising[index] = np.isfinite(t3_slow[index]) and np.isfinite(t3_slow[index - 1]) and t3_slow[index] >= t3_slow[index - 1]
    for name, values, mask in (("T3 Fast", t3_fast, fast_rising), ("T3 Slow", t3_slow, slow_rising)):
        plots.append({"name": name + " Bullish", "title": False, "data": line_segment(values, mask), "color": bull_color, "type": "line", "lineWidth": 2, "overlay": True})
        plots.append({"name": name + " Bearish", "title": False, "data": line_segment(values, ~mask), "color": bear_color, "type": "line", "lineWidth": 2, "overlay": True})
    plots.append({"name": "T3 Bullish Fill", "title": False, "data": cloud_segment(t3_fast, t3_slow, t3_fast >= t3_slow), "color": "rgba(100,181,246,0.18)", "type": "cloud", "overlay": True})
    plots.append({"name": "T3 Bearish Fill", "title": False, "data": cloud_segment(t3_fast, t3_slow, t3_fast < t3_slow), "color": "rgba(239,83,80,0.18)", "type": "cloud", "overlay": True})

show_ma_lines = show_ma or (strategy == "MA" and not disable_strategy_lines)
if show_ma_lines:
    for slot, values in enumerate(averages):
        rising = np.zeros(n, dtype=bool)
        for index in range(1, n):
            rising[index] = np.isfinite(values[index]) and np.isfinite(values[index - 1]) and values[index] >= values[index - 1]
        plots.append({"name": f"MA {slot + 1} {ma_types[slot]} {ma_lengths[slot]} Bullish", "title": False, "data": line_segment(values, rising), "color": bull_color, "type": "line", "lineWidth": 1 + slot, "overlay": True})
        plots.append({"name": f"MA {slot + 1} {ma_types[slot]} {ma_lengths[slot]} Bearish", "title": False, "data": line_segment(values, ~rising), "color": bear_color, "type": "line", "lineWidth": 1 + slot, "overlay": True})
    plots.append({"name": "MA Price Fill", "title": False, "data": cloud_segment(averages[0], ha_fast, averages[0] < ha_fast), "color": "rgba(100,181,246,0.12)", "type": "cloud", "overlay": True})
    plots.append({"name": "MA 1-2 Fill", "title": False, "data": cloud_segment(averages[0], averages[1], averages[1] < averages[0]), "color": "rgba(100,181,246,0.16)", "type": "cloud", "overlay": True})
    plots.append({"name": "MA 2-3 Fill", "title": False, "data": cloud_segment(averages[1], averages[2], averages[2] < averages[1]), "color": "rgba(100,181,246,0.20)", "type": "cloud", "overlay": True})

if show_psar:
    plots.append({
        "name": "Parabolic SAR",
        "data": [
            {"value": float(psar[index]), "color": bull_color if psar_bullish[index] else bear_color}
            if np.isfinite(psar[index]) else None for index in range(n)
        ],
        "color": bull_color,
        "type": "circle",
        "lineWidth": 1,
        "overlay": True,
    })

if ha_overlay:
    ha_candles = []
    for index in range(n):
        candle_bullish = ha_close[index] >= ha_open[index]
        candle_color = bar_colors[index] or (bull_color if candle_bullish else bear_color)
        fill_color = "rgba(0,0,0,0)" if ha_type == "Hollow" and candle_bullish else candle_color
        ha_candles.append({
            "open": float(ha_open[index]),
            "high": float(ha_high[index]),
            "low": float(ha_low[index]),
            "close": float(ha_close[index]),
            "color": candle_color,
            "fillColor": fill_color,
            "borderColor": candle_color,
            "wickColor": hold_color,
        })
    plots.append({"name": "Heikin-Ashi Overlay", "data": ha_candles, "type": "candlestick", "overlay": True})

curl_up = np.zeros(n, dtype=bool)
curl_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(tsi_value[index]) and np.isfinite(tsi_value[index - 1]) and np.isfinite(tsi_signal[index]):
        curl_up[index] = tsi_value[index] < tsi_signal[index] and tsi_value[index] > tsi_value[index - 1]
        curl_down[index] = tsi_value[index] > tsi_signal[index] and tsi_value[index] < tsi_value[index - 1]

signals = []
if show_arrows:
    signals.extend([
        {"type": "buy", "name": "TSI Curl Up", "text": "CURL", "color": "#17FF00", "renderMode": "events", "data": [float(low[index]) if curl_up[index] else None for index in range(n)]},
        {"type": "sell", "name": "TSI Curl Down", "text": "CURL", "color": "#FFEB3B", "renderMode": "events", "data": [float(high[index]) if curl_down[index] else None for index in range(n)]},
    ])
if show_signal_markers:
    signal_specs = [
        ("buy", "TRM Buy", "TRM", trm_buy_color, trm_buy, low),
        ("sell", "TRM Sell", "TRM", trm_sell_color, trm_sell, high),
        ("buy", "Reversal Up", "REV", bull_color, reversal_up, low),
        ("sell", "Reversal Down", "REV", bear_color, reversal_down, high),
        ("buy", "MA Cross Up", "MA", bull_color, ma_cross_up, low),
        ("sell", "MA Cross Down", "MA", bear_color, ma_cross_down, high),
        ("buy", "RSI Cross Up", "RSI", bull_color, rsi_up, low),
        ("sell", "RSI Cross Down", "RSI", bear_color, rsi_down, high),
        ("buy", "SAR Up", "SAR", bull_color, psar_up, low),
        ("sell", "SAR Down", "SAR", bear_color, psar_down, high),
    ]
    for signal_type, name, text, color, mask, prices in signal_specs:
        signals.append({"type": signal_type, "name": name, "text": text, "color": color, "renderMode": "events", "data": [float(prices[index]) if mask[index] else None for index in range(n)]})

rating_states = []
if n:
    last = n - 1
    rating_states = [
        ("TRM", 1 if trm_bullish[last] else -1 if trm_bearish[last] else 0),
        ("Reversal", 1 if t3_bullish[last] else -1 if t3_bearish[last] else 0),
        ("Curl", 1 if curl_up[last] else -1 if curl_down[last] else 0),
        ("RSI", 1 if np.isfinite(rsi_value[last]) and rsi_value[last] >= 50.0 else -1),
        ("TSI", 1 if np.isfinite(tsi_value[last]) and np.isfinite(tsi_signal[last]) and tsi_value[last] >= tsi_signal[last] else -1),
        (f"{ma_types[0]} {ma_lengths[0]}", 1 if np.isfinite(averages[0][last]) and close[last] >= averages[0][last] else -1),
        (f"{ma_types[1]} {ma_lengths[1]}", 1 if np.isfinite(averages[1][last]) and close[last] >= averages[1][last] else -1),
        (f"{ma_types[2]} {ma_lengths[2]}", 1 if np.isfinite(averages[2][last]) and close[last] >= averages[2][last] else -1),
        ("SAR", 1 if psar_bullish[last] else -1),
    ]
rating_score = int(sum(value for _, value in rating_states)) if rating_states else 0
tables = []
if show_rating:
    tables.append({
        "position": rating_position,
        "backgroundColor": "rgba(20,24,32,0.88)",
        "borderColor": bull_color if rating_score > 0 else bear_color if rating_score < 0 else hold_color,
        "textColor": "#FFFFFF",
        "accentColor": bull_color if rating_score > 0 else bear_color if rating_score < 0 else hold_color,
        "rows": [["SUPER RATING", f"{rating_score:+d}"]] + [
            [name, "Bull" if value > 0 else "Bear" if value < 0 else "Hold"]
            for name, value in rating_states
        ],
    })

last_rsi = float(rsi_value[-1]) if n and np.isfinite(rsi_value[-1]) else None
last_tsi = float(tsi_value[-1]) if n and np.isfinite(tsi_value[-1]) else None
output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "bar_colors": bar_colors,
    "tables": tables,
    "layers": [],
    "calculatedVars": {
        "strategy": strategy,
        "rating": rating_score,
        "rsi": last_rsi,
        "tsi": last_tsi,
        "regime": "bullish" if rating_score > 0 else "bearish" if rating_score < 0 else "neutral",
    },
}
