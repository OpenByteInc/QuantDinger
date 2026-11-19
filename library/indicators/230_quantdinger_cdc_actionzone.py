# ============================================================
# QuantDinger CDC ActionZone
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger CDC ActionZone"
my_indicator_description = (
    "Classifies price action around smoothed fast and slow EMAs, tracks governed "
    "buy/sell regime changes, and optionally confirms momentum with Stochastic RSI."
)

indicator_timeframes = ["1H", "4H", "1D", "1W"]

# @param source str Close Source data values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param fast_period int 12 Fast EMA period range=1:500:1
# @param slow_period int 26 Slow EMA period range=1:500:1
# @param smoothing int 1 Smoothing period range=1:100:1
# @param paint_bars bool true Paint bar colors
# @param show_fast bool true Show fast EMA
# @param show_slow bool true Show slow EMA
# @param assistive_text bool true Show current status label
# @param show_signals bool true Show buy and sell signals
# @param show_ribbon bool false Show trend ribbon
# @param ribbon_position str Top Ribbon position values=Top,Bottom
# @param use_fixed_timeframe bool false Use fixed timeframe
# @param fixed_timeframe str 1D Fixed timeframe values=1H,4H,1D,1W
# @param show_momentum_signals bool false Show momentum signals
# @param momentum_threshold int 1 Momentum signal threshold range=0:1:1
# @param max_visible_signals int 24 Maximum visible signal labels range=1:100:1

source_name = str(params.get("source", "Close"))
fast_period = min(500, max(1, int(params.get("fast_period", 12))))
slow_period = min(500, max(1, int(params.get("slow_period", 26))))
smoothing = min(100, max(1, int(params.get("smoothing", 1))))
paint_bars = bool(params.get("paint_bars", True))
show_fast = bool(params.get("show_fast", True))
show_slow = bool(params.get("show_slow", True))
assistive_text = bool(params.get("assistive_text", True))
show_signals = bool(params.get("show_signals", True))
show_ribbon = bool(params.get("show_ribbon", False))
ribbon_position = str(params.get("ribbon_position", "Top")).lower()
use_fixed_timeframe = bool(params.get("use_fixed_timeframe", False))
fixed_timeframe = str(params.get("fixed_timeframe", "1D")).upper()
show_momentum_signals = bool(params.get("show_momentum_signals", False))
momentum_threshold = min(1, max(0, int(params.get("momentum_threshold", 1))))
max_visible_signals = min(100, max(1, int(params.get("max_visible_signals", 24))))


def normalize_ohlc(raw):
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    result = raw.copy().reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = np.arange(len(result), dtype=float) * 3600.0 if column == "time" else np.nan
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["volume"] = result["volume"].fillna(0.0)
    result = result.dropna(subset=["time", "open", "high", "low", "close"])
    return result.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


def select_source(source_frame):
    opens = source_frame["open"].to_numpy(dtype=float)
    highs = source_frame["high"].to_numpy(dtype=float)
    lows = source_frame["low"].to_numpy(dtype=float)
    closes = source_frame["close"].to_numpy(dtype=float)
    key = source_name.replace("_", "").replace(" ", "").upper()
    choices = {
        "OPEN": opens,
        "HIGH": highs,
        "LOW": lows,
        "HL2": (highs + lows) * 0.5,
        "HLC3": (highs + lows + closes) / 3.0,
        "OHLC4": (opens + highs + lows + closes) * 0.25,
    }
    return choices.get(key, closes).astype(float)


def ema(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (float(length) + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            if np.isfinite(previous):
                result[index] = previous
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def rma(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    for index in range(length, len(values)):
        result[index] = (result[index - 1] * (length - 1) + values[index]) / float(length)
    return result


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


frame = normalize_ohlc(df)
n = len(frame)
time_values = frame["time"].to_numpy(dtype=float)
base_seconds = time_values.copy()
if n and np.nanmax(np.abs(base_seconds)) > 10000000000:
    base_seconds = base_seconds / 1000.0
base_open = frame["open"].to_numpy(dtype=float)
base_high = frame["high"].to_numpy(dtype=float)
base_low = frame["low"].to_numpy(dtype=float)
base_close = frame["close"].to_numpy(dtype=float)
base_source = select_source(frame)
x_price = ema(base_source, smoothing)

fixed_source_index = np.full(n, -1, dtype=int)
if use_fixed_timeframe:
    runtime_context = globals().get("ctx")
    raw_fixed_source = (
        runtime_context.raw_series(fixed_timeframe)
        if runtime_context is not None and hasattr(runtime_context, "raw_series")
        else None
    )
    raw_fixed = normalize_ohlc(raw_fixed_source)
    if not len(raw_fixed):
        raw_fixed = frame.copy()
    fixed_times = raw_fixed["time"].to_numpy(dtype=float)
    if len(fixed_times) and np.nanmax(np.abs(fixed_times)) > 10000000000:
        fixed_times = fixed_times / 1000.0
    fixed_source = select_source(raw_fixed)
    raw_fast = ema(fixed_source, fast_period)
    raw_slow = ema(fixed_source, slow_period)
    aligned_fast = np.full(n, np.nan, dtype=float)
    aligned_slow = np.full(n, np.nan, dtype=float)
    for index, timestamp in enumerate(base_seconds):
        current_source = int(np.searchsorted(fixed_times, timestamp, side="right") - 1)
        confirmed_source = current_source - 1
        if confirmed_source >= 0:
            fixed_source_index[index] = confirmed_source
            aligned_fast[index] = raw_fast[confirmed_source]
            aligned_slow[index] = raw_slow[confirmed_source]
    fast_ma = ema(aligned_fast, smoothing)
    slow_ma = ema(aligned_slow, smoothing)
else:
    fast_ma = ema(x_price, fast_period)
    slow_ma = ema(x_price, slow_period)

bull = fast_ma > slow_ma
bear = fast_ma < slow_ma
green = bull & (x_price > fast_ma)
blue = bear & (x_price > fast_ma) & (x_price > slow_ma)
light_blue = bear & (x_price > fast_ma) & (x_price < slow_ma)
red = bear & (x_price < fast_ma)
orange = bull & (x_price < fast_ma) & (x_price < slow_ma)
yellow = bull & (x_price < fast_ma) & (x_price > slow_ma)

state_colors = []
for index in range(n):
    if green[index]:
        state_colors.append("#22C55E")
    elif blue[index]:
        state_colors.append("#2563EB")
    elif light_blue[index]:
        state_colors.append("#22D3EE")
    elif red[index]:
        state_colors.append("#EF4444")
    elif orange[index]:
        state_colors.append("#F97316")
    elif yellow[index]:
        state_colors.append("#FACC15")
    else:
        state_colors.append("#64748B")

buy_condition = np.zeros(n, dtype=bool)
sell_condition = np.zeros(n, dtype=bool)
if n:
    buy_condition[0] = bool(green[0])
    sell_condition[0] = bool(red[0])
for index in range(1, n):
    buy_condition[index] = bool(green[index] and not green[index - 1])
    sell_condition[index] = bool(red[index] and not red[index - 1])

bullish = np.zeros(n, dtype=bool)
bearish = np.zeros(n, dtype=bool)
last_buy = -1
last_sell = -1
for index in range(n):
    if buy_condition[index]:
        last_buy = index
    if sell_condition[index]:
        last_sell = index
    bullish[index] = last_buy >= 0 and last_buy > last_sell
    bearish[index] = last_sell >= 0 and last_sell > last_buy

buy_event = np.zeros(n, dtype=bool)
sell_event = np.zeros(n, dtype=bool)
for index in range(1, n):
    buy_event[index] = bool(bearish[index - 1] and buy_condition[index])
    sell_event[index] = bool(bullish[index - 1] and sell_condition[index])

delta = np.zeros(n, dtype=float)
if n > 1:
    delta[1:] = np.diff(base_close)
gain = np.maximum(delta, 0.0)
loss = np.maximum(-delta, 0.0)
average_gain = rma(gain, 14)
average_loss = rma(loss, 14)
rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.isfinite(average_gain[index]) or not np.isfinite(average_loss[index]):
        continue
    if average_loss[index] == 0.0:
        rsi[index] = 100.0 if average_gain[index] > 0.0 else 50.0
    else:
        rsi[index] = 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])
rsi_series = pd.Series(rsi, dtype=float)
rsi_low = rsi_series.rolling(14, min_periods=14).min()
rsi_high = rsi_series.rolling(14, min_periods=14).max()
rsi_range = rsi_high - rsi_low
stoch_rsi = 100.0 * (rsi_series - rsi_low) / rsi_range.where(rsi_range != 0.0, np.nan)
k_line = stoch_rsi.rolling(3, min_periods=3).mean().to_numpy(dtype=float)
d_line = pd.Series(k_line).rolling(3, min_periods=3).mean().to_numpy(dtype=float)
cross_up = np.zeros(n, dtype=bool)
cross_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(k_line[index]) and np.isfinite(d_line[index]) and np.isfinite(k_line[index - 1]) and np.isfinite(d_line[index - 1]):
        cross_up[index] = k_line[index] > d_line[index] and k_line[index - 1] <= d_line[index - 1]
        cross_down[index] = k_line[index] < d_line[index] and k_line[index - 1] >= d_line[index - 1]

momentum_buy_score = np.zeros(n, dtype=int)
momentum_sell_score = np.zeros(n, dtype=int)
for index in range(n):
    if bullish[index] and cross_up[index] and np.isfinite(d_line[index]):
        momentum_buy_score[index] = 2 if d_line[index] < 30.0 else 1 if d_line[index] > 30.0 else 0
    if bearish[index] and cross_down[index] and np.isfinite(d_line[index]):
        momentum_sell_score[index] = 2 if d_line[index] > 70.0 else 1 if d_line[index] < 70.0 else 0
momentum_buy_event = momentum_buy_score > momentum_threshold
momentum_sell_event = momentum_sell_score > momentum_threshold

true_range = np.zeros(n, dtype=float)
if n:
    true_range[0] = base_high[0] - base_low[0]
for index in range(1, n):
    true_range[index] = max(base_high[index] - base_low[index], abs(base_high[index] - base_close[index - 1]), abs(base_low[index] - base_close[index - 1]))
atr = rma(true_range, 14)
fallback_range = np.maximum(base_high - base_low, np.maximum(np.abs(base_close) * 0.002, 1e-9))
display_range = np.where(np.isfinite(atr), atr, fallback_range)

visible_candidates = np.zeros(n, dtype=bool)
if show_signals:
    visible_candidates |= buy_event | sell_event
if show_momentum_signals:
    visible_candidates |= momentum_buy_event | momentum_sell_event
visible_indexes = set(np.flatnonzero(visible_candidates)[-max_visible_signals:].tolist())


def signal_data(mask, values):
    return [float(values[index]) if index in visible_indexes and mask[index] else None for index in range(n)]


cloud_data = []
for index in range(n):
    if np.isfinite(fast_ma[index]) and np.isfinite(slow_ma[index]):
        cloud_data.append({
            "upper": float(max(fast_ma[index], slow_ma[index])),
            "lower": float(min(fast_ma[index], slow_ma[index])),
            "color": "rgba(34,197,94,0.14)" if bull[index] else "rgba(239,68,68,0.14)" if bear[index] else "rgba(100,116,139,0.10)",
        })
    else:
        cloud_data.append(None)

plots = [
    {"name": "Action Zone", "title": False, "data": cloud_data, "color": "#64748B", "type": "cloud", "opacity": 0.14, "overlay": True},
]
if show_fast:
    plots.append({"name": "Fast EMA", "data": finite(fast_ma), "color": "#EF4444", "type": "line", "lineWidth": 2, "overlay": True})
if show_slow:
    plots.append({"name": "Slow EMA", "data": finite(slow_ma), "color": "#3B82F6", "type": "line", "lineWidth": 2, "overlay": True})
if show_ribbon:
    ribbon_values = base_high + display_range * 0.35 if ribbon_position == "top" else base_low - display_range * 0.35
    ribbon_data = []
    for index in range(n):
        color_value = "#22C55E" if bullish[index] else "#EF4444" if bearish[index] else "#64748B"
        ribbon_data.append({"value": float(ribbon_values[index]), "color": color_value, "size": 3.5})
    plots.append({"name": "Trend Ribbon", "title": False, "data": ribbon_data, "color": "#64748B", "type": "scatter", "overlay": True})

signals = []
if show_signals:
    signals.extend([
        {"type": "buy", "text": "BUY", "name": "Buy Signal", "data": signal_data(buy_event, base_low - display_range * 0.25), "color": "#22C55E", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Sell Signal", "data": signal_data(sell_event, base_high + display_range * 0.25), "color": "#EF4444", "renderMode": "events"},
    ])
if show_momentum_signals:
    signals.extend([
        {"type": "buy", "text": "M+", "name": "Momentum Buy", "data": signal_data(momentum_buy_event, base_low - display_range * 0.45), "color": "#14B8A6", "renderMode": "events"},
        {"type": "sell", "text": "M-", "name": "Momentum Sell", "data": signal_data(momentum_sell_event, base_high + display_range * 0.45), "color": "#F97316", "renderMode": "events"},
    ])

layers = []
if assistive_text and n and np.isfinite(slow_ma[-1]):
    trend_text = "Bullish" if bullish[-1] else "Bearish" if bearish[-1] else "Sideways"
    action_text = "BUY next bar · " if buy_event[-1] else "SELL next bar · " if sell_event[-1] else ""
    above = base_close[-1] > slow_ma[-1]
    layers.append({
        "type": "label",
        "timestamp": float(time_values[-1]),
        "price": float(base_high[-1] + display_range[-1] * 0.45 if above else base_low[-1] - display_range[-1] * 0.45),
        "text": f"{action_text}{trend_text} · {base_close[-1]:.2f}",
        "color": "#22C55E" if buy_event[-1] else "#EF4444" if sell_event[-1] else state_colors[-1],
        "fillColor": "rgba(15,23,42,0.88)",
        "borderColor": state_colors[-1],
        "textColor": "#F8FAFC",
        "fontSize": 9,
        "side": "above" if above else "below",
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": [],
    "bar_colors": state_colors if paint_bars else [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "xPrice": finite(x_price),
        "fastMA": finite(fast_ma),
        "slowMA": finite(slow_ma),
        "fixedSourceIndex": [int(value) for value in fixed_source_index],
        "bull": [bool(value) for value in bull],
        "bear": [bool(value) for value in bear],
        "green": [bool(value) for value in green],
        "blue": [bool(value) for value in blue],
        "lightBlue": [bool(value) for value in light_blue],
        "red": [bool(value) for value in red],
        "orange": [bool(value) for value in orange],
        "yellow": [bool(value) for value in yellow],
        "buyCondition": [bool(value) for value in buy_condition],
        "sellCondition": [bool(value) for value in sell_condition],
        "bullish": [bool(value) for value in bullish],
        "bearish": [bool(value) for value in bearish],
        "buy": [bool(value) for value in buy_event],
        "sell": [bool(value) for value in sell_event],
        "rsi": finite(rsi),
        "stochRsiK": finite(k_line),
        "stochRsiD": finite(d_line),
        "momentumBuyScore": [int(value) for value in momentum_buy_score],
        "momentumSellScore": [int(value) for value in momentum_sell_score],
    },
}
