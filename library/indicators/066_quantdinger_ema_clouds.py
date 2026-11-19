# ============================================================
# QuantDinger EMA Clouds
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger EMA Clouds"
my_indicator_description = (
    "Five configurable moving-average pairs rendered as direction-colored "
    "clouds, with optional slope-colored boundary lines and forward offset."
)

# @param average_type int 0 Moving average type range=0:1:1
# @param short_1 int 8 Cloud 1 short period range=1:500:1
# @param long_1 int 9 Cloud 1 long period range=1:500:1
# @param short_2 int 5 Cloud 2 short period range=1:500:1
# @param long_2 int 12 Cloud 2 long period range=1:500:1
# @param short_3 int 34 Cloud 3 short period range=1:500:1
# @param long_3 int 50 Cloud 3 long period range=1:500:1
# @param short_4 int 72 Cloud 4 short period range=1:500:1
# @param long_4 int 89 Cloud 4 long period range=1:500:1
# @param short_5 int 180 Cloud 5 short period range=1:500:1
# @param long_5 int 200 Cloud 5 long period range=1:500:1
# @param show_lines bool false Show average boundary lines
# @param show_cloud_1 bool true Show cloud 1
# @param show_cloud_2 bool true Show cloud 2
# @param show_cloud_3 bool true Show cloud 3
# @param show_cloud_4 bool false Show cloud 4
# @param show_cloud_5 bool false Show cloud 5
# @param leading_period int 0 Forward cloud offset range=0:100:1

average_type = max(0, min(1, int(params.get("average_type", 0))))
lengths = [
    (max(1, int(params.get("short_1", 8))), max(1, int(params.get("long_1", 9)))),
    (max(1, int(params.get("short_2", 5))), max(1, int(params.get("long_2", 12)))),
    (max(1, int(params.get("short_3", 34))), max(1, int(params.get("long_3", 50)))),
    (max(1, int(params.get("short_4", 72))), max(1, int(params.get("long_4", 89)))),
    (max(1, int(params.get("short_5", 180))), max(1, int(params.get("long_5", 200)))),
]
show_lines = bool(params.get("show_lines", False))
show_clouds = [
    bool(params.get("show_cloud_1", True)),
    bool(params.get("show_cloud_2", True)),
    bool(params.get("show_cloud_3", True)),
    bool(params.get("show_cloud_4", False)),
    bool(params.get("show_cloud_5", False)),
]
leading_period = max(0, int(params.get("leading_period", 0)))

df = df.copy()
source = ((df["high"].astype(float) + df["low"].astype(float)) / 2.0).to_numpy(dtype=float)
n = len(df)


def moving_average(period):
    if average_type == 1:
        return pd.Series(source).rolling(period, min_periods=period).mean().to_numpy(dtype=float)
    result = np.full(n, np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index in range(n):
        previous = source[index] if not np.isfinite(previous) else alpha * source[index] + (1.0 - alpha) * previous
        result[index] = previous
    return result


def shifted(values):
    result = np.full(n, np.nan, dtype=float)
    for index in range(n):
        target = index + leading_period
        if target < n and np.isfinite(values[index]):
            result[target] = values[index]
    return result


bull_colors = ["#036103", "#4CAF50", "#2196F3", "#009688", "#05BED5"]
bear_colors = ["#880E4F", "#F44336", "#FFB74D", "#F06292", "#E65100"]
opacities = [0.55, 0.35, 0.30, 0.35, 0.35]
plots = []

for pair_index in range(5):
    short_average = shifted(moving_average(lengths[pair_index][0]))
    long_average = shifted(moving_average(lengths[pair_index][1]))
    if show_clouds[pair_index]:
        cloud_data = []
        short_data = []
        long_data = []
        for index in range(n):
            if np.isfinite(short_average[index]) and np.isfinite(long_average[index]):
                color = bull_colors[pair_index] if short_average[index] >= long_average[index] else bear_colors[pair_index]
                cloud_data.append({"upper": float(max(short_average[index], long_average[index])), "lower": float(min(short_average[index], long_average[index])), "color": color})
                short_rising = index > 0 and np.isfinite(short_average[index - 1]) and short_average[index] >= short_average[index - 1]
                long_rising = index > 0 and np.isfinite(long_average[index - 1]) and long_average[index] >= long_average[index - 1]
                short_data.append({"value": float(short_average[index]), "color": "#808000" if short_rising else "#800000"})
                long_data.append({"value": float(long_average[index]), "color": "#16A34A" if long_rising else "#EF4444"})
            else:
                cloud_data.append(None)
                short_data.append(None)
                long_data.append(None)
        plots.append({"name": "MA Cloud " + str(pair_index + 1), "title": False, "data": cloud_data, "color": bull_colors[pair_index], "type": "cloud", "opacity": opacities[pair_index], "overlay": True})
        if show_lines:
            plots.append({"name": "Short MA " + str(pair_index + 1), "data": short_data, "color": "#808000", "type": "line", "lineWidth": 1, "overlay": True})
            plots.append({"name": "Long MA " + str(pair_index + 1), "data": long_data, "color": "#16A34A", "type": "line", "lineWidth": 3, "overlay": True})

output = {
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
}
