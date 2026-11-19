# ============================================================
# QuantDinger Buy Sell Signal
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Buy Sell Signal"
my_indicator_description = (
    "Tracks fast/slow EMA regime changes, opens a visual long or short plan, "
    "and manages ATR-based stop-loss and risk/reward targets on the chart."
)

# @param fast_ema int 5 Fast EMA range=1:500:1
# @param slow_ema int 13 Slow EMA range=1:500:1
# @param atr_period int 14 ATR period range=1:500:1
# @param stop_atr_multiplier float 0.5 Stop ATR multiplier range=0.1:20.0:0.1
# @param risk_reward float 3.0 Risk/reward ratio range=1.0:20.0:0.5
# @param require_candle_confirmation bool true Require candle confirmation
# @param show_fast_ema bool true Show fast EMA
# @param fast_ema_color str #22C55E Fast EMA color
# @param show_slow_ema bool true Show slow EMA
# @param slow_ema_color str #F59E0B Slow EMA color
# @param show_dashboard bool true Show position dashboard
# @param max_event_labels int 8 Recent event labels range=1:100:1

fast_length = min(500, max(1, int(params.get("fast_ema", 5))))
slow_length = min(500, max(1, int(params.get("slow_ema", 13))))
atr_length = min(500, max(1, int(params.get("atr_period", 14))))
stop_multiplier = min(20.0, max(0.1, float(params.get("stop_atr_multiplier", 0.5))))
risk_reward = min(20.0, max(1.0, float(params.get("risk_reward", 3.0))))
require_confirmation = bool(params.get("require_candle_confirmation", True))
show_fast = bool(params.get("show_fast_ema", True))
fast_color = str(params.get("fast_ema_color", "#22C55E"))
show_slow = bool(params.get("show_slow_ema", True))
slow_color = str(params.get("slow_ema_color", "#F59E0B"))
show_dashboard = bool(params.get("show_dashboard", True))
max_event_labels = min(100, max(1, int(params.get("max_event_labels", 8))))

frame = df.copy()
frame = frame.reset_index(drop=True)
n = len(frame)
if "time" not in frame.columns:
    frame["time"] = np.arange(n, dtype=float) * 3600.0
open_values = pd.to_numeric(frame["open"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
high_values = pd.to_numeric(frame["high"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
time_values = pd.to_numeric(frame["time"], errors="coerce").fillna(0.0).to_numpy(dtype=float)


def ema(values, length):
    result = np.full(n, np.nan, dtype=float)
    if not n:
        return result
    alpha = 2.0 / (float(length) + 1.0)
    result[0] = float(values[0])
    for index in range(1, n):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result


def wilder(values, length):
    result = np.full(n, np.nan, dtype=float)
    if n < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    alpha = 1.0 / float(length)
    for index in range(length, n):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result


fast_ema = ema(close_values, fast_length)
slow_ema = ema(close_values, slow_length)
true_range = np.zeros(n, dtype=float)
if n:
    true_range[0] = high_values[0] - low_values[0]
for index in range(1, n):
    true_range[index] = max(
        high_values[index] - low_values[index],
        abs(high_values[index] - close_values[index - 1]),
        abs(low_values[index] - close_values[index - 1]),
    )
atr = wilder(true_range, atr_length)

bull_regime = fast_ema > slow_ema
bear_regime = fast_ema < slow_ema
buy_signal = np.zeros(n, dtype=bool)
sell_signal = np.zeros(n, dtype=bool)
for index in range(1, n):
    trend_change = bool(bull_regime[index]) != bool(bull_regime[index - 1])
    candle_bullish = close_values[index] > open_values[index]
    candle_bearish = close_values[index] < open_values[index]
    buy_signal[index] = bool(bull_regime[index] and trend_change and (candle_bullish or not require_confirmation))
    sell_signal[index] = bool(bear_regime[index] and trend_change and (candle_bearish or not require_confirmation))

position_series = np.zeros(n, dtype=int)
entry_series = np.full(n, np.nan, dtype=float)
stop_series = np.full(n, np.nan, dtype=float)
final_target_series = np.full(n, np.nan, dtype=float)
final_target_hit = np.zeros(n, dtype=bool)
stop_hit = np.zeros(n, dtype=bool)
invalidated_long = np.zeros(n, dtype=bool)
invalidated_short = np.zeros(n, dtype=bool)
intermediate_hit_events = [[] for _ in range(n)]

position = 0
entry_price = np.nan
stop_price = np.nan
final_target = np.nan
intermediate_targets = []
intermediate_hit_flags = []
entry_index = -1
last_closed_reason = "None"
target_count = max(0, int(np.floor(risk_reward)) - 1)

for index in range(n):
    if position == 1:
        if high_values[index] >= final_target:
            final_target_hit[index] = True
            position = 0
            last_closed_reason = "Take Profit"
        elif low_values[index] <= stop_price:
            stop_hit[index] = True
            position = 0
            last_closed_reason = "Stop Loss"
    elif position == -1:
        if low_values[index] <= final_target:
            final_target_hit[index] = True
            position = 0
            last_closed_reason = "Take Profit"
        elif high_values[index] >= stop_price:
            stop_hit[index] = True
            position = 0
            last_closed_reason = "Stop Loss"

    if position != 0:
        for target_index, target_value in enumerate(intermediate_targets):
            if intermediate_hit_flags[target_index]:
                continue
            reached = high_values[index] >= target_value if position == 1 else low_values[index] <= target_value
            if reached:
                intermediate_hit_flags[target_index] = True
                intermediate_hit_events[index].append(target_index + 1)

    if buy_signal[index] and np.isfinite(atr[index]) and position != 1:
        if position == -1:
            invalidated_short[index] = True
            last_closed_reason = "Short Invalidated"
        entry_price = float(close_values[index])
        stop_price = float(low_values[index] - atr[index] * stop_multiplier)
        risk = max(entry_price - stop_price, 1e-12)
        final_target = float(entry_price + risk * risk_reward)
        intermediate_targets = [float(entry_price + risk * step) for step in range(1, target_count + 1)]
        intermediate_hit_flags = [False] * len(intermediate_targets)
        position = 1
        entry_index = index
    elif sell_signal[index] and np.isfinite(atr[index]) and position != -1:
        if position == 1:
            invalidated_long[index] = True
            last_closed_reason = "Long Invalidated"
        entry_price = float(close_values[index])
        stop_price = float(high_values[index] + atr[index] * stop_multiplier)
        risk = max(stop_price - entry_price, 1e-12)
        final_target = float(entry_price - risk * risk_reward)
        intermediate_targets = [float(entry_price - risk * step) for step in range(1, target_count + 1)]
        intermediate_hit_flags = [False] * len(intermediate_targets)
        position = -1
        entry_index = index

    position_series[index] = position
    if position != 0:
        entry_series[index] = entry_price
        stop_series[index] = stop_price
        final_target_series[index] = final_target


def finite_list(values):
    return [float(value) if np.isfinite(value) else None for value in values]


plots = [{
    "name": "Signal Alignment Anchor",
    "title": False,
    "data": [float(value) for value in close_values],
    "color": "rgba(0,0,0,0)",
    "type": "line",
    "lineWidth": 1,
    "overlay": True,
}]
if show_fast:
    plots.append({"name": "Fast EMA", "data": finite_list(fast_ema), "color": fast_color, "type": "line", "lineWidth": 1, "overlay": True})
if show_slow:
    plots.append({"name": "Slow EMA", "data": finite_list(slow_ema), "color": slow_color, "type": "line", "lineWidth": 1, "overlay": True})

active_position = int(position_series[-1]) if n else 0
if active_position != 0:
    active_mask = np.arange(n) >= max(0, entry_index)
    entry_plot = [float(entry_price) if active_mask[index] else None for index in range(n)]
    stop_plot = [float(stop_price) if active_mask[index] else None for index in range(n)]
    target_plot = [float(final_target) if active_mask[index] else None for index in range(n)]
    plots.extend([
        {"name": "Active Entry", "data": entry_plot, "color": "#E2E8F0", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
        {"name": "Active Stop", "data": stop_plot, "color": "#EF4444", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Final Target", "data": target_plot, "color": "#22C55E", "type": "line", "lineWidth": 2, "overlay": True},
    ])
    for target_index, target_value in enumerate(intermediate_targets):
        plots.append({
            "name": f"Target {target_index + 1}",
            "data": [float(target_value) if active_mask[index] else None for index in range(n)],
            "color": "rgba(34,197,94,0.55)",
            "type": "line",
            "lineWidth": 1,
            "lineStyle": "dotted",
            "overlay": True,
        })

event_specs = []
for index in range(n):
    if buy_signal[index] and np.isfinite(atr[index]):
        event_specs.append((index, "buy", "BUY", "#22C55E", low_values[index], "below"))
    if sell_signal[index] and np.isfinite(atr[index]):
        event_specs.append((index, "sell", "SELL", "#EF4444", high_values[index], "above"))
    if final_target_hit[index]:
        event_specs.append((index, "final", "TP", "#14B8A6", high_values[index], "above"))
    if stop_hit[index]:
        event_specs.append((index, "stop", "SL", "#DC2626", low_values[index], "below"))
    if invalidated_long[index]:
        event_specs.append((index, "invalidated_long", "INV BUY", "#FACC15", high_values[index], "above"))
    if invalidated_short[index]:
        event_specs.append((index, "invalidated_short", "INV SELL", "#FACC15", high_values[index], "above"))
    for target_number in intermediate_hit_events[index]:
        event_specs.append((index, f"target_{target_number}", f"TP{target_number}", "#16A34A", high_values[index], "above"))

visible_events = event_specs[-max_event_labels:]
visible_keys = {(index, event_type) for index, event_type, _, _, _, _ in visible_events}
signals = [
    {"type": "buy", "name": "Buy Signal", "text": "BUY", "color": "#22C55E", "renderMode": "events", "data": [float(low_values[index]) if (index, "buy") in visible_keys else None for index in range(n)]},
    {"type": "sell", "name": "Sell Signal", "text": "SELL", "color": "#EF4444", "renderMode": "events", "data": [float(high_values[index]) if (index, "sell") in visible_keys else None for index in range(n)]},
    {"type": "sell", "name": "Final Take Profit", "text": "TP", "color": "#14B8A6", "renderMode": "events", "data": [float(high_values[index]) if (index, "final") in visible_keys else None for index in range(n)]},
    {"type": "sell", "name": "Stop Loss", "text": "SL", "color": "#DC2626", "renderMode": "events", "data": [float(low_values[index]) if (index, "stop") in visible_keys else None for index in range(n)]},
]

layers = []
for index, event_type, text_value, color_value, price_value, side_value in visible_events:
    if event_type in ("buy", "sell", "final", "stop"):
        continue
    layers.append({
        "type": "label",
        "timestamp": float(time_values[index]),
        "price": float(price_value),
        "text": text_value,
        "color": color_value,
        "fillColor": color_value,
        "borderColor": color_value,
        "textColor": "#111827" if color_value == "#FACC15" else "#FFFFFF",
        "fontSize": 8,
        "side": side_value,
    })

position_text = "LONG" if active_position > 0 else "SHORT" if active_position < 0 else "No Position"
tables = []
if show_dashboard:
    tables.append({
        "position": "top-right",
        "backgroundColor": "rgba(15,23,42,0.90)",
        "borderColor": "#22C55E" if active_position > 0 else "#EF4444" if active_position < 0 else "#64748B",
        "textColor": "#F8FAFC",
        "accentColor": "#22C55E" if active_position > 0 else "#EF4444" if active_position < 0 else "#64748B",
        "rows": [
            ["BUY / SELL SIGNAL", position_text],
            ["Entry", f"{entry_price:.4f}" if active_position else "-"],
            ["Stop Loss", f"{stop_price:.4f}" if active_position else "-"],
            ["Take Profit", f"{final_target:.4f}" if active_position else "-"],
            ["Risk : Reward", f"1 : {risk_reward:.2f}"],
            ["Last Close", last_closed_reason],
        ],
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "fastEma": finite_list(fast_ema),
        "slowEma": finite_list(slow_ema),
        "atr": finite_list(atr),
        "bullRegime": [bool(value) for value in bull_regime],
        "bearRegime": [bool(value) for value in bear_regime],
        "buySignal": [bool(value) for value in buy_signal],
        "sellSignal": [bool(value) for value in sell_signal],
        "position": [int(value) for value in position_series],
        "entry": finite_list(entry_series),
        "stop": finite_list(stop_series),
        "finalTarget": finite_list(final_target_series),
        "finalTargetHit": [bool(value) for value in final_target_hit],
        "stopHit": [bool(value) for value in stop_hit],
        "invalidatedLong": [bool(value) for value in invalidated_long],
        "invalidatedShort": [bool(value) for value in invalidated_short],
    },
}
