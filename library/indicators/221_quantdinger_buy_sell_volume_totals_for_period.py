# ============================================================
# QuantDinger Buy/Sell Volume Totals for Period
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Buy/Sell Volume Totals for Period"
my_indicator_description = (
    "Estimates candle buy and sell volume, accumulates both sides over a selected "
    "date window, and highlights whether cumulative flow favors accumulation or distribution."
)

# @param start_time_ms int 1639094400000 Accumulation start timestamp in milliseconds
# @param use_end_time bool false Stop accumulation at the end timestamp
# @param end_time_ms int 1641254400000 Accumulation end timestamp in milliseconds
# @param show_regime_background bool true Show accumulation/distribution background
# @param total_volume_color str #EF4444 Total volume color
# @param buy_volume_color str #14B8A6 Estimated buy volume color
# @param accumulation_color str rgba(34,197,94,0.12) Accumulation background
# @param distribution_color str rgba(239,68,68,0.12) Distribution background
# @param show_dashboard bool true Show cumulative summary

start_time_ms = int(params.get("start_time_ms", 1639094400000))
use_end_time = bool(params.get("use_end_time", False))
end_time_ms = int(params.get("end_time_ms", 1641254400000))
show_regime_background = bool(params.get("show_regime_background", True))
total_volume_color = str(params.get("total_volume_color", "#EF4444"))
buy_volume_color = str(params.get("buy_volume_color", "#14B8A6"))
accumulation_color = str(params.get("accumulation_color", "rgba(34,197,94,0.12)"))
distribution_color = str(params.get("distribution_color", "rgba(239,68,68,0.12)"))
show_dashboard = bool(params.get("show_dashboard", True))

frame = df.copy()
frame = frame.reset_index(drop=True)
n = len(frame)
if "time" not in frame.columns:
    frame["time"] = np.arange(n, dtype=float) * 3600.0
high_values = pd.to_numeric(frame["high"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
volume_values = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(dtype=float)
time_values = pd.to_numeric(frame["time"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
time_ms = time_values.astype(np.int64, copy=True)
if n and abs(int(time_ms[-1])) < 100000000000:
    time_ms = time_ms * 1000

buy_volume = np.zeros(n, dtype=float)
sell_volume = np.zeros(n, dtype=float)
total_buy = np.zeros(n, dtype=float)
total_sell = np.zeros(n, dtype=float)
buy_sell_ratio = np.zeros(n, dtype=float)
in_date_range = np.zeros(n, dtype=bool)
is_accumulation = np.zeros(n, dtype=bool)
background_colors = [None] * n

running_buy = 0.0
running_sell = 0.0
for index in range(n):
    candle_range = float(high_values[index] - low_values[index])
    if candle_range == 0.0:
        current_buy = 0.0
        current_sell = 0.0
    else:
        current_buy = float(volume_values[index]) * (float(close_values[index] - low_values[index]) / candle_range)
        current_sell = float(volume_values[index]) * (float(high_values[index] - close_values[index]) / candle_range)
    buy_volume[index] = current_buy
    sell_volume[index] = current_sell

    in_range = int(time_ms[index]) >= start_time_ms and (not use_end_time or int(time_ms[index]) <= end_time_ms)
    in_date_range[index] = in_range
    if not in_range:
        running_buy = 0.0
        running_sell = 0.0
    else:
        running_buy += current_buy
        running_sell += current_sell

    total_buy[index] = running_buy
    total_sell[index] = running_sell
    buy_sell_ratio[index] = running_buy / running_sell if running_sell > 0.0 else 0.0
    is_accumulation[index] = running_buy > running_sell
    if show_regime_background and in_range:
        background_colors[index] = accumulation_color if is_accumulation[index] else distribution_color

plots = [
    {
        "name": "Total Volume",
        "data": [float(value) for value in volume_values],
        "color": total_volume_color,
        "type": "column",
        "lineWidth": 1,
        "overlay": False,
        "opacity": 0.55,
    },
    {
        "name": "Estimated Buy Volume",
        "data": [float(value) for value in buy_volume],
        "color": buy_volume_color,
        "type": "column",
        "lineWidth": 1,
        "overlay": False,
        "opacity": 0.95,
    },
]

last_index = n - 1
last_buy = float(total_buy[last_index]) if n else 0.0
last_sell = float(total_sell[last_index]) if n else 0.0
last_ratio = float(buy_sell_ratio[last_index]) if n else 0.0
last_in_range = bool(in_date_range[last_index]) if n else False
regime_text = "Accumulation" if n and is_accumulation[last_index] else "Distribution"
if not last_in_range:
    regime_text = "Outside Window"

tables = []
if show_dashboard:
    tables.append({
        "position": "top-right",
        "backgroundColor": "rgba(15,23,42,0.90)",
        "borderColor": "#22C55E" if regime_text == "Accumulation" else "#EF4444" if regime_text == "Distribution" else "#64748B",
        "textColor": "#F8FAFC",
        "accentColor": "#22C55E" if regime_text == "Accumulation" else "#EF4444" if regime_text == "Distribution" else "#64748B",
        "rows": [
            ["BUY / SELL VOLUME", regime_text],
            ["Cumulative Buy", f"{last_buy:,.3f}"],
            ["Cumulative Sell", f"{last_sell:,.3f}"],
            ["Buy / Sell Ratio", f"{last_ratio:.3f}"],
        ],
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": background_colors,
    "calculatedVars": {
        "buyVolume": [float(value) for value in buy_volume],
        "sellVolume": [float(value) for value in sell_volume],
        "totalBuyVolume": [float(value) for value in total_buy],
        "totalSellVolume": [float(value) for value in total_sell],
        "buySellRatio": [float(value) for value in buy_sell_ratio],
        "inDateRange": [bool(value) for value in in_date_range],
        "isAccumulation": [bool(value) for value in is_accumulation],
    },
}
