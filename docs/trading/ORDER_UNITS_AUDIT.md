# Order units, precision and Hong Kong board-lot audit

Reviewed 2026-11-26. Scope: desktop quick trade, mobile stock tickets, broker adapters and strategy execution. Options are excluded. Futures strategies remain disabled.

## Completed changes and important finding

Broker ordering is now Alpaca, Forthright, Tiger, Webull, IBKR in the desktop/mobile lists. Direct-broker amount conversion now uses decimal integer arithmetic before flooring in both frontends. This fixes boundaries such as 0.29 / 0.01 producing 28 instead of 29. Desktop quantity increments now follow the selected quantity precision.

**HK board-lot handling is still incomplete.** Frontends floor to whole shares; Forthright strategy normalization also floors to one share; the strategy engine defaults to a one-share step without explicit rules. None of these supplies the security's actual board lot. This audit does not implement a board-lot feed.

Quantity precision, minimum quantity, quantity step, minimum notional and price tick are separate constraints.

## Unit matrix

| Product | Quantity | Minimum/step | Price and amount | Current boundary |
| --- | --- | --- | --- | --- |
| Crypto spot | Base asset, such as BTC | Venue/pair quantity filters plus possible minimum notional | USDT/BTC and USDT for BTC/USDT; USD and USDT are not interchangeable | Backend InstrumentRules and venue adapters exist; a generic frontend precision is not a venue rule |
| Crypto perpetual | Usually base quantity internally; some venues require contracts | Contract conversion followed by native quantity and notional checks | Desktop margin input differs from notional, approximately margin × leverage | Do not confuse base quantity, contract count and margin |
| US equity | Shares, fractional only where eligible | Broker/security/session/account/order dependent | USD/share and USD for the supported dollar counters | Broker distinctions below |
| HK equity | Shares; each board lot contains a security-specific number | Ordinary board-lot orders use whole multiples; odd lots are distinct | Current application stock route assumes HKD counters | Dynamic board-lot and price-band metadata is missing; other currency counters are not established |
| Listed futures | Actual dated contract count | Current Forthright ticket uses positive integers and contract price ticks | Quotes may be index points, dollars/barrel or other units; margin has its own currency | Verified contract metadata is used where available; automated strategies remain disabled |

Crypto has no universal eight-decimal or five-USDT minimum. Read native symbol filters.[Binance filters](https://developers.binance.com/en/docs/products/spot/filters)

MES has a USD 5/index-point multiplier and a 0.25-point tick, giving USD 1.25 per tick per contract. That is not the margin required to open a contract.[CME specifications](https://www.cmegroup.com/markets/equities/sp/micro-e-mini-sandp-500.contractSpecs.html)

## US broker differences

- Alpaca documents up to nine quantity decimals and two USD notional decimals, with fractional eligibility checks. The desktop generic quantity input currently uses eight. Market amount orders use native notional; the backend limit-amount route converts to a nine-decimal quantity.
- Forthright documents at most four US quantity decimals. Frontends convert budgets to quantities at that precision. This alone does not establish every instrument's fractional eligibility or a universal minimum dollar amount.
- Tiger supports a scaled quantity representation in its SDK. The current adapter does not set that scale, so the UI uses whole shares. Amount entry currently means local conversion to whole shares.
- Webull documents QTY and AMOUNT orders, but our adapter currently sends QTY and frontend conversion uses six decimals. Six is an implementation setting, not verified universal eligibility across Webull jurisdictions and instruments. Per-instrument fractional/minimum-value checks remain incomplete.
- IBKR retains its existing adapter and remains excluded from the mobile trade selector.

Sources: [Alpaca](https://docs.alpaca.markets/us/docs/fractional-trading), [Forthright](https://dev-openapi-doc.techfx88.com/zh/trade/order/place_order.html), [Tiger](https://docs-en.itigerup.com/docs/place-order-java), [Webull](https://developer.webull.com/apis/docs/reference/common-order-place/).

## HK strategy implications

Board-lot size varies by issuer/security. Odd lots do not use ordinary board-lot automatic matching; they have a separate trading arrangement.[HKEX trading explanation](https://www.hkex.com.hk/Global/Exchange/FAQ/Securities-Market/Trading/Securities-Market-Operations?sc_lang=en)

Illustration only: for a 100-share lot priced at HKD 63.35, one lot costs HKD 6,335 before fees. A HKD 5,000 budget yields zero board lots, not 78 shares; HKD 10,000 yields 100 shares, not 157. A remaining 37-share holding needs an odd-lot exit path, not rounding up to 100 or silently declaring it closed. There is no established universal per-order HKD minimum in the reviewed Forthright endpoint; budget needs depend on actual lot size, price, fees and broker limits.

Forthright requires positive integer HK quantities. Its documented TradeType values do not identify an explicit odd-lot route. max_buy exposes enable_buy_qty/enable_amt and max_sell exposes enable_qty: capacity is not board-lot metadata and cannot establish lot size.[Trade types](https://dev-openapi-doc.techfx88.com/zh/trade/api.html), [max buy](https://dev-openapi-doc.techfx88.com/zh/trade/fund/max_buy.html), [max sell](https://dev-openapi-doc.techfx88.com/zh/trade/fund/max_sell.html)

## Required follow-up, not implemented by this audit

1. Obtain dated security-level lot, currency, tick and fractional/odd-lot eligibility metadata, with freshness and corporate-action handling. The [HKEX securities list](https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx) is a candidate board-lot source.
2. Use those same rules in preview and strategy sizing. Reserve costs, then floor budget / price / board lot to whole lots. Market estimates do not guarantee a cash spending ceiling.
3. Skip and explain entries below a lot; never round up, increase allocation or endlessly replay an invalid instruction.
4. Preflight broker maximum buy/sell capacity. Forthright uses total buying power in the instrument's quote currency; broker-managed conversion does not permit comparing an HKD number directly with a USD order.
5. Preserve strategy ownership and sellable quantity on exits. Odd-lot remainders require a supported broker path or an explicit unresolved residual.
6. Use traceable rules in backtests and execution. Submission acknowledgement is not a fill; reconcile unknown outcomes before retrying.
7. Complete per-security stock price-tick and fractional/minimum-notional validation. Some current adapter paths still rely on broker rejection.

## Code and verification boundary

Broker implementations remain in separate files under backend_api_python/app/services/openapi_brokers/ (forthright.py, tiger.py, webull.py); Alpaca has app/services/alpaca_trading/. These routing implementations were not changed in this audit. The default strategy step is in instrument_rules.default_rules_for_symbol, consumed by strategy_v2/runtime.py; Forthright normalization is called from shared ownership validation.

Frontend arithmetic lives in each repository's src/utils/orderQuantity.mjs, consumed by desktop QuickTradePanel and mobile stockOrder.js. Verification covers static code/document review, automated decimal-boundary regression and browser ordering. No new orders were sent. Sandbox acceptance does not establish production board-lot or odd-lot support.
