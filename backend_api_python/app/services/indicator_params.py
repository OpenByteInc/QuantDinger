"""Parameter parsing and composition for chart indicators."""

import re
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from app.utils.db import get_db_connection
from app.utils.logger import get_logger

if TYPE_CHECKING:
    import pandas as pd

logger = get_logger(__name__)

class IndicatorParamsParser:
    """Parse chart indicator ``# @param`` declarations."""
    
    PARAM_PATTERN = re.compile(
        r'#\s*@param\s+(\w+)\s+(int|float|bool|str|string)\s+(\S+)\s*(.*)',
        re.IGNORECASE
    )

    # Optional sweep declarations inside the description:
    #   range=3:30:2     -> inclusive arithmetic series from 3 to 30 step 2
    #   values=3,5,10    -> explicit discrete list
    _RANGE_RE = re.compile(r'range\s*=\s*(-?\d+(?:\.\d+)?)\s*:\s*(-?\d+(?:\.\d+)?)\s*:\s*(-?\d+(?:\.\d+)?)', re.IGNORECASE)
    _VALUES_RE = re.compile(r'values\s*=\s*([^\s]+)', re.IGNORECASE)
    
    @classmethod
    def parse_params(cls, indicator_code: str) -> List[Dict[str, Any]]:
        """
        Parse ``# @param`` declarations from indicator source.
        
        Returns:
            List of param definitions:
            [
                {
                    "name": "ma_fast",
                    "type": "int",
                    "default": 5,
                    "description": "Short moving-average period",
                    "values": [3, 5, 7, ...]   # optional: from `range=...` or `values=...`
                },
                ...
            ]

        Optional sweep grammar (numeric params only):
        Sweep markers are stripped from the human description before being returned.
        """
        params = []
        if not indicator_code:
            return params
        
        for line in indicator_code.split('\n'):
            line = line.strip()
            match = cls.PARAM_PATTERN.match(line)
            if match:
                name = match.group(1)
                param_type = match.group(2).lower()
                default_str = match.group(3)
                description = match.group(4).strip() if match.group(4) else ''
                
                default = cls._convert_value(default_str, param_type)
                
                if param_type == 'string':
                    param_type = 'str'

                values: Optional[List[Any]] = None
                if param_type in ('int', 'float'):
                    values = cls._extract_sweep_values(description, param_type)
                description = cls._strip_sweep_markers(description)

                entry: Dict[str, Any] = {
                    "name": name,
                    "type": param_type,
                    "default": default,
                    "description": description,
                }
                if values:
                    entry["values"] = values
                params.append(entry)
        
        return params

    @classmethod
    def _extract_sweep_values(cls, description: str, param_type: str) -> Optional[List[Any]]:
        if not description:
            return None
        # Prefer explicit `values=...` over inferred `range=...` when both are present.
        m_values = cls._VALUES_RE.search(description)
        if m_values:
            raw = m_values.group(1)
            out: List[Any] = []
            for token in raw.split(','):
                token = token.strip()
                if not token:
                    continue
                converted = cls._convert_value(token, param_type)
                if converted is not None:
                    out.append(converted)
            # Deduplicate but preserve declared order
            seen = set()
            unique: List[Any] = []
            for v in out:
                if v in seen:
                    continue
                seen.add(v)
                unique.append(v)
            return unique or None
        m_range = cls._RANGE_RE.search(description)
        if m_range:
            try:
                lo = float(m_range.group(1))
                hi = float(m_range.group(2))
                step = float(m_range.group(3))
            except (TypeError, ValueError):
                return None
            if step == 0 or (hi - lo) * step < 0:
                return None
            out: List[Any] = []
            cursor = lo
            # Guard against runaway loops on malicious or absurd inputs.
            max_count = 1024
            while (step > 0 and cursor <= hi + 1e-9) or (step < 0 and cursor >= hi - 1e-9):
                if param_type == 'int':
                    out.append(int(round(cursor)))
                else:
                    out.append(round(cursor, 8))
                cursor += step
                if len(out) >= max_count:
                    break
            seen = set()
            unique: List[Any] = []
            for v in out:
                if v in seen:
                    continue
                seen.add(v)
                unique.append(v)
            return unique or None
        return None

    @classmethod
    def _strip_sweep_markers(cls, description: str) -> str:
        cleaned = cls._RANGE_RE.sub('', description or '')
        cleaned = cls._VALUES_RE.sub('', cleaned)
        return cleaned.strip()
    
    @classmethod
    def _convert_value(cls, value_str: str, param_type: str) -> Any:
        """Convert a raw string value to the declared parameter type."""
        try:
            param_type = param_type.lower()
            if param_type == 'int':
                return int(value_str)
            elif param_type == 'float':
                return float(value_str)
            elif param_type == 'bool':
                return value_str.lower() in ('true', '1', 'yes', 'on')
            else:  # str/string
                return value_str
        except (ValueError, TypeError):
            return value_str
    
    @classmethod
    def merge_params(cls, declared_params: List[Dict], user_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge declared defaults with user-provided values.
        
        Args:
            declared_params: Parameter declarations parsed from code.
            user_params: User-provided parameter values.
            
        Returns:
            Merged parameter dictionary using user values or defaults.
        """
        result = {}
        for param in declared_params:
            name = param['name']
            param_type = param['type']
            default = param['default']
            
            if name in user_params:
                result[name] = cls._convert_value(str(user_params[name]), param_type)
            else:
                result[name] = default
        
        return result

    @classmethod
    def apply_defaults_to_code(cls, indicator_code: str, param_values: Dict[str, Any]) -> str:
        """
        Rewrite ``# @param`` default values in indicator source (IDE apply-params).

        Mirrors the QuantDinger-Vue ``applyIndicatorParamsToCode`` logic so the
        backend can patch code when needed.
        """
        if not indicator_code or not param_values:
            return indicator_code or ""

        lines = (indicator_code or "").split("\n")
        changed = False
        for idx, line in enumerate(lines):
            match = cls.PARAM_PATTERN.match(line.strip())
            if not match:
                continue
            name = match.group(1)
            if name not in param_values:
                continue
            param_type = match.group(2).lower()
            if param_type == "string":
                param_type = "str"
            raw_val = param_values[name]
            if isinstance(raw_val, bool):
                val_str = "true" if raw_val else "false"
            else:
                val_str = str(raw_val)
            desc = match.group(4) or ""
            lines[idx] = f"# @param {name} {param_type} {val_str} {desc}".rstrip()
            changed = True
        return "\n".join(lines) if changed else indicator_code


class IndicatorCaller:
    """
    Indicator caller that allows one chart indicator to call another.
    
    Usage examples in indicator code:
        rsi_df = call_indicator(5, df)
        
        macd_df = call_indicator('My MACD', df)
    """
    
    MAX_CALL_DEPTH = 5
    
    def __init__(self, user_id: int, current_indicator_id: int = None):
        self.user_id = user_id
        self.current_indicator_id = current_indicator_id
        self._call_stack = []  # Detect circular indicator dependencies.
    
    def call_indicator(
        self, 
        indicator_ref: Any,  # int ID or str name
        df: 'pd.DataFrame',
        params: Dict[str, Any] = None,
        _depth: int = 0
    ) -> Optional['pd.DataFrame']:
        """
        Execute another indicator and return its DataFrame.
        
        Args:
            indicator_ref: Indicator ID or name.
            df: Input OHLCV DataFrame.
            params: Parameters passed to the called indicator.
            _depth: Internal recursion depth.
            
        Returns:
            DataFrame after the called indicator runs.
        """
        if _depth >= self.MAX_CALL_DEPTH:
            logger.error(f"Indicator call depth exceeded {self.MAX_CALL_DEPTH}")
            return df.copy()
        
        indicator_code, indicator_id = self._get_indicator_code(indicator_ref)
        if not indicator_code:
            logger.warning(f"Indicator not found: {indicator_ref}")
            return df.copy()
        
        if indicator_id in self._call_stack:
            logger.error(f"Circular dependency detected: {self._call_stack} -> {indicator_id}")
            return df.copy()
        
        self._call_stack.append(indicator_id)
        
        try:
            declared_params = IndicatorParamsParser.parse_params(indicator_code)
            merged_params = IndicatorParamsParser.merge_params(declared_params, params or {})
            
            df_copy = df.copy()
            from app.utils.safe_exec import safe_exec_indicator_isolated

            exec_result = safe_exec_indicator_isolated(
                code=indicator_code,
                df=df_copy,
                params=merged_params,
                timeout=30,
            )
            if not exec_result['success']:
                logger.error(f"Indicator {indicator_ref} rejected: {exec_result['error']}")
                return df.copy()

            result = exec_result.get('result') or {}
            return result.get('df', df_copy)
            
        except Exception as e:
            logger.error(f"Error calling indicator {indicator_ref}: {e}")
            return df.copy()
        finally:
            self._call_stack.pop()
    
    def _get_indicator_code(self, indicator_ref: Any) -> Tuple[Optional[str], Optional[int]]:
        """Fetch indicator code by ID or name."""
        try:
            with get_db_connection() as db:
                cursor = db.cursor()
                
                if isinstance(indicator_ref, int):
                    cursor.execute("""
                        SELECT id, code FROM qd_indicator_codes 
                        WHERE id = %s AND (user_id = %s OR publish_to_community = 1)
                    """, (indicator_ref, self.user_id))
                else:
                    cursor.execute("""
                        SELECT id, code FROM qd_indicator_codes 
                        WHERE name = %s AND user_id = %s
                        UNION
                        SELECT id, code FROM qd_indicator_codes 
                        WHERE name = %s AND publish_to_community = 1
                        LIMIT 1
                    """, (str(indicator_ref), self.user_id, str(indicator_ref)))
                
                row = cursor.fetchone()
                cursor.close()
                
                if row:
                    return row['code'], row['id']
                return None, None
                
        except Exception as e:
            logger.error(f"Error fetching indicator code: {e}")
            return None, None


def get_indicator_params(indicator_id: int, user_id: int) -> Optional[List[Dict[str, Any]]]:
    """
    Return indicator parameter declarations for API consumers.
    
    Args:
        indicator_id: Indicator ID.
        user_id: Current authenticated user ID.
        
    Returns:
        Parameter declaration list, or ``None`` when the indicator is not
        visible to the current user.
    """
    try:
        with get_db_connection() as db:
            cursor = db.cursor()
            cursor.execute(
                """
                SELECT code
                FROM qd_indicator_codes
                WHERE id = %s
                  AND (
                    user_id = %s
                    OR (
                      publish_to_community = 1
                      AND (review_status = 'approved' OR review_status IS NULL)
                    )
                  )
                """,
                (int(indicator_id), int(user_id)),
            )
            row = cursor.fetchone()
            cursor.close()
            
            if row and row['code']:
                return IndicatorParamsParser.parse_params(row['code'])
            return None
    except Exception as e:
        logger.error(f"Error getting indicator params: {e}")
        raise
