# ============================================================
# 双均线策略（文档同步版 · 四路信号）
# Dual Moving Average Strategy (Doc-Aligned, Four-Way)
# ============================================================
#
# 适用场景:
# 1. 在 Indicator IDE 中快速验证均线交叉逻辑
# 2. 演示 `# @param` + `# @strategy` + 四路执行列
# 3. 与平台默认模板 / SIGNAL_EXECUTION_STANDARD v1 对齐
#
# 注意:
# - 杠杆请在产品面板中设置，不要写进源码
# - 触及型 tp/sl 请用 close_*，勿与 trailingEnabled 叠加
#
# ============================================================

my_indicator_name = "双均线交叉策略"
my_indicator_description = "EMA 金叉/死叉四路信号，边缘触发；退出由引擎 stopLoss/takeProfit 管理。"

# --- QuantDinger execution contract (v1) ---
# signal_form: four_way
# exit_owner: engine
# flip_mode: R2

# === 参数声明（供前端、AI 调参与代码质量检查识别） ===
# @param sma_short int 14 短期均线周期
# @param sma_long int 28 长期均线周期

# === 平台默认策略配置 ===
# @strategy stopLossPct 0.02
# @strategy takeProfitPct 0.05
# @strategy entryPct 0.25
# @strategy trailingEnabled false
# @strategy tradeDirection both

# 说明：close_* 只表达均线反转平仓；固定止损/止盈由 engine 风控负责。
# 如果改成触及型 TP/SL，请同步改为 exit_owner: indicator。


def edge(s):
    s = s.fillna(False).astype(bool)
    return s & ~s.shift(1).fillna(False)


sma_short_period = int(params.get("sma_short", 14))
sma_long_period = int(params.get("sma_long", 28))

df = df.copy()

sma_short = df["close"].rolling(sma_short_period).mean()
sma_long = df["close"].rolling(sma_long_period).mean()

golden = (sma_short > sma_long) & (sma_short.shift(1) <= sma_long.shift(1))
death = (sma_short < sma_long) & (sma_short.shift(1) >= sma_long.shift(1))

df["open_long"] = edge(golden)
df["open_short"] = edge(death)
df["close_long"] = edge(death)
df["close_short"] = edge(golden)

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
            "name": f"SMA{sma_short_period}",
            "data": sma_short.fillna(0).tolist(),
            "color": "#FF9800",
            "overlay": True,
        },
        {
            "name": f"SMA{sma_long_period}",
            "data": sma_long.fillna(0).tolist(),
            "color": "#3F51B5",
            "overlay": True,
        },
    ],
    "signals": [
        {"type": "buy", "text": "L", "data": open_long_marks, "color": "#00E676"},
        {"type": "sell", "text": "S", "data": open_short_marks, "color": "#FF5252"},
    ],
}
