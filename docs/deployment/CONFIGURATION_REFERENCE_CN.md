# QuantDinger 配置参考

QuantDinger 同时提供设置中心和根目录 `.env`。普通业务配置优先在 **Settings** 中管理；启动端口、基础设施、密钥和无法热更新的参数在宿主机 `.env` 管理。`.env.example` 是完整字段的机器可读来源，本页说明如何安全地找到和修改它们。

## 配置来源与优先级

| 来源 | 适用内容 | 修改后动作 |
| --- | --- | --- |
| Settings | 产品功能、AI、数据、通知、品牌等 | 按页面提示保存；必要时重启服务 |
| 根目录 `.env` | 端口、数据库、Redis、Kafka、核心密钥、Worker | 重建受影响容器 |
| Compose 文件 | 服务拓扑、卷、网络和健康检查 | 重新执行 `docker compose up -d` |
| 外部基础设施 | DNS、TLS、数据库、对象存储、邮件和支付商户 | 按提供商流程验证 |

不要在多个安装目录维护互相冲突的 `.env`。生产加固配置可能以只读方式挂载环境文件，此时必须在宿主机修改。

## 设置中心信息架构

| 分组 | 页面 | 典型用途 |
| --- | --- | --- |
| Core setup | Product features | 计费、市场和公开注册开关 |
| Core setup | Security & Authentication | 登录、会话和认证策略 |
| Core setup | AI / LLM | 模型提供商、密钥、模型和 Base URL |
| Core setup | Live Trading | 执行、同步和安全开关 |
| Core setup | Market Modules | 市场模块启用状态 |
| AI Research | Professional report data | 研究数据能力和专业源 |
| AI Research | News & web search | 新闻、搜索和证据来源 |
| AI Research | AI Agent | Agent 任务边界和限额 |
| AI Research | Data status & sync | 数据状态、同步和新鲜度 |
| Operations | Email/SMS | 通知渠道 |
| Operations | Network & Proxy | 出网代理与网络策略 |
| Operations | Registration & Security | 注册和安全控制 |
| Operations | System runtime | Worker 与运行时参数 |
| Organization | Billing、Brand、Contact、Social、Legal | SaaS 与品牌运营配置 |

## 必须备份且不得随意重置

| 变量 | 风险 |
| --- | --- |
| `POSTGRES_PASSWORD` | 变更后可能无法连接已有数据库 |
| `SECRET_KEY` | 使现有登录会话和签名失效 |
| `CREDENTIAL_ENCRYPTION_KEY` | 变更后已有交易与外部凭据可能无法解密 |
| `DATABASE_URL` | 指向错误数据库可能造成迁移或数据隔离事故 |
| OAuth、SMTP、支付密钥 | 泄露会导致账号、邮件或资金风险 |

## 常用配置分组

### 地址与端口

`FRONTEND_HOST`、`FRONTEND_PORT`、`MOBILE_HOST`、`MOBILE_PORT`、`BACKEND_PORT` 和 `FRONTEND_URL`。生产环境内部端口绑定回环地址，公网只通过反向代理开放 80/443。

### 产品与身份

`QUANTDINGER_DEPLOYMENT_MODE`、`ENABLE_REGISTRATION`、`BILLING_ENABLED`、`MARKETPLACE_ENABLED`、管理员账号、OAuth 和 Turnstile。个人部署不要启用不需要的 SaaS 能力。

### AI

`LLM_PROVIDER` 以及对应提供商的 API Key、模型和 Base URL。只配置正在使用的提供商；超时、温度和降级参数先保持默认。

### 数据、交易与 Agent

数据源、代理、`ALLOW_LOCAL_DESKTOP_BROKERS`、仓位同步、执行流以及 `AGENT_*` 限额。真实交易和 Agent 下单默认保持关闭，经过权限与限额验收后再启用。

### 基础设施

数据库池、Redis 内存、Kafka、Celery、Worker 副本数、日志大小和可观测性端口。增加副本前先看数据库连接、Kafka 分区和主机容量。

## 安全修改流程

1. 记录当前版本和变更目的。
2. 备份数据库与 `.env`。
3. 一次只修改一个配置组。
4. 执行 `docker compose config` 检查渲染结果。
5. 用 `docker compose up -d --force-recreate <service>` 重建受影响服务。
6. 检查健康、日志和关键业务流程。
7. 失败时恢复 `.env` 和固定镜像版本，不盲目删除卷。

完整默认值和注释始终以根目录 [`.env.example`](../../.env.example) 为准。

