# ============================================================
# QuantDinger Momentum and Exhaustion Reversals
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Momentum and Exhaustion Reversals"
my_indicator_description = "Tracks nine-step momentum phases, thirteen-step exhaustion phases, persistent risk levels, and price-flip reversal setups."

# @param momentum_display str Completed Momentum phase labels values=Completed,Detailed,None
# @param show_support_resistance bool true Show momentum support and resistance
# @param momentum_risk_levels bool false Show momentum risk levels
# @param exhaustion_display str Completed Exhaustion phase labels values=Completed,Detailed,None
# @param exhaustion_risk_levels bool false Show exhaustion risk levels
# @param exhaustion_target_levels bool false Show exhaustion target levels
# @param trade_setup str None Phase used for price-flip setups values=Momentum,Exhaustion,Qualified,None
# @param show_trade_warnings bool false Show warnings after active setups

momentum_display = str(params.get("momentum_display", "Completed"))
momentum_display = momentum_display if momentum_display in ("Completed", "Detailed", "None") else "Completed"
show_support_resistance = bool(params.get("show_support_resistance", True))
momentum_risk_levels = bool(params.get("momentum_risk_levels", False))
exhaustion_display = str(params.get("exhaustion_display", "Completed"))
exhaustion_display = exhaustion_display if exhaustion_display in ("Completed", "Detailed", "None") else "Completed"
exhaustion_risk_levels = bool(params.get("exhaustion_risk_levels", False))
exhaustion_target_levels = bool(params.get("exhaustion_target_levels", False))
trade_setup = str(params.get("trade_setup", "None"))
trade_setup = trade_setup if trade_setup in ("Momentum", "Exhaustion", "Qualified", "None") else "None"
show_trade_warnings = bool(params.get("show_trade_warnings", False))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _events(mask, prices, text, color, event_type):
    return {
        "type": event_type,
        "name": text,
        "data": [
            {"active": True, "price": float(prices[index]), "text": text, "color": color}
            if active and np.isfinite(prices[index]) else None
            for index, active in enumerate(mask)
        ],
        "color": color,
        "renderMode": "events",
    }


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

bull_momentum = np.zeros(n, dtype=int)
bear_momentum = np.zeros(n, dtype=int)
perfect_bull = np.zeros(n, dtype=bool)
perfect_bear = np.zeros(n, dtype=bool)
early_bull = np.zeros(n, dtype=bool)
early_bear = np.zeros(n, dtype=bool)
resistance = np.zeros(n, dtype=float)
support = np.zeros(n, dtype=float)
bull_momentum_risk = np.zeros(n, dtype=float)
bear_momentum_risk = np.zeros(n, dtype=float)

bull_exhaustion = np.zeros(n, dtype=int)
bear_exhaustion = np.zeros(n, dtype=int)
bull_exhaustion_active = np.zeros(n, dtype=bool)
bear_exhaustion_active = np.zeros(n, dtype=bool)
bull_exhaustion_risk = np.zeros(n, dtype=float)
bear_exhaustion_risk = np.zeros(n, dtype=float)
bull_exhaustion_target = np.zeros(n, dtype=float)
bear_exhaustion_target = np.zeros(n, dtype=float)

long_setup = np.zeros(n, dtype=bool)
short_setup = np.zeros(n, dtype=bool)
long_warning = np.zeros(n, dtype=bool)
short_warning = np.zeros(n, dtype=bool)

bull_phase_low = np.nan
bull_phase_high = np.nan
bear_phase_high = np.nan
bear_phase_low = np.nan
bull_count8_price = np.nan
bear_count8_price = np.nan
bull_exhaustion_highest = np.nan
bull_exhaustion_lowest = np.nan
bull_exhaustion_high_at_low = np.nan
bull_exhaustion_low_at_high = np.nan
bear_exhaustion_highest = np.nan
bear_exhaustion_lowest = np.nan
bear_exhaustion_high_at_low = np.nan
bear_exhaustion_low_at_high = np.nan
start_bull_exhaustion = False
start_bear_exhaustion = False
pending_long = False
pending_short = False
previous_long_confirmation = False
previous_short_confirmation = False
long_active = False
short_active = False
bull9_indices = []
bear9_indices = []
bull13_indices = []
bear13_indices = []

for index in range(n):
    previous_bull = bull_momentum[index - 1] if index else 0
    previous_bear = bear_momentum[index - 1] if index else 0
    down_four = index >= 4 and close[index] < close[index - 4]
    if down_four:
        bull_momentum[index] = 1 if previous_bull == 9 else previous_bull + 1
        bear_momentum[index] = 0
    else:
        bear_momentum[index] = 1 if previous_bear == 9 else previous_bear + 1
        bull_momentum[index] = 0

    perfect_bull[index] = index >= 3 and (
        (low[index] <= low[index - 3] and low[index] <= low[index - 2])
        or (low[index - 1] <= low[index - 3] and low[index - 1] <= low[index - 2])
    )
    perfect_bear[index] = index >= 3 and (
        (high[index] >= high[index - 3] and high[index] >= high[index - 2])
        or (high[index - 1] >= high[index - 3] and high[index - 1] >= high[index - 2])
    )
    early_bull[index] = previous_bull == 8 and bear_momentum[index] == 1
    early_bear[index] = previous_bear == 8 and bull_momentum[index] == 1

    rolling_high = float(np.max(high[max(0, index - 8):index + 1]))
    rolling_low = float(np.min(low[max(0, index - 8):index + 1]))
    prior_resistance = resistance[index - 1] if index else 0.0
    prior_support = support[index - 1] if index else 0.0
    resistance[index] = rolling_high if bull_momentum[index] == 9 or early_bull[index] else (0.0 if prior_resistance > 0.0 and close[index] > prior_resistance else prior_resistance)
    support[index] = rolling_low if bear_momentum[index] == 9 or early_bear[index] else (0.0 if prior_support > 0.0 and close[index] < prior_support else prior_support)

    if bull_momentum[index] == 1:
        bull_phase_low, bull_phase_high = low[index], high[index]
    elif bull_momentum[index] > 0:
        if not np.isfinite(bull_phase_low) or low[index] <= bull_phase_low:
            bull_phase_low, bull_phase_high = low[index], high[index]
    if bear_momentum[index] == 1:
        bear_phase_high, bear_phase_low = high[index], low[index]
    elif bear_momentum[index] > 0:
        if not np.isfinite(bear_phase_high) or high[index] >= bear_phase_high:
            bear_phase_high, bear_phase_low = high[index], low[index]

    prior_bull_risk = bull_momentum_risk[index - 1] if index else 0.0
    prior_bear_risk = bear_momentum_risk[index - 1] if index else 0.0
    if bull_momentum[index] == 9 and np.all(np.isfinite([bull_phase_low, bull_phase_high])):
        bull_momentum_risk[index] = 2.0 * bull_phase_low - bull_phase_high
    elif (prior_bull_risk > 0.0 and close[index] < prior_bull_risk) or bear_momentum[index] == 9:
        bull_momentum_risk[index] = 0.0
    else:
        bull_momentum_risk[index] = prior_bull_risk
    if bear_momentum[index] == 9 and np.all(np.isfinite([bear_phase_high, bear_phase_low])):
        bear_momentum_risk[index] = 2.0 * bear_phase_high - bear_phase_low
    elif (prior_bear_risk > 0.0 and close[index] > prior_bear_risk) or bull_momentum[index] == 9:
        bear_momentum_risk[index] = 0.0
    else:
        bear_momentum_risk[index] = prior_bear_risk

    previous_bull_exhaustion = bull_exhaustion[index - 1] if index else 0
    previous_bear_exhaustion = bear_exhaustion[index - 1] if index else 0
    bull_count_condition = index >= 2 and close[index] <= low[index - 2]
    bull_qualification_block = bull_count_condition and np.isfinite(bull_count8_price) and low[index] >= bull_count8_price
    if bull_momentum[index] == 9 and previous_bull_exhaustion == 0 and (perfect_bull[index] or (index and perfect_bull[index - 1])):
        start_bull_exhaustion = True
    elif bear_momentum[index] == 9 or previous_bull_exhaustion == 13 or (resistance[index] > 0.0 and close[index] > resistance[index]):
        start_bull_exhaustion = False
    if start_bull_exhaustion:
        if bull_momentum[index] == 9:
            next_bull_exhaustion = 1 if bull_count_condition else 0
        else:
            next_bull_exhaustion = previous_bull_exhaustion + 1 if bull_count_condition else previous_bull_exhaustion
    else:
        next_bull_exhaustion = 0
    if next_bull_exhaustion == 13 and bull_qualification_block:
        next_bull_exhaustion = 12
    bull_exhaustion[index] = next_bull_exhaustion
    bull_exhaustion_active[index] = start_bull_exhaustion
    if next_bull_exhaustion == 8 and next_bull_exhaustion != previous_bull_exhaustion:
        bull_count8_price = close[index]
    if next_bull_exhaustion == 1:
        bull_exhaustion_lowest = low[index]
        bull_exhaustion_highest = high[index]
        bull_exhaustion_high_at_low = high[index]
        bull_exhaustion_low_at_high = low[index]
    if start_bull_exhaustion and next_bull_exhaustion > 0:
        if high[index] >= bull_exhaustion_highest:
            bull_exhaustion_highest, bull_exhaustion_low_at_high = high[index], low[index]
        if low[index] <= bull_exhaustion_lowest:
            bull_exhaustion_lowest, bull_exhaustion_high_at_low = low[index], high[index]

    prior_bull_target = bull_exhaustion_target[index - 1] if index else 0.0
    prior_bull_exhaustion_risk = bull_exhaustion_risk[index - 1] if index else 0.0
    if next_bull_exhaustion == 13 and np.all(np.isfinite([bull_exhaustion_highest, bull_exhaustion_low_at_high])):
        bull_exhaustion_target[index] = 2.0 * bull_exhaustion_highest - bull_exhaustion_low_at_high
    elif (prior_bull_target > 0.0 and close[index] > prior_bull_target) or (prior_bull_exhaustion_risk == 0.0 and previous_bear_exhaustion == 13):
        bull_exhaustion_target[index] = 0.0
    else:
        bull_exhaustion_target[index] = prior_bull_target
    if next_bull_exhaustion == 13 and np.all(np.isfinite([bull_exhaustion_lowest, bull_exhaustion_high_at_low])):
        bull_exhaustion_risk[index] = 2.0 * bull_exhaustion_lowest - bull_exhaustion_high_at_low
    elif (prior_bull_exhaustion_risk > 0.0 and close[index] < prior_bull_exhaustion_risk) or (bull_exhaustion_target[index] == 0.0 and previous_bear_exhaustion == 13):
        bull_exhaustion_risk[index] = 0.0
    else:
        bull_exhaustion_risk[index] = prior_bull_exhaustion_risk

    bear_count_condition = index >= 2 and close[index] >= high[index - 2]
    bear_qualification_block = bear_count_condition and np.isfinite(bear_count8_price) and high[index] <= bear_count8_price
    if bear_momentum[index] == 9 and previous_bear_exhaustion == 0 and (perfect_bear[index] or (index and perfect_bear[index - 1])):
        start_bear_exhaustion = True
    elif bull_momentum[index] == 9 or previous_bear_exhaustion == 13 or (support[index] > 0.0 and close[index] < support[index]):
        start_bear_exhaustion = False
    if start_bear_exhaustion:
        if bear_momentum[index] == 9:
            next_bear_exhaustion = 1 if bear_count_condition else 0
        else:
            next_bear_exhaustion = previous_bear_exhaustion + 1 if bear_count_condition else previous_bear_exhaustion
    else:
        next_bear_exhaustion = 0
    if next_bear_exhaustion == 13 and bear_qualification_block:
        next_bear_exhaustion = 12
    bear_exhaustion[index] = next_bear_exhaustion
    bear_exhaustion_active[index] = start_bear_exhaustion
    if next_bear_exhaustion == 8 and next_bear_exhaustion != previous_bear_exhaustion:
        bear_count8_price = close[index]
    if next_bear_exhaustion == 1:
        bear_exhaustion_lowest = low[index]
        bear_exhaustion_highest = high[index]
        bear_exhaustion_high_at_low = high[index]
        bear_exhaustion_low_at_high = low[index]
    if start_bear_exhaustion and next_bear_exhaustion > 0:
        if high[index] >= bear_exhaustion_highest:
            bear_exhaustion_highest, bear_exhaustion_low_at_high = high[index], low[index]
        if low[index] <= bear_exhaustion_lowest:
            bear_exhaustion_lowest, bear_exhaustion_high_at_low = low[index], high[index]

    prior_bear_exhaustion_risk = bear_exhaustion_risk[index - 1] if index else 0.0
    prior_bear_target = bear_exhaustion_target[index - 1] if index else 0.0
    if next_bear_exhaustion == 13 and np.all(np.isfinite([bear_exhaustion_highest, bear_exhaustion_low_at_high])):
        bear_exhaustion_risk[index] = 2.0 * bear_exhaustion_highest - bear_exhaustion_low_at_high
    elif (prior_bear_exhaustion_risk > 0.0 and close[index] > prior_bear_exhaustion_risk) or (prior_bear_target == 0.0 and next_bull_exhaustion == 13):
        bear_exhaustion_risk[index] = 0.0
    else:
        bear_exhaustion_risk[index] = prior_bear_exhaustion_risk
    if next_bear_exhaustion == 13 and np.all(np.isfinite([bear_exhaustion_lowest, bear_exhaustion_high_at_low])):
        bear_exhaustion_target[index] = 2.0 * bear_exhaustion_lowest - bear_exhaustion_high_at_low
    elif (prior_bear_target > 0.0 and close[index] < prior_bear_target) or (bear_exhaustion_risk[index] == 0.0 and next_bull_exhaustion == 13):
        bear_exhaustion_target[index] = 0.0
    else:
        bear_exhaustion_target[index] = prior_bear_target

    if bull_momentum[index] == 9:
        bull9_indices.append(index)
    if bear_momentum[index] == 9:
        bear9_indices.append(index)
    if next_bull_exhaustion == 13 and previous_bull_exhaustion != 13:
        bull13_indices.append(index)
    if next_bear_exhaustion == 13 and previous_bear_exhaustion != 13:
        bear13_indices.append(index)

    short_qualified = len(bear9_indices) >= 2 and bool(bear13_indices) and bool(bull9_indices) and bear9_indices[-1] > bear13_indices[-1] > bear9_indices[-2] > bull9_indices[-1]
    long_qualified = len(bull9_indices) >= 2 and bool(bull13_indices) and bool(bear9_indices) and bull9_indices[-1] > bull13_indices[-1] > bull9_indices[-2] > bear9_indices[-1]
    short_open = (bear_momentum[index] == 9 or early_bear[index]) if trade_setup == "Momentum" else ((index >= 5 and bear_exhaustion[index - 5] == 13) if trade_setup == "Exhaustion" else (bear_momentum[index] == 9 and short_qualified))
    long_open = (bull_momentum[index] == 9 or early_bull[index]) if trade_setup == "Momentum" else ((index >= 5 and bull_exhaustion[index - 5] == 13) if trade_setup == "Exhaustion" else (bull_momentum[index] == 9 and long_qualified))
    if trade_setup == "None":
        short_open = long_open = False
    pending_short = True if short_open else (False if previous_short_confirmation else pending_short)
    pending_long = True if long_open else (False if previous_long_confirmation else pending_long)
    short_setup[index] = pending_short and index >= 5 and close[index] < close[index - 4] and close[index - 1] > close[index - 5]
    long_setup[index] = pending_long and index >= 5 and close[index] > close[index - 4] and close[index - 1] < close[index - 5]
    previous_short_confirmation = bool(short_setup[index])
    previous_long_confirmation = bool(long_setup[index])
    if short_setup[index]:
        short_active, long_active = True, False
    if long_setup[index]:
        long_active, short_active = True, False
    if show_trade_warnings and short_active and opened[index] < close[index] and bear_momentum[index] == 2 and not long_active:
        short_warning[index], short_active = True, False
    if show_trade_warnings and short_active and opened[index] < close[index] and ((index and bear_momentum_risk[index - 1] > 0.0 and close[index] > bear_momentum_risk[index - 1]) or (index and bear_exhaustion_risk[index - 1] > 0.0 and close[index] > bear_exhaustion_risk[index - 1])):
        short_warning[index], short_active = True, False
    if show_trade_warnings and long_active and opened[index] > close[index] and bull_momentum[index] == 2 and not short_active:
        long_warning[index], long_active = True, False
    if show_trade_warnings and long_active and opened[index] > close[index] and ((index and bull_momentum_risk[index - 1] > 0.0 and close[index] < bull_momentum_risk[index - 1]) or (index and bull_exhaustion_risk[index - 1] > 0.0 and close[index] < bull_exhaustion_risk[index - 1])):
        long_warning[index], long_active = True, False

bull9 = bull_momentum == 9
bear9 = bear_momentum == 9
bull13 = bull_exhaustion == 13
bear13 = bear_exhaustion == 13
detailed_bull_momentum = (bull_momentum > 0) & (bull_momentum < 9) if momentum_display == "Detailed" else np.zeros(n, dtype=bool)
detailed_bear_momentum = (bear_momentum > 0) & (bear_momentum < 9) if momentum_display == "Detailed" else np.zeros(n, dtype=bool)
detailed_bull_exhaustion = (bull_exhaustion > 0) & (bull_exhaustion < 13) if exhaustion_display == "Detailed" else np.zeros(n, dtype=bool)
detailed_bear_exhaustion = (bear_exhaustion > 0) & (bear_exhaustion < 13) if exhaustion_display == "Detailed" else np.zeros(n, dtype=bool)

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Resistance", "title": show_support_resistance, "data": _finite(np.where((resistance > 0.0) & show_support_resistance, resistance, np.nan)), "color": "#F23645", "type": "step", "lineWidth": 2, "overlay": True},
        {"name": "Support", "title": show_support_resistance, "data": _finite(np.where((support > 0.0) & show_support_resistance, support, np.nan)), "color": "#089981", "type": "step", "lineWidth": 2, "overlay": True},
        {"name": "Bull Momentum Risk", "title": momentum_risk_levels, "data": _finite(np.where((bull_momentum_risk != 0.0) & momentum_risk_levels, bull_momentum_risk, np.nan)), "color": "#2962FF", "type": "step", "overlay": True},
        {"name": "Bear Momentum Risk", "title": momentum_risk_levels, "data": _finite(np.where((bear_momentum_risk != 0.0) & momentum_risk_levels, bear_momentum_risk, np.nan)), "color": "#2962FF", "type": "step", "overlay": True},
        {"name": "Bull Exhaustion Risk", "title": exhaustion_risk_levels, "data": _finite(np.where((bull_exhaustion_risk != 0.0) & exhaustion_risk_levels, bull_exhaustion_risk, np.nan)), "color": "#00BCD4", "type": "step", "overlay": True},
        {"name": "Bear Exhaustion Risk", "title": exhaustion_risk_levels, "data": _finite(np.where((bear_exhaustion_risk != 0.0) & exhaustion_risk_levels, bear_exhaustion_risk, np.nan)), "color": "#00BCD4", "type": "step", "overlay": True},
        {"name": "Bull Exhaustion Target", "title": exhaustion_target_levels, "data": _finite(np.where((bull_exhaustion_target != 0.0) & exhaustion_target_levels, bull_exhaustion_target, np.nan)), "color": "#787B86", "type": "step", "overlay": True},
        {"name": "Bear Exhaustion Target", "title": exhaustion_target_levels, "data": _finite(np.where((bear_exhaustion_target != 0.0) & exhaustion_target_levels, bear_exhaustion_target, np.nan)), "color": "#787B86", "type": "step", "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "overlay": True},
    ],
    "signals": [
        _events(bull9 & (momentum_display != "None"), low, "Bull Momentum 9", "#089981", "buy"),
        _events(bear9 & (momentum_display != "None"), high, "Bear Momentum 9", "#F23645", "sell"),
        _events(bull13 & (exhaustion_display != "None"), low, "Bull Exhaustion 13", "#006400", "buy"),
        _events(bear13 & (exhaustion_display != "None"), high, "Bear Exhaustion 13", "#910000", "sell"),
        _events(detailed_bull_momentum, high, "Bull Momentum Count", "#089981", "info"),
        _events(detailed_bear_momentum, high, "Bear Momentum Count", "#F23645", "info"),
        _events(detailed_bull_exhaustion, low, "Bull Exhaustion Count", "#089981", "info"),
        _events(detailed_bear_exhaustion, high, "Bear Exhaustion Count", "#F23645", "info"),
        _events(long_setup, low, "Long Reversal", "#2962FF", "buy"),
        _events(short_setup, high, "Short Reversal", "#FDD835", "sell"),
        _events(long_warning, high, "Long Warning", "#FDD835", "warning"),
        _events(short_warning, low, "Short Warning", "#FDD835", "warning"),
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "bullMomentum": [int(value) for value in bull_momentum], "bearMomentum": [int(value) for value in bear_momentum],
        "perfectBull": perfect_bull.tolist(), "perfectBear": perfect_bear.tolist(), "earlyBull": early_bull.tolist(), "earlyBear": early_bear.tolist(),
        "resistance": _finite(resistance), "support": _finite(support), "bullMomentumRisk": _finite(bull_momentum_risk), "bearMomentumRisk": _finite(bear_momentum_risk),
        "bullExhaustion": [int(value) for value in bull_exhaustion], "bearExhaustion": [int(value) for value in bear_exhaustion],
        "bullExhaustionActive": bull_exhaustion_active.tolist(), "bearExhaustionActive": bear_exhaustion_active.tolist(),
        "bullExhaustionRisk": _finite(bull_exhaustion_risk), "bearExhaustionRisk": _finite(bear_exhaustion_risk),
        "bullExhaustionTarget": _finite(bull_exhaustion_target), "bearExhaustionTarget": _finite(bear_exhaustion_target),
        "longSetup": long_setup.tolist(), "shortSetup": short_setup.tolist(), "longWarning": long_warning.tolist(), "shortWarning": short_warning.tolist(),
    },
}
