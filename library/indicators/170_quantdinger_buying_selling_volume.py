# ============================================================
# QuantDinger Buying and Selling Volume
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Buying and Selling Volume"
my_indicator_description = (
    "Splits candle volume into buying and selling pressure, shows the dominant side above zero "
    "and the weaker side below zero, and optionally switches to a normalized volume index."
)

# @param volume_index_mode bool false Show normalized volume index instead of raw pressure
# @param volume_index_threshold float 1.0 Elevated volume index threshold range=0.1:10.0:0.1
# @param color_from_previous_close bool false Classify base volume from the previous close
# @param show_percent_points bool false Show buy and sell percentage points
# @param buy_color str #22C55E Buying pressure color
# @param sell_color str #EF4444 Selling pressure color
# @param bullish_volume_color str #16A34A Bullish base volume color
# @param bearish_volume_color str #DC2626 Bearish base volume color
# @param elevated_bull_color str #00FFFF Elevated bullish index color
# @param elevated_bear_color str #808080 Elevated bearish index color
# @param normal_index_color str #EAB308 Normal volume index color

volume_index_mode = bool(params.get("volume_index_mode", False))
volume_index_threshold = min(10.0, max(0.1, float(params.get("volume_index_threshold", 1.0))))
color_from_previous_close = bool(params.get("color_from_previous_close", False))
show_percent_points = bool(params.get("show_percent_points", False))
buy_color = str(params.get("buy_color", "#22C55E"))
sell_color = str(params.get("sell_color", "#EF4444"))
bullish_volume_color = str(params.get("bullish_volume_color", "#16A34A"))
bearish_volume_color = str(params.get("bearish_volume_color", "#DC2626"))
elevated_bull_color = str(params.get("elevated_bull_color", "#00FFFF"))
elevated_bear_color = str(params.get("elevated_bear_color", "#808080"))
normal_index_color = str(params.get("normal_index_color", "#EAB308"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

def ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    seed = values[:length]
    if not np.all(np.isfinite(seed)):
        return result
    result[length - 1] = float(np.mean(seed))
    alpha = 2.0 / (length + 1.0)
    for index in range(length, len(values)):
        if np.isfinite(values[index]):
            result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
        else:
            result[index] = result[index - 1]
    return result

buy_volume = np.zeros(n, dtype=float)
sell_volume = np.zeros(n, dtype=float)
safe_volume = np.where(volume > 0.0, volume, 1.0)
for index in range(n):
    candle_range = float(high[index] - low[index])
    if candle_range > 0.0:
        buy_volume[index] = float(volume[index]) * min(1.0, max(0.0, float(close[index] - low[index]) / candle_range))
        sell_volume[index] = float(volume[index]) * min(1.0, max(0.0, float(high[index] - close[index]) / candle_range))

total_pressure = buy_volume + sell_volume
buy_pressure = np.zeros(n, dtype=float)
sell_pressure = np.zeros(n, dtype=float)
valid_total = total_pressure > 0.0
buy_pressure[valid_total] = buy_volume[valid_total] / total_pressure[valid_total] * safe_volume[valid_total]
sell_pressure[valid_total] = sell_volume[valid_total] / total_pressure[valid_total] * safe_volume[valid_total]

volume_ema = ema(safe_volume, 20)
buy_ema = ema(buy_volume, 20)
sell_ema = ema(sell_volume, 20)
volume_index = np.full(n, np.nan, dtype=float)
normalized_buy = np.full(n, np.nan, dtype=float)
normalized_sell = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.isfinite(volume_ema[index]) and volume_ema[index] > 0.0:
        volume_index[index] = safe_volume[index] / volume_ema[index]
    if np.isfinite(volume_index[index]) and np.isfinite(buy_ema[index]) and buy_ema[index] > 0.0:
        normalized_buy[index] = buy_volume[index] / buy_ema[index] * volume_index[index] * 100.0
    if np.isfinite(volume_index[index]) and np.isfinite(sell_ema[index]) and sell_ema[index] > 0.0:
        normalized_sell[index] = sell_volume[index] / sell_ema[index] * volume_index[index] * 100.0

buy_conditioned = np.where(buy_pressure > sell_pressure, buy_pressure, -np.abs(buy_pressure))
sell_conditioned = np.where(sell_pressure > buy_pressure, sell_pressure, -np.abs(sell_pressure))
normalized_buy_conditioned = np.where(normalized_buy > normalized_sell, normalized_buy, -np.abs(normalized_buy))
normalized_sell_conditioned = np.where(normalized_sell > normalized_buy, normalized_sell, -np.abs(normalized_sell))

buy_percent = np.zeros(n, dtype=float)
sell_percent = np.zeros(n, dtype=float)
buy_percent[valid_total] = 100.0 * buy_volume[valid_total] / total_pressure[valid_total]
sell_percent[valid_total] = 100.0 * sell_volume[valid_total] / total_pressure[valid_total]

base_bull = [None for _ in range(n)]
base_bear = [None for _ in range(n)]
for index in range(n):
    rising = close[index] >= (close[index - 1] if color_from_previous_close and index > 0 else open_price[index])
    if rising:
        base_bull[index] = float(volume[index])
    else:
        base_bear[index] = float(volume[index])

plots = []
if volume_index_mode:
    normal_index = [None for _ in range(n)]
    elevated_bull = [None for _ in range(n)]
    elevated_bear = [None for _ in range(n)]
    for index in range(n):
        if not np.isfinite(volume_index[index]):
            continue
        value = float(volume_index[index])
        if value > volume_index_threshold and close[index] > open_price[index]:
            elevated_bull[index] = value
        elif value > volume_index_threshold and close[index] < open_price[index]:
            elevated_bear[index] = value
        else:
            normal_index[index] = value
    plots.extend(
        [
            {"name": "Normal Volume Index", "data": normal_index, "color": normal_index_color, "type": "column", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
            {"name": "Elevated Bull Volume Index", "data": elevated_bull, "color": elevated_bull_color, "type": "column", "baseValue": 0.0, "lineWidth": 3, "overlay": False},
            {"name": "Elevated Bear Volume Index", "data": elevated_bear, "color": elevated_bear_color, "type": "column", "baseValue": 0.0, "lineWidth": 3, "overlay": False},
        ]
    )
else:
    plots.extend(
        [
            {"name": "Bullish Base Volume", "data": base_bull, "color": bullish_volume_color, "type": "column", "baseValue": 0.0, "lineWidth": 1, "opacity": 0.24, "overlay": False},
            {"name": "Bearish Base Volume", "data": base_bear, "color": bearish_volume_color, "type": "column", "baseValue": 0.0, "lineWidth": 1, "opacity": 0.24, "overlay": False},
            {"name": "Buying Pressure", "data": [float(value) for value in buy_conditioned], "color": buy_color, "type": "column", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
            {"name": "Selling Pressure", "data": [float(value) for value in sell_conditioned], "color": sell_color, "type": "column", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
        ]
    )
    if show_percent_points:
        plots.extend(
            [
                {"name": "Buy Volume Percent", "data": [float(value) for value in buy_percent], "color": buy_color, "type": "circle", "size": 3, "overlay": False},
                {"name": "Sell Volume Percent", "data": [float(value) for value in sell_percent], "color": sell_color, "type": "circle", "size": 3, "overlay": False},
            ]
        )

summary = {
    "latestBuyPercent": float(buy_percent[-1]) if n else 0.0,
    "latestSellPercent": float(sell_percent[-1]) if n else 0.0,
    "latestVolumeIndex": float(volume_index[-1]) if n and np.isfinite(volume_index[-1]) else None,
    "latestNormalizedBuyPressure": float(normalized_buy_conditioned[-1]) if n and np.isfinite(normalized_buy_conditioned[-1]) else None,
    "latestNormalizedSellPressure": float(normalized_sell_conditioned[-1]) if n and np.isfinite(normalized_sell_conditioned[-1]) else None,
}
output = {"name": my_indicator_name, "plots": plots, "signals": [], "summary": summary}
