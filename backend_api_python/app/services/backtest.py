"""
Backtest Service
"""
import hashlib
import json
import math
import re
import threading
import time as _time
import traceback
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Dict, List, Any, Optional

import pandas as pd
import numpy as np

from app.data_sources import DataSourceFactory
from app.utils.logger import get_logger
from app.utils.db import get_db_connection
from app.utils.risk_guard import trailing_exit_locks_net_profit
from app.services.indicator_params import IndicatorParamsParser, IndicatorCaller

logger = get_logger(__name__)


class _KlineCache:
    """Simple in-memory K-line cache with TTL to avoid repeated external API calls."""

    def __init__(self, max_size: int = 64):
        self._store: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._max_size = max_size

    @staticmethod
    def _ttl_for_timeframe(timeframe: str) -> int:
        if timeframe in ('1m', '5m', '15m', '30m'):
            return 300   # 5 min for intraday
        return 1800      # 30 min for daily+

    def get(self, key: str) -> Optional[pd.DataFrame]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if _time.time() > entry['expires']:
                del self._store[key]
                return None
            return entry['df'].copy()

    def put(self, key: str, df: pd.DataFrame, timeframe: str):
        ttl = self._ttl_for_timeframe(timeframe)
        with self._lock:
            if len(self._store) >= self._max_size:
                oldest_key = min(self._store, key=lambda k: self._store[k]['expires'])
                del self._store[oldest_key]
            self._store[key] = {
                'df': df.copy(),
                'expires': _time.time() + ttl
            }


_kline_cache = _KlineCache()


class BacktestService:
    """Backtest Service"""
    
    # Timeframe in seconds
    TIMEFRAME_SECONDS = {
        '1m': 60, '5m': 300, '15m': 900, '30m': 1800,
        '1H': 3600, '4H': 14400, '1D': 86400, '1W': 604800
    }
    
    # Multi-timeframe backtest threshold configuration.
    # We pick the finest execution timeframe whose total candle count stays
    # within ~25k bars — that's the empirical sweet spot where:
    #   * CCXT paginated fetches stay under ~90 batches (well within budget),
    #   * the simulation loop completes in single-digit seconds,
    #   * the JSON response stays well under the frontend's 10-minute timeout.
    # If the user's date range exceeds the highest tier, we silently fall back
    # to a standard single-timeframe backtest (see `run_multi_timeframe`).
    MTF_CONFIG = {
        'max_1m_days': 15,         # 1m: 15d × 1440 = 21,600 candles
        'max_5m_days': 90,         # 5m: 90d × 288 = 25,920 candles (~86 CCXT calls)
        'max_15m_days': 240,       # 15m: 240d × 96 = 23,040 candles (~77 CCXT calls)
        'max_30m_days': 540,       # 30m: 540d × 48 = 25,920 candles (~86 CCXT calls)
        'default_exec_tf': '1m',   # Default execution timeframe
        'fallback_exec_tf': '5m',  # Fallback execution timeframe
    }

    ENGINE_VERSION = 'strategy-backtest-v1'

    def __init__(self):
        self._storage_schema_ready = False

    def _estimate_warmup_bars(
        self,
        indicator_code: str,
        indicator_params: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Estimate indicator warmup bars from declared numeric period params."""
        try:
            declared = IndicatorParamsParser.parse_params(indicator_code or "")
            merged = IndicatorParamsParser.merge_params(declared, indicator_params or {})
        except Exception:
            merged = {}

        max_period = 0
        period_name_re = re.compile(
            r"(len|length|period|window|lookback|ema|sma|rsi|adx|atr|vol|ma)$",
            re.IGNORECASE,
        )
        for key, value in (merged or {}).items():
            name = str(key or "")
            try:
                n = int(float(value))
            except Exception:
                continue
            if n <= 1 or n > 10000:
                continue
            if period_name_re.search(name) or name.endswith("_n"):
                max_period = max(max_period, n)

        if max_period <= 0:
            return 0
        return int(min(max_period + max(50, math.ceil(max_period * 0.5)), 2000))

    def _warmup_start_date(self, start_date: datetime, timeframe: str, warmup_bars: int) -> datetime:
        if warmup_bars <= 0:
            return start_date
        tf_seconds = self.TIMEFRAME_SECONDS.get(timeframe, 86400)
        return start_date - timedelta(seconds=tf_seconds * warmup_bars)

    def _slice_to_backtest_window(
        self,
        df: pd.DataFrame,
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        rs = pd.Timestamp(start_date)
        re = pd.Timestamp(end_date)
        out = df[(df.index >= rs) & (df.index <= re)].copy()
        if getattr(df, "attrs", None):
            out.attrs.update(df.attrs)
        return out

    def _slice_signals_to_window(
        self,
        signals: Dict[str, Any],
        target_index: pd.Index,
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, value in (signals or {}).items():
            if str(key).startswith("_"):
                out[key] = value
                continue
            if hasattr(value, "reindex"):
                fill = False
                try:
                    if getattr(value, "dtype", None) is not None and not pd.api.types.is_bool_dtype(value):
                        fill = 0.0
                except Exception:
                    fill = False
                out[key] = value.reindex(target_index, fill_value=fill)
            else:
                out[key] = value
        return out

    def _attach_warmup_to_result(
        self,
        result: Dict[str, Any],
        *,
        warmup_bars: int,
        warmup_start: datetime,
        requested_start: datetime,
    ) -> None:
        if warmup_bars <= 0:
            return
        ea = dict(result.get("executionAssumptions") or {})
        ea["indicatorWarmupBars"] = int(warmup_bars)
        ea["indicatorWarmupStart"] = str(warmup_start)
        ea["requestedStart"] = str(requested_start)
        result["executionAssumptions"] = ea

    def ensure_storage_schema(self) -> None:
        if self._storage_schema_ready:
            return
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS run_type VARCHAR(50) DEFAULT 'indicator'")
                cur.execute("ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS strategy_id INTEGER")
                cur.execute("ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS strategy_name VARCHAR(255) DEFAULT ''")
                cur.execute("ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS config_snapshot TEXT DEFAULT ''")
                cur.execute("ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS engine_version VARCHAR(50) DEFAULT ''")
                cur.execute("ALTER TABLE qd_backtest_runs ADD COLUMN IF NOT EXISTS code_hash VARCHAR(128) DEFAULT ''")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy_id ON qd_backtest_runs(strategy_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_backtest_runs_run_type ON qd_backtest_runs(run_type)")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS qd_backtest_trades (
                        id SERIAL PRIMARY KEY,
                        run_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL DEFAULT 1,
                        strategy_id INTEGER,
                        trade_index INTEGER DEFAULT 0,
                        trade_time VARCHAR(64) DEFAULT '',
                        trade_type VARCHAR(64) DEFAULT '',
                        side VARCHAR(32) DEFAULT '',
                        price DOUBLE PRECISION DEFAULT 0,
                        amount DOUBLE PRECISION DEFAULT 0,
                        profit DOUBLE PRECISION DEFAULT 0,
                        balance DOUBLE PRECISION DEFAULT 0,
                        reason VARCHAR(64) DEFAULT '',
                        payload_json TEXT DEFAULT '',
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_backtest_trades_run_id ON qd_backtest_trades(run_id)")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS qd_backtest_equity_points (
                        id SERIAL PRIMARY KEY,
                        run_id INTEGER NOT NULL,
                        point_index INTEGER DEFAULT 0,
                        point_time VARCHAR(64) DEFAULT '',
                        point_value DOUBLE PRECISION DEFAULT 0,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_backtest_equity_points_run_id ON qd_backtest_equity_points(run_id)")
                db.commit()
                cur.close()
            self._storage_schema_ready = True
        except Exception:
            logger.warning("Failed to ensure backtest storage schema", exc_info=True)

    def _detect_trade_side(self, trade_type: str) -> str:
        ty = str(trade_type or '').strip().lower()
        if 'long' in ty:
            return 'long'
        if 'short' in ty:
            return 'short'
        return ''
    
    @staticmethod
    def _infer_candle_path(open_: float, high: float, low: float, close: float) -> List[float]:
        """
        Infer the price path within a candle.
        
        Determines the order of price movement based on open/close relationship:
        - Bullish candle (close >= open): Open -> Low -> High -> Close (dip then rally)
        - Bearish candle (close < open): Open -> High -> Low -> Close (rally then dip)
        
        Returns:
            Price path list [price1, price2, price3, price4]
        """
        if close >= open_:
            # Bullish: dip first then rally
            return [open_, low, high, close]
        else:
            # Bearish: rally first then dip
            return [open_, high, low, close]
    
    def get_execution_timeframe(self, start_date: datetime, end_date: datetime, market: str = 'crypto') -> tuple:
        """
        Automatically select execution timeframe based on backtest date range.

        Strategy: pick the finest exec timeframe whose total candle count stays
        below ~25k bars. Longer windows degrade to coarser exec candles instead
        of failing — so users still get *some* intra-bar precision over a 6+
        month range, just not 1-minute precision.

        Args:
            start_date: Start date
            end_date: End date
            market: Market type

        Returns:
            (execution_timeframe, precision_info)
            - execution_timeframe: '1m' / '5m' / '15m' / '30m' or None
            - precision_info: Precision info dict for frontend display
        """
        days_diff = (end_date - start_date).days

        # Only crypto market supports high-precision backtest
        if market.lower() not in ['crypto', 'cryptocurrency']:
            return None, {
                'enabled': False,
                'reason': 'only_crypto',
                'message': 'High-precision backtest only supports cryptocurrency market'
            }

        # Tier table: (max_days, exec_tf, candles_per_day, precision_label).
        # Ordered finest-first; we pick the first tier whose cap covers the range.
        tiers = [
            (self.MTF_CONFIG['max_1m_days'],  '1m',  24 * 60, 'high'),
            (self.MTF_CONFIG['max_5m_days'],  '5m',  24 * 12, 'medium'),
            (self.MTF_CONFIG['max_15m_days'], '15m', 24 * 4,  'medium'),
            (self.MTF_CONFIG['max_30m_days'], '30m', 24 * 2,  'low'),
        ]

        for max_days, exec_tf, candles_per_day, precision in tiers:
            if days_diff <= max_days:
                estimated_candles = days_diff * candles_per_day
                return exec_tf, {
                    'enabled': True,
                    'timeframe': exec_tf,
                    'days': days_diff,
                    'estimated_candles': estimated_candles,
                    'precision': precision,
                    # Single, consistent message format so the UI can show the
                    # exact exec-tf picked + estimated workload at a glance.
                    'message': f'Using {exec_tf} precision backtest (~{estimated_candles:,} candles)',
                }

        # Beyond the largest tier: silently fall back to standard backtest at the
        # strategy timeframe. We mark enabled=False so run_multi_timeframe()
        # routes to self.run(); the UI surfaces this as "MTF auto-disabled,
        # using standard backtest" rather than a hard failure.
        return None, {
            'enabled': False,
            'reason': 'range_exceeds_high_precision',
            'days': days_diff,
            'max_days': self.MTF_CONFIG['max_30m_days'],
            'message': (
                f'High-precision MTF is capped at {self.MTF_CONFIG["max_30m_days"]} days '
                f'(requested {days_diff} days); falling back to standard candle backtest'
            ),
        }

    def _liquidation_loss(self, capital: Any) -> float:
        try:
            equity = max(0.0, float(capital or 0.0))
        except Exception:
            equity = 0.0
        return round(-equity, 2)

    def persist_run(
        self,
        *,
        user_id: int,
        market: str,
        symbol: str,
        timeframe: str,
        start_date_str: str,
        end_date_str: str,
        initial_capital: float,
        commission: float,
        slippage: float,
        leverage: int,
        trade_direction: str,
        strategy_config: Optional[Dict[str, Any]] = None,
        config_snapshot: Optional[Dict[str, Any]] = None,
        status: str = 'success',
        error_message: str = '',
        result: Optional[Dict[str, Any]] = None,
        indicator_id: Optional[int] = None,
        strategy_id: Optional[int] = None,
        strategy_name: str = '',
        run_type: str = 'indicator',
        code: str = '',
    ) -> Optional[int]:
        self.ensure_storage_schema()
        run_id = None
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    INSERT INTO qd_backtest_runs
                    (user_id, indicator_id, strategy_id, strategy_name, run_type, market, symbol, timeframe,
                     start_date, end_date, initial_capital, commission, slippage, leverage, trade_direction,
                     strategy_config, config_snapshot, engine_version, code_hash, status, error_message, result_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW())
                    """,
                    (
                        int(user_id or 1),
                        int(indicator_id) if indicator_id is not None else None,
                        int(strategy_id) if strategy_id is not None else None,
                        str(strategy_name or ''),
                        str(run_type or 'indicator'),
                        str(market or ''),
                        str(symbol or ''),
                        str(timeframe or ''),
                        str(start_date_str or ''),
                        str(end_date_str or ''),
                        float(initial_capital or 0),
                        float(commission or 0),
                        float(slippage or 0),
                        int(leverage or 1),
                        str(trade_direction or 'long'),
                        json.dumps(strategy_config or {}, ensure_ascii=False),
                        json.dumps(config_snapshot or {}, ensure_ascii=False),
                        self.ENGINE_VERSION,
                        hashlib.sha256(str(code or '').encode('utf-8')).hexdigest() if code else '',
                        str(status or 'success'),
                        str(error_message or ''),
                        json.dumps(result or {}, ensure_ascii=False) if result else ''
                    )
                )
                run_id = cur.lastrowid

                if run_id and status == 'success' and isinstance(result, dict):
                    for idx, trade in enumerate((result.get('trades') or []), start=1):
                        cur.execute(
                            """
                            INSERT INTO qd_backtest_trades
                            (run_id, user_id, strategy_id, trade_index, trade_time, trade_type, side, price, amount, profit, balance, reason, payload_json, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW())
                            """,
                            (
                                int(run_id),
                                int(user_id or 1),
                                int(strategy_id) if strategy_id is not None else None,
                                idx,
                                str(trade.get('time') or ''),
                                str(trade.get('type') or ''),
                                self._detect_trade_side(trade.get('type')),
                                float(trade.get('price') or 0),
                                float(trade.get('amount') or 0),
                                float(trade.get('profit') or 0),
                                float(trade.get('balance') or 0),
                                str(trade.get('reason') or trade.get('close_reason') or ''),
                                json.dumps(trade or {}, ensure_ascii=False),
                            )
                        )

                    for idx, point in enumerate((result.get('equityCurve') or []), start=1):
                        cur.execute(
                            """
                            INSERT INTO qd_backtest_equity_points
                            (run_id, point_index, point_time, point_value, created_at)
                            VALUES (?, ?, ?, ?, NOW())
                            """,
                            (
                                int(run_id),
                                idx,
                                str(point.get('time') or ''),
                                float(point.get('value') or 0),
                            )
                        )

                db.commit()
                cur.close()
        except Exception:
            logger.warning("Failed to persist backtest run", exc_info=True)
        return run_id

    def list_runs(
        self,
        *,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
        indicator_id: Optional[int] = None,
        strategy_id: Optional[int] = None,
        run_type: Optional[str] = None,
        symbol: str = '',
        market: str = '',
        timeframe: str = '',
    ) -> List[Dict[str, Any]]:
        self.ensure_storage_schema()
        where = ["user_id = ?"]
        params: List[Any] = [int(user_id or 1)]
        if indicator_id is not None:
            where.append("indicator_id = ?")
            params.append(int(indicator_id))
        if strategy_id is not None:
            where.append("strategy_id = ?")
            params.append(int(strategy_id))
        if run_type:
            where.append("run_type = ?")
            params.append(str(run_type))
        if symbol:
            where.append("symbol = ?")
            params.append(str(symbol))
        if market:
            where.append("market = ?")
            params.append(str(market))
        if timeframe:
            where.append("timeframe = ?")
            params.append(str(timeframe))

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                f"""
                SELECT id, user_id, indicator_id, strategy_id, strategy_name, run_type, market, symbol, timeframe,
                       start_date, end_date, initial_capital, commission, slippage, leverage, trade_direction,
                       strategy_config, config_snapshot, engine_version, code_hash, status, error_message,
                       result_json, created_at
                FROM qd_backtest_runs
                WHERE {" AND ".join(where)}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, int(limit), int(offset)),
            )
            rows = cur.fetchall() or []
            cur.close()

        return [self._hydrate_run_row(r, include_result=False) for r in rows]

    def get_run(self, *, user_id: int, run_id: int) -> Optional[Dict[str, Any]]:
        self.ensure_storage_schema()
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, user_id, indicator_id, strategy_id, strategy_name, run_type, market, symbol, timeframe,
                       start_date, end_date, initial_capital, commission, slippage, leverage, trade_direction,
                       strategy_config, config_snapshot, engine_version, code_hash, status, error_message,
                       result_json, created_at
                FROM qd_backtest_runs
                WHERE id = ? AND user_id = ?
                """,
                (int(run_id), int(user_id or 1)),
            )
            row = cur.fetchone()
            cur.close()
        if not row:
            return None
        return self._hydrate_run_row(row, include_result=True)

    def _hydrate_run_row(self, row: Dict[str, Any], include_result: bool = True) -> Dict[str, Any]:
        item = dict(row or {})
        try:
            item['strategy_config'] = json.loads(item.get('strategy_config') or '{}')
        except Exception:
            item['strategy_config'] = {}
        try:
            item['config_snapshot'] = json.loads(item.get('config_snapshot') or '{}')
        except Exception:
            item['config_snapshot'] = {}
        try:
            result = json.loads(item.get('result_json') or '{}')
        except Exception:
            result = {}

        item['total_return'] = result.get('totalReturn')
        item['total_return_pct'] = item['total_return']
        item['annual_return'] = result.get('annualReturn')
        item['win_rate'] = result.get('winRate')
        item['total_trades'] = result.get('totalTrades')
        ea = result.get('executionAssumptions') or {}
        pi = result.get('precision_info') or {}
        exec_cfg = (item.get('config_snapshot') or {}).get('executionConfig') or {}
        mtf_active = bool(ea.get('mtfActive') or pi.get('enabled'))
        strict_mode = ea.get('strictMode')
        if strict_mode is None:
            strict_mode = exec_cfg.get('strictMode')
        if strict_mode is None:
            timing = str(
                ea.get('signalTiming')
                or (item.get('strategy_config') or {}).get('execution', {}).get('signalTiming')
                or ''
            ).lower()
            strict_mode = timing not in ('same_bar_close', 'current_bar_close', 'bar_close', 'close')
        simulation_mode = str(
            ea.get('simulationMode') or pi.get('mode') or pi.get('precision') or ''
        ).lower()
        if strict_mode:
            summary_mode = 'strict'
        elif mtf_active or simulation_mode in ('aggressive_1m', 'mtf'):
            summary_mode = 'aggressive_1m'
        else:
            summary_mode = 'aggressive_bar'
        item['simulation_summary'] = {
            'mode': summary_mode,
            'strictMode': bool(strict_mode),
            'execTimeframe': ea.get('executionTimeframe') or pi.get('timeframe') or item.get('timeframe'),
            'mtfFallbackReason': ea.get('mtfFallbackReason') or pi.get('fallback_reason'),
        }
        if include_result:
            item['result'] = result
        item.pop('result_json', None)
        return item
    
    def run_multi_timeframe(
        self,
        indicator_code: str,
        market: str,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
        initial_capital: float = 10000.0,
        commission: float = 0.001,
        slippage: float = 0.0,
        leverage: int = 1,
        trade_direction: str = 'long',
        strategy_config: Optional[Dict[str, Any]] = None,
        enable_mtf: bool = True,
        indicator_params: Optional[Dict[str, Any]] = None,
        user_id: int = 1,
        indicator_id: Optional[int] = None,
        market_type: Optional[str] = None,
        exchange_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Multi-timeframe backtest.
        
        Uses strategy timeframe for signal generation and execution timeframe (1m/5m) 
        for precise trade simulation.
        
        Args:
            indicator_code: Indicator code
            market: Market type
            symbol: Trading symbol
            timeframe: Strategy timeframe (for signal generation)
            start_date: Start date
            end_date: End date
            initial_capital: Initial capital
            commission: Commission rate
            slippage: Slippage
            leverage: Leverage
            trade_direction: Trade direction
            strategy_config: Strategy configuration
            enable_mtf: Whether to enable multi-timeframe backtest
            
        Returns:
            Backtest result with precision info
        """
        # Get execution timeframe
        exec_tf, precision_info = self.get_execution_timeframe(start_date, end_date, market)
        cfg = strategy_config or {}
        exec_cfg = cfg.get('execution') or {}
        scale_cfg = cfg.get('scale') or {}
        signal_timing = str(exec_cfg.get('signalTiming') or 'next_bar_open').strip().lower()
        enabled_scale_keys = ['trendAdd', 'dcaAdd', 'trendReduce', 'adverseReduce']
        has_scale_rules = any(bool((scale_cfg.get(key) or {}).get('enabled')) for key in enabled_scale_keys)
        
        # Skip MTF when: disabled, not supported, or signal tf <= exec tf (no precision gain)
        signal_tf_seconds = self.TIMEFRAME_SECONDS.get(timeframe, 86400)
        exec_tf_seconds = self.TIMEFRAME_SECONDS.get(exec_tf, 300) if exec_tf else signal_tf_seconds
        same_bar_timing = signal_timing in (
            'same_bar_close', 'current_bar_close', 'bar_close', 'close',
        )
        timing_supported = signal_timing in (
            'next_bar_open', 'next_open', 'nextopen', 'next',
        ) or same_bar_timing
        skip_mtf = (
            not enable_mtf
            or not precision_info.get('enabled')
            or signal_tf_seconds <= exec_tf_seconds
            or has_scale_rules
            or not timing_supported
        )
        
        if skip_mtf:
            fallback_reason = None
            if has_scale_rules:
                fallback_reason = 'scale_rules_not_supported_in_mtf'
            elif not timing_supported:
                fallback_reason = 'signal_timing_not_supported_in_mtf'
            elif not precision_info.get('enabled'):
                # MTF was disabled by get_execution_timeframe (e.g. range exceeds
                # the 5m cap or non-crypto market). Surface its precise reason so
                # the UI can tell users "auto-fell-back to standard candles".
                fallback_reason = precision_info.get('reason') or 'mtf_unavailable'
            elif signal_tf_seconds <= exec_tf_seconds:
                fallback_reason = 'no_precision_gain'
            logger.info(
                f"Using standard backtest: tf={timeframe} "
                f"(MTF skipped, reason={fallback_reason}, signal_tf_s={signal_tf_seconds}, exec_tf_s={exec_tf_seconds})"
            )
            result = self.run(
                indicator_code=indicator_code,
                market=market,
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                initial_capital=initial_capital,
                commission=commission,
                slippage=slippage,
                leverage=leverage,
                trade_direction=trade_direction,
                strategy_config=strategy_config,
                indicator_params=indicator_params,
                user_id=user_id,
                indicator_id=indicator_id,
                market_type=market_type,
                exchange_id=exchange_id,
            )
            result['precision_info'] = precision_info or {
                'enabled': False,
                'timeframe': timeframe,
                'precision': 'standard',
                'message': 'Using standard candle backtest'
            }
            if fallback_reason:
                result['precision_info']['fallback_reason'] = fallback_reason
                if fallback_reason == 'scale_rules_not_supported_in_mtf':
                    result['precision_info']['message'] = 'Using standard backtest because scale rules are not fully supported in MTF mode'
                elif fallback_reason == 'signal_timing_not_supported_in_mtf':
                    result['precision_info']['message'] = 'Using standard backtest because this execution timing is not fully supported in MTF mode'
            ea = result.get('executionAssumptions') or {}
            ea['mtfRequested'] = bool(enable_mtf)
            ea['mtfActive'] = False
            if fallback_reason:
                ea['mtfFallbackReason'] = fallback_reason
            result['executionAssumptions'] = ea
            return result
        
        warmup_bars = self._estimate_warmup_bars(indicator_code, indicator_params)
        signal_start_date = self._warmup_start_date(start_date, timeframe, warmup_bars)

        logger.info(
            f"Multi-timeframe backtest: strategy_tf={timeframe}, exec_tf={exec_tf}, "
            f"range={start_date} ~ {end_date}, warmup_bars={warmup_bars}"
        )
        
        # 1. Fetch strategy timeframe candles (for signal generation)
        df_signal_full = self._fetch_kline_data(
            market,
            symbol,
            timeframe,
            signal_start_date,
            end_date,
            market_type=market_type,
            exchange_id=exchange_id,
        )
        if df_signal_full.empty:
            raise ValueError("No candle data available in the backtest date range")
        
        # 2. Execute indicator code to get signals
        backtest_params = {
            'leverage': leverage,
            'initial_capital': initial_capital,
            'commission': commission,
            'trade_direction': trade_direction,
            'indicator_params': indicator_params or {},
            'user_id': user_id,
            'indicator_id': indicator_id,
        }
        signals_full = self._execute_indicator(indicator_code, df_signal_full, backtest_params)
        df_signal = self._slice_to_backtest_window(df_signal_full, start_date, end_date)
        if df_signal.empty:
            raise ValueError("No candle data available in the backtest date range")
        signals = self._slice_signals_to_window(signals_full, df_signal.index)
        logger.info(f"Signals generated: {list(signals.keys()) if isinstance(signals, dict) else type(signals)}")
        
        # 3. Fetch execution timeframe candles (for precise trade simulation)
        logger.info(f"Fetching execution timeframe data: {exec_tf} for {market}:{symbol}")
        df_exec = self._fetch_kline_data(
            market,
            symbol,
            exec_tf,
            start_date,
            end_date,
            market_type=market_type,
            exchange_id=exchange_id,
        )
        logger.info(f"Execution timeframe data fetched: {len(df_exec)} candles")
        if df_exec.empty:
            logger.warning(f"Cannot fetch {exec_tf} candles, falling back to standard backtest")
            result = self.run(
                indicator_code=indicator_code,
                market=market,
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                initial_capital=initial_capital,
                commission=commission,
                slippage=slippage,
                leverage=leverage,
                trade_direction=trade_direction,
                strategy_config=strategy_config,
                indicator_params=indicator_params,
                user_id=user_id,
                indicator_id=indicator_id,
                market_type=market_type,
                exchange_id=exchange_id,
            )
            result['precision_info'] = {
                'enabled': False,
                'reason': 'data_unavailable',
                'message': f'Cannot fetch {exec_tf} data, using standard backtest'
            }
            ea = result.get('executionAssumptions') or {}
            ea['mtfRequested'] = bool(enable_mtf)
            ea['mtfActive'] = False
            ea['mtfFallbackReason'] = 'data_unavailable'
            result['executionAssumptions'] = ea
            return result
        
        logger.info(f"Data fetched: signal_candles={len(df_signal)}, exec_candles={len(df_exec)}")
        
        # 4. Use execution timeframe for precise trade simulation
        try:
            logger.info("Starting MTF trading simulation...")
            _mtf_ret = self._simulate_trading_mtf(
            df_signal=df_signal,
            df_exec=df_exec,
            signals=signals,
            initial_capital=initial_capital,
            commission=commission,
            slippage=slippage,
            leverage=leverage,
            trade_direction=trade_direction,
            strategy_config=strategy_config,
                signal_timeframe=timeframe,
                exec_timeframe=exec_tf
            )
            # Backward-compat: older signature returned 3-tuple, current returns
            # 4-tuple with `total_funding_paid` last. Tolerate both so older
            # forks of `_simulate_trading_mtf` don't break this call site.
            if len(_mtf_ret) == 4:
                equity_curve, trades, total_commission, total_funding = _mtf_ret
            else:
                equity_curve, trades, total_commission = _mtf_ret
                total_funding = 0.0
            logger.info(f"MTF simulation completed: {len(trades)} trades executed")
        except Exception as e:
            logger.error(f"MTF simulation failed: {str(e)}")
            logger.error(traceback.format_exc())
            raise
        
        # 5. Calculate metrics
        try:
            logger.info(f"Calculating metrics: equity_curve_len={len(equity_curve)}, trades_len={len(trades)}, initial_capital={initial_capital}")
            metrics = self._calculate_metrics(equity_curve, trades, initial_capital, timeframe, start_date, end_date, total_commission)
            logger.info(f"Metrics calculated successfully: {list(metrics.keys())}")
        except Exception as e:
            logger.error(f"Failed to calculate metrics: {str(e)}")
            logger.error(traceback.format_exc())
            raise
        
        # 6. Format result
        try:
            logger.info("Formatting backtest result...")
            result = self._format_result(metrics, equity_curve, trades)
            result['precision_info'] = precision_info
            result['execution_timeframe'] = exec_tf
            result['signal_candles'] = len(df_signal)
            result['execution_candles'] = len(df_exec)
            try:
                result['totalFundingPaid'] = round(float(total_funding or 0.0), 6)
            except Exception:
                result['totalFundingPaid'] = 0.0
            result['executionAssumptions'] = self._execution_assumptions(
                strategy_config,
                simulation_mode='mtf',
                signal_timeframe=timeframe,
                execution_timeframe=exec_tf,
                mtf_requested=True,
                mtf_active=True,
            )
            self._attach_warmup_to_result(
                result,
                warmup_bars=warmup_bars,
                warmup_start=signal_start_date,
                requested_start=start_date,
            )
            self._attach_buy_hold_benchmark(result, df_exec, initial_capital, symbol)
            self._attach_actual_range_to_result(result, df_signal)
            logger.info("Backtest result formatted successfully")
        except Exception as e:
            logger.error(f"Failed to format result: {str(e)}")
            logger.error(traceback.format_exc())
            raise
        
        return result
    
    def _simulate_trading_mtf(
        self,
        df_signal: pd.DataFrame,
        df_exec: pd.DataFrame,
        signals: dict,
        initial_capital: float,
        commission: float,
        slippage: float,
        leverage: int,
        trade_direction: str,
        strategy_config: Optional[Dict[str, Any]],
        signal_timeframe: str,
        exec_timeframe: str
    ) -> tuple:
        """
        Multi-timeframe trading simulation.
        
        Simulates trades candle by candle on execution timeframe, 
        using inferred candle price path to determine trigger order.
        """
        try:
            logger.info(f"Entering _simulate_trading_mtf: df_signal={len(df_signal)}, df_exec={len(df_exec)}, signals_type={type(signals)}")
        except Exception as e:
            logger.error(f"Error in _simulate_trading_mtf entry logging: {e}")
        
        equity_curve = []
        trades = []
        total_commission_paid = 0.0
        is_liquidated = False
        min_capital_to_trade = 1.0
        
        capital = initial_capital
        position = 0
        entry_price = 0.0
        position_type = None  # 'long' or 'short'
        lev = max(int(leverage or 1), 1)
        
        # Parse strategy config
        cfg = strategy_config or {}
        risk_cfg = cfg.get('risk') or {}
        stop_loss_pct = float(risk_cfg.get('stopLossPct') or 0.0)
        take_profit_pct = float(risk_cfg.get('takeProfitPct') or 0.0)
        trailing_cfg = risk_cfg.get('trailing') or {}
        trailing_enabled = bool(trailing_cfg.get('enabled'))
        trailing_pct = float(trailing_cfg.get('pct') or 0.0)
        trailing_activation_pct = float(trailing_cfg.get('activationPct') or 0.0)
        exit_owner = str(cfg.get('exitOwner') or cfg.get('exit_owner') or '').strip().lower()
        if exit_owner == 'indicator':
            stop_loss_pct = 0.0
            take_profit_pct = 0.0
            trailing_enabled = False
            trailing_pct = 0.0
            trailing_activation_pct = 0.0

        # Signal-timing mode (next_bar_open / same_bar_close / ...). Mirrors the
        # parsing done in `run_multi_timeframe`/`run`/`run_strategy_script`. It's
        # used at the tail of this function by `_annotate_signal_bar_times` to
        # align each trade with the originating signal candle on the chart.
        # Historically this was an unbound name here — any successful MTF run
        # would raise `NameError: name 'signal_timing' is not defined` at the
        # very last step, masking otherwise-correct results.
        exec_cfg = cfg.get('execution') or {}
        signal_timing = str(exec_cfg.get('signalTiming') or 'next_bar_open').strip().lower()
        same_bar_timing = signal_timing in (
            'same_bar_close', 'current_bar_close', 'bar_close', 'close',
        )
        # Non-strict (same_bar_close): fill at the signal bar's close on the exec TF.
        # Strict / next_bar_open: fill at the open of the first exec bar after the signal bar.
        signal_fill_mode = 'close' if same_bar_timing else 'open'

        # Funding rate simulation. Off by default for backward compatibility.
        # Annual rate accepts both decimal (0.10 = 10%) and percentage (10 = 10%).
        # We charge `notional * rate_per_period` from capital at every funding
        # boundary that falls within the bar's [open_ts, open_ts + tf) window.
        # Sign convention: positive rate => long pays / short receives.
        fees_cfg = cfg.get('fees') or {}
        funding_rate_annual_raw = fees_cfg.get('fundingRateAnnual', 0)
        try:
            funding_rate_annual = float(funding_rate_annual_raw or 0)
        except (TypeError, ValueError):
            funding_rate_annual = 0.0
        # Auto-detect "percent" inputs (e.g. 10 instead of 0.10).
        if abs(funding_rate_annual) > 1.5:
            funding_rate_annual = funding_rate_annual / 100.0
        try:
            funding_interval_hours = float(fees_cfg.get('fundingIntervalHours') or 8)
        except (TypeError, ValueError):
            funding_interval_hours = 8.0
        if funding_interval_hours <= 0:
            funding_interval_hours = 8.0
        funding_periods_per_year = (365.25 * 24.0) / funding_interval_hours
        funding_rate_per_period = (funding_rate_annual / funding_periods_per_year) if funding_periods_per_year > 0 else 0.0
        funding_enabled = abs(funding_rate_per_period) > 1e-12
        funding_interval_seconds = int(funding_interval_hours * 3600)
        total_funding_paid = 0.0

        # Risk percentages are the underlying's % price move directly.
        # Leverage only affects PnL magnitude and liquidation — it does NOT
        # scale trigger thresholds.
        stop_loss_pct_eff = stop_loss_pct if stop_loss_pct > 0 else 0
        take_profit_pct_eff = take_profit_pct if take_profit_pct > 0 else 0
        trailing_pct_eff = trailing_pct if trailing_pct > 0 else 0
        trailing_activation_pct_eff = trailing_activation_pct if trailing_activation_pct > 0 else 0
        
        # If trailing stop enabled but no activation threshold set, use take profit threshold
        if trailing_enabled and trailing_pct_eff > 0:
            if trailing_activation_pct_eff <= 0 and take_profit_pct_eff > 0:
                trailing_activation_pct_eff = take_profit_pct_eff
        
        # Entry percentage
        pos_cfg = cfg.get('position') or {}
        raw_entry_pct = pos_cfg.get('entryPct')
        # If entryPct is None, 0, or not provided, default to 1.0 (100%)
        if raw_entry_pct is None or raw_entry_pct == 0:
            entry_pct_cfg = 1.0
        else:
            entry_pct_cfg = float(raw_entry_pct)
            if entry_pct_cfg > 1:
                entry_pct_cfg = entry_pct_cfg / 100.0
        entry_pct_cfg = max(0.01, min(entry_pct_cfg, 1.0))  # Minimum 1% to avoid 0 position
        
        logger.info(f"Trading params: capital={capital}, leverage={lev}, entry_pct={entry_pct_cfg}, strategy_config={cfg}")
        
        highest_since_entry = None
        lowest_since_entry = None
        
        # Normalize signal format
        if not isinstance(signals, dict):
            raise ValueError("signals must be a dict")
        
        # Debug: check signal index compatibility
        signal_keys = list(signals.keys())
        logger.info(f"Signal keys: {signal_keys}")
        if signal_keys:
            first_key = signal_keys[0]
            if hasattr(signals[first_key], 'index'):
                sig_index = signals[first_key].index
                df_index = df_signal.index
                logger.info(f"Signal index len={len(sig_index)}, df_signal index len={len(df_index)}")
                if len(sig_index) > 0 and len(df_index) > 0:
                    logger.info(f"Signal index first={sig_index[0]}, df_signal index first={df_index[0]}")
                    # Check if indices match
                    if not sig_index.equals(df_index):
                        logger.warning("Signal index does NOT match df_signal index! This may cause signal lookup failures.")
        
        # Check if trade_direction is 'both' mode
        is_both_mode = str(trade_direction or 'both').lower() == 'both'
        
        if all(k in signals for k in ['open_long', 'close_long', 'open_short', 'close_short']):
            norm_signals = signals
            norm_signals['_both_mode'] = False  # Explicit 4-signal mode, not both mode
        elif all(k in signals for k in ['buy', 'sell']):
            # Ensure signals have the same index as df_signal
            buy_series = signals['buy']
            sell_series = signals['sell']
            
            # Reindex to match df_signal.index (fill missing with False)
            if not buy_series.index.equals(df_signal.index):
                logger.warning(f"Buy signal index mismatch! Signal index: {buy_series.index[:5].tolist()}, df_signal index: {df_signal.index[:5].tolist()}")
                buy_series = buy_series.reindex(df_signal.index, fill_value=False)
            if not sell_series.index.equals(df_signal.index):
                logger.warning(f"Sell signal index mismatch! Signal index: {sell_series.index[:5].tolist()}, df_signal index: {df_signal.index[:5].tolist()}")
                sell_series = sell_series.reindex(df_signal.index, fill_value=False)
            
            buy = buy_series.fillna(False).astype(bool)
            sell = sell_series.fillna(False).astype(bool)
            
            # Debug: log signal statistics
            buy_count = buy.sum()
            sell_count = sell.sum()
            logger.info(f"Signal statistics: buy={buy_count}, sell={sell_count}, total_candles={len(df_signal)}")
            
            td = str(trade_direction or 'both').lower()
            logger.info(f"Trade direction: {td} (original: {trade_direction})")
            if td == 'long':
                norm_signals = {
                    'open_long': buy, 'close_long': sell,
                    'open_short': pd.Series([False] * len(df_signal), index=df_signal.index, dtype=bool),
                    'close_short': pd.Series([False] * len(df_signal), index=df_signal.index, dtype=bool),
                }
            elif td == 'short':
                norm_signals = {
                    'open_long': pd.Series([False] * len(df_signal), index=df_signal.index, dtype=bool),
                    'close_long': pd.Series([False] * len(df_signal), index=df_signal.index, dtype=bool),
                    'open_short': sell, 'close_short': buy,
                }
            else:
                # Both mode: buy signal triggers long entry (close short if any, then open long)
                # sell signal triggers short entry (close long if any, then open short)
                # We use special signal types 'enter_long' and 'enter_short' to indicate
                # that the signal should auto-close opposing position before opening
                norm_signals = {
                    'open_long': buy, 'close_long': pd.Series([False] * len(df_signal), index=df_signal.index, dtype=bool),
                    'open_short': sell, 'close_short': pd.Series([False] * len(df_signal), index=df_signal.index, dtype=bool),
                    '_both_mode': True  # Flag to indicate both mode for special handling
                }
        else:
            raise ValueError("Invalid signal format")
        
        logger.info("Signal normalization completed, starting signal queue building...")
        
        # Map signals to execution timeframe
        # Strategy timeframe seconds (e.g. 1H=3600, 1D=86400)
        signal_tf_seconds = self.TIMEFRAME_SECONDS.get(signal_timeframe, 3600)
        exec_tf_seconds = self.TIMEFRAME_SECONDS.get(exec_timeframe, 60)
        
        logger.info(f"Signal timeframe: {signal_timeframe} ({signal_tf_seconds}s), Exec timeframe: {exec_timeframe} ({exec_tf_seconds}s)")
        
        # Preprocessing: create signal queue sorted by effective time.
        # next_bar_open → first exec-bar open after signal bar closes.
        # same_bar_close → last exec-bar close within the signal bar.
        logger.info(
            f"Initializing signal queue (fill_mode={signal_fill_mode}, signal_timing={signal_timing})..."
        )
        signal_queue = []  # [(sig_bar_end, signal_type, signal_bar_time, fill_mode), ...]
        
        # Debug: check signal values
        debug_signal_counts = {'open_long': 0, 'close_long': 0, 'open_short': 0, 'close_short': 0}
        
        # Verify all norm_signals have matching index
        for sig_type in ['open_long', 'close_long', 'open_short', 'close_short']:
            if not norm_signals[sig_type].index.equals(df_signal.index):
                logger.error(f"Critical: {sig_type} signal index does not match df_signal.index!")
                logger.error(f"  Signal index: {norm_signals[sig_type].index[:5].tolist()}")
                logger.error(f"  df_signal index: {df_signal.index[:5].tolist()}")
                # Reindex to fix
                norm_signals[sig_type] = norm_signals[sig_type].reindex(df_signal.index, fill_value=False)
                logger.warning(f"  Fixed by reindexing {sig_type}")
        
        for sig_time in df_signal.index:
            # Signal candle end time = start time + period
            sig_end = sig_time + timedelta(seconds=signal_tf_seconds)
            
            # Check if this signal candle has signals
            # All signals should now have matching index, so we can safely use .loc[]
            try:
                ol = bool(norm_signals['open_long'].loc[sig_time])
                cl = bool(norm_signals['close_long'].loc[sig_time])
                os = bool(norm_signals['open_short'].loc[sig_time])
                cs = bool(norm_signals['close_short'].loc[sig_time])
            except (KeyError, IndexError) as e:
                logger.warning(f"Error accessing signal at {sig_time}: {e}, signal index: {norm_signals['open_long'].index[:5].tolist()}, df_signal index: {df_signal.index[:5].tolist()}")
                continue
            except Exception as e:
                logger.warning(f"Unexpected error accessing signal at {sig_time}: {e}")
                continue
            
            if ol:
                signal_queue.append((sig_end, 'open_long', sig_time, signal_fill_mode))
                debug_signal_counts['open_long'] += 1
            if cl:
                signal_queue.append((sig_end, 'close_long', sig_time, signal_fill_mode))
                debug_signal_counts['close_long'] += 1
            if os:
                signal_queue.append((sig_end, 'open_short', sig_time, signal_fill_mode))
                debug_signal_counts['open_short'] += 1
            if cs:
                signal_queue.append((sig_end, 'close_short', sig_time, signal_fill_mode))
                debug_signal_counts['close_short'] += 1
        
        logger.info(f"Debug signal counts from queue building: {debug_signal_counts}")
        
        # If no signals found, log detailed diagnostic info
        if len(signal_queue) == 0:
            logger.warning("No signals found in signal queue! Diagnostic info:")
            logger.warning(f"  df_signal length: {len(df_signal)}")
            logger.warning(f"  df_signal index range: {df_signal.index[0]} to {df_signal.index[-1]}")
            for sig_type in ['open_long', 'close_long', 'open_short', 'close_short']:
                sig_series = norm_signals[sig_type]
                true_count = sig_series.sum()
                logger.warning(f"  {sig_type}: {true_count} True values out of {len(sig_series)}")
                if true_count > 0:
                    true_indices = sig_series[sig_series].index.tolist()[:5]
                    logger.warning(f"    First few True indices: {true_indices}")
            # Check if signals might be in wrong format
            if 'buy' in signals or 'sell' in signals:
                logger.warning("  Original signals had 'buy'/'sell' keys - check if conversion was correct")
        
        # Sort by effective time
        signal_queue.sort(key=lambda x: x[0])
        signal_queue_idx = 0  # Current signal queue pointer
        
        logger.info(f"Signal queue built: total {len(signal_queue)} signals")
        if signal_queue:
            logger.info(
                f"First signal: {signal_queue[0][1]} @ {signal_queue[0][0]} "
                f"(from {signal_queue[0][2]}, fill={signal_queue[0][3]})"
            )
            logger.info(
                f"Last signal: {signal_queue[-1][1]} @ {signal_queue[-1][0]} "
                f"(from {signal_queue[-1][2]}, fill={signal_queue[-1][3]})"
            )
        else:
            logger.error("Signal queue is empty! Backtest will fail. Check indicator code to ensure it generates buy/sell signals.")
        
        # Count signals by type
        signal_counts = {}
        for _, sig_type, _, _ in signal_queue:
            signal_counts[sig_type] = signal_counts.get(sig_type, 0) + 1
        logger.info(f"Signal counts: {signal_counts}")
        
        # Log first few signal details for debugging
        if signal_queue:
            logger.info(f"First 3 signals details:")
            for idx, (sig_time, sig_type, sig_bar_time, fill_mode) in enumerate(signal_queue[:3]):
                logger.info(
                    f"  Signal {idx+1}: {sig_type} @ effective_time={sig_time}, "
                    f"from_bar={sig_bar_time}, fill={fill_mode}"
                )
        
        # Log execution data range
        if len(df_exec) > 0:
            exec_start = df_exec.index[0]
            exec_end = df_exec.index[-1]
            logger.info(f"Exec data range: {exec_start} ~ {exec_end}")
            # Check first few candles for data validity
            first_row = df_exec.iloc[0]
            logger.info(f"First exec candle: open={first_row['open']}, high={first_row['high']}, low={first_row['low']}, close={first_row['close']}")
        
        # Current pending signal to execute
        pending_signal = None  # ('open_long', 'close_long', 'open_short', 'close_short')
        pending_signal_time = None  # Signal effective time
        pending_fill_mode = 'open'  # 'open' | 'close'
        executed_trades_count = 0  # Debug counter
        
        # Progress logging for large datasets
        total_exec_candles = len(df_exec)
        progress_log_interval = max(1000, total_exec_candles // 10)  # Log every 10% or every 1000 candles
        
        logger.info(f"Starting execution loop: {total_exec_candles} candles to process, {len(signal_queue)} signals in queue")
        
        # Funding cursor: epoch seconds of the next due funding payment.
        # Initialised lazily on the first bar so we don't need start_date here.
        next_funding_ts = None
        # NOTE: `df_exec.itertuples()` is ~5-10x faster than `iterrows()` on
        # large frames (50k+ rows in 5m / 90-day backtests) because it skips
        # building a fresh `pd.Series` per row. `row.Index` is the timestamp
        # and `row.open/.high/.low/.close` map to the OHLC columns.
        for i, row in enumerate(df_exec.itertuples(index=True)):
            timestamp = row.Index
            # Progress logging
            if i > 0 and i % progress_log_interval == 0:
                progress_pct = (i / total_exec_candles) * 100
                logger.info(f"Execution progress: {i}/{total_exec_candles} ({progress_pct:.1f}%), trades={executed_trades_count}, position={position}")
            if is_liquidated:
                break

            # Funding fee accrual — runs once per bar, BEFORE signal/SL processing
            # so trades that close on this bar still pay one period of carry.
            # We charge for every funding boundary that falls within
            # [bar_open_ts, bar_open_ts + exec_tf). Multiple boundaries per bar
            # are rare (only when exec_tf > funding interval) but handled.
            if funding_enabled and position != 0:
                try:
                    bar_ts = int(timestamp.timestamp())
                except Exception:
                    bar_ts = None
                if bar_ts is not None:
                    if next_funding_ts is None:
                        next_funding_ts = ((bar_ts // funding_interval_seconds) + 1) * funding_interval_seconds
                    bar_end_ts = bar_ts + exec_tf_seconds
                    while next_funding_ts < bar_end_ts:
                        if position != 0:
                            # Notional uses current entry-price reference; this is a
                            # conservative approximation for cross/isolated margin.
                            notional = abs(position) * (entry_price if entry_price else 0.0)
                            if notional > 0:
                                # Long pays positive funding, short receives it.
                                sign = 1.0 if position > 0 else -1.0
                                funding_charge = notional * funding_rate_per_period * sign
                                capital -= funding_charge
                                total_funding_paid += funding_charge
                                if capital <= 0:
                                    capital = 0
                                    is_liquidated = True
                                    break
                        next_funding_ts += funding_interval_seconds
                if is_liquidated:
                    break

            # bar_time: floor of execution timestamp to signal timeframe.
            # This is the chart-bar that the front-end displays and is used to
            # anchor buy/sell overlays — prevents sub-bar offset when exec_tf
            # is finer than signal_tf (e.g. 1m execution on a 1h chart).
            try:
                bar_time_str = timestamp.floor(f'{signal_tf_seconds}s').strftime('%Y-%m-%d %H:%M')
            except Exception:
                # Fallback: round down manually via epoch seconds
                try:
                    epoch = int(timestamp.timestamp())
                    floored = (epoch // signal_tf_seconds) * signal_tf_seconds
                    bar_time_str = datetime.utcfromtimestamp(floored).strftime('%Y-%m-%d %H:%M')
                except Exception:
                    bar_time_str = timestamp.strftime('%Y-%m-%d %H:%M')

            if position == 0 and capital < min_capital_to_trade:
                is_liquidated = True
                capital = 0
                equity_curve.append({'time': timestamp.strftime('%Y-%m-%d %H:%M'), 'value': 0})
                continue
            
            open_ = row.open
            high = row.high
            low = row.low
            close = row.close

            # Use inferred candle price path to determine trigger order
            price_path = self._infer_candle_path(open_, high, low, close)
            
            # Check if new signal becomes effective
            try:
                bar_ts_for_sig = int(timestamp.timestamp())
            except Exception:
                bar_ts_for_sig = None
            bar_end_ts_for_sig = (
                bar_ts_for_sig + exec_tf_seconds if bar_ts_for_sig is not None else None
            )

            while signal_queue_idx < len(signal_queue):
                sig_effective_time, sig_type, sig_bar_time, fill_mode = signal_queue[signal_queue_idx]
                try:
                    sig_eff_ts = int(pd.Timestamp(sig_effective_time).timestamp())
                except Exception:
                    sig_eff_ts = None
                
                # Debug: log first few signal checks
                if i < 10 and signal_queue_idx < len(signal_queue):
                    logger.debug(f"[i={i}] Checking signal #{signal_queue_idx}: {sig_type} @ {sig_effective_time}, exec_time={timestamp}, position={position}")
                
                if fill_mode == 'close':
                    # Last exec candle that completes the signal bar: bar_end >= sig_bar_end > bar_open
                    ready = (
                        bar_end_ts_for_sig is not None
                        and bar_ts_for_sig is not None
                        and sig_eff_ts is not None
                        and bar_end_ts_for_sig >= sig_eff_ts
                        and bar_ts_for_sig < sig_eff_ts
                    )
                else:
                    ready = timestamp >= sig_effective_time

                if ready:
                    # Check if signal can execute (based on current position)
                    # In both mode, open_long can execute even with short position (will auto-close first)
                    # Similarly, open_short can execute even with long position
                    can_execute = False
                    both_mode_active = norm_signals.get('_both_mode', False)
                    
                    if sig_type == 'open_long':
                        if position == 0:
                            can_execute = True
                        elif both_mode_active and position < 0:
                            # Both mode: have short position, will close short then open long
                            can_execute = True
                    elif sig_type == 'close_long' and position > 0:
                        can_execute = True
                    elif sig_type == 'open_short':
                        if position == 0:
                            can_execute = True
                        elif both_mode_active and position > 0:
                            # Both mode: have long position, will close long then open short
                            can_execute = True
                    elif sig_type == 'close_short' and position < 0:
                        can_execute = True
                    
                    if can_execute:
                        pending_signal = sig_type
                        pending_signal_time = sig_effective_time
                        pending_fill_mode = fill_mode
                        signal_queue_idx += 1
                        if executed_trades_count < 3:
                            logger.info(
                                f"Signal ready: {sig_type} @ {timestamp} "
                                f"(effective_time={sig_effective_time}, fill={fill_mode})"
                            )
                        break
                    else:
                        signal_queue_idx += 1
                        continue
                else:
                    # Not yet at signal effective time
                    break
            
            # Check trigger conditions along price path
            for path_price in price_path:
                if is_liquidated:
                    break
                
                # 1. Check stop-loss/take-profit/trailing stop (highest priority)
                if position != 0 and position_type in ['long', 'short']:
                    triggered = False
                    
                    if position_type == 'long' and position > 0:
                        if highest_since_entry is None:
                            highest_since_entry = entry_price
                        highest_since_entry = max(highest_since_entry, path_price)
                        
                        # Stop loss
                        if stop_loss_pct_eff > 0:
                            sl_price = entry_price * (1 - stop_loss_pct_eff)
                            if path_price <= sl_price:
                                exec_price = sl_price * (1 - slippage)
                                commission_fee = position * exec_price * commission
                                profit = (exec_price - entry_price) * position - commission_fee
                                capital += profit
                                if capital < 0:
                                    capital = 0
                                    is_liquidated = True
                                total_commission_paid += commission_fee
                                trades.append({
                                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                    'bar_time': bar_time_str,
                                    'type': 'close_long_stop',
                                    'price': round(exec_price, 4),
                                    'amount': round(position, 4),
                                    'profit': round(profit, 2),
                                    'balance': round(max(0, capital), 2)
                                })
                                position = 0
                                position_type = None
                                highest_since_entry = None
                                lowest_since_entry = None
                                triggered = True
                        
                        # Trailing stop
                        if not triggered and trailing_enabled and trailing_pct_eff > 0:
                            trail_active = True
                            if trailing_activation_pct_eff > 0:
                                trail_active = highest_since_entry >= entry_price * (1 + trailing_activation_pct_eff)
                            if trail_active:
                                tr_price = highest_since_entry * (1 - trailing_pct_eff)
                                exec_price = tr_price * (1 - slippage)
                                if path_price <= tr_price and trailing_exit_locks_net_profit(
                                    "long",
                                    entry_price=entry_price,
                                    exit_price=exec_price,
                                    fee_rate=commission,
                                ):
                                    commission_fee = position * exec_price * commission
                                    profit = (exec_price - entry_price) * position - commission_fee
                                    capital += profit
                                    total_commission_paid += commission_fee
                                    trades.append({
                                        'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                        'bar_time': bar_time_str,
                                        'type': 'close_long_trailing',
                                        'price': round(exec_price, 4),
                                        'amount': round(position, 4),
                                        'profit': round(profit, 2),
                                        'balance': round(max(0, capital), 2)
                                    })
                                    position = 0
                                    position_type = None
                                    highest_since_entry = None
                                    lowest_since_entry = None
                                    triggered = True
                        
                        # Fixed take profit (disabled when trailing stop is enabled)
                        if not triggered and not trailing_enabled and take_profit_pct_eff > 0:
                            tp_price = entry_price * (1 + take_profit_pct_eff)
                            if path_price >= tp_price:
                                exec_price = tp_price * (1 - slippage)
                                commission_fee = position * exec_price * commission
                                profit = (exec_price - entry_price) * position - commission_fee
                                capital += profit
                                total_commission_paid += commission_fee
                                trades.append({
                                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                    'bar_time': bar_time_str,
                                    'type': 'close_long_profit',
                                    'price': round(exec_price, 4),
                                    'amount': round(position, 4),
                                    'profit': round(profit, 2),
                                    'balance': round(max(0, capital), 2)
                                })
                                position = 0
                                position_type = None
                                highest_since_entry = None
                                lowest_since_entry = None
                                triggered = True
                    
                    elif position_type == 'short' and position < 0:
                        shares = abs(position)
                        if lowest_since_entry is None:
                            lowest_since_entry = entry_price
                        lowest_since_entry = min(lowest_since_entry, path_price)
                        
                        # Stop loss
                        if stop_loss_pct_eff > 0:
                            sl_price = entry_price * (1 + stop_loss_pct_eff)
                            if path_price >= sl_price:
                                exec_price = sl_price * (1 + slippage)
                                commission_fee = shares * exec_price * commission
                                profit = (entry_price - exec_price) * shares - commission_fee
                                if capital + profit <= 0:
                                    liquidation_loss = self._liquidation_loss(capital)
                                    capital = 0
                                    is_liquidated = True
                                    trades.append({
                                        'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                        'bar_time': bar_time_str,
                                        'type': 'liquidation',
                                        'price': round(exec_price, 4),
                                        'amount': round(shares, 4),
                                        'profit': liquidation_loss,
                                        'balance': 0
                                    })
                                else:
                                    capital += profit
                                    total_commission_paid += commission_fee
                                    trades.append({
                                        'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                        'bar_time': bar_time_str,
                                        'type': 'close_short_stop',
                                        'price': round(exec_price, 4),
                                        'amount': round(shares, 4),
                                        'profit': round(profit, 2),
                                        'balance': round(max(0, capital), 2)
                                    })
                                position = 0
                                position_type = None
                                highest_since_entry = None
                                lowest_since_entry = None
                                triggered = True
                        
                        # Trailing stop
                        if not triggered and trailing_enabled and trailing_pct_eff > 0:
                            trail_active = True
                            if trailing_activation_pct_eff > 0:
                                trail_active = lowest_since_entry <= entry_price * (1 - trailing_activation_pct_eff)
                            if trail_active:
                                tr_price = lowest_since_entry * (1 + trailing_pct_eff)
                                exec_price = tr_price * (1 + slippage)
                                if path_price >= tr_price and trailing_exit_locks_net_profit(
                                    "short",
                                    entry_price=entry_price,
                                    exit_price=exec_price,
                                    fee_rate=commission,
                                ):
                                    commission_fee = shares * exec_price * commission
                                    profit = (entry_price - exec_price) * shares - commission_fee
                                    if capital + profit <= 0:
                                        liquidation_loss = self._liquidation_loss(capital)
                                        capital = 0
                                        is_liquidated = True
                                        trades.append({
                                            'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                            'bar_time': bar_time_str,
                                            'type': 'liquidation',
                                            'price': round(exec_price, 4),
                                            'amount': round(shares, 4),
                                            'profit': liquidation_loss,
                                            'balance': 0
                                        })
                                    else:
                                        capital += profit
                                        total_commission_paid += commission_fee
                                        trades.append({
                                            'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                            'bar_time': bar_time_str,
                                            'type': 'close_short_trailing',
                                            'price': round(exec_price, 4),
                                            'amount': round(shares, 4),
                                            'profit': round(profit, 2),
                                            'balance': round(max(0, capital), 2)
                                        })
                                    position = 0
                                    position_type = None
                                    highest_since_entry = None
                                    lowest_since_entry = None
                                    triggered = True
                        
                        # Fixed take profit
                        if not triggered and not trailing_enabled and take_profit_pct_eff > 0:
                            tp_price = entry_price * (1 - take_profit_pct_eff)
                            if path_price <= tp_price:
                                exec_price = tp_price * (1 + slippage)
                                commission_fee = shares * exec_price * commission
                                profit = (entry_price - exec_price) * shares - commission_fee
                                capital += profit
                                total_commission_paid += commission_fee
                                trades.append({
                                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                    'bar_time': bar_time_str,
                                    'type': 'close_short_profit',
                                    'price': round(exec_price, 4),
                                    'amount': round(shares, 4),
                                    'profit': round(profit, 2),
                                    'balance': round(max(0, capital), 2)
                                })
                                position = 0
                                position_type = None
                                highest_since_entry = None
                                lowest_since_entry = None
                                triggered = True
                    
                    if triggered:
                        pending_signal = None
                        continue
                
                # 2. Execute pending signal (next-bar open or same-bar close)
                if pending_signal:
                    fill_now = (
                        (pending_fill_mode == 'open' and path_price == open_)
                        or (pending_fill_mode == 'close' and path_price == close)
                    )
                    if not fill_now:
                        continue
                    ref_px = close if pending_fill_mode == 'close' else open_
                    both_mode_active = norm_signals.get('_both_mode', False)
                    if executed_trades_count < 10:
                        logger.info(
                            f"Executing pending signal: {pending_signal} @ {timestamp}, "
                            f"fill={pending_fill_mode}, ref_px={ref_px}, position={position}"
                        )
                    
                    # open_long: In both mode, first close short if any, then open long
                    if pending_signal == 'open_long' and (position == 0 or (both_mode_active and position < 0)):
                        exec_price = ref_px * (1 + slippage)
                        
                        # If in both mode and have short position, close it first
                        if both_mode_active and position < 0:
                            shares_to_close = abs(position)
                            close_price = ref_px * (1 + slippage)
                            close_commission = shares_to_close * close_price * commission
                            close_profit = (entry_price - close_price) * shares_to_close - close_commission
                            capital += close_profit
                            if capital < 0:
                                capital = 0
                            total_commission_paid += close_commission
                            trades.append({
                                'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                'bar_time': bar_time_str,
                                'type': 'close_short',
                                'price': round(close_price, 4),
                                'amount': round(shares_to_close, 4),
                                'profit': round(close_profit, 2),
                                'balance': round(max(0, capital), 2)
                            })
                            position = 0
                            position_type = None
                            executed_trades_count += 1
                            if executed_trades_count <= 10:
                                logger.info(f"Trade #{executed_trades_count}: close_short (before open_long) @ {timestamp}, price={close_price:.4f}, profit={close_profit:.2f}")
                            if capital < min_capital_to_trade:
                                is_liquidated = True
                                capital = 0
                                pending_signal = None
                                continue
                        
                        # Now open long
                        use_capital = capital * entry_pct_cfg
                        if exec_price > 0:
                            shares = (use_capital * lev) / exec_price
                        else:
                            logger.warning(f"Invalid exec_price={exec_price} at {timestamp}, skipping open_long")
                            pending_signal = None
                            continue
                        commission_fee = shares * exec_price * commission
                        capital -= commission_fee
                        total_commission_paid += commission_fee
                        position = shares
                        entry_price = exec_price
                        position_type = 'long'
                        highest_since_entry = exec_price
                        lowest_since_entry = exec_price
                        trades.append({
                            'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                            'bar_time': bar_time_str,
                            'type': 'open_long',
                            'price': round(exec_price, 4),
                            'amount': round(shares, 4),
                            'profit': 0,
                            'balance': round(max(0, capital), 2)
                        })
                        executed_trades_count += 1
                        if executed_trades_count <= 10:
                            logger.info(f"Trade #{executed_trades_count}: open_long @ {timestamp}, price={exec_price:.4f}, shares={shares:.4f}")
                        pending_signal = None
                    
                    elif pending_signal == 'close_long' and position > 0:
                        exec_price = ref_px * (1 - slippage)
                        commission_fee = position * exec_price * commission
                        profit = (exec_price - entry_price) * position - commission_fee
                        capital += profit
                        if capital < 0:
                            capital = 0
                        total_commission_paid += commission_fee
                        trades.append({
                            'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                            'bar_time': bar_time_str,
                            'type': 'close_long',
                            'price': round(exec_price, 4),
                            'amount': round(position, 4),
                            'profit': round(profit, 2),
                            'balance': round(max(0, capital), 2)
                        })
                        position = 0
                        position_type = None
                        highest_since_entry = None
                        lowest_since_entry = None
                        pending_signal = None
                        if capital < min_capital_to_trade:
                            is_liquidated = True
                            capital = 0
                    
                    # open_short: In both mode, first close long if any, then open short
                    elif pending_signal == 'open_short' and (position == 0 or (both_mode_active and position > 0)):
                        exec_price = ref_px * (1 - slippage)
                        
                        # If in both mode and have long position, close it first
                        if both_mode_active and position > 0:
                            close_price = ref_px * (1 - slippage)
                            close_commission = position * close_price * commission
                            close_profit = (close_price - entry_price) * position - close_commission
                            capital += close_profit
                            if capital < 0:
                                capital = 0
                            total_commission_paid += close_commission
                            trades.append({
                                'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                'bar_time': bar_time_str,
                                'type': 'close_long',
                                'price': round(close_price, 4),
                                'amount': round(position, 4),
                                'profit': round(close_profit, 2),
                                'balance': round(max(0, capital), 2)
                            })
                            position = 0
                            position_type = None
                            executed_trades_count += 1
                            if executed_trades_count <= 10:
                                logger.info(f"Trade #{executed_trades_count}: close_long (before open_short) @ {timestamp}, price={close_price:.4f}, profit={close_profit:.2f}")
                            if capital < min_capital_to_trade:
                                is_liquidated = True
                                capital = 0
                                pending_signal = None
                                continue
                        
                        # Now open short
                        use_capital = capital * entry_pct_cfg
                        if exec_price > 0:
                            shares = (use_capital * lev) / exec_price
                        else:
                            logger.warning(f"Invalid exec_price={exec_price} at {timestamp}, skipping open_short")
                            pending_signal = None
                            continue
                        commission_fee = shares * exec_price * commission
                        capital -= commission_fee
                        total_commission_paid += commission_fee
                        position = -shares
                        entry_price = exec_price
                        position_type = 'short'
                        highest_since_entry = exec_price
                        lowest_since_entry = exec_price
                        trades.append({
                            'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                            'bar_time': bar_time_str,
                            'type': 'open_short',
                            'price': round(exec_price, 4),
                            'amount': round(shares, 4),
                            'profit': 0,
                            'balance': round(max(0, capital), 2)
                        })
                        executed_trades_count += 1
                        if executed_trades_count <= 10:
                            logger.info(f"Trade #{executed_trades_count}: open_short @ {timestamp}, price={exec_price:.4f}, shares={shares:.4f}")
                        pending_signal = None
                    
                    elif pending_signal == 'close_short' and position < 0:
                        shares = abs(position)
                        exec_price = ref_px * (1 + slippage)
                        commission_fee = shares * exec_price * commission
                        profit = (entry_price - exec_price) * shares - commission_fee
                        capital += profit
                        if capital < 0:
                            capital = 0
                        total_commission_paid += commission_fee
                        trades.append({
                            'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                            'bar_time': bar_time_str,
                            'type': 'close_short',
                            'price': round(exec_price, 4),
                            'amount': round(shares, 4),
                            'profit': round(profit, 2),
                            'balance': round(max(0, capital), 2)
                        })
                        position = 0
                        position_type = None
                        highest_since_entry = None
                        lowest_since_entry = None
                        pending_signal = None
                        if capital < min_capital_to_trade:
                            is_liquidated = True
                            capital = 0
            
            # Calculate current equity
            if position > 0:
                unrealized = (close - entry_price) * position
                current_equity = capital + unrealized
            elif position < 0:
                shares = abs(position)
                unrealized = (entry_price - close) * shares
                current_equity = capital + unrealized
            else:
                current_equity = capital
            
            equity_curve.append({
                'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                'value': round(max(0, current_equity), 2)
            })
        
        # Summary log
        logger.info(f"MTF simulation complete: executed_trades={executed_trades_count}, total_trades_recorded={len(trades)}, final_capital={capital:.2f}, final_position={position}")
        if len(trades) == 0:
            if len(signal_queue) == 0:
                logger.error(f"No trades executed because signal queue is empty! This usually means:")
                logger.error("  1. Indicator code did not generate any buy/sell signals")
                logger.error("  2. Signal index mismatch between indicator output and df_signal")
                logger.error("  3. All signal values are False")
                raise ValueError("No signals generated by indicator code. Please check your indicator code to ensure it sets df['buy'] and/or df['sell'] columns with boolean values.")
            else:
                logger.error(f"No trades executed despite {len(signal_queue)} signals in queue. signal_queue_idx={signal_queue_idx}")
                logger.error(f"  Signal queue processed: {signal_queue_idx}/{len(signal_queue)}")
                logger.error(f"  Final position: {position}, Final capital: {capital:.2f}")
                logger.error("  This may indicate:")
                logger.error("    1. Signal timing issues (signal effective time doesn't match execution timeframe)")
                logger.error("    2. Position state conflicts (signals skipped due to position state)")
                logger.error("    3. Capital insufficient for trading")
                logger.error(f"  First few signals: {signal_queue[:min(5, len(signal_queue))]}")
                logger.error(f"  Exec data range: {df_exec.index[0]} to {df_exec.index[-1]}")
                raise ValueError(f"No trades executed despite {len(signal_queue)} signals. Check signal timing and position state logic.")

        self._annotate_signal_bar_times(trades, signal_tf_seconds, signal_timing)

        return equity_curve, trades, total_commission_paid, total_funding_paid

    def run_strategy_snapshot(
        self,
        snapshot: Dict[str, Any],
        start_date: datetime,
        end_date: datetime,
    ) -> Dict[str, Any]:
        if not snapshot:
            raise ValueError("strategy snapshot is required")

        code = snapshot.get('code') or ''
        market = snapshot.get('market') or 'Crypto'
        symbol = snapshot.get('symbol') or ''
        timeframe = snapshot.get('timeframe') or '1D'
        initial_capital = float(snapshot.get('initial_capital') or 10000)
        commission = float(snapshot.get('commission') or 0)
        slippage = float(snapshot.get('slippage') or 0)
        leverage = int(snapshot.get('leverage') or 1)
        trade_direction = str(snapshot.get('trade_direction') or 'long')
        strategy_config = snapshot.get('strategy_config') or {}
        indicator_params = snapshot.get('indicator_params') or {}
        indicator_id = snapshot.get('indicator_id')
        user_id = int(snapshot.get('user_id') or 1)
        run_type = str(snapshot.get('run_type') or 'strategy_indicator')
        config_snapshot = snapshot.get('config_snapshot') if isinstance(snapshot.get('config_snapshot'), dict) else {}
        market_config = config_snapshot.get('marketConfig') if isinstance(config_snapshot.get('marketConfig'), dict) else {}
        market_type = snapshot.get('market_type') or market_config.get('marketType')
        exchange_id = snapshot.get('exchange_id') or market_config.get('exchangeId')

        if run_type == 'strategy_script':
            return self._run_script_strategy(
                code=code,
                market=market,
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                initial_capital=initial_capital,
                commission=commission,
                slippage=slippage,
                leverage=leverage,
                trade_direction=trade_direction,
                strategy_config=strategy_config,
                market_type=market_type,
                exchange_id=exchange_id,
            )

        strict_mode = bool(snapshot.get('strict_mode', True))
        return self.run_aligned(
            strict_mode=strict_mode,
            indicator_code=code,
            market=market,
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            commission=commission,
            slippage=slippage,
            leverage=leverage,
            trade_direction=trade_direction,
            strategy_config=strategy_config,
            indicator_params=indicator_params,
            user_id=user_id,
            indicator_id=indicator_id,
            market_type=market_type,
            exchange_id=exchange_id,
        )

    def _run_script_strategy(
        self,
        *,
        code: str,
        market: str,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
        initial_capital: float,
        commission: float,
        slippage: float,
        leverage: int,
        trade_direction: str,
        strategy_config: Optional[Dict[str, Any]] = None,
        market_type: Optional[str] = None,
        exchange_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        df = self._fetch_kline_data(
            market,
            symbol,
            timeframe,
            start_date,
            end_date,
            market_type=market_type,
            exchange_id=exchange_id,
        )
        if df.empty:
            raise ValueError("No candle data available in the backtest date range")

        signals = self._execute_script_strategy(code, df, {
            'initial_capital': initial_capital,
            'leverage': leverage,
            'trade_direction': trade_direction,
            'strategy_config': strategy_config or {},
        })
        script_logs = signals.pop('logs', [])
        equity_curve, trades, total_commission = self._simulate_trading(
            df, signals, initial_capital, commission, slippage, leverage, trade_direction, strategy_config
        )
        metrics = self._calculate_metrics(equity_curve, trades, initial_capital, timeframe, start_date, end_date, total_commission)
        result = self._format_result(metrics, equity_curve, trades)
        result['logs'] = script_logs
        result['precision_info'] = {
            'enabled': False,
            'timeframe': timeframe,
            'precision': 'standard',
            'message': 'Using standard strategy script backtest'
        }
        ea = self._execution_assumptions(
            strategy_config,
            simulation_mode='script_standard',
            signal_timeframe=timeframe,
            commission=commission,
            slippage=slippage,
        )
        ea['scriptBacktest'] = True
        ea['strictMode'] = False
        ea['simulationMode'] = 'script_standard'
        ea['fillRule'] = 'next_bar_open'
        if market_type:
            ea['marketType'] = market_type
        if exchange_id:
            ea['exchangeId'] = exchange_id
        result['executionAssumptions'] = ea
        self._attach_buy_hold_benchmark(result, df, initial_capital, symbol)
        self._attach_actual_range_to_result(result, df)
        return result
    
    def run_code_strategy(
        self,
        code: str,
        symbol: str,
        timeframe: str,
        limit: int = 1000
    ) -> Dict[str, Any]:
        """
        Run strategy code and return the 'output' variable defined in code.
        Used for signal bot preview functionality.
        """
        # 1. Calculate time range
        end_date = datetime.now()
        tf_seconds = self.TIMEFRAME_SECONDS.get(timeframe, 3600)
        start_date = end_date - timedelta(seconds=tf_seconds * limit)
        
        # 2. Fetch data (assuming market='crypto', can be optimized later)
        df = self._fetch_kline_data('crypto', symbol, timeframe, start_date, end_date)
        
        if df.empty:
            return {"error": "No data found"}

        # 3. Prepare execution environment
        local_vars = {
            'df': df.copy(),
            'np': np,
            'pd': pd,
            'output': {}  # Default empty output
        }
        
        # 4. Execute code (with validation + sandbox)
        try:
            from app.utils.safe_exec import build_safe_builtins, safe_exec_with_validation

            exec_env = local_vars.copy()
            exec_env['__builtins__'] = build_safe_builtins()

            exec_result = safe_exec_with_validation(
                code=code,
                exec_globals=exec_env,
                timeout=60,
            )
            if not exec_result['success']:
                return {"error": exec_result['error']}

            return exec_env.get('output', {})

        except Exception as e:
            logger.error(f"Strategy execution failed: {e}")
            logger.error(traceback.format_exc())
            return {"error": str(e)}

    def run_aligned(
        self,
        *,
        strict_mode: bool = True,
        indicator_code: str,
        market: str,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
        initial_capital: float = 10000.0,
        commission: float = 0.001,
        slippage: float = 0.0,
        leverage: int = 1,
        trade_direction: str = 'long',
        strategy_config: Optional[Dict[str, Any]] = None,
        indicator_params: Optional[Dict[str, Any]] = None,
        user_id: int = 1,
        indicator_id: Optional[int] = None,
        market_type: Optional[str] = None,
        exchange_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Live-aligned backtest: strict → closed-bar signals + next-bar open;
        non-strict → same-bar signals + 1m execution path (crypto).
        """
        from app.services.backtest_execution import (
            merge_strict_mode_into_strategy_config,
            precision_info_for_run,
        )

        cfg = merge_strict_mode_into_strategy_config(strategy_config, strict_mode)
        mkt = str(market or '').lower()

        if strict_mode:
            result = self.run(
                indicator_code=indicator_code,
                market=market,
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                initial_capital=initial_capital,
                commission=commission,
                slippage=slippage,
                leverage=leverage,
                trade_direction=trade_direction,
                strategy_config=cfg,
                indicator_params=indicator_params,
                user_id=user_id,
                indicator_id=indicator_id,
                market_type=market_type,
                exchange_id=exchange_id,
            )
            result['precision_info'] = precision_info_for_run(
                strict_mode=True, strategy_timeframe=timeframe,
            )
            ea = dict(result.get('executionAssumptions') or {})
            ea['strictMode'] = True
            ea['fillRule'] = 'next_bar_open'
            ea['subResolution'] = 'none'
            ea['simulationMode'] = 'strict_bar'
            ea['mtfRequested'] = False
            ea['mtfActive'] = False
            result['executionAssumptions'] = ea
            return result

        if mkt in ('crypto', 'cryptocurrency'):
            result = self.run_multi_timeframe(
                indicator_code=indicator_code,
                market=market,
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                initial_capital=initial_capital,
                commission=commission,
                slippage=slippage,
                leverage=leverage,
                trade_direction=trade_direction,
                strategy_config=cfg,
                enable_mtf=True,
                indicator_params=indicator_params,
                user_id=user_id,
                indicator_id=indicator_id,
                market_type=market_type,
                exchange_id=exchange_id,
            )
            pi_raw = result.get('precision_info') or {}
            mtf_active = bool(pi_raw.get('enabled'))
            exec_tf = pi_raw.get('timeframe')
            fallback = pi_raw.get('fallback_reason')
            result['precision_info'] = precision_info_for_run(
                strict_mode=False,
                strategy_timeframe=timeframe,
                mtf_active=mtf_active,
                exec_timeframe=exec_tf,
                fallback_reason=fallback,
            )
            ea = dict(result.get('executionAssumptions') or {})
            ea['strictMode'] = False
            ea['fillRule'] = 'intra_bar_1m' if mtf_active else 'same_bar_close'
            ea['subResolution'] = '1m' if mtf_active else 'none'
            ea['simulationMode'] = 'aggressive_1m' if mtf_active else 'aggressive_bar'
            ea['mtfRequested'] = True
            ea['mtfActive'] = mtf_active
            if fallback and not mtf_active:
                ea['mtfFallbackReason'] = fallback
            result['executionAssumptions'] = ea
            return result

        result = self.run(
            indicator_code=indicator_code,
            market=market,
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            commission=commission,
            slippage=slippage,
            leverage=leverage,
            trade_direction=trade_direction,
            strategy_config=cfg,
            indicator_params=indicator_params,
            user_id=user_id,
            indicator_id=indicator_id,
            market_type=market_type,
            exchange_id=exchange_id,
        )
        result['precision_info'] = precision_info_for_run(
            strict_mode=False,
            strategy_timeframe=timeframe,
            mtf_active=False,
            fallback_reason='non_crypto',
        )
        ea = dict(result.get('executionAssumptions') or {})
        ea['strictMode'] = False
        ea['fillRule'] = 'same_bar_close'
        ea['subResolution'] = 'none'
        ea['simulationMode'] = 'aggressive_bar'
        ea['mtfRequested'] = False
        ea['mtfActive'] = False
        ea['mtfFallbackReason'] = 'non_crypto'
        result['executionAssumptions'] = ea
        return result

    def run(
        self,
        indicator_code: str,
        market: str,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
        initial_capital: float = 10000.0,
        commission: float = 0.001,
        slippage: float = 0.0,  # Ideal backtest environment, no slippage
        leverage: int = 1,
        trade_direction: str = 'long',
        strategy_config: Optional[Dict[str, Any]] = None,
        indicator_params: Optional[Dict[str, Any]] = None,
        user_id: int = 1,
        indicator_id: Optional[int] = None,
        market_type: Optional[str] = None,
        exchange_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run backtest.
        
        Args:
            indicator_code: Indicator code
            market: Market type
            symbol: Trading symbol
            timeframe: Timeframe
            start_date: Start date
            end_date: End date
            initial_capital: Initial capital
            commission: Commission rate
            slippage: Slippage
            
        Returns:
            Backtest result
        """
        
        warmup_bars = self._estimate_warmup_bars(indicator_code, indicator_params)
        signal_start_date = self._warmup_start_date(start_date, timeframe, warmup_bars)

        # 1. Fetch candle data. Indicators execute on warmup+window data, while
        # trading starts strictly at the user-requested start_date.
        df_full = self._fetch_kline_data(
            market,
            symbol,
            timeframe,
            signal_start_date,
            end_date,
            market_type=market_type,
            exchange_id=exchange_id,
        )
        if df_full.empty:
            raise ValueError("No candle data available in the backtest date range")
        
        
        # 2. Execute indicator code to get signals (pass backtest params)
        backtest_params = {
            'leverage': leverage,
            'initial_capital': initial_capital,
            'commission': commission,
            'trade_direction': trade_direction,
            'indicator_params': indicator_params or {},
            'user_id': user_id,
            'indicator_id': indicator_id,
        }
        signals_full = self._execute_indicator(indicator_code, df_full, backtest_params)
        df = self._slice_to_backtest_window(df_full, start_date, end_date)
        if df.empty:
            raise ValueError("No candle data available in the backtest date range")
        signals = self._slice_signals_to_window(signals_full, df.index)
        
        # 3. Simulate trading
        equity_curve, trades, total_commission = self._simulate_trading(
            df, signals, initial_capital, commission, slippage, leverage, trade_direction, strategy_config
        )

        exec_cfg = (strategy_config or {}).get('execution') or {}
        signal_timing = str(exec_cfg.get('signalTiming') or 'next_bar_open').strip().lower()
        signal_tf_seconds = self.TIMEFRAME_SECONDS.get(timeframe, 3600)
        for trade in trades:
            if not trade.get('bar_time'):
                trade['bar_time'] = trade.get('time')
        self._annotate_signal_bar_times(trades, signal_tf_seconds, signal_timing)
        
        # 4. Calculate metrics
        metrics = self._calculate_metrics(equity_curve, trades, initial_capital, timeframe, start_date, end_date, total_commission)
        
        # 5. Format result
        result = self._format_result(metrics, equity_curve, trades)
        result['executionAssumptions'] = self._execution_assumptions(
            strategy_config,
            simulation_mode='standard',
            signal_timeframe=timeframe,
        )
        if market_type:
            result['executionAssumptions']['marketType'] = market_type
        if exchange_id:
            result['executionAssumptions']['exchangeId'] = exchange_id
        self._attach_warmup_to_result(
            result,
            warmup_bars=warmup_bars,
            warmup_start=signal_start_date,
            requested_start=start_date,
        )
        self._attach_buy_hold_benchmark(result, df, initial_capital, symbol)
        self._attach_actual_range_to_result(result, df)
        return result
    
    @staticmethod
    def _attach_actual_range_to_result(result: Dict[str, Any], df: pd.DataFrame) -> None:
        """因上游 K 线不足而缩短区间时，写入 executionAssumptions（供前端展示，非错误）。"""
        attrs = getattr(df, "attrs", None) or {}
        ar = attrs.get("backtestActualRange")
        if not ar:
            return
        ea = dict(result.get("executionAssumptions") or {})
        ea["actualDataRange"] = ar
        ea["requestedRangeAdjusted"] = True
        result["executionAssumptions"] = ea

    @staticmethod
    def _parse_curve_time(raw: Any) -> Optional[pd.Timestamp]:
        if raw is None:
            return None
        try:
            ts = pd.to_datetime(raw)
            if pd.isna(ts):
                return None
            if getattr(ts, "tzinfo", None) is not None:
                ts = ts.tz_convert(None)
            return ts
        except Exception:
            return None

    def _build_buy_hold_benchmark_curve(
        self,
        df: pd.DataFrame,
        equity_curve: List[Dict[str, Any]],
        initial_capital: float,
    ) -> Dict[str, Any]:
        """Build a same-period spot buy-and-hold benchmark aligned to equity points."""
        if df is None or df.empty or not equity_curve or "close" not in df.columns:
            return {}
        try:
            closes = df["close"].astype(float).replace([np.inf, -np.inf], np.nan).dropna()
        except Exception:
            return {}
        closes = closes[closes > 0]
        if closes.empty:
            return {}

        close_index = pd.to_datetime(closes.index)
        try:
            if getattr(close_index, "tz", None) is not None:
                close_index = close_index.tz_convert(None)
        except Exception:
            pass
        close_series = pd.Series(closes.to_numpy(dtype=float), index=close_index).sort_index()
        if close_series.empty:
            return {}

        base_price = float(close_series.iloc[0])
        if not math.isfinite(base_price) or base_price <= 0:
            return {}

        curve: List[Dict[str, Any]] = []
        last_price = base_price
        for point in equity_curve:
            ts = self._parse_curve_time((point or {}).get("time"))
            if ts is not None:
                pos = close_series.index.searchsorted(ts, side="right") - 1
                if pos < 0:
                    pos = 0
                try:
                    candidate = float(close_series.iloc[pos])
                    if math.isfinite(candidate) and candidate > 0:
                        last_price = candidate
                except Exception:
                    pass
            value = float(initial_capital or 0) * last_price / base_price
            curve.append({
                "time": (point or {}).get("time", ""),
                "value": round(value, 2),
                "price": round(last_price, 8),
            })

        if not curve:
            return {}
        initial = float(initial_capital or 0)
        final_value = float(curve[-1]["value"])
        benchmark_return = ((final_value - initial) / initial * 100) if initial > 0 else 0.0
        return {
            "benchmarkCurve": curve,
            "benchmarkReturn": round(benchmark_return, 2),
            "benchmarkFinalValue": round(final_value, 2),
            "benchmarkStartPrice": round(base_price, 8),
            "benchmarkEndPrice": round(last_price, 8),
        }

    def _attach_buy_hold_benchmark(
        self,
        result: Dict[str, Any],
        df: pd.DataFrame,
        initial_capital: float,
        symbol: str = "",
    ) -> None:
        benchmark = self._build_buy_hold_benchmark_curve(
            df,
            result.get("equityCurve") or [],
            initial_capital,
        )
        if not benchmark:
            return
        result.update(benchmark)
        try:
            total_return = float(result.get("totalReturn") or 0)
            result["alphaReturn"] = round(total_return - float(benchmark.get("benchmarkReturn") or 0), 2)
        except Exception:
            pass
        result["benchmark"] = {
            "type": "buy_hold_spot",
            "label": "Spot buy-and-hold",
            "symbol": symbol or "",
            "return": result.get("benchmarkReturn"),
            "finalValue": result.get("benchmarkFinalValue"),
            "startPrice": result.get("benchmarkStartPrice"),
            "endPrice": result.get("benchmarkEndPrice"),
        }
    
    def _fetch_kline_data(
        self,
        market: str,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
        market_type: Optional[str] = None,
        exchange_id: Optional[str] = None,
    ) -> pd.DataFrame:
        """Fetch candle data and convert to DataFrame (with in-memory caching)"""
        # Calculate required candle count (+ slack for 2y-class windows & upstream gaps)
        total_seconds = (end_date - start_date).total_seconds()
        tf_seconds = self.TIMEFRAME_SECONDS.get(timeframe, 86400)
        raw_bars = max(1, math.ceil(total_seconds / tf_seconds))
        limit = int(math.ceil(raw_bars * 1.15) + 200)

        # Earliest candle we need (backtest may span up to ~2y; align fetch window to real start)
        after_time = int((start_date - timedelta(days=1)).timestamp())

        # Calculate before_time (end date + 1 day)
        before_time = int((end_date + timedelta(days=1)).timestamp())

        mt_key = str(market_type or "").strip().lower()
        ex_key = str(exchange_id or "").strip().lower()
        cache_key = f"{market}:{symbol}:{timeframe}:{mt_key}:{ex_key}:{start_date.date()}:{end_date.date()}"
        cached = _kline_cache.get(cache_key)
        if cached is not None and not cached.empty:
            logger.info(f"K-line cache HIT for {cache_key} ({len(cached)} candles)")
            return cached
        
        # Fetch data. We deliberately swallow any upstream exception (CCXT
        # network/rate-limit errors, yfinance hiccups, etc.) and return an empty
        # DataFrame instead — the MTF entry point then falls back to a standard
        # backtest with a clear `mtfFallbackReason='data_unavailable'`, which is
        # far friendlier than bubbling up a 500.
        try:
            kline_data = DataSourceFactory.get_kline(
                market=market,
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                before_time=before_time,
                after_time=after_time,
                exchange_id=exchange_id,
                market_type=market_type,
            )
        except Exception as exc:
            logger.warning(
                f"DataSourceFactory.get_kline raised for {market}:{symbol} {timeframe} "
                f"(limit={limit}): {exc}; returning empty DataFrame so the caller can fall back."
            )
            return pd.DataFrame()

        if not kline_data:
            logger.warning(f"No candle data retrieved for {market}:{symbol}, timeframe={timeframe}, limit={limit}, before_time={before_time}")
            return pd.DataFrame()
        
        logger.info(f"Retrieved {len(kline_data)} candles for {market}:{symbol}, timeframe={timeframe}")
        
        # Convert to DataFrame
        try:
            df = pd.DataFrame(kline_data)
            if df.empty:
                logger.warning(f"DataFrame is empty after conversion")
                return pd.DataFrame()
            
            # Handle time column - could be seconds or milliseconds
            if 'time' not in df.columns:
                logger.error(f"Missing 'time' column in kline data. Columns: {df.columns.tolist()}")
                return pd.DataFrame()
            
            # Try seconds first, if fails try milliseconds
            try:
                df['time'] = pd.to_datetime(df['time'], unit='s')
            except (ValueError, OverflowError):
                # If seconds fails, try milliseconds
                try:
                    df['time'] = pd.to_datetime(df['time'], unit='ms')
                except (ValueError, OverflowError):
                    # If both fail, try direct conversion
                    df['time'] = pd.to_datetime(df['time'])
            
            df = df.set_index('time')
            
            if df.empty:
                logger.warning(f"DataFrame is empty after setting time index")
                return pd.DataFrame()
            
            # Log data range before filtering
            data_start = df.index.min()
            data_end = df.index.max()
            logger.info(f"Kline data range: {data_start} to {data_end}, requested range: {start_date} to {end_date}")

            if data_start > start_date or data_end < end_date:
                logger.debug(
                    "Backtest requested window wider than upstream: "
                    f"requested=[{start_date} ~ {end_date}], upstream=[{data_start} ~ {data_end}]"
                )

            rs = pd.Timestamp(start_date)
            re = pd.Timestamp(end_date)
            effective_start = max(rs, pd.Timestamp(data_start))
            effective_end = min(re, pd.Timestamp(data_end))
            window_adjusted = False

            if effective_start <= effective_end:
                df_filtered = df[(df.index >= effective_start) & (df.index <= effective_end)].copy()
            else:
                alt_start = pd.Timestamp(data_start)
                alt_end = min(re, pd.Timestamp(data_end))
                if alt_start <= alt_end:
                    df_filtered = df[(df.index >= alt_start) & (df.index <= alt_end)].copy()
                    effective_start, effective_end = alt_start, alt_end
                    window_adjusted = True
                    logger.info(
                        f"[Backtest] 可用K线未覆盖所选起点，已从首根可用K线开始回测 "
                        f"{market}:{symbol} {timeframe} effective=[{effective_start} ~ {effective_end}]"
                    )
                elif len(df) > 0:
                    df_filtered = df.copy()
                    effective_start = df_filtered.index.min()
                    effective_end = df_filtered.index.max()
                    window_adjusted = True
                    logger.info(
                        f"[Backtest] 所选区间与可用数据无重叠，使用全部可用K线 "
                        f"{market}:{symbol} {timeframe} [{effective_start} ~ {effective_end}]"
                    )
                else:
                    return pd.DataFrame()

            used_fallback = window_adjusted

            # Diagnostics: did we actually cover a meaningful portion of the requested range?
            requested_seconds = max(1.0, (end_date - start_date).total_seconds())
            covered_seconds = 0.0
            if not df_filtered.empty:
                covered_seconds = (df_filtered.index.max() - df_filtered.index.min()).total_seconds()
            coverage_ratio = covered_seconds / requested_seconds if requested_seconds > 0 else 0.0

            if df_filtered.empty:
                requested_candles = max(1, math.ceil(requested_seconds / tf_seconds))
                if len(df) > 0:
                    df_filtered = df.tail(min(len(df), requested_candles)).copy()
                    effective_start = df_filtered.index.min()
                    effective_end = df_filtered.index.max()
                    used_fallback = True
                    logger.debug(
                        f"[Backtest] 过滤后为空，已回退为最近 {len(df_filtered)} 根K线 "
                        f"{market}:{symbol} {timeframe} ({effective_start} ~ {effective_end})"
                    )
                else:
                    logger.debug(
                        f"[Backtest] 过滤后无K线 {market}:{symbol} {timeframe} "
                        f"upstream={data_start}~{data_end}"
                    )
                    return pd.DataFrame()

            if pd.Timestamp(data_start) > rs or window_adjusted:
                df_filtered.attrs["backtestActualRange"] = {
                    "requestedStart": str(rs),
                    "requestedEnd": str(re),
                    "actualStart": str(effective_start),
                    "actualEnd": str(effective_end),
                }

            logger.info(
                f"[Backtest] {market}:{symbol} {timeframe} | "
                f"requested [{start_date} ~ {end_date}] | "
                f"upstream [{data_start} ~ {data_end}] ({len(df)} candles) | "
                f"effective [{effective_start} ~ {effective_end}] ({len(df_filtered)} candles) | "
                f"coverage={coverage_ratio*100:.1f}% | fallback={used_fallback}"
            )
            _kline_cache.put(cache_key, df_filtered, timeframe)
            return df_filtered
            
        except Exception as e:
            logger.error(f"Error processing kline data: {str(e)}")
            logger.error(traceback.format_exc())
            return pd.DataFrame()
    
    def _execute_indicator(self, code: str, df: pd.DataFrame, backtest_params: dict = None):
        """Execute indicator code to get signals.
        
        Args:
            code: Indicator code
            df: Candle data
            backtest_params: Backtest parameters dict (leverage, initial_capital, commission, trade_direction)
        """
        # Supported indicator signal formats:
        # - Preferred (simple): df['buy'], df['sell'] as boolean
        # - Backtest/internal (4-way): df['open_long'], df['close_long'], df['open_short'], df['close_short'] as boolean
        signals = pd.Series(0, index=df.index)
        
        try:
            # Reset DatetimeIndex to integer so user code can use df.at[0, ...] or df.iloc[0, ...]
            df_for_exec = df.copy()
            if isinstance(df_for_exec.index, pd.DatetimeIndex):
                df_for_exec = df_for_exec.reset_index(drop=False)
                if 'time' not in df_for_exec.columns:
                    df_for_exec.rename(columns={df_for_exec.columns[0]: 'time'}, inplace=True)

            local_vars = {
                'df': df_for_exec,
                'open': df_for_exec['open'],
                'high': df_for_exec['high'],
                'low': df_for_exec['low'],
                'close': df_for_exec['close'],
                'volume': df_for_exec['volume'],
                'signals': pd.Series(0, index=df_for_exec.index),
                'np': np,
                'pd': pd,
            }
            
            # Add backtest params to execution environment (if provided)
            if backtest_params:
                local_vars['backtest_params'] = backtest_params
                local_vars['leverage'] = backtest_params.get('leverage', 1)
                local_vars['initial_capital'] = backtest_params.get('initial_capital', 10000)
                local_vars['commission'] = backtest_params.get('commission', 0.0002)
                local_vars['trade_direction'] = backtest_params.get('trade_direction', 'both')
            
            user_indicator_params = (backtest_params or {}).get('indicator_params', {})
            declared_params = IndicatorParamsParser.parse_params(code)
            merged_params = IndicatorParamsParser.merge_params(declared_params, user_indicator_params)
            local_vars['params'] = merged_params
            
            user_id = (backtest_params or {}).get('user_id', 1)
            indicator_id = (backtest_params or {}).get('indicator_id')
            indicator_caller = IndicatorCaller(user_id, indicator_id)
            local_vars['call_indicator'] = indicator_caller.call_indicator
            
            # Add technical indicator functions
            local_vars.update(self._get_indicator_functions())
            
            from app.utils.safe_exec import build_safe_builtins, safe_exec_with_validation

            exec_env = local_vars.copy()
            exec_env['__builtins__'] = build_safe_builtins()

            exec_result = safe_exec_with_validation(
                code=code,
                exec_globals=exec_env,
                exec_locals=exec_env,
                timeout=60,
            )
            
            if not exec_result['success']:
                raise RuntimeError(f"Code execution failed: {exec_result['error']}")
            
            # Get the executed df, restore DatetimeIndex for signal alignment
            executed_df = exec_env.get('df', df)
            if isinstance(df.index, pd.DatetimeIndex) and not isinstance(executed_df.index, pd.DatetimeIndex):
                if 'time' in executed_df.columns:
                    executed_df = executed_df.set_index('time')
                elif len(executed_df) == len(df):
                    executed_df.index = df.index

            # Validation: chart markers in output['signals'] require df execution columns.
            # four_way scripts may omit buy/sell when open/close_* columns are present.
            output_obj = exec_env.get('output')
            has_output_signals = isinstance(output_obj, dict) and isinstance(output_obj.get('signals'), list) and len(output_obj.get('signals')) > 0
            has_four_way = all(col in executed_df.columns for col in ['open_long', 'close_long', 'open_short', 'close_short'])
            has_buy_sell = all(col in executed_df.columns for col in ['buy', 'sell'])
            if has_output_signals and not has_four_way and not has_buy_sell:
                raise ValueError(
                    "Invalid indicator script: output['signals'] is provided, but df execution columns are missing. "
                    "Set four-way df['open_long'], df['close_long'], df['open_short'], and df['close_short']. "
                    "output['signals'] is chart-only and cannot place orders."
                )
            
            # Extract signals from executed df
            if all(col in executed_df.columns for col in ['open_long', 'close_long', 'open_short', 'close_short']):
                
                signals = {
                    'open_long': executed_df['open_long'].fillna(False).astype(bool),
                    'close_long': executed_df['close_long'].fillna(False).astype(bool),
                    'open_short': executed_df['open_short'].fillna(False).astype(bool),
                    'close_short': executed_df['close_short'].fillna(False).astype(bool)
                }
                
                # Convention: backtest uses 4-way signals only.
                # Position sizing, TP/SL, trailing, etc must be handled by strategy_config / strategy logic.
            elif all(col in executed_df.columns for col in ['buy', 'sell']):
                # Simple buy/sell signals (recommended for indicator authors)
                buy_series = executed_df['buy'].fillna(False).astype(bool)
                sell_series = executed_df['sell'].fillna(False).astype(bool)
                
                # Ensure signals have the same index as df
                if not buy_series.index.equals(df.index):
                    logger.warning(f"Buy signal index mismatch in _execute_indicator! Reindexing...")
                    buy_series = buy_series.reindex(df.index, fill_value=False)
                if not sell_series.index.equals(df.index):
                    logger.warning(f"Sell signal index mismatch in _execute_indicator! Reindexing...")
                    sell_series = sell_series.reindex(df.index, fill_value=False)
                
                # Debug: log signal statistics
                buy_count = buy_series.sum()
                sell_count = sell_series.sum()
                logger.info(f"Indicator execution: buy signals={buy_count}, sell signals={sell_count}, total_candles={len(df)}")
                
                signals = {
                    'buy': buy_series,
                    'sell': sell_series
                }
            
            else:
                raise ValueError(
                    "Indicator must define either 4-way columns "
                    "(df['open_long'], df['close_long'], df['open_short'], df['close_short']) "
                    "for new scripts. Legacy df['buy']/df['sell'] is still executable only for existing saved code."
                )
            
        except Exception as e:
            logger.error(f"Indicator code execution error: {e}")
            logger.error(traceback.format_exc())
        
        return signals

    def _execute_script_strategy(self, code: str, df: pd.DataFrame, runtime: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        runtime = runtime or {}
        if not code or not str(code).strip():
            raise ValueError("Strategy script is empty")

        df_exec = df.copy().reset_index(drop=False)
        if 'time' not in df_exec.columns:
            df_exec.rename(columns={df_exec.columns[0]: 'time'}, inplace=True)

        open_long = pd.Series(False, index=df.index)
        close_long = pd.Series(False, index=df.index)
        open_short = pd.Series(False, index=df.index)
        close_short = pd.Series(False, index=df.index)
        add_long = pd.Series(False, index=df.index)
        add_short = pd.Series(False, index=df.index)

        # Share the live-trading hedge-aware ctx implementation so the two
        # paths can't drift apart (P0-1, May 2026). ScriptBar is still needed
        # locally to inject the per-bar payload.
        from app.services.strategy_script_runtime import (
            ScriptBar,
            ScriptPosition,
            StrategyScriptContext as ScriptBacktestContext,
        )

        try:
            from app.utils.safe_exec import build_safe_builtins, safe_exec_with_validation

            ctx = ScriptBacktestContext(df_exec, float(runtime.get('initial_capital') or 10000))
            exec_env = {
                '__builtins__': build_safe_builtins(),
                'np': np,
                'pd': pd,
            }

            exec_result = safe_exec_with_validation(
                code=code,
                exec_globals=exec_env,
                exec_locals=exec_env,
                timeout=60,
            )
            if not exec_result['success']:
                raise RuntimeError(f"Code execution failed: {exec_result['error']}")

            on_init = exec_env.get('on_init')
            on_bar = exec_env.get('on_bar')
            if not callable(on_bar):
                raise ValueError("Strategy script must define on_bar(ctx, bar)")
            if callable(on_init):
                on_init(ctx)

            trade_direction = str(runtime.get('trade_direction') or 'both').lower()
            if trade_direction not in ('long', 'short', 'both'):
                trade_direction = 'both'

            for i, row in df_exec.iterrows():
                ctx.current_index = int(i)
                ctx._orders = []
                bar = ScriptBar(
                    open=float(row.get('open') or 0),
                    high=float(row.get('high') or 0),
                    low=float(row.get('low') or 0),
                    close=float(row.get('close') or 0),
                    volume=float(row.get('volume') or 0),
                    timestamp=row.get('time')
                )
                on_bar(ctx, bar)

                for order in ctx._orders:
                    action = str(order.get('action') or '').lower()
                    intent = str(order.get('intent') or 'auto').lower()
                    order_price = float(order.get('price') or bar['close'] or 0)
                    order_amount = float(order.get('amount') or 0)

                    if action == 'close':
                        if ctx.position.has_long():
                            close_long.iloc[i] = True
                            ctx.position.close_long()
                        if ctx.position.has_short():
                            close_short.iloc[i] = True
                            ctx.position.close_short()
                        continue

                    # Explicit hedge intents — ctx.close_long / close_short /
                    # open_long / open_short. Keep both legs independent.
                    if intent == 'close_long':
                        if ctx.position.has_long():
                            close_long.iloc[i] = True
                            ctx.position.reduce_long(order_amount or ctx.position.long_size)
                        continue
                    if intent == 'close_short':
                        if ctx.position.has_short():
                            close_short.iloc[i] = True
                            ctx.position.reduce_short(order_amount or ctx.position.short_size)
                        continue
                    if intent == 'open_long':
                        if trade_direction in ('long', 'both'):
                            if ctx.position.has_long():
                                add_long.iloc[i] = True
                            else:
                                open_long.iloc[i] = True
                            ctx.position.open_long(order_price, order_amount)
                        continue
                    if intent == 'open_short':
                        if trade_direction in ('short', 'both'):
                            if ctx.position.has_short():
                                add_short.iloc[i] = True
                            else:
                                open_short.iloc[i] = True
                            ctx.position.open_short(order_price, order_amount)
                        continue

                    if action == 'buy':
                        # Auto intent: cover short leg first, otherwise stack long.
                        if ctx.position.has_short():
                            close_short.iloc[i] = True
                            ctx.position.close_short()
                        if trade_direction in ('long', 'both'):
                            if ctx.position.has_long():
                                add_long.iloc[i] = True
                            else:
                                open_long.iloc[i] = True
                            ctx.position.open_long(order_price, order_amount)
                        continue

                    if action == 'sell':
                        if ctx.position.has_long():
                            close_long.iloc[i] = True
                            ctx.position.close_long()
                        if trade_direction in ('short', 'both'):
                            if ctx.position.has_short():
                                add_short.iloc[i] = True
                            else:
                                open_short.iloc[i] = True
                            ctx.position.open_short(order_price, order_amount)

            return {
                'open_long': open_long,
                'close_long': close_long,
                'open_short': open_short,
                'close_short': close_short,
                'add_long': add_long,
                'add_short': add_short,
                'logs': ctx.flush_logs(),
            }
        except Exception as e:
            logger.error(f"Strategy script execution error: {e}")
            logger.error(traceback.format_exc())
            raise
    
    def _get_indicator_functions(self) -> Dict:
        """Get technical indicator functions"""
        def SMA(series, period):
            return series.rolling(window=period).mean()
        
        def EMA(series, period):
            return series.ewm(span=period, adjust=False).mean()
        
        def RSI(series, period=14):
            delta = series.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            rs = gain / loss
            return 100 - (100 / (1 + rs))
        
        def MACD(series, fast=12, slow=26, signal=9):
            exp1 = series.ewm(span=fast, adjust=False).mean()
            exp2 = series.ewm(span=slow, adjust=False).mean()
            macd = exp1 - exp2
            macd_signal = macd.ewm(span=signal, adjust=False).mean()
            macd_hist = macd - macd_signal
            return macd, macd_signal, macd_hist
        
        def BOLL(series, period=20, std_dev=2):
            middle = series.rolling(window=period).mean()
            std = series.rolling(window=period).std()
            upper = middle + std_dev * std
            lower = middle - std_dev * std
            return upper, middle, lower
        
        def ATR(high, low, close, period=14):
            tr1 = high - low
            tr2 = abs(high - close.shift())
            tr3 = abs(low - close.shift())
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            return tr.rolling(window=period).mean()
        
        def CROSSOVER(series1, series2):
            return (series1 > series2) & (series1.shift(1) <= series2.shift(1))
        
        def CROSSUNDER(series1, series2):
            return (series1 < series2) & (series1.shift(1) >= series2.shift(1))
        
        return {
            'SMA': SMA,
            'EMA': EMA,
            'RSI': RSI,
            'MACD': MACD,
            'BOLL': BOLL,
            'ATR': ATR,
            'CROSSOVER': CROSSOVER,
            'CROSSUNDER': CROSSUNDER,
        }
    
    def _simulate_trading(
        self,
        df: pd.DataFrame,
        signals,
        initial_capital: float,
        commission: float,
        slippage: float,
        leverage: int = 1,
        trade_direction: str = 'long',
        strategy_config: Optional[Dict[str, Any]] = None
    ) -> tuple:
        """
        Simulate trading.
        
        Args:
            signals: Signals, can be pd.Series (old format) or dict (new 4-way format)
            trade_direction: Trade direction
                - 'long': Long only (buy->sell)
                - 'short': Short only (sell->buy, reversed PnL)
                - 'both': Both directions (buy->sell long + sell->buy short)
        """
        # Normalize supported signal formats into 4-way signals.
        if not isinstance(signals, dict):
            raise ValueError("signals must be a dict (either 4-way or buy/sell).")

        if all(k in signals for k in ['open_long', 'close_long', 'open_short', 'close_short']):
            norm = signals
        elif all(k in signals for k in ['buy', 'sell']):
            buy = signals['buy'].fillna(False).astype(bool)
            sell = signals['sell'].fillna(False).astype(bool)

            td = (trade_direction or 'both')
            td = str(td).lower()
            if td not in ['long', 'short', 'both']:
                td = 'both'

            # Mapping rules:
            # - long: buy=open_long, sell=close_long
            # - short: sell=open_short, buy=close_short
            # - both: buy=open_long+close_short, sell=open_short+close_long
            if td == 'long':
                norm = {
                    'open_long': buy,
                    'close_long': sell,
                    'open_short': pd.Series([False] * len(df), index=df.index),
                    'close_short': pd.Series([False] * len(df), index=df.index),
                }
            elif td == 'short':
                norm = {
                    'open_long': pd.Series([False] * len(df), index=df.index),
                    'close_long': pd.Series([False] * len(df), index=df.index),
                    'open_short': sell,
                    'close_short': buy,
                    '_both_mode': False,
                }
            else:
                # Both mode: buy signal opens long (auto-close short first)
                # sell signal opens short (auto-close long first)
                norm = {
                    'open_long': buy,
                    'close_long': pd.Series([False] * len(df), index=df.index),  # Disabled, handled by open_short
                    'open_short': sell,
                    'close_short': pd.Series([False] * len(df), index=df.index),  # Disabled, handled by open_long
                    '_both_mode': True,  # Flag to indicate auto-close opposing position
                }
        else:
            raise ValueError("signals dict must contain either 4-way keys or buy/sell keys.")

        try:
            data_start = df.index.min()
            data_end = df.index.max()
        except Exception:
            data_start = data_end = None
        try:
            ol = int(norm.get('open_long').sum()) if hasattr(norm.get('open_long'), 'sum') else 0
            cl = int(norm.get('close_long').sum()) if hasattr(norm.get('close_long'), 'sum') else 0
            os_ = int(norm.get('open_short').sum()) if hasattr(norm.get('open_short'), 'sum') else 0
            cs = int(norm.get('close_short').sum()) if hasattr(norm.get('close_short'), 'sum') else 0
        except Exception:
            ol = cl = os_ = cs = -1
        logger.info(
            f"[Backtest] simulate_trading: {len(df)} candles [{data_start} ~ {data_end}], "
            f"signals open_long={ol} close_long={cl} open_short={os_} close_short={cs}, "
            f"direction={trade_direction}"
        )
        return self._simulate_trading_new_format(df, norm, initial_capital, commission, slippage, leverage, trade_direction, strategy_config)

    def _simulate_trading_new_format(
        self,
        df: pd.DataFrame,
        signals: dict,
        initial_capital: float,
        commission: float,
        slippage: float,
        leverage: int = 1,
        trade_direction: str = 'both',
        strategy_config: Optional[Dict[str, Any]] = None
    ) -> tuple:
        """
        Simulate trading with 4-way signal format (supports position management and scaling).
        
        Args:
            trade_direction: Trade direction ('long', 'short', 'both')
        """
        equity_curve = []
        trades = []
        total_commission_paid = 0
        is_liquidated = False
        liquidation_price = 0
        min_capital_to_trade = 1.0  # Below this balance, consider wiped out, no new orders
        
        capital = initial_capital
        position = 0  # Positive=long, Negative=short
        entry_price = 0  # Average entry price
        position_type = None  # 'long' or 'short'
        
        # Position management related
        has_position_management = 'add_long' in signals and 'add_short' in signals
        position_batches = []  # Store each position batch: [{'price': xxx, 'amount': xxx}, ...]

        # --- Strategy config: signals + parameters = strategy (sent from BacktestModal as strategyConfig) ---
        cfg = strategy_config or {}
        exec_cfg = cfg.get('execution') or {}
        # Signal confirmation / execution timing:
        # - bar_close: execute on the same bar close (more aggressive)
        # - next_bar_open: execute on next bar open after signal is confirmed on bar close (recommended, closer to live)
        signal_timing = str(exec_cfg.get('signalTiming') or 'next_bar_open').strip().lower()
        risk_cfg = cfg.get('risk') or {}
        stop_loss_pct = float(risk_cfg.get('stopLossPct') or 0.0)
        take_profit_pct = float(risk_cfg.get('takeProfitPct') or 0.0)
        trailing_cfg = risk_cfg.get('trailing') or {}
        trailing_enabled = bool(trailing_cfg.get('enabled'))
        trailing_pct = float(trailing_cfg.get('pct') or 0.0)
        trailing_activation_pct = float(trailing_cfg.get('activationPct') or 0.0)
        exit_owner = str(cfg.get('exitOwner') or cfg.get('exit_owner') or '').strip().lower()
        if exit_owner == 'indicator':
            stop_loss_pct = 0.0
            take_profit_pct = 0.0
            trailing_enabled = False
            trailing_pct = 0.0
            trailing_activation_pct = 0.0

        # Risk percentages are the underlying's % price move directly.
        # Leverage only affects PnL magnitude / liquidation, NOT trigger thresholds.
        stop_loss_pct_eff = stop_loss_pct
        take_profit_pct_eff = take_profit_pct
        trailing_pct_eff = trailing_pct
        trailing_activation_pct_eff = trailing_activation_pct

        # Conflict rule (TP vs trailing):
        # - If trailing is enabled, it takes precedence.
        # - If activationPct is not provided, reuse takeProfitPct as the trailing activation threshold.
        # - When trailing is enabled, fixed take-profit exits are disabled to avoid ambiguity.
        if trailing_enabled and trailing_pct_eff > 0:
            if trailing_activation_pct_eff <= 0 and take_profit_pct_eff > 0:
                trailing_activation_pct_eff = take_profit_pct_eff

        pos_cfg = cfg.get('position') or {}
        entry_pct_cfg = float(pos_cfg.get('entryPct') or 1.0)  # expected 0~1
        # Accept both 0~1 and 0~100 inputs (some clients may send percent units).
        if entry_pct_cfg > 1:
            entry_pct_cfg = entry_pct_cfg / 100.0
        entry_pct_cfg = max(0.0, min(entry_pct_cfg, 1.0))

        scale_cfg = cfg.get('scale') or {}
        trend_add_cfg = scale_cfg.get('trendAdd') or {}
        dca_add_cfg = scale_cfg.get('dcaAdd') or {}
        trend_reduce_cfg = scale_cfg.get('trendReduce') or {}
        adverse_reduce_cfg = scale_cfg.get('adverseReduce') or {}

        trend_add_enabled = bool(trend_add_cfg.get('enabled'))
        trend_add_step_pct = float(trend_add_cfg.get('stepPct') or 0.0)
        trend_add_size_pct = float(trend_add_cfg.get('sizePct') or 0.0)
        trend_add_max_times = int(trend_add_cfg.get('maxTimes') or 0)

        dca_add_enabled = bool(dca_add_cfg.get('enabled'))
        dca_add_step_pct = float(dca_add_cfg.get('stepPct') or 0.0)
        dca_add_size_pct = float(dca_add_cfg.get('sizePct') or 0.0)
        dca_add_max_times = int(dca_add_cfg.get('maxTimes') or 0)

        # Prevent logical conflict: trend scale-in and mean-reversion scale-in should not run together.
        # Otherwise both may trigger in the same candle (high/low both hit), causing double scaling unexpectedly.
        if trend_add_enabled and dca_add_enabled:
            dca_add_enabled = False

        trend_reduce_enabled = bool(trend_reduce_cfg.get('enabled'))
        trend_reduce_step_pct = float(trend_reduce_cfg.get('stepPct') or 0.0)
        trend_reduce_size_pct = float(trend_reduce_cfg.get('sizePct') or 0.0)
        trend_reduce_max_times = int(trend_reduce_cfg.get('maxTimes') or 0)

        adverse_reduce_enabled = bool(adverse_reduce_cfg.get('enabled'))
        adverse_reduce_step_pct = float(adverse_reduce_cfg.get('stepPct') or 0.0)
        adverse_reduce_size_pct = float(adverse_reduce_cfg.get('sizePct') or 0.0)
        adverse_reduce_max_times = int(adverse_reduce_cfg.get('maxTimes') or 0)

        # Step percentages are the underlying's % price move directly (no leverage scaling).
        trend_add_step_pct_eff = trend_add_step_pct
        dca_add_step_pct_eff = dca_add_step_pct
        trend_reduce_step_pct_eff = trend_reduce_step_pct
        adverse_reduce_step_pct_eff = adverse_reduce_step_pct

        # State: used for trailing exits and scale-in/scale-out anchor levels
        highest_since_entry = None
        lowest_since_entry = None
        trend_add_times = 0
        dca_add_times = 0
        trend_reduce_times = 0
        adverse_reduce_times = 0
        last_trend_add_anchor = None
        last_dca_add_anchor = None
        last_trend_reduce_anchor = None
        last_adverse_reduce_anchor = None
        
        # Convert signals to arrays
        open_long_arr = signals['open_long'].values
        close_long_arr = signals['close_long'].values
        open_short_arr = signals['open_short'].values
        close_short_arr = signals['close_short'].values

        # Apply execution timing to avoid look-ahead bias:
        # If signals are computed using bar close, realistic execution is next bar open.
        if signal_timing in ['next_bar_open', 'next_open', 'nextopen', 'next']:
            open_long_arr = np.insert(open_long_arr[:-1], 0, False)
            close_long_arr = np.insert(close_long_arr[:-1], 0, False)
            open_short_arr = np.insert(open_short_arr[:-1], 0, False)
            close_short_arr = np.insert(close_short_arr[:-1], 0, False)
        
        # Filter signals by trade direction
        if trade_direction == 'long':
            # Long only: disable all short signals
            open_short_arr = np.zeros(len(df), dtype=bool)
            close_short_arr = np.zeros(len(df), dtype=bool)
        elif trade_direction == 'short':
            # Short only: disable all long signals
            open_long_arr = np.zeros(len(df), dtype=bool)
            close_long_arr = np.zeros(len(df), dtype=bool)
        else:
            pass
        
        # Add position signals
        if has_position_management:
            add_long_arr = signals['add_long'].values
            add_short_arr = signals['add_short'].values
            position_size_arr = signals.get('position_size', pd.Series([0.0] * len(df))).values
            
            # Filter add signals by trade direction
            if trade_direction == 'long':
                add_short_arr = np.zeros(len(df), dtype=bool)
            elif trade_direction == 'short':
                add_long_arr = np.zeros(len(df), dtype=bool)
        
        # Entry trigger price (if indicator provides)
        open_long_price_arr = signals.get('open_long_price', pd.Series([0.0] * len(df))).values
        open_short_price_arr = signals.get('open_short_price', pd.Series([0.0] * len(df))).values
        
        # Exit target price (if indicator provides)
        close_long_price_arr = signals.get('close_long_price', pd.Series([0.0] * len(df))).values
        close_short_price_arr = signals.get('close_short_price', pd.Series([0.0] * len(df))).values
        
        # Add position price (if indicator provides)
        add_long_price_arr = signals.get('add_long_price', pd.Series([0.0] * len(df))).values
        add_short_price_arr = signals.get('add_short_price', pd.Series([0.0] * len(df))).values
        
        for i, (timestamp, row) in enumerate(df.iterrows()):
            if is_liquidated:
                break

            # If no position and balance low, stop trading
            if position == 0 and capital < min_capital_to_trade:
                is_liquidated = True
                liquidation_loss = self._liquidation_loss(capital)
                capital = 0
                trades.append({
                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                    'type': 'liquidation',
                    'price': round(float(row.get('close', 0) or 0), 4),
                    'amount': 0,
                    'profit': liquidation_loss,
                    'balance': 0
                })
                equity_curve.append({'time': timestamp.strftime('%Y-%m-%d %H:%M'), 'value': 0})
                break  # 直接停止
            
            # Use OHLC to evaluate triggers.
            high = row['high']
            low = row['low']
            close = row['close']
            open_ = row.get('open', close)
            
            # Default execution price depends on timing mode
            # - bar_close: close
            # - next_bar_open: open (this bar is the next bar for a prior signal)
            exec_price = open_ if signal_timing in ['next_bar_open', 'next_open', 'nextopen', 'next'] else close

            # --- Risk controls: SL / TP / trailing exit (highest priority) ---
            if position != 0 and position_type in ['long', 'short']:
                # Update extreme prices for trailing stop
                if position_type == 'long':
                    if highest_since_entry is None:
                        highest_since_entry = entry_price
                    if lowest_since_entry is None:
                        lowest_since_entry = entry_price
                    highest_since_entry = max(highest_since_entry, high)
                    lowest_since_entry = min(lowest_since_entry, low)
                else:  # short
                    if lowest_since_entry is None:
                        lowest_since_entry = entry_price
                    if highest_since_entry is None:
                        highest_since_entry = entry_price
                    lowest_since_entry = min(lowest_since_entry, low)
                    highest_since_entry = max(highest_since_entry, high)

                # Collect forced exit points in same candle
                # Backtest is candle-level, cannot determine exact trigger order; using priority:
                # StopLoss > TrailingStop > TakeProfit
                candidates = []  # [(trade_type, trigger_price)]
                if position_type == 'long' and position > 0:
                    if stop_loss_pct_eff > 0:
                        sl_price = entry_price * (1 - stop_loss_pct_eff)
                        if low <= sl_price:
                            candidates.append(('close_long_stop', sl_price))
                    # Fixed take-profit exit is disabled when trailing is enabled (see conflict rule above).
                    if (not trailing_enabled) and take_profit_pct_eff > 0:
                        tp_price = entry_price * (1 + take_profit_pct_eff)
                        if high >= tp_price:
                            candidates.append(('close_long_profit', tp_price))
                    if trailing_enabled and trailing_pct_eff > 0 and highest_since_entry is not None:
                        trail_active = True
                        if trailing_activation_pct_eff > 0:
                            trail_active = highest_since_entry >= entry_price * (1 + trailing_activation_pct_eff)
                        if trail_active:
                            tr_price = highest_since_entry * (1 - trailing_pct_eff)
                            tr_exec_price = tr_price * (1 - slippage)
                            if low <= tr_price and trailing_exit_locks_net_profit(
                                "long",
                                entry_price=entry_price,
                                exit_price=tr_exec_price,
                                fee_rate=commission,
                            ):
                                candidates.append(('close_long_trailing', tr_price))

                    if candidates:
                        # Select by priority: SL > Trailing > TP
                        pri = {'close_long_stop': 0, 'close_long_trailing': 1, 'close_long_profit': 2}
                        trade_type, trigger_price = sorted(candidates, key=lambda x: (pri.get(x[0], 99), x[1]))[0]
                        exec_price_close = trigger_price * (1 - slippage)
                        commission_fee_close = position * exec_price_close * commission
                        # Entry commission deducted, only deduct exit commission
                        profit = (exec_price_close - entry_price) * position - commission_fee_close
                        capital += profit
                        total_commission_paid += commission_fee_close

                        trades.append({
                            'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                            'type': trade_type,
                            'price': round(exec_price_close, 4),
                            'amount': round(position, 4),
                            'profit': round(profit, 2),
                            'balance': round(max(0, capital), 2)
                        })

                        position = 0
                        position_type = None
                        liquidation_price = 0
                        highest_since_entry = None
                        lowest_since_entry = None
                        trend_add_times = dca_add_times = trend_reduce_times = adverse_reduce_times = 0
                        last_trend_add_anchor = last_dca_add_anchor = last_trend_reduce_anchor = last_adverse_reduce_anchor = None

                        equity_curve.append({'time': timestamp.strftime('%Y-%m-%d %H:%M'), 'value': round(capital, 2)})
                        continue

                if position_type == 'short' and position < 0:
                    shares = abs(position)
                    if stop_loss_pct_eff > 0:
                        sl_price = entry_price * (1 + stop_loss_pct_eff)
                        if high >= sl_price:
                            candidates.append(('close_short_stop', sl_price))
                    # Fixed take-profit exit is disabled when trailing is enabled (see conflict rule above).
                    if (not trailing_enabled) and take_profit_pct_eff > 0:
                        tp_price = entry_price * (1 - take_profit_pct_eff)
                        if low <= tp_price:
                            candidates.append(('close_short_profit', tp_price))
                    if trailing_enabled and trailing_pct_eff > 0 and lowest_since_entry is not None:
                        trail_active = True
                        if trailing_activation_pct_eff > 0:
                            trail_active = lowest_since_entry <= entry_price * (1 - trailing_activation_pct_eff)
                        if trail_active:
                            tr_price = lowest_since_entry * (1 + trailing_pct_eff)
                            tr_exec_price = tr_price * (1 + slippage)
                            if high >= tr_price and trailing_exit_locks_net_profit(
                                "short",
                                entry_price=entry_price,
                                exit_price=tr_exec_price,
                                fee_rate=commission,
                            ):
                                candidates.append(('close_short_trailing', tr_price))

                    if candidates:
                        # Select by priority: SL > Trailing > TP
                        pri = {'close_short_stop': 0, 'close_short_trailing': 1, 'close_short_profit': 2}
                        trade_type, trigger_price = sorted(candidates, key=lambda x: (pri.get(x[0], 99), -x[1]))[0]
                        exec_price_close = trigger_price * (1 + slippage)
                        commission_fee_close = shares * exec_price_close * commission
                        # Entry commission deducted, only deduct exit commission
                        profit = (entry_price - exec_price_close) * shares - commission_fee_close

                        if capital + profit <= 0:
                            liquidation_loss = self._liquidation_loss(capital)
                            capital = 0
                            is_liquidated = True
                            trades.append({
                                'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                'type': 'liquidation',
                                'price': round(exec_price_close, 4),
                                'amount': round(shares, 4),
                                'profit': liquidation_loss,
                                'balance': 0
                            })
                            position = 0
                            position_type = None
                            liquidation_price = 0
                            equity_curve.append({'time': timestamp.strftime('%Y-%m-%d %H:%M'), 'value': 0})
                            continue

                        capital += profit
                        total_commission_paid += commission_fee_close

                        trades.append({
                            'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                            'type': trade_type,
                            'price': round(exec_price_close, 4),
                            'amount': round(shares, 4),
                            'profit': round(profit, 2),
                            'balance': round(max(0, capital), 2)
                        })

                        position = 0
                        position_type = None
                        liquidation_price = 0
                        highest_since_entry = None
                        lowest_since_entry = None
                        trend_add_times = dca_add_times = trend_reduce_times = adverse_reduce_times = 0
                        last_trend_add_anchor = last_dca_add_anchor = last_trend_reduce_anchor = last_adverse_reduce_anchor = None

                        equity_curve.append({'time': timestamp.strftime('%Y-%m-%d %H:%M'), 'value': round(capital, 2)})
                        continue
            
            # Handle exit signals (priority, SL/TP)
            if position > 0 and close_long_arr[i]:
                # Close long: use indicator price or close
                if signal_timing in ['next_bar_open', 'next_open', 'nextopen', 'next']:
                    target_price = open_
                else:
                    target_price = close_long_price_arr[i] if close_long_price_arr[i] > 0 else close
                exec_price = target_price * (1 - slippage)
                commission_fee = position * exec_price * commission
                profit = (exec_price - entry_price) * position - commission_fee
                capital += profit
                total_commission_paid += commission_fee

                # NOTE:
                # This is a "signal close" (not a forced stop-loss/take-profit/trailing exit).
                # Do NOT label it as *_stop/*_profit based on PnL sign, otherwise it looks like a stop-loss happened
                # even when risk controls are disabled (stopLossPct/takeProfitPct == 0).
                trade_type = 'close_long'

                trades.append({
                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                    'type': trade_type,
                    'price': round(exec_price, 4),
                    'amount': round(position, 4),
                    'profit': round(profit, 2),
                    'balance': round(max(0, capital), 2)
                })
                
                position = 0
                position_type = None
                liquidation_price = 0
                highest_since_entry = None
                lowest_since_entry = None
                trend_add_times = dca_add_times = trend_reduce_times = adverse_reduce_times = 0
                last_trend_add_anchor = last_dca_add_anchor = last_trend_reduce_anchor = last_adverse_reduce_anchor = None

                # Stop if balance too low after exit
                if capital < min_capital_to_trade:
                    is_liquidated = True
                    liquidation_loss = self._liquidation_loss(capital)
                    capital = 0
                    trades.append({
                        'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                        'type': 'liquidation',
                        'price': round(exec_price, 4),
                        'amount': 0,
                        'profit': liquidation_loss,
                        'balance': 0
                    })
            
            elif position < 0 and close_short_arr[i]:
                # Close short: use indicator price or close
                if signal_timing in ['next_bar_open', 'next_open', 'nextopen', 'next']:
                    target_price = open_
                else:
                    target_price = close_short_price_arr[i] if close_short_price_arr[i] > 0 else close
                exec_price = target_price * (1 + slippage)
                shares = abs(position)
                commission_fee = shares * exec_price * commission
                profit = (entry_price - exec_price) * shares - commission_fee
                
                if capital + profit <= 0:
                    logger.warning(f"Insufficient funds when closing short - liquidation")
                    liquidation_loss = self._liquidation_loss(capital)
                    capital = 0
                    is_liquidated = True
                    trades.append({
                        'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                        'type': 'liquidation',
                        'price': round(exec_price, 4),
                        'amount': round(shares, 4),
                        'profit': liquidation_loss,
                        'balance': 0
                    })
                    position = 0
                    position_type = None
                    equity_curve.append({'time': timestamp.strftime('%Y-%m-%d %H:%M'), 'value': 0})
                    continue
                
                capital += profit
                total_commission_paid += commission_fee

                # Signal close (not forced TP/SL/trailing).
                trade_type = 'close_short'

                trades.append({
                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                    'type': trade_type,
                    'price': round(exec_price, 4),
                    'amount': round(shares, 4),
                    'profit': round(profit, 2),
                    'balance': round(max(0, capital), 2)
                })
                
                position = 0
                position_type = None
                liquidation_price = 0
                highest_since_entry = None
                lowest_since_entry = None
                trend_add_times = dca_add_times = trend_reduce_times = adverse_reduce_times = 0
                last_trend_add_anchor = last_dca_add_anchor = last_trend_reduce_anchor = last_adverse_reduce_anchor = None

                if capital < min_capital_to_trade:
                    is_liquidated = True
                    liquidation_loss = self._liquidation_loss(capital)
                    capital = 0
                    trades.append({
                        'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                        'type': 'liquidation',
                        'price': round(exec_price, 4),
                        'amount': 0,
                        'profit': liquidation_loss,
                        'balance': 0
                    })
            
            # If this candle has a main strategy signal (open/close long/short),
            # we must NOT apply any scale-in/scale-out actions on the same candle.
            main_signal_on_bar = bool(open_long_arr[i] or open_short_arr[i] or close_long_arr[i] or close_short_arr[i])

            # --- Parameterized scaling rules (no strategy code needed) ---
            # Rules:
            # - Trend scale-in: long triggers when price rises stepPct from anchor; short triggers when price falls stepPct from anchor
            # - Mean-reversion DCA: long triggers when price falls stepPct from anchor; short triggers when price rises stepPct from anchor
            # - Trend reduce: long reduces on rise; short reduces on fall
            # - Adverse reduce: long reduces on fall; short reduces on rise
            if (not main_signal_on_bar) and position != 0 and position_type in ['long', 'short'] and capital >= min_capital_to_trade:
                # Long
                if position_type == 'long' and position > 0:
                    # Trend scale-in (trigger on higher price)
                    if trend_add_enabled and trend_add_step_pct_eff > 0 and trend_add_size_pct > 0 and (trend_add_max_times == 0 or trend_add_times < trend_add_max_times):
                        anchor = last_trend_add_anchor if last_trend_add_anchor is not None else entry_price
                        trigger = anchor * (1 + trend_add_step_pct_eff)
                        if high >= trigger:
                            order_pct = trend_add_size_pct
                            if order_pct > 0:
                                exec_price_add = trigger * (1 + slippage)
                                use_capital = capital * order_pct
                                # Commission from notional value
                                shares_add = (use_capital * leverage) / exec_price_add
                                commission_fee = shares_add * exec_price_add * commission

                                total_cost_before = position * entry_price
                                total_cost_after = total_cost_before + shares_add * exec_price_add
                                position += shares_add
                                entry_price = total_cost_after / position

                                capital -= commission_fee
                                total_commission_paid += commission_fee
                                liquidation_price = entry_price * (1 - 1.0 / leverage)

                                trend_add_times += 1
                                last_trend_add_anchor = trigger

                                trades.append({
                                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                    'type': 'add_long',
                                    'price': round(exec_price_add, 4),
                                    'amount': round(shares_add, 4),
                                    'profit': 0,
                                    'balance': round(max(0, capital), 2)
                                })

                    # Mean-reversion DCA (trigger on lower price)
                    if dca_add_enabled and dca_add_step_pct_eff > 0 and dca_add_size_pct > 0 and (dca_add_max_times == 0 or dca_add_times < dca_add_max_times):
                        anchor = last_dca_add_anchor if last_dca_add_anchor is not None else entry_price
                        trigger = anchor * (1 - dca_add_step_pct_eff)
                        if low <= trigger:
                            order_pct = dca_add_size_pct
                            if order_pct > 0:
                                exec_price_add = trigger * (1 + slippage)
                                use_capital = capital * order_pct
                                shares_add = (use_capital * leverage) / exec_price_add
                                commission_fee = shares_add * exec_price_add * commission

                                total_cost_before = position * entry_price
                                total_cost_after = total_cost_before + shares_add * exec_price_add
                                position += shares_add
                                entry_price = total_cost_after / position

                                capital -= commission_fee
                                total_commission_paid += commission_fee
                                liquidation_price = entry_price * (1 - 1.0 / leverage)

                                dca_add_times += 1
                                last_dca_add_anchor = trigger

                                trades.append({
                                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                    'type': 'add_long',
                                    'price': round(exec_price_add, 4),
                                    'amount': round(shares_add, 4),
                                    'profit': 0,
                                    'balance': round(max(0, capital), 2)
                                })

                    # Trend reduce (trigger on higher price)
                    if trend_reduce_enabled and trend_reduce_step_pct_eff > 0 and trend_reduce_size_pct > 0 and (trend_reduce_max_times == 0 or trend_reduce_times < trend_reduce_max_times):
                        anchor = last_trend_reduce_anchor if last_trend_reduce_anchor is not None else entry_price
                        trigger = anchor * (1 + trend_reduce_step_pct_eff)
                        if high >= trigger:
                            reduce_pct = max(trend_reduce_size_pct, 0.0)
                            reduce_shares = position * reduce_pct
                            if reduce_shares > 0:
                                exec_price_reduce = trigger * (1 - slippage)
                                commission_fee = reduce_shares * exec_price_reduce * commission
                                profit = (exec_price_reduce - entry_price) * reduce_shares - commission_fee
                                capital += profit
                                total_commission_paid += commission_fee
                                position -= reduce_shares
                                if position <= 1e-12:
                                    position = 0
                                    position_type = None
                                    liquidation_price = 0
                                else:
                                    liquidation_price = entry_price * (1 - 1.0 / leverage)

                                trend_reduce_times += 1
                                last_trend_reduce_anchor = trigger

                                trades.append({
                                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                    'type': 'reduce_long',
                                    'price': round(exec_price_reduce, 4),
                                    'amount': round(reduce_shares, 4),
                                    'profit': round(profit, 2),
                                    'balance': round(max(0, capital), 2)
                                })

                    # Adverse reduce (trigger on lower price)
                    if position_type == 'long' and position > 0 and adverse_reduce_enabled and adverse_reduce_step_pct_eff > 0 and adverse_reduce_size_pct > 0 and (adverse_reduce_max_times == 0 or adverse_reduce_times < adverse_reduce_max_times):
                        anchor = last_adverse_reduce_anchor if last_adverse_reduce_anchor is not None else entry_price
                        trigger = anchor * (1 - adverse_reduce_step_pct_eff)
                        if low <= trigger:
                            reduce_pct = max(adverse_reduce_size_pct, 0.0)
                            reduce_shares = position * reduce_pct
                            if reduce_shares > 0:
                                exec_price_reduce = trigger * (1 - slippage)
                                commission_fee = reduce_shares * exec_price_reduce * commission
                                profit = (exec_price_reduce - entry_price) * reduce_shares - commission_fee
                                capital += profit
                                total_commission_paid += commission_fee
                                position -= reduce_shares
                                if position <= 1e-12:
                                    position = 0
                                    position_type = None
                                    liquidation_price = 0
                                else:
                                    liquidation_price = entry_price * (1 - 1.0 / leverage)

                                adverse_reduce_times += 1
                                last_adverse_reduce_anchor = trigger

                                trades.append({
                                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                    'type': 'reduce_long',
                                    'price': round(exec_price_reduce, 4),
                                    'amount': round(reduce_shares, 4),
                                    'profit': round(profit, 2),
                                    'balance': round(max(0, capital), 2)
                                })

                # Short
                if position_type == 'short' and position < 0:
                    shares_total = abs(position)

                    # Trend scale-in (trigger on lower price)
                    if trend_add_enabled and trend_add_step_pct_eff > 0 and trend_add_size_pct > 0 and (trend_add_max_times == 0 or trend_add_times < trend_add_max_times):
                        anchor = last_trend_add_anchor if last_trend_add_anchor is not None else entry_price
                        trigger = anchor * (1 - trend_add_step_pct_eff)
                        if low <= trigger:
                            order_pct = trend_add_size_pct
                            if order_pct > 0:
                                exec_price_add = trigger * (1 - slippage)  # Sell to add short, slippage unfavorable
                                use_capital = capital * order_pct
                                shares_add = (use_capital * leverage) / exec_price_add
                                commission_fee = shares_add * exec_price_add * commission

                                total_cost_before = shares_total * entry_price
                                total_cost_after = total_cost_before + shares_add * exec_price_add
                                position -= shares_add
                                shares_total = abs(position)
                                entry_price = total_cost_after / shares_total

                                capital -= commission_fee
                                total_commission_paid += commission_fee
                                liquidation_price = entry_price * (1 + 1.0 / leverage)

                                trend_add_times += 1
                                last_trend_add_anchor = trigger

                                trades.append({
                                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                    'type': 'add_short',
                                    'price': round(exec_price_add, 4),
                                    'amount': round(shares_add, 4),
                                    'profit': 0,
                                    'balance': round(max(0, capital), 2)
                                })

                    # Mean-reversion DCA (trigger on higher price)
                    if dca_add_enabled and dca_add_step_pct_eff > 0 and dca_add_size_pct > 0 and (dca_add_max_times == 0 or dca_add_times < dca_add_max_times):
                        anchor = last_dca_add_anchor if last_dca_add_anchor is not None else entry_price
                        trigger = anchor * (1 + dca_add_step_pct_eff)
                        if high >= trigger:
                            order_pct = dca_add_size_pct
                            if order_pct > 0:
                                exec_price_add = trigger * (1 - slippage)
                                use_capital = capital * order_pct
                                shares_add = (use_capital * leverage) / exec_price_add
                                commission_fee = shares_add * exec_price_add * commission

                                total_cost_before = shares_total * entry_price
                                total_cost_after = total_cost_before + shares_add * exec_price_add
                                position -= shares_add
                                shares_total = abs(position)
                                entry_price = total_cost_after / shares_total

                                capital -= commission_fee
                                total_commission_paid += commission_fee
                                liquidation_price = entry_price * (1 + 1.0 / leverage)

                                dca_add_times += 1
                                last_dca_add_anchor = trigger

                                trades.append({
                                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                    'type': 'add_short',
                                    'price': round(exec_price_add, 4),
                                    'amount': round(shares_add, 4),
                                    'profit': 0,
                                    'balance': round(max(0, capital), 2)
                                })

                    # Trend reduce (trigger on lower price)
                    if trend_reduce_enabled and trend_reduce_step_pct_eff > 0 and trend_reduce_size_pct > 0 and (trend_reduce_max_times == 0 or trend_reduce_times < trend_reduce_max_times):
                        anchor = last_trend_reduce_anchor if last_trend_reduce_anchor is not None else entry_price
                        trigger = anchor * (1 - trend_reduce_step_pct_eff)
                        if low <= trigger:
                            reduce_pct = max(trend_reduce_size_pct, 0.0)
                            reduce_shares = shares_total * reduce_pct
                            if reduce_shares > 0:
                                exec_price_reduce = trigger * (1 + slippage)  # Cover more expensive
                                commission_fee = reduce_shares * exec_price_reduce * commission
                                profit = (entry_price - exec_price_reduce) * reduce_shares - commission_fee
                                capital += profit
                                total_commission_paid += commission_fee
                                position += reduce_shares
                                shares_total = abs(position)
                                if shares_total <= 1e-12:
                                    position = 0
                                    position_type = None
                                    liquidation_price = 0
                                else:
                                    liquidation_price = entry_price * (1 + 1.0 / leverage)

                                trend_reduce_times += 1
                                last_trend_reduce_anchor = trigger

                                trades.append({
                                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                    'type': 'reduce_short',
                                    'price': round(exec_price_reduce, 4),
                                    'amount': round(reduce_shares, 4),
                                    'profit': round(profit, 2),
                                    'balance': round(max(0, capital), 2)
                                })

                    # Adverse reduce (trigger on higher price)
                    if position_type == 'short' and position < 0 and adverse_reduce_enabled and adverse_reduce_step_pct_eff > 0 and adverse_reduce_size_pct > 0 and (adverse_reduce_max_times == 0 or adverse_reduce_times < adverse_reduce_max_times):
                        anchor = last_adverse_reduce_anchor if last_adverse_reduce_anchor is not None else entry_price
                        trigger = anchor * (1 + adverse_reduce_step_pct_eff)
                        if high >= trigger:
                            reduce_pct = max(adverse_reduce_size_pct, 0.0)
                            reduce_shares = shares_total * reduce_pct
                            if reduce_shares > 0:
                                exec_price_reduce = trigger * (1 + slippage)
                                commission_fee = reduce_shares * exec_price_reduce * commission
                                profit = (entry_price - exec_price_reduce) * reduce_shares - commission_fee
                                capital += profit
                                total_commission_paid += commission_fee
                                position += reduce_shares
                                shares_total = abs(position)
                                if shares_total <= 1e-12:
                                    position = 0
                                    position_type = None
                                    liquidation_price = 0
                                else:
                                    liquidation_price = entry_price * (1 + 1.0 / leverage)

                                adverse_reduce_times += 1
                                last_adverse_reduce_anchor = trigger

                                trades.append({
                                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                    'type': 'reduce_short',
                                    'price': round(exec_price_reduce, 4),
                                    'amount': round(reduce_shares, 4),
                                    'profit': round(profit, 2),
                                    'balance': round(max(0, capital), 2)
                                })

            # Handle add position signals
            if has_position_management and (not main_signal_on_bar):
                if position > 0 and add_long_arr[i] and capital >= min_capital_to_trade:
                    # Add long: use indicator price or close
                    target_price = add_long_price_arr[i] if add_long_price_arr[i] > 0 else close
                    exec_price = target_price * (1 + slippage)
                    
                    # Use specified pct to add
                    position_pct = position_size_arr[i] if position_size_arr[i] > 0 else 0.1
                    use_capital = capital * position_pct
                    shares = (use_capital * leverage) / exec_price
                    commission_fee = shares * exec_price * commission
                    
                    # Update average cost
                    total_cost_before = position * entry_price
                    total_cost_after = total_cost_before + shares * exec_price
                    position += shares
                    entry_price = total_cost_after / position
                    
                    capital -= commission_fee
                    total_commission_paid += commission_fee
                    
                    # Recalculate liquidation price
                    liquidation_price = entry_price * (1 - 1.0 / leverage)
                    
                    trades.append({
                        'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                        'type': 'add_long',
                        'price': round(exec_price, 4),
                        'amount': round(shares, 4),
                        'profit': 0,
                        'balance': round(max(0, capital), 2)
                    })
                
                elif position < 0 and add_short_arr[i] and capital >= min_capital_to_trade:
                    # Add short: use indicator price or close
                    target_price = add_short_price_arr[i] if add_short_price_arr[i] > 0 else close
                    exec_price = target_price * (1 - slippage)
                    
                    # Use specified pct to add
                    position_pct = position_size_arr[i] if position_size_arr[i] > 0 else 0.1
                    use_capital = capital * position_pct
                    shares = (use_capital * leverage) / exec_price
                    commission_fee = shares * exec_price * commission
                    
                    # Update average cost
                    current_shares = abs(position)
                    total_cost_before = current_shares * entry_price
                    total_cost_after = total_cost_before + shares * exec_price
                    position -= shares  # Short is negative
                    current_shares = abs(position)
                    entry_price = total_cost_after / current_shares
                    
                    capital -= commission_fee
                    total_commission_paid += commission_fee
                    
                    # Recalculate liquidation price
                    liquidation_price = entry_price * (1 + 1.0 / leverage)
                    
                    trades.append({
                        'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                        'type': 'add_short',
                        'price': round(exec_price, 4),
                        'amount': round(shares, 4),
                        'profit': 0,
                        'balance': round(max(0, capital), 2)
                    })
            
            # Handle entry signals
            # In both mode, open_long/open_short can auto-close opposing position first
            both_mode_active = signals.get('_both_mode', False)
            
            # open_long: can execute when position==0, OR when both_mode and position<0 (auto-close short first)
            if open_long_arr[i] and (position == 0 or (both_mode_active and position < 0)) and capital >= min_capital_to_trade:
                    # In both mode with short position, close it first
                    if both_mode_active and position < 0:
                        shares_to_close = abs(position)
                        close_price = open_ * (1 + slippage)
                        close_commission = shares_to_close * close_price * commission
                        close_profit = (entry_price - close_price) * shares_to_close - close_commission
                        capital += close_profit
                        if capital < 0:
                            capital = 0
                        total_commission_paid += close_commission
                        trades.append({
                            'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                            'type': 'close_short',
                            'price': round(close_price, 4),
                            'amount': round(shares_to_close, 4),
                            'profit': round(close_profit, 2),
                            'balance': round(max(0, capital), 2)
                        })
                        position = 0
                        position_type = None
                        liquidation_price = 0
                        highest_since_entry = None
                        lowest_since_entry = None
                        trend_add_times = dca_add_times = trend_reduce_times = adverse_reduce_times = 0
                        last_trend_add_anchor = last_dca_add_anchor = last_trend_reduce_anchor = last_adverse_reduce_anchor = None
                        if capital < min_capital_to_trade:
                            is_liquidated = True
                            capital = 0
                            equity_curve.append({'time': timestamp.strftime('%Y-%m-%d %H:%M'), 'value': 0})
                            continue
                    
                    # Now open long (position is guaranteed to be 0 here)
                    # Use indicator entry price or close
                    if signal_timing in ['next_bar_open', 'next_open', 'nextopen', 'next']:
                        base_price = open_
                    else:
                        base_price = open_long_price_arr[i] if open_long_price_arr[i] > 0 else close
                    exec_price = base_price * (1 + slippage)
                    
                    # Use specified pct (entryPct > position_size > full)
                    position_pct = None
                    if entry_pct_cfg and entry_pct_cfg > 0:
                        position_pct = entry_pct_cfg
                    elif has_position_management and position_size_arr[i] > 0:
                        position_pct = position_size_arr[i]
                    if position_pct is not None and position_pct > 0 and position_pct < 1:
                        use_capital = capital * position_pct
                        shares = (use_capital * leverage) / exec_price
                    else:
                        shares = (capital * leverage) / exec_price
                    
                    commission_fee = shares * exec_price * commission
                    
                    position = shares
                    entry_price = exec_price
                    position_type = 'long'
                    capital -= commission_fee
                    total_commission_paid += commission_fee
                    liquidation_price = entry_price * (1 - 1.0 / leverage)
                    highest_since_entry = entry_price
                    lowest_since_entry = entry_price
                    last_trend_add_anchor = entry_price
                    last_dca_add_anchor = entry_price
                    last_trend_reduce_anchor = entry_price
                    last_adverse_reduce_anchor = entry_price
                    
                    trades.append({
                        'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                        'type': 'open_long',
                        'price': round(exec_price, 4),
                        'amount': round(shares, 4),
                        'profit': 0,
                        'balance': round(max(0, capital), 2)
                    })
                    
                    # Strict intrabar stop-loss / liquidation check right after entry (closer to live trading).
                    # If this bar touches stop-loss price, close immediately at stop price (with slippage).
                    # If this bar also touches liquidation price, assume stop-loss triggers first only if it is above liquidation.
                    if position_type == 'long' and position > 0:
                        sl_price = entry_price * (1 - stop_loss_pct_eff) if stop_loss_pct_eff > 0 else None
                        hit_sl = (sl_price is not None) and (low <= sl_price)
                        hit_liq = liquidation_price > 0 and (low <= liquidation_price)
                        if hit_sl or hit_liq:
                            if hit_liq and (not hit_sl or (sl_price is not None and sl_price <= liquidation_price)):
                                # Liquidation happens before stop-loss (or stop-loss not configured).
                                is_liquidated = True
                                liquidation_loss = self._liquidation_loss(capital)
                                capital = 0
                                trades.append({
                                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                    'type': 'liquidation',
                                    'price': round(liquidation_price, 4),
                                    'amount': round(position, 4),
                                    'profit': liquidation_loss,
                                    'balance': 0
                                })
                            else:
                                # Stop-loss triggers first.
                                exec_price_close = sl_price * (1 - slippage)
                                commission_fee_close = position * exec_price_close * commission
                                profit = (exec_price_close - entry_price) * position - commission_fee_close
                                capital += profit
                                total_commission_paid += commission_fee_close
                                if capital <= 0:
                                    is_liquidated = True
                                    capital = 0
                                trades.append({
                                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                    'type': 'close_long_stop',
                                    'price': round(exec_price_close, 4),
                                    'amount': round(position, 4),
                                    'profit': round(profit, 2),
                                    'balance': round(max(0, capital), 2)
                                })

                            position = 0
                            position_type = None
                            liquidation_price = 0
                            highest_since_entry = None
                            lowest_since_entry = None
                            equity_curve.append({'time': timestamp.strftime('%Y-%m-%d %H:%M'), 'value': round(capital, 2)})
                            continue
            
            # open_short: can execute when position==0, OR when both_mode and position>0 (auto-close long first)
            elif open_short_arr[i] and (position == 0 or (both_mode_active and position > 0)) and capital >= min_capital_to_trade:
                    # In both mode with long position, close it first
                    if both_mode_active and position > 0:
                        close_price = open_ * (1 - slippage)
                        close_commission = position * close_price * commission
                        close_profit = (close_price - entry_price) * position - close_commission
                        capital += close_profit
                        if capital < 0:
                            capital = 0
                        total_commission_paid += close_commission
                        trades.append({
                            'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                            'type': 'close_long',
                            'price': round(close_price, 4),
                            'amount': round(position, 4),
                            'profit': round(close_profit, 2),
                            'balance': round(max(0, capital), 2)
                        })
                        position = 0
                        position_type = None
                        liquidation_price = 0
                        highest_since_entry = None
                        lowest_since_entry = None
                        trend_add_times = dca_add_times = trend_reduce_times = adverse_reduce_times = 0
                        last_trend_add_anchor = last_dca_add_anchor = last_trend_reduce_anchor = last_adverse_reduce_anchor = None
                        if capital < min_capital_to_trade:
                            is_liquidated = True
                            capital = 0
                            equity_curve.append({'time': timestamp.strftime('%Y-%m-%d %H:%M'), 'value': 0})
                            continue
                    
                    # Now open short (position is guaranteed to be 0 here)
                    # Use indicator entry price or close
                    if signal_timing in ['next_bar_open', 'next_open', 'nextopen', 'next']:
                        base_price = open_
                    else:
                        base_price = open_short_price_arr[i] if open_short_price_arr[i] > 0 else close
                    exec_price = base_price * (1 - slippage)
                    
                    # Use specified pct (entryPct > position_size > full)
                    position_pct = None
                    if entry_pct_cfg and entry_pct_cfg > 0:
                        position_pct = entry_pct_cfg
                    elif has_position_management and position_size_arr[i] > 0:
                        position_pct = position_size_arr[i]
                    if position_pct is not None and position_pct > 0 and position_pct < 1:
                        use_capital = capital * position_pct
                        shares = (use_capital * leverage) / exec_price
                    else:
                        shares = (capital * leverage) / exec_price
                    
                    commission_fee = shares * exec_price * commission
                    
                    position = -shares
                    entry_price = exec_price
                    position_type = 'short'
                    capital -= commission_fee
                    total_commission_paid += commission_fee
                    liquidation_price = entry_price * (1 + 1.0 / leverage)
                    highest_since_entry = entry_price
                    lowest_since_entry = entry_price
                    last_trend_add_anchor = entry_price
                    last_dca_add_anchor = entry_price
                    last_trend_reduce_anchor = entry_price
                    last_adverse_reduce_anchor = entry_price
                    
                    trades.append({
                        'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                        'type': 'open_short',
                        'price': round(exec_price, 4),
                        'amount': round(shares, 4),
                        'profit': 0,
                        'balance': round(max(0, capital), 2)
                    })
                    
                    # Strict intrabar stop-loss / liquidation check right after entry (closer to live trading).
                    if position_type == 'short' and position < 0:
                        sl_price = entry_price * (1 + stop_loss_pct_eff) if stop_loss_pct_eff > 0 else None
                        hit_sl = (sl_price is not None) and (high >= sl_price)
                        hit_liq = liquidation_price > 0 and (high >= liquidation_price)
                        if hit_sl or hit_liq:
                            if hit_liq and (not hit_sl or (sl_price is not None and sl_price >= liquidation_price)):
                                # Liquidation happens before stop-loss (or stop-loss not configured).
                                is_liquidated = True
                                liquidation_loss = self._liquidation_loss(capital)
                                capital = 0
                                trades.append({
                                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                    'type': 'liquidation',
                                    'price': round(liquidation_price, 4),
                                    'amount': round(abs(position), 4),
                                    'profit': liquidation_loss,
                                    'balance': 0
                                })
                            else:
                                # Stop-loss triggers first.
                                exec_price_close = sl_price * (1 + slippage)
                                shares_close = abs(position)
                                commission_fee_close = shares_close * exec_price_close * commission
                                profit = (entry_price - exec_price_close) * shares_close - commission_fee_close
                                capital += profit
                                total_commission_paid += commission_fee_close
                                if capital <= 0:
                                    is_liquidated = True
                                    capital = 0
                                trades.append({
                                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                                    'type': 'close_short_stop',
                                    'price': round(exec_price_close, 4),
                                    'amount': round(shares_close, 4),
                                    'profit': round(profit, 2),
                                    'balance': round(max(0, capital), 2)
                                })

                            position = 0
                            position_type = None
                            liquidation_price = 0
                            highest_since_entry = None
                            lowest_since_entry = None
                            equity_curve.append({'time': timestamp.strftime('%Y-%m-%d %H:%M'), 'value': round(capital, 2)})
                            continue
            
            # Check if liquidation hit (safety net)
            # Note: check after all active exit signals
            # If liquidation hit, check SL signal first
            if position != 0 and not is_liquidated:
                if position_type == 'long' and low <= liquidation_price:
                    has_stop_loss = close_long_arr[i] and close_long_price_arr[i] > 0
                    stop_loss_price = close_long_price_arr[i] if has_stop_loss else 0
                    
                    # Determine SL or liquidation first
                    if has_stop_loss and stop_loss_price > liquidation_price:
                        # SL triggers before liquidation
                        exec_price_close = stop_loss_price * (1 - slippage)
                        commission_fee_close = position * exec_price_close * commission
                        profit = (exec_price_close - entry_price) * position - commission_fee_close
                        capital += profit
                        total_commission_paid += commission_fee_close
                        
                        trades.append({
                            'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                            'type': 'close_long_stop',
                            'price': round(exec_price_close, 4),
                            'amount': round(position, 4),
                            'profit': round(profit, 2),
                            'balance': round(max(0, capital), 2)
                        })
                    else:
                        # SL not strict enough, liquidation triggered
                        logger.warning(f"Long liquidation! entry={entry_price:.2f}, low={low:.2f}, "
                                     f"爆仓线={liquidation_price:.2f}, 止损价={stop_loss_price:.2f}")
                        is_liquidated = True
                        liquidation_loss = self._liquidation_loss(capital)
                        capital = 0
                        trades.append({
                            'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                            'type': 'liquidation',
                            'price': round(liquidation_price, 4),
                            'amount': round(abs(position), 4),
                            'profit': liquidation_loss,
                            'balance': 0
                        })
                    
                    position = 0
                    position_type = None
                    equity_curve.append({'time': timestamp.strftime('%Y-%m-%d %H:%M'), 'value': capital})
                    continue
                    
                elif position_type == 'short' and high >= liquidation_price:
                    has_stop_loss = close_short_arr[i] and close_short_price_arr[i] > 0
                    stop_loss_price = close_short_price_arr[i] if has_stop_loss else 0
                    
                    logger.warning(f"[candle {i}] Short hit liquidation! entry={entry_price:.2f}, high={high:.2f}, liq_price={liquidation_price:.2f}, "
                              f"止损信号={close_short_arr[i]}, 止损价={stop_loss_price:.4f}, 时间={timestamp}")
                    
                    # Determine SL or liquidation first
                    if has_stop_loss and stop_loss_price < liquidation_price:
                        # SL triggers before liquidation
                        exec_price_close = stop_loss_price * (1 + slippage)
                        shares_close = abs(position)
                        commission_fee_close = shares_close * exec_price_close * commission
                        profit = (entry_price - exec_price_close) * shares_close - commission_fee_close
                        capital += profit
                        total_commission_paid += commission_fee_close
                        
                        trades.append({
                            'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                            'type': 'close_short_stop',
                            'price': round(exec_price_close, 4),
                            'amount': round(shares_close, 4),
                            'profit': round(profit, 2),
                            'balance': round(max(0, capital), 2)
                        })
                    else:
                        # SL not strict enough, liquidation triggered
                        logger.warning(f"Short liquidation! entry={entry_price:.2f}, high={high:.2f}, "
                                     f"爆仓线={liquidation_price:.2f}, 止损价={stop_loss_price:.2f}")
                        is_liquidated = True
                        liquidation_loss = self._liquidation_loss(capital)
                        capital = 0
                        trades.append({
                            'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                            'type': 'liquidation',
                            'price': round(liquidation_price, 4),
                            'amount': round(abs(position), 4),
                            'profit': liquidation_loss,
                            'balance': 0
                        })
                    
                    position = 0
                    position_type = None
                    equity_curve.append({'time': timestamp.strftime('%Y-%m-%d %H:%M'), 'value': capital})
                    continue
            
            # Record equity (unrealized PnL from close)
            if position_type == 'long':
                unrealized_pnl = (close - entry_price) * position
                total_value = capital + unrealized_pnl
            elif position_type == 'short':
                shares = abs(position)
                unrealized_pnl = (entry_price - close) * shares
                total_value = capital + unrealized_pnl
            else:
                total_value = capital
            
            if total_value < 0:
                total_value = 0
            
            equity_curve.append({
                'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                'value': round(total_value, 2)
            })
        
        # Force exit at backtest end
        if position != 0:
            timestamp = df.index[-1]
            final_close = df.iloc[-1]['close']
            
            if position > 0:  # Close long
                exec_price = final_close * (1 - slippage)
                commission_fee = position * exec_price * commission
                profit = (exec_price - entry_price) * position - commission_fee
                capital += profit
                total_commission_paid += commission_fee
                
                trades.append({
                    'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                    'type': 'close_long',
                    'price': round(exec_price, 4),
                    'amount': round(position, 4),
                    'profit': round(profit, 2),
                    'balance': round(max(0, capital), 2)
                })
            else:  # Close short
                exec_price = final_close * (1 + slippage)
                shares = abs(position)
                commission_fee = shares * exec_price * commission
                profit = (entry_price - exec_price) * shares - commission_fee
                
                if capital + profit <= 0:
                    logger.warning(f"Liquidation at backtest end!")
                    liquidation_loss = self._liquidation_loss(capital)
                    capital = 0
                    is_liquidated = True
                    trades.append({
                        'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                        'type': 'liquidation',
                        'price': round(exec_price, 4),
                        'amount': round(shares, 4),
                        'profit': liquidation_loss,
                        'balance': 0
                    })
                else:
                    capital += profit
                    total_commission_paid += commission_fee
                    trades.append({
                        'time': timestamp.strftime('%Y-%m-%d %H:%M'),
                        'type': 'close_short',
                        'price': round(exec_price, 4),
                        'amount': round(shares, 4),
                        'profit': round(profit, 2),
                        'balance': round(max(0, capital), 2)
                    })
            
            if equity_curve:
                equity_curve[-1]['value'] = round(capital, 2)
        
        return equity_curve, trades, total_commission_paid
    
    def _calculate_metrics(
        self,
        equity_curve: List,
        trades: List,
        initial_capital: float,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
        total_commission: float = 0
    ) -> Dict:
        """计算回测指标"""
        if not equity_curve:
            return {}
        
        final_value = equity_curve[-1]['value']
        total_return = (final_value - initial_capital) / initial_capital * 100
        
        # Calculate annualized return: simple, not compound
        # For high-return strategies, compound annualization produces unrealistic numbers
        # Use actual data time range from equity_curve instead of requested start_date/end_date
        # This fixes the issue where data may only be available until a certain date (e.g., TSLA only to January)
        try:
            # Parse actual start and end times from equity_curve
            actual_start_str = equity_curve[0]['time']
            actual_end_str = equity_curve[-1]['time']
            actual_start = datetime.strptime(actual_start_str, '%Y-%m-%d %H:%M')
            actual_end = datetime.strptime(actual_end_str, '%Y-%m-%d %H:%M')
            actual_days = (actual_end - actual_start).total_seconds() / 86400
        except (KeyError, ValueError, IndexError) as e:
            # Fallback to requested date range if parsing fails
            logger.warning(f"Failed to parse actual time range from equity_curve: {e}, using requested range")
            actual_days = (end_date - start_date).total_seconds() / 86400
        
        years = actual_days / 365.0
        
        # Simple annualization: annualized return = total return / years
        if years > 0:
            annual_return = total_return / years
        else:
            annual_return = 0
        
        # Calculate max drawdown
        values = [e['value'] for e in equity_curve]
        max_drawdown = self._calculate_max_drawdown(values)
        
        # Calculate Sharpe ratio
        sharpe = self._calculate_sharpe(values, timeframe)
        
        # Calculate total PnL: final equity - initial capital (most accurate)
        total_profit = final_value - initial_capital
        
        # Calculate win rate (all exit trades)
        # Exit trades: trades with profit != 0
        closing_trades = [t for t in trades if t.get('profit', 0) != 0]
        win_trades = [t for t in closing_trades if t['profit'] > 0]
        loss_trades = [t for t in closing_trades if t['profit'] < 0]
        total_trades = len(closing_trades)
        win_rate = len(win_trades) / total_trades * 100 if total_trades > 0 else 0
        
        # Calculate profit factor (= total profit / total loss)
        total_wins = sum(t['profit'] for t in win_trades)
        total_losses = abs(sum(t['profit'] for t in loss_trades))
        profit_factor = total_wins / total_losses if total_losses > 0 else (total_wins if total_wins > 0 else 0)
        
        return {
            'totalReturn': round(total_return, 2),
            'annualReturn': round(annual_return, 2),
            'maxDrawdown': round(max_drawdown, 2),
            'sharpeRatio': round(sharpe, 2),
            'winRate': round(win_rate, 2),
            'profitFactor': round(profit_factor, 2),
            'totalTrades': total_trades,
            'totalProfit': round(total_profit, 2),
            'totalCommission': round(total_commission, 2)
        }
    
    def _calculate_max_drawdown(self, values: List[float]) -> float:
        """计算最大回撤"""
        if not values:
            return 0
        
        peak = values[0]
        max_dd = 0
        
        for value in values:
            if value > peak:
                peak = value
            dd = (peak - value) / peak * 100
            if dd > max_dd:
                max_dd = dd
        
        return -max_dd
    
    def _calculate_sharpe(self, values: List[float], timeframe: str = '1D', risk_free_rate: float = 0.02) -> float:
        """
        计算夏普比率
        
        Args:
            values: 权益曲线数值列表
            timeframe: 时间周期
            risk_free_rate: 无风险收益率（年化）
        """
        if len(values) < 2:
            return 0
        
        # Filter out zero values (post-liquidation data), avoid division by 0
        valid_values = [v for v in values if v > 0]
        if len(valid_values) < 2:
            return 0
        
        # Determine annualization factor by timeframe
        annualization_factor = {
            '1m': 252 * 24 * 60,      # 1m candle: ~362,880
            '5m': 252 * 24 * 12,      # 5分钟K：约72,576
            '15m': 252 * 24 * 4,      # 15分钟K：约24,192
            '30m': 252 * 24 * 2,      # 30分钟K：约12,096
            '1H': 252 * 24,           # 1H candle: 6,048
            '4H': 252 * 6,            # 4小时K：1,512
            '1D': 252,                # 1D candle: 252
            '1W': 52                  # 1W candle: 52
        }.get(timeframe, 252)
        
        try:
            # Calculate period returns
            returns = np.diff(valid_values) / valid_values[:-1]
            
            # Filter invalid values
            returns = returns[np.isfinite(returns)]
            if len(returns) == 0:
                return 0
            
            # Annualized mean return
            avg_return = np.mean(returns) * annualization_factor
            
            # Annualized std (volatility)
            std_return = np.std(returns) * np.sqrt(annualization_factor)
            
            if std_return == 0 or not np.isfinite(std_return):
                return 0
            
            # Sharpe ratio = (annualized return - risk-free rate) / annualized volatility
            sharpe = (avg_return - risk_free_rate) / std_return
            return sharpe if np.isfinite(sharpe) else 0
        except Exception as e:
            logger.warning(f"Sharpe ratio calculation failed: {e}")
            return 0
    
    def _execution_assumptions(
        self,
        strategy_config: Optional[Dict[str, Any]],
        *,
        simulation_mode: str,
        signal_timeframe: Optional[str] = None,
        execution_timeframe: Optional[str] = None,
        mtf_requested: bool = False,
        mtf_active: bool = False,
        mtf_fallback_reason: Optional[str] = None,
        commission: Optional[float] = None,
        slippage: Optional[float] = None,
        backtest_preset: Optional[str] = None,
        strict_mode_aligned: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Human-facing metadata so the UI can explain how trades were timed vs chart markers.
        Keys use camelCase for JSON consumers (frontend).
        """
        cfg = strategy_config or {}
        raw = str((cfg.get('execution') or {}).get('signalTiming') or 'next_bar_open').strip().lower()
        is_next_open = raw in ('next_bar_open', 'next_open', 'nextopen', 'next')
        if raw in ('bar_close', 'close', 'same_bar_close', 'current_bar_close'):
            timing_key = 'same_bar_close'
        elif is_next_open:
            timing_key = 'next_bar_open'
        else:
            timing_key = raw
        default_fill = 'open' if is_next_open else 'close'
        payload: Dict[str, Any] = {
            'signalTiming': timing_key,
            'signalTimingRaw': raw,
            'defaultFillPrice': default_fill,
            'simulationMode': simulation_mode,
            'strategyTimeframe': signal_timeframe,
            'executionTimeframe': execution_timeframe,
            'engineVersion': self.ENGINE_VERSION,
            'mtfRequested': bool(mtf_requested),
            'mtfActive': bool(mtf_active),
        }
        if mtf_fallback_reason:
            payload['mtfFallbackReason'] = mtf_fallback_reason
        try:
            if commission is not None:
                payload['commission'] = round(float(commission), 6)
        except (TypeError, ValueError):
            pass
        try:
            if slippage is not None:
                payload['slippage'] = round(float(slippage), 6)
        except (TypeError, ValueError):
            pass
        if backtest_preset:
            payload['backtestPreset'] = str(backtest_preset)
        if strict_mode_aligned is not None:
            payload['strictModeAligned'] = bool(strict_mode_aligned)
        return payload

    @staticmethod
    def _annotate_signal_bar_times(
        trades: List[Dict[str, Any]],
        signal_tf_seconds: int,
        signal_timing: str,
    ) -> None:
        """Backfill `signal_bar_time` onto every trade for chart double-display.

        Convention for the frontend overlay layer:
          * `bar_time`        = chart-aligned EXECUTION bar (what user sees as fill)
          * `signal_bar_time` = chart-aligned SIGNAL bar (where the rule fired)

        For pure signal-triggered open/close (no _stop/_profit/_trailing/liquidation
        suffix) under `next_bar_open`, signal bar is exactly one signal_tf BEFORE
        the execution bar. For SL/TP/trailing/liquidation triggers, there is no
        meaningful "signal bar" — they fire intra-bar from the price path — so we
        align signal_bar_time to bar_time so the renderer only draws a single marker.
        For `bar_close` execution mode, signal and execution coincide on the same bar.
        """
        if not trades:
            return
        delta_seconds = int(signal_tf_seconds) if signal_tf_seconds else 0
        use_offset = (
            delta_seconds > 0
            and str(signal_timing or '').strip().lower()
            in ('next_bar_open', 'next_open', 'nextopen', 'next')
        )
        delta = timedelta(seconds=delta_seconds) if use_offset else timedelta(0)
        for trade in trades:
            if 'signal_bar_time' in trade:
                continue
            bt = trade.get('bar_time') or trade.get('time')
            if not bt:
                trade['signal_bar_time'] = None
                continue
            ty = str(trade.get('type') or '').lower()
            price_path_trigger = (
                '_stop' in ty
                or '_profit' in ty
                or '_trailing' in ty
                or ty == 'liquidation'
            )
            if not use_offset or price_path_trigger:
                trade['signal_bar_time'] = bt
                continue
            try:
                bt_dt = pd.to_datetime(bt)
                trade['signal_bar_time'] = (bt_dt - delta).strftime('%Y-%m-%d %H:%M')
            except Exception:
                trade['signal_bar_time'] = bt

    def _format_result(
        self,
        metrics: Dict,
        equity_curve: List,
        trades: List
    ) -> Dict[str, Any]:
        """格式化回测结果"""
        # Simplify equity curve
        max_points = 500
        if len(equity_curve) > max_points:
            step = len(equity_curve) // max_points
            equity_curve = equity_curve[::step]
        
        # Clean NaN/Inf values for JSON serialization
        def clean_value(value):
            """清理数值，将NaN/Inf转换为0"""
            if isinstance(value, float):
                if np.isnan(value) or np.isinf(value):
                    return 0
            return value
        
        # Clean metrics
        cleaned_metrics = {}
        for key, value in metrics.items():
            cleaned_metrics[key] = clean_value(value)
        
        # Clean equity_curve
        cleaned_curve = []
        for item in equity_curve:
            cleaned_curve.append({
                'time': item['time'],
                'value': clean_value(item['value'])
            })
        
        # Clean trades
        cleaned_trades = []
        # Don't truncate trades: return all (frontend can paginate)
        def infer_trade_reason(trade_type: str) -> str:
            t = str(trade_type or '').lower()
            if 'liquidation' in t:
                return 'liquidation'
            if 'trailing' in t:
                return 'trailing_stop'
            if 'stop' in t:
                return 'stop_loss'
            if 'profit' in t:
                return 'take_profit'
            if 'reduce' in t:
                return 'reduce_position'
            if 'add' in t:
                return 'add_position'
            if 'close' in t:
                return 'signal_close'
            return ''

        for trade in trades:
            cleaned_trade = {}
            for key, value in trade.items():
                cleaned_trade[key] = clean_value(value)
            inferred_reason = infer_trade_reason(cleaned_trade.get('type'))
            if inferred_reason and not cleaned_trade.get('reason'):
                cleaned_trade['reason'] = inferred_reason
            if inferred_reason and not cleaned_trade.get('close_reason'):
                cleaned_trade['close_reason'] = inferred_reason
            cleaned_trades.append(cleaned_trade)
        
        return {
            **cleaned_metrics,
            'equityCurve': cleaned_curve,
            'trades': cleaned_trades
        }

