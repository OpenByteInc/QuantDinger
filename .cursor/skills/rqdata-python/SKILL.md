---
name: rqdata-python
description: "RQData数据API使用指南。当需要查询RQData数据接口、获取金融数据时使用。支持A股、港股、期货、期权、指数、基金、可转债等市场数据查询，包含HTTP API和Python API文档。"
---

# rqdata-python

每次skill使用前，从仓库根目录执行`python .cursor/skills/rqdata-python/scripts/init_skill.py`。如果返回RQData license不可用，则提示用户`需正确安装rqsdk，配置许可证，或者问题应联系RQData技术支持获得帮助`，终止skill的使用

## 使用方法

### 查找API接口

1. 确定所需API文档：在`cache/api_doc_index.md`中grep需要的API文档，示例：Grep `宏观|GDP` on `cache/api_doc_index.md`的结果显示满足GDP宏观数据查询需求的API文档是`macro-economy.md`

2. 确定所需API接口：在`cache/api_index/{API文档名}_index.md`中grep所需API接口。API索引文件中每行表一个API接口的API Name、Description、Line Range，确定匹配API接口的行号List Range，阅读API接口开始的50行来获取API接口定义，如果50行不够多阅读更多行。示例：Grep `宏观|GDP` on `cache/api_index/macro-economy_index.md`的结果显示满足GDP宏观数据查询需求的API接口是`econ.get_factors`，行号范围是87-131，阅读`cache/api_index/macro-economy.md`的第87到87+50行获得API定义
    - 注意也许需要调用多个API接口来满足需求，所以可能需要定位多个API接口

3. 若以上步骤没有定位到API接口，才尝试在`cache`中搜索

### 应使用真实资产代码

- 如果API参数涉及到资产代码（例如股票代码，期货代码，期权代码等），**强制**获取真实的资产代码：
  - 推断资产类型，资产名称（或资产代码），市场名称
  - 如果是查询期权合约代码请参考`references/options_contract_query.md`
  - 如果是查询期货合约代码请参考`references/futures_contract_query.md`
  - 如果是查询其他类型资产代码参考`references/common_asset_code_query.md`

### 其他注意事项

- 如果API参数涉及到宏观因子名称，查询宏观因子名称参考`cache/api_docs/macro_factor_names.csv`
- 调用RQData API前必须调用`rqdatac.init()`来初始化
- **禁止**阅读`scripts`中的源代码
- 当遇到使用问题的时候，参考`references/pitfall.md`了解常见错误使用陷阱

## Skil执行示例

用户prompt：`请为我展示近几年的中国的存款准备金率`

Agent执行步骤：

1. 强制执行skill初始化脚本
2. `存款准备金率`是宏观数据，根据api_doc_index.md，应在`macro-economy.md`中查找API
3. 使用`macro-economy_index.md`快速定位满足需求的API接口为`econ.get_reserve_ratio`，行范围87-131
4. 使用read工具读取`macro-economy.md`中读取接口头50行（第87到87+50行）
5. 从read工具返回中获取API定义和参数信息
6. 让我开始编写代码
