"""Machine-readable Strategy API V2 authoring contract for external agents."""

from __future__ import annotations

from typing import Any

from app.services.ai_generation_contracts import SCRIPT_STRATEGY_SYSTEM_PROMPT, STRATEGY_INSTRUMENT_IDENTITY_CONTRACT
from app.services.factors import list_factor_catalog
from app.services.fundamental_data import FUNDAMENTAL_FIELDS
from app.services.live_trading.capabilities import CRYPTO_VENUE_CAPABILITIES
from app.services.strategy_ai_capabilities import strategy_ai_capability_catalog
from app.services.strategy_direction import DIRECTION_MODES


_STARTER_TEMPLATE = '''"""
Daily Moving Average Regime
Trades a long-only SPY regime from completed daily bars with bounded exposure.
"""

# @param period int 20 Moving-average period range=5:100:5
# @param target_pct float 0.5 Target portfolio weight range=0.1:1.0:0.05


def initialize(context):
    g.symbol = "USStock:SPY"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1d", fields=["close"])
    context.set_warmup(22)
    context.set_metadata(direction_mode="long_only", strategy_family="trend")


def handle_data(context, data):
    period = int(context.params.get("period", 20))
    target_pct = float(context.params.get("target_pct", 0.5))
    bars = get_history(period + 1, "1d", "close", g.symbol)
    if len(bars) < period:
        return
    close = float(bars["close"].iloc[-1])
    average = float(bars["close"].tail(period).mean())
    order_target_percent(
        g.symbol,
        target_pct if close > average else 0.0,
        reason="daily_ma_regime_target",
    )
'''

_MULTI_TIMEFRAME_TEMPLATE = '''"""
One-Minute Cross with Hourly Confirmation
Enters on a completed one-minute golden cross only while completed hourly bars are bullish.
"""


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1m")
    context.subscribe(frequency="1h")
    context.set_warmup(62)
    context.allow_leverage(max_leverage=5)
    context.set_metadata(direction_mode="long_only", strategy_family="trend")


def handle_data(context, data):
    bars_1m = get_history(32, "1m", "close", g.symbol)
    bars_1h = get_history(52, "1h", "close", g.symbol)
    if len(bars_1m) < 31 or len(bars_1h) < 50:
        return
    close_1m = bars_1m["close"]
    fast_now = float(close_1m.tail(10).mean())
    fast_prev = float(close_1m.iloc[:-1].tail(10).mean())
    slow_now = float(close_1m.tail(30).mean())
    slow_prev = float(close_1m.iloc[:-1].tail(30).mean())
    golden_cross = fast_prev <= slow_prev and fast_now > slow_now
    death_cross = fast_prev >= slow_prev and fast_now < slow_now
    hourly_bullish = float(bars_1h["close"].tail(20).mean()) > float(
        bars_1h["close"].tail(50).mean()
    )
    position = get_position(g.symbol, position_side="long")
    amount = float(position.amount or 0.0)
    if amount <= 0 and golden_cross and hourly_bullish:
        order_target_percent(
            g.symbol,
            0.5,
            position_side="long",
            reason="one_minute_cross_hourly_confirmed",
        )
    elif amount > 0 and (death_cross or not hourly_bullish):
        order_target_percent(
            g.symbol,
            0.0,
            position_side="long",
            reason="cross_or_hourly_filter_exit",
        )
'''


def get_strategy_authoring_contract() -> dict[str, Any]:
    """Return the canonical source-ownership and runtime API contract."""
    return {
        "version": "strategy-api-v2-one-way-2026-09",
        "doc": "docs/trading/STRATEGY_DEV_GUIDE.md",
        "instrument_identity": {
            "contract": STRATEGY_INSTRUMENT_IDENTITY_CONTRACT,
            "examples": [{
                "instrument": "Crypto:00700/HKD@gate:spot",
                "market": "Crypto",
                "symbol": "00700/HKD",
                "exchange_id": "gate",
                "market_type": "spot",
                "asset_class": "equity",
                "product_type": "direct_equity",
                "api_family": "stock",
                "underlying_market": "HKStock",
                "underlying_symbol": "00700",
                "catalog_validation_required": True,
            }, {
                "instrument": "Crypto:AAPL/USD@gate:spot",
                "market": "Crypto",
                "symbol": "AAPL/USD",
                "exchange_id": "gate",
                "market_type": "spot",
                "asset_class": "equity",
                "product_type": "direct_equity",
                "api_family": "stock",
                "underlying_market": "USStock",
                "underlying_symbol": "AAPL",
                "catalog_validation_required": True,
            }, {
                "instrument": "Crypto:HK0700/USDT@binance:swap",
                "market": "Crypto",
                "symbol": "HK0700/USDT",
                "exchange_id": "binance",
                "market_type": "swap",
                "asset_class": "equity",
                "product_type": "stock_perpetual",
                "api_family": "swap",
                "underlying_market": "HKStock",
                "underlying_symbol": "00700",
                "catalog_validation_required": True,
                "direct_share_ownership": False,
            }, {
                "instrument": "Crypto:NVDAB/USDT@binance:spot",
                "market": "Crypto",
                "symbol": "NVDAB/USDT",
                "exchange_id": "binance",
                "market_type": "spot",
                "asset_class": "equity",
                "product_type": "tokenized_equity",
                "api_family": "spot",
                "underlying_market": "USStock",
                "underlying_symbol": "NVDA",
                "catalog_validation_required": True,
                "direct_share_ownership": False,
            }],
            "discovery_filters": {
                "required": ["exchange_id", "market_type", "product_type"],
                "gate_direct_equity": {
                    "exchange_id": "gate",
                    "market_type": "spot",
                    "product_type": "direct_equity",
                    "search_examples": ["00700", "AAPL"],
                },
                "binance_hk_equity_perpetual": {
                    "exchange_id": "binance",
                    "market_type": "swap",
                    "product_type": "stock_perpetual",
                    "search_examples": ["HK0700", "TENCENT"],
                },
                "binance_bstock": {
                    "exchange_id": "binance",
                    "market_type": "spot",
                    "product_type": "tokenized_equity",
                    "search_examples": ["NVDAB", "AAPLB"],
                },
            },
            "venue_capabilities": {
                exchange_id: [
                    {
                        "product_type": product_type,
                        "market_type": market_type,
                        "api_family": api_family,
                    }
                    for product_type, market_type, api_family in sorted(
                        capability.equity_api_families
                    )
                ]
                for exchange_id, capability in sorted(CRYPTO_VENUE_CAPABILITIES.items())
            },
            "research_or_broker_instruments": ["HKStock:00700.HK", "USStock:AAPL"],
        },
        "dataRequirements": {
            "fundamentalFields": list(FUNDAMENTAL_FIELDS),
            "netIncomeBasis": "latest_reported_period",
            "netIncomeTtmBasis": "four_consecutive_reported_quarters",
            "historicalUniverseCoverageField": "history_from",
            "historicalSnapshotBackfillAllowed": False,
            "liveCancellationStatus": "cancel_pending_until_exchange_confirmation",
        },
        "workflow": [
            "1. Fetch this contract before generating Strategy API V2 source.",
            "2. Generate complete Python source; never send natural language as code.",
            "3. Compile with /api/agent/v1/strategy-sources/compile and repair every validation error.",
            "4. Save the validated draft with /api/agent/v1/strategy-sources.",
            "5. Backtest the saved or validated source before creating a stopped deployment.",
        ],
        "ownership": {
            "source": [
                "universe",
                "market",
                "instrument_type",
                "frequency",
                "frequencies",
                "subscriptions",
                "direction",
                "sizing",
                "entries",
                "exits",
                "risk",
                "schedules",
            ],
            "run_panel": ["initial_capital", "date_range", "permitted_swap_leverage"],
        },
        "required": [
            "A metadata docstring whose first non-empty line is the strategy name",
            "initialize(context) with context.set_universe(...) and one context.subscribe(...) call per used timeframe",
            "At least one executable handler or registered schedule callback",
            "Canonical instruments such as Crypto:SOL/USDT@spot or USStock:SPY",
        ],
        "forbidden": [
            "get_current_data; use data.current(symbol, field='close')",
            "Position.quantity or Position.cost_basis; use amount and avg_cost",
            "context.run_daily/context.run_weekly/context.run_monthly; schedule helpers are global",
            "context.params reads inside initialize(context)",
            "Run-panel overrides for symbol, market type, frequency, or leverage permission",
            "File, network, process, reflection, or dynamic execution APIs",
        ],
        "timeframes": {
            "native": ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"],
            "maximum_subscriptions": 8,
            "weekly_literal": "1w",
            "monthly_supported": False,
            "driver": "fastest subscribed timeframe",
            "higher_timeframe_visibility": "completed bars only",
            "rules": [
                "Single-timeframe is the default; never add confirmation timeframes unless the user explicitly requests them.",
                "Preserve every timeframe explicitly requested by the user.",
                "Subscribe every timeframe read by history, current, indicator, or factor APIs.",
                "Never resample the driving frame to emulate a native higher timeframe.",
                "Guard each timeframe's returned history length independently.",
            ],
        },
        "direction_modes": {
            "allowed": sorted(DIRECTION_MODES),
            "net_bidirectional": "one_way",
            "hedged_bidirectional": "both",
            "swap_rule": (
                "Every new Crypto swap strategy declares direction_mode. one_way uses a signed "
                "net position and omits position_side; hedge-leg modes pass position_side on each "
                "contract position read and order."
            ),
        },
        "capability_packs": strategy_ai_capability_catalog(),
        "technical_factor_catalog": list_factor_catalog(factor_type="technical"),
        "system_contract": SCRIPT_STRATEGY_SYSTEM_PROMPT,
        "starter_template": _STARTER_TEMPLATE,
        "multi_timeframe_template": _MULTI_TIMEFRAME_TEMPLATE,
    }
