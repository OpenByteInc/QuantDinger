# ============================================================
# QuantDinger Fibonacci Support Resistance Trendline
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Fibonacci S/R Trendline"
my_indicator_description = (
    "Builds event-anchored Fibonacci retracements, prior-pivot support or resistance "
    "zones, and projected trendlines when new structural highs or lows are confirmed."
)

# @param find_long bool true Find long entries
# @param find_short bool false Find short entries
# @param left_bars int 10 Pivot left bars range=1:100:1
# @param right_bars int 5 Pivot right bars range=1:100:1
# @param fibonacci_width int 2 Fibonacci width range=1:8:1
# @param zone_width int 2 Support/resistance width range=1:8:1
# @param trendline_width int 2 Trendline width range=1:8:1
# @param fibonacci_extension int 5 Fibonacci extension bars range=1:100:1
# @param zone_extension int 5 Support/resistance extension bars range=1:100:1
# @param label_color str #FACC15 Label color
# @param long_alert str Fibo_0.5 Long alert test values=Fibo_0.382,Fibo_0.5,Fibo_0.618,Fibo_0.786,Support_Zone,Trendline
# @param short_alert str Fibo_0.5 Short alert test values=Fibo_0.382,Fibo_0.5,Fibo_0.618,Fibo_0.786,Resistance_Zone,Trendline

find_long = bool(params.get("find_long", True))
find_short = bool(params.get("find_short", False))
left_bars = min(100, max(1, int(params.get("left_bars", 10))))
right_bars = min(100, max(1, int(params.get("right_bars", 5))))
fibonacci_width = min(8, max(1, int(params.get("fibonacci_width", 2))))
zone_width = min(8, max(1, int(params.get("zone_width", 2))))
trendline_width = min(8, max(1, int(params.get("trendline_width", 2))))
fibonacci_extension = min(100, max(1, int(params.get("fibonacci_extension", 5))))
zone_extension = min(100, max(1, int(params.get("zone_extension", 5))))
label_color = str(params.get("label_color", "#FACC15"))
long_alert = str(params.get("long_alert", "Fibo_0.5")).replace("_", " ")
short_alert = str(params.get("short_alert", "Fibo_0.5")).replace("_", " ")


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def pivot_at(values, confirmation_index, left, right, is_high):
    anchor = confirmation_index - right
    if anchor - left < 0 or anchor < 0:
        return np.nan, -1
    window = values[anchor - left:anchor + right + 1]
    if len(window) != left + right + 1 or not np.all(np.isfinite(window)):
        return np.nan, -1
    candidate = float(values[anchor])
    extreme = float(np.max(window) if is_high else np.min(window))
    return (candidate, int(anchor)) if candidate == extreme else (np.nan, -1)


def layer_end(end_index, total):
    if end_index <= total - 1:
        return {"endIndex": int(end_index)}
    return {"endIndex": int(total - 1), "extendBars": int(end_index - (total - 1))}


frame = df.copy().reset_index(drop=True)
n = len(frame)
open_values = pd.to_numeric(frame["open"], errors="coerce").to_numpy(dtype=float)
high_values = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype=float)

ratios = np.asarray([0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0], dtype=float)
ratio_colors = ["rgba(192,192,192,0.40)", "rgba(242,54,69,0.40)", "rgba(129,199,132,0.40)", "rgba(34,197,94,0.40)", "rgba(8,153,129,0.40)", "rgba(0,255,255,0.40)", "rgba(192,192,192,0.40)"]
pivot_high_event = np.zeros(n, dtype=bool)
pivot_low_event = np.zeros(n, dtype=bool)
pivot_high_anchor = np.full(n, -1, dtype=int)
pivot_low_anchor = np.full(n, -1, dtype=int)
long_setup_event = np.zeros(n, dtype=bool)
short_setup_event = np.zeros(n, dtype=bool)
long_alert_event = np.zeros(n, dtype=bool)
short_alert_event = np.zeros(n, dtype=bool)

high_pivots = []
low_pivots = []
setups = []
layers = []
latest_long = None
latest_short = None

for index in range(n):
    high_price, high_anchor = pivot_at(high_values, index, left_bars, right_bars, True)
    low_price, low_anchor = pivot_at(low_values, index, left_bars, right_bars, False)

    if np.isfinite(high_price):
        pivot_high_event[index] = True
        pivot_high_anchor[index] = high_anchor
        high_pivots.insert(0, {
            "bar": int(high_anchor), "price": float(high_price),
            "body": float(max(open_values[high_anchor], close_values[high_anchor])),
        })

        if find_long and len(high_pivots) >= 2 and len(low_pivots) >= 1 and high_pivots[0]["price"] > high_pivots[1]["price"]:
            current_high = high_pivots[0]
            previous_high = high_pivots[1]
            swing_low = low_pivots[0]
            span = current_high["price"] - swing_low["price"]
            fib_prices = current_high["price"] - span * ratios
            fib_end = current_high["bar"] + right_bars + fibonacci_extension
            zone_end = current_high["bar"] + right_bars + zone_extension
            setup = {
                "side": "long", "confirmation": int(index),
                "swingStart": int(swing_low["bar"]), "swingEnd": int(current_high["bar"]),
                "swingLow": float(swing_low["price"]), "swingHigh": float(current_high["price"]),
                "previousPivotBar": int(previous_high["bar"]),
                "previousPivot": float(previous_high["price"]),
                "previousBody": float(previous_high["body"]),
                "fibonacci": fib_prices.tolist(), "trendline": None,
            }
            long_setup_event[index] = True

            swing_layer = {
                "type": "line", "role": "Long Swing",
                "startIndex": int(swing_low["bar"]), "endIndex": int(current_high["bar"]),
                "y1": float(swing_low["price"]), "y2": float(current_high["price"]),
                "color": "#22C55E", "lineWidth": 2,
            }
            layers.append(swing_layer)
            for level_index, ratio in enumerate(ratios):
                line = {
                    "type": "line", "role": f"Long Fibonacci {ratio:g}",
                    "startIndex": int(swing_low["bar"]),
                    "y1": float(fib_prices[level_index]), "y2": float(fib_prices[level_index]),
                    "color": ratio_colors[level_index], "lineWidth": fibonacci_width,
                }
                line.update(layer_end(fib_end, n))
                layers.append(line)

            zone = {
                "type": "box", "role": "Long Support Zone",
                "startIndex": int(previous_high["bar"]),
                "top": float(max(previous_high["price"], previous_high["body"])),
                "bottom": float(min(previous_high["price"], previous_high["body"])),
                "color": "rgba(34,197,94,0.15)", "fillColor": "rgba(34,197,94,0.15)",
                "borderColor": "rgba(34,197,94,0.80)", "borderSize": zone_width, "opacity": 1.0,
            }
            zone.update(layer_end(zone_end, n))
            layers.append(zone)

            trend_value = np.nan
            if len(high_pivots) >= 3 and high_pivots[1]["price"] < high_pivots[2]["price"]:
                second = high_pivots[1]
                third = high_pivots[2]
                denominator = second["bar"] - third["bar"]
                if denominator != 0:
                    slope = (second["price"] - third["price"]) / float(denominator)
                    trend_value = second["price"] + slope * (index - second["bar"])
                    layers.extend([
                        {
                            "type": "line", "role": "Long Resistance History",
                            "startIndex": int(third["bar"]), "endIndex": int(second["bar"]),
                            "y1": float(third["price"]), "y2": float(second["price"]),
                            "color": "#EF4444", "lineWidth": trendline_width, "dashed": True,
                        },
                        {
                            "type": "line", "role": "Long Resistance Projection",
                            "startIndex": int(second["bar"]), "endIndex": int(index),
                            "y1": float(second["price"]), "y2": float(trend_value),
                            "color": "#EF4444", "lineWidth": trendline_width, "dashed": True,
                        },
                    ])
                    setup["trendline"] = float(trend_value)

            selected = {"Fibo 0.382": fib_prices[2], "Fibo 0.5": fib_prices[3], "Fibo 0.618": fib_prices[4], "Fibo 0.786": fib_prices[5]}
            if long_alert in selected:
                long_alert_event[index] = close_values[index] < selected[long_alert]
            elif long_alert == "Support Zone":
                long_alert_event[index] = close_values[index] < previous_high["price"]
            elif long_alert == "Trendline" and np.isfinite(trend_value):
                long_alert_event[index] = close_values[index] < trend_value * 1.005

            setups.append(setup)
            latest_long = setup

    if np.isfinite(low_price):
        pivot_low_event[index] = True
        pivot_low_anchor[index] = low_anchor
        low_pivots.insert(0, {
            "bar": int(low_anchor), "price": float(low_price),
            "body": float(min(open_values[low_anchor], close_values[low_anchor])),
        })

        if find_short and len(low_pivots) >= 2 and len(high_pivots) >= 1 and low_pivots[0]["price"] < low_pivots[1]["price"]:
            current_low = low_pivots[0]
            previous_low = low_pivots[1]
            swing_high = high_pivots[0]
            span = swing_high["price"] - current_low["price"]
            fib_prices = current_low["price"] + span * ratios
            fib_end = current_low["bar"] + right_bars + fibonacci_extension
            zone_end = current_low["bar"] + right_bars + zone_extension
            setup = {
                "side": "short", "confirmation": int(index),
                "swingStart": int(swing_high["bar"]), "swingEnd": int(current_low["bar"]),
                "swingLow": float(current_low["price"]), "swingHigh": float(swing_high["price"]),
                "previousPivotBar": int(previous_low["bar"]),
                "previousPivot": float(previous_low["price"]),
                "previousBody": float(previous_low["body"]),
                "fibonacci": fib_prices.tolist(), "trendline": None,
            }
            short_setup_event[index] = True

            layers.append({
                "type": "line", "role": "Short Swing",
                "startIndex": int(swing_high["bar"]), "endIndex": int(current_low["bar"]),
                "y1": float(swing_high["price"]), "y2": float(current_low["price"]),
                "color": "#EF4444", "lineWidth": 2,
            })
            for level_index, ratio in enumerate(ratios):
                line = {
                    "type": "line", "role": f"Short Fibonacci {ratio:g}",
                    "startIndex": int(swing_high["bar"]),
                    "y1": float(fib_prices[level_index]), "y2": float(fib_prices[level_index]),
                    "color": ratio_colors[level_index], "lineWidth": fibonacci_width,
                }
                line.update(layer_end(fib_end, n))
                layers.append(line)

            zone = {
                "type": "box", "role": "Short Resistance Zone",
                "startIndex": int(previous_low["bar"]),
                "top": float(max(previous_low["price"], previous_low["body"])),
                "bottom": float(min(previous_low["price"], previous_low["body"])),
                "color": "rgba(239,68,68,0.15)", "fillColor": "rgba(239,68,68,0.15)",
                "borderColor": "rgba(239,68,68,0.80)", "borderSize": zone_width, "opacity": 1.0,
            }
            zone.update(layer_end(zone_end, n))
            layers.append(zone)

            trend_value = np.nan
            if len(low_pivots) >= 3 and low_pivots[1]["price"] > low_pivots[2]["price"]:
                second = low_pivots[1]
                third = low_pivots[2]
                denominator = second["bar"] - third["bar"]
                if denominator != 0:
                    slope = (second["price"] - third["price"]) / float(denominator)
                    trend_value = second["price"] + slope * (index - second["bar"])
                    layers.extend([
                        {
                            "type": "line", "role": "Short Support History",
                            "startIndex": int(third["bar"]), "endIndex": int(second["bar"]),
                            "y1": float(third["price"]), "y2": float(second["price"]),
                            "color": "#22C55E", "lineWidth": trendline_width, "dashed": True,
                        },
                        {
                            "type": "line", "role": "Short Support Projection",
                            "startIndex": int(second["bar"]), "endIndex": int(index),
                            "y1": float(second["price"]), "y2": float(trend_value),
                            "color": "#22C55E", "lineWidth": trendline_width, "dashed": True,
                        },
                    ])
                    setup["trendline"] = float(trend_value)

            selected = {"Fibo 0.382": fib_prices[2], "Fibo 0.5": fib_prices[3], "Fibo 0.618": fib_prices[4], "Fibo 0.786": fib_prices[5]}
            if short_alert in selected:
                short_alert_event[index] = close_values[index] > selected[short_alert]
            elif short_alert == "Resistance Zone":
                short_alert_event[index] = close_values[index] > previous_low["price"]
            elif short_alert == "Trendline" and np.isfinite(trend_value):
                short_alert_event[index] = close_values[index] > trend_value * 0.995

            setups.append(setup)
            latest_short = setup

pivot_high_points = [None for _ in range(n)]
pivot_low_points = [None for _ in range(n)]
for index in range(n):
    if pivot_high_event[index]:
        anchor = int(pivot_high_anchor[index])
        pivot_high_points[anchor] = {"value": float(high_values[anchor]), "color": "#EF4444", "size": 4}
    if pivot_low_event[index]:
        anchor = int(pivot_low_anchor[index])
        pivot_low_points[anchor] = {"value": float(low_values[anchor]), "color": "#22C55E", "size": 4}
plots = [
    {"name": "Price Anchor", "title": False, "data": finite(close_values), "color": "rgba(0,0,0,0)", "type": "line", "overlay": True},
    {"name": "Pivot High", "title": False, "data": pivot_high_points, "color": "#EF4444", "type": "scatter", "size": 4, "overlay": True},
    {"name": "Pivot Low", "title": False, "data": pivot_low_points, "color": "#22C55E", "type": "scatter", "size": 4, "overlay": True},
]

tables = []
for latest, position, title in ((latest_long, "top-right", "Long Fibonacci"), (latest_short, "bottom-right", "Short Fibonacci")):
    if latest is not None:
        rows = []
        for ratio, price, color in zip(ratios, latest["fibonacci"], ratio_colors):
            rows.append([{"text": f"{ratio:g}", "color": color}, {"text": f"{price:.2f}", "align": "right", "color": label_color}])
        tables.append({"title": title, "position": position, "compact": True, "columns": ["Ratio", "Price"], "rows": rows})

signals = [
    {"type": "buy", "name": "Long Test", "text": "LONG", "color": "#22C55E", "renderMode": "hidden", "data": [float(low_values[index]) if long_alert_event[index] else None for index in range(n)]},
    {"type": "sell", "name": "Short Test", "text": "SHORT", "color": "#EF4444", "renderMode": "hidden", "data": [float(high_values[index]) if short_alert_event[index] else None for index in range(n)]},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": tables,
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "pivotHighEvent": pivot_high_event.tolist(), "pivotLowEvent": pivot_low_event.tolist(),
        "pivotHighAnchor": pivot_high_anchor.tolist(), "pivotLowAnchor": pivot_low_anchor.tolist(),
        "longSetupEvent": long_setup_event.tolist(), "shortSetupEvent": short_setup_event.tolist(),
        "longAlertEvent": long_alert_event.tolist(), "shortAlertEvent": short_alert_event.tolist(),
        "ratios": ratios.tolist(), "highPivots": high_pivots, "lowPivots": low_pivots, "setups": setups,
    },
}
