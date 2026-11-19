# ============================================================
# QuantDinger ITG Scalper
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger ITG Scalper"
my_indicator_description = (
    "Alternates confirmed entries on triple-EMA direction changes and can "
    "filter them with a same-symbol current or higher-timeframe MACD regime."
)

indicator_timeframes = ["1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H", "6H", "8H", "12H", "1D", "1W"]

# @param show_tema bool true Show triple EMA
# @param color_trend bool true Color triple EMA by direction
# @param show_signals bool true Show buy and sell signals
# @param use_price_labels bool true Show entry prices on signals
# @param show_percent bool false Show change since last transaction
# @param noise_filter bool true Enable MACD noise filter
# @param use_current_timeframe bool true Use chart timeframe for filter
# @param filter_timeframe str 1H Custom filter timeframe values=1m,3m,5m,15m,30m,1H,2H,4H,6H,8H,12H,1D,1W
# @param tema_length int 14 Triple EMA length range=1:500:1
# @param fast_length int 12 Filter fast EMA range=1:500:1
# @param slow_length int 26 Filter slow EMA range=1:500:1
# @param signal_length int 9 Filter signal SMA range=1:500:1

show_tema = bool(params.get("show_tema", True))
color_trend = bool(params.get("color_trend", True))
show_signals = bool(params.get("show_signals", True))
use_price_labels = bool(params.get("use_price_labels", True))
show_percent = bool(params.get("show_percent", False))
noise_filter = bool(params.get("noise_filter", True))
use_current_timeframe = bool(params.get("use_current_timeframe", True))
filter_timeframe = str(params.get("filter_timeframe", "1H"))
if filter_timeframe not in indicator_timeframes:
    filter_timeframe = "1H"
tema_length = min(500, max(1, int(params.get("tema_length", 14))))
fast_length = min(500, max(1, int(params.get("fast_length", 12))))
slow_length = min(500, max(1, int(params.get("slow_length", 26))))
signal_length = min(500, max(1, int(params.get("signal_length", 9))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 3600.0
times = df["time"].to_numpy(dtype=float)
n = len(df)
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0


def _ema(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    state = np.nan
    for index, value in enumerate(source):
        if np.isfinite(value):
            state = value if not np.isfinite(state) else alpha * value + (1.0 - alpha) * state
            result[index] = state
    return result


def _sma(values, length):
    return pd.Series(np.asarray(values, dtype=float)).rolling(length, min_periods=length).mean().to_numpy(dtype=float)


ema_one = _ema(close, tema_length)
ema_two = _ema(ema_one, tema_length)
ema_three = _ema(ema_two, tema_length)
tema = 3.0 * (ema_one - ema_two) + ema_three
tema_up = np.zeros(n, dtype=bool)
for index in range(1, n):
    tema_up[index] = np.isfinite(tema[index]) and np.isfinite(tema[index - 1]) and tema[index] >= tema[index - 1]
tema_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    tema_down[index] = np.isfinite(tema[index]) and np.isfinite(tema[index - 1]) and tema[index] < tema[index - 1]

timeframe_seconds = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600,
    "8H": 28800, "12H": 43200, "1D": 86400, "1W": 604800,
}
try:
    chart_timeframe = str(ctx.timeframe or "")
except Exception:
    chart_timeframe = ""


def _group_key(timestamp_ms, timeframe):
    timestamp = pd.Timestamp(timestamp_ms, unit="ms", tz="UTC")
    if timeframe == "1W":
        start = timestamp.normalize() - pd.Timedelta(days=int(timestamp.weekday()))
        return int(start.value // 1000000)
    if timeframe == "1D":
        return int(timestamp.normalize().value // 1000000)
    seconds = timeframe_seconds[timeframe]
    return int(timestamp_ms // (seconds * 1000.0))


selected_timeframe = chart_timeframe if use_current_timeframe and chart_timeframe in timeframe_seconds else filter_timeframe
use_local_htf = (
    selected_timeframe in timeframe_seconds and chart_timeframe in timeframe_seconds
    and timeframe_seconds[selected_timeframe] > timeframe_seconds[chart_timeframe]
)
use_lower_tf = (
    selected_timeframe in timeframe_seconds and chart_timeframe in timeframe_seconds
    and timeframe_seconds[selected_timeframe] < timeframe_seconds[chart_timeframe]
)
lower_counts = np.zeros(n, dtype=int)

if use_lower_tf:
    try:
        lower_data = request_lower_tf(selected_timeframe, fields=["close"])
    except Exception:
        lower_data = {}
    lower_closes = lower_data.get("close", [[] for _ in range(n)]) if isinstance(lower_data, dict) else [[] for _ in range(n)]
    filter_macd = np.full(n, np.nan, dtype=float)
    filter_signal = np.full(n, np.nan, dtype=float)
    fast_state = np.nan
    slow_state = np.nan
    fast_alpha = 2.0 / (fast_length + 1.0)
    slow_alpha = 2.0 / (slow_length + 1.0)
    macd_window = []
    for index in range(n):
        values = lower_closes[index] if index < len(lower_closes) else []
        lower_counts[index] = len(values)
        for value in values:
            value = float(value)
            if not np.isfinite(value):
                continue
            fast_state = value if not np.isfinite(fast_state) else fast_alpha * value + (1.0 - fast_alpha) * fast_state
            slow_state = value if not np.isfinite(slow_state) else slow_alpha * value + (1.0 - slow_alpha) * slow_state
            macd_value = fast_state - slow_state
            macd_window.append(macd_value)
            if len(macd_window) > signal_length:
                macd_window.pop(0)
        if np.isfinite(fast_state) and np.isfinite(slow_state):
            filter_macd[index] = fast_state - slow_state
        if len(macd_window) == signal_length:
            filter_signal[index] = float(np.mean(macd_window))
elif use_local_htf:
    group_keys = [_group_key(times[index], selected_timeframe) for index in range(n)]
    group_ends = []
    group_closes = []
    for index in range(n):
        if index == n - 1 or group_keys[index + 1] != group_keys[index]:
            group_ends.append(index)
            group_closes.append(float(close[index]))
    group_closes = np.asarray(group_closes, dtype=float)
    group_macd = _ema(group_closes, fast_length) - _ema(group_closes, slow_length)
    group_signal = _sma(group_macd, signal_length)
    filter_macd = np.full(n, np.nan, dtype=float)
    filter_signal = np.full(n, np.nan, dtype=float)
    group_start = 0
    for group_index, group_end in enumerate(group_ends):
        previous = max(0, group_index - 1)
        for index in range(group_start, group_end):
            if group_index > 0:
                filter_macd[index] = group_macd[previous]
                filter_signal[index] = group_signal[previous]
        filter_macd[group_end] = group_macd[group_index]
        filter_signal[group_end] = group_signal[group_index]
        group_start = group_end + 1
else:
    filter_macd = _ema(close, fast_length) - _ema(close, slow_length)
    filter_signal = _sma(filter_macd, signal_length)

if noise_filter:
    filter_buy = np.isfinite(filter_macd) & np.isfinite(filter_signal) & (filter_macd >= filter_signal)
    filter_sell = np.isfinite(filter_macd) & np.isfinite(filter_signal) & (filter_macd < filter_signal)
else:
    filter_buy = np.ones(n, dtype=bool)
    filter_sell = np.ones(n, dtype=bool)

long_event = np.zeros(n, dtype=bool)
short_event = np.zeros(n, dtype=bool)
last_transaction = np.zeros(n, dtype=bool)
buy_price = np.zeros(n, dtype=float)
sell_price = np.zeros(n, dtype=float)
percent_change = np.zeros(n, dtype=float)
good_short = np.zeros(n, dtype=bool)
state_long = False
state_buy_price = 0.0
state_sell_price = 0.0

for index in range(n):
    long_event[index] = tema_up[index] and not state_long and filter_buy[index]
    short_event[index] = tema_down[index] and state_long and filter_sell[index]
    if long_event[index]:
        state_buy_price = float(close[index])
        state_long = True
    if short_event[index]:
        state_sell_price = float(close[index])
        state_long = False
        good_short[index] = state_sell_price > state_buy_price
    buy_price[index] = state_buy_price
    sell_price[index] = state_sell_price
    last_transaction[index] = state_long
    if state_long and state_buy_price != 0.0:
        percent_change[index] = 100.0 * (close[index] - state_buy_price) / state_buy_price
    elif not state_long and state_sell_price != 0.0:
        percent_change[index] = 100.0 * (close[index] - state_sell_price) / state_sell_price

tema_plot = []
for index in range(n):
    if not show_tema or not np.isfinite(tema[index]):
        tema_plot.append(None)
    else:
        color = "#00E676" if tema_up[index] else "#F44336" if tema_down[index] else "#00FFFF"
        if not color_trend:
            color = "#00FFFF"
        tema_plot.append({"value": float(tema[index]), "color": color})

buy_marks = [None] * n
sell_marks = [None] * n
display_event_indexes = set(
    [index for index in range(n) if long_event[index] or short_event[index]][-16:]
)
for index in range(n):
    if show_signals and index in display_event_indexes and long_event[index]:
        buy_marks[index] = {"value": float(low[index]), "text": f"{close[index]:.2f}" if use_price_labels else "BUY", "color": "#00E676"}
    if show_signals and index in display_event_indexes and short_event[index]:
        sell_marks[index] = {"value": float(high[index]), "text": f"{close[index]:.2f}" if use_price_labels else "SELL", "color": "#F44336" if good_short[index] else "#FFEB3B"}

layers = []
if show_percent and n:
    color = "#00E676" if percent_change[-1] >= 0.0 else "#FF9800"
    layers.append({
        "type": "label", "role": "Transaction Change", "index": n - 1,
        "price": float(high[-1]), "text": f"{np.trunc(percent_change[-1] * 100.0) / 100.0:.2f}%",
        "anchor": "above", "color": "rgba(0,0,0,0)",
        "borderColor": "rgba(0,0,0,0)", "textColor": color, "fontSize": 10,
    })


def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "TEMA", "data": tema_plot, "color": "#00FFFF", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite_or_none(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Buy", "text": "BUY", "data": buy_marks, "color": "#00E676", "renderMode": "events" if show_signals else "hidden"},
        {"type": "sell", "name": "Sell", "text": "SELL", "data": sell_marks, "color": "#F44336", "renderMode": "events" if show_signals else "hidden"},
    ],
    "layers": layers,
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "emaOne": _finite_or_none(ema_one), "emaTwo": _finite_or_none(ema_two),
        "emaThree": _finite_or_none(ema_three), "tema": _finite_or_none(tema),
        "temaUp": tema_up.tolist(), "temaDown": tema_down.tolist(),
        "filterMacd": _finite_or_none(filter_macd), "filterSignal": _finite_or_none(filter_signal),
        "filterBuy": filter_buy.tolist(), "filterSell": filter_sell.tolist(),
        "longEvent": long_event.tolist(), "shortEvent": short_event.tolist(),
        "lastTransaction": last_transaction.tolist(), "buyPrice": _finite_or_none(buy_price),
        "sellPrice": _finite_or_none(sell_price), "percentChange": _finite_or_none(percent_change),
        "goodShort": good_short.tolist(), "selectedTimeframe": selected_timeframe,
        "usedLocalHtf": use_local_htf, "usedLowerTf": use_lower_tf,
        "lowerCounts": lower_counts.tolist(),
    },
}
