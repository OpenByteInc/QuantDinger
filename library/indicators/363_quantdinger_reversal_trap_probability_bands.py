# ============================================================
# QuantDinger Reversal Trap Probability Bands
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Reversal Trap Probability Bands"
my_indicator_description = "Detects failed envelope breakouts, tracks RSI-bucket outcomes, and projects basis targets with structural ATR stops."

# @param envelope_length int 55 Envelope smoothness range=1:10000:1
# @param envelope_width float 4.0 Envelope ATR width range=0.1:100:0.1
# @param trap_window int 10 Maximum outside-band bars range=1:10000:1
# @param signal_gap int 10 Minimum bars between signals range=1:10000:1
# @param stop_atr_multiplier float 0.5 ATR stop multiplier range=0.1:100:0.1
# @param max_lookback_bars int 5000 Historical tracking limit range=1:100000:1
# @param max_total_trades int 500 Historical trade limit range=1:100000:1
# @param bear_color str #EC3333 Bearish color
# @param bull_color str #12AF9F Bullish color
# @param basis_color str #787B86 Basis color

envelope_length = min(10000, max(1, int(params.get("envelope_length", 55))))
envelope_width = min(100.0, max(0.1, float(params.get("envelope_width", 4.0))))
trap_window = min(10000, max(1, int(params.get("trap_window", 10))))
signal_gap = min(10000, max(1, int(params.get("signal_gap", 10))))
stop_atr_multiplier = min(100.0, max(0.1, float(params.get("stop_atr_multiplier", 0.5))))
max_lookback_bars = min(100000, max(1, int(params.get("max_lookback_bars", 5000))))
max_total_trades = min(100000, max(1, int(params.get("max_total_trades", 500))))
bear_color = str(params.get("bear_color", "#EC3333"))
bull_color = str(params.get("bull_color", "#12AF9F"))
basis_color = str(params.get("basis_color", "#787B86"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    seed = []
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) == length:
                previous = float(np.mean(seed))
                result[index] = previous
        else:
            previous = (previous * (length - 1.0) + float(value)) / length
            result[index] = previous
    return result


def _atr(high_values, low_values, close_values, length):
    true_range = np.full(len(close_values), np.nan, dtype=float)
    for index in range(len(close_values)):
        if index == 0:
            true_range[index] = high_values[index] - low_values[index]
        else:
            true_range[index] = max(high_values[index] - low_values[index], abs(high_values[index] - close_values[index - 1]), abs(low_values[index] - close_values[index - 1]))
    return _rma(true_range, length)


def _rsi(values, length):
    changes = np.diff(values, prepend=np.nan)
    gains = np.where(np.isfinite(changes), np.maximum(changes, 0.0), np.nan)
    losses = np.where(np.isfinite(changes), np.maximum(-changes, 0.0), np.nan)
    average_gain = _rma(gains, length)
    average_loss = _rma(losses, length)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        if np.isfinite(average_gain[index]) and np.isfinite(average_loss[index]):
            if average_loss[index] == 0.0:
                result[index] = 100.0 if average_gain[index] > 0.0 else 50.0
            elif average_gain[index] == 0.0:
                result[index] = 0.0
            else:
                result[index] = 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])
    return result


def _event_series(mask, prices, texts, color, event_type):
    return {
        "type": event_type,
        "name": event_type.replace("_", " ").title(),
        "data": [
            {"active": True, "price": float(prices[index]), "text": str(texts[index]), "color": color}
            if active and np.isfinite(prices[index]) else None
            for index, active in enumerate(mask)
        ],
        "color": color,
        "renderMode": "events",
    }


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

basis = _ema(close, envelope_length)
envelope_atr = _atr(high, low, close, envelope_length)
rsi = _rsi(close, 20)
stop_atr = _atr(high, low, close, 100) * stop_atr_multiplier
upper = basis + envelope_width * envelope_atr
lower = basis - envelope_width * envelope_atr
rsi_bucket = np.full(n, -1, dtype=int)
for index in range(n):
    if np.isfinite(rsi[index]):
        rsi_bucket[index] = int(min(10, max(0, np.floor(rsi[index] / 10.0 + 0.5))))

above_count = np.zeros(n, dtype=int)
below_count = np.zeros(n, dtype=int)
raw_bear_trap = np.zeros(n, dtype=bool)
raw_bull_trap = np.zeros(n, dtype=bool)
bear_trap = np.zeros(n, dtype=bool)
bull_trap = np.zeros(n, dtype=bool)
bull_entry = np.zeros(n, dtype=bool)
bear_entry = np.zeros(n, dtype=bool)
active_bull_series = np.zeros(n, dtype=bool)
active_bear_series = np.zeros(n, dtype=bool)
bull_target_series = np.full(n, np.nan, dtype=float)
bull_stop_series = np.full(n, np.nan, dtype=float)
bear_target_series = np.full(n, np.nan, dtype=float)
bear_stop_series = np.full(n, np.nan, dtype=float)
bull_win = np.zeros(n, dtype=bool)
bull_loss = np.zeros(n, dtype=bool)
bear_win = np.zeros(n, dtype=bool)
bear_loss = np.zeros(n, dtype=bool)
bull_probability = np.full(n, np.nan, dtype=float)
bear_probability = np.full(n, np.nan, dtype=float)
bull_label = [""] * n
bear_label = [""] * n
bull_total = np.zeros(11, dtype=int)
bull_wins = np.zeros(11, dtype=int)
bear_total = np.zeros(11, dtype=int)
bear_wins = np.zeros(11, dtype=int)
total_trades = 0
last_signal_index = -1000
active_bull = False
active_bear = False
bull_target = bull_stop = bear_target = bear_stop = np.nan
bull_active_bucket = bear_active_bucket = -1

for index in range(n):
    prior_above = above_count[index - 1] if index else 0
    prior_below = below_count[index - 1] if index else 0
    if index > 0 and np.isfinite(upper[index]) and close[index] < upper[index]:
        raw_bear_trap[index] = ((high[index] > upper[index] and close[index - 1] < upper[index] and prior_above <= trap_window) or (close[index - 1] > upper[index] and prior_above <= trap_window))
    above_count[index] = prior_above + 1 if np.isfinite(upper[index]) and high[index] > upper[index] else 0
    if index > 0 and np.isfinite(lower[index]) and close[index] > lower[index]:
        raw_bull_trap[index] = ((low[index] < lower[index] and close[index - 1] > lower[index] and prior_below <= trap_window) or (close[index - 1] < lower[index] and prior_below <= trap_window))
    below_count[index] = prior_below + 1 if np.isfinite(lower[index]) and low[index] < lower[index] else 0

    can_fire = index - last_signal_index >= signal_gap
    bull_trap[index] = raw_bull_trap[index] and can_fire
    bear_trap[index] = raw_bear_trap[index] and can_fire
    if bull_trap[index] or bear_trap[index]:
        last_signal_index = index

    allowed = n - 1 - index <= max_lookback_bars and total_trades < max_total_trades
    if bull_trap[index] and not active_bull and allowed and rsi_bucket[index] >= 0 and np.all(np.isfinite([basis[index], stop_atr[index]])):
        bull_entry[index] = True
        active_bull = True
        bull_active_bucket = rsi_bucket[index]
        total_trades += 1
        bull_target = basis[index]
        bull_stop = float(np.min(low[max(0, index - 1):index + 1])) - stop_atr[index]
        attempts = bull_total[bull_active_bucket]
        rate = np.nan if attempts == 0 else float(np.floor(100.0 * bull_wins[bull_active_bucket] / attempts + 0.5))
        bull_probability[index] = rate
        bull_label[index] = "Bull Trap" if not np.isfinite(rate) else "Bull Trap " + str(int(rate)) + "%"
    if active_bull:
        bull_target_series[index], bull_stop_series[index] = bull_target, bull_stop
        if index - last_signal_index > 1 and high[index] >= bull_target:
            bull_total[bull_active_bucket] += 1
            bull_wins[bull_active_bucket] += 1
            bull_win[index] = True
            active_bull = False
        elif index - last_signal_index > 1 and low[index] < bull_stop:
            bull_total[bull_active_bucket] += 1
            bull_loss[index] = True
            active_bull = False

    if bear_trap[index] and not active_bear and allowed and rsi_bucket[index] >= 0 and np.all(np.isfinite([basis[index], stop_atr[index]])):
        bear_entry[index] = True
        active_bear = True
        bear_active_bucket = rsi_bucket[index]
        total_trades += 1
        bear_target = basis[index]
        bear_stop = float(np.max(high[max(0, index - 1):index + 1])) + stop_atr[index]
        attempts = bear_total[bear_active_bucket]
        rate = np.nan if attempts == 0 else float(np.floor(100.0 * bear_wins[bear_active_bucket] / attempts + 0.5))
        bear_probability[index] = rate
        bear_label[index] = "Bear Trap" if not np.isfinite(rate) else "Bear Trap " + str(int(rate)) + "%"
    if active_bear:
        bear_target_series[index], bear_stop_series[index] = bear_target, bear_stop
        if index - last_signal_index > 1 and low[index] <= bear_target:
            bear_total[bear_active_bucket] += 1
            bear_wins[bear_active_bucket] += 1
            bear_win[index] = True
            active_bear = False
        elif index - last_signal_index > 1 and high[index] > bear_stop:
            bear_total[bear_active_bucket] += 1
            bear_loss[index] = True
            active_bear = False
    active_bull_series[index] = active_bull
    active_bear_series[index] = active_bear

upper_cloud = [
    {"upper": float(upper[index]), "lower": float(basis[index]), "color": bear_color, "opacity": 0.10}
    if np.all(np.isfinite([upper[index], basis[index]])) else None for index in range(n)
]
lower_cloud = [
    {"upper": float(basis[index]), "lower": float(lower[index]), "color": bull_color, "opacity": 0.10}
    if np.all(np.isfinite([basis[index], lower[index]])) else None for index in range(n)
]
outcome_text = ["Target"] * n
loss_text = ["Stop"] * n

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Envelope Ceiling", "title": True, "data": _finite(upper), "color": bear_color, "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Envelope Floor", "title": True, "data": _finite(lower), "color": bull_color, "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Envelope Basis", "title": True, "data": _finite(basis), "color": basis_color, "type": "line", "lineStyle": "dashed", "overlay": True},
        {"name": "Upper Envelope Fill", "title": False, "data": upper_cloud, "color": bear_color, "type": "cloud", "overlay": True},
        {"name": "Lower Envelope Fill", "title": False, "data": lower_cloud, "color": bull_color, "type": "cloud", "overlay": True},
        {"name": "Bull Target", "title": True, "data": _finite(bull_target_series), "color": bull_color, "type": "step", "lineStyle": "dashed", "overlay": True},
        {"name": "Bull Stop", "title": True, "data": _finite(bull_stop_series), "color": bull_color, "type": "step", "lineStyle": "dotted", "overlay": True},
        {"name": "Bear Target", "title": True, "data": _finite(bear_target_series), "color": bear_color, "type": "step", "lineStyle": "dashed", "overlay": True},
        {"name": "Bear Stop", "title": True, "data": _finite(bear_stop_series), "color": bear_color, "type": "step", "lineStyle": "dotted", "overlay": True},
    ],
    "signals": [
        _event_series(bull_entry, low - np.where(np.isfinite(stop_atr), stop_atr, 0.0), bull_label, bull_color, "bull_trap"),
        _event_series(bear_entry, high + np.where(np.isfinite(stop_atr), stop_atr, 0.0), bear_label, bear_color, "bear_trap"),
        _event_series(bull_win, high, outcome_text, bull_color, "bull_target"), _event_series(bear_win, low, outcome_text, bear_color, "bear_target"),
        _event_series(bull_loss, low, loss_text, bear_color, "bull_stop"), _event_series(bear_loss, high, loss_text, bull_color, "bear_stop"),
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "basis": _finite(basis), "envelopeAtr": _finite(envelope_atr), "rsi": _finite(rsi), "stopAtr": _finite(stop_atr),
        "upper": _finite(upper), "lower": _finite(lower), "rsiBucket": [int(value) for value in rsi_bucket],
        "aboveCount": [int(value) for value in above_count], "belowCount": [int(value) for value in below_count],
        "rawBearTrap": raw_bear_trap.tolist(), "rawBullTrap": raw_bull_trap.tolist(), "bearTrap": bear_trap.tolist(), "bullTrap": bull_trap.tolist(),
        "bullEntry": bull_entry.tolist(), "bearEntry": bear_entry.tolist(),
        "activeBull": active_bull_series.tolist(), "activeBear": active_bear_series.tolist(),
        "bullTarget": _finite(bull_target_series), "bullStop": _finite(bull_stop_series), "bearTarget": _finite(bear_target_series), "bearStop": _finite(bear_stop_series),
        "bullWin": bull_win.tolist(), "bullLoss": bull_loss.tolist(), "bearWin": bear_win.tolist(), "bearLoss": bear_loss.tolist(),
        "bullProbability": _finite(bull_probability), "bearProbability": _finite(bear_probability),
        "bullTotal": [int(value) for value in bull_total], "bullWins": [int(value) for value in bull_wins],
        "bearTotal": [int(value) for value in bear_total], "bearWins": [int(value) for value in bear_wins], "totalTrades": int(total_trades),
    },
}
