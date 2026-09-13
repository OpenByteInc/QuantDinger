# AMS 估值主脉络

AMS 的核心不是单纯查报表，而是围绕产品持续维护一条头寸链：

```text
输入数据 -> 估值计算 -> 头寸 / 净值 / 指标 / 分析结果
```

## 核心估值思路

估值计算的基本逻辑是：

```text
某日头寸 =
  上一日头寸
  + 当日交易/事件变化
  + 当日行情和估值价格
```

如果用户导入了估值表，则估值表可以直接作为某日头寸输入：

```text
某日头寸 = 当日估值表
```

输入主要分两类：

- 变动输入：交易流水、托管事件、份额事件、交割单、模拟交易信号
- 截面输入：估值表、持仓报表、起始日头寸

输出主要包括：

- 头寸结果：balance、asset snapshot、持仓明细
- 净值、指标和基准：单位净值、收益、回撤、风险指标、自定义指标、自定义基准
- 分析结果：绩效归因、交易分析、投资概览
- 运营结果：对账结果、周报等报告

## 模块位置

对象层：

- workspace：业务空间，不直接参与估值计算
- product / product group：估值对象，输入、计算和输出都挂在产品或产品组下

输入层：

- trade / settlement trade：记录产品交易，是头寸变化来源
- custodian event / unit event：记录非普通交易类变化，例如托管事件、份额变化；申购、赎回等托管事件会影响现金、份额和净值，具体写入前确认和净值补填规则见 `custodian_event_workflow.md`
- valuation report / position statement：提供某一天的完整估值或持仓状态，可作为头寸起点或校准点
- paper trading signal：模拟交易场景下的特殊输入
- customized instrument / customized-instrument-price：补充系统默认证券之外的自定义合约和公允价输入
- valuation report fair value：估值表持仓中的 `fair_value` / `fair_value_setl_ccy` 是估值表字段，不是独立接口；用于上传或覆盖估值表时走 valuation report 流程

计算层：

- recompute balance：从指定日期重新推进头寸链
- 估值计算：根据上一日头寸、当日变动和价格生成新头寸
- 实时头寸计算：基于最近可用头寸、盘中流水和实时行情生成实时结果

结果层：

- balance / balance series / asset snapshot：查看某日、某段时间或实时的资产和头寸结果
- indicator / indicator series：基于头寸和净值进一步计算收益、风险、净值等指标
- customized indicator：维护产品或产品组下的自定义指标结果口径
- customized benchmark：维护用于收益比较、归因或概览展示的自定义基准
- performance attribution / returns decomposition / trading analysis：解释收益来源、交易贡献和组合变化
- investment overview：从产品组或组合视角做汇总展示
- reconciliation：比较 AMS 计算结果和外部或托管数据是否一致
- reports：把估值、净值、指标等结果打包成报告

## Agent 判断顺序

先判断用户需求落在哪一层，再查具体命令：

```text
查空间或产品 -> workspace / product / product group
导入流水、估值表、持仓、事件或自定义合约价格 -> 输入层
录入或修正托管事件 -> 先读 `references/custodian_event_workflow.md`
维护自定义合约公允价 -> customized-instrument-price
处理估值表持仓公允价 -> valuation-report 中的 `fair_value` / `fair_value_setl_ccy`
处理对账价格差异 -> 先读 `references/reconciliation_workflow.md` 中的价格差异规则
要求重新计算、修正历史结果 -> recompute balance
查某日或实时头寸 -> balance / asset snapshot
查净值、收益、风险指标 -> indicator / indicator series
解释收益或交易贡献 -> attribution / decomposition / trading analysis
维护自定义指标或自定义基准 -> 结果层
检查数据是否一致 -> reconciliation / 结果层
下载或生成材料 -> reports / 结果层
模拟交易相关 -> paper trading
```

确定模块后，再阅读 `rqams-cli/SKILL.md` 和 CLI 文档确认命令命名、payload、文件路径字段和输出格式。
