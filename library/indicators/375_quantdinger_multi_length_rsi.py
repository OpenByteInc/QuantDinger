# ============================================================
# QuantDinger Multi-Length RSI
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Multi-Length RSI"
my_indicator_description = "Aggregates a continuous range of recursively smoothed RSI lengths and adapts channels to the share of extreme members."

# @param minimum_length int 10 Minimum RSI length range=1:500:1
# @param maximum_length int 20 Maximum RSI length range=1:500:1
# @param overbought float 70 Overbought threshold range=0:100:1
# @param oversold float 30 Oversold threshold range=0:100:1
# @param source str Close Price source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param average_down_color str #FF5D00 Lower gradient color
# @param average_up_color str #2157F3 Upper gradient color
# @param upper_color str #0CB51A Upper channel color
# @param lower_color str #FF1100 Lower channel color

minimum_length = min(500, max(1, int(params.get("minimum_length", 10))))
maximum_length = min(500, max(minimum_length, int(params.get("maximum_length", 20))))
overbought = min(100.0, max(0.0, float(params.get("overbought", 70.0))))
oversold = min(100.0, max(0.0, float(params.get("oversold", 30.0))))
source_name = str(params.get("source", "Close"))
source_name = source_name if source_name in ("Close", "Open", "High", "Low", "HL2", "HLC3", "OHLC4") else "Close"
average_down_color = str(params.get("average_down_color", "#FF5D00"))
average_up_color = str(params.get("average_up_color", "#2157F3"))
upper_color = str(params.get("upper_color", "#0CB51A"))
lower_color = str(params.get("lower_color", "#FF1100"))


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

source_map = {
    "Close": close,
    "Open": opened,
    "High": high,
    "Low": low,
    "HL2": (high + low) / 2.0,
    "HLC3": (high + low + close) / 3.0,
    "OHLC4": (opened + high + low + close) / 4.0,
}
source = source_map[source_name]
change = np.diff(source, prepend=np.nan)
change[~np.isfinite(change)] = 0.0
lengths = np.arange(minimum_length, maximum_length + 1, dtype=float)
member_count = len(lengths)
alpha = 1.0 / lengths
numerator_state = np.zeros(member_count, dtype=float)
denominator_state = np.zeros(member_count, dtype=float)
average_rsi = np.full(n, np.nan, dtype=float)
minimum_rsi = np.full(n, np.nan, dtype=float)
maximum_rsi = np.full(n, np.nan, dtype=float)
overbought_members = np.zeros(n, dtype=int)
oversold_members = np.zeros(n, dtype=int)
upper_channel = np.full(n, np.nan, dtype=float)
lower_channel = np.full(n, np.nan, dtype=float)
previous_upper = np.nan
previous_lower = np.nan

for index, difference in enumerate(change):
    numerator_state = alpha * difference + (1.0 - alpha) * numerator_state
    denominator_state = alpha * abs(difference) + (1.0 - alpha) * denominator_state
    members = np.divide(50.0 * numerator_state, denominator_state, out=np.full(member_count, np.nan, dtype=float), where=denominator_state != 0.0) + 50.0
    if np.all(np.isfinite(members)):
        average_rsi[index] = float(np.sum(members) / member_count)
        minimum_rsi[index] = float(members[0])
        maximum_rsi[index] = float(members[-1])
    overbought_members[index] = int(np.count_nonzero(members > overbought))
    oversold_members[index] = int(np.count_nonzero(members < oversold))
    if np.isfinite(average_rsi[index]):
        upper_weight = overbought_members[index] / float(member_count)
        lower_weight = oversold_members[index] / float(member_count)
        previous_upper = average_rsi[index] if not np.isfinite(previous_upper) else previous_upper + upper_weight * (average_rsi[index] - previous_upper)
        previous_lower = average_rsi[index] if not np.isfinite(previous_lower) else previous_lower + lower_weight * (average_rsi[index] - previous_lower)
        upper_channel[index] = previous_upper
        lower_channel[index] = previous_lower

overbought_percent = overbought_members.astype(float) / member_count * 100.0
oversold_percent = oversold_members.astype(float) / member_count * 100.0
oversold_ceiling = 100.0 - oversold_percent
channel_cloud = [
    {"upper": float(max(upper_channel[index], lower_channel[index])), "lower": float(min(upper_channel[index], lower_channel[index])), "color": average_up_color if average_rsi[index] >= (upper_channel[index] + lower_channel[index]) / 2.0 else average_down_color, "opacity": 0.10}
    if np.all(np.isfinite([upper_channel[index], lower_channel[index], average_rsi[index]])) else None for index in range(n)
]
overbought_cloud = [{"upper": float(overbought_percent[index]), "lower": 0.0, "color": upper_color, "opacity": 0.30} for index in range(n)]
oversold_cloud = [{"upper": 100.0, "lower": float(oversold_ceiling[index]), "color": lower_color, "opacity": 0.30} for index in range(n)]

last_index = n - 1
tables = []
if n:
    tables = [{
        "title": "Multi-Length RSI", "position": "top-right", "compact": True,
        "columns": [{"text": "Overbought"}, {"text": "Oversold"}],
        "rows": [[{"text": f"{overbought_percent[last_index]:.2f}%", "color": "#26A69A"}, {"text": f"{oversold_percent[last_index]:.2f}%", "color": "#EF5350"}]],
    }]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Overbought Participation", "title": False, "data": overbought_cloud, "color": upper_color, "type": "cloud", "overlay": False},
        {"name": "Oversold Participation", "title": False, "data": oversold_cloud, "color": lower_color, "type": "cloud", "overlay": False},
        {"name": "Adaptive Channel", "title": False, "data": channel_cloud, "color": average_up_color, "type": "cloud", "overlay": False},
        {"name": "Center", "title": True, "data": [50.0] * n, "color": "rgba(128,128,128,0.50)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Average Multi-Length RSI", "title": True, "data": _finite(average_rsi), "color": average_up_color, "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Upper Channel", "title": True, "data": _finite(upper_channel), "color": upper_color, "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Lower Channel", "title": True, "data": _finite(lower_channel), "color": lower_color, "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Overbought Percentage", "title": True, "data": _finite(overbought_percent), "color": "rgba(12,181,26,0.25)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Oversold Ceiling", "title": True, "data": _finite(oversold_ceiling), "color": "rgba(255,17,0,0.25)", "type": "line", "lineWidth": 1, "overlay": False},
    ],
    "signals": [],
    "layers": [],
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "source": _finite(source), "change": _finite(change), "minimumMemberRsi": _finite(minimum_rsi), "maximumMemberRsi": _finite(maximum_rsi),
        "averageRsi": _finite(average_rsi), "overboughtMembers": overbought_members.tolist(), "oversoldMembers": oversold_members.tolist(),
        "overboughtPercent": _finite(overbought_percent), "oversoldPercent": _finite(oversold_percent), "oversoldCeiling": _finite(oversold_ceiling),
        "upperChannel": _finite(upper_channel), "lowerChannel": _finite(lower_channel),
        "lengths": [int(value) for value in lengths], "finalNumerators": _finite(numerator_state), "finalDenominators": _finite(denominator_state),
    },
}
