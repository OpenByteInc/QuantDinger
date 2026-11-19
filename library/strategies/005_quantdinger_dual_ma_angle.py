"""
QuantDinger Dual MA Angle
One-way reversal strategy combining a recent moving-average cross with trend angle.
"""

# @param angle_threshold float 7 range=1:13:1
# @param angle_lookback int 2 range=1:20:1
# @param atr_period int 10 range=2:100:1
# @param cross_lookback int 15 range=1:50:1
# @param target_pct float 0.95 range=0.05:1:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.pending_target = 0.0
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(405)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    angle_threshold = float(context.params.get("angle_threshold", 7))
    angle_lookback = int(context.params.get("angle_lookback", 2))
    atr_period = int(context.params.get("atr_period", 10))
    cross_lookback = int(context.params.get("cross_lookback", 15))
    target_pct = float(context.params.get("target_pct", 0.95))
    if angle_lookback < 1 or atr_period < 2 or cross_lookback < 1:
        return

    required = max(4 * atr_period, 21 + angle_lookback + cross_lookback + 3)
    bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < required:
        return
    high = bars["high"]
    low = bars["low"]
    close = bars["close"]
    fast = close.rolling(8, min_periods=8).mean()
    slow = close.rolling(21, min_periods=21).mean()
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(alpha=1.0 / atr_period, adjust=False, min_periods=atr_period).mean()
    atr_now = float(atr.iloc[-1])
    if not atr_now == atr_now or atr_now <= 0:
        return
    slope_ratio = (float(slow.iloc[-1]) - float(slow.iloc[-1 - angle_lookback])) / atr_now / angle_lookback
    threshold_radians = angle_threshold * 3.141592653589793 / 180.0
    threshold_squared = threshold_radians * threshold_radians
    tan_threshold = threshold_radians * (
        1.0
        + threshold_squared / 3.0
        + 2.0 * threshold_squared * threshold_squared / 15.0
        + 17.0 * threshold_squared * threshold_squared * threshold_squared / 315.0
    )
    cross_up = (fast.shift(1) <= slow.shift(1)) & (fast > slow)
    cross_down = (fast.shift(1) >= slow.shift(1)) & (fast < slow)
    recent_up = bool(cross_up.iloc[-cross_lookback - 1:-1].any())
    recent_down = bool(cross_down.iloc[-cross_lookback - 1:-1].any())
    target = target_pct if recent_up and slope_ratio > tan_threshold else (-target_pct if recent_down and slope_ratio < -tan_threshold else 0.0)
    if target == 0.0:
        return

    amount = float(get_position(g.symbol).amount or 0.0)
    if abs(amount) <= 1e-12 and abs(float(g.pending_target)) > 0:
        target = float(g.pending_target)
        g.pending_target = 0.0
        order_target_percent(g.symbol, target, reason="dual_ma_angle_pending_reversal")
    elif amount * target < 0:
        g.pending_target = target
        order_target_percent(g.symbol, 0.0, reason="dual_ma_angle_close_for_reversal")
    elif amount == 0.0:
        order_target_percent(g.symbol, target, reason="dual_ma_angle_entry")
