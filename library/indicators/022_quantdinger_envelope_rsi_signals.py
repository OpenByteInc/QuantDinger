# ============================================================
# QuantDinger Envelope RSI Signals
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Envelope RSI Signals"
my_indicator_description = (
    "A compact price envelope whose crossings are filtered by RSI extremes, "
    "with optional take-profit and stop-loss guide points."
)

# @param envelope_period int 8 Envelope period range=1:300:1
# @param envelope_percent float 0.22 Envelope width percent range=0.01:20.0:0.01
# @param exponential bool false Use exponential basis
# @param rsi_period int 8 RSI period range=1:300:1
# @param rsi_overbought float 80.0 RSI overbought range=50.0:100.0:1.0
# @param rsi_oversold float 25.0 RSI oversold range=0.0:50.0:1.0
# @param take_profit_percent float 0.15 Take-profit percent range=0.0:20.0:0.05
# @param stop_loss_percent float 0.15 Stop-loss percent range=0.0:20.0:0.05

envelope_period = max(1, int(params.get("envelope_period", 8)))
envelope_percent = max(0.0, float(params.get("envelope_percent", 0.22)))
exponential = bool(params.get("exponential", False))
rsi_period = max(1, int(params.get("rsi_period", 8)))
rsi_overbought = float(params.get("rsi_overbought", 80.0))
rsi_oversold = float(params.get("rsi_oversold", 25.0))
take_profit_percent = max(0.0, float(params.get("take_profit_percent", 0.15)))
stop_loss_percent = max(0.0, float(params.get("stop_loss_percent", 0.15)))

df = df.copy()
open_price = df["open"].astype(float)
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
hl2 = (high + low) / 2.0
ohlc4 = (open_price + high + low + close) / 4.0
n = len(df)

if exponential:
    basis = hl2.ewm(span=envelope_period, adjust=False, min_periods=envelope_period).mean()
else:
    basis = hl2.rolling(window=envelope_period, min_periods=envelope_period).mean()
width = envelope_percent / 100.0
upper = basis * (1.0 + width)
lower = basis * (1.0 - width)

change = hl2.diff()
gain = change.clip(lower=0.0)
loss = -change.clip(upper=0.0)
average_gain = gain.ewm(alpha=1.0 / rsi_period, adjust=False, min_periods=rsi_period).mean()
average_loss = loss.ewm(alpha=1.0 / rsi_period, adjust=False, min_periods=rsi_period).mean()
rsi = 100.0 - 100.0 / (1.0 + average_gain / average_loss.replace(0.0, np.nan))
rsi = rsi.where(average_loss != 0.0, 100.0).where(average_gain != 0.0, 0.0)

cross_buy = (close > lower) & (close.shift(1) <= lower.shift(1))
cross_sell = (close < upper) & (close.shift(1) >= upper.shift(1))

def crossed(series, boundary):
    delta = series - boundary
    return ((delta > 0.0) & (delta.shift(1) <= 0.0)) | ((delta < 0.0) & (delta.shift(1) >= 0.0))

touch_lower = crossed(low, lower) | crossed(close, lower) | crossed(high, lower) | crossed(open_price, lower)
touch_upper = crossed(low, upper) | crossed(close, upper) | crossed(high, upper) | crossed(open_price, upper)
buy_condition = (rsi < rsi_oversold) & touch_lower
sell_condition = (rsi > rsi_overbought) & touch_upper
buy_signal = cross_buy & buy_condition
sell_signal = cross_sell & sell_condition

buy_tp = ohlc4 * (1.0 + take_profit_percent / 100.0)
buy_sl = ohlc4 * (1.0 - stop_loss_percent / 100.0)
sell_tp = ohlc4 * (1.0 - take_profit_percent / 100.0)
sell_sl = ohlc4 * (1.0 + stop_loss_percent / 100.0)

def finite_list(series):
    return [float(value) if np.isfinite(value) else None for value in series.to_numpy(dtype=float)]

basis_plot = finite_list(basis)
upper_plot = finite_list(upper)
lower_plot = finite_list(lower)
band_cloud = [
    {"upper": upper_plot[index], "lower": lower_plot[index]}
    if upper_plot[index] is not None and lower_plot[index] is not None
    else None
    for index in range(n)
]
tp_plot = []
sl_plot = []
for index in range(n):
    if bool(buy_signal.iloc[index]):
        tp_plot.append(float(buy_tp.iloc[index]))
        sl_plot.append(float(buy_sl.iloc[index]))
    elif bool(sell_signal.iloc[index]):
        tp_plot.append(float(sell_tp.iloc[index]))
        sl_plot.append(float(sell_sl.iloc[index]))
    else:
        tp_plot.append(None)
        sl_plot.append(None)

buy_marks = [float(low.iloc[index]) if bool(buy_signal.iloc[index]) else None for index in range(n)]
sell_marks = [float(high.iloc[index]) if bool(sell_signal.iloc[index]) else None for index in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Envelope Basis", "data": basis_plot, "color": "#ED7300", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Envelope Upper", "data": upper_plot, "color": "#FF2424", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Envelope Lower", "data": lower_plot, "color": "#24FF24", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Envelope Cloud", "title": False, "data": band_cloud, "color": "#2196F3", "type": "cloud", "opacity": 0.12, "overlay": True},
        {"name": "Take Profit", "data": tp_plot, "color": "#1DBC60", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Stop Loss", "data": sl_plot, "color": "#F74A58", "type": "line", "lineWidth": 2, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "Envelope RSI Buy", "data": buy_marks, "color": "#008000", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Envelope RSI Sell", "data": sell_marks, "color": "#FF0000", "renderMode": "events"},
    ],
    "layers": [],
}
