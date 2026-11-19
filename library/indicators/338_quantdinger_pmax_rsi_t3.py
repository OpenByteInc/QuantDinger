# ============================================================
# QuantDinger PMax on RSI with Tillson T3
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger PMax on RSI with Tillson T3"
my_indicator_description = "Applies a Tillson T3 average and recursive PMax stops to a zero-seeded Wilder RSI with an RSI-range volatility model."

# @param source str hl2 Price-cross source values=open,high,low,close,hl2,hlc3,ohlc4
# @param atr_multiplier float 3.0 RSI ATR multiplier range=-20:20:0.1
# @param t3_length int 8 Tillson T3 length range=1:500:1
# @param t3_factor float 0.7 Tillson T3 volume factor range=-5:5:0.1
# @param atr_length int 10 RSI ATR length range=1:500:1
# @param rsi_length int 14 RSI length range=1:500:1
# @param show_rsi bool true Show RSI
# @param show_average bool true Show T3 average
# @param show_signals bool true Show crossing signals
# @param highlighting bool true Highlight trend regime

source_name = str(params.get("source", "hl2")).lower()
atr_multiplier = float(params.get("atr_multiplier", 3.0))
t3_length = max(1, int(params.get("t3_length", 8)))
t3_factor = float(params.get("t3_factor", 0.7))
atr_length = max(1, int(params.get("atr_length", 10)))
rsi_length = max(1, int(params.get("rsi_length", 14)))
show_rsi = bool(params.get("show_rsi", True))
show_average = bool(params.get("show_average", True))
show_signals = bool(params.get("show_signals", True))
highlighting = bool(params.get("highlighting", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _zero_seeded_wilder(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 1.0 / period
    previous = 0.0
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = alpha * value + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _ratio_rsi(up_values, down_values):
    result = np.full(len(up_values), np.nan, dtype=float)
    for index, (up_value, down_value) in enumerate(zip(up_values, down_values)):
        if not np.all(np.isfinite([up_value, down_value])):
            continue
        if down_value == 0.0:
            if up_value > 0.0:
                result[index] = 100.0
        else:
            ratio = up_value / down_value
            result[index] = 0.0 if ratio == -1.0 else 100.0 - 100.0 / (1.0 + ratio)
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)
sources = {
    "open": opened, "high": high, "low": low, "close": close,
    "hl2": (high + low) / 2.0, "hlc3": (high + low + close) / 3.0,
    "ohlc4": (opened + high + low + close) / 4.0,
}
price_source = sources.get(source_name, (high + low) / 2.0)

up_move = np.zeros(n, dtype=float)
down_move = np.zeros(n, dtype=float)
high_up = np.zeros(n, dtype=float)
high_down = np.zeros(n, dtype=float)
low_up = np.zeros(n, dtype=float)
low_down = np.zeros(n, dtype=float)
for index in range(1, n):
    up_move[index] = max(close[index] - close[index - 1], 0.0)
    down_move[index] = max(close[index - 1] - close[index], 0.0)
    high_up[index] = max(high[index] - close[index - 1], 0.0)
    high_down[index] = max(close[index - 1] - high[index], 0.0)
    low_up[index] = max(low[index] - close[index - 1], 0.0)
    low_down[index] = max(close[index - 1] - low[index], 0.0)

average_up_wilder = _zero_seeded_wilder(up_move, rsi_length)
average_down_wilder = _zero_seeded_wilder(down_move, rsi_length)
average_up_sma = _sma(up_move, rsi_length)
average_down_sma = _sma(down_move, rsi_length)
average_up_high = (average_up_sma * (rsi_length - 1) + high_up) / rsi_length
average_down_high = (average_down_sma * (rsi_length - 1) + high_down) / rsi_length
average_up_low = (average_up_sma * (rsi_length - 1) + low_up) / rsi_length
average_down_low = (average_down_sma * (rsi_length - 1) + low_down) / rsi_length
rsi = _ratio_rsi(average_up_wilder, average_down_wilder)
rsi_high = _ratio_rsi(average_up_high, average_down_high)
rsi_low = _ratio_rsi(average_up_low, average_down_low)

true_range = np.full(n, np.nan, dtype=float)
for index in range(1, n):
    if np.all(np.isfinite([rsi_high[index], rsi_low[index], rsi[index - 1]])):
        true_range[index] = max(
            rsi_high[index] - rsi_low[index],
            abs(rsi_high[index] - rsi[index - 1]),
            abs(rsi_low[index] - rsi[index - 1]),
        )
rsi_atr = _sma(true_range, atr_length)

t3_e1 = _ema(rsi, t3_length)
t3_e2 = _ema(t3_e1, t3_length)
t3_e3 = _ema(t3_e2, t3_length)
t3_e4 = _ema(t3_e3, t3_length)
t3_e5 = _ema(t3_e4, t3_length)
t3_e6 = _ema(t3_e5, t3_length)
c1 = -(t3_factor ** 3)
c2 = 3.0 * (t3_factor ** 2) + 3.0 * (t3_factor ** 3)
c3 = -6.0 * (t3_factor ** 2) - 3.0 * t3_factor - 3.0 * (t3_factor ** 3)
c4 = 1.0 + 3.0 * t3_factor + t3_factor ** 3 + 3.0 * (t3_factor ** 2)
t3 = c1 * t3_e6 + c2 * t3_e5 + c3 * t3_e4 + c4 * t3_e3

long_stop = np.full(n, np.nan, dtype=float)
short_stop = np.full(n, np.nan, dtype=float)
direction = np.ones(n, dtype=int)
pmax = np.full(n, np.nan, dtype=float)
buy_signal = np.zeros(n, dtype=bool)
sell_signal = np.zeros(n, dtype=bool)
price_buy = np.zeros(n, dtype=bool)
price_sell = np.zeros(n, dtype=bool)
for index in range(n):
    raw_long = t3[index] - atr_multiplier * rsi_atr[index] if np.all(np.isfinite([t3[index], rsi_atr[index]])) else np.nan
    raw_short = t3[index] + atr_multiplier * rsi_atr[index] if np.all(np.isfinite([t3[index], rsi_atr[index]])) else np.nan
    previous_long = long_stop[index - 1] if index > 0 and np.isfinite(long_stop[index - 1]) else raw_long
    previous_short = short_stop[index - 1] if index > 0 and np.isfinite(short_stop[index - 1]) else raw_short
    long_stop[index] = max(raw_long, previous_long) if np.all(np.isfinite([raw_long, previous_long])) and t3[index] > previous_long else raw_long
    short_stop[index] = min(raw_short, previous_short) if np.all(np.isfinite([raw_short, previous_short])) and t3[index] < previous_short else raw_short
    previous_direction = direction[index - 1] if index > 0 else 1
    if previous_direction == -1 and np.isfinite(previous_short) and t3[index] > previous_short:
        direction[index] = 1
    elif previous_direction == 1 and np.isfinite(previous_long) and t3[index] < previous_long:
        direction[index] = -1
    else:
        direction[index] = previous_direction
    pmax[index] = long_stop[index] if direction[index] == 1 else short_stop[index]
    if index > 0 and np.all(np.isfinite([t3[index], pmax[index], t3[index - 1], pmax[index - 1]])):
        buy_signal[index] = t3[index] > pmax[index] and t3[index - 1] <= pmax[index - 1]
        sell_signal[index] = t3[index] < pmax[index] and t3[index - 1] >= pmax[index - 1]
    if index > 0 and np.all(np.isfinite([price_source[index], pmax[index], price_source[index - 1], pmax[index - 1]])):
        price_buy[index] = price_source[index] > pmax[index] and price_source[index - 1] <= pmax[index - 1]
        price_sell[index] = price_source[index] < pmax[index] and price_source[index - 1] >= pmax[index - 1]

highlight_data = []
for index in range(n):
    if highlighting and np.all(np.isfinite([rsi[index], pmax[index]])) and t3[index] != pmax[index]:
        highlight_data.append({
            "upper": float(max(rsi[index], pmax[index])), "lower": float(min(rsi[index], pmax[index])),
            "color": "rgba(0,128,0,0.20)" if t3[index] > pmax[index] else "rgba(255,0,0,0.20)",
        })
    else:
        highlight_data.append(None)

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "RSI", "data": _finite(rsi) if show_rsi else [None] * n, "color": "#8E1599", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Upper Band", "data": [70.0] * n, "color": "#C0C0C0", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Lower Band", "data": [30.0] * n, "color": "#C0C0C0", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "RSI Band", "title": False, "data": [{"upper": 70.0, "lower": 30.0, "color": "rgba(153,21,255,0.10)"} for _ in range(n)], "color": "rgba(153,21,255,0.10)", "type": "cloud", "overlay": False},
        {"name": "Tillson T3", "data": _finite(t3) if show_average else [None] * n, "color": "#000000", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "PMax", "data": _finite(pmax), "color": "#FF0000", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Trend Highlight", "title": False, "data": highlight_data, "color": "#008000", "type": "cloud", "overlay": False},
    ],
    "signals": [
        {"type": "buy", "name": "T3 Crossover", "data": [{"active": True, "price": float(pmax[index] * 0.995), "text": "Buy", "color": "#008000"} if show_signals and buy_signal[index] else None for index in range(n)], "color": "#008000", "renderMode": "events", "overlay": False},
        {"type": "sell", "name": "T3 Crossunder", "data": [{"active": True, "price": float(pmax[index] * 1.005), "text": "Sell", "color": "#FF0000"} if show_signals and sell_signal[index] else None for index in range(n)], "color": "#FF0000", "renderMode": "events", "overlay": False},
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "upMove": _finite(up_move), "downMove": _finite(down_move),
        "averageUpWilder": _finite(average_up_wilder), "averageDownWilder": _finite(average_down_wilder),
        "averageUpSma": _finite(average_up_sma), "averageDownSma": _finite(average_down_sma),
        "rsi": _finite(rsi), "rsiHigh": _finite(rsi_high), "rsiLow": _finite(rsi_low),
        "trueRange": _finite(true_range), "rsiAtr": _finite(rsi_atr), "t3": _finite(t3),
        "longStop": _finite(long_stop), "shortStop": _finite(short_stop), "direction": direction.tolist(), "pmax": _finite(pmax),
        "buySignal": buy_signal.tolist(), "sellSignal": sell_signal.tolist(),
        "priceBuy": price_buy.tolist(), "priceSell": price_sell.tolist(),
    },
}
