"""
Canonical default indicator templates for the QuantDinger execution contract.

Used by AI fallback code generation and as the reference for IDE / docs examples.
See docs/SIGNAL_EXECUTION_STANDARD_CN.md.
"""

from __future__ import annotations


def build_default_indicator_template(
    *,
    name: str = "EMA Four-Way Strategy Template",
    description: str = (
        "EMA crossover with four-way execution columns, edge-triggered signals, "
        "and engine-managed risk exits."
    ),
) -> str:
    """Four-way EMA crossover starter used when LLM generation is unavailable."""
    safe_name = (name or "EMA Four-Way Strategy Template").replace("\\", "\\\\").replace('"', '\\"')
    safe_desc = (description or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'''# ============================================================
# QuantDinger default indicator template - Pattern B contract v1
# ------------------------------------------------------------
# signal_form: four_way    exit_owner: engine    flip_mode: R2
# Reference: docs/SIGNAL_EXECUTION_STANDARD_CN.md
# ============================================================

my_indicator_name = "{safe_name}"
my_indicator_description = "{safe_desc}"

# ===== Engine-managed risk defaults =====
# Values are decimal price-move ratios, shared by backtest and live trading.
# stopLossPct 0.03 means a 3% adverse price move; entryPct 1 means 100% capital.
# close_* columns below express structural EMA reversals only. If the indicator
# owns TP/SL/channel exits, change the header to exit_owner: indicator.
# @strategy stopLossPct 0.03
# @strategy takeProfitPct 0.06
# @strategy entryPct 0.25
# @strategy trailingEnabled false
# @strategy tradeDirection both

# ===== Tunable parameters; always read them via params.get =====
# @param fast_period int 10 Fast EMA period
# @param slow_period int 30 Slow EMA period

def edge(s):
    """Return True only on the bar where a condition flips from false to true."""
    s = s.fillna(False).astype(bool)
    return s & ~s.shift(1).fillna(False)


fast_period = int(params.get("fast_period", 10))
slow_period = int(params.get("slow_period", 30))

df = df.copy()

ema_fast = df["close"].ewm(span=fast_period, adjust=False).mean()
ema_slow = df["close"].ewm(span=slow_period, adjust=False).mean()

golden = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
death = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))

# Reversal bars may close the opposite side and open the new side on the same bar.
# These close_* columns are structural exits, not fixed TP/SL rules.
raw_open_long = golden
raw_open_short = death
raw_close_long = death
raw_close_short = golden

df["open_long"] = edge(raw_open_long)
df["open_short"] = edge(raw_open_short)
df["close_long"] = edge(raw_close_long)
df["close_short"] = edge(raw_close_short)

n = len(df)
open_long_marks = [
    df["low"].iloc[i] * 0.995 if bool(df["open_long"].iloc[i]) else None for i in range(n)
]
open_short_marks = [
    df["high"].iloc[i] * 1.005 if bool(df["open_short"].iloc[i]) else None for i in range(n)
]

output = {{
    "name": my_indicator_name,
    "plots": [
        {{
            "name": f"EMA{{fast_period}}",
            "data": ema_fast.fillna(0).tolist(),
            "color": "#FF9800",
            "overlay": True,
        }},
        {{
            "name": f"EMA{{slow_period}}",
            "data": ema_slow.fillna(0).tolist(),
            "color": "#3F51B5",
            "overlay": True,
        }},
    ],
    "signals": [
        {{"type": "buy", "text": "L", "data": open_long_marks, "color": "#00E676"}},
        {{"type": "sell", "text": "S", "data": open_short_marks, "color": "#FF5252"}},
    ],
    "layers": [],
}}
'''
