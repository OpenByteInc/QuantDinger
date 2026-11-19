# ============================================================
# QuantDinger RS Support and Resistance
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger RS Support and Resistance"
my_indicator_description = (
    "Short- and long-window rolling extremes held as support and resistance, "
    "with trade-zone limits and optional formation events."
)

# @param use_alternative bool false Use an alternative price source
# @param alternative_source int 0 Alternative source range=0:2:1
# @param short_window int 8 Short lookback range=2:200:1
# @param long_window int 21 Long lookback range=2:300:1
# @param trade_zone_percent float 25.0 Trade-zone percent range=0.0:100.0:1.0
# @param show_events bool false Show level formation events

use_alternative = bool(params.get("use_alternative", False))
alternative_source = max(0, min(2, int(params.get("alternative_source", 0))))
short_window = max(2, int(params.get("short_window", 8)))
long_window = max(2, int(params.get("long_window", 21)))
trade_zone_percent = max(0.0, min(100.0, float(params.get("trade_zone_percent", 25.0)))) / 100.0
show_events = bool(params.get("show_events", False))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

if alternative_source == 1:
    alternative = open_price
elif alternative_source == 2:
    alternative = (high + low) / 2.0
else:
    alternative = close
high_source = alternative if use_alternative else high
low_source = alternative if use_alternative else low

short_high = pd.Series(high_source).rolling(short_window, min_periods=1).max().to_numpy(dtype=float)
short_low = pd.Series(low_source).rolling(short_window, min_periods=1).min().to_numpy(dtype=float)
long_high = pd.Series(high_source).rolling(long_window, min_periods=1).max().to_numpy(dtype=float)
long_low = pd.Series(low_source).rolling(long_window, min_periods=1).min().to_numpy(dtype=float)

short_top = np.full(n, np.nan, dtype=float)
short_bottom = np.full(n, np.nan, dtype=float)
long_top = np.full(n, np.nan, dtype=float)
long_bottom = np.full(n, np.nan, dtype=float)
for index in range(n):
    short_top[index] = high_source[index] if high_source[index] >= short_high[index] else short_top[index - 1] if index > 0 else high_source[index]
    short_bottom[index] = low_source[index] if low_source[index] <= short_low[index] else short_bottom[index - 1] if index > 0 else low_source[index]
    long_top[index] = high_source[index] if high_source[index] >= long_high[index] else long_top[index - 1] if index > 0 else high_source[index]
    long_bottom[index] = low_source[index] if low_source[index] <= long_low[index] else long_bottom[index - 1] if index > 0 else low_source[index]

short_resistance = np.full(n, np.nan, dtype=float)
short_support = np.full(n, np.nan, dtype=float)
long_resistance = np.full(n, np.nan, dtype=float)
long_support = np.full(n, np.nan, dtype=float)
long_resistance_marker = np.full(n, np.nan, dtype=float)
long_support_marker = np.full(n, np.nan, dtype=float)
fixed_long_resistance = np.full(n, np.nan, dtype=float)
fixed_long_support = np.full(n, np.nan, dtype=float)

for index in range(n):
    short_top_changed = index == 0 or short_top[index] != short_top[index - 1]
    short_bottom_changed = index == 0 or short_bottom[index] != short_bottom[index - 1]
    long_top_changed = index == 0 or long_top[index] != long_top[index - 1]
    long_bottom_changed = index == 0 or long_bottom[index] != long_bottom[index - 1]
    if not short_top_changed and short_top[index] != long_top[index]:
        short_resistance[index] = short_top[index]
    if not short_bottom_changed and short_bottom[index] != long_bottom[index]:
        short_support[index] = short_bottom[index]
    if not long_top_changed:
        long_resistance[index] = long_top[index]
    if not long_bottom_changed:
        long_support[index] = long_bottom[index]
    fixed_long_resistance[index] = long_resistance[index] if np.isfinite(long_resistance[index]) else fixed_long_resistance[index - 1] if index > 0 else np.nan
    fixed_long_support[index] = long_support[index] if np.isfinite(long_support[index]) else fixed_long_support[index - 1] if index > 0 else np.nan
    if index > 0 and np.isfinite(fixed_long_resistance[index]) and np.isfinite(fixed_long_resistance[index - 1]) and fixed_long_resistance[index] != fixed_long_resistance[index - 1]:
        long_resistance_marker[index] = long_resistance[index]
    if index > 0 and np.isfinite(fixed_long_support[index]) and np.isfinite(fixed_long_support[index - 1]) and fixed_long_support[index] != fixed_long_support[index - 1]:
        long_support_marker[index] = long_support[index]

fixed_short_resistance = np.full(n, np.nan, dtype=float)
fixed_short_support = np.full(n, np.nan, dtype=float)
resistance_limit = np.full(n, np.nan, dtype=float)
support_limit = np.full(n, np.nan, dtype=float)
for index in range(n):
    fixed_short_resistance[index] = short_resistance[index] if np.isfinite(short_resistance[index]) else fixed_short_resistance[index - 1] if index > 0 else np.nan
    fixed_short_support[index] = short_support[index] if np.isfinite(short_support[index]) else fixed_short_support[index - 1] if index > 0 else np.nan
    difference = fixed_short_resistance[index] - fixed_short_support[index] if np.isfinite(fixed_short_resistance[index]) and np.isfinite(fixed_short_support[index]) else np.nan
    if np.isfinite(short_resistance[index]):
        resistance_limit[index] = resistance_limit[index - 1] if index > 0 and np.isfinite(resistance_limit[index - 1]) else short_resistance[index] - difference * trade_zone_percent if np.isfinite(difference) else np.nan
    if np.isfinite(short_support[index]):
        support_limit[index] = support_limit[index - 1] if index > 0 and np.isfinite(support_limit[index - 1]) else short_support[index] + difference * trade_zone_percent if np.isfinite(difference) else np.nan

resistance_cloud = []
support_cloud = []
for index in range(n):
    resistance_cloud.append({"upper": float(max(short_resistance[index], long_resistance[index])), "lower": float(min(short_resistance[index], long_resistance[index])), "color": "#FF9800"} if np.isfinite(short_resistance[index]) and np.isfinite(long_resistance[index]) else None)
    support_cloud.append({"upper": float(max(short_support[index], long_support[index])), "lower": float(min(short_support[index], long_support[index])), "color": "#808000"} if np.isfinite(short_support[index]) and np.isfinite(long_support[index]) else None)


def finite_line(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Short Resistance", "data": finite_line(short_resistance), "color": "#000080", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Short Support", "data": finite_line(short_support), "color": "#000080", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Long Resistance", "data": finite_line(long_resistance), "color": "#2196F3B3", "type": "line", "lineWidth": 4, "overlay": True},
        {"name": "Long Support", "data": finite_line(long_support), "color": "#2196F3B3", "type": "line", "lineWidth": 4, "overlay": True},
        {"name": "Long Resistance Marker", "data": finite_line(long_resistance_marker), "color": "#0000FF", "type": "line", "lineWidth": 4, "overlay": True},
        {"name": "Long Support Marker", "data": finite_line(long_support_marker), "color": "#0000FF", "type": "line", "lineWidth": 4, "overlay": True},
        {"name": "Resistance Zone", "title": False, "data": resistance_cloud, "color": "#FF9800", "type": "cloud", "opacity": 0.20, "overlay": True},
        {"name": "Support Zone", "title": False, "data": support_cloud, "color": "#808000", "type": "cloud", "opacity": 0.20, "overlay": True},
        {"name": "Resistance Trade Limit", "data": finite_line(resistance_limit), "color": "#EF4444", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Support Trade Limit", "data": finite_line(support_limit), "color": "#84CC16", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "sell", "text": "SR", "name": "New Short Resistance", "data": [float(short_resistance[i]) if show_events and np.isfinite(short_resistance[i]) and (i == 0 or not np.isfinite(short_resistance[i - 1])) else None for i in range(n)], "color": "#FF9800", "renderMode": "events"},
        {"type": "buy", "text": "SS", "name": "New Short Support", "data": [float(short_support[i]) if show_events and np.isfinite(short_support[i]) and (i == 0 or not np.isfinite(short_support[i - 1])) else None for i in range(n)], "color": "#808000", "renderMode": "events"},
    ],
    "layers": [],
}
