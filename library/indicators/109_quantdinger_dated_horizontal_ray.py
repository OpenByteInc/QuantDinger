# ============================================================
# QuantDinger Dated Horizontal Ray
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Dated Horizontal Ray"
my_indicator_description = (
    "Locks a High, Low, Close, or Open level on a selected calendar day and "
    "extends a state-colored horizontal ray through the latest bar."
)

# @param day int 15 Date day range=1:31:1
# @param month int 9 Date month range=1:12:1
# @param year int 2026 Date year range=1990:2100:1
# @param price_source str High Price source values=High,Low,Close,Open
# @param above_color color #2FB36D Close-above color
# @param below_color color #D84A4A Close-below color
# @param line_width int 2 Ray thickness range=1:5:1
# @param line_style str Solid Ray style values=Solid,Dashed,Dotted
# @param show_text bool true Show ray label
# @param ray_text str Auto Label text or Auto for date
# @param show_price bool true Include price in label
# @param text_offset int 15 Label offset range=0:400:1

day = min(31, max(1, int(params.get("day", 15))))
month = min(12, max(1, int(params.get("month", 9))))
year = min(2100, max(1990, int(params.get("year", 2026))))
price_source = str(params.get("price_source", "High"))
above_color = str(params.get("above_color", "#2FB36D"))
below_color = str(params.get("below_color", "#D84A4A"))
line_width = min(5, max(1, int(params.get("line_width", 2))))
line_style = str(params.get("line_style", "Solid"))
show_text = bool(params.get("show_text", True))
ray_text = str(params.get("ray_text", "Auto"))
show_price = bool(params.get("show_price", True))
text_offset = min(400, max(0, int(params.get("text_offset", 15))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
times = df["time"].to_numpy(dtype=float)
n = len(df)

if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0

target_time = float(pd.Timestamp(year=year, month=month, day=day, tz="UTC").value / 1000000)
timestamps = pd.to_datetime(times, unit="ms", utc=True)
lock_index = -1
coverage_missing = bool(n and np.isfinite(times[0]) and target_time < times[0])
if not coverage_missing:
    for index in range(n):
        if np.isfinite(times[index]) and times[index] >= target_time:
            lock_index = index
            break

level = np.nan
level_bar = -1
lock_date = None
if lock_index >= 0:
    lock_date = timestamps[lock_index]
    same_day = []
    for index in range(lock_index, n):
        current = timestamps[index]
        if current.year == lock_date.year and current.month == lock_date.month and current.day == lock_date.day:
            same_day.append(index)
        else:
            break
    if price_source == "High":
        level_bar = int(same_day[int(np.argmax(high[same_day]))])
        level = float(high[level_bar])
    elif price_source == "Low":
        level_bar = int(same_day[int(np.argmin(low[same_day]))])
        level = float(low[level_bar])
    elif price_source == "Close":
        level_bar = int(same_day[-1])
        level = float(close[level_bar])
    else:
        level_bar = lock_index
        level = float(open_price[lock_index])

is_above = bool(np.isfinite(level) and n and close[-1] > level)
ray_color = above_color if is_above else below_color
style_map = {"Solid": "solid", "Dashed": "dashed", "Dotted": "dotted"}
ray_style = style_map.get(line_style, "solid")

ray_data = [None for _ in range(n)]
if level_bar >= 0 and np.isfinite(level):
    for index in range(level_bar, n):
        ray_data[index] = {"value": level, "color": ray_color}

layers = []
label_text = ""
if show_text and level_bar >= 0 and np.isfinite(level) and n:
    base_text = lock_date.strftime("%d %b %Y") if ray_text == "Auto" or not ray_text.strip() else ray_text
    label_text = f"{base_text} · {level:.2f}" if show_price else base_text
    layers.append({
        "type": "label", "index": n - 1, "price": float(level), "text": label_text,
        "color": "rgba(0,0,0,0)", "borderColor": "rgba(0,0,0,0)",
        "textColor": ray_color, "side": "right", "anchor": "right", "fontSize": 10,
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Dated Ray", "data": ray_data, "color": ray_color, "type": "line", "lineWidth": line_width, "lineStyle": ray_style, "overlay": True, "connectNulls": False},
        {"name": "Price Guide", "title": False, "data": [float(value) if np.isfinite(value) else None for value in close], "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [],
    "layers": layers,
    "tables": [{
        "title": "Dated Ray", "position": "top-right", "compact": True,
        "rows": [["Status", "Target date is outside loaded history"]],
    }] if coverage_missing else [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "lockIndex": int(lock_index), "levelBar": int(level_bar),
        "coverageMissing": bool(coverage_missing),
        "level": float(level) if np.isfinite(level) else None,
        "isAbove": bool(is_above), "distancePercent": float((close[-1] - level) / level * 100.0) if n and np.isfinite(level) and level != 0.0 else None,
        "labelText": label_text, "textOffset": int(text_offset),
    },
}
