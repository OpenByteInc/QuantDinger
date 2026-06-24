"""
Heuristic quality hints for QuantDinger indicator Python code.

Read-only analysis: @strategy parsing, structure checks, risk/position sanity.
Does not execute user code.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from app.services.indicator_params import IndicatorParamsParser, StrategyConfigParser

_IGNORED_STRATEGY_KEYS = frozenset({"leverage"})


def _has_df_buy_sell(code: str) -> bool:
    c = code or ""
    if re.search(r"df\s*\[\s*['\"]buy['\"]\s*\]", c):
        return True
    if re.search(r"df\s*\[\s*['\"]sell['\"]\s*\]", c):
        return True
    return False


def _has_four_way_signals(code: str) -> bool:
    c = code or ""
    cols = ("open_long", "close_long", "open_short", "close_short")
    return all(re.search(rf"df\s*\[\s*['\"]{col}['\"]\s*\]", c) for col in cols)


def _has_execution_signal_columns(code: str) -> bool:
    return _has_four_way_signals(code)


def _has_output_dict(code: str) -> bool:
    if re.search(r"\boutput\s*=\s*\{", code or ""):
        return True
    return False


def _has_my_indicator_meta(code: str) -> tuple[bool, bool]:
    c = code or ""
    name = bool(re.search(r"^\s*my_indicator_name\s*=", c, re.MULTILINE))
    desc = bool(re.search(r"^\s*my_indicator_description\s*=", c, re.MULTILINE))
    return name, desc


def _has_df_copy(code: str) -> bool:
    return bool(re.search(r"df\s*=\s*df\.copy\s*\(\s*\)", code or ""))


def _declared_param_names(code: str) -> List[str]:
    names: List[str] = []
    for m in re.finditer(
        r"^\s*#\s*@param\s+(\w+)\s+(int|float|bool|str|string)\s+\S+",
        code or "",
        re.MULTILINE | re.IGNORECASE,
    ):
        names.append(m.group(1))
    return names


def _uses_params_get(code: str, name: str) -> bool:
    pattern = rf"params\s*\.?\s*get\s*\(\s*['\"]{re.escape(name)}['\"]\s*,?"
    return bool(re.search(pattern, code or ""))


def _normalize_param_default(value: Any, param_type: str) -> Any:
    param_type = (param_type or "").lower()
    if param_type == "string":
        param_type = "str"
    if param_type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on")
        return bool(value)
    if param_type == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if param_type == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if param_type == "str":
        return str(value)
    return value


def _param_default_mismatches(code: str) -> List[Dict[str, Any]]:
    declared = {
        p.get("name"): p
        for p in IndicatorParamsParser.parse_params(code or "")
        if p.get("name")
    }
    if not declared:
        return []

    try:
        import ast

        tree = ast.parse(code or "")
    except SyntaxError:
        return []

    mismatches: List[Dict[str, Any]] = []

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node):  # type: ignore[override]
            try:
                func = node.func
                is_params_get = (
                    isinstance(func, ast.Attribute)
                    and func.attr == "get"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "params"
                )
                if not is_params_get or len(node.args) < 2:
                    return self.generic_visit(node)
                first = node.args[0]
                if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                    return self.generic_visit(node)
                name = first.value
                spec = declared.get(name)
                if not spec:
                    return self.generic_visit(node)
                second = node.args[1]
                if not isinstance(second, ast.Constant):
                    return self.generic_visit(node)
                param_type = spec.get("type") or ""
                declared_default = _normalize_param_default(spec.get("default"), param_type)
                fallback = _normalize_param_default(second.value, param_type)
                if declared_default != fallback:
                    mismatches.append(
                        {
                            "name": name,
                            "declared": spec.get("default"),
                            "fallback": second.value,
                        }
                    )
            finally:
                self.generic_visit(node)

    Visitor().visit(tree)
    return mismatches


def _uses_where_none_for_markers(code: str) -> bool:
    return bool(re.search(r"\.where\s*\([^)]*,\s*None\s*\)\s*\.tolist\s*\(", code or ""))


# pandas-only methods that will AttributeError if invoked on a numpy ndarray.
# This is the #1 source of "AI-translated Pine/TDX script crashes at backtest".
_PANDAS_ONLY_METHODS = (
    "rolling", "fillna", "shift", "ewm", "iloc", "tolist",
    "astype", "where", "mask", "diff", "cumsum", "replace",
    "interpolate", "dropna", "resample", "groupby",
)
_PANDAS_METHOD_ALT = "|".join(_PANDAS_ONLY_METHODS)
_NUMPY_NDARRAY_PRODUCERS = ("where", "maximum", "minimum")
_NP_PRODUCER_ALT = "|".join(_NUMPY_NDARRAY_PRODUCERS)


def _strip_comments(code: str) -> str:
    """Strip end-of-line `#` comments; keeps line structure intact."""
    out_lines: List[str] = []
    for raw_line in (code or "").split("\n"):
        in_str: str | None = None
        escape = False
        cut = len(raw_line)
        for i, ch in enumerate(raw_line):
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if in_str is not None:
                if ch == in_str:
                    in_str = None
                continue
            if ch in ("'", '"'):
                in_str = ch
                continue
            if ch == "#":
                cut = i
                break
        out_lines.append(raw_line[:cut])
    return "\n".join(out_lines)


def _ndarray_pandas_method_misuse(code: str) -> List[Dict[str, str]]:
    """
    Detect the high-impact "ndarray called like a Series" anti-pattern.

    Three sub-patterns:
      1. Direct chaining: `np.where(...).rolling(...)` etc.
      2. Tainted variable: `x = np.where(...)` then later `x.rolling(...)`
      3. Helper-returned ndarray: a user-defined `def fn(...): ... return np.where(...)`
         (or `np.maximum/minimum`) is treated as a tainted producer; calls like
         `y = fn(...)` then `y.rolling(...)` are flagged.

    All matches are deterministic; we do not emit warn-level guesses here to keep
    false-positive rate at zero on legit code.
    """
    src = _strip_comments(code or "")
    if not src.strip():
        return []

    findings: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _record(symbol: str, method: str) -> None:
        key = (symbol, method)
        if key in seen:
            return
        seen.add(key)
        findings.append({"symbol": symbol, "method": method})

    # --- Pattern 1: direct chaining np.where(...).METHOD ---
    # Tolerate nested parens up to 3 levels (good enough for typical inputs).
    direct_re = re.compile(
        rf"\bnp\.({_NP_PRODUCER_ALT})\s*\("
        r"(?:[^()]|\([^()]*(?:\([^()]*\)[^()]*)*\))*"
        rf"\)\s*\.\s*({_PANDAS_METHOD_ALT})\b"
    )
    for m in direct_re.finditer(src):
        _record(f"np.{m.group(1)}(...)", m.group(2))

    # --- Pattern 3 (must precede tainted scan): helper functions whose body
    # returns np.where / np.maximum / np.minimum at any return statement. ---
    tainted_helpers: set[str] = set()
    for name, body in _iter_function_bodies(src):
        if re.search(rf"\breturn\s+np\.({_NP_PRODUCER_ALT})\s*\(", body):
            tainted_helpers.add(name)

    # --- Pattern 2: tainted variables (np producers + tainted helpers) ---
    producer_alt = _NP_PRODUCER_ALT
    helpers_alt = "|".join(re.escape(h) for h in tainted_helpers) if tainted_helpers else None

    # Build assignment regex(es)
    np_assign_re = re.compile(
        rf"^\s*(\w+)\s*(?::[^=\n]*)?\s*=\s*np\.({producer_alt})\s*\(",
        re.MULTILINE,
    )
    helper_assign_re = (
        re.compile(
            rf"^\s*(\w+)\s*(?::[^=\n]*)?\s*=\s*({helpers_alt})\s*\(",
            re.MULTILINE,
        )
        if helpers_alt
        else None
    )

    tainted_vars: Dict[str, str] = {}
    for m in np_assign_re.finditer(src):
        tainted_vars[m.group(1)] = f"np.{m.group(2)}(...)"
    if helper_assign_re is not None:
        for m in helper_assign_re.finditer(src):
            tainted_vars[m.group(1)] = f"{m.group(2)}(...)"

    if tainted_vars:
        var_alt = "|".join(re.escape(v) for v in tainted_vars)
        method_use_re = re.compile(
            rf"\b({var_alt})\s*\.\s*({_PANDAS_METHOD_ALT})\b"
        )
        for m in method_use_re.finditer(src):
            var_name = m.group(1)
            method = m.group(2)
            origin = tainted_vars.get(var_name, var_name)
            _record(f"{var_name} = {origin}", method)

    return findings


def _iter_function_bodies(src: str):
    """Yield (function_name, body_text) for every `def fn(...):` found in src.

    Body extent is determined by indentation: any contiguous run of lines whose
    leading whitespace is strictly greater than the `def` line's indent (with
    blank lines tolerated inside). This stops cleanly at the next sibling `def`,
    `class`, or module-level statement and prevents accidentally splatting two
    helpers into one window.
    """
    lines = src.split("\n")
    def_re = re.compile(r"^(\s*)def\s+(\w+)\s*\(")
    i = 0
    while i < len(lines):
        m = def_re.match(lines[i])
        if not m:
            i += 1
            continue
        def_indent = len(m.group(1))
        name = m.group(2)
        body_start = i + 1
        j = body_start
        while j < len(lines):
            row = lines[j]
            if not row.strip():
                j += 1
                continue
            row_indent = len(row) - len(row.lstrip())
            if row_indent > def_indent:
                j += 1
            else:
                break
        yield name, "\n".join(lines[body_start:j])
        i = j


def _helper_returns_ndarray(code: str) -> List[str]:
    """Names of user-defined helpers that return `np.where/maximum/minimum(...)`.

    These helpers are technically legal but become a footgun the moment callers
    chain pandas methods on the result. We surface them as warn so the user
    knows to wrap with `pd.Series(arr, index=df.index)` or rewrite the helper.

    Body extent is detected via indentation so `def foo(): return pd.Series(...)`
    immediately followed by `def bar(): return np.where(...)` does not bleed
    `foo` into `bar`'s body and produce a false positive on `foo`.
    """
    src = _strip_comments(code or "")
    if not src.strip():
        return []
    names: List[str] = []
    for name, body in _iter_function_bodies(src):
        if re.search(rf"\breturn\s+np\.({_NP_PRODUCER_ALT})\s*\(", body):
            if name not in names:
                names.append(name)
    return names


_FUTURE_SHIFT_RE = re.compile(r"\.\s*shift\s*\(\s*-\s*(\d+)\s*[\),]")
_FUTURE_ILOC_RE = re.compile(r"\.\s*iloc\s*\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*\+\s*(\d+)\s*[\],:]")
_FUTURE_BARSAGO_RE = re.compile(r"\bbars_ago\s*\(\s*-\s*\d+")


def _future_data_leak(code: str) -> List[Dict[str, str]]:
    """
    Detect look-ahead bias / future data leakage in indicator code.

    Three deterministic patterns (zero false positives on legit code):
      1. `.shift(-N)` with literal negative integer (N>=1) — pulls future rows
         into present. Only legit use is ML label-prep, never inside a trading
         signal indicator.
      2. `.iloc[<var>+<int>]` — inside a loop iterating row indices, this fetches
         rows AFTER the current one. Allows trivial paper-trading "perfect
         strategies" that cannot exist live.
      3. `bars_ago(-N)` — custom helper variant of the same anti-pattern.

    Notes:
      * `.shift(1)` (positive), `.shift()` (default 1), `.iloc[-1]` (last row),
        `.iloc[i-1]` (previous), `.iloc[0:10]`, `.iloc[:5]` are all SAFE and
        intentionally NOT matched.
    """
    src = _strip_comments(code or "")
    if not src.strip():
        return []
    findings: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _record(kind: str, snippet: str) -> None:
        key = (kind, snippet)
        if key in seen:
            return
        seen.add(key)
        findings.append({"kind": kind, "snippet": snippet})

    for m in _FUTURE_SHIFT_RE.finditer(src):
        _record("shift", f"shift(-{m.group(1)})")
    for m in _FUTURE_ILOC_RE.finditer(src):
        _record("iloc", f"iloc[{m.group(1)}+{m.group(2)}]")
    for m in _FUTURE_BARSAGO_RE.finditer(src):
        _record("bars_ago", "bars_ago(-N)")
    return findings


def _unknown_strategy_keys(code: str) -> List[str]:
    valid = set(StrategyConfigParser.VALID_KEYS.keys())
    unknown: List[str] = []
    for m in re.finditer(
        r"^\s*#\s*@strategy\s+(\w+)\s+(\S+)", code or "", re.MULTILINE | re.IGNORECASE
    ):
        key = m.group(1)
        if key not in valid:
            if key in _IGNORED_STRATEGY_KEYS:
                continue
            unknown.append(key)
    return unknown


def analyze_indicator_code_quality(code: str) -> List[Dict[str, Any]]:
    """
    Returns a list of hints:
      { "severity": "info"|"warn"|"error", "code": str, "params": dict optional }
    """
    hints: List[Dict[str, Any]] = []
    raw = (code or "").strip()
    if not raw:
        return [{"severity": "error", "code": "EMPTY_CODE", "params": {}}]

    name_ok, desc_ok = _has_my_indicator_meta(raw)
    if not name_ok:
        hints.append({"severity": "warn", "code": "MISSING_INDICATOR_NAME", "params": {}})
    if not desc_ok:
        hints.append({"severity": "info", "code": "MISSING_INDICATOR_DESCRIPTION", "params": {}})

    if not _has_df_copy(raw):
        hints.append({"severity": "info", "code": "MISSING_DF_COPY", "params": {}})

    if not _has_output_dict(raw):
        hints.append({"severity": "error", "code": "MISSING_OUTPUT", "params": {}})

    if not _has_execution_signal_columns(raw):
        hints.append({"severity": "warn", "code": "MISSING_BUY_SELL_COLUMNS", "params": {}})

    declared_params = _declared_param_names(raw)
    if declared_params:
        unread = [name for name in declared_params if not _uses_params_get(raw, name)]
        if unread:
            hints.append(
                {
                    "severity": "warn",
                    "code": "DECLARED_PARAMS_NOT_READ_VIA_PARAMS_GET",
                    "params": {"names": unread},
                }
            )
        mismatches = _param_default_mismatches(raw)
        if mismatches:
            hints.append(
                {
                    "severity": "error",
                    "code": "PARAM_DEFAULT_MISMATCH",
                    "params": {"items": mismatches},
                }
            )

    if _uses_where_none_for_markers(raw):
        hints.append(
            {
                "severity": "info",
                "code": "SIGNAL_MARKERS_USE_WHERE_NONE",
                "params": {},
            }
        )

    for finding in _ndarray_pandas_method_misuse(raw):
        hints.append(
            {
                "severity": "error",
                "code": "NDARRAY_PANDAS_METHOD_MISUSE",
                "params": {
                    "symbol": finding.get("symbol", ""),
                    "method": finding.get("method", ""),
                },
            }
        )

    helper_names = _helper_returns_ndarray(raw)
    if helper_names:
        hints.append(
            {
                "severity": "warn",
                "code": "HELPER_RETURNS_NDARRAY",
                "params": {
                    "names": helper_names,
                    "names_str": ", ".join(helper_names),
                },
            }
        )

    for finding in _future_data_leak(raw):
        hints.append(
            {
                "severity": "error",
                "code": "FUTURE_DATA_LEAK",
                "params": {
                    "kind": finding.get("kind", ""),
                    "snippet": finding.get("snippet", ""),
                },
            }
        )

    for bad_key in _unknown_strategy_keys(raw):
        hints.append(
            {
                "severity": "warn",
                "code": "UNKNOWN_STRATEGY_KEY",
                "params": {"key": bad_key},
            }
        )

    cfg = StrategyConfigParser.parse(raw)

    trading = _has_execution_signal_columns(raw)
    if trading:
        if not cfg:
            hints.append(
                {
                    "severity": "info",
                    "code": "NO_STRATEGY_ANNOTATIONS",
                    "params": {},
                }
            )
        else:
            slp = cfg.get("stopLossPct")
            tpp = cfg.get("takeProfitPct")
            if slp is None and tpp is None:
                hints.append(
                    {
                        "severity": "warn",
                        "code": "NO_STOP_AND_TAKE_PROFIT",
                        "params": {},
                    }
                )
            elif slp is None:
                hints.append(
                    {"severity": "info", "code": "NO_STOP_LOSS", "params": {}}
                )
            elif tpp is None:
                hints.append(
                    {"severity": "info", "code": "NO_TAKE_PROFIT", "params": {}}
                )
            elif slp == 0 and tpp == 0:
                hints.append(
                    {
                        "severity": "info",
                        "code": "ZERO_STOP_AND_TAKE_PROFIT",
                        "params": {},
                    }
                )

            ep = cfg.get("entryPct")
            if ep is not None:
                if ep < 0.15:
                    hints.append(
                        {
                            "severity": "warn",
                            "code": "ENTRY_PCT_VERY_LOW",
                            "params": {"pct": f"{ep * 100:.1f}"},
                        }
                    )
            if cfg.get("trailingEnabled"):
                tpct = cfg.get("trailingStopPct")
                if tpct is None or tpct == 0:
                    hints.append(
                        {
                            "severity": "warn",
                            "code": "TRAILING_NO_PCT",
                            "params": {},
                        }
                    )

    # Optional: obviously empty visualization (starter template style)
    if re.search(r"['\"]plots['\"]\s*:\s*\[\s*\]", raw) and re.search(
        r"['\"]signals['\"]\s*:\s*\[\s*\]", raw
    ):
        codes = {h["code"] for h in hints}
        if "MISSING_OUTPUT" not in codes:
            hints.append(
                {"severity": "info", "code": "EMPTY_PLOTS_AND_SIGNALS", "params": {}}
            )

    return hints
