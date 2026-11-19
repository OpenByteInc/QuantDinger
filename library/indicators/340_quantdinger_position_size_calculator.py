# ============================================================
# QuantDinger Position Size Calculator
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Position Size Calculator"
my_indicator_description = "Calculates percentage stop and target distances, risk/reward, and account-risk position value with projected entry, stop, target, and summary labels."

# @param account_balance float 1000.0 Account balance range=0:1000000000:1
# @param risk_percentage float 2.0 Risk percentage range=-100:100:0.1
# @param entry_price float 0.0 Entry price range=-1000000000:1000000000:0.01
# @param stop_price float 0.0 Stop loss price range=-1000000000:1000000000:0.01
# @param target_price float 0.0 Target price range=-1000000000:1000000000:0.01
# @param show_position_label bool true Show position-size label
# @param position_label_offset int 30 Position label x-axis offset range=0:500:1
# @param show_rr_labels bool true Show risk/reward labels
# @param rr_label_offset int 10 Risk/reward label x-axis offset range=0:500:1

account_balance = float(params.get("account_balance", 1000.0))
risk_percentage = float(params.get("risk_percentage", 2.0))
entry_price = float(params.get("entry_price", 0.0))
stop_price = float(params.get("stop_price", 0.0))
target_price = float(params.get("target_price", 0.0))
show_position_label = bool(params.get("show_position_label", True))
position_label_offset = max(0, int(params.get("position_label_offset", 30)))
show_rr_labels = bool(params.get("show_rr_labels", True))
rr_label_offset = max(0, int(params.get("rr_label_offset", 10)))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _divide(numerator, denominator):
    if not np.all(np.isfinite([numerator, denominator])) or denominator == 0.0:
        return np.nan
    return numerator / denominator


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

stop_distance = entry_price - stop_price
target_distance = target_price - entry_price
stop_percentage = _divide(stop_distance, entry_price) * 100.0
target_percentage = _divide(target_distance, entry_price) * 100.0
risk_reward = _divide(target_percentage, stop_percentage)
position_size = _divide(account_balance * risk_percentage, stop_percentage)
levels_configured = bool(
    np.all(np.isfinite([entry_price, stop_price, target_price]))
    and min(entry_price, stop_price, target_price) > 0.0
    and stop_price != entry_price
    and target_price != entry_price
)

entry_plot = np.full(n, np.nan, dtype=float)
stop_plot = np.full(n, np.nan, dtype=float)
target_plot = np.full(n, np.nan, dtype=float)
if levels_configured:
    for index in range(max(0, n - 20), n):
        entry_plot[index] = entry_price
        stop_plot[index] = stop_price
        target_plot[index] = target_price

layers = []
future_anchor = n - 1 + 20 if n else 20
if levels_configured:
    layers.extend([
        {"type": "line", "role": "Entry", "startIndex": max(0, n - 20) + 20, "endIndex": future_anchor, "y1": entry_price, "y2": entry_price, "color": "rgba(120,123,134,0.35)", "lineWidth": 1},
        {"type": "line", "role": "Target", "startIndex": max(0, n - 20) + 20, "endIndex": future_anchor, "y1": target_price, "y2": target_price, "color": "rgba(76,175,80,0.35)", "lineWidth": 1},
        {"type": "line", "role": "Stop Loss", "startIndex": max(0, n - 20) + 20, "endIndex": future_anchor, "y1": stop_price, "y2": stop_price, "color": "rgba(255,82,82,0.35)", "lineWidth": 1},
        {"type": "zone", "role": "Reward", "startIndex": future_anchor, "endIndex": future_anchor + 1, "top": max(entry_price, target_price), "bottom": min(entry_price, target_price), "fillColor": "rgba(76,175,80,0.50)", "borderColor": "rgba(0,0,0,0)", "borderSize": 0},
        {"type": "zone", "role": "Risk", "startIndex": future_anchor, "endIndex": future_anchor + 1, "top": max(entry_price, stop_price), "bottom": min(entry_price, stop_price), "fillColor": "rgba(255,82,82,0.50)", "borderColor": "rgba(0,0,0,0)", "borderSize": 0},
    ])

def _number(value, decimals=2):
    return f"{value:.{decimals}f}" if np.isfinite(value) else "—"


if show_position_label:
    label_price = float((opened[-2] + high[-2] + low[-2] + close[-2]) / 4.0) if n >= 2 else float(close[-1]) if n else entry_price
    entry_text = f"{entry_price:g}" if levels_configured else "—"
    target_text = f"{target_price:g}" if levels_configured else "—"
    stop_text = f"{stop_price:g}" if levels_configured else "—"
    layers.append({
        "type": "label", "role": "Position Summary", "index": (n - 1 if n else 0) + position_label_offset,
        "price": label_price,
        "text": f"Account Balance : {account_balance:g}\nRisk % : {risk_percentage:g}\n\nEntry : {entry_text}\nTarget : {target_text}\nStop Loss : {stop_text}\n\nPosition Size : {_number(abs(position_size), 6)}\nR/R : {_number(risk_reward)} R",
        "color": "rgba(0,0,0,0)", "textColor": "#FFFFFF", "size": "normal",
    })
if show_rr_labels and levels_configured:
    label_index = (n - 1 if n else 0) + rr_label_offset
    layers.extend([
        {"type": "label", "role": "Target Summary", "index": label_index, "price": target_price, "text": f"Target: {target_distance:g} ({_number(target_percentage)}%)", "color": "#4CAF50", "textColor": "#FFFFFF", "size": "normal"},
        {"type": "label", "role": "Stop Summary", "index": label_index, "price": stop_price, "text": f"Stop: {stop_distance:g} ({_number(stop_percentage)}%)", "color": "#FF5252", "textColor": "#FFFFFF", "size": "normal"},
        {"type": "label", "role": "Risk Reward Summary", "index": label_index, "price": entry_price, "text": f"R/R: {_number(risk_reward)}", "color": "#787B86", "textColor": "#FFFFFF", "size": "normal", "tooltip": f"Account Balance : {account_balance:g}\nRisk % : {risk_percentage:g}\n\nPosition Size : {_number(abs(position_size), 6)}"},
    ])

tables = [{
    "title": "Position Size", "position": "top-right", "compact": True,
    "rows": [
        [{"text": "Stop %"}, {"text": _number(stop_percentage)}],
        [{"text": "Target %"}, {"text": _number(target_percentage)}],
        [{"text": "R/R"}, {"text": _number(risk_reward)}],
        [{"text": "Position"}, {"text": _number(abs(position_size), 6)}],
    ],
}]

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Entry", "title": False, "data": _finite(entry_plot), "color": "rgba(120,123,134,0.01)", "type": "line", "lineWidth": 1, "overlay": True, "offset": 20},
        {"name": "Target", "title": False, "data": _finite(target_plot), "color": "rgba(76,175,80,0.01)", "type": "line", "lineWidth": 1, "overlay": True, "offset": 20},
        {"name": "Stop Loss", "title": False, "data": _finite(stop_plot), "color": "rgba(255,82,82,0.01)", "type": "line", "lineWidth": 1, "overlay": True, "offset": 20},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [], "layers": layers, "tables": tables,
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "stopDistance": float(stop_distance), "targetDistance": float(target_distance),
        "stopPercentage": float(stop_percentage) if np.isfinite(stop_percentage) else None,
        "targetPercentage": float(target_percentage) if np.isfinite(target_percentage) else None,
        "riskReward": float(risk_reward) if np.isfinite(risk_reward) else None,
        "positionSize": float(position_size) if np.isfinite(position_size) else None,
        "absolutePositionSize": float(abs(position_size)) if np.isfinite(position_size) else None,
    },
}
