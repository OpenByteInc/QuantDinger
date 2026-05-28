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
    def __init__(self):
        super().__init__()
        self.clear_position()

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __bool__(self) -> bool:
        return bool(self.get('side')) and float(self.get('size') or 0) > 0

    def __int__(self) -> int:
        return int(self.get('direction') or 0)

    def __float__(self) -> float:
        return float(self.get('direction') or 0)

    def __eq__(self, other: Any) -> bool:
        try:
            return int(self) == int(other)
        except Exception:
            return dict.__eq__(self, other)

    def __lt__(self, other: Any) -> bool:
        return int(self) < int(other)

    def __le__(self, other: Any) -> bool:
        return int(self) <= int(other)

    def __gt__(self, other: Any) -> bool:
        return int(self) > int(other)

    def __ge__(self, other: Any) -> bool:
        return int(self) >= int(other)

    def clear_position(self) -> None:
        self.clear()
        self.update({
            'side': '',
            'size': 0.0,
            'entry_price': 0.0,
            'direction': 0,
            'amount': 0.0,
        })

    def open_position(self, side: str, entry_price: float, amount: float) -> None:
        direction = 1 if side == 'long' else (-1 if side == 'short' else 0)
        size = float(amount or 0.0)
        price = float(entry_price or 0.0)
        self.clear()
        self.update({
            'side': side,
            'size': size,
            'entry_price': price,
            'direction': direction,
            'amount': size,
        })

    def add_position(self, entry_price: float, amount: float) -> None:
        extra = float(amount or 0.0)
        if extra <= 0:
            return
        current_size = float(self.get('size') or 0.0)
        current_price = float(self.get('entry_price') or 0.0)
        next_size = current_size + extra
        next_price = float(entry_price or current_price or 0.0)
        if current_size > 0 and current_price > 0 and next_size > 0:
            next_price = ((current_price * current_size) + (float(entry_price or current_price) * extra)) / next_size
        self['size'] = next_size
        self['amount'] = next_size
        self['entry_price'] = next_price

    def reduce_position(self, amount: float) -> None:
        """Reduce position size by *amount*. Clears to flat when size reaches zero."""
        reduce = float(amount or 0.0)
        if reduce <= 0:
            return
        current_size = float(self.get('size') or 0.0)
        remaining = current_size - reduce
        if remaining <= 1e-12:
            self.clear_position()
        else:
            self['size'] = remaining
            self['amount'] = remaining


class StrategyScriptContext:
    """与回测 ScriptBacktestContext 行为一致，供实盘按根推进。"""

    def __init__(self, bars_df: pd.DataFrame, initial_balance: float):
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

    def buy(self, price: Any = None, amount: Any = None):
        self._orders.append({'action': 'buy', 'price': price, 'amount': amount})

    def sell(self, price: Any = None, amount: Any = None):
        self._orders.append({'action': 'sell', 'price': price, 'amount': amount})

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
