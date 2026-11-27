# Forthright OpenAPI integration

Forthright (方德证券) supports US and Hong Kong securities and exact dated futures through separate saved account types. Options are excluded. Desktop and mobile use the same tenant-scoped `/api/forthright` routes.

## Securities acceptance update (2026-11-26)

- Per the account owner's confirmation from Forthright, the broker handles automatic HKD/USD conversion. Tickets use `fund_total.enable_buy_power` (total buying power) denominated in the instrument currency: USD for US stocks and HKD for Hong Kong stocks. Original-currency cash is informational, not a separate purchase cap. No FX or transfer instruction is sent by QuantDinger. Zero buying power stays zero; missing or wrong-currency snapshots cannot fall back to cash.
- The inspected simulated securities account had USD cash 0, USD buying power 1,015,948.11 and HKD buying power 7,998,863.93. These are two currency representations of purchasing capacity, not two independent balances to add together. Mobile input accepted a one-share US ticket despite zero USD cash and blocked a ticket exceeding buying power.
- Live test-gateway requests accepted AAPL 1 share at limit 1 (order `20261127US00000132`) and 00700.HK 100 shares at limit 1 (order `20261127HK00000014`). Lookup by broker order ID and client ID agreed; both orders were cancelled with zero fills. The final account snapshot had no positions or open orders.
- Both exchanges were closed according to the trading calendar. This run did not validate a fresh market fill or a live strategy open/close cycle. Offline tests cover US/HK market and limit submissions, strategy open/close direction, closed-session blocking, client identity, cumulative fills and repeated fee snapshots. Historical HK fills remain evidence of earlier manual fills, not this run's strategy acceptance.
- Final securities fills use documented `fare_amt` and `ccy` to report cumulative actual fees, including explicit zero. Pending/partially-filled orders do not certify estimated fees. Fee currency and quote value stay USD/HKD as appropriate; absent fee evidence remains pending.
- Futures strategy deployment remains disabled. Securities strategy budgets remain single-currency; automatic broker conversion does not make a mixed USD/HKD strategy ledger valid.
- Validation: 454 backend regression tests, 793 desktop tests and 211 mobile tests passed. Both production builds and locale/encoding audits passed. Desktop lint had no errors but retained existing template-format warnings. PC and mobile tickets were checked in the built-in browser for USD/HKD switching and oversized buy prevention.
- Local API was rebuilt and its read-only historical fee queries verified (HK buy 64.38 HKD, sell 64.36 HKD). Existing strategy workers were not restarted while unrelated strategies were active; the fee-adapter update still needs to be rolled into those workers before the next live strategy acceptance run. A strategy's configured budget remains its allocation limit; the account's total buying power is not automatically allocated in full to each strategy.

### Broker isolation

`openapi_brokers/forthright.py`, `tiger.py` and `webull.py` own their protocol, authentication and normalization. `base.py` owns shared transport/result primitives. `clients.py` remains a compatibility facade, preserving existing imports and class identity. The Tiger and Webull class bodies were moved without changing their implementation. Alpaca and IBKR retain their separate `alpaca_trading/client.py` and `ibkr_trading/client.py` modules.

New brokers should implement a separate adapter, register it in the client factory and market/account capability rules, and add broker-specific contract tests plus shared strategy regression tests. The strategy queue, account ownership and cumulative fill ledger remain shared infrastructure; broker wire fields must not leak into another adapter.

## Connect and unlock

Enter the App ID, PKCS#8 Ed25519 or RSA private key and trading password once. The application discovers securities and futures subaccounts with `/account/list` and saves them in one database transaction. Each subaccount retains its own credential ID, market routing and unlock state. Options accounts are ignored. Reconnecting updates matching App ID/account/type records, including older records labelled sandbox, instead of creating duplicates. The login number is not necessarily the API trading account number.

The connection form selects a fixed gateway: test `https://test-openapi-gw.techfx88.com` or production `https://openapi-gw.forthright-sec.com.hk`. New credentials persist `gateway_environment` separately from legacy environment labels. Existing credentials without this field keep the former deployment gateway (`FORTHRIGHT_GATEWAY_URL`, or the test host when unset); upgrades never move an account to production. Deduplication includes the gateway, application, account and category. Strategies cannot override a saved credential's gateway. Gateway selection does not by itself establish whether an account is funded or virtual. The browser cannot supply an arbitrary upstream host.

Connection is read-only. Use Unlock on the saved account before trading. Error `10003` requires SMS second authentication for that account type; enter the code in the same account's verification field. The published API documents unlock and second authentication, but no separate SMS-send endpoint or delivery receipt. An unlock error does not prove a text message was delivered. If no code arrives, ask Forthright to check the account's bound phone and authentication settings. Do not repeatedly submit orders to trigger SMS.

## Trading and reconciliation

- Securities: market/limit, day orders; US quantities have at most four decimal places. Forthright HK orders must be exact multiples of the security's current board lot; odd lots are unsupported. Both preview and backend enforce this rule. Broker price and permission checks still apply. Market orders require a positive reference price. Stock strategies remain long-only.
- Futures quick trading: choose an exact `prodCode` from the contract list, enter integer lots and submit a market/limit day order. Continuous symbols and expired contracts are rejected; there is no automatic roll. Buy/sell operate the broker's net position, so a trade can close existing exposure.
- Strategy execution retains the durable pending-order queue, account ownership checks, idempotent client identifiers and fill reconciliation. Futures support long/short directions with net position ownership; simultaneous opposite allocations on the same account/instrument are blocked. Crypto leverage settings and grid/martingale bots are not enabled for futures.
- Acknowledgement is not a fill. Unknown network or gateway outcomes enter reconciliation and are never blindly retried. Only explicit `10005` non-acceptance permits unlock and one repeat with the same identifier. Futures cancellation recovers `extOrderId`, `accOrderNo`, product and side from the account's order snapshot, including after a restart.
- Futures fill prices come from decimal execution averages with matching cumulative quantities, not the integer `totalTrdPrc` field whose scale is unspecified. Missing/lagging execution details keep reconciliation pending. Fees stay pending until an authoritative fee source is implemented; they are not reported as confirmed zero.
- Attached broker stop-loss/take-profit orders are unavailable. Strategy runtime protection continues through the existing worker while it is running. Quick-trade protection controls are hidden for Forthright.

## Futures strategy metadata and market data

Configure `FORTHRIGHT_FUTURES_CONTRACT_SPECS` as a JSON object in the deployment `.env`, mounted by the normal Compose deployment into the API and workers. Restart affected processes after changes. Each exact broker `prodCode` needs:

| Field | Meaning |
| --- | --- |
| `point_value` | Verified settlement-currency value of a one-point move for one contract; positive number |
| `currency` | `USD` or `HKD`; must equal the saved futures credential's currency |
| `data_provider` | `yfinance` or `twelvedata` |
| `data_symbol` | Provider's exact dated contract code, verified to represent the same product, expiry, currency and price scale |

The point value/currency may also be keyed by the broker's `instmntCode`, but the market-data mapping must be keyed by the exact `prodCode`. Keep historical metadata for expired contracts so outstanding fills remain reconcilable. `lotSize` and `tickSize` are not assumed to be monetary point values. No unverified multipliers or continuous-contract feed mappings are supplied by default.

Deployment fails closed when these details are missing or inconsistent. The dated data path does not fall back to crypto, spot metals or continuous futures. Provider subscriptions and usable, sufficiently fresh data remain necessary; a configured mapping alone does not guarantee feed availability. Manual futures tickets do not need this strategy metadata.

The existing strategy ledger values quantity × price, so futures strategy quantities are stored as contracts × point value. For a verified multiplier of 5, ledger quantity 10 submits 2 lots and a one-point move changes P&L by 10 settlement-currency units. The adapter floors strategy quantities to whole contracts, never increases the budget, and converts broker fills/positions back to ledger units. Quick-trade quantities and broker-account views are always actual lots. Capital limits use full notional with leverage 1; exchange margin schedules are not modelled.

## Verification status

User-supplied SMS verification succeeded for both securities and futures. Signed account enumeration, funds, positions, orders and contract queries passed against the user-provided account, initially described by the user as virtual. This is not inferred from the gateway hostname.

- US AAPL: a one-share limit order was accepted, queried and cancelled. A market order was explicitly rejected outside the continuous session (`10007`); US market fills remain unverified.
- HK 00700.HK: a 100-share limit order was accepted, queried and cancelled. A separate 100-share market buy filled, followed by a filled 100-share market sell; the test position returned to zero. Actual broker responses verified share quantity and average execution price.
- Futures: MES and HSI limit tests and an MES market test returned trade-service error `10000`. The retained decoded diagnostic text was garbled; original response bytes were not retained, so corruption cannot be attributed to the gateway or local decoding. Queries have not found corresponding orders or positions. Futures fills and cancellation remain unverified. Reported test cash was USD 100; this alone does not establish the rejection reason.

Trade-service errors, transport failures and acknowledgements without valid order IDs enter reconciliation and are not automatically resubmitted. Before production rollout, Forthright must resolve the futures test-service errors and futures fill fields, fees and cancellations must be verified.

The automated regression suite covers the official signing vector, exact request bytes, account/type isolation, invalid products and quantities, expired contracts, safe error handling, futures quantity conversion, exact data routing, fill consistency and cancellation identifiers.

Official reference: [Forthright OpenAPI](https://dev-openapi-doc.techfx88.com/zh/index.html).

## Error diagnostics and display precision

Response bytes are decoded strictly as UTF-8 regardless of a misleading HTTP charset. Error envelopes with invalid UTF-8 may be decoded as GB18030; successful financial snapshots never use guessed encodings. Structured error details include the broker code, redacted message, decoding used and whether replacement characters were already present in the wire JSON. Text already replaced upstream cannot be reliably reconstructed. Private keys, passwords, application/account identifiers and submitted SMS codes are redacted. Generic `10000` outcomes remain unconfirmed and are not retried automatically; displaying a reason does not imply a confirmed rejection or fill.

US Forthright quantities allow four decimal places; HK quantities must be whole board-lot multiples, with trailing zeroes omitted. The stock ticket requests account totals in the order currency instead of relabelling USD totals as HKD. HK order queries normalize chart tickers such as `03690` to `03690.HK` before filtering.


## Currency presentation and authorization observations

The connection form offers USD and HKD as asset display currencies. Securities `fund_total.to_ccy` converts aggregate presentation only; it is not an FX instruction or proof of cash in that currency. `fund_detail` supplies per-currency available, settled and withdrawable cash, which are shown separately, including negative balances. Positions and order prices retain their instrument currency; zero-quantity rows are excluded from current positions and unfilled orders have no displayed average fill price.

Successful manual authorization observations are saved inside the tenant-scoped encrypted credential, with a timestamp, and restored on page reload. This is the last observed verification, not a claim that broker authorization can never expire. Reloading does not call unlock or send SMS. Broker requests remain authoritative.

A unified USD reporting layer remains separate from execution: US stock budgets use USD and HK stock budgets use HKD. Forthright handles conversion according to its account rules, while QuantDinger uses total buying power and does not send FX instructions. Never infer an executed FX transaction from a converted aggregate balance. Futures strategy deployment is disabled; its reserved metadata model requires verified settlement currency and point value.

### Encoding investigation on 2026-11-26

Historical futures order diagnostics read top-level `status` and `info` through `response.json()`, not retained HTTP bytes. The limit-test fields were `status=10000` and `info=service response failed: status=1 info=\uFFFD۸\uFFFD\uFFFD\uFFFD\uFFFD`; the market-test suffix contained ten `U+FFFD` replacement characters.

In an offline comparison, the GBK/GB18030 bytes for `价格错误` (price error), `bc db b8 f1 b4 ed ce f3`, produce exactly `\uFFFD۸\uFFFD\uFFFD\uFFFD\uFFFD` when incorrectly decoded as UTF-8 with replacement. This is a matching hypothesis, not a recovery from the historical wire response: replacement decoding is many-to-one. Strict reverse encoding of the retained replacements fails for GBK, Big5 and Latin-1; GB18030 reverse encoding does not recover the original UTF-8 text either. Do not treat this candidate as a confirmed broker rejection or use it to retry an order.

Read-only futures checks of `fund_total`, `position`, `working_order` and `history_order` returned HTTP 200, business status 0 and `info=success`, declaring `application/json; charset=utf-8`. These ASCII responses parse under UTF-8, GBK, GB18030, Big5 and Latin-1, but not UTF-16LE. Successful ASCII responses do not establish the encoding of the historical failed order response. No new orders or unlock requests were submitted during this investigation.

## Futures chart and ticket verification (2026-11-26)

The chart path supplies exact Yahoo Finance reference symbols for recognized CME, CBOT, COMEX and NYMEX contracts (for example `MESZ26.CME` and `ZCU27.CBT`). Explicit deployment mappings take precedence. Yahoo contract identity/type metadata is checked before rendering; continuous futures are never substituted for a dated contract. Unmapped roots, including HKFE, still require a verified provider mapping. This is delayed/reference data, not an exchange execution quote. Strategy deployment continues to require explicit contract currency, point value and data-source configuration.

Minute requests now use timezone-aware UTC boundaries. Three-minute and multi-hour bars are resampled from the appropriate intraday source; unsupported intervals no longer silently request daily candles. The bounded lookback spans market closures, subject to provider retention. MESZ26 daily, minute, three-minute and four-hour candles and MNQZ26 daily candles were retrieved through the local API. Both UI builds and the indicator preview were verified. NumPy reductions are initialized in trusted code to avoid cold-start private-import failures; private-module imports and file/network restrictions remain blocked.

Both clients use integer lots, explicit contract expiry and an exchange point-price increment. MES/MNQ show 0.25 index points, based on CME contract specifications. The broker contract field `tickSize: "25"` is retained as raw metadata and is not used to multiply order prices by 100. The user-submitted limit order returned `10000`, with `service response failed: status=1 info=价格过低`. This readable reason does not turn an unconfirmed generic gateway outcome into a confirmed rejection. No order was submitted by the agent in this verification pass.

Authenticated contract-list reads register only public, unexpired contract identities in the existing asset catalog. Account credentials and account permissions are not copied into that shared catalog. Desktop and mobile futures search refresh the list from a saved futures account, then use the regular asset-ID watchlist flow. Selecting another contract clears the previous limit price; mobile chart context changes also discard the previous instrument's candles.

## Expanded chart coverage and account settings

Default exact-expiry chart routes now cover recognized CME, CBOT, COMEX and NYMEX products. Each Yahoo response must identify the requested dated symbol and `FUTURE` instrument type. Missing provider data is not replaced by a continuous contract or another interval. Explicit strategy contract specifications remain required. Read-only checks returned minute bars for ZCU27, ZCZ26, YMZ26, MYMZ26, GCG27, MGCZ26, HGF27, CLF27, MCLF27, NGF27, ZSH27, ZWH27, ZMH27, ZLZ26, 6EZ26 and M2KZ26. This is sampled coverage, not a guarantee for every expiry or exchange. Agricultural quotes retain provider units (including US cents); prices are not rescaled.

Trading tickets no longer show duplicate feed explanations or authorization cards. Authorization lives in the desktop connection tab and mobile API credential detail page. The chart still reports unavailable data. No orders are submitted during these checks.

## Futures ticket acceptance (2026-11-26, current pass)

Desktop light/dark tickets were inspected in the in-app browser. Account/contract controls, expiry/tick metadata, order controls and compact positions now share one layout. Minimize/restore works for futures. Mobile light/dark themes were inspected, including a 390 x 844 viewport with no horizontal document overflow. Contract search, ZCU27-to-MESZ26 chart changes, invalid 0.25-tick prices, limit confirmation and cancellation were exercised. Account details returned positions, open orders and filled history without errors after Forthright reads were serialized; the earlier failure was HTTP 429 during concurrent reconnects. Futures no longer start the desktop crypto account/price pollers.

The account-level close action on desktop and mobile submits an opposite market order for the observed signed net lots. The backend re-reads working orders and positions, rejects changed quantities, and serializes close requests for the broker account/gateway. This is not a native reduce-only order: external trading can still change a net position between the query and submission. Unknown outcomes are not retried. Futures quotation prices are displayed numerically without a guessed account-currency prefix; cash values retain their actual currency.

Live account checks found no positions or working orders. The current deployment has no `FORTHRIGHT_FUTURES_CONTRACT_SPECS`; MES strategy preflight correctly fails with `contractMetadataRequired`. Do not interpret successful chart rendering as strategy readiness. Configure verified exact-contract data mapping, point value and matching currency before strategy acceptance. Order creation, actual futures fills and closing back to flat remain pending human final submission. No strategy was started during this pass.

Verification: desktop 790 and mobile 202 unit tests passed; both production builds and the 11-locale encoding/completeness audit passed. The broader backend run passed 192 tests; two existing Alpaca ownership execution tests failed on `equity_market_closed` because they depend on the current trading session. All Forthright cases in that run passed. No live-session restriction was disabled to make tests pass.

## Preset futures catalog and ticket balances

The bundled `app/data/forthright_futures_catalog.json` is a public identity snapshot from Forthright `trade_contracts`, captured on 2026-11-26: 43 instrument families and 364 dated contracts. It contains no account data or credentials. The normal futures symbol sync seeds these identities; authenticated contract reads refresh them. Update this snapshot from the broker catalog periodically as new expiries are listed. Expired contracts and contracts past their last trading date are excluded from search and new watchlist additions. The snapshot is not a statement of trading permission, quote availability or strategy readiness.

`GET /api/market/futures/products` returns preset families. Futures asset search permits an empty keyword for browsing; a family keyword resolves against its broker instrument code (for example AUD maps to 6A contracts), and results sort by expiry. Both clients select catalog-backed asset IDs. Mobile chart search now saves the selected asset before changing the chart, matching its Search/Add label.

Futures tickets show each reported cash balance with its original currency. The current fund summary schema does not supply buying power or available margin, so those values remain unavailable instead of being inferred from cash. Stock tickets filter account records to the chart symbol, localize submitted order states and preserve missing financial values as unavailable. One user-submitted MES futures order was observed as submitted with zero fills; no position was available to close during this pass.

## Current support boundary and funds correction (2026-11-26)

Automated futures trading is disabled. Desktop/mobile live selectors, deployment saves, persisted-deployment starts and the strategy client factory reject futures. Manual trading, charts, watchlists and research/backtests remain available. Strategy specifications above describe reserved implementation, not enabled support.

Account summaries now query /future/trade/fund_detail: baseCcy denominates avFund (available funds), cashBal, netEquity, imargin and mmargin. fund_total remains the original-currency cash breakdown. Cash is not available funds or occupied margin. The earlier assertion that the API did not expose these values is corrected. Official schema: https://dev-openapi-doc.techfx88.com/zh/future/fund/fund_detail.html .

Desktop tickets/account details and mobile account details refresh sequentially every 15 seconds while visible and idle, resume on visibility restoration and stop on unmount. Mobile tickets also refresh funds. Failed reads retain the last snapshot; order/close validation still uses fresh broker positions.

The nearest-expiry contract for each of 43 preset products was checked for one-minute candles: 36 returned data (QC returned 17 bars; the others up to 50). HHI, HSI, HTI, MCH, MHI, CUS and MCA have no verified dated-contract source. The broker trading documentation has no candle endpoint. Continuous contracts are never substituted; this sample does not certify every far-dated contract.

The existing one-lot MESZ26 buy remained raw status=1 (queued), tradedQty=0; history_order, done_trade and position were empty. Fill/close acceptance remains pending. No duplicate order was submitted.

## Securities reconciliation audit — 2026-11-26

- Securities fills now use `history_match` plus `today_match`, rather than only fully filled orders. Executions from partially filled/cancelled orders remain visible. Identity includes trading date, market, order ID and execution serial number; repeated rows are deduplicated. A failed execution query is not silently replaced by an incomplete filled-order list. The history window is currently 90 days, not lifetime history.
- Order creation/update times use `curr_date/curr_time` and `last_curr_date/last_curr_time`. Fill time uses the occurrence date and `business_time`; trading date remains part of the identity and may differ from the calendar date. These broker-local fields contain no documented UTC offset; no offset is invented. Local UI time rendering still needs authoritative broker timezone confirmation for users outside the broker timezone.
- `fare_amt` is the cumulative **total order fee**, not just commission. Terminal filled or partially filled/cancelled orders expose it once in instrument currency; missing fees and nonterminal estimates remain pending. The match response has no execution-level fee. The fills API joins terminal order fees by order identity and allocates them by execution notional only when symbol, side, currency, total quantity and notional agree with the complete deduplicated execution set. Allocation happens before pagination; rounding remainder is retained so allocations sum to the exact order fee. `fee_source=order_allocation` identifies this allocation, rather than a broker-reported execution charge. Missing/incomplete evidence or a failed fee lookup leaves fees pending without hiding fills. Desktop/mobile show native fee currency. Strategy reconciliation also revisits partially filled/cancelled orders with pending fees and updates both the trading ledger and matching strategy callback fill totals without adding quantity or charging twice. Deploy the backend and trading worker together, followed by desktop/mobile clients. Futures fee behavior is unchanged.
- Position `enable_qty` is carried as sellable quantity, including zero. Desktop sell sizing now honors it; mobile already consumes this field. Desktop record normalization also distinguishes execution shares from monetary turnover and accepts both normalized quantity names.

### Test-gateway evidence

Two existing Tencent executions matched their orders exactly: buy 100 at HKD 345.40 with HKD 64.38 total fees; sell 100 at HKD 343.20 with HKD 64.36 total fees. Gross trading loss was HKD 220 and combined fees HKD 128.74. This reconciles those orders, not the account's lifetime P&L.

Fresh AAPL 1-share and Tencent 100-share low-price limit orders were accepted, then confirmed cancelled with zero fills. Final securities positions and working orders were empty; HKD equity, buying power and available cash remained 7,998,863.93. Native USD available cash was zero, while the broker's USD-converted buying power was approximately 1,015,948.11. Converted buying power must not be relabelled as native USD cash.

Validation: 304 backend tests (Forthright, existing brokers, strategy execution, ownership, fill accounting, fee conversion and authorization), 806 desktop tests and 231 mobile tests passed. Both frontend production builds passed. Existing dependency/chunk-size warnings remain. The built-in browser control connection failed twice, so this audit does not certify visual/click-through acceptance. The local HTTP API was refreshed; active trading workers were not restarted. Deploy the adapter with workers through the normal controlled rollout before relying on worker changes.

Limits: new orders exercised submission/cancellation, not a fresh fill round trip; actual fill/fee evidence came from existing test-account trades. US execution fees, production-gateway behavior, per-security HK board-lot/odd-lot acceptance, and futures fill/close remain outside this acceptance. Automated futures strategies remain disabled. These checks do not guarantee execution or eliminate broker/network risk.

Primary contracts: [historical executions](https://dev-openapi-doc.techfx88.com/zh/trade/match/history_match.html), [today's executions](https://dev-openapi-doc.techfx88.com/zh/trade/match/today_match.html), [order history](https://dev-openapi-doc.techfx88.com/zh/trade/order/history_order.html), [positions](https://dev-openapi-doc.techfx88.com/zh/trade/fund/position.html), [fund totals](https://dev-openapi-doc.techfx88.com/zh/trade/fund/fund_total.html).

## Confirmed HK board-lot policy — 2026-11-26

The user confirmed with Forthright that its HK route accepts whole board lots only, with no odd-lot trading. The earlier integer-share-only validation is superseded. US quantity precision is unchanged; this change does not assert that every US security supports fractional execution.

`app/services/market/hk_board_lots.py` reads the [official HKEX securities list](https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx). It accepts HKD equity, exchange-traded products and REIT entries with a positive integer Board Lot. Metadata is cached per process for one hour, refreshed on a Hong Kong date change, and rejected if the publication is over seven days old or in the future. A failed refresh or unknown security blocks orders; expired rules and a guessed one-share lot are never used. Read-only checks from the local API container returned 100 shares for Tencent and 500 for CK Hutchison; the host also verified Meituan at 100 shares.

`GET /api/forthright/instrument-rules?credential_id=...&symbol=00700.HK` provides the selected account's rule to desktop/mobile. Account and symbol changes invalidate displayed rules. The backend independently checks the lot before submission, so a client-supplied quantity cannot bypass it. Other brokers' sizing remains unchanged.

- Manual quantities must be exact lot multiples; invalid input is rejected rather than silently changed.
- Amount-based orders and strategy quantities use `floor(budget / price / lot_size) * lot_size`. A HKD 1,000 budget at HKD 90 with 100 shares per lot is insufficient; HKD 10,000 buys 100 shares, with HKD 9,000 notional. Fees are additional; market-order estimates are not a guaranteed execution price or spend cap.
- Strategy exits reapply lot rounding after ownership, broker inventory and sellable-quantity caps. A 137-share allocation can sell at most 100 shares; the 37-share residual remains recorded. Below-one-lot requests fail with a localized reason, never round up or mark inventory closed without fills.

Validation: 264 backend tests, 808 desktop tests and 233 mobile tests passed; both frontend builds passed, and desktop source lint had zero errors. Eleven-locale additions passed completeness/encoding checks. The local API was refreshed and verified healthy. No new orders were sent. Browser control remained unavailable, so visual acceptance was not repeated. Existing long-running trading workers were not restarted; their controlled source/image rollout is still required for worker-side changes to take effect.

## Shared HK lot inputs and durable instrument catalog — 2026-11-26

This section supersedes the earlier process-cache description and manual amount/share inputs for HK stocks. At the user's request, Forthright and Tiger HK tickets accept **whole lots** on desktop and mobile. Each ticket displays the security-specific shares per lot and the resulting share quantity. Two lots mean 200 shares for Tencent, 1,000 for CK Hutchison and 2,000 for Hong Kong and China Gas. The broker request still sends shares. Positions, fills and historical orders retain their actual share quantities so partial fills and residual shares remain visible. Webull remains US-only; US stock and crypto order units are unchanged.

HKEX catalog synchronization persists `board_lot`, `rules_as_of` and `rules_source` on the existing `qd_market_symbols` table. Manual and scheduled market-catalog sync refresh the data; a trade reads PostgreSQL without downloading HKEX's file. Missing, future-dated or older-than-seven-day rules block the order with a localized instruction to synchronize the catalog. The additive migration is `20261126_instrument_board_lots.sql`. The authenticated `/api/{broker}/instrument-rules` endpoint accepts Forthright and Tiger, with account ownership checked before rule lookup. Both broker adapters validate whole-lot quantities again before submission.

Strategy budget sizing continues to floor to whole board lots, including after exit ownership/inventory caps. Below-one-lot budgets do not submit orders. The Tiger HK strategy path now shares this sizing policy; Forthright's existing policy is preserved. Futures strategies remain disabled.

Settings → Market Data now contains a searchable, paginated HK/futures instrument catalog. Futures reuse `qd_market_symbols.product_meta` for root, exchange, expiry, last trade date, quantity step and verified quote/tick metadata. No second instrument table or account permission cache is introduced. The read-only `/api/settings/instrument-catalog` endpoint requires an administrator. Unknown contract specifications remain blank rather than being guessed.

Local synchronization imported 3,284 HK security specifications and 377 futures records (13 roots and 364 dated contracts); the dated-contract administration view lists 364 contracts. Read-only database checks confirmed 00700=100, 00001=500 and 00003=1,000 shares per lot. Existing HK catalog rows absent from this source remain present but cannot trade without fresh rules. Focused validation passed 223 backend tests, 809 desktop tests and 234 mobile tests, plus both production builds and locale checks. No orders were submitted for this change. Browser automation was unavailable, so visual/click-through acceptance remains unverified. The local API has the updated code and migration; long-running trading workers were not restarted and still require controlled rollout for worker-side changes.

## Credential security review — 2026-11-26

Saved broker credentials are Fernet ciphertext in `qd_exchange_credentials.encrypted_config`. Runtime decryption uses the dedicated `CREDENTIAL_ENCRYPTION_KEY`, with the legacy session-key fallback retained. Do not rotate/delete an encryption key without a tested migration and backup recovery plan. The local review found 13 encrypted/decryptable credential rows and a separately configured encryption key of at least 32 bytes. These facts describe the local environment, not an uninspected production deployment.

Credential lists and broker account/status responses are scoped to the authenticated user and expose metadata rather than private keys. Response boundaries now mask sensitive nested provider fields, including private keys, trade passwords, app secrets, access tokens, SMS codes and ciphertext, and send `Cache-Control: no-store`. Unexpected credential/connection errors no longer echo raw exception text or log it. Structured strategy redaction now recognizes these broker-specific aliases. Tiger/Webull configuration representations exclude their secret fields.

Desktop forms clear sensitive inputs after emitting a copied connection payload and when deactivated/destroyed. Mobile clears the saved form on successful submission and unmount, and no longer logs full Axios save errors containing request data. Desktop request debugging no longer prints stored authentication token values. This reduces retained application state; it cannot erase browser developer-tool network history, extension access or JavaScript engine memory. The user's own initial input and submission remain visible to that browser. Production must use HTTPS and protect the host, encryption key and database backups.

Validation passed 175 focused backend tests, six frontend behavior tests and both frontend builds. Four local authenticated read endpoints returned no matching stored secrets and all sent no-store. No matching complete stored secrets were found in the newest 8 MiB of each of eight current log files. A pattern scan of desktop/mobile source and built assets found no embedded PEM private-key blocks; this is not proof against every secret format or encoding. No broker orders were submitted. Local API changes are loaded; long-running workers and production have not been rolled out. The local JWT signing secret is below the recommended 32 bytes; it was not rotated because doing so invalidates sessions. Replace it through controlled deployment, keeping the dedicated credential key unchanged.
