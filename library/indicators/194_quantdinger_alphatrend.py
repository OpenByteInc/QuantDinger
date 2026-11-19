# ============================================================
# QuantDinger AlphaTrend
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger AlphaTrend"
my_indicator_description = (
    "Tracks an ATR-based price floor or ceiling selected by money flow or RSI, "
    "then compares the trail with its two-bar-delayed value to highlight reversals."
)

# @param multiplier float 1.0 ATR multiplier range=0.1:20.0:0.1
# @param period int 14 Common calculation period range=2:300:1
# @param price_source str Close Momentum price source values=Close,HLC3,HL2,OHLC4
# @param momentum_mode str Money_Flow Direction calculation values=Money_Flow,RSI
# @param show_cloud bool true Show trend cloud
# @param show_signals bool true Show filtered reversal labels
# @param main_line_color str #2962FF Main trail color
# @param delayed_line_color str #FF1744 Delayed trail color
# @param bullish_cloud_color str #00C853 Bullish cloud color
# @param bearish_cloud_color str #80000B Bearish cloud color
# @param bullish_signal_color str #2962FF Buy label color
# @param bearish_signal_color str #80000B Sell label color

multiplier = min(20.0, max(0.1, float(params.get("multiplier", 1.0))))
period = min(300, max(2, int(params.get("period", 14))))
price_source_name = str(params.get("price_source", "Close"))
if price_source_name not in ("Close", "HLC3", "HL2", "OHLC4"):
    price_source_name = "Close"
momentum_mode = str(params.get("momentum_mode", "Money_Flow"))
if momentum_mode not in ("Money_Flow", "RSI"):
    momentum_mode = "Money_Flow"
show_cloud = bool(params.get("show_cloud", True))
show_signals = bool(params.get("show_signals", True))
main_line_color = str(params.get("main_line_color", "#2962FF"))
delayed_line_color = str(params.get("delayed_line_color", "#FF1744"))
bullish_cloud_color = str(params.get("bullish_cloud_color", "#00C853"))
bearish_cloud_color = str(params.get("bearish_cloud_color", "#80000B"))
bullish_signal_color = str(params.get("bullish_signal_color", "#2962FF"))
bearish_signal_color = str(params.get("bearish_signal_color", "#80000B"))

df = df.copy()
open_values = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)


def rolling_mean(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(length - 1, len(source)):
        window = source[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def wilder_rsi(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) <= length:
        return result

    changes = np.diff(source, prepend=np.nan)
    gains = np.where(changes > 0.0, changes, 0.0)
    losses = np.where(changes < 0.0, -changes, 0.0)
    average_gain = float(np.mean(gains[1:length + 1]))
    average_loss = float(np.mean(losses[1:length + 1]))

    for index in range(length, len(source)):
        if index > length:
            average_gain = (average_gain * (length - 1) + gains[index]) / length
            average_loss = (average_loss * (length - 1) + losses[index]) / length
        if average_loss <= 1e-12 and average_gain <= 1e-12:
            result[index] = 50.0
        elif average_loss <= 1e-12:
            result[index] = 100.0
        elif average_gain <= 1e-12:
            result[index] = 0.0
        else:
            strength = average_gain / average_loss
            result[index] = 100.0 - 100.0 / (1.0 + strength)
    return result


def money_flow_index(typical_price, volumes, length):
    price = np.asarray(typical_price, dtype=float)
    raw_flow = price * np.asarray(volumes, dtype=float)
    changes = np.diff(price, prepend=np.nan)
    positive = np.where(changes > 0.0, raw_flow, 0.0)
    negative = np.where(changes < 0.0, raw_flow, 0.0)
    result = np.full(len(price), np.nan, dtype=float)

    for index in range(length - 1, len(price)):
        positive_sum = float(np.sum(positive[index - length + 1:index + 1]))
        negative_sum = float(np.sum(negative[index - length + 1:index + 1]))
        if negative_sum <= 1e-12 and positive_sum <= 1e-12:
            result[index] = 50.0
        elif negative_sum <= 1e-12:
            result[index] = 100.0
        elif positive_sum <= 1e-12:
            result[index] = 0.0
        else:
            ratio = positive_sum / negative_sum
            result[index] = 100.0 - 100.0 / (1.0 + ratio)
    return result


def bars_since(events):
    result = np.full(len(events), np.nan, dtype=float)
    last_event = -1
    for index, event in enumerate(events):
        if bool(event):
            last_event = index
        if last_event >= 0:
            result[index] = float(index - last_event)
    return result


def finite_line(values):
    return [float(value) if np.isfinite(value) else None for value in values]


hl2 = (high + low) / 2.0
hlc3 = (high + low + close) / 3.0
ohlc4 = (open_values + high + low + close) / 4.0
if price_source_name == "HLC3":
    price_source = hlc3
elif price_source_name == "HL2":
    price_source = hl2
elif price_source_name == "OHLC4":
    price_source = ohlc4
else:
    price_source = close

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
average_range = rolling_mean(true_range, period)

if momentum_mode == "RSI":
    direction_score = wilder_rsi(price_source, period)
else:
    direction_score = money_flow_index(hlc3, volume, period)

upper_candidate = low - average_range * multiplier
lower_candidate = high + average_range * multiplier
alpha_trend = np.full(n, np.nan, dtype=float)
previous_trail = 0.0
for index in range(n):
    if not np.isfinite(direction_score[index]) or not np.isfinite(average_range[index]):
        continue
    if direction_score[index] >= 50.0:
        previous_trail = max(previous_trail, float(upper_candidate[index]))
    else:
        previous_trail = min(previous_trail, float(lower_candidate[index]))
    alpha_trend[index] = previous_trail

delayed_trend = np.full(n, np.nan, dtype=float)
if n > 2:
    delayed_trend[2:] = alpha_trend[:-2]

buy_cross = np.zeros(n, dtype=bool)
sell_cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    current_values = np.array(
        [alpha_trend[index], delayed_trend[index], alpha_trend[index - 1], delayed_trend[index - 1]],
        dtype=float,
    )
    if not np.all(np.isfinite(current_values)):
        continue
    buy_cross[index] = (
        alpha_trend[index] > delayed_trend[index]
        and alpha_trend[index - 1] <= delayed_trend[index - 1]
    )
    sell_cross[index] = (
        alpha_trend[index] < delayed_trend[index]
        and alpha_trend[index - 1] >= delayed_trend[index - 1]
    )

prior_buy_cross = np.r_[False, buy_cross[:-1]] if n else np.array([], dtype=bool)
prior_sell_cross = np.r_[False, sell_cross[:-1]] if n else np.array([], dtype=bool)
bars_from_buy = bars_since(buy_cross)
bars_from_sell = bars_since(sell_cross)
bars_from_prior_buy = bars_since(prior_buy_cross)
bars_from_prior_sell = bars_since(prior_sell_cross)
buy_signal = (
    buy_cross
    & np.isfinite(bars_from_prior_buy)
    & np.isfinite(bars_from_sell)
    & (bars_from_prior_buy > bars_from_sell)
)
sell_signal = (
    sell_cross
    & np.isfinite(bars_from_prior_sell)
    & np.isfinite(bars_from_buy)
    & (bars_from_prior_sell > bars_from_buy)
)

cloud_data = []
for index in range(n):
    current = alpha_trend[index]
    delayed = delayed_trend[index]
    if not show_cloud or not np.isfinite(current) or not np.isfinite(delayed):
        cloud_data.append(None)
        continue

    if current > delayed:
        cloud_color = bullish_cloud_color
    elif current < delayed:
        cloud_color = bearish_cloud_color
    elif index >= 3 and alpha_trend[index - 1] > alpha_trend[index - 3]:
        cloud_color = bullish_cloud_color
    else:
        cloud_color = bearish_cloud_color
    cloud_data.append(
        {
            "upper": float(max(current, delayed)),
            "lower": float(min(current, delayed)),
            "color": cloud_color,
        }
    )

buy_marks = []
sell_marks = []
for index in range(n):
    if show_signals and buy_signal[index] and np.isfinite(delayed_trend[index]):
        buy_marks.append(float(delayed_trend[index] * 0.9999))
    else:
        buy_marks.append(None)
    if show_signals and sell_signal[index] and np.isfinite(delayed_trend[index]):
        sell_marks.append(float(delayed_trend[index] * 1.0001))
    else:
        sell_marks.append(None)

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {
            "name": "AlphaTrend",
            "data": finite_line(alpha_trend),
            "color": main_line_color,
            "type": "line",
            "lineWidth": 3,
            "overlay": True,
        },
        {
            "name": "Delayed AlphaTrend",
            "data": finite_line(delayed_trend),
            "color": delayed_line_color,
            "type": "line",
            "lineWidth": 3,
            "overlay": True,
        },
        {
            "name": "Trend Cloud",
            "title": False,
            "data": cloud_data,
            "color": bullish_cloud_color,
            "type": "cloud",
            "opacity": 0.20,
            "overlay": True,
        },
    ],
    "signals": [
        {
            "type": "buy",
            "name": "AlphaTrend Buy",
            "text": "BUY",
            "color": bullish_signal_color,
            "renderMode": "events",
            "data": buy_marks,
        },
        {
            "type": "sell",
            "name": "AlphaTrend Sell",
            "text": "SELL",
            "color": bearish_signal_color,
            "renderMode": "events",
            "data": sell_marks,
        },
    ],
    "layers": [],
}
