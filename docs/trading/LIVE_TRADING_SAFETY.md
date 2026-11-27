# Live-trading safety guide

> Forthright futures currently support manual trading only. Automated futures strategy execution is disabled; contract specification notes below are retained for future integration.

This guide defines the minimum checks before moving from backtest or signal mode to real capital. Venue, broker, region, and account type affect available products; the active catalog and account response take precedence over static examples.

## Support boundary

| Market | Current live venues | Key limit |
| --- | --- | --- |
| Crypto | Binance, Bitget, Bybit, OKX, Gate, HTX | spot and swap depend on venue and account capability |
| Exchange direct equities | Gate stock channel | retain exact currency and catalog identity; long-only |
| Exchange tokenized equities | OKX, Bybit, Bitget Reality | catalog and regional availability required; long-only |
| Exchange equity perpetuals | Binance, OKX, Bitget, Bybit, Gate | derivative exposure; exact native contract and swap rules required |
| Binance bStocks | Binance | exact catalog pair, spot semantics, and no leverage |
| USStock | Alpaca, IBKR, Tiger, Webull, Forthright | current broker strategy policy is long-only |
| HKStock | Tiger, Forthright | current broker strategy policy is long-only |
| Futures | none | automated strategy execution is disabled; Forthright manual trading only |
| Other parsed markets | none | data or backtest support does not imply live support |

HTX equity products are rejected until authoritative metadata and an execution contract are available. Mixed-market live deployment is unsupported. See [Section 18 of the Strategy API V2 guide](STRATEGY_DEV_GUIDE.md#18-deployment-and-live-boundaries) for canonical identifiers and the full boundary.

### Bitget account modes

Bitget credentials default to automatic account-mode detection. Unified accounts use UTA V3, while Classic or `hybrid` accounts use the corresponding Classic V2 spot and futures APIs. Accounts reported as `switching` or `upgrading` are temporarily blocked from trading so an order cannot be sent to the wrong account during migration. An operator may pin a credential to `uta` or `classic` with `bitget_account_mode` when automatic detection is unsuitable.

## Preflight checklist

1. **Account:** use a dedicated subaccount or low-balance account; grant required trading permissions only and disable withdrawals.
2. **Instrument:** verify venue, market type, settlement currency, native symbol, and API family.
3. **Strategy:** source, manifest, direction, and leverage declarations pass validation.
4. **Sandbox:** use the supported Linux deployment and confirm the strategy starts without a kernel-isolation or sandbox-protocol failure; never bypass that failure with in-process execution.
5. **Backtest:** a person has reviewed data, costs, slippage, funding, execution ledger, and maximum drawdown.
6. **Signal:** run `signal` first and check notification frequency, time zone, and state restoration after restart.
7. **Positions:** identify manual and other-strategy holdings; resolve every unknown delta.
8. **Limits:** set notional, per-symbol, gross exposure, leverage, and loss boundaries.
9. **Incident path:** confirm an operator can stop the deployment, cancel orders, and use emergency stop.

## Resting-grid startup

Before opening an initial market position, the runner checks every funded grid cell against the venue's quantity step, minimum quantity and minimum notional. It uses the actual per-cell budget, swap leverage, contract multiplier and tick-normalized entry/exit prices. Zero-weight cells are skipped; a grid with no funded cells is rejected. Unavailable instrument rules or an untradable cell block startup, with the cell budget, requested quantity and available minimums recorded in the strategy log. The runner does not round orders up, increase capital or leverage, or reduce the grid count automatically.

A grid with no confirmed working orders must not report successful startup, unless it is deliberately paused outside its price bounds. Recovery can retain verified exit orders without opening new exposure. These checks do not reserve margin or guarantee venue acceptance. If later order placement fails after a new initial position has filled, the existing rollback path remains responsible for closing only that startup's new exposure; a failed rollback is reported separately.

## Grid order verification

The exchange-order panel reads persisted verification evidence. A refresh queues a deduplicated `reconcile` command for the trading worker; the HTTP process does not query the venue or start a grid runner. Verification uses the open-order snapshot when available, then at most 10 individual order lookups per pass. A failed or unsupported snapshot (including HTX) does not prevent an exact order lookup through the existing venue/account-mode adapter. Snapshot pagination checks the worker request budget on each page; individual lookups stop starting after eight seconds, and the REST request timeout is capped at three seconds, including routed Bitget clients. Adapter-internal metadata requests can take additional time. Large sets rotate through older evidence first. These are per-worker request budgets, not a global exchange quota.

Active results are refreshed after 30 seconds without hiding the last timestamped result while queued; orders absent from both current and historical venue responses are retried no sooner than five minutes. Confirmed terminal evidence does not expire, but account/order identity changes or newer local fill quantities invalidate it. Evidence replaces the previous audit metadata on each order rather than accumulating full snapshots in command history. A confirmed terminal status counts as verified, but pending local fill accounting is shown separately and clears when the normal accounting path catches up. Absence, an API error, and expired venue history never prove cancellation.

Confirmed zero-fill cancellations use a guarded local update; concurrent local fills prevent that update. Verification never posts trades, changes positions/cells, starts a runner or submits/cancels exchange orders. Running grids retain their existing poller/actor accounting owner. A partial/full fill not yet posted locally remains visibly pending; for a stopped grid it requires operator review, not automatic repair from this panel. No orders or holdings are deleted by verification. Contract tests cover ordinary spot and USDT swaps for Binance, OKX, Bitget (Classic/UTA), Bybit, Gate and HTX; this is not a live-account certification or support for every product family.

Deploy the updated backend and trading workers together, followed by the desktop frontend for queued/pending/terminal labels. No database migration is required. This path repairs recoverable evidence; it cannot reconstruct records already removed by the exchange's history-retention policy.

## Startup and worker recovery

`STRATEGY_RUNTIME_STARTUP_TIMEOUT_SECONDS` bounds startup readiness polling per
command (default 900 seconds, minimum 60). It includes historical-data
loading and is separate from the sandbox callback timeout. Pending readiness
polls retain the clock even when another controller claims them. Only takeover
of an expired processing claim by a different worker resets the clock. Restart
trading workers after changing this environment setting.

Polling runtimes expose readiness before their first risk or signal cycle.
Deferring a startup command stops its command heartbeat before returning it to
pending. A terminal startup failure fences and cancels the local runtime without
submitting closes or cancelling broker orders; an owned runtime lease is released
after local exit, or allowed to expire if local shutdown times out. Existing
orders and positions still require reconciliation. Deploy the worker fix before
starting a previously failed strategy again; do not repair this state by deleting
lease rows. No database migration is required.

## While running

- Monitor runtime state, order status, fills, positions, available balance, and notifications.
- An unknown reconciliation delta pauses same-side entries or additions; do not bypass that protection.
- Minimum quantity, step size, minimum notional, and available margin can change the submitted quantity.
- WebSocket improves latency; REST reconciliation remains the recovery source after disconnects.
- A strategy manages only its allocated position and must not absorb manual or other-strategy inventory.

Position-ownership checks retry transient transport failures only for read-only account snapshots. The default is one retry after a 0.5-second exponential backoff with 20% jitter, configured with `LIVE_TRADING_POSITION_SNAPSHOT_RETRIES`, `LIVE_TRADING_POSITION_SNAPSHOT_RETRY_BACKOFF_SEC`, and `LIVE_TRADING_POSITION_SNAPSHOT_RETRY_JITTER_RATIO`. If every snapshot attempt fails, entries remain fail-closed and the runtime records a temporary connectivity warning instead of assuming that the account is flat. Resting-grid entry orders are cancel-requested and reconciled, but confirmed exit, stop-loss, take-profit, and reduce-only protection remain in place. Exit orders are cancelled or rebuilt only after a fresh snapshot proves that account inventory is below the strategy or protected allocation; an unavailable snapshot or credential is not evidence that the position is flat.

Order submission uses a separate, stricter policy. A timeout, disconnect, or sent-without-ack response has an ambiguous outcome and is never submitted again directly; the durable client order id is reconciled first. Only an explicit venue response proving non-acceptance due to a rate limit or temporary market unavailability is retried, once by default, using the same stable client order id and a 0.5-second exponential backoff with 20% jitter. Configure the bounded policy with `LIVE_TRADING_ORDER_SAFE_RETRIES`, `LIVE_TRADING_ORDER_RETRY_BACKOFF_SEC`, and `LIVE_TRADING_ORDER_RETRY_JITTER_RATIO`. Authentication, permission, funding, sizing, instrument, and account-configuration rejections are not retried. Confirmed reduce-only close residuals continue to use their separate position-reconciliation policy.

Resting-grid submits persist a pending intent before contacting the venue. An unknown outcome blocks further submits for that grid until reconciliation resolves it, including after a worker restart. A missing or unreadable order ledger is not an empty ledger. Partial entry fills add only the uncovered exit quantity, preserving working exits; legitimate child exits are not treated as duplicates. Rebuilding oversized exits requires a fresh position snapshot and confirmed cancellation with racing fills posted first. Invalid position payloads are not evidence of a flat account.

Generic server errors do not prove non-acceptance: for example, [Bybit documents `10016` as a server error](https://bybit-exchange.github.io/docs/v5/error), and [Binance documents ambiguous execution status for some timeout responses](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info). Gate spot and perpetual orders can initially be reconciled using the original custom `text` identifier; persist the returned exchange order ID as soon as it is available. Custom-ID lookup has venue retention limits, so an unresolved intent must remain blocked for manual reconciliation instead of being declared failed merely because it is old. None of these connectivity safeguards overrides an explicit user stop or a genuine configured risk exit.

### Strategy equity and shared accounts

Strategy API V2 sizes orders from the deployment's current equity: configured initial capital plus lifetime realized price P&L, minus recorded trading fees, plus signed funding and broker activities, plus unrealized P&L on strategy-owned positions. Stopping and starting a strategy or restarting a worker does not reset that ledger. The runtime uses the same lifetime basis as the equity display; marks may differ briefly as prices update. Non-positive equity provides no opening budget. If the ledger query fails, the evaluation cycle fails instead of falling back to initial capital; existing runtime-failure handling applies. This does not cancel submitted orders or liquidate holdings.

For example, equity of 36.36 with 2x leverage gives a nominal capacity of 72.72 before the existing budget buffer, costs and venue constraints, not the original 500 times 2. Percentage targets use current equity. Explicit quantities must still pass the strategy budget guard; a valid reduce-only exit is not blocked by that opening budget.

Strategy net P&L uses the strategy's own recorded fills and allocated opening/closing fees. Venue-reported order P&L remains a separate value with the venue's fee basis. When manual trades share the same venue position, the venue's average entry cost can differ from the strategy's cost. Do not replace strategy P&L, equity or position cost with that order report, and do not import manual inventory to make the figures match. Use a dedicated account/subaccount for physical separation; advanced ownership mode protects quantities but cannot separate the venue's cost basis or margin.

Confirmed order P&L saved in `qd_exchange_order_pnl` is reused while its reported quantity matches the order's recorded fills. It does not expire after five minutes or require a new venue request on page refresh or worker restart. Missing reports or changed fill quantities still use bounded reconciliation; failures for other orders do not hide matching saved results. WebSocket execution evidence is also stored in `qd_execution_events` and can be used to reconstruct reports. These reports remain display-only and do not replace the strategy ledger.

### Closing precision and floating P&L

Full-fill quantity arithmetic uses decimal addition/subtraction and absorbs only binary rounding noise at the closing fill's scale. Position writes update unrealized P&L together with quantity and price; a delayed mark cannot overwrite a changed quantity or entry cost. Live equity metrics and the equity curve derive floating P&L from the current row's quantity, entry price and mark, so an old cached profit cannot inflate equity after a close. Normal mark updates also revalue positive precision-tail rows. This does not introduce a universal small-position cutoff, delete historical trades or bulk-delete residual inventory.

### Alpaca fractional-share residuals

For live `USStock` strategies using Alpaca, a full-exit intent is not queued when the remaining strategy position is at most `0.01` shares. This prevents repeated tail-close attempts and their failure notifications. The threshold is in shares, not dollars, and is an application policy rather than a broker minimum. The position and its value remain in the ledger; skipping an intent does not record a fill or mark the position flat. Larger closes, partial reductions, entries, signal mode, other brokers, and Crypto precision are unchanged. Existing queued orders are not cancelled by this rule.

### Repeated order failures

For individual orders outside a portfolio rebalance batch, the live pending-order worker stops a strategy after five consecutive definitive funding, credential, instrument, or account-configuration rejections. Invalid entry sizing also counts. The count belongs to the current strategy run and is stored in PostgreSQL; worker restarts do not reset it. A newly accepted exchange order resets the count. Enqueuing an order, an idle evaluation cycle, or receiving another update for an already accepted order does not reset it.

One-way-mode opposite-inventory guards, ownership drift, reduce-only conflicts, undersized exits, price-band limits, rate limits, temporary connectivity failures, and unknown submission outcomes do not count. Ignored outcomes do not clear earlier definitive failures. Existing immediate-stop handling for fatal authentication/adapter errors remains in place.

At the threshold, the run is marked stopped and its unsubmitted queued orders are blocked. A durable stop command cleans up the runtime; a command for an older runtime epoch cannot stop a restarted deployment. Already submitted orders still require exchange reconciliation. Automatic stops do not liquidate positions. If an operator explicitly selects stop-and-close while the command is still pending, that request is honored through the normal close-position safeguards.

The completed stop creates one in-app notice and sends the alert through each external channel enabled for the strategy: email, SMS, Telegram, Discord, or Webhook (including supported chat-platform webhooks). Targets use strategy overrides, then the user's notification settings; email can also fall back to the account address. Each channel needs its delivery configuration, such as SMTP for email or Twilio for SMS. The alert identifies the strategy and failure reason and asks for manual review before restarting. Stop notices are claimed once per run. Delivery is best-effort; one channel's failure does not prevent attempts on the others, restart the strategy, or trigger repeated alerts.

### Portfolio rebalance batches

The realtime producer claims each evaluation batch before queueing its first order. The key remains stable across worker ownership changes; a previously recorded batch is never reopened or assigned a new key to bypass sealing. Restart replays skip new submissions, while existing orders continue normal reconciliation. If queueing was interrupted, unqueued legs require review rather than automatic historical replay. This is duplicate prevention, not a guarantee that every leg completed. No existing order, trade, or holding is deleted by this recovery path.

Orders queued by one portfolio evaluation share a durable batch identifier. Dispatch is still asynchronous and does not wait for notification aggregation. Once queueing is sealed, the worker waits until every queued order has an acceptance or terminal result before assessing the batch. Batches are assessed in creation order, not reply order. A pending or unknown submission does not become a failure merely because time has passed.

Any accepted or partially/fully filled order makes the batch a success for the failure counter, even if other symbols fail. A completed batch with no success and at least one counted definitive rejection increments the counter once. Five such consecutive batches stop the strategy; ten rejected orders in one batch count as one failed batch. Batches containing only ignored protective outcomes do not increment or clear the counter. Individual-order and batch failure units are not added together. Successful legs are not rolled back, failed legs remain visible, and this policy does not add automatic retries.

Portfolio notifications are consolidated into one snapshot per user, strategy run and batch, in both live and signal mode. Each selected channel receives one summary instead of per-order notifications. The worker claims the summary when results are available, or on its first reconciliation pass at least 30 seconds after queueing finishes. The summary distinguishes accepted, filled, partially filled, failed, cancelled and pending orders; it does not claim a complete rebalance or real fills in signal mode. Short-message channels may truncate details at their existing message limits; counts appear first, and full order records remain available in the application. Later updates do not send another batch summary. Per-order logs remain intact. Automatic-stop alerts remain separate and do not wait for a batch summary.

Batch state and notification claims persist across worker restarts. Channel delivery is independent and best-effort: a failed channel does not block the others, but a claimed summary is not automatically resent after a delivery error or a crash during sending. Delivery runs separately from order dispatch and exchange reconciliation. Apply the registered `20261121_strategy_order_batches.sql` migration before starting the updated workers. Existing queued orders without batch identifiers keep their previous per-order behavior; there is no retroactive regrouping.

### Rejected exits and notification limits

There is no universal `0.01` quantity cutoff for Crypto or other brokers. Spot residual classification uses venue quantity steps, minimum quantities and minimum notionals, and requires a fresh, unlocked account balance consistent with the strategy's allocation. Missing rules or inconsistent balances do not establish a residual. Classified inventory retains its quantity and value and is reconsidered during reconciliation.

Strategy V2 skips a repeated live exit with the same quantity after an explicit minimum-quantity or minimum-notional rejection. The latest order is checked within the same user, strategy, credential, venue, symbol, market type and position side. There is no timed retry: restarting the strategy, changing the timestamp/reason or a price movement does not requeue the same rejected quantity. A changed quantity, partial fill or subsequent order is assessed normally. No holding is rounded, deleted or recorded as filled. Signal mode, entries, native zero-quantity full closes and unrelated balance, ownership, authentication or network failures are unaffected. Generic precision, `LOT_SIZE` and `invalid amount` errors alone do not establish a small residual and are not ignored.

The first size-rejection notification is sent normally; equivalent failures are deduplicated without a time expiry, including across worker and strategy restarts. PostgreSQL arbitrates concurrent notifications. Successful submission resets earlier failure notices; failed delivery releases its claim. A database error leaves alerts enabled. All failure records remain available for inspection.

Derivative closes retain the venue's reduce-only/full-close path; opening-order minimum notionals do not preemptively discard positions. Hong Kong odd lots remain holdings, not zero-value dust; only an explicit below-minimum rejection suppresses identical requests. This does not add an odd-lot execution channel or extend live-broker support. Existing queued orders are not cancelled. A residual that becomes tradable without changing quantity may require operator handling; there is no new automatic recovery service.

## Exchange-native flat closes

An exchange-native stop loss, take profit, liquidation, ADL event, or manual close can flatten the venue position before the normal pending-order path records the close. Position sync treats this as an account shortfall and blocks new entries or additions on that leg while keeping reconciliation and reduce-only risk controls available.

System close orders remain owned by the pending-order fill writer. Position sync defers while a close is being submitted or reconciled on the same account leg, and excludes matching completed orders from external-close history. Matching uses the credential, market, symbol, side and stored broker/client identifiers, never a parsed local numeric ID. True external fills retain their broker order IDs. Their trade and position updates commit together under the same strategy fill lock used by REST and private streams; a failed batch rolls back. This does not reconstruct missing migration history or rewrite previously duplicated trades.

QuantDinger clears the stale strategy leg only when all of these conditions hold:

- the exchange position snapshot is fresh rather than cached;
- the latest open or add fill is older than the configured grace window;
- one strategy owns the local account leg;
- every unrecorded close-side fill after the latest open or add belongs to the exact remaining quantity; and
- each fill is recorded idempotently before the local leg is removed.

If any condition is ambiguous, the ledger remains intact and the ownership block stays active for operator review. A ghost position alone does not stop the strategy process because stopping it can also stop monitoring and recovery. Stop or emergency-stop the strategy when exchange risk is still present, exposure is growing, credentials are unusable, or account truth cannot be recovered.

## Optional JEV System One entry filter

JEV is a System One model: it evaluates prepared state and typed questions into structured decisions instead of generating free-form text. Regular live strategies can use it before submitting entry orders. Quick Trade uses deterministic validation and explicit user confirmation without the AI decision filter. Strategy filter results are reduced to an auditable `PASS` or `REJECT`; exits, stop loss, take profit, and emergency risk reduction bypass the filter. If JEV is unavailable, QuantDinger falls back to the configured LLM. If neither provider is available, the order proceeds and the unavailable provider is recorded so AI failure does not disable trading.

## Equity regular-session execution

Live equity strategies evaluate trading callbacks, position protection, and portfolio risk exits only during the exchange's regular session. Existing exchange calendars handle DST, holidays, early closes, and lunch breaks; crypto spot and derivatives retain their continuous-market behavior. Heartbeats, position marking, and accepted-order reconciliation continue outside the session.

The queue boundary and each broker submission attempt check the session again. An unsubmitted instruction created outside the current continuous trading segment expires locally; it is not deferred for automatic execution at the next open. Reopening triggers a fresh evaluation of current prices and positions. A closed-session guard is not a consecutive exchange failure and does not send repeated failure notifications. Calendar lookup failure blocks new equity submissions rather than assuming the market is open.

Orders already accepted by a broker, including native brackets, remain broker-managed. Ambiguous submissions retain their client IDs and reconciliation path; this guard neither cancels them nor marks them unsubmitted. Existing broker orders from before deployment must be checked at the broker before assuming they cannot execute at the next open.

Broker-equity runtime prices retain the provider's observation timestamp through quote caching. Automatic protection, portfolio risk and daily rebalancing use prices only when that timestamp is known, no later than the evaluation time, within the configured maximum age, and within the current regular-session segment. **System Settings → Live Trading → Maximum quote age (minutes)** controls `EQUITY_QUOTE_MAX_AGE_MINUTES`: an integer from 1 to 60, default 16. The default accommodates 15-minute delayed feeds; it does not make them real-time. Delayed prices can delay local stops and differ from execution prices. Equity receipt-cache expiry uses the same window, so the shorter crypto receipt timeout does not override it. Repeated requests or cache hits never renew the provider timestamp. Missing, future, out-of-session or expired prices remain blocked; missing data does not cancel broker-accepted orders or stop reconciliation. With a delayed feed, the start of a regular-session segment may still wait until quotes from that segment arrive. Save the setting and restart the trading/runtime workers to apply it consistently across processes. Crypto and signal-mode price policies are unchanged; no database migration is required.

## Large equity universes and market-data diagnosis

Live US/HK daily warmup accepts the declared number of distinct, valid OHLC bars even when a newer listing does not cover the full padded calendar lookback. This exception is limited to runtime warmup; historical backtests still require their requested window. Latest completed-session checks, warmup readiness, and execution-quote freshness remain enforced. A successful HTTP response does not prove that a candle is current.

Daily runtime requests share normalized start dates and completed-session cutoffs across startup clocks. The process cache retains up to `STRATEGY_SHARED_FRAME_CACHE_MAX_SIZE` streams (default 4096), subject to `STRATEGY_SHARED_FRAME_CACHE_MAX_BYTES` (default 256 MiB). Each worker has its own bound. Redis reuse still depends on cache configuration and expiration; this is not a durable historical-data store. Crypto and intraday window rules are unchanged.

HK candle-provider exceptions now permit the next configured fallback. Tencent HTTP 403/429/501 responses open a 60-second process-local cooldown to avoid repeating blocked requests for every stock in a large pool. Already in-flight requests may still fail. No fallback fabricates candles, and public-provider availability or licensed coverage is not guaranteed by this mechanism.

For daily live evaluation, `Daily signal waiting for fresh quotes` lists missing symbols, provider sources and observation ages (up to ten examples). Recovery is logged separately. `Daily signal evaluated` reports the completed signal session and intent count, including zero. Intent count is before broker submission and does not mean filled orders. Compare the same source, parameters, completed session **and strategy-owned positions** when investigating different accounts: a held stock may qualify for continued allocation while an empty account has no new-entry signal. These changes require updated strategy workers; no database migration is needed.

## Binance REST request coordination

Binance public market data and private REST clients share a request guard. With the standard Compose deployment it uses jobs Redis; standalone processes without a configured Redis endpoint use process-local coordination only. `BINANCE_REST_REDIS_URL` overrides the endpoint. All QuantDinger processes/deployments sharing an outbound IP must use the same Redis endpoint and `BINANCE_REST_NAMESPACE`. A configured coordinator outage defers requests rather than falling back to unlimited traffic. Other software using the same IP is outside this guard.

Market data/fee queries and account reads have separate shared budgets, each defaulting to `BINANCE_REST_READS_PER_SECOND=5`. Brief congestion waits at most two seconds; longer waits return a retryable rate-limit error without sending HTTP. These are conservative request-count budgets, not a complete model of Binance endpoint weights. `X-MBX-USED-WEIGHT-1M` at `BINANCE_REST_WEIGHT_SOFT_LIMIT=1800` defers reads until the next minute. Writes do not consume these read budgets, and successful write responses are preserved even if publishing the weight feedback fails. There are no new automatic write retries.

HTTP 429/418 establishes a shared cooldown for all Binance REST traffic, including writes, based on `Retry-After` and any `banned until` timestamp (the longer deadline wins). Without a server deadline the fallback is 60/120 seconds respectively, plus one second of margin. Live hosts share a conservative scope; demo/test hosts are separate. Existing bans must still expire at Binance. Fee reconciliation remains pending and does not perform immediate retry loops; K-line retrieval does not repeatedly retry the same limited venue or label the symbol invalid. Futures execution requests the account's mark price first and fetches a generic quote only if it is unavailable.

Restart affected backend/market-data/trading workers after deploying or changing these environment settings. No migration is required. This guard reduces avoidable load; it cannot guarantee exchange availability or provide local stop execution while quotes/account REST are unavailable.

## Incident response

For non-Binance REST coordination and official venue references, see [Exchange REST limits](EXCHANGE_REST_LIMITS.md).

1. Stop new entries and additions.
2. Record deployment, account, instrument, order IDs, timestamps, and errors.
3. Compare the venue or broker's actual orders and positions; do not rely on cached UI state alone.
4. Before cancelling or reducing, confirm the action cannot cross protected manual inventory.
5. Stop the strategy or use emergency stop if risk continues to grow.
6. Do not restart until the root cause and ledger are reconciled.
