# ============================================================
# QuantDinger 四路信号空壳模板（契约 v1）
# ------------------------------------------------------------
# 复制到指标 IDE 作为新策略起点；与平台默认模板一致。
# 文档: docs/SIGNAL_EXECUTION_STANDARD_CN.md
# ============================================================

my_indicator_name = "四路信号策略模板"
my_indicator_description = "在此填写策略逻辑说明；默认双均线示例，可整体替换计算段。"

# --- QuantDinger execution contract (v1) ---
# signal_form: four_way
# exit_owner: engine
# flip_mode: R2

# @strategy stopLossPct 0.03
# @strategy takeProfitPct 0.06
# @strategy entryPct 0.25
# @strategy trailingEnabled false
# @strategy tradeDirection both

# 说明：close_* 只表达趋势反转时的结构性平仓；固定价格风控由 engine 负责。
# 若把 TP/SL/轨道触及退出写进 close_*，请改为 exit_owner: indicator。

# @param fast_period int 10 快线 EMA 周期
# @param slow_period int 30 慢线 EMA 周期


def edge(s):
    s = s.fillna(False).astype(bool)
    return s & ~s.shift(1).fillna(False)


fast_period = int(params.get("fast_period", 10))
slow_period = int(params.get("slow_period", 30))

df = df.copy()

ema_fast = df["close"].ewm(span=fast_period, adjust=False).mean()
ema_slow = df["close"].ewm(span=slow_period, adjust=False).mean()

golden = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
death = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))

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

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": f"EMA{fast_period}",
            "data": ema_fast.fillna(0).tolist(),
            "color": "#FF9800",
            "overlay": True,
        },
        {
            "name": f"EMA{slow_period}",
            "data": ema_slow.fillna(0).tolist(),
            "color": "#3F51B5",
            "overlay": True,
        },
    ],
    "signals": [
        {"type": "buy", "text": "L", "data": open_long_marks, "color": "#00E676"},
        {"type": "sell", "text": "S", "data": open_short_marks, "color": "#FF5252"},
    ],
}
