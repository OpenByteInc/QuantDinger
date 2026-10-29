# AI Research Pipeline

QuantDinger keeps the existing Flask collector, fast-analysis service and
`professional_report_v1` contract. A thin workflow layer now adds asset-aware
research requirements, deterministic calculations and explicit readiness
gates without replacing the trading or analysis architecture.

## Functional data baseline

| Asset | Keyless baseline | Optional configured sources | Research output |
| --- | --- | --- | --- |
| US equities | SEC EDGAR, Yahoo Finance, FINRA daily short volume | Finnhub, Alpha Vantage, FRED, Twelve Data | Financial periods, filings, analyst consensus, bounded options surface, reported short interest, FINRA short volume, ownership and transcript evidence |
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
