# QuantDinger Python v3 策略开发指南

> **平台级契约（必读）**：[信号与执行标准 v1](./SIGNAL_EXECUTION_STANDARD_CN.md) — 适用于所有指标策略的回测/实盘对齐、两路/四路选型、退出负责人与上线清单。本指南侧重教程与示例。

这份指南不是单纯罗列接口，而是站在**策略开发者**视角，回答一个更实际的问题：

**到底应该怎么写一个结构清晰、能回测、能落地成平台策略的指标策略？**

QuantDinger 当前支持两条 Python 开发路径：

- **IndicatorStrategy**：基于 `df` 的指标/信号脚本，用于 Indicator IDE、图表渲染和信号型回测。
- **ScriptStrategy**：基于 `on_init / on_bar` 的事件驱动脚本，用于策略运行时、策略回测与实盘执行。

如果你要从零开始开发一个策略，默认建议是：

1. 先用 `IndicatorStrategy` 把信号逻辑跑通。
2. 先验证图表、信号和回测语义。
3. 只有当你需要运行时状态、动态仓位管理或执行控制时，再升级为 `ScriptStrategy`。

---

## 1. 先建立正确心智模型

很多开发者会把**信号逻辑**、**止盈止损**、**仓位管理**、**执行逻辑**混在一起写，结果文档看不懂、代码也不好维护。

### 1.1 IndicatorStrategy 是什么

可以把 `IndicatorStrategy` 理解成：

- 基于 `df` 计算指标序列
- 生成布尔型 `buy` / `sell` 信号
- 通过元数据声明默认策略配置
- 返回 `output` 供图表展示

它最适合：

- 指标研究
- 策略原型验证
- 参数调优
- 信号型回测
- 先做信号、后保存成平台策略的工作流

### 1.2 ScriptStrategy 是什么

可以把 `ScriptStrategy` 理解成：

- 按 bar 逐根执行的运行时逻辑
- 通过 `ctx.position` 读取当前持仓状态
- 用 `ctx.buy()`、`ctx.sell()`、`ctx.close_position()` 发出动作
- 把退出、仓位、执行节奏写进代码

它最适合：

- 有状态的执行逻辑
- 动态止盈止损
- 分批加仓、减仓、部分止盈
- 冷却期、重入限制、bot 型执行策略

### 1.3 最重要的分层

对于 `IndicatorStrategy`，请强制把逻辑拆成三层：

1. **指标层**：均线、RSI、ATR、布林带、过滤条件。
2. **信号层**：`df['buy']` 和 `df['sell']`。
3. **风险默认配置层**：`# @strategy stopLossPct ...`、`takeProfitPct`、`entryPct` 等。

不要把这三层混成一团。

尤其要明确：

- `buy` / `sell` 负责表达**什么时候进出场**
- `# @strategy` 负责表达**引擎默认如何控风险、如何设仓位**
- 杠杆属于产品配置，不属于指标脚本

---

## 2. 应该选哪种模式？

| 使用场景 | 推荐模式 |
|----------|----------|
| 写指标、叠加图表、画买卖点 | `IndicatorStrategy` |
| 研究 dataframe 上的进出场信号 | `IndicatorStrategy` |
| 只想给策略补固定止损、止盈、仓位默认值 | `IndicatorStrategy` |
| 需要逐根读取持仓状态做判断 | `ScriptStrategy` |
| 止损止盈依赖当前持仓状态动态变化 | `ScriptStrategy` |
| 需要分批开平仓、状态机、bot 风格执行 | `ScriptStrategy` |

一个简单判断方法：

- 如果你的逻辑可以表述成“条件 A 出现就买，条件 B 出现就卖”，先用 `IndicatorStrategy`
- 如果你的逻辑更像“开仓后要持续盯着当前持仓，并根据状态做不同反应”，那就应该用 `ScriptStrategy`

---

## 3. 如何开发一个 IndicatorStrategy

这是大多数新策略最推荐的开发路径。

### 3.1 第一步：先把元数据和默认配置写清楚

脚本开头先定义名称、描述、可调参数、默认策略配置。

```python
my_indicator_name = "Trend Pullback Strategy"
my_indicator_description = "Buy pullbacks in an uptrend and exit on weakness."

# signal_form: two_way
# exit_owner: engine
# flip_mode: R2

# @param fast_len int 20 Fast EMA length
# @param slow_len int 50 Slow EMA length
# @param rsi_len int 14 RSI length
# @param rsi_floor float 45 Minimum RSI for long entries

# @strategy stopLossPct 0.03
# @strategy takeProfitPct 0.06
# @strategy entryPct 0.25
# @strategy trailingEnabled true
# @strategy trailingStopPct 0.02
# @strategy trailingActivationPct 0.04
# @strategy tradeDirection long
```

`# @param` 用来定义用户经常调的参数。

格式如下：

```python
# @param <name> <int|float|bool|str|string> <default> <描述>
```

最佳实践：

- 声明后的参数，应该通过 `params.get(...)` 读取
- `string` 与 `str` 等价
- 如果声明了参数，却仍然把值硬编码在正文里，平台内置的代码质量检查会给出提醒

`# @strategy` 用来定义策略默认配置，比如：

- `stopLossPct`：止损比例，例如 `0.03` 表示 **标的价格下跌 3%** 触发（0–1 小数；`0.001` = 0.1%）
- `takeProfitPct`：止盈比例，例如 `0.06` 表示 **标的价格上涨 6%** 触发
- `entryPct`：开仓资金占比（0–1；**`1` = 100%**，`0.25` = 25%）
- `trailingEnabled`
- `trailingStopPct`
- `trailingActivationPct`
- `tradeDirection`：`long`、`short` 或 `both`

这里有个非常关键的边界：

- 这些是**引擎读取的默认配置**
- 不是让你再去 dataframe 里造一列 `stop_loss`
- 不要在这里写 `leverage`
- 数值尽量保持合理，并结合回测验证；底层解析器允许的范围会比示例更宽松

### 3.2 第二步：复制 dataframe，再算指标

Indicator 代码运行在沙盒里，`pd`、`np` 和 `params` 字典已预置。

推荐开头固定写：

```python
df = df.copy()
```

通常可用列包括：

- `open`
- `high`
- `low`
- `close`
- `volume`

`time` 列可能存在，但不要假设其类型永远一致。

避免这些写法：

- 网络请求
- 文件读写
- 子进程
- `eval`、`exec`、`open`、`__import__`、`getattr` / `setattr` 等破坏沙盒边界的模式
- `import operator`（以及通过字符串拼接访问 `__class__` / `__globals__` 等 dunder 的绕过写法）

允许 `import` 的白名单模块（其余会被校验拒绝）：

`numpy`、`pandas`、`math`、`json`、`datetime`、`time`、`collections`、`functools`、`itertools`、`statistics`、`decimal`、`fractions`、`copy`

环境已预置 `pd`、`np`、`params`，一般无需再 `import pandas` / `import numpy`。

### 3.3 第三步：把原始条件变成干净的 `buy` / `sell`

回测引擎读取的是两列**布尔信号**：

- `df['buy']`
- `df['sell']`

它们应满足：

- 与 dataframe 长度完全一致
- `fillna(False)` 后为布尔值
- 除非你明确要连续触发，否则应尽量做成边缘触发

推荐模式：

```python
raw_buy = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
raw_sell = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))

df['buy'] = (raw_buy.fillna(False) & (~raw_buy.shift(1).fillna(False))).astype(bool)
df['sell'] = (raw_sell.fillna(False) & (~raw_sell.shift(1).fillna(False))).astype(bool)
```

这样可以避免同一段趋势里每根 bar 都重复发信号。

#### 3.3.1 `tradeDirection` 与 `buy` / `sell` 在引擎里如何解释

指标保存为策略后，后端会把 `df['buy']` / `df['sell']` 规范成执行信号。请按下面表格理解，**不要**在 `both` 模式下把 `buy` 当成“单独的平空列”：

| `tradeDirection` | `buy=True` | `sell=True` |
|------------------|------------|-------------|
| `long` | 开多 `open_long` | 平多 `close_long` |
| `short` | 平空 `close_short` | 开空 `open_short` |
| `both` | 开多 `open_long`；若当前持空则**先平空再开多** | 开空 `open_short`；若当前持多则**先平多再开空** |

要点：

- `both` 与回测 `BacktestService` 的 `_both_mode` 语义一致；实盘不应再拆出独立的 `close_short` / `close_long` 去“辅助”表达平仓。
- 若你把「空侧止盈 / 空侧止损」写进 `df['buy']`，在 `both` 下表达的是**退出空头并可能反手做多**，不是“只平空、保持空仓”。
- 若只想平仓、不想反手，请改用 `tradeDirection long/short`、四路布尔列（`open_long` / `close_long` / …），或迁移到 `ScriptStrategy` 用 `ctx.close_position()`。

常见组合写法（双向肯特纳类策略）：

```python
df['buy']  = sig_buy_entry | sig_short_tp | sig_short_sl
df['sell'] = sig_sell_entry | sig_long_tp | sig_long_sl
```

写代码时可以这样自检：回测成交里若出现「平空后立刻开多」，通常就是 `both` 下 `buy=True` 的预期行为，而不是引擎 bug。

### 3.4 第四步：先决定“谁负责退出”

止盈止损和仓位管理最容易在这里写乱。

在 `IndicatorStrategy` 里，退出逻辑通常有两种合法写法，并且必须通过头部契约声明清楚。

#### 写法 A：信号自己负责退出

也就是由你的指标逻辑直接生成 `df['sell']`。

典型例子：

- 均线死叉
- RSI 跌破阈值
- 收盘价跌破 ATR 止损线
- 均值回归到目标位后离场

如果退出本身就是策略思想的一部分，用这种写法最自然。

这种写法请声明：

```python
# exit_owner: indicator
# @strategy trailingEnabled false
```

当前后端会把 `exit_owner: indicator` 解释为：服务端固定止损、固定止盈、追踪止损都不主动平仓；退出以指标信号为准。`entryPct` / `tradeDirection` 仍然可以继续作为默认配置使用。

#### 写法 B：引擎负责固定止盈止损

也就是你只定义默认配置，由引擎按固定规则处理：

- `stopLossPct`
- `takeProfitPct`
- `entryPct`
- trailing 系列参数

如果你的信号逻辑想保持简洁，而保护性规则是固定的，就用这种写法。

这种写法请声明：

```python
# exit_owner: engine
```

`exit_owner: engine` 表示服务端价格风控有效。代码里可以保留“趋势反转平仓”这类结构性 `close_*`，但不要再把窄止盈/窄止损触及条件也写进指标信号里。

#### 最佳实践

尽量明确一个“主退出来源”。

例如：

- 如果你的核心逻辑是“金叉进，死叉出”，且没有额外固定价格风控，那退出就主要由指标信号负责，写 `# exit_owner: indicator`
- 如果你的逻辑是“信号进场，固定 3% 止损 + 6% 止盈管理交易”，或只把 `close_*` 当作趋势反转时的结构性平仓，那价格退出主要由引擎负责，写 `# exit_owner: engine`

不要写 `exit_owner: layered`。当前平台未实现第三种退出负责人；如果你确实要混合方案，先按 `engine` 写并在评审里说明哪些 `close_*` 只是趋势反转，不是窄 tp/sl。

### 3.5 第五步：最后再组装 `output`

脚本最后必须赋值 `output`：

```python
output = {
    "name": "My Strategy",
    "plots": [],
    "signals": []
}
```

主要支持键：

- `name`
- `plots`
- `signals`
- `calculatedVars`：可选元数据

每个 `plot` 项通常包含：

- `name`
- `data`，长度必须等于 `len(df)`
- `color`
- `overlay`
- 可选 `type`

每个 `signal` 项通常包含：

- `type`：`buy` 或 `sell`
- `text`
- `color`
- `data`：无信号的 bar 用 `None`

### 3.6 第六步：校验回测语义

指标回测是典型的信号驱动：

- 引擎读取 `df['buy']` 和 `df['sell']`
- 信号按 bar close 确认
- 通常在**下一根 bar 开盘价**成交

这件事非常重要，因为：

- 你在当前 K 线上画出来的“止损线”不等于系统一定按这根 K 线内部价格成交
- 一旦用了 `shift(-1)`，就基本等于引入未来函数

还要注意一个实现细节：

- 标准工作流下，最常见的成交语义仍然是“收盘确认、下一根开盘成交”
- 但保存后的策略快照，会根据产品配置被规范成 `next_bar_open` 或 `same_bar_close`
- 如果你改了成交时机配置，不要凭印象判断结果，要重新回测并核对成交明细

#### 3.6.1 实盘与回测为什么会在“开平仓时间”上差很多

| 维度 | 指标回测 | 指标实盘（默认） |
|------|----------|------------------|
| K 线 | 历史**已收盘** OHLC | 会用最新价**刷新未收盘 K** 的 `high` / `low` / `close` |
| 盘中触及 | 只在整根 K 收盘后才知道 high/low | 未收盘 K 上也可能提前触发「触及中轨 / 外轨」类条件 |
| 信号检查 | 按回测时间轴逐根推进 | 每个 tick 可能重算指标；`exit_signal_mode=immediate` 时平仓类信号可立即下单 |
| 每 tick 下单 | 按回测队列顺序 | 指标模式通常**每 tick 最多执行 1 个**信号（优先平仓） |

若策略逻辑依赖 `high >= 某线`、`low <= 某线` 这类**盘中触及**，回测往往比实盘**更晚**才出现信号；若未收盘 K 被实时刷新，实盘可能**更早**止盈/止损。

建议（与回测对齐时）：

- 策略配置里将 `signal_mode`、`exit_signal_mode` 设为 **`confirmed`**（只在上一根已收盘 K 上读信号）。
- 指标内已用 `sig_*_tp` / `sig_*_sl` 表达退出时，声明 `# exit_owner: indicator` 并保持 `# @strategy trailingEnabled false`，否则会出现「指标退出 + 服务端追踪止损」**双重平仓**，并放大时间差。
- 上线前对比：回测成交时间、实盘日志里的 `Signal submitted` / `server_trailing_stop` 是否成对出现。

实盘平仓若短暂出现「数量为 0」：执行器会**再次同步交易所持仓并重新解析数量**；若交易所确已空仓，才会拒单（例如移动止损已先平掉）。

---

## 4. 止盈止损和仓位管理到底怎么写

这一节就是给开发者的直接答案。

### 4.1 在 IndicatorStrategy 里写固定止损、止盈、仓位

如果你想要的是固定默认配置，就写成 `# @strategy`：

```python
# @strategy stopLossPct 0.03
# @strategy takeProfitPct 0.06
# @strategy entryPct 0.25
# @strategy tradeDirection long
```

含义分别是：

- `stopLossPct 0.03`：默认 3% 止损
- `takeProfitPct 0.06`：默认 6% 止盈
- `entryPct 0.25`：默认用 25% 资金开仓
- `tradeDirection long`：默认只做多

这种写法适合：

- 信号代码尽量简单
- 希望回测时能直接读懂默认风险参数
- 希望 UI 和引擎都能直接识别这些默认值

### 4.2 在 IndicatorStrategy 里写“指标驱动型止损”

如果你的“止损”本质上是策略逻辑的一部分，那就不要假装成外部配置，而是直接写进 `sell` 信号。

例如：跌破 ATR 风格止损线就卖出。

```python
atr = (df['high'] - df['low']).rolling(14).mean()
stop_line = df['close'].rolling(20).max() - atr * 2.0

raw_sell = df['close'] < stop_line.shift(1)
df['sell'] = (raw_sell.fillna(False) & (~raw_sell.shift(1).fillna(False))).astype(bool)
```

这种写法表示：

- 退出属于你的指标逻辑
- 引擎不是替你“发明”一个止损
- 你最好在描述或注释里说明这一点

### 4.3 IndicatorStrategy 里的仓位管理边界

对 `IndicatorStrategy` 来说，仓位管理应该尽量保持简单：

- 用 `entryPct` 管默认开仓资金占比
- 用 `tradeDirection` 管做多 / 做空 / 双向
- 用固定的止损止盈或 trailing 默认值做保护

如果你需要下面这些能力：

- 分批加仓、减仓
- 部分止盈
- 开仓前后用不同逻辑
- 止损线会跟随当前持仓状态动态变化
- 止损后冷却一段时间再重入

那就说明这套逻辑已经超出 `IndicatorStrategy` 该承担的范围，应该迁移到 `ScriptStrategy`。

---

## 5. 完整的 IndicatorStrategy 示例

下面这个例子展示了一个更符合开发者思维的完整结构：元数据、默认配置、指标计算、信号生成、图表输出分层清楚。

```python
my_indicator_name = "EMA Pullback Strategy"
my_indicator_description = "Buy pullbacks above the slow EMA and exit on trend failure."

# @param fast_len int 20 Fast EMA length
# @param slow_len int 50 Slow EMA length
# @param rsi_len int 14 RSI length
# @param rsi_floor float 50 Minimum RSI for entry

# @strategy stopLossPct 0.03
# @strategy takeProfitPct 0.06
# @strategy entryPct 0.25
# @strategy tradeDirection long

df = df.copy()

fast_len = int(params.get('fast_len', 20))
slow_len = int(params.get('slow_len', 50))
rsi_len = int(params.get('rsi_len', 14))
rsi_floor = float(params.get('rsi_floor', 50.0))

ema_fast = df['close'].ewm(span=fast_len, adjust=False).mean()
ema_slow = df['close'].ewm(span=slow_len, adjust=False).mean()

delta = df['close'].diff()
gain = delta.clip(lower=0).ewm(alpha=1 / rsi_len, adjust=False).mean()
loss = (-delta.clip(upper=0)).ewm(alpha=1 / rsi_len, adjust=False).mean()
rs = gain / loss.replace(0, np.nan)
rsi = 100 - (100 / (1 + rs))

trend_up = ema_fast > ema_slow
pullback_done = df['close'] > ema_fast
rsi_ok = rsi > rsi_floor

raw_buy = trend_up & pullback_done & rsi_ok & (~trend_up.shift(1).fillna(False))
raw_sell = (ema_fast < ema_slow) | (rsi < 45)

buy = (raw_buy.fillna(False) & (~raw_buy.shift(1).fillna(False))).astype(bool)
sell = (raw_sell.fillna(False) & (~raw_sell.shift(1).fillna(False))).astype(bool)

df['buy'] = buy
df['sell'] = sell

buy_marks = [df['low'].iloc[i] * 0.995 if buy.iloc[i] else None for i in range(len(df))]
sell_marks = [df['high'].iloc[i] * 1.005 if sell.iloc[i] else None for i in range(len(df))]

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "EMA Fast",
            "data": ema_fast.fillna(0).tolist(),
            "color": "#1890ff",
            "overlay": True
        },
        {
            "name": "EMA Slow",
            "data": ema_slow.fillna(0).tolist(),
            "color": "#faad14",
            "overlay": True
        },
        {
            "name": "RSI",
            "data": rsi.fillna(0).tolist(),
            "color": "#722ed1",
            "overlay": False
        }
    ],
    "signals": [
        {
            "type": "buy",
            "text": "B",
            "data": buy_marks,
            "color": "#00E676"
        },
        {
            "type": "sell",
            "text": "S",
            "data": sell_marks,
            "color": "#FF5252"
        }
    ]
}
```

这个例子刻意强调了几件事：

- 先算指标，再出信号
- 进出场逻辑通过布尔列表达
- 固定风险默认值通过 `# @strategy` 单独声明
- 图表输出是最后一步，不要和信号逻辑搅在一起

### 5.1 一个更贴近平台 UI 的示例

下面这个版本更接近 QuantDinger 当前真实使用方式：

- 用 `# @param` 暴露常调参数
- 用 `# @strategy` 暴露默认止损、止盈、仓位和跟踪止损
- 显式声明 `tradeDirection`，让代码、保存后的策略、回测面板保持一致
- 杠杆仍然留给产品 UI 管，不写进源码

```python
my_indicator_name = "Breakout Retest With Direction Control"
my_indicator_description = "Breakout-and-retest logic with platform-friendly params and default risk settings."

# signal_form: two_way
# exit_owner: engine
# flip_mode: R2

# @param breakout_len int 20 Breakout lookback bars
# @param retest_buffer float 0.002 Retest tolerance ratio
# @param volume_mult float 1.2 Minimum volume filter
# @param ema_filter_len int 50 Trend filter EMA length

# @strategy stopLossPct 0.02
# @strategy takeProfitPct 0.05
# @strategy entryPct 0.2
# @strategy trailingEnabled true
# @strategy trailingStopPct 0.015
# @strategy trailingActivationPct 0.03
# @strategy tradeDirection both

df = df.copy()

breakout_len = int(params.get('breakout_len', 20))
retest_buffer = float(params.get('retest_buffer', 0.002))
volume_mult = float(params.get('volume_mult', 1.2))
ema_filter_len = int(params.get('ema_filter_len', 50))

ema_filter = df['close'].ewm(span=ema_filter_len, adjust=False).mean()
range_high = df['high'].rolling(breakout_len).max().shift(1)
range_low = df['low'].rolling(breakout_len).min().shift(1)
volume_avg = df['volume'].rolling(breakout_len).mean()

long_breakout = df['close'] > range_high
long_retest_ok = df['low'] <= range_high * (1 + retest_buffer)
long_volume_ok = df['volume'] >= volume_avg * volume_mult
long_trend_ok = df['close'] > ema_filter

short_breakout = df['close'] < range_low
short_retest_ok = df['high'] >= range_low * (1 - retest_buffer)
short_volume_ok = df['volume'] >= volume_avg * volume_mult
short_trend_ok = df['close'] < ema_filter

raw_buy = long_breakout & long_retest_ok & long_volume_ok & long_trend_ok
raw_sell = short_breakout & short_retest_ok & short_volume_ok & short_trend_ok

buy = (raw_buy.fillna(False) & (~raw_buy.shift(1).fillna(False))).astype(bool)
sell = (raw_sell.fillna(False) & (~raw_sell.shift(1).fillna(False))).astype(bool)

df['buy'] = buy
df['sell'] = sell

buy_marks = [df['low'].iloc[i] * 0.995 if buy.iloc[i] else None for i in range(len(df))]
sell_marks = [df['high'].iloc[i] * 1.005 if sell.iloc[i] else None for i in range(len(df))]

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "EMA Filter",
            "data": ema_filter.fillna(0).tolist(),
            "color": "#1890ff",
            "overlay": True
        },
        {
            "name": "Range High",
            "data": range_high.fillna(0).tolist(),
            "color": "#52c41a",
            "overlay": True
        },
        {
            "name": "Range Low",
            "data": range_low.fillna(0).tolist(),
            "color": "#f5222d",
            "overlay": True
        }
    ],
    "signals": [
        {
            "type": "buy",
            "text": "L",
            "data": buy_marks,
            "color": "#00E676"
        },
        {
            "type": "sell",
            "text": "S",
            "data": sell_marks,
            "color": "#FF5252"
        }
    ]
}
```

这个例子为什么更贴近平台：

- `# @param` 的值可以直接被 AI 调参或手动参数修改流程接管
- `# @strategy` 能和保存后的策略默认值、右侧回测面板风险配置更自然地对齐
- `tradeDirection both` 让人一眼看出这份代码本身就是为多空双向设计的
- 杠杆继续交给产品配置层，不会被藏进源码里造成误解

---

## 6. 什么时候该切到 ScriptStrategy

当策略需要“运行时状态”而不是“纯 dataframe 信号”时，就该迁移到 `ScriptStrategy`。

典型信号包括：

- 止损止盈依赖当前持仓，而不是仅依赖历史序列
- 开仓后要动态移动止损
- 需要部分平仓或加仓
- 首次开仓和再次开仓逻辑不同
- 需要冷却期、节流、bot 风格执行规则

### 6.1 必需函数

面向当前产品链路，最稳妥的约定是：

- `def on_init(ctx): ...`
- `def on_bar(ctx, bar): ...`

原因是：

- 运行时编译器真正强制的是 `on_bar`
- 但部分产品侧校验路径仍然要求源码里同时存在 `on_init` 和 `on_bar`
- 为了避免“运行时能跑、校验却不过”的不一致，建议两个函数都写，即使 `on_init` 只是做初始化或打印日志

### 6.2 可用对象

`bar` 通常提供：

- `bar.open`
- `bar.high`
- `bar.low`
- `bar.close`
- `bar.volume`
- `bar.timestamp`

`ctx` 当前通常提供：

- `ctx.param(name, default=None)`
- `ctx.bars(n=1)`
- `ctx.position`
- `ctx.balance`
- `ctx.equity`
- `ctx.log(message)`
- `ctx.buy(price=None, amount=None)`
- `ctx.sell(price=None, amount=None)`
- `ctx.close_position()`

补充说明：

- `ctx` 不会直接把完整交易配置对象暴露给脚本
- 杠杆、交易标的、交易场所、账户凭证等，应放在产品配置层，而不是写死在脚本里
- 脚本源码内部需要的默认参数，优先通过 `ctx.param(...)` 管理

`ctx.position` 同时支持数值判断和字段访问，例如：

```python
if not ctx.position:
    ...

if ctx.position > 0:
    ...

if ctx.position["side"] == "long":
    ...
```

### 6.3 一个带运行时退出的 ScriptStrategy 示例

```python
def on_init(ctx):
    ctx.log("strategy initialized")


def on_bar(ctx, bar):
    stop_loss_pct = ctx.param("stop_loss_pct", 0.03)
    take_profit_pct = ctx.param("take_profit_pct", 0.06)
    order_amount = ctx.param("order_amount", 1)

    bars = ctx.bars(30)
    if len(bars) < 20:
        return

    closes = [b.close for b in bars]
    ma_fast = sum(closes[-10:]) / 10
    ma_slow = sum(closes[-20:]) / 20

    if not ctx.position and ma_fast > ma_slow:
        ctx.buy(price=bar.close, amount=order_amount)
        return

    if not ctx.position:
        return

    if ctx.position["side"] != "long":
        return

    entry_price = ctx.position["entry_price"]

    if bar.close <= entry_price * (1 - stop_loss_pct):
        ctx.close_position()
        return

    if bar.close >= entry_price * (1 + take_profit_pct):
        ctx.close_position()
        return

    if ma_fast < ma_slow:
        ctx.close_position()
```

这种写法适合“止盈止损属于运行时持仓管理”的场景，而不是单纯图表信号输出。

仓位语义还要补一句：

- 在当前系统里，保存后的策略回测，仓位大小仍然主要由规范化后的交易配置决定，例如 `entryPct`
- 因此 `ctx.buy()` / `ctx.sell()` 里的 `amount` 更适合理解成运行时下单意图，而不是回测仓位的唯一来源
- 真正准备上模拟盘或实盘前，一定要先通过“保存后的策略回测”核对实际仓位暴露

### 6.4 普通脚本模式与 bot 模式

大多数 `ScriptStrategy` 都运行在**已收盘 K 线**语义下：

- 引擎会在 bar 确认收盘后调用 `on_bar(ctx, bar)`
- 这也是普通策略回测和逐 bar 实盘最接近的心智模型

当前系统里还存在 bot 风格运行模式：

- bot 模式下，系统可能会基于最新价格构造“类 tick 的伪 bar”反复调用 `on_bar`
- 这种模式更适合网格、DCA 或其他更偏机器人执行的策略
- 如果你的脚本是为 bot 模式设计的，应该和标准 bar-close 策略分开测试，不要混为一谈

### 6.5 一个更贴近平台实盘的 ScriptStrategy 示例

下面这个例子更接近平台里真实可落地的实盘脚本写法：

- 用 `ctx.param(...)` 管脚本级默认参数
- 先看 `ctx.position`，再决定是开仓、反手、减仓还是全部平仓
- 用 `ctx.buy()` / `ctx.sell()` 表达方向性下单意图
- 当你的语义是“现在全部退出”时，用 `ctx.close_position()` 最明确

```python
def on_init(ctx):
    ctx.log("live strategy initialized")


def on_bar(ctx, bar):
    fast_len = int(ctx.param("fast_len", 10))
    slow_len = int(ctx.param("slow_len", 30))
    risk_pct = float(ctx.param("risk_pct", 0.25))
    stop_loss_pct = float(ctx.param("stop_loss_pct", 0.02))
    take_profit_pct = float(ctx.param("take_profit_pct", 0.05))
    allow_short = bool(ctx.param("allow_short", True))

    bars = ctx.bars(slow_len + 5)
    if len(bars) < slow_len:
        return

    closes = [b.close for b in bars]
    fast_ma = sum(closes[-fast_len:]) / fast_len
    slow_ma = sum(closes[-slow_len:]) / slow_len
    price = bar.close

    if not ctx.position:
        if fast_ma > slow_ma:
            ctx.buy(price=price, amount=risk_pct)
            return
        if allow_short and fast_ma < slow_ma:
            ctx.sell(price=price, amount=risk_pct)
            return
        return

    if ctx.position["side"] == "long":
        entry_price = float(ctx.position["entry_price"])
        if price <= entry_price * (1 - stop_loss_pct):
            ctx.close_position()
            return
        if price >= entry_price * (1 + take_profit_pct):
            ctx.close_position()
            return
        if allow_short and fast_ma < slow_ma:
            ctx.sell(price=price, amount=risk_pct)
            return

    if ctx.position["side"] == "short":
        entry_price = float(ctx.position["entry_price"])
        if price >= entry_price * (1 + stop_loss_pct):
            ctx.close_position()
            return
        if price <= entry_price * (1 - take_profit_pct):
            ctx.close_position()
            return
        if fast_ma > slow_ma:
            ctx.buy(price=price, amount=risk_pct)
            return
```

这个例子重点演示了：

- `ctx.param(...)` 让脚本默认值集中且清晰
- `ctx.position` 决定当前是空仓、做多还是做空分支
- `ctx.buy()` / `ctx.sell()` 表达的是方向性意图，不只是孤立的“开多”或“开空”
- 当规则的语义是“现在全部退出”时，`ctx.close_position()` 最不容易产生歧义

下面这些回测 / 实盘差异一定要记住：

- 标准脚本回测和普通实盘模式都以“已确认收盘的 bar”为核心，但 bot 模式可能会用类 tick 的伪 bar 反复驱动脚本
- `amount` 更适合理解成运行时下单意图；保存后的策略回测，仓位大小仍然主要受 `entryPct` 这类规范化交易配置影响
- 当你在多头持仓中调用 `ctx.sell()`，或在空头持仓中调用 `ctx.buy()` 时，实际效果可能会根据运行时状态与产品配置表现为“先平后反手”一类意图
- 如果你要的是明确的“全部平仓”，优先用 `ctx.close_position()`，不要依赖隐式解释

---

## 7. 回测、持久化与当前限制

保存后的策略会被后端解析成统一快照，再进入回测或执行链路。常见字段包括：

- `strategy_type`
- `strategy_mode`
- `strategy_code`
- `indicator_config`
- `trading_config`

当前常见 `run_type` 包括：

- `indicator`
- `strategy_indicator`
- `strategy_script`

当前限制包括：

- `cross_sectional` 在当前策略快照链路中不支持
- `ScriptStrategy` 当前不支持 `cross_sectional` 实盘运行
- 脚本策略回测当前不会走指标侧的 MTF 执行路径
- 策略回测要求 symbol 合法且代码非空

### 7.1 脚本策略回测的成交假设（与指标「严格模式」不同）

脚本策略回测**没有**指标 IDE 里的「严格 / 非严格」开关。UI 上应显示为：

**脚本标准回测 · 逐 bar · 下一根开盘成交**

含义：

1. 按策略周期（如 5m）逐根拉 K 线，每根已收盘 bar 调用一次 `on_bar(ctx, bar)`。
2. 脚本在当根 bar 内通过 `ctx.buy()` / `ctx.sell()` / `ctx.close_position()` 表达下单意图。
3. 撮合层默认把成交推到**下一根 K 线开盘价**（并计滑点/手续费），与 `trading_config.execution.signalTiming = next_bar_open` 一致。
4. 仓位大小仍主要受 `entryPct` 等交易配置约束，而不是脚本里 `amount` 的唯一来源。

这与 **IndicatorStrategy** 的严格模式（整表布尔信号 + 可选 MTF 子周期）是两条不同路径，不要混为一谈。

### 7.2 交易机器人 vs 脚本策略 vs 指标策略

| 类型 | 代码存放 | 典型入口 | 说明 |
|------|----------|----------|------|
| **IndicatorStrategy** | `qd_indicator_codes.code` | 指标 IDE | `df` + `buy/sell` 信号，适合研究与信号型回测 |
| **ScriptStrategy** | `qd_strategies_trading.strategy_code` | 策略工作室 `/strategy-script` | `on_init` + `on_bar`，适合有状态逻辑 |
| **Trading Bot** | 同上 `strategy_code`，`strategy_mode=bot` | 交易机器人向导 | 参数化模板；Live 网格等由专用引擎执行 |

**「克隆为脚本」**（机器人详情页）会把 `strategy_code` 复制为新的 **ScriptStrategy**，跳转到 **策略工作室** 编辑，**不会**写入指标 IDE 的 `indicator_code`。

- **网格机器人** 的 `strategy_code` 只是占位脚本（`on_bar: pass`），真实逻辑在 Live 挂单引擎；克隆后编辑器里看起来「几乎为空」是预期行为，不是 bug。
- **马丁 / 趋势 / DCA** 等机器人会生成完整 Python 模板，克隆后应能看到可编辑代码。
- 若克隆后在策略工作室仍看不到代码，请刷新页面；新版本会在编辑时调用 `/api/strategies/detail` 拉取完整 `strategy_code`。

---

## 8. 最佳实践

### 8.1 始终避免未来函数

- 只使用已完成 bar 的信息
- 优先使用 `shift(1)` 做确认
- 不要在信号逻辑中使用 `shift(-1)`

### 8.2 显式处理 NaN

滚动窗口和 EWM 都会产生前导 NaN，生成信号前必须先清理。

### 8.3 保持所有序列长度一致

所有 `plot['data']` 和 `signal['data']` 都必须与 `len(df)` 完全一致。

### 8.4 IndicatorStrategy 尽量保持向量化

核心指标计算优先用 pandas 原生向量化逻辑，不要把主逻辑写成逐行循环。

### 8.5 ScriptStrategy 保持确定性

`ScriptStrategy` 尽量避免 `ctx` 外部的隐式状态、随机行为，以及含糊不清的下单意图。

### 8.6 把配置放在正确层级

- 指标型默认值用 `# @param` 和 `# @strategy`
- 脚本型默认值优先用 `ctx.param()`
- 杠杆、成交时机、交易所和账户凭证放在产品配置层，不要硬编码

---

## 9. 故障排除

### 9.1 `column "strategy_mode" does not exist`

说明数据库结构版本落后于当前代码，需要对 `qd_strategies_trading` 执行对应迁移。

### 9.2 `Strategy script must define on_bar(ctx, bar)`

说明 `ScriptStrategy` 缺少必需的 `on_bar`。

### 9.3 `Missing required functions: on_init, on_bar`

说明当前 UI 校验器要求源码里同时存在这两个函数。

### 9.4 `Strategy code is empty and cannot be backtested`

说明保存后的策略在当前模式下没有有效代码。

### 9.5 图表长度不一致

说明某个 plot 或 signal 的数组长度没有和 `df` 对齐。

### 9.6 回测结果很奇怪

优先检查这几件事：

- 有没有误用未来数据
- `buy` / `sell` 是否做成了边缘触发
- 是否同时混用了“信号退出”和“引擎退出”却没有说明清楚
- `# @strategy` 默认值是否真的符合策略风格

### 9.7 后端日志排查

如果策略创建、校验、回测或执行失败，请优先查后端日志。常见问题包括：

- 数据库结构不匹配
- JSON / 配置载荷格式错误
- 代码校验失败
- 市场 / symbol 不匹配
- 交易所凭证或配置异常

---

## 10. 完整工作流：从 Indicator IDE 到保存成策略再到实盘

这是目前最符合产品链路的实战流程。

### 10.1 先在 Indicator IDE 里做原型

在策略还处于构思阶段时，优先从 Indicator IDE 开始：

1. 先把指标逻辑写在 `df` 上。
2. 用 `# @param` 声明可调参数。
3. 用 `# @strategy` 声明默认止损、止盈、仓位和方向。
4. 补齐图表展示需要的 `plots` 和 `signals`。
5. 先跑指标侧回测，确认信号密度、图表观感和成交语义都合理。

这个阶段的目标不是立刻上实盘，而是先把策略逻辑变得可见、可测、可迭代。

### 10.2 在产品里调参与校验

当指标逻辑已经基本正确后：

1. 先跑代码质量检查，排除缺少元数据或明显可疑写法。
2. 用真实的标的、周期、手续费、滑点、杠杆配置去跑回测。
3. 必要时使用 AI 调参或结构化扫参比较参数组合。
4. 把最终采用的参数重新写回源码，让代码本身仍然是最直观的“单一真相”。

这里推荐的分工方式是：

- 信号逻辑由代码表达
- `# @param` 和 `# @strategy` 表达默认参数和风控
- 市场、杠杆、日期区间、执行环境由产品面板管理

### 10.3 把指标保存成策略

当信号模型稳定后：

1. 先保存当前指标代码。
2. 通过产品流程创建或保存策略记录。
3. 确认保存后的策略快照模式、默认值和交易配置都符合预期。
4. 从持久化后的策略记录发起策略回测，而不是只看编辑器里的即时结果。

这一步很关键，因为：

- 保存后的策略回测更接近真实执行链路
- 规范化快照可能会补齐成交时机和交易配置默认值
- 很多 symbol、模式、配置、持久化层面的错配，都是在这一步才暴露出来

### 10.4 判断 IndicatorStrategy 是否已经够用

如果满足下面这些条件，就继续保留 `IndicatorStrategy`：

- 进出场核心仍然主要靠信号驱动
- 固定止损、止盈、跟踪止损默认值已经足够
- 不需要依赖当前持仓状态做复杂运行时逻辑

如果出现下面这些需求，就应升级成 `ScriptStrategy`：

- 开仓后要持续盯着持仓逐根处理
- 退出逻辑依赖当前持仓状态，而不只是历史序列
- 需要冷却期、分批止盈、加仓、减仓、机器人式执行逻辑

### 10.5 进入模拟盘或实盘前的最后检查

在真正开启实盘前，至少确认：

1. 交易所 / 经纪商 / 标的 / 凭证配置正确。
2. 对成交时机的假设已经再次核实（`signal_mode` / `exit_signal_mode` 是否与回测一致，见 §3.6.1）。
3. `tradeDirection both` 时已理解 `buy` / `sell` 的反手语义（§3.3.1），指标内 tp/sl 与 `trailingEnabled` 未重复（§11.7）。
4. 杠杆、方向、仓位大小放在了正确的产品配置层。
5. 先用保守仓位、小范围标的做验证。
6. 观察运行日志和真实下单行为（含 `server_trailing_stop`、拒单原因），再决定是否放大规模。

实盘不是编辑器实验的自然延伸，而是一个单独的验证阶段。

---

## 11. 常见错误示例 vs 正确写法

这一节专门列出最容易把回测做“虚高”、把策略行为写混乱、或者把产品配置和策略代码搞串的高频坑点。

### 11.1 声明了 `# @param`，却根本没读取

错误写法：

```python
# @param fast_len int 20 Fast EMA length

df = df.copy()
fast_len = 20
ema_fast = df['close'].ewm(span=fast_len, adjust=False).mean()
```

正确写法：

```python
# @param fast_len int 20 Fast EMA length

df = df.copy()
fast_len = int(params.get('fast_len', 20))
ema_fast = df['close'].ewm(span=fast_len, adjust=False).mean()
```

为什么：

- 只声明不读取，会让参数变成“看起来能调，实际上调不动”
- 平台代码质量检查也可能对此给出提醒

### 11.2 `buy` / `sell` 每根 bar 都在触发

错误写法：

```python
df['buy'] = df['close'] > ema_fast
df['sell'] = df['close'] < ema_fast
```

正确写法：

```python
raw_buy = df['close'] > ema_fast
raw_sell = df['close'] < ema_fast

df['buy'] = (raw_buy.fillna(False) & (~raw_buy.shift(1).fillna(False))).astype(bool)
df['sell'] = (raw_sell.fillna(False) & (~raw_sell.shift(1).fillna(False))).astype(bool)
```

为什么：

- 如果每根 bar 都重复发信号，进出场、标记、回测解释都会变得混乱
- 大多数策略真正想表达的是“条件刚刚成立时触发一次”，而不是“条件持续成立就一直触发”

### 11.3 把杠杆写进策略源码

错误写法：

```python
# @strategy leverage 10
```

正确写法：

```python
# @strategy entryPct 0.2
# @strategy stopLossPct 0.02
# @strategy takeProfitPct 0.05
```

然后把杠杆放到产品面板或保存后的策略交易配置里。

为什么：

- 杠杆属于执行配置，不属于指标元数据
- 把杠杆藏进代码里，会让回测解释更混乱，也更容易和产品侧配置打架

### 11.4 误用 `shift(-1)`，把未来函数写进策略

错误写法：

```python
df['buy'] = (df['close'].shift(-1) > ema_fast).fillna(False)
```

正确写法：

```python
raw_buy = df['close'] > ema_fast
df['buy'] = (raw_buy.fillna(False) & (~raw_buy.shift(1).fillna(False))).astype(bool)
```

为什么：

- `shift(-1)` 本质上是在偷看未来数据
- 这类策略在回测里往往会“好得离谱”，但一到真实执行就失真

### 11.5 在 `ScriptStrategy` 里把 `amount` 当成绝对回测仓位

错误心智模型：

```python
ctx.buy(price=bar.close, amount=1.0)
```

“这就等于回测里永远按 100% 仓位开仓。”

正确心智模型：

```python
position_pct = float(ctx.param("risk_pct", 0.25))
ctx.buy(price=bar.close, amount=position_pct)
```

然后再用保存后的策略回测去核对规范化交易配置。

为什么：

- 在当前系统里，保存后的策略回测，仓位大小仍然主要由 `entryPct` 这类规范化配置决定
- `amount` 更适合理解成运行时下单意图，而不是历史回测仓位的唯一真相

### 11.6 明明想“全部平仓”，却写成了 `ctx.sell()` / `ctx.buy()`

容易歧义的写法：

```python
if stop_hit:
    ctx.sell(price=bar.close, amount=0.25)
```

更清晰的写法：

```python
if stop_hit:
    ctx.close_position()
```

为什么：

- `ctx.buy()` / `ctx.sell()` 表达的是方向性意图，最终效果会结合当前持仓状态解释
- 如果你的规则语义就是“现在全部退出”，`ctx.close_position()` 最不容易被误解

### 11.7 信号退出和引擎退出混着用，却没写说明

容易出问题的写法：

```python
# @strategy stopLossPct 0.02
# @strategy takeProfitPct 0.05
# @strategy trailingEnabled true

df['sell'] = some_other_exit_condition  # 指标内多侧止盈
# 同时 buy 里还有 sig_short_tp / sig_short_sl …
```

更好的写法（二选一，并在注释里写清楚）：

```python
# 方案 A：退出全部由指标信号负责（推荐用于中轨/轨道触及类策略）
# exit_owner: indicator
# @strategy trailingEnabled false
# Primary exit: close_* 或 df['buy'] / df['sell'] 内的 tp/sl 条件
# stopLossPct / takeProfitPct / trailing* 不作为服务端退出

# 方案 B：退出由引擎风控负责（固定止损/止盈/移动止损）
# exit_owner: engine
# @strategy trailingEnabled true
# @strategy trailingStopPct 0.0025
# @strategy trailingActivationPct 0.0037
# df['buy'] / df['sell'] 或 open_* 只负责入场；结构性反转 close_* 可以保留，窄 tp/sl 不要再写进指标
```

为什么：

- 两种退出方式并存时，回测与实盘都会执行**先到为准**的那条路径，日志里可能出现 `server_trailing_stop` 与指标 `close_*` 紧挨着，甚至「平仓数量为 0」的拒单（仓位已被移动止损平掉）。
- `exit_owner: indicator` 时，后端会关闭服务端固定止损、固定止盈和追踪止损；`# @strategy` 里的这些退出参数不再生效。
- `exit_owner: engine` 时，服务端价格风控生效；指标里的 `close_*` 应只表达趋势反转等结构性退出，不要再塞一套窄 tp/sl。
- 真正的问题是没人说得清「主退出」是指标触及，还是 `# @strategy` 服务端风控；`exit_owner` 就是为了解决这个边界。

### 11.8 `both` 模式下把止盈/止损全塞进 `buy` / `sell`

容易误解的写法：

```python
df['buy']  = entry_long | short_tp | short_sl
df['sell'] = entry_short | long_tp | long_sl
# @strategy tradeDirection both
```

需要建立的心智模型：

- `short_tp` / `short_sl` 进入 `buy` 后，在 `both` 下不是「仅平空」，而是**平空并可能开多**（与回测一致）。
- 若你期望「空侧止盈后保持空仓」，当前 `IndicatorStrategy` + `both` **不适合**，应改 `tradeDirection`、四路信号或 `ScriptStrategy`。
- 实盘在 `exit_signal_mode=immediate` 时，这类退出可能比回测更早触发；要与回测对齐请用 `confirmed`（见 §3.6.1）。

### 11.9 实盘日志里 `invalid amount (0.0) for close_*`

常见原因：

1. 服务端止损/止盈/追踪止损已平仓，指标信号又提交了一次同向 `close_*`。
2. 本地持仓表滞后于交易所；执行器会先 **sync + 按交易所持仓重算数量**，仍为空才拒单。

处理建议：

- 按 §11.7 避免双重退出；检查是否应改成 `# exit_owner: indicator`，或是否同时启用了 `trailingEnabled` 与指标内 tp/sl。
- 确认 `tradeDirection both` 下对 `buy`/`sell` 的解释符合 §3.3.1，不要期待单独的 `close_short` 列。

---

## 12. 平台支持字段速查表

这一节可以当成“当前平台到底支持什么”的速查页，写策略时可以直接对着查。

### 12.1 `# @strategy` 支持的 key

| Key | 含义 | 常见写法 | 说明 |
|-----|------|----------|------|
| `stopLossPct` | 默认止损比例 | `# @strategy stopLossPct 0.02` | 引擎读取的默认风控配置 |
| `takeProfitPct` | 默认止盈比例 | `# @strategy takeProfitPct 0.05` | 底层解析器允许范围比示例更宽松 |
| `entryPct` | 默认开仓资金占比 | `# @strategy entryPct 0.25` | 是回测仓位的重要来源之一 |
| `trailingEnabled` | 是否开启跟踪止损 | `# @strategy trailingEnabled true` | 布尔值 |
| `trailingStopPct` | 跟踪止损比例 | `# @strategy trailingStopPct 0.015` | 通常与 trailing 一起使用 |
| `trailingActivationPct` | 启动跟踪止损前的盈利阈值 | `# @strategy trailingActivationPct 0.03` | 通常与 trailing 一起使用 |
| `tradeDirection` | 方向限制 | `# @strategy tradeDirection both` | 可选 `long`、`short`、`both` |

要点：

- 这些 key 用于指标侧默认策略配置
- **数值单位统一为 0–1 小数比例**（与 `StrategyConfigParser`、回测、实盘一致）
- **止损/止盈/追踪按标的涨跌幅计算，不除以杠杆**（杠杆只影响盈亏金额与爆仓）
- **`entryPct 1` 表示 100% 可用资金开仓**，不是 1%
- 若声明 `# exit_owner: indicator`，`stopLossPct` / `takeProfitPct` / `trailing*` 在回测和实盘中都不会触发服务端平仓；退出以指标信号为准
- 若声明 `# exit_owner: engine`，这些服务端价格风控参数才会参与平仓
- 不要把 `leverage` 写进 `# @strategy`
- 交易所、标的、凭证、杠杆都应该放在产品配置层

### 12.2 契约头注释

| Key | 支持值 | 含义 |
|-----|--------|------|
| `signal_form` | `two_way` / `four_way` | 执行信号形态 |
| `exit_owner` | `indicator` / `engine` | 价格退出负责人；当前不支持 `layered` |
| `flip_mode` | `R1` / `R2` | 反手时序：R1 下一根再开；R2 同 bar 先平后开 |

推荐写在文件顶部：

```python
# signal_form: four_way
# exit_owner: indicator
# flip_mode: R1
```

### 12.3 `# @param` 速查格式

| 部分 | 示例 | 含义 |
|------|------|------|
| 名称 | `fast_len` | 参数键名 |
| 类型 | `int` / `float` / `bool` / `str` / `string` | 支持的类型 |
| 默认值 | `20` | 系统看到的默认值 |
| 描述 | `Fast EMA length` | 给人看的说明 |

示例：

```python
# @param fast_len int 20 Fast EMA length
# @param allow_short bool true Allow short entries
```

随后用下面这种方式读取：

```python
fast_len = int(params.get('fast_len', 20))
allow_short = bool(params.get('allow_short', True))
```

### 12.3 `ScriptStrategy` 里的 `ctx` 支持什么

| 项目 | 类型 | 含义 |
|------|------|------|
| `ctx.param(name, default)` | 方法 | 读取或初始化脚本级默认参数 |
| `ctx.bars(n=1)` | 方法 | 取得当前运行时之前的最近若干根 bar |
| `ctx.log(message)` | 方法 | 写策略日志 |
| `ctx.buy(price=None, amount=None)` | 方法 | 表达买入 / 做多方向意图 |
| `ctx.sell(price=None, amount=None)` | 方法 | 表达卖出 / 做空方向意图 |
| `ctx.close_position()` | 方法 | 显式全部平仓 |
| `ctx.position` | 字段 | 当前持仓对象 |
| `ctx.balance` | 字段 | 当前余额快照 |
| `ctx.equity` | 字段 | 当前权益快照 |

`ctx.position` 常见字段：

| 字段 | 含义 |
|------|------|
| `side` | `long`、`short`，或空字符串表示空仓 |
| `size` | 当前持仓大小 |
| `entry_price` | 平均开仓价 |
| `direction` | `1`、`-1`、`0` |
| `amount` | 运行时数量镜像 |

### 12.4 `ScriptStrategy` 里的 `bar` 有哪些字段

| 字段 | 含义 |
|------|------|
| `bar.open` | 开盘价 |
| `bar.high` | 最高价 |
| `bar.low` | 最低价 |
| `bar.close` | 收盘价 |
| `bar.volume` | 成交量 |
| `bar.timestamp` | 当前运行时传入的时间值 |

### 12.5 `IndicatorStrategy` 的 `output` 允许哪些结构

顶层结构通常写成：

```python
output = {
    "name": my_indicator_name,
    "plots": [],
    "signals": [],
    "calculatedVars": {}
}
```

顶层常见 key：

| Key | 是否必需 | 含义 |
|-----|----------|------|
| `name` | 建议提供 | 展示名称 |
| `plots` | 建议提供 | 图表曲线输出 |
| `signals` | 建议提供 | 买卖点标记输出 |
| `calculatedVars` | 可选 | 额外元数据或计算结果 |

每个 `plot` 项常见字段：

| Key | 含义 |
|-----|------|
| `name` | 曲线名称 |
| `data` | 与 `len(df)` 对齐的数组 |
| `color` | 显示颜色 |
| `overlay` | 是否叠加在主图上 |
| `type` | 可选的渲染提示 |

每个 `signal` 项常见字段：

| Key | 含义 |
|-----|------|
| `type` | `buy` 或 `sell` |
| `text` | 标记文本 |
| `color` | 标记颜色 |
| `data` | 与 `len(df)` 对齐的数组；无信号位置用 `None` |

### 12.6 快速提醒

- `df['buy']` 和 `df['sell']` 应该是布尔值，并与 `df` 长度完全对齐
- 尽量使用边缘触发信号
- 不要在信号逻辑里使用 `shift(-1)`
- 当规则语义明显是“全部退出”时，优先用 `ctx.close_position()`
- `amount` 更适合作为运行时下单意图，最终仓位仍应通过保存后的策略回测核实

---

## 13. 推荐开发流程

1. 先用 `IndicatorStrategy` 把想法原型化。
2. 先验证图表、信号密度和 next-bar-open 的回测语义。
3. 把 `# @param` 和 `# @strategy` 元数据补完整。
4. 明确写清楚：退出到底是“信号负责”还是“引擎负责”。
5. 保存策略后，再从持久化记录跑策略回测。
6. 只有在确实需要运行时仓位管理时，再迁移到 `ScriptStrategy`。
7. 确认配置、凭证和市场语义都正确后，再进入模拟盘或实盘。

