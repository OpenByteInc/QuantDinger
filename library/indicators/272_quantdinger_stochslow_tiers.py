# ============================================================
# QuantDinger StochSlow Tiered Cross
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger StochSlow Tiered Cross"
my_indicator_description = (
    "Ranks Slow Stochastic zone crosses as EXTREME, A, B, or C using "
    "excursion, EMA/PSAR trend, and session-aware major-level proximity gates."
)

# @param k_period int 10 Stochastic K period range=1:500:1
# @param d_period int 3 Slow D period range=1:200:1
# @param slowing int 3 K slowing period range=1:200:1
# @param overbought float 80.0 Ordinary overbought level range=0:100:1
# @param oversold float 20.0 Ordinary oversold level range=0:100:1
# @param zone_bars int 5 Zone lookback bars range=1:100:1
# @param use_extreme bool true Enable EXTREME tier
# @param extreme_overbought float 95.0 Extreme overbought level range=0:100:1
# @param extreme_oversold float 5.0 Extreme oversold level range=0:100:1
# @param extreme_trend_gate bool false Require trend for EXTREME
# @param extreme_level_gate bool false Require major level for EXTREME
# @param excursion_gate bool true Require a fresh zone excursion
# @param trend_gate bool false Require EMA and PSAR alignment for A/B
# @param level_gate bool true Use major-level proximity for tier A
# @param ema_length int 8 Trend EMA length range=1:500:1
# @param show_counter bool true Show tier counter
# @param show_tier_c bool true Show tier C
# @param majors_csv str 7469,7526,7584,7604,7622,7663,7687,7695,7714,7728,7759,7782 Major levels
# @param rth_proximity float 8.0 RTH proximity range=0:1000:0.25
# @param globex_proximity float 5.0 Globex proximity range=0:1000:0.25

k_period = min(500, max(1, int(params.get("k_period", 10))))
d_period = min(200, max(1, int(params.get("d_period", 3))))
slowing = min(200, max(1, int(params.get("slowing", 3))))
overbought = min(100.0, max(0.0, float(params.get("overbought", 80.0))))
oversold = min(100.0, max(0.0, float(params.get("oversold", 20.0))))
zone_bars = min(100, max(1, int(params.get("zone_bars", 5))))
use_extreme = bool(params.get("use_extreme", True))
extreme_overbought = min(100.0, max(0.0, float(params.get("extreme_overbought", 95.0))))
extreme_oversold = min(100.0, max(0.0, float(params.get("extreme_oversold", 5.0))))
extreme_trend_gate = bool(params.get("extreme_trend_gate", False))
extreme_level_gate = bool(params.get("extreme_level_gate", False))
excursion_gate = bool(params.get("excursion_gate", True))
trend_gate = bool(params.get("trend_gate", False))
level_gate = bool(params.get("level_gate", True))
ema_length = min(500, max(1, int(params.get("ema_length", 8))))
show_counter = bool(params.get("show_counter", True))
show_tier_c = bool(params.get("show_tier_c", True))
majors_csv = str(params.get("majors_csv", "7469,7526,7584,7604,7622,7663,7687,7695,7714,7728,7759,7782"))
rth_proximity = min(1000.0, max(0.0, float(params.get("rth_proximity", 8.0))))
globex_proximity = min(1000.0, max(0.0, float(params.get("globex_proximity", 5.0))))

majors = []
for part in majors_csv.replace(" ", "").split(","):
    try:
        value = float(part)
        if np.isfinite(value):
            majors.append(value)
    except Exception:
        pass

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)
times = df["time"].to_numpy(dtype=float) if "time" in df.columns else np.arange(n, dtype=float) * 3600000.0
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0


def _sma(values, length):
    return pd.Series(np.asarray(values, dtype=float)).rolling(length, min_periods=length).mean().to_numpy(dtype=float)


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    state = np.nan
    for index, value in enumerate(values):
        state = value if not np.isfinite(state) else alpha * value + (1.0 - alpha) * state
        result[index] = state
    return result


highest = pd.Series(high).rolling(k_period, min_periods=k_period).max().to_numpy(dtype=float)
lowest = pd.Series(low).rolling(k_period, min_periods=k_period).min().to_numpy(dtype=float)
price_range = highest - lowest
raw_k = np.full(n, np.nan, dtype=float)
valid_range = np.isfinite(price_range)
raw_k[valid_range & (price_range == 0.0)] = 50.0
nonzero = valid_range & (price_range != 0.0)
raw_k[nonzero] = 100.0 * (close[nonzero] - lowest[nonzero]) / price_range[nonzero]
full_k = _sma(raw_k, slowing)
full_d = _sma(full_k, d_period)
ema_trend = _ema(close, ema_length)

psar = np.full(n, np.nan, dtype=float)
if n > 1:
    rising = close[1] > close[0]
    extreme = high[1] if rising else low[1]
    psar[1] = low[0] if rising else high[0]
    acceleration = 0.02
    for index in range(2, n):
        value = psar[index - 1] + acceleration * (extreme - psar[index - 1])
        if rising:
            value = min(value, low[index - 1], low[index - 2])
            if value > low[index]:
                rising = False
                value = extreme
                extreme = low[index]
                acceleration = 0.02
            elif high[index] > extreme:
                extreme = high[index]
                acceleration = min(0.20, acceleration + 0.02)
        else:
            value = max(value, high[index - 1], high[index - 2])
            if value < high[index]:
                rising = True
                value = extreme
                extreme = high[index]
                acceleration = 0.02
            elif low[index] < extreme:
                extreme = low[index]
                acceleration = min(0.20, acceleration + 0.02)
        psar[index] = value

trend_long = np.isfinite(ema_trend) & np.isfinite(psar) & (close > ema_trend) & (psar < close)
trend_short = np.isfinite(ema_trend) & np.isfinite(psar) & (close < ema_trend) & (psar > close)

timestamps = pd.to_datetime(times, unit="ms", utc=True).tz_convert("America/New_York") if n else []
in_rth = np.zeros(n, dtype=bool)
for index in range(n):
    timestamp = timestamps[index]
    minute = timestamp.hour * 60 + timestamp.minute
    in_rth[index] = timestamp.weekday() < 5 and 570 <= minute < 960
proximity = np.where(in_rth, rth_proximity, globex_proximity)
major_distance = np.full(n, np.nan, dtype=float)
if majors:
    major_array = np.asarray(majors, dtype=float)
    for index in range(n):
        major_distance[index] = float(np.min(np.abs(major_array - close[index])))
near_level = np.isfinite(major_distance) & (major_distance <= proximity)

bull_cross = np.zeros(n, dtype=bool)
bear_cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    if all(np.isfinite(value) for value in (full_k[index], full_d[index], full_k[index - 1], full_d[index - 1])):
        bull_cross[index] = full_k[index] > full_d[index] and full_k[index - 1] <= full_d[index - 1]
        bear_cross[index] = full_k[index] < full_d[index] and full_k[index - 1] >= full_d[index - 1]
k_low = pd.Series(full_k).rolling(zone_bars, min_periods=zone_bars).min().to_numpy(dtype=float)
k_high = pd.Series(full_k).rolling(zone_bars, min_periods=zone_bars).max().to_numpy(dtype=float)
bull_raw = bull_cross & np.isfinite(k_low) & (k_low <= oversold)
bear_raw = bear_cross & np.isfinite(k_high) & (k_high >= overbought)
bull_deep = bull_cross & np.isfinite(k_low) & (k_low <= extreme_oversold) & np.isfinite(full_k) & (full_k <= oversold)
bear_deep = bear_cross & np.isfinite(k_high) & (k_high >= extreme_overbought) & np.isfinite(full_k) & (full_k >= overbought)

bull_signal = np.zeros(n, dtype=bool)
bear_signal = np.zeros(n, dtype=bool)
bull_armed = True
bear_armed = True
for index in range(n):
    bull_signal[index] = bull_raw[index] and (not excursion_gate or bull_armed)
    bear_signal[index] = bear_raw[index] and (not excursion_gate or bear_armed)
    if bull_signal[index]:
        bull_armed = False
    elif np.isfinite(full_k[index]) and full_k[index] > oversold:
        bull_armed = True
    if bear_signal[index]:
        bear_armed = False
    elif np.isfinite(full_k[index]) and full_k[index] < overbought:
        bear_armed = True

all_true = np.ones(n, dtype=bool)
trend_ok_long = trend_long if trend_gate else all_true
trend_ok_short = trend_short if trend_gate else all_true
level_ok = near_level if level_gate else all_true
extreme_trend_long_ok = trend_long if extreme_trend_gate else all_true
extreme_trend_short_ok = trend_short if extreme_trend_gate else all_true
extreme_level_ok = near_level if extreme_level_gate else all_true
extreme_long = bull_signal & bull_deep & extreme_trend_long_ok & extreme_level_ok if use_extreme else np.zeros(n, dtype=bool)
extreme_short = bear_signal & bear_deep & extreme_trend_short_ok & extreme_level_ok if use_extreme else np.zeros(n, dtype=bool)
tier_a_long = bull_signal & ~extreme_long & trend_ok_long & level_ok
tier_a_short = bear_signal & ~extreme_short & trend_ok_short & level_ok
tier_b_long = bull_signal & ~extreme_long & trend_ok_long & ~tier_a_long
tier_b_short = bear_signal & ~extreme_short & trend_ok_short & ~tier_a_short
tier_c_long = bull_signal & ~extreme_long & ~trend_ok_long
tier_c_short = bear_signal & ~extreme_short & ~trend_ok_short

counts = {
    "EXTREME": int(np.sum(extreme_long | extreme_short)),
    "A": int(np.sum(tier_a_long | tier_a_short)),
    "B": int(np.sum(tier_b_long | tier_b_short)),
    "C": int(np.sum(tier_c_long | tier_c_short)),
}


def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _marks(events):
    return [float(full_k[index]) if events[index] and np.isfinite(full_k[index]) else None for index in range(n)]


plots = [
    {"name": "SlowK", "data": _finite_or_none(full_k), "color": "#26C6DA", "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "SlowD", "data": _finite_or_none(full_d), "color": "#EF5350", "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Overbought", "data": [overbought] * n, "color": "rgba(128,128,128,0.50)", "type": "line", "lineWidth": 1, "dashed": True, "overlay": False},
    {"name": "Oversold", "data": [oversold] * n, "color": "rgba(128,128,128,0.50)", "type": "line", "lineWidth": 1, "dashed": True, "overlay": False},
    {"name": "Extreme Overbought", "data": [extreme_overbought] * n, "color": "rgba(255,23,68,0.60)", "type": "line", "lineWidth": 1, "dotted": True, "overlay": False},
    {"name": "Extreme Oversold", "data": [extreme_oversold] * n, "color": "rgba(0,230,118,0.60)", "type": "line", "lineWidth": 1, "dotted": True, "overlay": False},
    {"name": "Upper Extreme Zone", "title": False, "data": [{"upper": 100.0, "lower": extreme_overbought, "color": "#FF1744"}] * n, "color": "#FF1744", "type": "cloud", "opacity": 0.12, "overlay": False},
    {"name": "Lower Extreme Zone", "title": False, "data": [{"upper": extreme_oversold, "lower": 0.0, "color": "#00E676"}] * n, "color": "#00E676", "type": "cloud", "opacity": 0.12, "overlay": False},
]
signals = [
    {"type": "buy", "name": "EXTREME Long", "text": "X", "data": _marks(extreme_long), "color": "#00E676", "renderMode": "events"},
    {"type": "sell", "name": "EXTREME Short", "text": "X", "data": _marks(extreme_short), "color": "#FF1744", "renderMode": "events"},
    {"type": "buy", "name": "A Long", "text": "A", "data": _marks(tier_a_long), "color": "#00E676", "renderMode": "events"},
    {"type": "sell", "name": "A Short", "text": "A", "data": _marks(tier_a_short), "color": "#FF1744", "renderMode": "events"},
    {"type": "buy", "name": "B Long", "text": "B", "data": _marks(tier_b_long), "color": "#00B85C", "renderMode": "events"},
    {"type": "sell", "name": "B Short", "text": "B", "data": _marks(tier_b_short), "color": "#C4142F", "renderMode": "events"},
    {"type": "buy", "name": "C Long", "text": "C", "data": _marks(tier_c_long), "color": "#B0BEC5", "renderMode": "events" if show_tier_c else "hidden"},
    {"type": "sell", "name": "C Short", "text": "C", "data": _marks(tier_c_short), "color": "#B0BEC5", "renderMode": "events" if show_tier_c else "hidden"},
]
tables = []
if show_counter:
    tables.append({"title": "StochSlow Tiers", "position": "top-right", "rows": [["EXTREME", str(counts["EXTREME"])], ["A", str(counts["A"])], ["B", str(counts["B"])], ["C", str(counts["C"])]], "compact": True})

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": [],
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": ["rgba(0,230,118,0.12)" if extreme_long[i] else "rgba(255,23,68,0.12)" if extreme_short[i] else None for i in range(n)],
    "calculatedVars": {
        "rawK": _finite_or_none(raw_k), "fullK": _finite_or_none(full_k), "fullD": _finite_or_none(full_d),
        "ema": _finite_or_none(ema_trend), "psar": _finite_or_none(psar),
        "trendLong": trend_long.tolist(), "trendShort": trend_short.tolist(),
        "inRth": in_rth.tolist(), "majorDistance": _finite_or_none(major_distance), "nearLevel": near_level.tolist(),
        "bullCross": bull_cross.tolist(), "bearCross": bear_cross.tolist(),
        "kLow": _finite_or_none(k_low), "kHigh": _finite_or_none(k_high),
        "bullRaw": bull_raw.tolist(), "bearRaw": bear_raw.tolist(),
        "bullDeep": bull_deep.tolist(), "bearDeep": bear_deep.tolist(),
        "bullSignal": bull_signal.tolist(), "bearSignal": bear_signal.tolist(),
        "extremeLong": extreme_long.tolist(), "extremeShort": extreme_short.tolist(),
        "tierALong": tier_a_long.tolist(), "tierAShort": tier_a_short.tolist(),
        "tierBLong": tier_b_long.tolist(), "tierBShort": tier_b_short.tolist(),
        "tierCLong": tier_c_long.tolist(), "tierCShort": tier_c_short.tolist(),
        "counts": counts, "majors": majors,
    },
}
