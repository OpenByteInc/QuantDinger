# Exchange REST coordination

Reviewed against official documentation on 2026-11-28. The implementation supplements existing transports; it never resends an HTTP request automatically.

| Venue | Official behavior used by the guard |
| --- | --- |
| [OKX](https://www.okx.com/docs-v5/en/#overview-rate-limits) | `50011` defers the affected endpoint; `50061` shares cooldown across new/amend order endpoints for the credential, excluding cancellation. Account configuration reads use a conservative two requests/second ceiling. |
| [Bybit](https://bybit-exchange.github.io/docs/v5/rate-limit) | `10006` limits an account endpoint. HTTP 429 defers venue REST traffic. HTTP 403 containing `access too frequent` establishes at least ten minutes of IP cooldown. Other 403 responses are not classified as bans. Read remaining/reset headers. |
| [Bitget](https://www.bitget.com/docs/classic/rest-api) | Recognize HTTP/body 429. Ordinary business errors and ambiguous order timeout code `40010` are not treated as quota exhaustion. |
| [Gate](https://www.gate.com/docs/developers/apiv4/en/) | Recognize HTTP 429, `TOO_MANY_REQUESTS`, and `CONCURRENCY_LIMIT_EXCEEDED`. Read remaining/reset headers. |
| [HTX spot](https://huobiapi.github.io/docs/spot/v1/en/) / [derivatives](https://huobiapi.github.io/docs/usdt_swap/v1/en/) | Recognize documented frequency errors and `1032`; read X-HB and derivatives remaining/reset headers. Legacy derivatives separate read/trade and contract-family cooldowns; V5 uses endpoint cooldowns. Known POST queries count as reads. |

`Retry-After` and future reset timestamps take precedence. With no usable deadline, endpoint fallback cooldowns are OKX 2s, Bybit/Bitget 1s, Gate 10s, HTX 3s, plus 100ms. Repeated responses can extend but never shorten an existing cooldown. Normalized paths prevent changing an order ID from bypassing the same endpoint cooldown. Account coordination identifiers use HMAC-SHA256 with the high-entropy API key as the MAC key and a fixed, domain-separated message. This is not password storage: raw credentials never enter Redis keys, and identifiers stay consistent across workers and restarts without a process-local random salt.

Upgrade all coordinating workers together: older plain-hash account identifiers do not share endpoint cooldowns with the new identifiers. IP cooldowns and shared read budgets keep their existing keys. No durable trading records or credentials are rewritten.

Each venue has two shared read budgets: public market data and authenticated reads, defaulting to five requests/second **per lane across processes**, not per strategy. Configure `OKX_REST_READS_PER_SECOND`, `BYBIT_REST_READS_PER_SECOND`, `BITGET_REST_READS_PER_SECOND`, `GATE_REST_READS_PER_SECOND`, and `HTX_REST_READS_PER_SECOND`. Brief congestion waits up to two seconds; longer congestion returns a rate-limit error before sending HTTP. Writes do not consume the read budget. Endpoint cooldowns include the method and credential, so an unrelated query/account/cancel is not blocked; actual IP cooldowns include writes. Live and demo are separated.

All processes sharing an outbound IP should use the same `EXCHANGE_REST_REDIS_URL` and `EXCHANGE_REST_NAMESPACE`. An empty URL defaults to jobs Redis when configured; standalone deployments without Redis only coordinate locally. A configured coordinator outage defers requests. These conservative request-count budgets do not fully model every UID, endpoint weight, VIP tier, multiple API keys belonging to one UID, WS quotas, or traffic outside QuantDinger.

Coverage includes native public candles/quotes, BaseRestClient account/trading REST, public order books and contract metadata, derivative-report enrichment, Gate native candles and Bitget Reality candles. Non-crypto broker hosts are unaffected. Rate-limited K-line requests are not immediately retried or marked as invalid symbols. When a fill is known but its fee query is limited, return the known fill with missing fee evidence for subsequent reconciliation. Successful orders remain successful even if publishing quota feedback fails; mixed batch responses retain their per-order results.

Deploy the backend and restart API/market-data/trading workers to apply the code and environment settings. No database migration is needed. Already active exchange bans must expire; this does not bypass restrictions or guarantee uninterrupted execution during a ban.

## 中文说明

已按上表官方文档接入 OKX、Bybit、Bitget、Gate、HTX。各交易所独立计算；公共行情与账户读取分别共享预算，默认每组每秒 5 次，支持上述环境变量调整。它是保守的请求次数控制，不是交易所全部权重、UID、VIP 或 WebSocket 限额的完整模型。

限流后遵循 `Retry-After`、剩余额度和恢复时间；没有有效恢复时间时使用表述的短暂冷却。普通接口限流按账户、方法和接口隔离，避免影响其他账户或无关撤单接口。明确的 IP 限制会暂停该范围内的写请求；Bybit 的普通 403 不等于 IP 封禁。

行情请求受限后不连续重试、不误报无效币种。已经确认的成交仍然保留；手续费暂时取不到时交给后续对账补齐，不当作已确认零手续费。成功下单不会因为记录限流反馈失败而变成失败，也没有新增下单自动重试。

多进程、多部署若共用出口 IP，必须使用相同的 Redis 协调地址和命名空间；没有 Redis 配置时只能进程内协调。已配置的协调服务不可用时暂缓请求。部署后重启相关后端、行情和交易进程，无需数据库迁移；已有封禁仍需等待交易所解除。

账户协调标识使用 API Key 作为密钥的 HMAC-SHA256，不将原始凭据写入 Redis 键，且跨进程、重启后保持一致。升级时统一更新所有参与协调的进程：旧版普通哈希与新版账户标识不共享接口冷却；IP 冷却和共享读取预算的键保持不变，不改写交易记录或凭据。
