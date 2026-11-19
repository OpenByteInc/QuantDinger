# 安全、版本与漏洞报告

QuantDinger 会处理模型密钥、交易凭证和账户数据。生产部署应把应用、数据库、缓存和反向代理视为同一安全边界，并限制管理入口的网络访问。

## 运营者责任

- 从受信任的版本或镜像部署，并在升级前阅读发布说明。
- 使用长随机密码；管理员与普通用户分离。
- 通过环境变量或密钥系统提供凭证，不写入源码、日志、截图或工单。
- 交易 API 只授予必要权限并关闭提币；优先使用独立子账户。
- 为数据库、对象存储和备份设置访问控制、加密和保留期限。
- 在反向代理启用 HTTPS，限制管理端点，及时更新依赖和基础镜像。
- 定期轮换模型密钥、OAuth 密钥、Agent Token 和交易凭证。

部署基线见[生产加固](../deployment/PRODUCTION_HARDENING_CN.md)，运行监控见[可观测性](../deployment/OBSERVABILITY_CN.md)。

## Agent 与自动化

Agent Token 应使用最小 Scope、有效期、速率限制和名义金额上限。人类 JWT 与 Agent Token 不可混用。所有产生副作用的 Agent 请求应使用唯一 `Idempotency-Key`。默认保持实盘总开关关闭，除非运营者明确启用并完成[实盘安全清单](../trading/LIVE_TRADING_SAFETY_CN.md)。

## 不受信任的指标与策略

- 接受用户代码的部署应使用官方 Linux 运行方式。沙箱要求支持 seccomp 和 Landlock 的 `x86_64` 或 `aarch64` 内核；控制不可用时会失败关闭。
- 不要向指标或策略子进程添加数据库 URL、应用密钥、券商凭证、云密钥、宿主机 Socket 或包含密钥的挂载。
- 指标执行和策略清单发现使用一次性进程；每次回测使用一个一次性运行进程；每个运行中的实盘部署使用一个持久回调进程。这些进程都不能直接向交易所下单。
- `STRATEGY_RUNTIME_SANDBOX_TIMEOUT_SECONDS`、`STRATEGY_RUNTIME_SANDBOX_MEMORY_MB`、`STRATEGY_BACKTEST_SANDBOX_TIMEOUT_SECONDS` 和 `STRATEGY_BACKTEST_SANDBOX_MEMORY_MB` 应保持与宿主资源匹配的有限值。实盘请求默认使用 120 秒；旧配置 `STRATEGY_SCRIPT_CALLBACK_TIMEOUT_SEC` 已停用，不再生效。提高限制只会增加资源暴露，不会授予策略更多能力。
- 沙箱升级后应重新构建或拉取后端镜像，并重启 API、交易、求值和回测工作进程；已有进程不会原地获得新的内核策略。

准确执行合同见[指标开发指南](../trading/INDICATOR_DEV_GUIDE_CN.md)、[Strategy API V2 指南](../trading/STRATEGY_DEV_GUIDE_CN.md)和[进程职责模型](../architecture/PROCESS_ROLES_AND_TASKS.md)。

## 发现泄露或异常时

1. 停止受影响的策略和新交易。
2. 吊销相关 Token、API Key 和会话。
3. 在交易所或券商核对订单、持仓和权限。
4. 保存必要日志，避免继续传播密钥和个人数据。
5. 修复和轮换完成后再恢复服务。

## 报告漏洞

不要在公开 Issue 中披露可利用细节、密钥或账户数据。请按照仓库根目录的[安全策略](../../SECURITY.md)使用私密渠道报告，并提供受影响版本、复现条件、影响和可行的缓解方式。
