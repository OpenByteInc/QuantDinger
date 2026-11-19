# ============================================================
# QuantDinger Sentiment Balance
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Sentiment Balance"
my_indicator_description = (
    "A smoothed bull-versus-bear sentiment oscillator derived from intrabar "
    "pressure, rolling group pressure, or their average."
)

# @param period int 10 Sentiment period range=2:300:1
# @param mode int 0 Calculation mode values=0,1,2

period = max(2, int(params.get("period", 10)))
mode = int(params.get("mode", 0))
if mode not in (0, 1, 2):
    mode = 0

df = df.copy()
open_price = df["open"].astype(float)
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)

intra_range = (high - low).replace(0.0, np.nan)
group_low = low.rolling(window=period, min_periods=period).min()
group_high = high.rolling(window=period, min_periods=period).max()
group_open = open_price.shift(period - 1)
group_range = (group_high - group_low).replace(0.0, np.nan)

intra_bulls = 50.0 * ((close - low) + (high - open_price)) / intra_range
intra_bears = 50.0 * ((high - close) + (open_price - low)) / intra_range
group_bulls = 50.0 * (
    (close - group_low) + (group_high - group_open)
) / group_range
group_bears = 50.0 * (
    (group_high - close) + (group_open - group_low)
) / group_range

if mode == 1:
    bull_source = intra_bulls
    bear_source = intra_bears
elif mode == 2:
    bull_source = group_bulls
    bear_source = group_bears
else:
    bull_source = (intra_bulls + group_bulls) / 2.0
    bear_source = (intra_bears + group_bears) / 2.0

bulls = bull_source.rolling(window=period, min_periods=period).mean()
bears = bear_source.rolling(window=period, min_periods=period).mean()
bull_plot = [float(value) if np.isfinite(value) else None for value in bulls]
bear_plot = [float(value) if np.isfinite(value) else None for value in bears]

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Bull Sentiment",
            "data": bull_plot,
            "color": "#2196F3",
            "type": "line",
            "lineWidth": 2,
            "overlay": False,
        },
        {
            "name": "Bear Sentiment",
            "data": bear_plot,
            "color": "#F23645",
            "type": "line",
            "lineWidth": 2,
            "overlay": False,
        },
    ],
    "signals": [],
    "layers": [],
}
