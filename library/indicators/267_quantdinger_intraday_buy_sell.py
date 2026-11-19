# ============================================================
# QuantDinger Intraday Buy Sell
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Intraday Buy Sell"
my_indicator_description = (
    "Combines Wilder RSI reversals with a long simple average and two-bar "
    "continuation patterns for intraday buy and sell markers."
)

# @param rsi_length int 14 RSI length range=1:200:1
# @param upper_rsi float 80.0 Upper RSI level range=0:100:1
# @param lower_rsi float 20.0 Lower RSI level range=0:100:1
# @param sma_length int 70 SMA length range=1:500:1
# @param show_trade_signals bool true Show buy and sell markers
# @param show_reversals bool true Show RSI reversal markers

rsi_length = min(200, max(1, int(params.get("rsi_length", 14))))
upper_rsi = min(100.0, max(0.0, float(params.get("upper_rsi", 80.0))))
lower_rsi = min(100.0, max(0.0, float(params.get("lower_rsi", 20.0))))
sma_length = min(500, max(1, int(params.get("sma_length", 70))))
show_trade_signals = bool(params.get("show_trade_signals", True))
show_reversals = bool(params.get("show_reversals", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

sma = pd.Series(close, dtype=float).rolling(sma_length, min_periods=sma_length).mean().to_numpy(dtype=float)
change = np.full(n, np.nan, dtype=float)
gain = np.full(n, np.nan, dtype=float)
loss = np.full(n, np.nan, dtype=float)
rsi = np.full(n, np.nan, dtype=float)
for index in range(1, n):
    change[index] = close[index] - close[index - 1]
    gain[index] = max(change[index], 0.0)
    loss[index] = max(-change[index], 0.0)

if rsi_length == 1:
    for index in range(1, n):
        rsi[index] = 100.0 if gain[index] > 0.0 else 0.0 if loss[index] > 0.0 else 50.0
elif n > rsi_length:
    average_gain = float(np.mean(gain[1:rsi_length + 1]))
    average_loss = float(np.mean(loss[1:rsi_length + 1]))
    for index in range(rsi_length, n):
        if index > rsi_length:
            average_gain = (average_gain * (rsi_length - 1) + gain[index]) / rsi_length
            average_loss = (average_loss * (rsi_length - 1) + loss[index]) / rsi_length
        if average_gain == 0.0 and average_loss == 0.0:
            rsi[index] = 50.0
        elif average_loss == 0.0:
            rsi[index] = 100.0
        elif average_gain == 0.0:
            rsi[index] = 0.0
        else:
            ratio = average_gain / average_loss
            rsi[index] = 100.0 - 100.0 / (1.0 + ratio)

upper_reversal = np.zeros(n, dtype=bool)
lower_reversal = np.zeros(n, dtype=bool)
buy = np.zeros(n, dtype=bool)
sell = np.zeros(n, dtype=bool)

for index in range(1, n):
    if np.isfinite(rsi[index]) and np.isfinite(rsi[index - 1]):
        upper_reversal[index] = rsi[index] < upper_rsi and rsi[index - 1] >= upper_rsi
        lower_reversal[index] = rsi[index] > lower_rsi and rsi[index - 1] <= lower_rsi and open_price[index] < close[index]
    if index >= 2 and np.isfinite(sma[index]) and np.isfinite(sma[index - 1]):
        delayed_close_cross_up = close[index - 1] > sma[index] and close[index - 2] <= sma[index - 1]
        delayed_low_cross_down = low[index - 1] < sma[index] and low[index - 2] >= sma[index - 1]
        buy[index] = (
            delayed_close_cross_up
            and close[index - 1] > open_price[index - 1]
            and high[index] > high[index - 1]
            and close[index] > open_price[index]
        )
        sell[index] = (
            delayed_low_cross_down
            and close[index - 1] < open_price[index - 1]
            and low[index] < low[index - 1]
            and close[index] < open_price[index]
        )

sma_plot = []
bar_colors = []
for index in range(n):
    if not np.isfinite(sma[index]):
        sma_plot.append(None)
    else:
        if np.isfinite(rsi[index]) and (rsi[index] >= upper_rsi or rsi[index] <= lower_rsi):
            color = "rgba(255,235,59,0.70)"
        elif low[index] > sma[index]:
            color = "rgba(0,230,118,0.70)"
        elif high[index] < sma[index]:
            color = "rgba(244,67,54,0.70)"
        else:
            color = "rgba(255,235,59,0.70)"
        sma_plot.append({"value": float(sma[index]), "color": color})
    if buy[index]:
        bar_colors.append("#00C853")
    elif sell[index]:
        bar_colors.append("rgba(128,0,0,0.70)")
    elif upper_reversal[index] or lower_reversal[index]:
        bar_colors.append("#FFEB3B")
    else:
        bar_colors.append(None)

signals = [
    {"type": "buy", "name": "Buy", "text": "B", "data": [float(low[i]) if show_trade_signals and buy[i] else None for i in range(n)], "color": "rgba(0,188,212,0.70)", "renderMode": "events" if show_trade_signals else "hidden"},
    {"type": "sell", "name": "Sell", "text": "S", "data": [float(high[i]) if show_trade_signals and sell[i] else None for i in range(n)], "color": "rgba(244,67,54,0.70)", "renderMode": "events" if show_trade_signals else "hidden"},
    {"type": "sell", "name": "Upper RSI Reversal", "text": "!", "data": [float(high[i]) if upper_reversal[i] else None for i in range(n)], "color": "rgba(255,235,59,0.80)", "renderMode": "events" if show_reversals else "hidden"},
    {"type": "buy", "name": "Lower RSI Reversal", "text": "!", "data": [float(low[i]) if lower_reversal[i] else None for i in range(n)], "color": "rgba(255,235,59,0.80)", "renderMode": "events" if show_reversals else "hidden"},
]


def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [{"name": "SMA", "data": sma_plot, "color": "#FFEB3B", "type": "line", "lineWidth": 1, "overlay": True}],
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": [None] * n,
    "calculatedVars": {
        "sma": _finite_or_none(sma), "rsi": _finite_or_none(rsi),
        "upperReversal": upper_reversal.tolist(), "lowerReversal": lower_reversal.tolist(),
        "buy": buy.tolist(), "sell": sell.tolist(),
    },
}
