# ============================================================
# QuantDinger Position Size Planner
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only calculator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Position Size Planner"
my_indicator_description = (
    "Calculates position value and reward-to-risk from account risk, entry, "
    "stop, and target levels, then displays the complete trade plan."
)

# @param account_balance float 1000.0 Account balance range=1:100000000:1
# @param risk_percent float 2.0 Account risk percent range=0.01:100:0.01
# @param entry_price float 0.0 Entry price; zero uses latest close range=0:100000000:0.01
# @param stop_loss float 0.0 Stop price; zero uses 2 percent below entry range=0:100000000:0.01
# @param target_price float 0.0 Target price; zero uses 4 percent above entry range=0:100000000:0.01
# @param show_summary bool true Show position summary
# @param show_level_labels bool true Show entry, target, and stop labels
# @param display_bars int 30 Number of recent bars to display range=5:200:1

account_balance = max(1.0, float(params.get("account_balance", 1000.0)))
risk_percent = min(100.0, max(0.01, float(params.get("risk_percent", 2.0))))
entry_input = max(0.0, float(params.get("entry_price", 0.0)))
stop_input = max(0.0, float(params.get("stop_loss", 0.0)))
target_input = max(0.0, float(params.get("target_price", 0.0)))
show_summary = bool(params.get("show_summary", True))
show_level_labels = bool(params.get("show_level_labels", True))
display_bars = min(200, max(5, int(params.get("display_bars", 30))))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

latest_close = float(close[-1]) if n and np.isfinite(close[-1]) else 1.0
entry = entry_input if entry_input > 0.0 else latest_close
stop = stop_input if stop_input > 0.0 else entry * 0.98
target = target_input if target_input > 0.0 else entry * 1.04

stop_distance = entry - stop
target_distance = target - entry
stop_percent = stop_distance / entry * 100.0 if entry != 0.0 else 0.0
target_percent = target_distance / entry * 100.0 if entry != 0.0 else 0.0

if abs(stop_percent) > 0.000000001:
    reward_risk = target_percent / stop_percent
    position_value = abs(account_balance * risk_percent / stop_percent)
else:
    reward_risk = 0.0
    position_value = 0.0

start = max(0, n - display_bars)
entry_line = [None for _ in range(n)]
target_line = [None for _ in range(n)]
stop_line = [None for _ in range(n)]
profit_zone = [None for _ in range(n)]
risk_zone = [None for _ in range(n)]

for index in range(start, n):
    entry_line[index] = entry
    target_line[index] = target
    stop_line[index] = stop
    profit_zone[index] = {
        "upper": float(max(entry, target)),
        "lower": float(min(entry, target)),
        "color": "rgba(76, 175, 80, 0.16)",
    }
    risk_zone[index] = {
        "upper": float(max(entry, stop)),
        "lower": float(min(entry, stop)),
        "color": "rgba(255, 82, 82, 0.16)",
    }

target_labels = [None for _ in range(n)]
stop_labels = [None for _ in range(n)]
if n and show_level_labels:
    target_text = f"Target {target:.2f} · {target_percent:.2f}%"
    if show_summary:
        target_text = (
            f"Target {target:.2f} · {target_percent:.2f}%\n"
            f"Entry {entry:.2f} · Size {position_value:.2f} · Risk {risk_percent:.2f}% · R/R {reward_risk:.2f}"
        )
    target_labels[-1] = {
        "value": target,
        "text": target_text,
        "color": "#4CAF50",
    }
    stop_labels[-1] = {
        "value": stop,
        "text": f"Stop {stop:.2f} · {stop_percent:.2f}%",
        "color": "#FF5252",
    }

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Entry", "data": entry_line, "color": "#787B86", "type": "line", "lineWidth": 2, "lineStyle": "dashed", "overlay": True},
        {"name": "Target", "data": target_line, "color": "#4CAF50", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Stop", "data": stop_line, "color": "#FF5252", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Profit Zone", "title": False, "data": profit_zone, "color": "#4CAF50", "type": "cloud", "opacity": 0.16, "overlay": True},
        {"name": "Risk Zone", "title": False, "data": risk_zone, "color": "#FF5252", "type": "cloud", "opacity": 0.16, "overlay": True},
    ],
    "signals": [
        {"type": "sell", "text": "Target", "name": "Target Plan", "data": target_labels, "color": "#4CAF50", "renderMode": "events"},
        {"type": "buy", "text": "Stop", "name": "Stop Plan", "data": stop_labels, "color": "#FF5252", "renderMode": "events"},
    ],
    "layers": [],
}
