"""Qlib-compatible Alpha158 and Alpha360 feature catalog."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from .registry import FactorError


EPSILON = 1e-12
QLIB_SOURCE_URL = "https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/loader.py"
QLIB_LICENSE_URL = "https://github.com/microsoft/qlib/blob/main/LICENSE"
ALPHA158_WINDOWS = (5, 10, 20, 30, 60)


@dataclass(frozen=True)
class QlibFeature:
    family: str
    name: str
    expression: str
    category: str
    required_fields: tuple[str, ...]
    window: int
    operation: str
    field: str
    compute: Callable[[pd.DataFrame], float]

    @property
    def factor_id(self) -> str:
        return f"qlib:{self.family.lower()}:{self.name}"

    def metadata(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "library_id": self.name,
            "version": "qlib-main",
            "display_name": f"{self.family} · {self.name}",
            "name_i18n_key": "",
            "description_i18n_key": "",
            "category": self.category,
            "factor_type": "technical",
            "provider": "microsoft-qlib",
            "family": self.family.lower(),
            "usage_mode": "model_feature",
            "required_fields": list(self.required_fields),
            "default_params": {},
            "parameter_schema": {},
            "outputs": [self.name],
            "direction_hint": "neutral",
            "supported_contexts": ["cta", "portfolio"],
            "default_warmup_bars": max(1, self.window),
            "expression": self.expression,
            "feature_spec": {
                "operation": self.operation,
                "field": self.field,
                "window": self.window,
            },
            "source_url": QLIB_SOURCE_URL,
            "license": "MIT",
            "license_url": QLIB_LICENSE_URL,
        }


def list_qlib_factors() -> list[dict[str, Any]]:
    return [feature.metadata() for feature in _feature_catalog().values()]


def get_qlib_factor_metadata(factor_id: str) -> dict[str, Any]:
    feature = _resolve_feature(factor_id)
    return feature.metadata()


def compute_qlib_factor(
    factor_id: str,
    frame: pd.DataFrame,
    params: Mapping[str, Any] | None = None,
) -> float:
    if params:
        raise FactorError("factor.parametersUnsupported")
    feature = _resolve_feature(factor_id)
    clean = _prepare_frame(frame, feature.required_fields, feature.window)
    try:
        value = float(feature.compute(clean))
    except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise FactorError("factor.computeFailed") from exc
    if not np.isfinite(value):
        raise FactorError("factor.insufficientHistory")
    return value


def _resolve_feature(factor_id: str) -> QlibFeature:
    canonical = str(factor_id or "").strip().lower()
    feature = _feature_catalog().get(canonical)
    if feature is None:
        raise FactorError("factor.notFound")
    return feature


def _prepare_frame(frame: pd.DataFrame, fields: tuple[str, ...], window: int) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise FactorError("factor.noData")
    clean = frame.copy()
    clean.columns = [str(column).strip().lower() for column in clean.columns]
    missing = sorted(set(fields) - set(clean.columns))
    if missing:
        raise FactorError(f"factor.missingFields:{','.join(missing)}")
    if len(clean.index) < max(1, window):
        raise FactorError("factor.insufficientHistory")
    for field in fields:
        clean[field] = pd.to_numeric(clean[field], errors="coerce")
    return clean


def _latest(frame: pd.DataFrame, field: str, lag: int = 0) -> float:
    return float(frame[field].iloc[-1 - lag])


def _window(frame: pd.DataFrame, field: str, size: int) -> pd.Series:
    return frame[field].iloc[-size:].astype(float)


def _ratio(numerator: float, denominator: float) -> float:
    return float(numerator / (denominator + EPSILON))


def _feature(
    family: str,
    name: str,
    expression: str,
    category: str,
    required_fields: tuple[str, ...],
    window: int,
    operation: str,
    field: str,
    compute: Callable[[pd.DataFrame], float],
) -> QlibFeature:
    return QlibFeature(
        family=family,
        name=name,
        expression=expression,
        category=category,
        required_fields=required_fields,
        window=window,
        operation=operation,
        field=field,
        compute=compute,
    )


@lru_cache(maxsize=1)
def _feature_catalog() -> dict[str, QlibFeature]:
    features = _alpha158_features() + _alpha360_features()
    return {feature.factor_id.lower(): feature for feature in features}


def _alpha360_features() -> list[QlibFeature]:
    features: list[QlibFeature] = []
    for field in ("close", "open", "high", "low", "vwap"):
        for lag in range(59, -1, -1):
            expression = f"Ref(${field}, {lag})/$close" if lag else f"${field}/$close"
            features.append(_feature(
                "Alpha360",
                f"{field.upper()}{lag}",
                expression,
                "price",
                (field, "close") if field != "close" else ("close",),
                lag + 1,
                "lag_ratio",
                field,
                lambda frame, source=field, offset=lag: _ratio(
                    _latest(frame, source, offset), _latest(frame, "close")
                ),
            ))
    for lag in range(59, -1, -1):
        expression = f"Ref($volume, {lag})/($volume+1e-12)" if lag else "$volume/($volume+1e-12)"
        features.append(_feature(
            "Alpha360",
            f"VOLUME{lag}",
            expression,
            "volume",
            ("volume",),
            lag + 1,
            "lag_ratio",
            "volume",
            lambda frame, offset=lag: _ratio(
                _latest(frame, "volume", offset), _latest(frame, "volume")
            ),
        ))
    return features


def _alpha158_features() -> list[QlibFeature]:
    features = _alpha158_kbar_features()
    for field in ("open", "high", "low", "vwap"):
        features.append(_feature(
            "Alpha158",
            f"{field.upper()}0",
            f"${field}/$close",
            "price",
            (field, "close"),
            1,
            "price_ratio",
            field,
            lambda frame, source=field: _ratio(_latest(frame, source), _latest(frame, "close")),
        ))
    for window in ALPHA158_WINDOWS:
        features.extend(_alpha158_rolling_features(window))
    return features


def _alpha158_kbar_features() -> list[QlibFeature]:
    fields = ("open", "high", "low", "close")
    specs = (
        ("KMID", "($close-$open)/$open", "candle_body", lambda o, h, l, c: _ratio(c - o, o)),
        ("KLEN", "($high-$low)/$open", "candle_range", lambda o, h, l, c: _ratio(h - l, o)),
        ("KMID2", "($close-$open)/($high-$low+1e-12)", "body_position", lambda o, h, l, c: _ratio(c - o, h - l)),
        ("KUP", "($high-Greater($open,$close))/$open", "upper_shadow", lambda o, h, l, c: _ratio(h - max(o, c), o)),
        ("KUP2", "($high-Greater($open,$close))/($high-$low+1e-12)", "upper_shadow_ratio", lambda o, h, l, c: _ratio(h - max(o, c), h - l)),
        ("KLOW", "(Less($open,$close)-$low)/$open", "lower_shadow", lambda o, h, l, c: _ratio(min(o, c) - l, o)),
        ("KLOW2", "(Less($open,$close)-$low)/($high-$low+1e-12)", "lower_shadow_ratio", lambda o, h, l, c: _ratio(min(o, c) - l, h - l)),
        ("KSFT", "(2*$close-$high-$low)/$open", "close_shift", lambda o, h, l, c: _ratio(2 * c - h - l, o)),
        ("KSFT2", "(2*$close-$high-$low)/($high-$low+1e-12)", "close_shift_ratio", lambda o, h, l, c: _ratio(2 * c - h - l, h - l)),
    )
    return [
        _feature(
            "Alpha158", name, expression, "pattern_recognition", fields, 1, operation, "ohlc",
            lambda frame, fn=compute: fn(
                _latest(frame, "open"), _latest(frame, "high"), _latest(frame, "low"), _latest(frame, "close")
            ),
        )
        for name, expression, operation, compute in specs
    ]


def _linear_stats(values: pd.Series) -> tuple[float, float, float]:
    y = values.to_numpy(dtype=float)
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    residual = float(y[-1] - fitted[-1])
    total = float(np.sum((y - y.mean()) ** 2))
    r_squared = (
        float("nan") if np.isclose(values.std(), 0, atol=2e-5)
        else 1.0 - float(np.sum((y - fitted) ** 2)) / total
    )
    return float(slope), float(r_squared), residual


def _changes(frame: pd.DataFrame, field: str, size: int) -> pd.Series:
    values = frame[field].iloc[-(size + 1):].astype(float)
    return values.diff().dropna()


def _returns(frame: pd.DataFrame, field: str, size: int) -> pd.Series:
    values = frame[field].iloc[-(size + 1):].astype(float)
    return values.pct_change(fill_method=None).dropna()


def _alpha158_rolling_features(size: int) -> list[QlibFeature]:
    close = lambda frame: _window(frame, "close", size)
    high = lambda frame: _window(frame, "high", size)
    low = lambda frame: _window(frame, "low", size)
    volume = lambda frame: _window(frame, "volume", size)
    current_close = lambda frame: _latest(frame, "close")
    current_volume = lambda frame: _latest(frame, "volume")

    def extreme_position(values: pd.Series, mode: str) -> float:
        array = values.to_numpy(dtype=float)
        index = int(np.nanargmax(array) if mode == "max" else np.nanargmin(array))
        return float(index + 1) / float(size)

    def signed_change_share(frame: pd.DataFrame, positive: bool, field: str = "close") -> float:
        changes = _changes(frame, field, size)
        selected = changes.clip(lower=0) if positive else (-changes).clip(lower=0)
        return _ratio(float(selected.sum()), float(changes.abs().sum()))

    def change_balance(frame: pd.DataFrame, field: str = "close") -> float:
        changes = _changes(frame, field, size)
        return _ratio(float(changes.sum()), float(changes.abs().sum()))

    def up_down_share(frame: pd.DataFrame, positive: bool) -> float:
        changes = _changes(frame, "close", size)
        return float((changes > 0 if positive else changes < 0).mean())

    def price_volume_corr(frame: pd.DataFrame, change: bool) -> float:
        if change:
            price = _returns(frame, "close", size)
            vol = np.log(_window(frame, "volume", size + 1) / _window(frame, "volume", size + 1).shift(1) + 1).dropna()
        else:
            price = close(frame)
            vol = np.log(volume(frame) + 1)
        return float(price.corr(vol))

    weighted = lambda frame: _returns(frame, "close", size).abs() * _window(frame, "volume", size).to_numpy(dtype=float)
    fields_ohlc = ("high", "low", "close")
    specs = [
        ("ROC", f"Ref($close,{size})/$close", "momentum", ("close",), lambda frame: _ratio(_latest(frame, "close", size), current_close(frame))),
        ("MA", f"Mean($close,{size})/$close", "trend", ("close",), lambda frame: _ratio(close(frame).mean(), current_close(frame))),
        ("STD", f"Std($close,{size})/$close", "volatility", ("close",), lambda frame: _ratio(close(frame).std(), current_close(frame))),
        ("BETA", f"Slope($close,{size})/$close", "trend", ("close",), lambda frame: _ratio(_linear_stats(close(frame))[0], current_close(frame))),
        ("RSQR", f"Rsquare($close,{size})", "trend", ("close",), lambda frame: _linear_stats(close(frame))[1]),
        ("RESI", f"Resi($close,{size})/$close", "reversal", ("close",), lambda frame: _ratio(_linear_stats(close(frame))[2], current_close(frame))),
        ("MAX", f"Max($high,{size})/$close", "price", fields_ohlc, lambda frame: _ratio(high(frame).max(), current_close(frame))),
        ("MIN", f"Min($low,{size})/$close", "price", fields_ohlc, lambda frame: _ratio(low(frame).min(), current_close(frame))),
        ("QTLU", f"Quantile($close,{size},0.8)/$close", "price", ("close",), lambda frame: _ratio(close(frame).quantile(0.8), current_close(frame))),
        ("QTLD", f"Quantile($close,{size},0.2)/$close", "price", ("close",), lambda frame: _ratio(close(frame).quantile(0.2), current_close(frame))),
        ("RANK", f"Rank($close,{size})", "price", ("close",), lambda frame: float(close(frame).rank(pct=True).iloc[-1])),
        ("RSV", f"($close-Min($low,{size}))/(Max($high,{size})-Min($low,{size})+1e-12)", "momentum", fields_ohlc, lambda frame: _ratio(current_close(frame) - low(frame).min(), high(frame).max() - low(frame).min())),
        ("IMAX", f"IdxMax($high,{size})/{size}", "trend", ("high",), lambda frame: extreme_position(high(frame), "max")),
        ("IMIN", f"IdxMin($low,{size})/{size}", "trend", ("low",), lambda frame: extreme_position(low(frame), "min")),
        ("IMXD", f"(IdxMax($high,{size})-IdxMin($low,{size}))/{size}", "trend", ("high", "low"), lambda frame: extreme_position(high(frame), "max") - extreme_position(low(frame), "min")),
        ("CORR", f"Corr($close,Log($volume+1),{size})", "volume", ("close", "volume"), lambda frame: price_volume_corr(frame, False)),
        ("CORD", f"Corr($close/Ref($close,1),Log($volume/Ref($volume,1)+1),{size})", "volume", ("close", "volume"), lambda frame: price_volume_corr(frame, True)),
        ("CNTP", f"Mean($close>Ref($close,1),{size})", "momentum", ("close",), lambda frame: up_down_share(frame, True)),
        ("CNTN", f"Mean($close<Ref($close,1),{size})", "momentum", ("close",), lambda frame: up_down_share(frame, False)),
        ("CNTD", f"Mean(up,{size})-Mean(down,{size})", "momentum", ("close",), lambda frame: up_down_share(frame, True) - up_down_share(frame, False)),
        ("SUMP", f"Sum(gain,{size})/Sum(abs_change,{size})", "momentum", ("close",), lambda frame: signed_change_share(frame, True)),
        ("SUMN", f"Sum(loss,{size})/Sum(abs_change,{size})", "momentum", ("close",), lambda frame: signed_change_share(frame, False)),
        ("SUMD", f"(Sum(gain,{size})-Sum(loss,{size}))/Sum(abs_change,{size})", "momentum", ("close",), lambda frame: change_balance(frame)),
        ("VMA", f"Mean($volume,{size})/($volume+1e-12)", "volume", ("volume",), lambda frame: _ratio(volume(frame).mean(), current_volume(frame))),
        ("VSTD", f"Std($volume,{size})/($volume+1e-12)", "volume", ("volume",), lambda frame: _ratio(volume(frame).std(), current_volume(frame))),
        ("WVMA", f"Std(abs_return*$volume,{size})/Mean(abs_return*$volume,{size})", "volume", ("close", "volume"), lambda frame: _ratio(weighted(frame).std(), weighted(frame).mean())),
        ("VSUMP", f"Sum(volume_gain,{size})/Sum(abs_volume_change,{size})", "volume", ("volume",), lambda frame: signed_change_share(frame, True, "volume")),
        ("VSUMN", f"Sum(volume_loss,{size})/Sum(abs_volume_change,{size})", "volume", ("volume",), lambda frame: signed_change_share(frame, False, "volume")),
        ("VSUMD", f"(Sum(volume_gain,{size})-Sum(volume_loss,{size}))/Sum(abs_volume_change,{size})", "volume", ("volume",), lambda frame: change_balance(frame, "volume")),
    ]
    return [
        _feature("Alpha158", f"{prefix}{size}", expression, category, fields, size + (1 if prefix in {"ROC", "CORD", "CNTP", "CNTN", "CNTD", "SUMP", "SUMN", "SUMD", "WVMA", "VSUMP", "VSUMN", "VSUMD"} else 0), prefix.lower(), "ohlcv", compute)
        for prefix, expression, category, fields, compute in specs
    ]
