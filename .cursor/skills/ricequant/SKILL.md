---
name: ricequant
description: "Ricequant量化平台文档阅读和API查询工具。当Claude需要访问Ricequant量化工具时使用，特别是提到某类金融相关数据或者RQ等字眼时：包括RQAlphaPlus回测框架、RQData数据API、RQFactor因子计算、RQOptimizer优化器、RQPAttr归因分析、RQAMS资产管理系统、RQAMSC Python SDK等文档的查询和阅读。"
license: Proprietary. LICENSE.txt has complete terms
---

# Ricequant文档阅读和API查询

## 概述

Ricequant（米筐）是一个专业的量化交易平台，提供完整的量化研究、回测和交易工具套件。当用户需要查询Ricequant相关文档、API使用方法或示例代码时，可以使用此skill。该skill能够自动检索Ricequant的官方文档，提取相关信息，并以结构化的方式提供给用户，帮助他们更高效地使用Ricequant平台进行量化研究和交易。

## 启动条件与方式
当用户提到与Ricequant相关的查询时，自动触发此skill，
启用一个subagent来处理Ricequant文档的检索和解析工作，讲查到的内容进行结构化整理，并给主Agent提供清晰的查询结果。

## 文档结构

Ricequant文档主要分为以下几个核心组件：

### 1. RQAlphaPlus - 回测框架
- **参数配置**: 各种类型的详尽的参数配置
- **入口函数**: 用于运行回测的函数
- **约定函数**: 策略中可选实现的函数
- **交易接口**: 策略中用于创建订单的函数
- **数据查询接口**: 策略中用于查询行情数据、财务数据等的函数

### 2. RQData - 数据API
- **HTTP API**: 通过HTTP接口的数据获取
- **Python API**: Python客户端数据查询接口
- **数据范围**: A股、港股、期货、期权、指数、基金、可转债等

### 3. RQFactor - 因子计算
- **内置因子**: 行情、财务、技术类因子
- **内置算子**: 数学运算、时间序列、横截面算子
- **自定义算子**: 开发自定义因子的方法

### 4. RQOptimizer - 优化器
- **选股API**: 股票选择和权重优化
- **优化器API**: 投资组合优化功能

### 5. RQPAttr - 归因分析
- **归因API**: 业绩归因分析工具
- **归因模型**: Brinson行业归因和因子归因

### 6. RQSDK - 本地开发套件
- **操作手册**: 本地开发环境配置
- **组件集成**: 各组件协同工作指南

### 7. RQAMS / RQAMSC - 资产管理系统与 Python SDK
- **RQAMS Web 文档**: 产品、工作空间、实时监控、持仓分析、风险归因等资产管理系统功能
- **RQAMSC 文档**: Python SDK 安装、初始化、产品、工作空间、交易流水、估值表、头寸、分析指标等接口

## 文档访问方法

### 1. 获取文档索引
```bash
curl -s "https://www.ricequant.com/doc/document-index.txt"
```
通用查询流程是：先用 `document-index.txt` 发现文档标题和 `/doc/sources/...` Markdown 源地址，再按命中的源地址读取正文。若目标组件已在站点导航中公开，但未出现在 `document-index.txt` 中，则按组件文档入口补充定位。

RQAMS / RQAMSC 属于这种补充入口：

```bash
curl -L -s "https://www.ricequant.com/doc/rqams/"
curl -L -s "https://www.ricequant.com/doc/rqamsc/"
curl -L -s "https://www.ricequant.com/doc/sources/rqamsc/index.md"
```

### 2. 使用curl参数找
```bash
# 获取并保存到文件
curl -s "https://www.ricequant.com/doc/sources/rqalpha-plus/api/config.md" -o config.md

# 显示HTTP状态码
curl -s -o /dev/null -w "%{http_code}" "https://www.ricequant.com/doc/document-index.txt"

# 跟随重定向
curl -L -s "https://www.ricequant.com/doc/document-index.txt"
```

## 常见查询场景

### 场景1：查询API使用方法
当用户询问如何调用Ricequant的特定API时：
1. 首先获取文档索引了解API所属组件
2. 根据组件找到对应的API文档
3. 提取相关API的使用示例和参数说明

### 场景2：查找配置参数
当用户需要配置回测参数时：
1. 访问RQAlphaPlus的参数配置文档
2. 查找具体的参数名称和取值范围
3. 提供配置示例

### 场景3：获取数据字段说明
当用户需要了解数据字段含义时：
1. 访问RQData对应市场的API文档
2. 查找数据字段的定义和说明
3. 提供数据获取示例代码

### 场景4：因子开发指导
当用户需要开发自定义因子时：
1. 访问RQFactor的自定义算子文档
2. 了解因子开发框架和接口
3. 提供开发示例

## 工作流程

### 步骤1：确定需求
- 明确用户需要查询的具体内容（API、配置、数据字段等）
- 确定所属的Ricequant组件
- 如果需求属于 RQAMS / RQAMSC，先尝试按通用索引流程定位；索引未命中时，使用 RQAMS / RQAMSC 组件入口或 RQAMSC Markdown 源文档
- 绝大部分的查询都是针对RQdata的数据API，其他组件的查询相对较少。无法判断的情况直接查询RQdata的文档索引，看看是否有相关内容。

### 步骤2：获取文档
- 使用curl获取相关文档内容
- 如果直接访问失败，尝试不同的URL格式或检查网络连接

### 步骤3：提取信息
- 从文档中提取相关部分
- 整理成清晰的结构化信息
- 提供代码示例和参数说明

### 步骤4：验证信息
- 检查信息的准确性和完整性
- 确保提供的示例代码可运行
- 注明文档版本和更新日期

## 注意事项

### 1. 文档版本
- Ricequant文档会定期更新，注意检查文档的更新日期
- 不同版本的API可能有差异，需要确认用户使用的版本

### 2. 网络访问
- 如果直接使用WebFetch工具失败，优先使用curl命令
- 确保网络可以访问ricequant.com域名

### 3.使用范围
- 绝大部分的API查询都是针对RQdata的数据API，其他组件的查询相对较少。
- RQDATA查询内容包括：API使用方法、参数配置、数据字段，示例。
- RQAMS / RQAMSC 查询内容包括：Web 功能说明、Python SDK 安装与初始化、产品和工作空间、交易流水、估值表、头寸、持仓与分析接口。

## 示例查询

### 示例1：查询回测配置参数
```bash
# 获取参数配置文档
curl -s "https://www.ricequant.com/doc/sources/rqalpha-plus/api/config.md" | grep -A5 -B5 "benchmark"
```

### 示例2：查询股票数据API
```bash
# 获取A股数据API文档
curl -s "https://www.ricequant.com/doc/sources/rqdata/python/stock-mod.md" | grep -A10 -B5 "get_price"
```

### 示例3：查询因子计算示例
```bash
# 获取因子计算文档
curl -s "https://www.ricequant.com/doc/sources/rqfactor/api/factor-calculation.md" | grep -A15 -B5 "execute_factor"
```

## 故障排除

### 1. 文档访问失败
- 检查网络连接：`ping www.ricequant.com`
- 尝试使用代理或VPN
- 检查URL是否正确

### 2. 内容解析错误
- 确认文档格式为Markdown
- 检查字符编码（通常为UTF-8）
- 使用`iconv`转换编码（如果需要）

