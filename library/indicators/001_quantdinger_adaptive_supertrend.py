# ============================================================
# QuantDinger Adaptive SuperTrend
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Adaptive SuperTrend"
my_indicator_description = (
    "QuantDinger implementation of an adaptive ATR trend overlay with "
    "selectable Wilder/SMA volatility modes, recursive trailing bands, "
    "trend-flip markers, and directional highlight clouds."
)

# @param atr_period int 10 ATR period range=1:200:1
# @param source str hl2 Source values=hl2,close,open,high,low,ohlc4
# @param multiplier float 3.0 ATR multiplier range=0.1:20.0:0.1
# @param change_atr bool true Use Wilder RMA instead of SMA
# @param show_signals bool true Show Buy/Sell labels
# @param highlighting bool true Enable trend highlight clouds

atr_period = max(1, int(params.get("atr_period", 10)))
source_name = str(params.get("source", "hl2")).strip().lower()
multiplier = float(params.get("multiplier", 3.0))
change_atr = bool(params.get("change_atr", True))
show_signals = bool(params.get("show_signals", True))
highlighting = bool(params.get("highlighting", True))

df = df.copy()
open_price = df["open"].astype(float)
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
hl2 = (high + low) / 2.0
ohlc4 = (open_price + high + low + close) / 4.0

sources = {
    "hl2": hl2,
    "close": close,
    "open": open_price,
    "high": high,
    "low": low,
    "ohlc4": ohlc4,
}
source = sources.get(source_name, hl2)
previous_close = close.shift(1)

# ta.tr(true), used by ta.atr(), falls back to high-low on the first bar.
true_range_rma = pd.concat(
    [
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs(),
    ],
    axis=1,
).max(axis=1, skipna=True)

# The ta.tr variable used by the alternative SMA path is na on the first bar.
true_range_sma = true_range_rma.copy()
if len(true_range_sma):
    true_range_sma.iloc[0] = np.nan

n = len(df)
atr_rma = np.full(n, np.nan, dtype=float)
tr_rma_values = true_range_rma.to_numpy(dtype=float)
if n >= atr_period:
    seed = float(np.sum(tr_rma_values[:atr_period]) / atr_period)
    atr_rma[atr_period - 1] = seed
    for index in range(atr_period, n):
        value = tr_rma_values[index]
        if np.isnan(value):
            continue
        atr_rma[index] = (
            atr_rma[index - 1] * (atr_period - 1) + value
        ) / atr_period

atr_sma = (
    true_range_sma.rolling(window=atr_period, min_periods=atr_period)
    .sum()
    .to_numpy(dtype=float)
    / atr_period
)
atr = atr_rma if change_atr else atr_sma

raw_up = source.to_numpy(dtype=float) - multiplier * atr
raw_down = source.to_numpy(dtype=float) + multiplier * atr
close_values = close.to_numpy(dtype=float)

up = np.full(n, np.nan, dtype=float)
down = np.full(n, np.nan, dtype=float)
trend = np.ones(n, dtype=np.int8)

for index in range(n):
    up_value = raw_up[index]
    down_value = raw_down[index]

    if index == 0:
        up_previous = up_value
        down_previous = down_value
        previous_trend = 1
        previous_price = np.nan
    else:
        up_previous = up[index - 1]
        down_previous = down[index - 1]
        if np.isnan(up_previous):
            up_previous = up_value
        if np.isnan(down_previous):
            down_previous = down_value
        previous_trend = int(trend[index - 1])
        previous_price = close_values[index - 1]

    if not np.isnan(up_value) and not np.isnan(up_previous):
        up[index] = (
            max(up_value, up_previous)
            if previous_price > up_previous
            else up_value
        )
    if not np.isnan(down_value) and not np.isnan(down_previous):
        down[index] = (
            min(down_value, down_previous)
            if previous_price < down_previous
            else down_value
        )

    next_trend = previous_trend
    if (
        previous_trend == -1
        and not np.isnan(down_previous)
        and close_values[index] > down_previous
    ):
        next_trend = 1
    elif (
        previous_trend == 1
        and not np.isnan(up_previous)
        and close_values[index] < up_previous
    ):
        next_trend = -1
    trend[index] = next_trend

previous_trend = np.concatenate(([0], trend[:-1]))
buy_signal = (trend == 1) & (previous_trend == -1)
sell_signal = (trend == -1) & (previous_trend == 1)

up_plot = [
    float(up[index]) if trend[index] == 1 and not np.isnan(up[index]) else None
    for index in range(n)
]
down_plot = [
    float(down[index]) if trend[index] == -1 and not np.isnan(down[index]) else None
    for index in range(n)
]
ohlc4_plot = [float(value) if not np.isnan(value) else None for value in ohlc4]

buy_marks = [
    float(up[index])
    if show_signals and buy_signal[index] and not np.isnan(up[index])
    else None
    for index in range(n)
]
sell_marks = [
    float(down[index])
    if show_signals and sell_signal[index] and not np.isnan(down[index])
    else None
    for index in range(n)
]

up_cloud = []
down_cloud = []
for index in range(n):
    center = ohlc4_plot[index]
    if center is not None and up_plot[index] is not None:
        up_cloud.append(
            {
                "upper": max(center, up_plot[index]),
                "lower": min(center, up_plot[index]),
                "color": "#4CAF50" if highlighting else "#FFFFFF",
            }
        )
    else:
        up_cloud.append(None)

    if center is not None and down_plot[index] is not None:
        down_cloud.append(
            {
                "upper": max(center, down_plot[index]),
                "lower": min(center, down_plot[index]),
                "color": "#F23645" if highlighting else "#FFFFFF",
            }
        )
    else:
        down_cloud.append(None)

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Up Trend",
            "data": up_plot,
            "color": "#4CAF50",
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        },
        {
            "name": "Down Trend",
            "data": down_plot,
            "color": "#F23645",
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        },
        {
            "name": "UpTrend Highlighter",
            "title": False,
            "data": up_cloud,
            "color": "#4CAF50",
            "type": "cloud",
            "opacity": 0.12,
            "overlay": True,
        },
        {
            "name": "DownTrend Highlighter",
            "title": False,
            "data": down_cloud,
            "color": "#F23645",
            "type": "cloud",
            "opacity": 0.12,
            "overlay": True,
        },
    ],
    "signals": [
        {
            "type": "buy",
            "text": "Buy",
            "name": "Buy",
            "data": buy_marks,
            "color": "#4CAF50",
            "renderMode": "events",
        },
        {
            "type": "sell",
            "text": "Sell",
            "name": "Sell",
            "data": sell_marks,
            "color": "#F23645",
            "renderMode": "events",
        },
    ],
    "layers": [],
}
