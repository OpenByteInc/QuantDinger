"""
Broker x market x market_type x trade_direction x bot_type compatibility policy.

This module is the *single source of truth* for which combinations of
exchange / market / contract type / direction / bot strategy QuantDinger
will accept. All strategy CRUD endpoints, the live execution worker, and
the frontend (via GET /api/policy/broker-market) read from here.

Centralizing the matrix prevents the previous bug pattern where the same
"which broker can run which market" question was answered three different
ways in:
  - app/services/strategy.py (create / update / batch validators)
  - app/services/pending_order_worker.py (live execution gate)
  - QuantDinger-Vue-src/src/views/trading-assistant/index.vue (UI guard)
  - QuantDinger-Vue-src/src/views/trading-bot/components/BotCreateWizard.vue (bot guard)

Adding a new broker now means updating BROKER_MARKETS here, plus the
broker's client implementation. Everything else picks it up automatically.
"""

from typing import Dict, Optional, Set

from app.services.live_trading.capabilities import CRYPTO_VENUE_CAPABILITIES


# ---------------------------------------------------------------------------
# Canonical sets
# ---------------------------------------------------------------------------

def _build_broker_markets() -> Dict[str, Dict[str, Set[str]]]:
    """Construct the master matrix.

    Shape: {exchange_id: {market_category: {allowed_market_type, ...}}}
    """
    matrix: Dict[str, Dict[str, Set[str]]] = {
        # US stocks via Interactive Brokers (TWS/Gateway, local desktop only)
        "ibkr": {"USStock": {"spot"}},
        # Forex via MetaTrader 5 terminal (local desktop only)
        "mt5": {"Forex": {"spot"}},
        # Alpaca: REST broker for US equities + crypto.
        # Crypto is *spot only* on Alpaca - they have no perpetual / margin
        # crypto product, so a 'swap' market_type is impossible regardless of
        # what we implement.
        "alpaca": {
            "USStock": {"spot"},
            "Crypto": {"spot"},
        },
    }
    for ex, capability in CRYPTO_VENUE_CAPABILITIES.items():
        matrix[ex] = {"Crypto": set(capability.market_types)}
    return matrix


# Master compatibility matrix.
BROKER_MARKETS: Dict[str, Dict[str, Set[str]]] = _build_broker_markets()


# Brokers whose live execution path in QuantDinger only supports long-only
# entries today. This is *our implementation choice*, not a platform
# limitation: the IB API and alpaca-py both technically allow SELL_SHORT on
# margin accounts, but neither _execute_ibkr_order nor _execute_alpaca_order
# in pending_order_worker.py implement the short path (they reject any
# signal containing 'short').
LONG_ONLY_BROKERS: Set[str] = {"ibkr", "alpaca"}


# Map bot strategy type -> markets where that bot makes sense and can
# actually execute. Reasoning:
#   grid: needs continuous high-frequency quotes + bidirectional. USStock
#         dies during the 16-hour close + open gaps. OK on Crypto and Forex.
#   martingale: needs tiny add-on lots + bidirectional. Stock min-share size
#               + gap risk makes it impractical outside crypto perpetuals.
#   dca / trend: long-only by nature, fine on every market we support.
BOT_TYPE_MARKETS: Dict[str, Set[str]] = {
    "grid":       {"Crypto", "Forex"},
    "martingale": {"Crypto"},
    "dca":        {"Crypto", "USStock", "Forex"},
    "trend":      {"Crypto", "USStock", "Forex"},
}


# Markets we recognize as legal canonical values. Anything outside this set
# is considered analysis/backtest-only (e.g. CNStock, HKStock, MOEX, Futures
# generic) and may not be used for live strategies.
LIVE_MARKET_CATEGORIES: Set[str] = {"Crypto", "USStock", "Forex"}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def _norm_exchange(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _norm_market_type(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    if raw in ("futures", "future", "perp", "perpetual"):
        return "swap"
    return raw


def is_compatible_credential(exchange_id: str, market_category: str) -> bool:
    """Return True if this broker can be used for this market at all."""
    ex = _norm_exchange(exchange_id)
    if not ex:
        return False
    return market_category in BROKER_MARKETS.get(ex, {})


def allowed_market_types(exchange_id: str, market_category: str) -> Set[str]:
    """Return the set of valid market_type values for (broker, market)."""
    ex = _norm_exchange(exchange_id)
    return set(BROKER_MARKETS.get(ex, {}).get(market_category, set()))


def allowed_bot_types(market_category: str) -> Set[str]:
    """Return the set of bot_type values that can run on this market."""
    if not market_category:
        return set()
    return {bot for bot, markets in BOT_TYPE_MARKETS.items()
            if market_category in markets}


def is_long_only_broker(exchange_id: str) -> bool:
    return _norm_exchange(exchange_id) in LONG_ONLY_BROKERS


def list_supported_brokers_for_market(market_category: str) -> Set[str]:
    """Return the set of exchange_ids that can serve this market."""
    return {ex for ex, markets in BROKER_MARKETS.items()
            if market_category in markets}


# ---------------------------------------------------------------------------
# The one validator everyone calls
# ---------------------------------------------------------------------------

def validate_strategy_config(
    *,
    exchange_id: Optional[str],
    market_category: Optional[str],
    market_type: Optional[str] = None,
    trade_direction: Optional[str] = None,
    bot_type: Optional[str] = None,
    require_exchange: bool = True,
) -> None:
    """Raise ValueError if the strategy config is not implementable.

    Args:
        exchange_id: e.g. 'ibkr', 'alpaca', 'binance'. Required for live
            strategies; pass require_exchange=False for signal-mode.
        market_category: e.g. 'Crypto', 'USStock', 'Forex'.
        market_type: 'spot' or 'swap'. May be None for non-live strategies.
        trade_direction: 'long' / 'short' / 'both'. May be None.
        bot_type: 'grid' / 'martingale' / 'dca' / 'trend' or None.
        require_exchange: Set False to allow empty exchange_id (signal mode
            uses no broker).

    Checked rules:
        1. market_category must be one of LIVE_MARKET_CATEGORIES (if set).
        2. exchange_id must be a known broker (if required / set).
        3. (broker, market) combination must be in BROKER_MARKETS.
        4. market_type must be in allowed_market_types(broker, market).
        5. Long-only brokers (ibkr, alpaca) must have trade_direction='long'.
        6. For Crypto, short signals require market_type='swap' (Forex on
           MT5 stores spot but is naturally bidirectional).
        7. bot_type must be compatible with market_category.

    Returns:
        None on success.
    """
    ex = _norm_exchange(exchange_id)
    mc = (market_category or "").strip()
    mt = _norm_market_type(market_type)
    td = (trade_direction or "").strip().lower()
    bt = (bot_type or "").strip().lower()

    # Rule 1: market is one we can route in live trading
    if mc and mc not in LIVE_MARKET_CATEGORIES:
        raise ValueError(
            f"market_category='{mc}' is not supported for live trading. "
            f"Supported: {sorted(LIVE_MARKET_CATEGORIES)}. "
            "(CNStock / HKStock / MOEX / Futures are analysis-only.)"
        )

    # Rule 2 + 3: broker x market combination
    if not ex:
        if require_exchange:
            raise ValueError(
                "exchange_id is required for live strategies. "
                "Set exchange_config.exchange_id (or credential_id), "
                "or trading_config.exchange_id for legacy clients."
            )
        # Signal-mode: skip the rest. Direction/bot rules need a broker
        # context to be meaningful.
        return

    if ex not in BROKER_MARKETS:
        known = sorted(BROKER_MARKETS.keys())
        raise ValueError(
            f"Unknown exchange_id='{ex}'. Known brokers: {known}."
        )

    if mc and mc not in BROKER_MARKETS[ex]:
        supported = sorted(BROKER_MARKETS[ex].keys())
        raise ValueError(
            f"{ex.upper()} cannot trade market_category='{mc}'. "
            f"{ex.upper()} supports: {supported}. "
            f"For {mc} use one of: "
            f"{sorted(list_supported_brokers_for_market(mc))}."
        )

    # Rule 4: market_type matches broker capabilities for this market
    if mc and mt:
        allowed_mts = allowed_market_types(ex, mc)
        if mt not in allowed_mts:
            # Special-case the most common confusion so the error is helpful.
            if ex == "alpaca" and mc == "Crypto" and mt == "swap":
                raise ValueError(
                    "Alpaca crypto desk is spot-only (no perpetual / margin "
                    "product). Got market_type='swap'. Set market_type='spot', "
                    "or to trade crypto perpetuals use Binance/OKX/Bybit/"
                    "Bitget with market_type='swap'."
                )
            raise ValueError(
                f"{ex.upper()} + {mc} does not support market_type='{mt}'. "
                f"Allowed: {sorted(allowed_mts)}."
            )

    # Rule 5: long-only brokers
    if ex in LONG_ONLY_BROKERS and td and td != "long":
        raise ValueError(
            f"{ex.upper()} live execution in QuantDinger is currently "
            f"long-only (got trade_direction='{td}'). For short selling "
            f"please use a perpetual-swap crypto exchange "
            f"(Binance/OKX/Bybit/Bitget) for crypto, or MT5 for forex. "
            f"Stock short selling on IBKR/Alpaca is not yet implemented."
        )

    # Rule 6: crypto short requires swap.
    # Forex on MT5 stores market_type='spot' but is naturally bidirectional
    # (short = SELL), so this rule only applies to crypto markets.
    if mc == "Crypto" and td == "short" and mt and mt != "swap":
        raise ValueError(
            f"Short selling crypto requires market_type='swap', got '{mt}'. "
            "Crypto spot markets cannot be shorted - use a perpetual contract."
        )

    # Rule 7: bot_type compatibility.
    # NOTE: an unknown bot_type is silently allowed through rather than
    # raising. This is intentional: old strategies in production may carry
    # historical bot_type values (e.g. 'arbitrage') that the current wizard
    # no longer creates, and we don't want a no-op `update_strategy` call
    # (e.g. user just renames the bot) to start failing for them. We only
    # enforce market compatibility for bots we explicitly know about.
    if bt and bt in BOT_TYPE_MARKETS:
        if mc and mc not in BOT_TYPE_MARKETS[bt]:
            ok = sorted(BOT_TYPE_MARKETS[bt])
            raise ValueError(
                f"bot_type='{bt}' cannot run on market_category='{mc}'. "
                f"This bot is supported on: {ok}."
            )


# ---------------------------------------------------------------------------
# Serialization for the GET /api/policy/broker-market endpoint
# ---------------------------------------------------------------------------

def to_dict() -> Dict[str, object]:
    """Return a JSON-serializable snapshot of the policy.

    The frontend consumes this via GET /api/policy/broker-market and
    mirrors the same compatibility checks in the UI without having to
    re-encode them in JavaScript.
    """
    return {
        "broker_markets": {
            ex: {mc: sorted(mts) for mc, mts in markets.items()}
            for ex, markets in BROKER_MARKETS.items()
        },
        "long_only_brokers": sorted(LONG_ONLY_BROKERS),
        "bot_type_markets": {
            bt: sorted(mks) for bt, mks in BOT_TYPE_MARKETS.items()
        },
        "live_market_categories": sorted(LIVE_MARKET_CATEGORIES),
    }
