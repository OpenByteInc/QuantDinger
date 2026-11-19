# QuantDinger Smart Money Volume Index
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name = "QuantDinger Smart Money Volume Index"
my_indicator_description = "Compares positive- and negative-volume participation through normalized PVI/NVI money-flow ratios."

# @param display_mode str Net Display mode values=Net,Compare
# @param index_period int 25 Index period range=1:500:1
# @param volume_flow_period int 14 Volume-flow RSI period range=1:200:1
# @param normalization_period int 500 Normalization period range=1:5000:1
# @param high_interest_threshold float 0.9 High-interest threshold range=0.01:0.99:0.01
# @param buy_color str #00FFBB Buy color
# @param sell_color str #FF1100 Sell color

mode = str(params.get("display_mode", "Net"))
mode = mode if mode in ("Net", "Compare") else "Net"
index_period = max(1, min(500, int(params.get("index_period", 25))))
rsi_length = max(1, min(200, int(params.get("volume_flow_period", 14))))
normalization_period = max(1, min(5000, int(params.get("normalization_period", 500))))
threshold = max(0.01, min(0.99, float(params.get("high_interest_threshold", 0.9))))
buy_color = str(params.get("buy_color", "#00FFBB"))
sell_color = str(params.get("sell_color", "#FF1100"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


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
        if not np.isfinite(value):
            continue
        if not np.isfinite(state):
            seed.append(float(value))
            if len(seed) == length:
                state = float(np.mean(seed)); result[index] = state
        else:
            state = (state * (length - 1.0) + float(value)) / length; result[index] = state
    return result


def _rsi(values, length):
    change = np.diff(values, prepend=np.nan)
    gain = np.where(np.isfinite(change), np.maximum(change, 0.0), np.nan)
    loss = np.where(np.isfinite(change), np.maximum(-change, 0.0), np.nan)
    average_gain = _rma(gain, length); average_loss = _rma(loss, length)
    result = np.full(len(values), np.nan)
    valid = np.isfinite(average_gain) & np.isfinite(average_loss)
    result[valid & (average_loss == 0.0)] = 100.0
    both_zero = valid & (average_gain == 0.0) & (average_loss == 0.0)
    result[both_zero] = 50.0
    normal = valid & (average_loss > 0.0)
    result[normal] = 100.0 - 100.0 / (1.0 + average_gain[normal] / average_loss[normal])
    return result


def _rolling_sum(values, length):
    result = np.full(len(values), np.nan)
    for index in range(length - 1, len(values)):
        window = values[index-length+1:index+1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.sum(window))
    return result


def _rolling_max(values, length):
    result = np.full(len(values), np.nan)
    for index in range(len(values)):
        window = values[max(0, index-length+1):index+1]
        finite = window[np.isfinite(window)]
        if len(finite):
            result[index] = float(np.max(finite))
    return result


frame = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in frame.columns:
        frame[column] = 0.0
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
close = frame["close"].to_numpy(float); volume = frame["volume"].to_numpy(float); n = len(frame)
pvi = np.full(n, np.nan); nvi = np.full(n, np.nan)
if n:
    pvi[0] = nvi[0] = 1000.0
for index in range(1, n):
    previous = close[index-1]
    change = 0.0 if not np.isfinite(previous) or previous == 0.0 else (close[index] - previous) / previous
    pvi[index] = pvi[index-1] * (1.0 + change) if volume[index] > volume[index-1] else pvi[index-1]
    nvi[index] = nvi[index-1] * (1.0 + change) if volume[index] < volume[index-1] else nvi[index-1]
dumb_flow = pvi - _ema(pvi, 255); smart_flow = nvi - _ema(nvi, 255)
dumb_rsi = _rsi(dumb_flow, rsi_length); smart_rsi = _rsi(smart_flow, rsi_length)
buy_ratio = np.divide(smart_rsi, dumb_rsi, out=np.full(n, np.nan), where=np.isfinite(dumb_rsi) & (dumb_rsi != 0.0))
sell_ratio = np.divide(100.0-smart_rsi, 100.0-dumb_rsi, out=np.full(n, np.nan), where=np.isfinite(dumb_rsi) & ((100.0-dumb_rsi) != 0.0))
buy_sum = _rolling_sum(buy_ratio, index_period); sell_sum = _rolling_sum(sell_ratio, index_period)
peak = _rolling_max(np.fmax(buy_sum, sell_sum), normalization_period)
buy_index = np.divide(buy_sum, peak, out=np.full(n, np.nan), where=np.isfinite(peak) & (peak != 0.0))
sell_index = np.divide(sell_sum, peak, out=np.full(n, np.nan), where=np.isfinite(peak) & (peak != 0.0))
net_index = buy_index - sell_index
buy_signal_source = buy_index if mode == "Compare" else net_index
sell_signal_source = sell_index if mode == "Compare" else -net_index
high_buy = np.zeros(n, bool); high_sell = np.zeros(n, bool)
for index in range(1, n):
    high_buy[index] = np.isfinite(buy_signal_source[index:index+1]).all() and np.isfinite(buy_signal_source[index-1]) and buy_signal_source[index] > threshold >= buy_signal_source[index-1]
    high_sell[index] = np.isfinite(sell_signal_source[index:index+1]).all() and np.isfinite(sell_signal_source[index-1]) and sell_signal_source[index] > threshold >= sell_signal_source[index-1]

plots = [
    {"name":"Zero","title":True,"data":[0.0]*n,"color":"#808080","type":"line","lineWidth":1,"overlay":False},
    {"name":"Buy Interest","title":True,"data":_finite(buy_index) if mode=="Compare" else [None]*n,"color":buy_color,"type":"line","lineWidth":2,"overlay":False},
    {"name":"Sell Interest","title":True,"data":_finite(-sell_index) if mode=="Compare" else [None]*n,"color":sell_color,"type":"line","lineWidth":2,"overlay":False},
    {"name":"Net Interest","title":True,"data":_finite(net_index) if mode=="Net" else [None]*n,"color":buy_color,"negativeColor":sell_color,"type":"histogram","lineWidth":2,"overlay":False},
    {"name":"High Buy","title":True,"data":[threshold]*n,"color":buy_color,"type":"line","lineWidth":1,"lineStyle":"dashed","overlay":False},
    {"name":"High Sell","title":True,"data":[-threshold]*n,"color":sell_color,"type":"line","lineWidth":1,"lineStyle":"dashed","overlay":False},
]
output = {
    "schema_version":2,"name":my_indicator_name,"plots":plots,
    "signals":[
        {"type":"buy","name":"High Smart Money Buy Interest","text":"BUY","data":[{"active":True,"price":float(frame["low"].iloc[i]),"text":"BUY","color":buy_color} if high_buy[i] else None for i in range(n)],"color":buy_color,"renderMode":"events","overlay":True},
        {"type":"sell","name":"High Smart Money Sell Interest","text":"SELL","data":[{"active":True,"price":float(frame["high"].iloc[i]),"text":"SELL","color":sell_color} if high_sell[i] else None for i in range(n)],"color":sell_color,"renderMode":"events","overlay":True},
    ],
    "layers":[],"tables":[],"bar_colors":[buy_color if np.isfinite(net_index[i]) and net_index[i]>0 else sell_color if np.isfinite(net_index[i]) and net_index[i]<0 else None for i in range(n)],"background_colors":[None]*n,
    "calculatedVars":{"pvi":_finite(pvi),"nvi":_finite(nvi),"dumbFlow":_finite(dumb_flow),"smartFlow":_finite(smart_flow),"dumbRsi":_finite(dumb_rsi),"smartRsi":_finite(smart_rsi),"buyRatio":_finite(buy_ratio),"sellRatio":_finite(sell_ratio),"buySum":_finite(buy_sum),"sellSum":_finite(sell_sum),"peak":_finite(peak),"buyIndex":_finite(buy_index),"sellIndex":_finite(sell_index),"netIndex":_finite(net_index),"highBuy":high_buy.tolist(),"highSell":high_sell.tolist()},
}
