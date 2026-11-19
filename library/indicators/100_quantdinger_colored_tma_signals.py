# ============================================================
# QuantDinger Colored TMA Signals
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Colored TMA Signals"
my_indicator_description = (
    "Builds a trailing triangular average with volatility bands, marks confirmed "
    "direction turns, and emits one-per-trend breakout confirmations."
)

# @param half_length int 12 TMA half length range=1:200:1
# @param price_source str hlcc4 Price source values=close,open,high,low,hl2,hlc3,hlcc4
# @param band_deviation float 2.0 Band deviation range=0.1:10.0:0.1
# @param use_smoothing bool false Smooth the selected source
# @param smoothing_period int 12 Source smoothing period range=1:200:1
# @param show_bands bool true Show deviation bands
# @param show_turns bool true Show TMA direction turns
# @param line_width int 2 TMA line width range=1:5:1
# @param show_trade_signals bool true Show BUY and SELL labels
# @param trade_level str TMA Break level values=TMA,Bands
# @param confirm_bars int 3 Confirmation bars range=1:10:1
# @param trade_only_with_trend bool true Require matching TMA direction
# @param one_per_color bool true Allow one trade per direction segment

half_length = min(200, max(1, int(params.get("half_length", 12))))
price_source = str(params.get("price_source", "hlcc4")).lower()
band_deviation = max(0.1, float(params.get("band_deviation", 2.0)))
use_smoothing = bool(params.get("use_smoothing", False))
smoothing_period = min(200, max(1, int(params.get("smoothing_period", 12))))
show_bands = bool(params.get("show_bands", True))
show_turns = bool(params.get("show_turns", True))
line_width = min(5, max(1, int(params.get("line_width", 2))))
show_trade_signals = bool(params.get("show_trade_signals", True))
trade_level = str(params.get("trade_level", "TMA"))
confirm_bars = min(10, max(1, int(params.get("confirm_bars", 3))))
trade_only_with_trend = bool(params.get("trade_only_with_trend", True))
one_per_color = bool(params.get("one_per_color", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

if price_source == "open":
    source = open_price.copy()
elif price_source == "high":
    source = high.copy()
elif price_source == "low":
    source = low.copy()
elif price_source == "hl2":
    source = (high + low) / 2.0
elif price_source == "hlc3":
    source = (high + low + close) / 3.0
elif price_source == "hlcc4":
    source = (high + low + close + close) / 4.0
else:
    source = close.copy()


def rolling_mean(values, period):
    data = np.asarray(values, dtype=float)
    result = np.full(len(data), np.nan, dtype=float)
    if period <= 0:
        return result
    running_sum = 0.0
    finite_count = 0
    for index, value in enumerate(data):
        if np.isfinite(value):
            running_sum += float(value)
            finite_count += 1
        if index >= period:
            old_value = data[index - period]
            if np.isfinite(old_value):
                running_sum -= float(old_value)
                finite_count -= 1
        if index >= period - 1 and finite_count == period:
            result[index] = running_sum / period
    return result


def exponential_average(values, period):
    data = np.asarray(values, dtype=float)
    result = np.full(len(data), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    seed = []
    previous = np.nan
    for index, value in enumerate(data):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) < period:
                continue
            previous = float(np.mean(seed[-period:]))
        else:
            previous = alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def rolling_std(values, period):
    data = np.asarray(values, dtype=float)
    result = np.full(len(data), np.nan, dtype=float)
    for index in range(period - 1, len(data)):
        window = data[index - period + 1 : index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.std(window, ddof=0))
    return result


def pine_rma(values, period):
    data = np.asarray(values, dtype=float)
    result = np.full(len(data), np.nan, dtype=float)
    seed = []
    previous = np.nan
    for index, value in enumerate(data):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) < period:
                continue
            previous = float(np.mean(seed[-period:]))
        else:
            previous = (previous * (period - 1) + float(value)) / period
        result[index] = previous
    return result


base = exponential_average(source, smoothing_period) if use_smoothing else source
tma_length = half_length * 2 - 1
tma = rolling_mean(rolling_mean(base, half_length), half_length)
deviation = rolling_std(base, tma_length)
upper_band = tma + deviation * band_deviation
lower_band = tma - deviation * band_deviation

true_range = np.full(n, np.nan, dtype=float)
if n:
    true_range[0] = high[0] - low[0]
for index in range(1, n):
    true_range[index] = max(
        high[index] - low[index],
        abs(high[index] - close[index - 1]),
        abs(low[index] - close[index - 1]),
    )
atr = pine_rma(true_range, 14)

trend_direction = np.zeros(n, dtype=int)
bullish_turn = np.zeros(n, dtype=bool)
bearish_turn = np.zeros(n, dtype=bool)
last_solid_trend = 0
for index in range(1, n):
    if not np.isfinite(tma[index]) or not np.isfinite(tma[index - 1]):
        continue
    current_trend = 1 if tma[index] > tma[index - 1] else -1 if tma[index] < tma[index - 1] else 0
    trend_direction[index] = current_trend
    if current_trend != 0:
        bullish_turn[index] = current_trend == 1 and last_solid_trend == -1
        bearish_turn[index] = current_trend == -1 and last_solid_trend == 1
        last_solid_trend = current_trend

trade_buy = np.zeros(n, dtype=bool)
trade_sell = np.zeros(n, dtype=bool)
bars_above = 0
bars_below = 0
long_used = False
short_used = False
for index in range(n):
    if bullish_turn[index]:
        long_used = False
        short_used = True
    if bearish_turn[index]:
        short_used = False
        long_used = True

    bull_level = upper_band[index] if trade_level == "Bands" else tma[index]
    bear_level = lower_band[index] if trade_level == "Bands" else tma[index]
    if np.isfinite(bull_level) and close[index] > bull_level:
        bars_above += 1
    else:
        bars_above = 0
    if np.isfinite(bear_level) and close[index] < bear_level:
        bars_below += 1
    else:
        bars_below = 0

    direction = trend_direction[index]
    trend_allows_long = (not trade_only_with_trend) or direction == 1
    trend_allows_short = (not trade_only_with_trend) or direction == -1
    long_slot_free = (not one_per_color) or (not long_used)
    short_slot_free = (not one_per_color) or (not short_used)
    if bars_above == confirm_bars and trend_allows_long and long_slot_free:
        trade_buy[index] = True
        long_used = True
    if bars_below == confirm_bars and trend_allows_short and short_slot_free:
        trade_sell[index] = True
        short_used = True

tma_points = []
for index in range(n):
    if not np.isfinite(tma[index]):
        tma_points.append(None)
        continue
    color = "#45E67A" if trend_direction[index] == 1 else "#FF5364" if trend_direction[index] == -1 else "#8B95A5"
    tma_points.append({"value": float(tma[index]), "color": color})

turn_up_data = [float(tma[index]) if show_turns and bullish_turn[index] and np.isfinite(tma[index]) else None for index in range(n)]
turn_down_data = [float(tma[index]) if show_turns and bearish_turn[index] and np.isfinite(tma[index]) else None for index in range(n)]
buy_data = []
sell_data = []
for index in range(n):
    gap = max(float(atr[index]) * 1.5 if np.isfinite(atr[index]) else 0.0, float(high[index] - low[index]))
    buy_data.append(float(low[index] - gap) if show_trade_signals and trade_buy[index] else None)
    sell_data.append(float(high[index] + gap) if show_trade_signals and trade_sell[index] else None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Colored TMA", "data": tma_points, "color": "#8B95A5", "type": "line", "lineWidth": line_width, "overlay": True},
        {"name": "Upper Band", "data": [float(value) if show_bands and np.isfinite(value) else None for value in upper_band], "color": "rgba(139, 149, 165, 0.55)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower Band", "data": [float(value) if show_bands and np.isfinite(value) else None for value in lower_band], "color": "rgba(139, 149, 165, 0.55)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "▲", "name": "TMA Bullish Turn", "data": turn_up_data, "color": "#45E67A", "renderMode": "events"},
        {"type": "sell", "text": "▼", "name": "TMA Bearish Turn", "data": turn_down_data, "color": "#FF5364", "renderMode": "events"},
        {"type": "buy", "text": "BUY", "name": "Confirmed Buy", "data": buy_data, "color": "rgba(69, 230, 122, 0.78)", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Confirmed Sell", "data": sell_data, "color": "rgba(255, 83, 100, 0.78)", "renderMode": "events"},
    ],
    "layers": [],
}
