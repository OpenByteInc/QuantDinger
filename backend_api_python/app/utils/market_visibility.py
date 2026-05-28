"""Market visibility resolution (shared across watchlist / agent / radar).

Operators control which markets the UI exposes through environment variables.
This module is the single source of truth so the *watchlist add-symbol modal*
(`/api/market/types`), the *Agent API market catalog*
(`/api/agent/v1/markets`), and the *home AI radar*
(`/api/global-market/opportunities`) all agree — without it the three places
drifted apart and operators had to disable the same market in three places.

Resolution order (first match wins):

1. ``ENABLED_MARKETS`` (CSV whitelist). When non-empty, ONLY the listed
   markets are visible. Unknown values are ignored. This is the primary knob
   for "I want X and Y, nothing else".
2. ``SHOW_CN_STOCK`` (legacy boolean, default ``false``). Drops ``CNStock``
   when off. Kept for back-compat with deployments that predate
   ``ENABLED_MARKETS``.
3. ``SHOW_HK_STOCK`` (legacy boolean, default ``true``). Drops ``HKStock``
   when off. Same back-compat reasoning.
4. Everything else defaults to visible.

The whitelist completely overrides the legacy flags — if ``ENABLED_MARKETS``
is set and does not list ``CNStock``, the market is hidden regardless of
``SHOW_CN_STOCK``. This keeps the new flag predictable.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, List, Set


_KNOWN_MARKETS = frozenset({
    'Crypto', 'USStock', 'CNStock', 'HKStock', 'Forex', 'Futures', 'MOEX',
})


def _flag(name: str, default: str) -> bool:
    return str(os.getenv(name, default)).strip().lower() in ('1', 'true', 'yes', 'on')


def _parse_csv(name: str) -> Set[str]:
    raw = (os.getenv(name) or '').strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(',') if part.strip()}


def enabled_markets_whitelist() -> Set[str]:
    """Return the active ENABLED_MARKETS whitelist, or empty set when unset.

    Empty set is the "no whitelist" signal; callers should fall back to the
    legacy ``SHOW_*`` flags via :func:`is_market_visible`.
    """
    return _parse_csv('ENABLED_MARKETS')


def is_market_visible(market: str) -> bool:
    """True iff ``market`` should be exposed in user-facing market pickers."""
    m = (market or '').strip()
    if not m:
        return False

    whitelist = enabled_markets_whitelist()
    if whitelist:
        return m in whitelist

    if m == 'CNStock':
        return _flag('SHOW_CN_STOCK', 'true')
    if m == 'HKStock':
        return _flag('SHOW_HK_STOCK', 'true')
    return True


def filter_market_items(items: Iterable[Any], key: str = 'value') -> List[Any]:
    """Filter a list whose items are either market strings or dicts of shape
    ``{key: <market>, ...}``. Items with falsy / unknown market values are
    dropped; the relative order of surviving items is preserved.
    """
    out: List[Any] = []
    for it in items or []:
        if isinstance(it, dict):
            mk = (it.get(key) or '').strip()
        elif isinstance(it, str):
            mk = it.strip()
        else:
            continue
        if mk and is_market_visible(mk):
            out.append(it)
    return out


def hidden_markets() -> Set[str]:
    """Return the set of known markets currently hidden by env config.

    Useful for *post-filtering* cached payloads (e.g. opportunities radar)
    where the data was computed before the latest env flip.
    """
    return {m for m in _KNOWN_MARKETS if not is_market_visible(m)}
