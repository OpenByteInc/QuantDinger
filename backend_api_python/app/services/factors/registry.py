"""Built-in technical and point-in-time fundamental factors."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Optional

import numpy as np
import pandas as pd


class FactorError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FactorDefinition:
    factor_id: str
    version: str
    name_i18n_key: str
    description_i18n_key: str
    category: str
    factor_type: str
    required_fields: tuple[str, ...]
    default_params: dict[str, Any] = field(default_factory=dict)
    parameter_schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    direction_hint: str = "neutral"
    supported_contexts: tuple[str, ...] = ("portfolio",)
    default_warmup_bars: int = 0
    compute: Callable[[pd.DataFrame, Mapping[str, Any]], float] = field(repr=False, compare=False, default=None)

    def metadata(self) -> dict:
        value = asdict(self)
        value.pop("compute", None)
        return value


def _technical_warmup(factor_id: str, params: Mapping[str, Any]) -> int:
    period = int(params.get("period") or 1)
    if factor_id in {
        "momentum",
        "roc",
        "rsi",
        "downside_volatility",
        "skewness",
        "kurtosis",
    }:
        return period + 1
    if factor_id == "ema_slope":
        return period + int(params.get("slope_period") or 1)
    if factor_id == "macd":
        return int(params.get("slow_period") or 26) + int(params.get("signal_period") or 9)
    if factor_id == "stochastic":
        return period + int(params.get("smooth_k") or 1) + int(params.get("smooth_d") or 1) - 2
    if factor_id == "trix":
        return period * 3 + 1
    if factor_id == "dema":
        return period * 2
    if factor_id == "tema":
        return period * 3
    if factor_id == "hma":
        return period + int(math.sqrt(period))
    if factor_id in {"cmo", "efficiency_ratio", "kama", "ulcer_index", "choppiness", "vortex"}:
        return period + 1
    if factor_id in {"ppo", "awesome_oscillator"}:
        return int(params.get("slow_period") or period)
    if factor_id == "ultimate_oscillator":
        return int(params.get("slow_period") or 28) + 1
    if factor_id == "tsi":
        return int(params.get("slow_period") or 25) + int(params.get("fast_period") or 13) + 1
    if factor_id == "adx":
        return period * 2
    if factor_id == "chaikin_oscillator":
        return int(params.get("slow_period") or 10)
    if factor_id in {"obv", "ad_line"}:
        return period + 1
    return max(1, period)


def _technical(
    factor_id: str,
    category: str,
    fields: tuple[str, ...],
    params: dict,
    direction: str,
    compute: Callable,
) -> FactorDefinition:
    return FactorDefinition(
        factor_id=factor_id,
        version="1.0.0",
        name_i18n_key=f"factor.{factor_id}.name",
        description_i18n_key=f"factor.{factor_id}.description",
        category=category,
        factor_type="technical",
        required_fields=fields,
        default_params=params,
        parameter_schema=_parameter_schema(factor_id, params),
        direction_hint=direction,
        supported_contexts=("cta", "portfolio"),
        default_warmup_bars=_technical_warmup(factor_id, params),
        compute=compute,
    )


_OUTPUT_OPTIONS = {
    "ad_line": ("value", "slope"),
    "adx": ("adx", "plus_di", "minus_di"),
    "aroon": ("up", "down", "oscillator"),
    "bollinger_bands": ("upper", "middle", "lower", "bandwidth", "position"),
    "donchian_channels": ("upper", "middle", "lower", "position"),
    "elder_ray": ("bull", "bear"),
    "kdj": ("k", "d", "j"),
    "keltner_channels": ("upper", "middle", "lower", "position"),
    "macd": ("line", "signal", "histogram"),
    "obv": ("value", "slope"),
    "stochastic": ("k", "d"),
    "supertrend": ("direction", "line"),
    "vwap": ("value", "distance"),
    "vortex": ("plus", "minus", "oscillator"),
}


def _parameter_schema(factor_id: str, params: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    schema: dict[str, dict[str, Any]] = {}
    for key, value in params.items():
        if key == "output" and factor_id in _OUTPUT_OPTIONS:
            schema[key] = {"type": "enum", "options": list(_OUTPUT_OPTIONS[factor_id])}
        elif isinstance(value, bool):
            schema[key] = {"type": "boolean"}
        elif isinstance(value, int):
            schema[key] = {"type": "integer", "minimum": 1, "maximum": 5000, "step": 1}
        elif isinstance(value, float):
            schema[key] = {"type": "number", "minimum": 0.000001, "step": 0.1}
        else:
            schema[key] = {"type": "string"}
    return schema


def _fundamental(
    factor_id: str,
    category: str,
    fields: tuple[str, ...],
    direction: str,
    compute: Callable,
) -> FactorDefinition:
    return FactorDefinition(
        factor_id=factor_id,
        version="1.0.0",
        name_i18n_key=f"factor.{factor_id}.name",
        description_i18n_key=f"factor.{factor_id}.description",
        category=category,
        factor_type="fundamental",
        required_fields=fields,
        default_params={},
        direction_hint=direction,
        supported_contexts=("portfolio",),
        compute=compute,
    )


_FACTORS = {
    definition.factor_id: definition
    for definition in (
        _technical("sma", "trend", ("close",), {"period": 20}, "neutral", lambda f, p: _sma(f, p)),
        _technical("ema", "trend", ("close",), {"period": 20}, "neutral", lambda f, p: _ema(f, p)),
        _technical("sma_distance", "trend", ("close",), {"period": 20}, "higher_is_bullish", lambda f, p: _ma_distance(f, p, exponential=False)),
        _technical("ema_distance", "trend", ("close",), {"period": 20}, "higher_is_bullish", lambda f, p: _ma_distance(f, p, exponential=True)),
        _technical("momentum", "momentum", ("close",), {"period": 60}, "higher_is_bullish", lambda f, p: _return(f, p)),
        _technical("roc", "momentum", ("close",), {"period": 12}, "higher_is_bullish", lambda f, p: _return(f, p)),
        _technical("rsi", "momentum", ("close",), {"period": 14}, "neutral", lambda f, p: _rsi(f, p)),
        _technical("macd", "trend", ("close",), {"fast_period": 12, "slow_period": 26, "signal_period": 9, "output": "histogram"}, "higher_is_bullish", lambda f, p: _macd(f, p)),
        _technical("bollinger_bands", "volatility", ("close",), {"period": 20, "stddev": 2.0, "output": "position"}, "neutral", lambda f, p: _bollinger(f, p)),
        _technical("stochastic", "momentum", ("high", "low", "close"), {"period": 14, "smooth_k": 3, "smooth_d": 3, "output": "k"}, "neutral", lambda f, p: _stochastic(f, p)),
        _technical("kdj", "momentum", ("high", "low", "close"), {"period": 9, "k_period": 3, "d_period": 3, "output": "j"}, "neutral", lambda f, p: _kdj(f, p)),
        _technical("cci", "momentum", ("high", "low", "close"), {"period": 20}, "neutral", lambda f, p: _cci(f, p)),
        _technical("williams_r", "momentum", ("high", "low", "close"), {"period": 14}, "neutral", lambda f, p: _williams_r(f, p)),
        _technical("mfi", "volume", ("high", "low", "close", "volume"), {"period": 14}, "neutral", lambda f, p: _mfi(f, p)),
        _technical("adx", "trend", ("high", "low", "close"), {"period": 14, "output": "adx"}, "neutral", lambda f, p: _adx(f, p)),
        _technical("aroon", "trend", ("high", "low"), {"period": 25, "output": "oscillator"}, "higher_is_bullish", lambda f, p: _aroon(f, p)),
        _technical("trix", "trend", ("close",), {"period": 15}, "higher_is_bullish", lambda f, p: _trix(f, p)),
        _technical("supertrend", "trend", ("high", "low", "close"), {"period": 10, "multiplier": 3.0, "output": "direction"}, "higher_is_bullish", lambda f, p: _supertrend(f, p)),
        _technical("atr", "volatility", ("high", "low", "close"), {"period": 14}, "neutral", lambda f, p: _atr(f, p)),
        _technical("realized_volatility", "risk", ("close",), {"period": 20}, "lower_is_bullish", lambda f, p: _realized_vol(f, p)),
        _technical("skewness", "risk", ("close",), {"period": 20}, "neutral", lambda f, p: _skewness(f, p)),
        _technical("kurtosis", "risk", ("close",), {"period": 20}, "lower_is_bullish", lambda f, p: _kurtosis(f, p)),
        _technical("ema_slope", "trend", ("close",), {"period": 20, "slope_period": 5}, "higher_is_bullish", lambda f, p: _ema_slope(f, p)),
        _technical("atr_pct", "risk", ("high", "low", "close"), {"period": 14}, "lower_is_bullish", lambda f, p: _atr_pct(f, p)),
        _technical("downside_volatility", "risk", ("close",), {"period": 20}, "lower_is_bullish", lambda f, p: _downside_volatility(f, p)),
        _technical("max_drawdown", "risk", ("close",), {"period": 60}, "higher_is_bullish", lambda f, p: _max_drawdown(f, p)),
        _technical("donchian_channels", "volatility", ("high", "low", "close"), {"period": 20, "output": "position"}, "neutral", lambda f, p: _donchian(f, p)),
        _technical("keltner_channels", "volatility", ("high", "low", "close"), {"period": 20, "atr_period": 10, "multiplier": 2.0, "output": "position"}, "neutral", lambda f, p: _keltner(f, p)),
        _technical("volume_zscore", "liquidity", ("volume",), {"period": 20}, "neutral", lambda f, p: _zscore_last(f["volume"], p)),
        _technical("volume_ratio", "liquidity", ("volume",), {"period": 20}, "higher_is_bullish", lambda f, p: _volume_ratio(f, p)),
        _technical("mean_reversion_zscore", "reversal", ("close",), {"period": 20}, "lower_is_bullish", lambda f, p: _zscore_last(f["close"], p)),
        _technical("turnover_proxy", "liquidity", ("close", "volume"), {"period": 20}, "higher_is_bullish", lambda f, p: _turnover(f, p)),
        _technical("obv", "volume", ("close", "volume"), {"period": 20, "output": "slope"}, "higher_is_bullish", lambda f, p: _obv(f, p)),
        _technical("ad_line", "volume", ("high", "low", "close", "volume"), {"period": 20, "output": "slope"}, "higher_is_bullish", lambda f, p: _ad_line(f, p)),
        _technical("chaikin_oscillator", "volume", ("high", "low", "close", "volume"), {"fast_period": 3, "slow_period": 10}, "higher_is_bullish", lambda f, p: _chaikin(f, p)),
        _technical("vwap", "volume", ("high", "low", "close", "volume"), {"period": 20, "output": "distance"}, "higher_is_bullish", lambda f, p: _vwap(f, p)),
        _technical("cmf", "volume", ("high", "low", "close", "volume"), {"period": 20}, "higher_is_bullish", lambda f, p: _cmf(f, p)),
        _technical("dema", "trend", ("close",), {"period": 20}, "neutral", lambda f, p: _dema(f, p)),
        _technical("tema", "trend", ("close",), {"period": 20}, "neutral", lambda f, p: _tema(f, p)),
        _technical("zlema", "trend", ("close",), {"period": 20}, "neutral", lambda f, p: _zlema(f, p)),
        _technical("hma", "trend", ("close",), {"period": 20}, "neutral", lambda f, p: _hma(f, p)),
        _technical("kama", "trend", ("close",), {"period": 10, "fast_period": 2, "slow_period": 30}, "neutral", lambda f, p: _kama(f, p)),
        _technical("ppo", "momentum", ("close",), {"fast_period": 12, "slow_period": 26}, "higher_is_bullish", lambda f, p: _ppo(f, p)),
        _technical("cmo", "momentum", ("close",), {"period": 14}, "neutral", lambda f, p: _cmo(f, p)),
        _technical("awesome_oscillator", "momentum", ("high", "low"), {"fast_period": 5, "slow_period": 34}, "higher_is_bullish", lambda f, p: _awesome_oscillator(f, p)),
        _technical("ultimate_oscillator", "momentum", ("high", "low", "close"), {"fast_period": 7, "medium_period": 14, "slow_period": 28}, "neutral", lambda f, p: _ultimate_oscillator(f, p)),
        _technical("tsi", "momentum", ("close",), {"slow_period": 25, "fast_period": 13}, "neutral", lambda f, p: _tsi(f, p)),
        _technical("vortex", "trend", ("high", "low", "close"), {"period": 14, "output": "oscillator"}, "higher_is_bullish", lambda f, p: _vortex(f, p)),
        _technical("choppiness", "trend", ("high", "low", "close"), {"period": 14}, "lower_is_bullish", lambda f, p: _choppiness(f, p)),
        _technical("efficiency_ratio", "trend", ("close",), {"period": 10}, "higher_is_bullish", lambda f, p: _efficiency_ratio(f, p)),
        _technical("elder_ray", "momentum", ("high", "low", "close"), {"period": 13, "output": "bull"}, "neutral", lambda f, p: _elder_ray(f, p)),
        _technical("force_index", "volume", ("close", "volume"), {"period": 13}, "higher_is_bullish", lambda f, p: _force_index(f, p)),
        _technical("ulcer_index", "risk", ("close",), {"period": 14}, "lower_is_bullish", lambda f, p: _ulcer_index(f, p)),
        _technical("parkinson_volatility", "risk", ("high", "low"), {"period": 20}, "lower_is_bullish", lambda f, p: _parkinson_volatility(f, p)),
        _technical("garman_klass_volatility", "risk", ("open", "high", "low", "close"), {"period": 20}, "lower_is_bullish", lambda f, p: _garman_klass_volatility(f, p)),
        _technical("amihud_illiquidity", "liquidity", ("close", "volume"), {"period": 20}, "lower_is_bullish", lambda f, p: _amihud_illiquidity(f, p)),
        _fundamental("market_cap", "size", ("market_cap",), "lower_is_bullish", lambda f, p: _last_value(f, "market_cap")),
        _fundamental("earnings_yield", "valuation", ("net_income", "market_cap"), "higher_is_bullish", lambda f, p: _ratio_last(f, "net_income", "market_cap")),
        _fundamental("book_to_price", "valuation", ("shareholder_equity", "market_cap"), "higher_is_bullish", lambda f, p: _ratio_last(f, "shareholder_equity", "market_cap")),
        _fundamental("return_on_equity", "quality", ("net_income", "shareholder_equity"), "higher_is_bullish", lambda f, p: _ratio_last(f, "net_income", "shareholder_equity")),
        _fundamental("revenue_growth", "growth", ("revenue_growth",), "higher_is_bullish", lambda f, p: _last_value(f, "revenue_growth")),
        _fundamental("debt_to_equity", "quality", ("total_debt", "shareholder_equity"), "lower_is_bullish", lambda f, p: _ratio_last(f, "total_debt", "shareholder_equity")),
        _fundamental("free_cash_flow_yield", "cashflow", ("free_cash_flow", "market_cap"), "higher_is_bullish", lambda f, p: _ratio_last(f, "free_cash_flow", "market_cap")),
    )
}


def list_factors(*, category: str = "", factor_type: str = "") -> list[dict]:
    values = []
    for definition in _FACTORS.values():
        if category and definition.category != category:
            continue
        if factor_type and definition.factor_type != factor_type:
            continue
        values.append(definition.metadata())
    return sorted(values, key=lambda item: (item["factor_type"], item["category"], item["factor_id"]))


def get_factor(factor_id: str) -> FactorDefinition:
    definition = _FACTORS.get(str(factor_id or "").strip())
    if definition is None:
        raise FactorError("factor.notFound")
    return definition


def compute_factor(factor_id: str, frame: pd.DataFrame, params: Optional[Mapping[str, Any]] = None) -> float:
    definition = get_factor(factor_id)
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise FactorError("factor.noData")
    missing = set(definition.required_fields) - set(frame.columns)
    if missing:
        raise FactorError("factor.missingFields")
    merged = {**definition.default_params, **dict(params or {})}
    try:
        value = float(definition.compute(frame, merged))
    except FactorError:
        raise
    except Exception as exc:
        raise FactorError("factor.computeFailed") from exc
    return value if math.isfinite(value) else float("nan")


def compute_panel_factor(
    factor_id: str,
    panel: Mapping[str, pd.DataFrame],
    params: Optional[Mapping[str, Any]] = None,
) -> dict[str, float]:
    if not isinstance(panel, Mapping):
        raise FactorError("factor.panelMustBeMapping")
    output = {}
    for symbol, frame in panel.items():
        try:
            value = compute_factor(factor_id, frame, params)
        except FactorError as exc:
            if exc.code in {"factor.noData", "factor.missingFields", "factor.insufficientHistory"}:
                continue
            raise
        if math.isfinite(value):
            output[str(symbol)] = value
    return output


def _period(params: Mapping[str, Any], key: str = "period", minimum: int = 2) -> int:
    try:
        value = int(params.get(key) or minimum)
    except (TypeError, ValueError) as exc:
        raise FactorError("factor.invalidParameter") from exc
    if value < minimum or value > 5000:
        raise FactorError("factor.invalidParameter")
    return value


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()


def _return(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    values = _numeric(frame["close"])
    if len(values) <= period:
        raise FactorError("factor.insufficientHistory")
    return float(values.iloc[-1] / values.iloc[-period - 1] - 1.0)


def _realized_vol(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    returns = _numeric(frame["close"]).pct_change().dropna()
    if len(returns) < period:
        raise FactorError("factor.insufficientHistory")
    return float(returns.iloc[-period:].std(ddof=1) * math.sqrt(252))


def _skewness(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    returns = _numeric(frame["close"]).pct_change().dropna()
    if len(returns) < period:
        raise FactorError("factor.insufficientHistory")
    return float(returns.iloc[-period:].skew())


def _kurtosis(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    returns = _numeric(frame["close"]).pct_change().dropna()
    if len(returns) < period:
        raise FactorError("factor.insufficientHistory")
    return float(returns.iloc[-period:].kurt())


def _ema_slope(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    slope_period = _period(params, "slope_period", minimum=1)
    values = _require(frame["close"], period + slope_period)
    ema = _ema_values(values, period)
    base = float(ema.iloc[-slope_period - 1])
    return float(ema.iloc[-1] / base - 1.0) if base else float("nan")


def _atr_pct(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    last_close = float(pd.to_numeric(frame["close"], errors="coerce").iloc[-1])
    return float(_atr(frame, params) / last_close) if last_close else float("nan")


def _zscore_last(series: pd.Series, params: Mapping[str, Any]) -> float:
    period = _period(params)
    values = _numeric(series)
    if len(values) < period:
        raise FactorError("factor.insufficientHistory")
    window = values.iloc[-period:]
    std = float(window.std(ddof=1))
    return float((window.iloc[-1] - window.mean()) / std) if std > 0 else 0.0


def _turnover(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    values = _numeric(frame["close"] * frame["volume"])
    if len(values) < period:
        raise FactorError("factor.insufficientHistory")
    return float(values.iloc[-period:].mean())


def _ratio_last(frame: pd.DataFrame, numerator: str, denominator: str) -> float:
    numerator_value = _last_value(frame, numerator)
    denominator_value = _last_value(frame, denominator)
    return numerator_value / denominator_value if denominator_value else float("nan")


def _growth_last(frame: pd.DataFrame, field: str) -> float:
    values = _numeric(frame[field])
    if len(values) < 2:
        raise FactorError("factor.insufficientHistory")
    previous = float(values.iloc[-2])
    return float(values.iloc[-1] / previous - 1.0) if previous else float("nan")


def _last_value(frame: pd.DataFrame, field: str) -> float:
    values = pd.to_numeric(frame[field], errors="coerce").replace([np.inf, -np.inf], np.nan)
    if values.empty or pd.isna(values.iloc[-1]):
        raise FactorError("factor.insufficientHistory")
    return float(values.iloc[-1])


def _positive_float(params: Mapping[str, Any], key: str, default: float) -> float:
    try:
        value = float(params.get(key, default))
    except (TypeError, ValueError) as exc:
        raise FactorError("factor.invalidParameter") from exc
    if not math.isfinite(value) or value <= 0:
        raise FactorError("factor.invalidParameter")
    return value


def _choice(params: Mapping[str, Any], key: str, allowed: set[str], default: str) -> str:
    value = str(params.get(key, default) or default).strip().lower()
    if value not in allowed:
        raise FactorError("factor.invalidParameter")
    return value


def _require(values: pd.Series, count: int) -> pd.Series:
    clean = _numeric(values)
    if len(clean) < count:
        raise FactorError("factor.insufficientHistory")
    return clean


def _ema_values(values: pd.Series, period: int) -> pd.Series:
    clean = _numeric(values).reset_index(drop=True)
    if len(clean) < period:
        raise FactorError("factor.insufficientHistory")
    raw = clean.to_numpy(copy=False)
    output = np.full(len(clean), np.nan, dtype=float)
    previous = float(clean.iloc[:period].mean())
    output[period - 1] = previous
    multiplier = 2.0 / (period + 1.0)
    for index in range(period, len(clean)):
        previous = (float(raw[index]) - previous) * multiplier + previous
        output[index] = previous
    return pd.Series(output, index=clean.index, dtype=float)


def _sma(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    values = _require(frame["close"], period)
    return float(values.iloc[-period:].mean())


def _ema(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    return float(_ema_values(frame["close"], period).iloc[-1])


def _ma_distance(frame: pd.DataFrame, params: Mapping[str, Any], *, exponential: bool) -> float:
    values = _numeric(frame["close"])
    average = _ema(frame, params) if exponential else _sma(frame, params)
    latest = float(values.iloc[-1])
    return latest / average - 1.0 if average else float("nan")


def _rsi(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    values = _require(frame["close"], period + 1)
    deltas = values.diff().dropna()
    gains = deltas.clip(lower=0.0)
    losses = (-deltas.clip(upper=0.0))
    avg_gain = float(gains.iloc[:period].mean())
    avg_loss = float(losses.iloc[:period].mean())
    gain_values = gains.to_numpy(copy=False)
    loss_values = losses.to_numpy(copy=False)
    for index in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + float(gain_values[index])) / period
        avg_loss = (avg_loss * (period - 1) + float(loss_values[index])) / period
    if avg_loss <= 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _macd(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    fast = _period(params, "fast_period", minimum=2)
    slow = _period(params, "slow_period", minimum=2)
    signal = _period(params, "signal_period", minimum=2)
    if fast >= slow:
        raise FactorError("factor.invalidParameter")
    values = _require(frame["close"], slow + signal - 1)
    fast_values = _ema_values(values, fast)
    slow_values = _ema_values(values, slow)
    line = (fast_values - slow_values).dropna().reset_index(drop=True)
    signal_values = _ema_values(line, signal)
    macd_value = float(line.iloc[-1])
    signal_value = float(signal_values.iloc[-1])
    output = _choice(params, "output", {"line", "signal", "histogram"}, "histogram")
    if output == "line":
        return macd_value
    if output == "signal":
        return signal_value
    return macd_value - signal_value


def _bollinger(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    multiplier = _positive_float(params, "stddev", 2.0)
    values = _require(frame["close"], period)
    window = values.iloc[-period:]
    middle = float(window.mean())
    std = float(window.std(ddof=0))
    upper = middle + multiplier * std
    lower = middle - multiplier * std
    output = _choice(params, "output", {"upper", "middle", "lower", "bandwidth", "position"}, "position")
    if output == "upper":
        return upper
    if output == "middle":
        return middle
    if output == "lower":
        return lower
    if output == "bandwidth":
        return (upper - lower) / middle if middle else float("nan")
    return (float(values.iloc[-1]) - lower) / (upper - lower) if upper > lower else 0.5


def _true_range(frame: pd.DataFrame) -> pd.Series:
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    return pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1).dropna().reset_index(drop=True)


def _wilder_last(values: pd.Series, period: int) -> float:
    clean = _require(values, period)
    result = float(clean.iloc[:period].mean())
    for value in clean.iloc[period:]:
        result = (result * (period - 1) + float(value)) / period
    return result


def _atr(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    return _wilder_last(_true_range(frame), _period(params))


def _stochastic(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    smooth_k = _period(params, "smooth_k", minimum=1)
    smooth_d = _period(params, "smooth_d", minimum=1)
    required = period + smooth_k + smooth_d - 2
    if len(frame) < required:
        raise FactorError("factor.insufficientHistory")
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    highest = high.rolling(period).max()
    lowest = low.rolling(period).min()
    raw_k = 100.0 * (close - lowest) / (highest - lowest).replace(0, np.nan)
    k = raw_k.rolling(smooth_k).mean()
    d = k.rolling(smooth_d).mean()
    output = _choice(params, "output", {"k", "d"}, "k")
    value = d.iloc[-1] if output == "d" else k.iloc[-1]
    if not math.isfinite(float(value)):
        raise FactorError("factor.insufficientHistory")
    return float(value)


def _kdj(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    k_period = _period(params, "k_period", minimum=1)
    d_period = _period(params, "d_period", minimum=1)
    if len(frame) < period:
        raise FactorError("factor.insufficientHistory")
    high = pd.to_numeric(frame["high"], errors="coerce").reset_index(drop=True)
    low = pd.to_numeric(frame["low"], errors="coerce").reset_index(drop=True)
    close = pd.to_numeric(frame["close"], errors="coerce").reset_index(drop=True)
    k_value = 50.0
    d_value = 50.0
    if np.isinf(high.to_numpy()).any() or np.isinf(low.to_numpy()).any():
        # Rolling extrema treat infinity as missing; preserve the scalar contract.
        highs = [float(high.iloc[max(0, i - period + 1):i + 1].max()) for i in range(len(frame))]
        lows = [float(low.iloc[max(0, i - period + 1):i + 1].min()) for i in range(len(frame))]
    else:
        highs = high.rolling(period, min_periods=1).max().to_numpy(copy=False)
        lows = low.rolling(period, min_periods=1).min().to_numpy(copy=False)
    closes = close.to_numpy(copy=False)
    for index in range(period - 1, len(frame)):
        highest = float(highs[index])
        lowest = float(lows[index])
        rsv = 50.0 if highest <= lowest else (float(closes[index]) - lowest) / (highest - lowest) * 100.0
        k_value = ((k_period - 1) * k_value + rsv) / k_period
        d_value = ((d_period - 1) * d_value + k_value) / d_period
    output = _choice(params, "output", {"k", "d", "j"}, "j")
    return {"k": k_value, "d": d_value, "j": 3.0 * k_value - 2.0 * d_value}[output]


def _cci(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    typical = (pd.to_numeric(frame["high"], errors="coerce") + pd.to_numeric(frame["low"], errors="coerce") + pd.to_numeric(frame["close"], errors="coerce")) / 3.0
    values = _require(typical, period).iloc[-period:]
    mean = float(values.mean())
    deviation = float((values - mean).abs().mean())
    return (float(values.iloc[-1]) - mean) / (0.015 * deviation) if deviation > 0 else 0.0


def _williams_r(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    if len(frame) < period:
        raise FactorError("factor.insufficientHistory")
    high = float(pd.to_numeric(frame["high"], errors="coerce").iloc[-period:].max())
    low = float(pd.to_numeric(frame["low"], errors="coerce").iloc[-period:].min())
    close = float(pd.to_numeric(frame["close"], errors="coerce").iloc[-1])
    return -50.0 if high <= low else -100.0 * (high - close) / (high - low)


def _mfi(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    if len(frame) < period + 1:
        raise FactorError("factor.insufficientHistory")
    typical = (pd.to_numeric(frame["high"], errors="coerce") + pd.to_numeric(frame["low"], errors="coerce") + pd.to_numeric(frame["close"], errors="coerce")) / 3.0
    flow = typical * pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
    direction = typical.diff()
    positive = flow.where(direction > 0, 0.0).iloc[-period:].sum()
    negative = flow.where(direction < 0, 0.0).iloc[-period:].sum()
    if negative <= 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + float(positive / negative))


def _adx_components(frame: pd.DataFrame, period: int) -> tuple[float, float, float]:
    if len(frame) < period + 1:
        raise FactorError("factor.insufficientHistory")
    high = pd.to_numeric(frame["high"], errors="coerce").to_numpy(copy=False)
    low = pd.to_numeric(frame["low"], errors="coerce").to_numpy(copy=False)
    close = pd.to_numeric(frame["close"], errors="coerce").to_numpy(copy=False)
    tr = []
    plus_dm = []
    minus_dm = []
    for index in range(1, len(frame)):
        tr.append(max(float(high[index] - low[index]), abs(float(high[index] - close[index - 1])), abs(float(low[index] - close[index - 1]))))
        up = float(high[index] - high[index - 1])
        down = float(low[index - 1] - low[index])
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    if len(tr) < period:
        raise FactorError("factor.insufficientHistory")
    smooth_tr = sum(tr[:period])
    smooth_plus = sum(plus_dm[:period])
    smooth_minus = sum(minus_dm[:period])
    dx_values = []
    for index in range(period - 1, len(tr)):
        if index >= period:
            smooth_tr = smooth_tr - smooth_tr / period + tr[index]
            smooth_plus = smooth_plus - smooth_plus / period + plus_dm[index]
            smooth_minus = smooth_minus - smooth_minus / period + minus_dm[index]
        plus_di = 100.0 * smooth_plus / smooth_tr if smooth_tr else 0.0
        minus_di = 100.0 * smooth_minus / smooth_tr if smooth_tr else 0.0
        total = plus_di + minus_di
        dx_values.append(100.0 * abs(plus_di - minus_di) / total if total else 0.0)
    adx_value = dx_values[0]
    for value in dx_values[1:]:
        adx_value = (adx_value * (period - 1) + value) / period
    return adx_value, plus_di, minus_di


def _adx(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    values = _adx_components(frame, _period(params))
    output = _choice(params, "output", {"adx", "plus_di", "minus_di"}, "adx")
    return values[{"adx": 0, "plus_di": 1, "minus_di": 2}[output]]


def _aroon(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    if len(frame) < period:
        raise FactorError("factor.insufficientHistory")
    high = pd.to_numeric(frame["high"], errors="coerce").iloc[-period:].to_numpy()
    low = pd.to_numeric(frame["low"], errors="coerce").iloc[-period:].to_numpy()
    up = 100.0 * (int(np.argmax(high)) + 1) / period
    down = 100.0 * (int(np.argmin(low)) + 1) / period
    output = _choice(params, "output", {"up", "down", "oscillator"}, "oscillator")
    return {"up": up, "down": down, "oscillator": up - down}[output]


def _trix(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    values = _require(frame["close"], period * 3 + 1)
    first = _ema_values(values, period).dropna().reset_index(drop=True)
    second = _ema_values(first, period).dropna().reset_index(drop=True)
    third = _ema_values(second, period).dropna().reset_index(drop=True)
    if len(third) < 2 or float(third.iloc[-2]) == 0:
        raise FactorError("factor.insufficientHistory")
    return float(third.iloc[-1] / third.iloc[-2] - 1.0)


def _supertrend(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    multiplier = _positive_float(params, "multiplier", 3.0)
    if len(frame) < period + 1:
        raise FactorError("factor.insufficientHistory")
    high = pd.to_numeric(frame["high"], errors="coerce").to_numpy(copy=False)
    low = pd.to_numeric(frame["low"], errors="coerce").to_numpy(copy=False)
    close = pd.to_numeric(frame["close"], errors="coerce").to_numpy(copy=False)
    tr = _true_range(frame)
    ranges = tr.to_numpy(copy=False)
    atr_values = np.full(len(frame), np.nan, dtype=float)
    atr_values[period - 1] = float(tr.iloc[:period].mean())
    for index in range(period, len(frame)):
        atr_values[index] = (float(atr_values[index - 1]) * (period - 1) + float(ranges[index])) / period
    direction = 1
    final_upper = final_lower = float("nan")
    line = float("nan")
    for index in range(period - 1, len(frame)):
        midpoint = (float(high[index]) + float(low[index])) / 2.0
        basic_upper = midpoint + multiplier * float(atr_values[index])
        basic_lower = midpoint - multiplier * float(atr_values[index])
        if index == period - 1:
            final_upper, final_lower = basic_upper, basic_lower
        else:
            final_upper = basic_upper if basic_upper < final_upper or float(close[index - 1]) > final_upper else final_upper
            final_lower = basic_lower if basic_lower > final_lower or float(close[index - 1]) < final_lower else final_lower
            if direction < 0 and float(close[index]) > final_upper:
                direction = 1
            elif direction > 0 and float(close[index]) < final_lower:
                direction = -1
        line = final_lower if direction > 0 else final_upper
    output = _choice(params, "output", {"direction", "line"}, "direction")
    return float(direction if output == "direction" else line)


def _downside_volatility(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    returns = _require(frame["close"], period + 1).pct_change().dropna().iloc[-period:]
    downside = returns.where(returns < 0, 0.0)
    return float(np.sqrt(np.mean(np.square(downside))) * math.sqrt(252))


def _max_drawdown(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    values = _require(frame["close"], period).iloc[-period:]
    drawdown = values / values.cummax() - 1.0
    return float(drawdown.min())


def _donchian(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    if len(frame) < period:
        raise FactorError("factor.insufficientHistory")
    upper = float(pd.to_numeric(frame["high"], errors="coerce").iloc[-period:].max())
    lower = float(pd.to_numeric(frame["low"], errors="coerce").iloc[-period:].min())
    middle = (upper + lower) / 2.0
    output = _choice(params, "output", {"upper", "middle", "lower", "position"}, "position")
    if output == "upper": return upper
    if output == "middle": return middle
    if output == "lower": return lower
    close = float(pd.to_numeric(frame["close"], errors="coerce").iloc[-1])
    return (close - lower) / (upper - lower) if upper > lower else 0.5


def _keltner(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    atr_period = _period(params, "atr_period", minimum=2)
    multiplier = _positive_float(params, "multiplier", 2.0)
    middle = _ema(frame, {"period": period})
    atr_value = _atr(frame, {"period": atr_period})
    upper = middle + multiplier * atr_value
    lower = middle - multiplier * atr_value
    output = _choice(params, "output", {"upper", "middle", "lower", "position"}, "position")
    if output == "upper": return upper
    if output == "middle": return middle
    if output == "lower": return lower
    close = float(pd.to_numeric(frame["close"], errors="coerce").iloc[-1])
    return (close - lower) / (upper - lower) if upper > lower else 0.5


def _volume_ratio(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    values = _require(frame["volume"], period)
    average = float(values.iloc[-period:].mean())
    return float(values.iloc[-1]) / average if average else float("nan")


def _normalized_slope(values: pd.Series, volumes: pd.Series, period: int) -> float:
    if len(values) <= period:
        raise FactorError("factor.insufficientHistory")
    denominator = float(_numeric(volumes).iloc[-period:].abs().sum())
    return float(values.iloc[-1] - values.iloc[-period - 1]) / denominator if denominator else 0.0


def _obv_series(frame: pd.DataFrame) -> pd.Series:
    close = pd.to_numeric(frame["close"], errors="coerce").reset_index(drop=True)
    volume = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0).reset_index(drop=True)
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum()


def _obv(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    values = _obv_series(frame)
    output = _choice(params, "output", {"value", "slope"}, "slope")
    return float(values.iloc[-1]) if output == "value" else _normalized_slope(values, frame["volume"], period)


def _ad_series(frame: pd.DataFrame) -> pd.Series:
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
    spread = (high - low).replace(0, np.nan)
    multiplier = ((close - low) - (high - close)) / spread
    return (multiplier.fillna(0.0) * volume).cumsum().reset_index(drop=True)


def _ad_line(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    values = _ad_series(frame)
    output = _choice(params, "output", {"value", "slope"}, "slope")
    return float(values.iloc[-1]) if output == "value" else _normalized_slope(values, frame["volume"], period)


def _chaikin(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    fast = _period(params, "fast_period", minimum=2)
    slow = _period(params, "slow_period", minimum=2)
    if fast >= slow:
        raise FactorError("factor.invalidParameter")
    values = _ad_series(frame)
    fast_value = float(_ema_values(values, fast).iloc[-1])
    slow_value = float(_ema_values(values, slow).iloc[-1])
    scale = float(_numeric(frame["volume"]).iloc[-slow:].mean())
    return (fast_value - slow_value) / scale if scale else 0.0


def _vwap(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    if len(frame) < period:
        raise FactorError("factor.insufficientHistory")
    typical = (pd.to_numeric(frame["high"], errors="coerce") + pd.to_numeric(frame["low"], errors="coerce") + pd.to_numeric(frame["close"], errors="coerce")) / 3.0
    volume = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
    denominator = float(volume.iloc[-period:].sum())
    if denominator <= 0:
        return float("nan")
    value = float((typical.iloc[-period:] * volume.iloc[-period:]).sum() / denominator)
    output = _choice(params, "output", {"value", "distance"}, "distance")
    close = float(pd.to_numeric(frame["close"], errors="coerce").iloc[-1])
    return value if output == "value" else close / value - 1.0


def _cmf(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    if len(frame) < period:
        raise FactorError("factor.insufficientHistory")
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
    spread = (high - low).replace(0, np.nan)
    money_flow_volume = (((close - low) - (high - close)) / spread).fillna(0.0) * volume
    denominator = float(volume.iloc[-period:].sum())
    return float(money_flow_volume.iloc[-period:].sum() / denominator) if denominator else 0.0


def _dema(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    first = _ema_values(frame["close"], period).dropna().reset_index(drop=True)
    second = _ema_values(first, period).dropna().reset_index(drop=True)
    return 2.0 * float(first.iloc[-1]) - float(second.iloc[-1])


def _tema(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    first = _ema_values(frame["close"], period).dropna().reset_index(drop=True)
    second = _ema_values(first, period).dropna().reset_index(drop=True)
    third = _ema_values(second, period).dropna().reset_index(drop=True)
    return 3.0 * float(first.iloc[-1]) - 3.0 * float(second.iloc[-1]) + float(third.iloc[-1])


def _zlema(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    lag = max(1, (period - 1) // 2)
    close = _require(frame["close"], period + lag)
    adjusted = close + (close - close.shift(lag))
    return float(_ema_values(adjusted.dropna(), period).iloc[-1])


def _wma_values(values: pd.Series, period: int) -> pd.Series:
    clean = _require(values, period).reset_index(drop=True)
    weights = np.arange(1, period + 1, dtype=float)
    return clean.rolling(period).apply(lambda window: float(np.dot(window, weights) / weights.sum()), raw=True)


def _hma(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    half_period = max(1, period // 2)
    root_period = max(1, int(math.sqrt(period)))
    values = _require(frame["close"], period + root_period)
    half = _wma_values(values, half_period)
    full = _wma_values(values, period)
    raw = (2.0 * half - full).dropna()
    return float(_wma_values(raw, root_period).iloc[-1])


def _kama(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    fast = _period(params, "fast_period", minimum=1)
    slow = _period(params, "slow_period", minimum=2)
    if fast >= slow:
        raise FactorError("factor.invalidParameter")
    values = _require(frame["close"], period + 1).reset_index(drop=True)
    kama = float(values.iloc[:period].mean())
    fast_constant = 2.0 / (fast + 1.0)
    slow_constant = 2.0 / (slow + 1.0)
    for index in range(period, len(values)):
        change = abs(float(values.iloc[index] - values.iloc[index - period]))
        volatility = float(values.iloc[index - period:index + 1].diff().abs().sum())
        efficiency = change / volatility if volatility else 0.0
        smoothing = (efficiency * (fast_constant - slow_constant) + slow_constant) ** 2
        kama += smoothing * (float(values.iloc[index]) - kama)
    return kama


def _ppo(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    fast = _period(params, "fast_period", minimum=2)
    slow = _period(params, "slow_period", minimum=2)
    if fast >= slow:
        raise FactorError("factor.invalidParameter")
    fast_value = float(_ema_values(frame["close"], fast).iloc[-1])
    slow_value = float(_ema_values(frame["close"], slow).iloc[-1])
    return 100.0 * (fast_value - slow_value) / slow_value if slow_value else float("nan")


def _cmo(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    changes = _require(frame["close"], period + 1).diff().dropna().iloc[-period:]
    gains = float(changes.clip(lower=0.0).sum())
    losses = float((-changes.clip(upper=0.0)).sum())
    total = gains + losses
    return 100.0 * (gains - losses) / total if total else 0.0


def _awesome_oscillator(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    fast = _period(params, "fast_period", minimum=2)
    slow = _period(params, "slow_period", minimum=2)
    if fast >= slow:
        raise FactorError("factor.invalidParameter")
    median = (pd.to_numeric(frame["high"], errors="coerce") + pd.to_numeric(frame["low"], errors="coerce")) / 2.0
    values = _require(median, slow)
    return float(values.iloc[-fast:].mean() - values.iloc[-slow:].mean())


def _ultimate_oscillator(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    fast = _period(params, "fast_period", minimum=2)
    medium = _period(params, "medium_period", minimum=2)
    slow = _period(params, "slow_period", minimum=2)
    if not fast < medium < slow:
        raise FactorError("factor.invalidParameter")
    if len(frame) < slow + 1:
        raise FactorError("factor.insufficientHistory")
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    previous = close.shift(1)
    buying_pressure = close - pd.concat([low, previous], axis=1).min(axis=1)
    true_range = pd.concat([high, previous], axis=1).max(axis=1) - pd.concat([low, previous], axis=1).min(axis=1)
    def average(length: int) -> float:
        denominator = float(true_range.iloc[-length:].sum())
        return float(buying_pressure.iloc[-length:].sum()) / denominator if denominator else 0.0
    return 100.0 * (4.0 * average(fast) + 2.0 * average(medium) + average(slow)) / 7.0


def _tsi(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    slow = _period(params, "slow_period", minimum=2)
    fast = _period(params, "fast_period", minimum=2)
    momentum = _require(frame["close"], slow + fast + 1).diff().dropna()
    numerator = _ema_values(_ema_values(momentum, slow).dropna(), fast).dropna()
    denominator = _ema_values(_ema_values(momentum.abs(), slow).dropna(), fast).dropna()
    scale = float(denominator.iloc[-1])
    return 100.0 * float(numerator.iloc[-1]) / scale if scale else 0.0


def _vortex(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    if len(frame) < period + 1:
        raise FactorError("factor.insufficientHistory")
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    true_range = _true_range(frame).iloc[-period:]
    denominator = float(true_range.sum())
    if denominator <= 0:
        return 0.0
    plus = float((high - low.shift(1)).abs().iloc[-period:].sum()) / denominator
    minus = float((low - high.shift(1)).abs().iloc[-period:].sum()) / denominator
    output = _choice(params, "output", {"plus", "minus", "oscillator"}, "oscillator")
    return {"plus": plus, "minus": minus, "oscillator": plus - minus}[output]


def _choppiness(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    if len(frame) < period + 1:
        raise FactorError("factor.insufficientHistory")
    high = pd.to_numeric(frame["high"], errors="coerce").iloc[-period:]
    low = pd.to_numeric(frame["low"], errors="coerce").iloc[-period:]
    price_range = float(high.max() - low.min())
    if price_range <= 0:
        return 100.0
    return 100.0 * math.log10(float(_true_range(frame).iloc[-period:].sum()) / price_range) / math.log10(period)


def _efficiency_ratio(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    values = _require(frame["close"], period + 1)
    change = abs(float(values.iloc[-1] - values.iloc[-period - 1]))
    volatility = float(values.iloc[-period - 1:].diff().abs().sum())
    return change / volatility if volatility else 0.0


def _elder_ray(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    average = float(_ema_values(frame["close"], period).iloc[-1])
    output = _choice(params, "output", {"bull", "bear"}, "bull")
    field = "high" if output == "bull" else "low"
    return float(pd.to_numeric(frame[field], errors="coerce").iloc[-1]) - average


def _force_index(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    close = pd.to_numeric(frame["close"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    raw = (close.diff() * volume).dropna()
    return float(_ema_values(raw, period).iloc[-1])


def _ulcer_index(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    values = _require(frame["close"], period).iloc[-period:]
    drawdown = 100.0 * (values / values.cummax() - 1.0)
    return float(np.sqrt(np.mean(np.square(drawdown))))


def _parkinson_volatility(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    high = _require(frame["high"], period).iloc[-period:]
    low = _require(frame["low"], period).iloc[-period:].replace(0, np.nan)
    variance = float(np.square(np.log(high / low)).mean() / (4.0 * math.log(2.0)))
    return math.sqrt(max(0.0, variance) * 252.0)


def _garman_klass_volatility(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    high = _require(frame["high"], period).iloc[-period:]
    low = _require(frame["low"], period).iloc[-period:].replace(0, np.nan)
    open_values = _require(frame["open"], period).iloc[-period:].replace(0, np.nan)
    close = _require(frame["close"], period).iloc[-period:]
    variance = 0.5 * np.square(np.log(high / low)) - (2.0 * math.log(2.0) - 1.0) * np.square(np.log(close / open_values))
    return math.sqrt(max(0.0, float(variance.mean())) * 252.0)


def _amihud_illiquidity(frame: pd.DataFrame, params: Mapping[str, Any]) -> float:
    period = _period(params)
    close = _require(frame["close"], period + 1)
    volume = pd.to_numeric(frame["volume"], errors="coerce").iloc[-period:]
    returns = close.pct_change().abs().iloc[-period:]
    traded_value = (close.iloc[-period:] * volume).replace(0, np.nan)
    return float((returns.to_numpy() / traded_value.to_numpy()).mean())
