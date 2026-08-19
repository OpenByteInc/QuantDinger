# Strategy API V2 Development Guide

> Applies to: the current executable QuantDinger strategy contract
> Audience: first-time strategy authors, indicator-conversion users, and developers targeting both backtest and live execution

QuantDinger has one current executable Python strategy contract: **Strategy API V2**. The same source compiles into a strategy manifest used by backtest and live runtimes for instruments, subscriptions, events, order intents, portfolio accounting, and protection rules.

The source owns its market, instruments, timeframe, schedules, and trading logic. Single-timeframe is the default; a source declares several timeframes only when its logic explicitly requires cross-timeframe confirmation. Run forms provide dates, initial capital, costs, source-permitted leverage, and user parameters; they do not override source-controlled markets, symbols, or timeframes.

Chart indicators are separate artifacts. Their plots, signals, and layers cannot place orders. Convert an indicator into Strategy API V2 before backtesting or deploying it.

---

## 1. Quick start: a minimal executable strategy

~~~python
"""SPY 20-Day Moving Average
Trades a long-only SPY regime from completed daily bars.
"""

# @param period int 20 Moving-average period range=5:100:5
# @param target_pct float 0.95 Target portfolio weight range=0.1:1.0:0.05


def initialize(context):
    g.symbol = "USStock:SPY"
    context.set_universe([g.symbol])
    context.subscribe(
        frequency="1d",
        fields=["open", "high", "low", "close", "volume"],
    )
    context.set_warmup(120)
    context.set_benchmark("USStock:SPY")


def handle_data(context, data):
    period = int(context.params.get("period", 20))
    target_pct = float(context.params.get("target_pct", 0.95))

    bars = get_history(
        period + 1,
        "1d",
        "close",
        g.symbol,
    )
    if len(bars) < period:
        return

    price = float(bars["close"].iloc[-1])
    average = float(bars["close"].tail(period).mean())
    position = get_position(g.symbol)
    desired = target_pct if price > average else 0.0

    if desired > 0 and position.amount <= 0:
        order_target_percent(
            g.symbol,
            desired,
            reason="ma_long_entry",
            stop_loss_pct=0.05,
        )
    elif desired == 0 and position.amount > 0:
        order_target_percent(
            g.symbol,
            0.0,
            reason="ma_long_exit",
        )
~~~

Workflow:

1. Create a script in the Strategy IDE and paste the source.
2. Save the source.
3. Verify it and inspect the compiled manifest.
4. Choose dates, capital, commission, slippage, and parameters.
5. Inspect executions, closed trades, the order ledger, equity, and holdings.
6. Create a deployment only after the backtest behaves as intended. New deployments start stopped.

---

## 2. Compiler requirements and authoring standard

Hard compiler requirements:

- Source is non-empty and executes in the safe sandbox.
- <code>initialize(context)</code> exists.
- <code>initialize</code> declares a static universe, index, or named pool through <code>context.set_universe(...)</code>.
- If no subscription is declared, the compiler adds a default daily subscription; this guide still recommends an explicit <code>context.subscribe</code>.
- The source exposes <code>handle_data</code>, <code>on_rebalance</code>, or at least one registered schedule callback.
- Leveraged strategies satisfy the Crypto-swap-only policy.

The project authoring standard additionally requires:

- Start with a triple-quoted docstring. Its first line is the strategy name; following lines describe universe, signals, schedule, and risk.
- Use English identifiers and source comments.
- Use stable, auditable parameter and reason names.
- Avoid look-ahead, implicit reversals, unbounded scaling, and uncapped exposure.

<code>initialize</code> runs during compilation/manifest discovery. Use it for declarations and initial <code>g</code> state. Do not request market data, inspect real positions, or place orders there.

Important compiler-facing API rules:

- <code>context.params</code> is not available inside <code>initialize</code>; read it from handlers or scheduled callbacks.
- <code>get_history</code> is count-first and uses <code>field</code>: <code>get_history(count, frequency, field, symbol)</code>. Do not pass a <code>fields=</code> keyword to it.
- <code>data.history</code> is a separate API: <code>data.history(symbols, count, fields)</code>.
- Single-instrument history returns a DataFrame; multi-instrument history returns a dictionary of DataFrames.
- <code>get_position</code> returns a Position object, not a dictionary.
- A pandas DataFrame or Series cannot be used directly as a Boolean condition. Use <code>len(...)</code>, <code>.empty</code>, <code>.any()</code>, or <code>.all()</code>.
- Undefined platform APIs and unsupported arguments are rejected during verification instead of failing later in a live session.

---

## 3. The source-owned manifest

Compilation discovers:

- API version and source hash;
- CTA or portfolio classification;
- static or dynamic universe;
- subscribed instruments, all timeframes, the driving timeframe, and fields;
- schedules;
- benchmark;
- lifecycle handlers;
- factor and fundamental dependencies;
- warm-up bars;
- leverage permission and maximum;
- custom metadata.

Verification endpoint:

~~~http
POST /api/strategies/verify
Content-Type: application/json

{"code": "...complete Strategy API V2 source..."}
~~~

A valid response contains <code>valid: true</code> and the manifest. Verify the final saved source before deployment, not only an earlier draft.

---

## 4. Canonical instruments

| Market | Example |
| --- | --- |
| China A-share | <code>CNStock:600519.SH</code> |
| US equity | <code>USStock:MSFT</code> |
| Hong Kong equity | <code>HKStock:00700.HK</code> |
| Crypto spot | <code>Crypto:BTC/USDT@spot</code> |
| Venue-specific Crypto spot | <code>Crypto:BTC/USDT@okx:spot</code> |
| Crypto perpetual | <code>Crypto:BTC/USDT@swap</code> |
| Venue-specific perpetual | <code>Crypto:BTC/USDT@okx:swap</code> |
| Forex | <code>Forex:EUR/USD</code> |
| Futures | <code>Futures:ES</code> |
| MOEX | <code>MOEX:SBER</code> |

The parser also normalizes selected aliases, such as <code>600519.XSHG</code> to <code>CNStock:600519.SH</code> and <code>BTCUSDT</code> to <code>BTC/USDT</code>.

Production strategies should use the full market prefix. Crypto defaults to spot when no market type is present. Only swap instruments can permit contract leverage. Parsing a market name does not by itself guarantee data coverage or live-trading support; see the live venue matrix in Section 18.

---

## 5. Static and dynamic universes

Static single instrument:

~~~python
context.set_universe(["USStock:SPY"])
~~~

Static basket:

~~~python
context.set_universe([
    "USStock:AAPL",
    "USStock:MSFT",
    "USStock:NVDA",
])
~~~

Index universe:

~~~python
context.set_universe(index="INDEX:SP500")
members = get_index_stocks("INDEX:SP500")
~~~

Named platform pool:

~~~python
context.set_universe(pool="sp500")
members = get_universe_stocks()
~~~

Dynamic universes resolve point-in-time constituents. Do not copy today's pool members into source and then use them for a historical backtest.

A dynamic universe, more than one static instrument, or <code>on_rebalance</code> normally classifies the manifest as portfolio. One static instrument normally classifies it as CTA.

---

## 6. Subscriptions, warm-up, and benchmark

~~~python
context.subscribe(
    frequency="1d",
    fields=["open", "high", "low", "close", "volume"],
)
context.set_warmup(260)
context.set_benchmark("USStock:SPY")
~~~

Rules:

- Frequency belongs in source, for example <code>1m</code>, <code>5m</code>, <code>1h</code>, <code>4h</code>, <code>1d</code>, or <code>1w</code>.
- Aliases such as <code>daily</code>, <code>day</code>, and <code>d</code> normalize to <code>1d</code>.
- Omitting symbols subscribes the current universe.
- <code>set_warmup</code> asks the data service for history before the requested backtest start. It does not remove the need for <code>len(bars)</code> guards.
- A benchmark is for comparison; it is not traded automatically.
- A source may declare at most eight distinct timeframes. Native timeframes are <code>1m</code>, <code>3m</code>, <code>5m</code>, <code>15m</code>, <code>30m</code>, <code>1h</code>, <code>4h</code>, <code>1d</code>, and <code>1w</code>.
- Five-, fifteen-, and thirty-minute bars can be combined with hourly, daily, and weekly bars in one source. Use lowercase <code>1w</code> for weekly data. Monthly bars are not currently a native Strategy API V2 timeframe.

### 6.1 Native multi-timeframe data

Multi-timeframe data is an optional capability, not a mode that every strategy or robot must use. A normal strategy declares and reads one timeframe by default; subscriptions are added only when the user or existing source explicitly requires cross-timeframe validation. Grid, DCA, and other robots that do not depend on cross-timeframe signals do not gain extra periods merely because the capability exists.

A strategy may subscribe to and read several independently loaded timeframes. The runtime does not build higher bars ad hoc from the driving frame. This example treats a completed one-minute golden cross as the entry event and confirms it against the bullish alignment of completed hourly bars:

~~~python
def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1m")
    context.subscribe(frequency="1h")
    context.set_warmup(62)


def handle_data(context, data):
    bars_1m = get_history(32, "1m", "close", g.symbol)
    bars_1h = get_history(52, "1h", "close", g.symbol)
    if len(bars_1m) < 31 or len(bars_1h) < 50:
        return

    close_1m = bars_1m["close"]
    fast_now = float(close_1m.tail(10).mean())
    fast_prev = float(close_1m.iloc[:-1].tail(10).mean())
    slow_now = float(close_1m.tail(30).mean())
    slow_prev = float(close_1m.iloc[:-1].tail(30).mean())
    golden_cross = fast_prev <= slow_prev and fast_now > slow_now
    death_cross = fast_prev >= slow_prev and fast_now < slow_now
    hourly_bullish = float(bars_1h["close"].tail(20).mean()) > float(
        bars_1h["close"].tail(50).mean()
    )
    amount = float(get_position(g.symbol).amount or 0.0)
    if amount <= 0 and golden_cross and hourly_bullish:
        order_target_percent(g.symbol, 0.95, reason="one_minute_cross_hourly_confirmed")
    elif amount > 0 and (death_cross or not hourly_bullish):
        order_target_percent(g.symbol, 0.0, reason="cross_or_hourly_filter_exit")
~~~

Multi-timeframe runtime rules:

- The fastest subscribed timeframe becomes <code>drivingFrequency</code>. The example runs <code>handle_data</code> once per completed one-minute bar; declaration order does not change the driving timeframe.
- <code>get_history(..., frequency, ...)</code> routes to that timeframe's independent frame. Requesting an unsubscribed timeframe raises <code>strategyV2.frequencyNotSubscribed</code> instead of silently falling back.
- A higher-timeframe bar becomes visible only when its close is no later than the driving bar's close. For example, a four-hour bar opened at 08:00 cannot affect a signal until 12:00.
- <code>set_warmup(n)</code> requests an appropriate lookback for every subscribed timeframe. The source must still guard the length of every returned DataFrame.
- The requested backtest window must satisfy every timeframe's provider limit. An instrument missing any required timeframe is not run with a partial bundle.
- Provider history limits still apply independently. Intraday feeds often retain less history than daily or weekly feeds, so a <code>5m + 1w</code> design may need a shorter backtest range even though both subscriptions are valid. Crypto venues, stock feeds, and forex feeds may expose different historical depths.
- Manifest <code>primaryFrequency</code> remains the first declaration for compatibility. Scheduling, execution, and performance annualization use <code>drivingFrequency</code>.
- <code>data.history(..., frequency="4h")</code>, <code>data.current(..., frequency="4h")</code>, <code>indicator(..., frequency="4h")</code>, and <code>factor(..., frequency="4h")</code> also accept an explicit timeframe. <code>data[symbol]</code> uses the driving timeframe.

When cross-timeframe confirmation is explicitly requested, a design can filter trend on the higher timeframe, confirm entries on the driving timeframe, and keep orders idempotent. Because <code>handle_data</code> runs on the fastest timeframe, a persistent higher-timeframe bullish state must not become an unconditional scale-in on every lower-timeframe bar. “Higher timeframe is bullish” and “higher timeframe has just crossed bullish” are different conditions; strategy code and AI generation must preserve the user's intended meaning.

AI-generated strategy source follows the same contract: when a request names one timeframe, it must remain single-timeframe and the AI must not invent confirmation periods. When a request names several timeframes, generation and repair must preserve every requested subscription rather than selecting one, resampling the lowest frame, or rewriting all history reads to the driver.

---

## 7. Lifecycle and schedules

Supported handlers:

~~~python
def initialize(context):
    pass

def before_trading_start(context, data):
    pass

def handle_data(context, data):
    pass

def on_rebalance(context, panel):
    pass

def after_trading_end(context, data):
    pass
~~~

Schedule registration:

~~~python
def initialize(context):
    context.set_universe(["USStock:SPY"])
    context.subscribe(frequency="5m")
    run_daily(rebalance, time="09:35")
    run_weekly(weekly_review, weekday=1, time="09:40")
    run_monthly(monthly_rebalance, monthday=1, time="09:45")
~~~

Rules:

- <code>weekday</code> is 1–7, with Monday as 1.
- A monthday past the end of a month resolves to that month's last day.
- On daily or lower-frequency bars, a specific intraday time does not create a nonexistent bar.
- Prefer <code>callback(context, data)</code>; the runtime also adapts callbacks that accept only context.
- A portfolio strategy with no registered schedules invokes <code>on_rebalance</code>.
- Backtests invoke <code>before_trading_start</code> and <code>after_trading_end</code> on every event timestamp. Live sessions invoke <code>before_trading_start</code> only when a newly processed bar enters a new calendar date; the current live runtime does not invoke <code>after_trading_end</code>. Put live-critical close logic in <code>handle_data</code> or a schedule.

Schedule time is interpreted in the live user's configured timezone. If no user timezone is available, the server <code>TZ</code> setting is used, then UTC. Set the user's timezone explicitly and test schedules against exchange sessions. Backtest timestamps follow the supplied market-data clock, so verify timezone alignment before relying on an exact intraday time.

---

## 8. The critical timing model

Backtests expose only point-in-time-visible data:

1. At a new bar, orders queued after the previous close execute first, using the current open.
2. <code>before_trading_start</code> and due schedule callbacks see data only through the previous bar; their orders can be processed at the current open.
3. The current completed bar becomes visible and <code>handle_data</code> runs.
4. Orders emitted by <code>handle_data</code> wait for the next bar open.
5. <code>after_trading_end</code> also sees the current bar; its new orders wait for the next bar.

This implements “confirm on close, fill at next open” without future leakage. Never use negative shifts or future rows to move execution earlier.

Live sessions process each closed bar once and preserve <code>g</code> state while the session remains alive. Receiving the same bar twice should not duplicate strategy work. Cross-restart state is opt-in as described in Section 9.

### Closed bars versus real-time prices

- The final row exposed by <code>get_history</code>, <code>data.current</code>, <code>data.history</code>, and indicator functions must be a completed bar. A real-time trade or mark price must not rewrite, overwrite, or extend that bar's OHLC.
- Moving averages, breakouts, patterns, factors, and all other entry or scale-in signals must use completed bars so backtests, live runs, and restart replay share the same semantics.
- Real-time prices are reserved for stop loss, take profit, trailing protection, and equity risk. They must not make an unfinished candle look complete or revise a confirmed strategy signal.
- Multi-timeframe strategies apply the completed-bar rule separately to every timeframe. A lower-timeframe advance never exposes a still-forming four-hour or daily bar.
- A future tick-driven strategy product needs a separate API, replay model, and contract. Do not simulate it inside ordinary Strategy API V2 source.

---

## 9. context, data, and g

Common context fields:

| Field | Meaning |
| --- | --- |
| <code>context.params</code> | run parameters |
| <code>context.current_dt</code> | current event timestamp |
| <code>context.previous_trading_date</code> | previous event timestamp |
| <code>context.portfolio.starting_cash</code> | initial capital |
| <code>context.portfolio.available_cash</code> | available cash |
| <code>context.portfolio.total_value</code> | current equity |
| <code>context.portfolio.positions</code> | current position map |
| <code>context.data</code> | data view |

Use <code>data.current(symbol, field, frequency="1h")</code> for a current visible value, <code>data.history(symbols, count, fields, frequency="4h")</code> for a selected timeframe, and <code>data[symbol]</code> for the driving timeframe's current visible DataFrame.

Persist state across callbacks on <code>g</code>:

~~~python
def initialize(context):
    g.last_signal = ""
    g.rebalance_count = 0
~~~

Do not store strategy state in files, databases, or external module services. <code>g</code> is the per-run user state namespace.

### State across restarts

By default, <code>g</code> survives callbacks in the current process but is rebuilt by <code>initialize</code> after a session restart. Opt into runtime-state snapshots when the strategy cannot reconstruct its cycle from positions and order status:

~~~python
PERSIST_RUNTIME_STATE = True
~~~

The equivalent deployment parameter is <code>persist_runtime_state=true</code>. When enabled, the runtime snapshots supported <code>g</code> values, the last processed bar, schedule clock, client-order statuses, and last exit reasons. Protection-engine state is restored independently. Keep persisted values JSON-like and still reconcile them with real account positions after restart; a snapshot is not an exchange ledger.

---

## 10. Parameters

~~~python
# @param fast_period int 20 Fast moving-average period range=2:100:1
# @param slow_period int 50 Slow moving-average period range=3:250:1
# @param target_pct float 0.95 Target weight values=0.5,0.75,0.95
# @param enabled bool true Enable entries
~~~

Read values through context:

~~~python
fast_period = int(context.params.get("fast_period", 20))
slow_period = int(context.params.get("slow_period", 50))
target_pct = float(context.params.get("target_pct", 0.95))
enabled = bool(context.params.get("enabled", True))
~~~

Declared defaults and code fallbacks must agree. The parameter panel supplies <code>context.params</code>; the fallback remains the final default when a value is absent.

Symbols, market, timeframe, and leverage permission are source contract fields. Do not disguise them as ordinary run-form overrides.

---

## 11. History, factors, and fundamentals

Single-instrument history:

~~~python
bars = get_history(
    60,
    "1d",
    ["open", "high", "low", "close", "volume"],
    "USStock:SPY",
)
~~~

One instrument returns a DataFrame. Multiple instruments return a dict of canonical instrument keys to DataFrames:

~~~python
frames = data.history(
    ["USStock:AAPL", "USStock:MSFT"],
    count=30,
    fields=["close", "volume"],
)
~~~

Technical indicators and factors:

~~~python
rsi_value = factor("rsi", g.symbol, period=14)
macd = indicator("MACD", g.symbol, fastperiod=12, slowperiod=26, signalperiod=9)
scores = get_factors(symbols, ["momentum_20", "volatility_20"])
~~~

Fundamentals:

~~~python
fundamentals = get_fundamentals(
    ["PE", "PB", "ROE", "MARKET_CAP"],
    symbols,
)
~~~

Other public aliases include <code>REVENUE_GROWTH</code>, <code>DEBT_TO_EQUITY</code>, and <code>FREE_CASH_FLOW</code>. Use only real point-in-time fields supported by the platform; do not invent fields or read future reports.

Pass a symbol to <code>factor</code>/<code>indicator</code> in a multi-asset strategy. The symbol may be omitted only when the data portal has exactly one instrument.

---

## 12. Positions and order APIs

Read positions:

~~~python
position = get_position(g.symbol)
all_positions = get_positions()
~~~

Common Position fields:

- <code>symbol</code>
- <code>amount</code>
- <code>avg_cost</code>
- <code>last_price</code>
- <code>market_value</code>
- <code>position_side</code>

In a hedge-mode swap strategy, read each leg explicitly:

~~~python
long_position = get_position(g.symbol, position_side="long")
short_position = get_position(g.symbol, position_side="short")
~~~

Do not treat <code>get_position(symbol)</code> as a synthetic net position in hedge mode. <code>get_positions()</code> may contain leg-aware keys such as <code>symbol::long</code> and <code>symbol::short</code>. Use <code>abs(position.amount)</code> when checking whether a leg is open.

Do not confuse these definitions from different layers:

| Name | Layer | Meaning |
| --- | --- | --- |
| <code>direction_mode</code> | strategy manifest | allowed capability: <code>long_only</code>, <code>short_only</code>, <code>both</code>, or <code>neutral</code> |
| <code>position_side</code> | position/order | <code>long</code> or <code>short</code> leg in swap hedge mode; spot has long inventory only |
| order value/target | strategy source | requested quantity, value, or weight change/target; short targets are negative in source |
| <code>open/add/reduce/close</code> | runtime order intent | canonical action derived from synchronized position and target delta; submitted quantity is absolute |
| <code>execution_mode</code> | deployment | <code>signal</code> emits signals, while <code>live</code> submits real orders |
| <code>coexistence_mode</code> | account ownership | <code>strict</code> or <code>advanced</code> manual/strategy inventory policy; it is not trade direction |

Order functions:

| Function | Meaning |
| --- | --- |
| <code>order(symbol, amount)</code> | add/subtract a quantity |
| <code>order_value(symbol, value)</code> | add/subtract quote-currency value |
| <code>order_target(symbol, amount)</code> | set a target quantity |
| <code>order_target_value(symbol, value)</code> | set a target quote value |
| <code>order_target_percent(symbol, percent)</code> | set a target share of portfolio equity |

Target APIs are usually best for repeatable rebalancing. Give every order a stable reason:

~~~python
order_target_percent(
    g.symbol,
    0.5,
    reason="breakout_long_entry",
)
~~~

Common order options:

| Option | Meaning |
| --- | --- |
| <code>reason</code> | stable audit reason |
| <code>position_side</code> | <code>long</code> or <code>short</code> leg for swap hedge mode |
| <code>client_order_id</code> | stable idempotency and status reference, at most 100 characters |
| <code>order_type</code> | <code>market</code> or <code>limit</code> |
| <code>limit_price</code> | required positive price for a limit order |
| <code>execution_algo</code> | <code>market</code>, <code>limit</code>, or <code>maker_then_market</code> |
| <code>maker_wait_sec</code> | maker wait before market fallback |
| <code>maker_offset_bps</code> | maker-price offset in basis points |

Example with a stable client reference:

~~~python
def submit_entry():
    g.entry_ref = order(
        g.symbol,
        1,
        position_side="long",
        order_type="limit",
        limit_price=100.0,
        client_order_id="breakout-long-20250102",
        reason="breakout_long_entry",
    )


def monitor_entry(cancel_requested):
    status = get_order_status(g.entry_ref)
    working = ("queued", "deferred", "submitted", "open", "partial")
    if cancel_requested and status["status"] in working:
        cancel_order(g.entry_ref)
~~~

In new strategies, every order that may be retried, cancelled, reconciled, or used to advance a cycle must supply a stable <code>client_order_id</code>. The order helper returns that ID for <code>get_order_status</code>. Legacy source without an explicit ID may still receive <code>None</code>, but that is migration behavior rather than the new-strategy contract.

Typical working states are <code>unknown</code>, <code>queued</code>, <code>deferred</code>, <code>submitted</code>, <code>open</code>, and <code>partial</code>. Terminal states include <code>filled</code>, <code>rejected</code>, <code>failed</code>, <code>cancelled</code>/<code>canceled</code>, and <code>expired</code>. <code>partial</code> is not a full fill and must not advance state by the planned quantity. A terminal order status and synchronized exchange position can become visible at slightly different times; confirm both before reusing capital, beginning a new cycle, or opening the opposite leg. Live cancellation is also asynchronous. <code>consume_last_exit_reason(symbol)</code> returns and clears the most recently recorded protection exit reason.

Write spot and all non-Crypto markets as long-only under the current product policy. A long exit and a short entry are independent; do not turn a zero target into a negative position automatically.

The engine accounts for commission, slippage, lot size, liquidity caps, price limits, and suspensions. Deferred and rejected requests appear in the order audit ledger. “No fill” does not necessarily mean “no signal.”

In live execution, an active same-leg request suppresses duplicate requests until reconciliation resolves it. A target that crosses through zero is executed close-first: the runtime closes the current leg, waits for confirmed reconciliation, then opens the opposite leg. Strategy state must advance from confirmed order status or synchronized positions, not merely because an order function was called.

---

## 13. Stop, take-profit, trailing, and time protection

Attach protection to an entry:

~~~python
order_target_percent(
    g.symbol,
    0.8,
    reason="breakout_long_entry",
    stop_loss_pct=0.03,
    take_profit_pct=0.08,
    trailing_stop_pct=0.025,
    trailing_activation_pct=0.02,
    time_limit_seconds=86400 * 10,
)
~~~

Or set defaults for later entries:

~~~python
set_default_protection(
    stop_loss_pct=0.03,
    take_profit_pct=0.08,
)
~~~

Percentage fields are ratios: <code>0.03</code> means 3%. Values are clamped to safe ranges, and negatives become zero.

Backtest behavior:

- A gap through a protection threshold fills at the available bar open.
- An intrabar touch fills at the trigger price.
- If several protections trigger in one bar, conservative mode prioritizes stop-loss, trailing stop, time limit, then take-profit.

Live execution checks the same protection semantics on an independent price clock instead of waiting for the next strategy bar. Protection state can be persisted and restored after a session restart.

---

## 14. Leverage and shorting

Only a static universe consisting entirely of Crypto swap instruments may declare:

~~~python
def initialize(context):
    g.symbol = "Crypto:BTC/USDT@okx:swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1h")
    context.allow_leverage(max_leverage=5)
~~~

Rules:

- Do not call <code>allow_leverage</code> for Crypto spot, equities, index/pool universes, or non-Crypto markets.
- Dynamic universes cannot enable contract leverage.
- Backtest/deployment leverage cannot exceed the source maximum.
- A run form cannot force leverage on when the source has not permitted it.
- The runtime applies the selected leverage; do not multiply order sizing by leverage again.
- Shorting belongs only in swap strategies and requires independent short-entry, short-exit, and risk rules.

### Trading-direction capability

New Crypto swap strategies should declare their capability in `initialize`:

~~~python
context.set_metadata(direction_mode="both")
~~~

Supported values are `long_only`, `short_only`, `both`, and `neutral`. This declaration does not place orders or override strategy signals. It lets deployment validation reserve the correct hedge-mode leg or legs and reject new entry signals that exceed the declared capability. `both` and `neutral` require hedge mode for live execution.

Every new Crypto swap strategy must declare <code>direction_mode</code> and pass an explicit <code>position_side</code> on each contract-position read and order call. Compiler inference from legacy <code>DIRECTION = 1/-1</code> constants or literal legs exists only for migration; it is not the recommended contract and must not be used by new templates. Write spot strategies as <code>long_only</code>.

### Hedge-mode example

The following example keeps a one-contract long core and independently enables a one-contract short hedge below the moving average:

~~~python
"""BTC Long Core With Short Hedge
Maintains independent long and short swap legs in exchange hedge mode.
"""


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@okx:swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1h")
    context.set_warmup(60)
    context.allow_leverage(max_leverage=5)
    context.set_metadata(direction_mode="both")


def handle_data(context, data):
    bars = get_history(51, "1h", "close", g.symbol)
    if len(bars) < 51:
        return

    price = float(bars["close"].iloc[-1])
    average = float(bars["close"].tail(50).mean())
    long_position = get_position(g.symbol, position_side="long")
    short_position = get_position(g.symbol, position_side="short")

    if abs(float(long_position.amount or 0.0)) < 0.5:
        order_target(
            g.symbol,
            1,
            position_side="long",
            reason="core_long",
        )

    hedge_required = price < average
    if hedge_required and abs(float(short_position.amount or 0.0)) < 0.5:
        order_target(
            g.symbol,
            -1,
            position_side="short",
            reason="open_short_hedge",
        )
    elif not hedge_required and abs(float(short_position.amount or 0.0)) >= 0.5:
        order_target(
            g.symbol,
            0,
            position_side="short",
            reason="close_short_hedge",
        )
~~~

The quantity unit follows the venue instrument specification; do not assume one contract always equals one base coin. Before live start, the platform confirms the account position mode. `both` and `neutral` fail closed when hedge mode cannot be confirmed. A running strategy reserves its account/exchange/market/symbol leg; overlapping ownership raises <code>strategyV2.liveLegConflict</code>. In confirmed hedge mode, separate long-only and short-only strategies may own opposite legs, but a strategy declaring <code>both</code> or <code>neutral</code> owns both legs.

Never maintain authoritative quantities only in <code>g.long_qty</code>/<code>g.short_qty</code>. An order can be rejected, deferred, partially filled, or rounded by venue rules. Read synchronized leg positions and order status before updating cycle state.

---

## 15. Complete CTA tutorial: dual EMA trend

~~~python
"""Dual EMA Long Trend
Trades a long-only daily SPY trend with a protected entry and next-open fills.
"""

# @param fast_period int 20 Fast EMA period range=5:80:5
# @param slow_period int 50 Slow EMA period range=20:250:10
# @param target_pct float 0.95 Target portfolio weight range=0.1:1.0:0.05
# @param stop_loss_pct float 0.05 Entry stop-loss ratio range=0.01:0.15:0.01


def initialize(context):
    g.symbol = "USStock:SPY"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1d")
    context.set_warmup(300)
    context.set_benchmark("USStock:SPY")


def handle_data(context, data):
    fast_period = int(context.params.get("fast_period", 20))
    slow_period = int(context.params.get("slow_period", 50))
    target_pct = float(context.params.get("target_pct", 0.95))
    stop_loss_pct = float(context.params.get("stop_loss_pct", 0.05))

    if fast_period >= slow_period:
        log.warning("fast_period must be smaller than slow_period")
        return

    bars = get_history(
        slow_period + 2,
        "1d",
        "close",
        g.symbol,
    )
    if len(bars) < slow_period + 1:
        return

    close = bars["close"]
    fast_now = float(close.ewm(span=fast_period, adjust=False).mean().iloc[-1])
    slow_now = float(close.ewm(span=slow_period, adjust=False).mean().iloc[-1])
    position = get_position(g.symbol)

    if fast_now > slow_now and position.amount <= 0:
        order_target_percent(
            g.symbol,
            target_pct,
            reason="dual_ema_long_entry",
            stop_loss_pct=stop_loss_pct,
        )
    elif fast_now < slow_now and position.amount > 0:
        order_target_percent(
            g.symbol,
            0.0,
            reason="dual_ema_long_exit",
        )
~~~

Why it is structured this way:

- Universe, frequency, and benchmark live in source.
- Warm-up covers the slow EMA, while runtime length is still checked.
- Invalid fast/slow combinations stop the current event.
- Entry and exit are exclusive; the bearish condition exits a long but does not short.
- A completed daily bar emits an order for the next open.
- Protection is attached to the entry; the exit targets zero.

---

## 16. Portfolio tutorial: weekly factor rebalance

~~~python
"""S&P 500 Momentum Basket
Selects the strongest five point-in-time pool members and rebalances weekly.
"""

# @param holdings int 5 Number of holdings range=3:20:1
# @param max_weight float 0.18 Maximum weight per holding range=0.05:0.3:0.01


def initialize(context):
    context.set_universe(pool="sp500")
    context.subscribe(frequency="1d")
    context.set_warmup(80)
    context.set_benchmark("USStock:SPY")
    run_weekly(rebalance, weekday=1, time="09:35")


def rebalance(context, data):
    holdings = int(context.params.get("holdings", 5))
    max_weight = float(context.params.get("max_weight", 0.18))
    symbols = get_universe_stocks()
    if len(symbols) < holdings:
        return

    scores = get_factors(symbols, "momentum_20")
    if scores.empty or "momentum_20" not in scores.columns:
        return

    ranked = scores["momentum_20"].dropna().sort_values(ascending=False)
    selected = list(ranked.head(holdings).index)
    if not selected:
        return

    target_weight = min(max_weight, 0.95 / len(selected))
    current = get_positions()

    for symbol in current:
        if symbol not in selected:
            order_target_percent(symbol, 0.0, reason="weekly_remove")

    for symbol in selected:
        order_target_percent(symbol, target_weight, reason="weekly_select")
~~~

This strategy class must use point-in-time universe and factor data. Evaluate coverage, survivorship bias, turnover, trading costs, lot sizes, and unfilled orders in addition to headline return.

---

## 17. Backtests, results, and diagnosis

Core backtest request:

~~~json
{
  "code": "...",
  "startDate": "2024-01-01",
  "endDate": "2025-12-31",
  "initialCapital": 100000,
  "commission": 0.0005,
  "slippage": 0.0005,
  "leverageEnabled": false,
  "leverage": 1,
  "params": {},
  "persist": true
}
~~~

You may supply <code>sourceId</code> or <code>strategyId</code> to load saved source. The request cannot override source markets, instruments, or frequency.

Inspect:

- <code>resultStatus</code>: <code>no_signals</code>, <code>open_position_only</code>, or <code>completed_trades</code>.
- <code>totalExecutions</code>: fill count.
- <code>totalTrades</code>: closed round-trip count, not fill count.
- <code>rawTrades</code>/<code>executions</code>: opens, adds, reductions, and closes.
- <code>closedTrades</code>: completed round trips.
- <code>orderLedger</code>: fills, deferrals, rejections, and reasons.
- <code>holdingSnapshots</code>/<code>rebalanceRecords</code>: portfolio evolution.
- <code>equityCurve</code>, drawdown, win rate, Profit Factor, benchmark, and excess return.
- <code>dataProvenance</code>/<code>executionAssumptions</code>: data origin and fill model.

### Costs and execution assumptions

- Commission is charged on every fill. A completed round trip deducts both allocated entry commission and exit commission from realized profit.
- Slippage is applied according to the reported execution assumptions.
- Crypto funding payments are currently **not modeled in Strategy API V2 backtests**. Confirm <code>executionAssumptions.fundingMode == "not_modeled"</code>; do not compare a leveraged swap backtest directly with live net profit without estimating funding separately.
- Live trading uses venue-reported fill fees and, where available, funding/account-ledger records. A fee may be charged in quote, base, or a discount token, so conversion and reconciliation can lag the fill.
- Test a range of commission and slippage assumptions. A strategy whose edge disappears under a small cost increase is not robust.

The backtest center also supports factor research and parameter tuning. Tuning accepts grid or random parameter spaces, caps a request at 500 variants, and reports out-of-sample validation for the selected result. Backtests may consume a system-configured credit amount; a failed execution is refunded automatically. UI request timeouts do not prove the server job failed—check backtest history before submitting a duplicate run.

Zero executions can be valid: insufficient history, conditions never met, poor parameters, missing data, or rejected orders. Read logs and the order ledger before treating it as an engine failure.

---

## 18. Deployment and live boundaries

Core deployment fields include:

- <code>sourceId</code>
- <code>name</code>
- <code>initialCapital</code>
- <code>executionMode</code>: <code>signal</code> or <code>live</code>
- optional <code>credentialId</code>, <code>params</code>, leverage, position side, and notifications

A new deployment is stopped and must be started explicitly. Stop it before deletion.

Current live-account boundaries:

| Market | Supported live venues | Product boundary |
| --- | --- | --- |
| Crypto | Binance, Bitget, Bybit, OKX, Gate, HTX | spot and swap according to venue/account capability |
| USStock | Alpaca, IBKR | current broker policy is long-only |
| Other parsed markets | none | backtest/data availability does not imply live support |

Mixed-market live deployment is unsupported. Other markets cannot be forced through a mismatched credential.

### Position ownership, reconciliation, and account risk

- A live strategy owns only its allocated strategy position. Manual holdings and positions owned by another strategy are not available for it to close.
- Advanced coexistence supports both Crypto spot and derivatives. Baselines are keyed by credential, market type, canonical symbol, and position leg; spot has long inventory only, while derivatives use long/short legs.
- The reconciliation identity is <code>account position = strategy allocation + protected manual position + unknown delta</code>. New entries require the unknown delta to remain within tolerance.

| Ownership mode | Protected manual position | Behavior |
| --- | --- | --- |
| <code>strict</code> (default) | always 0 | unallocated account inventory pauses same-side entries/adds; it never auto-closes a position |
| <code>advanced</code> | records <code>account position - strategy position</code> after explicit confirmation | strategy inventory may coexist with that floor; any later unknown delta pauses entries/adds again |

- Drift pauses same-side opens/adds only. The first state transition logs account, strategy, protected, and unknown quantities; an unchanged blocked state does not spam duplicate logs.
- Grid strategies run the same ownership check on every resting-order sync. Drift cancels unfilled entry orders on that leg. If the account is below its protected allocation or the protection ledger cannot be verified, potentially oversized exits are also cancelled and rebuilt from strategy inventory, existing exits, and the protected floor.
- Closes/reductions remain available but are capped by the strategy ledger, actual exchange inventory, and protected baseline. They can never cross protected manual inventory, and are not a tool for absorbing an unknown delta.
- The Ownership & Repair page exposes <code>protect_manual</code> (record the current delta and enable advanced mode), <code>strict_mode</code> (clear the floor and return to strict mode), and <code>recheck</code> (refresh and reconcile). These actions update ownership records only and never trade automatically.
- Advanced coexistence is QuantDinger ledger isolation, not physical venue isolation. Spot inventory still shares the account balance; same-side derivatives still share venue entry price, margin, and liquidation risk.
- Same account/exchange/market/symbol/leg ownership is exclusive. Confirmed hedge mode can allow separate long-only and short-only strategies on opposite legs; <code>both</code>/<code>neutral</code> reserves both.
- Minimum quantity, quantity step, minimum notional, available margin, leverage, and venue caps are applied after strategy sizing. The final submitted quantity can differ from the raw request.
- Only derivative opens/adds configure margin mode and leverage. Closes/reductions skip account configuration so a configuration endpoint failure cannot block an exit. After Binance HTTP 408, <code>-1007</code>, or “execution status unknown,” the runtime reads configuration back and proceeds only when observed margin mode/leverage matches the target.
- Optional account-risk limits can reject orders for gross notional, estimated margin, gross leverage, or per-symbol notional. Treat those as risk warnings that require configuration or sizing changes, not as reasons to bypass the guard.
- Market data, private WebSocket events, and periodic REST reconciliation work together. WebSocket improves latency; REST remains the recovery source after disconnects or missed events.

Use signal mode first to validate notifications, signal frequency, and state restoration. A successful backtest does not prove that credentials, balances, venue rules, minimum order sizes, and network health are ready for live trading.

---

## 19. Sandbox and common failures

Strategy source runs in a safe execution environment. File, network, database, process, dynamic execution, reflection, and unsafe imports are prohibited. Do not use <code>eval</code>, <code>exec</code>, <code>compile</code>, <code>open</code>, dunder bypasses, or external state.

Allowed import roots are <code>numpy</code>, <code>pandas</code>, <code>math</code>, <code>json</code>, <code>datetime</code>, <code>time</code>, <code>collections</code>, <code>functools</code>, <code>itertools</code>, <code>statistics</code>, <code>decimal</code>, <code>fractions</code>, and <code>copy</code>. File/URL/database methods such as pandas <code>read_*</code>/<code>to_*</code>, NumPy load/save, pickle-like deserialization, and string-expression evaluators remain blocked even through an allowed module.

| Error | Meaning | Fix |
| --- | --- | --- |
| <code>strategyV2.codeRequired</code> | empty source | submit complete source |
| <code>strategyV2.initializeRequired</code> | initialize missing | add it |
| <code>strategyV2.initializeFailed:...</code> | initialization failed | keep initialize declarative |
| <code>strategyV2.universeRequired</code> | universe missing | call <code>set_universe</code> |
| <code>strategyV2.handlerRequired</code> | no handler/schedule | add a handler or schedule |
| <code>strategyV2.leverageCryptoSwapOnly</code> | invalid leverage market | use static Crypto swaps only |
| <code>strategyV2.leverageNotAllowed</code> | run requests unpermitted leverage | permit it legally or disable it |
| <code>strategyV2.leverageExceedsStrategyLimit</code> | requested leverage too high | lower the request |
| <code>strategyV2.dataUnavailable:...</code> | instrument data unavailable | check canonical symbol and range |
| <code>strategyV2.frequencyUnsupported:...</code> | source declares an unsupported timeframe | use a native timeframe listed in Section 6 |
| <code>strategyV2.tooManyFrequencies:8</code> | source declares more than eight timeframes | remove unnecessary subscriptions |
| <code>strategyV2.frequencyNotSubscribed:...</code> | source reads an undeclared timeframe | subscribe it in <code>initialize</code> or correct the call |
| <code>strategyV2.noMarketData</code> | live cycle has no usable frame | verify symbol, source, connection, and subscribed timeframe |
| <code>strategyV2.initializeParamsUnavailable</code> | params read during discovery | move the read to a handler |
| <code>strategyV2.directionModeViolation:...</code> | entry exceeds declared direction | fix metadata or signal direction; exits remain allowed |
| <code>strategyV2.dualDirectionHedgeModeRequired:...</code> | account is not in hedge mode | enable venue hedge/dual-side mode |
| <code>strategyV2.hedgeModeUnknown:...</code> | account mode could not be confirmed | repair credential/API access and retry |
| <code>strategyV2.liveLegConflict:...</code> | another live strategy owns the leg | stop/reconfigure the conflicting strategy |
| <code>position_drift_detected:...</code> | account, strategy, and protected baseline contain an unknown delta | recheck, protect manual inventory, or restore strict mode in Ownership & Repair; do not bypass |
| <code>unallocated_account_position</code> | account position exceeds strategy plus protected inventory | verify and protect the delta as manual inventory, or restore equality manually |
| <code>account_below_protected_allocation</code> | account position is below strategy plus protected inventory | stop new entries and reconcile venue, strategy ledger, and baseline |
| invalid amount/minimum notional | rounded quantity cannot be submitted | increase capital/weight or choose a suitable instrument |
| account-risk rejection | configured account exposure limit exceeded | reduce size/leverage or deliberately revise the limit |
| <code>strategyV2.runtimeFailed:...</code> | handler raised | inspect the named handler and cause |

---

## 20. System presets and visual robot templates

### System preset strategy templates

The system preset catalog currently uses <code>system_seed version=11</code>. It contains eight CTA templates (single MA, dual MA, bullish candle through three averages, trend-filtered bullish candle, Turtle, indicator resonance, MACD/KDJ, and SuperTrend) and four portfolio templates (market-cap barbell, momentum Top N, low volatility, and quality growth). Presets are examples and the executable baseline for the current recommended Strategy API V2 contract.

Every system preset must satisfy these rules:

- Declare <code>direction_mode</code> explicitly; Crypto swap templates read and write explicit <code>position_side</code> legs.
- A bidirectional trend template closes the opposite leg, waits for fill and position synchronization, and only then opens the target leg. It must not replace two hedge legs with one net-position variable.
- Reconstructible state comes from synchronized <code>amount</code>, <code>avg_cost</code>, and order status. State that cannot be rebuilt reliably must enable <code>PERSIST_RUNTIME_STATE</code>.
- Every catalog revision must pass parameter-contract, compilation, direction-capability, and synthetic-backtest tests. After copying a preset, revalidate the manifest whenever market, direction, or frequency changes.

### Visual robot templates

Robot templates generate editable Strategy API V2 source. The generated source—not the preview alone—is the deployable contract. Verify it after every manual edit.

Current generated-source contract revisions are <code>GRID_TEMPLATE_VERSION = 6</code> for grid, <code>DCA_TEMPLATE_VERSION = 6</code> for DCA, and <code>ROBOT_TEMPLATE_VERSION = 6</code> for martingale and layered martingale. These constants help diagnose generated source; they do not replace manifest or preflight validation.

| Template | Trigger and sizing | Current boundary |
| --- | --- | --- |
| Grid | range split into arithmetic/geometric cells; each filled entry arms its paired cell exit | live uses resting limit orders; backtest replays OHLC touches |
| DCA | fixed elapsed-minute interval and fixed capital fraction per purchase | Crypto spot, long-only |
| Martingale | adverse-price levels with increasing allocation | capped levels, total budget, and cycle risk required |
| Layered martingale | martingale levels organized into multiple allocation groups | same hard caps plus per-group limits |

Grid rules:

- A grid cell is a lifecycle: entry ready → entry working/filled → paired exit working/filled → next cycle. It is not “buy every lower level and liquidate the whole position at one price.”
- <code>max_open_orders</code> limits simultaneously armed entries. Stable <code>client_order_id</code> values prevent duplicate cell orders.
- Live grid execution uses exchange limit orders and fill reconciliation. Backtests use bar high/low touch replay and cannot know the exact intrabar path when several levels are crossed in one bar; use a sufficiently fine timeframe.
- Neutral grids require swap hedge mode and own both legs. Spot grids are long-only.

DCA rules:

- The interval is measured in elapsed minutes, not “number of K-lines.” The handler can only act when a subscribed bar is processed, so an interval shorter than the source timeframe becomes effective on the next available bar.
- Each purchase is capped by both per-order percentage and total cycle budget. Optional price filters, take-profit, hard stop, and trailing protection do not remove the need for a maximum order count.
- A submitted purchase first becomes pending. Only a <code>filled</code> result from <code>get_order_status</code> increments the order count and charges actual filled notional plus fees to cycle spend; calling <code>order_value</code> does not imply a fill.
- <code>partial</code> remains pending and must not be treated as fully filled. <code>rejected</code>, <code>failed</code>, <code>cancelled/canceled</code>, or <code>expired</code> releases pending state without consuming count or filled budget, and a retry uses a new stable client reference.
- Generated DCA source enables <code>PERSIST_RUNTIME_STATE</code>. After an exit, reset the cycle only when the account position is confirmed flat and an explicit exit reason exists; a short position-sync delay must not start a duplicate cycle.

Martingale rules:

- Each level needs a price trigger, planned allocation, maximum attempts, stable client reference, and confirmed-fill transition.
- Generated martingale and layered-martingale sources enable <code>PERSIST_RUNTIME_STATE</code>. Preserve the recovery and final-sweep logic when editing them.
- A rejected or partial order must not advance a level as if fully filled. After restart, reconcile state with the strategy-owned position before issuing another level or close.
- Martingale is a high-tail-risk sizing method. Always cap total deployed capital, levels, leverage, stop loss, and restart-after-stop behavior.

---

## 21. Pre-publication checklist

- [ ] The file has an English docstring covering name, universe, signals, schedule, and risk.
- [ ] <code>initialize</code> only declares universe, subscription, warm-up, benchmark, schedules, leverage permission, and initial <code>g</code>.
- [ ] Instruments are canonical and Crypto explicitly distinguishes spot/swap.
- [ ] The source owns instruments and frequency; no run-form override is assumed.
- [ ] Parameter defaults and code fallbacks agree.
- [ ] Every history window checks actual length.
- [ ] No future rows, negative shifts, or centered rolling.
- [ ] Indicators and entry/scale-in signals use completed bars only; real-time prices do not overwrite the last OHLC and are limited to protection and equity risk.
- [ ] Long exits and short entries are independent.
- [ ] Hedge-mode code reads and writes explicit <code>position_side</code> legs.
- [ ] <code>direction_mode</code>, <code>position_side</code>, <code>execution_mode</code>, and account <code>coexistence_mode</code> are not conflated.
- [ ] Exposure is capped; grid, DCA, martingale, and scaling layers have hard limits.
- [ ] Every order has an auditable reason.
- [ ] Retryable/working orders use stable client IDs and do not advance state before confirmation.
- [ ] Risk percentages use decimal ratios.
- [ ] Leverage is declared only for Crypto swaps and is not multiplied twice.
- [ ] Schedule timezone and cross-restart state requirements are explicit.
- [ ] The manifest verifies successfully.
- [ ] The order ledger is reviewed, not only the equity curve.
- [ ] Both entry and exit fees are included; swap funding is evaluated separately from the current backtest.
- [ ] Robustness is tested across periods and cost assumptions.
- [ ] At least one successful backtest exists before publication.
- [ ] Credentials, market, balance, lot size, and notifications are checked before live use.
- [ ] Reusing existing spot or derivative inventory includes an explicit strict/advanced ownership choice and verified protected baseline.
