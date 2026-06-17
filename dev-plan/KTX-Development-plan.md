# KTX交易所接入开发需求

## 说明
* KTX是已经新兴Crypto交易所， 基本模仿Binance的。
* 本系统已经实现接入了Binance/OKX/Bitget/Bybit等各大交易所的Spot/Futures交易，现在要求参考已有的inance/OKX/Bitget/Bybit实现架构，完成KTX的接入。以下会补充KTX交易的API说明。

## KTX API接口说明参考
* 官方API接口说明：https://ktx-private.github.io/api-zh/#b122f813d5
* 官方的AI SKILLS实现(Javascript)：
 1) https://github.com/KTX-private/ktx.ai.skills/blob/main/README.md
 2) https://github.com/KTX-private/ktx.ai.skills/blob/main/scripts/README.md
 3) https://github.com/KTX-private/ktx.ai.skills/blob/main/references/api_documentation.md
 4) https://github.com/KTX-private/ktx.ai.skills/blob/main/references/signature_spec.md
 5) https://github.com/KTX-private/ktx.ai.skills/blob/main/references/trading_guide.md

## 开发说明
* 实现KTX的Spot和futures(U本位合约)交易。
* 完全参考现有交易接入框架和规范，用Python实现，做到QuantDinger项目内风格统一。
* KTX交易的api域名用https://api.ktx.com。
