"""Deterministic feature extraction for professional crypto reports.

The report generator must not guess whether ``0.01`` means one percent or one
basis point, nor silently combine Binance spot volume with OKX perpetual open
interest.  This module accepts ordinary dictionaries, preserves the source
scope on every feature, and emits explicit quality warnings when the input is
not safe to compare.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


VERSION = "crypto_features.v1"

_SPOT_ALIASES = {"cash", "spot"}
_PERPETUAL_ALIASES = {
    "perp",
    "perpetual",
    "perpetual_contract",
    "perpetual_future",
    "perpetual_futures",
    "perpetual_swap",
    "swap",
}
_PERCENT_UNITS = {"%", "pct", "percent", "percentage"}
_DECIMAL_UNITS = {"decimal", "fraction", "ratio_decimal"}
_BPS_UNITS = {"bp", "bps", "basis_point", "basis_points"}
_RATIO_UNITS = {"ratio", "long_short_ratio", "multiple", "x"}
_AMOUNT_UNITS = {
    "base",
    "base_asset",
    "coin",
    "coins",
    "contract",
    "contracts",
    "quote",
    "quote_asset",
    "usd",
    "usdc",
    "usdt",
}


def _warning(code: str, field: str, message: str, *, severity: str = "warning") -> dict[str, str]:
    return {"code": code, "severity": severity, "field": field, "message": message}


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalise_venue(value: Any) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    return text.lower().replace(" ", "_")


def _normalise_product(value: Any) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    normalised = text.lower().replace("-", "_").replace(" ", "_")
    if normalised in _SPOT_ALIASES:
        return "spot"
    if normalised in _PERPETUAL_ALIASES:
        return "perpetual"
    return normalised


def _normalise_unit(value: Any) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    normalised = text.lower().replace("-", "_").replace(" ", "_")
    if normalised in _PERCENT_UNITS:
        return "percent"
    if normalised in _DECIMAL_UNITS:
        return "decimal"
    if normalised in _BPS_UNITS:
        return "bps"
    if normalised in _RATIO_UNITS:
        return "ratio"
    return normalised


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _metric(raw: Any, *, default_venue: str | None, default_product: str | None) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        evidence_ref = _first(raw, "evidence_ref", "evidence_id", "source_ref", "ref")
        unit = _normalise_unit(_first(raw, "unit", "value_unit", "rate_unit"))
        currency = _clean_text(_first(raw, "currency", "quote_currency", "denomination"))
        if currency is None and unit in {"usd", "usdc", "usdt"}:
            currency = unit.upper()
        return {
            "value": _number(_first(raw, "value", "rate", "amount", "ratio")),
            "unit": unit,
            "currency": currency,
            "venue": _normalise_venue(_first(raw, "venue", "exchange")) or default_venue,
            "product_type": _normalise_product(_first(raw, "product_type", "market_type", "instrument_type"))
            or default_product,
            "evidence_ref": _clean_text(evidence_ref),
            "raw": dict(raw),
        }
    return {
        "value": _number(raw),
        "unit": None,
        "currency": None,
        "venue": default_venue,
        "product_type": default_product,
        "evidence_ref": None,
        "raw": raw,
    }


def _metric_input(
    factors: Mapping[str, Any],
    *names: str,
    rate: bool = False,
    amount: bool = False,
) -> Any:
    """Read nested metrics and explicit legacy companion fields.

    Scalars remain unitless unless the payload carries a companion such as
    ``funding_rate_unit``. Explicit names such as ``funding_rate_percent``
    and ``open_interest_usd`` are also accepted without guessing.
    """

    found_name = next((name for name in names if name in factors and factors[name] is not None), None)
    raw = factors.get(found_name) if found_name else None
    implied_unit = None

    suffixes: tuple[tuple[str, str], ...] = ()
    if rate:
        suffixes += (
            ("_decimal", "decimal"),
            ("_percent", "percent"),
            ("_pct", "percent"),
            ("_bps", "bps"),
        )
    if amount:
        suffixes += (
            ("_usd", "usd"),
            ("_usdt", "usdt"),
            ("_usdc", "usdc"),
            ("_contracts", "contracts"),
        )

    if raw is None:
        for name in names:
            for suffix, unit in suffixes:
                explicit_name = f"{name}{suffix}"
                if explicit_name in factors and factors[explicit_name] is not None:
                    found_name = name
                    raw = factors[explicit_name]
                    implied_unit = unit
                    break
            if raw is not None:
                break
    if raw is None:
        return None

    result = dict(raw) if isinstance(raw, Mapping) else {"value": raw}
    companion_bases = tuple(dict.fromkeys(((found_name,) if found_name else ()) + names))
    companions = {
        "unit": ("unit", "value_unit", "rate_unit"),
        "currency": ("currency", "quote_currency", "denomination"),
        "venue": ("venue", "exchange"),
        "product_type": ("product_type", "market_type", "instrument_type"),
        "evidence_ref": ("evidence_ref", "evidence_id", "source_ref"),
    }
    for output_key, suffix_names in companions.items():
        if result.get(output_key) is not None:
            continue
        for base in companion_bases:
            value = _first(factors, *(f"{base}_{suffix}" for suffix in suffix_names))
            if value is not None:
                result[output_key] = value
                break
    if result.get("unit") is None and implied_unit is not None:
        result["unit"] = implied_unit
    return result


def _liquidation_input(factors: Mapping[str, Any]) -> Any:
    raw = _metric_input(factors, "liquidations", "liquidation", amount=True)
    if raw is not None:
        return raw
    values = {
        "long": _first(factors, "long_liquidations", "liquidations_long", "liquidation_long"),
        "short": _first(factors, "short_liquidations", "liquidations_short", "liquidation_short"),
        "total": _first(factors, "total_liquidations", "liquidations_total", "liquidation_total"),
    }
    if all(value is None for value in values.values()):
        return None
    result = {key: value for key, value in values.items() if value is not None}
    for suffix in ("unit", "currency", "venue", "product_type", "evidence_ref"):
        value = _first(factors, f"liquidations_{suffix}", f"liquidation_{suffix}")
        if value is not None:
            result[suffix] = value
    return result


def _long_short_input(factors: Mapping[str, Any]) -> Any:
    raw = _metric_input(factors, "long_short_ratio", "longShortRatio", "long_short")
    if raw is not None:
        return raw
    shares = {
        "long_pct": _first(factors, "long_pct", "long_percent", "long_account_percent"),
        "short_pct": _first(factors, "short_pct", "short_percent", "short_account_percent"),
        "long_share": _first(factors, "long_share", "long_account_share"),
        "short_share": _first(factors, "short_share", "short_account_share"),
    }
    if all(value is None for value in shares.values()):
        return None
    result = {key: value for key, value in shares.items() if value is not None}
    for suffix in ("venue", "product_type", "evidence_ref"):
        value = _first(factors, f"long_short_{suffix}", f"long_short_ratio_{suffix}")
        if value is not None:
            result[suffix] = value
    return result


def _scope(metric: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "venue": metric.get("venue"),
        "product_type": metric.get("product_type"),
        "evidence_ref": metric.get("evidence_ref"),
    }


def _normalise_rate(
    raw: Any,
    *,
    field: str,
    default_venue: str | None,
    default_product: str | None,
    warnings: list[dict[str, str]],
) -> dict[str, Any] | None:
    if raw is None:
        return None
    metric = _metric(raw, default_venue=default_venue, default_product=default_product)
    value = metric["value"]
    unit = metric["unit"]
    result = {**_scope(metric), "raw_value": value, "source_unit": unit, "decimal": None, "percent": None}
    if value is None:
        warnings.append(_warning("INVALID_NUMERIC_VALUE", field, f"{field} is not a finite number."))
        return result
    if unit == "decimal":
        result["decimal"] = value
        result["percent"] = value * 100.0
    elif unit == "percent":
        result["decimal"] = value / 100.0
        result["percent"] = value
    elif unit == "bps":
        result["decimal"] = value / 10_000.0
        result["percent"] = value / 100.0
    else:
        warnings.append(
            _warning(
                "AMBIGUOUS_RATE_UNIT",
                field,
                f"{field} requires an explicit decimal, percent, or bps unit; no magnitude-based inference was used.",
            )
        )
    return result


def _normalise_basis(
    raw: Any,
    *,
    default_venue: str | None,
    default_product: str | None,
    warnings: list[dict[str, str]],
) -> dict[str, Any] | None:
    """Normalise basis while retaining whether it is a rate or price spread."""

    if raw is None:
        return None
    metric = _metric(raw, default_venue=default_venue, default_product=default_product)
    value = metric["value"]
    unit = metric["unit"]
    result = {
        **_scope(metric),
        "kind": None,
        "raw_value": value,
        "source_unit": unit,
        "decimal": None,
        "percent": None,
        "absolute_value": None,
        "currency": metric["currency"],
    }
    if value is None:
        warnings.append(_warning("INVALID_NUMERIC_VALUE", "basis", "basis is not a finite number."))
    elif unit in {"decimal", "percent", "bps"}:
        result["kind"] = "rate"
        if unit == "decimal":
            result["decimal"] = value
            result["percent"] = value * 100.0
        elif unit == "percent":
            result["decimal"] = value / 100.0
            result["percent"] = value
        else:
            result["decimal"] = value / 10_000.0
            result["percent"] = value / 100.0
    elif unit in {"usd", "usdc", "usdt", "quote", "quote_asset"} and metric["currency"]:
        result["kind"] = "absolute"
        result["absolute_value"] = value
    else:
        warnings.append(
            _warning(
                "AMBIGUOUS_BASIS_UNIT",
                "basis",
                "basis requires percent/decimal/bps for a rate or an explicit quote-currency denomination.",
            )
        )
    return result


def _normalise_amount(
    raw: Any,
    *,
    field: str,
    default_venue: str | None,
    default_product: str | None,
    warnings: list[dict[str, str]],
) -> dict[str, Any] | None:
    if raw is None:
        return None
    metric = _metric(raw, default_venue=default_venue, default_product=default_product)
    result = {
        **_scope(metric),
        "value": metric["value"],
        "unit": metric["unit"],
        "currency": metric["currency"],
    }
    if metric["value"] is None:
        warnings.append(_warning("INVALID_NUMERIC_VALUE", field, f"{field} is not a finite number."))
    if metric["unit"] is None and metric["currency"] is None:
        warnings.append(
            _warning(
                "AMBIGUOUS_AMOUNT_UNIT",
                field,
                f"{field} requires contracts, base asset, quote asset, or currency denomination.",
            )
        )
    elif metric["unit"] not in _AMOUNT_UNITS and metric["unit"] not in {None, "percent", "decimal", "ratio"}:
        warnings.append(_warning("UNKNOWN_AMOUNT_UNIT", field, f"Unrecognised {field} unit: {metric['unit']}."))
    return result


def _liquidations(
    raw: Any,
    *,
    default_venue: str | None,
    default_product: str | None,
    warnings: list[dict[str, str]],
) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        return _normalise_amount(
            raw,
            field="liquidations",
            default_venue=default_venue,
            default_product=default_product,
            warnings=warnings,
        )

    context = _metric(raw, default_venue=default_venue, default_product=default_product)
    unit = context["unit"]
    currency = context["currency"]
    long_value = _number(_first(raw, "long", "long_liquidations", "longLiquidations"))
    short_value = _number(_first(raw, "short", "short_liquidations", "shortLiquidations"))
    total_value = _number(_first(raw, "total", "value", "total_liquidations", "totalLiquidations"))
    if total_value is None and long_value is not None and short_value is not None:
        total_value = long_value + short_value
    if unit is None and currency is None:
        warnings.append(
            _warning(
                "AMBIGUOUS_AMOUNT_UNIT",
                "liquidations",
                "Liquidation values require contracts, base asset, quote asset, or currency denomination.",
            )
        )
    if long_value is None and short_value is None and total_value is None:
        warnings.append(_warning("INVALID_NUMERIC_VALUE", "liquidations", "No finite liquidation value was supplied."))
    return {
        **_scope(context),
        "long": long_value,
        "short": short_value,
        "total": total_value,
        "unit": unit,
        "currency": currency,
    }


def _long_short(
    raw: Any,
    *,
    default_venue: str | None,
    default_product: str | None,
    warnings: list[dict[str, str]],
) -> dict[str, Any] | None:
    if raw is None:
        return None
    metric = _metric(raw, default_venue=default_venue, default_product=default_product)
    source = metric["raw"] if isinstance(metric["raw"], Mapping) else {}
    unit = metric["unit"]
    ratio = metric["value"]
    long_share = _number(_first(source, "long_share", "longShare"))
    short_share = _number(_first(source, "short_share", "shortShare"))
    long_pct = _number(_first(source, "long_pct", "long_percent", "longPercent"))
    short_pct = _number(_first(source, "short_pct", "short_percent", "shortPercent"))

    if long_pct is not None or short_pct is not None:
        if long_pct is None or short_pct is None:
            warnings.append(
                _warning("INCOMPLETE_LONG_SHORT_SHARES", "long_short_ratio", "Both long and short percentages are required.")
            )
        else:
            long_share = long_pct / 100.0
            short_share = short_pct / 100.0
            if not math.isclose(long_share + short_share, 1.0, rel_tol=0.0, abs_tol=0.02):
                warnings.append(
                    _warning(
                        "LONG_SHORT_SHARES_NOT_NORMALISED",
                        "long_short_ratio",
                        "Long and short percentages do not add to approximately 100%.",
                    )
                )
            if short_share > 0:
                ratio = long_share / short_share
            unit = "percent"
    elif long_share is not None or short_share is not None:
        if long_share is None or short_share is None:
            warnings.append(
                _warning("INCOMPLETE_LONG_SHORT_SHARES", "long_short_ratio", "Both long and short shares are required.")
            )
        else:
            if not math.isclose(long_share + short_share, 1.0, rel_tol=0.0, abs_tol=0.02):
                warnings.append(
                    _warning(
                        "LONG_SHORT_SHARES_NOT_NORMALISED",
                        "long_short_ratio",
                        "Long and short shares do not add to approximately 1.0.",
                    )
                )
            if short_share > 0:
                ratio = long_share / short_share
            unit = "decimal"
    elif unit == "ratio" and ratio is not None:
        if ratio < 0:
            warnings.append(_warning("INVALID_RATIO", "long_short_ratio", "Long/short ratio cannot be negative."))
        else:
            long_share = ratio / (1.0 + ratio)
            short_share = 1.0 / (1.0 + ratio)
    else:
        warnings.append(
            _warning(
                "AMBIGUOUS_LONG_SHORT_UNIT",
                "long_short_ratio",
                "Specify unit=ratio, or provide both long_pct and short_pct (or decimal shares).",
            )
        )

    return {
        **_scope(metric),
        "ratio": ratio if unit in {"ratio", "percent", "decimal"} else None,
        "long_share": long_share,
        "short_share": short_share,
        "source_unit": unit,
    }


def _collect_context(feature: Any, venues: set[str], products: set[str], evidence_refs: list[str]) -> None:
    if not isinstance(feature, Mapping):
        return
    venue = _normalise_venue(feature.get("venue"))
    product = _normalise_product(feature.get("product_type"))
    evidence = _clean_text(feature.get("evidence_ref"))
    if venue:
        venues.add(venue)
    if product:
        products.add(product)
    if evidence and evidence not in evidence_refs:
        evidence_refs.append(evidence)


def build_crypto_features(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build an auditable crypto feature slice from an ordinary dictionary.

    Supported input forms are ``{"crypto_factors": {...}, "instrument": {...}}``
    and a direct factors dictionary.  Units are never inferred from magnitude.
    """

    source: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}
    nested = source.get("crypto_factors")
    factors: Mapping[str, Any] = nested if isinstance(nested, Mapping) else source
    instrument_raw = source.get("instrument")
    if not isinstance(instrument_raw, Mapping):
        instrument_raw = factors.get("instrument")
    instrument: Mapping[str, Any] = instrument_raw if isinstance(instrument_raw, Mapping) else {}

    venue = _normalise_venue(_first(instrument, "venue", "exchange")) or _normalise_venue(
        _first(factors, "venue", "exchange")
    )
    product = _normalise_product(_first(instrument, "product_type", "market_type", "instrument_type")) or (
        _normalise_product(_first(factors, "product_type", "market_type", "instrument_type"))
    )
    market = {
        "asset": _clean_text(_first(instrument, "asset", "underlying", "base_asset"))
        or _clean_text(_first(factors, "asset", "underlying", "base_asset")),
        "symbol": _clean_text(_first(instrument, "symbol", "instrument_id"))
        or _clean_text(_first(factors, "symbol", "instrument_id")),
        "venue": venue,
        "product_type": product,
        "quote_asset": _clean_text(_first(instrument, "quote_asset", "quote", "quote_currency"))
        or _clean_text(_first(factors, "quote_asset", "quote", "quote_currency")),
        "settlement_asset": _clean_text(_first(instrument, "settlement_asset", "settle_asset")),
    }

    warnings: list[dict[str, str]] = []
    funding = _normalise_rate(
        _metric_input(factors, "funding_rate", "fundingRate", rate=True),
        field="funding_rate",
        default_venue=venue,
        default_product=product,
        warnings=warnings,
    )
    open_interest = _normalise_amount(
        _metric_input(factors, "open_interest", "openInterest", "oi", amount=True),
        field="open_interest",
        default_venue=venue,
        default_product=product,
        warnings=warnings,
    )
    oi_change = _normalise_rate(
        _metric_input(
            factors,
            "open_interest_change_24h",
            "openInterestChange24h",
            "openInterestCh24h",
            rate=True,
        ),
        field="open_interest_change_24h",
        default_venue=venue,
        default_product=product,
        warnings=warnings,
    )
    basis = _normalise_basis(
        _metric_input(
            factors,
            "basis",
            "basis_rate",
            "basisRate",
            "premium_rate",
            "premiumRate",
            rate=True,
            amount=True,
        ),
        default_venue=venue,
        default_product=product,
        warnings=warnings,
    )
    liquidations = _liquidations(
        _liquidation_input(factors),
        default_venue=venue,
        default_product=product,
        warnings=warnings,
    )
    long_short = _long_short(
        _long_short_input(factors),
        default_venue=venue,
        default_product=product,
        warnings=warnings,
    )
    market_cap = _normalise_amount(
        _metric_input(factors, "market_cap", "marketCap", amount=True),
        field="market_cap",
        default_venue=None,
        default_product=None,
        warnings=warnings,
    )
    volume = _normalise_amount(
        _metric_input(factors, "volume_24h", "volume24h", "volume", amount=True),
        field="volume_24h",
        default_venue=venue,
        default_product=product,
        warnings=warnings,
    )
    turnover = _normalise_amount(
        _metric_input(factors, "turnover_24h", "turnover24h", "turnover", amount=True),
        field="turnover_24h",
        default_venue=venue,
        default_product=product,
        warnings=warnings,
    )

    derivatives = {
        "funding_rate": funding,
        "open_interest": open_interest,
        "open_interest_change_24h": oi_change,
        "basis": basis,
        "liquidations": liquidations,
        "long_short_ratio": long_short,
    }
    for name, feature in derivatives.items():
        if feature is None:
            continue
        if not feature.get("venue"):
            warnings.append(
                _warning(
                    "MISSING_METRIC_VENUE",
                    name,
                    f"{name} requires an explicit venue or aggregate venue scope.",
                )
            )
        if not feature.get("product_type"):
            warnings.append(
                _warning(
                    "MISSING_PRODUCT_TYPE",
                    name,
                    f"{name} requires an explicit perpetual product type.",
                )
            )
        elif feature.get("product_type") == "spot":
            warnings.append(
                _warning(
                    "DERIVATIVE_METRIC_ON_SPOT",
                    name,
                    f"{name} was tagged as spot; it cannot be treated as a perpetual-market feature.",
                    severity="error",
                )
            )

    volume_to_market_cap = None
    if volume is not None and market_cap is not None:
        vol_value = volume.get("value")
        cap_value = market_cap.get("value")
        vol_ccy = (volume.get("currency") or "").upper()
        cap_ccy = (market_cap.get("currency") or "").upper()
        if vol_value is not None and cap_value is not None and cap_value > 0:
            if not vol_ccy or not cap_ccy:
                warnings.append(
                    _warning(
                        "AMBIGUOUS_TURNOVER_CURRENCY",
                        "volume_to_market_cap",
                        "Volume-to-market-cap requires an explicit currency on both inputs.",
                    )
                )
            elif vol_ccy != cap_ccy:
                warnings.append(
                    _warning(
                        "TURNOVER_CURRENCY_MISMATCH",
                        "volume_to_market_cap",
                        f"Volume currency {vol_ccy} differs from market-cap currency {cap_ccy}.",
                        severity="error",
                    )
                )
            else:
                volume_to_market_cap = {
                    "ratio": vol_value / cap_value,
                    "percent": (vol_value / cap_value) * 100.0,
                    "currency": vol_ccy,
                    "volume_scope": {"venue": volume.get("venue"), "product_type": volume.get("product_type")},
                    "market_cap_scope": "asset_global",
                }

    features = {
        "market_activity": {
            "market_cap": market_cap,
            "volume_24h": volume,
            "turnover_24h": turnover,
            "volume_to_market_cap": volume_to_market_cap,
        },
        "derivatives": derivatives,
    }

    # Scope conflicts are evaluated inside the derivatives family. A clearly
    # tagged spot-volume observation may legitimately contextualise a
    # perpetual strategy; it must not make the derivatives slice unusable.
    derivative_venues: set[str] = set()
    derivative_products: set[str] = set()
    evidence_refs: list[str] = []
    for feature in [funding, open_interest, oi_change, basis, liquidations, long_short]:
        _collect_context(feature, derivative_venues, derivative_products, evidence_refs)
    for feature in [volume, turnover]:
        _collect_context(feature, set(), set(), evidence_refs)
    _collect_context(market_cap, set(), set(), evidence_refs)

    for container in (source, factors):
        explicit_refs = _first(container, "evidence_refs", "evidenceRefs")
        if isinstance(explicit_refs, Sequence) and not isinstance(explicit_refs, (str, bytes)):
            for value in explicit_refs:
                ref = _clean_text(value)
                if ref and ref not in evidence_refs:
                    evidence_refs.append(ref)

    specific_derivative_venues = {item for item in derivative_venues if item != "aggregate"}
    mixed_venues = len(specific_derivative_venues) > 1
    mixed_products = len(derivative_products) > 1
    if mixed_venues:
        warnings.append(
            _warning(
                "MIXED_VENUES",
                "market",
                f"Derivative metrics contain multiple venues ({', '.join(sorted(derivative_venues))}); no cross-venue equivalence was assumed.",
                severity="error",
            )
        )
    if mixed_products:
        warnings.append(
            _warning(
                "MIXED_PRODUCT_TYPES",
                "market",
                f"Derivative metrics contain multiple product types ({', '.join(sorted(derivative_products))}); spot and derivatives were not merged.",
                severity="error",
            )
        )

    warning_codes = {item["code"] for item in warnings}
    has_unit_ambiguity = any("AMBIGUOUS" in code or "UNKNOWN" in code for code in warning_codes)
    has_scope_ambiguity = bool({"MISSING_METRIC_VENUE", "MISSING_PRODUCT_TYPE"} & warning_codes)
    has_context_error = any(item["severity"] == "error" for item in warnings)
    quality_flags = {
        "mixed_venues": mixed_venues,
        "mixed_product_types": mixed_products,
        "unit_ambiguity": has_unit_ambiguity,
        "scope_ambiguity": has_scope_ambiguity,
        "derivative_metric_on_spot": "DERIVATIVE_METRIC_ON_SPOT" in warning_codes,
        "has_evidence": bool(evidence_refs),
        "usable_for_directional_analysis": any(
            feature is not None for feature in (funding, open_interest, oi_change, basis, liquidations, long_short, market_cap, volume, turnover)
        ) and not (has_context_error or has_unit_ambiguity or has_scope_ambiguity),
    }

    return {
        "version": VERSION,
        "market": market,
        "features": features,
        "evidence_refs": evidence_refs,
        "warnings": warnings,
        "quality_flags": quality_flags,
    }


def extract_crypto_features(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Compatibility alias for feature-pipeline callers."""

    return build_crypto_features(payload)


__all__ = ["VERSION", "build_crypto_features", "extract_crypto_features"]
