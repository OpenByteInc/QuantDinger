"""
实时交易执行服务。

策略线程：拉 K 线/价格、算信号，将订单写入 pending_orders。
实盘成交由 PendingOrderWorker + app.services.live_trading（各所直连 REST）完成，不在此模块使用 ccxt 下单。
"""
import time
import threading
import traceback
import os
try:
    import resource  # Linux/Unix only
except Exception:
    resource = None
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import json
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import pandas as pd
import numpy as np

from app.utils.logger import get_logger
from app.utils.db import get_db_connection
from app.utils.strategy_runtime_logs import append_strategy_log
from app.data_sources import DataSourceFactory, UnsupportedMarketError
from app.services.kline import KlineService
from app.services.indicator_params import IndicatorParamsParser, IndicatorCaller, StrategyConfigParser
from app.services.strategy_script_runtime import (
    ScriptBar,
    StrategyScriptContext,
    compile_strategy_script_handlers,
)

logger = get_logger(__name__)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "on", "enabled"):
        return True
    if s in ("0", "false", "no", "off", "disabled", ""):
        return False
    return bool(default)


def _kline_boundary_poll_offset_sec() -> float:
    try:
        return max(0.0, float(os.getenv("KLINE_BOUNDARY_POLL_OFFSET_SEC", "2")))
    except (TypeError, ValueError):
        return 2.0


def next_kline_boundary_poll_ts(
    now_ts: float,
    timeframe_seconds: int,
    offset_sec: Optional[float] = None,
) -> float:
    """
    Next wall-clock time to poll K-lines: just after the upcoming bar close.

    Bars are aligned to Unix epoch buckets (matches crypto exchange 30m/1H grids).
    Example for 30m + 2s offset: … 17:00:02, 17:30:02, 18:00:02 …
    """
    tf = max(1, int(timeframe_seconds or 60))
    offset = _kline_boundary_poll_offset_sec() if offset_sec is None else max(0.0, float(offset_sec))
    ts = float(now_ts)
    next_close = (int(ts) // tf + 1) * tf
    return next_close + offset


def normalize_trading_execution_modes(trading_config: Optional[Dict[str, Any]]) -> None:
    """
    Align live execution knobs with the UI ``strict_mode`` toggle.

    ``strict_mode=True``  → backtest-like: closed-bar signals only.
    ``strict_mode=False`` → allow intra-bar (aggressive) signals + immediate entry triggers.
    """
    if not isinstance(trading_config, dict):
        return
    tc = trading_config
    has_strict_toggle = "strict_mode" in tc or "strictMode" in tc
    strict = _coerce_bool(tc.get("strict_mode", tc.get("strictMode")), default=True)
    tc["strict_mode"] = strict
    if has_strict_toggle:
        if strict:
            tc["signal_mode"] = "confirmed"
            tc["exit_signal_mode"] = "confirmed"
        else:
            tc["signal_mode"] = "aggressive"
            tc["exit_signal_mode"] = "aggressive"
            tc["entry_trigger_mode"] = "immediate"
    elif strict:
        tc.setdefault("exit_signal_mode", "confirmed")
        tc.setdefault("signal_mode", "confirmed")
    else:
        tc.setdefault("signal_mode", "aggressive")
        tc.setdefault("exit_signal_mode", "aggressive")
        tc.setdefault("entry_trigger_mode", "immediate")


class TradingExecutor:
    """实时交易执行器 (Signal Provider Mode)"""
    
    def __init__(self):
        # 不再使用全局连接，改为每次使用时从连接池获取
        self.running_strategies = {}  # {strategy_id: thread}
        self.lock = threading.Lock()
        # Local-only lightweight in-memory price cache (symbol -> (price, expiry_ts)).
        # This replaces the old Redis-based PriceCache for local deployments.
        self._price_cache = {}
        self._price_cache_lock = threading.Lock()
        # Default to 10s to match the unified tick cadence.
        self._price_cache_ttl_sec = int(os.getenv("PRICE_CACHE_TTL_SEC", "10"))

        # In-memory signal de-dup cache to prevent repeated orders on the same candle signal.
        # Keyed by (strategy_id, symbol, signal_type, signal_timestamp).
        self._signal_dedup = {}  # type: Dict[int, Dict[str, float]]
        self._signal_dedup_lock = threading.Lock()
        self.kline_service = KlineService()   # K线服务（带缓存）
        # Throttle writes to qd_strategy_logs (heartbeat), per strategy_id -> monotonic time
        self._strategy_ui_log_last_tick_ts = {}  # type: Dict[int, float]
        
        # 单实例线程上限，避免无限制创建线程导致 can't start new thread/OOM
        self.max_threads = int(os.getenv('STRATEGY_MAX_THREADS', '64'))
        # 最近一次 start_strategy(False) 的原因（供 API 返回给用户）
        self._last_start_failure: str = ""
        # 线程退出原因（供启动后健康检查返回给用户）
        self._last_exit_reason: Dict[int, str] = {}

        # Per-strategy exchange fee-rate cache: {strategy_id: {"maker": float, "taker": float}}
        self._exchange_fee_cache: Dict[int, Optional[Dict[str, float]]] = {}
        self._exchange_fee_cache_lock = threading.Lock()
        
        # 确保数据库字段存在
        self._ensure_db_columns()

    def _ensure_db_columns(self):
        """确保必要的数据库字段存在（PostgreSQL）"""
        try:
            with get_db_connection() as db:
                cursor = db.cursor()
                col_names = set()

                # PostgreSQL: 使用 information_schema 查询列
                try:
                    cursor.execute("""
                        SELECT column_name FROM information_schema.columns 
                        WHERE table_name = 'qd_strategy_positions'
                    """)
                    cols = cursor.fetchall() or []
                    col_names = {c.get('column_name') or c.get('COLUMN_NAME') for c in cols if isinstance(c, dict)}
                except Exception:
                    col_names = set()

                if 'highest_price' not in col_names:
                    logger.info("Adding highest_price column to qd_strategy_positions...")
                    cursor.execute("ALTER TABLE qd_strategy_positions ADD COLUMN IF NOT EXISTS highest_price DOUBLE PRECISION DEFAULT 0")
                    db.commit()
                    logger.info("highest_price column added")

                if 'lowest_price' not in col_names:
                    logger.info("Adding lowest_price column to qd_strategy_positions...")
                    cursor.execute("ALTER TABLE qd_strategy_positions ADD COLUMN IF NOT EXISTS lowest_price DOUBLE PRECISION DEFAULT 0")
                    db.commit()
                    logger.info("lowest_price column added")

                trade_col_names = set()
                try:
                    cursor.execute("""
                        SELECT column_name FROM information_schema.columns
                        WHERE table_name = 'qd_strategy_trades'
                    """)
                    trade_cols = cursor.fetchall() or []
                    trade_col_names = {c.get('column_name') or c.get('COLUMN_NAME') for c in trade_cols if isinstance(c, dict)}
                except Exception:
                    trade_col_names = set()

                if 'close_reason' not in trade_col_names:
                    logger.info("Adding close_reason column to qd_strategy_trades...")
                    cursor.execute(
                        "ALTER TABLE qd_strategy_trades ADD COLUMN IF NOT EXISTS close_reason VARCHAR(64) DEFAULT ''"
                    )
                    db.commit()
                    logger.info("close_reason column added")

                cursor.close()
        except Exception as e:
            logger.error(f"Failed to check/ensure DB columns: {str(e)}")
        try:
            from app.services.live_trading.records import ensure_position_ledger_schema

            ensure_position_ledger_schema()
        except Exception as e:
            logger.warning("ensure_position_ledger_schema failed: %s", e)

    def _normalize_trade_symbol(self, exchange: Any, symbol: str, market_type: str, exchange_id: str) -> str:
        """
        将数据库/配置里的 symbol 规范化为交易所合约可用的 CCXT symbol。

        典型场景：OKX 永续统一符号通常是 `BNB/USDT:USDT`，但前端/数据库可能传 `BNB/USDT`。
        """
        try:
            # 新系统：仅支持 swap(合约永续) / spot(现货)
            if market_type != 'swap':
                return symbol
            if not symbol or ':' in symbol:
                return symbol
            if not getattr(exchange, 'markets', None):
                return symbol

            # 如果 symbol 本身就是合约市场，直接返回
            try:
                m = exchange.market(symbol)
                if m and (m.get('swap') or m.get('future') or m.get('contract')):
                    return symbol
            except Exception:
                pass

            # OKX/部分交易所：永续常见为 BASE/QUOTE:QUOTE 或 BASE/QUOTE:USDT
            if '/' not in symbol:
                return symbol
            base, quote = symbol.split('/', 1)
            candidates = []
            if quote:
                candidates.append(f"{base}/{quote}:{quote}")
                if quote.upper() != 'USDT':
                    candidates.append(f"{base}/{quote}:USDT")

            for cand in candidates:
                if cand in exchange.markets:
                    cm = exchange.markets[cand]
                    if cm and (cm.get('swap') or cm.get('future') or cm.get('contract')):
                        logger.info(f"symbol normalized: {symbol} -> {cand} (exchange={exchange_id}, market_type={market_type})")
                        return cand

            return symbol
        except Exception:
            return symbol

    def _log_resource_status(self, prefix: str = ""):
        """调试：记录线程/内存使用，便于定位 can't start new thread 根因"""
        try:
            import psutil  # 如果有安装则使用更精确的指标
            p = psutil.Process()
            mem = p.memory_info().rss / 1024 / 1024
            th = p.num_threads()
            logger.warning(f"{prefix}resource status: memory={mem:.1f}MB, threads={th}, "
                           f"running_strategies={len(self.running_strategies)}")
        except Exception:
            try:
                th = threading.active_count()
                # 从 /proc/self/status 读取 VmRSS（适用于 Linux 容器）
                vmrss = None
                try:
                    with open('/proc/self/status') as f:
                        for line in f:
                            if line.startswith('VmRSS:'):
                                vmrss = line.split()[1:3]  # e.g. ['123456', 'kB']
                                break
                except Exception:
                    pass
                vmrss_str = f"{vmrss[0]}{vmrss[1]}" if vmrss else "N/A"
                logger.warning(f"{prefix}resource status: VmRSS={vmrss_str}, active_threads={th}, "
                               f"running_strategies={len(self.running_strategies)}")
            except Exception:
                pass

    def _console_print(self, msg: str) -> None:
        """
        Local-only observability: print to stdout so user can see strategy status in console.
        """
        try:
            print(str(msg or ""), flush=True)
        except Exception:
            pass

    def _position_state(self, positions: List[Dict[str, Any]]) -> str:
        """
        Return current position state for a strategy+symbol in local single-position mode.

        Returns: 'flat' | 'long' | 'short'
        """
        try:
            if not positions:
                return "flat"
            # Local mode assumes single-direction position per symbol.
            side = (positions[0].get("side") or "").strip().lower()
            if side in ("long", "short"):
                return side
        except Exception:
            pass
        return "flat"

    @staticmethod
    def _symbol_match_key(symbol: str) -> str:
        return str(symbol or "").split(":")[0].strip()

    def _inflight_open_side(self, strategy_id: int, symbol: str) -> Optional[str]:
        """
        Return 'long' or 'short' when an open_* order is pending/processing for
        this strategy+symbol, else None.
        """
        sym_key = self._symbol_match_key(symbol)
        if not sym_key:
            return None
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT signal_type, symbol
                    FROM pending_orders
                    WHERE strategy_id = %s
                      AND status IN ('pending', 'processing')
                      AND signal_type IN ('open_long', 'open_short')
                    ORDER BY id DESC
                    LIMIT 20
                    """,
                    (int(strategy_id),),
                )
                rows = cur.fetchall() or []
                cur.close()
            for row in rows:
                row_sym = self._symbol_match_key(str(row.get("symbol") or ""))
                if row_sym != sym_key:
                    continue
                sig = str(row.get("signal_type") or "").strip().lower()
                if sig == "open_long":
                    return "long"
                if sig == "open_short":
                    return "short"
        except Exception as e:
            logger.debug("inflight open lookup failed sid=%s: %s", strategy_id, e)
        return None

    def _effective_position_state(
        self,
        strategy_id: int,
        symbol: str,
        positions: List[Dict[str, Any]],
    ) -> str:
        """Local DB state plus in-flight open orders (live dedup guard)."""
        state = self._position_state(positions)
        if state != "flat":
            return state
        inflight = self._inflight_open_side(strategy_id, symbol)
        return inflight or "flat"

    @staticmethod
    def _is_live_script_hydrate_candidate(trading_config: Optional[Dict[str, Any]]) -> bool:
        tc = trading_config if isinstance(trading_config, dict) else {}
        if str(tc.get("execution_mode") or "live").strip().lower() != "live":
            return False
        bot_type = str(tc.get("bot_type") or "").strip().lower()
        if bot_type == "grid":
            return True
        is_bot_script = bool(
            bot_type in ("martingale", "dca")
            or tc.get("strategy_mode") == "bot"
        )
        return not is_bot_script

    @staticmethod
    def _is_indicator_both_mode(trading_config: Optional[Dict[str, Any]]) -> bool:
        """True only when buy/sell was normalized with backtest-style both-mode flip semantics."""
        tc = trading_config if isinstance(trading_config, dict) else {}
        return bool(tc.get('_indicator_both_mode'))

    def _is_signal_allowed(
        self,
        state: str,
        signal_type: str,
        *,
        indicator_both_mode: bool = False,
    ) -> bool:
        """
        Enforce strict state machine:
        - flat: only open_long/open_short
        - long: only add_long/close_long
        - short: only add_short/close_short

        Indicator both-mode (buy/sell) matches BacktestService: buy -> open_long may flip
        from short; sell -> open_short may flip from long. Explicit close_* still apply.
        """
        st = (state or "flat").strip().lower()
        sig = (signal_type or "").strip().lower()
        if indicator_both_mode:
            if sig == "open_long":
                return st in ("flat", "short")
            if sig == "open_short":
                return st in ("flat", "long")
        if st == "flat":
            return sig in ("open_long", "open_short")
        if st == "long":
            return sig in ("add_long", "reduce_long", "close_long")
        if st == "short":
            return sig in ("add_short", "reduce_short", "close_short")
        return False

    def _signal_priority(self, signal_type: str) -> int:
        """
        Lower value = higher priority. We always close before (re)opening/adding.
        """
        sig = (signal_type or "").strip().lower()
        if sig.startswith("close_"):
            return 0
        if sig.startswith("reduce_"):
            return 1
        if sig.startswith("open_"):
            return 2
        if sig.startswith("add_"):
            return 3
        return 99

    def _dedup_key(self, strategy_id: int, symbol: str, signal_type: str, signal_ts: int) -> str:
        sym = (symbol or "").strip().upper()
        if ":" in sym:
            sym = sym.split(":", 1)[0]
        return f"{int(strategy_id)}|{sym}|{(signal_type or '').strip().lower()}|{int(signal_ts or 0)}"

    def _should_skip_signal_once_per_candle(
        self,
        strategy_id: int,
        symbol: str,
        signal_type: str,
        signal_ts: int,
        timeframe_seconds: int,
        now_ts: Optional[int] = None,
    ) -> bool:
        """
        Prevent repeated orders for the same candle signal across ticks.

        This is especially important for 'confirmed' signals that point to the previous closed candle:
        the signal timestamp stays constant for the entire next candle, so without de-dup the system
        would re-enqueue the same order every tick.
        """
        try:
            now = int(now_ts or time.time())
            tf = int(timeframe_seconds or 0)
            if tf <= 0:
                tf = 60
            # Keep keys long enough to cover at least the next candle.
            ttl_sec = max(tf * 2, 120)
            expiry = float(now + ttl_sec)
            key = self._dedup_key(strategy_id, symbol, signal_type, int(signal_ts or 0))

            with self._signal_dedup_lock:
                bucket = self._signal_dedup.get(int(strategy_id))
                if bucket is None:
                    bucket = {}
                    self._signal_dedup[int(strategy_id)] = bucket

                # Opportunistic cleanup
                stale = [k for k, exp in bucket.items() if float(exp) <= now]
                for k in stale[:512]:
                    try:
                        del bucket[k]
                    except Exception:
                        pass

                exp = bucket.get(key)
                if exp is not None and float(exp) > now:
                    return True

                # Reserve the key (best-effort). Caller may still fail to enqueue; that's acceptable
                # because repeated failures should not flood the queue.
                bucket[key] = expiry
                return False
        except Exception:
            return False

    def _to_ratio(self, v: Any, default: float = 0.0) -> float:
        """
        Convert a stored percent value into ratio in [0, 1].

        Convention (single source of truth): ``trading_config.*_pct`` fields
        store percent (e.g. ``9`` means 9%, ``0.01`` means 0.01%). This is
        the same unit the snapshot resolver and the bot wizard already
        produce.

        The previous "auto-detect" branch (``if x > 1: /= 100``) silently
        promoted sub-1% inputs (0.01, 0.5, …) to ratio interpretation
        (1%, 50%), which broke any strategy needing < 1% SL / TP — and was
        the only place left in the system that did that guessing.
        """
        try:
            x = float(v if v is not None else default)
        except Exception:
            x = float(default or 0.0)
        if x < 0:
            return 0.0
        x = x / 100.0
        if x > 1.0:
            return 1.0
        return float(x)

    def _code_strategy_cfg(self, trading_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        tc = trading_config if isinstance(trading_config, dict) else {}
        code_cfg = tc.get("_strategy_cfg_from_code")
        return code_cfg if isinstance(code_cfg, dict) else {}

    def _risk_params_from_trading_config(self, trading_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Resolve risk/position ratios for live execution.

        When indicator code carries @strategy annotations, use the same 0–1
        ratio semantics as BacktestService. Otherwise fall back to flat
        trading_config *_pct fields (stored as percent numbers).
        """
        code_cfg = self._code_strategy_cfg(trading_config)
        if code_cfg:
            risk = code_cfg.get("risk") or {}
            trailing = risk.get("trailing") or {}
            pos = code_cfg.get("position") or {}
            return {
                "entry_ratio": float(
                    pos.get("entryPct")
                    if pos.get("entryPct") is not None
                    else StrategyConfigParser.normalize_entry_ratio(None)
                ),
                "stop_loss_ratio": float(risk.get("stopLossPct") or 0),
                "take_profit_ratio": float(risk.get("takeProfitPct") or 0),
                "trailing_enabled": bool(trailing.get("enabled")),
                "trailing_stop_ratio": float(trailing.get("pct") or 0),
                "trailing_activation_ratio": float(trailing.get("activationPct") or 0),
            }

        tc = trading_config or {}
        return {
            "entry_ratio": self._to_ratio(tc.get("entry_pct"), default=1.0),
            "stop_loss_ratio": self._to_ratio(tc.get("stop_loss_pct")),
            "take_profit_ratio": self._to_ratio(tc.get("take_profit_pct")),
            "trailing_enabled": bool(tc.get("trailing_enabled")),
            "trailing_stop_ratio": self._to_ratio(tc.get("trailing_stop_pct")),
            "trailing_activation_ratio": self._to_ratio(tc.get("trailing_activation_pct")),
        }

    def _build_cfg_from_trading_config(self, trading_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a backtest-modal compatible config dict for indicator scripts.

        Frontend (trading assistant) stores most params as flat keys under `trading_config`.
        Backtest service expects nested structure: cfg.risk/cfg.scale/cfg.position (camelCase).

        We provide BOTH:
        - `trading_config`: the original flat dict (so existing scripts keep working)
        - `cfg`: a normalized nested dict (so scripts can reuse backtest-style helpers)
        """
        tc = trading_config or {}
        code_cfg = self._code_strategy_cfg(tc)
        if code_cfg:
            risk = code_cfg.get("risk") or {}
            trailing = risk.get("trailing") or {}
            pos = code_cfg.get("position") or {}
            stop_loss_pct = float(risk.get("stopLossPct") or 0)
            take_profit_pct = float(risk.get("takeProfitPct") or 0)
            trailing_enabled = bool(trailing.get("enabled"))
            trailing_stop_pct = float(trailing.get("pct") or 0)
            trailing_activation_pct = float(trailing.get("activationPct") or 0)
            entry_pct = float(
                pos.get("entryPct")
                if pos.get("entryPct") is not None
                else StrategyConfigParser.normalize_entry_ratio(None)
            )
        else:
            stop_loss_pct = self._to_ratio(tc.get("stop_loss_pct"))
            take_profit_pct = self._to_ratio(tc.get("take_profit_pct"))
            trailing_enabled = bool(tc.get("trailing_enabled"))
            trailing_stop_pct = self._to_ratio(tc.get("trailing_stop_pct"))
            trailing_activation_pct = self._to_ratio(tc.get("trailing_activation_pct"))
            entry_pct = self._to_ratio(tc.get("entry_pct"))

        # Scale-in
        trend_add_enabled = bool(tc.get("trend_add_enabled"))
        trend_add_step_pct = self._to_ratio(tc.get("trend_add_step_pct"))
        trend_add_size_pct = self._to_ratio(tc.get("trend_add_size_pct"))
        trend_add_max_times = int(tc.get("trend_add_max_times") or 0)

        dca_add_enabled = bool(tc.get("dca_add_enabled"))
        dca_add_step_pct = self._to_ratio(tc.get("dca_add_step_pct"))
        dca_add_size_pct = self._to_ratio(tc.get("dca_add_size_pct"))
        dca_add_max_times = int(tc.get("dca_add_max_times") or 0)

        # Scale-out / reduce
        trend_reduce_enabled = bool(tc.get("trend_reduce_enabled"))
        trend_reduce_step_pct = self._to_ratio(tc.get("trend_reduce_step_pct"))
        trend_reduce_size_pct = self._to_ratio(tc.get("trend_reduce_size_pct"))
        trend_reduce_max_times = int(tc.get("trend_reduce_max_times") or 0)

        adverse_reduce_enabled = bool(tc.get("adverse_reduce_enabled"))
        adverse_reduce_step_pct = self._to_ratio(tc.get("adverse_reduce_step_pct"))
        adverse_reduce_size_pct = self._to_ratio(tc.get("adverse_reduce_size_pct"))
        adverse_reduce_max_times = int(tc.get("adverse_reduce_max_times") or 0)

        return {
            "risk": {
                "stopLossPct": stop_loss_pct,
                "takeProfitPct": take_profit_pct,
                "trailing": {
                    "enabled": trailing_enabled,
                    "pct": trailing_stop_pct,
                    "activationPct": trailing_activation_pct,
                },
            },
            "position": {
                "entryPct": entry_pct,
            },
            "scale": {
                "trendAdd": {
                    "enabled": trend_add_enabled,
                    "stepPct": trend_add_step_pct,
                    "sizePct": trend_add_size_pct,
                    "maxTimes": trend_add_max_times,
                },
                "dcaAdd": {
                    "enabled": dca_add_enabled,
                    "stepPct": dca_add_step_pct,
                    "sizePct": dca_add_size_pct,
                    "maxTimes": dca_add_max_times,
                },
                "trendReduce": {
                    "enabled": trend_reduce_enabled,
                    "stepPct": trend_reduce_step_pct,
                    "sizePct": trend_reduce_size_pct,
                    "maxTimes": trend_reduce_max_times,
                },
                "adverseReduce": {
                    "enabled": adverse_reduce_enabled,
                    "stepPct": adverse_reduce_step_pct,
                    "sizePct": adverse_reduce_size_pct,
                    "maxTimes": adverse_reduce_max_times,
                },
            },
        }
    
    def start_strategy(self, strategy_id: int) -> bool:
        """
        启动策略
        
        Args:
            strategy_id: 策略ID
            
        Returns:
            是否成功
        """
        try:
            with self.lock:
                self._last_start_failure = ""
                # 清理已退出的线程，防止计数膨胀
                stale_ids = [sid for sid, th in self.running_strategies.items() if not th.is_alive()]
                for sid in stale_ids:
                    del self.running_strategies[sid]

                if len(self.running_strategies) >= self.max_threads:
                    n = len(self.running_strategies)
                    self._last_start_failure = (
                        f"已达到单进程策略线程上限 {self.max_threads}（当前已登记 {n} 个）；"
                        f"请停止部分运行中策略，或提高环境变量 STRATEGY_MAX_THREADS 后重启 API。"
                    )
                    logger.error(
                        f"Thread limit reached ({self.max_threads}); refuse to start strategy {strategy_id}. "
                        f"Reduce running strategies or increase STRATEGY_MAX_THREADS."
                    )
                    self._log_resource_status(prefix="start_denied: ")
                    return False

                if strategy_id in self.running_strategies:
                    self._last_start_failure = "该策略的执行线程已在运行中"
                    logger.warning(f"Strategy {strategy_id} is already running")
                    return False
                
                # 创建并启动线程
                thread = threading.Thread(
                    target=self._run_strategy_loop,
                    args=(strategy_id,),
                    daemon=True
                )
                try:
                    thread.start()
                except Exception as e:
                    self._last_start_failure = f"启动线程失败: {e}"
                    # 捕获 can't start new thread 等异常，记录资源状态
                    self._log_resource_status(prefix="启动异常")
                    raise e
                self.running_strategies[strategy_id] = thread
                
                logger.info(f"Strategy {strategy_id} started")
                self._console_print(f"[strategy:{strategy_id}] started")
                append_strategy_log(strategy_id, "info", "Strategy execution thread started")
                return True
                
        except Exception as e:
            self._last_start_failure = self._last_start_failure or f"异常: {e}"
            logger.error(f"Failed to start strategy {strategy_id}: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    def _fetch_recent_strategy_log_hint(self, strategy_id: int) -> str:
        """Best-effort: read recent runtime log for start-failure diagnosis."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT message FROM qd_strategy_logs
                    WHERE strategy_id = %s
                    ORDER BY timestamp DESC, id DESC
                    LIMIT 8
                    """,
                    (int(strategy_id),),
                )
                rows = cur.fetchall() or []
                cur.close()
            skip = {
                "strategy execution thread started",
                "strategy execution loop exited",
            }
            for row in rows:
                msg = str((row or {}).get("message") or "").strip()
                if not msg:
                    continue
                low = msg.lower()
                if low in skip:
                    continue
                if "auto-stopped:" in low:
                    return msg.split(":", 1)[-1].strip()[:500]
                return msg[:500]
        except Exception:
            pass
        return ""

    def wait_strategy_running(self, strategy_id: int, timeout: float = 3.0) -> Tuple[bool, str]:
        """
        Poll briefly after start_strategy() to catch threads that exit during init.
        Returns (still_running, hint_if_not).
        """
        sid = int(strategy_id)
        deadline = time.monotonic() + max(0.5, float(timeout))
        while time.monotonic() < deadline:
            with self.lock:
                th = self.running_strategies.get(sid)
                alive = th is not None and th.is_alive()
            if not alive:
                reason = (self._last_exit_reason.pop(sid, None) or "").strip()
                if not reason:
                    reason = self._fetch_recent_strategy_log_hint(sid)
                return False, reason or (
                    "执行线程已退出（常见：策略脚本/指标为空、K线拉取失败、类型不支持实盘）"
                )
            time.sleep(0.25)
        with self.lock:
            th = self.running_strategies.get(sid)
            alive = th is not None and th.is_alive()
        if alive:
            return True, ""
        reason = (self._last_exit_reason.pop(sid, None) or "").strip()
        if not reason:
            reason = self._fetch_recent_strategy_log_hint(sid)
        return False, reason or "执行线程已退出"
    
    def stop_strategy(self, strategy_id: int) -> bool:
        """
        停止策略
        
        Args:
            strategy_id: 策略ID
            
        Returns:
            是否成功
        """
        try:
            with self.lock:
                had_thread = strategy_id in self.running_strategies

                # Always mark DB stopped (also when auto-stop runs without a live thread).
                with get_db_connection() as db:
                    cursor = db.cursor()
                    cursor.execute(
                        "UPDATE qd_strategies_trading SET status = 'stopped' WHERE id = %s",
                        (strategy_id,),
                    )
                    db.commit()
                    cursor.close()

                if had_thread:
                    del self.running_strategies[strategy_id]
                    self._exchange_fee_cache.pop(strategy_id, None)
                    try:
                        from app.services.grid.runner import shutdown_grid_for_strategy

                        shutdown_grid_for_strategy(strategy_id)
                    except Exception:
                        pass
                    logger.info(f"Strategy {strategy_id} stopped")
                    self._console_print(f"[strategy:{strategy_id}] stopped (requested)")
                    append_strategy_log(strategy_id, "info", "Strategy stop requested (run flag cleared)")
                else:
                    logger.info(f"Strategy {strategy_id} marked stopped in DB (no active thread)")
                    try:
                        from app.services.grid.runner import shutdown_grid_for_strategy

                        shutdown_grid_for_strategy(strategy_id)
                    except Exception:
                        pass
                return True
                
        except Exception as e:
            logger.error(f"Failed to stop strategy {strategy_id}: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    def _df_to_script_exec_df(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.reset_index()
        c0 = out.columns[0]
        if c0 != 'time':
            out.rename(columns={c0: 'time'}, inplace=True)
        return out

    def _script_default_position_ratio(self, trading_config: Dict[str, Any]) -> float:
        try:
            entry_ratio = self._risk_params_from_trading_config(trading_config).get("entry_ratio")
            if entry_ratio is not None and float(entry_ratio) > 0:
                return float(entry_ratio)
        except Exception:
            pass
        return 0.06

    def _hydrate_script_ctx_from_positions(
        self,
        ctx: StrategyScriptContext,
        strategy_id: int,
        symbol: str,
        initial_capital: Optional[float] = None,
        current_price: Optional[float] = None,
        trading_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        ctx.position.clear_position()
        pl = self._get_current_positions(strategy_id, symbol)
        # DB stores long/short legs as separate rows keyed by (strategy_id,
        # symbol, side); hydrate both so hedge-mode strategies (neutral grid)
        # see independent ctx.position.long_size / short_size.
        if pl:
            for p in pl:
                side = (p.get('side') or '').strip().lower()
                size = float(p.get('size') or 0)
                ep = float(p.get('entry_price') or 0)
                if size <= 0:
                    continue
                if side == 'long':
                    ctx.position.open_long(ep, size)
                elif side == 'short':
                    ctx.position.open_short(ep, size)

        # Live script/grid: when DB is empty/stale, fall back to the exchange book.
        tc = trading_config if isinstance(trading_config, dict) else {}
        if self._is_live_script_hydrate_candidate(tc):
            self._hydrate_grid_ctx_from_exchange_best_effort(
                ctx,
                strategy_id=strategy_id,
                symbol=symbol,
                current_price=current_price,
                trading_config=tc,
                db_had_long=ctx.position.has_long(),
                db_had_short=ctx.position.has_short(),
            )
            pl = self._get_current_positions(strategy_id, symbol)
        # 把 ctx.balance 刷新为最新权益(初始资金 + 已实现盈亏 + 未实现盈亏),
        # 这样趋势等使用 ctx.balance * POS_PCT 计算仓位的脚本能反映真实资金
        try:
            if initial_capital is not None and float(initial_capital) > 0:
                eq = self._calculate_current_equity(
                    strategy_id,
                    float(initial_capital),
                    current_positions=pl,
                    current_price=current_price,
                    symbol=symbol,
                )
                ctx.balance = float(eq)
                ctx.equity = float(eq)
        except Exception:
            pass

    def _hydrate_grid_ctx_from_exchange_best_effort(
        self,
        ctx: StrategyScriptContext,
        *,
        strategy_id: int,
        symbol: str,
        current_price: Optional[float],
        trading_config: Dict[str, Any],
        db_had_long: bool,
        db_had_short: bool,
    ) -> None:
        """Fill missing grid legs from the exchange when local DB snapshot is empty."""
        if db_had_long and db_had_short:
            return
        try:
            from app.services.exchange_execution import load_strategy_configs, resolve_exchange_config

            sc = load_strategy_configs(int(strategy_id))
            raw_ex = trading_config.get("exchange_config") or sc.get("exchange_config") or {}
            ex_cfg = resolve_exchange_config(
                raw_ex if isinstance(raw_ex, dict) else {},
                user_id=int(sc.get("user_id") or 1),
            )
        except Exception as e:
            logger.warning("Grid exchange hydrate resolve failed for sid=%s: %s", strategy_id, e)
            return
        if not isinstance(ex_cfg, dict) or not (ex_cfg.get("api_key") or ex_cfg.get("apiKey")):
            return
        market_type = str(trading_config.get("market_type") or "swap").strip().lower()
        try:
            from app.services.live_trading.factory import create_client
            from app.services.live_trading.position_query import query_exchange_position_size

            client = create_client(ex_cfg, market_type=market_type)
            ref_px = float(current_price or 0.0)
            for side in ("long", "short"):
                if side == "long" and db_had_long:
                    continue
                if side == "short" and db_had_short:
                    continue
                sz = query_exchange_position_size(
                    client=client,
                    symbol=str(symbol or ""),
                    pos_side=side,
                    market_type=market_type,
                    exchange_config=ex_cfg,
                )
                if sz <= 0:
                    continue
                if side == "long":
                    ctx.position.open_long(ref_px, sz)
                else:
                    ctx.position.open_short(ref_px, sz)
                try:
                    from app.services.live_trading.records import upsert_position

                    upsert_position(
                        strategy_id=int(strategy_id),
                        symbol=str(symbol or ""),
                        side=side,
                        size=float(sz),
                        entry_price=float(ref_px or 0.0),
                        current_price=float(ref_px or 0.0),
                    )
                except Exception:
                    pass
                logger.info(
                    "Exchange hydrate from book: %s %s size=%s (db missing leg)",
                    symbol,
                    side,
                    sz,
                )
        except Exception as e:
            logger.warning("Grid exchange hydrate skipped for %s: %s", symbol, e)

    def _init_script_strategy_context(
        self,
        strategy_id: int,
        df: pd.DataFrame,
        trading_config: Dict[str, Any],
        initial_capital: float,
    ) -> Tuple[StrategyScriptContext, Optional[pd.Timestamp]]:
        df_exec = self._df_to_script_exec_df(df)
        ctx = StrategyScriptContext(df_exec, float(initial_capital or 0))
        raw = (trading_config or {}).get('script_runtime_state') or {}
        params = raw.get('params') if isinstance(raw, dict) else {}
        persisted = dict(params) if isinstance(params, dict) else {}
        bp = (trading_config or {}).get('bot_params')
        bot_params = dict(bp) if isinstance(bp, dict) else {}
        # 交易机器人参数在 trading_config.bot_params；持久化 state 里只有 layer 等运行时字段。
        # 若只恢复 persisted，on_init 里 ctx.param('takeProfitPct', 0) 会把止盈写成 0，导致永不止盈。
        ctx._params = {**persisted, **bot_params}
        last_ts = None
        ts_s = raw.get('last_closed_bar_ts') if isinstance(raw, dict) else None
        if ts_s:
            try:
                last_ts = pd.Timestamp(ts_s)
                if last_ts.tzinfo is None:
                    last_ts = last_ts.tz_localize('UTC')
                else:
                    last_ts = last_ts.tz_convert('UTC')
            except Exception:
                last_ts = None
        return ctx, last_ts

    def _persist_script_runtime_state(self, strategy_id: int, closed_ts: Any, params: Dict[str, Any]) -> None:
        try:
            safe_params = json.loads(json.dumps(params or {}, default=str))
        except Exception:
            safe_params = {}
        ts_str = ''
        try:
            if closed_ts is not None:
                ts_str = pd.Timestamp(closed_ts).isoformat()
        except Exception:
            ts_str = ''
        state = {'last_closed_bar_ts': ts_str, 'params': safe_params}
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("SELECT trading_config FROM qd_strategies_trading WHERE id = %s", (strategy_id,))
                row = cur.fetchone()
                if not row:
                    cur.close()
                    return
                tc = row.get('trading_config')
                if isinstance(tc, str) and tc.strip():
                    try:
                        tc = json.loads(tc)
                    except Exception:
                        tc = {}
                elif not isinstance(tc, dict):
                    tc = {}
                tc['script_runtime_state'] = state
                cur.execute(
                    "UPDATE qd_strategies_trading SET trading_config = %s WHERE id = %s",
                    (json.dumps(tc, ensure_ascii=False), strategy_id),
                )
                db.commit()
                cur.close()
        except Exception as e:
            logger.warning(f"Persist script runtime state failed: {e}")

    def _bot_type_key(self, trading_config: Optional[Dict[str, Any]]) -> str:
        return str((trading_config or {}).get("bot_type") or "").strip().lower()

    def _is_live_grid_resting(
        self,
        trading_config: Optional[Dict[str, Any]],
        execution_mode: str,
        market_category: str = "Crypto",
    ) -> bool:
        """Live grid bots always use the resting limit-order engine."""
        if str(execution_mode or "").strip().lower() != "live":
            return False
        if self._bot_type_key(trading_config) != "grid":
            return False
        mc = str(market_category or "Crypto").strip()
        return mc in ("Crypto", "Forex", "Futures")

    def _grid_enqueue_market(
        self,
        strategy_id: int,
        symbol: str,
        signal_type: str,
        usdt_amount: float,
        price: float,
        reason: str,
        *,
        trading_config: Dict[str, Any],
        execution_mode: str,
        market_type: str,
        market_category: str,
        leverage: float,
        notification_config: Dict[str, Any],
        kline_exchange_id: Optional[str],
    ) -> bool:
        try:
            lev = float(leverage or 1.0)
            qty = (float(usdt_amount or 0) * lev / float(price)) if price > 0 and market_type != "spot" else (float(usdt_amount or 0) / float(price) if price > 0 else 0)
            if signal_type.startswith("close_"):
                qty = 0
            bp = trading_config if isinstance(trading_config, dict) else {}
            order_mode = str(bp.get("order_mode") or (bp.get("bot_params") or {}).get("orderMode") or "market")
            res = self._execute_exchange_order(
                exchange=None,
                strategy_id=strategy_id,
                symbol=symbol,
                signal_type=signal_type,
                amount=qty if not signal_type.startswith("close_") else 0,
                ref_price=float(price or 0),
                market_type=market_type,
                market_category=market_category,
                leverage=lev,
                execution_mode=execution_mode,
                notification_config=notification_config,
                signal_reason=reason,
                signal_ts=int(time.time()),
                price_exchange_id=kline_exchange_id,
                order_mode=order_mode if order_mode != "maker" else "market",
            )
            return bool(res and res.get("success"))
        except Exception as e:
            logger.warning("grid enqueue market sid=%s: %s", strategy_id, e)
            return False

    def _setup_grid_resting_runner(
        self,
        strategy_id: int,
        symbol: str,
        trading_config: Dict[str, Any],
        exchange_config: Dict[str, Any],
        *,
        user_id: int = 1,
        initial_capital: float,
        execution_mode: str,
        market_type: str,
        market_category: str,
        leverage: float,
        notification_config: Dict[str, Any],
        kline_exchange_id: Optional[str],
    ):
        from app.services.grid.runner import GridRestingRunner
        from app.services.exchange_execution import resolve_exchange_config
        from app.services.live_trading.factory import create_client

        ex_cfg = resolve_exchange_config(
            exchange_config if isinstance(exchange_config, dict) else {},
            user_id=int(user_id or 1),
        )
        mt = str(market_type or "swap")

        def _create_client():
            return create_client(ex_cfg, market_type=mt)

        def _enqueue(sig: str, usdt: float, px: float, reason: str) -> bool:
            return self._grid_enqueue_market(
                strategy_id,
                symbol,
                sig,
                usdt,
                px,
                reason,
                trading_config=trading_config,
                execution_mode=execution_mode,
                market_type=mt,
                market_category=market_category,
                leverage=leverage,
                notification_config=notification_config,
                kline_exchange_id=kline_exchange_id,
            )

        def _risk_exits(px: float):
            return self._grid_bot_risk_exits(
                strategy_id=strategy_id,
                symbol=symbol,
                current_price=float(px),
                trading_config=trading_config,
                timeframe_seconds=60,
                initial_capital=float(initial_capital or 0),
            )

        return GridRestingRunner(
            strategy_id,
            symbol,
            trading_config,
            ex_cfg,
            user_id=int(user_id or 1),
            initial_capital=float(initial_capital or 0),
            enqueue_market_fn=_enqueue,
            create_client_fn=_create_client,
            risk_exit_fn=_risk_exits,
        )

    def _bot_has_market_guards(self, trading_config: Optional[Dict[str, Any]]) -> bool:
        return self._bot_type_key(trading_config) in ("grid", "martingale")

    def _prepare_grid_bot_before_bar(
        self,
        script_ctx: StrategyScriptContext,
        trading_config: Dict[str, Any],
        *,
        price: float,
        high: float,
        low: float,
        is_closed_bar: bool,
    ) -> None:
        if not self._bot_has_market_guards(trading_config):
            return
        try:
            from app.services.bot_scripts.grid_runtime import prepare_bot_market_guards

            bars_df = getattr(script_ctx, "_bars_df", None)
            prepare_bot_market_guards(
                self._bot_type_key(trading_config),
                script_ctx._params,
                price=float(price or 0),
                high=float(high or price or 0),
                low=float(low or price or 0),
                bars_df=bars_df,
                is_closed_bar=bool(is_closed_bar),
            )
        except Exception as e:
            logger.warning(f"Bot market-guard prepare failed: {e}")

    def _post_process_grid_bot_signals(
        self,
        signals: List[Dict[str, Any]],
        script_ctx: StrategyScriptContext,
        trading_config: Dict[str, Any],
        *,
        price: float,
        timestamp: int,
    ) -> List[Dict[str, Any]]:
        if not self._bot_has_market_guards(trading_config):
            return signals
        try:
            from app.services.bot_scripts.grid_runtime import (
                filter_grid_signals_under_waterfall,
                inject_waterfall_close_signal,
            )

            params = script_ctx._params if isinstance(script_ctx._params, dict) else {}
            pos = script_ctx.position
            has_long = False
            has_short = False
            try:
                if int(pos) > 0:
                    has_long = True
                elif int(pos) < 0:
                    has_short = True
            except Exception:
                side = str((pos.get("side") if isinstance(pos, dict) else getattr(pos, "side", "")) or "").lower()
                has_long = side == "long" and float((pos.get("size") if isinstance(pos, dict) else getattr(pos, "size", 0)) or 0) > 0
                has_short = side == "short" and float((pos.get("size") if isinstance(pos, dict) else getattr(pos, "size", 0)) or 0) > 0

            out = filter_grid_signals_under_waterfall(list(signals or []), params)
            return inject_waterfall_close_signal(
                out,
                params,
                has_long=has_long,
                has_short=has_short,
                price=float(price or 0),
                timestamp=int(timestamp or 0),
            )
        except Exception as e:
            logger.warning(f"Bot market-guard signal post-process failed: {e}")
            return signals

    def _script_orders_to_execution_signals(
        self,
        ctx: StrategyScriptContext,
        trade_direction: str,
        bar_close: float,
        closed_ts: pd.Timestamp,
        trading_config: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        td = str(trade_direction or 'both').lower()
        if td not in ('long', 'short', 'both'):
            td = 'both'
        default_ratio = self._script_default_position_ratio(trading_config)
        try:
            ts_i = int(closed_ts.timestamp())
        except Exception:
            ts_i = int(time.time())

        bot_type = (trading_config or {}).get('bot_type', '')
        is_grid_bot = bot_type in ('grid', 'dca')

        out: List[Dict[str, Any]] = []
        trig = float(bar_close or 0)
        # 把 bot 脚本传来的 USDT 名义金额换算成本 tick 的近似 qty,用于维护
        # 本地 ctx.position 在同一 bar/tick 内多个 order 之间的一致性(只影响
        # 脚本对 ctx.position 的符号/数量判断,真实下单数量仍由
        # _execute_signal 按 leverage/market_type 计算)
        try:
            is_bot_script = bool(
                (trading_config or {}).get('bot_type')
                or (trading_config or {}).get('strategy_mode') == 'bot'
            )
        except Exception:
            is_bot_script = False
        try:
            leverage = float((trading_config or {}).get('leverage') or 1) or 1.0
        except Exception:
            leverage = 1.0
        market_type = str((trading_config or {}).get('market_type') or 'swap').lower()

        def _to_local_qty(usdt_or_ratio: float, ref_price: float) -> float:
            if ref_price is None or ref_price <= 0 or usdt_or_ratio is None or usdt_or_ratio <= 0:
                return 0.0
            if is_bot_script and float(usdt_or_ratio) > 1.0:
                lev = leverage if market_type != 'spot' else 1.0
                return float(usdt_or_ratio) * lev / float(ref_price)
            return float(usdt_or_ratio)

        def _script_qty_extra() -> Dict[str, Any]:
            """Non-bot scripts pass base-asset qty via ctx.buy/sell; honor it live like backtest."""
            if is_bot_script or raw_amt is None:
                return {}
            try:
                if float(raw_amt) > 0 and local_qty > 0:
                    return {'script_base_qty': float(local_qty)}
            except Exception:
                pass
            return {}

        def _emit(sig: Dict[str, Any], reason_override: Optional[str]) -> None:
            if reason_override:
                sig.setdefault('reason', reason_override)
            sig.update(_script_qty_extra())
            out.append(sig)

        for order in list(ctx._orders or []):
            action = str(order.get('action') or '').lower()
            intent = str(order.get('intent') or 'auto').lower()
            reason_hint = order.get('reason')
            try:
                order_price = float(order.get('price') or bar_close or 0)
            except Exception:
                order_price = trig
            raw_amt = order.get('amount')
            pos_ratio = default_ratio
            if raw_amt is not None:
                try:
                    v = float(raw_amt)
                    if v > 0:
                        pos_ratio = v
                except Exception:
                    pass
            ref_px = order_price if order_price > 0 else trig
            local_qty = _to_local_qty(pos_ratio, ref_px)

            if action == 'close':
                # Legacy ctx.close_position() — closes whichever leg is dominant.
                if ctx.position.has_long():
                    closed_qty, avg_entry = ctx.position.close_long()
                    _emit({
                        'type': 'close_long', 'trigger_price': ref_px, 'position_size': 0,
                        'timestamp': ts_i, 'matched_entry_price': avg_entry,
                    }, 'grid_close_all' if is_grid_bot else None)
                if ctx.position.has_short():
                    closed_qty, avg_entry = ctx.position.close_short()
                    _emit({
                        'type': 'close_short', 'trigger_price': ref_px, 'position_size': 0,
                        'timestamp': ts_i, 'matched_entry_price': avg_entry,
                    }, 'grid_close_all' if is_grid_bot else None)
                continue

            # ---- Explicit hedge intents from the new ctx API -----------------
            if intent == 'close_long':
                close_qty = local_qty
                if close_qty <= 0 and ctx.position.has_long():
                    close_qty = ctx.position.long_size
                if close_qty <= 0:
                    continue
                avg_entry = 0.0
                if ctx.position.has_long():
                    _, avg_entry = ctx.position.reduce_long(close_qty)
                _emit({
                    'type': 'close_long', 'trigger_price': ref_px,
                    'position_size': pos_ratio if local_qty else 0,
                    'timestamp': ts_i, 'matched_entry_price': avg_entry,
                }, reason_hint or ('grid_reduce_long' if is_grid_bot else None))
                continue

            if intent == 'close_short':
                close_qty = local_qty
                if close_qty <= 0 and ctx.position.has_short():
                    close_qty = ctx.position.short_size
                if close_qty <= 0:
                    continue
                avg_entry = 0.0
                if ctx.position.has_short():
                    _, avg_entry = ctx.position.reduce_short(close_qty)
                _emit({
                    'type': 'close_short', 'trigger_price': ref_px,
                    'position_size': pos_ratio if local_qty else 0,
                    'timestamp': ts_i, 'matched_entry_price': avg_entry,
                }, reason_hint or ('grid_reduce_short' if is_grid_bot else None))
                continue

            if intent == 'open_long':
                if td not in ('long', 'both'):
                    continue
                sig_type = 'add_long' if ctx.position.has_long() else 'open_long'
                ctx.position.open_long(ref_px, local_qty)
                _emit({
                    'type': sig_type, 'trigger_price': ref_px, 'position_size': pos_ratio,
                    'timestamp': ts_i,
                }, reason_hint)
                continue

            if intent == 'open_short':
                if td not in ('short', 'both'):
                    continue
                sig_type = 'add_short' if ctx.position.has_short() else 'open_short'
                ctx.position.open_short(ref_px, local_qty)
                _emit({
                    'type': sig_type, 'trigger_price': ref_px, 'position_size': pos_ratio,
                    'timestamp': ts_i,
                }, reason_hint)
                continue

            # ---- Legacy ctx.buy / ctx.sell (intent == 'auto') ----------------
            # For grid bots: a buy first covers the short leg if any, then
            # opens/adds the long leg. The order's `amount` is interpreted as
            # ONE atomic step — if the short leg can absorb it we don't also
            # add a long, mirroring the old behaviour but using hedge state.
            if action == 'buy':
                if is_grid_bot:
                    if ctx.position.has_short():
                        closed_qty, avg_entry = ctx.position.reduce_short(local_qty)
                        _emit({
                            'type': 'close_short', 'trigger_price': ref_px,
                            'position_size': pos_ratio,
                            'timestamp': ts_i, 'matched_entry_price': avg_entry,
                        }, reason_hint or 'grid_reduce_short')
                    else:
                        sig_type = 'add_long' if ctx.position.has_long() else 'open_long'
                        ctx.position.open_long(ref_px, local_qty)
                        _emit({
                            'type': sig_type, 'trigger_price': ref_px,
                            'position_size': pos_ratio, 'timestamp': ts_i,
                        }, reason_hint)
                else:
                    if ctx.position.has_short():
                        closed_qty, avg_entry = ctx.position.close_short()
                        _emit({
                            'type': 'close_short', 'trigger_price': ref_px,
                            'position_size': 0, 'timestamp': ts_i,
                            'matched_entry_price': avg_entry,
                        }, reason_hint)
                    if td in ('long', 'both'):
                        sig_type = 'add_long' if ctx.position.has_long() else 'open_long'
                        ctx.position.open_long(ref_px, local_qty)
                        _emit({
                            'type': sig_type, 'trigger_price': ref_px,
                            'position_size': pos_ratio, 'timestamp': ts_i,
                        }, reason_hint)
                continue

            if action == 'sell':
                if is_grid_bot:
                    if ctx.position.has_long():
                        closed_qty, avg_entry = ctx.position.reduce_long(local_qty)
                        _emit({
                            'type': 'close_long', 'trigger_price': ref_px,
                            'position_size': pos_ratio,
                            'timestamp': ts_i, 'matched_entry_price': avg_entry,
                        }, reason_hint or 'grid_reduce_long')
                    else:
                        sig_type = 'add_short' if ctx.position.has_short() else 'open_short'
                        ctx.position.open_short(ref_px, local_qty)
                        _emit({
                            'type': sig_type, 'trigger_price': ref_px,
                            'position_size': pos_ratio, 'timestamp': ts_i,
                        }, reason_hint)
                else:
                    if ctx.position.has_long():
                        closed_qty, avg_entry = ctx.position.close_long()
                        _emit({
                            'type': 'close_long', 'trigger_price': ref_px,
                            'position_size': 0, 'timestamp': ts_i,
                            'matched_entry_price': avg_entry,
                        }, reason_hint)
                    if td in ('short', 'both'):
                        sig_type = 'add_short' if ctx.position.has_short() else 'open_short'
                        ctx.position.open_short(ref_px, local_qty)
                        _emit({
                            'type': sig_type, 'trigger_price': ref_px,
                            'position_size': pos_ratio, 'timestamp': ts_i,
                        }, reason_hint)
        return out

    def _script_evaluate_new_closed_bar(
        self,
        df: pd.DataFrame,
        ctx: StrategyScriptContext,
        on_bar,
        trade_direction: str,
        last_closed_ts: Optional[pd.Timestamp],
        strategy_id: int,
        symbol: str,
        trading_config: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], Optional[pd.Timestamp]]:
        if df is None or len(df) < 2:
            return [], last_closed_ts
        closed_ts = df.index[-2]
        try:
            if last_closed_ts is not None and closed_ts <= last_closed_ts:
                return [], last_closed_ts
        except Exception:
            pass
        df_exec = self._df_to_script_exec_df(df)
        ctx._bars_df = df_exec
        pos = len(df) - 2
        ctx.current_index = int(pos)
        row = df_exec.iloc[pos]
        _init_cap = (trading_config or {}).get('initial_capital')
        _bar_close_for_hydrate = None
        try:
            _bar_close_for_hydrate = float(row.get('close') or 0)
        except Exception:
            _bar_close_for_hydrate = None
        self._hydrate_script_ctx_from_positions(
            ctx, strategy_id, symbol,
            initial_capital=_init_cap,
            current_price=_bar_close_for_hydrate,
            trading_config=trading_config,
        )
        ctx._orders = []
        bar = ScriptBar(
            open=float(row.get('open') or 0),
            high=float(row.get('high') or 0),
            low=float(row.get('low') or 0),
            close=float(row.get('close') or 0),
            volume=float(row.get('volume') or 0),
            timestamp=row.get('time'),
        )
        self._prepare_grid_bot_before_bar(
            ctx, trading_config,
            price=float(bar.close or 0),
            high=float(bar.high or bar.close or 0),
            low=float(bar.low or bar.close or 0),
            is_closed_bar=True,
        )
        try:
            on_bar(ctx, bar)
        except Exception as e:
            logger.error(f"Strategy {strategy_id} script on_bar error: {e}")
            logger.error(traceback.format_exc())
            return [], last_closed_ts
        finally:
            self._flush_ctx_logs(strategy_id, ctx)
        bar_close = float(row.get('close') or 0)
        pending = self._script_orders_to_execution_signals(ctx, trade_direction, bar_close, closed_ts, trading_config)
        try:
            ts_i = int(closed_ts.timestamp())
        except Exception:
            ts_i = int(time.time())
        pending = self._post_process_grid_bot_signals(
            pending, ctx, trading_config, price=bar_close, timestamp=ts_i,
        )
        self._persist_script_runtime_state(strategy_id, closed_ts, ctx._params)
        logger.info(f"Strategy {strategy_id} script closed bar {closed_ts} -> {len(pending)} signal(s)")
        return pending, closed_ts

    def _flush_ctx_logs(self, strategy_id: int, ctx: StrategyScriptContext) -> None:
        """Flush ``ctx.log()`` lines into ``qd_strategy_logs`` for the strategy UI."""
        if ctx is None:
            return
        try:
            logs = ctx.flush_logs()
        except Exception as e:
            logger.debug(f"Strategy {strategy_id} flush_logs skipped: {e}")
            return
        for log in logs:
            append_strategy_log(strategy_id, "info", log)

    def _maybe_log_bar_close_ui(
        self,
        strategy_id: int,
        *,
        symbol: str,
        timeframe: str,
        close_price: float,
        pending_count: int,
        bar_ts: int,
    ) -> None:
        """One UI log line per closed bar so users can see the loop is alive."""
        try:
            sid = int(strategy_id)
            ts = int(bar_ts or 0)
            if ts <= 0:
                return
            last = self._strategy_ui_log_last_tick_ts.get(sid, 0)
            if ts <= last:
                return
            self._strategy_ui_log_last_tick_ts[sid] = ts
            append_strategy_log(
                sid,
                "info",
                f"Bar closed {symbol} {timeframe} close={float(close_price or 0):.6f} "
                f"pending_signals={int(pending_count or 0)}",
            )
        except Exception:
            pass

    def _script_evaluate_in_progress_bar(
        self,
        df: pd.DataFrame,
        ctx: StrategyScriptContext,
        on_bar,
        trade_direction: str,
        strategy_id: int,
        symbol: str,
        trading_config: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Evaluate the forming (last) bar — used when strict_mode is off."""
        if df is None or len(df) < 1:
            return []
        df_exec = self._df_to_script_exec_df(df)
        ctx._bars_df = df_exec
        pos = len(df) - 1
        ctx.current_index = int(pos)
        row = df_exec.iloc[pos]
        _init_cap = (trading_config or {}).get("initial_capital")
        _bar_close_for_hydrate = None
        try:
            _bar_close_for_hydrate = float(row.get("close") or 0)
        except Exception:
            _bar_close_for_hydrate = None
        self._hydrate_script_ctx_from_positions(
            ctx, strategy_id, symbol,
            initial_capital=_init_cap,
            current_price=_bar_close_for_hydrate,
            trading_config=trading_config,
        )
        ctx._orders = []
        bar_ts = df.index[pos]
        bar = ScriptBar(
            open=float(row.get("open") or 0),
            high=float(row.get("high") or 0),
            low=float(row.get("low") or 0),
            close=float(row.get("close") or 0),
            volume=float(row.get("volume") or 0),
            timestamp=row.get("time"),
        )
        self._prepare_grid_bot_before_bar(
            ctx, trading_config,
            price=float(bar.close or 0),
            high=float(bar.high or bar.close or 0),
            low=float(bar.low or bar.close or 0),
            is_closed_bar=False,
        )
        try:
            on_bar(ctx, bar)
        except Exception as e:
            logger.error(f"Strategy {strategy_id} script in-progress on_bar error: {e}")
            logger.error(traceback.format_exc())
            return []
        finally:
            self._flush_ctx_logs(strategy_id, ctx)
        bar_close = float(row.get("close") or 0)
        pending = self._script_orders_to_execution_signals(
            ctx, trade_direction, bar_close, bar_ts, trading_config,
        )
        try:
            ts_i = int(bar_ts.timestamp())
        except Exception:
            ts_i = int(time.time())
        pending = self._post_process_grid_bot_signals(
            pending, ctx, trading_config, price=bar_close, timestamp=ts_i,
        )
        self._persist_script_runtime_state(strategy_id, None, ctx._params)
        if pending:
            logger.info(
                f"Strategy {strategy_id} script in-progress bar {bar_ts} -> {len(pending)} signal(s)"
            )
        return pending
    
    def _run_strategy_loop(self, strategy_id: int):
        """
        策略运行循环
        
        Args:
            strategy_id: 策略ID
        """
        logger.info(f"Strategy {strategy_id} loop starting")
        self._console_print(f"[strategy:{strategy_id}] loop initializing")
        
        # Auto-stop policy: prevent endless error spam when a strategy is no longer runnable
        # (expired API keys, delisted symbols, SaaS禁止本机券商等).
        try:
            max_consecutive_errors = int(os.getenv("STRATEGY_MAX_CONSECUTIVE_ERRORS", "5"))
        except Exception:
            max_consecutive_errors = 5
        if max_consecutive_errors < 1:
            max_consecutive_errors = 1
        consecutive_errors = 0
        exit_reason: str = ""

        def _set_db_stopped_best_effort(reason: str) -> None:
            """Best-effort: mark strategy stopped to avoid zombie 'running' status."""
            try:
                with get_db_connection() as db:
                    cur = db.cursor()
                    cur.execute(
                        "UPDATE qd_strategies_trading SET status = 'stopped' WHERE id = %s AND status = 'running'",
                        (int(strategy_id),),
                    )
                    db.commit()
                    cur.close()
            except Exception:
                pass
            try:
                if reason:
                    append_strategy_log(strategy_id, "error", f"Auto-stopped: {reason}")
            except Exception:
                pass

        def _abort_loop(reason: str) -> None:
            nonlocal exit_reason
            exit_reason = reason
            logger.error(f"Strategy {strategy_id} abort: {reason}")
            _set_db_stopped_best_effort(reason)

        def _is_fatal_error(err: Exception, msg: str) -> bool:
            # Config errors from data sources should stop immediately.
            if isinstance(err, UnsupportedMarketError):
                return True
            m = (msg or "").lower()
            if not m:
                return False
            # IBKR/MT5 disabled in SaaS/cloud installs.
            if "disabled ibkr/mt5" in m or "已关闭 ibkr / mt5" in msg:
                return True
            # Broker gateway not reachable (Docker 127.0.0.1, TWS down, etc.)
            if any(
                t in m
                for t in (
                    "connection refused",
                    "connect call failed",
                    "errno 111",
                    "failed to connect to ibkr",
                    "make sure api port on tws",
                )
            ):
                return True
            # Common auth/permission failures (API key expired/invalid).
            fatal_tokens = [
                "invalid api", "api key", "apikey", "secret", "signature",
                "authentication", "unauthorized", "forbidden", "permission",
                "invalid_key", "invalid key",
                '"code":-2015', "-2015", "50111", "40018",
            ]
            if any(t in m for t in fatal_tokens):
                return True
            # Symbol/product not found (delisted / not supported).
            symbol_tokens = [
                "symbol not found", "unknown symbol", "invalid symbol",
                "instrument not found", "product not found", "does not exist",
                "delist", "delisted", "not supported",
            ]
            if any(t in m for t in symbol_tokens):
                return True
            return False

        try:
            grid_resting_runner = None
            use_grid_resting = False
            # 加载策略配置
            strategy = self._load_strategy(strategy_id)
            if not strategy:
                _abort_loop("strategy not found")
                return
            
            stype = strategy.get('strategy_type') or ''
            if stype not in ('IndicatorStrategy', 'ScriptStrategy'):
                _abort_loop(f"unsupported strategy_type for realtime execution: {stype or '(empty)'}")
                return
            is_script = stype == 'ScriptStrategy'

            # 初始化策略状态
            trading_config = strategy['trading_config']
            normalize_trading_execution_modes(trading_config)
            logger.info(
                f"Strategy {strategy_id} execution modes: strict_mode={trading_config.get('strict_mode')}, "
                f"signal_mode={trading_config.get('signal_mode')}, "
                f"entry_trigger_mode={trading_config.get('entry_trigger_mode')}, "
                f"exit_signal_mode={trading_config.get('exit_signal_mode')}"
            )
            # `strict_mode` opts the strategy into "backtest-equivalent" semantics:
            # closed-bar signals only (drop in-progress bar) and a confirmed exit
            # signal (no aggressive intra-bar close). This reduces the drift
            # between backtest and live but typically delays entries/exits by
            # one full bar. Both knobs default to existing behaviour when
            # strict_mode is unset.
            strict_mode = bool(trading_config.get('strict_mode', False))
            ai_model_config = strategy.get('ai_model_config') or {}
            execution_mode = (strategy.get('execution_mode') or 'signal').strip().lower()
            if execution_mode not in ['signal', 'live']:
                execution_mode = 'signal'
            strategy_mode = (strategy.get('strategy_mode') or 'signal').strip().lower()
            is_bot_mode = strategy_mode == 'bot'
            notification_config = strategy.get('notification_config') or {}
            strategy_name = strategy.get('strategy_name') or f"strategy_{int(strategy_id)}"
            # Strategy owner: used to scope cross-feature notifications (e.g. portfolio
            # linkage) to the user who actually runs this strategy. Without it, a signal
            # from user A's strategy would leak to every user holding the same symbol.
            try:
                strategy_user_id = int(strategy.get('user_id') or 0) or None
            except (TypeError, ValueError):
                strategy_user_id = None
            symbol = trading_config.get('symbol', '')
            timeframe = trading_config.get('timeframe', '1H')
            
            # 安全获取 leverage 和 trade_direction
            try:
                leverage_val = trading_config.get('leverage', 1)
                if isinstance(leverage_val, (list, tuple)):
                    leverage_val = leverage_val[0] if leverage_val else 1
                leverage = float(leverage_val)
            except:
                logger.warning(f"Strategy {strategy_id} invalid leverage format, reset to 1: {trading_config.get('leverage')}")
                leverage = 1.0
            
            # 获取市场类型，严格以策略配置为准，不再通过杠杆反推。
            market_type = trading_config.get('market_type', 'swap')
            if market_type not in ['swap', 'spot']:
                _abort_loop(f"invalid market_type={market_type} (only swap/spot supported)")
                return
            if market_type == 'swap':
                # 合约市场统一使用 swap（永续），避免 futures/delivery 混淆导致持仓/下单查错市场
                logger.info(f"Strategy {strategy_id} derivatives trading; normalize market_type to: swap")
            
            # 根据市场类型限制杠杆
            if market_type == 'spot':
                leverage = 1.0  # 现货固定1倍杠杆
            elif leverage < 1:
                leverage = 1.0
            elif leverage > 125:
                leverage = 125.0
                logger.warning(f"Strategy {strategy_id} leverage > 125; capped to 125")
            
            # 获取交易方向，现货只能做多
            trade_direction = trading_config.get('trade_direction', 'long')
            if market_type == 'spot':
                trade_direction = 'long'  # 现货只能做多
                if isinstance(trading_config, dict):
                    trading_config['trade_direction'] = 'long'
                logger.info(f"Strategy {strategy_id} spot trading; force trade_direction=long")

            # 获取市场类别（Crypto, USStock, Forex, Futures）
            # 这决定了使用哪个数据源来获取价格和K线数据
            market_category = (strategy.get('market_category') or 'Crypto').strip()
            logger.info(f"Strategy {strategy_id} market_category: {market_category}")

            # 安全获取 initial_capital（横截面分支也需要）
            try:
                initial_capital_val = strategy.get('initial_capital', 1000)
                if isinstance(initial_capital_val, (list, tuple)):
                    initial_capital_val = initial_capital_val[0] if initial_capital_val else 1000
                initial_capital = float(initial_capital_val)
            except Exception:
                logger.warning(f"Strategy {strategy_id} invalid initial_capital format, reset to 1000: {strategy.get('initial_capital')}")
                initial_capital = 1000.0

            indicator_id = None
            indicator_code = ''
            strategy_code = ''
            on_init_script = None
            on_bar_script = None

            if is_script:
                strategy_code = (strategy.get('strategy_code') or '').strip()
                if not strategy_code:
                    _abort_loop("strategy_code is empty")
                    return
                if '\\n' in strategy_code and '\n' not in strategy_code:
                    try:
                        decoded = json.loads(f'"{strategy_code}"')
                        if isinstance(decoded, str):
                            strategy_code = decoded
                    except Exception:
                        strategy_code = (
                            strategy_code.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
                            .replace('\\"', '"').replace("\\'", "'").replace('\\\\', '\\')
                        )
                try:
                    on_init_script, on_bar_script = compile_strategy_script_handlers(strategy_code)
                except Exception as e:
                    _abort_loop(f"script compile failed: {e}")
                    logger.error(traceback.format_exc())
                    return
            else:
                indicator_config = strategy['indicator_config']
                indicator_id = indicator_config.get('indicator_id')
                indicator_code = indicator_config.get('indicator_code', '')
                if not indicator_code and indicator_id:
                    indicator_code = self._get_indicator_code_from_db(indicator_id)
                if not indicator_code:
                    _abort_loop("indicator_code is empty")
                    return
                if not isinstance(indicator_code, str):
                    indicator_code = str(indicator_code)
                if '\\n' in indicator_code and '\n' not in indicator_code:
                    try:
                        decoded = json.loads(f'"{indicator_code}"')
                        if isinstance(decoded, str):
                            indicator_code = decoded
                            logger.info(f"Strategy {strategy_id} decoded escaped indicator_code")
                    except Exception as e:
                        logger.warning(f"Strategy {strategy_id} JSON decode failed; falling back to manual unescape: {str(e)}")
                        indicator_code = (
                            indicator_code.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
                            .replace('\\"', '"').replace("\\'", "'").replace('\\\\', '\\')
                        )

                code_cfg = StrategyConfigParser.build_nested_cfg_from_code(indicator_code)
                if code_cfg:
                    trading_config = dict(trading_config or {})
                    trading_config["_strategy_cfg_from_code"] = code_cfg
                    td = code_cfg.get("tradeDirection")
                    if td and not trading_config.get("trade_direction"):
                        trading_config["trade_direction"] = td
                    strategy["trading_config"] = trading_config

            # Resolve credential references before any strategy branch (grid / bots / cross-sectional).
            exchange_config = strategy.get('exchange_config') or {}
            try:
                from app.services.exchange_execution import resolve_exchange_config

                exchange_config = resolve_exchange_config(
                    exchange_config if isinstance(exchange_config, dict) else {},
                    user_id=int(strategy_user_id or strategy.get('user_id') or 1),
                )
                strategy['exchange_config'] = exchange_config
            except Exception as e:
                logger.warning(f"Strategy {strategy_id} resolve exchange_config failed: {e}")
            if isinstance(trading_config, dict):
                trading_config.setdefault('execution_mode', execution_mode)
                if exchange_config:
                    trading_config['exchange_config'] = exchange_config

            # Check if this is a cross-sectional strategy（仅指标策略支持）
            cs_strategy_type = trading_config.get('cs_strategy_type', 'single')
            if (not is_script) and cs_strategy_type == 'cross_sectional':
                self._run_cross_sectional_strategy_loop(
                    strategy_id, strategy, trading_config, strategy['indicator_config'],
                    ai_model_config, execution_mode, notification_config,
                    strategy_name, market_category, market_type, leverage,
                    initial_capital, indicator_code, indicator_id
                )
                return

            if is_script and cs_strategy_type == 'cross_sectional':
                _abort_loop("ScriptStrategy does not support cross_sectional mode")
                return

            # 初始化交易所连接（信号模式下无需真实连接）
            exchange = None

            kline_exchange_id, kline_market_type = self._live_crypto_kline_params(
                market_category=market_category,
                market_type=market_type,
                execution_mode=execution_mode,
                exchange_config=exchange_config,
                trading_config=trading_config,
                user_id=int(strategy_user_id or strategy.get('user_id') or 1),
            )
            self._log_crypto_kline_source(
                strategy_id, market_category, execution_mode, kline_exchange_id, kline_market_type
            )
            if exchange_config and exchange_config.get('api_key') or exchange_config.get('apiKey'):
                try:
                    self._query_exchange_fee_rate(strategy_id, exchange_config, symbol, market_type)
                except Exception as e:
                    logger.debug(f"Strategy {strategy_id} skipped fee-rate query: {e}")

            # ============================================
            # 初始化阶段：获取历史K线并计算指标
            # ============================================
            # logger.info(f"策略 {strategy_id} 初始化：获取历史K线数据...")
            history_limit = int(os.getenv('K_LINE_HISTORY_GET_NUMBER', 500))
            klines = self._fetch_latest_kline(
                symbol, timeframe, limit=history_limit, market_category=market_category,
                exchange_id=kline_exchange_id, market_type=kline_market_type,
            )
            if not klines or len(klines) < 2:
                _abort_loop("failed to fetch K-lines (need at least 2 bars)")
                return
            logger.info(rf'Strategy {strategy_id} history kline number: {len(klines)}')
            
            # 转换为DataFrame
            df = self._klines_to_dataframe(klines)
            if len(df) == 0:
                _abort_loop("K-lines are empty after normalization")
                return

            # ============================================
            # 启动时：同步持仓状态，清理"幽灵持仓"
            # ============================================
            # 即使信号模式下，也要在启动时检查并清理用户在交易所手动平仓但数据库记录还在的情况
            # 这样可以避免策略认为还有持仓而无法执行新的开仓信号
            try:
                logger.info(f"策略 {strategy_id} 启动时检查持仓同步...")
                # 调用持仓同步逻辑（即使signal模式也要检查）
                from app import get_pending_order_worker
                worker = get_pending_order_worker()
                if worker and hasattr(worker, '_sync_positions_best_effort'):
                    worker._sync_positions_best_effort(target_strategy_id=strategy_id)
                    logger.info(f"策略 {strategy_id} 启动时持仓同步完成")
            except Exception as e:
                logger.warning(f"策略 {strategy_id} 启动时持仓同步失败（不影响启动）: {e}")

            # 获取当前持仓最高价（从本地数据库读取）
            current_pos_list = self._get_current_positions(strategy_id, symbol)
            initial_highest = 0.0
            initial_position = 0  # 0=无持仓, 1=多头, -1=空头
            initial_avg_entry_price = 0.0
            initial_position_count = 0
            initial_last_add_price = 0.0
            
            if current_pos_list:
                pos = current_pos_list[0]  # 取第一个持仓（单向持仓模式）
                initial_highest = float(pos.get('highest_price', 0) or 0)
                pos_side = pos.get('side', 'long')
                initial_position = 1 if pos_side == 'long' else -1
                initial_avg_entry_price = float(pos.get('entry_price', 0) or 0)
                initial_position_count = 1  # 简化处理，假设是单笔持仓
                initial_last_add_price = initial_avg_entry_price

            logger.info(
                f"策略 {strategy_id} 持仓快照: count={len(current_pos_list)}, "
                f"position={initial_position}, entry_price={initial_avg_entry_price}, highest={initial_highest}"
            )

            indicator_both_mode = False

            script_ctx = None
            last_script_closed_ts = None
            if is_script:
                script_ctx, last_script_closed_ts = self._init_script_strategy_context(
                    strategy_id, df, trading_config, initial_capital
                )
                if on_init_script:
                    self._hydrate_script_ctx_from_positions(
                        script_ctx, strategy_id, symbol,
                        initial_capital=initial_capital,
                        current_price=(float(df['close'].iloc[-1]) if df is not None and len(df) > 0 else None),
                        trading_config=trading_config,
                    )
                    try:
                        on_init_script(script_ctx)
                    except Exception as e:
                        logger.error(f"Strategy {strategy_id} on_init error: {e}")
                        logger.error(traceback.format_exc())
                    finally:
                        self._flush_ctx_logs(strategy_id, script_ctx)
                pending_signals, last_script_closed_ts = self._script_evaluate_new_closed_bar(
                    df, script_ctx, on_bar_script, trade_direction,
                    last_script_closed_ts, strategy_id, symbol, trading_config,
                )
                if not strict_mode and len(df) > 0:
                    try:
                        init_price = float(df['close'].iloc[-1])
                        rt_df = self._update_dataframe_with_current_price(
                            df.copy(), init_price, timeframe,
                        )
                        ip_sig = self._script_evaluate_in_progress_bar(
                            rt_df, script_ctx, on_bar_script, trade_direction,
                            strategy_id, symbol, trading_config,
                        )
                        if ip_sig:
                            pending_signals = ip_sig
                    except Exception as e:
                        logger.warning(
                            f"Strategy {strategy_id} script init in-progress eval failed: {e}"
                        )
                try:
                    last_kline_time = int(df.index[-1].timestamp())
                except Exception:
                    last_kline_time = int(time.time())
            else:
                indicator_result = self._execute_indicator_with_prices(
                    indicator_code, df, trading_config,
                    initial_highest_price=initial_highest,
                    initial_position=initial_position,
                    initial_avg_entry_price=initial_avg_entry_price,
                    initial_position_count=initial_position_count,
                    initial_last_add_price=initial_last_add_price
                )
                if indicator_result is None:
                    _abort_loop("indicator execution failed")
                    return
                pending_signals = indicator_result.get('pending_signals', [])
                last_kline_time = indicator_result.get('last_kline_time', 0)
                if indicator_result.get('indicator_both_mode'):
                    indicator_both_mode = True

            logger.info(f"Strategy {strategy_id} initialized; pending_signals={len(pending_signals)}")
            if pending_signals:
                logger.info(f"Initial signals: {pending_signals}")

            grid_resting_runner = None
            use_grid_resting = self._is_live_grid_resting(trading_config, execution_mode, market_category)
            if self._bot_type_key(trading_config) == "grid" and execution_mode == "live" and not use_grid_resting:
                _abort_loop(
                    f"Live grid requires resting engine; unsupported market_category={market_category}"
                )
                return
            if use_grid_resting:
                try:
                    init_px = float(df['close'].iloc[-1]) if df is not None and len(df) > 0 else 0.0
                    grid_resting_runner = self._setup_grid_resting_runner(
                        strategy_id,
                        symbol,
                        trading_config,
                        exchange_config,
                        user_id=int(strategy_user_id or strategy.get('user_id') or 1),
                        initial_capital=initial_capital,
                        execution_mode=execution_mode,
                        market_type=market_type,
                        market_category=market_category,
                        leverage=leverage,
                        notification_config=notification_config,
                        kline_exchange_id=kline_exchange_id,
                    )
                    ok_gr, err_gr = grid_resting_runner.startup(init_px, bars_df=df)
                    if not ok_gr:
                        _abort_loop(f"grid resting startup failed: {err_gr}")
                        return
                    if grid_resting_runner.should_stop:
                        _abort_loop("Grid auto-stopped during startup: exchange error")
                        return
                    pending_signals = []
                    append_strategy_log(
                        strategy_id,
                        "info",
                        f"Grid resting live active on {symbol} (limit orders + fill poller)",
                    )
                except Exception as e:
                    _abort_loop(f"grid resting setup failed: {e}")
                    return

            append_strategy_log(
                strategy_id,
                "info",
                f"Live loop ready {symbol} {timeframe}, pending signals: {len(pending_signals or [])}",
            )
            
            # ============================================
            # Main loop: tick cadence (P1-2)
            # ============================================
            # One tick = fetch current price once + evaluate triggers once + (if needed) refresh K-lines / recalc indicator.
            # Note: `pending_orders` scanning stays at 1s (see PendingOrderWorker) to reduce live dispatch latency.
            #
            # Resolution order (first hit wins):
            #   1. trading_config.tick_interval_sec — per-strategy override.
            #   2. Grid/DCA bots default to 1s (their fills are price-cross
            #      driven, a 10s poll would routinely miss grid lines on
            #      volatile pairs).
            #   3. STRATEGY_TICK_INTERVAL_SEC env var, fallback 10s.
            try:
                env_tick = int(os.getenv('STRATEGY_TICK_INTERVAL_SEC', '10'))
            except Exception:
                env_tick = 10

            _bot_type_for_tick = str((trading_config or {}).get('bot_type') or '').strip().lower()
            # Grid bots use a fixed server-side tick (price-driven); not user-configurable.
            if _bot_type_for_tick == 'grid':
                try:
                    tick_interval_sec = max(1, int(os.getenv('GRID_STRATEGY_TICK_SEC', '1')))
                except Exception:
                    tick_interval_sec = 1
            else:
                tick_interval_sec = None
                try:
                    tc_override = (trading_config or {}).get('tick_interval_sec')
                    if tc_override is not None:
                        tick_interval_sec = int(tc_override)
                except Exception:
                    tick_interval_sec = None
                if tick_interval_sec is None and _bot_type_for_tick == 'dca':
                    tick_interval_sec = 1
                if tick_interval_sec is None:
                    tick_interval_sec = env_tick
            if tick_interval_sec < 1:
                tick_interval_sec = 1

            last_tick_time = 0.0

            # 计算K线周期（秒）
            from app.data_sources.base import TIMEFRAME_SECONDS
            timeframe_seconds = TIMEFRAME_SECONDS.get(timeframe, 3600)

            kline_poll_offset = _kline_boundary_poll_offset_sec()
            next_kline_poll_at = next_kline_boundary_poll_ts(
                time.time(), timeframe_seconds, kline_poll_offset,
            )
            logger.info(
                f"Strategy {strategy_id} K-line poll aligned to bar boundaries "
                f"(tf={timeframe}, offset={kline_poll_offset}s, next={datetime.fromtimestamp(next_kline_poll_at).isoformat()})"
            )
            
            while True:
                try:
                    # 检查策略状态
                    if not self._is_strategy_running(strategy_id):
                        logger.info(f"Strategy {strategy_id} stopped")
                        break
                    
                    current_time = time.time()

                    # Sleep until next tick to avoid CPU spin. Cap each
                    # sleep at the tick interval (or 1s, whichever is smaller)
                    # so a stop-strategy command is honoured promptly even on
                    # long intervals.
                    if last_tick_time > 0:
                        sleep_sec = (last_tick_time + tick_interval_sec) - current_time
                        if sleep_sec > 0:
                            time.sleep(min(sleep_sec, max(0.05, min(1.0, float(tick_interval_sec)))))
                            continue
                    last_tick_time = current_time

                    # ============================================
                    # 0. 虚拟持仓模式，无需同步交易所
                    # ============================================
                    # pass
                    
                    # ============================================
                    # 1. Fetch current price once per tick
                    # ============================================
                    current_price = self._fetch_current_price(
                        exchange, symbol, market_type=market_type, market_category=market_category,
                        exchange_id=kline_exchange_id, kline_market_type=kline_market_type,
                    )
                    if current_price is None:
                        logger.warning(f"Strategy {strategy_id} failed to fetch current price for {market_category}:{symbol}")
                        consecutive_errors += 1
                        if consecutive_errors >= max_consecutive_errors:
                            exit_reason = (
                                f"failed to fetch price for {market_category}:{symbol} "
                                f"({consecutive_errors}/{max_consecutive_errors})"
                            )
                            logger.error(f"Strategy {strategy_id} {exit_reason}; stopping")
                            self._console_print(f"[strategy:{strategy_id}] auto-stopping: {exit_reason}")
                            _set_db_stopped_best_effort(exit_reason)
                            break
                        continue

                    # ============================================
                    # 2. 检查是否需要更新K线（对齐 K 线周期边界 + 小偏移后拉取）
                    # ============================================
                    if current_time >= next_kline_poll_at:
                        klines = self._fetch_latest_kline(
                            symbol, timeframe, limit=history_limit, market_category=market_category,
                            exchange_id=kline_exchange_id, market_type=kline_market_type,
                        )
                        try:
                            if klines and len(klines) >= 2:
                                df = self._klines_to_dataframe(klines)
                                if len(df) > 0:
                                    if is_script:
                                        if use_grid_resting and grid_resting_runner is not None:
                                            try:
                                                bar_h = float(df['high'].iloc[-1])
                                                bar_l = float(df['low'].iloc[-1])
                                                grid_resting_runner.tick(
                                                    float(current_price),
                                                    high=bar_h,
                                                    low=bar_l,
                                                    bars_df=df,
                                                    is_closed_bar=True,
                                                )
                                            except Exception as e:
                                                logger.warning(
                                                    f"Strategy {strategy_id} grid resting kline tick error: {e}"
                                                )
                                            pending_signals = []
                                            if grid_resting_runner.should_stop:
                                                exit_reason = "Grid auto-stopped: exchange error"
                                                logger.error(f"Strategy {strategy_id} {exit_reason}")
                                                _set_db_stopped_best_effort(exit_reason)
                                                break
                                            try:
                                                last_kline_time = int(df.index[-1].timestamp())
                                            except Exception:
                                                last_kline_time = int(time.time())
                                        elif self._bot_type_key(trading_config) != "grid":
                                            new_sig, last_script_closed_ts = self._script_evaluate_new_closed_bar(
                                                df, script_ctx, on_bar_script, trade_direction,
                                                last_script_closed_ts, strategy_id, symbol, trading_config,
                                            )
                                            pending_signals = new_sig
                                            if not strict_mode:
                                                try:
                                                    rt_df = self._update_dataframe_with_current_price(
                                                        df.copy(), current_price, timeframe,
                                                    )
                                                    ip_sig = self._script_evaluate_in_progress_bar(
                                                        rt_df, script_ctx, on_bar_script, trade_direction,
                                                        strategy_id, symbol, trading_config,
                                                    )
                                                    if ip_sig:
                                                        pending_signals = ip_sig
                                                except Exception as e:
                                                    logger.warning(
                                                        f"Strategy {strategy_id} script kline in-progress eval failed: {e}"
                                                    )
                                            try:
                                                last_kline_time = int(df.index[-1].timestamp())
                                            except Exception:
                                                last_kline_time = int(time.time())
                                    else:
                                        current_pos_list = self._get_current_positions(strategy_id, symbol)
                                        initial_highest = 0.0
                                        initial_position = 0
                                        initial_avg_entry_price = 0.0
                                        initial_position_count = 0
                                        initial_last_add_price = 0.0

                                        if current_pos_list:
                                            pos = current_pos_list[0]
                                            initial_highest = float(pos.get('highest_price', 0) or 0)
                                            pos_side = pos.get('side', 'long')
                                            initial_position = 1 if pos_side == 'long' else -1
                                            initial_avg_entry_price = float(pos.get('entry_price', 0) or 0)
                                            initial_position_count = 1
                                            initial_last_add_price = initial_avg_entry_price

                                        indicator_result = self._execute_indicator_with_prices(
                                            indicator_code, df, trading_config,
                                            initial_highest_price=initial_highest,
                                            initial_position=initial_position,
                                            initial_avg_entry_price=initial_avg_entry_price,
                                            initial_position_count=initial_position_count,
                                            initial_last_add_price=initial_last_add_price
                                        )
                                        if indicator_result:
                                            pending_signals = indicator_result.get('pending_signals', [])
                                            last_kline_time = indicator_result.get('last_kline_time', 0)
                                            new_hp = indicator_result.get('new_highest_price', 0)

                                            if new_hp > 0 and current_pos_list:
                                                current_close = float(df['close'].iloc[-1])
                                                for p in current_pos_list:
                                                    self._update_position(
                                                        strategy_id, p['symbol'], p['side'],
                                                        float(p['size']), float(p['entry_price']),
                                                        current_close,
                                                        highest_price=new_hp,
                                                        execution_mode=execution_mode,
                                                    )
                                            try:
                                                bar_ts = int(
                                                    indicator_result.get('last_kline_time', 0)
                                                    or df.index[-1].timestamp()
                                                )
                                            except Exception:
                                                bar_ts = 0
                                            self._maybe_log_bar_close_ui(
                                                strategy_id,
                                                symbol=symbol,
                                                timeframe=timeframe,
                                                close_price=float(df['close'].iloc[-1]),
                                                pending_count=len(pending_signals or []),
                                                bar_ts=bar_ts,
                                            )
                        finally:
                            next_kline_poll_at = next_kline_boundary_poll_ts(
                                max(current_time, next_kline_poll_at),
                                timeframe_seconds,
                                kline_poll_offset,
                            )
                    else:
                        # ============================================
                        # 3. 非K线更新 tick
                        # ============================================
                        # 3a. Grid resting live: limit-order engine
                        if use_grid_resting and grid_resting_runner is not None:
                            try:
                                grid_resting_runner.tick(
                                    float(current_price),
                                    high=float(current_price),
                                    low=float(current_price),
                                    bars_df=df if 'df' in locals() else None,
                                )
                                pending_signals = []
                            except Exception as e:
                                logger.warning(f"Strategy {strategy_id} grid resting tick error: {e}")
                            if grid_resting_runner.should_stop:
                                exit_reason = "Grid auto-stopped: exchange error"
                                logger.error(f"Strategy {strategy_id} {exit_reason}")
                                _set_db_stopped_best_effort(exit_reason)
                                break
                        # 3a2. Bot-mode scripts (martingale / DCA tick; grid uses resting engine)
                        elif (
                            is_script
                            and is_bot_mode
                            and on_bar_script
                            and script_ctx is not None
                            and self._bot_type_key(trading_config) != "grid"
                        ):
                            try:
                                self._hydrate_script_ctx_from_positions(
                                    script_ctx, strategy_id, symbol,
                                    initial_capital=initial_capital,
                                    current_price=float(current_price),
                                    trading_config=trading_config,
                                )
                                script_ctx._orders = []
                                tick_bar = ScriptBar(
                                    open=float(current_price),
                                    high=float(current_price),
                                    low=float(current_price),
                                    close=float(current_price),
                                    volume=0,
                                    timestamp=int(time.time()),
                                )
                                self._prepare_grid_bot_before_bar(
                                    script_ctx, trading_config,
                                    price=float(current_price),
                                    high=float(current_price),
                                    low=float(current_price),
                                    is_closed_bar=False,
                                )
                                try:
                                    on_bar_script(script_ctx, tick_bar)
                                finally:
                                    self._flush_ctx_logs(strategy_id, script_ctx)
                                if script_ctx._orders:
                                    tick_ts = pd.Timestamp.now(tz='UTC')
                                    new_sig = self._script_orders_to_execution_signals(
                                        script_ctx, trade_direction, float(current_price), tick_ts, trading_config,
                                    )
                                    try:
                                        tick_ts_i = int(tick_ts.timestamp())
                                    except Exception:
                                        tick_ts_i = int(time.time())
                                    new_sig = self._post_process_grid_bot_signals(
                                        new_sig, script_ctx, trading_config,
                                        price=float(current_price), timestamp=tick_ts_i,
                                    )
                                    if new_sig:
                                        pending_signals = new_sig
                                        self._persist_script_runtime_state(strategy_id, tick_ts, script_ctx._params)
                                        logger.info(f"Strategy {strategy_id} bot tick -> {len(new_sig)} signal(s)")
                                    else:
                                        self._persist_script_runtime_state(strategy_id, None, script_ctx._params)
                                else:
                                    self._persist_script_runtime_state(strategy_id, None, script_ctx._params)
                            except Exception as e:
                                logger.warning(f"Strategy {strategy_id} bot tick on_bar error: {e}")

                        # 3a2. Non-bot scripts: evaluate forming bar when strict mode is off
                        elif (
                            is_script
                            and not is_bot_mode
                            and not strict_mode
                            and on_bar_script
                            and script_ctx is not None
                            and 'df' in locals()
                            and df is not None
                            and len(df) > 0
                        ):
                            try:
                                rt_df = self._update_dataframe_with_current_price(
                                    df.copy(), current_price, timeframe,
                                )
                                new_sig = self._script_evaluate_in_progress_bar(
                                    rt_df, script_ctx, on_bar_script, trade_direction,
                                    strategy_id, symbol, trading_config,
                                )
                                if new_sig:
                                    pending_signals = new_sig
                            except Exception as e:
                                logger.warning(
                                    f"Strategy {strategy_id} script in-progress recompute failed: {e}"
                                )

                        # 3b. Indicator strategies: real-time recompute
                        elif (not is_script) and 'df' in locals() and df is not None and len(df) > 0:
                            try:
                                realtime_df = df.copy()
                                # `strict_mode` (default False) keeps the dataframe
                                # exactly as upstream returned it. The default
                                # behaviour paints the in-progress bar's
                                # open/high/low/close with the current price so
                                # indicators react sooner — this is the largest
                                # source of "backtest vs. live drift" because the
                                # backtester operates on closed bars only. When
                                # users opt into strict mode we additionally
                                # drop the in-progress bar so the indicator sees
                                # the same bar sequence the backtester saw.
                                if strict_mode:
                                    try:
                                        from app.data_sources.base import TIMEFRAME_SECONDS as _TFS
                                        _tf_key = timeframe if timeframe in _TFS else str(timeframe).upper()
                                        _tf_seconds = _TFS.get(_tf_key, 60)
                                        if len(realtime_df) > 1:
                                            _last_ts = float(realtime_df.index[-1].timestamp())
                                            _now_ts = float(time.time())
                                            _current_period_start = int(_now_ts // _tf_seconds) * _tf_seconds
                                            if abs(_last_ts - _current_period_start) < 2:
                                                realtime_df = realtime_df.iloc[:-1].copy()
                                    except Exception as _strict_drop_e:
                                        logger.debug(f"strict_mode last-bar drop skipped: {_strict_drop_e}")
                                else:
                                    realtime_df = self._update_dataframe_with_current_price(realtime_df, current_price, timeframe)

                                current_pos_list = self._get_current_positions(strategy_id, symbol)
                                initial_highest = 0.0
                                initial_position = 0
                                initial_avg_entry_price = 0.0
                                initial_position_count = 0
                                initial_last_add_price = 0.0

                                if current_pos_list:
                                    pos = current_pos_list[0]
                                    initial_highest = float(pos.get('highest_price', 0) or 0)
                                    pos_side = pos.get('side', 'long')
                                    initial_position = 1 if pos_side == 'long' else -1
                                    initial_avg_entry_price = float(pos.get('entry_price', 0) or 0)
                                    initial_position_count = 1
                                    initial_last_add_price = initial_avg_entry_price

                                indicator_result = self._execute_indicator_with_prices(
                                    indicator_code, realtime_df, trading_config,
                                    initial_highest_price=initial_highest,
                                    initial_position=initial_position,
                                    initial_avg_entry_price=initial_avg_entry_price,
                                    initial_position_count=initial_position_count,
                                    initial_last_add_price=initial_last_add_price
                                )
                                if indicator_result:
                                    pending_signals = indicator_result.get('pending_signals', [])
                                    new_hp = indicator_result.get('new_highest_price', 0)
                                    if indicator_result.get('indicator_both_mode'):
                                        indicator_both_mode = True

                                    if new_hp > 0 and current_pos_list:
                                        for p in current_pos_list:
                                            self._update_position(
                                                strategy_id, p['symbol'], p['side'],
                                                float(p['size']), float(p['entry_price']),
                                                current_price,
                                                highest_price=new_hp,
                                                execution_mode=execution_mode,
                                            )
                            except Exception as e:
                                logger.warning(f"Strategy {strategy_id} realtime indicator recompute failed: {str(e)}")
                    
                    # ============================================
                    # 4. Evaluate triggers once per tick
                    # ============================================
                    # 优化点4: 信号有效期清理 (Signal Expiration)
                    current_ts = int(time.time())
                    if pending_signals:
                        expiration_threshold = timeframe_seconds * 2
                        valid_signals = []
                        for s in pending_signals:
                            signal_time = s.get('timestamp', 0)
                            if signal_time == 0 or (current_ts - signal_time) < expiration_threshold:
                                valid_signals.append(s)
                            else:
                                logger.warning(f"Signal expired and removed: {s}")
                        if len(valid_signals) != len(pending_signals):
                            pending_signals = valid_signals

                    # Unified cadence log: at most once per tick.
                    if pending_signals:
                        logger.info(f"[monitoring] strategy={strategy_id} price={current_price}, pending_signals={len(pending_signals)}")

                    # 检查是否有待触发的信号
                    triggered_signals = []
                    signals_to_remove = []
                        
                    for signal_info in pending_signals:
                        signal_type = signal_info.get('type')  # 'open_long', 'close_long', 'open_short', 'close_short'
                        trigger_price = signal_info.get('trigger_price', 0)
                        
                        # 检查价格是否触发
                        triggered = False

                        # Bot-mode scripts (grid / DCA / martingale) handle their own
                        # timing inside on_bar; execute signals immediately.
                        if is_bot_mode:
                            triggered = True

                        # 【关键修复】平仓/止损止盈信号默认“立即触发”
                        exit_trigger_mode = trading_config.get('exit_trigger_mode', 'immediate')  # 'immediate' or 'price'
                        if signal_type in ['close_long', 'close_short'] and exit_trigger_mode == 'immediate':
                            triggered = True
                        
                        # 【可选】开仓/加仓信号是否“立即触发”
                        entry_trigger_mode = trading_config.get('entry_trigger_mode', 'price')  # 'price' or 'immediate'
                        if signal_type in ['open_long', 'open_short', 'add_long', 'add_short'] and entry_trigger_mode == 'immediate':
                            triggered = True

                        if trigger_price > 0:
                            if signal_type in ['open_long', 'close_short', 'add_long']:
                                if current_price >= trigger_price:
                                    triggered = True
                            elif signal_type in ['open_short', 'close_long', 'add_short']:
                                if current_price <= trigger_price:
                                    triggered = True
                        else:
                            triggered = True
                        
                        if triggered:
                            triggered_signals.append(signal_info)
                            signals_to_remove.append(signal_info)

                    # ============================================
                    # 4.1 Server-side exits (config-driven): SL / TP / trailing
                    # ============================================
                    # Note: stop-loss is only applied when stop_loss_pct > 0. No default fallback.
                    risk_tp = self._server_side_take_profit_or_trailing_signal(
                        strategy_id=strategy_id,
                        symbol=symbol,
                        current_price=float(current_price),
                        market_type=market_type,
                        leverage=float(leverage),
                        trading_config=trading_config,
                        timeframe_seconds=int(timeframe_seconds or 60),
                        execution_mode=execution_mode,
                    )
                    if risk_tp:
                        triggered_signals.append(risk_tp)
                        # Server exit already handled the leg; drop stale indicator close_* pending.
                        risk_close = str(risk_tp.get('type') or '').strip().lower()
                        if risk_close in ('close_long', 'close_short'):
                            pending_signals = [
                                s for s in pending_signals
                                if str(s.get('type') or '').strip().lower() != risk_close
                            ]

                    risk_sl = self._server_side_stop_loss_signal(
                        strategy_id=strategy_id,
                        symbol=symbol,
                        current_price=float(current_price),
                        market_type=market_type,
                        leverage=float(leverage),
                        trading_config=trading_config,
                        timeframe_seconds=int(timeframe_seconds or 60),
                    )
                    if risk_sl:
                        triggered_signals.append(risk_sl)
                        risk_close = str(risk_sl.get('type') or '').strip().lower()
                        if risk_close in ('close_long', 'close_short'):
                            pending_signals = [
                                s for s in pending_signals
                                if str(s.get('type') or '').strip().lower() != risk_close
                            ]

                    # Grid / DCA bot risk exits — resting grid handles risk inside runner.tick.
                    grid_exits = []
                    if not use_grid_resting:
                        grid_exits = self._grid_bot_risk_exits(
                            strategy_id=strategy_id,
                            symbol=symbol,
                            current_price=float(current_price),
                            trading_config=trading_config,
                            timeframe_seconds=int(timeframe_seconds or 60),
                            initial_capital=float(initial_capital or 0),
                        )
                    if grid_exits:
                        triggered_signals.extend(grid_exits)
                        types_to_drop = {
                            str(s.get('type') or '').strip().lower() for s in grid_exits
                        }
                        if types_to_drop:
                            pending_signals = [
                                s for s in pending_signals
                                if str(s.get('type') or '').strip().lower() not in types_to_drop
                            ]

                    # 从待触发列表中移除已触发的信号
                    for signal_info in signals_to_remove:
                        if signal_info in pending_signals:
                            pending_signals.remove(signal_info)
                        
                    # 执行触发的信号
                    if triggered_signals:
                        logger.info(f"Strategy {strategy_id} triggered signals: {triggered_signals}")

                        current_positions = self._get_current_positions(strategy_id, symbol)
                        state = self._effective_position_state(strategy_id, symbol, current_positions)

                        # Strict state machine + priority:
                        # - Only allow signals matching current state (flat/long/short).
                        # - Always prefer close_* over open_*/add_*.
                        # - Bot-mode may need multiple state transitions in one tick
                        #   (e.g. grid partial take-profit / reverse across levels),
                        #   while indicator mode still executes at most one signal.
                        if is_bot_mode:
                            candidates = list(triggered_signals)
                        else:
                            candidates = [
                                s for s in triggered_signals
                                if self._is_signal_allowed(
                                    state,
                                    s.get('type'),
                                    indicator_both_mode=indicator_both_mode,
                                )
                            ]

                        # If both directions are present while flat, choose by trade_direction (deterministic).
                        if state == "flat" and candidates:
                            td = (trade_direction or "both").strip().lower()
                            if td == "long":
                                candidates = [s for s in candidates if s.get("type") == "open_long"]
                            elif td == "short":
                                candidates = [s for s in candidates if s.get("type") == "open_short"]

                        candidates = sorted(
                            candidates,
                            key=lambda s: (
                                self._signal_priority(s.get("type")),
                                int(s.get("timestamp") or 0),
                                str(s.get("type") or ""),
                            ),
                        )

                        now_i = int(time.time())
                        execution_batch: List[Dict[str, Any]] = []
                        for s in candidates:
                            stype = s.get("type")
                            sts = int(s.get("timestamp") or 0)
                            if (not is_bot_mode) and self._should_skip_signal_once_per_candle(
                                strategy_id=strategy_id,
                                symbol=symbol,
                                signal_type=str(stype or ""),
                                signal_ts=sts,
                                timeframe_seconds=int(timeframe_seconds or 60),
                                now_ts=now_i,
                            ):
                                continue
                            execution_batch.append(s)
                            if not is_bot_mode:
                                break

                        for selected in execution_batch:
                            signal_type = selected.get('type')
                            position_size = selected.get('position_size', 0)
                            trigger_price = selected.get('trigger_price', current_price)
                            execute_price = trigger_price if trigger_price > 0 else current_price
                            signal_ts = int(selected.get("timestamp") or 0)
                            current_positions = self._get_current_positions(strategy_id, symbol)

                            if not self._is_signal_allowed(
                                self._effective_position_state(strategy_id, symbol, current_positions),
                                signal_type,
                                indicator_both_mode=indicator_both_mode,
                            ):
                                continue

                            ok = self._execute_signal(
                                strategy_id=strategy_id,
                                strategy_name=strategy_name,
                                exchange=exchange,
                                symbol=symbol,
                                current_price=execute_price,
                                signal_type=signal_type,
                                position_size=position_size,
                                signal_ts=signal_ts,
                                current_positions=current_positions,
                                trade_direction=trade_direction,
                                leverage=leverage,
                                initial_capital=initial_capital,
                                market_type=market_type,
                                market_category=market_category,
                                price_exchange_id=kline_exchange_id,
                                execution_mode=execution_mode,
                                notification_config=notification_config,
                                trading_config=trading_config,
                                ai_model_config=ai_model_config,
                                stop_loss_price=selected.get("stop_loss_price"),
                                take_profit_price=selected.get("take_profit_price"),
                                signal_reason=selected.get("reason"),
                                trailing_stop_price=selected.get("trailing_stop_price"),
                                script_base_qty=selected.get("script_base_qty"),
                            )
                            if ok:
                                logger.info(f"Strategy {strategy_id} signal executed: {signal_type} @ {execute_price}")
                                append_strategy_log(
                                    strategy_id,
                                    "signal",
                                    f"Signal submitted: {signal_type} @ {float(execute_price or 0):.6f}{self._signal_reason_log_suffix(selected)}",
                                )
                                # Notify portfolio positions linked to this symbol.
                                # IMPORTANT: scope to the strategy owner only. Without
                                # `user_id`, the callee would fan out to every user
                                # holding the same symbol and leak the strategy name /
                                # signal details across tenants.
                                try:
                                    from app.services.portfolio_monitor import notify_strategy_signal_for_positions
                                    if strategy_user_id:
                                        notify_strategy_signal_for_positions(
                                            market=market_type or 'Crypto',
                                            symbol=symbol,
                                            signal_type=signal_type,
                                            signal_detail=f"Strategy: {strategy_name}\nSignal: {signal_type}\nPrice: {execute_price:.4f}",
                                            user_id=strategy_user_id,
                                        )
                                    else:
                                        logger.warning(
                                            f"Strategy {strategy_id} missing owner user_id; "
                                            f"skipping portfolio linkage notification to avoid cross-user broadcast"
                                        )
                                except Exception as link_e:
                                    logger.warning(f"Strategy signal linkage notification failed: {link_e}")
                            else:
                                logger.warning(f"Strategy {strategy_id} signal rejected/failed: {signal_type}")
                                append_strategy_log(
                                    strategy_id,
                                    "error",
                                    f"Signal rejected or not executed: {signal_type}",
                                )

                    # Update positions once per tick.
                    self._update_positions(strategy_id, symbol, current_price)

                    # Heartbeat for UI observability (once per tick).
                    self._console_print(
                        f"[strategy:{strategy_id}] tick price={float(current_price or 0.0):.8f} pending_signals={len(pending_signals or [])}"
                    )
                    # Tick heartbeat kept for console only; no longer persisted to qd_strategy_logs.

                    # Successful tick: reset consecutive error counter.
                    consecutive_errors = 0
                    
                except Exception as e:
                    msg = str(e)
                    consecutive_errors += 1
                    logger.error(f"Strategy {strategy_id} loop error ({consecutive_errors}/{max_consecutive_errors}): {msg}")
                    logger.error(traceback.format_exc())
                    self._console_print(f"[strategy:{strategy_id}] loop error: {e}")
                    try:
                        append_strategy_log(strategy_id, "error", f"Loop error: {e}")
                    except Exception:
                        pass

                    fatal = _is_fatal_error(e, msg)
                    if fatal or consecutive_errors >= max_consecutive_errors:
                        if fatal and isinstance(e, UnsupportedMarketError):
                            exit_reason = (
                                f"Unsupported market type: {getattr(e, 'market', '')}. "
                                f"Please set strategy market_category/market to one of: "
                                f"Crypto/USStock/CNStock/HKStock/Forex/Futures/MOEX."
                            )
                        else:
                            exit_reason = msg if fatal else f"too many consecutive errors: {consecutive_errors}/{max_consecutive_errors}"
                        logger.error(f"Strategy {strategy_id} auto-stopping due to {'fatal error' if fatal else 'error threshold'}: {exit_reason}")
                        self._console_print(f"[strategy:{strategy_id}] auto-stopping: {exit_reason}")
                        _set_db_stopped_best_effort(exit_reason)
                        break

                    time.sleep(5)
                    
        except Exception as e:
            logger.error(f"Strategy {strategy_id} crashed: {str(e)}")
            logger.error(traceback.format_exc())
            self._console_print(f"[strategy:{strategy_id}] fatal error: {e}")
            try:
                append_strategy_log(strategy_id, "error", f"Strategy thread fatal error: {e}")
            except Exception:
                pass
            # Ensure DB state doesn't stay "running" on fatal crash.
            try:
                exit_reason = exit_reason or f"thread fatal error: {e}"
                _set_db_stopped_best_effort(exit_reason)
            except Exception:
                pass
        finally:
            try:
                if grid_resting_runner is not None:
                    grid_resting_runner.shutdown()
            except Exception:
                pass
            # 清理
            try:
                self._last_exit_reason[int(strategy_id)] = (exit_reason or "strategy thread exited").strip()
            except Exception:
                pass
            with self.lock:
                if strategy_id in self.running_strategies:
                    del self.running_strategies[strategy_id]
            # If the thread exited but DB still says running, mark it stopped to avoid zombie status.
            try:
                with get_db_connection() as db:
                    cur = db.cursor()
                    cur.execute("SELECT status FROM qd_strategies_trading WHERE id = %s", (int(strategy_id),))
                    row = cur.fetchone() or {}
                    cur.close()
                if (row.get("status") or "").strip().lower() == "running":
                    _set_db_stopped_best_effort(exit_reason or "strategy thread exited")
            except Exception:
                pass
            self._console_print(f"[strategy:{strategy_id}] loop exited")
            logger.info(f"Strategy {strategy_id} loop exited")
            try:
                append_strategy_log(strategy_id, "info", "Strategy execution loop exited")
            except Exception:
                pass
    
    def _sync_positions_with_exchange(self, strategy_id: int, exchange: Any, symbol: str, market_type: str):
        """
        [Depracated] 信号模式下无需同步交易所持仓
        """
        pass

    def _load_strategy(self, strategy_id: int) -> Optional[Dict[str, Any]]:
        """Load strategy config (local deployment: no encryption/decryption)."""
        try:
            with get_db_connection() as db:
                cursor = db.cursor()
                query = """
                    SELECT
                        id, user_id, strategy_name, strategy_type, status,
                        initial_capital, leverage, decide_interval,
                        execution_mode, notification_config,
                        indicator_config, exchange_config, trading_config, ai_model_config,
                        market_category, strategy_mode, strategy_code
                    FROM qd_strategies_trading
                    WHERE id = %s
                """
                cursor.execute(query, (strategy_id,))
                strategy = cursor.fetchone()
                cursor.close()
            
            if strategy:
                # 解析JSON字段
                for field in ['indicator_config', 'trading_config', 'notification_config', 'ai_model_config']:
                    if isinstance(strategy.get(field), str):
                        try:
                            strategy[field] = json.loads(strategy[field])
                        except:
                            strategy[field] = {}
                
                # exchange_config: local deployment stores plaintext JSON
                exchange_config_str = strategy.get('exchange_config', '{}')
                if isinstance(exchange_config_str, str) and exchange_config_str:
                    try:
                        strategy['exchange_config'] = json.loads(exchange_config_str)
                    except Exception as e:
                        logger.error(f"Strategy {strategy_id} failed to parse exchange_config: {str(e)}")
                        # 尝试直接解析 JSON（向后兼容）
                        try:
                            strategy['exchange_config'] = json.loads(exchange_config_str)
                        except:
                            strategy['exchange_config'] = {}
                else:
                    strategy['exchange_config'] = {}
            
            return strategy
            
        except Exception as e:
            logger.error(f"Failed to load strategy config: {str(e)}")
            return None
    
    def _is_strategy_running(self, strategy_id: int) -> bool:
        """
        检查策略是否在运行
        同时检查数据库状态和线程状态，避免重启后状态不一致
        """
        try:
            # 1. 检查数据库状态
            with get_db_connection() as db:
                cursor = db.cursor()
                cursor.execute(
                    "SELECT status FROM qd_strategies_trading WHERE id = %s",
                    (strategy_id,)
                )
                result = cursor.fetchone()
                cursor.close()
                db_status = result and result.get('status') == 'running'
            
            # 2. 检查线程是否真的在运行
            with self.lock:
                thread = self.running_strategies.get(strategy_id)
                thread_running = thread is not None and thread.is_alive()
            
            # 3. 如果数据库状态是running但线程不在运行，说明状态不一致（可能是重启后恢复失败）
            if db_status and not thread_running:
                logger.warning(f"Strategy {strategy_id} status mismatch: DB=running but thread not running. Updating DB status to stopped.")
                # 更新数据库状态为stopped，避免策略"僵尸"状态
                try:
                    with get_db_connection() as db:
                        cursor = db.cursor()
                        cursor.execute(
                            "UPDATE qd_strategies_trading SET status = 'stopped' WHERE id = %s",
                            (strategy_id,)
                        )
                        db.commit()
                        cursor.close()
                except Exception as e:
                    logger.error(f"Failed to update strategy {strategy_id} status to stopped: {e}")
                return False
            
            # 4. 只有数据库状态和线程状态都一致时才返回True
            return db_status and thread_running
        except Exception as e:
            logger.error(f"Error checking strategy {strategy_id} running status: {e}")
            return False
    
    def _init_exchange(
        self,
        exchange_config: Dict[str, Any],
        market_type: str = None,
        leverage: float = None,
        strategy_id: int = None
    ) -> Any:
        """
        占位：策略线程内不创建交易所 SDK 实例。

        实盘下单不经过本方法。信号经 _execute_exchange_order 写入 pending_orders，
        由 PendingOrderWorker 使用 app.services.live_trading 下的直连 REST 客户端执行。
        K 线/现价由 KlineService、DataSourceFactory 等数据层提供（该层可能使用 ccxt 拉行情，与下单解耦）。
        """
        return None
    
    def _query_exchange_fee_rate(
        self,
        strategy_id: int,
        exchange_config: Dict[str, Any],
        symbol: str,
        market_type: str = "swap",
    ) -> Optional[Dict[str, float]]:
        """Query and cache the account's real fee-rate from the exchange."""
        with self._exchange_fee_cache_lock:
            if strategy_id in self._exchange_fee_cache:
                return self._exchange_fee_cache[strategy_id]
        try:
            from app.services.live_trading.factory import query_fee_rate
            result = query_fee_rate(exchange_config, symbol, market_type=market_type)
            with self._exchange_fee_cache_lock:
                self._exchange_fee_cache[strategy_id] = result
            if result:
                logger.info(f"Strategy {strategy_id} exchange fee rate: maker={result['maker']}, taker={result['taker']}")
            return result
        except Exception as e:
            logger.warning(f"Strategy {strategy_id} failed to query exchange fee rate: {e}")
            with self._exchange_fee_cache_lock:
                self._exchange_fee_cache[strategy_id] = None
            return None

    @staticmethod
    def _live_crypto_kline_params(
        *,
        market_category: str,
        market_type: str,
        execution_mode: str,
        exchange_config: Optional[Dict[str, Any]],
        trading_config: Optional[Dict[str, Any]] = None,
        user_id: int = 1,
    ) -> Tuple[Optional[str], Optional[str]]:
        """加密货币策略（signal/live）：有交易所则按绑定所拉 K 线；否则走 Settings 全局数据源。"""
        if (market_category or "").strip() != "Crypto":
            return None, None
        mode = (execution_mode or "").strip().lower()
        if mode not in ("live", "signal"):
            return None, None
        from app.data_sources.crypto import resolve_crypto_venue

        cfg = exchange_config if isinstance(exchange_config, dict) else {}
        cred_ref = cfg.get("credential_id") or cfg.get("credentials_id")
        if cred_ref and not (
            cfg.get("exchange_id") or cfg.get("exchangeId") or cfg.get("exchange")
        ):
            try:
                from app.services.exchange_execution import resolve_exchange_config

                cfg = resolve_exchange_config(cfg, user_id=int(user_id or 1))
            except Exception:
                pass

        ex, mt = resolve_crypto_venue(
            exchange_config=cfg,
            trading_config=trading_config,
            market_type=market_type,
        )
        return ex, mt

    @staticmethod
    def _log_crypto_kline_source(
        strategy_id: int,
        market_category: str,
        execution_mode: str,
        kline_exchange_id: Optional[str],
        kline_market_type: Optional[str],
    ) -> None:
        if (market_category or "").strip() != "Crypto":
            return
        mode = (execution_mode or "").strip().lower()
        if mode not in ("live", "signal"):
            return
        if kline_exchange_id:
            logger.info(
                f"Strategy {strategy_id} crypto K-line ({mode}): "
                f"{kline_exchange_id}/{kline_market_type}"
            )
            return
        try:
            from app.config.data_sources import CCXTConfig

            default_ex = (CCXTConfig.DEFAULT_EXCHANGE or "binance").strip().lower()
        except Exception:
            default_ex = "binance"
        if mode == "signal":
            logger.info(
                f"Strategy {strategy_id} signal mode: no exchange selected; "
                f"K-lines use Settings default data source ({default_ex})"
            )
        else:
            logger.warning(
                f"Strategy {strategy_id} live mode: missing exchange_id; "
                f"K-lines fall back to Settings default ({default_ex}), "
                "may differ from execution venue — bind an exchange for live trading"
            )

    def _fetch_latest_kline(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        market_category: str = "Crypto",
        exchange_id: Optional[str] = None,
        market_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """获取最新K线数据（优先从缓存获取）
        
        Args:
            symbol: 交易对/代码
            timeframe: 时间周期
            limit: 数据条数
            market_category: 市场类型 (Crypto, USStock, Forex, Futures)
            exchange_id: 实盘加密货币 — 策略绑定交易所
            market_type: 实盘加密货币 — spot 或 swap
        """
        try:
            # 使用 KlineService 获取K线数据（自动处理缓存）
            return self.kline_service.get_kline(
                market=market_category,
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                before_time=int(time.time()),
                exchange_id=exchange_id,
                market_type=market_type,
            )
        except Exception as e:
            logger.error(f"Failed to fetch K-lines for {market_category}:{symbol}: {str(e)}")
            return []
    
    def _fetch_current_price(
        self,
        exchange: Any,
        symbol: str,
        market_type: str = None,
        market_category: str = "Crypto",
        exchange_id: Optional[str] = None,
        kline_market_type: Optional[str] = None,
    ) -> Optional[float]:
        """获取当前价格 (根据 market_category 选择正确的数据源)
        
        Args:
            exchange: 交易所实例（信号模式下为 None）
            symbol: 交易对/代码
            market_type: 交易类型 (swap/spot)
            market_category: 市场类型 (Crypto, USStock, Forex, Futures)
            exchange_id: 实盘加密货币 — 策略绑定交易所
            kline_market_type: 实盘加密货币 — spot 或 swap（与 K 线一致）
        """
        ex_key = (exchange_id or "").strip().lower()
        mt_key = (kline_market_type or market_type or "").strip().lower()
        # Local in-memory cache first
        cache_key = f"{market_category}:{ex_key}:{mt_key}:{(symbol or '').strip().upper()}"
        if cache_key and self._price_cache_ttl_sec > 0:
            now = time.time()
            try:
                with self._price_cache_lock:
                    item = self._price_cache.get(cache_key)
                    if item:
                        price, expiry = item
                        if expiry > now:
                            return float(price)
                        # expired
                        del self._price_cache[cache_key]
            except Exception:
                pass
            
        try:
            # 根据 market_category 选择正确的数据源
            # 支持: Crypto, USStock, Forex, Futures
            ticker = DataSourceFactory.get_ticker(
                market_category, symbol, exchange_id=exchange_id, market_type=kline_market_type or market_type
            )
            if ticker:
                price = float(ticker.get('last') or ticker.get('close') or 0)
                if price > 0:
                    if cache_key and self._price_cache_ttl_sec > 0:
                        try:
                            with self._price_cache_lock:
                                self._price_cache[cache_key] = (float(price), time.time() + self._price_cache_ttl_sec)
                        except Exception:
                            pass
                    return price
        except Exception as e:
            logger.warning(f"Failed to fetch price for {market_category}:{symbol}: {e}")
            
        return None

    def _server_side_stop_loss_signal(
        self,
        strategy_id: int,
        symbol: str,
        current_price: float,
        market_type: str,
        leverage: float,
        trading_config: Dict[str, Any],
        timeframe_seconds: int,
    ) -> Optional[Dict[str, Any]]:
        """
        服务端兜底止损：当价格穿透止损线时，直接生成 close_long/close_short 信号。

        目的：防止“指标回放逻辑导致最后一根K线没有 close_* 信号”或“插针反弹导致二次触发条件不满足”时不止损。
        """
        try:
            if trading_config is None:
                return None

            # Grid / DCA bots use a different risk model — their entry_price is
            # a sliding average across many fills, so "price vs entry %" is
            # meaningless. They are handled by ``_grid_bot_risk_exits`` which is
            # invoked separately in the strategy loop.
            bot_type = str((trading_config or {}).get('bot_type') or '').strip().lower()
            if bot_type in ('grid', 'dca'):
                return None

            if not self._is_server_side_exit_enabled(trading_config, 'enable_server_side_stop_loss'):
                return None

            # 获取当前持仓（使用本地数据库记录作为风控依据）
            current_positions = self._get_current_positions(strategy_id, symbol)
            if not current_positions:
                return None

            sl = float(self._risk_params_from_trading_config(trading_config).get("stop_loss_ratio") or 0)
            if sl <= 0:
                return None

            now_ts = int(time.time())
            tf = int(timeframe_seconds or 60)
            candle_ts = int(now_ts // tf) * tf

            for pos in current_positions:
                side = (pos.get('side') or '').strip().lower()
                if side not in ('long', 'short'):
                    continue

                entry_price = float(pos.get('entry_price', 0) or 0)
                if entry_price <= 0 or current_price <= 0:
                    continue

                if side == 'long':
                    stop_line = entry_price * (1 - sl)
                    if current_price <= stop_line:
                        return {
                            'type': 'close_long',
                            'trigger_price': 0,
                            'position_size': 0,
                            'timestamp': candle_ts,
                            'reason': 'server_stop_loss',
                            'stop_loss_price': stop_line,
                        }
                else:
                    stop_line = entry_price * (1 + sl)
                    if current_price >= stop_line:
                        return {
                            'type': 'close_short',
                            'trigger_price': 0,
                            'position_size': 0,
                            'timestamp': candle_ts,
                            'reason': 'server_stop_loss',
                            'stop_loss_price': stop_line,
                        }

            return None
        except Exception as e:
            logger.warning(f"Strategy {strategy_id} server-side stop-loss check failed: {str(e)}")
            return None

    def _server_side_take_profit_or_trailing_signal(
        self,
        strategy_id: int,
        symbol: str,
        current_price: float,
        market_type: str,
        leverage: float,
        trading_config: Dict[str, Any],
        timeframe_seconds: int,
        execution_mode: str = "signal",
    ) -> Optional[Dict[str, Any]]:
        """
        Server-side exits driven by trading_config / @strategy code annotations:
        - Fixed take-profit: takeProfitPct
        - Trailing stop: trailingEnabled + trailingStopPct + trailingActivationPct

        Semantics align with BacktestService:
        - Percentages are the underlying's % price move (0.001 = 0.1%).
        - Leverage does NOT divide or scale trigger thresholds.
        - When trailing is enabled, fixed take-profit is disabled to avoid ambiguity.
        """
        try:
            if not trading_config:
                return None

            bot_type = str((trading_config or {}).get('bot_type') or '').strip().lower()
            if bot_type in ('grid', 'dca'):
                # Grid / DCA bots: see ``_grid_bot_risk_exits``.
                return None

            if not self._is_server_side_exit_enabled(trading_config, 'enable_server_side_take_profit'):
                return None

            current_positions = self._get_current_positions(strategy_id, symbol)
            if not current_positions:
                return None

            # TP / trailing are the underlying's % price move; leverage does not
            # affect trigger thresholds (only PnL magnitude / liquidation).
            risk_params = self._risk_params_from_trading_config(trading_config)
            tp = float(risk_params.get("take_profit_ratio") or 0)
            trailing_enabled = bool(risk_params.get("trailing_enabled"))
            trailing_pct = float(risk_params.get("trailing_stop_ratio") or 0)
            trailing_act = float(risk_params.get("trailing_activation_ratio") or 0)

            tp_eff = tp if tp > 0 else 0.0
            trailing_pct_eff = trailing_pct if trailing_pct > 0 else 0.0
            trailing_act_eff = trailing_act if trailing_act > 0 else 0.0

            # Conflict rule: when trailing is enabled, fixed TP is disabled.
            if trailing_enabled and trailing_pct_eff > 0:
                tp_eff = 0.0
                if trailing_act_eff <= 0 and tp > 0:
                    trailing_act_eff = tp

            now_ts = int(time.time())
            tf = int(timeframe_seconds or 60)
            candle_ts = int(now_ts // tf) * tf

            for pos in current_positions:
                side = (pos.get('side') or '').strip().lower()
                if side not in ('long', 'short'):
                    continue

                entry_price = float(pos.get('entry_price', 0) or 0)
                if entry_price <= 0 or current_price <= 0:
                    continue

                try:
                    hp = float(pos.get('highest_price') or 0.0)
                except Exception:
                    hp = 0.0
                try:
                    lp = float(pos.get('lowest_price') or 0.0)
                except Exception:
                    lp = 0.0

                if hp <= 0:
                    hp = entry_price
                hp = max(hp, float(current_price))

                if lp <= 0:
                    lp = entry_price
                lp = min(lp, float(current_price))

                try:
                    self._update_position(
                        strategy_id=strategy_id,
                        symbol=pos.get('symbol') or symbol,
                        side=side,
                        size=float(pos.get('size') or 0.0),
                        entry_price=entry_price,
                        current_price=float(current_price),
                        highest_price=hp,
                        lowest_price=lp,
                        execution_mode=execution_mode,
                    )
                except Exception:
                    pass

                if trailing_enabled and trailing_pct_eff > 0:
                    if side == 'long':
                        active = True
                        if trailing_act_eff > 0:
                            active = hp >= entry_price * (1 + trailing_act_eff)
                        if active:
                            stop_line = hp * (1 - trailing_pct_eff)
                            if current_price <= stop_line:
                                return {
                                    'type': 'close_long',
                                    'trigger_price': 0,
                                    'position_size': 0,
                                    'timestamp': candle_ts,
                                    'reason': 'server_trailing_stop',
                                    'trailing_stop_price': stop_line,
                                    'highest_price': hp,
                                }
                    else:
                        active = True
                        if trailing_act_eff > 0:
                            active = lp <= entry_price * (1 - trailing_act_eff)
                        if active:
                            stop_line = lp * (1 + trailing_pct_eff)
                            if current_price >= stop_line:
                                return {
                                    'type': 'close_short',
                                    'trigger_price': 0,
                                    'position_size': 0,
                                    'timestamp': candle_ts,
                                    'reason': 'server_trailing_stop',
                                    'trailing_stop_price': stop_line,
                                    'lowest_price': lp,
                                }

                if tp_eff > 0:
                    if side == 'long':
                        tp_line = entry_price * (1 + tp_eff)
                        if current_price >= tp_line:
                            return {
                                'type': 'close_long',
                                'trigger_price': 0,
                                'position_size': 0,
                                'timestamp': candle_ts,
                                'reason': 'server_take_profit',
                                'take_profit_price': tp_line,
                            }
                    else:
                        tp_line = entry_price * (1 - tp_eff)
                        if current_price <= tp_line:
                            return {
                                'type': 'close_short',
                                'trigger_price': 0,
                                'position_size': 0,
                                'timestamp': candle_ts,
                                'reason': 'server_take_profit',
                                'take_profit_price': tp_line,
                            }

            return None
        except Exception:
            return None

    def _grid_bot_risk_exits(
        self,
        strategy_id: int,
        symbol: str,
        current_price: float,
        trading_config: Dict[str, Any],
        timeframe_seconds: int,
        initial_capital: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Server-side risk exits dedicated to grid/DCA bots.

        Two complementary trigger families, both designed for a strategy whose
        ``entry_price`` is a noisy sliding average:

        1. **Drawdown / take-profit on account equity** — ``stop_loss_pct`` and
           ``take_profit_pct`` are interpreted as *equity moves vs initial
           capital* (e.g. ``stop_loss_pct = 10`` ≈ "stop the bot when total
           equity is 10% below initial capital"). This mirrors what users
           expect from a "grid bot stop loss" on Binance / Pionex.

        2. **Out-of-grid price protection** — ``grid_oob_buffer_pct`` (default
           5%) plus ``upperPrice`` / ``lowerPrice`` from ``bot_params``: if
           price spikes far above or far below the configured grid, close both
           legs to stop the bot from bleeding on a trending breakout.

        Returns *all* close legs in one call (long first, then short) so the
        executor can fan them out as separate exchange orders within the same
        tick.
        """
        try:
            tc = trading_config if isinstance(trading_config, dict) else {}
            bot_type = str(tc.get('bot_type') or '').strip().lower()
            if bot_type not in ('grid', 'dca'):
                return []

            positions = self._get_current_positions(strategy_id, symbol)
            if not positions:
                return []

            has_long = any(
                (str(p.get('side') or '').lower() == 'long' and float(p.get('size') or 0) > 0)
                for p in positions
            )
            has_short = any(
                (str(p.get('side') or '').lower() == 'short' and float(p.get('size') or 0) > 0)
                for p in positions
            )
            if not has_long and not has_short:
                return []

            now_ts = int(time.time())
            tf = int(timeframe_seconds or 60)
            candle_ts = int(now_ts // tf) * tf

            def _close_all(reason: str, **extra: Any) -> List[Dict[str, Any]]:
                exits: List[Dict[str, Any]] = []
                if has_long:
                    exits.append({
                        'type': 'close_long',
                        'trigger_price': 0,
                        'position_size': 0,
                        'timestamp': candle_ts,
                        'reason': reason,
                        **extra,
                    })
                if has_short:
                    exits.append({
                        'type': 'close_short',
                        'trigger_price': 0,
                        'position_size': 0,
                        'timestamp': candle_ts,
                        'reason': reason,
                        **extra,
                    })
                return exits

            # --- 1) Out-of-grid breakout protection -------------------------
            bot_params = tc.get('bot_params') if isinstance(tc.get('bot_params'), dict) else {}
            try:
                upper = float(bot_params.get('upperPrice') or 0)
            except Exception:
                upper = 0.0
            try:
                lower = float(bot_params.get('lowerPrice') or 0)
            except Exception:
                lower = 0.0
            try:
                oob_buf = self._to_ratio(tc.get('grid_oob_buffer_pct'), default=0.05)
            except Exception:
                oob_buf = 0.05

            if upper > 0 and lower > 0 and upper > lower and current_price > 0 and oob_buf > 0:
                if current_price >= upper * (1 + oob_buf):
                    return _close_all(
                        'grid_out_of_bounds_up',
                        oob_threshold=upper * (1 + oob_buf),
                        upper_price=upper,
                    )
                if current_price <= lower * (1 - oob_buf):
                    return _close_all(
                        'grid_out_of_bounds_down',
                        oob_threshold=lower * (1 - oob_buf),
                        lower_price=lower,
                    )

            # --- 2) Equity drawdown / take-profit ---------------------------
            init_cap = float(initial_capital or 0)
            if init_cap <= 0:
                # Fall back to whatever the strategy recorded as starting balance.
                try:
                    with get_db_connection() as db:
                        cur = db.cursor()
                        cur.execute(
                            "SELECT trading_config FROM qd_strategies_trading WHERE id = %s",
                            (strategy_id,),
                        )
                        row = cur.fetchone() or {}
                        cur.close()
                        tc_db = row.get('trading_config')
                        if isinstance(tc_db, str) and tc_db.strip():
                            try:
                                tc_db = json.loads(tc_db)
                            except Exception:
                                tc_db = {}
                        if isinstance(tc_db, dict):
                            init_cap = float(tc_db.get('initial_capital') or 0)
                except Exception:
                    init_cap = 0.0

            sl_pct = self._to_ratio(tc.get('stop_loss_pct'))
            tp_pct = self._to_ratio(tc.get('take_profit_pct'))

            if init_cap > 0 and (sl_pct > 0 or tp_pct > 0):
                equity = self._calculate_current_equity(
                    strategy_id,
                    init_cap,
                    current_positions=positions,
                    current_price=current_price,
                    symbol=symbol,
                )
                pnl_pct = (equity - init_cap) / init_cap

                if sl_pct > 0 and pnl_pct <= -sl_pct:
                    return _close_all(
                        'grid_equity_stop_loss',
                        equity=equity,
                        initial_capital=init_cap,
                        equity_pct=pnl_pct,
                    )
                if tp_pct > 0 and pnl_pct >= tp_pct:
                    return _close_all(
                        'grid_equity_take_profit',
                        equity=equity,
                        initial_capital=init_cap,
                        equity_pct=pnl_pct,
                    )

            return []
        except Exception as e:
            logger.warning(f"Strategy {strategy_id} grid risk-exit check failed: {e}")
            return []

    def _is_server_side_exit_enabled(self, trading_config: Optional[Dict[str, Any]], config_key: str) -> bool:
        """
        Determine if a server-side exit (SL/TP) should be active.

        For non-bot strategies: enabled by default (historical behavior).
        For bot strategies: enabled only when the corresponding pct value > 0,
        since the user explicitly configured it in the risk form.
        """
        tc = trading_config if isinstance(trading_config, dict) else {}
        bot_type = str(tc.get('bot_type') or '').strip().lower()

        if config_key in tc:
            v = tc[config_key]
            if isinstance(v, str):
                return v.strip().lower() not in ['0', 'false', 'no', 'off']
            return bool(v)

        if not bot_type:
            return True

        if config_key == 'enable_server_side_stop_loss':
            sl = float(self._risk_params_from_trading_config(tc).get('stop_loss_ratio') or 0)
            return sl > 0
        if config_key == 'enable_server_side_take_profit':
            risk = self._risk_params_from_trading_config(tc)
            tp = float(risk.get('take_profit_ratio') or 0)
            trailing = bool(risk.get('trailing_enabled')) and float(risk.get('trailing_stop_ratio') or 0) > 0
            return tp > 0 or trailing

        return False
    
    def _klines_to_dataframe(self, klines: List[Dict[str, Any]]) -> pd.DataFrame:
        """将K线数据转换为DataFrame"""
        if not klines:
            # 返回空的 DataFrame，包含正确的列
            return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
        
        # 创建 DataFrame
        df = pd.DataFrame(klines)
        
        # Convert time column.
        # IMPORTANT: use UTC tz-aware index to avoid timezone skew when computing candle boundaries.
        if 'time' in df.columns:
            df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
            df = df.set_index('time')
        elif 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
            df = df.set_index('timestamp')
        
        # 确保只包含需要的列
        required_columns = ['open', 'high', 'low', 'close', 'volume']
        available_columns = [col for col in required_columns if col in df.columns]
        if not available_columns:
            logger.warning("K-lines are missing required columns")
            return pd.DataFrame(columns=required_columns)
        
        df = df[available_columns]
        
        # 强制转换所有数值列为 float64 类型
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                # 先转换为数值类型，然后强制转换为 float64
                df[col] = pd.to_numeric(df[col], errors='coerce').astype('float64')
        
        # 删除包含 NaN 的行
        df = df.dropna()
        
        return df

    def _update_dataframe_with_current_price(self, df: pd.DataFrame, current_price: float, timeframe: str) -> pd.DataFrame:
        """
        使用当前价格更新DataFrame的最后一根K线（用于实时计算）
        """
        if df is None or len(df) == 0:
            return df
            
        try:
            # 获取最后一根K线的时间
            last_time = df.index[-1]
            
            # 计算当前时间对应的K线起始时间
            from app.data_sources.base import TIMEFRAME_SECONDS
            timeframe_key = timeframe
            if timeframe_key not in TIMEFRAME_SECONDS:
                timeframe_key = str(timeframe_key).upper()
            if timeframe_key not in TIMEFRAME_SECONDS:
                timeframe_key = str(timeframe_key).lower()
            tf_seconds = TIMEFRAME_SECONDS.get(timeframe_key, 60)
            
            # Use epoch seconds directly to avoid naive datetime timezone conversion issues.
            last_ts = float(last_time.timestamp())
            now_ts = float(time.time())
            
            # 计算当前价格所属的 K 线开始时间
            current_period_start = int(now_ts // tf_seconds) * tf_seconds
            
            # 检查最后一根K线是否就是当前周期的
            if abs(last_ts - current_period_start) < 2:
                # 更新最后一根
                df.iloc[-1, df.columns.get_loc('close')] = current_price
                df.iloc[-1, df.columns.get_loc('high')] = max(df.iloc[-1]['high'], current_price)
                df.iloc[-1, df.columns.get_loc('low')] = min(df.iloc[-1]['low'], current_price)
            elif current_period_start > last_ts:
                # 追加新行
                new_row = pd.DataFrame({
                    'open': [current_price],
                    'high': [current_price],
                    'low': [current_price],
                    'close': [current_price],
                    'volume': [0.0]
                }, index=[pd.to_datetime(current_period_start, unit='s', utc=True)])
                
                df = pd.concat([df, new_row])
            
            return df
            
        except Exception as e:
            logger.error(f"Failed to update realtime candle: {str(e)}")
            return df
    
    def _execute_indicator_with_prices(
        self, indicator_code: str, df: pd.DataFrame, trading_config: Dict[str, Any], 
        initial_highest_price: float = 0.0,
        initial_position: int = 0,
        initial_avg_entry_price: float = 0.0,
        initial_position_count: int = 0,
        initial_last_add_price: float = 0.0
    ) -> Optional[Dict[str, Any]]:
        """
        执行指标代码并提取待触发的信号和价格
        """
        try:
            # 执行指标代码
            executed_df, exec_env = self._execute_indicator_df(
                indicator_code, df, trading_config, 
                initial_highest_price=initial_highest_price,
                initial_position=initial_position,
                initial_avg_entry_price=initial_avg_entry_price,
                initial_position_count=initial_position_count,
                initial_last_add_price=initial_last_add_price
            )
            if executed_df is None:
                return None
            
            # 提取最新的 highest_price
            new_highest_price = exec_env.get('highest_price', 0.0)
            
            # 提取最后一根K线的时间
            last_kline_time = int(df.index[-1].timestamp()) if hasattr(df.index[-1], 'timestamp') else int(time.time())
            
            # 提取待触发的信号
            pending_signals = []
            
            # Supported indicator signal formats:
            # - Preferred (simple): df['buy'], df['sell'] as boolean
            # - Internal (4-way): df['open_long'], df['close_long'], df['open_short'], df['close_short'] as boolean
            if all(col in executed_df.columns for col in ['buy', 'sell']) and not all(col in executed_df.columns for col in ['open_long', 'close_long', 'open_short', 'close_short']):
                # Normalize buy/sell into 4-way columns for execution.
                td = trading_config.get('trade_direction', trading_config.get('tradeDirection', 'both'))
                td = str(td or 'both').lower()
                if td not in ['long', 'short', 'both']:
                    td = 'both'

                buy = executed_df['buy'].fillna(False).astype(bool)
                sell = executed_df['sell'].fillna(False).astype(bool)

                executed_df = executed_df.copy()
                if td == 'long':
                    executed_df['open_long'] = buy
                    executed_df['close_long'] = sell
                    executed_df['open_short'] = False
                    executed_df['close_short'] = False
                elif td == 'short':
                    executed_df['open_long'] = False
                    executed_df['close_long'] = False
                    executed_df['open_short'] = sell
                    executed_df['close_short'] = buy
                else:
                    # Align with BacktestService both-mode: buy/sell only enter/flip;
                    # opposing legs are closed inside open_long/open_short execution.
                    executed_df['open_long'] = buy
                    executed_df['close_long'] = False
                    executed_df['open_short'] = sell
                    executed_df['close_short'] = False
                    if isinstance(trading_config, dict):
                        trading_config['_indicator_both_mode'] = True

            # Check for 4-way columns after normalization
            if all(col in executed_df.columns for col in ['open_long', 'close_long', 'open_short', 'close_short']):
                # 优化点3: 防“信号闪烁” (Repainting)
                signal_mode = trading_config.get('signal_mode', 'confirmed') # 'confirmed' or 'aggressive'
                exit_signal_mode = trading_config.get('exit_signal_mode', 'aggressive') # 'confirmed' or 'aggressive'
                
                entry_check_set = set()
                exit_check_set = set()
                
                if len(executed_df) > 1:
                    # 始终检查上一根已完成K线
                    entry_check_set.add(len(executed_df) - 2)
                    exit_check_set.add(len(executed_df) - 2)
                
                if signal_mode == 'aggressive' and len(executed_df) > 0:
                    entry_check_set.add(len(executed_df) - 1)
                
                if exit_signal_mode == 'aggressive' and len(executed_df) > 0:
                    exit_check_set.add(len(executed_df) - 1)
                
                # 统一遍历索引（保持确定性排序）
                check_indices = sorted(entry_check_set.union(exit_check_set), reverse=True)
                
                for idx in check_indices:
                    # 获取该K线的收盘价（作为默认触发价）
                    close_price = float(executed_df['close'].iloc[idx])
                    # 该信号的时间戳
                    signal_timestamp = int(executed_df.index[idx].timestamp()) if hasattr(executed_df.index[idx], 'timestamp') else last_kline_time
                    
                    # 开多信号（仅在 entry_check_set 中检查）
                    if idx in entry_check_set and executed_df['open_long'].iloc[idx]:
                        trigger_price = close_price
                        position_size = 0.08
                        if 'position_size' in executed_df.columns:
                            pos_size = executed_df['position_size'].iloc[idx]
                            if pos_size > 0:
                                position_size = float(pos_size)
                        
                        if not any(s['type'] == 'open_long' and s.get('timestamp') == signal_timestamp for s in pending_signals):
                            pending_signals.append({
                                'type': 'open_long',
                                'trigger_price': trigger_price,
                                'position_size': position_size,
                                'timestamp': signal_timestamp
                            })
                    
                    # 平多信号
                    if idx in exit_check_set and executed_df['close_long'].iloc[idx]:
                        trigger_price = close_price
                        if not any(s['type'] == 'close_long' and s.get('timestamp') == signal_timestamp for s in pending_signals):
                            pending_signals.append({
                                'type': 'close_long',
                                'trigger_price': trigger_price,
                                'position_size': 0,
                                'timestamp': signal_timestamp
                            })
                    
                    # 开空信号
                    if idx in entry_check_set and executed_df['open_short'].iloc[idx]:
                        trigger_price = close_price
                        position_size = 0.08
                        if 'position_size' in executed_df.columns:
                            pos_size = executed_df['position_size'].iloc[idx]
                            if pos_size > 0:
                                position_size = float(pos_size)
                        
                        if not any(s['type'] == 'open_short' and s.get('timestamp') == signal_timestamp for s in pending_signals):
                            pending_signals.append({
                                'type': 'open_short',
                                'trigger_price': trigger_price,
                                'position_size': position_size,
                                'timestamp': signal_timestamp
                            })
                    
                    # 平空信号
                    if idx in exit_check_set and executed_df['close_short'].iloc[idx]:
                        trigger_price = close_price
                        if not any(s['type'] == 'close_short' and s.get('timestamp') == signal_timestamp for s in pending_signals):
                            pending_signals.append({
                                'type': 'close_short',
                                'trigger_price': trigger_price,
                                'position_size': 0,
                                'timestamp': signal_timestamp
                            })
                            
                    # 加多信号
                    if idx in entry_check_set and 'add_long' in executed_df.columns and executed_df['add_long'].iloc[idx]:
                        trigger_price = close_price
                        position_size = 0.06
                        if 'position_size' in executed_df.columns:
                            pos_size = executed_df['position_size'].iloc[idx]
                            if pos_size > 0:
                                position_size = float(pos_size)

                        if not any(s['type'] == 'add_long' and s.get('timestamp') == signal_timestamp for s in pending_signals):
                            pending_signals.append({
                                'type': 'add_long',
                                'trigger_price': trigger_price,
                                'position_size': position_size,
                                'timestamp': signal_timestamp
                            })
                            
                    # 加空信号
                    if idx in entry_check_set and 'add_short' in executed_df.columns and executed_df['add_short'].iloc[idx]:
                        trigger_price = close_price
                        position_size = 0.06
                        if 'position_size' in executed_df.columns:
                            pos_size = executed_df['position_size'].iloc[idx]
                            if pos_size > 0:
                                position_size = float(pos_size)

                        if not any(s['type'] == 'add_short' and s.get('timestamp') == signal_timestamp for s in pending_signals):
                            pending_signals.append({
                                'type': 'add_short',
                                'trigger_price': trigger_price,
                                'position_size': position_size,
                                'timestamp': signal_timestamp
                            })

                    # Reduce / scale-out signals (optional)
                    # These are used by position management rules (trend/adverse reduce) and should be treated as exits.
                    if idx in exit_check_set and 'reduce_long' in executed_df.columns and executed_df['reduce_long'].iloc[idx]:
                        trigger_price = close_price
                        reduce_pct = 0.1
                        if 'reduce_size' in executed_df.columns:
                            try:
                                reduce_pct = float(executed_df['reduce_size'].iloc[idx] or 0)
                            except Exception:
                                reduce_pct = 0.1
                        elif 'position_size' in executed_df.columns:
                            try:
                                reduce_pct = float(executed_df['position_size'].iloc[idx] or 0)
                            except Exception:
                                reduce_pct = 0.1
                        if reduce_pct <= 0:
                            reduce_pct = 0.1
                        if not any(s['type'] == 'reduce_long' and s.get('timestamp') == signal_timestamp for s in pending_signals):
                            pending_signals.append({
                                'type': 'reduce_long',
                                'trigger_price': trigger_price,
                                'position_size': reduce_pct,
                                'timestamp': signal_timestamp
                            })

                    if idx in exit_check_set and 'reduce_short' in executed_df.columns and executed_df['reduce_short'].iloc[idx]:
                        trigger_price = close_price
                        reduce_pct = 0.1
                        if 'reduce_size' in executed_df.columns:
                            try:
                                reduce_pct = float(executed_df['reduce_size'].iloc[idx] or 0)
                            except Exception:
                                reduce_pct = 0.1
                        elif 'position_size' in executed_df.columns:
                            try:
                                reduce_pct = float(executed_df['position_size'].iloc[idx] or 0)
                            except Exception:
                                reduce_pct = 0.1
                        if reduce_pct <= 0:
                            reduce_pct = 0.1
                        if not any(s['type'] == 'reduce_short' and s.get('timestamp') == signal_timestamp for s in pending_signals):
                            pending_signals.append({
                                'type': 'reduce_short',
                                'trigger_price': trigger_price,
                                'position_size': reduce_pct,
                                'timestamp': signal_timestamp
                            })
            
            return {
                'pending_signals': pending_signals,
                'last_kline_time': last_kline_time,
                'new_highest_price': new_highest_price,
                'indicator_both_mode': bool((trading_config or {}).get('_indicator_both_mode')),
            }
            
        except Exception as e:
            logger.error(f"Failed to execute indicator and extract prices: {str(e)}")
            logger.error(traceback.format_exc())
            return None
    
    def _execute_indicator_df(
        self, indicator_code: str, df: pd.DataFrame, trading_config: Dict[str, Any], 
        initial_highest_price: float = 0.0,
        initial_position: int = 0,
        initial_avg_entry_price: float = 0.0,
        initial_position_count: int = 0,
        initial_last_add_price: float = 0.0
    ) -> tuple[Optional[pd.DataFrame], dict]:
        """执行指标代码，返回执行后的DataFrame和执行环境"""
        try:
            # 确保 DataFrame 的所有数值列都是 float64 类型
            df = df.copy()
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in df.columns:
                    if not pd.api.types.is_numeric_dtype(df[col]):
                        df[col] = pd.to_numeric(df[col], errors='coerce').astype('float64')
                    else:
                        df[col] = df[col].astype('float64')
            
            # 删除包含 NaN 的行
            df = df.dropna()
            
            if len(df) == 0:
                logger.warning("DataFrame is empty; cannot execute indicator script")
                return None, {}
            
            # 初始化信号Series
            signals = pd.Series(0, index=df.index, dtype='float64')
            
            # 准备执行环境
            # Expose the full trading config to indicator scripts so frontend parameters
            # (scale-in/out, position sizing, risk params) can be used directly.
            # Also provide a backtest-modal compatible nested config object: cfg.risk/cfg.scale/cfg.position.
            tc = dict(trading_config or {})
            cfg = self._build_cfg_from_trading_config(tc)
            
            # === 指标参数支持 ===
            # 从 trading_config 获取用户设置的指标参数
            user_indicator_params = tc.get('indicator_params', {})
            # 解析指标代码中声明的参数
            declared_params = IndicatorParamsParser.parse_params(indicator_code)
            # 合并参数（用户值优先，否则使用默认值）
            merged_params = IndicatorParamsParser.merge_params(declared_params, user_indicator_params)
            
            # === 指标调用器支持 ===
            # 获取用户ID和指标ID（用于 call_indicator 权限检查）
            user_id = tc.get('user_id', 1)
            indicator_id = tc.get('indicator_id')
            indicator_caller = IndicatorCaller(user_id, indicator_id)
            
            local_vars = {
                'df': df,
                'open': df['open'].astype('float64'),
                'high': df['high'].astype('float64'),
                'low': df['low'].astype('float64'),
                'close': df['close'].astype('float64'),
                'volume': df['volume'].astype('float64'),
                'signals': signals,
                'np': np,
                'pd': pd,
                'trading_config': tc,
                'config': tc,  # alias
                'cfg': cfg,    # normalized nested config
                'params': merged_params,  # 指标参数 (新增)
                'call_indicator': indicator_caller.call_indicator,  # 调用其他指标 (新增)
                'leverage': float(trading_config.get('leverage', 1)),
                'initial_capital': float(trading_config.get('initial_capital', 1000)),
                'commission': 0.001,
                'trade_direction': str(trading_config.get('trade_direction', 'long')),
                'initial_highest_price': float(initial_highest_price),
                'initial_position': int(initial_position),
                'initial_avg_entry_price': float(initial_avg_entry_price),
                'initial_position_count': int(initial_position_count),
                'initial_last_add_price': float(initial_last_add_price)
            }
            
            from app.utils.safe_exec import build_safe_builtins, safe_exec_with_validation

            exec_env = local_vars.copy()
            exec_env['__builtins__'] = build_safe_builtins()

            # 兼容性修复：pandas 2.0+ 移除了 fillna(method=...) 参数
            import re
            compatibility_fixed_code = indicator_code
            compatibility_fixed_code = re.sub(
                r'\.fillna\(\s*method\s*=\s*["\']ffill["\']\s*\)',
                '.ffill()',
                compatibility_fixed_code
            )
            compatibility_fixed_code = re.sub(
                r'\.fillna\(\s*method\s*=\s*["\']bfill["\']\s*\)',
                '.bfill()',
                compatibility_fixed_code
            )

            exec_result = safe_exec_with_validation(
                code=compatibility_fixed_code,
                exec_globals=exec_env,
                timeout=60,
            )
            if not exec_result['success']:
                raise ValueError(f"Indicator execution failed: {exec_result['error']}")
            
            executed_df = exec_env.get('df', df)

            # Validation: chart markers in output['signals'] require df execution columns.
            output_obj = exec_env.get('output')
            has_output_signals = isinstance(output_obj, dict) and isinstance(output_obj.get('signals'), list) and len(output_obj.get('signals')) > 0
            has_four_way = all(col in executed_df.columns for col in ['open_long', 'close_long', 'open_short', 'close_short'])
            has_buy_sell = all(col in executed_df.columns for col in ['buy', 'sell'])
            if has_output_signals and not has_four_way and not has_buy_sell:
                raise ValueError(
                    "Invalid indicator script: output['signals'] is provided, but df execution columns are missing. "
                    "Set df['buy'] and df['sell'], or four-way df['open_long'/'close_long'/'open_short'/'close_short']."
                )
            
            return executed_df, exec_env
            
        except Exception as e:
            logger.error(f"Failed to execute indicator script: {str(e)}")
            logger.error(traceback.format_exc())
            return None, {}
    
    def _execute_indicator(self, indicator_code: str, df: pd.DataFrame, trading_config: Dict[str, Any]) -> Optional[Any]:
        """兼容旧版本"""
        executed_df, _ = self._execute_indicator_df(indicator_code, df, trading_config)
        if executed_df is None:
            return None
        return 0

    def _get_current_positions(self, strategy_id: int, symbol: str) -> List[Dict[str, Any]]:
        """获取当前持仓（支持symbol规范化匹配）"""
        try:
            with get_db_connection() as db:
                cursor = db.cursor()
                query = """
                    SELECT id, symbol, side, size, entry_price, highest_price, lowest_price
                    FROM qd_strategy_positions
                    WHERE strategy_id = %s
                """
                cursor.execute(query, (strategy_id,))
                all_positions = cursor.fetchall()
                
                matched_positions = []
                for pos in all_positions:
                    # 简化匹配逻辑：只匹配前缀
                    if pos['symbol'].split(':')[0] == symbol.split(':')[0]:
                        matched_positions.append(pos)
                
                cursor.close()
                return matched_positions
        except Exception as e:
            logger.error(f"Failed to fetch positions: {str(e)}")
            return []

    def _execute_trading_logic(self, *args, **kwargs):
        """已废弃"""
        pass
    
    def _execute_signal(
        self,
        strategy_id: int,
        strategy_name: str,
        exchange: Any,
        symbol: str,
        current_price: float,
        signal_type: str,
        position_size: float,
        current_positions: List[Dict[str, Any]],
        trade_direction: str,
        leverage: int,
        initial_capital: float,
        market_type: str = 'swap',
        market_category: str = 'Crypto',
        margin_mode: str = 'cross',
        stop_loss_price: float = None,
        take_profit_price: float = None,
        signal_reason: str = "",
        trailing_stop_price: float = None,
        execution_mode: str = 'signal',
        notification_config: Optional[Dict[str, Any]] = None,
        trading_config: Optional[Dict[str, Any]] = None,
        ai_model_config: Optional[Dict[str, Any]] = None,
        signal_ts: int = 0,
        price_exchange_id: Optional[str] = None,
        script_base_qty: Optional[float] = None,
    ):
        """执行具体的交易信号"""
        try:
            indicator_both_mode = self._is_indicator_both_mode(trading_config)

            # Hard state-machine guard (double safety in addition to loop-level filtering).
            state = self._effective_position_state(strategy_id, symbol, current_positions)
            if not self._is_signal_allowed(state, signal_type, indicator_both_mode=indicator_both_mode):
                append_strategy_log(strategy_id, "info", f"Signal filtered by state machine: {signal_type} (state={state})")
                return False

            sig = (signal_type or "").strip().lower()

            # Both-mode flip: close opposing leg before open (matches BacktestService).
            if indicator_both_mode and sig == "open_long" and state == "short":
                self._execute_signal(
                    strategy_id=strategy_id,
                    strategy_name=strategy_name,
                    exchange=exchange,
                    symbol=symbol,
                    current_price=current_price,
                    signal_type="close_short",
                    position_size=0,
                    current_positions=current_positions,
                    trade_direction=trade_direction,
                    leverage=leverage,
                    initial_capital=initial_capital,
                    market_type=market_type,
                    market_category=market_category,
                    margin_mode=margin_mode,
                    execution_mode=execution_mode,
                    notification_config=notification_config,
                    trading_config=trading_config,
                    ai_model_config=ai_model_config,
                    signal_ts=signal_ts,
                    price_exchange_id=price_exchange_id,
                )
                current_positions = self._get_current_positions(strategy_id, symbol)
                state = self._effective_position_state(strategy_id, symbol, current_positions)
                if state == "short":
                    append_strategy_log(
                        strategy_id, "info",
                        f"Flip open_long skipped: close_short did not clear short for {symbol}",
                    )
                    return False
            elif indicator_both_mode and sig == "open_short" and state == "long":
                self._execute_signal(
                    strategy_id=strategy_id,
                    strategy_name=strategy_name,
                    exchange=exchange,
                    symbol=symbol,
                    current_price=current_price,
                    signal_type="close_long",
                    position_size=0,
                    current_positions=current_positions,
                    trade_direction=trade_direction,
                    leverage=leverage,
                    initial_capital=initial_capital,
                    market_type=market_type,
                    market_category=market_category,
                    margin_mode=margin_mode,
                    execution_mode=execution_mode,
                    notification_config=notification_config,
                    trading_config=trading_config,
                    ai_model_config=ai_model_config,
                    signal_ts=signal_ts,
                    price_exchange_id=price_exchange_id,
                )
                current_positions = self._get_current_positions(strategy_id, symbol)
                state = self._effective_position_state(strategy_id, symbol, current_positions)
                if state == "long":
                    append_strategy_log(
                        strategy_id, "info",
                        f"Flip open_short skipped: close_long did not clear long for {symbol}",
                    )
                    return False

            # 1. 检查交易方向限制
            if market_type == 'spot' and 'short' in signal_type:
                 append_strategy_log(strategy_id, "info", f"Signal rejected: spot market does not support {signal_type}")
                 return False

            # 1.1 开仓 AI 过滤（仅 open_*）
            if sig in ("open_long", "open_short") and self._is_entry_ai_filter_enabled(ai_model_config=ai_model_config, trading_config=trading_config):
                ok_ai, ai_info = self._entry_ai_filter_allows(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    signal_type=sig,
                    ai_model_config=ai_model_config,
                    trading_config=trading_config,
                )
                if not ok_ai:
                    # Best-effort persist a browser notification so UI can show "HOLD due to AI filter".
                    reason = (ai_info or {}).get("reason") or "ai_filter_rejected"
                    ai_decision = (ai_info or {}).get("ai_decision") or ""
                    title = f"AI过滤拦截开仓 | {symbol}"
                    msg = f"策略信号={sig}，AI决策={ai_decision or 'UNKNOWN'}，原因={reason}；已HOLD（不下单）"
                    self._persist_browser_notification(
                        strategy_id=strategy_id,
                        symbol=symbol,
                        signal_type="ai_filter_hold",
                        title=title,
                        message=msg,
                        payload={
                            "event": "qd.ai_filter",
                            "strategy_id": int(strategy_id),
                            "strategy_name": str(strategy_name or ""),
                            "symbol": str(symbol or ""),
                            "signal_type": str(sig),
                            "ai_decision": str(ai_decision),
                            "reason": str(reason),
                            "signal_ts": int(signal_ts or 0),
                        },
                    )
                    logger.info(
                        f"AI entry filter rejected: strategy_id={strategy_id} symbol={symbol} signal={sig} ai={ai_decision} reason={reason}"
                    )
                    append_strategy_log(
                        strategy_id, "info",
                        f"AI filter blocked entry: {sig} {symbol}, decision={ai_decision}, reason={reason}",
                    )
                    return False

            # 1.2 Max position limit (risk control)
            if sig in ("open_long", "open_short", "add_long", "add_short"):
                max_pos = float((trading_config or {}).get('max_position') or 0)
                if max_pos > 0:
                    cur_pos_value = self._current_position_value(current_positions, current_price)
                    if cur_pos_value >= max_pos:
                        append_strategy_log(
                            strategy_id, "info",
                            f"Risk: max_position reached ({cur_pos_value:.2f} >= {max_pos:.2f}), blocking {sig}",
                        )
                        return False

            # 1.3 Max daily loss limit (risk control)
            if sig in ("open_long", "open_short", "add_long", "add_short"):
                max_daily = float((trading_config or {}).get('max_daily_loss') or 0)
                if max_daily > 0:
                    daily_pnl = self._get_daily_pnl(strategy_id)
                    if daily_pnl < 0 and abs(daily_pnl) >= max_daily:
                        append_strategy_log(
                            strategy_id, "info",
                            f"Risk: max_daily_loss reached (loss={abs(daily_pnl):.2f} >= {max_daily:.2f}), blocking {sig}",
                        )
                        return False

            # 2. 计算下单数量
            available_capital = self._get_available_capital(
                strategy_id,
                initial_capital,
                current_positions=current_positions,
                current_price=current_price,
                symbol=symbol,
            )
            
            amount = 0.0

            bot_type = (trading_config or {}).get('bot_type', '')
            is_bot_script = bool(bot_type)

            try:
                explicit_script_qty = (
                    float(script_base_qty)
                    if script_base_qty is not None and float(script_base_qty) > 0
                    else None
                )
            except Exception:
                explicit_script_qty = None

            # Frontend position sizing alignment:
            # - non-bot open_* uses entry_pct from trading_config if provided
            # - bot scripts pass their own amount/ratio from ctx.buy()/ctx.sell()
            # - script strategies with ctx.buy(price, qty) pass script_base_qty (base coins)
            entry_ratio_override = None
            cs_mode = (
                isinstance(trading_config, dict)
                and str(trading_config.get("cs_strategy_type") or "").strip().lower() == "cross_sectional"
            )
            if (
                explicit_script_qty is None
                and (not is_bot_script)
                and (not cs_mode)
                and sig in ("open_long", "open_short")
                and isinstance(trading_config, dict)
            ):
                entry_ratio = self._risk_params_from_trading_config(trading_config).get("entry_ratio")
                if entry_ratio is not None and float(entry_ratio) > 0:
                    position_size = float(entry_ratio)
                    entry_ratio_override = float(entry_ratio)

            # Open / add sizing
            if ('open' in sig or 'add' in sig):
                 if explicit_script_qty is not None and not is_bot_script:
                     amount = explicit_script_qty
                 elif position_size is None or float(position_size) <= 0:
                     position_size = 0.05

                 if explicit_script_qty is None and is_bot_script and float(position_size) > 1.0:
                     # Bot scripts pass amount as absolute USDT notional, not ratio.
                     usdt_notional = float(position_size)
                     if market_type == 'spot':
                         from app.services.live_trading.spot_sizing import scale_spot_open_notional
                         usdt_notional = scale_spot_open_notional(usdt_notional)
                         amount = usdt_notional / current_price
                     else:
                         amount = (usdt_notional * leverage) / current_price
                 elif explicit_script_qty is None:
                     use_code_ratios = bool(self._code_strategy_cfg(trading_config))
                     if use_code_ratios and sig in ("open_long", "open_short", "add_long", "add_short"):
                         position_ratio = float(position_size)
                     elif entry_ratio_override is not None:
                         position_ratio = float(entry_ratio_override)
                     else:
                         position_ratio = self._to_ratio(position_size, default=0.05)
                     if market_type == 'spot':
                         from app.services.live_trading.spot_sizing import scale_spot_open_notional
                         quote_stake = scale_spot_open_notional(available_capital * position_ratio)
                         amount = quote_stake / current_price
                     else:
                         amount = (available_capital * position_ratio * leverage) / current_price

            # Reduce sizing: position_size is treated as a reduce ratio (close X% of current position).
            if sig in ("reduce_long", "reduce_short"):
                pos_side = "long" if "long" in sig else "short"
                pos = next((p for p in current_positions if (p.get('side') or '').strip().lower() == pos_side), None)
                if not pos:
                    return False
                cur_size = float(pos.get("size") or 0.0)
                if cur_size <= 0:
                    return False
                reduce_ratio = self._to_ratio(position_size, default=0.1)
                reduce_amount = cur_size * reduce_ratio
                # If reduce is effectively full, treat as close_*.
                if reduce_amount >= cur_size * 0.999:
                    sig = "close_long" if pos_side == "long" else "close_short"
                    signal_type = sig
                    amount = cur_size
                else:
                    amount = reduce_amount
            
            # 3. 检查反向持仓（单向持仓逻辑）
            # ... (简化处理，假设无反向或由用户处理) ...

            # 4. Execute order enqueue (PendingOrderWorker will dispatch notifications in signal mode)
            if 'close' in sig:
                pos_side = 'long' if 'long' in sig else 'short'
                pos = next((p for p in current_positions if (p.get('side') or '').strip().lower() == pos_side), None)
                if not pos:
                    append_strategy_log(
                        strategy_id, "info",
                        f"Skip close: no local {pos_side} position for {symbol} ({sig})",
                    )
                    return False
                full_size = float(pos.get('size') or 0.0)
                if full_size <= 0:
                    append_strategy_log(
                        strategy_id, "info",
                        f"Skip close: zero local size for {symbol} ({sig})",
                    )
                    return False

                if is_bot_script and position_size is not None and float(position_size) > 1.0 and current_price > 0:
                    usdt_notional = float(position_size)
                    close_qty = (usdt_notional * leverage) / current_price if market_type != 'spot' else usdt_notional / current_price
                    if close_qty < full_size * 0.99:
                        amount = close_qty
                        sig = f"reduce_{pos_side}"
                        signal_type = sig
                    else:
                        amount = full_size
                elif explicit_script_qty is not None:
                    amount = min(float(explicit_script_qty), full_size)
                    if amount < full_size * 0.999:
                        sig = f"reduce_{pos_side}"
                        signal_type = sig
                else:
                    amount = full_size

            if amount <= 0 and ('open' in signal_type or 'add' in signal_type):
                return False
            
            bot_order_mode = (trading_config or {}).get('order_mode') or None
            order_result = self._execute_exchange_order(
                exchange=exchange,
                strategy_id=strategy_id,
                symbol=symbol,
                signal_type=signal_type,
                amount=amount,
                ref_price=float(current_price or 0.0),
                market_type=market_type,
                market_category=market_category,
                price_exchange_id=price_exchange_id,
                leverage=leverage,
                execution_mode=execution_mode,
                notification_config=notification_config,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                signal_reason=signal_reason,
                trailing_stop_price=trailing_stop_price,
                signal_ts=int(signal_ts or 0),
                order_mode=bot_order_mode,
            )
            
            if order_result and order_result.get('success'):
                # For live execution, the order is only enqueued here.
                # The actual fill/trade/position updates are performed by PendingOrderWorker.
                if str(execution_mode or "").strip().lower() == "live":
                    return True

                # 更新数据库状态 (signal mode / local simulation)
                # Prefer real exchange fee-rate; fall back to user-configured rate
                _exchange_fee = self._exchange_fee_cache.get(strategy_id)
                if _exchange_fee and _exchange_fee.get('taker', 0) > 0:
                    _comm_rate = _exchange_fee['taker']
                else:
                    _comm_rate = float((trading_config or {}).get('commission', 0) or 0) / 100.0
                    if _comm_rate <= 0:
                        _comm_rate = 0.001
                _est_commission = round(float(current_price or 0) * float(amount or 0) * _comm_rate, 8)
                from app.utils.trade_close_reason import resolve_close_reason_for_record
                _exit_reason = resolve_close_reason_for_record(
                    signal_type,
                    signal_reason=str(signal_reason or ""),
                    trading_config=trading_config,
                )

                if 'open' in sig or 'add' in sig:
                    self._record_trade(
                        strategy_id=strategy_id, symbol=symbol, type=signal_type,
                        price=current_price, amount=amount, value=amount*current_price,
                        commission=_est_commission
                    )
                    side = 'short' if 'short' in signal_type else 'long'
                    
                    old_pos = next((p for p in current_positions if p['side'] == side), None)
                    new_size = amount
                    new_entry = current_price
                    if old_pos:
                        old_size = float(old_pos['size'])
                        old_entry = float(old_pos['entry_price'])
                        new_size += old_size
                        new_entry = ((old_size * old_entry) + (amount * current_price)) / new_size

                    self._update_position(
                        strategy_id=strategy_id, symbol=symbol, side=side,
                        size=new_size, entry_price=new_entry, current_price=current_price
                    )
                    append_strategy_log(
                        strategy_id, "trade",
                        f"Open position: {signal_type} {symbol} amount={amount:.6f} @ {current_price:.6f}, fee={_est_commission:.6f}",
                    )
                elif sig.startswith("reduce_"):
                    # Partial scale-out: reduce position size, keep entry price unchanged.
                    # 信号模式下计算部分平仓盈亏
                    side = 'short' if 'short' in signal_type else 'long'
                    old_pos = next((p for p in current_positions if p.get('side') == side), None)
                    if not old_pos:
                        return True
                    old_size = float(old_pos.get('size') or 0.0)
                    old_entry = float(old_pos.get('entry_price') or 0.0)
                    
                    reduce_profit = None
                    if old_entry > 0 and amount > 0:
                        if side == 'long':
                            reduce_profit = (current_price - old_entry) * amount
                        else:
                            reduce_profit = (old_entry - current_price) * amount
                        reduce_profit = round(reduce_profit - _est_commission, 8)

                    self._record_trade(
                        strategy_id=strategy_id, symbol=symbol, type=signal_type,
                        price=current_price, amount=amount, value=amount*current_price,
                        profit=reduce_profit, commission=_est_commission,
                        close_reason=_exit_reason,
                    )
                    
                    new_size = max(0.0, old_size - float(amount or 0.0))
                    if new_size <= old_size * 0.001:
                        self._close_position(strategy_id, symbol, side)
                    else:
                        self._update_position(
                            strategy_id=strategy_id, symbol=symbol, side=side,
                            size=new_size, entry_price=old_entry, current_price=current_price
                        )
                    _pstr = f", profit={reduce_profit:.4f}" if reduce_profit is not None else ""
                    append_strategy_log(
                        strategy_id, "trade",
                        f"Reduce position: {signal_type} {symbol} amount={amount:.6f} @ {current_price:.6f}, fee={_est_commission:.6f}{_pstr}",
                    )
                elif 'close' in sig:
                    # 信号模式下计算平仓盈亏
                    side = 'short' if 'short' in signal_type else 'long'
                    old_pos = next((p for p in current_positions if p.get('side') == side), None)
                    
                    close_profit = None
                    if old_pos:
                        entry_price = float(old_pos.get('entry_price') or 0)
                        if entry_price > 0 and amount > 0:
                            if side == 'long':
                                close_profit = (current_price - entry_price) * amount
                            else:
                                close_profit = (entry_price - current_price) * amount
                            close_profit = round(close_profit - _est_commission, 8)

                    self._record_trade(
                        strategy_id=strategy_id, symbol=symbol, type=signal_type,
                        price=current_price, amount=amount, value=amount*current_price,
                        profit=close_profit, commission=_est_commission,
                        close_reason=_exit_reason,
                    )
                    self._close_position(strategy_id, symbol, side)
                    _pstr = f", profit={close_profit:.4f}" if close_profit is not None else ""
                    append_strategy_log(
                        strategy_id, "trade",
                        f"Close position: {signal_type} {symbol} amount={amount:.6f} @ {current_price:.6f}, fee={_est_commission:.6f}{_pstr}",
                    )

                return True

            _err = (order_result or {}).get("error", "unknown")
            append_strategy_log(strategy_id, "error", f"Order enqueue failed: {signal_type} {symbol}, error={_err}")
            return False
            
        except Exception as e:
            logger.error(f"Failed to execute signal: {e}")
            append_strategy_log(strategy_id, "error", f"Signal execution exception: {signal_type} {symbol}, {e}")
            return False

    def _is_entry_ai_filter_enabled(self, *, ai_model_config: Optional[Dict[str, Any]], trading_config: Optional[Dict[str, Any]]) -> bool:
        """Detect whether the strategy enabled 'AI filter on entry (open positions only)'."""
        amc = ai_model_config if isinstance(ai_model_config, dict) else {}
        tc = trading_config if isinstance(trading_config, dict) else {}

        # Accept multiple key names for forward/backward compatibility.
        candidates = [
            amc.get("entry_ai_filter_enabled"),
            amc.get("entryAiFilterEnabled"),
            amc.get("ai_filter_enabled"),
            amc.get("aiFilterEnabled"),
            amc.get("enable_ai_filter"),
            amc.get("enableAiFilter"),
            tc.get("entry_ai_filter_enabled"),
            tc.get("ai_filter_enabled"),
            tc.get("enable_ai_filter"),
            tc.get("enableAiFilter"),
        ]
        for v in candidates:
            if v is None:
                continue
            if isinstance(v, bool):
                return bool(v)
            s = str(v).strip().lower()
            if s in ("1", "true", "yes", "y", "on", "enabled"):
                return True
            if s in ("0", "false", "no", "n", "off", "disabled"):
                return False
        return False

    def _entry_ai_filter_allows(
        self,
        *,
        strategy_id: int,
        symbol: str,
        signal_type: str,
        ai_model_config: Optional[Dict[str, Any]],
        trading_config: Optional[Dict[str, Any]],
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Run internal AI analysis and decide whether an entry signal is allowed.

        Returns:
          (allowed, info)
          - allowed: True -> proceed; False -> hold (reject open)
          - info: {ai_decision, reason, analysis_error?}
        """
        amc = ai_model_config if isinstance(ai_model_config, dict) else {}
        tc = trading_config if isinstance(trading_config, dict) else {}

        # Market for AnalysisService. Live trading executor is Crypto-focused.
        market = str(amc.get("market") or amc.get("analysis_market") or "Crypto").strip() or "Crypto"

        # Optional model override (OpenRouter model id)
        model = amc.get("model") or amc.get("openrouter_model") or amc.get("openrouterModel") or None
        model = str(model).strip() if model else None

        # Prefer zh-CN for local UI; can be overridden.
        language = amc.get("language") or amc.get("lang") or tc.get("language") or "zh-CN"
        language = str(language or "zh-CN")

        # ── Billing: AI filter uses the same cost as ai_analysis ──
        try:
            from app.services.billing_service import get_billing_service
            billing = get_billing_service()
            if billing.is_billing_enabled():
                user_id = 1
                try:
                    with get_db_connection() as db:
                        cur = db.cursor()
                        cur.execute("SELECT user_id FROM qd_strategies_trading WHERE id = ?", (strategy_id,))
                        row = cur.fetchone()
                        cur.close()
                    user_id = int((row or {}).get('user_id') or 1)
                except Exception:
                    pass
                ok, msg = billing.check_and_consume(
                    user_id=user_id,
                    feature='ai_analysis',
                    reference_id=f"ai_filter_{strategy_id}_{symbol}"
                )
                if not ok:
                    logger.warning(f"AI filter billing failed for strategy {strategy_id}: {msg}")
                    return False, {"ai_decision": "", "reason": f"billing_failed:{msg}"}
        except Exception as e:
            logger.warning(f"AI filter billing check error: {e}")

        try:
            from app.services.fast_analysis import get_fast_analysis_service

            service = get_fast_analysis_service()
            result = service.analyze(market, symbol, language, model=model)

            if isinstance(result, dict) and result.get("error"):
                return False, {"ai_decision": "", "reason": "analysis_error", "analysis_error": str(result.get("error") or "")}

            # FastAnalysisService 直接返回 decision 字段
            ai_dec = str(result.get("decision", "")).strip().upper()
            if not ai_dec or ai_dec not in ("BUY", "SELL", "HOLD"):
                return False, {"ai_decision": ai_dec, "reason": "missing_ai_decision"}

            expected = "BUY" if signal_type == "open_long" else "SELL"
            confidence = result.get("confidence", 50)
            summary = result.get("summary", "")
            
            if ai_dec == expected:
                return True, {"ai_decision": ai_dec, "reason": "match", "confidence": confidence, "summary": summary}
            if ai_dec == "HOLD":
                return False, {"ai_decision": ai_dec, "reason": "ai_hold", "confidence": confidence, "summary": summary}
            return False, {"ai_decision": ai_dec, "reason": "direction_mismatch", "confidence": confidence, "summary": summary}
        except Exception as e:
            return False, {"ai_decision": "", "reason": "analysis_exception", "analysis_error": str(e)}

    def _extract_ai_trade_decision(self, analysis_result: Any) -> str:
        """
        Normalize AI analysis output into one of: BUY / SELL / HOLD / "".
        We primarily look at final_decision.decision, with fallbacks.
        """
        if not isinstance(analysis_result, dict):
            return ""

        def _pick(*paths: str) -> str:
            for p in paths:
                cur: Any = analysis_result
                ok = True
                for k in p.split("."):
                    if not isinstance(cur, dict):
                        ok = False
                        break
                    cur = cur.get(k)
                if ok and cur is not None:
                    s = str(cur).strip()
                    if s:
                        return s
            return ""

        raw = _pick("final_decision.decision", "trader_decision.decision", "decision", "final.decision")
        s = raw.strip().upper()
        if not s:
            return ""

        # Common variants / synonyms
        if "BUY" in s or s == "LONG" or "LONG" in s:
            return "BUY"
        if "SELL" in s or s == "SHORT" or "SHORT" in s:
            return "SELL"
        if "HOLD" in s or "WAIT" in s or "NEUTRAL" in s:
            return "HOLD"
        return s if s in ("BUY", "SELL", "HOLD") else ""

    def _persist_browser_notification(
        self,
        *,
        strategy_id: int,
        symbol: str,
        signal_type: str,
        title: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
        user_id: int = None,
    ) -> None:
        """Best-effort persist notification row for the frontend '通知' panel (browser channel)."""
        try:
            now = int(time.time())
            # Get user_id from strategy if not provided
            if user_id is None:
                try:
                    with get_db_connection() as db:
                        cur = db.cursor()
                        cur.execute("SELECT user_id FROM qd_strategies_trading WHERE id = ?", (strategy_id,))
                        row = cur.fetchone()
                        cur.close()
                    user_id = int((row or {}).get('user_id') or 1)
                except Exception:
                    user_id = 1
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    INSERT INTO qd_strategy_notifications
                    (user_id, strategy_id, symbol, signal_type, channels, title, message, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW())
                    """,
                    (
                        int(user_id),
                        int(strategy_id),
                        str(symbol or ""),
                        str(signal_type or ""),
                        "browser",
                        str(title or ""),
                        str(message or ""),
                        json.dumps(payload or {}, ensure_ascii=False),
                    ),
                )
                db.commit()
                cur.close()
        except Exception as e:
            logger.warning(f"persist_browser_notification failed: {e}")

    def _execute_exchange_order(
        self,
        exchange: Any,
        strategy_id: int,
        symbol: str,
        signal_type: str,
        amount: float,
        ref_price: Optional[float] = None,
        market_type: str = 'swap',
        market_category: str = 'Crypto',
        leverage: float = 1.0,
        margin_mode: str = 'cross',
        stop_loss_price: float = None,
        take_profit_price: float = None,
        signal_reason: str = "",
        trailing_stop_price: float = None,
        # Order execution params (order_mode, maker_wait_sec, maker_offset_bps) are now
        # configured via environment variables: ORDER_MODE, MAKER_WAIT_SEC, MAKER_OFFSET_BPS
        # These parameters are kept for backward compatibility but will be ignored.
        order_mode: str = None,
        maker_wait_sec: float = None,
        maker_retries: int = 3,
        close_fallback_to_market: bool = True,
        open_fallback_to_market: bool = True,
        execution_mode: str = 'signal',
        notification_config: Optional[Dict[str, Any]] = None,
        signal_ts: int = 0,
        price_exchange_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        将信号转为 pending_orders 队列记录（本方法不直连交易所、不使用 ccxt）。

        PendingOrderWorker 轮询执行：
        - execution_mode='signal'：仅通知/模拟路径。
        - execution_mode='live'：通过 live_trading 包内的各交易所 REST 客户端下单（非 ccxt）。

        行情/K 线不在此处拉取；order_mode 等由环境变量配置。
        """
        try:
            # Reference price at enqueue time: use current tick price if provided to avoid extra fetch.
            if ref_price is None:
                ref_price = self._fetch_current_price(
                    None,
                    symbol,
                    market_type=market_type,
                    market_category=market_category,
                    exchange_id=price_exchange_id,
                    kline_market_type=market_type,
                ) or 0.0
            ref_price = float(ref_price or 0.0)

            extra_payload = {
                "ref_price": float(ref_price or 0.0),
                "signal_ts": int(signal_ts or 0),
                "stop_loss_price": float(stop_loss_price or 0.0) if stop_loss_price is not None else 0.0,
                "take_profit_price": float(take_profit_price or 0.0) if take_profit_price is not None else 0.0,
                "trailing_stop_price": float(trailing_stop_price or 0.0) if trailing_stop_price is not None else 0.0,
                "reason": str(signal_reason or "").strip(),
                "margin_mode": str(margin_mode or "cross"),
                "maker_retries": int(maker_retries or 0),
                "close_fallback_to_market": bool(close_fallback_to_market),
                "open_fallback_to_market": bool(open_fallback_to_market),
            }
            if order_mode:
                extra_payload["order_mode"] = order_mode
            pending_id = self._enqueue_pending_order(
                strategy_id=strategy_id,
                symbol=symbol,
                signal_type=signal_type,
                amount=float(amount or 0.0),
                price=float(ref_price or 0.0),
                signal_ts=int(signal_ts or 0),
                market_type=market_type,
                leverage=float(leverage or 1.0),
                execution_mode=execution_mode,
                notification_config=notification_config,
                extra_payload=extra_payload,
            )

            pending_flag = str(execution_mode or "").strip().lower() == "live"

            # Local "signal provider mode": we keep the local state machine moving forward.
            return {
                'success': True,
                'pending': bool(pending_flag),
                'order_id': f"pending_{pending_id or int(time.time()*1000)}",
                'filled_amount': 0 if pending_flag else amount,
                'filled_base_amount': 0 if pending_flag else amount,
                'filled_price': 0 if pending_flag else ref_price,
                'total_cost': 0 if pending_flag else (float(amount or 0.0) * float(ref_price or 0.0) if ref_price else 0),
                'fee': 0,
                'message': 'Order enqueued to pending_orders'
            }
        except Exception as e:
             logger.error(f"Signal execution failed: {e}")
             return {'success': False, 'error': str(e)}

    def _enqueue_pending_order(
        self,
        strategy_id: int,
        symbol: str,
        signal_type: str,
        amount: float,
        price: float,
        signal_ts: int,
        market_type: str,
        leverage: float,
        execution_mode: str,
        notification_config: Optional[Dict[str, Any]] = None,
        extra_payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Insert a pending order record and return its id."""
        try:
            now = int(time.time())
            # Local deployment supports both "signal" and "live" (live is executed by PendingOrderWorker).
            mode = (execution_mode or "signal").strip().lower()
            if mode not in ("signal", "live"):
                mode = "signal"

            payload: Dict[str, Any] = {
                "strategy_id": int(strategy_id),
                "symbol": symbol,
                "signal_type": signal_type,
                "market_type": market_type,
                "amount": float(amount or 0.0),
                "price": float(price or 0.0),
                "leverage": float(leverage or 1.0),
                "execution_mode": mode,
                "notification_config": notification_config or {},
                "signal_ts": int(signal_ts or 0),
            }
            if extra_payload and isinstance(extra_payload, dict):
                payload.update(extra_payload)

            with get_db_connection() as db:
                cur = db.cursor()

                # Extra dedup/cooldown guard (DB-based, more rigorous than local position state):
                # The indicator recompute runs on a fixed tick cadence, and some strategies may keep emitting the same
                # entry/exit signal across multiple ticks/candles (especially when orders fail).
                # We prevent spamming the queue by skipping if a very recent identical order already exists.
                #
                # Rules:
                # - If signal_ts is provided (>0), treat (strategy_id, symbol, signal_type, signal_ts) as the canonical
                #   "same candle" key: if any record already exists, do NOT enqueue again.
                # - Otherwise, fall back to the older (strategy_id, symbol, signal_type) cooldown guard.
                cooldown_sec = 30  # keep small; worker already retries the claimed order via attempts/max_attempts
                try:
                    stsig = int(signal_ts or 0)
                    # Strict "same candle" de-dup applies to open and close signals.
                    # Rationale: 
                    # - open_* signals should only trigger once per candle (prevents repeated entries)
                    # - close_* signals should only trigger once per candle (prevents repeated close attempts)
                    # - add_*/reduce_* signals may legitimately trigger multiple times within same candle
                    #   as price evolves for DCA/scaling strategies
                    sig_norm = str(signal_type or "").strip().lower()
                    strict_candle_dedup = stsig > 0 and sig_norm in ("open_long", "open_short", "close_long", "close_short")

                    if strict_candle_dedup:
                        cur.execute(
                            """
                            SELECT id, status, created_at
                            FROM pending_orders
                            WHERE strategy_id = %s
                              AND symbol = %s
                              AND signal_type = %s
                              AND signal_ts = %s
                            ORDER BY id DESC
                            LIMIT 1
                            """,
                            (int(strategy_id), str(symbol), str(signal_type), int(stsig)),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT id, status, created_at
                            FROM pending_orders
                            WHERE strategy_id = %s
                              AND symbol = %s
                              AND signal_type = %s
                            ORDER BY id DESC
                            LIMIT 1
                            """,
                            (int(strategy_id), str(symbol), str(signal_type)),
                        )
                    last = cur.fetchone() or {}
                    last_id = int(last.get("id") or 0)
                    last_status = str(last.get("status") or "").strip().lower()
                    last_created = int(last.get("created_at") or 0)
                    if last_id > 0:
                        if strict_candle_dedup:
                            logger.info(
                                f"enqueue_pending_order skipped (same candle): existing id={last_id} "
                                f"strategy_id={strategy_id} symbol={symbol} signal={signal_type} signal_ts={stsig} status={last_status}"
                            )
                            cur.close()
                            return None
                        if last_status in ("pending", "processing"):
                            logger.info(
                                f"enqueue_pending_order skipped: existing_inflight id={last_id} "
                                f"strategy_id={strategy_id} symbol={symbol} signal={signal_type} status={last_status}"
                            )
                            cur.close()
                            return None
                        if last_created > 0 and (now - last_created) < cooldown_sec:
                            logger.info(
                                f"enqueue_pending_order cooldown: last_id={last_id} last_status={last_status} "
                                f"age_sec={now - last_created} (<{cooldown_sec}) "
                                f"strategy_id={strategy_id} symbol={symbol} signal={signal_type}"
                            )
                            cur.close()
                            return None
                except Exception:
                    # Best-effort only; do not block enqueue on dedup query errors.
                    pass

                # Get user_id from strategy
                user_id = 1
                try:
                    cur.execute("SELECT user_id FROM qd_strategies_trading WHERE id = %s", (strategy_id,))
                    row = cur.fetchone()
                    user_id = int((row or {}).get('user_id') or 1)
                except Exception:
                    pass

                cur.execute(
                    """
                    INSERT INTO pending_orders
                    (user_id, strategy_id, symbol, signal_type, signal_ts, market_type, order_type, amount, price,
                     execution_mode, status, priority, attempts, max_attempts, last_error, payload_json,
                     created_at, updated_at, processed_at, sent_at)
                    VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                     %s, %s, %s, %s, %s, %s, %s,
                     NOW(), NOW(), NULL, NULL)
                    """,
                    (
                        int(user_id),
                        int(strategy_id),
                        symbol,
                        signal_type,
                        int(signal_ts or 0),
                        market_type or 'swap',
                        'market',
                        float(amount or 0.0),
                        float(price or 0.0),
                        mode,
                        'pending',
                        0,
                        0,
                        10,
                        '',
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
                pending_id = cur.lastrowid
                db.commit()
                cur.close()
            return int(pending_id) if pending_id is not None else None
        except Exception as e:
            logger.error(f"enqueue_pending_order failed: {e}")
            return None

    def _place_stop_loss_order(self, *args, **kwargs):
        pass

    @staticmethod
    def _signal_reason_log_suffix(signal: Optional[Dict[str, Any]]) -> str:
        info = signal if isinstance(signal, dict) else {}
        reason = str(info.get("reason") or "").strip()
        if not reason:
            return ""

        parts = [f"reason={reason}"]
        for key, label in (
            ("stop_loss_price", "sl"),
            ("take_profit_price", "tp"),
            ("trailing_stop_price", "trail"),
        ):
            value = info.get(key)
            if value is None:
                continue
            try:
                fv = float(value)
            except Exception:
                continue
            if fv > 0:
                parts.append(f"{label}={fv:.6f}")
        return f", {', '.join(parts)}"

    def _get_available_capital(
        self,
        strategy_id: int,
        initial_capital: float,
        current_positions: Optional[List[Dict[str, Any]]] = None,
        current_price: Optional[float] = None,
        symbol: str = "",
    ) -> float:
        """获取当前策略可用于仓位计算的净值口径资金。"""
        return self._calculate_current_equity(
            strategy_id,
            initial_capital,
            current_positions=current_positions,
            current_price=current_price,
            symbol=symbol,
        )

    def _calculate_current_equity(
        self,
        strategy_id: int,
        initial_capital: float,
        current_positions: Optional[List[Dict[str, Any]]] = None,
        current_price: Optional[float] = None,
        symbol: str = "",
    ) -> float:
        realized_pnl = 0.0
        unrealized_pnl = 0.0
        try:
            with get_db_connection() as db:
                cursor = db.cursor()
                cursor.execute(
                    """
                    SELECT COALESCE(SUM(COALESCE(profit, 0) - COALESCE(commission, 0)), 0) AS realized_pnl
                    FROM qd_strategy_trades
                    WHERE strategy_id = %s
                    """,
                    (strategy_id,)
                )
                row = cursor.fetchone() or {}
                realized_pnl = float(row.get('realized_pnl') or 0.0)
                cursor.close()
        except Exception as e:
            logger.warning(f"Failed to calculate realized pnl for strategy {strategy_id}: {e}")

        positions = list(current_positions or [])
        if not positions:
            try:
                positions = self._get_all_positions(strategy_id) or []
            except Exception:
                positions = []

        normalized_symbol = (symbol or "").split(':')[0]
        for pos in positions:
            try:
                side = str(pos.get('side') or '').strip().lower()
                size = float(pos.get('size') or 0.0)
                entry_price = float(pos.get('entry_price') or 0.0)
                if size <= 0 or entry_price <= 0 or side not in ('long', 'short'):
                    continue

                mark_price = pos.get('current_price')
                pos_symbol = str(pos.get('symbol') or '')
                if current_price and normalized_symbol and pos_symbol.split(':')[0] == normalized_symbol:
                    mark_price = current_price
                mark_price = float(mark_price or 0.0)
                if mark_price <= 0:
                    continue

                if side == 'long':
                    unrealized_pnl += (mark_price - entry_price) * size
                else:
                    unrealized_pnl += (entry_price - mark_price) * size
            except Exception:
                continue

        equity = float(initial_capital or 0.0) + realized_pnl + unrealized_pnl
        return max(0.0, equity)

    def _current_position_value(
        self,
        current_positions: Optional[List[Dict[str, Any]]],
        current_price: Optional[float],
    ) -> float:
        """Calculate total USDT notional of all open positions."""
        total = 0.0
        for pos in (current_positions or []):
            try:
                size = float(pos.get("size") or 0)
                entry = float(pos.get("entry_price") or 0)
                mark = float(current_price or entry or 0)
                if size > 0 and mark > 0:
                    total += size * mark
            except Exception:
                continue
        return total

    def _get_daily_pnl(self, strategy_id: int) -> float:
        """Get today's realized PnL (profit minus fees) for the strategy."""
        try:
            with get_db_connection() as db:
                cursor = db.cursor()
                cursor.execute(
                    """
                    SELECT COALESCE(SUM(COALESCE(profit, 0) - COALESCE(commission, 0)), 0) AS daily_pnl
                    FROM qd_strategy_trades
                    WHERE strategy_id = %s AND created_at::date = CURRENT_DATE
                    """,
                    (strategy_id,),
                )
                row = cursor.fetchone() or {}
                cursor.close()
                return float(row.get("daily_pnl") or 0.0)
        except Exception as e:
            logger.warning(f"Failed to get daily pnl for strategy {strategy_id}: {e}")
            return 0.0

    def _record_trade(
        self,
        strategy_id: int,
        symbol: str,
        type: str,
        price: float,
        amount: float,
        value: float,
        profit: float = None,
        commission: float = None,
        close_reason: str = "",
        matched_entry_price: Optional[float] = None,
        grid_matched_profit: Optional[float] = None,
    ):
        """记录交易到数据库（signal 模拟模式）"""
        try:
            from app.services.live_trading.leg_context import resolve_leg_context
            from app.services.live_trading.records import record_trade

            leg = resolve_leg_context(
                strategy_id=int(strategy_id),
                symbol=str(symbol or ""),
                fill_source="signal_sim",
            )
            record_trade(
                strategy_id=int(strategy_id),
                symbol=str(symbol or ""),
                trade_type=str(type or ""),
                price=float(price or 0.0),
                amount=float(amount or 0.0),
                commission=float(commission or 0.0),
                profit=profit,
                close_reason=str(close_reason or ""),
                matched_entry_price=matched_entry_price,
                grid_matched_profit=grid_matched_profit,
                leg=leg,
            )
        except Exception as e:
            logger.error(f"Failed to record trade: {e}")

    def _update_position(
        self,
        strategy_id: int,
        symbol: str,
        side: str,
        size: float,
        entry_price: float,
        current_price: float,
        highest_price: float = 0.0,
        lowest_price: float = 0.0,
        execution_mode: str = "signal",
    ):
        """更新持仓状态"""
        mode = str(execution_mode or "signal").strip().lower()
        if mode == "live":
            # Live size/entry is owned by PendingOrderWorker + position sync.
            # Only patch trailing markers on rows that still exist.
            try:
                from app.services.live_trading.records import patch_position_markers

                patch_position_markers(
                    strategy_id=int(strategy_id),
                    symbol=str(symbol or ""),
                    side=str(side or ""),
                    current_price=float(current_price or 0.0),
                    highest_price=float(highest_price or 0.0),
                    lowest_price=float(lowest_price or 0.0),
                )
            except Exception as e:
                logger.warning("patch_position_markers failed sid=%s: %s", strategy_id, e)
            return
        try:
            # Get user_id from strategy
            user_id = 1
            with get_db_connection() as db:
                cursor = db.cursor()
                try:
                    cursor.execute("SELECT user_id FROM qd_strategies_trading WHERE id = %s", (strategy_id,))
                    row = cursor.fetchone()
                    user_id = int((row or {}).get('user_id') or 1)
                except Exception:
                    pass
                # 简化：直接 Update 或 Insert
                upsert_query = """
                    INSERT INTO qd_strategy_positions (
                        user_id, strategy_id, symbol, side, size, entry_price, current_price, highest_price, lowest_price, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                    ) ON CONFLICT(strategy_id, symbol, side) DO UPDATE SET
                        size = excluded.size,
                        entry_price = excluded.entry_price,
                        current_price = excluded.current_price,
                        highest_price = CASE WHEN excluded.highest_price > 0 THEN excluded.highest_price ELSE qd_strategy_positions.highest_price END,
                        lowest_price = CASE WHEN excluded.lowest_price > 0 THEN excluded.lowest_price ELSE qd_strategy_positions.lowest_price END,
                        updated_at = NOW()
                """
                cursor.execute(upsert_query, (
                    user_id, strategy_id, symbol, side, size, entry_price, current_price, highest_price, lowest_price
                ))
                db.commit()
                cursor.close()
        except Exception as e:
            logger.error(f"Failed to update position: {e}")

    def _close_position(self, strategy_id: int, symbol: str, side: str):
        """平仓：删除持仓记录"""
        try:
            with get_db_connection() as db:
                cursor = db.cursor()
                cursor.execute("DELETE FROM qd_strategy_positions WHERE strategy_id = %s AND symbol = %s AND side = %s", (strategy_id, symbol, side))
                db.commit()
                cursor.close()
        except Exception as e:
            logger.error(f"Failed to close position: {e}")
    
    def _delete_position_by_id(self, position_id: int):
         pass

    def _update_positions(self, strategy_id: int, symbol: str, current_price: float):
        """更新所有持仓的当前价格"""
        try:
            with get_db_connection() as db:
                cursor = db.cursor()
                cursor.execute("UPDATE qd_strategy_positions SET current_price = %s WHERE strategy_id = %s AND symbol = %s", (current_price, strategy_id, symbol))
                db.commit()
                cursor.close()
        except Exception:
            pass
            
    def _get_indicator_code_from_db(self, indicator_id: int) -> Optional[str]:
        try:
            with get_db_connection() as db:
                cursor = db.cursor()
                cursor.execute("SELECT code FROM qd_indicator_codes WHERE id = %s", (indicator_id,))
                result = cursor.fetchone()
                return result['code'] if result else None
        except:
            return None
    
    def _get_all_positions(self, strategy_id: int) -> List[Dict[str, Any]]:
        """获取策略的所有持仓（截面策略使用）"""
        try:
            with get_db_connection() as db:
                cursor = db.cursor()
                cursor.execute("""
                    SELECT id, symbol, side, size, entry_price, current_price, highest_price, lowest_price
                    FROM qd_strategy_positions
                    WHERE strategy_id = %s
                """, (strategy_id,))
                return cursor.fetchall() or []
        except Exception as e:
            logger.error(f"Failed to get all positions: {e}")
            return []
    
    def _should_rebalance(self, strategy_id: int, rebalance_frequency: str) -> bool:
        """检查是否应该调仓"""
        try:
            with get_db_connection() as db:
                cursor = db.cursor()
                cursor.execute("""
                    SELECT last_rebalance_at FROM qd_strategies_trading WHERE id = %s
                """, (strategy_id,))
                result = cursor.fetchone()
                if not result or not result.get('last_rebalance_at'):
                    return True
                
                last_rebalance = result['last_rebalance_at']
                if isinstance(last_rebalance, str):
                    from datetime import datetime
                    last_rebalance = datetime.fromisoformat(last_rebalance.replace('Z', '+00:00'))
                
                now = datetime.now()
                delta = now - last_rebalance
                
                if rebalance_frequency == 'daily':
                    return delta.days >= 1
                elif rebalance_frequency == 'weekly':
                    return delta.days >= 7
                elif rebalance_frequency == 'monthly':
                    return delta.days >= 30
                return True
        except Exception as e:
            logger.error(f"Failed to check rebalance: {e}")
            return True
    
    def _update_last_rebalance(self, strategy_id: int):
        """更新上次调仓时间"""
        try:
            with get_db_connection() as db:
                cursor = db.cursor()
                # Try to update, if column doesn't exist, ignore
                try:
                    cursor.execute("""
                        UPDATE qd_strategies_trading 
                        SET last_rebalance_at = NOW() 
                        WHERE id = %s
                    """, (strategy_id,))
                    db.commit()
                except Exception:
                    # Column may not exist, that's OK
                    pass
                cursor.close()
        except Exception as e:
            logger.warning(f"Failed to update last_rebalance_at: {e}")
    
    @staticmethod
    def _cs_bare_symbol(symbol: str) -> str:
        """Strip market prefix for K-line fetch / position matching (``Crypto:BTC/USDT`` -> ``BTC/USDT``)."""
        s = str(symbol or "").strip()
        if ":" in s:
            return s.split(":", 1)[-1].strip()
        return s

    @staticmethod
    def _normalize_cs_symbol_list(symbol_list: List[str], market_category: str) -> List[str]:
        """Return bare symbols for cross-sectional execution."""
        from app.services.symbol_name import normalize_crypto_symbol

        out: List[str] = []
        default_market = (market_category or "Crypto").strip() or "Crypto"
        for entry in symbol_list or []:
            raw = str(entry or "").strip()
            if not raw:
                continue
            if ":" in raw:
                mkt, sym = raw.split(":", 1)
            else:
                mkt, sym = default_market, raw
            sym = sym.strip()
            if not sym:
                continue
            if mkt == "Crypto":
                sym = normalize_crypto_symbol(sym)
            out.append(sym)
        return out

    def _execute_cross_sectional_indicator(
        self,
        indicator_code: str,
        symbols: List[str],
        trading_config: Dict[str, Any],
        market_category: str,
        timeframe: str,
        exchange_id: Optional[str] = None,
        market_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        执行截面策略指标，返回所有标的的评分和排序
        """
        try:
            # 获取所有标的的K线数据
            all_data = {}
            for symbol in symbols:
                kline_symbol = self._cs_bare_symbol(symbol)
                try:
                    klines = self._fetch_latest_kline(
                        kline_symbol, timeframe, limit=200, market_category=market_category,
                        exchange_id=exchange_id, market_type=market_type,
                    )
                    if klines and len(klines) >= 2:
                        df = self._klines_to_dataframe(klines)
                        if len(df) > 0:
                            all_data[kline_symbol] = df
                except Exception as e:
                    logger.warning(f"Failed to fetch data for {kline_symbol}: {e}")
                    continue
            
            if not all_data:
                logger.error("No data available for cross-sectional strategy")
                return None
            
            # 准备执行环境
            exec_env = {
                'symbols': list(all_data.keys()),
                'data': all_data,  # {symbol: df}
                'scores': {},  # 用于存储评分
                'rankings': [],  # 用于存储排序
                'np': np,
                'pd': pd,
                'trading_config': trading_config,
                'config': trading_config,
            }
            
            from app.utils.safe_exec import build_safe_builtins, safe_exec_with_validation

            exec_env['__builtins__'] = build_safe_builtins()

            exec_result = safe_exec_with_validation(
                code=indicator_code,
                exec_globals=exec_env,
                timeout=60,
            )
            if not exec_result['success']:
                raise ValueError(f"Cross-sectional indicator failed: {exec_result['error']}")
            
            scores_raw = exec_env.get('scores', {}) or {}
            rankings_raw = exec_env.get('rankings', []) or []

            scores: Dict[str, float] = {}
            for k, v in scores_raw.items():
                bare = self._cs_bare_symbol(k)
                try:
                    scores[bare] = float(v)
                except (TypeError, ValueError):
                    scores[bare] = 0.0

            rankings: List[str] = []
            for item in rankings_raw:
                bare = self._cs_bare_symbol(item)
                if bare and bare not in rankings:
                    rankings.append(bare)

            # 如果没有提供rankings，根据scores排序
            if not rankings and scores:
                rankings = sorted(scores.keys(), key=lambda x: scores.get(x, 0), reverse=True)
            
            return {
                'scores': scores,
                'rankings': rankings
            }
        except Exception as e:
            logger.error(f"Failed to execute cross-sectional indicator: {e}")
            logger.error(traceback.format_exc())
            return None
    
    def _generate_cross_sectional_signals(
        self,
        strategy_id: int,
        rankings: List[str],
        scores: Dict[str, float],
        trading_config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        根据排序结果生成截面策略信号
        """
        try:
            portfolio_size = int(trading_config.get('portfolio_size', 10) or 10)
        except (TypeError, ValueError):
            portfolio_size = 10
        portfolio_size = max(1, portfolio_size)

        market_type = str(trading_config.get('market_type') or 'swap').strip().lower()
        long_ratio = float(trading_config.get('long_ratio', 0.5))
        if market_type == 'spot':
            long_ratio = 1.0

        per_leg_ratio = 1.0 / float(portfolio_size)

        # 选择持仓标的
        long_count = int(portfolio_size * long_ratio)
        short_count = portfolio_size - long_count
        
        long_symbols = set(rankings[:long_count]) if long_count > 0 else set()
        short_symbols = set(rankings[-short_count:]) if short_count > 0 and len(rankings) >= short_count else set()
        
        # 获取当前持仓
        current_positions = self._get_all_positions(strategy_id)
        current_long = {self._cs_bare_symbol(p['symbol']) for p in current_positions if p.get('side') == 'long'}
        current_short = {self._cs_bare_symbol(p['symbol']) for p in current_positions if p.get('side') == 'short'}
        
        signals = []
        
        # 生成做多信号
        for symbol in long_symbols:
            sym = self._cs_bare_symbol(symbol)
            if sym not in current_long:
                # 如果当前没有多仓，开多
                if sym in current_short:
                    # 如果当前是空仓，先平空再开多
                    signals.append({
                        'symbol': sym,
                        'type': 'close_short',
                        'score': scores.get(sym, 0),
                        'position_size': per_leg_ratio,
                    })
                signals.append({
                    'symbol': sym,
                    'type': 'open_long',
                    'score': scores.get(sym, 0),
                    'position_size': per_leg_ratio,
                })
        
        # 平掉不在做多列表中的多仓
        long_bare = {self._cs_bare_symbol(s) for s in long_symbols}
        for symbol in current_long:
            if symbol not in long_bare:
                signals.append({
                    'symbol': symbol,
                    'type': 'close_long',
                    'score': scores.get(symbol, 0),
                })
        
        # 生成做空信号
        for symbol in short_symbols:
            sym = self._cs_bare_symbol(symbol)
            if sym not in current_short:
                # 如果当前没有空仓，开空
                if sym in current_long:
                    # 如果当前是多仓，先平多再开空
                    signals.append({
                        'symbol': sym,
                        'type': 'close_long',
                        'score': scores.get(sym, 0),
                    })
                signals.append({
                    'symbol': sym,
                    'type': 'open_short',
                    'score': scores.get(sym, 0),
                    'position_size': per_leg_ratio,
                })
        
        # 平掉不在做空列表中的空仓
        short_bare = {self._cs_bare_symbol(s) for s in short_symbols}
        for symbol in current_short:
            if symbol not in short_bare:
                signals.append({
                    'symbol': symbol,
                    'type': 'close_short',
                    'score': scores.get(symbol, 0),
                })
        
        return signals
    
    def _run_cross_sectional_strategy_loop(
        self,
        strategy_id: int,
        strategy: Dict[str, Any],
        trading_config: Dict[str, Any],
        indicator_config: Dict[str, Any],
        ai_model_config: Dict[str, Any],
        execution_mode: str,
        notification_config: Dict[str, Any],
        strategy_name: str,
        market_category: str,
        market_type: str,
        leverage: float,
        initial_capital: float,
        indicator_code: str,
        indicator_id: Optional[int]
    ):
        """
        截面策略执行循环
        """
        logger.info(f"Starting cross-sectional strategy loop for strategy {strategy_id}")

        exchange_config = strategy.get('exchange_config') or {}
        kline_exchange_id, kline_market_type = self._live_crypto_kline_params(
            market_category=market_category,
            market_type=market_type,
            execution_mode=execution_mode,
            exchange_config=exchange_config,
            trading_config=trading_config,
            user_id=int(strategy.get('user_id') or 1),
        )
        self._log_crypto_kline_source(
            strategy_id, market_category, execution_mode, kline_exchange_id, kline_market_type
        )
        
        symbol_list = self._normalize_cs_symbol_list(
            trading_config.get('symbol_list', []) or [],
            market_category,
        )
        if not symbol_list:
            logger.error(f"Strategy {strategy_id} has no symbol_list for cross-sectional strategy")
            return
        
        timeframe = trading_config.get('timeframe', '1H')
        rebalance_frequency = trading_config.get('rebalance_frequency', 'daily')
        tick_interval_sec = int(trading_config.get('decide_interval', 300))
        
        last_tick_time = 0
        last_rebalance_time = 0
        
        while True:
            try:
                # 检查策略状态
                if not self._is_strategy_running(strategy_id):
                    logger.info(f"Cross-sectional strategy {strategy_id} stopped")
                    break
                
                current_time = time.time()
                
                # Sleep until next tick
                if last_tick_time > 0:
                    sleep_sec = (last_tick_time + tick_interval_sec) - current_time
                    if sleep_sec > 0:
                        time.sleep(min(sleep_sec, 1.0))
                        continue
                last_tick_time = current_time
                
                # 检查是否需要调仓
                if not self._should_rebalance(strategy_id, rebalance_frequency):
                    continue
                
                logger.info(f"Cross-sectional strategy {strategy_id} rebalancing...")
                
                # 执行截面指标
                result = self._execute_cross_sectional_indicator(
                    indicator_code, symbol_list, trading_config, market_category, timeframe,
                    exchange_id=kline_exchange_id, market_type=kline_market_type,
                )
                
                if not result:
                    logger.warning(f"Cross-sectional indicator returned no result")
                    continue
                
                # 生成信号
                signals = self._generate_cross_sectional_signals(
                    strategy_id, result['rankings'], result['scores'], trading_config
                )
                
                if not signals:
                    logger.info(f"No rebalancing needed for strategy {strategy_id}")
                    self._update_last_rebalance(strategy_id)
                    continue
                
                logger.info(f"Generated {len(signals)} signals for cross-sectional strategy {strategy_id}")
                
                current_positions = self._get_all_positions(strategy_id) or []

                # 批量执行交易
                from concurrent.futures import ThreadPoolExecutor, as_completed
                with ThreadPoolExecutor(max_workers=min(10, len(signals))) as executor:
                    futures = {}
                    for signal in signals:
                        future = executor.submit(
                            self._execute_signal,
                            strategy_id=strategy_id,
                            strategy_name=strategy_name,
                            exchange=None,  # Signal mode
                            symbol=signal['symbol'],
                            current_price=0.0,  # Will be fetched in _execute_signal
                            signal_type=signal['type'],
                            position_size=signal.get('position_size'),
                            current_positions=current_positions,
                            trade_direction='both',
                            leverage=leverage,
                            initial_capital=initial_capital,
                            market_type=market_type,
                            market_category=market_category,
                            margin_mode='cross',
                            stop_loss_price=None,
                            take_profit_price=None,
                            execution_mode=execution_mode,
                            notification_config=notification_config,
                            trading_config=trading_config,
                            ai_model_config=ai_model_config,
                            signal_ts=int(current_time),
                            price_exchange_id=kline_exchange_id,
                        )
                        futures[future] = signal
                    
                    # 等待所有交易完成
                    for future in as_completed(futures):
                        signal = futures[future]
                        try:
                            result = future.result(timeout=30)
                            if result:
                                logger.info(f"Successfully executed signal: {signal['symbol']} {signal['type']}")
                        except Exception as e:
                            logger.error(f"Failed to execute signal {signal['symbol']} {signal['type']}: {e}")
                
                # 更新调仓时间
                self._update_last_rebalance(strategy_id)
                last_rebalance_time = current_time
                
            except Exception as e:
                logger.error(f"Cross-sectional strategy loop error: {e}")
                logger.error(traceback.format_exc())
                time.sleep(5)  # Wait before retrying