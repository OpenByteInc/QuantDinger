# QuantDinger 指标开发指南

> 适用范围：当前 QuantDinger 图表指标契约
> 面向读者：第一次编写指标的用户、从 Pine/通达信公式迁移的开发者，以及需要审查 AI 生成代码的维护者

QuantDinger 指标是运行在指标编辑器中的 Python 图表程序。它读取当前图表的 K 线数据，计算序列，并通过 <code>output</code> 返回曲线、标记和稀疏图层。

最重要的边界是：**指标只负责看图，不负责交易执行。**

指标不能下单、回测、运行实盘、读取账户、管理仓位、设置杠杆或执行止盈止损。需要交易时，应先把指标中的视觉信号转换成 Strategy API V2 策略，再在策略页完成验证、回测和部署。

与 TradingView/Pine 的兼容目标是：**覆盖常用单标的指标的计算与视觉语义，包括由宿主有界预取的同标的其他周期；不兼容 Pine 语法、跨标的数据、全部 Pine API 或逐 Tick 执行模型。** 从 Pine 迁移时需要把公式改写为 Python/pandas，并按本文的 <code>output</code> 契约返回结果。

---

## 1. 先完成一个最小指标

把下面代码粘贴到指标编辑器并运行：

~~~python
my_indicator_name = "Close Line"
my_indicator_description = "Displays the close price as a chart overlay."

df = df.copy()

close_line = [
    None if pd.isna(value) else float(value)
    for value in df["close"]
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Close",
            "data": close_line,
            "color": "#3B82F6",
            "type": "line",
            "overlay": True,
        }
    ],
    "signals": [],
    "layers": [],
    "tables": [],
}
~~~

这个例子展示了完整的最小契约：

1. 声明名称和描述。
2. 用 <code>df = df.copy()</code> 创建工作副本。
3. 计算与 K 线等长的数据。
4. 设置 <code>output</code> 字典。

建议新指标按“先画一条线，再加参数，再加事件标记，最后才加复杂图层”的顺序开发。

---

## 2. 指标、策略和转换流程的边界

| 产物 | 负责内容 | 不负责内容 |
| --- | --- | --- |
| Chart Indicator | 曲线、副图、灯带、视觉标记、区域、标签 | 回测、实盘、订单、仓位、杠杆、交易风控 |
| Strategy API V2 | 数据订阅、交易信号、订单意图、仓位、回测、实盘、保护规则 | 指标页的 <code>output</code> 图表渲染 |
| Indicator-to-Strategy | 把视觉信号的真实含义翻译成可执行策略 | 在原指标中混入下单逻辑 |

<code>output["signals"]</code> 只是图上的事件标记。例如 <code>sell</code> 类型的 “Death” 标记可以表示多头离场提醒，也可以表示行情转弱；它不会自动开空，更不会自动反手。

将只做多指标转换成策略时，通常采用：

- 明确的看多入场事件 → <code>open_long</code>
- 明确的看空离场事件 → <code>close_long</code>
- 只有用户明确要求做空，并提供独立的看空入场规则时，才生成 <code>open_short</code>

不要在指标里创建 <code>open_long</code>、<code>close_long</code>、<code>open_short</code>、<code>close_short</code>、<code>add_long</code> 或 <code>reduce_long</code> 等执行列，也不要使用旧式 <code># @strategy</code> 注解。

---

## 3. 运行环境与输入数据

运行时预置：

- <code>df</code>：当前图表的 pandas DataFrame，按时间从旧到新排列，每行对应一根 K 线。
- <code>params</code>：由参数声明和参数面板合并得到的字典。
- <code>pd</code>：预置的 pandas。
- <code>np</code>：预置的 numpy。
- <code>ctx</code>、<code>request_series</code> 和 <code>request_lower_tf</code>：第 18 节说明的有界同标的多周期助手。
- <code>open</code>、<code>high</code>、<code>low</code>、<code>close</code>、<code>volume</code>：部分运行入口还提供同名便捷 Series；为了可读性和可移植性，教程建议优先使用 <code>df["close"]</code>。

每次验证或预览都会在新的无凭证进程中执行指标。宿主会先解析并限制同标的多周期请求，再只发送序列化行情、参数和指标上下文。<code>request_series</code>、<code>request_lower_tf</code> 等助手读取的是宿主预取的数据，不会从指标代码内发起网络或数据库请求。进程返回经过校验的输出后立即退出，因此模块全局变量和缓存不会跨运行保留。

标准 OHLCV 字段：

~~~python
open_price = df["open"]
high = df["high"]
low = df["low"]
close = df["close"]
volume = df["volume"]
~~~

注意：

- 不要假设一定存在 <code>time</code> 列，时间也可能已经在 DataFrame 索引中。
- 不要重命名或删除 OHLCV 列。
- 可选字段必须先检查，例如 <code>if "turnover" in df.columns:</code>。
- 修改 DataFrame 前先执行 <code>df = df.copy()</code>。
- 核心序列计算优先使用 <code>rolling</code>、<code>ewm</code>、<code>shift</code>、<code>where</code> 等向量化操作。

---

## 4. 文件结构与元数据

推荐结构：

~~~python
# @param period int 20 Calculation period

my_indicator_name = "Example Indicator"
my_indicator_description = "Explains what is drawn and how events are marked."

df = df.copy()

period = int(params.get("period", 20))

# Helper functions
# Series calculation
# Marker construction

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [],
    "signals": [],
    "layers": [],
    "tables": [],
}
~~~

每个指标都应声明：

~~~python
my_indicator_name = "Dual EMA Viewer"
my_indicator_description = "Chart-only EMA crossover indicator with visual event markers."
~~~

名称应简短稳定；描述应说明：

- 计算了什么；
- 在主图还是副图显示；
- 标记代表什么事件；
- 有哪些重要参数。

不要在描述中承诺收益或暗示已通过实盘验证。

根据项目源码规则，代码标识符、元数据、注释、参数描述和默认显示标签使用英文。中文可以用于本教程正文；只有用户明确要求本地化显示标签时，指标中的展示文本才使用指定语言。

---

## 5. 参数声明与参数面板

语法：

~~~python
# @param <name> <int|float|bool|str> <default> <description>
~~~

示例：

~~~python
# @param fast_len int 12 Fast EMA period
# @param slow_len int 26 Slow EMA period
# @param band_pct float 1.5 Channel width percent
# @param show_marks bool true Show crossover markers
# @param source str close Price source
~~~

声明不会自动创建 Python 变量，必须显式读取：

~~~python
fast_len = int(params.get("fast_len", 12))
slow_len = int(params.get("slow_len", 26))
band_pct = float(params.get("band_pct", 1.5))
show_marks = bool(params.get("show_marks", True))
source = str(params.get("source", "close"))
~~~

硬性规则：

- 每行只声明一个参数。
- 参数名使用合法 Python 标识符。
- 类型只使用 <code>int</code>、<code>float</code>、<code>bool</code>、<code>str</code> 或 <code>string</code>。
- 注释中的默认值必须和 <code>params.get</code> 的回退值一致。
- 布尔值在声明中写 <code>true</code>/<code>false</code>，Python 中写 <code>True</code>/<code>False</code>。
- 字符串默认值不能包含空格，因为解析器把默认值读取为一个 token。

参数搜索可在描述末尾声明候选范围：

~~~python
# @param period int 20 Lookback period range=5:60:5
# @param multiplier float 2.0 Band multiplier values=1.5,2.0,2.5,3.0
~~~

<code>range=start:end:step</code> 是包含终点的等差候选序列；<code>values=a,b,c</code> 是显式候选列表。单个参数最多展开 1024 个候选值。描述中的范围标记会从用户可见描述中移除。

指标参数只控制计算和显示。不要声明账户、标的、仓位、杠杆、止损或止盈等执行参数。用户可选择的同标的周期只能使用第 18 节规定的字面量白名单与参数绑定；不能把任意字符串参数变成网络请求或跨标的请求。

---

## 6. output 输出契约

指标运行结束时必须设置字典类型的 <code>output</code>：

~~~python
output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": tables,
}
~~~

可选的图表样式和诊断字段：

~~~python
output["bar_colors"] = [None] * len(df)
output["background_colors"] = [None] * len(df)
output["calculatedVars"] = {}
~~~

验证要求：

- <code>output</code> 必须是字典。
- 新代码应将 <code>schema_version</code> 设为 <code>2</code>；已有版本 1 的 <code>plots</code>、<code>signals</code> 和 <code>layers</code> 继续兼容。
- 至少存在一个受支持的图表字段：<code>plots</code>、<code>signals</code>、<code>layers</code>、<code>tables</code>、<code>bar_colors</code> 或 <code>background_colors</code>。
- 每个 <code>plot["data"]</code> 的长度必须等于 <code>len(df)</code>。
- 每个 <code>signal["data"]</code> 的长度必须等于 <code>len(df)</code>。
- 如果提供 <code>bar_colors</code> 或 <code>background_colors</code>，长度也必须等于 <code>len(df)</code>。
- 序列中不要输出 NaN、正无穷或负无穷；缺失点使用 <code>None</code>。
- <code>layers</code> 不需要逐 bar 数组，但索引、时间和价格必须落在当前数据的有效语义范围内。
- <code>calculatedVars</code> 是供编辑器查看的 JSON-safe 诊断数据，本身不会渲染图形。

推荐总是显式提供空列表，这样结构最清晰：

~~~python
output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [],
    "signals": [],
    "layers": [],
    "tables": [],
}
~~~

---

## 7. plots：主图曲线与副图序列

每个 plot 至少包含：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| <code>name</code> | str | 图例和序列名称 |
| <code>data</code> | list | 与 <code>df</code> 等长的数值/<code>None</code> 列表 |
| <code>color</code> | str | 推荐使用 <code>#RRGGBB</code> |
| <code>overlay</code> | bool | <code>True</code> 主图，<code>False</code> 副图 |
| <code>type</code> | str，可选 | <code>line</code>、<code>bar</code>、<code>circle</code>、<code>cloud</code>、<code>candlestick</code> 或 <code>background</code>；别名见下表 |

当前公开绘图类型：

| 写法 | 规范化结果 | 典型 Pine 对应 |
| --- | --- | --- |
| <code>line</code> | 折线 | <code>plot(..., style=plot.style_line)</code> |
| <code>bar</code>、<code>histogram</code>、<code>column</code> | 从基准值绘制的柱 | histogram/columns |
| <code>circle</code>、<code>dot</code>、<code>point</code>、<code>scatter</code> | 圆点 | circles/scatter |
| <code>lamp</code> | 灯带圆点；同一副图有至少 3 个灯带序列时自动按行排列 | 状态面板的近似表达 |
| <code>cloud</code>、<code>band</code>、<code>fill_between</code>、<code>fill-between</code> | 填充上下两条序列之间的区域 | <code>fill(plot1, plot2)</code> |
| <code>candlestick</code>、<code>candle</code>、<code>ohlc</code> | 指标生成的 OHLC K 线 | <code>plotcandle()</code>/<code>plotbar()</code> |
| <code>background</code> | 逐 bar 图表背景 | <code>bgcolor()</code> |

样式字段按图形类型生效：

- 通用：<code>color</code>、<code>opacity</code>。
- 折线：<code>lineWidth</code>，以及 <code>lineStyle</code>/<code>style</code>（例如 <code>solid</code>、<code>dashed</code>）。
- 柱和圆点：<code>size</code>/<code>radius</code>、<code>borderColor</code>、<code>borderSize</code>；柱还可设置 <code>baseValue</code>。
- 云带：每个点使用 <code>upper</code> 和 <code>lower</code>，可逐点设置 <code>color</code>；<code>opacity</code> 的有效范围为 <code>0.02</code> 至 <code>0.30</code>。设置 <code>title=False</code> 可隐藏云带图例。
- K 线：每个点为 <code>None</code>，或包含有限数值的 <code>open</code>、<code>high</code>、<code>low</code>、<code>close</code>，并可设置 <code>color</code>、<code>fillColor</code>、<code>borderColor</code> 和 <code>wickColor</code>。
- 背景：使用透明度克制的逐 bar 颜色，确保宿主 K 线、信号和文字可读。给宿主图表着色时，通常直接设置 <code>output["background_colors"]</code> 更简单。
- 同一指标内所有 <code>overlay=False</code> 的序列共享一个副图，不会像多个独立 Pine 脚本那样自动创建多个副图。

需要 Pine 的 series color/动态点大小时，<code>data</code> 的单点可以从数值扩展为对象：

~~~python
dynamic_points = [
    None,
    {"value": 102.5, "color": "#22C55E", "size": 4},
    {"value": 101.8, "color": "#EF4444", "size": 6},
]
~~~

对象中的数值键可使用 <code>value</code>、<code>y</code> 或 <code>data</code>；颜色键可使用 <code>color</code>、<code>fillColor</code> 或 <code>backgroundColor</code>；大小键可使用 <code>size</code>、<code>radius</code> 或 <code>r</code>。推荐在新代码中只使用第一组规范字段 <code>value/color/size</code>。

### 填充云带

云带的每个点是包含 <code>upper</code> 和 <code>lower</code> 的对象，列表长度仍须等于 <code>len(df)</code>。需要中断云带的预热区或震荡区应输出 <code>None</code>，避免跨越空白区错误连线。渲染器会自动排序上下值，因此两条线交叉时云带仍能正确显示。

~~~python
# @param cloud_opacity float 0.10 Cloud opacity range=0.03:0.24:0.01

cloud_opacity = min(0.24, max(0.03, float(params.get("cloud_opacity", 0.10))))
trend_cloud = []

for index in range(len(df)):
    fast_value = ema_fast.iloc[index]
    slow_value = ema_slow.iloc[index]

    if pd.isna(fast_value) or pd.isna(slow_value) or not bool(trending.iloc[index]):
        trend_cloud.append(None)
        continue

    trend_cloud.append(
        {
            "upper": float(max(fast_value, slow_value)),
            "lower": float(min(fast_value, slow_value)),
            "color": "#22C55E" if fast_value >= slow_value else "#F43F5E",
        }
    )

plots = [
    {
        "name": "趋势云",
        "title": False,
        "data": trend_cloud,
        "color": "#22C55E",
        "type": "cloud",
        "overlay": True,
        "opacity": cloud_opacity,
    },
]
~~~

规范字段为 <code>upper/lower/color</code>。上沿兼容 <code>top</code>、<code>high</code>、<code>value</code>、<code>y</code>、<code>data</code>，下沿兼容 <code>bottom</code>、<code>low</code>。新指标应优先使用规范字段。

示例：

~~~python
plots = [
    {
        "name": "EMA Fast",
        "data": fast_values,
        "color": "#22C55E",
        "type": "line",
        "overlay": True,
    },
    {
        "name": "RSI",
        "data": rsi_values,
        "color": "#8B5CF6",
        "type": "line",
        "overlay": False,
    },
]
~~~

价格均线、布林带和通道通常使用主图；RSI、MACD 和状态灯带通常使用副图。

统一处理空值：

~~~python
def to_plot_list(series):
    return [
        None if pd.isna(value) else float(value)
        for value in series
    ]
~~~

不要把价格叠加线的预热空值填成 0，否则图上会出现从零点拉到真实价格的误导性线段。

---

## 8. signals：稀疏视觉事件

signal 示例：

~~~python
signals = [
    {
        "type": "buy",
        "text": "Long Entry",
        "color": "#22C55E",
        "data": entry_marks,
    },
    {
        "type": "sell",
        "text": "Long Exit",
        "color": "#EF4444",
        "data": exit_marks,
    },
]
~~~

规则：

- <code>type</code> 通常为 <code>buy</code> 或 <code>sell</code>，只控制标记方向，不是信号名称。
- <code>text</code> 是稳定的信号名；可选 <code>textData</code> 可为每根 bar 提供不同标签。
- 最稳定、最便于通知系统识别的写法，是用 <code>data[i]</code> 中的有限非零价格激活第 i 根 bar 的信号。
- <code>text</code> 或 <code>textData</code> 本身不会激活信号。
- 无信号位置必须使用真实的 <code>None</code>。
- 默认标记一次性事件，不要在条件持续为真时每根 bar 都重复标记。

兼容格式也允许 <code>True</code>，或 <code>{"active": True, "price": ..., "text": ..., "color": ...}</code>。布尔值没有价格时，渲染器会按当前 K 线高低点定位。为了让图表预览、保存后的信号监控和未来客户端保持一致，新指标优先使用“价格或 <code>None</code>”格式；只有确实需要逐点文本/颜色时才使用对象。

<code>renderMode</code> 控制持续条件如何变成标记：

| 值 | 行为 |
| --- | --- |
| <code>events</code>（显式事件模式） | 每个非空事件点都显示并可触发通知 |
| <code>points</code> / <code>markers</code> / <code>raw</code> | 明确保留每个激活点 |
| <code>state</code> / <code>continuous</code> / <code>condition</code> | 只在 False → True 的边沿显示并通知 |

如果不指定模式，运行时会把高密度激活序列识别为持续状态并自动做边沿化。公共指标不要依赖这个启发式规则；应直接生成稀疏事件，或显式设置 <code>renderMode</code>。

把状态转换成边沿事件：

~~~python
def edge(condition):
    current = condition.fillna(False).astype(bool)
    previous = current.shift(1, fill_value=False).astype(bool)
    return current & ~previous
~~~

生成价格标记：

~~~python
entry_event = edge(ema_fast > ema_slow)
exit_event = edge(ema_fast < ema_slow)

entry_marks = [
    float(df["low"].iloc[i] * 0.995)
    if bool(entry_event.iloc[i])
    else None
    for i in range(len(df))
]

exit_marks = [
    float(df["high"].iloc[i] * 1.005)
    if bool(exit_event.iloc[i])
    else None
    for i in range(len(df))
]
~~~

如果要求“确认后下一根显示”：

~~~python
confirmed_entry = edge(raw_entry).shift(
    1,
    fill_value=False,
).astype(bool)
~~~

这只是把已确认事件向后移动一根，并没有读取未来数据。

---

## 9. layers：区域、线段、标签和折线对象

普通指标优先使用 plots 和 signals。只有在供需区、支撑阻力、通道、失效位或结构标签确实能提高可读性时才使用 layers。

区域：

~~~python
{
    "type": "zone",
    "startIndex": 120,
    "endIndex": 180,
    "top": 105.2,
    "bottom": 101.8,
    "text": "Demand",
    "fillColor": "#22C55E",
    "borderColor": "#22C55E",
    "opacity": 0.12,
}
~~~

水平线：

~~~python
{
    "type": "line",
    "startIndex": 100,
    "endIndex": len(df) - 1,
    "price": 98.5,
    "text": "Support",
    "color": "#F59E0B",
    "dashed": True,
}
~~~

斜线把 <code>price</code> 换成 <code>startPrice</code> 和 <code>endPrice</code>。标签：

~~~python
{
    "type": "label",
    "index": len(df) - 1,
    "price": float(df["close"].iloc[-1]),
    "text": "Trend Weakens",
    "color": "#EF4444",
    "textColor": "#FFFFFF",
}
~~~

折线对象：

~~~python
{
    "type": "polyline",
    "points": [
        {"index": 100, "price": 98.5},
        {"index": 120, "price": 101.2},
        {"index": 145, "price": 99.8},
    ],
    "color": "#38BDF8",
    "lineWidth": 2,
}
~~~

索引写法对当前 <code>df</code> 最稳定。也支持与 K 线时间戳匹配的 <code>startTime</code>、<code>endTime</code> 和 <code>time</code>。

图层仍然只是视觉对象，不能表示真实订单、仓位或已托管止损。

---

## 10. 与 TradingView/Pine 的能力对应

QuantDinger 不执行 Pine 源码。迁移目标是保持指标含义、数值时序和视觉表达一致，再用 Python/pandas 实现。以下是当前契约的真实边界：

| TradingView/Pine 能力域 | 当前状态 | QuantDinger 写法或限制 |
| --- | --- | --- |
| OHLCV、历史引用、滚动窗口 | 支持 | <code>df</code>、<code>shift</code>、<code>rolling</code>、<code>ewm</code> |
| <code>input.int/float/bool/string</code> | 支持主要语义 | <code># @param</code>；暂不支持 Pine 的 group、inline、confirm 和专用 color/source/timeframe/symbol 输入控件 |
| <code>ta.*</code> 常用技术指标 | 计算语义可实现 | 使用 pandas/numpy；指标运行时暂不提供与 Pine 同名的 <code>ta</code> 命名空间，迁移时必须核对平滑算法、预热值和 NaN 规则 |
| <code>plot()</code>、histogram、circles | 支持常用子集 | <code>plots</code> 的 line/bar/circle，支持主图/副图和逐点颜色/大小 |
| 两条 plot 之间的 <code>fill()</code> | 支持 | 使用 <code>cloud</code> plot，逐点提供 <code>upper</code>/<code>lower</code>，并可设置逐点颜色 |
| <code>plotshape()</code>、<code>plotchar()</code> | 支持主要事件语义 | 使用 <code>signals</code>；动态文字用 <code>textData</code> 或点对象 |
| <code>hline()</code>、line、box、label | 支持常用静态结果 | 使用 line/zone/label layers；每次运行返回最终对象列表，没有 Pine 对象 ID、修改方法或垃圾回收语义 |
| <code>alert()</code>、<code>alertcondition()</code> | 部分支持 | 指标输出 <code>signals</code>，保存后由用户配置监控通知；指标代码本身不直接发送通知或下单 |
| arrays、maps、用户函数与状态计算 | Python 等价能力 | 使用 list/dict、函数、pandas Series；这是整段 DataFrame 批量计算，不是 Pine 的逐 bar <code>var</code>/<code>varip</code> 执行模型 |
| linefill、<code>bgcolor()</code>、<code>barcolor()</code> | 支持常用子集 | 使用 cloud、<code>background_colors</code> 和 <code>bar_colors</code>；Pine 渐变和全部显示标志不会自动复刻 |
| area/step/cross/arrow、<code>plotcandle()</code>/<code>plotbar()</code> | 部分支持 | 已支持 candlestick/OHLC；area、step、cross、arrow 仍需明确转换为原生近似形式 |
| table、polyline、动态 drawing 对象生命周期 | 部分支持 | 已支持固定 table 和 polyline；Pine 状态对象要按时间顺序重放并输出有界的最终对象列表 |
| <code>request.security()</code>、<code>request.*()</code>、跨标的/多周期 | 支持同标的子集 | 使用有界预取的 <code>request_series</code>/<code>request_lower_tf</code>；跨标的、宏观数据、网络访问和任意 Pine request API 仍不支持 |
| <code>barstate.*</code>、逐 tick 更新和 rollback | 不同执行模型 | 当前按收到的完整 DataFrame 批量重算；应以已完成 K 线事件作为可复现基准 |
| strategy.*、订单、仓位和回测 | 不属于指标 | 转换到 Strategy API V2 |

因此，“大部分功能一致”的推荐验收口径是：常见单标的指标在相同 OHLCV、参数、已完成 K 线和明确请求的同标的周期输入上得到等价数值和事件。不要求 Pine 源码直接运行；跨标的或宏观请求、Tick 回滚、外部 Pine 库和未支持的绘图样式仍不在兼容范围内。

迁移 Pine 指标时逐项核对：

1. 明确 Pine 版本、输入参数、源序列和指标所在周期。
2. 对齐 SMA/EMA/RMA/WMA、标准差、ATR 和交叉等函数的精确定义；不要只比较函数名称。
3. 对齐预热期、<code>na</code> 传播、除零处理和首次有效 bar。
4. 把持续条件与一次性事件分开；Pine 的条件序列不能直接当成每根 bar 的重复通知。
5. 用固定 OHLCV 样本比较序列容差、事件 bar 索引和图层锚点。
6. Pine 脚本包含未支持能力时，必须删减、近似表达，或先扩展平台契约，不能在指南或代码注释中声称完全一致。

Pine 能力分类可参考 TradingView 官方文档：[Visuals overview](https://www.tradingview.com/pine-script-docs/visuals/overview/)、[Inputs](https://www.tradingview.com/pine-script-docs/concepts/inputs/)、[Other timeframes and data](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/)、[Bar states](https://www.tradingview.com/pine-script-docs/concepts/bar-states/)。

---

## 11. pandas 与 numpy 类型陷阱

最常见的错误是把 numpy ndarray 当成 pandas Series。

错误：

~~~python
values = np.where(close > close.shift(1), close, 0)
average = values.rolling(10).mean()
~~~

<code>np.where</code> 可能返回 ndarray，而 ndarray 没有 <code>rolling</code>、<code>shift</code>、<code>ewm</code>、<code>fillna</code> 或 <code>iloc</code>。

优先使用 pandas 原生写法：

~~~python
values = close.where(close > close.shift(1), 0)
average = values.rolling(10).mean()
~~~

必须包装 ndarray 时：

~~~python
array = np.where(close > close.shift(1), close, 0)
values = pd.Series(array, index=df.index)
~~~

一定传入 <code>index=df.index</code>。否则新 Series 使用 RangeIndex，与 DatetimeIndex 数据做运算时会静默错位。

常用替换：

| numpy 写法 | pandas 优先写法 |
| --- | --- |
| <code>np.where(cond, a, b)</code> | <code>a.where(cond, b)</code> |
| <code>np.maximum(s, 0)</code> | <code>s.clip(lower=0)</code> |
| <code>np.minimum(s, k)</code> | <code>s.clip(upper=k)</code> |
| <code>np.abs(s)</code> | <code>s.abs()</code> |

---

## 12. 避免未来数据和重绘

指标只能使用当前及历史 bar。禁止：

- <code>shift(-1)</code>、<code>shift(-N)</code>；
- 循环中的 <code>iloc[i + 1]</code>；
- <code>bars_ago(-N)</code>；
- <code>rolling(..., center=True)</code>；
- 用完整数据集最后一行反向修改历史信号；
- 任何利用未来最高价、最低价或未来确认结果标记过去 bar 的写法。

合法确认通常使用当前条件与上一根状态：

~~~python
cross_up = (
    (ema_fast > ema_slow)
    & (ema_fast.shift(1) <= ema_slow.shift(1))
)
~~~

如果信号必须等当前 bar 收盘才能确定，转换成策略后应在下一根 bar 执行，不要为了让图形更漂亮而把信号提前。

---

## 13. 沙箱与安全限制

允许的计算模块包括 numpy、pandas、math、json、datetime、time、collections、functools、itertools、statistics、decimal、fractions 和 copy。<code>pd</code> 与 <code>np</code> 已预置，通常无需 import。

静态校验只是第一层。指标执行使用 <code>python -I</code>、干净环境、关闭继承文件描述符、临时工作目录、仅 JSON 输入输出，以及硬性的超时、内存、文件大小和输出大小限制。在受支持的 Linux 部署中，工作进程会在运行指标源码前启用 seccomp 和 Landlock。它不能读取应用目录或任意宿主机文件，不能创建网络连接或子进程，也不会继承数据库、交易所、模型提供商、JWT 或凭证加密密钥。内核限制无法启用时，执行会失败关闭。

禁止：

- 网络、文件、数据库和子进程访问；
- <code>eval</code>、<code>exec</code>、<code>compile</code>、<code>open</code>；
- 反射、动态导入、dunder 逃逸和沙箱绕过；
- pandas/numpy 的文件读取、写入和序列化方法；
- <code>os</code>、<code>sys</code>、<code>requests</code>、<code>socket</code>、<code>subprocess</code>、<code>threading</code>、<code>sqlite3</code>、<code>pathlib</code>、<code>pickle</code>、<code>ctypes</code>、<code>operator</code> 等模块。

以下完整标识符属于保留能力，即使只是作为值引用、作为回调传递或复用为局部名称也会被拒绝：<code>eval</code>、<code>exec</code>、<code>compile</code>、<code>__import__</code>、<code>getattr</code>、<code>setattr</code>、<code>delattr</code>、<code>globals</code>、<code>vars</code>、<code>dir</code>、<code>breakpoint</code>、<code>open</code>、<code>input</code>、<code>exit</code>、<code>quit</code>、<code>chr</code> 和 <code>format</code>。校验按完整标识符匹配，因此 <code>open_long</code>、<code>format_signal</code>、<code>dir_value</code> 等描述性名称仍然合法。不要通过变量、字符串拼接、字符串乘法或 f-string 间接构造 dunder 名称。

指标验证有超时限制。避免无界循环、递归爆炸和逐行执行的高复杂度算法。

标准验证流程采用 20 秒超时、1 GiB 地址空间和 32 MiB 编码输出上限；其他指标流程可以定义不同但仍有界的超时。超时或资源超限会终止一次性进程。不要依赖文件、进程环境、模块全局缓存、后台线程或上一次预览留下的状态。非 Linux 原生执行不能替代官方 Linux 沙箱，也不得配置为降级到进程内执行。

---

## 14. 完整教程：双 EMA 交叉指标

~~~python
# @param fast_len int 12 Fast EMA period range=5:30:1
# @param slow_len int 26 Slow EMA period range=10:80:2
# @param confirm_next_bar bool false Show markers one bar after confirmation
# @param show_marks bool true Show crossover markers

my_indicator_name = "Dual EMA Viewer"
my_indicator_description = "Displays two EMAs and marks confirmed crossover events."

df = df.copy()

fast_len = int(params.get("fast_len", 12))
slow_len = int(params.get("slow_len", 26))
confirm_next_bar = bool(params.get("confirm_next_bar", False))
show_marks = bool(params.get("show_marks", True))

close = df["close"]
high = df["high"]
low = df["low"]


def edge(condition):
    current = condition.fillna(False).astype(bool)
    previous = current.shift(1, fill_value=False).astype(bool)
    return current & ~previous


def to_plot_list(series):
    return [
        None if pd.isna(value) else float(value)
        for value in series
    ]


ema_fast = close.ewm(span=fast_len, adjust=False).mean()
ema_slow = close.ewm(span=slow_len, adjust=False).mean()

golden = edge(ema_fast > ema_slow)
death = edge(ema_fast < ema_slow)

if confirm_next_bar:
    golden = golden.shift(1, fill_value=False).astype(bool)
    death = death.shift(1, fill_value=False).astype(bool)

golden_marks = [
    float(low.iloc[i] * 0.995)
    if show_marks and bool(golden.iloc[i])
    else None
    for i in range(len(df))
]

death_marks = [
    float(high.iloc[i] * 1.005)
    if show_marks and bool(death.iloc[i])
    else None
    for i in range(len(df))
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {
            "name": "EMA Fast",
            "data": to_plot_list(ema_fast),
            "color": "#22C55E",
            "type": "line",
            "overlay": True,
        },
        {
            "name": "EMA Slow",
            "data": to_plot_list(ema_slow),
            "color": "#3B82F6",
            "type": "line",
            "overlay": True,
        },
    ],
    "signals": [
        {
            "type": "buy",
            "text": "Long Entry",
            "color": "#22C55E",
            "data": golden_marks,
            "renderMode": "events",
        },
        {
            "type": "sell",
            "text": "Long Exit",
            "color": "#EF4444",
            "data": death_marks,
            "renderMode": "events",
        },
    ],
    "layers": [],
    "tables": [],
}
~~~

逐步理解：

1. 参数声明决定参数面板和搜索范围。
2. <code>params.get</code> 读取与声明完全一致的默认值。
3. 两条 EMA 是持续状态，因此放进 plots。
4. 金叉和死叉是一次性事件，因此经过 <code>edge</code> 后放进 signals。
5. 空标记使用 <code>None</code>。
6. 指标明确把死叉命名为 “Long Exit”，避免转换策略时误解成开空。

---

## 15. 验证、调试和常见错误

建议每次按以下顺序：

1. 保存版本。
2. 运行/预览指标。
3. 执行代码验证。
4. 检查图上起始空值、极端行情和短数据区间。
5. 修改参数，确认参数确实影响结果。
6. 检查信号是否只在事件 bar 出现。

| 提示或错误 | 原因 | 修复 |
| --- | --- | --- |
| <code>EMPTY_CODE</code> | 代码为空 | 提供完整指标源码 |
| <code>MISSING_OUTPUT</code> | 没有设置 <code>output</code> | 添加字典输出 |
| <code>MissingOutput</code> | 执行后未得到输出变量 | 检查分支和变量作用域 |
| <code>InvalidOutputType</code> | <code>output</code> 不是字典 | 改为 dict |
| <code>InvalidOutputStructure</code> | 没有受支持的图表字段 | 至少提供 plots、signals、layers、tables、bar colors 或 background colors 之一 |
| <code>LengthMismatch</code> | 序列长度与 K 线不一致 | 让每个 data 等于 <code>len(df)</code> |
| <code>MISSING_DF_COPY</code> | 缺少工作副本 | 在计算前添加 <code>df = df.copy()</code> |
| <code>PARAM_DEFAULT_MISMATCH</code> | 声明和读取默认值不同 | 对齐两个默认值 |
| <code>DECLARED_PARAMS_NOT_READ_VIA_PARAMS_GET</code> | 声明后没有读取 | 显式调用 <code>params.get</code> |
| <code>EXECUTION_COLUMNS_IGNORED_FOR_INDICATOR</code> | 指标中写了交易执行列 | 删除并转换成 V2 策略 |
| <code>STRATEGY_ANNOTATIONS_IGNORED_FOR_INDICATOR</code> | 使用旧策略注解 | 删除旧注解 |
| <code>NDARRAY_PANDAS_METHOD_MISUSE</code> | ndarray 被当作 Series | 用 pandas 写法或包装并保留索引 |
| <code>FUTURE_DATA_LEAK</code> | 检测到未来数据 | 改为只使用当前和历史数据 |

---

## 16. 转换成策略前的语义清单

转换前明确回答：

- 哪个标记是多头入场？
- 哪个标记是多头离场？
- 是否真的需要做空？看空离场不能自动等同于做空入场。
- 是否需要反手？如果需要，平仓和反向开仓是否为两个独立动作？
- 信号在哪个周期、哪根已收盘 bar 确认？
- 是否允许重复入场、加仓或减仓？
- 仓位大小、止损、止盈和追踪止损如何定义？
- 交易标的和市场类型是什么？
- 如果是交易所股票，执行使用哪个准确的目录产品、交易所、币种、产品类型和 API family？必须保留 <code>Crypto:00700/HKD@gate:spot</code> 或目录确认的 <code>Crypto:HK0700/USDT@binance:swap</code> 等完整标识，不能替换成底层股票。
- 它是只做多的股票现货，还是必须声明方向 metadata 和 <code>position_side</code> 的 Crypto swap 衍生品？

转换后应删除图表专用的颜色、标签偏移、plots、layers 和 marker 数组，保留信号代数，并用 Strategy API V2 明确声明标的、周期、仓位和风险。

只有当前产品目录提供有效 <code>USStock</code> 或 <code>HKStock</code> 映射时，底层股票数据才能用于图表历史行情和时点基本面。数据映射不会改变执行标的，转换器也不能根据显示名称猜测地区。

生成的策略必须重新验证和回测。发布到市场前，系统要求至少有一条成功回测记录。

---

## 17. 发布前检查清单

- [ ] 名称和描述存在，且不包含收益承诺。
- [ ] 代码注释、标识符、元数据和默认标签为英文。
- [ ] <code>df = df.copy()</code> 已执行。
- [ ] 每个参数都通过 <code>params.get</code> 读取，默认值一致。
- [ ] 不包含订单、仓位、杠杆或交易风控代码。
- [ ] <code>output</code> 是字典。
- [ ] 新指标使用 <code>schema_version: 2</code>；table 和图表样式数组使用规范字段。
- [ ] 每个 plot/signal data 长度都等于 <code>len(df)</code>。
- [ ] 如果存在 <code>bar_colors</code> 或 <code>background_colors</code>，长度等于 <code>len(df)</code>。
- [ ] 缺失值使用 <code>None</code>，没有 NaN 或无穷值输出。
- [ ] signals 表示稀疏事件，持续状态放在 plots 或少量 layers。
- [ ] 重复标签、区域、计划和目标采用有界的近期显示预算，不遮挡 K 线。
- [ ] 价格叠加序列不会用过远的陈旧值把当前行情压缩成窄条。
- [ ] 除非原始公式明确要求渐进窗口，否则 rolling 指标在完整预热前保持不可用。
- [ ] 不读取未来数据；同标的周期请求保持 <code>lookahead="off"</code>。
- [ ] 周期请求使用字面量或文档规定的白名单绑定，不超过 8 个，并处理覆盖不足。
- [ ] numpy 结果在调用 pandas 方法前已转换成带正确索引的 Series。
- [ ] 短数据、预热区和异常参数不会导致崩溃。
- [ ] 已保存版本、通过运行验证，并且 1H 和 4H 预览保持清晰可读。

---

## 18. 统一图表输出与同标的多周期数据

新指标应将 <code>schema_version</code> 设为 <code>2</code>。已有版本 1 的
<code>plots</code>、<code>signals</code> 和 <code>layers</code> 继续兼容。版本 2 增加：

- <code>candlestick</code> 图层，每个点包含 <code>open</code>、<code>high</code>、<code>low</code>、<code>close</code>，并可分别设置实体、边框和影线颜色；
- 使用 <code>bar_colors</code> 设置宿主 K 线颜色，使用 <code>background_colors</code> 设置逐 bar 图表背景；
- 固定在 <code>top-left</code>、<code>top-right</code>、<code>bottom-left</code> 或 <code>bottom-right</code> 的 <code>tables</code>；
- 由索引和价格点组成的 <code>polyline</code> 图层。

紧凑 table 示例：

~~~python
tables = [{
    "id": "status",
    "position": "top-right",
    "title": "Trend Status",
    "columns": ["Metric", "Value"],
    "rows": [
        ["Trend", {"text": "Bullish", "color": "#22C55E"}],
        ["Strength", "Moderate"],
    ],
    "compact": True,
}]
~~~

同一交易标的的其他周期由宿主预取，指标代码仍在隔离沙箱中运行：

~~~python
daily_close = request_series("1D", field="close")
daily_ohlcv = request_series("1D")
minute_records = request_lower_tf("1m")
minute_volume = request_lower_tf("1m", fields="volume")
minute_fields = request_lower_tf("1m", fields=["open", "close", "volume"])
~~~

提供 <code>field</code> 时，<code>request_series</code> 返回与 <code>df</code> 对齐的 pandas Series；省略 <code>field</code> 时返回对齐的 OHLCV DataFrame。默认 <code>lookahead="off"</code> 时，高周期值只有在对应高周期 K 线收盘后才可用。计算、信号、通知和普通视觉显示都必须保持 lookahead 关闭。

<code>request_lower_tf</code> 有三种返回结构：

- 不提供 <code>fields</code>：列表中的每一项是当前图表 K 线对应的低周期 OHLCV 字典列表；
- 提供一个字符串字段：列表中的每一项是当前图表 K 线对应的字段值列表；
- 提供字段列表：返回字典，每个字段映射到上述逐图表 K 线的值列表。

支持的周期为 <code>1m</code>、<code>3m</code>、<code>5m</code>、<code>15m</code>、
<code>30m</code>、<code>1H</code>、<code>2H</code>、<code>4H</code>、<code>6H</code>、
<code>8H</code>、<code>12H</code>、<code>1D</code> 和 <code>1W</code>。一个指标最多请求 8 个不同的周期/模式组合。

直接调用必须使用字面量周期，宿主才能在执行前发现请求。用户可选择周期时，要声明完整字面量白名单并绑定字符串参数：

~~~python
# @param confirmation_tf str 4H Confirmation timeframe values=1H,4H,1D

indicator_timeframes = ["1H", "4H", "1D"]
indicator_timeframe_param = "confirmation_tf"
confirmation_tf = str(params.get("confirmation_tf", "4H"))

confirmation_close = request_series(confirmation_tf, field="close")
~~~

低周期选择器使用 <code>indicator_lower_timeframes</code> 和 <code>indicator_lower_timeframe_param</code>。实际选择值必须属于声明的白名单。

宿主预取历史会保持“最近且有界”：普通周期序列最多取 1,000 根，低周期数据最多取 5,000 根；一次预览中每个请求周期最多触发一次宿主拉取。数据源限流、可用范围和数量上限都可能让较早的图表 K 线得到 <code>NaN</code> 或空数组。应将其视为数据不可用，不能循环分页、反复请求或用 0 代替。

为保证视觉正确，长周期水平或统计值不能只用一两根样本就判定有效。除非原始公式明确要求渐进窗口，否则 <code>min_periods</code> 应等于声明的 lookback。重复事件标签和历史 drawing 对象要保持近期、稀疏并按 bar 间隔；辅助序列使用 <code>title=False</code> 隐藏图例；会拉伸价格轴的远端陈旧水平不应继续绘制。需要保留的完整非视觉诊断可放入 <code>calculatedVars</code>。

该服务不提供其他标的、宏观数据、外部 Pine 库、未收盘 bar 回滚或 Tick 数据。低周期数组是历史 OHLCV，不能还原一根 K 线内部真实逐笔成交顺序。
