# 下单单位、精度与港股整手规则核对

核对日期：2026-11-26。范围：PC 闪电交易、手机股票交易、券商适配器和策略执行。期权不在范围内；期货策略保持关闭。

## 结论与完成范围

- 券商顺序已调整为 Alpaca → Forthright → Tiger → Webull → IBKR，PC 与手机共用列表分别同步。
- 两端直接券商的“金额换算股数”已改用十进制整数运算后向下取整，修复浮点边界少算一个数量单位的问题；例如 0.29 / 0.01 应为 29，不是 28。PC 数量增减步长与当前券商数量精度一致。
- **港股整手链路尚不完整。** 目前港股前端按 1 股取整；方德策略数量按 1 股取整；策略引擎无显式规则时也默认 1 股。这不等于取得该股票的每手股数。此次核对没有补装每手股数数据源，也没有把它标为已完成。
- 数量的小数位、价格的最小变动、最低数量、最低金额是四个独立限制。页面显示小数位不能代替交易规则。

## 各市场的单位

| 市场 | 数量单位 | 数量/最低单位 | 价格与金额 | 当前实现及边界 |
| --- | --- | --- | --- | --- |
| 加密现货 | 基础币，例如 BTC | 按交易所、交易对读取步长和最小数量；还可能有最小名义金额 | BTC/USDT 的价格是 USDT/BTC，金额是 USDT，不能标成 USD | 后端有 InstrumentRules 和各交易所适配；前端通用小数位不代表每个交易对都支持该精度 |
| 加密永续 | 系统通常用基础币，交易所可能收张数 | 需要合约乘数换算，再校验张数/数量步长与最低金额 | PC 保证金输入与名义金额分开，名义金额约为保证金 × 杠杆 | 不能把币数量、张数和保证金共用同一个字段含义 |
| 美股 | 股，支持时可为小数股 | 能否碎股取决于券商、股票、订单类型、时段和账户权限 | 普通美元柜台：价格 USD/股，预算 USD | 各券商能力不相同，详见下表 |
| 港股 | 股；一手包含该股票规定的整数股数 | 普通整手单按每手股数的整数倍；碎股是另一种交易安排 | 当前系统港股股票路径按 HKD 柜台处理：HKD/股、HKD 预算 | 目前缺少动态每手股数和价格档位规则，不能扩展声称覆盖其他币种柜台 |
| 交易所期货 | 手/张，即到期合约数量 | 当前方德入口为正整数；价格须符合该合约 tick | 报价可能是指数点、美元/桶、美分等；账户保证金是另一种单位 | 使用实际到期合约及已核验价格元数据。期货策略未开放 |

加密最小值没有统一的“8 位小数”或“5 USDT”标准，应以对应交易对的数量过滤器和金额过滤器为准。[Binance 官方过滤器](https://developers.binance.com/en/docs/products/spot/filters)

MES 示例：1 张对应每指数点 5 USD，最小价格跳动 0.25 点，因此每跳盈亏 1.25 USD；这些都不是“开一张只需 1.25 USD 保证金”。保证金需要读账户/合约实际要求。[CME 合约规格](https://www.cmegroup.com/markets/equities/sp/micro-e-mini-sandp-500.contractSpecs.html)

## 美股券商差异：接口能力与当前实现分开看

| 券商 | 已查证/当前限制 | 当前金额下单路径 |
| --- | --- | --- |
| Alpaca | 官方 qty 最多 9 位小数，USD notional 最多 2 位；小数股须核对 fractionable 等条件。PC 通用输入当前保留 8 位，是系统上限，不是官方上限 | 市价使用原生 notional；后端限价金额路径仍换算为数量（9 位向下截断） |
| Forthright | 下单文档明确 US 数量最多 4 位小数；这不能证明所有美股标的都可碎股 | PC/手机按价格换算 4 位数量，提交 order_qty；接口未声明统一最低美元金额 |
| Tiger | SDK 支持 quantity + total_quantity_scale 表示小数股；当前适配器没有接该缩放字段，UI 按整数股 | 系统换算为整数股，不宣称已接入 Tiger 原生股票金额单或碎股功能 |
| Webull | 官方下单接口支持 QTY/AMOUNT 和美股碎股；当前适配器固定 QTY，前端使用 6 位数量精度 | 系统换算为数量；6 位是当前实现值，不能作为所有地区账户/标的均支持的结论。仍缺完整标的碎股资格与最小金额预检 |
| IBKR | 保留原适配路径；本次不改其能力与订单语义 | 手机交易选择器仍不纳入 IBKR |

资料：[Alpaca 碎股](https://docs.alpaca.markets/us/docs/fractional-trading)、[方德下单](https://dev-openapi-doc.techfx88.com/zh/trade/order/place_order.html)、[Tiger 下单](https://docs-en.itigerup.com/docs/place-order-java)、[Webull 下单](https://developer.webull.com/apis/docs/reference/common-order-place/)。

## 港股为什么可能影响策略成交

港交所说明每手股数由发行人确定，不能统一写成 100。碎股不参加普通整手自动撮合，有单独的交易安排，流动性和成交价也可能不同。[港交所交易说明](https://www.hkex.com.hk/Global/Exchange/FAQ/Securities-Market/Trading/Securities-Market-Operations?sc_lang=en)

假设某股票每手 100 股、价格 63.35 HKD：
- 一手股票金额为 6,335 HKD，另需考虑费用及价格变动。
- 预算 5,000 HKD 时，按整数股计算是 78 股，但普通整手可下数量应是 0。
- 预算 10,000 HKD 时，普通整手可下数量为 100 股，而不是 157 股。
- 卖出剩余 37 股是碎股处理问题，不能假装已按整手平仓，不能向上补成 100 股卖出。

这里的 100 股仅用于算例。最低资金随具体每手股数、价格、费用及券商限制变化，不是所有港股统一最低 100 股或统一最低 1,000 HKD。

方德文档只写 HK order_qty 为正整数；TradeType 中普通订单为 0，没有明确的碎股通道枚举。max_buy 返回 enable_buy_qty 和 enable_amt，max_sell 返回 enable_qty。这些是交易能力/可用量，不是每手股数，不能从返回的最大数量反推手数，也不能把额度当作成交保证。[交易枚举](https://dev-openapi-doc.techfx88.com/zh/trade/api.html)、[最大可买](https://dev-openapi-doc.techfx88.com/zh/trade/fund/max_buy.html)、[最大可卖](https://dev-openapi-doc.techfx88.com/zh/trade/fund/max_sell.html)

## 待补齐的策略与预检链路

以下是本次发现的缺口及建议实现要求，**尚未落地**：

1. 维护证券级规则：交易所、柜台币种、每手股数、生效日期、价格档位、碎股/小数股资格、数据来源和更新时间。港交所的[证券清单](https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx)可作为每手股数来源候选，需做更新、公司行动和失败处理。
2. 手动金额预览与策略使用同一套规则：可下数量 = floor(可用预算 / 参考价 / 每手股数) × 每手股数；先预留费用。参考价只能用于估算，市价单不能保证严格不超过输入金额。
3. 资金不足一手：跳过本次开仓并记清原因，不能向上凑一手、不能自动追加资金、不能无限重试同一条无效指令。
4. 下单前查券商实际最大可买/可卖数量；方德按对应报价币种的总购买力处理，不要求原币现金必须覆盖。自动换汇由券商处理，不能把 HKD 数字直接当 USD。
5. 减仓/平仓单独处理：遵守策略归属持仓和可卖数量；碎股余额不能静默舍弃或标为完全平仓，需明确支持的碎股通道或提示券商处理。
6. 回测、预览、执行采用可追溯的相同规则。提交成功不等于成交；策略只按真实回报更新持仓。订单结果未知先对账，不能盲目重发。
7. 同时补充证券价格 tick 和逐标的碎股/最小金额预检；当前其他股票适配器有些检查依赖券商拒单，不能视为完整本地校验。

## 代码定位与验证边界

- 后端券商独立文件：backend_api_python/app/services/openapi_brokers/forthright.py、tiger.py、webull.py；Alpaca 位于 app/services/alpaca_trading/。此次未改这些交易路由。
- 策略默认步长：app/services/instrument_rules.py 的 default_rules_for_symbol；消费位置在 strategy_v2/runtime.py。
- 方德数量归一化：forthright.py 的 normalize_strategy_quantity；共享持仓归属校验调用它。
- PC：src/utils/forthright.js、src/utils/orderQuantity.mjs、QuickTradePanel.vue。
- 手机：src/utils/stockOrder.js、src/utils/orderQuantity.mjs。
- 本次进行代码核对、官方文档核对、边界值自动测试及浏览器排序验证；没有发送新订单，因此不把测试网关接受或模拟成交当作生产整手/碎股能力验证。
