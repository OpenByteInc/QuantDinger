# ============================================================
# QuantDinger Buy/Sell Volume Totals
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Buy/Sell Volume Totals"
my_indicator_description = (
    "Estimates buy and sell volume from each candle's close location, accumulates both sides "
    "inside a configurable date window, and highlights the dominant cumulative side."
)

# @param start_time_ms int 1639094400000 Accumulation start timestamp in milliseconds
# @param use_end_time bool false Stop accumulation at the end timestamp
# @param end_time_ms int 1641254400000 Accumulation end timestamp in milliseconds
# @param show_regime bool true Show cumulative accumulation/distribution tint
# @param total_volume_color str #D94B4B Total volume color
# @param buy_volume_color str #14B8A6 Estimated buy volume color
# @param accumulation_color str #22C55E Accumulation regime color
# @param distribution_color str #EF4444 Distribution regime color

start_time_ms = int(params.get("start_time_ms", 1639094400000))
use_end_time = bool(params.get("use_end_time", False))
end_time_ms = int(params.get("end_time_ms", 1641254400000))
show_regime = bool(params.get("show_regime", True))
total_volume_color = str(params.get("total_volume_color", "#D94B4B"))
buy_volume_color = str(params.get("buy_volume_color", "#14B8A6"))
accumulation_color = str(params.get("accumulation_color", "#22C55E"))
distribution_color = str(params.get("distribution_color", "#EF4444"))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
raw_time = df["time"].astype("int64").to_numpy()
n = len(df)

time_ms = raw_time.copy()
if n and abs(int(time_ms[-1])) < 100000000000:
    time_ms = time_ms * 1000

buy_volume = np.zeros(n, dtype=float)
sell_volume = np.zeros(n, dtype=float)
total_buy = np.zeros(n, dtype=float)
total_sell = np.zeros(n, dtype=float)
buy_sell_ratio = np.zeros(n, dtype=float)
accumulation_tint = [None for _ in range(n)]
distribution_tint = [None for _ in range(n)]

running_buy = 0.0
running_sell = 0.0
for index in range(n):
    candle_range = float(high[index] - low[index])
    if candle_range > 0.0:
        buy_value = float(volume[index]) * min(1.0, max(0.0, float(close[index] - low[index]) / candle_range))
        sell_value = float(volume[index]) * min(1.0, max(0.0, float(high[index] - close[index]) / candle_range))
    else:
        buy_value = 0.0
        sell_value = 0.0
    buy_volume[index] = buy_value
    sell_volume[index] = sell_value

    in_range = int(time_ms[index]) >= start_time_ms and (not use_end_time or int(time_ms[index]) <= end_time_ms)
    if not in_range:
        running_buy = 0.0
        running_sell = 0.0
    else:
        running_buy += buy_value
        running_sell += sell_value

    total_buy[index] = running_buy
    total_sell[index] = running_sell
    buy_sell_ratio[index] = running_buy / running_sell if running_sell > 0.0 else 0.0
    if show_regime and in_range:
        if running_buy > running_sell:
            accumulation_tint[index] = float(volume[index])
        else:
            distribution_tint[index] = float(volume[index])

plots = [
    {"name": "Accumulation Regime", "data": accumulation_tint, "color": accumulation_color, "type": "column", "lineWidth": 1, "overlay": False, "opacity": 0.16},
    {"name": "Distribution Regime", "data": distribution_tint, "color": distribution_color, "type": "column", "lineWidth": 1, "overlay": False, "opacity": 0.16},
    {"name": "Total Volume", "data": [float(value) for value in volume], "color": total_volume_color, "type": "column", "lineWidth": 1, "overlay": False, "opacity": 0.58},
    {"name": "Estimated Buy Volume", "data": [float(value) for value in buy_volume], "color": buy_volume_color, "type": "column", "lineWidth": 1, "overlay": False, "opacity": 0.92},
]

summary = {
    "cumulativeBuyVolume": float(total_buy[-1]) if n else 0.0,
    "cumulativeSellVolume": float(total_sell[-1]) if n else 0.0,
    "cumulativeBuySellRatio": float(buy_sell_ratio[-1]) if n else 0.0,
}
output = {"name": my_indicator_name, "plots": plots, "signals": [], "summary": summary}
