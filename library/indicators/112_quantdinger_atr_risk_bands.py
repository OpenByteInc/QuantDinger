# ============================================================
# QuantDinger ATR Risk Bands
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger ATR Risk Bands"
my_indicator_description = (
    "Plots close-centered Wilder ATR stop bands and optional reward-to-risk "
    "target bands, with an optional latest-bar trade planning summary."
)

# @param atr_period int 3 Wilder ATR period range=1:100:1
# @param atr_multiplier float 2.5 ATR distance multiplier range=0.01:10:0.01
# @param show_take_profit bool false Show reward-to-risk target bands
# @param reward_risk float 1.5 Target distance divided by stop distance range=1:10:0.1
# @param show_summary bool false Show latest stop and target values

atr_period = min(100, max(1, int(params.get("atr_period", 3))))
atr_multiplier = min(10.0, max(0.01, float(params.get("atr_multiplier", 2.5))))
show_take_profit = bool(params.get("show_take_profit", False))
reward_risk = min(10.0, max(1.0, float(params.get("reward_risk", 1.5))))
show_summary = bool(params.get("show_summary", False))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not (np.isfinite(high[index]) and np.isfinite(low[index])):
        continue
    if index == 0 or not np.isfinite(close[index - 1]):
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )

atr = np.full(n, np.nan, dtype=float)
if n >= atr_period:
    seed = true_range[:atr_period]
    if np.all(np.isfinite(seed)):
        atr[atr_period - 1] = float(np.mean(seed))
        for index in range(atr_period, n):
            if np.isfinite(true_range[index]):
                atr[index] = (
                    atr[index - 1] * (atr_period - 1) + true_range[index]
                ) / atr_period

upper_stop = [None for _ in range(n)]
lower_stop = [None for _ in range(n)]
upper_target = [None for _ in range(n)]
lower_target = [None for _ in range(n)]

for index in range(n):
    if not (np.isfinite(close[index]) and np.isfinite(atr[index])):
        continue
    distance = float(atr[index] * atr_multiplier)
    upper_stop[index] = float(close[index] + distance)
    lower_stop[index] = float(close[index] - distance)
    if show_take_profit:
        upper_target[index] = float(close[index] + distance * reward_risk)
        lower_target[index] = float(close[index] - distance * reward_risk)

summary_marks = [None for _ in range(n)]
if n and show_summary and upper_stop[-1] is not None:
    upper_target_text = (
        f"{upper_target[-1]:.2f}" if upper_target[-1] is not None else "hidden"
    )
    lower_target_text = (
        f"{lower_target[-1]:.2f}" if lower_target[-1] is not None else "hidden"
    )
    summary_marks[-1] = {
        "value": float(upper_stop[-1]),
        "text": (
            f"ATR {atr[-1]:.2f} · x{atr_multiplier:.2f}\n"
            f"Stops {lower_stop[-1]:.2f} / {upper_stop[-1]:.2f}\n"
            f"Targets {lower_target_text} / {upper_target_text}"
        ),
        "color": "#26A69A",
    }

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Upper ATR Stop", "data": upper_stop, "color": "rgba(0, 255, 0, 0.65)", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Lower ATR Stop", "data": lower_stop, "color": "rgba(255, 0, 0, 0.65)", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Upper ATR Target", "data": upper_target, "color": "rgba(255, 255, 255, 0.72)", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
        {"name": "Lower ATR Target", "data": lower_target, "color": "rgba(255, 214, 0, 0.72)", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
    ],
    "signals": [
        {"type": "sell", "text": "ATR", "name": "ATR Summary", "data": summary_marks, "color": "#26A69A", "renderMode": "events"},
    ],
    "layers": [],
}
