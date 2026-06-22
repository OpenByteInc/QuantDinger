# QuantDinger v3 Python Strategy Development Guide

> **Platform contract (required reading)**: [Signal & Execution Standard v1](./SIGNAL_EXECUTION_STANDARD.md) — backtest/live alignment, two-way vs four-way signals, exit ownership, and release checklist. This guide focuses on tutorials and examples.

This guide is written from a **developer** point of view. Its goal is not only to list the current contracts, but to answer the practical question:

**How do I build an indicator strategy that is clear, backtestable, and ready to become a saved trading strategy?**

QuantDinger currently supports two Python authoring models:

- **IndicatorStrategy**: dataframe-based code for indicator research, chart rendering, and signal-style backtests.
- **ScriptStrategy**: event-driven code for runtime execution, strategy backtests, and live trading.

If you are starting a new strategy, the default recommendation is:

1. Start with `IndicatorStrategy`.
2. Prove the signal logic visually and in backtests.
3. Move to `ScriptStrategy` only if you need bar-by-bar state, dynamic position management, or execution control.

---

## 1. Start With the Right Mental Model

The most common source of confusion is mixing up **signal logic**, **risk defaults**, and **runtime execution**.

### 1.1 IndicatorStrategy

Think of `IndicatorStrategy` as:

- compute indicator series from `df`
- generate boolean `buy` / `sell` signals
- declare default strategy settings through metadata comments
- return chart-friendly `output`

This is the best fit for:

- indicator research
- strategy prototyping
- parameter tuning
- signal-based backtests
- saved strategies that still follow a signal-first workflow

### 1.2 ScriptStrategy

Think of `ScriptStrategy` as:

- maintain runtime logic bar by bar
- inspect current position state through `ctx.position`
- place explicit actions with `ctx.buy()`, `ctx.sell()`, and `ctx.close_position()`
- manage exits and sizing in code when needed

This is the best fit for:

- stateful execution logic
- dynamic stop-loss or take-profit handling
- partial exits, scale-ins, cooldowns, or other runtime rules
- strategies that behave more like trading bots than pure indicators

### 1.3 The Most Important Separation

For `IndicatorStrategy`, you usually have **three layers**:

1. **Indicator layer**: moving averages, RSI, ATR, bands, filters.
2. **Signal layer**: `df['buy']` and `df['sell']`.
3. **Risk defaults layer**: `# @strategy stopLossPct ...`, `takeProfitPct`, `entryPct`, and related defaults.

Do not mix these into one thing.

In particular:

- `buy` / `sell` decide **when the strategy wants to enter or exit**
- `# @strategy` decides **how the engine should size and protect positions by default**
- leverage belongs in product configuration, not in indicator code

---

## 2. Which Mode Should You Use?

| Use Case | Recommended Mode |
|----------|------------------|
| Build indicators, overlays, and signal markers | `IndicatorStrategy` |
| Research entry and exit rules on a dataframe | `IndicatorStrategy` |
| Add fixed stop-loss, take-profit, or entry sizing defaults | `IndicatorStrategy` |
| Need runtime position state and bar-by-bar control | `ScriptStrategy` |
| Need dynamic exits based on current open position | `ScriptStrategy` |
| Need partial close, scale-in/out, or bot-like logic | `ScriptStrategy` |

Rule of thumb:

- If your logic can be described as "when condition A happens, buy; when condition B happens, sell", start with `IndicatorStrategy`.
- If your logic sounds like "after entry, keep watching the open position and react differently depending on state", you probably need `ScriptStrategy`.

---

## 3. How To Develop an IndicatorStrategy

This is the recommended workflow for most new strategy development.

### 3.1 Step 1: Declare metadata and defaults first

At the top of the script, define name, description, tunable params, and strategy defaults.

```python
my_indicator_name = "Trend Pullback Strategy"
my_indicator_description = "Buy pullbacks in an uptrend and exit on weakness."

# signal_form: two_way
# exit_owner: engine
# flip_mode: R2

# @param fast_len int 20 Fast EMA length
# @param slow_len int 50 Slow EMA length
# @param rsi_len int 14 RSI length
# @param rsi_floor float 45 Minimum RSI for long entries

# @strategy stopLossPct 0.03
# @strategy takeProfitPct 0.06
# @strategy entryPct 0.25
# @strategy trailingEnabled true
# @strategy trailingStopPct 0.02
# @strategy trailingActivationPct 0.04
# @strategy tradeDirection long
```

Use `# @param` for values the user may tune often.

Format:

```python
# @param <name> <int|float|bool|str|string> <default> <description>
```

Best practice:

- read declared params through `params.get(...)`
- `string` is accepted as the same type family as `str`
- if you declare params but hardcode the values instead, the built-in code quality checker will flag it

Use `# @strategy` for strategy defaults such as:

- `stopLossPct`: stop-loss ratio, for example `0.03` = **3% adverse underlying price move** (`0.001` = 0.1%)
- `takeProfitPct`: take-profit ratio, for example `0.06` = **6% favorable price move**
- `entryPct`: fraction of capital on entry (`1` = **100%**, `0.25` = 25%)
- `trailingEnabled`
- `trailingStopPct`
- `trailingActivationPct`
- `tradeDirection`: `long`, `short`, or `both`

Important:

- These are **defaults consumed by the engine**.
- They are not extra dataframe columns.
- Do not put `leverage` here.
- Keep the values realistic and confirm them in backtests; the parser is intentionally more permissive than the toy examples shown here.

### 3.2 Step 2: Copy the dataframe and compute indicators

Indicator code runs in a sandbox. `pd`, `np`, and a `params` dictionary are already available.

Recommended baseline:

```python
df = df.copy()
```

Expected columns usually include:

- `open`
- `high`
- `low`
- `close`
- `volume`

A `time` column may exist, but do not rely on a fixed type.

Avoid:

- network access
- file I/O
- subprocesses
- unsafe metaprogramming such as `eval`, `exec`, `open`, `__import__`, `getattr`, or `setattr`
- `import operator` (and dunder-bypass patterns such as string-built `__class__` / `__globals__`)

Allowed `import` roots (anything else is rejected by validation):

`numpy`, `pandas`, `math`, `json`, `datetime`, `time`, `collections`, `functools`, `itertools`, `statistics`, `decimal`, `fractions`, `copy`

`pd`, `np`, and `params` are already injected — you usually do not need `import pandas` / `import numpy`.

### 3.3 Step 3: Turn raw conditions into clean `buy` / `sell` signals

The backtest engine reads **boolean** columns:

- `df['buy']`
- `df['sell']`

They should:

- match the dataframe length exactly
- be boolean after `fillna(False)`
- usually be edge-triggered, unless repeated signals are intentional

Recommended pattern:

```python
raw_buy = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
raw_sell = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))

df['buy'] = (raw_buy.fillna(False) & (~raw_buy.shift(1).fillna(False))).astype(bool)
df['sell'] = (raw_sell.fillna(False) & (~raw_sell.shift(1).fillna(False))).astype(bool)
```

This keeps your signals from firing on every bar of the same regime.

#### 3.3.1 How `tradeDirection` maps `buy` / `sell` at execution time

After an indicator is saved as a strategy, the backend normalizes `df['buy']` / `df['sell']` into execution signals. In `both` mode, **do not** treat `buy` as a standalone `close_short` column:

| `tradeDirection` | `buy=True` | `sell=True` |
|------------------|------------|-------------|
| `long` | `open_long` | `close_long` |
| `short` | `close_short` | `open_short` |
| `both` | `open_long`; if currently short, **close short then open long** | `open_short`; if currently long, **close long then open short** |

Notes:

- `both` matches `BacktestService` `_both_mode`; live execution is aligned with that semantics.
- Putting short take-profit / stop-loss into `df['buy']` means **exit short and possibly flip long**, not "flat only".
- For flat-only exits, use `long`/`short`, explicit four-way columns, or `ScriptStrategy` with `ctx.close_position()`.

Typical dual-Keltner style wiring:

```python
df['buy']  = sig_buy_entry | sig_short_tp | sig_short_sl
df['sell'] = sig_sell_entry | sig_long_tp | sig_long_sl
```

If backtests show "close short then open long" on the same bar, that is usually expected under `both`, not a engine bug.

### 3.4 Step 4: Decide who owns the exit logic

This is where stop-loss, take-profit, and position management usually become confusing.

There are **two valid exit styles** in `IndicatorStrategy`, and the header contract must make the choice explicit.

#### Style A: Signal-managed exits

Your indicator logic itself decides when to exit by setting `df['sell']`.

Examples:

- moving-average bearish crossover
- RSI falling below a threshold
- close dropping below an ATR-based stop line
- mean reversion target hit

Use this style when the exit is part of the strategy idea itself.

Declare it like this:

```python
# exit_owner: indicator
# @strategy trailingEnabled false
```

The current backend interprets `exit_owner: indicator` as: server-side fixed stop-loss, fixed take-profit, and trailing stop do not close positions. Exits come from indicator signals. `entryPct` / `tradeDirection` can still be used as defaults.

#### Style B: Engine-managed exits

You let the strategy engine apply fixed defaults declared with `# @strategy`, such as:

- `stopLossPct`
- `takeProfitPct`
- `entryPct`
- trailing settings

Use this style when the signal logic should stay simple, and you want the engine to handle fixed protective rules.

Declare it like this:

```python
# exit_owner: engine
```

`exit_owner: engine` keeps server-side price risk active. You may still keep structural reverse `close_*` signals, but do not also encode tight in-indicator TP/SL touch exits.

#### Best practice

Pick one primary owner for exits whenever possible.

For example:

- if your edge is "enter on crossover, exit on reverse crossover" with no extra fixed price-risk exits, make indicator signals the owner and write `# exit_owner: indicator`
- if your edge is "enter on signal and let a fixed 3% stop + 6% target manage the trade", or if `close_*` only represents structural trend reversal, make the engine the price-exit owner and write `# exit_owner: engine`

Do not write `exit_owner: layered`. The current platform does not implement a third owner. If you truly need a mixed design, write it as `engine` and document which `close_*` signals are structural reversals rather than tight TP/SL.

### 3.5 Step 5: Build the `output` object

Your script must assign a final `output` dictionary:

```python
output = {
    "name": "My Strategy",
    "plots": [],
    "signals": []
}
```

Supported keys:

- `name`
- `plots`
- `signals`
- `calculatedVars` as optional metadata

Each plot item should contain:

- `name`
- `data` with length exactly `len(df)`
- `color`
- `overlay`
- optional `type`

Each signal item should contain:

- `type`: `buy` or `sell`
- `text`
- `color`
- `data`: list with `None` on bars without a marker

### 3.6 Step 6: Validate backtest semantics

Indicator backtests are signal-driven:

- the engine reads `df['buy']` and `df['sell']`
- signals are treated as bar-close confirmation
- fills are typically on the **next bar open**

This matters because:

- an intrabar-looking stop drawn on the current candle is not the same as a next-bar-open fill
- using `shift(-1)` in signal logic introduces look-ahead bias

Practical nuance:

- the normalized strategy snapshot can execute with either `next_bar_open` or `same_bar_close`
- most indicator workflows should still be designed with a "confirm on close, usually fill on next open" mental model
- if product settings change the execution timing, rerun the backtest and review fills instead of assuming the semantics stayed the same

#### 3.6.1 Why live entry/exit times can diverge from backtests

| Topic | Indicator backtest | Indicator live (default) |
|-------|-------------------|-------------------------|
| Bars | Historical **closed** OHLC | Last bar may be updated with the **latest tick** (`high` / `low` / `close`) |
| Touch conditions | Known only after the bar closes | May fire earlier on the **forming** bar |
| Signal evaluation | Bar-by-bar on the backtest timeline | Indicator may be recomputed every tick; `exit_signal_mode=immediate` fires exits right away |
| Orders per tick | Queued in backtest order | Indicator mode usually **one signal per tick** (closes first) |

If your logic uses `high >= line` / `low <= line`, backtests are often **later** than live; realtime bar updates can trigger **earlier** exits.

Alignment tips:

- Set `signal_mode` and `exit_signal_mode` to **`confirmed`** when you want live to track closed-bar backtests.
- If in-indicator `sig_*_tp` / `sig_*_sl` exits exist, declare `# exit_owner: indicator` and keep `# @strategy trailingEnabled false`; otherwise indicator exits plus server trailing can duplicate closes and amplify timing drift.
- Before going live, compare backtest fills with logs for `Signal submitted` and `server_trailing_stop`.

If a live close briefly shows amount `0`, the worker **re-syncs exchange positions and resolves size again**; rejection happens only when the exchange is already flat.

---

## 4. How To Write Stop-Loss, Take-Profit, and Position Sizing

This section is the practical answer to the most common implementation question.

### 4.1 Fixed stop-loss, take-profit, and entry sizing in IndicatorStrategy

If you want fixed risk defaults, write them as `# @strategy` lines:

```python
# @strategy stopLossPct 0.03
# @strategy takeProfitPct 0.06
# @strategy entryPct 0.25
# @strategy tradeDirection long
```

Meaning:

- `stopLossPct 0.03`: use a 3% stop-loss default
- `takeProfitPct 0.06`: use a 6% take-profit default
- `entryPct 0.25`: allocate 25% of capital on entry
- `tradeDirection long`: long-only by default

This is the correct choice when you want:

- simple signal code
- consistent defaults in backtests
- strategy settings that the UI and engine can understand directly

### 4.2 Indicator-driven exits in IndicatorStrategy

If your "stop-loss" is actually part of the indicator model, write it as a `sell` signal.

Example: exit a long when close falls below an ATR-style stop line.

```python
atr = (df['high'] - df['low']).rolling(14).mean()
stop_line = df['close'].rolling(20).max() - atr * 2.0

raw_sell = df['close'] < stop_line.shift(1)
df['sell'] = (raw_sell.fillna(False) & (~raw_sell.shift(1).fillna(False))).astype(bool)
```

In this style:

- the exit belongs to your indicator logic
- the engine is not inventing the stop for you
- you should explain this in the strategy description or comments

### 4.3 How position management works in IndicatorStrategy

For indicator strategies, position management is intentionally simple:

- use `entryPct` for default entry sizing
- use `tradeDirection` to limit long, short, or both
- use engine-managed stop, take-profit, or trailing defaults if they are fixed

If you need:

- scale-in / scale-out
- partial exits
- different logic before and after entry
- stop movement that depends on the current live position state
- cooldowns after a stop

then the strategy has outgrown `IndicatorStrategy` and should move to `ScriptStrategy`.

---

## 5. Full IndicatorStrategy Example

This example shows a complete developer-oriented pattern: metadata, defaults, indicator calculation, signal generation, and chart output.

```python
my_indicator_name = "EMA Pullback Strategy"
my_indicator_description = "Buy pullbacks above the slow EMA and exit on trend failure."

# @param fast_len int 20 Fast EMA length
# @param slow_len int 50 Slow EMA length
# @param rsi_len int 14 RSI length
# @param rsi_floor float 50 Minimum RSI for entry

# @strategy stopLossPct 0.03
# @strategy takeProfitPct 0.06
# @strategy entryPct 0.25
# @strategy tradeDirection long

df = df.copy()

fast_len = int(params.get('fast_len', 20))
slow_len = int(params.get('slow_len', 50))
rsi_len = int(params.get('rsi_len', 14))
rsi_floor = float(params.get('rsi_floor', 50.0))

ema_fast = df['close'].ewm(span=fast_len, adjust=False).mean()
ema_slow = df['close'].ewm(span=slow_len, adjust=False).mean()

delta = df['close'].diff()
gain = delta.clip(lower=0).ewm(alpha=1 / rsi_len, adjust=False).mean()
loss = (-delta.clip(upper=0)).ewm(alpha=1 / rsi_len, adjust=False).mean()
rs = gain / loss.replace(0, np.nan)
rsi = 100 - (100 / (1 + rs))

trend_up = ema_fast > ema_slow
pullback_done = df['close'] > ema_fast
rsi_ok = rsi > rsi_floor

raw_buy = trend_up & pullback_done & rsi_ok & (~trend_up.shift(1).fillna(False))
raw_sell = (ema_fast < ema_slow) | (rsi < 45)

buy = (raw_buy.fillna(False) & (~raw_buy.shift(1).fillna(False))).astype(bool)
sell = (raw_sell.fillna(False) & (~raw_sell.shift(1).fillna(False))).astype(bool)

df['buy'] = buy
df['sell'] = sell

buy_marks = [df['low'].iloc[i] * 0.995 if buy.iloc[i] else None for i in range(len(df))]
sell_marks = [df['high'].iloc[i] * 1.005 if sell.iloc[i] else None for i in range(len(df))]

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "EMA Fast",
            "data": ema_fast.fillna(0).tolist(),
            "color": "#1890ff",
            "overlay": True
        },
        {
            "name": "EMA Slow",
            "data": ema_slow.fillna(0).tolist(),
            "color": "#faad14",
            "overlay": True
        },
        {
            "name": "RSI",
            "data": rsi.fillna(0).tolist(),
            "color": "#722ed1",
            "overlay": False
        }
    ],
    "signals": [
        {
            "type": "buy",
            "text": "B",
            "data": buy_marks,
            "color": "#00E676"
        },
        {
            "type": "sell",
            "text": "S",
            "data": sell_marks,
            "color": "#FF5252"
        }
    ]
}
```

What this example teaches:

- indicators are computed first
- entries and exits are expressed as boolean signals
- fixed risk defaults are declared separately through `# @strategy`
- chart output is treated as a final rendering step, not mixed into signal logic

### 5.1 A platform-UI-aligned example

This version is closer to how developers actually use QuantDinger today:

- tune common values through `# @param`
- expose default stop / take-profit / entry sizing through `# @strategy`
- set `tradeDirection` explicitly so the saved strategy and backtest panel stay aligned
- keep leverage outside the code and let the product UI own it

```python
my_indicator_name = "Breakout Retest With Direction Control"
my_indicator_description = "Breakout-and-retest logic with platform-friendly params and default risk settings."

# signal_form: two_way
# exit_owner: engine
# flip_mode: R2

# @param breakout_len int 20 Breakout lookback bars
# @param retest_buffer float 0.002 Retest tolerance ratio
# @param volume_mult float 1.2 Minimum volume filter
# @param ema_filter_len int 50 Trend filter EMA length

# @strategy stopLossPct 0.02
# @strategy takeProfitPct 0.05
# @strategy entryPct 0.2
# @strategy trailingEnabled true
# @strategy trailingStopPct 0.015
# @strategy trailingActivationPct 0.03
# @strategy tradeDirection both

df = df.copy()

breakout_len = int(params.get('breakout_len', 20))
retest_buffer = float(params.get('retest_buffer', 0.002))
volume_mult = float(params.get('volume_mult', 1.2))
ema_filter_len = int(params.get('ema_filter_len', 50))

ema_filter = df['close'].ewm(span=ema_filter_len, adjust=False).mean()
range_high = df['high'].rolling(breakout_len).max().shift(1)
range_low = df['low'].rolling(breakout_len).min().shift(1)
volume_avg = df['volume'].rolling(breakout_len).mean()

long_breakout = df['close'] > range_high
long_retest_ok = df['low'] <= range_high * (1 + retest_buffer)
long_volume_ok = df['volume'] >= volume_avg * volume_mult
long_trend_ok = df['close'] > ema_filter

short_breakout = df['close'] < range_low
short_retest_ok = df['high'] >= range_low * (1 - retest_buffer)
short_volume_ok = df['volume'] >= volume_avg * volume_mult
short_trend_ok = df['close'] < ema_filter

raw_buy = long_breakout & long_retest_ok & long_volume_ok & long_trend_ok
raw_sell = short_breakout & short_retest_ok & short_volume_ok & short_trend_ok

buy = (raw_buy.fillna(False) & (~raw_buy.shift(1).fillna(False))).astype(bool)
sell = (raw_sell.fillna(False) & (~raw_sell.shift(1).fillna(False))).astype(bool)

df['buy'] = buy
df['sell'] = sell

buy_marks = [df['low'].iloc[i] * 0.995 if buy.iloc[i] else None for i in range(len(df))]
sell_marks = [df['high'].iloc[i] * 1.005 if sell.iloc[i] else None for i in range(len(df))]

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "EMA Filter",
            "data": ema_filter.fillna(0).tolist(),
            "color": "#1890ff",
            "overlay": True
        },
        {
            "name": "Range High",
            "data": range_high.fillna(0).tolist(),
            "color": "#52c41a",
            "overlay": True
        },
        {
            "name": "Range Low",
            "data": range_low.fillna(0).tolist(),
            "color": "#f5222d",
            "overlay": True
        }
    ],
    "signals": [
        {
            "type": "buy",
            "text": "L",
            "data": buy_marks,
            "color": "#00E676"
        },
        {
            "type": "sell",
            "text": "S",
            "data": sell_marks,
            "color": "#FF5252"
        }
    ]
}
```

Why this example maps cleanly to the UI:

- `# @param` values can be tuned by AI or by manual parameter editing workflows
- `# @strategy` defaults line up with saved strategy defaults and backtest-side risk settings
- `tradeDirection both` makes it obvious that the code is designed for long and short signals
- leverage is still controlled in the product panel instead of being hidden inside source code

---

## 6. When You Should Switch to ScriptStrategy

Move to `ScriptStrategy` when the strategy needs runtime state rather than pure dataframe signals.

Typical triggers:

- the stop-loss depends on the current open position rather than only on historical series
- you want to adjust stops after entry
- you need partial close or pyramiding
- you want different logic for first entry versus re-entry
- you need cooldown logic, execution throttling, or bot-style workflows

### 6.1 Required functions

The safest product-facing contract is:

- `def on_init(ctx): ...`
- `def on_bar(ctx, bar): ...`

Why this matters:

- the runtime compiler strictly requires `on_bar`
- some product-side validation paths still expect both `on_init` and `on_bar`
- to avoid validator/runtime mismatches, define both functions even if `on_init` only initializes state or writes a log line

### 6.2 Available objects

`bar` typically exposes:

- `bar.open`
- `bar.high`
- `bar.low`
- `bar.close`
- `bar.volume`
- `bar.timestamp`

`ctx` currently exposes:

- `ctx.param(name, default=None)`
- `ctx.bars(n=1)`
- `ctx.position`
- `ctx.balance`
- `ctx.equity`
- `ctx.log(message)`
- `ctx.buy(price=None, amount=None)`
- `ctx.sell(price=None, amount=None)`
- `ctx.close_position()`

Notes:

- `ctx` does not expose the full trading config object directly
- keep leverage, symbol, venue, and credentials in product configuration rather than hardcoding them into the script
- use `ctx.param(...)` for script-level defaults that belong in source code

`ctx.position` supports both numeric checks and field access patterns such as:

```python
if not ctx.position:
    ...

if ctx.position > 0:
    ...

if ctx.position["side"] == "long":
    ...
```

### 6.3 Script example with runtime exits

```python
def on_init(ctx):
    ctx.log("strategy initialized")


def on_bar(ctx, bar):
    stop_loss_pct = ctx.param("stop_loss_pct", 0.03)
    take_profit_pct = ctx.param("take_profit_pct", 0.06)
    order_amount = ctx.param("order_amount", 1)

    bars = ctx.bars(30)
    if len(bars) < 20:
        return

    closes = [b.close for b in bars]
    ma_fast = sum(closes[-10:]) / 10
    ma_slow = sum(closes[-20:]) / 20

    if not ctx.position and ma_fast > ma_slow:
        ctx.buy(price=bar.close, amount=order_amount)
        return

    if not ctx.position:
        return

    if ctx.position["side"] != "long":
        return

    entry_price = ctx.position["entry_price"]

    if bar.close <= entry_price * (1 - stop_loss_pct):
        ctx.close_position()
        return

    if bar.close >= entry_price * (1 + take_profit_pct):
        ctx.close_position()
        return

    if ma_fast < ma_slow:
        ctx.close_position()
```

Use this style when stop-loss and take-profit truly belong to runtime position management instead of pure indicator output.

Important sizing note:

- in the current system, saved-strategy backtests still derive position sizing primarily from normalized trading config such as `entryPct`
- treat `amount` in `ctx.buy()` / `ctx.sell()` as runtime order intent, not as the only source of truth for backtest sizing
- always verify actual exposure with a saved-strategy backtest before promoting the script to paper or live trading

### 6.4 Normal script mode vs bot mode

Most `ScriptStrategy` workflows run on **closed bars**:

- the engine evaluates `on_bar(ctx, bar)` after a bar is confirmed
- this is the best mental model for standard strategy backtests and bar-by-bar live execution

There is also a bot-style runtime mode in the current system:

- bot mode may feed `on_bar` with synthetic tick-like bars built from the latest price
- this is more suitable for grid, DCA, or other bot-style execution patterns
- if you write code intended for bot mode, test it separately from standard bar-close strategy behavior

### 6.5 A platform-live-oriented ScriptStrategy example

This example is closer to how a platform-facing live strategy is usually written:

- use `ctx.param(...)` for script defaults
- inspect `ctx.position` before deciding whether to open, reverse, reduce, or fully close
- use `ctx.buy()` / `ctx.sell()` for directional intent
- use `ctx.close_position()` when you want an explicit full exit

```python
def on_init(ctx):
    ctx.log("live strategy initialized")


def on_bar(ctx, bar):
    fast_len = int(ctx.param("fast_len", 10))
    slow_len = int(ctx.param("slow_len", 30))
    risk_pct = float(ctx.param("risk_pct", 0.25))
    stop_loss_pct = float(ctx.param("stop_loss_pct", 0.02))
    take_profit_pct = float(ctx.param("take_profit_pct", 0.05))
    allow_short = bool(ctx.param("allow_short", True))

    bars = ctx.bars(slow_len + 5)
    if len(bars) < slow_len:
        return

    closes = [b.close for b in bars]
    fast_ma = sum(closes[-fast_len:]) / fast_len
    slow_ma = sum(closes[-slow_len:]) / slow_len
    price = bar.close

    if not ctx.position:
        if fast_ma > slow_ma:
            ctx.buy(price=price, amount=risk_pct)
            return
        if allow_short and fast_ma < slow_ma:
            ctx.sell(price=price, amount=risk_pct)
            return
        return

    if ctx.position["side"] == "long":
        entry_price = float(ctx.position["entry_price"])
        if price <= entry_price * (1 - stop_loss_pct):
            ctx.close_position()
            return
        if price >= entry_price * (1 + take_profit_pct):
            ctx.close_position()
            return
        if allow_short and fast_ma < slow_ma:
            ctx.sell(price=price, amount=risk_pct)
            return

    if ctx.position["side"] == "short":
        entry_price = float(ctx.position["entry_price"])
        if price >= entry_price * (1 + stop_loss_pct):
            ctx.close_position()
            return
        if price <= entry_price * (1 - take_profit_pct):
            ctx.close_position()
            return
        if fast_ma > slow_ma:
            ctx.buy(price=price, amount=risk_pct)
            return
```

What this example demonstrates:

- `ctx.param(...)` keeps script defaults visible and editable in source
- `ctx.position` is the switch that separates flat / long / short behavior
- `ctx.buy()` and `ctx.sell()` express directional intent, not just "open long" or "open short" in isolation
- `ctx.close_position()` is the clearest choice when your rule means "exit everything now"

Backtest vs live differences you should remember:

- standard script backtests and normal live mode both think in confirmed-bar logic, but bot mode may call the script with synthetic tick-like bars
- `amount` is best treated as runtime order intent; saved-strategy backtests still size mainly from normalized trading config such as `entryPct`
- `ctx.sell()` while long, or `ctx.buy()` while short, may behave like a close-plus-reverse style intent depending on runtime state and product configuration
- if you want a guaranteed full flatten action, prefer `ctx.close_position()` over relying on implicit interpretation

---

## 7. Backtesting, Persistence, and Current Limits

Saved strategies are resolved by the backend into a normalized snapshot for backtesting and execution. Common fields include:

- `strategy_type`
- `strategy_mode`
- `strategy_code`
- `indicator_config`
- `trading_config`

Current run types include:

- `indicator`
- `strategy_indicator`
- `strategy_script`

Current limitations:

- cross-sectional strategies are not supported in the current strategy snapshot flow
- `ScriptStrategy` does not support `cross_sectional` live execution mode
- script-strategy backtests do not use the indicator MTF execution path
- strategy backtests expect a valid symbol and non-empty code

### 7.1 Script backtest fill assumptions (not indicator strict mode)

Script backtests have **no** indicator IDE strict/non-strict toggle. The UI label should read:

**Script standard · bar-by-bar · next-bar open fill**

1. Candles are replayed at the strategy timeframe; each closed bar calls `on_bar(ctx, bar)`.
2. Orders come from `ctx.buy()` / `ctx.sell()` / `ctx.close_position()` inside the script.
3. The simulator fills at the **next bar open** by default (plus slippage/fees), aligned with `execution.signalTiming = next_bar_open`.
4. Position size still mainly follows `entryPct` and related trading config, not `amount` alone.

### 7.2 Trading Bot vs ScriptStrategy vs IndicatorStrategy

| Type | Code storage | Entry | Notes |
|------|--------------|-------|-------|
| IndicatorStrategy | `qd_indicator_codes.code` | Indicator IDE | `df` + boolean signals |
| ScriptStrategy | `qd_strategies_trading.strategy_code` | Strategy Studio | `on_init` + `on_bar` |
| Trading Bot | same `strategy_code`, `strategy_mode=bot` | Bot wizard | Grid live logic is engine-side |

**Clone as Script** (bot detail) copies `strategy_code` into a new ScriptStrategy on **Strategy Studio**, not into Indicator IDE.

- **Grid bots** ship a placeholder script (`on_bar: pass`); the editor may look nearly empty by design.
- **Martingale / trend / DCA** bots generate full Python templates.
- If code is missing after clone, refresh and edit again — the app fetches `/api/strategies/detail` for the full `strategy_code`.

---

## 8. Best Practices

### 8.1 Avoid look-ahead bias

- use completed-bar information only
- prefer `shift(1)` for confirmation
- do not use `shift(-1)` in signal logic

### 8.2 Handle NaNs explicitly

Rolling and EWM calculations create leading NaNs. Clean them before signal generation.

### 8.3 Keep all series aligned

Every `plot['data']` and `signal['data']` list must match `len(df)` exactly.

### 8.4 Prefer vectorized indicator logic

For `IndicatorStrategy`, core calculations should be pandas-native whenever possible.

### 8.5 Keep runtime scripts deterministic

For `ScriptStrategy`, avoid hidden state outside `ctx`, avoid randomness, and make order intent explicit.

### 8.6 Put configuration in the right layer

- use `# @param` and `# @strategy` for indicator defaults
- use `ctx.param()` for script defaults
- keep leverage, signal timing, venue configuration, and credentials outside the strategy code

---

## 9. Troubleshooting

### 9.1 `column "strategy_mode" does not exist`

Your database schema is older than the running code. Apply the required migration on `qd_strategies_trading`.

### 9.2 `Strategy script must define on_bar(ctx, bar)`

Your `ScriptStrategy` code is missing the required handler.

### 9.3 `Missing required functions: on_init, on_bar`

The current UI verifier expects both functions to exist in the source text.

### 9.4 `Strategy code is empty and cannot be backtested`

The saved strategy does not contain valid code for the selected mode.

### 9.5 Marker or plot length mismatch

All chart output arrays must align exactly with the dataframe length.

### 9.6 Strategy behaves strangely in backtest

Check these first:

- did you accidentally use future data?
- are your `buy` / `sell` signals edge-triggered?
- are you mixing signal-driven exits with engine-driven exits without documenting it?
- are your `# @strategy` defaults aligned with the strategy idea?

### 9.7 Backend logs

If strategy creation, verification, backtest, or execution fails, check backend logs first. Common issue classes:

- schema mismatch
- invalid JSON or config payloads
- code verification failure
- market or symbol mismatch
- credential or exchange configuration issues

---

## 10. Full Workflow: Indicator IDE -> Saved Strategy -> Live Trading

This is the most practical product workflow for most teams.

### 10.1 Prototype in Indicator IDE

Start in the Indicator IDE when you are still shaping the idea:

1. Write indicator logic on `df`.
2. Declare tunable inputs with `# @param`.
3. Declare default risk settings with `# @strategy`.
4. Add chart-friendly `plots` and `signals`.
5. Run indicator-side backtests until the signal density and fills make sense.

At this stage, the goal is not to perfect live execution. The goal is to make the logic visible, testable, and easy to iterate.

### 10.2 Tune and validate in the product

Once the indicator behaves correctly:

1. Use the code quality checker to catch missing metadata or suspicious patterns.
2. Run backtests with realistic symbol, timeframe, commission, slippage, and leverage settings.
3. If needed, use AI tuning or structured tuning to compare parameter combinations.
4. Apply tuned values back into the code so the source remains the single visible truth.

Recommended mindset:

- let the code describe the signal logic
- let `# @param` and `# @strategy` describe tunable defaults
- let the panel own market, leverage, date range, and execution environment

### 10.3 Save the indicator as a strategy

Once the signal model is stable:

1. Save the current indicator code.
2. Create or save a strategy record from the product flow.
3. Confirm that the saved strategy snapshot has the expected mode, defaults, and trading config.
4. Run strategy backtests from the persisted record, not only from the raw editor state.

Why this matters:

- saved-strategy backtests are closer to the real execution path
- normalized snapshots may apply execution timing and trading-config defaults differently from the raw editor view
- this is the right place to catch symbol, mode, config, and persistence mismatches

### 10.4 Decide whether IndicatorStrategy is enough

Stay with `IndicatorStrategy` if:

- entries and exits are mainly signal-based
- fixed stop-loss / take-profit / trailing defaults are enough
- you do not need position-state-dependent runtime logic

Promote to `ScriptStrategy` if:

- the open position must be monitored bar by bar
- exits depend on current position state rather than only on historical series
- you need cooldowns, partial exits, scale-ins, or bot-like runtime behavior

### 10.5 Move to paper or live trading carefully

Before enabling live trading:

1. Verify exchange, broker, symbol, and credential configuration.
2. Recheck execution timing assumptions (`signal_mode` / `exit_signal_mode` vs backtest — §3.6.1).
3. Under `tradeDirection both`, confirm `buy` / `sell` flip semantics (§3.3.1) and avoid duplicate indicator + `trailingEnabled` exits (§11.7).
4. Confirm that leverage, direction, and sizing live in the right product configuration layer.
5. Start with conservative sizing and narrow symbol scope.
6. Review runtime logs and actual order behavior (including `server_trailing_stop` and close rejections) before scaling up.

Live trading should be treated as a separate validation stage, not as a continuation of editor-only experimentation.

---

## 11. Common Mistakes vs Correct Patterns

This section highlights the mistakes that most often cause misleading backtests, confusing strategy behavior, or product/runtime mismatches.

### 11.1 Declaring `# @param` but never reading it

Wrong:

```python
# @param fast_len int 20 Fast EMA length

df = df.copy()
fast_len = 20
ema_fast = df['close'].ewm(span=fast_len, adjust=False).mean()
```

Correct:

```python
# @param fast_len int 20 Fast EMA length

df = df.copy()
fast_len = int(params.get('fast_len', 20))
ema_fast = df['close'].ewm(span=fast_len, adjust=False).mean()
```

Why:

- declaring a param tells the product and AI tuning flow that the value is intended to be adjustable
- if the code never reads `params.get(...)`, the declaration becomes cosmetic and the quality checker may warn

### 11.2 Making `buy` / `sell` fire on every bar

Wrong:

```python
df['buy'] = df['close'] > ema_fast
df['sell'] = df['close'] < ema_fast
```

Correct:

```python
raw_buy = df['close'] > ema_fast
raw_sell = df['close'] < ema_fast

df['buy'] = (raw_buy.fillna(False) & (~raw_buy.shift(1).fillna(False))).astype(bool)
df['sell'] = (raw_sell.fillna(False) & (~raw_sell.shift(1).fillna(False))).astype(bool)
```

Why:

- repeated signals on every bar can distort entries, exits, and chart markers
- edge-triggered signals are usually closer to the intended strategy semantics

### 11.3 Writing leverage into the strategy source

Wrong:

```python
# @strategy leverage 10
```

Correct:

```python
# @strategy entryPct 0.2
# @strategy stopLossPct 0.02
# @strategy takeProfitPct 0.05
```

Then set leverage in the product panel or saved-strategy trading configuration.

Why:

- leverage belongs to execution configuration, not indicator metadata
- hiding leverage in code makes backtests harder to read and easier to misconfigure

### 11.4 Using `shift(-1)` and accidentally introducing look-ahead bias

Wrong:

```python
df['buy'] = (df['close'].shift(-1) > ema_fast).fillna(False)
```

Correct:

```python
raw_buy = df['close'] > ema_fast
df['buy'] = (raw_buy.fillna(False) & (~raw_buy.shift(1).fillna(False))).astype(bool)
```

Why:

- `shift(-1)` reaches into future data
- strategies that look amazing with future leakage usually collapse in real execution

### 11.5 Treating `ctx.buy(..., amount=...)` as absolute backtest size

Wrong mental model:

```python
ctx.buy(price=bar.close, amount=1.0)
```

"This guarantees the backtest always uses exactly 100% of capital."

Correct mental model:

```python
position_pct = float(ctx.param("risk_pct", 0.25))
ctx.buy(price=bar.close, amount=position_pct)
```

And then verify the saved-strategy backtest using the normalized trading config.

Why:

- in the current system, saved-strategy backtests still derive sizing mainly from normalized config such as `entryPct`
- `amount` is best treated as runtime order intent, not as the only source of truth for historical sizing

### 11.6 Using `ctx.sell()` or `ctx.buy()` when you really mean "fully flatten now"

Risky:

```python
if stop_hit:
    ctx.sell(price=bar.close, amount=0.25)
```

Clearer:

```python
if stop_hit:
    ctx.close_position()
```

Why:

- `ctx.buy()` / `ctx.sell()` express directional intent and may be interpreted through current position state
- if your rule means "exit everything now", `ctx.close_position()` is the least ambiguous choice

### 11.7 Mixing signal exits and engine exits without documenting it

Risky:

```python
# @strategy stopLossPct 0.02
# @strategy takeProfitPct 0.05
# @strategy trailingEnabled true

df['sell'] = some_other_exit_condition
# plus tp/sl flags merged into df['buy'] / df['sell']
```

Better (pick one primary exit path and comment it):

```python
# Option A: exits only from indicator signals (touch-based strategies)
# exit_owner: indicator
# @strategy trailingEnabled false
# Primary exit: close_* or tp/sl conditions inside df['buy'] / df['sell']
# stopLossPct / takeProfitPct / trailing* are not server-side exits

# Option B: exits from engine risk (fixed SL/TP/trailing)
# exit_owner: engine
# @strategy trailingEnabled true
# @strategy trailingStopPct 0.0025
# @strategy trailingActivationPct 0.0037
# df['buy'] / df['sell'] or open_* should focus on entries;
# structural reverse close_* is OK, tight in-indicator tp/sl is not
```

Why:

- duplicate exits produce `server_trailing_stop` next to indicator closes, timing skew, and sometimes `invalid amount (0.0)` when the position is already flat.
- `exit_owner: indicator` disables server-side fixed stop-loss, fixed take-profit, and trailing stop in backtest/live; those `# @strategy` exit fields no longer close the position.
- `exit_owner: engine` keeps server-side price risk active; indicator `close_*` signals should express structural reversals, not a second tight TP/SL system.
- the real problem is when nobody knows which exit path is supposed to dominate; `exit_owner` is the boundary.

### 11.8 Putting all tp/sl flags into `buy` / `sell` under `both`

```python
df['buy']  = entry_long | short_tp | short_sl
df['sell'] = entry_short | long_tp | long_sl
# @strategy tradeDirection both
```

Under `both`, `short_tp` inside `buy` is a **flip-long** intent, not flat-only. For flat-only exits, change `tradeDirection`, use four-way columns, or move to `ScriptStrategy`. Use `confirmed` signal modes if live fires earlier than backtests (§3.6.1).

### 11.9 Live log: `invalid amount (0.0) for close_*`

Common causes:

1. Server stop-loss / take-profit / trailing already closed the leg; an indicator `close_*` was still submitted.
2. Local DB lag vs the exchange — the worker retries **sync + exchange size** before rejecting.

Mitigation: §11.7 (one exit owner), §3.3.1 (`both` semantics). If the indicator owns TP/SL, use `# exit_owner: indicator`; if the engine owns price exits, do not also write tight in-indicator TP/SL booleans.

---

## 12. Platform Reference Sheet

Use this section as a fast "what is supported right now?" reference when writing strategy code.

### 12.1 `# @strategy` supported keys

| Key | Meaning | Typical Example | Notes |
|-----|---------|-----------------|-------|
| `stopLossPct` | Default stop-loss ratio | `# @strategy stopLossPct 0.02` | Engine-managed default risk setting |
| `takeProfitPct` | Default take-profit ratio | `# @strategy takeProfitPct 0.05` | Engine parser is more permissive than the toy examples |
| `entryPct` | Default capital allocation ratio | `# @strategy entryPct 0.25` | Common source of backtest sizing |
| `trailingEnabled` | Enable trailing stop logic | `# @strategy trailingEnabled true` | Boolean |
| `trailingStopPct` | Trailing stop ratio | `# @strategy trailingStopPct 0.015` | Used with trailing enabled |
| `trailingActivationPct` | Profit threshold before trailing activates | `# @strategy trailingActivationPct 0.03` | Used with trailing enabled |
| `tradeDirection` | Direction filter | `# @strategy tradeDirection both` | `long`, `short`, or `both` |

Important:

- these keys are for indicator-side strategy defaults
- **all values use 0–1 decimal ratios** (same as `StrategyConfigParser`, backtest, and live)
- **stop/take-profit/trailing thresholds are underlying price moves, not divided by leverage**
- **`entryPct 1` means 100% of available capital**, not 1%
- with `# exit_owner: indicator`, `stopLossPct` / `takeProfitPct` / `trailing*` do not trigger server-side closes in backtest or live; exits come from indicator signals
- with `# exit_owner: engine`, those server-side price-risk fields are active
- do not put `leverage` in `# @strategy`
- keep exchange, symbol, credentials, and leverage in product configuration

### 12.2 Contract header comments

| Key | Values | Meaning |
|-----|--------|---------|
| `signal_form` | `two_way` / `four_way` | Execution signal form |
| `exit_owner` | `indicator` / `engine` | Price-exit owner; `layered` is not supported |
| `flip_mode` | `R1` / `R2` | Flip timing: R1 opens on the next bar; R2 closes then opens on the same bar |

Recommended top-of-file block:

```python
# signal_form: four_way
# exit_owner: indicator
# flip_mode: R1
```

### 12.3 `# @param` quick format

| Part | Example | Meaning |
|------|---------|---------|
| Name | `fast_len` | Parameter key |
| Type | `int` / `float` / `bool` / `str` / `string` | Supported types |
| Default | `20` | Default value shown to the system |
| Description | `Fast EMA length` | Human-readable hint |

Example:

```python
# @param fast_len int 20 Fast EMA length
# @param allow_short bool true Allow short entries
```

And then read them with:

```python
fast_len = int(params.get('fast_len', 20))
allow_short = bool(params.get('allow_short', True))
```

### 12.3 `ctx` methods and fields for `ScriptStrategy`

| Item | Type | Meaning |
|------|------|---------|
| `ctx.param(name, default)` | method | Read or initialize script-level defaults |
| `ctx.bars(n=1)` | method | Get recent bars up to the current runtime index |
| `ctx.log(message)` | method | Write strategy log messages |
| `ctx.buy(price=None, amount=None)` | method | Express buy / long-side intent |
| `ctx.sell(price=None, amount=None)` | method | Express sell / short-side intent |
| `ctx.close_position()` | method | Explicitly flatten current position |
| `ctx.position` | field | Current position object |
| `ctx.balance` | field | Runtime balance snapshot |
| `ctx.equity` | field | Runtime equity snapshot |

`ctx.position` common fields:

| Field | Meaning |
|-------|---------|
| `side` | `long`, `short`, or empty when flat |
| `size` | Current position size |
| `entry_price` | Average entry price |
| `direction` | `1`, `-1`, or `0` |
| `amount` | Runtime amount mirror |

### 12.4 `bar` fields for `ScriptStrategy`

| Field | Meaning |
|-------|---------|
| `bar.open` | Open price |
| `bar.high` | High price |
| `bar.low` | Low price |
| `bar.close` | Close price |
| `bar.volume` | Volume |
| `bar.timestamp` | Time value from runtime feed |

### 12.5 `output` structure for `IndicatorStrategy`

Top-level structure:

```python
output = {
    "name": my_indicator_name,
    "plots": [],
    "signals": [],
    "calculatedVars": {}
}
```

Supported top-level keys:

| Key | Required | Meaning |
|-----|----------|---------|
| `name` | recommended | Display name |
| `plots` | recommended | Chart series output |
| `signals` | recommended | Buy/sell marker output |
| `calculatedVars` | optional | Extra metadata or computed values |

Each `plot` item commonly contains:

| Key | Meaning |
|-----|---------|
| `name` | Plot label |
| `data` | List aligned to `len(df)` |
| `color` | Display color |
| `overlay` | Whether to draw on price chart |
| `type` | Optional rendering hint |

Each `signal` item commonly contains:

| Key | Meaning |
|-----|---------|
| `type` | `buy` or `sell` |
| `text` | Marker label |
| `color` | Marker color |
| `data` | List aligned to `len(df)`, using `None` where no marker exists |

### 12.6 Fast reminders

- `df['buy']` and `df['sell']` should be boolean and length-aligned
- prefer edge-triggered signals
- avoid `shift(-1)` in signal logic
- prefer `ctx.close_position()` when the rule clearly means "exit everything now"
- treat `amount` as runtime order intent, then verify sizing with saved-strategy backtests

---

## 13. Recommended Development Workflow

1. Prototype the idea as an `IndicatorStrategy`.
2. Validate plots, signal density, and next-bar-open backtest behavior.
3. Add clear `# @param` and `# @strategy` metadata.
4. Decide explicitly whether exits are signal-managed or engine-managed.
5. Save the strategy and run strategy backtests from the persisted record.
6. Promote to `ScriptStrategy` only when you truly need runtime position logic.
7. Move to paper or live trading only after configuration, credentials, and market semantics are verified.

