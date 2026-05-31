# ============================================================
# 截面策略指标示例（研究参考版）
# Cross-Sectional Strategy Indicator Example (Research Reference)
# Momentum + RSI Composite Score
# ============================================================
#
# 使用方法:
# 1. 作为截面研究思路示例阅读
# 2. 用于理解“对多个标的打分再排序”的基本写法
#
# 当前限制:
# - 策略快照回测（cross_sectional）暂不支持；实盘指标截面策略可在「交易助手」创建
# - 创建时选择「截面策略」并插入模板，或在本指标中实现 scores 字典
#
# 评分逻辑:
# - 动量因子（20周期）：价格变化率，越高越好
# - RSI 指标（14周期）：反转 RSI 值，越低越好（100 - RSI）
# - 综合评分：70% 动量 + 30% RSI 反转值
#
# ============================================================

# 截面策略指标
# 输入: data = {symbol1: df1, symbol2: df2, ...}
# 输出: scores = {symbol1: score1, symbol2: score2, ...}

scores = {}

# 遍历全部标的
for symbol, df in data.items():
    # 确保数据长度足够
    if len(df) < 20:
        scores[symbol] = 0
        continue
    
    # === 1. 计算动量因子 (20周期) ===
    # 动量 = (当前价格 / 20周期前价格 - 1) * 100
    momentum = (df['close'].iloc[-1] / df['close'].iloc[-20] - 1) * 100
    
    # === 2. 计算RSI指标 (14周期) ===
    def calculate_rsi(prices, period=14):
        """计算 RSI 指标"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]
    
    rsi_value = calculate_rsi(df['close'], 14)
    
    # === 3. 综合评分 ===
    # 动量越高 = 评分越高
    # RSI越低（超卖）= 评分越高（100 - RSI）
    # 权重: 70% 动量 + 30% RSI反转值
    momentum_score = momentum
    rsi_score = 100 - rsi_value  # 反转 RSI（RSI 越低，评分越高）
    
    composite_score = momentum_score * 0.7 + rsi_score * 0.3
    
    scores[symbol] = composite_score

# === 可选：手动指定排序 ===
# 如果不提供，系统会根据 scores 自动排序
# rankings = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

# === 研究语义说明 ===
# 1. 根据评分对所有标的进行排序（从高到低）
# 2. 选择排名靠前的 N 个标的做多（基于 portfolio_size * long_ratio）
# 3. 选择排名靠后的 N 个标的做空（基于 portfolio_size * (1 - long_ratio)）
# 4. 在未来平台链路完善后，可由系统统一生成买入 / 卖出 / 平仓动作
