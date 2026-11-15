# QuantDinger Indicator Development Guide

> Applies to: the current QuantDinger chart-indicator contract
> Audience: first-time indicator authors, developers migrating Pine/TDX formulas, and reviewers of AI-generated code

A QuantDinger indicator is a Python chart program that runs in the Indicator IDE. It reads the current K-line DataFrame, calculates aligned series, and returns plots, markers, and sparse chart layers through <code>output</code>.

The most important boundary is: **an indicator is for chart analysis, not trade execution.**

An indicator cannot place orders, backtest, run live trading, read an account, manage positions, enable leverage, or execute stop-loss/take-profit rules. To trade an idea, convert its visual signals into a Strategy API V2 strategy, then verify, backtest, and deploy that strategy separately.

The TradingView/Pine compatibility target is: **preserve the calculation and visual semantics of common single-symbol, single-timeframe indicators, not Pine syntax, every Pine API, or Pine's tick-by-tick execution model.** Pine migrations must rewrite formulas in Python/pandas and return the result through the <code>output</code> contract in this guide.

---

## 1. Build a minimal indicator first

Paste this into the Indicator IDE and run it:

~~~python
my_indicator_name = "Close Line"
my_indicator_description = "Displays the close price as a chart overlay."

df = df.copy()

close_line = [
    None if pd.isna(value) else float(value)
    for value in df["close"]
]

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Close",
            "data": close_line,
            "color": "#3B82F6",
            "type": "line",
            "overlay": True,
        }
    ],
    "signals": [],
    "layers": [],
}
~~~

This is the complete minimal contract:

1. Declare display metadata.
2. Create a working copy with <code>df = df.copy()</code>.
3. calculate data aligned one-to-one with the K-line rows.
4. Set the <code>output</code> dictionary.

For a new indicator, first draw one line, then add parameters, then event markers, and only then add advanced layers.

---

## 2. Indicator, strategy, and conversion boundaries

| Artifact | Owns | Does not own |
| --- | --- | --- |
| Chart Indicator | plots, panes, lamp rows, visual markers, zones, labels | backtest, live trading, orders, positions, leverage, execution risk |
| Strategy API V2 | subscriptions, signals, order intents, positions, backtest, live execution, protections | Indicator IDE <code>output</code> rendering |
| Indicator-to-Strategy | translating visual signal meaning into executable code | mixing order behavior into the source indicator |

<code>output["signals"]</code> contains chart markers only. A <code>sell</code>-oriented “Death” marker may mean a long-exit warning or weakening conditions. It does not automatically open a short or reverse a position.

For a long-only conversion, the usual mapping is:

- explicit bullish entry event → <code>open_long</code>
- explicit bearish exit event → <code>close_long</code>
- generate <code>open_short</code> only when the user explicitly requests shorting and provides a distinct bearish entry rule

Do not create execution columns such as <code>open_long</code>, <code>close_long</code>, <code>open_short</code>, <code>close_short</code>, <code>add_long</code>, or <code>reduce_long</code> in an indicator. Do not use legacy <code># @strategy</code> annotations.

---

## 3. Runtime and input data

The runtime provides:

- <code>df</code>: a pandas DataFrame for the current chart, ordered oldest to newest, one row per bar.
- <code>params</code>: a dict produced by merging declared defaults with values from the parameter panel.
- <code>pd</code>: preloaded pandas.
- <code>np</code>: preloaded numpy.
- <code>open</code>, <code>high</code>, <code>low</code>, <code>close</code>, and <code>volume</code>: some runtime entries also expose convenience Series. Prefer explicit <code>df["close"]</code> access for clarity and portability.

Standard OHLCV access:

~~~python
open_price = df["open"]
high = df["high"]
low = df["low"]
close = df["close"]
volume = df["volume"]
~~~

Important rules:

- Do not assume a <code>time</code> column exists; time may already be the DataFrame index.
- Do not rename or remove required OHLCV columns.
- Check before using optional fields, for example <code>if "turnover" in df.columns:</code>.
- Run <code>df = df.copy()</code> before mutations.
- Prefer vectorized <code>rolling</code>, <code>ewm</code>, <code>shift</code>, and <code>where</code> operations for core series.

---

## 4. Recommended file structure and metadata

~~~python
# @param period int 20 Calculation period

my_indicator_name = "Example Indicator"
my_indicator_description = "Explains what is drawn and how events are marked."

df = df.copy()

period = int(params.get("period", 20))

# Helper functions
# Series calculation
# Marker construction

output = {
    "name": my_indicator_name,
    "plots": [],
    "signals": [],
    "layers": [],
}
~~~

Every indicator should declare:

~~~python
my_indicator_name = "Dual EMA Viewer"
my_indicator_description = "Chart-only EMA crossover indicator with visual event markers."
~~~

Keep the name short and stable. The description should say:

- what is calculated;
- whether it appears on the price chart or a separate pane;
- what each event marker means;
- which parameters matter.

Do not promise returns or imply live validation.

Project source rules require English identifiers, metadata, comments, parameter descriptions, and default display labels. Localize display text only when a user explicitly requests a target language.

---

## 5. Declaring parameters

Syntax:

~~~python
# @param <name> <int|float|bool|str> <default> <description>
~~~

Example:

~~~python
# @param fast_len int 12 Fast EMA period
# @param slow_len int 26 Slow EMA period
# @param band_pct float 1.5 Channel width percent
# @param show_marks bool true Show crossover markers
# @param source str close Price source
~~~

A declaration does not create a Python variable. Read every value explicitly:

~~~python
fast_len = int(params.get("fast_len", 12))
slow_len = int(params.get("slow_len", 26))
band_pct = float(params.get("band_pct", 1.5))
show_marks = bool(params.get("show_marks", True))
source = str(params.get("source", "close"))
~~~

Contract rules:

- Declare one parameter per line.
- Use valid Python identifiers for names.
- Use only <code>int</code>, <code>float</code>, <code>bool</code>, <code>str</code>, or <code>string</code>.
- The declared default must match the <code>params.get</code> fallback after type conversion.
- Write booleans as <code>true</code>/<code>false</code> in declarations and <code>True</code>/<code>False</code> in Python.
- A string default cannot contain spaces because it is parsed as one token.

Declare search candidates at the end of the description:

~~~python
# @param period int 20 Lookback period range=5:60:5
# @param multiplier float 2.0 Band multiplier values=1.5,2.0,2.5,3.0
~~~

<code>range=start:end:step</code> produces an inclusive arithmetic sequence. <code>values=a,b,c</code> declares an explicit list. One parameter expands to at most 1,024 values. Range markers are removed from the human-facing description.

Indicator parameters control calculations and display only. Do not declare account, symbol, timeframe, position, leverage, stop-loss, or take-profit settings.

---

## 6. The output contract

The program must finish by setting a dict named <code>output</code>:

~~~python
output = {
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
}
~~~

Optional:

~~~python
output["calculatedVars"] = {}
~~~

Validation rules:

- <code>output</code> must be a dict.
- At least the <code>plots</code> or <code>signals</code> key must exist.
- Every <code>plot["data"]</code> must have length <code>len(df)</code>.
- Every <code>signal["data"]</code> must have length <code>len(df)</code>.
- Do not emit NaN, positive infinity, or negative infinity; use <code>None</code> for missing points.
- Layers do not need per-bar arrays, but indices, times, and prices must be meaningful for the current data.

Explicit empty lists are recommended:

~~~python
output = {
    "name": my_indicator_name,
    "plots": [],
    "signals": [],
    "layers": [],
}
~~~

---

## 7. plots: price overlays and pane series

Each plot normally contains:

| Field | Type | Meaning |
| --- | --- | --- |
| <code>name</code> | str | legend and series name |
| <code>data</code> | list | values/<code>None</code>, length <code>len(df)</code> |
| <code>color</code> | str | preferably <code>#RRGGBB</code> |
| <code>overlay</code> | bool | <code>True</code> on price chart, <code>False</code> in a pane |
| <code>type</code> | optional str | <code>line</code>, <code>bar</code>, <code>circle</code>, or <code>cloud</code>; see aliases below |

Public plot types:

| Input | Normalized result | Typical Pine equivalent |
| --- | --- | --- |
| <code>line</code> | connected line | <code>plot(..., style=plot.style_line)</code> |
| <code>bar</code>, <code>histogram</code>, <code>column</code> | columns from a baseline | histogram/columns |
| <code>circle</code>, <code>dot</code>, <code>point</code>, <code>scatter</code> | circles | circles/scatter |
| <code>lamp</code> | lamp dots; three or more lamp series in one pane are arranged into rows | an approximation for state panels |
| <code>cloud</code>, <code>band</code>, <code>fill_between</code>, <code>fill-between</code> | filled range between an upper and lower series | <code>fill(plot1, plot2)</code> |

Style fields apply by figure type:

- Common: <code>color</code>, <code>opacity</code>.
- Line: <code>lineWidth</code> and <code>lineStyle</code>/<code>style</code>, such as <code>solid</code> or <code>dashed</code>.
- Bar and circle: <code>size</code>/<code>radius</code>, <code>borderColor</code>, and <code>borderSize</code>; bars also accept <code>baseValue</code>.
- Cloud: each point uses <code>upper</code> and <code>lower</code>; <code>color</code> may change per point. Use <code>opacity</code> between <code>0.02</code> and <code>0.30</code>. Set <code>title=False</code> to hide the cloud from the legend.
- All <code>overlay=False</code> plots from one indicator share one pane. They do not automatically create several panes as separate Pine scripts would.

For Pine-like series colors or dynamic point sizes, a <code>data</code> entry may be an object instead of a scalar:

~~~python
dynamic_points = [
    None,
    {"value": 102.5, "color": "#22C55E", "size": 4},
    {"value": 101.8, "color": "#EF4444", "size": 6},
]
~~~

Value aliases are <code>value</code>, <code>y</code>, and <code>data</code>; color aliases are <code>color</code>, <code>fillColor</code>, and <code>backgroundColor</code>; size aliases are <code>size</code>, <code>radius</code>, and <code>r</code>. New code should use the canonical <code>value/color/size</code> fields.

### Filled clouds and bands

A cloud point is an object with <code>upper</code> and <code>lower</code>. The list must still have <code>len(df)</code> entries. Use <code>None</code> where the band should stop; this prevents a neutral or warm-up region from being connected across a gap. The renderer orders the two values automatically, so the cloud remains valid when two lines cross.

~~~python
# @param cloud_opacity float 0.10 Cloud opacity range=0.03:0.24:0.01

cloud_opacity = min(0.24, max(0.03, float(params.get("cloud_opacity", 0.10))))
trend_cloud = []

for index in range(len(df)):
    fast_value = ema_fast.iloc[index]
    slow_value = ema_slow.iloc[index]

    if pd.isna(fast_value) or pd.isna(slow_value) or not bool(trending.iloc[index]):
        trend_cloud.append(None)
        continue

    trend_cloud.append(
        {
            "upper": float(max(fast_value, slow_value)),
            "lower": float(min(fast_value, slow_value)),
            "color": "#22C55E" if fast_value >= slow_value else "#F43F5E",
        }
    )

plots = [
    {
        "name": "Trend cloud",
        "title": False,
        "data": trend_cloud,
        "color": "#22C55E",
        "type": "cloud",
        "overlay": True,
        "opacity": cloud_opacity,
    },
]
~~~

The canonical fields are <code>upper/lower/color</code>. Compatibility aliases for the upper value are <code>top</code>, <code>high</code>, <code>value</code>, <code>y</code>, and <code>data</code>; aliases for the lower value are <code>bottom</code> and <code>low</code>. Prefer the canonical fields in new indicators.

~~~python
plots = [
    {
        "name": "EMA Fast",
        "data": fast_values,
        "color": "#22C55E",
        "type": "line",
        "overlay": True,
    },
    {
        "name": "RSI",
        "data": rsi_values,
        "color": "#8B5CF6",
        "type": "line",
        "overlay": False,
    },
]
~~~

Moving averages, Bollinger bands, and price channels normally overlay the main chart. RSI, MACD, and lamp rows normally use separate panes.

Use one missing-value conversion helper:

~~~python
def to_plot_list(series):
    return [
        None if pd.isna(value) else float(value)
        for value in series
    ]
~~~

Do not fill warm-up gaps in price overlays with zero. It draws misleading lines from zero to the actual market price.

---

## 8. signals: sparse visual events

~~~python
signals = [
    {
        "type": "buy",
        "text": "Long Entry",
        "color": "#22C55E",
        "data": entry_marks,
    },
    {
        "type": "sell",
        "text": "Long Exit",
        "color": "#EF4444",
        "data": exit_marks,
    },
]
~~~

Rules:

- <code>type</code> is commonly <code>buy</code> or <code>sell</code>. It controls marker orientation, not the signal name.
- <code>text</code> is the stable name. Optional <code>textData</code> can provide a per-bar label.
- The most stable format for both rendering and notification monitoring is a finite, non-zero marker price in <code>data[i]</code>.
- <code>text</code> and <code>textData</code> never activate a signal by themselves.
- Empty positions must contain real <code>None</code> values.
- Mark one-bar events by default instead of repeating a persistent state.

Compatibility formats also accept <code>True</code>, or <code>{"active": True, "price": ..., "text": ..., "color": ...}</code>. When a boolean has no price, the renderer anchors it around the current candle's high or low. For consistent preview, saved signal monitoring, and future clients, new indicators should prefer the “price or <code>None</code>” form and use point objects only when per-point text or color is required.

<code>renderMode</code> controls how persistent conditions become markers:

| Value | Behavior |
| --- | --- |
| <code>events</code> (explicit event mode) | every non-empty event point is rendered and can notify |
| <code>points</code> / <code>markers</code> / <code>raw</code> | explicitly preserve every active point |
| <code>state</code> / <code>continuous</code> / <code>condition</code> | render and notify only on the False → True edge |

When the mode is omitted, the runtime identifies high-density active series as persistent state and applies edge behavior. Public indicators should not depend on that heuristic; generate sparse events directly or set <code>renderMode</code> explicitly.

Convert a state into its rising edge:

~~~python
def edge(condition):
    current = condition.fillna(False).astype(bool)
    previous = current.shift(1, fill_value=False).astype(bool)
    return current & ~previous
~~~

Build marker-price lists:

~~~python
entry_event = edge(ema_fast > ema_slow)
exit_event = edge(ema_fast < ema_slow)

entry_marks = [
    float(df["low"].iloc[i] * 0.995)
    if bool(entry_event.iloc[i])
    else None
    for i in range(len(df))
]

exit_marks = [
    float(df["high"].iloc[i] * 1.005)
    if bool(exit_event.iloc[i])
    else None
    for i in range(len(df))
]
~~~

For “show on the next bar after confirmation”:

~~~python
confirmed_entry = edge(raw_entry).shift(
    1,
    fill_value=False,
).astype(bool)
~~~

This moves a completed event later; it does not read future data.

---

## 9. layers: zones, lines, and labels

Prefer plots and signals for normal indicators. Add layers only for supply/demand zones, support/resistance, channels, invalidation levels, or structure labels that materially improve readability.

Zone:

~~~python
{
    "type": "zone",
    "startIndex": 120,
    "endIndex": 180,
    "top": 105.2,
    "bottom": 101.8,
    "text": "Demand",
    "fillColor": "#22C55E",
    "borderColor": "#22C55E",
    "opacity": 0.12,
}
~~~

Horizontal line:

~~~python
{
    "type": "line",
    "startIndex": 100,
    "endIndex": len(df) - 1,
    "price": 98.5,
    "text": "Support",
    "color": "#F59E0B",
    "dashed": True,
}
~~~

For a sloped line, replace <code>price</code> with <code>startPrice</code> and <code>endPrice</code>. Label:

~~~python
{
    "type": "label",
    "index": len(df) - 1,
    "price": float(df["close"].iloc[-1]),
    "text": "Trend Weakens",
    "color": "#EF4444",
    "textColor": "#FFFFFF",
}
~~~

Indices are the most stable representation for the current <code>df</code>. Matching <code>startTime</code>, <code>endTime</code>, and <code>time</code> values are also supported.

Layers remain visual objects. They do not represent real orders, positions, or hosted stops.

---

## 10. TradingView/Pine capability mapping

QuantDinger does not execute Pine source. Migration means preserving indicator meaning, numerical timing, and visual expression with Python/pandas. This table is the current contract boundary:

| TradingView/Pine capability | Current status | QuantDinger equivalent or limit |
| --- | --- | --- |
| OHLCV, history references, rolling windows | Supported | <code>df</code>, <code>shift</code>, <code>rolling</code>, <code>ewm</code> |
| <code>input.int/float/bool/string</code> | Core semantics supported | <code># @param</code>; Pine group, inline, confirm, and dedicated color/source/timeframe/symbol controls are not available |
| Common <code>ta.*</code> indicators | Calculation semantics are implementable | use pandas/numpy; the indicator runtime has no Pine-compatible <code>ta</code> namespace, so smoothing, warmup, and NaN behavior must be verified |
| <code>plot()</code>, histogram, circles | Common subset supported | line/bar/circle plots, overlay/pane placement, and per-point colors/sizes |
| <code>fill()</code> between two plots | Supported | use a <code>cloud</code> plot with per-point <code>upper</code>/<code>lower</code> values and optional per-point color |
| <code>plotshape()</code>, <code>plotchar()</code> | Main event semantics supported | use <code>signals</code>; use <code>textData</code> or point objects for dynamic text |
| <code>hline()</code>, line, box, label | Common static results supported | line/zone/label layers; each run returns a final object list, without Pine object IDs, mutation methods, or garbage-collection semantics |
| <code>alert()</code>, <code>alertcondition()</code> | Partially supported | emit <code>signals</code>, then configure monitoring after saving; indicator code does not directly notify or trade |
| arrays, maps, user functions, state calculations | Python equivalents available | use list/dict, functions, and pandas Series; calculation is vectorized over the DataFrame, not Pine's per-bar <code>var</code>/<code>varip</code> model |
| linefill, <code>bgcolor()</code>, <code>barcolor()</code> | No first-class equivalent yet | a fixed range may be approximated with a zone; this is not full background or candle-color compatibility |
| area/step/cross/arrow, <code>plotcandle()</code>/<code>plotbar()</code> | Not supported yet | an unknown <code>type</code> currently falls back to line; do not rely on that fallback |
| table, polyline, dynamic drawing lifecycle | Not supported yet | do not emit undefined objects |
| <code>request.security()</code>, <code>request.*()</code>, cross-symbol/MTF | Not supported yet | indicators receive only the current chart DataFrame and cannot fetch network or exchange data |
| <code>barstate.*</code>, tick updates, rollback | Different execution model | the runtime recalculates from the received DataFrame; confirmed-bar events are the reproducible baseline |
| strategy.*, orders, positions, backtesting | Outside indicator scope | convert to Strategy API V2 |

The recommended acceptance criterion for “most features are consistent” is therefore: common single-symbol, single-timeframe indicators produce equivalent values and event bars from the same OHLCV, parameters, and confirmed candles. It does not mean Pine source runs directly, and it excludes multi-timeframe requests, tick state, and advanced drawing objects until those contracts are implemented.

For every Pine migration:

1. Record the Pine version, inputs, source series, and indicator timeframe.
2. Match exact SMA/EMA/RMA/WMA, standard-deviation, ATR, and crossover definitions; matching names are not enough.
3. Match warmup, <code>na</code> propagation, division-by-zero handling, and the first valid bar.
4. Separate persistent conditions from one-time events; a Pine condition series must not become a notification on every bar.
5. Compare numerical tolerance, event bar indexes, and drawing anchors on a fixed OHLCV fixture.
6. If the Pine script uses a “not supported yet” row, simplify it, document an approximation, or extend the platform contract first. Never label it fully compatible in documentation or code comments.

See TradingView's official Pine documentation for its capability categories: [Visuals overview](https://www.tradingview.com/pine-script-docs/visuals/overview/), [Inputs](https://www.tradingview.com/pine-script-docs/concepts/inputs/), [Other timeframes and data](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/), and [Bar states](https://www.tradingview.com/pine-script-docs/concepts/bar-states/).

---

## 11. pandas and numpy type traps

The most common failure is treating a numpy ndarray as a pandas Series.

Incorrect:

~~~python
values = np.where(close > close.shift(1), close, 0)
average = values.rolling(10).mean()
~~~

<code>np.where</code> may return ndarray, which has no <code>rolling</code>, <code>shift</code>, <code>ewm</code>, <code>fillna</code>, or <code>iloc</code>.

Prefer pandas-native operations:

~~~python
values = close.where(close > close.shift(1), 0)
average = values.rolling(10).mean()
~~~

When wrapping an ndarray is unavoidable:

~~~python
array = np.where(close > close.shift(1), close, 0)
values = pd.Series(array, index=df.index)
~~~

Always pass <code>index=df.index</code>. Otherwise the new RangeIndex can silently misalign with a DatetimeIndex.

| numpy form | Preferred pandas form |
| --- | --- |
| <code>np.where(cond, a, b)</code> | <code>a.where(cond, b)</code> |
| <code>np.maximum(s, 0)</code> | <code>s.clip(lower=0)</code> |
| <code>np.minimum(s, k)</code> | <code>s.clip(upper=k)</code> |
| <code>np.abs(s)</code> | <code>s.abs()</code> |

---

## 12. Avoid look-ahead and repainting

An indicator may use only the current and earlier bars. Do not use:

- <code>shift(-1)</code> or <code>shift(-N)</code>;
- <code>iloc[i + 1]</code> inside row loops;
- <code>bars_ago(-N)</code>;
- <code>rolling(..., center=True)</code>;
- the final full-dataset row to rewrite past signals;
- future highs, lows, or confirmations to mark an earlier bar.

A valid crossover uses the current and previous state:

~~~python
cross_up = (
    (ema_fast > ema_slow)
    & (ema_fast.shift(1) <= ema_slow.shift(1))
)
~~~

If a signal requires the current close to confirm, its converted strategy should execute later. Do not move the signal backward merely to improve the chart or backtest.

---

## 13. Sandbox and safety rules

Allowed computational modules include numpy, pandas, math, json, datetime, time, collections, functools, itertools, statistics, decimal, fractions, and copy. Since <code>pd</code> and <code>np</code> are preloaded, imports are normally unnecessary.

Do not use:

- network, file, database, or subprocess access;
- <code>eval</code>, <code>exec</code>, <code>compile</code>, or <code>open</code>;
- reflection, dynamic imports, dunder escapes, or sandbox bypasses;
- pandas/numpy file read, write, or serialization methods;
- modules such as <code>os</code>, <code>sys</code>, <code>requests</code>, <code>socket</code>, <code>subprocess</code>, <code>threading</code>, <code>sqlite3</code>, <code>pathlib</code>, <code>pickle</code>, <code>ctypes</code>, or <code>operator</code>.

The exact identifiers <code>eval</code>, <code>exec</code>, <code>compile</code>, <code>__import__</code>, <code>getattr</code>, <code>setattr</code>, <code>delattr</code>, <code>globals</code>, <code>vars</code>, <code>dir</code>, <code>breakpoint</code>, <code>open</code>, <code>input</code>, <code>exit</code>, <code>quit</code>, <code>chr</code>, and <code>format</code> are reserved even when referenced as values, passed as callbacks, or reused as local names. Matching is by complete identifier, so descriptive names such as <code>open_long</code>, <code>format_signal</code>, and <code>dir_value</code> remain valid. Do not build dunder names indirectly from variables, concatenation, multiplication, or f-strings.

Validation has a timeout. Avoid unbounded loops, explosive recursion, and unnecessarily expensive row-by-row algorithms.

---

## 14. Complete tutorial: dual EMA viewer

~~~python
# @param fast_len int 12 Fast EMA period range=5:30:1
# @param slow_len int 26 Slow EMA period range=10:80:2
# @param confirm_next_bar bool false Show markers one bar after confirmation
# @param show_marks bool true Show crossover markers

my_indicator_name = "Dual EMA Viewer"
my_indicator_description = "Displays two EMAs and marks confirmed crossover events."

df = df.copy()

fast_len = int(params.get("fast_len", 12))
slow_len = int(params.get("slow_len", 26))
confirm_next_bar = bool(params.get("confirm_next_bar", False))
show_marks = bool(params.get("show_marks", True))

close = df["close"]
high = df["high"]
low = df["low"]


def edge(condition):
    current = condition.fillna(False).astype(bool)
    previous = current.shift(1, fill_value=False).astype(bool)
    return current & ~previous


def to_plot_list(series):
    return [
        None if pd.isna(value) else float(value)
        for value in series
    ]


ema_fast = close.ewm(span=fast_len, adjust=False).mean()
ema_slow = close.ewm(span=slow_len, adjust=False).mean()

golden = edge(ema_fast > ema_slow)
death = edge(ema_fast < ema_slow)

if confirm_next_bar:
    golden = golden.shift(1, fill_value=False).astype(bool)
    death = death.shift(1, fill_value=False).astype(bool)

golden_marks = [
    float(low.iloc[i] * 0.995)
    if show_marks and bool(golden.iloc[i])
    else None
    for i in range(len(df))
]

death_marks = [
    float(high.iloc[i] * 1.005)
    if show_marks and bool(death.iloc[i])
    else None
    for i in range(len(df))
]

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "EMA Fast",
            "data": to_plot_list(ema_fast),
            "color": "#22C55E",
            "type": "line",
            "overlay": True,
        },
        {
            "name": "EMA Slow",
            "data": to_plot_list(ema_slow),
            "color": "#3B82F6",
            "type": "line",
            "overlay": True,
        },
    ],
    "signals": [
        {
            "type": "buy",
            "text": "Long Entry",
            "color": "#22C55E",
            "data": golden_marks,
        },
        {
            "type": "sell",
            "text": "Long Exit",
            "color": "#EF4444",
            "data": death_marks,
        },
    ],
    "layers": [],
}
~~~

How it works:

1. Parameter declarations drive the panel and candidate ranges.
2. <code>params.get</code> reads exactly matching defaults.
3. EMAs are persistent state, so they belong in plots.
4. Crossovers are one-time events, so <code>edge</code> converts them into signals.
5. Empty marker slots contain <code>None</code>.
6. The bearish marker is explicitly named “Long Exit,” preventing an accidental short-entry interpretation during conversion.

---

## 15. Validation, debugging, and common errors

Use this workflow after each meaningful change:

1. Save a version.
2. Run/preview the indicator.
3. Execute code validation.
4. Inspect warm-up bars, extreme markets, and short datasets.
5. Change parameters and confirm that results respond.
6. Confirm that signals appear only on event bars.

| Hint or error | Cause | Fix |
| --- | --- | --- |
| <code>EMPTY_CODE</code> | no source | provide complete indicator code |
| <code>MISSING_OUTPUT</code> | no output assignment | add the output dict |
| <code>MissingOutput</code> | no output after execution | check branches and scope |
| <code>InvalidOutputType</code> | output is not a dict | return a dict |
| <code>InvalidOutputStructure</code> | neither plots nor signals exists | provide at least one key |
| <code>LengthMismatch</code> | data length differs from bars | make every data list <code>len(df)</code> |
| <code>MISSING_DF_COPY</code> | missing working copy | add <code>df = df.copy()</code> |
| <code>PARAM_DEFAULT_MISMATCH</code> | declaration and fallback differ | align both defaults |
| <code>DECLARED_PARAMS_NOT_READ_VIA_PARAMS_GET</code> | declared but not read | call <code>params.get</code> |
| <code>EXECUTION_COLUMNS_IGNORED_FOR_INDICATOR</code> | trade columns detected | remove them and convert to V2 |
| <code>STRATEGY_ANNOTATIONS_IGNORED_FOR_INDICATOR</code> | legacy strategy annotations | remove them |
| <code>NDARRAY_PANDAS_METHOD_MISUSE</code> | ndarray used as Series | use pandas or wrap with its index |
| <code>FUTURE_DATA_LEAK</code> | future access detected | use current and past data only |

---

## 16. Semantic checklist before strategy conversion

Answer these questions before converting:

- Which marker is the long entry?
- Which marker is the long exit?
- Is shorting genuinely required? A bearish long exit is not automatically a short entry.
- Is reversal required? If so, are closing and reverse entry separate actions?
- On which timeframe and completed bar is the signal confirmed?
- Are repeated entries, scale-in, or scale-out allowed?
- How should sizing, stop-loss, take-profit, and trailing protection work?
- Which instrument and market type will the strategy own?
- If it is an exchange equity, what exact catalog product, venue, currency, product type, and API family will execution use? Preserve an instrument such as <code>Crypto:00700/HKD@gate:spot</code> or a catalog-confirmed <code>Crypto:HK0700/USDT@binance:swap</code>; do not replace it with its underlying stock.
- Is the product a long-only spot equity or a Crypto swap derivative that requires explicit direction metadata and <code>position_side</code>?

The converted strategy should remove chart-only colors, label offsets, plots, layers, and marker arrays. Preserve the signal algebra, then declare instruments, frequency, sizing, and risk explicitly with Strategy API V2.

Underlying stock data can support chart history and point-in-time fundamentals only when the active catalog supplies a valid <code>USStock</code> or <code>HKStock</code> mapping. That data mapping never changes the execution instrument, and the converter must not guess a region from a display name.

Always verify and backtest generated strategy code again. Marketplace publication requires at least one successful backtest record.

---

## 17. Pre-publication checklist

- [ ] Name and description exist and make no return claims.
- [ ] Comments, identifiers, metadata, and default labels are English.
- [ ] <code>df = df.copy()</code> is present.
- [ ] Every parameter is read through <code>params.get</code> with a matching default.
- [ ] No order, position, leverage, or execution-risk logic is present.
- [ ] <code>output</code> is a dict.
- [ ] Every plot/signal data list has length <code>len(df)</code>.
- [ ] Missing values are <code>None</code>; no NaN or infinity is emitted.
- [ ] Signals are sparse events; persistent states use plots or a few layers.
- [ ] No future data is accessed.
- [ ] Numpy results are converted to indexed Series before pandas-only methods.
- [ ] Short datasets, warm-up periods, and unusual parameters do not crash.
- [ ] A version is saved and preview/validation passes.
