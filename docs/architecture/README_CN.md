# 系统架构总览

QuantDinger v5 将 HTTP 请求、交易控制与执行、K 线收盘分发与求值、领域调度、
有限后台任务和数据库迁移拆成明确的进程角色。PostgreSQL 负责持久状态与所有权协调，
Kafka 承载版本化运行时事件，两套 Redis 分别服务于缓存和持久任务，不能混用。

## 运行职责

| 进程 | 负责 | 不应负责 |
| --- | --- | --- |
| `backend` | 鉴权、校验、查询、提交持久命令 | 交易循环和长期调度 |
| `trading-worker` | 控制/实时策略、券商会话、带 fencing 的下单、对账、持久化网格 Actor | 对外 HTTP 请求和分布式 K 线求值 |
| `strategy-dispatcher-worker` | 消费 K 线收盘事件并发布稳定策略分片批次 | 执行策略代码和向交易所下单 |
| `strategy-evaluator-worker` | 常驻 K 线策略运行时、收件箱认领、租约、检查点和订单意图 | 直接向交易所下单 |
| 策略沙箱子进程 | 执行不受信任的指标、清单发现、回测和实盘回调；保存可序列化策略状态 | 租约、凭证、数据库访问或直接向交易所下单 |
| `kafka-audit-worker` | 独立审计事件协议与投递结果 | 策略决策和下单 |
| `scheduler-worker` | 组合、部署、支付和通知等领域调度 | 通用 Celery 任务 |
| `celery-worker` | 有限、可序列化、可重试的后台任务 | 长期策略运行时 |
| `migration` | 按顺序应用数据库结构更新 | 并发提供 API 服务 |
| `kafka-init` | 创建版本化 Kafka 主题 | 处理运行时事件 |

## 状态与并发

- PostgreSQL 是命令、租约、心跳、审计、事件收件箱、Actor 邮箱、策略和交易状态的唯一记录来源。
- Kafka 是有序的跨进程事件骨干，但消费者 offset 不能替代 PostgreSQL 业务状态或订单幂等。
- `redis` 是可淘汰缓存，不能承担持久任务队列。
- `redis-jobs` 是 Celery broker 与结果存储，启用 AOF 和 `noeviction`，需要监控内存。
- 幂等键、数据库认领、可续租租约和 fencing token 用于阻止重复执行与过期 Worker 写入。
- 策略沙箱子进程只是从属执行边界，不是持久服务所有者。可信工作进程负责创建它、提供有界快照、校验输出，并继续承担恢复和下单授权。

## 按修改类型阅读

| 修改任务 | 详细文档 |
| --- | --- |
| 理解包与进程归属 | [后端架构（英文）](ARCHITECTURE.md) |
| 保持依赖方向 | [模块边界（英文）](MODULE_BOUNDARIES.md) |
| 修改并发或持久任务 | [并发模型（英文）](CONCURRENCY_MODEL.md) |
| 判断任务属于哪个进程 | [进程职责（英文）](PROCESS_ROLES_AND_TASKS.md) |
| 部署或扩容事件运行时 | [分布式运行时部署与扩容](../deployment/DISTRIBUTED_RUNTIME_SCALING_CN.md) |
| 新增路由、适配器、任务或服务 | [扩展指南（英文）](EXTENSION_GUIDE.md) |
| 修改 HTTP 契约 | [API 约定（英文）](API_CONVENTIONS.md) |

进行较大修改前，应先明确：哪个进程拥有该工作、持久状态存在哪里、如何重试与幂等，
以及哪项测试能够证明边界没有被破坏。
