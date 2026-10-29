# 第一次运行 QuantDinger

本指南把安装、配置和第一次验证串成一条最短路径。建议先完成研究、回测和信号模式，再连接真实资金。

## 1. 启动并登录

先从[安装与部署总览](INSTALLATION_CN.md)选择 Windows、Linux/macOS、云服务器、SaaS 或移动端路径。首次打开 Web 页面后，用部署时设置的管理员账号登录，并按[部署后首次设置](POST_INSTALL_SETUP_CN.md)立即完成管理员安全和基础配置。

如果页面、数据库或容器无法启动，先查看[安装排障](../deployment/INSTALL_TROUBLESHOOTING_CN.md)。面向公网部署前完成[生产加固](../deployment/PRODUCTION_HARDENING_CN.md)。

## 2. 完成基础设置

在管理设置中逐项确认：

- 站点地址、时区和默认语言正确。
- 至少配置一个可用的 AI 模型服务；密钥只保存在服务端。
- 数据源能返回目标市场和周期的历史数据。
- 邮件、短信或 Telegram 通知按需要配置并发送测试消息。
- 多用户部署已经划分管理员和普通用户权限。

相关文档：[管理员与设置排障](../deployment/ADMIN_AND_SETTINGS_TROUBLESHOOTING_CN.md)、[多用户配置](../deployment/MULTI_USER_SETUP_CN.md)。

## 3. 完成第一次 AI 研究

进入 AI Research，选择市场和标的，确认行情时间与数据源，再运行一次分析。先判断数据是否新鲜、引用是否完整，再使用模型结论。详情见 [AI Research 使用指南](../product/AI_RESEARCH_CN.md)。

## 4. 完成第一次策略回测

从 Strategy API V2 模板开始：

1. 在策略编辑器中选择模板，明确市场、标的和频率。
2. 校验源码与 manifest，修复所有编译错误。
3. 在 Backtest Center 选择时间范围、初始资金和费用假设。
4. 检查基准、最大回撤、成交记录和执行假设，而不只看收益率。
5. 保存源码和本次结果，便于后续比较。

操作流程见[策略工作流](../product/STRATEGY_WORKFLOW_CN.md)和[回测中心指南](../product/BACKTEST_CENTER_CN.md)。完整编程接口见 [Strategy API V2 开发指南](../trading/STRATEGY_DEV_GUIDE_CN.md)。

## 5. 连接账户并验证信号

添加交易账户后先执行连接测试，只授予所需权限。创建部署时选择 `signal`，确认通知频率、状态恢复和策略方向都符合预期。回测通过不代表连接、余额、最小下单量或网络已经满足实盘要求。

进入真实资金前，请逐项完成[实盘安全清单](../trading/LIVE_TRADING_SAFETY_CN.md)。

## 6. 可选：连接 Agent

需要在 Codex、Cursor 或 Claude Code 中使用 QuantDinger 时，创建最小 Scope 的 Agent Token，再按 [MCP 接入指南](../agent/MCP_SETUP_CN.md)连接。不要把管理员密码或人类 JWT 交给 Agent。

## 完成标准

- 可以稳定登录，设置页没有关键配置错误。
- AI Research 能读取目标标的的当前数据。
- 至少一个策略能够通过校验并完成回测。
- 回测结果中的数据范围、费用和限制已被人工检查。
- 通知测试成功；实盘账户仍保持未启动或信号模式。
