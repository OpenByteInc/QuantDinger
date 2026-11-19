# ============================================================
# QuantDinger EMA Trade Plan
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger EMA Trade Plan"
my_indicator_description = (
    "Uses fast and slow EMA trend changes for entries, manages ATR stops and "
    "reward-to-risk targets, and marks exits, invalidations, and partial targets."
)

# @param fast_length int 5 Fast EMA length range=1:100:1
# @param slow_length int 13 Slow EMA length range=1:200:1
# @param atr_period int 14 Wilder ATR period range=1:100:1
# @param atr_stop_multiplier float 0.5 Stop offset in ATR units range=0.1:10:0.1
# @param risk_reward float 3.0 Final reward-to-risk ratio range=1:10:0.5
# @param confirm_candle bool true Require candle direction confirmation
# @param show_fast_ema bool true Show fast EMA
# @param show_slow_ema bool true Show slow EMA

fast_length = min(100, max(1, int(params.get("fast_length", 5))))
slow_length = min(200, max(1, int(params.get("slow_length", 13))))
atr_period = min(100, max(1, int(params.get("atr_period", 14))))
atr_stop_multiplier = min(10.0, max(0.1, float(params.get("atr_stop_multiplier", 0.5))))
risk_reward = min(10.0, max(1.0, float(params.get("risk_reward", 3.0))))
confirm_candle = bool(params.get("confirm_candle", True))
show_fast_ema = bool(params.get("show_fast_ema", True))
show_slow_ema = bool(params.get("show_slow_ema", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

def ema_series(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) == 0:
        return result
    alpha = 2.0 / (length + 1.0)
    result[0] = float(values[0])
    for index in range(1, len(values)):
        result[index] = alpha * values[index] + (1.0 - alpha) * result[index - 1]
    return result

fast_ema = ema_series(close, fast_length)
slow_ema = ema_series(close, slow_length)

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )

atr = np.full(n, np.nan, dtype=float)
if n >= atr_period:
    atr[atr_period - 1] = float(np.mean(true_range[:atr_period]))
    for index in range(atr_period, n):
        atr[index] = (
            atr[index - 1] * (atr_period - 1) + true_range[index]
        ) / atr_period

fast_plot = [float(value) if show_fast_ema and np.isfinite(value) else None for value in fast_ema]
slow_plot = [float(value) if show_slow_ema and np.isfinite(value) else None for value in slow_ema]
price_guide = [float(value) if np.isfinite(value) else None for value in close]
entry_plan = [None for _ in range(n)]
stop_plan = [None for _ in range(n)]
target_plan = [None for _ in range(n)]
partial_plans = [[None for _ in range(n)] for _ in range(9)]

buy_entries = [None for _ in range(n)]
sell_entries = [None for _ in range(n)]
long_targets = [None for _ in range(n)]
short_targets = [None for _ in range(n)]
long_stops = [None for _ in range(n)]
short_stops = [None for _ in range(n)]
long_partials = [None for _ in range(n)]
short_partials = [None for _ in range(n)]
invalidated_longs = [None for _ in range(n)]
invalidated_shorts = [None for _ in range(n)]
summary_marks = [None for _ in range(n)]

active = False
position_side = None
entry_price = np.nan
stop_price = np.nan
target_price = np.nan
partial_levels = []
partial_hit = []

for index in range(1, n):
    new_position = False
    if active and position_side == "long":
        if high[index] >= target_price:
            long_targets[index] = float(high[index])
            active = False
            position_side = None
        elif low[index] <= stop_price:
            long_stops[index] = float(low[index])
            active = False
            position_side = None
    elif active and position_side == "short":
        if low[index] <= target_price:
            short_targets[index] = float(low[index])
            active = False
            position_side = None
        elif high[index] >= stop_price:
            short_stops[index] = float(high[index])
            active = False
            position_side = None

    if active:
        for level_index, level in enumerate(partial_levels):
            if partial_hit[level_index]:
                continue
            if position_side == "long" and high[index] >= level:
                partial_hit[level_index] = True
                long_partials[index] = {
                    "value": float(high[index]),
                    "text": f"TP{level_index + 1}",
                    "color": "#4CAF50",
                }
            elif position_side == "short" and low[index] <= level:
                partial_hit[level_index] = True
                short_partials[index] = {
                    "value": float(low[index]),
                    "text": f"TP{level_index + 1}",
                    "color": "#4CAF50",
                }

    bull_trend = fast_ema[index] > slow_ema[index]
    previous_bull = fast_ema[index - 1] > slow_ema[index - 1]
    bear_trend = fast_ema[index] < slow_ema[index]
    trend_change = bull_trend != previous_bull
    buy_signal = bull_trend and trend_change
    sell_signal = bear_trend and trend_change
    if confirm_candle:
        buy_signal = buy_signal and close[index] > open_price[index]
        sell_signal = sell_signal and close[index] < open_price[index]

    if buy_signal and (not active or position_side != "long") and np.isfinite(atr[index]):
        if active and position_side == "short":
            invalidated_shorts[index] = float(low[index])
        entry_price = float(close[index])
        stop_price = float(low[index] - atr[index] * atr_stop_multiplier)
        risk = entry_price - stop_price
        target_price = float(entry_price + risk * risk_reward)
        partial_levels = [entry_price + risk * step for step in range(1, int(np.floor(risk_reward)))]
        partial_hit = [False for _ in partial_levels]
        active = True
        position_side = "long"
        new_position = True
        buy_entries[index] = float(low[index])

    if sell_signal and (not active or position_side != "short") and np.isfinite(atr[index]):
        if active and position_side == "long":
            invalidated_longs[index] = float(high[index])
        entry_price = float(close[index])
        stop_price = float(high[index] + atr[index] * atr_stop_multiplier)
        risk = stop_price - entry_price
        target_price = float(entry_price - risk * risk_reward)
        partial_levels = [entry_price - risk * step for step in range(1, int(np.floor(risk_reward)))]
        partial_hit = [False for _ in partial_levels]
        active = True
        position_side = "short"
        new_position = True
        sell_entries[index] = float(high[index])

    if active and not new_position:
        entry_plan[index] = entry_price
        stop_plan[index] = stop_price
        target_plan[index] = target_price
        for level_index, level in enumerate(partial_levels[:9]):
            partial_plans[level_index][index] = float(level)

if n and active:
    summary_marks[-1] = {
        "value": float(target_price),
        "text": (
            f"{position_side.upper()} · Entry {entry_price:.2f}\n"
            f"Stop {stop_price:.2f} · Target {target_price:.2f} · R/R {risk_reward:.2f}"
        ),
        "color": "#FFC107",
    }

plots = [
    {"name": "Fast EMA", "data": fast_plot, "color": "#26A69A", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Slow EMA", "data": slow_plot, "color": "#FF9800", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Entry Plan", "data": entry_plan, "color": "#B0B4BC", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
    {"name": "Stop Plan", "data": stop_plan, "color": "#F44336", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Final Target", "data": target_plan, "color": "#4CAF50", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Price Guide", "title": False, "data": price_guide, "color": "rgba(0, 0, 0, 0)", "type": "line", "lineWidth": 1, "overlay": True},
]
for level_index, data in enumerate(partial_plans):
    plots.append({
        "name": f"Partial Target {level_index + 1}",
        "title": False,
        "data": data,
        "color": "rgba(76, 175, 80, 0.38)",
        "type": "line",
        "lineWidth": 1,
        "lineStyle": "dotted",
        "overlay": True,
    })

output = {
    "name": my_indicator_name,
    "plots": plots,
    "signals": [
        {"type": "buy", "text": "BUY", "name": "Long Entry", "data": buy_entries, "color": "#00C853", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Short Entry", "data": sell_entries, "color": "#FF1744", "renderMode": "events"},
        {"type": "sell", "text": "TP", "name": "Long Final Target", "data": long_targets, "color": "#00E676", "renderMode": "events"},
        {"type": "buy", "text": "TP", "name": "Short Final Target", "data": short_targets, "color": "#00E676", "renderMode": "events"},
        {"type": "buy", "text": "SL", "name": "Long Stop", "data": long_stops, "color": "#F44336", "renderMode": "events"},
        {"type": "sell", "text": "SL", "name": "Short Stop", "data": short_stops, "color": "#F44336", "renderMode": "events"},
        {"type": "sell", "text": "TP", "name": "Long Partial Targets", "data": long_partials, "color": "#4CAF50", "renderMode": "events"},
        {"type": "buy", "text": "TP", "name": "Short Partial Targets", "data": short_partials, "color": "#4CAF50", "renderMode": "events"},
        {"type": "sell", "text": "INV", "name": "Invalidated Long", "data": invalidated_longs, "color": "#FFC107", "renderMode": "events"},
        {"type": "buy", "text": "INV", "name": "Invalidated Short", "data": invalidated_shorts, "color": "#FFC107", "renderMode": "events"},
        {"type": "sell", "text": "PLAN", "name": "Active Plan", "data": summary_marks, "color": "#FFC107", "renderMode": "events"},
    ],
    "layers": [],
}
