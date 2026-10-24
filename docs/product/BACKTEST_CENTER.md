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
`benchmarkRelativeMetrics`. The calculation samples the portfolio at the
benchmark's native observation times before calculating returns. This matters
when a long intraday backtest uses a coarser benchmark series: both return
streams and the annualization factor use that coarser frequency, without
forward-filled prices creating artificial tracking error.

Annualized portfolio, benchmark, and active returns use arithmetic periodic
means. Annualized tracking error uses the sample standard deviation of periodic
active returns, and the Information Ratio divides annualized active return by
annualized tracking error. Continuous-market gaps and non-consecutive equity
sessions are excluded rather than treated as one ordinary period.

Check `status` before reading the ratio. `insufficient_history` means fewer than
two valid aligned return observations were available. `zero_tracking_error`
leaves the ratio and classification unset instead of emitting infinity.
Missing, non-finite, and non-positive observations are excluded, while negative
Information Ratios are preserved.

The default interpretation bands (`weak`, `acceptable`, `good`, and
`exceptional`) are operational labels rather than a universal market standard.
The calculation accepts alternative bands for mandates that require them.

In addition to the Information Ratio, `benchmarkRelativeMetrics` provides:

- **Beta ($\beta$):** Systematic risk relative to the benchmark ($\text{Cov}(R_p, R_b) / \text{Var}(R_b)$). Zero benchmark variance yields `zero_benchmark_variance` status.
- **Jensen's Alpha ($\alpha_{\text{ann}}$):** Annualized risk-adjusted excess return over CAPM expected return: $(R_{p,\text{ann}} - R_f) - \beta (R_{b,\text{ann}} - R_f)$. Strategy API V2 currently uses an explicit zero annual risk-free rate and returns that assumption in `riskFreeRate`.
- **Treynor Ratio:** Annualized excess return per unit of systematic risk: $(R_{p,\text{ann}} - R_f) / \beta$.
- **Correlation & $R^2$:** Pearson correlation coefficient ($r \in [-1, 1]$) and coefficient of determination ($R^2 \in [0, 1]$).
- **Capture Ratios:**
  - `upCapture`: Geometric-mean portfolio return divided by geometric-mean benchmark return across periods where $R_b > 0$.
  - `downCapture`: Geometric-mean portfolio return divided by geometric-mean benchmark return across periods where $R_b < 0$.
  - `captureRatio`: Ratio of upside capture to downside capture (`upCapture / downCapture`). A value $> 1.0$ indicates greater participation in up markets than down markets.
- **Modigliani–Modigliani ($M^2$) & $M^2 	ext{ Alpha}$:** Scales portfolio volatility to match benchmark volatility, converting the Sharpe ratio into an annualized percentage return: $M^2 = R_f + 	ext{Sharpe}_p 	imes \sigma_{b,	ext{ann}}$ and $M^2_{	ext{alpha}} = M^2 - R_{b,	ext{ann}}$.
- **Dual Beta (Up-Market & Down-Market Beta):**
  - `upBeta` ($eta^+$): Systematic sensitivity evaluated strictly over benchmark up-market periods ($R_b > 0$).
  - `downBeta` ($eta^-$): Systematic sensitivity evaluated strictly over benchmark down-market periods ($R_b < 0$).
  - `betaSpread`: Difference between upside and downside beta ($eta^+ - eta^-$). A positive spread indicates convex upside participation with defensive downside insulation.
- **Batting Average & Outperformance Payoff:**
  - `battingAverage`: Percentage of observation periods where portfolio return exceeds benchmark return ($\sum \mathbb{I}(R_{p,t} > R_{b,t}) / T$).
  - `outperformancePayoffRatio`: Ratio of average active return during outperforming periods to average active loss during underperforming periods.

The top-level `status` remains the Information Ratio status for compatibility.
`betaStatus`, `correlationStatus`, `treynorStatus`, `captureStatus`, `mSquaredStatus`, `dualBetaStatus`, and `battingStatus`
describe metric-specific availability. In particular, a flat benchmark makes
Beta unavailable but does not suppress a valid Information Ratio. Correlation
and $R^2$ remain unset when either return series is constant because Pearson
correlation is undefined in that case. Capture values use ratio units: `1.0`
means 100% capture.

Benchmark choice remains part of the research hypothesis. CDI can be suitable
for Brazilian cash-like and low-duration fixed-income strategies, but it is not
an automatic default for inflation-linked, longer-duration, or credit-risk
mandates. CDI ingestion and its canonical identifier require a separate market
data design; do not substitute a static annual CDI rate for periodic benchmark
observations.

## Common misreadings

- Zero executions may mean missing data, insufficient warmup, or unreachable conditions.
- Intraday history is often shorter, so a long requested range may be unavailable.
- Leveraged results cannot be extrapolated without funding and liquidation risk.
- Tokenized equities, exchange equity perpetuals, and broker securities are different execution products.
- Historical data availability does not imply live support.

## Continue safely

Save the strategy version, parameters, data range, and result. Create a stopped deployment and then use `signal` mode. Complete the [live-trading safety checklist](../trading/LIVE_TRADING_SAFETY.md) before real capital. See the [Strategy API V2 guide](../trading/STRATEGY_DEV_GUIDE.md) for the programming contract and errors.
