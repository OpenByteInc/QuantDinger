# ============================================================
# QuantDinger Alpha Trend
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Alpha Trend"
my_indicator_description = (
    "A money-flow or RSI-directed ATR trail with a two-bar comparison cloud "
    "and filtered reversal markers."
)

# @param multiplier float 1.0 ATR multiplier range=0.1:20.0:0.1
# @param period int 14 Common period range=2:300:1
# @param show_signals bool true Show reversal labels
# @param no_volume_mode bool false Use RSI instead of money flow

multiplier = float(params.get("multiplier", 1.0))
period = max(2, int(params.get("period", 14)))
show_signals = bool(params.get("show_signals", True))
no_volume_mode = bool(params.get("no_volume_mode", False))

df = df.copy()
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
volume = df["volume"].astype(float)
n = len(df)
previous_close = close.shift(1)
true_range = pd.concat(
    [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
).max(axis=1, skipna=True)
if n:
    true_range.iloc[0] = np.nan
average_range = true_range.rolling(window=period, min_periods=period).mean()

change = close.diff()
gain = change.clip(lower=0.0)
loss = (-change.clip(upper=0.0))
average_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
average_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
rsi = 100.0 - 100.0 / (1.0 + average_gain / average_loss.replace(0.0, np.nan))
rsi = rsi.where(average_loss != 0.0, 100.0).where(average_gain != 0.0, 0.0)

hlc3 = (high + low + close) / 3.0
raw_flow = hlc3 * volume
flow_change = hlc3.diff()
positive_flow = raw_flow.where(flow_change > 0.0, 0.0)
negative_flow = raw_flow.where(flow_change < 0.0, 0.0)
positive_sum = positive_flow.rolling(window=period, min_periods=period).sum()
negative_sum = negative_flow.rolling(window=period, min_periods=period).sum()
money_flow = 100.0 - 100.0 / (1.0 + positive_sum / negative_sum.replace(0.0, np.nan))
money_flow = money_flow.where(negative_sum != 0.0, 100.0)

direction_source = rsi if no_volume_mode else money_flow
up_threshold = (low - average_range * multiplier).to_numpy(dtype=float)
down_threshold = (high + average_range * multiplier).to_numpy(dtype=float)
direction_values = direction_source.to_numpy(dtype=float)
alpha_trend = np.full(n, np.nan, dtype=float)
previous_value = 0.0
for index in range(n):
    if not np.isfinite(direction_values[index]):
        continue
    if direction_values[index] >= 50.0 and np.isfinite(up_threshold[index]):
        current_value = max(up_threshold[index], previous_value)
    elif np.isfinite(down_threshold[index]):
        current_value = min(down_threshold[index], previous_value)
    else:
        continue
    alpha_trend[index] = current_value
    previous_value = current_value

delayed = np.concatenate((np.full(2, np.nan), alpha_trend[:-2])) if n >= 2 else np.full(n, np.nan)
buy_cross = np.zeros(n, dtype=bool)
sell_cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    if all(np.isfinite(value) for value in (alpha_trend[index], delayed[index], alpha_trend[index - 1], delayed[index - 1])):
        buy_cross[index] = alpha_trend[index] > delayed[index] and alpha_trend[index - 1] <= delayed[index - 1]
        sell_cross[index] = alpha_trend[index] < delayed[index] and alpha_trend[index - 1] >= delayed[index - 1]

def bars_since(events):
    result = np.full(n, np.nan, dtype=float)
    last = None
    for index, event in enumerate(events):
        if event:
            last = index
        if last is not None:
            result[index] = index - last
    return result

k1 = bars_since(buy_cross)
k2 = bars_since(sell_cross)
o1 = bars_since(np.concatenate(([False], buy_cross[:-1])))
o2 = bars_since(np.concatenate(([False], sell_cross[:-1])))
buy_events = buy_cross & np.isfinite(o1) & np.isfinite(k2) & (o1 > k2)
sell_events = sell_cross & np.isfinite(o2) & np.isfinite(k1) & (o2 > k1)

main_plot = [float(value) if np.isfinite(value) else None for value in alpha_trend]
delayed_plot = [float(value) if np.isfinite(value) else None for value in delayed]
cloud_plot = []
for index in range(n):
    if main_plot[index] is None or delayed_plot[index] is None:
        cloud_plot.append(None)
    else:
        rising = alpha_trend[index] > delayed[index] or (index > 0 and alpha_trend[index - 1] > (delayed[index - 1] if np.isfinite(delayed[index - 1]) else alpha_trend[index - 1]))
        cloud_plot.append({"upper": max(alpha_trend[index], delayed[index]), "lower": min(alpha_trend[index], delayed[index]), "color": "#00E60F" if rising else "#80000B"})

buy_marks = [float(delayed[index] * 0.9999) if show_signals and buy_events[index] and np.isfinite(delayed[index]) else None for index in range(n)]
sell_marks = [float(delayed[index] * 1.0001) if show_signals and sell_events[index] and np.isfinite(delayed[index]) else None for index in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Alpha Trend", "data": main_plot, "color": "#0022FC", "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Delayed Alpha Trend", "data": delayed_plot, "color": "#FC0400", "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Trend Cloud", "title": False, "data": cloud_plot, "color": "#00E60F", "type": "cloud", "opacity": 0.18, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "Buy", "data": buy_marks, "color": "#0022FC", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Sell", "data": sell_marks, "color": "#80000B", "renderMode": "events"},
    ],
    "layers": [],
}
