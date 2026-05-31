# 截面策略使用指南

## 概述

截面策略（Cross-Sectional Strategy）是一种同时交易多个标的的策略类型。它根据某些因子对所有标的进行评分和排序，然后做多排名靠前的标的，做空排名靠后的标的。

## 功能特点

1. **多标的支持**：可以同时交易多个标的（股票、币种等）
2. **自动排序**：根据指标计算的评分自动排序标的
3. **组合管理**：自动管理持仓组合，保持做多/做空比例
4. **定期调仓**：支持每日/每周/每月调仓频率
5. **批量执行**：并行执行多个标的的交易，提高效率

## 配置说明

### 策略配置参数

在 **交易助手 → 创建策略** 中选择「截面策略」，自选标的池并配置组合参数；也可在 API/数据库的 `trading_config` 中直接写入以下字段：

```json
{
  "cs_strategy_type": "cross_sectional",  // 策略类型：'single' 或 'cross_sectional'
  "symbol_list": [                        // 标的列表
    "Crypto:BTC/USDT",
    "Crypto:ETH/USDT",
    "Crypto:BNB/USDT"
  ],
  "portfolio_size": 10,                   // 持仓组合大小（做多+做空的总数）
  "long_ratio": 0.5,                     // 做多比例（0-1之间，0.5表示50%做多，50%做空）
  "rebalance_frequency": "daily"          // 调仓频率：'daily' | 'weekly' | 'monthly'
}
```

### 参数说明

- **cs_strategy_type**: 
  - `'single'`: 单标的策略（默认，原有功能）
  - `'cross_sectional'`: 截面策略

- **symbol_list**: 
  - 标的列表，格式为 `["Market:SYMBOL", ...]`
  - 例如：`["Crypto:BTC/USDT", "Crypto:ETH/USDT"]`

- **portfolio_size**: 
  - 持仓组合大小，即同时持有的标的数量
  - 例如：10 表示同时持有10个标的

- **long_ratio**: 
  - 做多比例，0-1之间的浮点数
  - 例如：0.5 表示50%做多，50%做空
  - 例如：1.0 表示100%做多（不做空）

- **rebalance_frequency**: 
  - 调仓频率
  - `'daily'`: 每日调仓
  - `'weekly'`: 每周调仓
  - `'monthly'`: 每月调仓

## 指标代码编写

截面策略的指标代码需要返回所有标的的评分和排序。

### 指标代码模板

```python
# 截面策略指标模板
# 输入：data = {symbol1: df1, symbol2: df2, ...}
# 输出：scores = {symbol1: score1, symbol2: score2, ...}
#       rankings = [symbol1, symbol2, ...]  # 可选，如果不提供会根据scores自动排序

scores = {}
for symbol, df in data.items():
    # 计算每个标的的因子值
    # 例如：动量因子
    momentum = (df['close'].iloc[-1] / df['close'].iloc[-20] - 1) * 100
    
    # 例如：RSI指标
    def calculate_rsi(prices, period=14):
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]
    
    rsi = calculate_rsi(df['close'], 14)
    
    # 综合评分（可以根据需要调整权重）
    score = momentum * 0.6 + (100 - rsi) * 0.4
    scores[symbol] = score

# 可选：手动指定排序（如果不提供，系统会根据scores自动排序）
# rankings = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
```

### 指标代码环境变量

在指标代码执行时，可以使用以下变量：

- `symbols`: 标的列表 `['Crypto:BTC/USDT', 'Crypto:ETH/USDT', ...]`
- `data`: 所有标的的K线数据 `{symbol: df, ...}`
- `scores`: 用于存储评分的字典（需要在代码中填充）
- `rankings`: 用于存储排序的列表（可选，如果不提供会根据scores自动排序）
- `np`: numpy
- `pd`: pandas
- `trading_config`: 交易配置
- `config`: 交易配置（别名）

### 输出要求

指标代码需要填充 `scores` 字典：

```python
scores[symbol] = score_value  # score_value 可以是任意数值
```

可选：填充 `rankings` 列表（如果不提供，系统会根据scores自动排序）：

```python
rankings = [symbol1, symbol2, ...]  # 按评分从高到低排序
```

## 信号生成逻辑

系统会根据以下逻辑自动生成交易信号：

1. **排序标的**：根据指标计算的评分对所有标的进行排序
2. **选择持仓**：
   - 排名靠前的 `portfolio_size * long_ratio` 个标的 → 做多
   - 排名靠后的 `portfolio_size * (1 - long_ratio)` 个标的 → 做空
3. **生成信号**：
   - 新增标的：如果标的不在当前持仓中，生成开仓信号
   - 移除标的：如果标的不在目标持仓中，生成平仓信号
   - 方向变更：如果标的需要从多转空或从空转多，先生成平仓信号，再生成开仓信号

## 使用示例

### 1. 创建截面策略

通过API创建策略时，在请求体中包含：

```json
{
  "strategy_name": "动量截面策略",
  "trading_config": {
    "cs_strategy_type": "cross_sectional",
    "symbol_list": [
      "Crypto:BTC/USDT",
      "Crypto:ETH/USDT",
      "Crypto:BNB/USDT",
      "Crypto:ADA/USDT",
      "Crypto:SOL/USDT"
    ],
    "portfolio_size": 5,
    "long_ratio": 0.6,
    "rebalance_frequency": "daily",
    "timeframe": "1H",
    "initial_capital": 10000,
    "leverage": 1,
    "market_type": "swap"
  },
  "indicator_config": {
    "indicator_id": 123,
    "indicator_code": "..."
  }
}
```

### 2. 指标代码示例

```python
# 动量+RSI综合评分
scores = {}
for symbol, df in data.items():
    # 20周期动量
    momentum = (df['close'].iloc[-1] / df['close'].iloc[-20] - 1) * 100
    
    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    rsi_value = rsi.iloc[-1]
    
    # 综合评分
    score = momentum * 0.7 + (100 - rsi_value) * 0.3
    scores[symbol] = score
```

## 注意事项

1. **数据获取**：系统会为每个标的获取K线数据，如果某个标的数据获取失败，会跳过该标的
2. **调仓频率**：系统会根据 `rebalance_frequency` 设置检查是否需要调仓，未到调仓时间时不会执行交易
3. **批量执行**：所有交易信号会并行执行，最多同时执行10个交易
4. **持仓管理**：系统会自动管理持仓，确保持仓组合符合配置要求
5. **兼容性**：截面策略功能不影响现有的单标的策略，两者可以共存

## 数据库迁移

如果需要使用数据库字段存储截面策略配置（可选），可以运行迁移脚本：

```sql
-- 运行 migrations/add_cross_sectional_strategy.sql
```

如果不运行迁移脚本，截面策略配置会存储在 `trading_config` JSON字段中，功能完全正常。

## 故障排查

1. **策略不执行**：
   - 检查 `cs_strategy_type` 是否为 `'cross_sectional'`
   - 检查 `symbol_list` 是否不为空
   - 检查调仓频率是否已到时间

2. **指标执行失败**：
   - 检查指标代码是否正确填充 `scores` 字典
   - 检查所有标的的数据是否都能正常获取

3. **信号不生成**：
   - 检查 `portfolio_size` 是否小于等于 `symbol_list` 的长度
   - 检查评分是否有效
