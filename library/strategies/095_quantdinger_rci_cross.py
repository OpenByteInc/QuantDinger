"""Rank Correlation Index crossing its smoothing average."""

# @param rci_length int 10 range=2:100:1
# @param ma_length int 14 range=1:200:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _rank_correlation(values):
    length = len(values)
    if length < 2:
        return 0.0
    order = sorted(range(length), key=lambda index: values[index])
    ranks = [0.0] * length
    cursor = 0
    while cursor < length:
        end = cursor
        while end + 1 < length and values[order[end + 1]] == values[order[cursor]]:
            end += 1
        rank = (cursor + end + 2) / 2.0
        for position in range(cursor, end + 1):
            ranks[order[position]] = rank
        cursor = end + 1
    squared = sum((float(index + 1) - ranks[index]) ** 2 for index in range(length))
    return 100.0 * (1.0 - 6.0 * squared / float(length * (length * length - 1)))


def _new_state(signature):
    return {"signature": signature, "closes": [], "rci": [], "previous_rci": None, "previous_ma": None}


def _step(state, close, rci_length, ma_length):
    state["closes"].append(close)
    state["closes"] = state["closes"][-rci_length:]
    if len(state["closes"]) < rci_length:
        return False, False
    rci = _rank_correlation(state["closes"])
    state["rci"].append(rci)
    state["rci"] = state["rci"][-ma_length:]
    if len(state["rci"]) < ma_length:
        return False, False
    average = sum(state["rci"]) / float(ma_length)
    previous_rci = state["previous_rci"]
    previous_ma = state["previous_ma"]
    buy = previous_rci is not None and previous_ma is not None and float(previous_rci) <= float(previous_ma) and rci > average
    sell = previous_rci is not None and previous_ma is not None and float(previous_rci) >= float(previous_ma) and rci < average
    state["previous_rci"] = rci
    state["previous_ma"] = average
    return buy, sell


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.rci_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="momentum")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    rci_length = int(context.params.get("rci_length", 10))
    ma_length = int(context.params.get("ma_length", 14))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [rci_length, ma_length]
    state = dict(g.rci_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(320, (rci_length + ma_length) * 12)
    bars = get_history(required if cold else 1, "1h", "close", g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    buy = sell = False
    for value in bars["close"]:
        buy, sell = _step(state, float(value), rci_length, ma_length)
    g.rci_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="rci_long")
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="rci_short")
