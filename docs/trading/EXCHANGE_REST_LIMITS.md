# Exchange REST coordination

## Strategy recovery during throttling

Exchange cooldowns preserve the strategy's desired `running` state. Runtime
rate-limit failures do not count toward the repeated-failure stop threshold.
Startup throttling persists an epoch-scoped `startup_cooldown` record in the
existing runtime-state table (run ID zero); controllers and distributed evaluators
wait before starting again. A deferred start does not consume a command attempt,
and startup receives its normal readiness budget after the cooldown expires.
The REST guard remains authoritative if the exchange extends the cooldown.

Manual stops and newer runtime generations fence recovery. Authentication,
configuration and other terminal failures retain their existing stop policy.
Recovery rechecks account requirements and data readiness, retains run/order
idempotency, and does not cancel resting grid orders solely because of throttling.
No running strategy is guaranteed timely local risk execution while its required
exchange data or private REST operations are unavailable.

Deploy the same backend revision to trading and evaluator workers. Previously
stopped strategies are not automatically reactivated: an operator must explicitly
start those strategies again after deployment.

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

## Binance incident tracing

### USD-M WebSocket routing

Binance USD-M live mark-price feeds use `/market/stream`; desktop and mobile
K-line subscriptions use `/market/ws`, and depth subscriptions use `/public/ws`.
The live execution adapter uses `/private/ws/<listenKey>` as specified in the
[user data stream guide](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams).
This raw subscription preserves the existing event payloads and listen-key
creation/keepalive lifecycle. Demo/testnet private streams use the documented
`wss://demo-fstream.binance.com/ws/<listenKey>` host. Spot subscriptions and the
separate order-placement WebSocket API are unchanged.

See the [routing migration notice](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice)
and [testnet endpoints](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info).
A successful handshake alone does not prove market data is arriving. Verify fresh
`public_websocket` prices and candle updates after deployment; REST fallback and
execution reconciliation remain enabled for outages. Deploy backend workers and
both frontend builds together. No database migration is required. Correct routing
removes one cause of excess REST traffic; it does not clear an active IP ban.

### USD-M WebSocket 路由

币安永续实盘价格使用 `/market/stream`，桌面端和移动端 K 线使用 `/market/ws`，盘口使用
`/public/ws`。实盘成交订阅使用用户数据文档规定的 `/private/ws/<listenKey>`，保留原有事件格式、
listenKey 创建与续期逻辑；模拟和测试环境使用 `demo-fstream.binance.com/ws/<listenKey>`。
现货订阅和独立的 WebSocket 下单接口保持不变。

部署时统一更新后端进程及两个前端，无需数据库迁移。连接握手成功不代表行情已经到达，需要确认
价格来源为 `public_websocket` 且持续更新，并检查 K 线推送。REST 兜底和成交对账继续保留；
正确路由能消除一类额外 REST 请求，但不会提前解除已经生效的 IP 封禁。

Deploy the same backend revision to every API, trading, evaluator, scheduler and
Celery process that can call Binance. Verify that they share
`BINANCE_REST_REDIS_URL` (or the configured jobs Redis fallback) and
`BINANCE_REST_NAMESPACE`. Restarting only the trading process leaves other callers
on their previous code. Fee queries stop their local retries on a rate-limit
exception; missing fees remain pending reconciliation. K-line rate limits do not
start immediate pagination retries or fallback requests. Existing bans must expire.

For a short diagnostic window, set `BINANCE_REST_TRACE_ENABLED=1` with INFO logging
and recreate the relevant containers to load the environment. It defaults to `0`
to avoid high-volume request logs; turn it off after capture. Logs distinguish:

- `event=start`: one transport invocation after guard admission; count these by
  timestamp for per-second request attempts. A transport failure does not prove
  that the request reached Binance or that an order was rejected.
- `event=response`: HTTP status, elapsed milliseconds, and the numeric
  `used_weight_1m` header if present. Weight is not a request count.
- `event=transport_error`: invocation raised before returning a response.
- `event=blocked`: local guard denied the request; no HTTP call was made.

`trace_id` correlates start and outcome; `pid`, process role and container identify
the caller. Do not sum start and response as two requests. Capture all callers;
the sample cannot account for another application sharing the outbound IP.
The trace excludes query strings, API keys, signatures, response bodies and
exception text. Tracing never retries HTTP or alters a successful order result.

## 币安故障追踪

需要统一更新会访问币安的 API、交易、策略评估、调度和 Celery 进程，并确认它们使用同一个
`BINANCE_REST_REDIS_URL`（或默认任务 Redis）及 `BINANCE_REST_NAMESPACE`。
只重启交易进程不能更新其他调用方。手续费查询遇到限流立即结束本轮重试，费用留待对账；
K 线限流不立即重试分页或备用请求。已有封禁仍需等待到期。

需要统计时，临时设置 `BINANCE_REST_TRACE_ENABLED=1`，保持 INFO 日志并重建相关容器使环境变量生效。
默认关闭，采集结束后恢复为 `0`。按秒统计 `event=start`，它表示通过检查后开始一次 HTTP 调用；
超时不保证请求未到达币安，也不证明订单被拒绝。`event=response` 记录状态码、耗时和币安返回的分钟权重，
`event=transport_error` 表示未拿到响应，`event=blocked` 表示本地拦截、没有发出 HTTP 调用。
不要把开始和响应各算一次请求，也不要把权重当请求次数。用 `trace_id` 配对，结合进程角色、PID 和容器定位来源。
统计需要涵盖所有调用进程，不包含同一出口 IP 上其他应用的流量。日志不记录 URL 查询参数、密钥、签名或响应正文。

账户协调标识使用 API Key 作为密钥的 HMAC-SHA256，不将原始凭据写入 Redis 键，且跨进程、重启后保持一致。升级时统一更新所有参与协调的进程：旧版普通哈希与新版账户标识不共享接口冷却；IP 冷却和共享读取预算的键保持不变，不改写交易记录或凭据。
