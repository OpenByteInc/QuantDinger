# QuantDinger SaaS 部署指南

SaaS 不是打开一个环境变量就完成的部署方式。它是在生产部署之上增加多用户注册、域名与邮件、支付、权限、审计、备份和持续监控。本指南给出上线顺序和验收边界。

## 1. 适用范围

适用于由一个运营方托管、多个用户通过同一站点使用的 QuantDinger。个人或团队内网部署请使用[安装总览](../getting-started/INSTALLATION_CN.md)，不要无故启用公开注册和支付。

## 2. 先完成生产底座

1. 按[云服务器部署](CLOUD_DEPLOYMENT_CN.md)使用固定版本镜像。
2. 配置域名、Nginx、HTTPS 和仅开放 `80/443` 的防火墙。
3. 完成[生产加固](PRODUCTION_HARDENING_CN.md)与[可观测性](OBSERVABILITY_CN.md)。
4. 生产规模优先使用托管 PostgreSQL、Redis 和 Kafka；多机扩展遵循[分布式运行时部署](DISTRIBUTED_RUNTIME_SCALING_CN.md)。

## 3. 核心环境配置

在 `.env` 中至少审查：

```ini
QUANTDINGER_DEPLOYMENT_MODE=saas
FRONTEND_URL=https://app.example.com,https://m.example.com
ENABLE_REGISTRATION=true
ALLOW_LOCAL_DESKTOP_BROKERS=false
BILLING_ENABLED=true
```

同时必须使用稳定且唯一的 `SECRET_KEY`、`CREDENTIAL_ENCRYPTION_KEY` 和数据库密码。已经投入使用后不得随意重新生成这些值，否则会导致会话失效、凭据无法解密或数据库失联。

## 4. 注册、邮件和身份登录

- 明确是否允许任何人注册，还是仅邀请注册。
- 配置 SMTP 并验证注册、找回密码、告警和账单邮件。
- 需要 Google、GitHub 等登录时，按[OAuth 配置](OAUTH_CONFIG_CN.md)设置回调地址。
- 管理员与普通用户必须分离；操作流程见[多用户配置](MULTI_USER_SETUP_CN.md)。
- 使用服务条款和隐私政策的真实公开 URL，不要保留示例地址。

## 5. 支付与账单

启用账单前，配置 Stripe 商户、产品/价格、Webhook、成功/取消跳转和生产密钥。Webhook 必须使用 HTTPS，并验证签名。不要在确认税务、退款、争议和所在地区合规责任前接受真实付款。

至少完成这些测试：

- 新用户订阅、续费、取消和支付失败。
- Webhook 重放不会重复入账。
- 套餐变更和权限生效一致。
- 后台退款与系统账单状态一致。

## 6. 数据与交易隔离

- 每个请求都必须在服务端验证用户和租户归属，不能依赖前端隐藏按钮。
- API、Agent Token 和交易凭据使用最小 Scope。
- 券商或交易所凭据只在服务端加密保存，日志中不得出现明文。
- 管理员支持操作要有审计记录，不使用用户密码代登录。
- 默认先开放研究、回测和模拟盘；真实交易按地区、用户资格和风险流程单独开放。

## 7. 上线前验收

| 范围 | 必须通过 |
| --- | --- |
| 身份 | 注册、验证、登录、退出、找回密码、封禁 |
| 权限 | 用户之间不能读取对方账户、策略、回测、订单或报告 |
| 支付 | 订阅全生命周期和 Webhook 幂等 |
| 运行 | 健康检查、Worker、迁移、队列积压和定时任务 |
| 安全 | TLS、密钥轮换方案、漏洞扫描、速率限制和审计 |
| 数据 | 自动备份、异地副本、恢复演练和保留策略 |
| 运维 | 告警接收人、值班流程、回滚步骤和状态通知 |

## 8. 发布顺序

建议按“内部管理员 → 邀请用户 → 小规模付费 → 公开注册”逐级开放。每一级都要观察登录失败率、API 延迟、队列积压、数据库连接、支付失败和交易异常，再决定是否扩大流量。

上线后设置见[部署后首次设置](../getting-started/POST_INSTALL_SETUP_CN.md)，常见安装问题见[安装 FAQ](INSTALLATION_FAQ_CN.md)。

