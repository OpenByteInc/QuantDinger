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
2. Legacy per-market boolean flags (kept for back-compat):
   - ``SHOW_CN_STOCK`` (default ``true``) — A股 (CNStock)
   - ``SHOW_HK_STOCK`` (default ``true``) — H股 (HKStock)
   - ``SHOW_US_STOCK`` (default ``true``) — 美股 (USStock)
3. All other markets (Crypto / Forex / Futures / MOEX) default to **hidden**.
   To enable any of them, either set ``ENABLED_MARKETS`` (recommended) or the
   corresponding ``SHOW_*`` flag, e.g. ``SHOW_CRYPTO=true``.

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

# Markets that are visible by default (without any env override).
# Current product focus: A股 + H股 + 美股.
# Other markets (Crypto / Forex / Futures / MOEX) must be enabled explicitly
# via ``ENABLED_MARKETS`` or the matching ``SHOW_*`` flag.
_DEFAULT_VISIBLE_MARKETS = frozenset({
    'USStock', 'CNStock', 'HKStock',
})

# Map each market to its legacy ``SHOW_*`` env flag and the default value
# used when neither the flag nor ``ENABLED_MARKETS`` is set.
# Defaults match :data:`_DEFAULT_VISIBLE_MARKETS` — three stock markets on,
# everything else off.
_LEGACY_SHOW_FLAGS = {
    'CNStock':  ('SHOW_CN_STOCK',  'true'),
    'HKStock':  ('SHOW_HK_STOCK',  'true'),
    'USStock':  ('SHOW_US_STOCK',  'true'),
    'Crypto':   ('SHOW_CRYPTO',    'false'),
    'Forex':    ('SHOW_FOREX',     'false'),
    'Futures':  ('SHOW_FUTURES',   'false'),
    'MOEX':     ('SHOW_MOEX',      'false'),
}


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
    """True iff ``market`` should be exposed in user-facing market pickers.

    Defaults to showing only the three stock markets (USStock / CNStock /
    HKStock). To show any other market, either:
      * set ``ENABLED_MARKETS=Crypto,CNStock,...`` (recommended — overrides
        everything), or
      * set the matching ``SHOW_<MARKET>=true`` legacy flag.
    """
    m = (market or '').strip()
    if not m:
        return False

    # 1. ENABLED_MARKETS whitelist always wins when set.
    whitelist = enabled_markets_whitelist()
    if whitelist:
        return m in whitelist

    # 2. Legacy per-market SHOW_* flag (with sensible default).
    #    Unknown markets (not in _KNOWN_MARKETS) fall through to False.
    entry = _LEGACY_SHOW_FLAGS.get(m)
    if entry is None:
        # Unknown market: hide by default to avoid surfacing typos / future
        # values that the operator hasn't opted into.
        return m in _DEFAULT_VISIBLE_MARKETS
    flag_name, default_val = entry
    return _flag(flag_name, default_val)


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
