# ============================================================
# QuantDinger Mavilim Weighted Trend
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Mavilim Weighted Trend"
my_indicator_description = (
    "A six-stage weighted moving-average cascade whose color tracks its current slope."
)

# @param first_period int 3 First weighted period range=1:100:1
# @param second_period int 5 Second weighted period range=1:100:1
# @param show_reference bool false Show fixed 3-5-8-13-21-34 reference

first_period = max(1, int(params.get("first_period", 3)))
second_period = max(1, int(params.get("second_period", 5)))
show_reference = bool(params.get("show_reference", False))

df = df.copy()
close = df["close"].astype(float)
n = len(df)

def weighted_average(series, period):
    denominator = float(period * (period + 1) / 2)

    def weighted_window(values):
        weighted_total = 0.0
        for index in range(len(values)):
            weighted_total += float(values[index]) * float(index + 1)
        return weighted_total / denominator

    return series.rolling(window=period, min_periods=period).apply(
        weighted_window, raw=True
    )

third_period = first_period + second_period
fourth_period = second_period + third_period
fifth_period = third_period + fourth_period
sixth_period = fourth_period + fifth_period

stage_1 = weighted_average(close, first_period)
stage_2 = weighted_average(stage_1, second_period)
stage_3 = weighted_average(stage_2, third_period)
stage_4 = weighted_average(stage_3, fourth_period)
stage_5 = weighted_average(stage_4, fifth_period)
trend = weighted_average(stage_5, sixth_period)

reference = weighted_average(
    weighted_average(
        weighted_average(
            weighted_average(weighted_average(weighted_average(close, 3), 5), 8),
            13,
        ),
        21,
    ),
    34,
)

trend_values = trend.to_numpy(dtype=float)
trend_plot = []
for index, value in enumerate(trend_values):
    if not np.isfinite(value):
        trend_plot.append(None)
        continue
    previous = trend_values[index - 1] if index > 0 else np.nan
    color = "#0000FF" if np.isfinite(previous) and value > previous else "#FF0000" if np.isfinite(previous) and value < previous else "#FFFF00"
    trend_plot.append({"value": float(value), "color": color})

reference_plot = [float(value) if show_reference and np.isfinite(value) else None for value in reference.to_numpy(dtype=float)]
rising = (trend > trend.shift(1)) & (trend.shift(1) <= trend.shift(2))
falling = (trend < trend.shift(1)) & (trend.shift(1) >= trend.shift(2))
buy_marks = [float(trend.iloc[index]) if bool(rising.iloc[index]) and np.isfinite(trend.iloc[index]) else None for index in range(n)]
sell_marks = [float(trend.iloc[index]) if bool(falling.iloc[index]) and np.isfinite(trend.iloc[index]) else None for index in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Weighted Trend", "data": trend_plot, "color": "#0000FF", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Reference Trend", "data": reference_plot, "color": "#4477FF", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "Slope Turns Up", "data": buy_marks, "color": "#0000FF", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Slope Turns Down", "data": sell_marks, "color": "#FF0000", "renderMode": "events"},
    ],
    "layers": [],
}
