# 期货contract获取指南

分三步执行：

1. 推断期货品种(underlying symbol)，例如沪深300期货为'IF'，铜期货为'CU'等

2. 使用获取到的期货品种（underlying symbol）调用期货API获取期货合约
     - 可获取可交易合约列表
     - 可获取主力合约
