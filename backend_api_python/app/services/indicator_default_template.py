"""
Canonical default indicator templates (QuantDinger execution contract v1).

Used by AI fallback code generation and as the reference for IDE / docs examples.
See docs/SIGNAL_EXECUTION_STANDARD_CN.md.
"""

from __future__ import annotations


def build_default_indicator_template(
    *,
    name: str = "策略模板（四路信号）",
    description: str = (
        "双均线金叉/死叉：四路显式信号 + 边缘触发 + 引擎风控。"
        "详见 docs/SIGNAL_EXECUTION_STANDARD_CN.md"
    ),
) -> str:
    """Four-way EMA crossover starter (platform default)."""
    safe_name = (name or "策略模板（四路信号）").replace("\\", "\\\\").replace('"', '\\"')
    safe_desc = (description or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'''# ============================================================
# QuantDinger 默认指标模板 — 形态 B（四路信号）· 契约 v1
# ------------------------------------------------------------
# signal_form: four_way    exit_owner: engine    flip_mode: R2
# 文档: docs/SIGNAL_EXECUTION_STANDARD_CN.md
# ============================================================

my_indicator_name = "{safe_name}"
my_indicator_description = "{safe_desc}"

# ===== 平台默认风控（引擎退出；指标内勿再写窄 tp/sl）=====
# 单位：0–1 小数比例（与回测/实盘一致；按标的涨跌幅，不除杠杆）
#   stopLossPct 0.03 = 3% 价格止损；0.001 = 0.1%；entryPct 1 = 100% 资金
# @strategy stopLossPct 0.03
# @strategy takeProfitPct 0.06
# @strategy entryPct 0.25
# @strategy trailingEnabled false
# @strategy tradeDirection both

# ===== 可调参数（须用 params.get 读取）=====
# @param fast_period int 10 快线 EMA 周期
# @param slow_period int 30 慢线 EMA 周期

def edge(s):
    """边缘触发：仅条件由 false→true 的 K 线记为信号。"""
    s = s.fillna(False).astype(bool)
    return s & ~s.shift(1).fillna(False)


fast_period = int(params.get("fast_period", 10))
slow_period = int(params.get("slow_period", 30))

df = df.copy()

ema_fast = df["close"].ewm(span=fast_period, adjust=False).mean()
ema_slow = df["close"].ewm(span=slow_period, adjust=False).mean()

golden = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
death = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))

# 反手 bar：同根 K 先平对侧再开（R2）；仅趋势翻转时触发
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
}}
'''
