# ============================================================
# QuantDinger Signal Forge
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Signal Forge"
my_indicator_description = "Combines eleven configurable trend filters and tracks standalone plus combined ATR-managed signal performance."

# @param require_all bool true Require every enabled indicator to align
# @param atr_length int 14 ATR length range=1:500:1
# @param enable_take_profit bool false Enable take profit
# @param take_profit_multiplier float 2.0 Take-profit ATR multiplier range=0.1:20:0.1
# @param enable_stop_loss bool false Enable stop loss
# @param stop_loss_multiplier float 1.5 Stop-loss ATR multiplier range=0.1:20:0.1
# @param enable_trailing_stop bool false Enable trailing stop
# @param trailing_stop_multiplier float 1.0 Trailing-stop ATR multiplier range=0.1:20:0.1
# @param orb_distance float 1.5 Signal orb distance range=0.1:20:0.1
# @param orb_size int 10 Signal orb size range=3:20:1
# @param enable_sma bool true Enable SMA crossover
# @param sma_fast_length int 10 SMA fast length range=1:500:1
# @param sma_slow_length int 20 SMA slow length range=1:500:1
# @param enable_rsi bool false Enable RSI filter
# @param rsi_length int 14 RSI length range=1:500:1
# @param rsi_long_level float 50 RSI long threshold range=0:100:1
# @param rsi_short_level float 50 RSI short threshold range=0:100:1
# @param enable_macd bool false Enable MACD crossover
# @param macd_fast_length int 12 MACD fast length range=1:500:1
# @param macd_slow_length int 26 MACD slow length range=1:500:1
# @param macd_signal_length int 9 MACD signal length range=1:500:1
# @param enable_supertrend bool false Enable SuperTrend
# @param supertrend_factor float 3.0 SuperTrend factor range=0.1:20:0.1
# @param supertrend_length int 10 SuperTrend length range=1:500:1
# @param enable_stochastic bool false Enable stochastic
# @param stochastic_k_length int 14 Stochastic K length range=1:500:1
# @param stochastic_d_length int 3 Stochastic D length range=1:500:1
# @param stochastic_smooth int 3 Stochastic smoothing range=1:500:1
# @param enable_bollinger bool false Enable Bollinger trend
# @param bollinger_length int 20 Bollinger length range=1:500:1
# @param bollinger_multiplier float 2.0 Bollinger multiplier range=0.1:20:0.1
# @param enable_ema bool false Enable EMA crossover
# @param ema_fast_length int 10 EMA fast length range=1:500:1
# @param ema_slow_length int 20 EMA slow length range=1:500:1
# @param enable_ao bool false Enable Awesome Oscillator
# @param enable_sar bool false Enable Parabolic SAR
# @param sar_start float 0.02 SAR start range=0.001:1:0.01
# @param sar_increment float 0.02 SAR increment range=0.001:1:0.01
# @param sar_maximum float 0.2 SAR maximum range=0.01:2:0.1
# @param enable_cci bool false Enable CCI
# @param cci_length int 20 CCI length range=1:500:1
# @param cci_long_level float 0 CCI long threshold range=-500:500:1
# @param cci_short_level float 0 CCI short threshold range=-500:500:1
# @param enable_adx bool false Enable ADX
# @param adx_smoothing int 14 ADX smoothing range=1:500:1
# @param di_length int 14 DI length range=1:500:1
# @param adx_threshold float 20 ADX threshold range=0:100:1
# @param show_dashboard bool true Show dashboards


def _integer(name, default):
    return min(500, max(1, int(params.get(name, default))))


require_all = bool(params.get("require_all", True))
atr_length = _integer("atr_length", 14)
enable_tp = bool(params.get("enable_take_profit", False)); tp_mult = min(20.0, max(0.1, float(params.get("take_profit_multiplier", 2.0))))
enable_sl = bool(params.get("enable_stop_loss", False)); sl_mult = min(20.0, max(0.1, float(params.get("stop_loss_multiplier", 1.5))))
enable_ts = bool(params.get("enable_trailing_stop", False)); ts_mult = min(20.0, max(0.1, float(params.get("trailing_stop_multiplier", 1.0))))
orb_distance = min(20.0, max(0.1, float(params.get("orb_distance", 1.5)))); orb_size = min(20, max(3, int(params.get("orb_size", 10))))
enable_sma = bool(params.get("enable_sma", True)); sma_fast_len = _integer("sma_fast_length", 10); sma_slow_len = _integer("sma_slow_length", 20)
enable_rsi = bool(params.get("enable_rsi", False)); rsi_len = _integer("rsi_length", 14); rsi_long = min(100.0, max(0.0, float(params.get("rsi_long_level", 50)))); rsi_short = min(100.0, max(0.0, float(params.get("rsi_short_level", 50))))
enable_macd = bool(params.get("enable_macd", False)); macd_fast_len = _integer("macd_fast_length", 12); macd_slow_len = _integer("macd_slow_length", 26); macd_signal_len = _integer("macd_signal_length", 9)
enable_supertrend = bool(params.get("enable_supertrend", False)); st_factor = min(20.0, max(0.1, float(params.get("supertrend_factor", 3.0)))); st_len = _integer("supertrend_length", 10)
enable_stochastic = bool(params.get("enable_stochastic", False)); stoch_k_len = _integer("stochastic_k_length", 14); stoch_d_len = _integer("stochastic_d_length", 3); stoch_smooth = _integer("stochastic_smooth", 3)
enable_bollinger = bool(params.get("enable_bollinger", False)); bb_len = _integer("bollinger_length", 20); bb_mult = min(20.0, max(0.1, float(params.get("bollinger_multiplier", 2.0))))
enable_ema = bool(params.get("enable_ema", False)); ema_fast_len = _integer("ema_fast_length", 10); ema_slow_len = _integer("ema_slow_length", 20)
enable_ao = bool(params.get("enable_ao", False))
enable_sar = bool(params.get("enable_sar", False)); sar_start = min(1.0, max(0.001, float(params.get("sar_start", 0.02)))); sar_increment = min(1.0, max(0.001, float(params.get("sar_increment", 0.02)))); sar_maximum = min(2.0, max(0.01, float(params.get("sar_maximum", 0.2))))
enable_cci = bool(params.get("enable_cci", False)); cci_len = _integer("cci_length", 20); cci_long = min(500.0, max(-500.0, float(params.get("cci_long_level", 0)))); cci_short = min(500.0, max(-500.0, float(params.get("cci_short_level", 0))))
enable_adx = bool(params.get("enable_adx", False)); adx_len = _integer("adx_smoothing", 14); di_len = _integer("di_length", 14); adx_threshold = min(100.0, max(0.0, float(params.get("adx_threshold", 20))))
show_dashboard = bool(params.get("show_dashboard", True))


def _finite(values): return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, length):
    result = np.full(len(values), np.nan)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)): result[index] = float(np.mean(window))
    return result


def _ema(values, length):
    result = np.full(len(values), np.nan); state = np.nan; alpha = 2.0 / (length + 1.0)
    for index, value in enumerate(values):
        if np.isfinite(value):
            state = float(value) if not np.isfinite(state) else state + alpha * (float(value) - state)
            result[index] = state
    return result


def _rma(values, length):
    result = np.full(len(values), np.nan); state = np.nan; seed = []
    for index, value in enumerate(values):
        if not np.isfinite(value): continue
        if not np.isfinite(state):
            seed.append(float(value))
            if len(seed) == length: state = float(np.mean(seed)); result[index] = state
        else: state = (state * (length - 1.0) + float(value)) / length; result[index] = state
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns: df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened, high, low, close = (df[key].to_numpy(float) for key in ("open", "high", "low", "close")); n = len(df); hl2 = (high + low) / 2.0
previous_close = np.r_[np.nan, close[:-1]]
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close)))
if n: true_range[0] = high[0] - low[0]
atr = _rma(true_range, atr_length)
sma_fast, sma_slow = _sma(close, sma_fast_len), _sma(close, sma_slow_len)
delta = np.diff(close, prepend=np.nan); gain = _rma(np.where(np.isfinite(delta), np.maximum(delta, 0.0), np.nan), rsi_len); loss = _rma(np.where(np.isfinite(delta), np.maximum(-delta, 0.0), np.nan), rsi_len)
rsi = np.full(n, np.nan)
for index in range(n):
    if np.all(np.isfinite([gain[index], loss[index]])): rsi[index] = 100.0 if loss[index] == 0 else 0.0 if gain[index] == 0 else 100.0 - 100.0 / (1.0 + gain[index] / loss[index])
macd_line = _ema(close, macd_fast_len) - _ema(close, macd_slow_len); macd_signal = _ema(macd_line, macd_signal_len)


def _supertrend():
    local_atr = _rma(true_range, st_len); lower = np.full(n, np.nan); upper = np.full(n, np.nan); direction = np.ones(n, int); line = np.full(n, np.nan)
    for index in range(n):
        basic_lower, basic_upper = hl2[index] - st_factor * local_atr[index], hl2[index] + st_factor * local_atr[index]
        if index == 0 or not np.isfinite(local_atr[index - 1]): lower[index], upper[index] = basic_lower, basic_upper
        else:
            lower[index] = basic_lower if basic_lower > lower[index - 1] or close[index - 1] < lower[index - 1] else lower[index - 1]
            upper[index] = basic_upper if basic_upper < upper[index - 1] or close[index - 1] > upper[index - 1] else upper[index - 1]
            if not np.isfinite(line[index - 1]): direction[index] = 1
            elif line[index - 1] == upper[index - 1]: direction[index] = -1 if close[index] > upper[index] else 1
            else: direction[index] = 1 if close[index] < lower[index] else -1
        line[index] = lower[index] if direction[index] == -1 else upper[index]
    return line, direction


st_line, st_direction = _supertrend()
rolling_high = np.full(n, np.nan); rolling_low = np.full(n, np.nan)
for index in range(stoch_k_len - 1, n): rolling_high[index] = np.max(high[index-stoch_k_len+1:index+1]); rolling_low[index] = np.min(low[index-stoch_k_len+1:index+1])
stoch_raw = np.asarray([100.0 * (close[index] - rolling_low[index]) / (rolling_high[index] - rolling_low[index]) if np.isfinite(rolling_high[index]) and rolling_high[index] != rolling_low[index] else np.nan for index in range(n)])
stoch_k = _sma(stoch_raw, stoch_smooth); stoch_d = _sma(stoch_k, stoch_d_len)
bb_middle = _sma(close, bb_len); bb_std = np.full(n, np.nan)
for index in range(bb_len - 1, n): bb_std[index] = float(np.std(close[index-bb_len+1:index+1], ddof=0))
bb_upper, bb_lower = bb_middle + bb_mult * bb_std, bb_middle - bb_mult * bb_std
ema_fast, ema_slow = _ema(close, ema_fast_len), _ema(close, ema_slow_len)
ao = _sma(hl2, 5) - _sma(hl2, 34)


def _sar():
    result = np.full(n, np.nan)
    if n < 2: return result
    rising = close[1] > close[0]; extreme = high[1] if rising else low[1]; state = low[0] if rising else high[0]; acceleration = sar_start; result[1] = state
    for index in range(2, n):
        state = state + acceleration * (extreme - state)
        if rising:
            state = min(state, low[index - 1], low[index - 2])
            if low[index] < state: rising = False; state = extreme; extreme = low[index]; acceleration = sar_start
            elif high[index] > extreme: extreme = high[index]; acceleration = min(sar_maximum, acceleration + sar_increment)
        else:
            state = max(state, high[index - 1], high[index - 2])
            if high[index] > state: rising = True; state = extreme; extreme = high[index]; acceleration = sar_start
            elif low[index] < extreme: extreme = low[index]; acceleration = min(sar_maximum, acceleration + sar_increment)
        result[index] = state
    return result


sar = _sar(); cci_basis = _sma(close, cci_len); mean_deviation = np.full(n, np.nan)
for index in range(cci_len - 1, n): mean_deviation[index] = float(np.mean(np.abs(close[index-cci_len+1:index+1] - cci_basis[index])))
cci = np.asarray([(close[index] - cci_basis[index]) / (0.015 * mean_deviation[index]) if np.isfinite(mean_deviation[index]) and mean_deviation[index] != 0 else np.nan for index in range(n)])
up_move = np.diff(high, prepend=np.nan); down_move = -np.diff(low, prepend=np.nan)
plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0); minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
di_atr = _rma(true_range, di_len); plus_di = 100.0 * _rma(plus_dm, di_len) / di_atr; minus_di = 100.0 * _rma(minus_dm, di_len) / di_atr
dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di); adx = _rma(dx, adx_len)

bulls = [sma_fast > sma_slow, rsi > rsi_long, macd_line > macd_signal, st_direction == -1, stoch_k > 50, close > bb_middle, ema_fast > ema_slow, ao > 0, close > sar, cci > cci_long, (adx > adx_threshold) & (plus_di > minus_di)]
bears = [sma_fast < sma_slow, rsi < rsi_short, macd_line < macd_signal, st_direction == 1, stoch_k < 50, close < bb_middle, ema_fast < ema_slow, ao < 0, close < sar, cci < cci_short, (adx > adx_threshold) & (minus_di > plus_di)]
enabled = [enable_sma, enable_rsi, enable_macd, enable_supertrend, enable_stochastic, enable_bollinger, enable_ema, enable_ao, enable_sar, enable_cci, enable_adx]
enabled_indices = [index for index, value in enumerate(enabled) if value]
long_condition = np.zeros(n, bool); short_condition = np.zeros(n, bool)
if enabled_indices:
    for index in range(n):
        long_condition[index] = all(bulls[item][index] for item in enabled_indices) if require_all else any(bulls[item][index] for item in enabled_indices)
        short_condition[index] = all(bears[item][index] for item in enabled_indices) if require_all else any(bears[item][index] for item in enabled_indices)


def _simulate(bull, bear):
    state = 0; entry = sl = tp = ts = np.nan; total = wins = 0; net = gross_profit = gross_loss = 0.0
    entries_long = np.zeros(n, bool); entries_short = np.zeros(n, bool); exit_long = np.zeros(n, bool); exit_short = np.zeros(n, bool)
    state_history = np.zeros(n, int); sl_history = np.full(n, np.nan); tp_history = np.full(n, np.nan); ts_history = np.full(n, np.nan)
    previous_bull = previous_bear = False
    for index in range(n):
        long_exit = short_exit = False; exit_price = np.nan
        if state == 1:
            if enable_sl and low[index] <= sl: long_exit, exit_price = True, sl
            elif enable_tp and high[index] >= tp: long_exit, exit_price = True, tp
            elif enable_ts and low[index] <= ts: long_exit, exit_price = True, ts
            elif bear[index]: long_exit, exit_price = True, close[index]
        if state == -1:
            if enable_sl and high[index] >= sl: short_exit, exit_price = True, sl
            elif enable_tp and low[index] <= tp: short_exit, exit_price = True, tp
            elif enable_ts and high[index] >= ts: short_exit, exit_price = True, ts
            elif bull[index]: short_exit, exit_price = True, close[index]
        if long_exit or short_exit:
            trade_return = ((exit_price - entry) / entry if long_exit else (entry - exit_price) / entry) * 100.0
            total += 1; net += trade_return
            if trade_return > 0: wins += 1; gross_profit += trade_return
            else: gross_loss += abs(trade_return)
            state = 0; sl = tp = ts = np.nan; exit_long[index], exit_short[index] = long_exit, short_exit
        enter_long = bool(bull[index] and not previous_bull and state != 1)
        enter_short = bool(bear[index] and not previous_bear and state != -1)
        if enter_long:
            state = 1; entry = close[index]; sl = close[index] - atr[index] * sl_mult if enable_sl else np.nan; tp = close[index] + atr[index] * tp_mult if enable_tp else np.nan; ts = close[index] - atr[index] * ts_mult if enable_ts else np.nan; entries_long[index] = True
        if enter_short:
            state = -1; entry = close[index]; sl = close[index] + atr[index] * sl_mult if enable_sl else np.nan; tp = close[index] - atr[index] * tp_mult if enable_tp else np.nan; ts = close[index] + atr[index] * ts_mult if enable_ts else np.nan; entries_short[index] = True
        if state == 1 and not enter_long and enable_ts and np.isfinite(atr[index]): ts = max(ts, close[index] - atr[index] * ts_mult)
        if state == -1 and not enter_short and enable_ts and np.isfinite(atr[index]): ts = min(ts, close[index] + atr[index] * ts_mult)
        state_history[index] = state; sl_history[index] = sl if state else np.nan; tp_history[index] = tp if state else np.nan; ts_history[index] = ts if state else np.nan
        previous_bull, previous_bear = bool(bull[index]), bool(bear[index])
    return {"state": state_history, "long": entries_long, "short": entries_short, "exitLong": exit_long, "exitShort": exit_short, "sl": sl_history, "tp": tp_history, "ts": ts_history, "total": total, "wins": wins, "net": net, "grossProfit": gross_profit, "grossLoss": gross_loss}


standalone = [_simulate(bulls[index], bears[index]) for index in range(11)]
combined = _simulate(long_condition, short_condition)
long_glow = np.where(combined["long"], low - atr * orb_distance, np.nan); short_glow = np.where(combined["short"], high + atr * orb_distance, np.nan)
trailing_cloud = [{"upper": float(max(close[index], combined["ts"][index])), "lower": float(min(close[index], combined["ts"][index])), "color": "#2962FF", "opacity": 0.30} if enable_ts and combined["state"][index] != 0 and not combined["long"][index] and not combined["short"][index] and np.isfinite(combined["ts"][index]) else None for index in range(n)]

names = ["SMA Cross", "RSI", "MACD", "SuperTrend", "Stochastic", "Bollinger", "EMA Cross", "AO", "SAR", "CCI", "ADX"]
tables = []
if show_dashboard and n:
    rows = []
    for index, name in enumerate(names):
        status = "Bullish" if bulls[index][-1] else "Bearish" if bears[index][-1] else "Neutral"
        total, wins = standalone[index]["total"], standalone[index]["wins"]
        rows.append([{"text": name}, {"text": status, "color": "#089981" if status == "Bullish" else "#F23645" if status == "Bearish" else "#808080"}, {"text": f"{(wins / total * 100.0 if total else 0.0):.1f}%"}, {"text": "On" if enabled[index] else "Off"}])
    tables.append({"title": "Indicator Dashboard", "position": "top-right", "compact": True, "rows": rows})
    total, wins = combined["total"], combined["wins"]; profit_factor = combined["grossProfit"] / combined["grossLoss"] if combined["grossLoss"] > 0 else 999.0 if combined["grossProfit"] > 0 else 0.0
    tables.append({"title": "Backtest Results", "position": "bottom-right", "compact": True, "rows": [[{"text": "Trades"}, {"text": str(total)}], [{"text": "Wins"}, {"text": str(wins)}], [{"text": "Win Rate"}, {"text": f"{(wins / total * 100 if total else 0):.2f}%"}], [{"text": "Profit Factor"}, {"text": "MAX" if profit_factor == 999 else f"{profit_factor:.2f}"}], [{"text": "PnL"}, {"text": f"{combined['net']:.2f}%"}]]})

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Long Glow", "title": False, "data": _finite(long_glow), "color": "#089981", "type": "scatter", "lineWidth": orb_size, "overlay": True},
        {"name": "Short Glow", "title": False, "data": _finite(short_glow), "color": "#F23645", "type": "scatter", "lineWidth": orb_size, "overlay": True},
        {"name": "Stop Loss", "title": bool(enable_sl and n and np.isfinite(combined["sl"][-1])), "data": _finite(combined["sl"]) if enable_sl else [None] * n, "color": "#F23645", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Take Profit", "title": bool(enable_tp and n and np.isfinite(combined["tp"][-1])), "data": _finite(combined["tp"]) if enable_tp else [None] * n, "color": "#089981", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Trailing Stop", "title": bool(enable_ts and n and np.isfinite(combined["ts"][-1])), "data": _finite(combined["ts"]) if enable_ts else [None] * n, "color": "#2962FF", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Trailing Stop Fill", "title": False, "data": trailing_cloud, "color": "#2962FF", "type": "cloud", "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Combined Long", "text": "BUY", "data": [{"active": True, "price": float(long_glow[index]), "text": "BUY", "color": "#089981"} if combined["long"][index] and np.isfinite(long_glow[index]) else None for index in range(n)], "color": "#089981", "renderMode": "events", "overlay": True},
        {"type": "sell", "name": "Combined Short", "text": "SELL", "data": [{"active": True, "price": float(short_glow[index]), "text": "SELL", "color": "#F23645"} if combined["short"][index] and np.isfinite(short_glow[index]) else None for index in range(n)], "color": "#F23645", "renderMode": "events", "overlay": True},
        {"type": "sell", "name": "Exit Long", "text": "X", "data": [{"active": True, "price": float(high[index]), "text": "X", "color": "#F23645"} if combined["exitLong"][index] and not combined["short"][index] else None for index in range(n)], "color": "#F23645", "renderMode": "events", "overlay": True},
        {"type": "buy", "name": "Exit Short", "text": "X", "data": [{"active": True, "price": float(low[index]), "text": "X", "color": "#089981"} if combined["exitShort"][index] and not combined["long"][index] else None for index in range(n)], "color": "#089981", "renderMode": "events", "overlay": True},
    ],
    "layers": [], "tables": tables, "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "atr": _finite(atr), "smaFast": _finite(sma_fast), "smaSlow": _finite(sma_slow), "rsi": _finite(rsi), "macdLine": _finite(macd_line), "macdSignal": _finite(macd_signal), "superTrend": _finite(st_line), "superTrendDirection": st_direction.tolist(), "stochasticK": _finite(stoch_k), "stochasticD": _finite(stoch_d), "bollingerMiddle": _finite(bb_middle), "bollingerUpper": _finite(bb_upper), "bollingerLower": _finite(bb_lower), "emaFast": _finite(ema_fast), "emaSlow": _finite(ema_slow), "awesomeOscillator": _finite(ao), "sar": _finite(sar), "cci": _finite(cci), "plusDi": _finite(plus_di), "minusDi": _finite(minus_di), "adx": _finite(adx),
        "longCondition": long_condition.tolist(), "shortCondition": short_condition.tolist(), "tradeState": combined["state"].tolist(), "enterLong": combined["long"].tolist(), "enterShort": combined["short"].tolist(), "exitLong": combined["exitLong"].tolist(), "exitShort": combined["exitShort"].tolist(), "stopLevel": _finite(combined["sl"]), "targetLevel": _finite(combined["tp"]), "trailingLevel": _finite(combined["ts"]),
        "totalTrades": int(combined["total"]), "winTrades": int(combined["wins"]), "netProfitPct": float(combined["net"]), "grossProfit": float(combined["grossProfit"]), "grossLoss": float(combined["grossLoss"]), "indicatorTotalTrades": [int(item["total"]) for item in standalone], "indicatorWinTrades": [int(item["wins"]) for item in standalone], "indicatorStates": [item["state"].tolist() for item in standalone], "bullStates": [item.tolist() for item in bulls], "bearStates": [item.tolist() for item in bears],
    },
}
