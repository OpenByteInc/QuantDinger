# ============================================================
# QuantDinger Colored TMA Trend Signals
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Colored TMA Trend Signals"
my_indicator_description = (
    "Plots a colored triangular moving average with deviation bands, confirmed "
    "trend turns, and governed breakout trade signals."
)

indicator_timeframes = ["1H", "4H", "1D", "1W"]

# @param half_length int 12 TMA half length range=1:200:1
# @param price_source str Weighted_Price Price source values=Close,Open,High,Low,Median_Price,Typical_Price,Weighted_Price
# @param band_deviation float 2.0 Band deviation range=0.1:10.0:0.1
# @param use_smoothing bool false Smooth source with EMA
# @param smoothing_period int 12 Source smoothing period range=1:200:1
# @param show_bands bool true Show deviation bands
# @param show_trend_signals bool true Show TMA color-change markers
# @param line_width int 2 TMA line width range=1:5:1
# @param show_trade_signals bool true Show BUY/SELL markers
# @param trade_level str TMA Break level values=TMA,Bands
# @param confirm_bars int 3 Consecutive confirmation bars range=1:10:1
# @param trade_only_with_trend bool true Require matching TMA trend
# @param one_per_color bool true Allow one trade per matching color turn
# @param bullish_color str #22C55E Bullish color
# @param bearish_color str #EF4444 Bearish color
# @param neutral_color str #94A3B8 Neutral color
# @param band_color str rgba(148,163,184,0.50) Band color
# @param signal_lookback int 240 Visible signal lookback range=40:500:10
# @param max_trade_labels int 16 Maximum visible BUY/SELL labels range=2:40:1
# @param signal_spacing int 4 Minimum bars between visible trade labels range=0:20:1

half_length = min(200, max(1, int(params.get("half_length", 12))))
price_source = str(params.get("price_source", "Weighted_Price"))
band_deviation = min(10.0, max(0.1, float(params.get("band_deviation", 2.0))))
use_smoothing = bool(params.get("use_smoothing", False))
smoothing_period = min(200, max(1, int(params.get("smoothing_period", 12))))
show_bands = bool(params.get("show_bands", True))
show_trend_signals = bool(params.get("show_trend_signals", True))
line_width = min(5, max(1, int(params.get("line_width", 2))))
show_trade_signals = bool(params.get("show_trade_signals", True))
trade_level = str(params.get("trade_level", "TMA")).upper()
confirm_bars = min(10, max(1, int(params.get("confirm_bars", 3))))
trade_only_with_trend = bool(params.get("trade_only_with_trend", True))
one_per_color = bool(params.get("one_per_color", True))
bullish_color = str(params.get("bullish_color", "#22C55E"))
bearish_color = str(params.get("bearish_color", "#EF4444"))
neutral_color = str(params.get("neutral_color", "#94A3B8"))
band_color = str(params.get("band_color", "rgba(148,163,184,0.50)"))
signal_lookback = min(500, max(40, int(params.get("signal_lookback", 240))))
max_trade_labels = min(40, max(2, int(params.get("max_trade_labels", 16))))
signal_spacing = min(20, max(0, int(params.get("signal_spacing", 4))))

frame = df.copy().reset_index(drop=True)
n = len(frame)


def numeric(column):
    return pd.to_numeric(frame[column], errors="coerce").ffill().bfill().fillna(0.0).to_numpy(dtype=float)


open_values = numeric("open")
high_values = numeric("high")
low_values = numeric("low")
close_values = numeric("close")

source_key = price_source.replace(" ", "_").upper()
source_choices = {
    "OPEN": open_values,
    "HIGH": high_values,
    "LOW": low_values,
    "MEDIAN_PRICE": (high_values + low_values) * 0.5,
    "TYPICAL_PRICE": (high_values + low_values + close_values) / 3.0,
    "WEIGHTED_PRICE": (high_values + low_values + 2.0 * close_values) * 0.25,
}
source = source_choices.get(source_key, close_values).astype(float)


def sma(values, length):
    return pd.Series(values, dtype=float).rolling(length, min_periods=length).mean().to_numpy(dtype=float)


def ema(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    seed = values[:length]
    if not np.all(np.isfinite(seed)):
        return result
    previous = float(np.mean(seed))
    result[length - 1] = previous
    alpha = 2.0 / (float(length) + 1.0)
    for index in range(length, len(values)):
        if np.isfinite(values[index]):
            previous = alpha * float(values[index]) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def rma(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    seed = values[:length]
    if not np.all(np.isfinite(seed)):
        return result
    previous = float(np.mean(seed))
    result[length - 1] = previous
    for index in range(length, len(values)):
        if np.isfinite(values[index]):
            previous = (previous * (length - 1) + float(values[index])) / float(length)
        result[index] = previous
    return result


base = ema(source, smoothing_period) if use_smoothing else source.copy()
tma_length = half_length * 2 - 1
tma = sma(sma(base, half_length), half_length)
deviation = pd.Series(base, dtype=float).rolling(tma_length, min_periods=tma_length).std(ddof=0).to_numpy(dtype=float)
upper_band = tma + deviation * band_deviation
lower_band = tma - deviation * band_deviation

true_range = np.zeros(n, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high_values[index] - low_values[index]
    else:
        true_range[index] = max(
            high_values[index] - low_values[index],
            abs(high_values[index] - close_values[index - 1]),
            abs(low_values[index] - close_values[index - 1]),
        )
atr = rma(true_range, 14)

trend_up = np.zeros(n, dtype=bool)
trend_down = np.zeros(n, dtype=bool)
bullish_turn = np.zeros(n, dtype=bool)
bearish_turn = np.zeros(n, dtype=bool)
last_solid_trend = 0
for index in range(1, n):
    if np.isfinite(tma[index]) and np.isfinite(tma[index - 1]):
        trend_up[index] = tma[index] > tma[index - 1]
        trend_down[index] = tma[index] < tma[index - 1]
        current_trend = 1 if trend_up[index] else -1 if trend_down[index] else 0
        if current_trend != 0:
            bullish_turn[index] = current_trend == 1 and last_solid_trend == -1
            bearish_turn[index] = current_trend == -1 and last_solid_trend == 1
            last_solid_trend = current_trend

bull_break = upper_band if trade_level == "BANDS" else tma
bear_break = lower_band if trade_level == "BANDS" else tma
bars_above = np.zeros(n, dtype=int)
bars_below = np.zeros(n, dtype=int)
trade_bull = np.zeros(n, dtype=bool)
trade_bear = np.zeros(n, dtype=bool)
long_used = False
short_used = False
for index in range(n):
    if bullish_turn[index]:
        long_used = False
        short_used = True
    if bearish_turn[index]:
        short_used = False
        long_used = True
    bars_above[index] = bars_above[index - 1] + 1 if index > 0 and np.isfinite(bull_break[index]) and close_values[index] > bull_break[index] else (1 if np.isfinite(bull_break[index]) and close_values[index] > bull_break[index] else 0)
    bars_below[index] = bars_below[index - 1] + 1 if index > 0 and np.isfinite(bear_break[index]) and close_values[index] < bear_break[index] else (1 if np.isfinite(bear_break[index]) and close_values[index] < bear_break[index] else 0)
    trend_allows_long = not trade_only_with_trend or trend_up[index]
    trend_allows_short = not trade_only_with_trend or trend_down[index]
    long_slot_free = not one_per_color or not long_used
    short_slot_free = not one_per_color or not short_used
    if bars_above[index] == confirm_bars and trend_allows_long and long_slot_free:
        trade_bull[index] = True
        long_used = True
    if bars_below[index] == confirm_bars and trend_allows_short and short_slot_free:
        trade_bear[index] = True
        short_used = True

tma_colors = []
for index in range(n):
    if trend_up[index]:
        tma_colors.append(bullish_color)
    elif trend_down[index]:
        tma_colors.append(bearish_color)
    else:
        tma_colors.append(neutral_color)


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def colored(values, colors):
    return [{"value": float(value), "color": colors[index]} if np.isfinite(value) else None for index, value in enumerate(values)]


def visible_indexes(events, maximum, spacing):
    candidates = [index for index in range(max(0, n - signal_lookback), n) if events[index]]
    if n > 80 and spacing > 0:
        spaced = []
        for index in candidates:
            if spaced and index - spaced[-1] < spacing:
                spaced[-1] = index
            else:
                spaced.append(index)
        candidates = spaced
    return set(candidates[-maximum:])


turn_events = bullish_turn | bearish_turn
visible_turns = visible_indexes(turn_events, 40, 0)
trade_events = trade_bull | trade_bear
visible_trades = visible_indexes(trade_events, max_trade_labels, signal_spacing)

bull_turn_marks = [float(tma[index]) if show_trend_signals and bullish_turn[index] and index in visible_turns else None for index in range(n)]
bear_turn_marks = [float(tma[index]) if show_trend_signals and bearish_turn[index] and index in visible_turns else None for index in range(n)]
buy_marks = [None] * n
sell_marks = [None] * n
for index in range(n):
    gap = max((float(atr[index]) if np.isfinite(atr[index]) else 0.0) * 1.5, high_values[index] - low_values[index])
    if show_trade_signals and trade_bull[index] and index in visible_trades:
        buy_marks[index] = float(low_values[index] - gap)
    if show_trade_signals and trade_bear[index] and index in visible_trades:
        sell_marks[index] = float(high_values[index] + gap)

plots = [{
    "name": "TMA",
    "data": colored(tma, tma_colors),
    "color": neutral_color,
    "type": "line",
    "lineWidth": line_width,
    "overlay": True,
}]
if show_bands:
    plots.extend([
        {"name": "Upper Band", "data": finite(upper_band), "color": band_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower Band", "data": finite(lower_band), "color": band_color, "type": "line", "lineWidth": 1, "overlay": True},
    ])

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [
        {"type": "buy", "text": "T UP", "name": "TMA Bullish Turn", "data": bull_turn_marks, "color": bullish_color, "renderMode": "events"},
        {"type": "sell", "text": "T DOWN", "name": "TMA Bearish Turn", "data": bear_turn_marks, "color": bearish_color, "renderMode": "events"},
        {"type": "buy", "text": "BUY", "name": "Trade Long", "data": buy_marks, "color": bullish_color, "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Trade Short", "data": sell_marks, "color": bearish_color, "renderMode": "events"},
    ],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "source": finite(source),
        "base": finite(base),
        "tma": finite(tma),
        "deviation": finite(deviation),
        "upperBand": finite(upper_band),
        "lowerBand": finite(lower_band),
        "atr": finite(atr),
        "trendUp": [bool(value) for value in trend_up],
        "trendDown": [bool(value) for value in trend_down],
        "bullishTurn": [bool(value) for value in bullish_turn],
        "bearishTurn": [bool(value) for value in bearish_turn],
        "barsAbove": [int(value) for value in bars_above],
        "barsBelow": [int(value) for value in bars_below],
        "tradeBull": [bool(value) for value in trade_bull],
        "tradeBear": [bool(value) for value in trade_bear],
        "alertBullishTurn": [bool(value) for value in bullish_turn],
        "alertBearishTurn": [bool(value) for value in bearish_turn],
        "alertAnyTurn": [bool(bullish_turn[index] or bearish_turn[index]) for index in range(n)],
        "alertTradeBull": [bool(value) for value in trade_bull],
        "alertTradeBear": [bool(value) for value in trade_bear],
        "alertAnyTrade": [bool(trade_bull[index] or trade_bear[index]) for index in range(n)],
    },
}
