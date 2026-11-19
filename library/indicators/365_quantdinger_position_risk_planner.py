# ============================================================
# QuantDinger Position Risk Planner
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Position Risk Planner"
my_indicator_description = "Calculates entry, target, stop, risk/reward, position size, and rolling open P&L for long or short positions."

# @param position_type str Long Position direction values=Long,Short
# @param account_size float 1000 Account size range=0:1000000000:1
# @param risk_amount float 2 Risk amount range=0:1000000000:0.1
# @param risk_type str % Risk unit values=%,USD
# @param entry_price float 0 Manual entry price; zero leaves the plan unconfigured range=0:1000000000:0.01
# @param entry_from_cross bool false Derive entry from a delayed price/source cross
# @param cross_source str Open Cross source values=Open,High,Low,HL2,HLC3
# @param take_profit float 5 Take-profit value range=0:100000:0.1
# @param take_profit_type str % Take-profit unit values=ATR,%,Price,Range
# @param stop_loss float 5 Stop-loss value range=0:100000:0.1
# @param stop_loss_type str % Stop-loss unit values=ATR,%,Price,Range
# @param profit_color str #009688 Profit color
# @param loss_color str #F44336 Loss color

position_type = str(params.get("position_type", "Long"))
position_type = position_type if position_type in ("Long", "Short") else "Long"
account_size = max(0.0, float(params.get("account_size", 1000.0)))
risk_amount = max(0.0, float(params.get("risk_amount", 2.0)))
risk_type = str(params.get("risk_type", "%"))
risk_type = risk_type if risk_type in ("%", "USD") else "%"
entry_price = max(0.0, float(params.get("entry_price", 0.0)))
entry_from_cross = bool(params.get("entry_from_cross", False))
cross_source_name = str(params.get("cross_source", "Open"))
cross_source_name = cross_source_name if cross_source_name in ("Open", "High", "Low", "HL2", "HLC3") else "Open"
take_profit = max(0.0, float(params.get("take_profit", 5.0)))
take_profit_type = str(params.get("take_profit_type", "%"))
take_profit_type = take_profit_type if take_profit_type in ("ATR", "%", "Price", "Range") else "%"
stop_loss = max(0.0, float(params.get("stop_loss", 5.0)))
stop_loss_type = str(params.get("stop_loss_type", "%"))
stop_loss_type = stop_loss_type if stop_loss_type in ("ATR", "%", "Price", "Range") else "%"
profit_color = str(params.get("profit_color", "#009688"))
loss_color = str(params.get("loss_color", "#F44336"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _pine_round(value):
    return int(np.floor(float(value) + 0.5))


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
        true_range[index] = high_values[index] - low_values[index] if index == 0 else max(high_values[index] - low_values[index], abs(high_values[index] - close_values[index - 1]), abs(low_values[index] - close_values[index - 1]))
    return _rma(true_range, length)


def _range(high_values, low_values, length):
    result = np.full(len(high_values), np.nan, dtype=float)
    for index in range(length - 1, len(high_values)):
        result[index] = float(np.max(high_values[index - length + 1:index + 1]) - np.min(low_values[index - length + 1:index + 1]))
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

if cross_source_name == "High":
    cross_source = high
elif cross_source_name == "Low":
    cross_source = low
elif cross_source_name == "HL2":
    cross_source = (high + low) / 2.0
elif cross_source_name == "HLC3":
    cross_source = (high + low + close) / 3.0
else:
    cross_source = opened

cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    cross[index] = (close[index] > cross_source[index] and close[index - 1] <= cross_source[index - 1]) or (close[index] < cross_source[index] and close[index - 1] >= cross_source[index - 1])
plan_configured = entry_from_cross or entry_price > 0.0
entry = np.full(n, entry_price if plan_configured else np.nan, dtype=float)
if entry_from_cross:
    entry[:] = np.nan
    latest_entry = np.nan
    for index in range(n):
        if index > 0 and cross[index - 1]:
            latest_entry = opened[index]
        entry[index] = latest_entry

tp_length = max(_pine_round(take_profit), 1)
sl_length = max(_pine_round(stop_loss), 1)
tp_atr = _atr(high, low, close, tp_length)
sl_atr = _atr(high, low, close, sl_length)
tp_range = _range(high, low, tp_length)
sl_range = _range(high, low, sl_length)
direction = 1.0 if position_type == "Long" else -1.0
target = np.full(n, np.nan, dtype=float)
stop = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.isfinite(entry[index]):
        continue
    if take_profit_type == "ATR" and np.isfinite(tp_atr[index]):
        target[index] = entry[index] + tp_atr[index] * direction
    elif take_profit_type == "%":
        target[index] = entry[index] + take_profit / 100.0 * entry[index] * direction
    elif take_profit_type == "Price":
        target[index] = take_profit
    elif take_profit_type == "Range" and np.isfinite(tp_range[index]):
        target[index] = entry[index] + tp_range[index] * direction
    if stop_loss_type == "ATR" and np.isfinite(sl_atr[index]):
        stop[index] = entry[index] - sl_atr[index] * direction
    elif stop_loss_type == "%":
        stop[index] = entry[index] - stop_loss / 100.0 * entry[index] * direction
    elif stop_loss_type == "Price":
        stop[index] = stop_loss
    elif stop_loss_type == "Range" and np.isfinite(sl_range[index]):
        stop[index] = entry[index] - sl_range[index] * direction

open_pl = (close - entry) * direction
open_pl_percent = np.divide(open_pl, entry, out=np.full(n, np.nan, dtype=float), where=np.isfinite(entry) & (entry != 0.0))
stop_distance = (entry - stop) * direction
reward_distance = (target - entry) * direction
risk_reward = np.divide(np.abs(reward_distance), np.abs(stop_distance), out=np.full(n, np.nan, dtype=float), where=np.isfinite(stop_distance) & (stop_distance != 0.0))
risk_capital = risk_amount / 100.0 * account_size if risk_type == "%" else risk_amount
position_size = np.divide(risk_capital, stop_distance, out=np.full(n, np.nan, dtype=float), where=np.isfinite(stop_distance) & (stop_distance != 0.0))

profit_zone = [
    {"upper": float(max(target[index], entry[index])), "lower": float(min(target[index], entry[index])), "color": profit_color, "opacity": 0.20}
    if plan_configured and np.all(np.isfinite([target[index], entry[index]])) else None for index in range(n)
]
loss_zone = [
    {"upper": float(max(entry[index], stop[index])), "lower": float(min(entry[index], stop[index])), "color": loss_color, "opacity": 0.20}
    if plan_configured and np.all(np.isfinite([entry[index], stop[index]])) else None for index in range(n)
]
pl_zone = [
    {"upper": float(max(close[index], entry[index])), "lower": float(min(close[index], entry[index])), "color": profit_color if open_pl[index] > 0.0 else loss_color, "opacity": 0.12}
    if plan_configured and np.all(np.isfinite([close[index], entry[index]])) else None for index in range(n)
]
last_index = n - 1
summary = []
if n:
    summary = [{"text": "Metric"}, {"text": "Value"}], [
        {"text": "Configuration"}, {"text": "Ready" if plan_configured else "Set entry price"}
    ], [
        {"text": "Risk / Reward"}, {"text": "N/A" if not np.isfinite(risk_reward[last_index]) else f"{float(risk_reward[last_index]):.4f}"}
    ], [
        {"text": "Position Size"}, {"text": "N/A" if not np.isfinite(position_size[last_index]) else f"{float(position_size[last_index]):.4f}"}
    ], [
        {"text": "Open P&L"}, {"text": "N/A" if not np.isfinite(open_pl[last_index]) else f"{float(open_pl[last_index]):.4f}"}
    ]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Take Profit", "title": True, "data": _finite(target), "color": profit_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Entry", "title": True, "data": _finite(entry), "color": "#9E9E9E", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Stop Loss", "title": True, "data": _finite(stop), "color": loss_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Profit Zone", "title": False, "data": profit_zone, "color": profit_color, "type": "cloud", "overlay": True},
        {"name": "Loss Zone", "title": False, "data": loss_zone, "color": loss_color, "type": "cloud", "overlay": True},
        {"name": "Open P&L Zone", "title": False, "data": pl_zone, "color": profit_color, "type": "cloud", "overlay": True},
    ],
    "signals": [], "layers": [],
    "tables": [{"title": "Risk Plan", "position": "bottom-right", "compact": True, "columns": summary[0], "rows": list(summary[1:])}] if summary else [],
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "planConfigured": bool(plan_configured),
        "crossSource": _finite(cross_source), "cross": cross.tolist(), "entry": _finite(entry),
        "tpAtr": _finite(tp_atr), "slAtr": _finite(sl_atr), "tpRange": _finite(tp_range), "slRange": _finite(sl_range),
        "target": _finite(target), "stop": _finite(stop), "openPl": _finite(open_pl), "openPlPercent": _finite(open_pl_percent),
        "stopDistance": _finite(stop_distance), "rewardDistance": _finite(reward_distance), "riskReward": _finite(risk_reward),
        "riskCapital": float(risk_capital), "positionSize": _finite(position_size),
    },
}
