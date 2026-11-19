"""Canonical default chart indicator template for QuantDinger."""

from __future__ import annotations


def build_default_indicator_template(
    *,
    name: str = "EMA Chart Indicator Template",
    description: str = "EMA crossover chart indicator with visual markers only.",
) -> str:
    """EMA crossover chart starter used when LLM generation is unavailable."""
    safe_name = (name or "EMA Chart Indicator Template").replace("\\", "\\\\").replace('"', '\\"')
    safe_desc = (description or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'''# ============================================================
# QuantDinger default chart indicator template
# ------------------------------------------------------------
# Indicators are chart-only. They do not open, close, size, or manage trades.
# Convert an indicator to a script strategy before backtesting or live trading.
# ============================================================

my_indicator_name = "{safe_name}"
my_indicator_description = "{safe_desc}"

# ===== Tunable parameters; always read them via params.get =====
# @param fast_period int 10 Fast EMA period
# @param slow_period int 30 Slow EMA period

def edge(s):
    """Return True only on the bar where a condition flips from false to true."""
    s = s.fillna(False).astype(bool)
    previous = s.shift(1, fill_value=False).astype(bool)
    return s & ~previous


fast_period = int(params.get("fast_period", 10))
slow_period = int(params.get("slow_period", 30))

df = df.copy()

ema_fast = df["close"].ewm(span=fast_period, adjust=False).mean()
ema_slow = df["close"].ewm(span=slow_period, adjust=False).mean()

def plot_list(s):
    """Convert warm-up NaN to None so price overlays do not draw fake zero lines."""
    return [None if pd.isna(v) else float(v) for v in s]

golden = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
death = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))
golden_edge = edge(golden)
death_edge = edge(death)

n = len(df)
golden_marks = [
    df["low"].iloc[i] * 0.995 if bool(golden_edge.iloc[i]) else None for i in range(n)
]
death_marks = [
    df["high"].iloc[i] * 1.005 if bool(death_edge.iloc[i]) else None for i in range(n)
]

output = {{
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {{
            "name": f"EMA{{fast_period}}",
            "data": plot_list(ema_fast),
            "color": "#FF9800",
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        }},
        {{
            "name": f"EMA{{slow_period}}",
            "data": plot_list(ema_slow),
            "color": "#3F51B5",
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        }},
    ],
    "signals": [
        {{"type": "buy", "text": "Golden", "data": golden_marks, "color": "#00E676", "renderMode": "events"}},
        {{"type": "sell", "text": "Death", "data": death_marks, "color": "#FF5252", "renderMode": "events"}},
    ],
    "layers": [],
    "tables": [],
}}
'''
