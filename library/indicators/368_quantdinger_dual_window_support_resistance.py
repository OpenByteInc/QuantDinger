# ============================================================
# QuantDinger Dual-Window Support and Resistance
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Dual-Window Support and Resistance"
my_indicator_description = "Tracks short- and long-window breakout levels, delayed long-level markers, and resettable trade zones."

# @param use_alternative_source bool false Use one price series for both high and low tests
# @param alternative_source str Close Alternative price source values=Close,Open,HL2,HLC3,OHLC4
# @param short_window int 8 Short lookback window range=1:10000:1
# @param long_window int 21 Long lookback window range=1:10000:1
# @param trade_zone_percent float 25 Percent of short-level range range=0:100:0.1
# @param use_alert_signals bool false Original alert toggle (source behavior remains unconditional)

use_alternative_source = bool(params.get("use_alternative_source", False))
alternative_source_name = str(params.get("alternative_source", "Close"))
alternative_source_name = alternative_source_name if alternative_source_name in ("Close", "Open", "HL2", "HLC3", "OHLC4") else "Close"
short_window = min(10000, max(1, int(params.get("short_window", 8))))
long_window = min(10000, max(1, int(params.get("long_window", 21))))
trade_zone_percent = min(100.0, max(0.0, float(params.get("trade_zone_percent", 25.0)))) / 100.0
alerts_enabled_input = bool(params.get("use_alert_signals", False))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

if alternative_source_name == "Open":
    alternative_source = opened
elif alternative_source_name == "HL2":
    alternative_source = (high + low) / 2.0
elif alternative_source_name == "HLC3":
    alternative_source = (high + low + close) / 3.0
elif alternative_source_name == "OHLC4":
    alternative_source = (opened + high + low + close) / 4.0
else:
    alternative_source = close
high_source = alternative_source if use_alternative_source else high
low_source = alternative_source if use_alternative_source else low

short_top = np.full(n, np.nan, dtype=float)
short_bottom = np.full(n, np.nan, dtype=float)
long_top = np.full(n, np.nan, dtype=float)
long_bottom = np.full(n, np.nan, dtype=float)
latest_short_top = latest_short_bottom = latest_long_top = latest_long_bottom = np.nan
for index in range(n):
    if high_source[index] >= np.max(high_source[max(0, index - short_window + 1):index + 1]):
        latest_short_top = high_source[index]
    if low_source[index] <= np.min(low_source[max(0, index - short_window + 1):index + 1]):
        latest_short_bottom = low_source[index]
    if high_source[index] >= np.max(high_source[max(0, index - long_window + 1):index + 1]):
        latest_long_top = high_source[index]
    if low_source[index] <= np.min(low_source[max(0, index - long_window + 1):index + 1]):
        latest_long_bottom = low_source[index]
    short_top[index], short_bottom[index] = latest_short_top, latest_short_bottom
    long_top[index], long_bottom[index] = latest_long_top, latest_long_bottom

short_resistance = np.full(n, np.nan, dtype=float)
short_support = np.full(n, np.nan, dtype=float)
long_resistance = np.full(n, np.nan, dtype=float)
long_support = np.full(n, np.nan, dtype=float)
fixed_long_resistance = np.full(n, np.nan, dtype=float)
fixed_long_support = np.full(n, np.nan, dtype=float)
new_long_resistance = np.zeros(n, dtype=bool)
new_long_support = np.zeros(n, dtype=bool)
long_resistance_marker = np.full(n, np.nan, dtype=float)
long_support_marker = np.full(n, np.nan, dtype=float)
last_fixed_resistance = last_fixed_support = np.nan
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
    previous_fixed_resistance = last_fixed_resistance
    previous_fixed_support = last_fixed_support
    if np.isfinite(long_resistance[index]):
        last_fixed_resistance = long_resistance[index]
    if np.isfinite(long_support[index]):
        last_fixed_support = long_support[index]
    fixed_long_resistance[index] = last_fixed_resistance
    fixed_long_support[index] = last_fixed_support
    new_long_resistance[index] = index > 0 and np.isfinite(previous_fixed_resistance) and np.isfinite(last_fixed_resistance) and last_fixed_resistance != previous_fixed_resistance
    new_long_support[index] = index > 0 and np.isfinite(previous_fixed_support) and np.isfinite(last_fixed_support) and last_fixed_support != previous_fixed_support
    if new_long_resistance[index]:
        long_resistance_marker[index] = long_resistance[index]
    if new_long_support[index]:
        long_support_marker[index] = long_support[index]

fixed_short_resistance = np.full(n, np.nan, dtype=float)
fixed_short_support = np.full(n, np.nan, dtype=float)
last_short_resistance = last_short_support = np.nan
resistance_trade_limit = np.full(n, np.nan, dtype=float)
support_trade_limit = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.isfinite(short_resistance[index]):
        last_short_resistance = short_resistance[index]
    if np.isfinite(short_support[index]):
        last_short_support = short_support[index]
    fixed_short_resistance[index] = last_short_resistance
    fixed_short_support[index] = last_short_support
    difference = last_short_resistance - last_short_support if np.all(np.isfinite([last_short_resistance, last_short_support])) else np.nan
    if np.isfinite(short_resistance[index]):
        if index > 0 and np.isfinite(resistance_trade_limit[index - 1]):
            resistance_trade_limit[index] = resistance_trade_limit[index - 1]
        elif np.isfinite(difference):
            resistance_trade_limit[index] = short_resistance[index] - difference * trade_zone_percent
    if np.isfinite(short_support[index]):
        if index > 0 and np.isfinite(support_trade_limit[index - 1]):
            support_trade_limit[index] = support_trade_limit[index - 1]
        elif np.isfinite(difference):
            support_trade_limit[index] = short_support[index] + difference * trade_zone_percent

new_short_resistance = np.asarray([index > 0 and np.isfinite(short_resistance[index]) and not np.isfinite(short_resistance[index - 1]) for index in range(n)], dtype=bool)
new_short_support = np.asarray([index > 0 and np.isfinite(short_support[index]) and not np.isfinite(short_support[index - 1]) for index in range(n)], dtype=bool)
resistance_cloud = [
    {"upper": float(max(short_resistance[index], long_resistance[index])), "lower": float(min(short_resistance[index], long_resistance[index])), "color": "#FF9800", "opacity": 0.20}
    if np.all(np.isfinite([short_resistance[index], long_resistance[index]])) else None for index in range(n)
]
support_cloud = [
    {"upper": float(max(short_support[index], long_support[index])), "lower": float(min(short_support[index], long_support[index])), "color": "#808000", "opacity": 0.20}
    if np.all(np.isfinite([short_support[index], long_support[index]])) else None for index in range(n)
]


def _events(mask, prices, text, color, event_type):
    return {"type": event_type, "name": text, "data": [{"active": True, "price": float(prices[index]), "text": text, "color": color} if active and np.isfinite(prices[index]) else None for index, active in enumerate(mask)], "color": color, "renderMode": "events"}


def _limited_event_masks(masks, recent_bars=120, total_cap=14, minimum_gap=4):
    result = [np.zeros(n, dtype=bool) for _ in masks]
    candidates = []
    first_index = max(0, n - recent_bars)
    for mask_index, mask in enumerate(masks):
        for index in np.flatnonzero(np.asarray(mask, dtype=bool)):
            if index >= first_index:
                candidates.append((int(index), mask_index))
    selected_indices = []
    for index, mask_index in sorted(candidates, reverse=True):
        if len(selected_indices) >= total_cap:
            break
        if any(abs(index - selected) < minimum_gap for selected in selected_indices):
            continue
        result[mask_index][index] = True
        selected_indices.append(index)
    return result


display_short_resistance, display_short_support, display_long_resistance, display_long_support = _limited_event_masks([
    new_short_resistance,
    new_short_support,
    new_long_resistance,
    new_long_support,
])


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Short Resistance", "title": True, "data": _finite(short_resistance), "color": "#000080", "type": "line", "overlay": True},
        {"name": "Short Support", "title": True, "data": _finite(short_support), "color": "#000080", "type": "line", "overlay": True},
        {"name": "Long Resistance", "title": True, "data": _finite(long_resistance), "color": "#2196F3", "type": "line", "lineWidth": 4, "overlay": True},
        {"name": "Long Support", "title": True, "data": _finite(long_support), "color": "#2196F3", "type": "line", "lineWidth": 4, "overlay": True},
        {"name": "Resistance Zone", "title": False, "data": resistance_cloud, "color": "#FF9800", "type": "cloud", "overlay": True},
        {"name": "Support Zone", "title": False, "data": support_cloud, "color": "#808000", "type": "cloud", "overlay": True},
        {"name": "Resistance Trade Limit", "title": True, "data": _finite(resistance_trade_limit), "color": "#FF0000", "type": "line", "overlay": True},
        {"name": "Support Trade Limit", "title": True, "data": _finite(support_trade_limit), "color": "#00FF00", "type": "line", "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "overlay": True},
    ],
    "signals": [
        _events(display_short_resistance, short_resistance, "New STR", "#1D4ED8", "resistance"), _events(display_short_support, short_support, "New STS", "#1D4ED8", "support"),
        _events(display_long_resistance, long_resistance_marker, "New LTR", "#2563EB", "resistance"), _events(display_long_support, long_support_marker, "New LTS", "#2563EB", "support"),
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "alertsEnabledInput": bool(alerts_enabled_input), "highSource": _finite(high_source), "lowSource": _finite(low_source),
        "shortTop": _finite(short_top), "shortBottom": _finite(short_bottom), "longTop": _finite(long_top), "longBottom": _finite(long_bottom),
        "shortResistance": _finite(short_resistance), "shortSupport": _finite(short_support), "longResistance": _finite(long_resistance), "longSupport": _finite(long_support),
        "fixedLongResistance": _finite(fixed_long_resistance), "fixedLongSupport": _finite(fixed_long_support),
        "newShortResistance": new_short_resistance.tolist(), "newShortSupport": new_short_support.tolist(), "newLongResistance": new_long_resistance.tolist(), "newLongSupport": new_long_support.tolist(),
        "longResistanceMarker": _finite(long_resistance_marker), "longSupportMarker": _finite(long_support_marker),
        "fixedShortResistance": _finite(fixed_short_resistance), "fixedShortSupport": _finite(fixed_short_support),
        "resistanceTradeLimit": _finite(resistance_trade_limit), "supportTradeLimit": _finite(support_trade_limit),
    },
}
