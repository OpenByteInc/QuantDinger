# ============================================================
# QuantDinger Three-Way Bollinger Trend
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Three-Way Bollinger Trend"
my_indicator_description = (
    "Combines a Hull-centered volatility band with RSI trend memory, confirmed HMA pullbacks, "
    "optional trade tracking, ATR risk, staged targets, cooldowns, and dashboard statistics."
)

# @param trade_mode bool false Enable trade tracking
# @param hma_length int 20 HMA center length range=2:500:1
# @param band_stdev_mult float 2.0 Band deviation multiplier range=0.5:10.0:0.1
# @param rsi_length int 14 RSI length range=2:500:1
# @param rsi_bullish float 55.0 Bullish RSI threshold range=51.0:99.0:1.0
# @param rsi_bearish float 45.0 Bearish RSI threshold range=1.0:49.0:1.0
# @param confirmation_bars int 2 Confirmation bars range=1:50:1
# @param stop_atr_mult float 1.5 Stop ATR multiplier range=0.1:20.0:0.1
# @param show_setup_labels bool true Show setup labels
# @param show_expiry_labels bool true Show expiry labels
# @param tp1_r float 1.0 First target R range=0.1:20.0:0.1
# @param tp2_r float 2.0 Second target R range=0.1:20.0:0.1
# @param tp3_r float 3.0 Third target R range=0.1:20.0:0.1
# @param enable_tp2 bool true Enable second target
# @param enable_tp3 bool true Enable third target
# @param max_trade_duration int 200 Maximum trade bars range=10:2000:1
# @param max_stored_trades int 30 Maximum displayed trades range=5:100:1
# @param stability_mode bool true Block entries while a trade is open
# @param alternating_filter bool false Force alternating directions
# @param cooldown_bars int 5 Signal cooldown range=0:500:1
# @param dashboard_position str top-right Dashboard position values=top-left,top-right,bottom-left,bottom-right
# @param bullish_color str #1A935B Bullish color
# @param bearish_color str #FF4757 Bearish color
# @param neutral_color str #FACC15 Neutral color

trade_mode = bool(params.get("trade_mode", False))
hma_length = min(500, max(2, int(params.get("hma_length", 20))))
band_stdev_mult = min(10.0, max(0.5, float(params.get("band_stdev_mult", 2.0))))
rsi_length = min(500, max(2, int(params.get("rsi_length", 14))))
rsi_bullish = min(99.0, max(51.0, float(params.get("rsi_bullish", 55.0))))
rsi_bearish = min(49.0, max(1.0, float(params.get("rsi_bearish", 45.0))))
confirmation_bars = min(50, max(1, int(params.get("confirmation_bars", 2))))
stop_atr_mult = min(20.0, max(0.1, float(params.get("stop_atr_mult", 1.5))))
show_setup_labels = bool(params.get("show_setup_labels", True))
show_expiry_labels = bool(params.get("show_expiry_labels", True))
tp1_r = min(20.0, max(0.1, float(params.get("tp1_r", 1.0))))
tp2_r = min(20.0, max(0.1, float(params.get("tp2_r", 2.0))))
tp3_r = min(20.0, max(0.1, float(params.get("tp3_r", 3.0))))
enable_tp2 = bool(params.get("enable_tp2", True))
enable_tp3 = bool(params.get("enable_tp3", True))
max_trade_duration = min(2000, max(10, int(params.get("max_trade_duration", 200))))
max_stored_trades = min(100, max(5, int(params.get("max_stored_trades", 30))))
stability_mode = bool(params.get("stability_mode", True))
alternating_filter = bool(params.get("alternating_filter", False))
cooldown_bars = min(500, max(0, int(params.get("cooldown_bars", 5))))
dashboard_position = str(params.get("dashboard_position", "top-right"))
if dashboard_position not in ("top-left", "top-right", "bottom-left", "bottom-right"):
    dashboard_position = "top-right"
bullish_color = str(params.get("bullish_color", "#1A935B"))
bearish_color = str(params.get("bearish_color", "#FF4757"))
neutral_color = str(params.get("neutral_color", "#FACC15"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

def wma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
    return result

def hma(values, length):
    half_length = max(1, int(length / 2))
    root_length = max(1, int(round(np.sqrt(length))))
    full = wma(values, length)
    half = wma(values, half_length)
    raw = 2.0 * half - full
    return wma(raw, root_length)

def rolling_std(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        result[index] = float(np.std(window, ddof=0))
    return result

def rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    for index in range(length, len(values)):
        result[index] = (result[index - 1] * (length - 1) + values[index]) / length
    return result

def atr(values_high, values_low, values_close, length):
    true_range = np.zeros(len(values_close), dtype=float)
    for index in range(len(values_close)):
        if index == 0:
            true_range[index] = values_high[index] - values_low[index]
        else:
            true_range[index] = max(
                values_high[index] - values_low[index],
                abs(values_high[index] - values_close[index - 1]),
                abs(values_low[index] - values_close[index - 1]),
            )
    return rma(true_range, length)

def rsi(values, length):
    changes = np.zeros(len(values), dtype=float)
    if len(values) > 1:
        changes[1:] = values[1:] - values[:-1]
    gains = np.maximum(changes, 0.0)
    losses = np.maximum(-changes, 0.0)
    average_gain = rma(gains, length)
    average_loss = rma(losses, length)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        if not (np.isfinite(average_gain[index]) and np.isfinite(average_loss[index])):
            continue
        if average_loss[index] <= 1e-12:
            result[index] = 100.0 if average_gain[index] > 1e-12 else 50.0
        else:
            ratio = average_gain[index] / average_loss[index]
            result[index] = 100.0 - 100.0 / (1.0 + ratio)
    return result

atr_values = atr(high, low, close, 14)
hma_center = hma(close, hma_length)
price_stdev = rolling_std(close, hma_length)
band_upper = hma_center + band_stdev_mult * price_stdev
band_lower = hma_center - band_stdev_mult * price_stdev
rsi_values = rsi(close, rsi_length)

trend_direction = np.zeros(n, dtype=int)
band_colors = [neutral_color for _ in range(n)]
last_trend_direction = 0
for index in range(n):
    if np.isfinite(rsi_values[index]) and rsi_values[index] > rsi_bullish:
        last_trend_direction = 1
        band_colors[index] = bullish_color
    elif np.isfinite(rsi_values[index]) and rsi_values[index] < rsi_bearish:
        last_trend_direction = -1
        band_colors[index] = bearish_color
    else:
        band_colors[index] = neutral_color
    trend_direction[index] = last_trend_direction

raw_buy = np.zeros(n, dtype=bool)
raw_sell = np.zeros(n, dtype=bool)
awaiting_buy = False
awaiting_sell = False
confirmation_counter = 0
for index in range(1, n):
    if not (
        np.isfinite(hma_center[index]) and np.isfinite(hma_center[index - 1])
    ):
        continue
    trigger_buy = close[index] > hma_center[index] and close[index - 1] <= hma_center[index - 1]
    trigger_sell = close[index] < hma_center[index] and close[index - 1] >= hma_center[index - 1]
    if trigger_buy:
        awaiting_buy = True
        awaiting_sell = False
        confirmation_counter = 0
    elif trigger_sell:
        awaiting_sell = True
        awaiting_buy = False
        confirmation_counter = 0
    elif awaiting_buy:
        if close[index] <= hma_center[index]:
            awaiting_buy = False
            confirmation_counter = 0
        else:
            confirmation_counter += 1
    elif awaiting_sell:
        if close[index] >= hma_center[index]:
            awaiting_sell = False
            confirmation_counter = 0
        else:
            confirmation_counter += 1

    confirmed_buy = awaiting_buy and confirmation_counter >= confirmation_bars
    confirmed_sell = awaiting_sell and confirmation_counter >= confirmation_bars
    if confirmed_buy:
        awaiting_buy = False
        confirmation_counter = 0
    if confirmed_sell:
        awaiting_sell = False
        confirmation_counter = 0
    raw_buy[index] = confirmed_buy and trend_direction[index] == 1
    raw_sell[index] = confirmed_sell and trend_direction[index] == -1

fire_buy = np.zeros(n, dtype=bool)
fire_sell = np.zeros(n, dtype=bool)
entry_buy = np.zeros(n, dtype=bool)
entry_sell = np.zeros(n, dtype=bool)
exit_win = np.zeros(n, dtype=bool)
exit_stop = np.zeros(n, dtype=bool)
exit_timeout = np.zeros(n, dtype=bool)
tp1_hits = np.zeros(n, dtype=bool)
tp2_hits = np.zeros(n, dtype=bool)
trades = []
last_signal_bar = -1
last_signal_direction = 0
pending_direction = 0
pending_entry_bar = -1
total_trades = 0
total_wins = 0.0
total_losses = 0
total_profit_r = 0.0
total_loss_r = 0.0

for index in range(n):
    any_trade_open = any(trade["active"] for trade in trades)
    cooldown_ok = last_signal_bar < 0 or index - last_signal_bar >= cooldown_bars
    stability_allowed = not trade_mode or not (stability_mode and any_trade_open)
    smart_buy_allowed = not trade_mode or not (alternating_filter and last_signal_direction == 1)
    smart_sell_allowed = not trade_mode or not (alternating_filter and last_signal_direction == -1)
    fire_buy[index] = raw_buy[index] and cooldown_ok and stability_allowed and smart_buy_allowed
    fire_sell[index] = raw_sell[index] and cooldown_ok and stability_allowed and smart_sell_allowed
    if fire_buy[index] or fire_sell[index]:
        pending_direction = 1 if fire_buy[index] else -1
        pending_entry_bar = index + 1
        last_signal_bar = index
        last_signal_direction = pending_direction

    if pending_entry_bar == index and np.isfinite(atr_values[index]):
        is_buy = pending_direction == 1
        entry_price = float(open_price[index])
        stop_distance = float(stop_atr_mult * atr_values[index])
        if stop_distance > 0:
            entry_buy[index] = is_buy
            entry_sell[index] = not is_buy
            if trade_mode:
                stop_price = entry_price - stop_distance if is_buy else entry_price + stop_distance
                final_r = tp3_r if enable_tp3 else tp2_r if enable_tp2 else tp1_r
                final_target = entry_price + final_r * stop_distance if is_buy else entry_price - final_r * stop_distance
                trades.append({
                    "id": len(trades) + 1,
                    "active": True,
                    "is_buy": is_buy,
                    "entry": entry_price,
                    "stop": stop_price,
                    "risk": stop_distance,
                    "entry_bar": index,
                    "max_tp": 0,
                    "final_r": final_r,
                    "final_target": final_target,
                    "highest_price": entry_price,
                    "highest_bar": index,
                    "exit_bar": None,
                    "result": "",
                    "result_r": 0.0,
                    "stop_history": [None for _ in range(n)],
                })
                total_trades += 1
        pending_entry_bar = -1

    if trade_mode:
        for trade in trades:
            if not trade["active"]:
                continue
            is_buy = trade["is_buy"]
            entry_price = trade["entry"]
            risk_distance = trade["risk"]
            tp1_price = entry_price + tp1_r * risk_distance if is_buy else entry_price - tp1_r * risk_distance
            tp2_price = entry_price + tp2_r * risk_distance if is_buy else entry_price - tp2_r * risk_distance
            current_stop = tp1_price if trade["max_tp"] >= 2 else entry_price if trade["max_tp"] >= 1 else trade["stop"]
            trade["stop_history"][index] = float(current_stop)
            hit_stop = low[index] <= current_stop if is_buy else high[index] >= current_stop
            hit_final = high[index] >= trade["final_target"] if is_buy else low[index] <= trade["final_target"]
            close_now = False
            result_kind = ""
            if hit_stop:
                close_now = True
                result_kind = "TP" if trade["max_tp"] > 0 else "SL"
            elif hit_final:
                close_now = True
                result_kind = "WIN"
                trade["highest_price"] = trade["final_target"]
                trade["highest_bar"] = index
            else:
                hit_tp1 = high[index] >= tp1_price if is_buy else low[index] <= tp1_price
                hit_tp2 = enable_tp2 and (high[index] >= tp2_price if is_buy else low[index] <= tp2_price)
                if trade["max_tp"] < 1 and hit_tp1:
                    trade["max_tp"] = 1
                    trade["highest_price"] = tp1_price
                    trade["highest_bar"] = index
                    tp1_hits[index] = True
                if trade["max_tp"] == 1 and hit_tp2:
                    trade["max_tp"] = 2
                    trade["highest_price"] = tp2_price
                    trade["highest_bar"] = index
                    tp2_hits[index] = True
                if index - trade["entry_bar"] >= max_trade_duration:
                    close_now = True
                    result_kind = "TIMEOUT"

            if close_now:
                trade["active"] = False
                trade["exit_bar"] = index
                trade["result"] = result_kind
                result_r = trade["final_r"] if result_kind == "WIN" else tp1_r if trade["max_tp"] >= 2 else 0.0
                trade["result_r"] = result_r
                clean_loss = result_kind == "SL" and trade["max_tp"] == 0
                if result_kind in ("SL", "WIN", "TP"):
                    if clean_loss:
                        total_losses += 1
                        total_loss_r += 1.0
                        exit_stop[index] = True
                    else:
                        total_wins += 0.5 if result_r == 0.0 else 1.0
                        total_profit_r += result_r
                        exit_win[index] = True
                elif result_kind == "TIMEOUT":
                    exit_timeout[index] = True

plots = [
    {
        "name": "HMA Center",
        "title": not trade_mode,
        "data": [
            {"value": float(hma_center[index]), "color": band_colors[index]}
            if np.isfinite(hma_center[index]) else None
            for index in range(n)
        ],
        "color": bullish_color if not trade_mode else "rgba(26,147,91,0.001)",
        "type": "line",
        "lineWidth": 2,
        "overlay": True,
    },
]
if not trade_mode:
    plots.append({"name": "Upper Band", "data": [{"value": float(band_upper[index]), "color": band_colors[index]} if np.isfinite(band_upper[index]) else None for index in range(n)], "color": bullish_color, "type": "line", "overlay": True})
    plots.append({"name": "Lower Band", "data": [{"value": float(band_lower[index]), "color": band_colors[index]} if np.isfinite(band_lower[index]) else None for index in range(n)], "color": bearish_color, "type": "line", "overlay": True})
    plots.append({
        "name": "Trend Band",
        "data": [
            {"upper": float(band_upper[index]), "lower": float(band_lower[index]), "color": band_colors[index]}
            if np.isfinite(band_upper[index]) and np.isfinite(band_lower[index]) else None
            for index in range(n)
        ],
        "color": "rgba(250,204,21,0.10)",
        "type": "cloud",
        "overlay": True,
    })

if trade_mode:
    visible_trades = trades[-max_stored_trades:]
    for trade in visible_trades:
        end_bar = trade["exit_bar"] if trade["exit_bar"] is not None else n - 1
        achieved_bar = trade["highest_bar"] if trade["exit_bar"] is not None else end_bar
        achieved_price = trade["highest_price"] if trade["exit_bar"] is not None else trade["final_target"]
        reward_data = [None for _ in range(n)]
        risk_data = [None for _ in range(n)]
        entry_line = [None for _ in range(n)]
        stop_line = [None for _ in range(n)]
        for index in range(trade["entry_bar"], end_bar + 1):
            entry_line[index] = float(trade["entry"])
            if trade["stop_history"][index] is not None:
                stop_line[index] = float(trade["stop_history"][index])
            risk_data[index] = {
                "upper": float(max(trade["entry"], trade["stop"])),
                "lower": float(min(trade["entry"], trade["stop"])),
            }
        for index in range(trade["entry_bar"], achieved_bar + 1):
            reward_data[index] = {
                "upper": float(max(trade["entry"], achieved_price)),
                "lower": float(min(trade["entry"], achieved_price)),
            }
        trade_id = trade["id"]
        plots.append({"name": f"Trade {trade_id} Reward", "title": False, "data": reward_data, "color": "rgba(0,217,126,0.14)", "type": "cloud", "overlay": True})
        plots.append({"name": f"Trade {trade_id} Risk", "title": False, "data": risk_data, "color": "rgba(245,71,74,0.18)", "type": "cloud", "overlay": True})
        plots.append({"name": f"Trade {trade_id} Entry", "title": False, "data": entry_line, "color": "rgba(255,255,255,0.55)", "type": "line", "lineWidth": 1, "overlay": True})
        plots.append({"name": f"Trade {trade_id} Stop", "title": False, "data": stop_line, "color": "rgba(239,68,68,0.65)", "type": "line", "lineWidth": 1, "overlay": True})

def event_points(mask, values, text, color):
    return [
        {"value": float(values[index]), "text": text, "color": color} if mask[index] else None
        for index in range(n)
    ]

signals = [
    {"type": "buy", "name": "Confirmed Pullback Buy", "text": "BUY", "color": bullish_color, "data": event_points(fire_buy, low, "BUY", bullish_color), "renderMode": "events"},
    {"type": "sell", "name": "Confirmed Pullback Sell", "text": "SELL", "color": bearish_color, "data": event_points(fire_sell, high, "SELL", bearish_color), "renderMode": "events"},
    {"type": "buy", "name": "Buy Entry", "text": "▲", "color": bullish_color, "data": event_points(entry_buy, low, "▲", bullish_color), "renderMode": "events"},
    {"type": "sell", "name": "Sell Entry", "text": "▼", "color": bearish_color, "data": event_points(entry_sell, high, "▼", bearish_color), "renderMode": "events"},
    {"type": "buy", "name": "TP1 Reached", "text": "TP1", "color": "#00D97E", "data": event_points(tp1_hits, high, "TP1", "#00D97E"), "renderMode": "events"},
    {"type": "buy", "name": "TP2 Reached", "text": "TP2", "color": "#00D97E", "data": event_points(tp2_hits, high, "TP2", "#00D97E"), "renderMode": "events"},
    {"type": "sell", "name": "Trade Stop", "text": "SL", "color": "#EF4444", "data": event_points(exit_stop, low, "SL", "#EF4444"), "renderMode": "events"},
    {"type": "buy", "name": "Trade Win", "text": "WIN", "color": "#10B981", "data": event_points(exit_win, high, "WIN", "#10B981"), "renderMode": "events"},
]
if show_expiry_labels:
    signals.append({"type": "sell", "name": "Trade Timeout", "text": "TIMEOUT", "color": "#9CA3AF", "data": event_points(exit_timeout, high, "TIMEOUT", "#9CA3AF"), "renderMode": "events"})

last_rsi = float(rsi_values[-1]) if n and np.isfinite(rsi_values[-1]) else np.nan
zone_text = "BULLISH" if np.isfinite(last_rsi) and last_rsi > rsi_bullish else "BEARISH" if np.isfinite(last_rsi) and last_rsi < rsi_bearish else "SIDEWAY"
zone_color = bullish_color if zone_text == "BULLISH" else bearish_color if zone_text == "BEARISH" else neutral_color
table_rows = [
    [{"text": "RSI", "color": "#9CA3AF"}, {"text": "—" if not np.isfinite(last_rsi) else f"{last_rsi:.1f}", "color": zone_color}],
    [{"text": "Momentum Zone", "color": "#9CA3AF"}, {"text": zone_text, "color": zone_color}],
]
if trade_mode:
    denominator = total_wins + total_losses
    win_rate = total_wins / denominator * 100.0 if denominator > 0 else 0.0
    profit_factor = total_profit_r / total_loss_r if total_loss_r > 0 else 999.0 if total_profit_r > 0 else 0.0
    table_rows.append([{"text": "Trades", "color": "#9CA3AF"}, {"text": str(total_trades), "color": "#FFFFFF"}])
    table_rows.append([{"text": "Win Rate / PF", "color": "#9CA3AF"}, {"text": f"{win_rate:.1f}% / {profit_factor:.2f}", "color": "#10B981" if win_rate >= 50 else "#EF4444"}])

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "tables": [{"title": "3-WAY BOLL TREND", "position": dashboard_position, "rows": table_rows, "compact": True}],
}
