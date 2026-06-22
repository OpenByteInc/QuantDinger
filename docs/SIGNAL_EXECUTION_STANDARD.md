# QuantDinger Signal & Execution Standard (SSOT)

**Version**: 1.0  
**Status**: Current  
**Scope**: All `IndicatorStrategy` flows (indicator Python saved as strategies) for backtest and live  
**Implementation**: `BacktestService`, `TradingExecutor`, `validate_code_safety` / `verifyCode`  
**Guide**: [STRATEGY_DEV_GUIDE.md](./STRATEGY_DEV_GUIDE.md)

---

## 1. Purpose

The platform runs **many different strategies**. Without a single contract, teams see:

- Different meanings of `buy`/`sell` across scripts;
- Backtests filling on **closed bar → next open** while live trades on **forming bars + immediate exits**;
- Indicator exits plus `# @strategy trailingEnabled` → duplicate closes and `invalid amount (0.0)` rejects.

This document is the **single source of truth** for what authors write and how engines interpret it.  
New strategies **SHOULD** comply; existing ones **SHOULD** migrate per §9.

---

## 2. Terms

| Term | Meaning |
|------|---------|
| **Signal bar** | Bar where a boolean flag becomes true (timestamp = that bar’s close) |
| **Fill bar** | Bar where execution is anchored (default: bar after signal bar) |
| **Edge trigger** | true only on false→true transition vs previous bar |
| **Repaint** | Conditions on the forming bar flip as price updates |
| **Exit owner** | One primary close path: indicator signals **or** engine risk, not both narrow rules |

---

## 3. Choose a strategy form first

| Form | Use when | Avoid when |
|------|----------|------------|
| **A. Two-way** `buy` / `sell` | Simple crossovers; symmetric reversal | Separate tp/sl per side, state machines, “close only” semantics |
| **B. Four-way** `open_long` / `close_long` / `open_short` / `close_short` | State machines, touch exits, explicit queue alignment | — (recommended default for new strategies) |
| **C. ScriptStrategy** `on_bar` + `ctx` | Position-aware bots, scaling, cooldowns | Pure vectorized signals on full `df` |

**Platform default for new listings**: **Form B (four-way)**.

---

## 4. Signal output rules (IndicatorStrategy)

### 4.1 Universal MUST

1. **MUST** start mutations with `df = df.copy()`.  
2. **MUST** define `output` (`name`, `plots`; optional `signals` for markers only).  
3. **MUST** execution columns: length `len(df)`, dtype bool after `fillna(False).astype(bool)`.  
4. **MUST** edge-trigger execution columns (§4.3) unless documented otherwise.  
5. **MUST NOT** use `shift(-1)` or any look-ahead.  
6. **MUST NOT** mix form A and form B execution columns as fill drivers (may keep `buy`/`sell` all-false for legacy UI).

### 4.2 Form A: `buy` / `sell`

| `tradeDirection` | `buy=True` | `sell=True` |
|------------------|------------|-------------|
| `long` | open long | close long |
| `short` | close short | open short |
| `both` | open long; **close short first if short** | open short; **close long first if long** |

**MUST NOT** treat `buy` as “close short only” under `both`. Use form B `close_*` for flat-only exits.

### 4.3 Form B: Four-way (recommended)

| Column | Meaning |
|--------|---------|
| `open_long` | Open / add long |
| `close_long` | Close long |
| `open_short` | Open / add short |
| `close_short` | Close short |

**MUST**: all four columns exist as bool.  
**SHOULD**: same-bar priority close before open; avoid simultaneous `open_long` and `open_short` on one bar.

Fills follow **explicit four-way** semantics (not buy/sell both-mode remapping).

### 4.4 `tradeDirection`

`long` / `short` / `both` filters which legs are active; it does not replace four-way columns.

---

## 5. Exit ownership

Each strategy **MUST** declare one owner in comments or docs:

| Mode | Script | `# @strategy` |
|------|--------|---------------|
| **Indicator exits** `exit_owner: indicator` | `close_*` or equivalent explicit signal exits | `trailingEnabled false`; do not rely on SL/TP/trailing |
| **Engine exits** `exit_owner: engine` | entries only, or structural reverse `close_*` signals | SL/TP/trailing as needed |

**MUST NOT** combine tight indicator tp/sl with tight `trailingEnabled` on the same leg.

The current backend supports only `exit_owner: indicator` and `exit_owner: engine`. Do not emit `exit_owner: layered`; a mixed “indicator exits + engine backup” mode needs an explicit product/runtime change first.

---

## 6. Execution contract (backtest ↔ live)

### 6.1 Signal timing

| Setting | Standard |
|---------|----------|
| `signal_mode` | **`confirmed`** |
| `exit_signal_mode` | **`confirmed`** |

Forming-bar evaluation is for display/research only by default.

### 6.2 Fill timing

Default: **`next_bar_open`** (signal on bar *t*, fill on bar *t+1* open ± slippage).

### 6.3 Same-bar ordering

Priority: `close_*` > `reduce_*` > `open_*` > `add_*`.  
Document flip mode **R1** (close on bar *t*, open on *t+1*, recommended) or **R2** (same-bar flip).

### 6.4 Close sizing

Sync positions → resolve size from DB and exchange → retry once if zero → fail with log if still zero.

---

## 7. Configuration

Use `# @strategy` for defaults; leverage and credentials stay in product UI.  
Optional header block:

```python
# --- QuantDinger execution contract (v1) ---
# signal_form: four_way
# exit_owner: indicator
# flip_mode: R1
# @strategy tradeDirection both
# @strategy trailingEnabled false
```

---

## 8. Release checklist

- Form A/B/C chosen and documented  
- Exit owner declared  
- `confirmed` modes for live  
- Backtest fills reviewed (signal bar vs fill bar)  
- Pilot live run without systematic zero-amount closes  

---

## 9. Migration tiers

**P0**: `both` + merged tp/sl in `buy`/`sell` + trailing → fix first.  
**P1**: Touch logic + large live/backtest drift → four-way + confirmed + edge.  
**P2**: Simple two-way → keep A, add contract header.  

Optional badge: “Contract v1 certified”.

---

## 10. Implementation map

| Rule | Code |
|------|------|
| Four-way | `TradingExecutor._execute_indicator_with_prices`, `BacktestService` |
| Two-way both | `_indicator_both_mode` only for buy/sell normalization |
| Confirmed bars | `signal_mode`, `exit_signal_mode` |
| Close retry | `PendingOrderWorker`, `resolve_reduce_only_quantity` |
| Sandbox | `safe_exec.py` |

---

## 11. Revision history

| Version | Date | Notes |
|---------|------|-------|
| 1.0 | 2026-05 | Initial standard |
