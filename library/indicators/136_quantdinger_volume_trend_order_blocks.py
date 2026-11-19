# ============================================================
# QuantDinger Volume-Trend Order Blocks
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Volume-Trend Order Blocks"
my_indicator_description = (
    "Builds trend-qualified pivot order blocks, splits each zone by local "
    "buy/sell volume, removes invalidated blocks, and marks filtered retests."
)

# @param volatility_length int 50 Volatility SMA length range=2:250:1
# @param volatility_multiplier float 3.5 Volatility multiplier range=0.1:20.0:0.1
# @param show_trend_cloud bool true Show trend cloud
# @param pivot_strength int 7 Pivot strength range=1:50:1
# @param delete_on_break bool true Delete block on complete break
# @param show_bullish_retests bool true Show bullish retests
# @param minimum_buy_percent float 50.0 Minimum buy volume percent range=0.0:100.0:5.0
# @param show_bearish_retests bool true Show bearish retests
# @param minimum_sell_percent float 50.0 Minimum sell volume percent range=0.0:100.0:5.0

volatility_length = min(250, max(2, int(params.get("volatility_length", 50))))
volatility_multiplier = min(20.0, max(0.1, float(params.get("volatility_multiplier", 3.5))))
show_trend_cloud = bool(params.get("show_trend_cloud", True))
pivot_strength = min(50, max(1, int(params.get("pivot_strength", 7))))
delete_on_break = bool(params.get("delete_on_break", True))
show_bullish_retests = bool(params.get("show_bullish_retests", True))
minimum_buy_percent = min(100.0, max(0.0, float(params.get("minimum_buy_percent", 50.0))))
show_bearish_retests = bool(params.get("show_bearish_retests", True))
minimum_sell_percent = min(100.0, max(0.0, float(params.get("minimum_sell_percent", 50.0))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

bar_range = high - low
custom_volatility = (
    pd.Series(bar_range, dtype=float)
    .rolling(volatility_length, min_periods=volatility_length)
    .mean()
    .to_numpy(dtype=float)
)
midpoint = (high + low) / 2.0
lower_band = np.full(n, np.nan, dtype=float)
upper_band = np.full(n, np.nan, dtype=float)
market_trend = np.ones(n, dtype=int)
trend_stop = np.full(n, np.nan, dtype=float)

for index in range(n):
    raw_lower = midpoint[index] - volatility_multiplier * custom_volatility[index]
    raw_upper = midpoint[index] + volatility_multiplier * custom_volatility[index]
    previous_lower = lower_band[index - 1] if index > 0 and np.isfinite(lower_band[index - 1]) else 0.0
    previous_upper = upper_band[index - 1] if index > 0 and np.isfinite(upper_band[index - 1]) else 0.0
    previous_close = close[index - 1] if index > 0 else np.nan

    if np.isfinite(raw_lower) and (raw_lower > previous_lower or (np.isfinite(previous_close) and previous_close < previous_lower)):
        lower_band[index] = float(raw_lower)
    else:
        lower_band[index] = float(previous_lower)
    if np.isfinite(raw_upper) and (raw_upper < previous_upper or (np.isfinite(previous_close) and previous_close > previous_upper)):
        upper_band[index] = float(raw_upper)
    else:
        upper_band[index] = float(previous_upper)

    if index == 0 or not np.isfinite(custom_volatility[index - 1]):
        market_trend[index] = 1
    elif trend_stop[index - 1] == upper_band[index - 1]:
        market_trend[index] = 1 if close[index] > upper_band[index] else -1
    else:
        market_trend[index] = -1 if close[index] < lower_band[index] else 1
    trend_stop[index] = lower_band[index] if market_trend[index] == 1 else upper_band[index]

pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
for confirmation_index in range(2 * pivot_strength, n):
    pivot_index = confirmation_index - pivot_strength
    first_index = pivot_index - pivot_strength
    last_index = pivot_index + pivot_strength + 1
    if high[pivot_index] >= float(np.max(high[first_index:last_index])):
        pivot_high[confirmation_index] = float(high[pivot_index])
    if low[pivot_index] <= float(np.min(low[first_index:last_index])):
        pivot_low[confirmation_index] = float(low[pivot_index])

blocks = []
active_block = None
bullish_retest = np.zeros(n, dtype=bool)
bearish_retest = np.zeros(n, dtype=bool)

for index in range(n):
    pivot_index = index - pivot_strength
    new_top = np.nan
    new_bottom = np.nan
    new_trend = 0
    if pivot_index >= 0 and np.isfinite(custom_volatility[index]):
        if market_trend[index] == 1 and np.isfinite(pivot_low[index]):
            new_top = float(min(open_price[pivot_index], close[pivot_index]))
            new_bottom = float(new_top - custom_volatility[index])
            new_trend = 1
        elif market_trend[index] == -1 and np.isfinite(pivot_high[index]):
            new_bottom = float(max(open_price[pivot_index], close[pivot_index]))
            new_top = float(new_bottom + custom_volatility[index])
            new_trend = -1

    if np.isfinite(new_top) and np.isfinite(new_bottom):
        overlaps = False
        if active_block is not None and not active_block["deleted"]:
            overlaps = not (
                new_bottom > active_block["top"]
                or new_top < active_block["bottom"]
            )
        if not overlaps:
            if active_block is not None and not active_block["deleted"]:
                active_block["end"] = int(pivot_index)

            first_volume_index = max(0, index - pivot_strength)
            buy_volume = 0.0
            sell_volume = 0.0
            for volume_index in range(first_volume_index, index + 1):
                if close[volume_index] >= open_price[volume_index]:
                    buy_volume += float(volume[volume_index])
                else:
                    sell_volume += float(volume[volume_index])
            total_volume = buy_volume + sell_volume
            buy_ratio = buy_volume / total_volume if total_volume > 0.0 else 0.5
            active_block = {
                "start": int(pivot_index),
                "end": int(index),
                "top": float(new_top),
                "bottom": float(new_bottom),
                "buy_ratio": float(buy_ratio),
                "trend": int(new_trend),
                "deleted": False,
            }
            blocks.append(active_block)

    if active_block is not None and not active_block["deleted"]:
        broken = (
            active_block["trend"] == 1 and high[index] < active_block["bottom"]
        ) or (
            active_block["trend"] == -1 and low[index] > active_block["top"]
        )
        if delete_on_break and broken:
            active_block["deleted"] = True
            active_block = None

    if active_block is not None and not active_block["deleted"]:
        active_block["end"] = int(index)
        market_changed = index > 0 and market_trend[index] != market_trend[index - 1]
        pivot_low_absent = not np.isfinite(pivot_low[index])
        pivot_high_absent = not np.isfinite(pivot_high[index])
        crossed_above_top = (
            index > 0
            and low[index] > active_block["top"]
            and low[index - 1] <= active_block["top"]
        )
        crossed_below_bottom = (
            index > 0
            and high[index] < active_block["bottom"]
            and high[index - 1] >= active_block["bottom"]
        )
        bullish_retest[index] = (
            show_bullish_retests
            and crossed_above_top
            and pivot_low_absent
            and active_block["buy_ratio"] >= minimum_buy_percent / 100.0
            and not market_changed
        )
        bearish_retest[index] = (
            show_bearish_retests
            and crossed_below_bottom
            and pivot_high_absent
            and (1.0 - active_block["buy_ratio"]) >= minimum_sell_percent / 100.0
            and not market_changed
        )

trend_stop_data = []
cloud_upper = []
cloud_lower = []
for index in range(n):
    trend_changed = index == 0 or market_trend[index] != market_trend[index - 1]
    color = "#00BFA5" if market_trend[index] == 1 else "#F50078"
    if np.isfinite(custom_volatility[index]) and not trend_changed:
        trend_stop_data.append({"value": float(trend_stop[index]), "color": color})
        if show_trend_cloud:
            cloud_color = "rgba(0, 191, 165, 0.35)" if market_trend[index] == 1 else "rgba(245, 0, 120, 0.35)"
            cloud_upper.append({"value": float(max(close[index], trend_stop[index])), "color": cloud_color})
            cloud_lower.append({"value": float(min(close[index], trend_stop[index])), "color": cloud_color})
        else:
            cloud_upper.append(None)
            cloud_lower.append(None)
    else:
        trend_stop_data.append(None)
        cloud_upper.append(None)
        cloud_lower.append(None)

layers = []
for block in blocks:
    if block["deleted"]:
        continue
    split_price = block["bottom"] + (block["top"] - block["bottom"]) * block["buy_ratio"]
    bullish = block["trend"] == 1
    border_color = "#00BFA5" if bullish else "#F50078"
    top_fill = "rgba(0, 191, 165, 0.15)" if bullish else "rgba(245, 0, 120, 0.25)"
    bottom_fill = "rgba(0, 191, 165, 0.25)" if bullish else "rgba(245, 0, 120, 0.15)"
    sell_percent = round((1.0 - block["buy_ratio"]) * 100.0)
    buy_percent = round(block["buy_ratio"] * 100.0)
    layers.append(
        {
            "type": "zone",
            "startIndex": int(block["start"]),
            "endIndex": int(block["end"]),
            "top": float(block["top"]),
            "bottom": float(split_price),
            "fillColor": top_fill,
            "borderColor": border_color,
            "borderSize": 1,
            "text": f"Sell: {sell_percent}%",
            "textColor": "#E0E0E0",
        }
    )
    layers.append(
        {
            "type": "zone",
            "startIndex": int(block["start"]),
            "endIndex": int(block["end"]),
            "top": float(split_price),
            "bottom": float(block["bottom"]),
            "fillColor": bottom_fill,
            "borderColor": border_color,
            "borderSize": 1,
            "text": f"Buy: {buy_percent}%",
            "textColor": "#E0E0E0",
        }
    )

layers = layers[-500:]
bullish_data = [None for _ in range(n)]
bearish_data = [None for _ in range(n)]
for index in range(n):
    if bullish_retest[index]:
        bullish_data[index] = {"value": float(low[index]), "text": "+", "color": "#00BFA5"}
    if bearish_retest[index]:
        bearish_data[index] = {"value": float(high[index]), "text": "+", "color": "#F50078"}

plots = [
    {"name": "Trend Stop", "data": trend_stop_data, "color": "#00BFA5", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Trend Cloud Upper", "title": False, "data": cloud_upper, "color": "#00BFA5", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Trend Cloud Lower", "title": False, "data": cloud_lower, "color": "#00BFA5", "type": "line", "lineWidth": 1, "overlay": True},
]
plots = [plot for plot in plots if any(item is not None for item in plot["data"])]
plots.append(
    {
        "name": "Price Reference",
        "title": False,
        "data": [float(value) for value in close],
        "color": "rgba(255, 255, 255, 0.01)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    }
)
signals = [
    {"type": "buy", "text": "+", "name": "Bullish OB Retest", "data": bullish_data, "color": "#00BFA5", "renderMode": "events"},
    {"type": "sell", "text": "+", "name": "Bearish OB Retest", "data": bearish_data, "color": "#F50078", "renderMode": "events"},
]

signals = [signal for signal in signals if any(item is not None for item in signal["data"])]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers}
