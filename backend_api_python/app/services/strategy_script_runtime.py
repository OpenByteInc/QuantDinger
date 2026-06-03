"""
Python 策略脚本（on_init / on_bar + ctx.buy/sell/close_position）运行时。
与回测逻辑对齐，供 TradingExecutor 实盘逐根 K 线调用。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.utils.logger import get_logger

logger = get_logger(__name__)


class ScriptBar(dict):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class ScriptPosition(dict):
    """Hedge-aware position container exposed to bot scripts as ``ctx.position``.

    Stores ``long_*`` and ``short_*`` legs independently so neutral grid bots
    can hold long and short exposure at the same time (mirrors Binance
    Futures hedge mode). The legacy ``side / size / entry_price / direction``
    keys are kept in sync as the *net* view so existing scripts that do
    ``if ctx.position > 0`` or ``if ctx.position == 0`` keep working unchanged.

    Numeric/bool dunders return the **net** direction (long_size - short_size),
    so ``ctx.position > 0`` means "net long", ``ctx.position == 0`` means flat
    on both legs.
    """

    def __init__(self):
        super().__init__()
        self.clear_position()

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __bool__(self) -> bool:
        return self._long_size() > 0 or self._short_size() > 0

    def __int__(self) -> int:
        net = self._long_size() - self._short_size()
        if net > 1e-12:
            return 1
        if net < -1e-12:
            return -1
        return 0

    def __float__(self) -> float:
        return float(int(self))

    def __eq__(self, other: Any) -> bool:
        try:
            return int(self) == int(other)
        except Exception:
            return dict.__eq__(self, other)

    def __ne__(self, other: Any) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return id(self)

    def __lt__(self, other: Any) -> bool:
        return int(self) < int(other)

    def __le__(self, other: Any) -> bool:
        return int(self) <= int(other)

    def __gt__(self, other: Any) -> bool:
        return int(self) > int(other)

    def __ge__(self, other: Any) -> bool:
        return int(self) >= int(other)

    # ------------------------------------------------------------------ helpers

    def _long_size(self) -> float:
        try:
            return max(0.0, float(self.get('long_size') or 0.0))
        except Exception:
            return 0.0

    def _short_size(self) -> float:
        try:
            return max(0.0, float(self.get('short_size') or 0.0))
        except Exception:
            return 0.0

    def _long_entry(self) -> float:
        try:
            return max(0.0, float(self.get('long_entry') or 0.0))
        except Exception:
            return 0.0

    def _short_entry(self) -> float:
        try:
            return max(0.0, float(self.get('short_entry') or 0.0))
        except Exception:
            return 0.0

    def _refresh_legacy_view(self) -> None:
        """Recompute the deprecated single-leg fields from the hedge state."""
        long_size = self._long_size()
        short_size = self._short_size()
        long_entry = self._long_entry()
        short_entry = self._short_entry()
        net = long_size - short_size
        if net > 1e-12:
            side = 'long'
            size = long_size
            entry = long_entry
            direction = 1
        elif net < -1e-12:
            side = 'short'
            size = short_size
            entry = short_entry
            direction = -1
        else:
            side = ''
            size = 0.0
            entry = 0.0
            direction = 0
        self['side'] = side
        self['size'] = size
        self['entry_price'] = entry
        self['direction'] = direction
        self['amount'] = size
        self['long_size'] = long_size
        self['short_size'] = short_size
        self['long_entry'] = long_entry
        self['short_entry'] = short_entry

    # ------------------------------------------------------------------ hedge API

    @property
    def long_size(self) -> float:
        return self._long_size()

    @property
    def short_size(self) -> float:
        return self._short_size()

    @property
    def long_entry(self) -> float:
        return self._long_entry()

    @property
    def short_entry(self) -> float:
        return self._short_entry()

    def has_long(self) -> bool:
        return self._long_size() > 1e-12

    def has_short(self) -> bool:
        return self._short_size() > 1e-12

    def is_flat(self) -> bool:
        return not self.has_long() and not self.has_short()

    def open_long(self, entry_price: float, amount: float) -> None:
        amt = max(0.0, float(amount or 0.0))
        if amt <= 0:
            return
        self['long_size'] = self._long_size() + amt
        # Existing long leg keeps weighted-avg semantics with the new fill.
        old_size = self._long_size() - amt
        old_entry = self._long_entry()
        new_entry = float(entry_price or 0.0)
        if old_size > 0 and old_entry > 0 and new_entry > 0:
            blended = ((old_entry * old_size) + (new_entry * amt)) / (old_size + amt)
            self['long_entry'] = blended
        else:
            self['long_entry'] = new_entry
        self._refresh_legacy_view()

    def add_long(self, entry_price: float, amount: float) -> None:
        # Identical math to ``open_long``; separate name keeps callsites readable.
        self.open_long(entry_price, amount)

    def reduce_long(self, amount: float) -> tuple[float, float]:
        """Reduce the long leg by ``amount`` and return ``(closed_qty, avg_entry)``.

        Callers use the returned average entry price to compute matched-grid
        PnL against the fill price. Entry price of the remaining long leg is
        kept unchanged (FIFO-on-average semantics).
        """
        cur_size = self._long_size()
        if cur_size <= 0:
            return 0.0, 0.0
        reduce = max(0.0, float(amount or 0.0))
        if reduce <= 0:
            return 0.0, 0.0
        closed = min(reduce, cur_size)
        avg_entry = self._long_entry()
        remaining = cur_size - closed
        if remaining <= 1e-12:
            self['long_size'] = 0.0
            self['long_entry'] = 0.0
        else:
            self['long_size'] = remaining
        self._refresh_legacy_view()
        return closed, avg_entry

    def close_long(self) -> tuple[float, float]:
        return self.reduce_long(self._long_size())

    def open_short(self, entry_price: float, amount: float) -> None:
        amt = max(0.0, float(amount or 0.0))
        if amt <= 0:
            return
        self['short_size'] = self._short_size() + amt
        old_size = self._short_size() - amt
        old_entry = self._short_entry()
        new_entry = float(entry_price or 0.0)
        if old_size > 0 and old_entry > 0 and new_entry > 0:
            blended = ((old_entry * old_size) + (new_entry * amt)) / (old_size + amt)
            self['short_entry'] = blended
        else:
            self['short_entry'] = new_entry
        self._refresh_legacy_view()

    def add_short(self, entry_price: float, amount: float) -> None:
        self.open_short(entry_price, amount)

    def reduce_short(self, amount: float) -> tuple[float, float]:
        cur_size = self._short_size()
        if cur_size <= 0:
            return 0.0, 0.0
        reduce = max(0.0, float(amount or 0.0))
        if reduce <= 0:
            return 0.0, 0.0
        closed = min(reduce, cur_size)
        avg_entry = self._short_entry()
        remaining = cur_size - closed
        if remaining <= 1e-12:
            self['short_size'] = 0.0
            self['short_entry'] = 0.0
        else:
            self['short_size'] = remaining
        self._refresh_legacy_view()
        return closed, avg_entry

    def close_short(self) -> tuple[float, float]:
        return self.reduce_short(self._short_size())

    # ------------------------------------------------------------------ legacy API

    def clear_position(self) -> None:
        self.clear()
        self.update({
            'side': '',
            'size': 0.0,
            'entry_price': 0.0,
            'direction': 0,
            'amount': 0.0,
            'long_size': 0.0,
            'long_entry': 0.0,
            'short_size': 0.0,
            'short_entry': 0.0,
        })

    def open_position(self, side: str, entry_price: float, amount: float) -> None:
        """Legacy single-leg open. Routes to the matching hedge leg.

        Resets the *target* leg only — does not touch the opposite leg, so a
        neutral-grid script can ``open_position('long', ...)`` while still
        holding a short leg from a previous bar.
        """
        s = (side or '').strip().lower()
        amt = max(0.0, float(amount or 0.0))
        if amt <= 0:
            return
        if s == 'long':
            self['long_size'] = amt
            self['long_entry'] = float(entry_price or 0.0)
        elif s == 'short':
            self['short_size'] = amt
            self['short_entry'] = float(entry_price or 0.0)
        self._refresh_legacy_view()

    def add_position(self, entry_price: float, amount: float) -> None:
        """Legacy add. Adds to the leg implied by the current net direction.

        When both legs are zero we cannot infer a direction, so this becomes a
        no-op (the script should call ``open_position`` or the new
        ``open_long``/``open_short`` explicitly).
        """
        direction = int(self)
        if direction > 0:
            self.add_long(entry_price, amount)
        elif direction < 0:
            self.add_short(entry_price, amount)

    def reduce_position(self, amount: float) -> None:
        """Legacy reduce. Reduces the leg matching the current net direction."""
        direction = int(self)
        if direction > 0:
            self.reduce_long(amount)
        elif direction < 0:
            self.reduce_short(amount)


class StrategyScriptContext:
    """Live script context with behavior aligned to ScriptBacktestContext."""

    def __init__(self, bars_df: Optional[pd.DataFrame] = None, initial_balance: float = 0.0):
        if bars_df is None:
            bars_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume", "time"])
        self._bars_df = bars_df
        self._params: Dict[str, Any] = {}
        self._orders: List[Dict[str, Any]] = []
        self._logs: List[str] = []
        self.current_index = -1
        self.position = ScriptPosition()
        self.balance = float(initial_balance)
        self.equity = float(initial_balance)

    def param(self, name: str, default: Any = None) -> Any:
        if name not in self._params:
            self._params[name] = default
        return self._params[name]

    def bars(self, n: int = 1):
        start = max(0, self.current_index - int(n) + 1)
        out = []
        for _, row in self._bars_df.iloc[start:self.current_index + 1].iterrows():
            out.append(ScriptBar(
                open=float(row.get('open') or 0),
                high=float(row.get('high') or 0),
                low=float(row.get('low') or 0),
                close=float(row.get('close') or 0),
                volume=float(row.get('volume') or 0),
                timestamp=row.get('time')
            ))
        return out

    def log(self, message: Any):
        self._logs.append(str(message))

    def flush_logs(self) -> List[str]:
        logs = self._logs.copy()
        self._logs = []
        return logs

    def buy(self, price: Any = None, amount: Any = None, *, intent: str = 'auto', reason: Optional[str] = None):
        # ``intent`` lets hedge-aware bot scripts disambiguate between
        # "close my short leg" vs "open/add a long leg" instead of forcing the
        # executor to guess from the (single-net) position view.
        self._orders.append({
            'action': 'buy',
            'price': price,
            'amount': amount,
            'intent': intent,
            'reason': reason,
        })

    def sell(self, price: Any = None, amount: Any = None, *, intent: str = 'auto', reason: Optional[str] = None):
        self._orders.append({
            'action': 'sell',
            'price': price,
            'amount': amount,
            'intent': intent,
            'reason': reason,
        })

    def close_long(self, amount: Any = None, price: Any = None, reason: Optional[str] = None):
        self._orders.append({
            'action': 'sell',
            'price': price,
            'amount': amount,
            'intent': 'close_long',
            'reason': reason or 'script_close_long',
        })

    def close_short(self, amount: Any = None, price: Any = None, reason: Optional[str] = None):
        self._orders.append({
            'action': 'buy',
            'price': price,
            'amount': amount,
            'intent': 'close_short',
            'reason': reason or 'script_close_short',
        })

    def open_long(self, amount: Any = None, price: Any = None, reason: Optional[str] = None):
        self._orders.append({
            'action': 'buy',
            'price': price,
            'amount': amount,
            'intent': 'open_long',
            'reason': reason,
        })

    def add_long(self, amount: Any = None, price: Any = None, reason: Optional[str] = None):
        self._orders.append({
            'action': 'buy',
            'price': price,
            'amount': amount,
            'intent': 'add_long',
            'reason': reason or 'script_add_long',
        })

    def open_short(self, amount: Any = None, price: Any = None, reason: Optional[str] = None):
        self._orders.append({
            'action': 'sell',
            'price': price,
            'amount': amount,
            'intent': 'open_short',
            'reason': reason,
        })

    def add_short(self, amount: Any = None, price: Any = None, reason: Optional[str] = None):
        self._orders.append({
            'action': 'sell',
            'price': price,
            'amount': amount,
            'intent': 'add_short',
            'reason': reason or 'script_add_short',
        })

    def close_position(self):
        self._orders.append({'action': 'close'})


def compile_strategy_script_handlers(code: str) -> Tuple[Optional[Callable], Optional[Callable]]:
    """
    校验并编译策略脚本，返回 (on_init, on_bar)。
    on_bar 不可缺省；on_init 可选。
    """
    if not code or not str(code).strip():
        raise ValueError("Strategy script is empty")

    from app.utils.safe_exec import build_safe_builtins, safe_exec_with_validation

    exec_env = {
        '__builtins__': build_safe_builtins(),
        'np': np,
        'pd': pd,
    }

    exec_result = safe_exec_with_validation(
        code=code,
        exec_globals=exec_env,
        exec_locals=exec_env,
        timeout=60,
    )
    if not exec_result['success']:
        raise RuntimeError(f"Code execution failed: {exec_result.get('error')}")

    on_init = exec_env.get('on_init')
    on_bar = exec_env.get('on_bar')
    if not callable(on_bar):
        raise ValueError("Strategy script must define on_bar(ctx, bar)")
    if on_init is not None and not callable(on_init):
        on_init = None
    return (on_init if callable(on_init) else None), on_bar
