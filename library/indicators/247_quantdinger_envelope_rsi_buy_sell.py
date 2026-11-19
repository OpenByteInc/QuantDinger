# ============================================================
# QuantDinger Envelope RSI Buy / Sell
# Independent Python implementation for the QuantDinger runtime.
# ============================================================

my_indicator_name = "QuantDinger Envelope RSI Buy / Sell"
my_indicator_description = (
    "Filters price-envelope re-entry events with Wilder RSI extremes and "
    "tracks the source script's take-profit and stop-loss guide calculations."
)

# @param envelope_length int 8 Envelope length range=1:500:1
# @param envelope_percent float 0.22 Envelope width percent range=0.0001:20.0:0.01
# @param envelope_source str hl2 Envelope source values=open,high,low,close,hl2,hlc3,ohlc4
# @param exponential bool false Use EMA envelope basis
# @param rsi_length int 8 RSI length range=1:500:1
# @param rsi_source str hl2 RSI source values=open,high,low,close,hl2,hlc3,ohlc4
# @param rsi_overbought float 80.0 RSI overbought range=50.0:100.0:1.0
# @param rsi_oversold float 25.0 RSI oversold range=0.0:50.0:1.0
# @param take_profit_percent float 0.15 Take-profit percent range=0.0:20.0:0.05
# @param stop_loss_percent float 0.15 Stop-loss percent range=0.0:20.0:0.05

envelope_length = min(500, max(1, int(params.get("envelope_length", 8))))
envelope_percent = min(20.0, max(0.0001, float(params.get("envelope_percent", 0.22))))
envelope_source_name = str(params.get("envelope_source", "hl2")).lower()
exponential = bool(params.get("exponential", False))
rsi_length = min(500, max(1, int(params.get("rsi_length", 8))))
rsi_source_name = str(params.get("rsi_source", "hl2")).lower()
rsi_overbought = min(100.0, max(50.0, float(params.get("rsi_overbought", 80.0))))
rsi_oversold = min(50.0, max(0.0, float(params.get("rsi_oversold", 25.0))))
take_profit_percent = min(20.0, max(0.0, float(params.get("take_profit_percent", 0.15))))
stop_loss_percent = min(20.0, max(0.0, float(params.get("stop_loss_percent", 0.15))))

df = df.copy()
open_values = pd.to_numeric(df["open"], errors="coerce").to_numpy(dtype=float)
high_values = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
n = len(df)
hl2 = (high_values + low_values) * 0.5
hlc3 = (high_values + low_values + close_values) / 3.0
ohlc4 = (open_values + high_values + low_values + close_values) * 0.25
sources = {
    "open": open_values, "high": high_values, "low": low_values, "close": close_values,
    "hl2": hl2, "hlc3": hlc3, "ohlc4": ohlc4,
}
envelope_source = np.asarray(sources.get(envelope_source_name, hl2), dtype=float)
rsi_source = np.asarray(sources.get(rsi_source_name, hl2), dtype=float)


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def ema(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if not len(source):
        return result
    alpha = 2.0 / (float(length) + 1.0)
    state = float(source[0])
    result[0] = state
    for index in range(1, len(source)):
        state = alpha * float(source[index]) + (1.0 - alpha) * state
        result[index] = state
    return result


def sma(values, length):
    return pd.Series(values, dtype=float).rolling(length, min_periods=length).mean().to_numpy(dtype=float)


def wilder(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if length == 1:
        return source.copy()
    if len(source) >= length:
        state = float(np.mean(source[:length]))
        result[length - 1] = state
        for index in range(length, len(source)):
            state = (state * (length - 1) + float(source[index])) / float(length)
            result[index] = state
    return result


def cross(first, second):
    result = np.zeros(n, dtype=bool)
    if n > 1:
        valid = np.isfinite(first[1:]) & np.isfinite(first[:-1]) & np.isfinite(second[1:]) & np.isfinite(second[:-1])
        result[1:] = valid & (
            ((first[1:] > second[1:]) & (first[:-1] <= second[:-1]))
            | ((first[1:] < second[1:]) & (first[:-1] >= second[:-1]))
        )
    return result


def crossover(first, second):
    result = np.zeros(n, dtype=bool)
    if n > 1:
        result[1:] = (
            np.isfinite(first[1:]) & np.isfinite(first[:-1]) & np.isfinite(second[1:]) & np.isfinite(second[:-1])
            & (first[1:] > second[1:]) & (first[:-1] <= second[:-1])
        )
    return result


def crossunder(first, second):
    result = np.zeros(n, dtype=bool)
    if n > 1:
        result[1:] = (
            np.isfinite(first[1:]) & np.isfinite(first[:-1]) & np.isfinite(second[1:]) & np.isfinite(second[:-1])
            & (first[1:] < second[1:]) & (first[:-1] >= second[:-1])
        )
    return result


basis = ema(envelope_source, envelope_length) if exponential else sma(envelope_source, envelope_length)
width = envelope_percent / 100.0
upper = basis * (1.0 + width)
lower = basis * (1.0 - width)

changes = np.r_[np.nan, np.diff(rsi_source)]
gains = np.where(np.isfinite(changes), np.maximum(changes, 0.0), np.nan)
losses = np.where(np.isfinite(changes), np.maximum(-changes, 0.0), np.nan)
# Pine ta.rma ignores the leading na from ta.change and seeds from the first full valid window.
valid_gains = gains[1:]
valid_losses = losses[1:]
average_gain = np.full(n, np.nan, dtype=float)
average_loss = np.full(n, np.nan, dtype=float)
if len(valid_gains) >= rsi_length:
    gain_rma = wilder(valid_gains, rsi_length)
    loss_rma = wilder(valid_losses, rsi_length)
    average_gain[1:] = gain_rma
    average_loss[1:] = loss_rma
rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    up = average_gain[index]
    down = average_loss[index]
    if not np.isfinite(up) or not np.isfinite(down):
        continue
    rsi[index] = 100.0 if down == 0.0 else 0.0 if up == 0.0 else 100.0 - 100.0 / (1.0 + up / down)

cross_buy = crossover(close_values, lower)
cross_sell = crossunder(close_values, upper)
touch_lower = cross(low_values, lower) | cross(close_values, lower) | cross(high_values, lower) | cross(open_values, lower)
touch_upper = cross(low_values, upper) | cross(close_values, upper) | cross(high_values, upper) | cross(open_values, upper)
buy_condition = np.isfinite(rsi) & (rsi < rsi_oversold) & touch_lower
sell_condition = np.isfinite(rsi) & (rsi > rsi_overbought) & touch_upper
buy_signal = cross_buy & buy_condition
sell_signal = cross_sell & sell_condition

take_profit = np.full(n, np.nan, dtype=float)
stop_loss = np.full(n, np.nan, dtype=float)
take_profit[sell_condition] = ohlc4[sell_condition] * (1.0 - take_profit_percent / 100.0)
take_profit[buy_condition & ~sell_condition] = ohlc4[buy_condition & ~sell_condition] * (1.0 + take_profit_percent / 100.0)
stop_loss[sell_condition] = ohlc4[sell_condition] * (1.0 + stop_loss_percent / 100.0)
stop_loss[buy_condition & ~sell_condition] = ohlc4[buy_condition & ~sell_condition] * (1.0 - stop_loss_percent / 100.0)

recent_cross = np.zeros(n, dtype=bool)
for index in range(n):
    recent_cross[index] = (
        (index >= 1 and (cross_sell[index - 1] or cross_buy[index - 1]))
        or (index >= 2 and (cross_sell[index - 2] or cross_buy[index - 2]))
    )
profit_cloud = [None for _ in range(n)]
loss_cloud = [None for _ in range(n)]
for index in range(n):
    if not recent_cross[index] or not np.isfinite(take_profit[index]) or not np.isfinite(stop_loss[index]):
        continue
    anchor = float(high_values[index] if sell_condition[index] else ohlc4[index])
    profit_cloud[index] = {
        "upper": float(max(anchor, take_profit[index])), "lower": float(min(anchor, take_profit[index])),
        "color": "rgba(29,188,96,0.30)",
    }
    loss_cloud[index] = {
        "upper": float(max(anchor, stop_loss[index])), "lower": float(min(anchor, stop_loss[index])),
        "color": "rgba(247,74,88,0.30)",
    }

band_cloud = [
    {"upper": float(upper[index]), "lower": float(lower[index]), "color": "rgba(33,150,243,0.05)"}
    if np.isfinite(upper[index]) and np.isfinite(lower[index]) else None for index in range(n)
]
plots = [
    {"name": "Envelope Basis", "data": finite(basis), "color": "#ED7300", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Envelope Upper", "data": finite(upper), "color": "#FF2424", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Envelope Lower", "data": finite(lower), "color": "#24FF24", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Envelope Cloud", "title": False, "data": band_cloud, "type": "cloud", "overlay": True},
    {"name": "Take Profit Guide", "title": False, "data": profit_cloud, "type": "cloud", "overlay": True},
    {"name": "Stop Loss Guide", "title": False, "data": loss_cloud, "type": "cloud", "overlay": True},
]
signals = [
    {"type": "buy", "text": "BUY", "name": "Envelope RSI Buy", "data": [float(low_values[index]) if buy_signal[index] else None for index in range(n)], "color": "#008000", "renderMode": "events"},
    {"type": "sell", "text": "SELL", "name": "Envelope RSI Sell", "data": [float(high_values[index]) if sell_signal[index] else None for index in range(n)], "color": "#FF0000", "renderMode": "events"},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "envelopeSource": finite(envelope_source), "rsiSource": finite(rsi_source),
        "basis": finite(basis), "upper": finite(upper), "lower": finite(lower),
        "change": finite(changes), "averageGain": finite(average_gain), "averageLoss": finite(average_loss),
        "rsi": finite(rsi), "crossBuy": cross_buy.tolist(), "crossSell": cross_sell.tolist(),
        "touchLower": touch_lower.tolist(), "touchUpper": touch_upper.tolist(),
        "buyCondition": buy_condition.tolist(), "sellCondition": sell_condition.tolist(),
        "buySignal": buy_signal.tolist(), "sellSignal": sell_signal.tolist(),
        "takeProfit": finite(take_profit), "stopLoss": finite(stop_loss), "recentCross": recent_cross.tolist(),
    },
}
