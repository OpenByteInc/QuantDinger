# ============================================================
# QuantDinger RSI Supply and Demand Zones
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger RSI Supply and Demand Zones"
my_indicator_description = "Maintains four independently configured RSI extreme zones with combined, support-only, and resistance-only views."

# @param show_supply_demand_1 bool false Show combined zone 1
# @param show_support_1 bool false Show support zone 1
# @param show_resistance_1 bool false Show resistance zone 1
# @param rsi_length_1 int 7 RSI 1 length range=1:10000:1
# @param threshold_1 str 70/30 RSI 1 thresholds values=70/30,75/25,80/20,85/15,90/10,95/5
# @param confirmation_bars_1 int 3 RSI 1 confirmation bars range=1:10000:1
# @param show_supply_demand_2 bool true Show combined zone 2
# @param show_support_2 bool false Show support zone 2
# @param show_resistance_2 bool false Show resistance zone 2
# @param rsi_length_2 int 14 RSI 2 length range=1:10000:1
# @param threshold_2 str 70/30 RSI 2 thresholds values=70/30,75/25,80/20,85/15,90/10,95/5
# @param confirmation_bars_2 int 3 RSI 2 confirmation bars range=1:10000:1
# @param show_supply_demand_3 bool false Show combined zone 3
# @param show_support_3 bool false Show support zone 3
# @param show_resistance_3 bool false Show resistance zone 3
# @param rsi_length_3 int 21 RSI 3 length range=1:10000:1
# @param threshold_3 str 70/30 RSI 3 thresholds values=70/30,75/25,80/20,85/15,90/10,95/5
# @param confirmation_bars_3 int 3 RSI 3 confirmation bars range=1:10000:1
# @param show_supply_demand_4 bool false Show combined zone 4
# @param show_support_4 bool false Show support zone 4
# @param show_resistance_4 bool false Show resistance zone 4
# @param rsi_length_4 int 28 RSI 4 length range=1:10000:1
# @param threshold_4 str 70/30 RSI 4 thresholds values=70/30,75/25,80/20,85/15,90/10,95/5
# @param confirmation_bars_4 int 3 RSI 4 confirmation bars range=1:10000:1
# @param supply_demand_color str #2962FF Combined-zone color
# @param support_color str #00FF00 Support-zone color
# @param resistance_color str #FF0000 Resistance-zone color

show_supply_demand = [bool(params.get("show_supply_demand_1", False)), bool(params.get("show_supply_demand_2", True)), bool(params.get("show_supply_demand_3", False)), bool(params.get("show_supply_demand_4", False))]
show_support = [bool(params.get("show_support_1", False)), bool(params.get("show_support_2", False)), bool(params.get("show_support_3", False)), bool(params.get("show_support_4", False))]
show_resistance = [bool(params.get("show_resistance_1", False)), bool(params.get("show_resistance_2", False)), bool(params.get("show_resistance_3", False)), bool(params.get("show_resistance_4", False))]
rsi_lengths = [
    min(10000, max(1, int(params.get("rsi_length_1", 7)))), min(10000, max(1, int(params.get("rsi_length_2", 14)))),
    min(10000, max(1, int(params.get("rsi_length_3", 21)))), min(10000, max(1, int(params.get("rsi_length_4", 28)))),
]
threshold_names = [str(params.get("threshold_1", "70/30")), str(params.get("threshold_2", "70/30")), str(params.get("threshold_3", "70/30")), str(params.get("threshold_4", "70/30"))]
confirmation_bars = [
    min(10000, max(1, int(params.get("confirmation_bars_1", 3)))), min(10000, max(1, int(params.get("confirmation_bars_2", 3)))),
    min(10000, max(1, int(params.get("confirmation_bars_3", 3)))), min(10000, max(1, int(params.get("confirmation_bars_4", 3)))),
]
supply_demand_color = str(params.get("supply_demand_color", "#2962FF"))
support_color = str(params.get("support_color", "#00FF00"))
resistance_color = str(params.get("resistance_color", "#FF0000"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


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


def _rsi(values, length):
    change = np.diff(values, prepend=np.nan)
    gain = _rma(np.where(np.isfinite(change), np.maximum(change, 0.0), np.nan), length)
    loss = _rma(np.where(np.isfinite(change), np.maximum(-change, 0.0), np.nan), length)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        if np.all(np.isfinite([gain[index], loss[index]])):
            result[index] = 100.0 if loss[index] == 0.0 else 0.0 if gain[index] == 0.0 else 100.0 - 100.0 / (1.0 + gain[index] / loss[index])
    return result


def _thresholds(name):
    normalized = name.replace(" ", "")
    if normalized == "70/30": return 70.0, 30.0
    if normalized == "75/25": return 75.0, 25.0
    if normalized == "80/20": return 80.0, 20.0
    if normalized == "90/10": return 90.0, 10.0
    if normalized == "95/5": return 95.0, 5.0
    return 100.0, 0.0


def _zone_group(close_values, high_values, low_values, length, threshold_name, confirmation):
    count = len(close_values)
    rsi_values = _rsi(close_values, length)
    overbought, oversold = _thresholds(threshold_name)
    both_count = np.zeros(count, dtype=int)
    resistance_count = np.zeros(count, dtype=int)
    support_count = np.zeros(count, dtype=int)
    combined_high = np.full(count, np.nan, dtype=float)
    combined_low = np.full(count, np.nan, dtype=float)
    resistance_high = np.full(count, np.nan, dtype=float)
    resistance_low = np.full(count, np.nan, dtype=float)
    support_high = np.full(count, np.nan, dtype=float)
    support_low = np.full(count, np.nan, dtype=float)
    latest_combined_high = latest_combined_low = np.nan
    latest_resistance_high = latest_resistance_low = np.nan
    latest_support_high = latest_support_low = np.nan
    for index in range(count):
        is_overbought = np.isfinite(rsi_values[index]) and rsi_values[index] > overbought
        is_oversold = np.isfinite(rsi_values[index]) and rsi_values[index] < oversold
        both_count[index] = (both_count[index - 1] if index else 0) + 1 if is_overbought or is_oversold else 0
        resistance_count[index] = (resistance_count[index - 1] if index else 0) + 1 if is_overbought else 0
        support_count[index] = (support_count[index - 1] if index else 0) + 1 if is_oversold else 0
        if both_count[index] >= confirmation:
            latest_combined_high, latest_combined_low = high_values[index], low_values[index]
        if resistance_count[index] >= confirmation:
            latest_resistance_high, latest_resistance_low = high_values[index], low_values[index]
        if support_count[index] >= confirmation:
            latest_support_high, latest_support_low = high_values[index], low_values[index]
        combined_high[index], combined_low[index] = latest_combined_high, latest_combined_low
        resistance_high[index], resistance_low[index] = latest_resistance_high, latest_resistance_low
        support_high[index], support_low[index] = latest_support_high, latest_support_low
    combined_stable = np.asarray([index > 0 and np.isfinite(combined_high[index]) and combined_high[index] == combined_high[index - 1] for index in range(count)], dtype=bool)
    resistance_stable = np.asarray([index > 0 and np.isfinite(resistance_high[index]) and resistance_high[index] == resistance_high[index - 1] for index in range(count)], dtype=bool)
    support_stable = np.asarray([index > 0 and np.isfinite(support_low[index]) and support_low[index] == support_low[index - 1] for index in range(count)], dtype=bool)
    price_inside = np.asarray([combined_stable[index] and combined_low[index] <= close_values[index] <= combined_high[index] for index in range(count)], dtype=bool)
    enters = price_inside & ~np.r_[False, price_inside[:-1]]
    return {
        "rsi": rsi_values, "overbought": float(overbought), "oversold": float(oversold),
        "bothCount": both_count, "resistanceCount": resistance_count, "supportCount": support_count,
        "combinedHigh": combined_high, "combinedLow": combined_low, "resistanceHigh": resistance_high, "resistanceLow": resistance_low,
        "supportHigh": support_high, "supportLow": support_low, "combinedStable": combined_stable,
        "resistanceStable": resistance_stable, "supportStable": support_stable, "priceInside": price_inside, "enters": enters,
    }


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

groups = [_zone_group(close, high, low, rsi_lengths[index], threshold_names[index], confirmation_bars[index]) for index in range(4)]
plots = []
signals = []
calculated = {}
for group_index, group in enumerate(groups):
    number = group_index + 1
    combined_cloud = [
        {"upper": float(group["combinedHigh"][index]), "lower": float(group["combinedLow"][index]), "color": supply_demand_color, "opacity": 0.15}
        if show_supply_demand[group_index] and group["combinedStable"][index] and np.all(np.isfinite([group["combinedHigh"][index], group["combinedLow"][index]])) else None for index in range(n)
    ]
    resistance_cloud = [
        {"upper": float(group["resistanceHigh"][index]), "lower": float(group["resistanceLow"][index]), "color": resistance_color, "opacity": 0.08}
        if show_resistance[group_index] and group["resistanceStable"][index] and np.all(np.isfinite([group["resistanceHigh"][index], group["resistanceLow"][index]])) else None for index in range(n)
    ]
    support_cloud = [
        {"upper": float(group["supportHigh"][index]), "lower": float(group["supportLow"][index]), "color": support_color, "opacity": 0.08}
        if show_support[group_index] and group["supportStable"][index] and np.all(np.isfinite([group["supportHigh"][index], group["supportLow"][index]])) else None for index in range(n)
    ]
    plots.extend([
        {"name": "Supply Demand " + str(number), "title": show_supply_demand[group_index], "data": combined_cloud, "color": supply_demand_color, "type": "cloud", "overlay": True},
        {"name": "Resistance " + str(number), "title": show_resistance[group_index], "data": resistance_cloud, "color": resistance_color, "type": "cloud", "overlay": True},
        {"name": "Support " + str(number), "title": show_support[group_index], "data": support_cloud, "color": support_color, "type": "cloud", "overlay": True},
        {"name": "Zone " + str(number) + " High", "title": show_supply_demand[group_index], "data": _finite(group["combinedHigh"]) if show_supply_demand[group_index] else [None] * n, "color": supply_demand_color, "type": "step", "overlay": True},
        {"name": "Zone " + str(number) + " Low", "title": show_supply_demand[group_index], "data": _finite(group["combinedLow"]) if show_supply_demand[group_index] else [None] * n, "color": supply_demand_color, "type": "step", "overlay": True},
    ])
    signals.append({
        "type": "zone_entry", "name": "Price Enters S/D Zone " + str(number),
        "data": [{"active": True, "price": float(close[index]), "text": "S/D " + str(number), "color": supply_demand_color} if group["enters"][index] else None for index in range(n)],
        "color": supply_demand_color, "renderMode": "events",
    })
    for key, values in group.items():
        output_key = "group" + str(number) + key[0].upper() + key[1:]
        if isinstance(values, np.ndarray):
            calculated[output_key] = values.tolist() if values.dtype == bool or np.issubdtype(values.dtype, np.integer) else _finite(values)
        else:
            calculated[output_key] = float(values)

plots.append({"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "overlay": True})
output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals,
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n, "calculatedVars": calculated,
}
