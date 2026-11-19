# ============================================================
# QuantDinger Buying Selling Volume
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Buying Selling Volume"
my_indicator_description = (
    "Splits each candle's volume into buying and selling pressure, optionally normalizes "
    "pressure by its EMA, and always displays the relative volume index."
)

# @param color_volume_from_previous_close bool false Color base volume from previous close
# @param color_price_bars bool false Color price bars from dominant pressure
# @param volume_index_mode bool false Hide raw pressure and use normalized pressure for bar colors
# @param color_price_bars_from_index bool false Color price bars from volume-index regime
# @param volume_index_threshold float 1.0 Elevated volume-index threshold range=0.1:10.0:0.1
# @param show_percent_points bool true Show buy and sell percentage points in raw mode
# @param buy_color str #22C55E Buying pressure color
# @param sell_color str #EF4444 Selling pressure color
# @param bullish_volume_color str rgba(34,197,94,0.30) Bullish base volume color
# @param bearish_volume_color str rgba(239,68,68,0.30) Bearish base volume color
# @param elevated_bull_color str #00FFFF Elevated bullish index color
# @param elevated_bear_color str #94A3B8 Elevated bearish index color
# @param normal_index_color str #EAB308 Normal volume-index color

color_from_previous = bool(params.get("color_volume_from_previous_close", False))
color_price_bars = bool(params.get("color_price_bars", False))
volume_index_mode = bool(params.get("volume_index_mode", False))
color_price_bars_from_index = bool(params.get("color_price_bars_from_index", False))
volume_index_threshold = min(10.0, max(0.1, float(params.get("volume_index_threshold", 1.0))))
show_percent_points = bool(params.get("show_percent_points", True))
buy_color = str(params.get("buy_color", "#22C55E"))
sell_color = str(params.get("sell_color", "#EF4444"))
bullish_volume_color = str(params.get("bullish_volume_color", "rgba(34,197,94,0.30)"))
bearish_volume_color = str(params.get("bearish_volume_color", "rgba(239,68,68,0.30)"))
elevated_bull_color = str(params.get("elevated_bull_color", "#00FFFF"))
elevated_bear_color = str(params.get("elevated_bear_color", "#94A3B8"))
normal_index_color = str(params.get("normal_index_color", "#EAB308"))

frame = df.copy()
frame = frame.reset_index(drop=True)
n = len(frame)
open_values = pd.to_numeric(frame["open"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
high_values = pd.to_numeric(frame["high"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
volume_values = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)


def ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if not len(values):
        return result
    alpha = 2.0 / (float(length) + 1.0)
    result[0] = float(values[0])
    for index in range(1, len(values)):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result


buy_volume = np.zeros(n, dtype=float)
sell_volume = np.zeros(n, dtype=float)
for index in range(n):
    candle_range = float(high_values[index] - low_values[index])
    if candle_range != 0.0:
        buy_volume[index] = float(volume_values[index]) * float(close_values[index] - low_values[index]) / candle_range
        sell_volume[index] = float(volume_values[index]) * float(high_values[index] - close_values[index]) / candle_range

safe_volume = np.where(volume_values > 0.0, volume_values, 1.0)
total_pressure = buy_volume + sell_volume
buy_pressure = np.full(n, np.nan, dtype=float)
sell_pressure = np.full(n, np.nan, dtype=float)
valid_pressure = total_pressure != 0.0
buy_pressure[valid_pressure] = buy_volume[valid_pressure] / total_pressure[valid_pressure] * safe_volume[valid_pressure]
sell_pressure[valid_pressure] = sell_volume[valid_pressure] / total_pressure[valid_pressure] * safe_volume[valid_pressure]

volume_ema = ema(safe_volume, 20)
buy_ema = ema(buy_volume, 20)
sell_ema = ema(sell_volume, 20)
volume_index = np.full(n, np.nan, dtype=float)
normalized_buy = np.full(n, np.nan, dtype=float)
normalized_sell = np.full(n, np.nan, dtype=float)
valid_volume_ema = np.isfinite(volume_ema) & (volume_ema != 0.0)
volume_index[valid_volume_ema] = safe_volume[valid_volume_ema] / volume_ema[valid_volume_ema]
valid_buy_ema = np.isfinite(volume_index) & np.isfinite(buy_ema) & (buy_ema != 0.0)
valid_sell_ema = np.isfinite(volume_index) & np.isfinite(sell_ema) & (sell_ema != 0.0)
normalized_buy[valid_buy_ema] = buy_volume[valid_buy_ema] / buy_ema[valid_buy_ema] * volume_index[valid_buy_ema] * 100.0
normalized_sell[valid_sell_ema] = sell_volume[valid_sell_ema] / sell_ema[valid_sell_ema] * volume_index[valid_sell_ema] * 100.0

buy_conditioned = np.where(buy_pressure > sell_pressure, buy_pressure, -np.abs(buy_pressure))
sell_conditioned = np.where(sell_pressure > buy_pressure, sell_pressure, -np.abs(sell_pressure))
normalized_buy_conditioned = np.where(normalized_buy > normalized_sell, normalized_buy, -np.abs(normalized_buy))
normalized_sell_conditioned = np.where(normalized_sell > normalized_buy, normalized_sell, -np.abs(normalized_sell))

buy_percent = np.full(n, np.nan, dtype=float)
sell_percent = np.full(n, np.nan, dtype=float)
buy_percent[valid_pressure] = 100.0 * buy_volume[valid_pressure] / total_pressure[valid_pressure]
sell_percent[valid_pressure] = 100.0 * sell_volume[valid_pressure] / total_pressure[valid_pressure]

base_bull = [None] * n
base_bear = [None] * n
normal_index = [None] * n
elevated_bull_index = [None] * n
elevated_bear_index = [None] * n
index_colors = [normal_index_color] * n
dominance_colors = ["#64748B"] * n
for index in range(n):
    comparison = close_values[index - 1] if color_from_previous and index > 0 else open_values[index]
    if close_values[index] < comparison:
        base_bear[index] = float(volume_values[index])
    else:
        base_bull[index] = float(volume_values[index])

    buy_comparison = normalized_buy_conditioned[index] if volume_index_mode else buy_conditioned[index]
    sell_comparison = normalized_sell_conditioned[index] if volume_index_mode else sell_conditioned[index]
    if np.isfinite(buy_comparison) and np.isfinite(sell_comparison):
        dominance_colors[index] = "#2563EB" if buy_comparison > sell_comparison else "#7C3AED" if buy_comparison < sell_comparison else "#64748B"

    if not np.isfinite(volume_index[index]):
        continue
    index_value = float(volume_index[index])
    if index_value > volume_index_threshold and close_values[index] > open_values[index]:
        elevated_bull_index[index] = index_value
        index_colors[index] = elevated_bull_color
    elif index_value > volume_index_threshold and close_values[index] < open_values[index]:
        elevated_bear_index[index] = index_value
        index_colors[index] = elevated_bear_color
    else:
        normal_index[index] = index_value
        index_colors[index] = normal_index_color


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


plots = []
if not volume_index_mode:
    plots.extend([
        {"name": "Bullish Base Volume", "data": base_bull, "color": bullish_volume_color, "type": "column", "baseValue": 0.0, "lineWidth": 3, "overlay": False},
        {"name": "Bearish Base Volume", "data": base_bear, "color": bearish_volume_color, "type": "column", "baseValue": 0.0, "lineWidth": 3, "overlay": False},
        {"name": "Buy Volume", "data": finite(buy_conditioned), "color": buy_color, "type": "column", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
        {"name": "Sell Volume", "data": finite(sell_conditioned), "color": sell_color, "type": "column", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
    ])
    if show_percent_points:
        plots.extend([
            {"name": "Buy Volume Percent", "data": finite(buy_percent), "color": buy_color, "type": "circle", "size": 3, "overlay": False},
            {"name": "Sell Volume Percent", "data": finite(sell_percent), "color": sell_color, "type": "circle", "size": 3, "overlay": False},
        ])

plots.extend([
    {"name": "Normal Volume Index", "data": normal_index, "color": normal_index_color, "type": "column", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
    {"name": "Elevated Bull Volume Index", "data": elevated_bull_index, "color": elevated_bull_color, "type": "column", "baseValue": 0.0, "lineWidth": 3, "overlay": False},
    {"name": "Elevated Bear Volume Index", "data": elevated_bear_index, "color": elevated_bear_color, "type": "column", "baseValue": 0.0, "lineWidth": 3, "overlay": False},
])

bar_colors = [None] * n
if color_price_bars:
    bar_colors = dominance_colors.copy()
if color_price_bars_from_index:
    bar_colors = index_colors.copy()

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": [None] * n,
    "calculatedVars": {
        "buyVolume": finite(buy_volume),
        "sellVolume": finite(sell_volume),
        "buyPressure": finite(buy_pressure),
        "sellPressure": finite(sell_pressure),
        "volumeIndex": finite(volume_index),
        "normalizedBuy": finite(normalized_buy),
        "normalizedSell": finite(normalized_sell),
        "buyConditioned": finite(buy_conditioned),
        "sellConditioned": finite(sell_conditioned),
        "normalizedBuyConditioned": finite(normalized_buy_conditioned),
        "normalizedSellConditioned": finite(normalized_sell_conditioned),
        "buyPercent": finite(buy_percent),
        "sellPercent": finite(sell_percent),
    },
}
