"""
Indicator Parameters Parser and Helper Functions

支持两个核心功能：
1. 指标参数外部传递 - 解析指标代码中的 @param 声明
2. 指标调用其他指标 - 提供 call_indicator() 函数

参数声明格式：
# @param param_name type default_value 描述
# @param ma_fast int 5 短期均线周期
# @param ma_slow int 20 长期均线周期
# @param threshold float 0.5 阈值

支持的类型：int, float, bool, str
"""

import re
import json
from typing import Dict, Any, List, Optional, Tuple
from app.utils.logger import get_logger
from app.utils.db import get_db_connection

logger = get_logger(__name__)


class StrategyConfigParser:
    """
    解析指标代码中的 @strategy 注解，提取策略配置（止盈止损、仓位等）。

    支持的注解格式:
        # @strategy stopLossPct 0.02 止损比例
        # @strategy takeProfitPct 0.05 止盈比例
        # @strategy entryPct 0.5 仓位比例 (0-1)
        # 杠杆倍数由指标 IDE 回测面板单独设置，不再使用 @strategy leverage
        # @strategy trailingEnabled true 启用追踪止损
        # @strategy trailingStopPct 0.02 追踪止损比例
        # @strategy trailingActivationPct 0.03 追踪激活比例
        # @strategy tradeDirection long 交易方向
    """

    # 允许 key 与数值之间使用可选冒号，与指标 IDE 前端解析一致
    STRATEGY_PATTERN = re.compile(
        r'#\s*@strategy\s+(\w+)\s*:?\s*(\S+)\s*(.*)',
        re.IGNORECASE
    )

    VALID_KEYS = {
        'stopLossPct':          {'type': 'float', 'min': 0, 'max': 1},
        'takeProfitPct':        {'type': 'float', 'min': 0, 'max': 5},
        'entryPct':             {'type': 'float', 'min': 0.01, 'max': 1},
        'trailingEnabled':      {'type': 'bool'},
        'trailingStopPct':      {'type': 'float', 'min': 0, 'max': 1},
        'trailingActivationPct':{'type': 'float', 'min': 0, 'max': 1},
        'tradeDirection':       {'type': 'str',   'enum': ['long', 'short', 'both']},
    }

    @classmethod
    def parse(cls, code: str) -> Dict[str, Any]:
        """
        解析代码中的 @strategy 注解，返回策略配置字典。
        只包含代码中声明的键，未声明的不包含。
        """
        config: Dict[str, Any] = {}
        if not code:
            return config
        for line in code.split('\n'):
            line = line.strip()
            m = cls.STRATEGY_PATTERN.match(line)
            if not m:
                continue
            key = m.group(1)
            raw_val = m.group(2)
            if key not in cls.VALID_KEYS:
                continue
            spec = cls.VALID_KEYS[key]
            val = cls._convert(raw_val, spec)
            if val is not None:
                config[key] = val
        return config

    @classmethod
    def _convert(cls, raw: str, spec: Dict) -> Any:
        t = spec['type']
        try:
            if t == 'float':
                v = float(raw)
                v = max(spec.get('min', v), min(spec.get('max', v), v))
                return round(v, 6)
            elif t == 'int':
                v = int(raw)
                v = max(spec.get('min', v), min(spec.get('max', v), v))
                return v
            elif t == 'bool':
                return raw.lower() in ('true', '1', 'yes', 'on')
            elif t == 'str':
                if 'enum' in spec and raw not in spec['enum']:
                    return spec['enum'][0]
                return raw
        except (ValueError, TypeError):
            return None
        return None

    @classmethod
    def generate_annotations(cls, config: Dict[str, Any]) -> str:
        """
        从策略配置字典生成 @strategy 注解行。
        用于AI生成代码时自动附加。
        """
        lines = []
        for key, spec in cls.VALID_KEYS.items():
            if key in config:
                val = config[key]
                if spec['type'] == 'bool':
                    val = 'true' if val else 'false'
                lines.append(f'# @strategy {key} {val}')
        return '\n'.join(lines)


class IndicatorParamsParser:
    """解析指标代码中的参数声明"""
    
    # 参数声明正则：# @param name type default description
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
        解析指标代码中的参数声明
        
        Returns:
            List of param definitions:
            [
                {
                    "name": "ma_fast",
                    "type": "int",
                    "default": 5,
                    "description": "短期均线周期",
                    "values": [3, 5, 7, ...]   # optional: from `range=...` or `values=...`
                },
                ...
            ]

        Optional sweep grammar (numeric params only):
          # @param ma_fast int 5 短期均线周期 range=3:30:2
          # @param threshold float 0.5 阈值 values=0.3,0.5,0.7,0.9
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
                
                # 转换默认值类型
                default = cls._convert_value(default_str, param_type)
                
                # 规范化类型名
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
        """转换字符串值为对应类型"""
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
        合并声明的参数和用户提供的参数
        
        Args:
            declared_params: 从代码中解析的参数声明
            user_params: 用户提供的参数值
            
        Returns:
            合并后的参数字典（使用用户值或默认值）
        """
        result = {}
        for param in declared_params:
            name = param['name']
            param_type = param['type']
            default = param['default']
            
            if name in user_params:
                # 用户提供了值，转换为正确类型
                result[name] = cls._convert_value(str(user_params[name]), param_type)
            else:
                # 使用默认值
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
    指标调用器 - 允许一个指标调用另一个指标
    
    使用方式（在指标代码中）：
        # 按ID调用
        rsi_df = call_indicator(5, df)
        
        # 按名称调用（自己的指标）
        macd_df = call_indicator('My MACD', df)
    """
    
    # 最大调用深度，防止循环依赖
    MAX_CALL_DEPTH = 5
    
    def __init__(self, user_id: int, current_indicator_id: int = None):
        self.user_id = user_id
        self.current_indicator_id = current_indicator_id
        self._call_stack = []  # 调用栈，用于检测循环依赖
    
    def call_indicator(
        self, 
        indicator_ref: Any,  # int (ID) 或 str (名称)
        df: 'pd.DataFrame',
        params: Dict[str, Any] = None,
        _depth: int = 0
    ) -> Optional['pd.DataFrame']:
        """
        调用另一个指标并返回结果
        
        Args:
            indicator_ref: 指标ID或名称
            df: 输入的K线数据
            params: 传递给被调用指标的参数
            _depth: 内部使用，跟踪调用深度
            
        Returns:
            执行后的DataFrame，包含被调用指标计算的列
        """
        import pandas as pd
        import numpy as np
        
        # 检查调用深度
        if _depth >= self.MAX_CALL_DEPTH:
            logger.error(f"Indicator call depth exceeded {self.MAX_CALL_DEPTH}")
            return df.copy()
        
        # 获取指标代码
        indicator_code, indicator_id = self._get_indicator_code(indicator_ref)
        if not indicator_code:
            logger.warning(f"Indicator not found: {indicator_ref}")
            return df.copy()
        
        # 检查循环依赖
        if indicator_id in self._call_stack:
            logger.error(f"Circular dependency detected: {self._call_stack} -> {indicator_id}")
            return df.copy()
        
        self._call_stack.append(indicator_id)
        
        try:
            # 解析并合并参数
            declared_params = IndicatorParamsParser.parse_params(indicator_code)
            merged_params = IndicatorParamsParser.merge_params(declared_params, params or {})
            
            # 准备执行环境
            df_copy = df.copy()
            local_vars = {
                'df': df_copy,
                'open': df_copy['open'].astype('float64') if 'open' in df_copy.columns else pd.Series(dtype='float64'),
                'high': df_copy['high'].astype('float64') if 'high' in df_copy.columns else pd.Series(dtype='float64'),
                'low': df_copy['low'].astype('float64') if 'low' in df_copy.columns else pd.Series(dtype='float64'),
                'close': df_copy['close'].astype('float64') if 'close' in df_copy.columns else pd.Series(dtype='float64'),
                'volume': df_copy['volume'].astype('float64') if 'volume' in df_copy.columns else pd.Series(dtype='float64'),
                'signals': pd.Series(0, index=df_copy.index, dtype='float64'),
                'np': np,
                'pd': pd,
                'params': merged_params,
                # 递归调用支持
                'call_indicator': lambda ref, d, p=None: self.call_indicator(ref, d, p, _depth + 1)
            }
            
            from app.utils.safe_exec import build_safe_builtins, safe_exec_with_validation

            exec_env = local_vars.copy()
            exec_env['__builtins__'] = build_safe_builtins()

            exec_result = safe_exec_with_validation(
                code=indicator_code,
                exec_globals=exec_env,
                timeout=30,
            )
            if not exec_result['success']:
                logger.error(f"Indicator {indicator_ref} rejected: {exec_result['error']}")
                return df.copy()
            
            return exec_env.get('df', df_copy)
            
        except Exception as e:
            logger.error(f"Error calling indicator {indicator_ref}: {e}")
            return df.copy()
        finally:
            self._call_stack.pop()
    
    def _get_indicator_code(self, indicator_ref: Any) -> Tuple[Optional[str], Optional[int]]:
        """获取指标代码"""
        try:
            with get_db_connection() as db:
                cursor = db.cursor()
                
                if isinstance(indicator_ref, int):
                    # 按ID查询
                    cursor.execute("""
                        SELECT id, code FROM qd_indicator_codes 
                        WHERE id = %s AND (user_id = %s OR publish_to_community = 1)
                    """, (indicator_ref, self.user_id))
                else:
                    # 按名称查询（优先自己的指标）
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


def get_indicator_params(indicator_id: int) -> List[Dict[str, Any]]:
    """
    获取指标的参数声明（供API调用）
    
    Args:
        indicator_id: 指标ID
        
    Returns:
        参数声明列表
    """
    try:
        with get_db_connection() as db:
            cursor = db.cursor()
            cursor.execute("SELECT code FROM qd_indicator_codes WHERE id = %s", (indicator_id,))
            row = cursor.fetchone()
            cursor.close()
            
            if row and row['code']:
                return IndicatorParamsParser.parse_params(row['code'])
            return []
    except Exception as e:
        logger.error(f"Error getting indicator params: {e}")
        return []
