# Backtest Center guide

A backtest validates strategy behavior against specified historical data and execution assumptions. It is a research tool, not a promise of future returns or proof that live connectivity and venue rules are ready.

## Before running

Verify these inputs:

- Source and manifest are the intended strategy version.
- Market, instruments, and frequency use canonical identifiers.
- The start leaves enough warmup history for indicators and universe selection.
- Initial capital, fees, slippage, and position limits match the test.
- The benchmark is comparable to the strategy market and quote currency.

## Read a result

Review in this order:

1. **Data and status:** success does not prove complete data. Check first and last timestamps, bar counts, warmup, and missing fields.
2. **Execution assumptions:** inspect fees, slippage, liquidity, and unmodeled items. Strategy API V2 currently does not model Crypto funding payments.
3. **Risk:** review maximum drawdown, volatility, concentration, and the worst period.
4. **Execution ledger:** sample entries, exits, quantities, prices, fees, and position changes against the source.
5. **Benchmark:** compare both absolute return and relative performance.
6. **Robustness:** vary ranges, parameters, and regimes instead of keeping only the best run.

### Benchmark-relative metrics

When benchmark data is available, Strategy API V2 includes
`benchmarkRelativeMetrics`. The calculation first aligns portfolio and
benchmark observations to identical return intervals, then reports arithmetic
annualized portfolio, benchmark, and active returns. Annualized tracking error
uses the sample standard deviation of periodic active returns; the Information
Ratio is annualized active return divided by annualized tracking error. The
annualization factor is the same one used by the Strategy V2 backtest: 252
trading days for non-crypto markets and 365.25 days for crypto, with intraday
frequencies expanded using the corresponding session length; weekly data uses
52 periods per year.

Check `status` before reading the ratio. `insufficient_history` means fewer than
two aligned return observations were available. An interval is retained only
when both curves have valid positive levels at its exact start and end times,
so a missing endpoint discards that interval rather than stretching it across
multiple periods. `zero_tracking_error` leaves
the ratio and classification unset rather than emitting infinity. Missing or
non-finite observations are excluded, and negative ratios are preserved.

The default interpretation bands (`weak`, `acceptable`, `good`, and
`exceptional`) are operational labels, not a universal market standard. The
calculation accepts alternative bands when a research mandate requires them.

Benchmark choice remains part of the research hypothesis. CDI can be suitable
for Brazilian cash-like and low-duration fixed-income strategies, but it is not
an automatic default for inflation-linked, longer-duration, or credit-risk
mandates. Use an appropriate aligned benchmark series such as an IRF-M, IMA-B,
or credit index where the mandate calls for it; do not substitute a static
annual CDI rate for periodic benchmark observations.

## Common misreadings

- Zero executions may mean missing data, insufficient warmup, or unreachable conditions.
- Intraday history is often shorter, so a long requested range may be unavailable.
- Leveraged results cannot be extrapolated without funding and liquidation risk.
- Tokenized equities, exchange equity perpetuals, and broker securities are different execution products.
- Historical data availability does not imply live support.

## Continue safely

Save the strategy version, parameters, data range, and result. Create a stopped deployment and then use `signal` mode. Complete the [live-trading safety checklist](../trading/LIVE_TRADING_SAFETY.md) before real capital. See the [Strategy API V2 guide](../trading/STRATEGY_DEV_GUIDE.md) for the programming contract and errors.

