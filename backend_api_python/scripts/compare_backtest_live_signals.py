#!/usr/bin/env python3
"""
Compare backtest vs live signal normalization for a given indicator on ETH 1H.

Usage (from backend_api_python/):
  python scripts/compare_backtest_live_signals.py

Requires: pandas, numpy, ccxt (for live klines).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stub_app_logger() -> None:
    import types

    log = types.SimpleNamespace(
        debug=lambda *a, **k: None,
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )
    for pkg in ("app", "app.utils", "app.utils.logger"):
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)
    sys.modules["app.utils.logger"].get_logger = lambda _name=None: log  # type: ignore[attr-defined]


def _load_module(name: str, rel_path: str):
    import importlib.util

    _stub_app_logger()
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_safe_exec = _load_module("qd_safe_exec", "app/utils/safe_exec.py")

# --- User strategy (Filtered SuperTrend) ---
INDICATOR_CODE = r'''
my_indicator_name = "Filtered SuperTrend"
my_indicator_description = "SuperTrend with EMA60 filter (buy only above EMA60, sell only below)"

df = df.copy()

periods = 10
multiplier = 3.0
change_atr = True

src = (df["high"] + df["low"]) / 2.0

prev_close = df["close"].shift(1)
tr0 = df["high"] - df["low"]
tr1 = (df["high"] - prev_close).abs()
tr2 = (df["low"] - prev_close).abs()
tr = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)

n = len(df)
close_arr = df["close"].values

atr_sma = tr.rolling(periods).mean()

if change_atr:
    atr = pd.Series([np.nan] * n, index=df.index, dtype="float64")
    if n >= periods:
        atr.iloc[periods - 1] = float(tr.iloc[:periods].mean())
        for i in range(periods, n):
            atr.iloc[i] = (atr.iloc[i - 1] * (periods - 1) + float(tr.iloc[i])) / periods
else:
    atr = atr_sma

up_final = [np.nan] * n
dn_final = [np.nan] * n

for i in range(n):
    a = atr.iloc[i]
    if pd.isna(a):
        continue

    up_raw = float(src.iloc[i] - multiplier * a)
    dn_raw = float(src.iloc[i] + multiplier * a)

    if i == 0 or pd.isna(up_final[i - 1]):
        up1 = up_raw
    else:
        up1 = float(up_final[i - 1])

    if i == 0 or pd.isna(dn_final[i - 1]):
        dn1 = dn_raw
    else:
        dn1 = float(dn_final[i - 1])

    prev_c = float(close_arr[i - 1]) if i > 0 else float(close_arr[i])

    up_final[i] = max(up_raw, up1) if prev_c > up1 else up_raw
    dn_final[i] = min(dn_raw, dn1) if prev_c < dn1 else dn_raw

trend = [1] * n
for i in range(1, n):
    if pd.isna(up_final[i - 1]) or pd.isna(dn_final[i - 1]):
        trend[i] = trend[i - 1]
        continue

    prev_t = trend[i - 1]
    if prev_t == -1 and float(close_arr[i]) > float(dn_final[i - 1]):
        trend[i] = 1
    elif prev_t == 1 and float(close_arr[i]) < float(up_final[i - 1]):
        trend[i] = -1
    else:
        trend[i] = prev_t

trend_s = pd.Series(trend, index=df.index)

ema60 = df["close"].ewm(span=10, adjust=False).mean()

orig_buy = (trend_s == 1) & (trend_s.shift(1) == -1)
orig_sell = (trend_s == -1) & (trend_s.shift(1) == 1)

buy_signal = orig_buy & (df["close"] > ema60)
sell_signal = orig_sell & (df["close"] < ema60)
buy_signal = buy_signal.fillna(False).astype(bool)
sell_signal = sell_signal.fillna(False).astype(bool)

df["buy"] = buy_signal
df["sell"] = sell_signal

up_plot = [float(up_final[i]) if (trend[i] == 1 and not pd.isna(up_final[i])) else None for i in range(n)]
dn_plot = [float(dn_final[i]) if (trend[i] == -1 and not pd.isna(dn_final[i])) else None for i in range(n)]
ema60_data = [None if pd.isna(x) else float(x) for x in ema60]

buy_marks = [float(up_final[i]) if (buy_signal.iloc[i] and not pd.isna(up_final[i])) else None for i in range(n)]
sell_marks = [float(dn_final[i]) if (sell_signal.iloc[i] and not pd.isna(dn_final[i])) else None for i in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Up Trend", "type": "line", "data": up_plot, "color": "#00C853", "overlay": True},
        {"name": "Down Trend", "type": "line", "data": dn_plot, "color": "#D50000", "overlay": True},
        {"name": "EMA 60", "type": "line", "data": ema60_data, "color": "#2196F3", "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "Buy", "data": buy_marks, "color": "#00E676"},
        {"type": "sell", "text": "Sell", "data": sell_marks, "color": "#FF5252"},
    ],
}
'''


def _coerce_bool(val: Any, default: bool = False) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False
    return default


def normalize_trading_execution_modes(trading_config: Optional[Dict[str, Any]]) -> None:
    """Copy of app.services.trading_executor.normalize_trading_execution_modes (no Flask import)."""
    if not isinstance(trading_config, dict):
        return
    tc = trading_config
    has_strict_toggle = "strict_mode" in tc or "strictMode" in tc
    strict = _coerce_bool(tc.get("strict_mode", tc.get("strictMode")), default=False)
    tc["strict_mode"] = strict
    if has_strict_toggle:
        if strict:
            tc["signal_mode"] = "confirmed"
            tc["exit_signal_mode"] = "confirmed"
        else:
            tc["signal_mode"] = "aggressive"
            tc["exit_signal_mode"] = "aggressive"
            tc["entry_trigger_mode"] = "immediate"
    elif strict:
        tc.setdefault("exit_signal_mode", "confirmed")
        tc.setdefault("signal_mode", "confirmed")
    else:
        tc.setdefault("signal_mode", "aggressive")
        tc.setdefault("exit_signal_mode", "aggressive")
        tc.setdefault("entry_trigger_mode", "immediate")


def fetch_eth_1h(limit: int = 500) -> pd.DataFrame:
    try:
        import ccxt  # type: ignore
    except ImportError as e:
        raise RuntimeError("ccxt required: pip install ccxt") from e

    candidates = [
        os.getenv("CCXT_DEFAULT_EXCHANGE", "").strip().lower(),
        "binance",
        "okx",
        "bybit",
    ]
    last_err: Optional[Exception] = None
    ohlcv = None
    used_ex = ""
    for ex_id in candidates:
        if not ex_id or not hasattr(ccxt, ex_id):
            continue
        try:
            exchange = getattr(ccxt, ex_id)({"enableRateLimit": True})
            ohlcv = exchange.fetch_ohlcv("ETH/USDT", "1h", limit=limit)
            used_ex = ex_id
            break
        except Exception as e:
            last_err = e
            continue
    if ohlcv is None:
        raise RuntimeError(last_err or "no exchange")
    rows = []
    for candle in ohlcv:
        rows.append(
            {
                "time": int(candle[0] / 1000),
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
            }
        )
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("time")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df.attrs["exchange"] = used_ex
    return df.dropna()


def exec_backtest_style(code: str, df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, pd.Series]]:
    df_orig = df.copy()
    df_for_exec = df_orig.copy()
    if isinstance(df_for_exec.index, pd.DatetimeIndex):
        df_for_exec = df_for_exec.reset_index(drop=False)
        if "time" not in df_for_exec.columns:
            df_for_exec.rename(columns={df_for_exec.columns[0]: "time"}, inplace=True)

    env: Dict[str, Any] = {
        "df": df_for_exec,
        "pd": pd,
        "np": np,
        "params": {},
        "output": None,
        "__builtins__": _safe_exec.build_safe_builtins(),
    }
    r = _safe_exec.safe_exec_with_validation(
        code=code, exec_globals=env, exec_locals=env, timeout=120
    )
    if not r.get("success"):
        raise RuntimeError(f"backtest-style exec failed: {r.get('error')}")

    executed = env.get("df", df_for_exec)
    if isinstance(df_orig.index, pd.DatetimeIndex) and not isinstance(
        executed.index, pd.DatetimeIndex
    ):
        if "time" in executed.columns:
            executed = executed.set_index("time")
        elif len(executed) == len(df_orig):
            executed.index = df_orig.index

    signals = {
        "buy": executed["buy"].fillna(False).astype(bool).reindex(df_orig.index, fill_value=False),
        "sell": executed["sell"].fillna(False).astype(bool).reindex(df_orig.index, fill_value=False),
    }
    return executed, signals


def exec_live_style(code: str, df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, pd.Series]]:
    df_exec = df.copy()
    for col in ("open", "high", "low", "close", "volume"):
        df_exec[col] = df_exec[col].astype("float64")
    df_exec = df_exec.dropna()

    env: Dict[str, Any] = {
        "df": df_exec,
        "pd": pd,
        "np": np,
        "params": {},
        "output": None,
        "__builtins__": _safe_exec.build_safe_builtins(),
    }
    r = _safe_exec.safe_exec_with_validation(
        code=code, exec_globals=env, exec_locals=env, timeout=120
    )
    if not r.get("success"):
        raise RuntimeError(f"live-style exec failed: {r.get('error')}")

    executed = env.get("df", df_exec)
    signals = {
        "buy": executed["buy"].fillna(False).astype(bool),
        "sell": executed["sell"].fillna(False).astype(bool),
    }
    return executed, signals


def backtest_normalize(
    signals: Dict[str, pd.Series], df_signal: pd.DataFrame, trade_direction: str = "both"
) -> Dict[str, Any]:
    td = str(trade_direction or "both").lower()
    buy = signals["buy"].reindex(df_signal.index, fill_value=False).astype(bool)
    sell = signals["sell"].reindex(df_signal.index, fill_value=False).astype(bool)

    if td == "long":
        norm = {
            "open_long": buy,
            "close_long": sell,
            "open_short": pd.Series(False, index=df_signal.index),
            "close_short": pd.Series(False, index=df_signal.index),
            "_both_mode": False,
        }
    elif td == "short":
        norm = {
            "open_long": pd.Series(False, index=df_signal.index),
            "close_long": pd.Series(False, index=df_signal.index),
            "open_short": sell,
            "close_short": buy,
            "_both_mode": False,
        }
    else:
        norm = {
            "open_long": buy,
            "close_long": pd.Series(False, index=df_signal.index),
            "open_short": sell,
            "close_short": pd.Series(False, index=df_signal.index),
            "_both_mode": True,
        }
    return norm


def backtest_signal_queue(
    norm: Dict[str, Any], df_signal: pd.DataFrame, signal_tf_seconds: int = 3600
) -> List[Tuple[str, str, str]]:
    """(effective_iso, signal_type, signal_bar_iso)"""
    out: List[Tuple[str, str, str]] = []
    for sig_time in df_signal.index:
        sig_end = sig_time + timedelta(seconds=signal_tf_seconds)
        try:
            flags = {
                t: bool(norm[t].loc[sig_time])
                for t in ("open_long", "close_long", "open_short", "close_short")
            }
        except Exception:
            continue
        for st, on in flags.items():
            if on:
                out.append((sig_end.isoformat(), st, sig_time.isoformat()))
    out.sort(key=lambda x: x[0])
    return out


def live_normalize_columns(executed_df: pd.DataFrame, trading_config: Dict[str, Any]) -> pd.DataFrame:
    """Mirror TradingExecutor._execute_indicator_with_prices buy/sell -> 4-way."""
    td = trading_config.get("trade_direction", trading_config.get("tradeDirection", "both"))
    td = str(td or "both").lower()
    if td not in ("long", "short", "both"):
        td = "both"

    buy = executed_df["buy"].fillna(False).astype(bool)
    sell = executed_df["sell"].fillna(False).astype(bool)
    executed_df = executed_df.copy()

    if all(c in executed_df.columns for c in ("open_long", "close_long", "open_short", "close_short")):
        return executed_df

    if td == "long":
        executed_df["open_long"] = buy
        executed_df["close_long"] = sell
        executed_df["open_short"] = False
        executed_df["close_short"] = False
    elif td == "short":
        executed_df["open_long"] = False
        executed_df["close_long"] = False
        executed_df["open_short"] = sell
        executed_df["close_short"] = buy
    else:
        executed_df["open_long"] = buy
        executed_df["close_long"] = False
        executed_df["open_short"] = sell
        executed_df["close_short"] = False
        trading_config["_indicator_both_mode"] = True
    return executed_df


def live_pending_snapshot(
    executed_df: pd.DataFrame, trading_config: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Mirror live tick: only last 1-2 bars checked."""
    signal_mode = trading_config.get("signal_mode", "confirmed")
    exit_signal_mode = trading_config.get("exit_signal_mode", "aggressive")

    entry_check = set()
    exit_check = set()
    n = len(executed_df)
    if n > 1:
        entry_check.add(n - 2)
        exit_check.add(n - 2)
    if signal_mode == "aggressive" and n > 0:
        entry_check.add(n - 1)
    if exit_signal_mode == "aggressive" and n > 0:
        exit_check.add(n - 1)

    pending: List[Dict[str, Any]] = []
    check_indices = sorted(entry_check.union(exit_check), reverse=True)
    for idx in check_indices:
        ts = executed_df.index[idx]
        signal_timestamp = int(ts.timestamp()) if hasattr(ts, "timestamp") else 0
        close_price = float(executed_df["close"].iloc[idx])

        if idx in entry_check and executed_df["open_long"].iloc[idx]:
            pending.append(
                {"type": "open_long", "timestamp": signal_timestamp, "bar": str(ts)}
            )
        if idx in exit_check and executed_df["close_long"].iloc[idx]:
            pending.append(
                {"type": "close_long", "timestamp": signal_timestamp, "bar": str(ts)}
            )
        if idx in entry_check and executed_df["open_short"].iloc[idx]:
            pending.append(
                {"type": "open_short", "timestamp": signal_timestamp, "bar": str(ts)}
            )
        if idx in exit_check and executed_df["close_short"].iloc[idx]:
            pending.append(
                {"type": "close_short", "timestamp": signal_timestamp, "bar": str(ts)}
            )
    return pending


def live_confirmed_historical_events(executed_df: pd.DataFrame) -> List[Tuple[str, str]]:
    """
    Walk forward: at each bar i (as if i is forming), confirmed live only fires
    signals on bar i-1. Collect (bar_iso, type).
    """
    events: List[Tuple[str, str]] = []
    n = len(executed_df)
    for end_len in range(2, n + 1):
        sub = executed_df.iloc[:end_len]
        tc = {"signal_mode": "confirmed", "exit_signal_mode": "confirmed", "tradeDirection": "both"}
        sub = live_normalize_columns(sub.copy(), tc)
        pend = live_pending_snapshot(sub, tc)
        for p in pend:
            events.append((p["bar"], p["type"]))
    # de-dup consecutive duplicates at same bar
    deduped: List[Tuple[str, str]] = []
    seen = set()
    for bar, typ in events:
        key = (bar, typ)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((bar, typ))
    return deduped


def _norm_bar_key(bar: str) -> str:
    """Normalize ISO timestamps for set comparison."""
    return pd.Timestamp(bar).isoformat()


def digest_events(events: List[Tuple[str, ...]]) -> str:
    norm = []
    for e in events:
        parts = list(e)
        if parts:
            parts[0] = _norm_bar_key(str(parts[0]))
        norm.append(tuple(parts))
    payload = "\n".join("|".join(str(x) for x in e) for e in sorted(norm))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def main() -> int:
    print("=" * 72)
    print("ETH/USDT 1H — Filtered SuperTrend: backtest vs live signal comparison")
    print("=" * 72)

    try:
        df = fetch_eth_1h(500)
        source = f"ccxt/{df.attrs.get('exchange', 'unknown')}"
    except Exception as e:
        print(f"WARN: fetch failed ({e}); using synthetic random-walk OHLC")
        source = "synthetic"
        n = 500
        close = 3000 * np.exp(np.cumsum(np.random.default_rng(42).normal(0, 0.002, n)))
        df = pd.DataFrame(
            {
                "open": close,
                "high": close * 1.002,
                "low": close * 0.998,
                "close": close,
                "volume": np.full(n, 1e6),
            },
            index=pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC"),
        )

    print(f"Data: {source}, bars={len(df)}, from={df.index[0]} to={df.index[-1]}")

    bt_df, bt_sig = exec_backtest_style(INDICATOR_CODE, df)
    lv_df, lv_sig = exec_live_style(INDICATOR_CODE, df)

    buy_match = bt_sig["buy"].equals(lv_sig["buy"])
    sell_match = bt_sig["sell"].equals(lv_sig["sell"])
    print(f"\n[1] Indicator raw signals (same OHLC, no realtime tick)")
    print(f"    buy series match:  {buy_match}")
    print(f"    sell series match: {sell_match}")
    print(f"    buy count:  backtest={int(bt_sig['buy'].sum())} live={int(lv_sig['buy'].sum())}")
    print(f"    sell count: backtest={int(bt_sig['sell'].sum())} live={int(lv_sig['sell'].sum())}")

    if not buy_match or not sell_match:
        diff_buy = bt_sig["buy"] != lv_sig["buy"]
        print(f"    buy mismatch bars: {int(diff_buy.sum())}")
        if diff_buy.any():
            idx = df.index[diff_buy][:5]
            print(f"    first buy mismatches: {[str(t) for t in idx]}")

    norm_bt = backtest_normalize(bt_sig, df, "both")
    tc_strict = {"strict_mode": True, "tradeDirection": "both"}
    normalize_trading_execution_modes(tc_strict)
    lv_norm_df = live_normalize_columns(lv_df.copy(), dict(tc_strict))
    norm_lv = {
        k: lv_norm_df[k].astype(bool)
        for k in ("open_long", "close_long", "open_short", "close_short")
    }

    norm_match = all(norm_bt[k].equals(norm_lv[k]) for k in ("open_long", "close_long", "open_short", "close_short"))
    print(f"\n[2] both-mode normalization (buy/sell -> 4-way)")
    print(f"    4-way columns match: {norm_match}")

    queue_bt = backtest_signal_queue(norm_bt, df, 3600)
    bt_events = [(bar, typ) for _, typ, bar in queue_bt]

    live_hist = live_confirmed_historical_events(lv_norm_df)
    print(f"\n[3] Historical signal bars (confirmed live walk-forward vs backtest queue)")
    print(f"    backtest queue events: {len(bt_events)}")
    print(f"    live confirmed events: {len(live_hist)}")
    print(f"    digest backtest: {digest_events(bt_events)}")
    print(f"    digest live:     {digest_events(live_hist)}")

    set_bt = {(_norm_bar_key(b), t) for b, t in bt_events}
    set_lv = {(_norm_bar_key(b), t) for b, t in live_hist}
    only_bt = sorted(set_bt - set_lv)[:10]
    only_lv = sorted(set_lv - set_bt)[:10]
    print(f"    only in backtest ({len(set_bt - set_lv)}): {only_bt}")
    print(f"    only in live ({len(set_lv - set_bt)}): {only_lv}")

    print(f"\n[4] Last 8 backtest queue entries (effective_time, type, signal_bar)")
    for row in queue_bt[-8:]:
        print(f"    {row}")

    configs = {
        "strict_confirmed": {"strict_mode": True, "tradeDirection": "both"},
        "legacy_default": {},
        "explicit_aggressive": {
            "strict_mode": False,
            "tradeDirection": "both",
            "signal_mode": "aggressive",
            "exit_signal_mode": "aggressive",
        },
    }
    print(f"\n[5] Live pending_signals at LAST bar (only checks last 1-2 indices)")
    for label, tc in configs.items():
        tc = dict(tc)
        normalize_trading_execution_modes(tc)
        sub = live_normalize_columns(lv_df.copy(), tc)
        pend = live_pending_snapshot(sub, tc)
        print(f"    [{label}] signal_mode={tc.get('signal_mode')} pending={pend}")

    # Realtime last-bar perturbation
    df_rt = df.copy()
    last_close = float(df_rt["close"].iloc[-1])
    bumped = last_close * 1.008
    df_rt.iloc[-1, df_rt.columns.get_loc("close")] = bumped
    df_rt.iloc[-1, df_rt.columns.get_loc("high")] = max(
        float(df_rt["high"].iloc[-1]), bumped
    )
    df_rt.iloc[-1, df_rt.columns.get_loc("low")] = min(
        float(df_rt["low"].iloc[-1]), bumped
    )
    _, lv_sig_rt = exec_live_style(INDICATOR_CODE, df_rt)
    chg_buy = int(lv_sig_rt["buy"].sum()) - int(lv_sig["buy"].sum())
    chg_sell = int(lv_sig_rt["sell"].sum()) - int(lv_sig["sell"].sum())
    print(f"\n[6] Realtime sensitivity (last bar close +0.8%)")
    print(f"    buy count delta: {chg_buy}, sell count delta: {chg_sell}")
    if chg_buy or chg_sell:
        print("    -> Forming-bar price changes CAN alter signals vs backtest (closed OHLC only).")

    print(f"\n[7] Strategy note")
    print("    Code uses ewm(span=10) but description says EMA60 — filter is EMA10, not 60.")

    print("\n" + "=" * 72)
    if buy_match and norm_match and not (set_bt - set_lv) and not (set_lv - set_bt):
        print("RESULT: Raw + normalized signals ALIGN on this dataset (confirmed path).")
    else:
        print("RESULT: GAPS remain — see sections above.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
