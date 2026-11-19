# ============================================================
# QuantDinger Candlestick Reversal
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Candlestick Reversal"
my_indicator_description = (
    "A four-part reversal detector combining wick, extreme-range, outside-bar, "
    "and doji-break patterns at fresh swing extremes."
)

# @param pivot_length int 5 Swing lookback range=1:100:1
# @param show_sma bool false Show simple moving average
# @param sma_length int 10 Average length range=1:300:1
# @param wick_enabled bool true Enable wick reversals
# @param wick_multiplier float 2.5 Wick multiplier range=0.1:20.0:0.1
# @param wick_body_percent float 0.25 Wick body percentage range=0.0:1.0:0.05
# @param extreme_enabled bool true Enable extreme reversals
# @param extreme_body_size float 0.525 Extreme body size range=0.0:1.0:0.025
# @param extreme_lookback int 50 Extreme lookback range=2:250:1
# @param extreme_multiplier float 2.0 Extreme multiplier range=0.1:5.0:0.1
# @param outside_enabled bool true Enable outside reversals
# @param outside_lookback int 50 Outside lookback range=2:250:1
# @param outside_multiplier float 1.25 Outside multiplier range=0.1:3.5:0.05
# @param doji_enabled bool true Enable doji reversals
# @param doji_body_percent float 0.10 Doji body percentage range=0.0:1.0:0.05

pivot_length = max(1, int(params.get("pivot_length", 5)))
show_sma = bool(params.get("show_sma", False))
sma_length = max(1, int(params.get("sma_length", 10)))
wick_enabled = bool(params.get("wick_enabled", True))
wick_multiplier = max(0.1, float(params.get("wick_multiplier", 2.5)))
wick_body_percent = max(0.0, min(1.0, float(params.get("wick_body_percent", 0.25))))
extreme_enabled = bool(params.get("extreme_enabled", True))
extreme_body_size = max(0.0, min(1.0, float(params.get("extreme_body_size", 0.525))))
extreme_lookback = max(2, int(params.get("extreme_lookback", 50)))
extreme_multiplier = max(0.1, float(params.get("extreme_multiplier", 2.0)))
outside_enabled = bool(params.get("outside_enabled", True))
outside_lookback = max(2, int(params.get("outside_lookback", 50)))
outside_multiplier = max(0.1, float(params.get("outside_multiplier", 1.25)))
doji_enabled = bool(params.get("doji_enabled", True))
doji_body_percent = max(0.0, min(1.0, float(params.get("doji_body_percent", 0.10))))

df = df.copy()
open_price = df["open"].astype(float)
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
n = len(df)

body = (close - open_price).abs()
candle_range = high - low
average_body = body.rolling(window=extreme_lookback, min_periods=extreme_lookback).mean()
average_candle = candle_range.rolling(window=extreme_lookback, min_periods=extreme_lookback).mean()
outside_average = candle_range.rolling(window=outside_lookback, min_periods=outside_lookback).mean()
range_average_50 = candle_range.rolling(window=50, min_periods=50).mean()
sma = close.rolling(window=sma_length, min_periods=sma_length).mean()

fresh_high = high >= high.rolling(window=pivot_length + 1, min_periods=pivot_length + 1).max()
fresh_low = low <= low.rolling(window=pivot_length + 1, min_periods=pivot_length + 1).min()

wick_long = (
    ((close > open_price) & ((open_price - low) >= (close - open_price) * wick_multiplier) & ((high - close) <= candle_range * wick_body_percent))
    | ((close < open_price) & ((close - low) >= (open_price - close) * wick_multiplier) & ((high - close) <= candle_range * wick_body_percent))
    | ((close == open_price) & (close != high) & (candle_range >= (high - close) * wick_multiplier) & ((high - close) <= candle_range * wick_body_percent))
    | ((open_price == high) & (close == high) & (candle_range >= range_average_50))
)

previous_bear_body = open_price.shift(1) - close.shift(1)
previous_bull_body = close.shift(1) - open_price.shift(1)
previous_range = high.shift(1) - low.shift(1)
extreme_long = (
    (previous_bear_body >= extreme_body_size * previous_range)
    & (previous_range > average_candle * extreme_multiplier)
    & (previous_bear_body > average_body)
    & (close > open_price)
)
extreme_short = (
    (previous_bull_body >= extreme_body_size * previous_range)
    & (previous_range > average_candle * extreme_multiplier)
    & (previous_bull_body > average_body)
    & (open_price > close)
)

outside_long = (low < low.shift(1)) & (close > high.shift(1)) & (candle_range >= outside_average * outside_multiplier)
outside_short = (high > high.shift(1)) & (close < low.shift(1)) & (candle_range >= outside_average * outside_multiplier)

previous_body = (close.shift(1) - open_price.shift(1)).abs()
previous_range = high.shift(1) - low.shift(1)
sma_10 = close.rolling(window=10, min_periods=10).mean()
doji_long = (
    ((previous_body <= previous_range * doji_body_percent) & (close > high.shift(1)) & (high.shift(1) < sma_10) & (close > open_price))
    | ((close > high.shift(2)) & (close.shift(1) <= high.shift(2)) & (previous_body <= previous_body * doji_body_percent) & (close > open_price) & (high.shift(2) < sma_10))
)
doji_short = (
    ((previous_body <= previous_range * doji_body_percent) & (close < low.shift(1)) & (low.shift(1) > sma_10) & (close < open_price))
    | ((close < low.shift(2)) & (close.shift(1) >= low.shift(2)) & (previous_body <= previous_body * doji_body_percent) & (close < open_price) & (low.shift(2) > sma_10))
)

long_event = fresh_low & (
    (wick_enabled & wick_long)
    | (extreme_enabled & extreme_long)
    | (outside_enabled & outside_long)
    | (doji_enabled & doji_long)
)
# The source's short wick branch intentionally reuses the long-wick test.
short_event = fresh_high & (
    (wick_enabled & wick_long)
    | (extreme_enabled & extreme_short)
    | (outside_enabled & outside_short)
    | (doji_enabled & doji_short)
)

sma_plot = [float(value) if show_sma and np.isfinite(value) else None for value in sma.to_numpy(dtype=float)]
anchor_plot = [float(value) for value in close.to_numpy(dtype=float)]
long_marks = [float(low.iloc[index] * 0.995) if bool(long_event.iloc[index]) else None for index in range(n)]
short_marks = [float(high.iloc[index] * 1.005) if bool(short_event.iloc[index]) else None for index in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Signal Anchor", "title": False, "data": anchor_plot, "color": "rgba(0, 0, 0, 0)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Optional Average", "data": sma_plot, "color": "#0000FF", "type": "line", "lineWidth": 2, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "REV", "name": "Bullish Reversal", "data": long_marks, "color": "#0000FF", "renderMode": "events"},
        {"type": "sell", "text": "REV", "name": "Bearish Reversal", "data": short_marks, "color": "#FF0000", "renderMode": "events"},
    ],
    "layers": [],
}
