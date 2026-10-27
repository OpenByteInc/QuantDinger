# Alpaca execution-fee reconciliation

Alpaca can report an order fill before its authoritative fee evidence is available. QuantDinger therefore treats fill quantity and fee finality as separate states: the trade may already exist while its fee is still marked `pending`.

## What the interface means

| Display | Meaning |
| --- | --- |
| `Pending confirmation` | The fill is recorded, but no authoritative fee or zero-fee evidence has been received yet. |
| A non-zero fee | Alpaca activity data reported an execution cost and QuantDinger stored it as an actual fee. |
| `$0.00` after confirmation | Alpaca explicitly reported zero commission; this is stored as `actual_zero`, not guessed from a missing value. |

The top cost cards aggregate persisted execution records. A fee that is still pending is not silently converted to zero and is not included as an authoritative cost until reconciliation completes.

## Reconciliation flow

1. The order and its fill quantity are persisted as soon as the broker confirms execution.
2. When the fill payload does not contain authoritative fee evidence, the trade remains `fee_status=pending`.
3. The Alpaca synchronization worker retries completed orders that still contain pending-fee trades after the configured retry interval.
4. Authoritative broker activity data is normalized into a currency-by-currency fee breakdown.
5. QuantDinger converts the breakdown into quote currency where possible and updates the matching persisted trades proportionally by executed value.
6. The trade is finalized as `actual` for a non-zero fee or `actual_zero` for an explicitly confirmed zero fee. The evidence source is stored as `rest`.

This process is idempotent: only trades whose fee state is still pending are backfilled, so repeated synchronization does not charge the same fee twice.

## Operational checks

If a fee remains pending longer than expected:

1. Confirm the trading worker is healthy and has access to the same database as the API.
2. Verify the Alpaca credential is active and can read orders and account activities.
3. Confirm the pending order has an exchange order ID and a positive filled quantity.
4. Check trading-worker logs for `alpaca_fill_sync`, authentication errors, or activity-query failures.
5. Verify the relevant `qd_strategy_trades` row still has `fee_status=pending`; completed reconciliation should show `actual` or `actual_zero` and a `fee_source`.

Do not manually replace a pending fee with zero merely because the broker has not returned it yet. That would turn missing evidence into false accounting data.

## Scope

This guide describes QuantDinger's Alpaca strategy-execution ledger. Broker statements and Alpaca's own account activity remain the final external source for account-level charges. Regulatory, activity, ADR, interest, and other broker-level costs may arrive independently from per-fill commission and are displayed separately when available.
