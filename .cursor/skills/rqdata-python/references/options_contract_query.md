
# 期权contract获取指南

分三步执行：

1. 推断期权类型
    - 期货期权
    - ETF期权
    - 个股期权
    - 其他类型期权

2. 获取期权标的（underlying）
    - 期货期权：underlyiny就是期货品种，例如铜期权的underlying是`CU`
    - ETF期权：使用命令行获取underlying，例如50ETF使用命令行`python ~/.claude/skills/rqdata-python/scripts/code_index_manager.py -q "50ETF" -m cn -t ETF`获取underlying
    - 个股期权：和ETF期权的underlying获取方法一致
    - 其他类型期权：自行推断如何获取

3. 使用获取到的期权标的(underlying)调用期权API获取期权合约
