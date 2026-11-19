"""Marketplace applicability contracts for Strategy API V2 sources.

The runtime manifest answers what a source *declares*.  Marketplace users also
need to know whether an instrument is immutable, safely replaceable through a
single source binding, or supplied by a dynamic universe.  This module derives
that distinction once at publish time and provides the only supported source
adaptation path.
"""

from __future__ import annotations

import ast
import hashlib
from typing import Any, Optional

from app.services.strategy_v2 import inspect_strategy_v2
from app.services.strategy_v2.instruments import instrument_key_from_metadata, parse_instrument


CONTRACT_VERSION = 2
_BINDING_NAMES = {"INSTRUMENT", "SYMBOL", "TRADE_SYMBOL"}
_SCHEDULE_CALLS = {"run_daily", "run_weekly", "run_monthly"}


def _instrument_key(item: dict[str, Any]) -> str:
    return instrument_key_from_metadata(item)


def _reference_key(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return ""


def _literal_assignment(source: str, expected_instrument: str) -> Optional[dict[str, Any]]:
    """Return a structurally safe single-instrument binding, if one exists."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    candidates: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target_key = _reference_key(node.targets[0])
        if not target_key:
            continue
        allowed = target_key in _BINDING_NAMES or target_key == "g.symbol"
        if not allowed or not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            continue
        try:
            assigned_key = parse_instrument(node.value.value).key
        except Exception:
            continue
        if assigned_key != expected_instrument:
            continue
        candidates.append({
            "reference": target_key,
            "value": node.value.value,
            "lineno": int(node.value.lineno),
            "col_offset": int(node.value.col_offset),
            "end_lineno": int(node.value.end_lineno),
            "end_col_offset": int(node.value.end_col_offset),
        })

    if len(candidates) != 1:
        return None
    binding = candidates[0]

    # The same binding must feed the declared universe.  Merely having a
    # variable named SYMBOL is not enough to advertise portability.
    universe_refs: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "set_universe" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, (ast.List, ast.Tuple)) and len(first.elts) == 1:
            universe_refs.append(_reference_key(first.elts[0]))
    if universe_refs != [binding["reference"]]:
        return None
    return binding


def _marketplace_execution_metadata(source: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """Derive user-facing cadence metadata without changing the V2 manifest.

    ``execution_mode`` is deliberately a marketplace concept.  It describes
    how the published source is triggered; it is not the Strategy V2
    deployment ``executionMode`` (signal/live), and it is never fed back into
    the compiler or runtime.
    """
    frequencies = [str(value or "").strip() for value in (manifest.get("frequencies") or [])]
    frequencies = list(dict.fromkeys(value for value in frequencies if value))
    execution_frequency = str(
        manifest.get("drivingFrequency") or manifest.get("primaryFrequency") or ""
    ).strip()
    confirmation_frequencies = [
        value for value in frequencies if value != execution_frequency
    ]

    has_schedule = False
    has_bar_handler = False
    try:
        tree = ast.parse(str(source or ""))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "handle_data":
                has_bar_handler = True
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in _SCHEDULE_CALLS:
                    has_schedule = True
    except SyntaxError:
        pass

    if has_schedule and has_bar_handler:
        execution_mode = "hybrid"
    elif has_schedule:
        execution_mode = "scheduled"
    else:
        # Strategy V2 currently drives handle_data from completed subscribed
        # bars.  Do not infer a fictitious realtime/tick contract.
        execution_mode = "bar"

    return {
        "execution_mode": execution_mode,
        "execution_frequency": execution_frequency,
        "confirmation_frequencies": confirmation_frequencies,
        "frequencies": frequencies,
    }


def derive_strategy_contract(
    code: str,
    param_schema: Optional[dict[str, Any]] = None,
    *,
    source: str = "published_code",
) -> dict[str, Any]:
    program = inspect_strategy_v2(str(code or ""))
    manifest = program.manifest.metadata()
    universe = manifest.get("universe") if isinstance(manifest.get("universe"), dict) else {}
    instruments = [dict(item) for item in (universe.get("instruments") or []) if isinstance(item, dict)]
    instrument_keys = [key for key in (_instrument_key(item) for item in instruments) if key]
    universe_reference = str(universe.get("reference") or "")

    binding: Optional[dict[str, Any]] = None
    if universe_reference:
        binding_mode = "universe"
    elif len(instrument_keys) > 1:
        binding_mode = "portfolio"
    elif len(instrument_keys) == 1:
        binding = _literal_assignment(str(code or ""), instrument_keys[0])
        binding_mode = "parameterized" if binding else "fixed"
    else:
        binding_mode = "unknown"

    parameters = []
    raw_params = (param_schema or {}).get("params") if isinstance(param_schema, dict) else []
    for item in raw_params or []:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            continue
        parameters.append({
            "name": str(item.get("name") or ""),
            "label_key": str(item.get("labelKey") or item.get("label_key") or ""),
            "label": str(item.get("label") or ""),
            "type": str(item.get("type") or "number"),
            "default": item.get("default"),
            "min": item.get("min"),
            "max": item.get("max"),
            "step": item.get("step"),
        })

    data_fields: list[str] = []
    for subscription in manifest.get("subscriptions") or []:
        if not isinstance(subscription, dict):
            continue
        for field in subscription.get("fields") or []:
            value = str(field or "").strip().lower()
            if value and value not in data_fields:
                data_fields.append(value)

    market_types = sorted({str(item.get("market_type") or "").strip().lower() for item in instruments if item.get("market_type")})
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    execution = _marketplace_execution_metadata(str(code or ""), manifest)
    return {
        "contract_version": CONTRACT_VERSION,
        "contract_hash": hashlib.sha256(str(code or "").encode("utf-8")).hexdigest(),
        "source": source,
        "api_version": int(manifest.get("apiVersion") or 2),
        "binding_mode": binding_mode,
        "instrument_binding": binding and binding["reference"] or "",
        "strategy_type": str(manifest.get("strategyType") or "cta"),
        "direction_mode": str(manifest.get("directionMode") or ""),
        "execution_mode": execution["execution_mode"],
        "execution_frequency": execution["execution_frequency"],
        "confirmation_frequencies": execution["confirmation_frequencies"],
        "frequencies": execution["frequencies"],
        # Deprecated read aliases.  They keep older clients functional while
        # the canonical marketplace vocabulary remains unambiguous.
        "primary_frequency": execution["execution_frequency"],
        "driving_frequency": execution["execution_frequency"],
        "markets": list(manifest.get("markets") or []),
        "market_types": market_types,
        "universe_kind": str(universe.get("kind") or "static"),
        "universe_reference": universe_reference,
        "instruments": instruments,
        "bound_instruments": instrument_keys,
        "supported_markets": list(manifest.get("markets") or []),
        "supported_market_types": market_types,
        "benchmark": dict(manifest.get("benchmark")) if isinstance(manifest.get("benchmark"), dict) else None,
        "leverage_allowed": bool(manifest.get("leverageAllowed") or False),
        "max_leverage": float(manifest.get("maxLeverage") or 1.0),
        "warmup_bars": int(manifest.get("warmupBars") or 0),
        "factor_dependencies": list(manifest.get("factorDependencies") or []),
        "fundamental_dependencies": list(manifest.get("fundamentalDependencies") or []),
        "data_fields": data_fields,
        "parameters": parameters,
        "metadata": dict(metadata),
    }


# Canonical public name.  Keep the old function as a compatibility alias for
# internal callers and third-party extensions during the schema transition.
derive_marketplace_contract = derive_strategy_contract


def compatibility_for_target(
    contract: dict[str, Any],
    *,
    target_instrument: str = "",
) -> dict[str, Any]:
    mode = str(contract.get("binding_mode") or "unknown")
    target_raw = str(target_instrument or "").strip()
    reasons: list[str] = []
    normalized_target = ""
    target_meta: dict[str, Any] = {}

    if mode in {"universe", "portfolio"} and target_raw:
        reasons.append("individual_target_not_supported")
    elif mode in {"fixed", "parameterized"} and not target_raw:
        reasons.append("target_required")
    elif target_raw:
        try:
            target = parse_instrument(target_raw)
            normalized_target = target.key
            target_meta = target.metadata()
        except Exception:
            reasons.append("invalid_instrument")
        else:
            bound = list(contract.get("bound_instruments") or [])
            if mode == "fixed" and normalized_target not in bound:
                reasons.append("fixed_instrument_mismatch")
            if mode == "parameterized":
                markets = set(contract.get("supported_markets") or contract.get("markets") or [])
                market_types = set(contract.get("supported_market_types") or contract.get("market_types") or [])
                if markets and target.market not in markets:
                    reasons.append("market_mismatch")
                if market_types and target.market_type not in market_types:
                    reasons.append("market_type_mismatch")
                if contract.get("leverage_allowed") and not (
                    target.market == "Crypto" and target.market_type == "swap"
                ):
                    reasons.append("leverage_contract_requires_crypto_swap")

    return {
        "compatible": not reasons,
        "binding_mode": mode,
        "target_instrument": normalized_target or target_raw,
        "target": target_meta,
        "execution_mode": str(contract.get("execution_mode") or "bar"),
        "execution_frequency": str(
            contract.get("execution_frequency") or contract.get("primary_frequency") or ""
        ),
        "confirmation_frequencies": list(contract.get("confirmation_frequencies") or []),
        "requires_rebacktest": mode == "parameterized" and not reasons,
        "reason_codes": reasons,
        "bound_instruments": list(contract.get("bound_instruments") or []),
    }


def adapt_parameterized_source(code: str, contract: dict[str, Any], target_instrument: str) -> str:
    if str(contract.get("binding_mode") or "") != "parameterized":
        raise ValueError("marketplace.strategyNotParameterized")
    target = parse_instrument(target_instrument)
    compatibility = compatibility_for_target(contract, target_instrument=target.key)
    if not compatibility["compatible"]:
        raise ValueError("marketplace.strategyIncompatible:" + ",".join(compatibility["reason_codes"]))

    bound = list(contract.get("bound_instruments") or [])
    binding = _literal_assignment(str(code or ""), bound[0] if bound else "")
    if not binding:
        raise ValueError("marketplace.strategyBindingNotFound")
    lines = str(code or "").splitlines(keepends=True)
    start_line = binding["lineno"] - 1
    end_line = binding["end_lineno"] - 1
    if start_line != end_line:
        raise ValueError("marketplace.strategyBindingMultiline")
    line = lines[start_line]
    lines[start_line] = (
        line[:binding["col_offset"]]
        + repr(target.key)
        + line[binding["end_col_offset"]:]
    )
    adapted = "".join(lines)
    verified = derive_strategy_contract(adapted, source="marketplace_adaptation")
    if verified.get("bound_instruments") != [target.key]:
        raise ValueError("marketplace.strategyAdaptationVerificationFailed")
    return adapted
