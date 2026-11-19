# ============================================================
# QuantDinger Extreme Candle Reversal
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Extreme Candle Reversal"
my_indicator_description = "Arms on closes beyond every prior high or low in a lookback and confirms a reversal through that candle within a configurable window."

# @param candle_lookback int 20 Candle lookback range=1:10000:1
# @param confirm_within int 3 Confirmation window range=0:10000:1
# @param non_repainting bool false Delay confirmed signals by one bar

candle_lookback = min(10000, max(1, int(params.get("candle_lookback", 20))))
confirm_within = min(10000, max(0, int(params.get("confirm_within", 3))))
non_repainting = bool(params.get("non_repainting", False))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

bull_candidate = np.zeros(n, dtype=bool)
bear_candidate = np.zeros(n, dtype=bool)
bull_active = np.zeros(n, dtype=bool)
bear_active = np.zeros(n, dtype=bool)
bull_low = np.zeros(n, dtype=float)
bull_high = np.zeros(n, dtype=float)
bear_low = np.zeros(n, dtype=float)
bear_high = np.zeros(n, dtype=float)
bull_signal_state = np.zeros(n, dtype=bool)
bear_signal_state = np.zeros(n, dtype=bool)
bull_count = np.zeros(n, dtype=int)
bear_count = np.zeros(n, dtype=int)
bull_signal = np.zeros(n, dtype=bool)
bear_signal = np.zeros(n, dtype=bool)

for index in range(n):
    prior_bull_active = bull_active[index - 1] if index else False
    prior_bear_active = bear_active[index - 1] if index else False
    prior_bull_low = bull_low[index - 1] if index else 0.0
    prior_bull_high = bull_high[index - 1] if index else 0.0
    prior_bear_low = bear_low[index - 1] if index else 0.0
    prior_bear_high = bear_high[index - 1] if index else 0.0
    prior_bull_signal = bull_signal_state[index - 1] if index else False
    prior_bear_signal = bear_signal_state[index - 1] if index else False
    prior_bull_count = bull_count[index - 1] if index else 0
    prior_bear_count = bear_count[index - 1] if index else 0

    bull_active[index] = prior_bull_active
    bear_active[index] = prior_bear_active
    bull_low[index], bull_high[index] = prior_bull_low, prior_bull_high
    bear_low[index], bear_high[index] = prior_bear_low, prior_bear_high
    bull_signal_state[index], bear_signal_state[index] = prior_bull_signal, prior_bear_signal
    bull_count[index], bear_count[index] = prior_bull_count, prior_bear_count

    if index >= candle_lookback - 1:
        prior_start = index - candle_lookback + 1
        prior_lows = low[prior_start:index]
        prior_highs = high[prior_start:index]
        bull_candidate[index] = len(prior_lows) == candle_lookback - 1 and bool(np.all(close[index] < prior_lows))
        bear_candidate[index] = len(prior_highs) == candle_lookback - 1 and bool(np.all(close[index] > prior_highs))
    if bull_candidate[index]:
        bull_active[index] = True
        bull_low[index], bull_high[index] = low[index], high[index]
        bull_signal_state[index] = False
        bull_count[index] = 0
    if bull_active[index]:
        bull_count[index] += 1
    if bear_candidate[index]:
        bear_active[index] = True
        bear_low[index], bear_high[index] = low[index], high[index]
        bear_signal_state[index] = False
        bear_count[index] = 0
    if bear_active[index]:
        bear_count[index] += 1
    if close[index] < bull_low[index]:
        bull_active[index] = False
    if close[index] > bear_high[index]:
        bear_active[index] = False

    if non_repainting:
        if index > 0 and bull_active[index - 1] and close[index - 1] > bull_high[index - 1] and not bull_signal_state[index - 1] and bull_count[index - 1] <= confirm_within + 1:
            bull_signal_state[index] = True
            bull_signal[index] = True
        if index > 0 and bear_active[index - 1] and close[index - 1] < bear_low[index - 1] and not bear_signal_state[index - 1] and bear_count[index - 1] <= confirm_within + 1:
            bear_signal_state[index] = True
            bear_signal[index] = True
    else:
        if bull_active[index] and close[index] > bull_high[index] and not bull_signal_state[index] and bull_count[index] <= confirm_within + 1:
            bull_signal_state[index] = True
            bull_signal[index] = True
        if bear_active[index] and close[index] < bear_low[index] and not bear_signal_state[index] and bear_count[index] <= confirm_within + 1:
            bear_signal_state[index] = True
            bear_signal[index] = True


def _events(mask, prices, text, color, event_type):
    return {"type": event_type, "name": text, "data": [{"active": True, "price": float(prices[index]), "text": text, "color": color} if active else None for index, active in enumerate(mask)], "color": color, "renderMode": "events"}


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [{"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True}],
    "signals": [_events(bull_signal, low, "▲", "#00FF00", "buy"), _events(bear_signal, high, "▼", "#F23645", "sell")],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "bullCandidate": bull_candidate.tolist(), "bearCandidate": bear_candidate.tolist(), "bullActive": bull_active.tolist(), "bearActive": bear_active.tolist(),
        "bullLow": _finite(bull_low), "bullHigh": _finite(bull_high), "bearLow": _finite(bear_low), "bearHigh": _finite(bear_high),
        "bullSignalState": bull_signal_state.tolist(), "bearSignalState": bear_signal_state.tolist(), "bullCount": [int(value) for value in bull_count], "bearCount": [int(value) for value in bear_count],
        "bullSignal": bull_signal.tolist(), "bearSignal": bear_signal.tolist(),
    },
}
