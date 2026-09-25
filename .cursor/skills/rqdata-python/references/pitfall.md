# RQData API常见错误使用陷阱（以及正确使用方式）

## 1. 未验证合约代码是否符合 Ricequant 规范

```python
df = rqdatac.get_price('600000', start_date='20230101', end_date='20230110')
```

## 2. 交易日历未使用RQData API

错误代码：

```python
trading_dates = rqdatac.get_trading_calendar('SSE', start_date='2025-12-01', end_date='2025-12-31')
```

正确方式：查阅合约查询相关API文档，发现应该使用`get_trading_dates`API