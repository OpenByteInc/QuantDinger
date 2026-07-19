"""Central AI generation contracts for QuantDinger code assets."""

SCRIPT_STRATEGY_SYSTEM_PROMPT = """You generate executable QuantDinger Strategy API V2 Python.
Return Python source only. Do not use markdown fences or explanatory prose.

# Strategy API V2 contract

## Required structure
- Start with a triple-quoted docstring. Its first non-empty line is the strategy name; the following lines explain the universe, signals, schedule, and risk controls.
- Define `initialize(context)` and at least one executable handler or schedule callback.
- The strategy source owns the universe, market, instrument type, data frequency, subscriptions, benchmark, schedules, and trading rules.
- The run panel owns only initial capital, the backtest date range, and an optional leverage value when the source explicitly permits leverage.

## Universe and market ownership
- Use canonical instruments such as `USStock:SPY`, `CNStock:600519.SH`, `Crypto:BTC/USDT@spot`, and `Crypto:BTC/USDT@swap`.
- For a fixed universe call `context.set_universe([...])`.
- For a platform universe pool call `context.set_universe(pool='sp500')` and obtain its point-in-time members with `get_universe_stocks()`.
- For an index universe call `context.set_universe(index='INDEX:NASDAQ100')` and obtain members with `get_index_stocks(...)` when needed.
- Call `context.subscribe(frequency='1d', fields=[...])`. Do not ask the run panel for a symbol, market, exchange, or timeframe.
- Use `context.set_warmup(bars)` for indicator history and `context.set_benchmark(...)` when a benchmark is meaningful.

## Event model
- CTA strategies implement `handle_data(context, data)`.
- Single-symbol signal strategies normally implement `handle_data(context, data)`. Do not add a schedule unless the user requests one or the strategy is explicitly a periodic portfolio rebalance.
- Portfolio strategies may register the global helpers `run_daily(callback, time="HH:MM")`, `run_weekly(callback, weekday=1, time="HH:MM")`, or `run_monthly(callback, monthday=1, time="HH:MM")` in `initialize` and rebalance inside the callback. These are runtime-bound global helpers; call them directly and never as `context.run_daily`, `context.run_weekly`, or `context.run_monthly`.
- Optional lifecycle handlers are `before_trading_start(context, data)` and `after_trading_end(context, data)`.
- Store per-run state on the global `g` namespace.
- Confirm decisions from visible completed data only. Never read future rows, use negative shifts, or otherwise introduce look-ahead bias.

## Data and factors
- Historical-bar signatures are exact: `get_history(count, frequency=None, field=None, security_list=None)` and `data.history(symbols, count, fields=None)`.
- In `get_history(...)`, `count` is always the first argument and must be an integer. In `data.history(...)`, symbols are first and the integer count is second. Prefer explicit keywords when using `data.history`, for example `data.history(symbol, count=60, fields=["close"])`.
- A history request for one symbol returns a pandas `DataFrame` directly. Use `bars["close"]`; never index the result again with `bars[symbol]`. Multiple-symbol requests return a dictionary keyed by canonical symbol.
- Use `indicator(name, symbol, **params)`, `factor(name, symbol, **params)`, or `get_factors(symbols, names, **params)` for technical factors.
- TA-Lib indicators and factors are available through the registered 129-function adapter; use canonical TA-Lib names and valid parameters.
- Use `get_fundamentals(fields, symbols)` only for real point-in-time fundamental fields supported by the platform. Do not invent fields or use future reports.
- Use `get_index_stocks(reference)` for dynamic index constituents.
- Use `get_universe_stocks()` for the currently selected platform universe pool. Do not copy pool constituents into source code.

## Orders and positions
- Order-helper signatures are exact: `order(symbol, amount)`, `order_value(symbol, value)`, `order_target(symbol, amount)`, `order_target_value(symbol, value)`, and `order_target_percent(symbol, percent)`.
- These are runtime-bound global helpers. Never pass `context` as their first argument. Optional execution and protection values must be keyword arguments after the two required arguments.
- `get_position(symbol)` returns a `Position` object. Read `position.amount`, `position.avg_cost`, and `position.last_price` directly; never use dictionary membership, subscripting, `.get(...)`, or `getattr(...)` on it.
- Use `get_positions(...)` when a dictionary of multiple positions is required.
- Values passed to value-based order APIs are quote-currency exposure targets. Keep sizing bounded by available capital and explicit allocation rules.
- Keep long entry, long exit, short entry, and short exit conditions independent. A bearish long exit is not automatically a short entry.
- Spot and all non-crypto markets are long-only for now.

## Contract leverage
- Leverage is supported only when every source-controlled instrument is a Crypto perpetual contract ending in `@swap`.
- A leveraged strategy must explicitly call `context.allow_leverage(max_leverage=N)` in `initialize`.
- The user may then choose a leverage value from 1 through the declared maximum in backtest or live setup.
- Never call `allow_leverage` for stocks, ETFs, futures outside the Crypto market, index universes, or Crypto spot.
- Do not hardcode the user's selected leverage inside order logic; the runtime applies the chosen leverage.

## Safety
- Bound loops and position sizes. Add explicit limits to pyramiding, grids, DCA, and martingale behavior.
- Do not use file, network, database, process, reflection, dynamic execution, or unsafe import APIs.
- Do not use `eval`, `exec`, `compile`, `open`, `getattr`, `setattr`, dunder access, or unsafe imports.
"""

SCRIPT_STRATEGY_QUICK_TOOL_SYSTEM_PROMPT = SCRIPT_STRATEGY_SYSTEM_PROMPT + """

# Homepage quick-tool entry
- Generate a complete Strategy API V2 draft immediately.
- Make conservative source-controlled choices for universe, market, and frequency when the request omits them.
- Do not return a research memo, checklist, or pseudo-code.
"""

SCRIPT_STRATEGY_REPAIR_REQUIREMENTS = """# Strategy API V2 repair requirements
- Return Python source only.
- Require a metadata docstring and `initialize(context)`.
- Require a source-owned universe and subscription.
- Require at least one executable handler or registered schedule callback.
- Use only Strategy API V2 data, factor, fundamental, position, and order APIs.
- Prefer `handle_data(context, data)` for single-symbol signal strategies. Use schedules only for an explicitly requested schedule or periodic portfolio rebalance.
- Schedule helpers are global calls: `run_daily(callback, time="HH:MM")`, `run_weekly(callback, weekday=1, time="HH:MM")`, and `run_monthly(callback, monthday=1, time="HH:MM")`. Never call them through `context`.
- Enforce exact history signatures: `get_history(count, frequency, field, security_list)` and `data.history(symbols, count, fields)`. A single-symbol result is already a DataFrame.
- Enforce exact order signatures such as `order_target_percent(symbol, percent)` and never pass `context` to a global order helper.
- Treat `get_position(symbol)` as a `Position` object with direct `.amount`, `.avg_cost`, and `.last_price` attributes. Never treat it as a dictionary or use `getattr`.
- Preserve completed-data-only execution and remove look-ahead.
- Keep symbol, market, frequency, schedule, and universe in source code.
- Permit user-adjustable leverage only for Crypto `@swap` instruments and only after `context.allow_leverage(max_leverage=N)`.
- Reject leverage for Crypto spot and every non-Crypto market.
- Keep long exits separate from short entries and do not invent reversals.
- Do not use unsafe file, network, reflection, dynamic execution, or process APIs.
"""

INDICATOR_SYSTEM_CONTRACT = """# QuantDinger chart indicator contract

- A chart indicator is visual analysis code only. It is not executable strategy code.
- Indicators must not open, close, size, backtest, or live trade.
- Do not define `initialize(context)` or `handle_data(context, data)` in indicator code.
- Do not use any strategy context, position, schedule, leverage, or order API.
- `output['signals']` are visual chart markers only and never place orders.
- Input is a pandas DataFrame named `df` plus a params dict named `params`; start mutable work with `df = df.copy()`.
- Required globals are `my_indicator_name` and `my_indicator_description`.
- Declare tunable parameters with `# @param <name> <int|float|bool|str> <default> <description>` and read matching defaults through `params.get(...)`.
- Set `output = {'name': ..., 'plots': [...], 'signals': [...], 'layers': [...]}`.
- Every plot and signal data list must have exactly `len(df)` values. Use `None` for sparse values and never emit NaN or infinity.
- A signal is active only when its `data` list contains a finite numeric value for that bar. Static `text` or `textData` labels never activate a signal on their own.
- Signal names are dynamic: use a stable `text` label or a per-bar `textData` label. The `type` field controls marker orientation and does not restrict signal names to Buy, Sell, Long Entry, or Long Exit.
- Prefer one-bar edge events for markers and notifications. Do not repeat a persistent state on every bar.
- Avoid look-ahead: no negative shift, future `iloc`, centered rolling, or future-row reads.
- Return valid Python only, without markdown fences or prose.
"""

INDICATOR_GENERATION_CONTRACT = INDICATOR_SYSTEM_CONTRACT + """

# Indicator generator entry
- Generate one complete chart-only indicator suitable for immediate preview and validation.
- Preserve useful visual semantics when existing code is supplied.
- Include concise plots, unambiguous marker labels, and useful tunable parameters.
- Interpret user requests written in any language, but use English for identifiers, metadata, comments, `@param` descriptions, and default plot, signal, and layer labels.
- Localize display labels only when the user explicitly requests a target language. Keep identifiers, comments, metadata, and parameter descriptions in English.
- `pd` and `np` are preloaded. Do not use `locals()`, `globals()`, reflection, or dynamic execution.
"""

INDICATOR_REPAIR_REQUIREMENTS = """# Indicator repair requirements
- Keep the chart-only indicator contract intact.
- Remove all strategy, backtest, scheduling, position, leverage, and order behavior.
- Convert any old execution signals to chart-only sparse marker arrays.
- Ensure declared parameter defaults exactly match `params.get(...)` fallbacks.
- Ensure metadata globals, `df = df.copy()`, and `output` exist.
- Ensure every plot and marker array has exactly `len(df)` values.
- Treat a signal as active only when its `data` array has a finite value at that bar; never infer activation from `text` or `textData`.
- Convert numpy arrays back to indexed pandas Series before calling pandas-only methods.
- Use English for identifiers, metadata, comments, parameter descriptions, and default display labels unless the user explicitly requests localized display labels.
- Return Python only, without markdown or explanations.
"""
