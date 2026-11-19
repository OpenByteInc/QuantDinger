"""Continuously re-entered long position with a ratcheting percentage stop."""

# @param initial_stop_pct float 3 range=0.1:30:0.1
# @param trail_step_pct float 1 range=0.1:10:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.trailing_long_state = {"stop": None, "anchor": None}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(50)
    context.set_metadata(direction_mode="long_only", strategy_family="risk_managed")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    initial_stop_pct = float(context.params.get("initial_stop_pct", 3.0))
    trail_step_pct = float(context.params.get("trail_step_pct", 1.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    bars = get_history(1, "1h", "close", g.symbol)
    if len(bars) < 1:
        return
    close = float(bars["close"].iloc[-1])
    state = dict(g.trailing_long_state or {"stop": None, "anchor": None})
    amount = float(get_position(g.symbol).amount or 0.0)
    if amount <= 0:
        state["anchor"] = close
        state["stop"] = close * (1.0 - initial_stop_pct / 100.0)
        g.trailing_long_state = state
        order_target_percent(g.symbol, target_pct, reason="continuous_trailing_entry")
        return
    if state.get("anchor") is None or state.get("stop") is None:
        state["anchor"] = close
        state["stop"] = close * (1.0 - initial_stop_pct / 100.0)
    if close >= float(state["anchor"]) * (1.0 + trail_step_pct / 100.0):
        state["anchor"] = close
        state["stop"] = max(float(state["stop"]), close * (1.0 - trail_step_pct / 100.0))
    if close <= float(state["stop"]):
        state["anchor"] = None
        state["stop"] = None
        order_target_percent(g.symbol, 0.0, reason="continuous_trailing_stop")
    g.trailing_long_state = state
