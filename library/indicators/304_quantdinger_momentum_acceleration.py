# ============================================================
# QuantDinger Momentum Acceleration
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Momentum Acceleration"
my_indicator_description = "Price or OBV momentum acceleration with chart projection, delayed confirmation alerts and an optional visual trade simulator."

# @param data_source str Price Acceleration source values=Price,On Balance Volume
# @param acceleration_period int 13 Acceleration period range=1:500:1
# @param bullish_color str rgba(0,255,255,0.35) Bullish color
# @param bearish_color str rgba(255,165,0,0.35) Bearish color
# @param display_length int 233 Recent arrow count range=1:2000:1
# @param show_price_projection bool false Show price acceleration projection
# @param price_projection_color str #00FFFF Price projection color
# @param show_obv_projection bool false Show OBV acceleration projection
# @param obv_projection_color str #FFA500 OBV projection color
# @param projection_scale int 7 Projection scale range=1:10:1
# @param projection_offset int 3 Projection vertical offset range=-3:10:1
# @param projection_placement str Bottom Projection placement values=Top,Bottom
# @param enable_backtest bool false Enable visual backtest
# @param trade_mode str Both Trade mode values=Long Only,Short Only,Both
# @param reverse_on_signal bool true Reverse on opposite signal
# @param initial_capital float 1000.0 Initial capital range=1:100000000:1
# @param backtest_years float 1.0 Backtest years range=0:100:0.1
# @param apply_stop_loss bool false Apply stop loss
# @param stop_loss_percent float 1.0 Stop loss percent range=0:100:0.1
# @param long_candle_confirmation bool false Confirm long with bullish candle
# @param short_candle_confirmation bool false Confirm short with bearish candle
# @param avoid_same_bar_conflicts bool true Avoid simultaneous directions
# @param next_bar_mode bool false Earlier repainting simulation mode
# @param show_entry_exit_labels bool true Show simulator events
# @param show_early_warnings bool true Show early warnings
# @param show_trade_statistics bool true Show simulator statistics

data_source = str(params.get("data_source", "Price"))
if data_source not in ("Price", "On Balance Volume"):
    data_source = "Price"
acceleration_period = min(500, max(1, int(params.get("acceleration_period", 13))))
bullish_color = str(params.get("bullish_color", "rgba(0,255,255,0.35)"))
bearish_color = str(params.get("bearish_color", "rgba(255,165,0,0.35)"))
display_length = min(2000, max(1, int(params.get("display_length", 233))))
show_price_projection = bool(params.get("show_price_projection", False))
price_projection_color = str(params.get("price_projection_color", "#00FFFF"))
show_obv_projection = bool(params.get("show_obv_projection", False))
obv_projection_color = str(params.get("obv_projection_color", "#FFA500"))
projection_scale = min(10, max(1, int(params.get("projection_scale", 7))))
projection_offset = min(10, max(-3, int(params.get("projection_offset", 3))))
projection_placement = str(params.get("projection_placement", "Bottom"))
if projection_placement not in ("Top", "Bottom"):
    projection_placement = "Bottom"
enable_backtest = bool(params.get("enable_backtest", False))
trade_mode = str(params.get("trade_mode", "Both"))
if trade_mode not in ("Long Only", "Short Only", "Both"):
    trade_mode = "Both"
reverse_on_signal = bool(params.get("reverse_on_signal", True))
initial_capital = max(1.0, float(params.get("initial_capital", 1000.0)))
backtest_years = min(100.0, max(0.0, float(params.get("backtest_years", 1.0))))
apply_stop_loss = bool(params.get("apply_stop_loss", False))
stop_loss_percent = min(100.0, max(0.0, float(params.get("stop_loss_percent", 1.0)))) / 100.0
long_candle_confirmation = bool(params.get("long_candle_confirmation", False))
short_candle_confirmation = bool(params.get("short_candle_confirmation", False))
avoid_same_bar_conflicts = bool(params.get("avoid_same_bar_conflicts", True))
next_bar_mode = bool(params.get("next_bar_mode", False))
show_entry_exit_labels = bool(params.get("show_entry_exit_labels", True))
show_early_warnings = bool(params.get("show_early_warnings", True))
show_trade_statistics = bool(params.get("show_trade_statistics", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _seconds(values):
    result = np.asarray(values, dtype=float).copy()
    if len(result) and max(abs(float(value)) for value in result) > 10_000_000_000:
        result /= 1000.0
    return result


def _obv(close_values, volume_values):
    result = np.zeros(len(close_values), dtype=float)
    for index in range(1, len(close_values)):
        signed = volume_values[index] if close_values[index] > close_values[index - 1] else -volume_values[index] if close_values[index] < close_values[index - 1] else 0.0
        result[index] = result[index - 1] + signed
    return result


def _speedy(values, period):
    count = len(values)
    velocity_source = np.full(count, np.nan, dtype=float)
    velocity = np.full(count, np.nan, dtype=float)
    acceleration = np.full(count, np.nan, dtype=float)
    result = np.full(count, np.nan, dtype=float)
    for index in range(period, count):
        velocity_source[index] = (values[index] - values[index - period]) / period
    for index in range(period + 2, count):
        window = velocity_source[index - 2:index + 1]
        if np.all(np.isfinite(window)):
            velocity[index] = float(np.mean(window))
    for index in range(2 * period + 2, count):
        if np.isfinite(velocity[index]) and np.isfinite(velocity[index - period]):
            acceleration[index] = (velocity[index] - velocity[index - period]) / period
            result[index] = velocity[index] - acceleration[index]
    return velocity, acceleration, result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close", "volume"]).reset_index(drop=True)
times = _seconds(df["time"].to_numpy(dtype=float))
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].to_numpy(dtype=float)
n = len(df)
obv_values = _obv(close, volume)
price_velocity, price_acceleration, price_oscillator = _speedy(close, acceleration_period)
obv_velocity, obv_acceleration, obv_oscillator = _speedy(obv_values, acceleration_period)
oscillator = price_oscillator if data_source == "Price" else obv_oscillator

long_cross = np.zeros(n, dtype=bool)
short_cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(oscillator[index]) and np.isfinite(oscillator[index - 1]):
        long_cross[index] = oscillator[index] > 0.0 and oscillator[index - 1] <= 0.0
        short_cross[index] = oscillator[index] < 0.0 and oscillator[index - 1] >= 0.0
any_cross = long_cross | short_cross

lookback = min(max(n - 1, 0), display_length)
visible_start = max(0, n - display_length)
positive_arrows = [float(low[index]) if index >= visible_start and np.isfinite(oscillator[index]) and oscillator[index] > 0.0 else None for index in range(n)]
negative_arrows = [float(high[index]) if index >= visible_start and np.isfinite(oscillator[index]) and oscillator[index] < 0.0 else None for index in range(n)]

projected_price = np.full(n, np.nan, dtype=float)
projected_obv = np.full(n, np.nan, dtype=float)
projection_mid = np.nan
projection_valid = False
if lookback > 0 and (show_price_projection or show_obv_projection):
    start = n - lookback
    price_highest = float(np.max(high[start:n]))
    price_lowest = float(np.min(low[start:n]))
    if price_highest != 0.0:
        change_rate = (price_highest - price_lowest) / price_highest
        offset = projection_offset / 10.0
        adjusted_low = price_lowest * (1.0 - change_rate * offset)
        adjusted_high = price_highest * (1.0 + change_rate * offset)
        anchor = adjusted_high if projection_placement == "Top" else adjusted_low
        height_factor = change_rate / (11 - projection_scale)
        price_window = price_oscillator[start:n]
        obv_window = obv_oscillator[start:n]
        price_maximum = float(np.max(price_window[np.isfinite(price_window)])) if np.any(np.isfinite(price_window)) else np.nan
        obv_maximum = float(np.max(obv_window[np.isfinite(obv_window)])) if np.any(np.isfinite(obv_window)) else np.nan
        if show_price_projection and np.isfinite(price_maximum) and price_maximum != 0.0:
            valid = np.isfinite(price_oscillator[start - 1:n])
            values = anchor * (1.0 + price_oscillator[start - 1:n] / price_maximum * height_factor)
            projected_price[start - 1:n] = np.where(valid, values, np.nan)
            projection_valid = True
        if show_obv_projection and np.isfinite(obv_maximum) and obv_maximum != 0.0:
            valid = np.isfinite(obv_oscillator[start - 1:n])
            values = anchor * (1.0 + obv_oscillator[start - 1:n] / obv_maximum * height_factor)
            projected_obv[start - 1:n] = np.where(valid, values, np.nan)
            projection_valid = True
        projection_mid = anchor

direction = np.zeros(n, dtype=int)
entry_price = np.zeros(n, dtype=float)
exit_price = np.zeros(n, dtype=float)
capital = np.full(n, initial_capital, dtype=float)
trade_count = np.zeros(n, dtype=int)
win_long = np.zeros(n, dtype=int)
loss_long = np.zeros(n, dtype=int)
win_short = np.zeros(n, dtype=int)
loss_short = np.zeros(n, dtype=int)
long_condition = np.zeros(n, dtype=bool)
short_condition = np.zeros(n, dtype=bool)
stop_hit = np.zeros(n, dtype=bool)
long_entry = np.zeros(n, dtype=bool)
short_entry = np.zeros(n, dtype=bool)
long_exit = np.zeros(n, dtype=bool)
short_exit = np.zeros(n, dtype=bool)
stop_exit = np.zeros(n, dtype=bool)
entry_exit_line = np.full(n, np.nan, dtype=float)

reference_time = times[-1] if n else 0.0
start_backtest = times > reference_time - backtest_years * 31_556_952.0 if n else np.zeros(0, dtype=bool)
current_direction = 0
current_entry = 0.0
last_exit = 0.0
current_capital = initial_capital
trades = 0
wins_l = losses_l = wins_s = losses_s = 0

for index in range(n):
    if enable_backtest:
        signal_lag = 1 if next_bar_mode else 2
        candle_lag = 0 if next_bar_mode else 1
        signal_index = index - signal_lag
        candle_index = index - candle_lag
        signal_long = signal_index >= 0 and long_cross[signal_index]
        signal_short = signal_index >= 0 and short_cross[signal_index]
        conflict = candle_index >= 0 and long_cross[candle_index] and short_cross[candle_index]
        okay_long = candle_index >= 0 and (not long_candle_confirmation or close[candle_index] > opened[candle_index]) and (not avoid_same_bar_conflicts or not conflict)
        okay_short = candle_index >= 0 and (not short_candle_confirmation or close[candle_index] < opened[candle_index]) and (not avoid_same_bar_conflicts or not conflict)
        long_condition[index] = signal_long and okay_long
        short_condition[index] = signal_short and okay_short
        can_long = trade_mode != "Short Only"
        can_short = trade_mode != "Long Only"
        stop_long = apply_stop_loss and current_direction == 1 and low[index] <= current_entry * (1.0 - stop_loss_percent)
        stop_short = apply_stop_loss and current_direction == -1 and high[index] >= current_entry * (1.0 + stop_loss_percent)
        stop_hit[index] = stop_long or stop_short
        if start_backtest[index]:
            reversed_trade = False
            if reverse_on_signal and trade_mode == "Both":
                reverse_direction = -1 if current_direction == 1 and short_condition[index] else 1 if current_direction == -1 and long_condition[index] else 0
                if reverse_direction:
                    old_direction = current_direction
                    realized_exit = opened[index]
                    ratio = realized_exit / max(current_entry, 1e-12) if old_direction == 1 else max(current_entry, 1e-12) / max(realized_exit, 1e-12)
                    current_capital *= ratio
                    profitable = ratio > 1.0
                    if old_direction == 1:
                        wins_l += int(profitable)
                        losses_l += int(not profitable)
                        long_exit[index] = True
                    else:
                        wins_s += int(profitable)
                        losses_s += int(not profitable)
                        short_exit[index] = True
                    last_exit = realized_exit
                    current_direction = reverse_direction
                    current_entry = opened[index]
                    trades += 1
                    long_entry[index] = reverse_direction == 1
                    short_entry[index] = reverse_direction == -1
                    reversed_trade = True
            if not reversed_trade and current_direction != 0:
                opposite = (current_direction == 1 and short_condition[index]) or (current_direction == -1 and long_condition[index])
                if opposite or stop_hit[index]:
                    old_direction = current_direction
                    realized_exit = close[index] if stop_hit[index] else opened[index]
                    ratio = realized_exit / max(current_entry, 1e-12) if old_direction == 1 else max(current_entry, 1e-12) / max(realized_exit, 1e-12)
                    current_capital *= ratio
                    profitable = ratio > 1.0
                    if old_direction == 1:
                        wins_l += int(profitable)
                        losses_l += int(not profitable)
                        long_exit[index] = not stop_hit[index]
                    else:
                        wins_s += int(profitable)
                        losses_s += int(not profitable)
                        short_exit[index] = not stop_hit[index]
                    stop_exit[index] = stop_hit[index]
                    last_exit = realized_exit
                    current_direction = 0
            if current_direction == 0:
                if long_condition[index] and can_long:
                    current_entry = opened[index]
                    current_direction = 1
                    trades += 1
                    long_entry[index] = True
                if short_condition[index] and can_short and (not long_condition[index] or not can_long or not avoid_same_bar_conflicts):
                    current_entry = opened[index]
                    current_direction = -1
                    trades += 1
                    short_entry[index] = True
    direction[index] = current_direction
    entry_price[index] = current_entry
    exit_price[index] = last_exit
    capital[index] = current_capital
    trade_count[index] = trades
    win_long[index], loss_long[index], win_short[index], loss_short[index] = wins_l, losses_l, wins_s, losses_s
    if current_direction != 0:
        entry_exit_line[index] = current_entry
    elif last_exit > 0.0:
        entry_exit_line[index] = last_exit

estimated_capital = current_capital
if n and current_direction == 1 and current_entry > 0.0:
    estimated_capital = current_capital * close[-1] / current_entry
elif n and current_direction == -1 and close[-1] > 0.0:
    estimated_capital = current_capital * current_entry / close[-1]
closed_wins = wins_l + wins_s
closed_losses = losses_l + losses_s
win_rate = 100.0 * closed_wins / trades if trades else 0.0
gain_percent = (estimated_capital / initial_capital - 1.0) * 100.0

plots = [
    {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Entry Exit Price", "data": _finite(entry_exit_line) if enable_backtest else [None] * n, "color": "#808080", "type": "circle", "size": 3, "overlay": True},
    {"name": "Price Acceleration Projection", "data": _finite(projected_price), "color": price_projection_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "OBV Acceleration Projection", "data": _finite(projected_obv), "color": obv_projection_color, "type": "line", "lineWidth": 1, "overlay": True},
]

signals = [
    {"type": "buy", "name": "Bullish Cross", "text": "Bull", "data": [float(low[index]) if index >= visible_start and long_cross[index] else None for index in range(n)], "color": bullish_color, "renderMode": "events"},
    {"type": "sell", "name": "Bearish Cross", "text": "Bear", "data": [float(high[index]) if index >= visible_start and short_cross[index] else None for index in range(n)], "color": bearish_color, "renderMode": "events"},
]
if enable_backtest and show_entry_exit_labels:
    signals.extend([
        {"type": "buy", "name": "Simulator Long Entry", "text": "L", "data": [float(low[index]) if long_entry[index] else None for index in range(n)], "color": "#008080", "renderMode": "events"},
        {"type": "sell", "name": "Simulator Short Entry", "text": "S", "data": [float(high[index]) if short_entry[index] else None for index in range(n)], "color": "#FF0000", "renderMode": "events"},
        {"type": "sell", "name": "Simulator Take Profit", "text": "TP", "data": [float(high[index] if long_exit[index] else low[index]) if long_exit[index] or short_exit[index] else None for index in range(n)], "color": "#008080", "renderMode": "events"},
        {"type": "sell", "name": "Simulator Stop Loss", "text": "SL", "data": [float(close[index]) if stop_exit[index] else None for index in range(n)], "color": "#FF0000", "renderMode": "events"},
    ])
if enable_backtest and show_early_warnings:
    warning_lag = 1
    signals.extend([
        {"type": "buy", "name": "Long Early Warning", "text": "!", "data": [float(low[index]) if index >= warning_lag and long_cross[index - warning_lag] and direction[index - 1] != 1 else None for index in range(n)], "color": "#008080", "renderMode": "events"},
        {"type": "sell", "name": "Short Early Warning", "text": "!", "data": [float(high[index]) if index >= warning_lag and short_cross[index - warning_lag] and direction[index - 1] != -1 else None for index in range(n)], "color": "#FF0000", "renderMode": "events"},
    ])

layers = []
if projection_valid and np.isfinite(projection_mid) and n:
    layers.append({"type": "line", "role": "Projection Midline", "start": float(times[max(0, n - lookback)] * 1000.0), "end": float(times[-1] * 1000.0), "y1": float(projection_mid), "y2": float(projection_mid), "color": "rgba(128,128,128,0.75)", "style": "dashed", "lineWidth": 1})

tables = [{"title": "Momentum Acceleration", "position": "bottom-right", "compact": True, "rows": [[{"text": "☼☾", "color": "#008080"}, {"text": data_source, "color": "#FFFFFF"}]]}]
if enable_backtest and show_trade_statistics:
    status = "Long" if current_direction == 1 else "Short" if current_direction == -1 else "Flat"
    tables.append({"title": "Visual Trade Simulation", "position": "top-right", "compact": True, "rows": [
        [{"text": "Mode / status", "color": "#BDBDBD"}, {"text": f"{trade_mode} / {status}", "color": "#FFFFFF"}],
        [{"text": "Trades", "color": "#BDBDBD"}, {"text": str(trades), "color": "#FFFFFF"}],
        [{"text": "Wins / losses", "color": "#BDBDBD"}, {"text": f"{closed_wins} / {closed_losses}", "color": "#FFFFFF"}],
        [{"text": "Long W/L", "color": "#BDBDBD"}, {"text": f"{wins_l} / {losses_l}", "color": "#FFFFFF"}],
        [{"text": "Short W/L", "color": "#BDBDBD"}, {"text": f"{wins_s} / {losses_s}", "color": "#FFFFFF"}],
        [{"text": "Win rate", "color": "#BDBDBD"}, {"text": f"{win_rate:.1f}%", "color": "#FFFFFF"}],
        [{"text": "Capital / gain", "color": "#BDBDBD"}, {"text": f"{estimated_capital:.2f} / {gain_percent:.2f}%", "color": "#FFFFFF"}],
        [{"text": "Visual simulation only · no costs", "color": "#9E9E9E"}, {"text": "", "color": "#9E9E9E"}],
    ]})

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
        "obv": _finite(obv_values), "priceVelocity": _finite(price_velocity), "priceAcceleration": _finite(price_acceleration), "priceOscillator": _finite(price_oscillator),
        "obvVelocity": _finite(obv_velocity), "obvAcceleration": _finite(obv_acceleration), "obvOscillator": _finite(obv_oscillator), "oscillator": _finite(oscillator),
        "longCross": long_cross.tolist(), "shortCross": short_cross.tolist(), "anyCross": any_cross.tolist(),
        "projectedPrice": _finite(projected_price), "projectedOBV": _finite(projected_obv), "projectionMidline": float(projection_mid) if np.isfinite(projection_mid) else None,
        "startBacktest": start_backtest.tolist(), "longCondition": long_condition.tolist(), "shortCondition": short_condition.tolist(), "stopHit": stop_hit.tolist(),
        "direction": direction.tolist(), "entryPrice": _finite(entry_price), "exitPrice": _finite(exit_price), "capital": _finite(capital), "tradeCount": trade_count.tolist(),
        "winLong": win_long.tolist(), "lossLong": loss_long.tolist(), "winShort": win_short.tolist(), "lossShort": loss_short.tolist(),
        "longEntry": long_entry.tolist(), "shortEntry": short_entry.tolist(), "longExit": long_exit.tolist(), "shortExit": short_exit.tolist(), "stopExit": stop_exit.tolist(),
        "entryExitLine": _finite(entry_exit_line), "estimatedCapital": float(estimated_capital), "winRate": float(win_rate), "gainPercent": float(gain_percent),
        "dataSource": data_source, "displayLength": int(display_length), "projectionLookback": int(lookback), "projectionValid": bool(projection_valid),
        "tradeMode": trade_mode, "nextBarMode": bool(next_bar_mode), "backtestEnabled": bool(enable_backtest),
    },
}
