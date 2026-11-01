"""User-owned formula factors with a bounded, deterministic expression runtime."""

from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from app.services.factors import (
    FactorError,
    compute_catalog_factor,
    factor_warmup_bars,
    get_factor_metadata,
)
from app.utils.db import get_db_connection


_FACTOR_REF = re.compile(r"^custom:(\d+)$", re.IGNORECASE)
_RAW_FIELDS = {
    "open", "high", "low", "close", "volume", "amount",
    "market_cap", "net_income", "shareholder_equity", "revenue_growth",
    "total_debt", "free_cash_flow", "pe_ratio", "pb_ratio",
    "return_on_equity", "debt_to_equity",
}
_SAFE_FUNCTIONS = {"abs", "min", "max", "log", "log1p", "sqrt", "clip", "factor"}
_SAFE_BINARY = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.Pow: lambda left, right: left ** right,
    ast.Mod: lambda left, right: left % right,
}
_SAFE_COMPARE = {
    ast.Gt: lambda left, right: left > right,
    ast.GtE: lambda left, right: left >= right,
    ast.Lt: lambda left, right: left < right,
    ast.LtE: lambda left, right: left <= right,
    ast.Eq: lambda left, right: left == right,
    ast.NotEq: lambda left, right: left != right,
}


class CustomFactorError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CustomFactorAnalysis:
    required_fields: tuple[str, ...]
    warmup_bars: int
    dependencies: tuple[str, ...]


def _parameter_schema(values: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    schema: dict[str, dict[str, Any]] = {}
    for key, value in values.items():
        if isinstance(value, bool):
            schema[key] = {"type": "boolean"}
        elif isinstance(value, int):
            schema[key] = {"type": "integer", "minimum": 1, "maximum": 5000, "step": 1}
        elif isinstance(value, float):
            schema[key] = {"type": "number", "step": 0.1}
        else:
            raise CustomFactorError("factor.customInvalidParameter")
    return schema


def _parse_expression(expression: str) -> ast.Expression:
    source = str(expression or "").strip()
    if not source:
        raise CustomFactorError("factor.customExpressionRequired")
    if len(source) > 2000:
        raise CustomFactorError("factor.customExpressionTooLarge")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise CustomFactorError("factor.customSyntaxInvalid") from exc
    if sum(1 for _ in ast.walk(tree)) > 120:
        raise CustomFactorError("factor.customExpressionTooComplex")
    return tree


def _literal_or_parameter(node: ast.AST, parameters: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool)):
        return node.value
    if isinstance(node, ast.Name) and node.id in parameters:
        return parameters[node.id]
    raise CustomFactorError("factor.customParameterMustBeLiteral")


def analyze_custom_factor(expression: str, default_params: Mapping[str, Any] | None = None) -> CustomFactorAnalysis:
    parameters = dict(default_params or {})
    _parameter_schema(parameters)
    tree = _parse_expression(expression)
    required_fields: set[str] = set()
    dependencies: set[str] = set()
    warmup = 1

    def visit(node: ast.AST) -> None:
        nonlocal warmup
        if isinstance(node, ast.Expression):
            visit(node.body)
            return
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (str, int, float, bool)):
                raise CustomFactorError("factor.customNodeUnsupported")
            return
        if isinstance(node, ast.Name):
            if node.id in parameters:
                return
            if node.id in _RAW_FIELDS:
                required_fields.add(node.id)
                return
            raise CustomFactorError("factor.customUnknownName")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub, ast.Not)):
            visit(node.operand)
            return
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_BINARY:
            visit(node.left)
            visit(node.right)
            return
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            for value in node.values:
                visit(value)
            return
        if isinstance(node, ast.Compare) and all(type(item) in _SAFE_COMPARE for item in node.ops):
            visit(node.left)
            for comparator in node.comparators:
                visit(comparator)
            return
        if isinstance(node, ast.IfExp):
            visit(node.test)
            visit(node.body)
            visit(node.orelse)
            return
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            raise CustomFactorError("factor.customNodeUnsupported")
        function_name = node.func.id
        if function_name in _SAFE_FUNCTIONS - {"factor"}:
            if node.keywords:
                raise CustomFactorError("factor.customKeywordUnsupported")
            for argument in node.args:
                visit(argument)
            return
        if function_name == "factor":
            if not node.args:
                raise CustomFactorError("factor.customDependencyRequired")
            dependency = str(_literal_or_parameter(node.args[0], parameters)).strip()
            if dependency.lower().startswith("custom:"):
                raise CustomFactorError("factor.customNestedDependencyUnsupported")
            for argument in node.args[1:]:
                visit(argument)
        else:
            dependency = function_name
            for argument in node.args:
                visit(argument)
        kwargs = {
            item.arg: _literal_or_parameter(item.value, parameters)
            for item in node.keywords
            if item.arg
        }
        try:
            metadata = get_factor_metadata(dependency)
        except (FactorError, ValueError) as exc:
            raise CustomFactorError("factor.customDependencyNotFound") from exc
        dependencies.add(dependency)
        required_fields.update(str(item) for item in metadata.get("required_fields") or [])
        try:
            warmup = max(warmup, factor_warmup_bars(dependency.lower(), kwargs))
        except FactorError:
            warmup = max(warmup, int(metadata.get("default_warmup_bars") or 1))

    visit(tree)
    return CustomFactorAnalysis(
        required_fields=tuple(sorted(required_fields)),
        warmup_bars=max(1, int(warmup)),
        dependencies=tuple(sorted(dependencies)),
    )


def compute_custom_factor(
    definition: Mapping[str, Any],
    frame: pd.DataFrame,
    params: Mapping[str, Any] | None = None,
) -> float:
    if frame is None or frame.empty:
        raise CustomFactorError("factor.noData")
    defaults = dict(definition.get("default_params") or definition.get("defaultParams") or {})
    values = {**defaults, **dict(params or {})}
    _parameter_schema(values)
    tree = _parse_expression(str(definition.get("expression") or ""))

    def evaluate(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in values:
                return values[node.id]
            if node.id in _RAW_FIELDS and node.id in frame.columns:
                series = pd.to_numeric(frame[node.id], errors="coerce").dropna()
                if series.empty:
                    raise CustomFactorError("factor.missingFields")
                return float(series.iloc[-1])
            raise CustomFactorError("factor.customUnknownName")
        if isinstance(node, ast.UnaryOp):
            value = evaluate(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +value
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.Not):
                return not value
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_BINARY:
            return _SAFE_BINARY[type(node.op)](evaluate(node.left), evaluate(node.right))
        if isinstance(node, ast.BoolOp):
            resolved = [bool(evaluate(item)) for item in node.values]
            return all(resolved) if isinstance(node.op, ast.And) else any(resolved)
        if isinstance(node, ast.Compare):
            left = evaluate(node.left)
            for operator, comparator in zip(node.ops, node.comparators):
                right = evaluate(comparator)
                if not _SAFE_COMPARE[type(operator)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return evaluate(node.body) if evaluate(node.test) else evaluate(node.orelse)
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            raise CustomFactorError("factor.customNodeUnsupported")
        name = node.func.id
        args = [evaluate(item) for item in node.args]
        kwargs = {item.arg: evaluate(item.value) for item in node.keywords if item.arg}
        if name == "abs":
            return abs(args[0])
        if name == "min":
            return min(args)
        if name == "max":
            return max(args)
        if name == "log":
            return math.log(args[0])
        if name == "log1p":
            return math.log1p(args[0])
        if name == "sqrt":
            return math.sqrt(args[0])
        if name == "clip":
            return min(max(args[0], args[1]), args[2])
        if name == "factor":
            dependency = str(args[0])
            return compute_catalog_factor(dependency, frame, kwargs)
        return compute_catalog_factor(name, frame, kwargs)

    try:
        result = float(evaluate(tree))
    except CustomFactorError:
        raise
    except (ArithmeticError, FactorError, TypeError, ValueError) as exc:
        raise CustomFactorError("factor.customComputeFailed") from exc
    if not math.isfinite(result):
        raise CustomFactorError("factor.customComputeFailed")
    return result


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return value
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    factor_id = int(row.get("id") or 0)
    return {
        "id": factor_id,
        "factor_id": f"custom:{factor_id}",
        "version": str(row.get("version") or 1),
        "display_name": str(row.get("name") or ""),
        "description": str(row.get("description") or ""),
        "category": str(row.get("category") or "custom"),
        "factor_type": "technical",
        "provider": "user",
        "family": "custom",
        "required_fields": list(_loads(row.get("required_fields"), [])),
        "default_params": dict(_loads(row.get("default_params"), {})),
        "parameter_schema": dict(_loads(row.get("parameter_schema"), {})),
        "direction_hint": str(row.get("direction_hint") or "neutral"),
        "supported_contexts": ["portfolio"],
        "default_warmup_bars": int(row.get("warmup_bars") or 1),
        "expression": str(row.get("expression") or ""),
        "dependencies": list(_loads(row.get("dependencies"), [])),
        "is_custom": True,
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


def list_custom_factors(user_id: int) -> list[dict[str, Any]]:
    with get_db_connection() as db:
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT * FROM qd_user_factors
            WHERE user_id = ? AND status = 'active'
            ORDER BY updated_at DESC, id DESC
            """,
            (int(user_id),),
        )
        rows = cursor.fetchall()
        cursor.close()
    return [_metadata(row) for row in rows]


def get_custom_factor(user_id: int, factor_ref: str | int) -> dict[str, Any]:
    match = _FACTOR_REF.match(str(factor_ref or "").strip())
    factor_id = int(match.group(1)) if match else int(factor_ref)
    with get_db_connection() as db:
        cursor = db.cursor()
        cursor.execute(
            "SELECT * FROM qd_user_factors WHERE id = ? AND user_id = ? AND status = 'active'",
            (factor_id, int(user_id)),
        )
        row = cursor.fetchone()
        cursor.close()
    if not row:
        raise CustomFactorError("factor.customNotFound")
    return _metadata(row)


def save_custom_factor(user_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    expression = str(payload.get("expression") or "").strip()
    category = str(payload.get("category") or "custom").strip().lower()[:40]
    direction = str(payload.get("directionHint") or payload.get("direction_hint") or "neutral").strip()
    if not name or len(name) > 120:
        raise CustomFactorError("factor.customNameInvalid")
    if direction not in {"neutral", "higher_is_bullish", "lower_is_bullish"}:
        raise CustomFactorError("factor.customDirectionInvalid")
    default_params = payload.get("defaultParams") or payload.get("default_params") or {}
    if not isinstance(default_params, Mapping):
        raise CustomFactorError("factor.customInvalidParameter")
    default_params = dict(default_params)
    schema = _parameter_schema(default_params)
    analysis = analyze_custom_factor(expression, default_params)

    with get_db_connection() as db:
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO qd_user_factors (
                user_id, name, description, category, direction_hint, expression,
                default_params, parameter_schema, required_fields, dependencies,
                warmup_bars, version, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?::jsonb, ?::jsonb, ?::jsonb, ?::jsonb, ?, 1, 'active')
            RETURNING id
            """,
            (
                int(user_id), name, description, category, direction, expression,
                json.dumps(default_params), json.dumps(schema),
                json.dumps(list(analysis.required_fields)),
                json.dumps(list(analysis.dependencies)), analysis.warmup_bars,
            ),
        )
        created = cursor.fetchone()
        factor_id = int(created["id"])
        cursor.execute(
            """
            INSERT INTO qd_user_factor_versions (
                factor_id, user_id, version_no, expression, metadata
            ) VALUES (?, ?, 1, ?, ?::jsonb)
            """,
            (
                factor_id, int(user_id), expression,
                json.dumps({
                    "name": name,
                    "description": description,
                    "category": category,
                    "directionHint": direction,
                    "defaultParams": default_params,
                    "requiredFields": list(analysis.required_fields),
                    "dependencies": list(analysis.dependencies),
                    "warmupBars": analysis.warmup_bars,
                }),
            ),
        )
        db.commit()
        cursor.close()
    return get_custom_factor(user_id, factor_id)


def archive_custom_factor(user_id: int, factor_id: int) -> None:
    with get_db_connection() as db:
        cursor = db.cursor()
        cursor.execute(
            "UPDATE qd_user_factors SET status = 'archived', updated_at = NOW() WHERE id = ? AND user_id = ?",
            (int(factor_id), int(user_id)),
        )
        if cursor.rowcount != 1:
            raise CustomFactorError("factor.customNotFound")
        db.commit()
        cursor.close()
