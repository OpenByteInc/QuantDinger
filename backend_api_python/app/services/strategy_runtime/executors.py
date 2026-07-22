"""Built-in executor strategy contracts and preview helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


EXECUTOR_TYPES = ("grid", "dca", "martingale", "layered_martingale")


def executor_engine_compatibility() -> Dict[str, Any]:
    return {
        "strategy": {
            "supported": True,
            "api_version": 2,
            "editable_source": True,
        },
        "backtest": {
            "supported": True,
            "engine": "quantdinger-strategy-api-v2",
        },
        "live": {
            "supported": True,
            "engine": "quantdinger-strategy-api-v2",
            "credential_required": True,
        },
        "markets": ["Crypto"],
        "market_types": ["spot", "swap"],
        "sides": ["long", "short", "neutral"],
    }


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _ratio(value: Any, default: float = 0.0) -> float:
    out = _float(value, default)
    if abs(out) > 1:
        out = out / 100.0
    return out


def _trailing_take_profit_config(
    cfg: Dict[str, Any],
    *,
    default_activation: float,
) -> Dict[str, Any]:
    activation_raw = (
        cfg.get("trailing_activation_pct")
        if "trailing_activation_pct" in cfg
        else cfg.get("trailingActivationPct")
    )
    callback_raw = (
        cfg.get("trailing_callback_pct")
        if "trailing_callback_pct" in cfg
        else cfg.get("trailingCallbackPct")
    )
    enabled = _bool(
        cfg.get("trailing_take_profit_enabled")
        if "trailing_take_profit_enabled" in cfg
        else cfg.get("trailingTakeProfitEnabled"),
        True,
    )
    activation = max(
        0.0,
        _ratio(
            activation_raw,
            default_activation,
        ),
    )
    callback = max(
        0.0,
        _ratio(
            callback_raw,
            0.002,
        ),
    )
    return {
        "trailing_take_profit_enabled": enabled,
        "trailing_activation_pct": activation,
        "trailing_callback_pct": callback,
    }


def _ratio_list(value: Any, defaults: List[float], *, expected: int = 0) -> List[float]:
    raw_values: List[Any]
    if isinstance(value, (list, tuple)):
        raw_values = list(value)
    elif isinstance(value, str) and value.strip():
        raw_values = [part.strip() for part in value.split(",") if part.strip()]
    else:
        raw_values = []
    out = [_ratio(item, 0.0) for item in raw_values]
    if not out:
        out = list(defaults)
    target = max(0, int(expected or 0))
    if target > 0:
        if not out:
            out = [0.0]
        while len(out) < target:
            out.append(out[-1])
        out = out[:target]
    return [max(0.0, float(item or 0.0)) for item in out]


def _side(value: Any, *, allow_neutral: bool = False) -> str:
    out = str(value or "long").strip().lower()
    if allow_neutral and out == "neutral":
        return "neutral"
    return "short" if out == "short" else "long"


def _market_type(value: Any) -> str:
    out = str(value or "swap").strip().lower()
    if out in ("future", "futures", "perp", "perpetual"):
        return "swap"
    return "spot" if out == "spot" else "swap"


def _timeframe_minutes(value: Any) -> int:
    text = str(value or "1m").strip().lower()
    units = {
        "m": 1,
        "h": 60,
        "d": 1440,
        "w": 10080,
    }
    try:
        amount = int(text[:-1])
    except (TypeError, ValueError):
        return 1
    return max(1, amount * units.get(text[-1:], 1))


def _linspace(start: float, end: float, count: int) -> List[float]:
    if count <= 1:
        return [round((start + end) / 2.0, 8)]
    step = (end - start) / float(count - 1)
    return [round(start + step * i, 8) for i in range(count)]


def _geospace(start: float, end: float, count: int) -> List[float]:
    if count <= 1 or start <= 0 or end <= 0:
        return _linspace(start, end, count)
    ratio = (end / start) ** (1.0 / float(count - 1))
    return [round(start * (ratio ** i), 8) for i in range(count)]


def _basket_take_profit_price(
    *,
    total_quote: float,
    total_quantity: float,
    side: str,
    take_profit: float,
) -> float:
    if total_quote <= 0 or total_quantity <= 0:
        return 0.0
    average_price = total_quote / total_quantity
    if side == "short":
        return average_price * (1.0 - take_profit)
    return average_price * (1.0 + take_profit)


@dataclass
class ExecutorLevel:
    level: int
    action: str
    side: str
    price: float
    amount_quote: float
    take_profit_price: float = 0.0
    trigger_pct: float = 0.0
    state: str = "not_active"
    layer_index: int = 0
    order_index: int = 0
    scheduled_bar: int = 0
    scheduled_offset_minutes: int = 0
    cumulative_amount_quote: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "level": self.level,
            "layer_index": self.layer_index or self.level,
            "order_index": self.order_index or 1,
            "action": self.action,
            "side": self.side,
            "price": round(float(self.price or 0.0), 8),
            "amount_quote": round(float(self.amount_quote or 0.0), 8),
            "take_profit_price": round(float(self.take_profit_price or 0.0), 8),
            "trigger_pct": round(float(self.trigger_pct or 0.0), 8),
            "state": self.state,
        }
        if self.scheduled_bar:
            payload["scheduled_bar"] = int(self.scheduled_bar)
        if self.scheduled_offset_minutes:
            payload["scheduled_offset_minutes"] = int(
                self.scheduled_offset_minutes
            )
        if self.cumulative_amount_quote:
            payload["cumulative_amount_quote"] = round(
                float(self.cumulative_amount_quote), 8
            )
        return payload


@dataclass
class ExecutorPreview:
    executor_type: str
    config: Dict[str, Any]
    levels: List[ExecutorLevel] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "executor_type": self.executor_type,
            "config": dict(self.config),
            "levels": [level.to_dict() for level in self.levels],
            "warnings": list(self.warnings),
            "summary": {
                "level_count": len(self.levels),
                "total_amount_quote": round(sum(level.amount_quote for level in self.levels), 8),
                "first_price": round(self.levels[0].price, 8) if self.levels else 0.0,
                "last_price": round(self.levels[-1].price, 8) if self.levels else 0.0,
            },
        }


def normalize_executor_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    executor_type = str(raw.get("executor_type") or raw.get("type") or "grid").strip().lower()
    if executor_type not in EXECUTOR_TYPES:
        raise ValueError(f"unsupported_executor_type:{executor_type}")
    symbol = str(raw.get("symbol") or "BTC/USDT").strip() or "BTC/USDT"
    market_type = _market_type(raw.get("market_type") or raw.get("marketType"))
    side = _side(raw.get("side"), allow_neutral=executor_type == "grid")
    if executor_type == "dca":
        market_type = "spot"
        side = "long"
        raw["timeframe"] = "1H"
    if side == "neutral" and market_type == "spot":
        raise ValueError("NEUTRAL_GRID_REQUIRES_SWAP")
    leverage = 1 if executor_type == "dca" else max(1, _int(raw.get("leverage"), 1))
    execution_mode = str(raw.get("execution_mode") or raw.get("executionMode") or "signal").strip().lower()
    if execution_mode not in ("signal", "live"):
        execution_mode = "signal"
    return {
        **raw,
        "executor_type": executor_type,
        "symbol": symbol,
        "side": side,
        "market_type": market_type,
        "leverage": leverage,
        "execution_mode": execution_mode,
    }


def preview_executor(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = normalize_executor_payload(payload)
    kind = cfg["executor_type"]
    if kind == "grid":
        preview = _preview_grid(cfg)
    elif kind == "dca":
        preview = _preview_dca(cfg)
    elif kind == "martingale":
        preview = _preview_martingale(cfg)
    else:
        preview = _preview_layered_martingale(cfg)
    return preview.to_dict()


def executor_templates() -> Dict[str, Any]:
    return {
        "compatibility": executor_engine_compatibility(),
        "items": [
            {
                "executor_type": "grid",
                "defaults": {
                    "side": "long",
                    "market_type": "swap",
                    "timeframe": "1m",
                    "dynamic_anchor": True,
                    "start_price": 0.98,
                    "end_price": 1.02,
                    "limit_price": 0.97,
                    "grid_count": 8,
                    "total_amount_quote": 8,
                    "initial_position_pct": 0.6,
                    "take_profit_pct": 0.004,
                    "max_open_orders": 4,
                    "grid_mode": "arithmetic",
                    "min_spread_between_orders": 0.0005,
                },
            },
            {
                "executor_type": "dca",
                "defaults": {
                    "side": "long",
                    "market_type": "spot",
                    "timeframe": "1H",
                    "dynamic_anchor": True,
                    "entry_price": 1,
                    "dca_interval_minutes": 1440,
                    "dca_max_orders": 5,
                    "dca_total_budget_pct": 1.0,
                    "dca_price_filter_enabled": False,
                    "dca_max_adverse_price_pct": 0.05,
                    "take_profit_pct": 0.006,
                    "trailing_take_profit_enabled": True,
                    "trailing_activation_pct": 0.006,
                    "trailing_callback_pct": 0.002,
                    "hard_stop_pct": 0.12,
                    "max_entry_drift_pct": 0.03,
                },
            },
            {
                "executor_type": "martingale",
                "defaults": {
                    "side": "long",
                    "market_type": "swap",
                    "timeframe": "1m",
                    "dynamic_anchor": True,
                    "entry_price": 1,
                    "base_order_size": 0.8,
                    "safety_order_size": 1,
                    "price_deviation_pct": 0.012,
                    "step_multiplier": 1.4,
                    "volume_multiplier": 1.6,
                    "max_layers": 5,
                    "take_profit_pct": 0.005,
                    "trailing_take_profit_enabled": True,
                    "trailing_activation_pct": 0.005,
                    "trailing_callback_pct": 0.002,
                    "hard_stop_pct": 0.12,
                    "max_entry_drift_pct": 0.03,
                },
            },
            {
                "executor_type": "layered_martingale",
                "defaults": {
                    "side": "long",
                    "market_type": "swap",
                    "timeframe": "1m",
                    "dynamic_anchor": True,
                    "entry_price": 1,
                    "layer_count": 5,
                    "orders_per_layer": 3,
                    "base_order_size": 1,
                    "volume_multiplier": 1.8,
                    "intra_spacing_1_pct": 0.005,
                    "intra_spacing_2_pct": 0.008,
                    "inter_spacing_1_pct": 0.012,
                    "inter_spacing_2_pct": 0.015,
                    "inter_spacing_3_pct": 0.018,
                    "inter_spacing_4_pct": 0.022,
                    "take_profit_pct": 0.006,
                    "trailing_take_profit_enabled": True,
                    "trailing_activation_pct": 0.006,
                    "trailing_callback_pct": 0.002,
                    "hard_stop_pct": 0.12,
                    "max_entry_drift_pct": 0.03,
                },
            },
        ]
    }


def build_executor_strategy_payload(payload: Dict[str, Any], *, user_id: int) -> Dict[str, Any]:
    from app.services.strategy_v2 import compile_strategy_v2

    cfg = normalize_executor_payload(payload)
    exchange_config = cfg.get("exchange_config") or cfg.get("exchangeConfig") or {}
    if not isinstance(exchange_config, dict):
        exchange_config = {}
    if cfg["execution_mode"] == "live" and not exchange_config.get("credential_id"):
        raise ValueError("LIVE_EXECUTOR_CREDENTIAL_REQUIRED")
    preview = preview_executor(cfg)
    kind = cfg["executor_type"]
    strategy_name = str(cfg.get("strategy_name") or cfg.get("name") or f"{kind.upper()} {cfg['symbol']}").strip()
    timeframe = str(cfg.get("timeframe") or "1m").strip() or "1m"
    initial_capital = max(10.0, _float(cfg.get("initial_capital") or cfg.get("investment_amount"), 1000.0))
    trade_direction = "long" if cfg["market_type"] == "spot" else cfg["side"]
    executor_config = preview["config"]
    executor_config["dynamic_anchor"] = bool(cfg.get("dynamic_anchor"))
    if cfg["market_type"] == "spot":
        executor_config["side"] = "long"
    leverage_enabled = cfg["market_type"] == "swap" and cfg["leverage"] > 1
    trading_config = {
        "api_version": 2,
        "strategy_family": "robot",
        "executor_type": kind,
        "executor_config": executor_config,
        "executor_preview": preview,
    }
    if kind == "grid" and trade_direction == "neutral":
        grid_count = max(2, int(executor_config.get("grid_count") or 2))
        total_amount = max(0.0, float(executor_config.get("total_amount_quote") or 0.0))
        trading_config.update({
            "bot_type": "grid",
            "symbol": cfg["symbol"],
            "market_type": cfg["market_type"],
            "leverage": cfg["leverage"],
            "margin_mode": str(cfg.get("margin_mode") or cfg.get("marginMode") or "cross"),
            "stop_loss_pct": float(executor_config.get("hard_stop_pct") or 0.0),
            "take_profit_pct": float(executor_config.get("take_profit_pct") or 0.0),
            "bot_params": {
                "upperPrice": float(executor_config.get("end_price") or 0.0),
                "lowerPrice": float(executor_config.get("start_price") or 0.0),
                "gridCount": grid_count,
                "amountPerGrid": total_amount / grid_count if grid_count else 0.0,
                "gridMode": str(executor_config.get("grid_mode") or "arithmetic"),
                "gridDirection": "neutral",
                "initialPositionPct": 0.0,
                "orderMode": "maker",
                "boundaryAction": "pause",
                "maxOpenOrders": int(executor_config.get("max_open_orders") or 4),
                "minSpreadBetweenOrders": float(
                    executor_config.get("min_spread_between_orders") or 0.0
                ),
                "orderFrequency": int(executor_config.get("order_frequency") or 0),
                "dynamicAnchor": bool(executor_config.get("dynamic_anchor")),
            },
        })
    generated_code = _executor_code(
        kind,
        executor_config,
        preview,
        symbol=cfg["symbol"],
        market_type=cfg["market_type"],
        timeframe=timeframe,
    )
    code = (
        f'"""\n{strategy_name}\n'
        f'Strategy API V2 {kind.replace("_", " ")} robot generated from the visual builder.\n'
        f'"""\n\n{generated_code}'
    )
    program = compile_strategy_v2(code)
    return {
        "user_id": user_id,
        "strategy_name": strategy_name,
        "strategy_type": "StrategyV2",
        "code": code,
        "asset_type": "script",
        "template_key": f"robot_v2_{kind}",
        "description": f"Strategy API V2 {kind.replace('_', ' ')} robot.",
        "market_category": "Crypto",
        "execution_mode": cfg["execution_mode"],
        "status": "stopped",
        "symbol": cfg["symbol"],
        "timeframe": timeframe,
        "market_type": cfg["market_type"],
        "trade_direction": trade_direction,
        "leverage": cfg["leverage"],
        "leverage_enabled": leverage_enabled,
        "initial_capital": initial_capital,
        "trading_config": trading_config,
        "exchange_config": exchange_config,
        "notification_config": cfg.get("notification_config") or cfg.get("notificationConfig") or {},
        "metadata": {
            "api_version": 2,
            "source": "robot_builder",
            "executor_type": kind,
            "executor_config": executor_config,
            "strategy_manifest": program.manifest.metadata(),
        },
        "compatibility": executor_engine_compatibility(),
    }


def _preview_grid(cfg: Dict[str, Any]) -> ExecutorPreview:
    start = _float(cfg.get("start_price") or cfg.get("startPrice"), 0.0)
    end = _float(cfg.get("end_price") or cfg.get("endPrice"), 0.0)
    count = max(2, _int(cfg.get("grid_count") or cfg.get("gridCount"), 2))
    total = max(0.0, _float(cfg.get("total_amount_quote") or cfg.get("totalAmountQuote"), float(count)))
    side = cfg["side"]
    mode = str(cfg.get("grid_mode") or cfg.get("gridMode") or "arithmetic").strip().lower()
    take_profit = max(0.0, _ratio(cfg.get("take_profit_pct") or cfg.get("takeProfitPct"), 0.004))
    hard_stop = max(0.0, _ratio(cfg.get("hard_stop_pct") or cfg.get("hardStopPct"), 0.0))
    warnings: List[str] = []
    if start <= 0 or end <= 0 or start == end:
        warnings.append("invalid_price_bounds")
    low, high = sorted([start, end])
    prices = _geospace(low, high, count) if mode == "geometric" else _linspace(low, high, count)
    reference = (low + high) / 2.0
    if side == "long":
        prices = sorted(prices, reverse=True)
    if bool(cfg.get("dynamic_anchor")):
        actionable = [
            price for price in prices
            if (side == "long" and price < reference) or (side == "short" and price > reference)
        ]
        if actionable:
            prices = actionable
    if side == "neutral":
        prices = [price for price in prices if abs(price - reference) > 1e-10]
    amount = total / max(1, len(prices))
    levels = []
    for idx, price in enumerate(prices, start=1):
        level_side = side
        if side == "neutral":
            level_side = "long" if price < reference else "short"
        tp = price * (1.0 + take_profit) if level_side == "long" else price * (1.0 - take_profit)
        levels.append(ExecutorLevel(idx, "open", level_side, price, amount, tp, 0.0))
    initial_position_raw = (
        cfg.get("initial_position_pct")
        if "initial_position_pct" in cfg
        else cfg.get("initialPositionPct", 0.6)
    )
    initial_position_pct = min(1.0, max(0.0, _ratio(initial_position_raw, 0.6)))
    if side == "neutral":
        initial_position_pct = 0.0
    config = {
        "side": side,
        "market_type": cfg["market_type"],
        "start_price": low,
        "end_price": high,
        "limit_price": _float(cfg.get("limit_price") or cfg.get("limitPrice"), low if side == "long" else high),
        "grid_count": count,
        "grid_mode": mode if mode in ("arithmetic", "geometric") else "arithmetic",
        "total_amount_quote": total,
        "initial_position_pct": initial_position_pct,
        "take_profit_pct": take_profit,
        "hard_stop_pct": hard_stop,
        "max_open_orders": max(1, _int(cfg.get("max_open_orders") or cfg.get("maxOpenOrders"), 4)),
        "min_spread_between_orders": max(0.0, _ratio(cfg.get("min_spread_between_orders") or cfg.get("minSpreadBetweenOrders"), 0.0005)),
        "order_frequency": max(0, _int(cfg.get("order_frequency") or cfg.get("orderFrequency"), 0)),
    }
    return ExecutorPreview("grid", config, levels, warnings)


def _preview_dca(cfg: Dict[str, Any]) -> ExecutorPreview:
    entry = _float(cfg.get("entry_price") or cfg.get("entryPrice"), 1.0)
    legacy_interval_bars = max(
        1,
        _int(
            cfg.get("dca_interval_bars")
            or cfg.get("dcaIntervalBars")
            or cfg.get("interval_bars")
            or cfg.get("intervalBars"),
            60,
        ),
    )
    interval_minutes = max(
        1,
        _int(
            cfg.get("dca_interval_minutes")
            or cfg.get("dcaIntervalMinutes"),
            legacy_interval_bars * _timeframe_minutes(cfg.get("timeframe")),
        ),
    )
    max_orders = max(
        1,
        _int(
            cfg.get("dca_max_orders")
            or cfg.get("dcaMaxOrders")
            or cfg.get("max_orders")
            or cfg.get("maxOrders")
            or cfg.get("max_layers")
            or cfg.get("maxLayers"),
            5,
        ),
    )
    total_budget_pct = min(
        1.0,
        max(
            0.0,
            _ratio(
                cfg.get("dca_total_budget_pct")
                if "dca_total_budget_pct" in cfg
                else cfg.get("dcaTotalBudgetPct"),
                1.0,
            ),
        ),
    )
    price_filter_enabled = _bool(
        cfg.get("dca_price_filter_enabled")
        if "dca_price_filter_enabled" in cfg
        else cfg.get("dcaPriceFilterEnabled"),
        False,
    )
    max_adverse_price_pct = max(
        0.0,
        _ratio(
            cfg.get("dca_max_adverse_price_pct")
            if "dca_max_adverse_price_pct" in cfg
            else cfg.get("dcaMaxAdversePricePct"),
            0.05,
        ),
    )
    take_profit = max(
        0.0,
        _ratio(cfg.get("take_profit_pct") or cfg.get("takeProfitPct"), 0.006),
    )
    trailing = _trailing_take_profit_config(cfg, default_activation=take_profit)
    hard_stop = max(
        0.0,
        _ratio(cfg.get("hard_stop_pct") or cfg.get("hardStopPct"), 0.0),
    )
    order_pct = total_budget_pct / max_orders
    warnings: List[str] = []
    if total_budget_pct <= 0:
        warnings.append("missing_dca_budget")
    if price_filter_enabled and entry <= 0 and not bool(cfg.get("dynamic_anchor")):
        warnings.append("missing_entry_price")
    if trailing["trailing_take_profit_enabled"] and (
        trailing["trailing_activation_pct"] <= 0
        or trailing["trailing_callback_pct"] <= 0
        or trailing["trailing_callback_pct"] >= trailing["trailing_activation_pct"]
    ):
        warnings.append("invalid_trailing_take_profit")

    levels = []
    cumulative = 0.0
    for order_index in range(1, max_orders + 1):
        cumulative += order_pct
        levels.append(
            ExecutorLevel(
                order_index,
                "open" if order_index == 1 else "add",
                cfg["side"],
                entry,
                order_pct,
                0.0,
                0.0,
                layer_index=order_index,
                order_index=order_index,
                scheduled_offset_minutes=(order_index - 1) * interval_minutes,
                cumulative_amount_quote=cumulative,
            )
        )
    config = {
        "side": cfg["side"],
        "market_type": cfg["market_type"],
        "entry_price": entry,
        "dca_interval_minutes": interval_minutes,
        "dca_max_orders": max_orders,
        "dca_total_budget_pct": total_budget_pct,
        "dca_order_pct": order_pct,
        "dca_price_filter_enabled": price_filter_enabled,
        "dca_max_adverse_price_pct": max_adverse_price_pct,
        "take_profit_pct": take_profit,
        **trailing,
        "hard_stop_pct": hard_stop,
    }
    return ExecutorPreview("dca", config, levels, warnings)


def _preview_martingale(cfg: Dict[str, Any]) -> ExecutorPreview:
    return _preview_layered_dca(cfg, "martingale")


def _preview_layered_martingale(cfg: Dict[str, Any]) -> ExecutorPreview:
    entry = _float(cfg.get("entry_price") or cfg.get("entryPrice"), 0.0)
    layer_count = max(1, _int(cfg.get("layer_count") or cfg.get("layerCount"), 5))
    orders_per_layer = max(1, _int(cfg.get("orders_per_layer") or cfg.get("ordersPerLayer"), 3))
    base = max(0.0, _float(cfg.get("base_order_size") or cfg.get("baseOrderSize"), 0.0))
    volume_mult = max(1.0, _float(cfg.get("volume_multiplier") or cfg.get("volumeMultiplier"), 1.8))
    take_profit = max(0.0, _ratio(cfg.get("take_profit_pct") or cfg.get("takeProfitPct"), 0.006))
    trailing = _trailing_take_profit_config(cfg, default_activation=take_profit)
    hard_stop = max(0.0, _ratio(cfg.get("hard_stop_pct") or cfg.get("hardStopPct"), 0.0))
    max_entry_drift = max(0.0, _ratio(cfg.get("max_entry_drift_pct") or cfg.get("maxEntryDriftPct"), 0.03))
    side = cfg["side"]
    intra_defaults = [
        _ratio(cfg.get("intra_spacing_1_pct") or cfg.get("intraSpacing1Pct"), 0.005),
        _ratio(cfg.get("intra_spacing_2_pct") or cfg.get("intraSpacing2Pct"), 0.008),
    ]
    inter_defaults = [
        _ratio(cfg.get("inter_spacing_1_pct") or cfg.get("interSpacing1Pct"), 0.012),
        _ratio(cfg.get("inter_spacing_2_pct") or cfg.get("interSpacing2Pct"), 0.015),
        _ratio(cfg.get("inter_spacing_3_pct") or cfg.get("interSpacing3Pct"), 0.018),
        _ratio(cfg.get("inter_spacing_4_pct") or cfg.get("interSpacing4Pct"), 0.022),
    ]
    intra_spacings = _ratio_list(
        cfg.get("intra_spacings") or cfg.get("intraSpacings"),
        intra_defaults,
        expected=max(0, orders_per_layer - 1),
    )
    inter_spacings = _ratio_list(
        cfg.get("inter_spacings") or cfg.get("interSpacings"),
        inter_defaults,
        expected=max(0, layer_count - 1),
    )
    warnings: List[str] = []
    if entry <= 0:
        warnings.append("missing_entry_price")
    if base <= 0:
        warnings.append("missing_base_order_size")
    if trailing["trailing_take_profit_enabled"] and (
        trailing["trailing_activation_pct"] <= 0
        or trailing["trailing_callback_pct"] <= 0
        or trailing["trailing_callback_pct"] >= trailing["trailing_activation_pct"]
    ):
        warnings.append("invalid_trailing_take_profit")
    levels: List[ExecutorLevel] = []
    price = entry
    seq = 1
    cumulative_quote = 0.0
    cumulative_quantity = 0.0
    for layer_idx in range(1, layer_count + 1):
        for order_idx in range(1, orders_per_layer + 1):
            if seq == 1:
                price = entry
                trigger = 0.0
            elif order_idx == 1:
                spacing = inter_spacings[layer_idx - 2] if layer_idx >= 2 and inter_spacings else 0.0
                price = price * (1.0 - spacing) if side == "long" else price * (1.0 + spacing)
                trigger = spacing
            else:
                spacing = intra_spacings[order_idx - 2] if intra_spacings else 0.0
                price = price * (1.0 - spacing) if side == "long" else price * (1.0 + spacing)
                trigger = spacing
            amount = base * (volume_mult ** (order_idx - 1))
            cumulative_quote += amount
            if price > 0:
                cumulative_quantity += amount / price
            exit_reference = (
                trailing["trailing_activation_pct"]
                if trailing["trailing_take_profit_enabled"]
                else take_profit
            )
            tp = _basket_take_profit_price(
                total_quote=cumulative_quote,
                total_quantity=cumulative_quantity,
                side=side,
                take_profit=exit_reference,
            )
            levels.append(
                ExecutorLevel(
                    seq,
                    "open" if seq == 1 else "add",
                    side,
                    price,
                    amount,
                    tp,
                    trigger,
                    layer_index=layer_idx,
                    order_index=order_idx,
                )
            )
            seq += 1
    config = {
        "side": side,
        "market_type": cfg["market_type"],
        "entry_price": entry,
        "layer_count": layer_count,
        "orders_per_layer": orders_per_layer,
        "base_order_size": base,
        "volume_multiplier": volume_mult,
        "intra_spacings": intra_spacings,
        "inter_spacings": inter_spacings,
        "take_profit_pct": take_profit,
        **trailing,
        "hard_stop_pct": hard_stop,
        "max_entry_drift_pct": max_entry_drift,
    }
    return ExecutorPreview("layered_martingale", config, levels, warnings)


def _preview_layered_dca(cfg: Dict[str, Any], kind: str) -> ExecutorPreview:
    entry = _float(cfg.get("entry_price") or cfg.get("entryPrice"), 0.0)
    base = max(0.0, _float(cfg.get("base_order_size") or cfg.get("baseOrderSize"), 0.0))
    safety = max(0.0, _float(cfg.get("safety_order_size") or cfg.get("safetyOrderSize"), base))
    max_layers = max(1, _int(cfg.get("max_layers") or cfg.get("maxLayers"), 1))
    deviation = max(0.0, _ratio(cfg.get("price_deviation_pct") or cfg.get("priceDeviationPct"), 0.01))
    step_mult = max(1.0, _float(cfg.get("step_multiplier") or cfg.get("stepMultiplier"), 1.0))
    volume_mult = max(1.0, _float(cfg.get("volume_multiplier") or cfg.get("volumeMultiplier"), 1.0))
    take_profit = max(0.0, _ratio(cfg.get("take_profit_pct") or cfg.get("takeProfitPct"), 0.005))
    trailing = _trailing_take_profit_config(cfg, default_activation=take_profit)
    max_entry_drift = max(0.0, _ratio(cfg.get("max_entry_drift_pct") or cfg.get("maxEntryDriftPct"), 0.03))
    side = cfg["side"]
    warnings: List[str] = []
    if entry <= 0:
        warnings.append("missing_entry_price")
    if base <= 0:
        warnings.append("missing_base_order_size")
    if trailing["trailing_take_profit_enabled"] and (
        trailing["trailing_activation_pct"] <= 0
        or trailing["trailing_callback_pct"] <= 0
        or trailing["trailing_callback_pct"] >= trailing["trailing_activation_pct"]
    ):
        warnings.append("invalid_trailing_take_profit")
    levels = []
    cumulative_deviation = 0.0
    cumulative_quote = 0.0
    cumulative_quantity = 0.0
    for layer in range(1, max_layers + 1):
        if layer == 1:
            amount = base
            price = entry
            trigger = 0.0
        else:
            trigger = deviation * (step_mult ** (layer - 2))
            cumulative_deviation += trigger
            price = entry * (1.0 - cumulative_deviation) if side == "long" else entry * (1.0 + cumulative_deviation)
            amount = safety * (volume_mult ** (layer - 2))
        cumulative_quote += amount
        if price > 0:
            cumulative_quantity += amount / price
        exit_reference = (
            trailing["trailing_activation_pct"]
            if trailing["trailing_take_profit_enabled"]
            else take_profit
        )
        tp = _basket_take_profit_price(
            total_quote=cumulative_quote,
            total_quantity=cumulative_quantity,
            side=side,
            take_profit=exit_reference,
        )
        levels.append(ExecutorLevel(layer, "open" if layer == 1 else "add", side, price, amount, tp, trigger))
    config = {
        "side": side,
        "market_type": cfg["market_type"],
        "entry_price": entry,
        "base_order_size": base,
        "safety_order_size": safety,
        "max_layers": max_layers,
        "price_deviation_pct": deviation,
        "step_multiplier": step_mult,
        "volume_multiplier": volume_mult,
        "take_profit_pct": take_profit,
        **trailing,
        "hard_stop_pct": max(0.0, _ratio(cfg.get("hard_stop_pct") or cfg.get("hardStopPct"), 0.0)),
        "max_entry_drift_pct": max_entry_drift,
    }
    return ExecutorPreview(kind, config, levels, warnings)


def _executor_code(
    kind: str,
    config: Dict[str, Any],
    preview: Dict[str, Any],
    *,
    symbol: str,
    market_type: str,
    timeframe: str,
) -> str:
    from .robot_v2 import build_robot_v2_source

    return build_robot_v2_source(
        kind,
        config,
        preview,
        symbol=symbol,
        market_type=market_type,
        timeframe=timeframe,
    )
