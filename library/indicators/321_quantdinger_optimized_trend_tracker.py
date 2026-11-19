# ============================================================
# QuantDinger Optimized Trend Tracker
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Optimized Trend Tracker"
my_indicator_description = "Tracks a selectable support average with a two-bar delayed optimized trailing level and three signal families."

# @param source str Close Price source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param length int 2 OTT period range=1:500:1
# @param percent float 1.4 OTT percent range=0:100:0.1
# @param show_support bool true Show support line
# @param show_support_signals bool true Show support crossing signals
# @param show_price_signals bool false Show price crossing signals
# @param highlight_ott bool false Color OTT by slope
# @param show_color_signals bool false Show OTT color-change signals
# @param highlighting bool true Highlight price regime
# @param average_type str VAR Average type values=SMA,EMA,WMA,TMA,VAR,WWMA,ZLEMA,TSF

source_name = str(params.get("source", "Close")).upper()
if source_name not in ("CLOSE", "OPEN", "HIGH", "LOW", "HL2", "HLC3", "OHLC4"):
    source_name = "CLOSE"
length = min(500, max(1, int(params.get("length", 2))))
percent = min(100.0, max(0.0, float(params.get("percent", 1.4))))
show_support = bool(params.get("show_support", True))
show_support_signals = bool(params.get("show_support_signals", True))
show_price_signals = bool(params.get("show_price_signals", False))
highlight_ott = bool(params.get("highlight_ott", False))
show_color_signals = bool(params.get("show_color_signals", False))
highlighting = bool(params.get("highlighting", True))
average_type = str(params.get("average_type", "VAR")).upper()
if average_type not in ("SMA", "EMA", "WMA", "TMA", "VAR", "WWMA", "ZLEMA", "TSF"):
    average_type = "VAR"


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _wma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.sum(window * weights) / denominator)
    return result


def _var(values, period):
    result = np.zeros(len(values), dtype=float)
    up, down = np.zeros(len(values)), np.zeros(len(values))
    alpha = 2.0 / (period + 1.0)
    for index in range(1, len(values)):
        up[index], down[index] = max(values[index] - values[index - 1], 0.0), max(values[index - 1] - values[index], 0.0)
    for index in range(len(values)):
        start = max(0, index - 8)
        upward, downward = float(np.sum(up[start:index + 1])), float(np.sum(down[start:index + 1]))
        cmo = (upward - downward) / (upward + downward) if upward + downward > 1e-12 else 0.0
        factor = alpha * abs(cmo)
        result[index] = factor * values[index] + (1.0 - factor) * (result[index - 1] if index else 0.0)
    return result


def _wwma(values, period):
    result = np.zeros(len(values), dtype=float)
    alpha = 1.0 / period
    for index, value in enumerate(values):
        result[index] = alpha * value + (1.0 - alpha) * (result[index - 1] if index else 0.0)
    return result


def _zlema(values, period):
    lag = max(0, int(period / 2))
    adjusted = values.copy() if lag == 0 else np.full(len(values), np.nan, dtype=float)
    if lag:
        adjusted[lag:] = values[lag:] + values[lag:] - values[:-lag]
    return _ema(adjusted, period)


def _tsf(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    x_values = np.arange(period, dtype=float)
    sum_x, sum_x_square = float(np.sum(x_values)), float(np.sum(x_values * x_values))
    denominator = period * sum_x_square - sum_x * sum_x
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if period == 1 or abs(denominator) <= 1e-12:
            result[index] = window[-1]
        elif np.all(np.isfinite(window)):
            slope = (period * float(np.sum(window * x_values)) - sum_x * float(np.sum(window))) / denominator
            result[index] = float(np.mean(window)) - slope * sum_x / period + slope * period
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)
source = {"OPEN": opened, "HIGH": high, "LOW": low, "HL2": (high + low) / 2.0, "HLC3": (high + low + close) / 3.0, "OHLC4": (opened + high + low + close) / 4.0}.get(source_name, close)

if average_type == "SMA":
    support = _sma(source, length)
elif average_type == "EMA":
    support = _ema(source, length)
elif average_type == "WMA":
    support = _wma(source, length)
elif average_type == "TMA":
    support = _sma(_sma(source, int(math.ceil(length / 2.0))), int(math.floor(length / 2.0)) + 1)
elif average_type == "WWMA":
    support = _wwma(source, length)
elif average_type == "ZLEMA":
    support = _zlema(source, length)
elif average_type == "TSF":
    support = _tsf(source, length)
else:
    support = _var(source, length)

long_stop, short_stop, ott = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
direction = np.ones(n, dtype=int)
for index in range(n):
    if not np.isfinite(support[index]):
        direction[index] = direction[index - 1] if index else 1
        continue
    difference = support[index] * percent * 0.01
    raw_long, raw_short = support[index] - difference, support[index] + difference
    previous_long = long_stop[index - 1] if index and np.isfinite(long_stop[index - 1]) else raw_long
    previous_short = short_stop[index - 1] if index and np.isfinite(short_stop[index - 1]) else raw_short
    long_stop[index] = max(raw_long, previous_long) if support[index] > previous_long else raw_long
    short_stop[index] = min(raw_short, previous_short) if support[index] < previous_short else raw_short
    previous_direction = direction[index - 1] if index else 1
    direction[index] = 1 if previous_direction == -1 and support[index] > previous_short else -1 if previous_direction == 1 and support[index] < previous_long else previous_direction
    tracker = long_stop[index] if direction[index] == 1 else short_stop[index]
    ott[index] = tracker * (200.0 + percent) / 200.0 if support[index] > tracker else tracker * (200.0 - percent) / 200.0

delayed_ott = np.zeros(n, dtype=float)
delayed_three = np.zeros(n, dtype=float)
for index in range(n):
    if index >= 2 and np.isfinite(ott[index - 2]):
        delayed_ott[index] = ott[index - 2]
    if index >= 3 and np.isfinite(ott[index - 3]):
        delayed_three[index] = ott[index - 3]


def _cross_pair(first, second):
    up = np.zeros(n, dtype=bool)
    down = np.zeros(n, dtype=bool)
    for index in range(1, n):
        if np.all(np.isfinite([first[index], first[index - 1], second[index], second[index - 1]])):
            up[index] = first[index] > second[index] and first[index - 1] <= second[index - 1]
            down[index] = first[index] < second[index] and first[index - 1] >= second[index - 1]
    return up, down


support_up, support_down = _cross_pair(support, delayed_ott)
price_up, price_down = _cross_pair(source, delayed_ott)
color_up, color_down = _cross_pair(delayed_ott, delayed_three)
ott_colors = ["#B800D9" if not highlight_ott else "#008000" if delayed_ott[index] > delayed_three[index] else "#FF0000" for index in range(n)]
regime_fill = []
ohlc4 = (opened + high + low + close) / 4.0
for index in range(n):
    if highlighting and np.isfinite(support[index]):
        color = "rgba(0,128,0,0.25)" if support[index] > ott[index] else "rgba(255,0,0,0.25)" if support[index] < ott[index] else "rgba(0,0,0,0)"
        regime_fill.append({"upper": float(max(ohlc4[index], delayed_ott[index])), "lower": float(min(ohlc4[index], delayed_ott[index])), "color": color})
    else:
        regime_fill.append(None)


def _points(up, down, enabled, prefix):
    buy = [{"value": float(ott[index] * 0.995), "color": "#008000", "size": 5} if enabled and up[index] and np.isfinite(ott[index]) else None for index in range(n)]
    sell = [{"value": float(ott[index] * 1.005), "color": "#FF0000", "size": 5} if enabled and down[index] and np.isfinite(ott[index]) else None for index in range(n)]
    return [
        {"name": f"{prefix} Buy", "data": buy, "color": "#008000", "type": "circle", "size": 5, "overlay": True},
        {"name": f"{prefix} Sell", "data": sell, "color": "#FF0000", "type": "circle", "size": 5, "overlay": True},
    ]


plots = [
    {"name": "Support Line", "data": _finite(support) if show_support else [None] * n, "color": "#0585E1", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "OTT", "data": _finite(delayed_ott), "color": ott_colors, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "OTT Regime", "title": False, "data": regime_fill, "color": "#008000", "type": "cloud", "overlay": True},
]
plots += _points(support_up, support_down, show_support_signals, "Support")
plots += _points(price_up, price_down, show_price_signals, "Price")
plots += _points(color_up, color_down, show_color_signals, "Color")

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": [], "layers": [], "tables": [],
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "support": _finite(support), "longStop": _finite(long_stop), "shortStop": _finite(short_stop), "direction": direction.tolist(), "ott": _finite(ott),
        "delayedOtt": _finite(delayed_ott), "delayedOttThree": _finite(delayed_three), "supportCrossUp": support_up.tolist(), "supportCrossDown": support_down.tolist(),
        "priceCrossUp": price_up.tolist(), "priceCrossDown": price_down.tolist(), "colorCrossUp": color_up.tolist(), "colorCrossDown": color_down.tolist(),
        "averageType": average_type, "source": source_name,
    },
}
