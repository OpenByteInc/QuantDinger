# Live-trading safety guide

This guide defines the minimum checks before moving from backtest or signal mode to real capital. Venue, broker, region, and account type affect available products; the active catalog and account response take precedence over static examples.

## Support boundary

| Market | Current live venues | Key limit |
| --- | --- | --- |
| Crypto | Binance, Bitget, Bybit, OKX, Gate, HTX | spot and swap depend on venue and account capability |
| Exchange direct equities | Gate stock channel | retain exact currency and catalog identity; long-only |
| Exchange tokenized equities | OKX, Bybit, Bitget Reality | catalog and regional availability required; long-only |
| Exchange equity perpetuals | Binance, OKX, Bitget, Bybit, Gate | derivative exposure; exact native contract and swap rules required |
| Binance bStocks | Binance | exact catalog pair, spot semantics, and no leverage |
| USStock | Alpaca, IBKR, Futu/Moomoo OpenD, Webull OpenAPI | current broker strategy policy is long-only |
| HKStock | Futu/Moomoo OpenD | current broker strategy policy is long-only |
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

## While running

- Monitor runtime state, order status, fills, positions, available balance, and notifications.
- An unknown reconciliation delta pauses same-side entries or additions; do not bypass that protection.
- Minimum quantity, step size, minimum notional, and available margin can change the submitted quantity.
- WebSocket improves latency; REST reconciliation remains the recovery source after disconnects.
- A strategy manages only its allocated position and must not absorb manual or other-strategy inventory.

### Alpaca fractional-share residuals

For live `USStock` strategies using Alpaca, a full-exit intent is not queued when the remaining strategy position is at most `0.01` shares. This prevents repeated tail-close attempts and their failure notifications. The threshold is in shares, not dollars, and is an application policy rather than a broker minimum. The position and its value remain in the ledger; skipping an intent does not record a fill or mark the position flat. Larger closes, partial reductions, entries, signal mode, other brokers, and Crypto precision are unchanged. Existing queued orders are not cancelled by this rule.

### Repeated order failures

For individual orders outside a portfolio rebalance batch, the live pending-order worker stops a strategy after five consecutive definitive funding, credential, instrument, or account-configuration rejections. Invalid entry sizing also counts. The count belongs to the current strategy run and is stored in PostgreSQL; worker restarts do not reset it. A newly accepted exchange order resets the count. Enqueuing an order, an idle evaluation cycle, or receiving another update for an already accepted order does not reset it.

One-way-mode opposite-inventory guards, ownership drift, reduce-only conflicts, undersized exits, price-band limits, rate limits, temporary connectivity failures, and unknown submission outcomes do not count. Ignored outcomes do not clear earlier definitive failures. Existing immediate-stop handling for fatal authentication/adapter errors remains in place.

At the threshold, the run is marked stopped and its unsubmitted queued orders are blocked. A durable stop command cleans up the runtime; a command for an older runtime epoch cannot stop a restarted deployment. Already submitted orders still require exchange reconciliation. Automatic stops do not liquidate positions. If an operator explicitly selects stop-and-close while the command is still pending, that request is honored through the normal close-position safeguards.

The completed stop creates one in-app notice and sends the alert through each external channel enabled for the strategy: email, SMS, Telegram, Discord, or Webhook (including supported chat-platform webhooks). Targets use strategy overrides, then the user's notification settings; email can also fall back to the account address. Each channel needs its delivery configuration, such as SMTP for email or Twilio for SMS. The alert identifies the strategy and failure reason and asks for manual review before restarting. Stop notices are claimed once per run. Delivery is best-effort; one channel's failure does not prevent attempts on the others, restart the strategy, or trigger repeated alerts.

### Portfolio rebalance batches

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

QuantDinger clears the stale strategy leg only when all of these conditions hold:

- the exchange position snapshot is fresh rather than cached;
- the latest open or add fill is older than the configured grace window;
- one strategy owns the local account leg;
- every unrecorded close-side fill after the latest open or add belongs to the exact remaining quantity; and
- each fill is recorded idempotently before the local leg is removed.

If any condition is ambiguous, the ledger remains intact and the ownership block stays active for operator review. A ghost position alone does not stop the strategy process because stopping it can also stop monitoring and recovery. Stop or emergency-stop the strategy when exchange risk is still present, exposure is growing, credentials are unusable, or account truth cannot be recovered.

## Optional JEV System One entry filter

JEV is a System One model: it evaluates prepared state and typed questions into structured decisions instead of generating free-form text. Regular live strategies can use it before submitting entry orders. Quick Trade uses deterministic validation and explicit user confirmation without the AI decision filter. Strategy filter results are reduced to an auditable `PASS` or `REJECT`; exits, stop loss, take profit, and emergency risk reduction bypass the filter. If JEV is unavailable, QuantDinger falls back to the configured LLM. If neither provider is available, the order proceeds and the unavailable provider is recorded so AI failure does not disable trading.

## Incident response

1. Stop new entries and additions.
2. Record deployment, account, instrument, order IDs, timestamps, and errors.
3. Compare the venue or broker's actual orders and positions; do not rely on cached UI state alone.
4. Before cancelling or reducing, confirm the action cannot cross protected manual inventory.
5. Stop the strategy or use emergency stop if risk continues to grow.
6. Do not restart until the root cause and ledger are reconciled.
