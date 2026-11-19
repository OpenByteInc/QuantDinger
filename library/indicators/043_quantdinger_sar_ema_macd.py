# ============================================================
# QuantDinger SAR EMA MACD
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger SAR EMA MACD"
my_indicator_description = (
    "Parabolic SAR and a long EMA trend filter combined with MACD histogram "
    "zero-cross events for selective overlay signals."
)

# @param sar_start float 0.02 SAR start factor range=0.001:1.0:0.001
# @param sar_increment float 0.02 SAR increment range=0.001:1.0:0.001
# @param sar_maximum float 0.2 SAR maximum range=0.01:2.0:0.01
# @param ema_length int 200 Trend EMA length range=1:500:1
# @param macd_fast int 12 MACD fast length range=1:200:1
# @param macd_slow int 26 MACD slow length range=2:400:1
# @param signal_length int 9 Signal length range=1:100:1
# @param oscillator_ma int 1 Oscillator MA type range=0:1:1
# @param signal_ma int 1 Signal MA type range=0:1:1

sar_start = max(0.001, float(params.get("sar_start", 0.02)))
sar_increment = max(0.001, float(params.get("sar_increment", 0.02)))
sar_maximum = max(sar_start, float(params.get("sar_maximum", 0.2)))
ema_length = max(1, int(params.get("ema_length", 200)))
macd_fast = max(1, int(params.get("macd_fast", 12)))
macd_slow = max(2, int(params.get("macd_slow", 26)))
signal_length = max(1, int(params.get("signal_length", 9)))
oscillator_ma = max(0, min(1, int(params.get("oscillator_ma", 1))))
signal_ma = max(0, min(1, int(params.get("signal_ma", 1))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def sma(values, period):
    return pd.Series(np.asarray(values, dtype=float)).rolling(period, min_periods=period).mean().to_numpy(dtype=float)


def ema(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    seed = []
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) == period:
                previous = float(np.mean(seed))
                result[index] = previous
        else:
            previous = alpha * value + (1.0 - alpha) * previous
            result[index] = previous
    return result


sar = np.full(n, np.nan, dtype=float)
direction = np.zeros(n, dtype=int)
if n == 1:
    sar[0] = low[0]
    direction[0] = 1
elif n > 1:
    bullish = close[1] > close[0]
    extreme = high[1] if bullish else low[1]
    current_sar = low[0] if bullish else high[0]
    acceleration = sar_start
    sar[0] = current_sar
    direction[0] = 1 if bullish else -1
    for index in range(1, n):
        candidate = current_sar + acceleration * (extreme - current_sar)
        if bullish:
            if candidate > low[index]:
                bullish = False
                candidate = extreme
                extreme = low[index]
                acceleration = sar_start
            else:
                if high[index] > extreme:
                    extreme = high[index]
                    acceleration = min(sar_maximum, acceleration + sar_increment)
                candidate = min(candidate, low[index - 1])
                if index > 1:
                    candidate = min(candidate, low[index - 2])
        else:
            if candidate < high[index]:
                bullish = True
                candidate = extreme
                extreme = high[index]
                acceleration = sar_start
            else:
                if low[index] < extreme:
                    extreme = low[index]
                    acceleration = min(sar_maximum, acceleration + sar_increment)
                candidate = max(candidate, high[index - 1])
                if index > 1:
                    candidate = max(candidate, high[index - 2])
        current_sar = candidate
        sar[index] = current_sar
        direction[index] = 1 if current_sar < close[index] else -1

trend_ema = ema(close, ema_length)
fast = sma(close, macd_fast) if oscillator_ma == 0 else ema(close, macd_fast)
slow = sma(close, macd_slow) if oscillator_ma == 0 else ema(close, macd_slow)
macd = fast - slow
signal = sma(macd, signal_length) if signal_ma == 0 else ema(macd, signal_length)
delta = macd - signal
cross_up = np.concatenate(([False], (delta[1:] > 0.0) & (delta[:-1] <= 0.0)))
cross_down = np.concatenate(([False], (delta[1:] < 0.0) & (delta[:-1] >= 0.0)))
ohlc4 = (open_price + high + low + close) / 4.0
buy_event = (sar < low) & cross_up & np.isfinite(trend_ema) & (ohlc4 >= trend_ema)
sell_event = (sar > high) & cross_down & np.isfinite(trend_ema) & (ohlc4 < trend_ema)

sar_plot = []
trend_cloud = []
for index in range(n):
    color = "#2962FF" if direction[index] == 1 else "#FFB300"
    sar_plot.append({"value": float(sar[index]), "color": color} if np.isfinite(sar[index]) else None)
    if np.isfinite(trend_ema[index]):
        trend_cloud.append({"upper": float(max(close[index], trend_ema[index])), "lower": float(min(close[index], trend_ema[index])), "color": "#00C853" if close[index] >= trend_ema[index] else "#FF1744"})
    else:
        trend_cloud.append(None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Parabolic SAR", "data": sar_plot, "color": "#2962FF", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Trend EMA", "data": [float(value) if np.isfinite(value) else None for value in trend_ema], "color": "#2962FF", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "EMA Trend State", "title": False, "data": trend_cloud, "color": "#00C853", "type": "cloud", "opacity": 0.10, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "SAR EMA MACD Buy", "data": [float(low[i]) if buy_event[i] else None for i in range(n)], "color": "#00C853", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "SAR EMA MACD Sell", "data": [float(high[i]) if sell_event[i] else None for i in range(n)], "color": "#FF1744", "renderMode": "events"},
    ],
    "layers": [],
}
