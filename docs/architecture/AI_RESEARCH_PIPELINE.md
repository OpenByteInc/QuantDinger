# AI Research Pipeline

QuantDinger keeps the existing Flask collector, fast-analysis service and
`professional_report_v1` contract. A thin workflow layer now adds asset-aware
research requirements, deterministic calculations and explicit readiness
gates without replacing the trading or analysis architecture.

## Functional data baseline

| Asset | Keyless baseline | Optional configured sources | Research output |
| --- | --- | --- | --- |
| US equities | SEC EDGAR, Yahoo Finance, FINRA daily short volume | Finnhub, Alpha Vantage, FRED, Twelve Data | Financial periods, filings, analyst consensus, bounded options surface, reported short interest, FINRA short volume, ownership and transcript evidence |
| Mainland China A-shares | Tencent quotes, AkShare/Eastmoney financial data, Yahoo Finance fallback | Twelve Data fundamentals/statements where entitled; configured news search provider | Company identity, valuation and available financial periods. Issuer news is searched by company/code; unrelated results remain background. |
| Hong Kong equities | HKEX public information, HKMA Open API, Eastmoney/ET Net through AkShare | Licensed HKEX Data Marketplace or LSEG feeds | Financial periods, announcements, Southbound holdings proxy, short selling, CCASS, A/H applicability and analyst consensus |
| Crypto | Exchange public APIs, CoinGecko | CoinGlass, CryptoQuant | Spot and perpetual scope, funding, open interest, liquidations, flow and on-chain evidence |
| FX | Yahoo Finance fallback | Twelve Data, Tiingo and FRED | Base/quote identity, technical structure, policy-rate context and policy-rate differential |
| Traditional futures | Yahoo Finance and CFTC weekly reports | Twelve Data | Nearby expiry curve, continuous-contract fallback, CFTC participant positioning and technical structure |

## Optional environment keys

- `FRED_API_KEY`: policy rates and official US macro series.
- `ALPHA_VANTAGE_API_KEY`: bounded earnings-call transcript evidence.
- `FINNHUB_API_KEY`: US equity fundamentals, estimates and news fallback.
- `TWELVE_DATA_API_KEY`: FX and traditional-futures quotes and history.
- `TIINGO_API_KEY`: FX and precious-metals fallback.
- `COINGLASS_API_KEY`: aggregate crypto derivatives evidence.
- `CRYPTOQUANT_API_KEY`: on-chain and exchange-flow evidence.

Missing optional keys do not produce synthetic values. The report exposes the
missing capability, lowers its research-readiness posture and applies the
existing quality gate before any directional trade action.

## Semantics that must remain distinct

### A-share collection, synchronization and coverage

The analysis collector reads its short-lived memory cache, then the shared
`qd_fundamental_snapshots` store. Missing, incomplete or refresh-due snapshots
trigger provider requests; successful results, including the richer statement
payload, are written back. A fresh stored provider payload can be reused across
processes. The database refresh interval defaults to 24 hours
(`AI_FUNDAMENTAL_DB_TTL_SEC`); this is a cache policy, not a guarantee of a new
financial disclosure every day.

System Settings → market-data management → fundamental data preparation can
prepopulate or refresh an A-share universe's **current snapshots**. This is not
a prerequisite for one-off company analysis and does not establish historical
financial coverage. Check task failures and provider reachability before
forcing a full refresh. Optional Twelve Data access requires both a configured
key and an appropriate provider entitlement; no key can guarantee coverage.

A-share news uses the configured search service even when Finnhub has global
headlines. Search results without a company-name or stock-code match are
background, not issuer-specific sentiment. Configure/test a search provider in
System Settings → AI Research → search engines; synchronizing financial
snapshots does not collect news.

Exchange announcements, Northbound data, margin financing/lending, analyst
expectations and domestic macro evidence are separate capabilities. The current
A-share collector does not automatically collect these optional enrichments.
They remain explicit report gaps; neither a financial sync nor filling in an
unrelated API key enables them. Availability and granularity depend on source
disclosures, and missing values are not replaced by zero.

Core coverage for A-shares includes company identity, quote/K-line evidence,
technical structure and fundamental metrics. It does not certify every research
capability. Core freshness/conflict percentages use the same limited metric
set. Research readiness separately lists required and optional gaps, and a
company profile is recognized from the collector's `company` field. Saved
reports retain their original evidence and scores; regenerate a report to use
the corrected assessment. Clients also localize legacy generic scenario text
in the monitoring checklist.

### Market-specific interpretation

- FINRA short volume is not reported short interest and does not prove new
  directional short positions.
- CFTC Commitments of Traders is weekly positioning, not real-time order flow.
- A continuous front-contract history is not an expiry term structure. The
  report only labels contango or backwardation when at least two distinct
  expiry contracts are available.
- FX policy-rate differential is only calculated when both base and quote
  currency observations are available.
- Provider estimates, exchange data and regulatory filings retain separate
  source and observation timestamps.

## Compatibility

The report schema name remains `professional_report_v1`. New
`research_intelligence` and cross-asset `market_features` fields are additive,
so saved V1 reports and existing clients remain valid. The research mode sent
by the Copilot (`research`, `diagnosis`, `technical`, `plan`, `news` or `macro`)
is preserved in the workflow metadata.
