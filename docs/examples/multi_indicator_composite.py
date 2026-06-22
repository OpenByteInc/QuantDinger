# ============================================================
# 多指标组合策略（文档同步版）
# Multi-Indicator Composite Strategy (Doc-Aligned Example)
# ============================================================
#
# 示例目标:
# 1. 演示如何把 `# @param`、`# @strategy` 和平台 UI 对齐
# 2. 演示如何组合均线、RSI、MACD、成交量过滤
# 3. 演示如何把原始条件整理成更稳定的边缘触发信号
#
# ============================================================

my_indicator_name = "多指标组合策略"
my_indicator_description = "均线、RSI、MACD 与成交量过滤共同参与的组合信号示例。"

# --- QuantDinger execution contract (v1) ---
# signal_form: four_way
# exit_owner: engine
# flip_mode: R2

# === 参数声明 ===
# @param sma_short int 10 短期均线周期
# @param sma_long int 30 长期均线周期
# @param rsi_period int 14 RSI周期
# @param rsi_oversold int 30 RSI超卖阈值
# @param rsi_overbought int 70 RSI超买阈值
# @param use_macd bool true 是否使用MACD过滤
# @param use_volume bool false 是否使用成交量过滤
# @param volume_mult float 1.5 成交量放大倍数

# === 平台默认策略配置 ===
# @strategy stopLossPct 0.025
# @strategy takeProfitPct 0.06
# @strategy entryPct 0.2
# @strategy trailingEnabled true
# @strategy trailingStopPct 0.02
# @strategy trailingActivationPct 0.04
# @strategy tradeDirection both

# 说明：本示例使用四路执行信号；固定止损、止盈和追踪止损由引擎负责。
# output["signals"] 只负责图表标记，不参与下单。

df = df.copy()

# === 获取参数 ===
sma_short_period = int(params.get('sma_short', 10))
sma_long_period = int(params.get('sma_long', 30))
rsi_period = int(params.get('rsi_period', 14))
rsi_oversold = int(params.get('rsi_oversold', 30))
rsi_overbought = int(params.get('rsi_overbought', 70))
use_macd = bool(params.get('use_macd', True))
use_volume = bool(params.get('use_volume', False))
volume_mult = float(params.get('volume_mult', 1.5))

# === 计算均线 ===
sma_short = df["close"].rolling(sma_short_period).mean()
sma_long = df["close"].rolling(sma_long_period).mean()

# === 计算RSI ===
delta = df["close"].diff()
gain = delta.where(delta > 0, 0).rolling(window=rsi_period).mean()
loss = (-delta.where(delta < 0, 0)).rolling(window=rsi_period).mean()
rs = gain / loss.replace(0, np.nan)
rsi = 100 - (100 / (1 + rs))

# === 计算MACD ===
exp1 = df["close"].ewm(span=12, adjust=False).mean()
exp2 = df["close"].ewm(span=26, adjust=False).mean()
macd = exp1 - exp2
macd_signal = macd.ewm(span=9, adjust=False).mean()

# === 计算成交量均线 ===
volume_ma = df["volume"].rolling(20).mean()

# === 原始条件 ===
ma_golden = (sma_short > sma_long) & (sma_short.shift(1) <= sma_long.shift(1))
ma_death = (sma_short < sma_long) & (sma_short.shift(1) >= sma_long.shift(1))
rsi_buy = rsi < rsi_oversold
rsi_sell = rsi > rsi_overbought
macd_up = macd > macd_signal
macd_down = macd < macd_signal
volume_up = df["volume"] > volume_ma * volume_mult

# === 组合条件 ===
raw_buy = ma_golden | rsi_buy
raw_sell = ma_death | rsi_sell

if use_macd:
    raw_buy = raw_buy & macd_up
    raw_sell = raw_sell | macd_down

if use_volume:
    raw_buy = raw_buy & volume_up

# === 四路边缘触发执行信号 ===
def edge(signal):
    signal = signal.fillna(False).astype(bool)
    return signal & ~signal.shift(1).fillna(False)


open_long = edge(raw_buy)
open_short = edge(raw_sell)
df["open_long"] = open_long
df["close_short"] = open_long
df["open_short"] = open_short
df["close_long"] = open_short

# === 买卖标记点 ===
buy_marks = [df["low"].iloc[i] * 0.995 if df["open_long"].iloc[i] else None for i in range(len(df))]
sell_marks = [df["high"].iloc[i] * 1.005 if df["open_short"].iloc[i] else None for i in range(len(df))]

# === 图表输出配置 ===
output = {
    "name": my_indicator_name,
    "plots": [
        {"name": f"SMA{sma_short_period}", "data": sma_short.fillna(0).tolist(), "color": "#FF9800", "overlay": True},
        {"name": f"SMA{sma_long_period}", "data": sma_long.fillna(0).tolist(), "color": "#3F51B5", "overlay": True},
        {"name": "RSI", "data": rsi.fillna(50).tolist(), "color": "#722ED1", "overlay": False},
        {"name": "MACD", "data": macd.fillna(0).tolist(), "color": "#13C2C2", "overlay": False}
    ],
    "signals": [
        {"type": "buy", "text": "B", "data": buy_marks, "color": "#00E676"},
        {"type": "sell", "text": "S", "data": sell_marks, "color": "#FF5252"}
    ]
}
