# ============================================================
# QuantDinger Double EMA Pullback
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Double EMA Pullback"
my_indicator_description = (
    "Fast and slow exponential averages with pullback-qualified price crosses "
    "that mark bullish and bearish trend-continuation events."
)

# @param backstep int 5 Pullback backstep range=1:100:1
# @param fast_source str close Fast EMA source values=open,high,low,close,hl2,hlc3,ohlc4
# @param fast_period int 50 Fast EMA period range=1:500:1
# @param fast_color str #2962FF Fast EMA color
# @param slow_source str close Slow EMA source values=open,high,low,close,hl2,hlc3,ohlc4
# @param slow_period int 200 Slow EMA period range=1:1000:1
# @param slow_color str #EF4444 Slow EMA color
# @param show_signals bool true Show pullback events
# @param buy_text str Buy Buy signal text
# @param buy_color str #16A34A Buy signal color
# @param sell_text str Sell Sell signal text
# @param sell_color str #EF4444 Sell signal color
# @param text_color str #FFFFFF Label text color

backstep = max(1, int(params.get("backstep", 5)))
fast_source = str(params.get("fast_source", "close")).lower()
fast_period = max(1, int(params.get("fast_period", 50)))
fast_color = str(params.get("fast_color", "#2962FF"))
slow_source = str(params.get("slow_source", "close")).lower()
slow_period = max(1, int(params.get("slow_period", 200)))
slow_color = str(params.get("slow_color", "#EF4444"))
show_signals = bool(params.get("show_signals", True))
buy_text = str(params.get("buy_text", "Buy"))
buy_color = str(params.get("buy_color", "#16A34A"))
sell_text = str(params.get("sell_text", "Sell"))
sell_color = str(params.get("sell_color", "#EF4444"))
text_color = str(params.get("text_color", "#FFFFFF"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def select_source(mode):
    if mode in ("open", "1"):
        return open_price.copy()
    if mode in ("high",):
        return high.copy()
    if mode in ("low",):
        return low.copy()
    if mode in ("hl2", "2"):
        return (high + low) / 2.0
    if mode in ("hlc3",):
        return (high + low + close) / 3.0
    if mode in ("ohlc4", "3"):
        return (open_price + high + low + close) / 4.0
    return close.copy()


def pine_ema(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


fast_ema = pine_ema(select_source(fast_source), fast_period)
slow_ema = pine_ema(select_source(slow_source), slow_period)
buy_event = np.zeros(n, dtype=bool)
sell_event = np.zeros(n, dtype=bool)

for index in range(1, n):
    if index < backstep:
        continue
    valid = (
        np.isfinite(fast_ema[index])
        and np.isfinite(fast_ema[index - 1])
        and np.isfinite(slow_ema[index])
    )
    if not valid:
        continue
    crossed_up = close[index] > fast_ema[index] and close[index - 1] <= fast_ema[index - 1]
    crossed_down = close[index] < fast_ema[index] and close[index - 1] >= fast_ema[index - 1]
    buy_event[index] = crossed_up and fast_ema[index] > slow_ema[index] and close[index - backstep] > fast_ema[index]
    sell_event[index] = crossed_down and fast_ema[index] < slow_ema[index] and close[index - backstep] < fast_ema[index]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Fast EMA",
            "data": [float(value) if np.isfinite(value) else None for value in fast_ema],
            "color": fast_color,
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        },
        {
            "name": "Slow EMA",
            "data": [float(value) if np.isfinite(value) else None for value in slow_ema],
            "color": slow_color,
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        },
    ],
    "signals": [
        {
            "type": "buy",
            "text": buy_text,
            "name": "Pullback Buy",
            "data": [float(low[index]) if show_signals and buy_event[index] else None for index in range(n)],
            "color": buy_color,
            "textColor": text_color,
            "renderMode": "events",
        },
        {
            "type": "sell",
            "text": sell_text,
            "name": "Pullback Sell",
            "data": [float(high[index]) if show_signals and sell_event[index] else None for index in range(n)],
            "color": sell_color,
            "textColor": text_color,
            "renderMode": "events",
        },
    ],
    "layers": [],
    "tables": [],
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "fastEma": [float(value) if np.isfinite(value) else None for value in fast_ema],
        "slowEma": [float(value) if np.isfinite(value) else None for value in slow_ema],
        "buyEvent": [bool(value) for value in buy_event],
        "sellEvent": [bool(value) for value in sell_event],
    },
}
