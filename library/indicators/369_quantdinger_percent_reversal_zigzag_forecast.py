# ============================================================
# QuantDinger Percent-Reversal ZigZag Forecast
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Percent-Reversal ZigZag Forecast"
my_indicator_description = "Builds a manual or ATR-scaled reversal ZigZag, learns swing distributions, forecasts the next pivot, and identifies geometric patterns."

# @param reversal_method str MANUAL Reversal range method values=MANUAL,ATR005,ATR010,ATR020,ATR050,ATR100,ATR250
# @param reversal_percent float 0.25 Manual reversal percent range=0:99:0.01
# @param atr_multiplier float 1.0 ATR reversal multiplier range=0:100:0.1
# @param show_real_pivots bool true Place pivots at the actual extreme bar
# @param show_zigzag_lines bool true Show ZigZag lines
# @param show_pivot_price bool true Show pivot labels
# @param show_triangle_rates bool true Include latest triangle rates
# @param show_patterns bool true Show detected patterns
# @param show_harmonic bool true Detect harmonic triangles
# @param show_tap bool true Detect tap patterns
# @param show_abcd bool true Detect AB=CD patterns
# @param show_bat bool true Detect Bat patterns
# @param show_butterfly bool true Detect Butterfly patterns
# @param show_gartley bool true Detect Gartley patterns
# @param show_crab bool true Detect Crab patterns
# @param show_shark bool true Detect Shark patterns
# @param show_five_o bool true Detect 5-O patterns
# @param show_wolfe bool true Detect Wolfe patterns
# @param show_contracting_triangle bool true Detect contracting triangles
# @param show_expanding_triangle bool true Detect expanding triangles
# @param show_head_shoulders bool true Detect head and shoulders
# @param show_projections bool true Show next-pivot forecast
# @param show_projection_bounds bool true Show forecast bounds
# @param show_projection_rates bool false Show 0.618 and 1.618 values in the table
# @param averaging_length float 10.0 Forecast averaging length range=1:10000:1
# @param pattern_error_percent float 5.0 Pattern error margin range=0:100:0.1

reversal_method = str(params.get("reversal_method", "MANUAL"))
reversal_method = reversal_method if reversal_method in ("MANUAL", "ATR005", "ATR010", "ATR020", "ATR050", "ATR100", "ATR250") else "MANUAL"
reversal_percent = min(99.0, max(0.0, float(params.get("reversal_percent", 0.25)))) / 100.0
atr_multiplier = min(100.0, max(0.0, float(params.get("atr_multiplier", 1.0))))
show_real_pivots = bool(params.get("show_real_pivots", True))
show_zigzag_lines = bool(params.get("show_zigzag_lines", True))
show_pivot_price = bool(params.get("show_pivot_price", True))
show_triangle_rates = bool(params.get("show_triangle_rates", True))
show_patterns = bool(params.get("show_patterns", True))
show_harmonic = bool(params.get("show_harmonic", True))
show_tap = bool(params.get("show_tap", True))
show_abcd = bool(params.get("show_abcd", True))
show_bat = bool(params.get("show_bat", True))
show_butterfly = bool(params.get("show_butterfly", True))
show_gartley = bool(params.get("show_gartley", True))
show_crab = bool(params.get("show_crab", True))
show_shark = bool(params.get("show_shark", True))
show_five_o = bool(params.get("show_five_o", True))
show_wolfe = bool(params.get("show_wolfe", True))
show_contracting_triangle = bool(params.get("show_contracting_triangle", True))
show_expanding_triangle = bool(params.get("show_expanding_triangle", True))
show_head_shoulders = bool(params.get("show_head_shoulders", True))
show_projections = bool(params.get("show_projections", True))
show_projection_bounds = bool(params.get("show_projection_bounds", True))
show_projection_rates = bool(params.get("show_projection_rates", False))
averaging_length = min(10000.0, max(1.0, float(params.get("averaging_length", 10.0))))
pattern_error_percent = min(100.0, max(0.0, float(params.get("pattern_error_percent", 5.0))))


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


def _atr(high_values, low_values, close_values, length):
    true_range = np.full(len(close_values), np.nan, dtype=float)
    for index in range(len(close_values)):
        true_range[index] = high_values[index] - low_values[index] if index == 0 else max(high_values[index] - low_values[index], abs(high_values[index] - close_values[index - 1]), abs(low_values[index] - close_values[index - 1]))
    return _rma(true_range, length)


def _safe_rate(c_value, b_value, a_value):
    denominator = b_value - c_value
    return (a_value - b_value) / denominator if np.all(np.isfinite([a_value, b_value, c_value])) and denominator != 0.0 else np.nan


def _in_range(value, minimum, maximum):
    return np.isfinite(value) and minimum <= value <= maximum


def _pattern_names(prices, margin):
    if len(prices) < 7:
        return []
    g, f, e, d, c, b, a = prices[-7:]
    rates = {
        "gfe": _safe_rate(g, f, e), "gfc": _safe_rate(g, f, c), "gfa": _safe_rate(g, f, a),
        "gdc": _safe_rate(g, d, c), "gda": _safe_rate(g, d, a), "gba": _safe_rate(g, b, a),
        "fed": _safe_rate(f, e, d), "feb": _safe_rate(f, e, b), "fcb": _safe_rate(f, c, b),
        "edc": _safe_rate(e, d, c), "eda": _safe_rate(e, d, a), "eba": _safe_rate(e, b, a),
        "dcb": _safe_rate(d, c, b), "cba": _safe_rate(c, b, a),
    }
    r = rates
    valid_gfa = r["fed"] >= -1.0 and r["feb"] >= -1.0 and r["cba"] <= -1.0 and r["eda"] <= -1.0
    valid_gda = r["fed"] <= -1.0 and r["gfe"] >= -1.0 and r["cba"] <= -1.0 and r["dcb"] >= -1.0
    valid_gba = r["feb"] <= -1.0 and r["gfe"] >= -1.0 and r["cba"] <= -1.0 and r["dcb"] <= -1.0
    valid_eba = r["cba"] <= -1.0 and r["dcb"] <= -1.0
    valid_eda = r["cba"] <= -1.0 and r["dcb"] >= -1.0
    valid_fcb = r["fed"] >= -1.0 and r["edc"] <= -1.0
    valid_feb = r["edc"] >= -1.0 and r["dcb"] <= -1.0
    names = []

    def harmonic(value):
        if not np.isfinite(value):
            return False
        return any(_in_range(value, -(1.618 ** (-5 + power)) - margin, -(1.618 ** (-5 + power)) + margin) for power in range(1, 13))

    def tap2(value): return _in_range(value, -1.0 - margin, -1.0 + margin)
    def tap3(first, second): return tap2(first) and tap2(second)
    def tap4(first, second, third): return tap2(first) and tap2(second) and tap2(third)
    def abcd(first, second): return _in_range(first, -1.618 - margin, -1.270 + margin) and _in_range(second, -0.786 - margin, -0.618 + margin)
    def bat(one, two, three, four): return _in_range(one, -0.500 - margin, -0.382 + margin) and _in_range(two, -0.886 - margin, -0.382 + margin) and _in_range(three, -2.618 - margin, -1.618 + margin) and _in_range(four, -0.886 - margin, -0.886 + margin)
    def butterfly(one, two, three, four): return _in_range(one, -0.786 - margin, -0.786 + margin) and _in_range(two, -0.886 - margin, -0.382 + margin) and _in_range(three, -2.618 - margin, -1.618 + margin) and _in_range(four, -1.618 - margin, -1.270 + margin)
    def gartley(one, two, three, four): return _in_range(one, -0.618 - margin, -0.618 + margin) and _in_range(two, -0.886 - margin, -0.382 + margin) and _in_range(three, -2.618 - margin, -1.130 + margin) and _in_range(four, -0.786 - margin, -0.786 + margin)
    def crab(one, two, three, four): return _in_range(one, -0.886 - margin, -0.886 + margin) and _in_range(two, -0.886 - margin, -0.382 + margin) and _in_range(three, -3.618 - margin, -2.000 + margin) and _in_range(four, -1.618 - margin, -1.618 + margin)
    def shark(one, two, three, four): return _in_range(one, -0.886 - margin, -0.886 + margin) and _in_range(two, -1.618 - margin, -1.130 + margin) and _in_range(three, -2.240 - margin, -1.270 + margin) and _in_range(four, -1.130 - margin, -0.886 + margin)
    def five_o(one, two, three, four): return _in_range(one, -1.618 - margin, -1.130 + margin) and _in_range(two, -2.240 - margin, -1.618 + margin) and _in_range(three, -0.500 - margin, -0.500 + margin) and _in_range(four, -0.236 - margin, 0.236 + margin)
    def wolfe(one, two, three, four): return _in_range(one, -1.618 - margin, -1.270 + margin) and _in_range(two, -5.000 - margin, 0.000 + margin) and _in_range(three, -1.618 - margin, -1.270 + margin) and _in_range(four, -5.000 - margin, 0.000 + margin)
    def contracting(one, two, three, four): return all(_in_range(value, -0.886 - margin, -0.236 + margin) for value in (one, two, three, four))
    def expanding(one, two, three, four): return all(_in_range(value, -2.618 - margin, -1.125 + margin) for value in (one, two, three, four))
    def head_shoulders():
        return _in_range(r["fed"], -0.618 - margin, -0.090 + margin) and _in_range(r["feb"], -0.886 - margin, -0.090 + margin) and _in_range(r["edc"], -9.999 - margin, -1.000 + margin) and _in_range(r["eda"], -1.618 - margin, -0.090 + margin) and _in_range(r["dcb"], -1.250 - margin, -0.750 + margin) and _in_range(r["cba"], -0.886 - margin, -0.090 + margin)

    if show_harmonic:
        for suffix, condition in (("GFA", harmonic(r["gfa"]) and valid_gfa), ("GDA", harmonic(r["gda"]) and valid_gda), ("GBA", harmonic(r["gba"]) and valid_gba), ("EBA", harmonic(r["eba"]) and valid_eba), ("EDA", harmonic(r["eda"]) and valid_eda), ("CBA", harmonic(r["cba"]))):
            if condition: names.append("Harmonic " + suffix)
    if show_tap:
        if tap2(r["cba"]): names.append("Double Tap CBA")
        if tap2(r["eba"]) and valid_eba: names.append("Double Tap EBA")
        if tap2(r["eda"]) and valid_eda: names.append("Double Tap EDA")
        if tap3(r["edc"], r["cba"]): names.append("Triple Tap")
        if tap4(r["gfe"], r["edc"], r["cba"]): names.append("Quadruple Tap")
    combinations = ((r["edc"], r["dcb"], r["cba"], r["eda"], True), (r["gfe"], r["feb"], r["eba"], r["gfa"], valid_eba), (r["gfe"], r["fed"], r["eda"], r["gfa"], valid_eda))
    if show_abcd:
        for first, second, valid in ((r["cba"], r["dcb"], True), (r["cba"], r["fcb"], valid_fcb), (r["eba"], r["feb"], valid_feb), (r["eda"], r["fed"], valid_eda)):
            if valid and abcd(first, second): names.append("AB=CD")
    for enabled, label, function in ((show_bat, "Bat", bat), (show_butterfly, "Butterfly", butterfly), (show_gartley, "Gartley", gartley), (show_crab, "Crab", crab), (show_shark, "Shark", shark), (show_five_o, "5-O", five_o), (show_wolfe, "Wolfe", wolfe), (show_contracting_triangle, "Contracting Triangle", contracting), (show_expanding_triangle, "Expanding Triangle", expanding)):
        if enabled:
            for one, two, three, four, valid in combinations:
                if valid and function(one, two, three, four): names.append(label)
    if show_head_shoulders and head_shoulders(): names.append("Head & Shoulders")
    return names


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
times = df["time"].to_numpy(dtype=float)
time_count = len(times)
if time_count:
    if max(abs(float(value)) for value in times) > 10_000_000_000:
        times = times / 1000.0
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].to_numpy(dtype=float)
n = len(df)

atr_lengths = {"ATR005": 5, "ATR010": 10, "ATR020": 20, "ATR050": 50, "ATR100": 100, "ATR250": 250}
selected_atr = _atr(high, low, close, atr_lengths[reversal_method]) if reversal_method != "MANUAL" else np.full(n, np.nan, dtype=float)
percent = np.full(n, reversal_percent, dtype=float)
if reversal_method != "MANUAL":
    percent = np.divide(selected_atr, opened, out=np.full(n, np.nan, dtype=float), where=np.isfinite(selected_atr) & (opened != 0.0)) * atr_multiplier

pivot = np.full(n, np.nan, dtype=float)
direction_up = np.zeros(n, dtype=bool)
reverse_line = np.full(n, np.nan, dtype=float)
real_pivot_index = np.full(n, -1, dtype=int)
new_pivot = np.zeros(n, dtype=bool)
state_up = None
high_track = low_track = current_pivot = np.nan
high_track_index = low_track_index = -1
current_real_index = -1
for index in range(n):
    if index >= 1:
        if state_up is None:
            state_up = True
        prior_pivot = pivot[index - 1]
        reversal_range = (prior_pivot if np.isfinite(prior_pivot) else 0.0) * percent[index] if np.isfinite(percent[index]) else np.nan
        if state_up:
            low_track, low_track_index = np.nan, index
            if not np.isfinite(high_track):
                if high[index] > high[index - 1]: high_track, high_track_index = high[index], index
                else: high_track, high_track_index = high[index - 1], index - 1
            elif high[index] > high_track:
                high_track, high_track_index = high[index], index
            reverse_line[index] = high_track - reversal_range if np.isfinite(reversal_range) else np.nan
            if np.isfinite(reverse_line[index]) and close[index] <= reverse_line[index]:
                current_pivot, current_real_index, state_up = high_track, high_track_index, False
        if state_up is False:
            high_track, high_track_index = np.nan, -1
            if not np.isfinite(low_track):
                if low[index] < low[index - 1]: low_track, low_track_index = low[index], index
                else: low_track, low_track_index = low[index - 1], index - 1
            elif low[index] < low_track:
                low_track, low_track_index = low[index], index
            reverse_line[index] = low_track + reversal_range if np.isfinite(reversal_range) else np.nan
            if np.isfinite(reverse_line[index]) and close[index] >= reverse_line[index]:
                current_pivot, current_real_index, state_up = low_track, low_track_index, True
    pivot[index] = current_pivot
    direction_up[index] = bool(state_up) if state_up is not None else False
    real_pivot_index[index] = current_real_index
    new_pivot[index] = index > 0 and np.isfinite(pivot[index]) and np.isfinite(pivot[index - 1]) and pivot[index] != pivot[index - 1]

pivot_prices = []
pivot_indices = []
pivot_confirmation_indices = []
pattern_text_at_pivot = [""] * n
pattern_names_by_bar = [""] * n
for index in range(n):
    if new_pivot[index]:
        visual_index = real_pivot_index[index] if show_real_pivots and real_pivot_index[index] >= 0 else index
        pivot_prices.append(float(pivot[index]))
        pivot_indices.append(int(visual_index))
        pivot_confirmation_indices.append(index)
        names = _pattern_names(pivot_prices, pattern_error_percent / 100.0) if show_patterns else []
        compact_names = []
        for name in names:
            if name not in compact_names:
                compact_names.append(name)
        text = ", ".join(compact_names)
        pattern_names_by_bar[index] = text
        if 0 <= visual_index < n:
            label_parts = []
            if show_pivot_price: label_parts.append(f"{pivot[index]:.5f}")
            if text: label_parts.append(text)
            pattern_text_at_pivot[visual_index] = " | ".join(label_parts)

ab_price_difference = np.full(n, np.nan, dtype=float)
ab_time_difference = np.full(n, np.nan, dtype=float)
latest_a = latest_b = latest_time_a = latest_time_b = np.nan
event_cursor = 0
for index in range(n):
    if new_pivot[index]:
        latest_b, latest_time_b = latest_a, latest_time_a
        latest_a = pivot[index]
        visual_index = real_pivot_index[index] if show_real_pivots and real_pivot_index[index] >= 0 else index
        latest_time_a = times[visual_index]
        event_cursor += 1
    if np.all(np.isfinite([latest_a, latest_b])):
        ab_price_difference[index] = abs(latest_a - latest_b)
    if np.all(np.isfinite([latest_time_a, latest_time_b])):
        ab_time_difference[index] = latest_time_a - latest_time_b


def _process_stats(data, weight_value):
    average = np.full(len(data), np.nan, dtype=float)
    maximum = np.full(len(data), np.nan, dtype=float)
    positive_dev = np.zeros(len(data), dtype=float)
    negative_dev = np.zeros(len(data), dtype=float)
    positive_range = np.full(len(data), np.nan, dtype=float)
    negative_range = np.full(len(data), np.nan, dtype=float)
    for index in range(len(data)):
        if index == 0:
            average[index] = data[index]
            maximum[index] = 0.0
        else:
            changed = np.isfinite(data[index]) and np.isfinite(data[index - 1]) and data[index] != data[index - 1]
            if changed:
                previous_average = average[index - 1] if np.isfinite(average[index - 1]) else data[index - 1]
                average[index] = weight_value * data[index] + (1.0 - weight_value) * previous_average
            else:
                average[index] = average[index - 1]
            if np.isfinite(data[index]):
                previous_maximum = maximum[index - 1] if np.isfinite(maximum[index - 1]) else data[index]
                maximum[index] = max(previous_maximum, data[index])
            else:
                maximum[index] = maximum[index - 1]
        if np.all(np.isfinite([data[index], average[index]])):
            positive_range[index] = max(0.0, data[index] - average[index])
            negative_range[index] = min(0.0, data[index] - average[index])
        if index > 0:
            positive_changed = np.isfinite(positive_range[index]) and np.isfinite(positive_range[index - 1]) and positive_range[index] != positive_range[index - 1]
            negative_changed = np.isfinite(negative_range[index]) and np.isfinite(negative_range[index - 1]) and negative_range[index] != negative_range[index - 1]
            positive_dev[index] = weight_value * positive_range[index] + (1.0 - weight_value) * positive_dev[index - 1] if positive_changed else positive_dev[index - 1]
            negative_dev[index] = weight_value * negative_range[index] + (1.0 - weight_value) * negative_dev[index - 1] if negative_changed else negative_dev[index - 1]
    return average, maximum, positive_dev, negative_dev


weight = 2.0 / (averaging_length + 1.0)
price_average, price_maximum, price_positive_dev, price_negative_dev = _process_stats(ab_price_difference, weight)
time_average, time_maximum, time_positive_dev, time_negative_dev = _process_stats(ab_time_difference, weight)
target_average_price = np.full(n, np.nan, dtype=float)
target_upper_price = np.full(n, np.nan, dtype=float)
target_lower_price = np.full(n, np.nan, dtype=float)
target_0618_price = np.full(n, np.nan, dtype=float)
target_1618_price = np.full(n, np.nan, dtype=float)
target_average_time = np.full(n, np.nan, dtype=float)
target_0618_time = np.full(n, np.nan, dtype=float)
target_1618_time = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.all(np.isfinite([pivot[index], price_average[index], price_positive_dev[index], price_negative_dev[index]])):
        rising_swing = False
        previous_pivot_value = np.nan
        prior_events = [value for event_index, value in zip(pivot_confirmation_indices, pivot_prices) if event_index <= index]
        if len(prior_events) >= 2:
            previous_pivot_value = prior_events[-2]
            rising_swing = pivot[index] > previous_pivot_value
        if np.isfinite(previous_pivot_value):
            if rising_swing:
                target_average_price[index] = pivot[index] - price_average[index]
                target_upper_price[index] = pivot[index] - price_average[index] - price_negative_dev[index]
                target_lower_price[index] = pivot[index] - price_average[index] - price_positive_dev[index]
                target_0618_price[index] = pivot[index] - (price_average[index] + price_negative_dev[index]) * 0.618
                target_1618_price[index] = pivot[index] - (price_average[index] + price_positive_dev[index]) * 1.618
            else:
                target_average_price[index] = pivot[index] + price_average[index]
                target_upper_price[index] = pivot[index] + price_average[index] + price_negative_dev[index]
                target_lower_price[index] = pivot[index] + price_average[index] + price_positive_dev[index]
                target_0618_price[index] = pivot[index] + (price_average[index] + price_negative_dev[index]) * 0.618
                target_1618_price[index] = pivot[index] + (price_average[index] + price_positive_dev[index]) * 1.618
    current_time = times[real_pivot_index[index]] if show_real_pivots and real_pivot_index[index] >= 0 else times[index] if n else np.nan
    if np.all(np.isfinite([current_time, time_average[index]])):
        target_average_time[index] = current_time + time_average[index]
        target_0618_time[index] = current_time + (time_average[index] + time_negative_dev[index]) * 0.618
        target_1618_time[index] = current_time + (time_average[index] + time_positive_dev[index]) * 1.618

zigzag_plot = np.full(n, np.nan, dtype=float)
for price_value, visual_index in zip(pivot_prices, pivot_indices):
    if 0 <= visual_index < n:
        zigzag_plot[visual_index] = price_value
reverse_shifted = np.full(n, np.nan, dtype=float)
if n > 1:
    reverse_shifted[1:] = reverse_line[:-1]

projection_line = np.full(n, np.nan, dtype=float)
projection_bounds = [None] * n
if show_projections and pivot_confirmation_indices:
    latest_confirmation = pivot_confirmation_indices[-1]
    start_index = pivot_indices[-1]
    start_time = times[start_index]
    start_price = pivot_prices[-1]
    target_time = target_average_time[latest_confirmation]
    target_price = target_average_price[latest_confirmation]
    lower_bound = min(target_0618_price[latest_confirmation], target_1618_price[latest_confirmation]) if np.all(np.isfinite([target_0618_price[latest_confirmation], target_1618_price[latest_confirmation]])) else np.nan
    upper_bound = max(target_0618_price[latest_confirmation], target_1618_price[latest_confirmation]) if np.all(np.isfinite([target_0618_price[latest_confirmation], target_1618_price[latest_confirmation]])) else np.nan
    for index in range(max(0, start_index), n):
        if np.all(np.isfinite([target_time, target_price])):
            fraction = min(1.0, max(0.0, (times[index] - start_time) / (target_time - start_time))) if target_time != start_time else 1.0
            projection_line[index] = start_price + fraction * (target_price - start_price)
        if show_projection_bounds and np.all(np.isfinite([lower_bound, upper_bound])):
            projection_bounds[index] = {"upper": float(upper_bound), "lower": float(lower_bound), "color": "#808080", "opacity": 0.16}


def _text_events(texts, prices):
    return {"type": "pivot", "name": "ZigZag Pivot", "data": [{"active": True, "price": float(prices[index]), "text": texts[index], "color": "#F59E0B" if index and prices[index] > prices[index - 1] else "#14B8A6"} if texts[index] and np.isfinite(prices[index]) else None for index in range(n)], "color": "#F59E0B", "renderMode": "events"}


def _limited_event_texts(texts, recent_bars=120, total_cap=14, minimum_gap=4):
    result = [""] * n
    first_index = max(0, n - recent_bars)
    candidates = [index for index in range(first_index, n) if texts[index]]
    selected_indices = []
    for index in reversed(candidates):
        if len(selected_indices) >= total_cap:
            break
        if any(abs(index - selected) < minimum_gap for selected in selected_indices):
            continue
        result[index] = texts[index]
        selected_indices.append(index)
    return result


display_pivot_text = _limited_event_texts(pattern_text_at_pivot)


rows = []
if pivot_confirmation_indices:
    latest = pivot_confirmation_indices[-1]
    rows.append(["Swing pivots", str(len(pivot_prices))])
    rows.append(["Next target", "N/A" if not np.isfinite(target_average_price[latest]) else f"{target_average_price[latest]:.5f}"])
    rows.append(["Target ETA", "N/A" if not np.isfinite(target_average_time[latest]) else f"{target_average_time[latest]:.0f}"])
    if show_triangle_rates and len(pivot_prices) >= 3:
        rows.append(["Latest price rate", f"{_safe_rate(pivot_prices[-3], pivot_prices[-2], pivot_prices[-1]):.3f}"])
    if show_projection_rates:
        rows.append(["Target 0.618", "N/A" if not np.isfinite(target_0618_price[latest]) else f"{target_0618_price[latest]:.5f}"])
        rows.append(["Target 1.618", "N/A" if not np.isfinite(target_1618_price[latest]) else f"{target_1618_price[latest]:.5f}"])

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "ZigZag", "title": show_zigzag_lines, "data": _finite(zigzag_plot) if show_zigzag_lines else [None] * n, "color": "#14B8A6", "type": "line", "lineWidth": 2, "overlay": True, "connectNulls": True},
        {"name": "Reversal Line", "title": True, "data": _finite(reverse_shifted), "color": "#808080", "type": "step", "overlay": True},
        {"name": "Forecast", "title": show_projections, "data": _finite(projection_line) if show_projections else [None] * n, "color": "#F59E0B", "type": "line", "lineStyle": "dashed", "overlay": True},
        {"name": "Forecast Bounds", "title": False, "data": projection_bounds if show_projections and show_projection_bounds else [None] * n, "color": "#808080", "type": "cloud", "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "overlay": True},
    ],
    "signals": [_text_events(display_pivot_text, zigzag_plot)],
    "layers": [], "tables": [{"title": "ZigZag Forecast", "position": "top-right", "compact": True, "rows": rows}] if rows else [],
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "selectedAtr": _finite(selected_atr), "percent": _finite(percent), "pivot": _finite(pivot), "directionUp": direction_up.tolist(),
        "reverseLine": _finite(reverse_line), "realPivotIndex": [int(value) for value in real_pivot_index], "newPivot": new_pivot.tolist(),
        "pivotPrices": [float(value) for value in pivot_prices], "pivotIndices": [int(value) for value in pivot_indices], "pivotConfirmationIndices": [int(value) for value in pivot_confirmation_indices],
        "patternText": pattern_names_by_bar, "abPriceDifference": _finite(ab_price_difference), "abTimeDifference": _finite(ab_time_difference),
        "priceAverage": _finite(price_average), "priceMaximum": _finite(price_maximum), "pricePositiveDeviation": _finite(price_positive_dev), "priceNegativeDeviation": _finite(price_negative_dev),
        "timeAverage": _finite(time_average), "timeMaximum": _finite(time_maximum), "timePositiveDeviation": _finite(time_positive_dev), "timeNegativeDeviation": _finite(time_negative_dev),
        "targetAveragePrice": _finite(target_average_price), "targetUpperPrice": _finite(target_upper_price), "targetLowerPrice": _finite(target_lower_price),
        "target0618Price": _finite(target_0618_price), "target1618Price": _finite(target_1618_price), "targetAverageTime": _finite(target_average_time),
        "target0618Time": _finite(target_0618_time), "target1618Time": _finite(target_1618_time),
    },
}
