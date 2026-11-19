# ============================================================
# QuantDinger Logistic Oscillator Suite
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Logistic Oscillator Suite"
my_indicator_description = "Applies a 30-step logistic transform to five oscillator families with an optional deterministic trade simulator."

# @param indicator_mode str Logistic Dominance Base oscillator values=Awesome Oscillator (AO),Logistic Dominance,Rate of Change (ROC),Relative Strength Index (RSI),Stochastic (STOCH)
# @param source str Close Source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param logistic_length int 13 Logistic normalization length range=1:1000:1
# @param dominance_length int 5 Logistic dominance length range=1:1000:1
# @param roc_length int 9 Rate-of-change length range=1:1000:1
# @param rsi_length int 14 RSI length range=1:1000:1
# @param stochastic_length int 14 Stochastic length range=1:1000:1
# @param style str Cross Iteration display style values=Cross,Line,StepLine
# @param bullish_color color #26C6DA Bullish color
# @param bearish_color color #FF5252 Bearish color
# @param standard_deviation_only bool false Show only aggregate standard deviation
# @param enable_backtest bool false Enable deterministic trade simulation
# @param trade_mode str Both Simulated directions values=Long Only,Short Only,Both
# @param reverse_on_signal bool true Reverse on an opposite signal
# @param initial_capital float 1000 Initial capital range=0.01:1000000000:1
# @param period_years float 1 Backtest window in years range=0:100:0.1
# @param apply_stop_loss bool false Apply intrabar stop loss
# @param stop_loss_percent float 1 Stop-loss percent range=0:100:0.1
# @param long_candle_confirmation bool false Require a bullish reference candle
# @param short_candle_confirmation bool false Require a bearish reference candle
# @param avoid_same_bar_conflicts bool true Avoid conflicting entries
# @param next_bar_mode bool false Use repainting next-bar simulation mode
# @param show_trade_labels bool true Show simulated entry and exit labels
# @param show_early_warnings bool true Show early-warning events
# @param show_trade_statistics bool true Show trade statistics table

indicator_mode = str(params.get("indicator_mode", "Logistic" + " Dominance"))
source_name = str(params.get("source", "Close"))
logistic_length = min(1000, max(1, int(params.get("logistic_length", 13))))
dominance_length = min(1000, max(1, int(params.get("dominance_length", 5))))
roc_length = min(1000, max(1, int(params.get("roc_length", 9))))
rsi_length = min(1000, max(1, int(params.get("rsi_length", 14))))
stochastic_length = min(1000, max(1, int(params.get("stochastic_length", 14))))
display_style = str(params.get("style", "Cross"))
bullish_color = str(params.get("bullish_color", "#26C6DA"))
bearish_color = str(params.get("bearish_color", "#FF5252"))
standard_deviation_only = bool(params.get("standard_deviation_only", False))
enable_backtest = bool(params.get("enable_backtest", False))
trade_mode = str(params.get("trade_mode", "Both"))
reverse_on_signal = bool(params.get("reverse_on_signal", True))
initial_capital = max(0.01, float(params.get("initial_capital", 1000.0)))
period_years = min(100.0, max(0.0, float(params.get("period_years", 1.0))))
apply_stop_loss = bool(params.get("apply_stop_loss", False))
stop_loss = min(1.0, max(0.0, float(params.get("stop_loss_percent", 1.0)) / 100.0))
long_candle_confirmation = bool(params.get("long_candle_confirmation", False))
short_candle_confirmation = bool(params.get("short_candle_confirmation", False))
avoid_same_bar_conflicts = bool(params.get("avoid_same_bar_conflicts", True))
next_bar_mode = bool(params.get("next_bar_mode", False))
show_trade_labels = bool(params.get("show_trade_labels", True))
show_early_warnings = bool(params.get("show_early_warnings", True))
show_trade_statistics = bool(params.get("show_trade_statistics", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].fillna(0.0).astype(float).to_numpy(dtype=float)
n = len(df)
times = df["time"].to_numpy(dtype=float) if "time" in df.columns else np.arange(n, dtype=float) * 3600000.0
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0

source_lookup = {
    "Open": open_price, "High": high, "Low": low, "HL2": (high + low) * 0.5,
    "HLC3": (high + low + close) / 3.0, "OHLC4": (open_price + high + low + close) / 4.0,
}
source = source_lookup.get(source_name, close)


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if length == 1:
        return values.astype(float).copy()
    sums = np.cumsum(np.insert(values.astype(float), 0, 0.0))
    result[length - 1:] = (sums[length:] - sums[:-length]) / length
    return result


def _rolling_high(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.max(window))
    return result


def _rolling_low(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.min(window))
    return result


def _change_ratio(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) > length:
        denominator = values[:-length]
        numerator = values[length:] - denominator
        valid = np.isfinite(numerator) & np.isfinite(denominator) & (denominator != 0.0)
        target = result[length:]
        target[valid] = numerator[valid] / denominator[valid]
    return result


def _rsi(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= length:
        return result
    changes = np.diff(values)
    gains = np.maximum(changes, 0.0)
    losses = np.maximum(-changes, 0.0)
    average_gain = float(np.mean(gains[:length]))
    average_loss = float(np.mean(losses[:length]))
    for index in range(length, len(values)):
        if index > length:
            average_gain = (average_gain * (length - 1.0) + gains[index - 1]) / length
            average_loss = (average_loss * (length - 1.0) + losses[index - 1]) / length
        if average_loss == 0.0:
            result[index] = 100.0 if average_gain > 0.0 else 50.0
        elif average_gain == 0.0:
            result[index] = 0.0
        else:
            result[index] = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


highest_for_logistic = _rolling_high(high, logistic_length)
if indicator_mode == "Awesome Oscillator (AO)":
    midpoint = (high + low) * 0.5
    short_average = _sma(midpoint, 5)
    long_average = _sma(midpoint, 34)
    base_oscillator = np.full(n, np.nan, dtype=float)
    valid = np.isfinite(short_average) & np.isfinite(long_average) & (long_average != 0.0)
    base_oscillator[valid] = short_average[valid] / long_average[valid] - 1.0
elif indicator_mode == "Rate of Change (ROC)":
    base_oscillator = _change_ratio(source, roc_length)
elif indicator_mode == "Relative Strength Index (RSI)":
    base_oscillator = _rsi(source, rsi_length) / 100.0 - 0.5
elif indicator_mode == "Stochastic (STOCH)":
    stochastic_high = _rolling_high(high, stochastic_length)
    stochastic_low = _rolling_low(low, stochastic_length)
    base_oscillator = np.full(n, np.nan, dtype=float)
    stochastic_range = stochastic_high - stochastic_low
    valid = np.isfinite(source) & np.isfinite(stochastic_range) & (stochastic_range != 0.0)
    base_oscillator[valid] = (source[valid] - stochastic_low[valid]) / stochastic_range[valid] - 0.5
else:
    dominance_rate = _change_ratio(source, dominance_length)
    dominance_high = _rolling_high(high, dominance_length)
    base_oscillator = np.full(n, np.nan, dtype=float)
    valid = np.isfinite(dominance_rate) & np.isfinite(dominance_high) & (dominance_high != 0.0)
    negative_ratio = -source[valid] / dominance_high[valid]
    positive_ratio = source[valid] / dominance_high[valid]
    negative_map = dominance_rate[valid] * negative_ratio * (1.0 - negative_ratio)
    positive_map = dominance_rate[valid] * positive_ratio * (1.0 - positive_ratio)
    base_oscillator[valid] = -negative_map - positive_map

map_equation = np.full(n, np.nan, dtype=float)
valid_map = np.isfinite(source) & np.isfinite(base_oscillator) & np.isfinite(highest_for_logistic) & (highest_for_logistic != 0.0)
normalized_source = np.zeros(n, dtype=float)
normalized_source[valid_map] = source[valid_map] / highest_for_logistic[valid_map]
map_equation[valid_map] = base_oscillator[valid_map] * normalized_source[valid_map] * (1.0 - normalized_source[valid_map])

iterations = np.full((30, n), np.nan, dtype=float)
iterations[0] = map_equation
for iteration in range(1, 30):
    lag = iteration - 1
    if lag == 0:
        historical_map = map_equation
        valid_history = np.isfinite(historical_map) & np.isfinite(base_oscillator)
        iterations[iteration, valid_history] = base_oscillator[valid_history] * np.abs(historical_map[valid_history]) * (1.0 - historical_map[valid_history])
    elif n > lag:
        historical_map = map_equation[:-lag]
        oscillator_slice = base_oscillator[lag:]
        valid_history = np.isfinite(historical_map) & np.isfinite(oscillator_slice)
        target = iterations[iteration, lag:]
        target[valid_history] = oscillator_slice[valid_history] * np.abs(historical_map[valid_history]) * (1.0 - historical_map[valid_history])

last_iteration = np.full(n, np.nan, dtype=float)
if n > 29:
    historical_map = map_equation[:-29]
    oscillator_slice = base_oscillator[29:]
    valid_history = np.isfinite(historical_map) & np.isfinite(oscillator_slice)
    target = last_iteration[29:]
    target[valid_history] = oscillator_slice[valid_history] * np.abs(historical_map[valid_history]) * (1.0 - historical_map[valid_history])

array_average = np.full(n, np.nan, dtype=float)
array_deviation = np.full(n, np.nan, dtype=float)
aggregate = np.full(n, np.nan, dtype=float)
for index in range(n):
    values = iterations[1:30, index]
    if np.all(np.isfinite(values)):
        array_average[index] = float(np.mean(values))
        array_deviation[index] = float(np.std(values, ddof=0))
        aggregate[index] = float(np.sign(array_average[index]) * array_deviation[index])

long_alert = np.zeros(n, dtype=bool)
short_alert = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(aggregate[index]) and np.isfinite(aggregate[index - 1]):
        long_alert[index] = aggregate[index] > 0.0 and aggregate[index - 1] <= 0.0
        short_alert[index] = aggregate[index] < 0.0 and aggregate[index - 1] >= 0.0

plot_style = "cross" if display_style == "Cross" else "step" if display_style == "StepLine" else "line"
selected_iterations = [1, 5, 10, 15, 20, 25, 28]
iteration_palette = ["#26C6DA", "#42A5F5", "#7E57C2", "#AB47BC", "#FF5252", "#FF8A65", "#FFA726"]
plots = []
if standard_deviation_only:
    hidden = [None] * n
    plots.append({"name": "Last Iteration", "title": False, "data": hidden, "color": bullish_color, "type": "line", "style": plot_style, "lineWidth": 2, "overlay": False})
    for iteration in selected_iterations:
        plots.append({"name": f"Iteration {iteration + 1}", "title": False, "data": hidden, "color": bullish_color, "type": "line", "style": plot_style, "lineWidth": 2, "overlay": False})
else:
    plots.append({"name": "Last Iteration", "title": False, "data": _finite(last_iteration), "color": "#FFFFFF", "type": "line", "style": plot_style, "lineWidth": 2, "overlay": False})
    for palette_index, iteration in enumerate(selected_iterations):
        values = iterations[iteration]
        plots.append({"name": f"Iteration {iteration + 1}", "title": False, "data": _finite(values), "color": iteration_palette[palette_index], "type": "line", "style": plot_style, "lineWidth": 2, "overlay": False})
plots.append({"name": "Logistic Aggregate Deviation", "data": _finite(aggregate), "color": "#FFD54F", "type": "line", "lineWidth": 3, "overlay": False})

direction = np.zeros(n, dtype=int)
entry_price_history = np.full(n, np.nan, dtype=float)
exit_price_history = np.full(n, np.nan, dtype=float)
capital_history = np.full(n, initial_capital, dtype=float)
trade_count_history = np.zeros(n, dtype=int)
win_long_history = np.zeros(n, dtype=int)
loss_long_history = np.zeros(n, dtype=int)
win_short_history = np.zeros(n, dtype=int)
loss_short_history = np.zeros(n, dtype=int)
entry_exit_line = np.full(n, np.nan, dtype=float)
background_colors = [None] * n
entry_long = np.zeros(n, dtype=bool)
entry_short = np.zeros(n, dtype=bool)
exit_take_profit = np.zeros(n, dtype=bool)
exit_stop = np.zeros(n, dtype=bool)
early_long = np.zeros(n, dtype=bool)
early_short = np.zeros(n, dtype=bool)

current_direction = 0
current_entry = 0.0
current_exit = 0.0
capital = initial_capital
trade_count = win_long = loss_long = win_short = loss_short = 0
cutoff = float(times[-1] - period_years * 31556952000.0) if n else 0.0
can_long = trade_mode != "Short Only"
can_short = trade_mode != "Long Only"


def _close_trade(index, stop_hit):
    global current_direction, current_exit, capital, win_long, loss_long, win_short, loss_short
    previous_direction = current_direction
    current_exit = float(close[index] if stop_hit else open_price[index])
    safe_entry = max(current_entry, 1e-12)
    safe_exit = max(current_exit, 1e-12)
    multiplier = safe_exit / safe_entry if previous_direction == 1 else safe_entry / safe_exit
    capital *= multiplier
    profitable = multiplier > 1.0
    if previous_direction == 1:
        win_long += 1 if profitable else 0
        loss_long += 0 if profitable else 1
    else:
        win_short += 1 if profitable else 0
        loss_short += 0 if profitable else 1
    exit_stop[index] = bool(stop_hit)
    exit_take_profit[index] = not stop_hit
    current_direction = 0


def _open_trade(index, new_direction):
    global current_direction, current_entry, trade_count
    current_entry = float(open_price[index])
    current_direction = int(new_direction)
    trade_count += 1
    if new_direction == 1:
        entry_long[index] = True
    else:
        entry_short[index] = True


for index in range(n):
    if enable_backtest:
        signal_offset = 1 if next_bar_mode else 2
        signal_index = index - signal_offset
        reference_index = index if next_bar_mode else index - 1
        long_condition_signal = bool(signal_index >= 0 and long_alert[signal_index])
        short_condition_signal = bool(signal_index >= 0 and short_alert[signal_index])
        conflict = bool(reference_index >= 0 and long_alert[reference_index] and short_alert[reference_index])
        long_ok = bool(reference_index >= 0 and (not long_candle_confirmation or close[reference_index] > open_price[reference_index]) and (not avoid_same_bar_conflicts or not conflict))
        short_ok = bool(reference_index >= 0 and (not short_candle_confirmation or close[reference_index] < open_price[reference_index]) and (not avoid_same_bar_conflicts or not conflict))
        long_condition = long_condition_signal and long_ok
        short_condition = short_condition_signal and short_ok
        stop_long = apply_stop_loss and current_direction == 1 and low[index] <= current_entry * (1.0 - stop_loss)
        stop_short = apply_stop_loss and current_direction == -1 and high[index] >= current_entry * (1.0 + stop_loss)
        stop_hit = stop_long or stop_short
        if times[index] > cutoff:
            reversed_trade = False
            if reverse_on_signal and trade_mode == "Both":
                if current_direction == 1 and short_condition:
                    _close_trade(index, False)
                    if can_short:
                        _open_trade(index, -1)
                    reversed_trade = True
                elif current_direction == -1 and long_condition:
                    _close_trade(index, False)
                    if can_long:
                        _open_trade(index, 1)
                    reversed_trade = True
            if not reversed_trade:
                should_exit_long = current_direction == 1 and (short_condition or stop_hit)
                should_exit_short = current_direction == -1 and (long_condition or stop_hit)
                if should_exit_long or should_exit_short:
                    _close_trade(index, stop_hit)
            if current_direction == 0:
                if long_condition and can_long:
                    _open_trade(index, 1)
                if short_condition and can_short:
                    _open_trade(index, -1)
        if show_early_warnings:
            early_long[index] = bool(can_long and current_direction != 1 and index >= 1 and long_alert[index - 1] and not short_alert[index])
            early_short[index] = bool(can_short and current_direction != -1 and index >= 1 and short_alert[index - 1] and not long_alert[index])
    direction[index] = current_direction
    entry_price_history[index] = current_entry if current_entry > 0.0 else np.nan
    exit_price_history[index] = current_exit if current_exit > 0.0 else np.nan
    capital_history[index] = capital
    trade_count_history[index] = trade_count
    win_long_history[index] = win_long; loss_long_history[index] = loss_long
    win_short_history[index] = win_short; loss_short_history[index] = loss_short
    entry_exit_line[index] = current_entry if current_direction != 0 else current_exit if current_exit > 0.0 else np.nan
    if enable_backtest and times[index] > cutoff:
        background_colors[index] = "rgba(0,128,128,0.080)" if current_direction == 1 else "rgba(255,0,0,0.080)" if current_direction == -1 else None

plots.append({"name": "Entry / Exit Price", "data": _finite(entry_exit_line), "color": [bullish_color if value == 1 else bearish_color if value == -1 else "#808080" for value in direction], "type": "circles", "lineWidth": 1, "overlay": True})

signals = [
    {"type": "buy", "name": "Long Early Warning", "text": "⚠", "data": [float(low[index]) if early_long[index] else None for index in range(n)], "color": bullish_color, "renderMode": "events", "visible": show_early_warnings},
    {"type": "sell", "name": "Short Early Warning", "text": "⚠", "data": [float(high[index]) if early_short[index] else None for index in range(n)], "color": bearish_color, "renderMode": "events", "visible": show_early_warnings},
    {"type": "buy", "name": "Long Entry", "text": "L", "data": [float(low[index]) if entry_long[index] and show_trade_labels else None for index in range(n)], "color": bullish_color, "renderMode": "events", "overlay": True},
    {"type": "sell", "name": "Short Entry", "text": "S", "data": [float(high[index]) if entry_short[index] and show_trade_labels else None for index in range(n)], "color": bearish_color, "renderMode": "events", "overlay": True},
    {"type": "sell", "name": "Trade Exit", "text": "TP", "data": [float(high[index]) if exit_take_profit[index] and show_trade_labels else None for index in range(n)], "color": "#808080", "renderMode": "events", "overlay": True},
    {"type": "sell", "name": "Stop Loss", "text": "SL", "data": [float(close[index]) if exit_stop[index] and show_trade_labels else None for index in range(n)], "color": bearish_color, "renderMode": "events", "overlay": True},
]

estimated_capital = capital
if n and current_direction == 1 and current_entry > 0.0:
    estimated_capital = capital * close[-1] / current_entry
elif n and current_direction == -1 and close[-1] > 0.0:
    estimated_capital = capital * current_entry / close[-1]
total_wins = win_long + win_short
total_losses = loss_long + loss_short
win_rate = total_wins / trade_count * 100.0 if trade_count else 0.0
gain_percent = (estimated_capital / initial_capital - 1.0) * 100.0
tables = [{"title": "☼☾", "position": "bottom-right", "rows": [["Model", "Logistic Map"]], "compact": True}]
if enable_backtest and show_trade_statistics:
    tables.append({
        "title": "Trade Statistics", "position": "top-right", "compact": True,
        "rows": [
            ["Mode", trade_mode], ["Status", "Long" if current_direction == 1 else "Short" if current_direction == -1 else "Flat"],
            ["Trades", str(trade_count)], ["Win rate", f"{win_rate:.1f}%"], ["Win / Loss", f"{total_wins} / {total_losses}"],
            ["Long W/L", f"{win_long} / {loss_long}"], ["Short W/L", f"{win_short} / {loss_short}"],
            ["Gain / Loss", f"{gain_percent:.2f}%"], ["Capital", f"{initial_capital:.2f} / {estimated_capital:.2f}"],
        ],
    })

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": [], "tables": tables,
    "bar_colors": [None] * n, "background_colors": background_colors,
    "calculatedVars": {
        "source": _finite(source), "baseOscillator": _finite(base_oscillator), "highestForLogistic": _finite(highest_for_logistic),
        "mapEquation": _finite(map_equation), "iterations": [_finite(values) for values in iterations], "lastIteration": _finite(last_iteration),
        "arrayAverage": _finite(array_average), "arrayDeviation": _finite(array_deviation), "aggregate": _finite(aggregate),
        "longAlert": long_alert.tolist(), "shortAlert": short_alert.tolist(), "direction": direction.tolist(),
        "entryPrice": _finite(entry_price_history), "exitPrice": _finite(exit_price_history), "capital": _finite(capital_history),
        "tradeCount": trade_count_history.tolist(), "winLong": win_long_history.tolist(), "lossLong": loss_long_history.tolist(),
        "winShort": win_short_history.tolist(), "lossShort": loss_short_history.tolist(), "entryLong": entry_long.tolist(),
        "entryShort": entry_short.tolist(), "exitTakeProfit": exit_take_profit.tolist(), "exitStop": exit_stop.tolist(),
        "earlyLong": early_long.tolist(), "earlyShort": early_short.tolist(), "cutoffTime": cutoff,
    },
}
