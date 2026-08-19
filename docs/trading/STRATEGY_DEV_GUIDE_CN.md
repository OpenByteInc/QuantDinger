# Strategy API V2 策略开发指南

> 适用范围：当前 QuantDinger 可执行策略契约 Strategy API V2
> 面向读者：第一次编写策略的用户、指标转策略用户，以及需要同时覆盖回测与实盘的策略开发者

QuantDinger 只有一套当前可执行的 Python 策略契约：**Strategy API V2**。同一份源码会编译成策略清单，并由回测和实盘运行时共享标的、订阅、事件模型、订单意图、组合记账和保护规则。

策略源码拥有市场、标的、周期、调度和交易逻辑。单周期是默认模式；只有策略逻辑明确需要跨周期确认时，源码才声明多个周期。运行面板只提供日期、初始资金、交易成本、源码允许范围内的杠杆，以及用户参数；它不能改写源码声明的市场、标的或周期。

图表指标是另一种产物。指标中的 plots、signals 和 layers 不能下单，必须先转换成 Strategy API V2。

---

## 1. 快速开始：最小可运行策略

~~~python
"""SPY 20-Day Moving Average
Trades a long-only SPY regime from completed daily bars.
"""

# @param period int 20 Moving-average period range=5:100:5
# @param target_pct float 0.95 Target portfolio weight range=0.1:1.0:0.05


def initialize(context):
    g.symbol = "USStock:SPY"
    context.set_universe([g.symbol])
    context.subscribe(
        frequency="1d",
        fields=["open", "high", "low", "close", "volume"],
    )
    context.set_warmup(120)
    context.set_benchmark("USStock:SPY")


def handle_data(context, data):
    period = int(context.params.get("period", 20))
    target_pct = float(context.params.get("target_pct", 0.95))

    bars = get_history(
        period + 1,
        "1d",
        "close",
        g.symbol,
    )
    if len(bars) < period:
        return

    price = float(bars["close"].iloc[-1])
    average = float(bars["close"].tail(period).mean())
    position = get_position(g.symbol)
    desired = target_pct if price > average else 0.0

    if desired > 0 and position.amount <= 0:
        order_target_percent(
            g.symbol,
            desired,
            reason="ma_long_entry",
            stop_loss_pct=0.05,
        )
    elif desired == 0 and position.amount > 0:
        order_target_percent(
            g.symbol,
            0.0,
            reason="ma_long_exit",
        )
~~~

运行步骤：

1. 在策略 IDE 新建脚本并粘贴源码。
2. 保存源码。
3. 调用验证或在界面点击验证，确认编译清单正确。
4. 选择回测日期、初始资金、手续费、滑点和参数。
5. 检查成交、已平仓交易、订单审计、权益曲线和持仓快照。
6. 只有回测符合预期后才创建部署；新部署默认为停止状态。

---

## 2. 编译器硬性要求与编写规范

编译器硬性要求：

- 源码非空且能在安全沙箱中执行。
- 必须定义 <code>initialize(context)</code>。
- <code>initialize</code> 必须通过 <code>context.set_universe(...)</code> 声明静态标的、指数或命名股票池。
- 如果未显式订阅，编译器会创建默认日线订阅；教程仍建议始终显式调用 <code>context.subscribe</code>。
- 必须存在 <code>handle_data</code>、<code>on_rebalance</code>，或至少注册一个定时回调。
- 杠杆策略必须满足 Crypto swap 专用规则。

项目编写规范还要求：

- 文件以三引号 docstring 开头；第一行是策略名称，后续说明标的、信号、调度和风控。
- 标识符和源码注释使用英文。
- 参数和交易原因使用稳定、可审计的名称。
- 禁止未来数据、隐式反手、无界加仓和不受控仓位。

<code>initialize</code> 在编译/清单发现阶段执行，用于声明配置和初始化 <code>g</code>。不要在这里请求行情、读取真实仓位或下单。

与编译器直接相关的重要 API 规则：

- <code>initialize</code> 内不能读取 <code>context.params</code>；应在处理器或定时回调中读取。
- <code>get_history</code> 以数量为第一个参数，并使用 <code>field</code>：<code>get_history(count, frequency, field, symbol)</code>。不要向它传 <code>fields=</code>。
- <code>data.history</code> 是另一套接口：<code>data.history(symbols, count, fields)</code>。
- 单标的历史返回 DataFrame，多标的历史返回 DataFrame 字典。
- <code>get_position</code> 返回 Position 对象，不是字典。
- pandas DataFrame 或 Series 不能直接作为布尔条件；应使用 <code>len(...)</code>、<code>.empty</code>、<code>.any()</code> 或 <code>.all()</code>。
- 未定义的平台 API 和不支持的参数会在验证阶段直接拒绝，避免到实盘才失败。

---

## 3. 源码拥有的策略清单

编译后清单包含：

- API 版本与源码哈希；
- CTA 或 portfolio 类型；
- 静态/动态 universe；
- 订阅标的、全部周期、驱动周期和字段；
- 定时任务；
- benchmark；
- 生命周期处理器；
- 因子和基本面依赖；
- warm-up 数量；
- 是否允许杠杆及最大杠杆；
- 自定义 metadata。

验证接口：

~~~http
POST /api/strategies/verify
Content-Type: application/json

{"code": "...complete Strategy API V2 source..."}
~~~

成功响应会返回 <code>valid: true</code> 和 manifest。部署前必须重新验证最终保存的源码，不要只验证早期草稿。

---

## 4. 标的规范

推荐使用规范标的：

| 市场 | 示例 |
| --- | --- |
| A 股 | <code>CNStock:600519.SH</code> |
| 美股 | <code>USStock:MSFT</code> |
| 港股 | <code>HKStock:00700.HK</code> |
| Crypto 现货 | <code>Crypto:BTC/USDT@spot</code> |
| 指定交易所 Crypto 现货 | <code>Crypto:BTC/USDT@okx:spot</code> |
| Crypto 永续 | <code>Crypto:BTC/USDT@swap</code> |
| 指定交易所 Crypto 永续 | <code>Crypto:BTC/USDT@okx:swap</code> |
| 外汇 | <code>Forex:EUR/USD</code> |
| 期货 | <code>Futures:ES</code> |
| 莫斯科交易所 | <code>MOEX:SBER</code> |

系统也会规范化部分别名，例如 <code>600519.XSHG</code> → <code>CNStock:600519.SH</code>、<code>BTCUSDT</code> → <code>BTC/USDT</code>。

为避免歧义，生产策略应写完整市场前缀。Crypto 未写市场类型时默认为 spot。只有 swap 可以启用合约杠杆。能够解析市场名称并不代表一定有数据或支持实盘，实盘支持范围见第 18 节。

---

## 5. 静态和动态 universe

静态单标的：

~~~python
context.set_universe(["USStock:SPY"])
~~~

静态多标的：

~~~python
context.set_universe([
    "USStock:AAPL",
    "USStock:MSFT",
    "USStock:NVDA",
])
~~~

指数 universe：

~~~python
context.set_universe(index="INDEX:SP500")
members = get_index_stocks("INDEX:SP500")
~~~

平台命名股票池：

~~~python
context.set_universe(pool="sp500")
members = get_universe_stocks()
~~~

动态 universe 在每个历史时点解析当时成分，避免直接把今天的成分复制进历史回测。不要把 pool 成分硬编码进源码。

使用动态 universe、多个静态标的或 <code>on_rebalance</code> 时，清单通常分类为 portfolio；单一静态标的通常分类为 CTA。

---

## 6. 订阅、预热和 benchmark

~~~python
context.subscribe(
    frequency="1d",
    fields=["open", "high", "low", "close", "volume"],
)
context.set_warmup(260)
context.set_benchmark("USStock:SPY")
~~~

要点：

- 周期写在源码中，例如 <code>1m</code>、<code>5m</code>、<code>1h</code>、<code>4h</code>、<code>1d</code>、<code>1w</code>。
- <code>daily</code>、<code>day</code>、<code>d</code> 等别名会规范化为 <code>1d</code>。
- 未指定 symbols 时，订阅当前 universe。
- <code>set_warmup</code> 告诉数据服务在回测开始日前额外获取历史数据；它不代表策略可以跳过 <code>len(bars)</code> 检查。
- benchmark 只用于对比收益，不会自动交易。
- 最多可声明 8 个不同周期。当前原生支持 <code>1m</code>、<code>3m</code>、<code>5m</code>、<code>15m</code>、<code>30m</code>、<code>1h</code>、<code>4h</code>、<code>1d</code> 和 <code>1w</code>。
- 5 分钟、15 分钟、30 分钟可以和小时线、日线、周线同时用于一个策略。周线统一写成小写 <code>1w</code>；月线目前不是 Strategy API V2 原生周期。

### 6.1 原生多周期

多周期是一项可选能力，不是所有策略或机器人必须采用的模式。普通策略默认只声明并读取一个周期；只有用户或原策略明确要求跨周期验证时，才增加相应订阅。网格、定投等不依赖跨周期信号的机器人不会因为这项能力自动增加周期。

同一策略可以独立订阅并读取多个周期。每个周期由数据服务单独加载，不会从最细周期在策略内临时拼接。下面示例用 1 分钟金叉作为入场事件，并用已完成的 1 小时均线多头状态进行确认：

~~~python
def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1m")
    context.subscribe(frequency="1h")
    context.set_warmup(62)


def handle_data(context, data):
    bars_1m = get_history(32, "1m", "close", g.symbol)
    bars_1h = get_history(52, "1h", "close", g.symbol)
    if len(bars_1m) < 31 or len(bars_1h) < 50:
        return

    close_1m = bars_1m["close"]
    fast_now = float(close_1m.tail(10).mean())
    fast_prev = float(close_1m.iloc[:-1].tail(10).mean())
    slow_now = float(close_1m.tail(30).mean())
    slow_prev = float(close_1m.iloc[:-1].tail(30).mean())
    golden_cross = fast_prev <= slow_prev and fast_now > slow_now
    death_cross = fast_prev >= slow_prev and fast_now < slow_now
    hourly_bullish = float(bars_1h["close"].tail(20).mean()) > float(
        bars_1h["close"].tail(50).mean()
    )
    amount = float(get_position(g.symbol).amount or 0.0)
    if amount <= 0 and golden_cross and hourly_bullish:
        order_target_percent(g.symbol, 0.95, reason="one_minute_cross_hourly_confirmed")
    elif amount > 0 and (death_cross or not hourly_bullish):
        order_target_percent(g.symbol, 0.0, reason="cross_or_hourly_filter_exit")
~~~

多周期运行规则：

- 最细的已订阅周期自动成为 <code>drivingFrequency</code>。上例每根已完成 1 分钟 K 线驱动一次 <code>handle_data</code>；声明顺序不会改变驱动周期。
- <code>get_history(..., frequency, ...)</code> 会路由到该周期的独立历史帧。请求未订阅周期会报 <code>strategyV2.frequencyNotSubscribed</code>，不会静默回退到其他周期。
- 高周期 K 线只有在其收盘时间不晚于当前驱动 K 线的收盘时间时才可见。例如 08:00 开始的 4 小时 K 线，要到 12:00 后才能参与信号。
- <code>set_warmup(n)</code> 对每个已订阅周期都请求至少相应的预热窗口；策略仍须分别检查每个 DataFrame 的长度。
- 回测日期范围必须同时满足所有周期的数据源限制；任一必需周期缺失的标的不会以不完整周期集合继续运行。
- 各数据源的历史深度限制仍分别生效。分钟线通常比日线、周线保留时间短，所以 <code>5m + 1w</code> 虽然是合法组合，回测区间仍可能需要按 5 分钟数据的可用范围缩短；加密货币、股票和外汇数据源的历史深度也可能不同。
- 清单中的 <code>primaryFrequency</code> 为首个声明周期的兼容字段；调度、执行时钟和绩效年化使用 <code>drivingFrequency</code>。
- <code>data.history(..., frequency="4h")</code>、<code>data.current(..., frequency="4h")</code>、<code>indicator(..., frequency="4h")</code> 和 <code>factor(..., frequency="4h")</code> 同样支持显式周期。<code>data[symbol]</code> 默认使用驱动周期。

当用户明确要求跨周期确认时，可以用高周期过滤趋势、低周期确认入场，并让下单条件保持幂等。因为 <code>handle_data</code> 按最细周期运行，不能把“高周期多头”直接写成每个低周期 bar 都无条件加仓。“高周期处于多头排列”和“高周期刚刚发生金叉”是不同条件；策略和 AI 必须按用户原意实现，不能相互替换。

AI 生成策略同样遵守这份契约：用户只给出一个周期时必须保持单周期，不得主动添加确认周期；用户明确给出多个周期时，生成和修复流程必须保留全部订阅，不能只挑一个周期、用最低周期自行重采样，或把全部历史读取改写成驱动周期。

---

## 7. 生命周期与调度

支持的处理器：

~~~python
def initialize(context):
    pass

def before_trading_start(context, data):
    pass

def handle_data(context, data):
    pass

def on_rebalance(context, panel):
    pass

def after_trading_end(context, data):
    pass
~~~

定时任务：

~~~python
def initialize(context):
    context.set_universe(["USStock:SPY"])
    context.subscribe(frequency="5m")
    run_daily(rebalance, time="09:35")
    run_weekly(weekly_review, weekday=1, time="09:40")
    run_monthly(monthly_rebalance, monthday=1, time="09:45")
~~~

规则：

- <code>weekday</code> 使用 1–7，1 为星期一。
- 月度日期超出当月天数时会落在当月最后一天。
- 日线及更低频率下，具体 <code>time</code> 不用于制造不存在的盘中 bar。
- 回调推荐签名为 <code>callback(context, data)</code>；运行时也会适配只接收 context 的函数。
- portfolio 策略如果没有定时任务，会调用 <code>on_rebalance</code>。
- 回测会在每个事件时间戳调用 <code>before_trading_start</code> 和 <code>after_trading_end</code>。实盘只会在新处理的 bar 进入新日历日期时调用 <code>before_trading_start</code>；当前实盘运行时不会调用 <code>after_trading_end</code>。实盘关键收盘逻辑应放在 <code>handle_data</code> 或定时任务中。

实盘定时任务按用户配置的时区解释。如果用户没有设置时区，则依次回退到服务器 <code>TZ</code> 和 UTC。应明确设置用户时区，并按交易所交易时段验证调度。回测时间来自行情数据时钟，依赖精确盘中时间前必须确认回测与实盘时区一致。

---

## 8. 最重要的时间语义

回测只向策略暴露当时可见的数据：

1. 进入新 bar 时，先执行上一 bar 收盘后排队的订单，成交参考当前 bar 开盘。
2. <code>before_trading_start</code> 和到期的定时回调只看到前一根及更早的数据；其订单可以在当前开盘处理。
3. 然后当前 bar 变为可见，调用 <code>handle_data</code>。
4. <code>handle_data</code> 根据当前已完成 bar 产生的订单排队到下一根 bar 开盘。
5. <code>after_trading_end</code> 同样能看到当前 bar；其新订单也等待下一根 bar。

因此，“收盘确认、下一开盘成交”是默认的无未来执行模型。不要用负 shift 或未来行把成交提前。

实盘会对每根已收盘 bar 只处理一次，并在当前会话存活期间保留 <code>g</code> 状态。重复收到同一根 bar 不应重复触发策略。跨重启状态需要显式开启，见第 9 节。

### 已完成 K 线与实时价格的边界

- <code>get_history</code>、<code>data.current</code>、<code>data.history</code> 和指标函数看到的最后一行必须是已经完成的 K 线。实时成交价或 mark price 不能回写、覆盖或延长这根 K 线的 OHLC。
- 均线、突破、形态、因子和其他入场/加仓信号只能根据已完成 K 线计算，保证回测、实盘和重启重放具有相同语义。
- 实时价格仅供止损、止盈、追踪止损和权益风控使用；它不能把一根尚未收盘的 K 线伪装成完成 bar，也不能改变已经确认的策略信号。
- 多周期策略对每个周期分别执行已完成 K 线约束；低周期推进不会提前暴露仍在形成中的 4 小时线或日线。
- 如果产品以后提供独立的逐 tick 策略契约，应使用单独的 API、回测模型和文档；不要在普通 Strategy API V2 源码中自行模拟。

---

## 9. context、data 和 g

常用 context 字段：

| 字段 | 含义 |
| --- | --- |
| <code>context.params</code> | 本次运行参数 |
| <code>context.current_dt</code> | 当前事件时间 |
| <code>context.previous_trading_date</code> | 上一个事件时间 |
| <code>context.portfolio.starting_cash</code> | 初始资金 |
| <code>context.portfolio.available_cash</code> | 可用现金 |
| <code>context.portfolio.total_value</code> | 当前总权益 |
| <code>context.portfolio.positions</code> | 当前持仓字典 |
| <code>context.data</code> | 数据视图 |

<code>data.current(symbol, field, frequency="1h")</code> 读取当前可见值；<code>data.history(symbols, count, fields, frequency="4h")</code> 读取指定周期历史；<code>data[symbol]</code> 返回驱动周期的当前可见 DataFrame。

跨回调状态放在 <code>g</code>：

~~~python
def initialize(context):
    g.last_signal = ""
    g.rebalance_count = 0
~~~

不要把用户状态放在文件、数据库或模块外部全局服务中。<code>g</code> 是单次运行的策略状态空间。

### 跨重启状态

默认情况下，<code>g</code> 只在当前进程的多个回调之间保留；会话重启后会重新执行 <code>initialize</code>。如果策略无法仅根据仓位和订单状态重建运行周期，应显式开启状态快照：

~~~python
PERSIST_RUNTIME_STATE = True
~~~

等效部署参数是 <code>persist_runtime_state=true</code>。开启后，运行时会保存可支持的 <code>g</code> 值、最后处理的 bar、调度时钟、客户端订单状态和最近离场原因。保护引擎状态会独立恢复。持久化值应保持为类似 JSON 的结构，并在重启后继续与交易所真实仓位核对；快照不能替代交易所账本。

---

## 10. 参数

~~~python
# @param fast_period int 20 Fast moving-average period range=2:100:1
# @param slow_period int 50 Slow moving-average period range=3:250:1
# @param target_pct float 0.95 Target weight values=0.5,0.75,0.95
# @param enabled bool true Enable entries
~~~

读取：

~~~python
fast_period = int(context.params.get("fast_period", 20))
slow_period = int(context.params.get("slow_period", 50))
target_pct = float(context.params.get("target_pct", 0.95))
enabled = bool(context.params.get("enabled", True))
~~~

声明默认值和代码回退值必须一致。参数面板把用户值放入 <code>context.params</code>；若没有用户值，代码回退值是最后保障。

标的、市场、周期和杠杆许可属于源码契约，不要把它们伪装成可由运行面板任意覆盖的普通参数。

---

## 11. 历史数据、因子和基本面

单标的历史：

~~~python
bars = get_history(
    60,
    "1d",
    ["open", "high", "low", "close", "volume"],
    "USStock:SPY",
)
~~~

一个标的返回 DataFrame；多个标的返回以规范标的为键的 DataFrame 字典：

~~~python
frames = data.history(
    ["USStock:AAPL", "USStock:MSFT"],
    count=30,
    fields=["close", "volume"],
)
~~~

技术指标和因子：

~~~python
rsi_value = factor("rsi", g.symbol, period=14)
macd = indicator("MACD", g.symbol, fastperiod=12, slowperiod=26, signalperiod=9)
scores = get_factors(symbols, ["momentum_20", "volatility_20"])
~~~

基本面：

~~~python
fundamentals = get_fundamentals(
    ["PE", "PB", "ROE", "MARKET_CAP"],
    symbols,
)
~~~

常用公开别名还包括 <code>REVENUE_GROWTH</code>、<code>DEBT_TO_EQUITY</code> 和 <code>FREE_CASH_FLOW</code>。只使用平台真实支持、按时点可见的字段，不要发明字段或读取未来财报。

多标的 <code>factor</code>/<code>indicator</code> 调用必须传 symbol；只有单标的数据门户可以省略 symbol。

---

## 12. 仓位与订单 API

读取仓位：

~~~python
position = get_position(g.symbol)
all_positions = get_positions()
~~~

Position 常用字段：

- <code>symbol</code>
- <code>amount</code>
- <code>avg_cost</code>
- <code>last_price</code>
- <code>market_value</code>
- <code>position_side</code>

swap 双向持仓策略必须显式读取每一条腿：

~~~python
long_position = get_position(g.symbol, position_side="long")
short_position = get_position(g.symbol, position_side="short")
~~~

在 hedge mode 下，不要把 <code>get_position(symbol)</code> 当成自动合成的净仓位。<code>get_positions()</code> 可能包含 <code>symbol::long</code>、<code>symbol::short</code> 这样的分腿键。判断某条腿是否有仓时建议使用 <code>abs(position.amount)</code>。

不要混淆下面几个不同层级的定义：

| 名称 | 所属层级 | 含义 |
| --- | --- | --- |
| <code>direction_mode</code> | 策略清单 | 策略被允许使用的方向能力：<code>long_only</code>、<code>short_only</code>、<code>both</code> 或 <code>neutral</code> |
| <code>position_side</code> | 仓位/订单 | 合约 hedge mode 中的 <code>long</code> 或 <code>short</code> 分腿；现货只有 long 库存 |
| 订单 value/target | 策略源码 | 希望增减或达到的数量、价值、权重；做空目标在源码中使用负数 |
| <code>open/add/reduce/close</code> | 运行时订单意图 | 引擎根据当前同步仓位和目标差额生成的标准动作，提交数量使用绝对值 |
| <code>execution_mode</code> | 部署 | <code>signal</code> 只发信号，<code>live</code> 才提交真实订单 |
| <code>coexistence_mode</code> | 账户仓位归属 | <code>strict</code> 或 <code>advanced</code>，决定用户仓位怎样与策略仓位共存；它不是交易方向 |

订单函数：

| 函数 | 含义 |
| --- | --- |
| <code>order(symbol, amount)</code> | 增减指定数量 |
| <code>order_value(symbol, value)</code> | 增减指定报价币价值 |
| <code>order_target(symbol, amount)</code> | 把持仓调整到目标数量 |
| <code>order_target_value(symbol, value)</code> | 调整到目标价值 |
| <code>order_target_percent(symbol, percent)</code> | 调整到组合权益的目标比例 |

目标型 API 最适合可重复执行的再平衡逻辑。每个订单都应提供稳定的 <code>reason</code>：

~~~python
order_target_percent(
    g.symbol,
    0.5,
    reason="breakout_long_entry",
)
~~~

常用订单参数：

| 参数 | 含义 |
| --- | --- |
| <code>reason</code> | 稳定、可审计的原因 |
| <code>position_side</code> | swap 双向持仓的 <code>long</code> 或 <code>short</code> 腿 |
| <code>client_order_id</code> | 幂等与状态查询引用，最多 100 个字符 |
| <code>order_type</code> | <code>market</code> 或 <code>limit</code> |
| <code>limit_price</code> | 限价单必需的正数价格 |
| <code>execution_algo</code> | <code>market</code>、<code>limit</code> 或 <code>maker_then_market</code> |
| <code>maker_wait_sec</code> | maker 等待多久后回退为市价 |
| <code>maker_offset_bps</code> | maker 价格偏移，单位为基点 |

使用稳定客户端引用的示例：

~~~python
def submit_entry():
    g.entry_ref = order(
        g.symbol,
        1,
        position_side="long",
        order_type="limit",
        limit_price=100.0,
        client_order_id="breakout-long-20250102",
        reason="breakout_long_entry",
    )


def monitor_entry(cancel_requested):
    status = get_order_status(g.entry_ref)
    working = ("queued", "deferred", "submitted", "open", "partial")
    if cancel_requested and status["status"] in working:
        cancel_order(g.entry_ref)
~~~

新策略中，所有需要重试、取消、对账或推进运行周期的订单都必须显式传入稳定的 <code>client_order_id</code>。订单函数会返回该 ID，供 <code>get_order_status</code> 查询。旧源码未传 ID 时仍可能返回 <code>None</code>，但这只是迁移行为，不是新策略契约。

常见活动状态包括 <code>unknown</code>、<code>queued</code>、<code>deferred</code>、<code>submitted</code>、<code>open</code> 和 <code>partial</code>；终态包括 <code>filled</code>、<code>rejected</code>、<code>failed</code>、<code>cancelled</code>/<code>canceled</code> 和 <code>expired</code>。<code>partial</code> 不是完全成交，不能按计划数量推进状态。订单终态和交易所仓位同步可能短暂错开，因此复用资金、开始新周期或反向开仓前，必须同时确认订单终态和同步仓位。实盘撤单也是异步过程。<code>consume_last_exit_reason(symbol)</code> 会返回并清除最近一次保护离场原因。

现货和所有非 Crypto 市场当前按 long-only 编写。多头离场条件与空头入场条件必须独立；不要把 <code>target=0</code> 的离场自动改成负仓位。

引擎会处理手续费、滑点、最小交易单位、成交量上限、涨跌停和停牌。被延迟或拒绝的订单会出现在订单审计账本中，不应从“没有成交”直接推断策略没有发单。

实盘中，同一持仓腿存在活动订单时会抑制重复请求，直到订单对账完成。目标仓位跨越零点时采用“先平后开”：先平掉当前腿，等待成交和仓位同步确认，再开反向腿。策略状态必须根据确认后的订单状态或同步仓位推进，不能仅因为调用了订单函数就假定成交。

---

## 13. 止损、止盈、追踪和时间保护

随开仓声明：

~~~python
order_target_percent(
    g.symbol,
    0.8,
    reason="breakout_long_entry",
    stop_loss_pct=0.03,
    take_profit_pct=0.08,
    trailing_stop_pct=0.025,
    trailing_activation_pct=0.02,
    time_limit_seconds=86400 * 10,
)
~~~

或设置后续开仓的默认保护：

~~~python
set_default_protection(
    stop_loss_pct=0.03,
    take_profit_pct=0.08,
)
~~~

所有 pct 都使用小数比率，<code>0.03</code> 表示 3%。保护值会限制在安全范围内；负值按 0 处理。

回测规则：

- 跳空越过保护价时按可成交的 bar 开盘价处理。
- bar 内触发按触发价处理。
- 同一 bar 同时触发多个保护时，默认 conservative 模式优先止损，再追踪止损、时间限制、止盈。

实盘使用独立价格时钟检查同样的保护语义，不必等待下一根策略 bar。保护状态会保存并可在会话重启后恢复。

---

## 14. 杠杆和做空

只有 universe 中全部静态标的都是 Crypto swap 时，源码才能声明：

~~~python
def initialize(context):
    g.symbol = "Crypto:BTC/USDT@okx:swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1h")
    context.allow_leverage(max_leverage=5)
~~~

规则：

- Crypto spot、股票、指数/股票池和其他非 Crypto 市场不能调用 <code>allow_leverage</code>。
- 动态 universe 不能启用合约杠杆。
- 回测或部署选择的杠杆不能超过源码声明的最大值。
- 源码没有许可时，运行面板不能强制开启杠杆。
- 用户选择的杠杆由运行时应用，不要再在订单金额中手工乘一次。
- 做空只应出现在 swap 策略中，并且必须有独立的空头入场、空头离场和风险规则。

### 交易方向能力

新的 Crypto swap 策略应在 `initialize` 中声明方向能力：

~~~python
context.set_metadata(direction_mode="both")
~~~

支持 `long_only`（仅做多）、`short_only`（仅做空）、`both`（多空双向）和 `neutral`（中性双腿）。这个声明不会下单，也不会覆盖策略信号；它用于在部署时分配正确的双向持仓腿，并拒绝超出声明能力的新开仓信号。`both` 和 `neutral` 在实盘中要求交易所账户开启双向持仓模式。

新建 Crypto swap 策略必须显式声明 <code>direction_mode</code>，并在每次合约仓位读取和订单调用中显式传入 <code>position_side</code>。编译器对旧源码中 <code>DIRECTION = 1/-1</code> 或字面量仓位腿的推断只用于迁移，不属于推荐契约，也不应作为新模板的实现方式。现货策略按 <code>long_only</code> 编写。

### 双向持仓示例

下面示例持续保留一张多头核心仓，并在价格跌破均线时独立开启一张空头对冲仓：

~~~python
"""BTC Long Core With Short Hedge
Maintains independent long and short swap legs in exchange hedge mode.
"""


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@okx:swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1h")
    context.set_warmup(60)
    context.allow_leverage(max_leverage=5)
    context.set_metadata(direction_mode="both")


def handle_data(context, data):
    bars = get_history(51, "1h", "close", g.symbol)
    if len(bars) < 51:
        return

    price = float(bars["close"].iloc[-1])
    average = float(bars["close"].tail(50).mean())
    long_position = get_position(g.symbol, position_side="long")
    short_position = get_position(g.symbol, position_side="short")

    if abs(float(long_position.amount or 0.0)) < 0.5:
        order_target(
            g.symbol,
            1,
            position_side="long",
            reason="core_long",
        )

    hedge_required = price < average
    if hedge_required and abs(float(short_position.amount or 0.0)) < 0.5:
        order_target(
            g.symbol,
            -1,
            position_side="short",
            reason="open_short_hedge",
        )
    elif not hedge_required and abs(float(short_position.amount or 0.0)) >= 0.5:
        order_target(
            g.symbol,
            0,
            position_side="short",
            reason="close_short_hedge",
        )
~~~

数量单位取决于交易所合约规格，不能假定一张合约一定等于一个基础币。实盘启动前，平台会确认账户持仓模式；无法确认 hedge mode 时，`both` 和 `neutral` 会按安全原则拒绝启动。运行中的策略会占用账户/交易所/市场/标的/持仓腿；重复占用会返回 <code>strategyV2.liveLegConflict</code>。确认处于 hedge mode 时，两个独立的 long-only 与 short-only 策略可以分别占用相反方向，但声明 <code>both</code> 或 <code>neutral</code> 的策略会同时占用两条腿。

不要只用 <code>g.long_qty</code>/<code>g.short_qty</code> 维护权威仓位。订单可能被拒绝、延迟、部分成交或按交易所规则取整。推进策略周期前必须读取同步后的分腿仓位和订单状态。

---

## 15. 完整 CTA 教程：双 EMA 趋势策略

~~~python
"""Dual EMA Long Trend
Trades a long-only daily SPY trend with a protected entry and next-open fills.
"""

# @param fast_period int 20 Fast EMA period range=5:80:5
# @param slow_period int 50 Slow EMA period range=20:250:10
# @param target_pct float 0.95 Target portfolio weight range=0.1:1.0:0.05
# @param stop_loss_pct float 0.05 Entry stop-loss ratio range=0.01:0.15:0.01


def initialize(context):
    g.symbol = "USStock:SPY"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1d")
    context.set_warmup(300)
    context.set_benchmark("USStock:SPY")


def handle_data(context, data):
    fast_period = int(context.params.get("fast_period", 20))
    slow_period = int(context.params.get("slow_period", 50))
    target_pct = float(context.params.get("target_pct", 0.95))
    stop_loss_pct = float(context.params.get("stop_loss_pct", 0.05))

    if fast_period >= slow_period:
        log.warning("fast_period must be smaller than slow_period")
        return

    bars = get_history(
        slow_period + 2,
        "1d",
        "close",
        g.symbol,
    )
    if len(bars) < slow_period + 1:
        return

    close = bars["close"]
    fast_now = float(close.ewm(span=fast_period, adjust=False).mean().iloc[-1])
    slow_now = float(close.ewm(span=slow_period, adjust=False).mean().iloc[-1])
    position = get_position(g.symbol)

    if fast_now > slow_now and position.amount <= 0:
        order_target_percent(
            g.symbol,
            target_pct,
            reason="dual_ema_long_entry",
            stop_loss_pct=stop_loss_pct,
        )
    elif fast_now < slow_now and position.amount > 0:
        order_target_percent(
            g.symbol,
            0.0,
            reason="dual_ema_long_exit",
        )
~~~

为什么这样写：

- universe、周期和 benchmark 都在源码中。
- warm-up 覆盖慢 EMA，但仍检查实际数据长度。
- 快慢周期错误时直接停止本 bar。
- 入场与离场互斥，死叉只平多，不开空。
- 读取当前已完成日线后发单，下一根开盘成交。
- 只有入场附带保护，离场目标为 0。

---

## 16. Portfolio 教程：每周因子再平衡

~~~python
"""S&P 500 Momentum Basket
Selects the strongest five point-in-time pool members and rebalances weekly.
"""

# @param holdings int 5 Number of holdings range=3:20:1
# @param max_weight float 0.18 Maximum weight per holding range=0.05:0.3:0.01


def initialize(context):
    context.set_universe(pool="sp500")
    context.subscribe(frequency="1d")
    context.set_warmup(80)
    context.set_benchmark("USStock:SPY")
    run_weekly(rebalance, weekday=1, time="09:35")


def rebalance(context, data):
    holdings = int(context.params.get("holdings", 5))
    max_weight = float(context.params.get("max_weight", 0.18))
    symbols = get_universe_stocks()
    if len(symbols) < holdings:
        return

    scores = get_factors(symbols, "momentum_20")
    if scores.empty or "momentum_20" not in scores.columns:
        return

    ranked = scores["momentum_20"].dropna().sort_values(ascending=False)
    selected = list(ranked.head(holdings).index)
    if not selected:
        return

    target_weight = min(max_weight, 0.95 / len(selected))
    current = get_positions()

    for symbol in current:
        if symbol not in selected:
            order_target_percent(symbol, 0.0, reason="weekly_remove")

    for symbol in selected:
        order_target_percent(symbol, target_weight, reason="weekly_select")
~~~

此类策略必须使用按时点解析的 universe 和因子数据。回测还要关注覆盖率、幸存者偏差、换手、交易成本、最小交易单位和无法成交订单。

---

## 17. 回测、结果和诊断

回测请求的核心字段：

~~~json
{
  "code": "...",
  "startDate": "2024-01-01",
  "endDate": "2025-12-31",
  "initialCapital": 100000,
  "commission": 0.0005,
  "slippage": 0.0005,
  "leverageEnabled": false,
  "leverage": 1,
  "params": {},
  "persist": true
}
~~~

还可以传 <code>sourceId</code> 或 <code>strategyId</code> 读取已保存源码。市场、标的和周期不能从请求覆盖。

重点检查：

- <code>resultStatus</code>：<code>no_signals</code>、<code>open_position_only</code> 或 <code>completed_trades</code>。
- <code>totalExecutions</code>：实际成交次数。
- <code>totalTrades</code>：已平仓交易次数，不等于成交次数。
- <code>rawTrades</code>/<code>executions</code>：开仓、加仓、减仓、平仓成交。
- <code>closedTrades</code>：完整往返交易。
- <code>orderLedger</code>：成交、延迟、拒绝及原因。
- <code>holdingSnapshots</code>、<code>rebalanceRecords</code>：组合过程。
- <code>equityCurve</code>、回撤、胜率、Profit Factor 和 benchmark/excess return。
- <code>dataProvenance</code> 和 <code>executionAssumptions</code>：数据来源与执行假设。

### 成本与执行假设

- 每次成交都会收取手续费。完整往返交易的已实现利润会同时扣除分摊后的开仓手续费和平仓手续费。
- 滑点按照结果中的执行假设应用。
- Strategy API V2 回测当前**不模拟 Crypto 资金费用**。应确认 <code>executionAssumptions.fundingMode == "not_modeled"</code>；杠杆 swap 回测不能不估算资金费就直接与实盘净利润比较。
- 实盘使用交易所返回的成交手续费，并在可用时同步资金费/账户账单。手续费可能以报价币、基础币或平台折扣币收取，因此换算与对账可能晚于成交。
- 应测试多组手续费和滑点假设。成本轻微上升就失去优势的策略不够稳健。

回测中心还支持因子研究和参数调优。调优可使用网格或随机参数空间，单次最多 500 个组合，并为选中结果报告样本外验证。回测可按系统设置扣除积分；执行失败会自动退款。前端请求超时不等于服务端任务失败，重复提交前应先检查回测历史。

零成交不一定是系统错误：可能是数据不足、条件从未触发、参数不合理、标的无数据或订单被拒绝。先看日志和 orderLedger。

---

## 18. 部署与实盘边界

部署核心字段包括：

- <code>sourceId</code>
- <code>name</code>
- <code>initialCapital</code>
- <code>executionMode</code>：<code>signal</code> 或 <code>live</code>
- 可选 <code>credentialId</code>、<code>params</code>、杠杆、仓位方向和通知配置

部署创建后状态为 stopped，必须显式 start。删除前必须先停止。

当前 live 账户边界：

| 市场 | 支持的实盘通道 | 产品边界 |
| --- | --- | --- |
| Crypto | Binance、Bitget、Bybit、OKX、Gate、HTX | 按交易所和账户能力支持 spot 与 swap |
| USStock | Alpaca、IBKR | 当前券商策略按 long-only |
| 其他可解析市场 | 暂无 | 可回测或有数据不等于支持实盘 |

混合市场 live 不支持，其他市场不能强行用不匹配的凭证部署。

### 仓位归属、对账与账户风控

- 实盘策略只能管理分配给自己的策略仓位。用户手工持仓和其他策略拥有的仓位不能被该策略平掉。
- Crypto 现货和合约都支持高级共存。归属基线按账户凭证、市场类型、规范标的和持仓腿分别记录；现货只有 long 库存，合约按 long/short 分腿。
- 核对恒等式是：<code>账户仓位 = 策略分配仓位 + 用户保护仓位 + 未知差额</code>。允许继续开仓要求未知差额处于容差范围内。

| 归属模式 | 用户保护仓位 | 行为 |
| --- | --- | --- |
| <code>strict</code>（默认） | 固定为 0 | 账户出现未分配仓位时暂停该方向开仓/加仓，不会自动平仓 |
| <code>advanced</code> | 用户确认时记录 <code>账户仓位 - 策略仓位</code> | 策略可以与该保护基线共存；后续产生新的未知差额时仍暂停开仓/加仓 |

- 漂移只暂停同方向的新开仓和加仓，并在状态首次变化时记录一次包含账户、策略、保护和未知数量的日志；相同状态不会反复刷日志。
- 网格策略会在每次挂单同步时执行同一归属核对。发生漂移会撤销该持仓腿尚未成交的 entry 挂单；账户低于保护分配或无法确认保护账本时，也会撤销可能超量的 exit 挂单，再按策略仓位、已有退出挂单和保护基线重新计算安全退出数量。
- 平仓和减仓保持可用，但数量同时受策略账本、交易所实际仓位和用户保护基线约束，绝不会越过保护仓位。平仓不是修复未知差额的工具。
- “持仓归属与修复”页面提供：<code>protect_manual</code>（把当前差额设为用户保护基线并启用高级共存）、<code>strict_mode</code>（清除基线并恢复严格模式）和 <code>recheck</code>（重新拉取并核对）。这些动作只修改归属记录，不会自动开仓或平仓。
- 高级共存是 QuantDinger 的账本隔离，不是交易所物理隔离。现货同币种仍共享账户余额；合约同方向仓位仍共享交易所均价、保证金和强平风险。
- 同账户/交易所/市场/标的/持仓腿采用独占归属。确认 hedge mode 后，可以由两个 long-only、short-only 策略分别使用相反腿；<code>both</code>/<code>neutral</code> 会占用两条腿。
- 策略计算后还会应用最小数量、数量步长、最小名义金额、可用保证金、杠杆和交易所上限，最终提交数量可能与原始请求不同。
- 只有合约开仓/加仓会设置保证金模式和杠杆；平仓/减仓跳过账户配置，避免配置接口故障阻塞退出。Binance 返回 HTTP 408、<code>-1007</code> 或“execution status unknown”时，运行时会回读保证金模式/杠杆；只有回读与目标一致才继续开仓。
- 可选账户风控会按总名义敞口、预计保证金、总杠杆或单标的敞口拒绝订单。这类结果是需要调整配置或仓位的风控警告，不能绕过保护。
- 行情、私有 WebSocket 事件与定期 REST 对账共同工作。WebSocket 提供低延迟，REST 仍是断线或漏事件后的恢复来源。

先用 signal 模式验证通知、信号频率和状态恢复，再考虑 live。回测通过不代表连接、余额、最小下单量、交易所规则和网络状态一定满足实盘。

---

## 19. 安全限制和常见失败

策略运行在安全执行环境中。禁止文件、网络、数据库、进程、动态执行、反射和不安全导入。不要使用 <code>eval</code>、<code>exec</code>、<code>compile</code>、<code>open</code>、dunder 绕过或外部状态。

允许导入的根模块包括 <code>numpy</code>、<code>pandas</code>、<code>math</code>、<code>json</code>、<code>datetime</code>、<code>time</code>、<code>collections</code>、<code>functools</code>、<code>itertools</code>、<code>statistics</code>、<code>decimal</code>、<code>fractions</code> 和 <code>copy</code>。即使模块允许导入，pandas <code>read_*</code>/<code>to_*</code>、NumPy 文件加载/保存、类似 pickle 的反序列化以及字符串表达式执行方法仍会被禁止。

常见编译错误：

| 错误 | 含义 | 修复 |
| --- | --- | --- |
| <code>strategyV2.codeRequired</code> | 源码为空 | 提交完整源码 |
| <code>strategyV2.initializeRequired</code> | 缺少 initialize | 添加初始化函数 |
| <code>strategyV2.initializeFailed:...</code> | 初始化执行失败 | 只在 initialize 做声明和状态初始化 |
| <code>strategyV2.universeRequired</code> | 未声明 universe | 调用 <code>set_universe</code> |
| <code>strategyV2.handlerRequired</code> | 没有可执行处理器/定时任务 | 添加 handler 或 schedule |
| <code>strategyV2.leverageCryptoSwapOnly</code> | 杠杆市场不合法 | 仅用于静态 Crypto swap |
| <code>strategyV2.leverageNotAllowed</code> | 面板开了源码未许可的杠杆 | 源码合法许可或关闭杠杆 |
| <code>strategyV2.leverageExceedsStrategyLimit</code> | 请求杠杆超过上限 | 降低请求值 |
| <code>strategyV2.dataUnavailable:...</code> | 标的没有可用数据 | 检查规范标的和数据范围 |
| <code>strategyV2.frequencyUnsupported:...</code> | 声明了运行时不支持的周期 | 改用第 6 节列出的原生周期 |
| <code>strategyV2.tooManyFrequencies:8</code> | 策略声明超过 8 个周期 | 删除非必需订阅 |
| <code>strategyV2.frequencyNotSubscribed:...</code> | 代码读取了未订阅周期 | 在 <code>initialize</code> 中声明该周期或修正调用 |
| <code>strategyV2.noMarketData</code> | 实盘周期没有可用行情帧 | 检查标的、数据源、连接和订阅周期 |
| <code>strategyV2.initializeParamsUnavailable</code> | 在清单发现阶段读取参数 | 把读取移到处理器 |
| <code>strategyV2.directionModeViolation:...</code> | 开仓方向超出声明能力 | 修正 metadata 或信号方向；平仓仍允许 |
| <code>strategyV2.dualDirectionHedgeModeRequired:...</code> | 账户没有开启双向持仓 | 在交易所开启 hedge/双向持仓模式 |
| <code>strategyV2.hedgeModeUnknown:...</code> | 无法确认账户持仓模式 | 修复凭证/API 权限后重试 |
| <code>strategyV2.liveLegConflict:...</code> | 另一实盘策略已占用该腿 | 停止或调整冲突策略 |
| <code>position_drift_detected:...</code> | 账户、策略和保护基线存在未知差额 | 在“持仓归属与修复”中重新核对、保护用户仓位或恢复严格模式；不要绕过 |
| <code>unallocated_account_position</code> | 账户仓位高于策略仓位与保护基线之和 | 核对后将差额登记为用户保护仓位，或手工恢复一致 |
| <code>account_below_protected_allocation</code> | 账户仓位低于策略仓位与保护基线之和 | 停止新增订单并核对交易所、策略账本和保护基线 |
| 数量无效/低于最小名义金额 | 取整后无法提交 | 增加资金/权重或更换合适标的 |
| 账户风控拒绝 | 超出账户配置的敞口上限 | 降低仓位/杠杆，或有意调整限制 |
| <code>strategyV2.runtimeFailed:...</code> | 回调运行异常 | 根据处理器名和原始异常修复 |

---

## 20. 系统预设与可视化机器人模板

### 系统预设策略模板

系统预设目录当前使用 <code>system_seed version=11</code>，包含 8 个 CTA 模板（单均线、双均线、阳线穿三线、趋势过滤阳线穿三线、海龟、指标共振、MACD/KDJ、SuperTrend）和 4 个组合模板（市值杠铃、动量 Top N、低波动、质量成长）。预设模板既是示例，也是当前 Strategy API V2 推荐契约的可执行基线。

系统预设必须满足：

- 每个模板显式声明 <code>direction_mode</code>；Crypto swap 模板显式读取和操作 <code>position_side</code> 分腿。
- 双向趋势模板执行“先平反向腿，等待成交与仓位同步，再开目标腿”，不能用一个净仓位变量代替两条腿。
- 可从交易所仓位恢复的状态应以同步后的 <code>amount</code>、<code>avg_cost</code> 和订单状态为准；无法可靠重建的状态必须启用 <code>PERSIST_RUNTIME_STATE</code>。
- 每次目录更新都必须通过参数契约、编译、方向能力和合成回测测试。复制模板后如果修改了市场、方向或周期，应重新验证 manifest，而不是继续依赖模板身份。

### 可视化机器人模板

机器人模板会生成可编辑的 Strategy API V2 源码。真正可部署的契约是生成后的源码，不是右侧预览；每次手工修改后都必须重新验证。

当前生成源码的契约版本为：网格 <code>GRID_TEMPLATE_VERSION = 6</code>、DCA <code>DCA_TEMPLATE_VERSION = 6</code>、马丁与分仓马丁 <code>ROBOT_TEMPLATE_VERSION = 6</code>。版本常量用于诊断生成源码，不能代替 manifest 和运行前验证。

| 模板 | 触发与资金分配 | 当前边界 |
| --- | --- | --- |
| 网格 | 将区间划分为等差/等比网格，每个入场成交后挂出对应格子的离场 | 实盘使用交易所限价挂单，回测按 OHLC 触碰重放 |
| DCA | 按固定经过分钟数、固定资金比例持续买入 | 仅 Crypto spot、仅做多 |
| 马丁 | 价格向不利方向触发层级，并逐层提高分配 | 必须限制层数、总预算和周期风险 |
| 分仓马丁 | 将马丁层级组织成多个资金分组 | 除总上限外还要限制每组 |

网格规则：

- 每个网格格子都有完整生命周期：等待入场 → 入场挂单/成交 → 对应离场挂单/成交 → 下一周期。不能实现成“逐格买入，最后一次性全部卖出”。
- <code>max_open_orders</code> 控制同时激活的入场单数量，稳定的 <code>client_order_id</code> 用于避免重复挂单。
- 实盘网格使用交易所限价单并根据成交通知对账；回测依据 bar 的 high/low 判断触碰。当一根 bar 同时跨越多格时，回测无法知道准确盘中路径，应使用足够细的周期。
- 中性网格要求 swap hedge mode 并占用多空两条腿；现货网格只能做多。

DCA 规则：

- 定投间隔按实际经过的分钟数计算，不是“K 线根数”。处理器只能在订阅 bar 到达时执行，因此间隔小于源码周期时，实际会在下一根可用 bar 执行。
- 每次投入同时受单次比例和周期总预算限制。即使启用了价格过滤、止盈、硬止损或追踪保护，也仍需设置最大定投次数。
- 提交定投后先进入 pending。只有 <code>get_order_status</code> 返回 <code>filled</code>，才按实际成交额和手续费扣减周期预算并增加定投次数；调用 <code>order_value</code> 本身不代表成交。
- <code>partial</code> 必须继续等待对账，不能按完全成交处理。<code>rejected</code>、<code>failed</code>、<code>cancelled/canceled</code> 或 <code>expired</code> 应释放挂起状态，不消耗次数和成交预算，并使用新的稳定客户端引用重试。
- DCA 生成源码开启 <code>PERSIST_RUNTIME_STATE</code>。退出后只有在账户仓位确认归零且存在明确离场原因时才能重置周期；不能因为仓位同步暂时落后就重复开始新周期。

马丁规则：

- 每一层都需要价格触发、计划分配、最大尝试次数、稳定客户端引用和确认成交后的状态迁移。
- 系统生成的马丁与分仓马丁源码会开启 <code>PERSIST_RUNTIME_STATE</code>。手工编辑时应保留恢复和最终清仓逻辑。
- 被拒绝或部分成交的订单不能按完整成交推进层级。重启后，继续加层或平仓前必须用策略归属仓位完成状态对账。
- 马丁属于尾部风险很高的资金管理方式，必须限制总投入、层数、杠杆、止损和止损后是否重新开始。

---

## 21. 发布前检查清单

- [ ] 文件有英文 docstring，说明名称、universe、信号、调度和风控。
- [ ] <code>initialize</code> 只声明 universe、订阅、预热、benchmark、调度、杠杆许可和初始 <code>g</code>。
- [ ] 标的使用规范格式，Crypto 明确 spot/swap。
- [ ] 源码拥有标的和周期，不依赖运行面板覆盖。
- [ ] 参数默认值与代码回退值一致。
- [ ] 所有历史窗口都检查长度。
- [ ] 不使用未来行、负 shift 或居中 rolling。
- [ ] 指标和入场/加仓信号只读取已完成 K 线；实时价格没有覆盖最后一根 OHLC，且只用于保护与权益风控。
- [ ] 多头离场与空头入场独立。
- [ ] 双向持仓代码显式读取和操作 <code>position_side</code> 分腿。
- [ ] 已区分 <code>direction_mode</code>、<code>position_side</code>、<code>execution_mode</code> 与账户 <code>coexistence_mode</code>。
- [ ] 仓位有明确上限；网格、DCA、马丁和加仓层数有硬限制。
- [ ] 订单都有可审计 reason。
- [ ] 可重试/活动订单使用稳定客户端 ID，并且确认前不推进状态。
- [ ] 风险百分比使用小数比率。
- [ ] 只对 Crypto swap 声明杠杆，且不重复乘杠杆。
- [ ] 已明确调度时区和跨重启状态要求。
- [ ] 已验证 manifest。
- [ ] 已检查 orderLedger，而不只看收益曲线。
- [ ] 已计入开仓和平仓手续费，并在当前回测之外单独评估 swap 资金费。
- [ ] 已用不同时间区间和成本假设做稳健性测试。
- [ ] 已有至少一次成功回测后再发布。
- [ ] live 前先确认凭证、市场、余额、最小交易单位和通知。
- [ ] 复用已有现货或合约仓位时，已在持仓修复页面确认严格/高级共存模式及用户保护基线。
