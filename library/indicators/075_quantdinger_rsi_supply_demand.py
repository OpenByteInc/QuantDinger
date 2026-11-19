# ============================================================
# QuantDinger RSI Supply and Demand
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger RSI Supply and Demand"
my_indicator_description = (
    "Four configurable RSI exhaustion engines that retain combined, support, "
    "and resistance price zones after a confirmed extreme sequence."
)

# @param show_sd_1 bool false Show group 1 supply-demand zone
# @param show_support_1 bool false Show group 1 support zone
# @param show_resistance_1 bool false Show group 1 resistance zone
# @param rsi_length_1 int 7 Group 1 RSI length range=1:200:1
# @param threshold_mode_1 int 0 Group 1 threshold mode range=0:5:1
# @param confirmation_1 int 3 Group 1 confirmation bars range=1:20:1
# @param show_sd_2 bool true Show group 2 supply-demand zone
# @param show_support_2 bool false Show group 2 support zone
# @param show_resistance_2 bool false Show group 2 resistance zone
# @param rsi_length_2 int 14 Group 2 RSI length range=1:200:1
# @param threshold_mode_2 int 0 Group 2 threshold mode range=0:5:1
# @param confirmation_2 int 3 Group 2 confirmation bars range=1:20:1
# @param show_sd_3 bool false Show group 3 supply-demand zone
# @param show_support_3 bool false Show group 3 support zone
# @param show_resistance_3 bool false Show group 3 resistance zone
# @param rsi_length_3 int 21 Group 3 RSI length range=1:200:1
# @param threshold_mode_3 int 0 Group 3 threshold mode range=0:5:1
# @param confirmation_3 int 3 Group 3 confirmation bars range=1:20:1
# @param show_sd_4 bool false Show group 4 supply-demand zone
# @param show_support_4 bool false Show group 4 support zone
# @param show_resistance_4 bool false Show group 4 resistance zone
# @param rsi_length_4 int 28 Group 4 RSI length range=1:200:1
# @param threshold_mode_4 int 0 Group 4 threshold mode range=0:5:1
# @param confirmation_4 int 3 Group 4 confirmation bars range=1:20:1

show_sd = [
    bool(params.get("show_sd_1", False)),
    bool(params.get("show_sd_2", True)),
    bool(params.get("show_sd_3", False)),
    bool(params.get("show_sd_4", False)),
]
show_support = [
    bool(params.get("show_support_1", False)),
    bool(params.get("show_support_2", False)),
    bool(params.get("show_support_3", False)),
    bool(params.get("show_support_4", False)),
]
show_resistance = [
    bool(params.get("show_resistance_1", False)),
    bool(params.get("show_resistance_2", False)),
    bool(params.get("show_resistance_3", False)),
    bool(params.get("show_resistance_4", False)),
]
rsi_lengths = [
    max(1, int(params.get("rsi_length_1", 7))),
    max(1, int(params.get("rsi_length_2", 14))),
    max(1, int(params.get("rsi_length_3", 21))),
    max(1, int(params.get("rsi_length_4", 28))),
]
threshold_modes = [
    max(0, min(5, int(params.get("threshold_mode_1", 0)))),
    max(0, min(5, int(params.get("threshold_mode_2", 0)))),
    max(0, min(5, int(params.get("threshold_mode_3", 0)))),
    max(0, min(5, int(params.get("threshold_mode_4", 0)))),
]
confirmations = [
    max(1, int(params.get("confirmation_1", 3))),
    max(1, int(params.get("confirmation_2", 3))),
    max(1, int(params.get("confirmation_3", 3))),
    max(1, int(params.get("confirmation_4", 3))),
]

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def pine_rsi(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) <= period:
        return result
    gains = np.zeros(len(source), dtype=float)
    losses = np.zeros(len(source), dtype=float)
    for index in range(1, len(source)):
        change = source[index] - source[index - 1]
        gains[index] = max(change, 0.0)
        losses[index] = max(-change, 0.0)
    average_gain = float(np.sum(gains[1 : period + 1])) / period
    average_loss = float(np.sum(losses[1 : period + 1])) / period
    if average_loss == 0.0:
        result[period] = 100.0 if average_gain > 0.0 else 0.0
    else:
        result[period] = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    for index in range(period + 1, len(source)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
        if average_loss == 0.0:
            result[index] = 100.0 if average_gain > 0.0 else 0.0
        elif average_gain == 0.0:
            result[index] = 0.0
        else:
            result[index] = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


def retained_zone(condition, confirmation):
    zone_high = np.full(n, np.nan, dtype=float)
    zone_low = np.full(n, np.nan, dtype=float)
    streak = 0
    retained_high = np.nan
    retained_low = np.nan
    for index in range(n):
        streak = streak + 1 if bool(condition[index]) else 0
        if streak >= confirmation:
            retained_high = high[index]
            retained_low = low[index]
        zone_high[index] = retained_high
        zone_low[index] = retained_low
    return zone_high, zone_low


def group_zones(period, threshold_mode, confirmation):
    thresholds = [70.0, 75.0, 80.0, 85.0, 90.0, 95.0]
    upper = thresholds[threshold_mode]
    lower = 100.0 - upper
    oscillator = pine_rsi(close, period)
    overbought = np.isfinite(oscillator) & (oscillator > upper)
    oversold = np.isfinite(oscillator) & (oscillator < lower)
    combined_high, combined_low = retained_zone(overbought | oversold, confirmation)
    resistance_high, resistance_low = retained_zone(overbought, confirmation)
    support_high, support_low = retained_zone(oversold, confirmation)
    return {
        "combined_high": combined_high,
        "combined_low": combined_low,
        "resistance_high": resistance_high,
        "resistance_low": resistance_low,
        "support_high": support_high,
        "support_low": support_low,
    }


def line_data(values, visible):
    if not visible:
        return [None for _ in range(n)]
    data = []
    for index in range(n):
        stable = (
            index > 0
            and np.isfinite(values[index])
            and np.isfinite(values[index - 1])
            and values[index] == values[index - 1]
        )
        data.append(float(values[index]) if stable else None)
    return data


def cloud_data(upper, lower, visible, color):
    data = []
    for index in range(n):
        stable = (
            index > 0
            and np.isfinite(upper[index])
            and np.isfinite(lower[index])
            and np.isfinite(upper[index - 1])
            and upper[index] == upper[index - 1]
        )
        if visible and stable:
            data.append({"upper": float(upper[index]), "lower": float(lower[index]), "color": color})
        else:
            data.append(None)
    return data


groups = [
    group_zones(rsi_lengths[index], threshold_modes[index], confirmations[index])
    for index in range(4)
]

plots = [
    {
        "name": "Zone Anchor",
        "title": False,
        "data": [float(value) for value in close],
        "color": "rgba(0, 0, 0, 0)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    }
]
for index, group in enumerate(groups):
    number = index + 1
    combined_high = group["combined_high"]
    combined_low = group["combined_low"]
    resistance_high = group["resistance_high"]
    resistance_low = group["resistance_low"]
    support_high = group["support_high"]
    support_low = group["support_low"]
    plots.extend(
        [
            {
                "name": "Supply Demand %d High" % number,
                "data": line_data(combined_high, show_sd[index]),
                "color": "#2979FF",
                "type": "line",
                "lineWidth": 1,
                "overlay": True,
            },
            {
                "name": "Supply Demand %d Low" % number,
                "data": line_data(combined_low, show_sd[index]),
                "color": "#2979FF",
                "type": "line",
                "lineWidth": 1,
                "overlay": True,
            },
            {
                "name": "Supply Demand %d Fill" % number,
                "title": False,
                "data": cloud_data(combined_high, combined_low, show_sd[index], "#2979FF"),
                "color": "#2979FF",
                "type": "cloud",
                "opacity": 0.15,
                "overlay": True,
            },
            {
                "name": "Resistance %d Fill" % number,
                "title": False,
                "data": cloud_data(resistance_high, resistance_low, show_resistance[index], "#FF1744"),
                "color": "#FF1744",
                "type": "cloud",
                "opacity": 0.08,
                "overlay": True,
            },
            {
                "name": "Support %d Fill" % number,
                "title": False,
                "data": cloud_data(support_high, support_low, show_support[index], "#00C853"),
                "color": "#00C853",
                "type": "cloud",
                "opacity": 0.08,
                "overlay": True,
            },
        ]
    )

output = {
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
}
