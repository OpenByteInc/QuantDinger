"""
Pending order worker.

This worker polls `pending_orders` periodically and dispatches orders based on `execution_mode`:
- signal: send notifications (no real trading).
- live: not implemented (paper mode only).
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from app.services.signal_notifier import SignalNotifier
from app.services.exchange_execution import load_strategy_configs, resolve_exchange_config, safe_exchange_config_for_log
from app.services.live_trading.execution import place_order_from_signal
from app.services.live_trading.factory import create_client
from app.services.live_trading.records import apply_fill_to_local_position, record_trade
from app.utils.pnl import calc_notional_value
from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.okx import OkxClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.coinbase_exchange import CoinbaseExchangeClient
from app.services.live_trading.kraken import KrakenClient
from app.services.live_trading.kraken_futures import KrakenFuturesClient
from app.services.live_trading.kucoin import KucoinSpotClient
from app.services.live_trading.kucoin import KucoinFuturesClient
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
from app.services.live_trading.deepcoin import DeepcoinClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.symbols import to_okx_swap_inst_id
from app.services.live_trading.symbols import to_gate_currency_pair
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.strategy_runtime_logs import append_strategy_log
from app.services.strategy_lifecycle import (
    auto_stop_live_strategy,
    is_fatal_exchange_error,
    should_skip_position_sync,
)

# Lazy import IBKR to avoid ImportError if ib_insync not installed
IBKRClient = None

# Lazy import MT5 to avoid ImportError if MetaTrader5 not installed
MT5Client = None

# Lazy import Alpaca to avoid ImportError if alpaca-py not installed
AlpacaClient = None

logger = get_logger(__name__)

# PositionSync: one exchange snapshot per credential per TTL window (many strategies
# often share the same API key — without this we N× get_positions and trigger bans).
_position_sync_snapshot_cache: Dict[str, Tuple[float, Dict[str, Dict[str, float]], Dict[str, Dict[str, float]]]] = {}
_exchange_sync_backoff_until: Dict[str, float] = {}
_position_sync_cache_lock = threading.Lock()


def _position_sync_cache_key(
    user_id: int,
    exchange_id: str,
    market_type: str,
    exchange_config: Dict[str, Any],
) -> str:
    cred_id = exchange_config.get("credential_id") or exchange_config.get("credentials_id")
    if cred_id:
        return f"u{int(user_id)}:{exchange_id}:{market_type}:cred:{int(cred_id)}"
    hint = str(exchange_config.get("api_key") or exchange_config.get("apiKey") or "")[-16:]
    return f"u{int(user_id)}:{exchange_id}:{market_type}:inline:{hint}"


def _position_sync_cache_ttl_sec() -> float:
    try:
        custom = float(os.getenv("POSITION_SYNC_CACHE_TTL_SEC", "0"))
        if custom > 0:
            return custom
    except Exception:
        pass
    try:
        interval = float(os.getenv("POSITION_SYNC_INTERVAL_SEC", "30"))
        return max(30.0, interval)
    except Exception:
        return 60.0


def _get_position_sync_snapshot(
    cache_key: str,
) -> Optional[Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, float]]]]:
    now = time.time()
    with _position_sync_cache_lock:
        entry = _position_sync_snapshot_cache.get(cache_key)
        if not entry:
            return None
        expires, exch_size, exch_entry = entry
        if now >= expires:
            _position_sync_snapshot_cache.pop(cache_key, None)
            return None
        return exch_size, exch_entry


def _set_position_sync_snapshot(
    cache_key: str,
    exch_size: Dict[str, Dict[str, float]],
    exch_entry_price: Dict[str, Dict[str, float]],
) -> None:
    ttl = _position_sync_cache_ttl_sec()
    with _position_sync_cache_lock:
        _position_sync_snapshot_cache[cache_key] = (
            time.time() + ttl,
            exch_size,
            exch_entry_price,
        )


def _exchange_sync_backoff_sec() -> float:
    try:
        return max(60.0, float(os.getenv("EXCHANGE_SYNC_BACKOFF_SEC", "900")))
    except Exception:
        return 900.0


def _is_exchange_sync_backoff(cache_key: str) -> bool:
    with _position_sync_cache_lock:
        until = float(_exchange_sync_backoff_until.get(cache_key) or 0.0)
    return time.time() < until


def _set_exchange_sync_backoff(cache_key: str, seconds: Optional[float] = None) -> None:
    sec = float(seconds if seconds is not None else _exchange_sync_backoff_sec())
    with _position_sync_cache_lock:
        _exchange_sync_backoff_until[cache_key] = time.time() + sec


def _is_exchange_rate_limit_error(msg: str) -> bool:
    m = (msg or "").lower()
    return any(
        token in m
        for token in (
            "418",
            "-1003",
            "too many requests",
            "rate limit",
            "banned until",
        )
    )


class PendingOrderWorker:
    def __init__(self, poll_interval_sec: float = 1.0, batch_size: int = 50):
        self.poll_interval_sec = float(poll_interval_sec)
        self.batch_size = int(batch_size)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._notifier = SignalNotifier()

        # Reclaim stuck orders (e.g. if the worker crashed after claiming an order).
        try:
            self._stale_processing_sec = int(os.getenv("PENDING_ORDER_STALE_SEC", "90"))
        except Exception:
            self._stale_processing_sec = 90

        # Position sync self-check (best-effort): keep local positions aligned with exchange.
        self._position_sync_enabled = os.getenv("POSITION_SYNC_ENABLED", "true").lower() == "true"
        self._position_sync_interval_sec = float(os.getenv("POSITION_SYNC_INTERVAL_SEC", "30"))
        self._last_position_sync_ts = 0.0
        logger.info(f"PendingOrderWorker: sync_enabled={self._position_sync_enabled}, interval={self._position_sync_interval_sec}s")

    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, name="PendingOrderWorker", daemon=True)
            self._thread.start()
            logger.info("PendingOrderWorker started")
            return True

    def stop(self, timeout_sec: float = 5.0) -> None:
        with self._lock:
            self._stop_event.set()
            th = self._thread
        if th and th.is_alive():
            th.join(timeout=timeout_sec)
        logger.info("PendingOrderWorker stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.warning(f"PendingOrderWorker tick error: {e}")
            time.sleep(self.poll_interval_sec)

    def _tick(self) -> None:
        # logger.info(f"[PendingOrderWorker] _tick start. last_sync={self._last_position_sync_ts}")
        orders = self._fetch_pending_orders(limit=self.batch_size)
        # logger.info(f"[PendingOrderWorker] orders fetched: {len(orders)}")
        if not orders:
            self._maybe_sync_positions()
            return

        for o in orders:
            oid = o.get("id")
            if not oid:
                continue

            # Mark processing (best-effort)
            if not self._mark_processing(order_id=int(oid)):
                continue

            try:
                self._dispatch_one(o)
            except Exception as e:
                self._mark_failed(order_id=int(oid), error=str(e))

        self._maybe_sync_positions()

    def _maybe_sync_positions(self) -> None:
        if not self._position_sync_enabled:
            return
        now = time.time()
        if self._position_sync_interval_sec <= 0:
            return
        if now - float(self._last_position_sync_ts or 0.0) < float(self._position_sync_interval_sec):
            return
        logger.debug(f"[PendingOrderWorker] Triggering sync... (now={now}, last={self._last_position_sync_ts})")
        self._last_position_sync_ts = now
        try:
            self._sync_positions_best_effort()
        except Exception as e:
            logger.debug(f"position sync skipped/failed: {e}")

    def _sync_positions_best_effort(self, target_strategy_id: Optional[int] = None) -> None:
        """
        Best-effort reconciliation:
        - If exchange position is flat, delete local row from qd_strategy_positions.
        - If exchange position size differs, update local size (optional best-effort).

        This prevents "ghost positions" when positions are closed externally on the exchange.
        """
        # 1) Load local positions (filtered if target_strategy_id provided)
        logger.debug(f"[PositionSync] Entering _sync_positions_best_effort for target={target_strategy_id}")
        with get_db_connection() as db:
            cur = db.cursor()
            if target_strategy_id:
                cur.execute(
                    "SELECT id, strategy_id, symbol, side, size, entry_price FROM qd_strategy_positions WHERE strategy_id = %s ORDER BY updated_at DESC", 
                    (int(target_strategy_id),)
                )
            else:
                cur.execute("SELECT id, strategy_id, symbol, side, size, entry_price FROM qd_strategy_positions ORDER BY updated_at DESC")
            rows = cur.fetchall() or []
            cur.close()

        # [Defect Fix] Removed early return to allow syncing active strategies even if local DB is empty.
        # if not rows and not target_strategy_id:
        #    return

        # Group by strategy_id for efficient exchange queries.
        sid_to_rows: Dict[int, List[Dict[str, Any]]] = {}
        for r in rows:
            sid = int(r.get("strategy_id") or 0)
            if sid <= 0:
                continue
            sid_to_rows.setdefault(sid, []).append(r)
        
        # If targeted sync but no local rows found, we assume user might have opened position externally 
        # but DB is empty. However, without knowing *which* symbol to check, we can't easily auto-discover 
        # unless we fetch ALL positions from exchange for that strategy.
        # But `load_strategy_configs(sid)` gives us the exchange keys. 
        # So if target_strategy_id is set but `sid_to_rows` is empty, we SHOULD explicitly add it to `sid_to_rows`
        # so logic below enters and calls `client.get_positions()`.
        if target_strategy_id and target_strategy_id not in sid_to_rows:
             sid_to_rows[target_strategy_id] = []

        # [Log Fix] Load all ACTIVE LIVE strategies to ensure we sync/log them even if local DB is empty.
        # Otherwise, if we have no local positions, we would silently skip the exchange check.
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                # Fetch all strategies configured for LIVE execution
                cur.execute("SELECT id FROM qd_strategies_trading WHERE status = 'running' AND execution_mode = 'live'")
                active_rows = cur.fetchall() or []
                cur.close()
            
            logger.debug(f"[PositionSync] Found {len(active_rows)} active live strategies in DB.")
            for _ar in active_rows:
                _sid = int(_ar.get("id") or 0)
                if _sid <= 0 or should_skip_position_sync(_sid):
                    continue
                if _sid not in sid_to_rows:
                    if target_strategy_id and target_strategy_id != _sid:
                        continue
                    sid_to_rows[_sid] = []
        except Exception as e:
            logger.error(f"Failed to load active strategies for sync: {e}", exc_info=True)

        # 2) Reconcile per strategy
        for sid, plist in sid_to_rows.items():
            if target_strategy_id and sid != target_strategy_id:
                continue
            if should_skip_position_sync(int(sid)):
                continue
            try:
                sc = load_strategy_configs(int(sid))
                exec_mode = (sc.get("execution_mode") or "").strip().lower()
                # 修改：即使signal模式，如果指定了target_strategy_id（策略启动时调用），也要同步
                # 这样可以清理用户在交易所手动平仓但数据库记录还在的"幽灵持仓"
                if exec_mode != "live" and not target_strategy_id:
                    logger.debug(f"[PositionSync] Strategy {sid} skipped: execution_mode='{exec_mode}' (needs 'live' or explicit target)")
                    continue
                sync_user_id = int(sc.get("user_id") or 1)
                exchange_config = resolve_exchange_config(sc.get("exchange_config") or {}, user_id=sync_user_id)
                safe_cfg = safe_exchange_config_for_log(exchange_config)
                
                # 检查 exchange_id 是否有效，如果为空或无效则跳过同步（signal模式可能没有配置交易所）
                exchange_id = str(exchange_config.get("exchange_id") or "").strip().lower()
                if not exchange_id:
                    logger.debug(f"[PositionSync] Strategy {sid} skipped: exchange_id is empty (signal mode or no exchange config)")
                    continue
                
                market_type = (sc.get("market_type") or exchange_config.get("market_type") or "swap")
                market_type = str(market_type or "swap").strip().lower()
                if market_type in ("futures", "future", "perp", "perpetual"):
                    market_type = "swap"
                
                # Get strategy's trading symbol(s) to filter positions
                # Only sync positions for symbols that this strategy actually trades
                strategy_symbol = (sc.get("symbol") or "").strip()
                trading_config = sc.get("trading_config") or {}
                symbol_list = trading_config.get("symbol_list") or []
                # Normalize symbol list: convert to set for fast lookup
                allowed_symbols = set()
                if strategy_symbol:
                    allowed_symbols.add(strategy_symbol.upper())
                for sym in symbol_list:
                    if sym and isinstance(sym, str):
                        allowed_symbols.add(sym.strip().upper())

                # Lazy import MT5 / IBKR / Alpaca clients here so the elif chain
                # below can rely on isinstance() checks without paying the import
                # cost on systems that don't ship those broker libs.
                global MT5Client
                if MT5Client is None:
                    try:
                        from app.services.mt5_trading import MT5Client as _MT5Client
                        MT5Client = _MT5Client
                    except ImportError:
                        pass

                global IBKRClient
                if IBKRClient is None:
                    try:
                        from app.services.ibkr_trading import IBKRClient as _IBKRClient
                        IBKRClient = _IBKRClient
                    except ImportError:
                        pass

                global AlpacaClient
                if AlpacaClient is None:
                    try:
                        from app.services.alpaca_trading import AlpacaClient as _AlpacaClient
                        AlpacaClient = _AlpacaClient
                    except ImportError:
                        pass

                cache_key = _position_sync_cache_key(sync_user_id, exchange_id, market_type, exchange_config)
                cached_snap = _get_position_sync_snapshot(cache_key)
                exch_size: Dict[str, Dict[str, float]] = {}
                exch_entry_price: Dict[str, Dict[str, float]] = {}

                if cached_snap is not None:
                    exch_size, exch_entry_price = cached_snap
                else:
                    if _is_exchange_sync_backoff(cache_key):
                        logger.warning(
                            "[PositionSync] Strategy %s skipped: %s sync backoff active (key=%s)",
                            sid,
                            exchange_id,
                            cache_key,
                        )
                        continue

                    # 尝试创建客户端，如果失败则跳过（可能是配置错误）
                    try:
                        client = create_client(exchange_config, market_type=market_type)
                    except Exception as e:
                        msg = str(e)
                        if is_fatal_exchange_error(msg):
                            logger.error(
                                "[PositionSync] Strategy %s fatal client error; auto-stopping. error=%s",
                                sid,
                                msg,
                            )
                            auto_stop_live_strategy(int(sid), msg, source="position_sync_client")
                        else:
                            logger.debug(
                                f"[PositionSync] Strategy {sid} skipped: failed to create client (exchange_id={exchange_id}): {e}"
                            )
                        continue

                    if isinstance(client, BinanceFuturesClient) and market_type == "swap":
                        try:
                            all_pos = client.get_positions() or []
                        except Exception as e:
                            msg = str(e)
                            if is_fatal_exchange_error(msg):
                                logger.error(f"[PositionSync] Strategy {sid} fatal auth error; auto-stopping. error={msg}")
                                auto_stop_live_strategy(int(sid), msg, source="position_sync_binance")
                                continue
                            if _is_exchange_rate_limit_error(msg):
                                _set_exchange_sync_backoff(cache_key)
                                logger.error(
                                    "[PositionSync] Binance rate limit for key=%s; backing off %ss. error=%s",
                                    cache_key,
                                    int(_exchange_sync_backoff_sec()),
                                    msg,
                                )
                                continue
                            logger.error(f"[PositionSync] Strategy {sid} get_positions failed: {msg}", exc_info=True)
                            continue
                        if isinstance(all_pos, dict) and "raw" in all_pos:
                            all_pos = all_pos["raw"]

                        if isinstance(all_pos, list):
                            for p in all_pos:
                                sym = str(p.get("symbol") or "").strip().upper()
                                try:
                                    amt = float(p.get("positionAmt") or 0.0)
                                    ep = float(p.get("entryPrice") or 0.0)
                                except Exception:
                                    amt = 0.0
                                    ep = 0.0
                                if not sym or abs(amt) <= 0:
                                    continue
                                hb_sym = sym
                                if hb_sym.endswith("USDT") and len(hb_sym) > 4 and "/" not in hb_sym:
                                    hb_sym = f"{hb_sym[:-4]}/USDT"
                                side = "long" if amt > 0 else "short"
                                exch_size.setdefault(hb_sym, {"long": 0.0, "short": 0.0})[side] = abs(float(amt))
                                exch_entry_price.setdefault(hb_sym, {"long": 0.0, "short": 0.0})[side] = abs(float(ep))


                    elif isinstance(client, OkxClient) and market_type == "swap":
                        try:
                            resp = client.get_positions()
                        except Exception as e:
                            # Fatal auth/config errors should auto-stop the strategy to avoid endless spam.
                            # Typical OKX response: HTTP 401 {"msg":"Invalid OK-ACCESS-KEY","code":"50111"}
                            msg = str(e)
                            m = msg.lower()
                            if is_fatal_exchange_error(msg):
                                logger.error(f"[PositionSync] Strategy {sid} fatal auth error; auto-stopping. error={msg}")
                                auto_stop_live_strategy(int(sid), msg, source="position_sync_okx")
                                continue
                            # Non-fatal: keep syncing other strategies, but don't crash the worker loop.
                            logger.error(f"[PositionSync] Strategy {sid} get_positions failed: {msg}", exc_info=True)
                            continue
                        data = (resp.get("data") or []) if isinstance(resp, dict) else []
                        if isinstance(data, list):
                            for p in data:
                                inst_id = str(p.get("instId") or "")
                                pos_side = str(p.get("posSide") or "").lower()
                                try:
                                    pos = float(p.get("pos") or 0.0)
                                except Exception:
                                    pos = 0.0
                                if not inst_id or abs(pos) <= 0:
                                    continue
                                # instId: BTC-USDT-SWAP -> BTC/USDT
                                hb_sym = inst_id.replace("-SWAP", "").replace("-", "/")
                                side = "long" if pos_side == "long" else ("short" if pos_side == "short" else ("long" if pos > 0 else "short"))
                                # IMPORTANT: OKX swap positions `pos` is in contracts, but our system uses base-asset quantity.
                                # Convert contracts -> base using ctVal when available.
                                qty_base = abs(float(pos))
                                try:
                                    inst = client.get_instrument(inst_type="SWAP", inst_id=inst_id) or {}
                                    ct_val = float(inst.get("ctVal") or 0.0)
                                    if ct_val > 0:
                                        qty_base = qty_base * ct_val
                                except Exception:
                                    pass
                                exch_size.setdefault(hb_sym, {"long": 0.0, "short": 0.0})[side] = float(qty_base)
                            
                                # Extract entry price from OKX position data
                                # OKX API returns avgPx (average price) or avgPxEp (average price in equity) for positions
                                try:
                                    # Try avgPx first (average entry price)
                                    avg_px = p.get("avgPx")
                                    if avg_px:
                                        entry_price = float(avg_px)
                                    else:
                                        # Fallback to avgPxEp (average price in equity)
                                        avg_px_ep = p.get("avgPxEp")
                                        if avg_px_ep:
                                            entry_price = float(avg_px_ep)
                                        else:
                                            # Fallback to last price if available
                                            last_px = p.get("last")
                                            entry_price = float(last_px) if last_px else 0.0
                                
                                    if entry_price > 0:
                                        exch_entry_price.setdefault(hb_sym, {"long": 0.0, "short": 0.0})[side] = entry_price
                                        logger.debug(f"[PositionSync] OKX {hb_sym} {side}: entry_price={entry_price} from avgPx={p.get('avgPx')} or avgPxEp={p.get('avgPxEp')}")
                                    else:
                                        logger.warning(f"[PositionSync] OKX {hb_sym} {side}: Could not extract entry price from position data: {p}")
                                except Exception as e:
                                    logger.warning(f"[PositionSync] Failed to extract entry price for OKX {hb_sym} {side}: {e}")
                                    # Don't set entry_price, will remain 0.0

                    elif isinstance(client, BitgetMixClient) and market_type == "swap":
                        product_type = str(exchange_config.get("product_type") or exchange_config.get("productType") or "USDT-FUTURES")
                        resp = client.get_positions(product_type=product_type)
                        data = resp.get("data") if isinstance(resp, dict) else None
                        if isinstance(data, list):
                            for p in data:
                                sym = str(p.get("symbol") or "")
                                hold_side = str(p.get("holdSide") or "").lower()
                                try:
                                    total = float(p.get("total") or 0.0)
                                except Exception:
                                    total = 0.0
                                if not sym or abs(total) <= 0:
                                    continue
                                hb_sym = sym.upper()
                                if hb_sym.endswith("USDT") and len(hb_sym) > 4 and "/" not in hb_sym:
                                    hb_sym = f"{hb_sym[:-4]}/USDT"
                                side = "long" if hold_side == "long" else "short"
                                exch_size.setdefault(hb_sym, {"long": 0.0, "short": 0.0})[side] = abs(float(total))
                                try:
                                    ep = float(p.get("openPriceAvg") or p.get("averageOpenPrice") or 0.0)
                                    if ep > 0:
                                        exch_entry_price.setdefault(hb_sym, {"long": 0.0, "short": 0.0})[side] = ep
                                except Exception:
                                    pass

                    elif isinstance(client, BybitClient) and market_type == "swap":
                        # Bybit v5 requires symbol or settleCoin — use USDT for full linear book
                        resp = client.get_positions(settle_coin="USDT")
                        lst = (((resp.get("result") or {}).get("list")) if isinstance(resp, dict) else None) or []
                        if isinstance(lst, list):
                            for p in lst:
                                if not isinstance(p, dict):
                                    continue
                                sym = str(p.get("symbol") or "").strip().upper()
                                side0 = str(p.get("side") or "").strip().lower()  # Buy/Sell
                                try:
                                    sz = float(p.get("size") or 0.0)
                                except Exception:
                                    sz = 0.0
                                if not sym or abs(sz) <= 0:
                                    continue
                                hb_sym = sym
                                if hb_sym.endswith("USDT") and len(hb_sym) > 4 and "/" not in hb_sym:
                                    hb_sym = f"{hb_sym[:-4]}/USDT"
                                side = "long" if side0 == "buy" else ("short" if side0 == "sell" else ("long" if sz > 0 else "short"))
                                exch_size.setdefault(hb_sym, {"long": 0.0, "short": 0.0})[side] = abs(float(sz))
                                try:
                                    ep = float(p.get("avgPrice") or p.get("entryPrice") or 0.0)
                                    if ep > 0:
                                        exch_entry_price.setdefault(hb_sym, {"long": 0.0, "short": 0.0})[side] = ep
                                except Exception:
                                    pass

                    elif isinstance(client, GateUsdtFuturesClient) and market_type == "swap":
                        resp = client.get_positions()
                        items = resp if isinstance(resp, list) else []
                        if isinstance(items, list):
                            for p in items:
                                if not isinstance(p, dict):
                                    continue
                                contract = str(p.get("contract") or "").strip()
                                try:
                                    sz_ct = float(p.get("size") or 0.0)  # contracts, signed
                                except Exception:
                                    sz_ct = 0.0
                                if not contract or abs(sz_ct) <= 0:
                                    continue
                                hb_sym = contract.replace("_", "/")
                                side = "long" if sz_ct > 0 else "short"
                                # Convert contracts -> base using quanto_multiplier.
                                qty_base = abs(sz_ct)
                                try:
                                    meta = client.get_contract(contract=contract) or {}
                                    qm = float(meta.get("quanto_multiplier") or meta.get("contract_size") or 0.0)
                                    if qm > 0:
                                        qty_base = qty_base * qm
                                except Exception:
                                    pass
                                exch_size.setdefault(hb_sym, {"long": 0.0, "short": 0.0})[side] = float(qty_base)
                                try:
                                    ep = float(p.get("entry_price") or p.get("open_price") or 0.0)
                                    if ep > 0:
                                        exch_entry_price.setdefault(hb_sym, {"long": 0.0, "short": 0.0})[side] = ep
                                except Exception:
                                    pass

                    elif isinstance(client, KucoinFuturesClient) and market_type == "swap":
                        resp = client.get_positions()
                        data = (resp.get("data") if isinstance(resp, dict) else None) or []
                        if isinstance(data, list):
                            for p in data:
                                if not isinstance(p, dict):
                                    continue
                                sym = str(p.get("symbol") or "").strip()
                                try:
                                    qty_ct = float(p.get("currentQty") or p.get("quantity") or 0.0)
                                except Exception:
                                    qty_ct = 0.0
                                if not sym or abs(qty_ct) <= 0:
                                    continue
                                side = "long" if qty_ct > 0 else "short"
                                # Convert contracts -> base using multiplier.
                                qty_base = abs(qty_ct)
                                try:
                                    meta = client.get_contract(symbol=sym) or {}
                                    mult = float(meta.get("multiplier") or meta.get("lotSize") or 0.0)
                                    if mult > 0:
                                        qty_base = qty_base * mult
                                except Exception:
                                    pass
                                exch_size.setdefault(sym, {"long": 0.0, "short": 0.0})[side] = float(qty_base)
                                try:
                                    ep = float(p.get("avgEntryPrice") or p.get("realLeverage") and float(p.get("posCost") or 0) / max(abs(qty_ct), 1e-12) or 0.0)
                                    if ep > 0:
                                        exch_entry_price.setdefault(sym, {"long": 0.0, "short": 0.0})[side] = ep
                                except Exception:
                                    pass

                    elif isinstance(client, KrakenFuturesClient) and market_type == "swap":
                        resp = client.get_open_positions()
                        positions = (resp.get("openPositions") if isinstance(resp, dict) else None) or (resp.get("open_positions") if isinstance(resp, dict) else None) or []
                        if isinstance(positions, list):
                            for p in positions:
                                if not isinstance(p, dict):
                                    continue
                                sym = str(p.get("symbol") or p.get("instrument") or "").strip()
                                try:
                                    sz = float(p.get("size") or p.get("positionSize") or 0.0)
                                except Exception:
                                    sz = 0.0
                                if not sym or abs(sz) <= 0:
                                    continue
                                side = "long" if sz > 0 else "short"
                                exch_size.setdefault(sym, {"long": 0.0, "short": 0.0})[side] = abs(float(sz))
                                try:
                                    ep = float(p.get("price") or p.get("avgPrice") or 0.0)
                                    if ep > 0:
                                        exch_entry_price.setdefault(sym, {"long": 0.0, "short": 0.0})[side] = ep
                                except Exception:
                                    pass

                    elif MT5Client is not None and isinstance(client, MT5Client):
                        # MT5 forex positions
                        positions = client.get_positions()
                        if isinstance(positions, list):
                            for p in positions:
                                if not isinstance(p, dict):
                                    continue
                                sym = str(p.get("symbol") or "").strip()
                                pos_type = str(p.get("type") or "").strip().lower()
                                try:
                                    vol = float(p.get("volume") or 0.0)
                                except Exception:
                                    vol = 0.0
                                if not sym or vol <= 0:
                                    continue
                                # MT5: type "buy" = long, "sell" = short
                                side = "long" if pos_type == "buy" else "short"
                                exch_size.setdefault(sym, {"long": 0.0, "short": 0.0})[side] = float(vol)
                        # Continue to reconciliation logic below

                    elif IBKRClient is not None and isinstance(client, IBKRClient):
                        # IBKR US-stock positions. `quantity` is signed: >0 = long, <0 = short.
                        # We currently only enforce long-only entries (see _execute_ibkr_order),
                        # but still mirror short rows so reconciliation does not orphan them
                        # if the user had pre-existing inventory in TWS.
                        try:
                            positions = client.get_positions() or []
                        except Exception as e:
                            msg = str(e)
                            if is_fatal_exchange_error(msg):
                                logger.error(
                                    "[PositionSync] Strategy %s IBKR fatal error; auto-stopping. error=%s",
                                    sid,
                                    msg,
                                )
                                auto_stop_live_strategy(int(sid), msg, source="position_sync_ibkr")
                            else:
                                logger.error(f"[PositionSync] Strategy {sid} IBKR get_positions failed: {e}", exc_info=True)
                            continue
                        if isinstance(positions, list):
                            for p in positions:
                                if not isinstance(p, dict):
                                    continue
                                sym = str(p.get("symbol") or p.get("ib_symbol") or "").strip()
                                try:
                                    qty = float(p.get("quantity") or 0.0)
                                except Exception:
                                    qty = 0.0
                                try:
                                    avg = float(p.get("avgCost") or 0.0)
                                except Exception:
                                    avg = 0.0
                                if not sym or abs(qty) <= 0:
                                    continue
                                side = "long" if qty > 0 else "short"
                                exch_size.setdefault(sym, {"long": 0.0, "short": 0.0})[side] = abs(qty)
                                if avg > 0:
                                    exch_entry_price.setdefault(sym, {"long": 0.0, "short": 0.0})[side] = avg
                        # Continue to reconciliation logic below

                    elif AlpacaClient is not None and isinstance(client, AlpacaClient):
                        # Alpaca positions cover both US stocks and crypto. The client
                        # already returns a normalized `side` string ("long" / "short")
                        # plus `quantity` and `avgCost`. Crypto symbols come through as
                        # "BTC/USD" — same format the strategy stores, so no extra
                        # normalization is needed here.
                        try:
                            positions = client.get_positions() or []
                        except Exception as e:
                            logger.error(f"[PositionSync] Strategy {sid} Alpaca get_positions failed: {e}", exc_info=True)
                            continue
                        if isinstance(positions, list):
                            for p in positions:
                                if not isinstance(p, dict):
                                    continue
                                sym = str(p.get("symbol") or "").strip()
                                try:
                                    qty = float(p.get("quantity") or 0.0)
                                except Exception:
                                    qty = 0.0
                                try:
                                    avg = float(p.get("avgCost") or 0.0)
                                except Exception:
                                    avg = 0.0
                                if not sym or abs(qty) <= 0:
                                    continue
                                side_str = str(p.get("side") or "").strip().lower()
                                if side_str not in ("long", "short"):
                                    side_str = "long" if qty > 0 else "short"
                                exch_size.setdefault(sym, {"long": 0.0, "short": 0.0})[side_str] = abs(qty)
                                if avg > 0:
                                    exch_entry_price.setdefault(sym, {"long": 0.0, "short": 0.0})[side_str] = avg
                        # Continue to reconciliation logic below

                    else:
                        # Spot reconciliation is optional; skip for now (keeps self-check low-risk).
                        logger.debug(f"position sync: skip unsupported market/client: sid={sid}, cfg={safe_cfg}, market_type={market_type}, client={type(client)}")
                        continue

                    _set_position_sync_snapshot(cache_key, exch_size, exch_entry_price)

                # [DEBUG] Log all normalized exchange keys for inspection
                logger.debug(f"[PositionSync] Strategy {sid} Exchange Keys: {list(exch_size.keys())}")

                # [Log Optimization] Log current positions each sync cycle (see POSITION_SYNC_INTERVAL_SEC)
                pos_summary_parts = []
                for _sym, _sides in exch_size.items():
                    for _side_key, _qty in _sides.items():
                        if _qty > 0:
                            _ep = exch_entry_price.get(_sym, {}).get(_side_key, 0.0)
                            pos_summary_parts.append(f"{_sym} {_side_key} size={_qty} entry={_ep}")

                if pos_summary_parts:
                    logger.info(f"[PositionSync] Strategy {sid} ({safe_cfg.get('exchange_id', 'unknown')}) positions: {'; '.join(pos_summary_parts)}")
                else:
                    logger.info(f"[PositionSync] Strategy {sid} ({safe_cfg.get('exchange_id', 'unknown')}) has NO positions on exchange.")

                # 3) Apply reconciliation to local rows.
                to_delete_ids: List[int] = []
                to_update: List[Dict[str, Any]] = []
                eps = 1e-12

                for r in plist:
                    rid = int(r.get("id") or 0)
                    sym = str(r.get("symbol") or "").strip()
                    side = str(r.get("side") or "").strip().lower()
                    if not rid or not sym or side not in ("long", "short"):
                        continue
                    try:
                        local_size = float(r.get("size") or 0.0)
                    except Exception:
                        local_size = 0.0

                    exch = exch_size.get(sym) or {}
                    exch_qty = float(exch.get(side) or 0.0)

                    # Lookup entry price
                    exch_ep_map = exch_entry_price.get(sym) or {}
                    exch_price = float(exch_ep_map.get(side) or 0.0)

                    try:
                        local_price = float(r.get("entry_price") or 0.0)
                    except Exception:
                        local_price = 0.0
                    logger.debug(f"[PositionSync] Check ID={rid} {sym} {side}: local_sz={local_size} px={local_price}, exch_sz={exch_qty} px={exch_price}")

                    if exch_qty <= eps:
                        # Exchange is flat -> delete local position (self-heal).
                        to_delete_ids.append(rid)
                    else:
                        # Update local size if it diverged materially (best-effort), OR if entry_price changed significantly (>0.5% diff)
                        # or if local_price is 0 (first sync)
                        price_diff_ratio = 0.0
                        if local_price > 0:
                            price_diff_ratio = abs(exch_price - local_price) / local_price
                        else:
                            price_diff_ratio = 1.0 if exch_price > 0 else 0.0

                        if (local_size <= 0 or abs(exch_qty - local_size) / max(1.0, local_size) > 0.01) or (price_diff_ratio > 0.005):
                            logger.info(f"[PositionSync] -> Flagged for UPDATE: {sym} (local_sz={local_size}->{exch_qty}, px={local_price}->{exch_price})")
                            to_update.append({"id": rid, "size": exch_qty, "entry_price": exch_price})

                # [New Feature] Detect positions that exist on exchange but not in local DB, and insert them.
                # IMPORTANT: Only insert positions for symbols that this strategy actually trades
                # This prevents syncing positions from quick trade or other sources
                to_insert: List[Dict[str, Any]] = []
                local_symbols_sides = {(str(r.get("symbol") or "").strip(), str(r.get("side") or "").strip().lower()) for r in plist}
                
                for _sym, _sides_map in exch_size.items():
                    # Filter: only sync positions for symbols that this strategy trades
                    # If strategy has no symbol configured, skip auto-insert to prevent syncing quick trade positions
                    _sym_upper = _sym.strip().upper()
                    if allowed_symbols and _sym_upper not in allowed_symbols:
                        logger.debug(f"[PositionSync] Skipping {_sym}: not in strategy's symbol list (strategy trades: {allowed_symbols})")
                        continue
                    elif not allowed_symbols:
                        # Strategy has no symbol configured - skip to prevent syncing unrelated positions
                        logger.debug(f"[PositionSync] Skipping {_sym}: strategy has no symbol configured (preventing quick trade position sync)")
                        continue
                    
                    for _side, _qty in _sides_map.items():
                        if _qty > 1e-12 and (_sym, _side) not in local_symbols_sides:
                            # Exchange has this position but local DB does not
                            _ep = exch_entry_price.get(_sym, {}).get(_side, 0.0)
                            to_insert.append({
                                "strategy_id": sid,
                                "symbol": _sym,
                                "side": _side,
                                "size": _qty,
                                "entry_price": _ep
                            })
                            logger.info(f"[PositionSync] -> Flagged for INSERT: {_sym} {_side} size={_qty} entry={_ep}")

                if not to_delete_ids and not to_update and not to_insert:
                    continue

                with get_db_connection() as db:
                    cur = db.cursor()
                    for rid in to_delete_ids:
                        cur.execute("DELETE FROM qd_strategy_positions WHERE id = %s", (int(rid),))
                    for u in to_update:
                        cur.execute(
                            "UPDATE qd_strategy_positions SET size = %s, entry_price = %s, updated_at = NOW() WHERE id = %s", 
                            (float(u["size"]), float(u["entry_price"]), int(u["id"]))
                        )
                    for ins in to_insert:
                        # Get user_id from strategy
                        ins_user_id = 1
                        try:
                            cur.execute("SELECT user_id FROM qd_strategies_trading WHERE id = %s", (int(ins["strategy_id"]),))
                            strategy_row = cur.fetchone()
                            if strategy_row and strategy_row.get("user_id"):
                                ins_user_id = int(strategy_row["user_id"])
                        except Exception:
                            pass
                        cur.execute(
                            """INSERT INTO qd_strategy_positions (user_id, strategy_id, symbol, side, size, entry_price, updated_at)
                               VALUES (%s, %s, %s, %s, %s, %s, NOW())""",
                            (ins_user_id, int(ins["strategy_id"]), str(ins["symbol"]), str(ins["side"]), float(ins["size"]), float(ins["entry_price"]))
                        )
                    db.commit()
                    cur.close()

                if to_delete_ids:
                    logger.debug(f"position sync: removed {len(to_delete_ids)} ghost positions for strategy_id={sid}")
                if to_update:
                    logger.debug(f"position sync: updated {len(to_update)} positions for strategy_id={sid}")
                if to_insert:
                    logger.debug(f"position sync: inserted {len(to_insert)} new positions for strategy_id={sid}")
            except Exception as e:
                msg = str(e)
                if is_fatal_exchange_error(msg):
                    logger.error(f"[PositionSync] Strategy {sid} fatal error; auto-stopping. error={msg}", exc_info=True)
                    auto_stop_live_strategy(int(sid), msg, source="position_sync")
                else:
                    logger.error(f"position sync: strategy_id={sid} failed: {e}", exc_info=True)

    def _fetch_pending_orders(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            # Best-effort: requeue stale "processing" rows to avoid deadlocks after crashes.
            try:
                stale_sec = int(self._stale_processing_sec or 0)
            except Exception:
                stale_sec = 0
            if stale_sec > 0:
                with get_db_connection() as db:
                    cur = db.cursor()
                    cur.execute(
                        """
                        UPDATE pending_orders
                        SET status = 'pending',
                            updated_at = NOW(),
                            dispatch_note = CASE
                                WHEN dispatch_note IS NULL OR dispatch_note = '' THEN 'requeued_stale_processing'
                                ELSE dispatch_note
                            END
                        WHERE status = 'processing'
                          AND (updated_at IS NULL OR updated_at < NOW() - INTERVAL '%s seconds')
                          AND (attempts < max_attempts)
                        """,
                        (stale_sec,),
                    )
                    db.commit()
                    cur.close()

            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT *
                    FROM pending_orders
                    WHERE status = 'pending'
                      AND (attempts < max_attempts)
                    ORDER BY priority DESC, id ASC
                    LIMIT %s
                    """,
                    (int(limit),),
                )
                rows = cur.fetchall() or []
                cur.close()
            return rows
        except Exception as e:
            logger.warning(f"fetch_pending_orders failed: {e}")
            return []

    def _mark_processing(self, order_id: int) -> bool:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                # Only claim if still pending to avoid double-processing.
                cur.execute(
                    """
                    UPDATE pending_orders
                    SET status = 'processing',
                        attempts = COALESCE(attempts, 0) + 1,
                        processed_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s AND status = 'pending'
                    """,
                    (int(order_id),),
                )
                claimed = getattr(cur, "rowcount", None)
                db.commit()
                cur.close()
            # Only treat as success if we actually changed a row.
            if claimed is None:
                return True
            return int(claimed) > 0
        except Exception as e:
            logger.warning(f"mark_processing failed: id={order_id}, err={e}")
            return False

    def _dispatch_one(self, order_row: Dict[str, Any]) -> None:
        order_id = int(order_row["id"])
        mode = (order_row.get("execution_mode") or "signal").strip().lower()
        payload_json = order_row.get("payload_json") or ""

        payload: Dict[str, Any] = {}
        if payload_json and isinstance(payload_json, str):
            try:
                payload = json.loads(payload_json) or {}
            except Exception:
                payload = {}

        signal_type = payload.get("signal_type") or order_row.get("signal_type")
        symbol = payload.get("symbol") or order_row.get("symbol")
        strategy_id = payload.get("strategy_id") or order_row.get("strategy_id")
        price = float(payload.get("price") or order_row.get("price") or 0.0)
        amount = float(payload.get("amount") or order_row.get("amount") or 0.0)
        direction = "short" if "short" in str(signal_type) else "long"
        notification_config = payload.get("notification_config") or {}
        strategy_name = str(payload.get("strategy_name") or "").strip()
        if not strategy_name:
            # Best-effort: load from DB for nicer notifications.
            strategy_name = self._load_strategy_name(int(strategy_id or 0)) if strategy_id else ""
        if not strategy_name:
            strategy_name = f"Strategy_{strategy_id}"

        # If the queued record is legacy ("signal") but the strategy is configured as live,
        # automatically upgrade it to live execution to keep the system moving.
        try:
            if mode != "live" and strategy_id:
                sc = load_strategy_configs(int(strategy_id))
                if (sc.get("execution_mode") or "").strip().lower() == "live":
                    mode = "live"
        except Exception:
            pass

        if mode == "signal":
            # Signal-only mode: dispatch notifications (no real trading).
            # Note: notification_config is stored in payload_json at enqueue time; fallback to DB if missing.
            if (not notification_config) and strategy_id:
                notification_config = self._load_notification_config(int(strategy_id))

            stake_quote = calc_notional_value(float(price or 0.0), float(amount or 0.0)) or float(amount or 0.0)
            results = self._notifier.notify_signal(
                strategy_id=int(strategy_id or 0),
                strategy_name=str(strategy_name or ""),
                symbol=str(symbol or ""),
                signal_type=str(signal_type or ""),
                price=float(price or 0.0),
                stake_amount=float(stake_quote),
                direction=str(direction or "long"),
                notification_config=notification_config if isinstance(notification_config, dict) else {},
                extra={"pending_order_id": order_id, "mode": mode},
            )

            attempted = list(results.keys())
            ok_channels = [c for c, r in results.items() if (r or {}).get("ok")]
            fail_channels = [c for c, r in results.items() if not (r or {}).get("ok")]

            if ok_channels:
                note = f"notified_ok={','.join(ok_channels)}"
                if fail_channels:
                    note += f";fail={','.join(fail_channels)}"
                self._mark_sent(order_id=order_id, note=note[:200])
                append_strategy_log(
                    int(strategy_id or 0), "signal",
                    f"Signal notification sent: {signal_type} {symbol} @ {price:.6f}, channels={','.join(ok_channels)}",
                )
            else:
                # Nothing succeeded -> mark failed with a compact error summary.
                first_err = ""
                for c in attempted:
                    err = (results.get(c) or {}).get("error") or ""
                    if err:
                        first_err = f"{c}:{err}"
                        break
                self._mark_failed(order_id=order_id, error=first_err or "notify_failed")
                append_strategy_log(
                    int(strategy_id or 0), "error",
                    f"Signal notification failed: {signal_type} {symbol}, error={first_err or 'notify_failed'}",
                )
            return

        if mode == "live":
            self._execute_live_order(order_id=order_id, order_row=order_row, payload=payload)
            return

        self._mark_failed(order_id=order_id, error=f"unsupported_execution_mode:{mode}")

    def _load_notification_config(self, strategy_id: int) -> Dict[str, Any]:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    "SELECT notification_config FROM qd_strategies_trading WHERE id = ?",
                    (int(strategy_id),),
                )
                row = cur.fetchone() or {}
                cur.close()
            s = row.get("notification_config") or ""
            if isinstance(s, dict):
                return s
            if isinstance(s, str) and s.strip():
                try:
                    obj = json.loads(s)
                    return obj if isinstance(obj, dict) else {}
                except Exception:
                    return {}
            return {}
        except Exception:
            return {}

    def _load_strategy_name(self, strategy_id: int) -> str:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("SELECT strategy_name FROM qd_strategies_trading WHERE id = ?", (int(strategy_id),))
                row = cur.fetchone() or {}
                cur.close()
            return str(row.get("strategy_name") or "").strip()
        except Exception:
            return ""

    def _execute_live_order(self, *, order_id: int, order_row: Dict[str, Any], payload: Dict[str, Any]) -> None:
        """
        Execute a pending order using direct exchange REST clients (no ccxt).
        """
        strategy_id = int(payload.get("strategy_id") or order_row.get("strategy_id") or 0)
        if strategy_id <= 0:
            self._mark_failed(order_id=order_id, error="missing_strategy_id")
            return

        def _notify_live_best_effort(
            *,
            status: str,
            error: str = "",
            exchange_id: str = "",
            exchange_order_id: str = "",
            price_hint: Optional[float] = None,
            amount_hint: Optional[float] = None,
        ) -> None:
            """
            Best-effort notifications for live execution.

            Historically this worker only notified in execution_mode='signal'. For real trading ('live'),
            users still want Telegram/browser alerts. This hook never blocks or changes order status.
            """
            try:
                notification_config = payload.get("notification_config") or {}
                if (not notification_config) and strategy_id:
                    notification_config = self._load_notification_config(int(strategy_id))
                if not notification_config:
                    return

                strategy_name = str(payload.get("strategy_name") or "").strip()
                if not strategy_name:
                    strategy_name = self._load_strategy_name(int(strategy_id)) or f"Strategy_{strategy_id}"

                sym0 = payload.get("symbol") or order_row.get("symbol") or ""
                sig0 = payload.get("signal_type") or order_row.get("signal_type") or ""
                ref0 = float(payload.get("ref_price") or payload.get("price") or order_row.get("price") or 0.0)
                amt0 = float(payload.get("amount") or order_row.get("amount") or 0.0)

                px = float(price_hint) if (price_hint is not None and float(price_hint or 0.0) > 0) else ref0
                amt = float(amount_hint) if (amount_hint is not None and float(amount_hint or 0.0) > 0) else amt0

                stake_quote = calc_notional_value(float(px or 0.0), float(amt or 0.0)) or float(amt or 0.0)
                results = self._notifier.notify_signal(
                    strategy_id=int(strategy_id),
                    strategy_name=str(strategy_name or ""),
                    symbol=str(sym0 or ""),
                    signal_type=str(sig0 or ""),
                    price=float(px or 0.0),
                    stake_amount=float(stake_quote),
                    direction=("short" if "short" in str(sig0 or "").lower() else "long"),
                    notification_config=notification_config if isinstance(notification_config, dict) else {},
                    extra={
                        "pending_order_id": int(order_id),
                        "mode": "live",
                        "status": str(status or ""),
                        "error": str(error or ""),
                        "exchange_id": str(exchange_id or ""),
                        "exchange_order_id": str(exchange_order_id or ""),
                    },
                )
                ok_channels = [c for c, r in (results or {}).items() if (r or {}).get("ok")]
                fail_channels = [c for c, r in (results or {}).items() if not (r or {}).get("ok")]
                if ok_channels or fail_channels:
                    logger.info(
                        f"live notify: pending_id={order_id}, strategy_id={strategy_id}, "
                        f"ok={','.join(ok_channels) if ok_channels else '-'} "
                        f"fail={','.join(fail_channels) if fail_channels else '-'}"
                    )
            except Exception as e:
                logger.info(f"live notify skipped/failed: pending_id={order_id}, strategy_id={strategy_id}, err={e}")

        def _console_print(msg: str) -> None:
            try:
                print(str(msg or ""), flush=True)
            except Exception:
                pass

        signal_type = payload.get("signal_type") or order_row.get("signal_type")
        symbol = payload.get("symbol") or order_row.get("symbol")
        amount = float(payload.get("amount") or order_row.get("amount") or 0.0)
        if not symbol or not signal_type:
            self._mark_failed(order_id=order_id, error="missing_symbol_or_signal_type")
            _console_print(f"[worker] order rejected: strategy_id={strategy_id} pending_id={order_id} missing symbol/signal_type")
            _notify_live_best_effort(status="failed", error="missing_symbol_or_signal_type")
            append_strategy_log(strategy_id, "error", f"Order rejected: missing symbol or signal_type")
            return

        cfg = load_strategy_configs(strategy_id)
        strategy_user_id = int(cfg.get("user_id") or 1)
        exchange_config = resolve_exchange_config(cfg.get("exchange_config") or {}, user_id=strategy_user_id)
        safe_cfg = safe_exchange_config_for_log(exchange_config)
        exchange_id = str(exchange_config.get("exchange_id") or "").strip().lower()
        market_category = str(cfg.get("market_category") or "Crypto").strip()

        # Pre-resolve market_type / trade_direction / bot_type so we can hand
        # them to the centralized policy validator. Reading these here also
        # lets the validator catch e.g. "alpaca + crypto + market_type=swap"
        # instead of waiting for the broker to reject the order.
        _pre_market_type = (
            payload.get("market_type")
            or order_row.get("market_type")
            or cfg.get("market_type")
            or exchange_config.get("market_type")
            or "swap"
        )
        _pre_trading_cfg = cfg.get("trading_config") or {}
        _pre_trade_direction = _pre_trading_cfg.get("trade_direction")
        _pre_bot_type = _pre_trading_cfg.get("bot_type")

        # Centralized validation: broker x market x market_type x direction x
        # bot_type. Same call site that strategy CRUD uses, so an order can
        # only fail here if the strategy was somehow created before the
        # policy was tightened, or if the cfg drifted out of sync.
        from app.services.broker_market_policy import validate_strategy_config
        try:
            validate_strategy_config(
                exchange_id=exchange_id,
                market_category=market_category,
                market_type=_pre_market_type,
                trade_direction=_pre_trade_direction,
                bot_type=_pre_bot_type,
                require_exchange=True,
            )
        except ValueError as e:
            err = f"policy_violation:{e}"
            self._mark_failed(order_id=order_id, error=err)
            _console_print(f"[worker] order rejected by policy: strategy_id={strategy_id} pending_id={order_id} err={e}")
            _notify_live_best_effort(status="failed", error=err)
            append_strategy_log(strategy_id, "error", f"Order rejected: {e}")
            return

        market_type = (payload.get("market_type") or order_row.get("market_type") or cfg.get("market_type") or exchange_config.get("market_type") or "swap")
        market_type = str(market_type or "swap").strip().lower()
        if market_type in ("futures", "future", "perp", "perpetual"):
            market_type = "swap"

        client = None
        try:
            client = create_client(exchange_config, market_type=market_type)
        except Exception as e:
            self._mark_failed(order_id=order_id, error=f"create_client_failed:{e}")
            _console_print(f"[worker] create_client_failed: strategy_id={strategy_id} pending_id={order_id} err={e}")
            _notify_live_best_effort(status="failed", error=f"create_client_failed:{e}")
            append_strategy_log(strategy_id, "error", f"Exchange client creation failed ({exchange_id}): {e}")
            return

        # Check if this is an IBKR client (US stocks)
        global IBKRClient
        if IBKRClient is None:
            try:
                from app.services.ibkr_trading import IBKRClient as _IBKRClient
                IBKRClient = _IBKRClient
            except ImportError:
                pass

        if IBKRClient is not None and isinstance(client, IBKRClient):
            # Execute IBKR order (separate flow for stocks)
            self._execute_ibkr_order(
                order_id=order_id,
                order_row=order_row,
                payload=payload,
                client=client,
                strategy_id=strategy_id,
                exchange_config=exchange_config,
                _notify_live_best_effort=_notify_live_best_effort,
                _console_print=_console_print,
            )
            return

        # Check if this is an MT5 client (Forex)
        global MT5Client
        if MT5Client is None:
            try:
                from app.services.mt5_trading import MT5Client as _MT5Client
                MT5Client = _MT5Client
            except ImportError:
                pass

        if MT5Client is not None and isinstance(client, MT5Client):
            # Execute MT5 order (separate flow for forex)
            self._execute_mt5_order(
                order_id=order_id,
                order_row=order_row,
                payload=payload,
                client=client,
                strategy_id=strategy_id,
                exchange_config=exchange_config,
                _notify_live_best_effort=_notify_live_best_effort,
                _console_print=_console_print,
            )
            return

        # Check if this is an Alpaca client (US stocks + crypto via REST)
        global AlpacaClient
        if AlpacaClient is None:
            try:
                from app.services.alpaca_trading import AlpacaClient as _AlpacaClient
                AlpacaClient = _AlpacaClient
            except ImportError:
                pass

        if AlpacaClient is not None and isinstance(client, AlpacaClient):
            self._execute_alpaca_order(
                order_id=order_id,
                order_row=order_row,
                payload=payload,
                client=client,
                strategy_id=strategy_id,
                exchange_config=exchange_config,
                market_category=market_category,
                _notify_live_best_effort=_notify_live_best_effort,
                _console_print=_console_print,
            )
            return

        def _make_client_oid(phase: str = "") -> str:
            """
            Build a client order id.

            OKX has strict clOrdId rules (length <= 32, alphanumeric only in practice).
            We generate a compact, deterministic id per (strategy_id, pending_order_id, phase).
            """
            ph = str(phase or "").strip().lower()
            # Keep ids stable and short.
            if exchange_id == "okx":
                base = f"qd{int(strategy_id)}{int(order_id)}{ph}"
                # Keep only alphanumeric.
                base = "".join([c for c in base if c.isalnum()])
                if not base:
                    base = f"qd{int(strategy_id)}{int(order_id)}"
                # OKX max length is 32.
                return base[:32]
            # Other exchanges are more permissive.
            return f"qd_{int(strategy_id)}_{int(order_id)}{('_' + ph) if ph else ''}"

        client_oid = _make_client_oid("")
        sig = str(signal_type or "").strip().lower()
        # Spot does not support short signals in this system.
        if market_type == "spot" and "short" in sig:
            self._mark_failed(order_id=order_id, error="spot_market_does_not_support_short_signals")
            _console_print(f"[worker] order rejected: strategy_id={strategy_id} pending_id={order_id} spot short not supported")
            _notify_live_best_effort(status="failed", error="spot_market_does_not_support_short_signals")
            append_strategy_log(strategy_id, "error", f"Order rejected: spot market does not support short signals ({symbol} {signal_type})")
            return

        # Unified maker->market fallback settings
        # Priority: payload config > environment variable > default value
        _default_order_mode = os.getenv("ORDER_MODE", "market").strip().lower()
        _default_maker_wait_sec = float(os.getenv("MAKER_WAIT_SEC", "10"))
        _default_maker_offset_bps = float(os.getenv("MAKER_OFFSET_BPS", "2"))

        order_mode = str(payload.get("order_mode") or payload.get("orderMode") or _default_order_mode).strip().lower()
        maker_wait_sec = float(payload.get("maker_wait_sec") or payload.get("makerWaitSec") or _default_maker_wait_sec)
        maker_offset_bps = float(payload.get("maker_offset_bps") or payload.get("makerOffsetBps") or _default_maker_offset_bps)
        if maker_wait_sec <= 0:
            maker_wait_sec = _default_maker_wait_sec if _default_maker_wait_sec > 0 else 10.0
        if maker_offset_bps < 0:
            maker_offset_bps = 0.0
        maker_offset = maker_offset_bps / 10000.0

        ref_price = float(payload.get("ref_price") or payload.get("price") or order_row.get("price") or 0.0)

        # Helper: map signal -> side/posSide/reduceOnly
        # Include stop/tp/trailing exit labels (same exchange side as plain close_*).
        def _signal_to_side_pos_reduce(sig_type: str):
            st = (sig_type or "").strip().lower()
            if st in ("open_long", "add_long"):
                return "buy", "long", False
            if st in ("open_short", "add_short"):
                return "sell", "short", False
            if st in ("close_long", "reduce_long", "close_long_stop", "close_long_profit", "close_long_trailing"):
                return "sell", "long", True
            if st in ("close_short", "reduce_short", "close_short_stop", "close_short_profit", "close_short_trailing"):
                return "buy", "short", True
            raise LiveTradingError(f"Unsupported signal_type: {sig_type}")

        side, pos_side, reduce_only = _signal_to_side_pos_reduce(signal_type)

        # Leverage handling (best-effort):
        # - For OKX swap, leverage must be set via private endpoint; otherwise exchange defaults apply.
        # - For other exchanges, leverage setting is not implemented yet in this local client.
        leverage = payload.get("leverage")
        if leverage is None:
            leverage = cfg.get("leverage")
        try:
            leverage = float(leverage or 1.0)
        except Exception:
            leverage = 1.0
        if leverage <= 0:
            leverage = 1.0

        # [FEATURE] Sync positions before execution to ensure size is checking against reality
        # The user requested to sync before EVERY live order to prevent mismatch.
        try:
            logger.info(f"[Sync] Triggering pre-execution sync for strategy {strategy_id} before order {order_id}")
            self._sync_positions_best_effort(target_strategy_id=strategy_id)
        except Exception as e:
            logger.warning(f"Pre-execution sync failed: {e}")

        # [FEATURE] Auto-correct amount for Close/Reduce signals if we hold less than requested
        if reduce_only:
            try:
                with get_db_connection() as db:
                    cur = db.cursor()
                    # We need to find the specific position. 
                    # Symbol stored in DB is normalized (e.g. BTC/USDT). 
                    # The payload 'symbol' might be 'BTCUSDT' or 'BTC/USDT'.
                    # We try to match what stored in DB.
                    # Best effort: try exact match, then normalized.
                    qry_sym = str(symbol or "").strip().upper()
                    # Mapping logic similar to _sync:
                    if qry_sym.endswith("USDT") and "/" not in qry_sym:
                        qry_sym = f"{qry_sym[:-4]}/USDT"
                    
                    cur.execute(
                        "SELECT size FROM qd_strategy_positions WHERE strategy_id = %s AND symbol = %s AND side = %s",
                        (strategy_id, qry_sym, pos_side)
                    )
                    row = cur.fetchone()
                    cur.close()
                    
                    if row:
                        held_size = float(row["size"] or 0.0)
                        if amount > held_size:
                            logger.warning(f"[RiskControl] Adjusting Close amount from {amount} to {held_size} (Held) for {symbol}")
                            amount = held_size
                    else:
                        # No position found in DB?
                        # If reduce_only, and no position, maybe it's 0.
                        logger.warning(f"[RiskControl] Close signal for {symbol} but NO position found in DB. Setting amount=0.")
                        amount = 0.0
            except Exception as e:
                 logger.error(f"[RiskControl] Failed to check DB position logic: {e}")


        # Collect raw exchange interactions / intermediate states for debugging & persistence.
        phases: Dict[str, Any] = {}

        # Ensure ref price exists (used by maker pricing, fallbacks, and local DB snapshots).
        if ref_price <= 0:
            try:
                if isinstance(client, BinanceFuturesClient):
                    ref_price = float(client.get_mark_price(symbol=str(symbol)) or 0.0)
            except Exception:
                pass

        # Binance Futures leverage is per-symbol on the exchange side.
        # If we do not set it, Binance may keep default 1x and the user will observe
        # margin ~= notional (i.e., "margin = invested * leverage" when we sized using leverage).
        if isinstance(client, BinanceFuturesClient) and market_type == "swap":
            try:
                client.set_leverage(symbol=str(symbol), leverage=float(leverage or 1.0))
                phases["set_leverage"] = {"exchange": "binance", "symbol": str(symbol), "leverage": float(leverage or 1.0)}
            except Exception as e:
                # Safer default: do NOT place orders with an unintended leverage.
                err = f"binance_set_leverage_failed:{e}"
                logger.warning(f"live leverage set failed: pending_id={order_id}, strategy_id={strategy_id}, cfg={safe_cfg}, err={e}")
                self._mark_failed(order_id=order_id, error=err)
                _console_print(f"[worker] order rejected: strategy_id={strategy_id} pending_id={order_id} {err}")
                _notify_live_best_effort(status="failed", error=err, amount_hint=amount, price_hint=ref_price)
                append_strategy_log(strategy_id, "error", f"Binance set leverage failed for {symbol}: {e}")
                return

        # Accumulate fills across phases
        total_base = 0.0
        total_quote = 0.0
        total_fee = 0.0
        fee_ccy = ""

        def _apply_fill(filled_qty: float, avg_px: float) -> None:
            nonlocal total_base, total_quote
            fq = float(filled_qty or 0.0)
            px = float(avg_px or 0.0)
            if fq > 0 and px > 0:
                total_base += fq
                total_quote += fq * px

        def _apply_fee(fee: float, ccy: str = "") -> None:
            nonlocal total_fee, fee_ccy
            try:
                fv = abs(float(fee or 0.0))
            except Exception:
                fv = 0.0
            if fv > 0:
                total_fee += fv
                if (not fee_ccy) and ccy:
                    fee_ccy = str(ccy or "")

        def _current_avg() -> float:
            return float(total_quote / total_base) if total_base > 0 else 0.0

        # For close/reduce signals, query actual exchange position to avoid insufficient balance due to fees
        # The exchange position may be smaller than our recorded amount due to trading fees
        if reduce_only and market_type == "swap":
            try:
                actual_pos_size = 0.0
                if isinstance(client, OkxClient):
                    inst_id = to_okx_swap_inst_id(str(symbol))
                    pos_resp = client.get_positions(inst_id=inst_id)
                    pos_data = (pos_resp.get("data") or []) if isinstance(pos_resp, dict) else []
                    for pos in pos_data:
                        if not isinstance(pos, dict):
                            continue
                        pos_inst = str(pos.get("instId") or "").strip()
                        pos_ps = str(pos.get("posSide") or "").strip().lower()
                        # Match instrument and position side
                        if pos_inst == inst_id and pos_ps == pos_side:
                            # OKX pos field is signed for net mode; use abs for simplicity
                            pos_qty = abs(float(pos.get("pos") or 0.0))
                            # Convert contracts to base amount using ctVal
                            ct_val = float(pos.get("ctVal") or 0.0)
                            if ct_val > 0:
                                actual_pos_size = pos_qty * ct_val
                            else:
                                actual_pos_size = pos_qty
                            break
                elif isinstance(client, BinanceFuturesClient):
                    pos_resp = client.get_positions() or []
                    pos_list = pos_resp if isinstance(pos_resp, list) else []
                    # Normalize symbol for matching (remove / or -)
                    norm_sym = str(symbol or "").replace("/", "").replace("-", "").upper()
                    for pos in pos_list:
                        if not isinstance(pos, dict):
                            continue
                        pos_sym = str(pos.get("symbol") or "").upper()
                        if pos_sym != norm_sym:
                            continue
                        # Match position side
                        p_side = str(pos.get("positionSide") or "").strip().lower()
                        if p_side == pos_side or (p_side == "both" and pos_side in ("long", "short")):
                            pos_amt = abs(float(pos.get("positionAmt") or 0.0))
                            if pos_amt > 0:
                                actual_pos_size = pos_amt
                                break
                elif isinstance(client, BybitClient):
                    pos_resp = client.get_positions(symbol=str(symbol or "")) or {}
                    pos_list = (pos_resp.get("result") or {}).get("list") or [] if isinstance(pos_resp, dict) else []
                    want = str(symbol or "").replace("/", "").replace("-", "").upper()
                    for pos in pos_list:
                        if not isinstance(pos, dict):
                            continue
                        pos_sym = str(pos.get("symbol") or "").strip().upper()
                        if pos_sym != want:
                            continue
                        p_side = str(pos.get("side") or "").strip().lower()
                        if (p_side == "buy" and pos_side == "long") or (p_side == "sell" and pos_side == "short"):
                            pos_sz = abs(float(pos.get("size") or 0.0))
                            if pos_sz > 0:
                                actual_pos_size = pos_sz
                                break
                elif isinstance(client, BitgetMixClient):
                    product_type = str(exchange_config.get("product_type") or exchange_config.get("productType") or "USDT-FUTURES")
                    pos_resp = client.get_positions(product_type=product_type) or {}
                    pos_list = (pos_resp.get("data") or []) if isinstance(pos_resp, dict) else []
                    for pos in pos_list:
                        if not isinstance(pos, dict):
                            continue
                        pos_sym = str(pos.get("symbol") or "")
                        if pos_sym != str(symbol or ""):
                            continue
                        p_side = str(pos.get("holdSide") or "").strip().lower()
                        if p_side == pos_side:
                            pos_sz = abs(float(pos.get("total") or pos.get("available") or 0.0))
                            if pos_sz > 0:
                                actual_pos_size = pos_sz
                                break
                
                # If we found actual position and it's smaller than requested, use actual size
                if actual_pos_size > 0 and actual_pos_size < float(amount or 0.0):
                    logger.info(
                        f"Close position adjustment: pending_id={order_id}, strategy_id={strategy_id}, "
                        f"requested={amount}, actual_pos={actual_pos_size}, using actual"
                    )
                    phases["pos_adjustment"] = {
                        "requested": float(amount or 0.0),
                        "actual_position": actual_pos_size,
                        "using": actual_pos_size,
                    }
                    amount = actual_pos_size
            except Exception as e:
                # Best-effort only; log and continue with original amount
                logger.warning(f"Failed to query position for close adjustment: pending_id={order_id}, err={e}")
                phases["pos_query_error"] = str(e)

        # Spot close: cap to exchange free base (fees often make DB size > sellable free).
        if reduce_only and market_type == "spot" and side == "sell":
            try:
                from app.services.live_trading.spot_sizing import clamp_spot_close_quantity

                new_amt, spot_meta = clamp_spot_close_quantity(
                    client, symbol=str(symbol), requested_qty=float(amount or 0.0)
                )
                if spot_meta.get("adjusted"):
                    phases["spot_close_adjustment"] = spot_meta
                amount = new_amt
            except Exception as e:
                logger.warning(
                    "Spot close amount adjustment failed: pending_id=%s, err=%s", order_id, e
                )
                phases["spot_close_adjust_error"] = str(e)

        # Decide if we should use limit-first flow.
        use_limit_first = order_mode in ("maker", "limit", "limit_first", "maker_then_market")

        remaining = float(amount or 0.0)
        if remaining <= 0:
            self._mark_failed(order_id=order_id, error="invalid_amount")
            _notify_live_best_effort(status="failed", error="invalid_amount", amount_hint=amount)
            append_strategy_log(strategy_id, "error", f"Order rejected: invalid amount ({amount}) for {symbol} {signal_type}")
            return

        # Phase 1: limit (hang order)
        limit_order_id = ""
        if use_limit_first:
            try:
                # price adjustment to reduce immediate taker fills (best-effort)
                limit_price = float(ref_price or 0.0)
                if limit_price <= 0:
                    raise LiveTradingError("missing_ref_price_for_limit_order")
                if side == "buy":
                    limit_price = limit_price * (1.0 - maker_offset)
                else:
                    limit_price = limit_price * (1.0 + maker_offset)

                limit_client_oid = _make_client_oid("lmt")
                if isinstance(client, BinanceFuturesClient):
                    res1 = client.place_limit_order(
                        symbol=str(symbol),
                        side="BUY" if side == "buy" else "SELL",
                        quantity=remaining,
                        price=limit_price,
                        reduce_only=reduce_only,
                        position_side=pos_side,
                        client_order_id=limit_client_oid,
                    )
                elif isinstance(client, BinanceSpotClient):
                    res1 = client.place_limit_order(
                        symbol=str(symbol),
                        side="BUY" if side == "buy" else "SELL",
                        quantity=remaining,
                        price=limit_price,
                        client_order_id=limit_client_oid,
                    )
                elif isinstance(client, OkxClient):
                    td_mode = str(payload.get("margin_mode") or payload.get("td_mode") or "cross")
                    # Ensure leverage is configured for this instrument before placing order.
                    if market_type == "swap":
                        try:
                            inst_id = to_okx_swap_inst_id(str(symbol))
                            client.set_leverage(inst_id=inst_id, lever=leverage, mgn_mode=td_mode, pos_side=pos_side)
                        except Exception:
                            # If leverage set fails, let place_order raise and mark failed.
                            pass
                    res1 = client.place_limit_order(
                        market_type=market_type,
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        price=limit_price,
                        pos_side=pos_side,
                        td_mode=td_mode,
                        reduce_only=reduce_only,
                        client_order_id=limit_client_oid,
                    )
                elif isinstance(client, BitgetMixClient):
                    product_type = str(exchange_config.get("product_type") or exchange_config.get("productType") or "USDT-FUTURES")
                    margin_coin = str(exchange_config.get("margin_coin") or exchange_config.get("marginCoin") or "USDT")
                    margin_mode = str(payload.get("margin_mode") or payload.get("marginMode") or exchange_config.get("margin_mode") or exchange_config.get("marginMode") or "cross")
                    # Best-effort set leverage for Bitget mix before placing orders (otherwise exchange defaults apply).
                    try:
                        if market_type == "swap":
                            client.set_leverage(
                                symbol=str(symbol),
                                leverage=leverage,
                                margin_coin=margin_coin,
                                product_type=product_type,
                                margin_mode=margin_mode,
                                hold_side=pos_side,
                            )
                    except Exception:
                        pass
                    res1 = client.place_limit_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        price=limit_price,
                        margin_coin=margin_coin,
                        product_type=product_type,
                        margin_mode=margin_mode,
                        reduce_only=reduce_only,
                        post_only=(order_mode in ("maker", "maker_then_market", "limit_first", "limit")),
                        client_order_id=limit_client_oid,
                    )
                elif isinstance(client, BitgetSpotClient):
                    res1 = client.place_limit_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        price=limit_price,
                        client_order_id=limit_client_oid,
                    )
                elif isinstance(client, BybitClient):
                    res1 = client.place_limit_order(
                        symbol=str(symbol),
                        side=side,
                        qty=remaining,
                        price=limit_price,
                        reduce_only=reduce_only,
                        pos_side=pos_side,
                        client_order_id=limit_client_oid,
                    )
                elif isinstance(client, CoinbaseExchangeClient):
                    res1 = client.place_limit_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        price=limit_price,
                        client_order_id=limit_client_oid,
                    )
                elif isinstance(client, KrakenClient):
                    # Kraken is spot-only and returns txid as order id.
                    res1 = client.place_limit_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        price=limit_price,
                        client_order_id=limit_client_oid,
                    )
                elif isinstance(client, KrakenFuturesClient):
                    # Kraken Futures expects instrument symbols; size is treated as contracts in this client.
                    res1 = client.place_limit_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        price=limit_price,
                        reduce_only=reduce_only,
                        post_only=(order_mode in ("maker", "maker_then_market", "limit_first", "limit")),
                        client_order_id=limit_client_oid,
                    )
                elif isinstance(client, KucoinSpotClient):
                    res1 = client.place_limit_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        price=limit_price,
                        client_order_id=limit_client_oid,
                    )
                elif isinstance(client, KucoinFuturesClient):
                    try:
                        if market_type == "swap":
                            client.set_leverage(symbol=str(symbol), leverage=leverage)
                    except Exception:
                        pass
                    res1 = client.place_limit_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        price=limit_price,
                        reduce_only=reduce_only,
                        post_only=(order_mode in ("maker", "maker_then_market", "limit_first", "limit")),
                        client_order_id=limit_client_oid,
                    )
                elif isinstance(client, GateSpotClient):
                    res1 = client.place_limit_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        price=limit_price,
                        client_order_id=limit_client_oid,
                    )
                elif isinstance(client, GateUsdtFuturesClient):
                    # Best-effort set leverage before futures order
                    try:
                        client.set_leverage(contract=to_gate_currency_pair(str(symbol)), leverage=leverage)
                    except Exception:
                        pass
                    res1 = client.place_limit_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        price=limit_price,
                        reduce_only=reduce_only,
                        client_order_id=limit_client_oid,
                    )
                elif isinstance(client, DeepcoinClient):
                    res1 = client.place_limit_order(
                        symbol=str(symbol),
                        side=side,
                        qty=remaining,
                        price=limit_price,
                        reduce_only=reduce_only,
                        pos_side=pos_side,
                        client_order_id=limit_client_oid,
                    )
                elif isinstance(client, HtxClient):
                    if market_type == "swap":
                        try:
                            client.set_leverage(symbol=str(symbol), leverage=leverage)
                        except Exception:
                            pass
                    res1 = client.place_limit_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        price=limit_price,
                        reduce_only=reduce_only,
                        pos_side=pos_side,
                        client_order_id=limit_client_oid,
                    )
                else:
                    raise LiveTradingError(f"Unsupported client type: {type(client)}")

                limit_order_id = str(res1.exchange_order_id or "")
                phases["limit_place"] = res1.raw

                # Wait for fills
                if isinstance(client, BinanceFuturesClient):
                    q = client.wait_for_fill(symbol=str(symbol), order_id=limit_order_id, client_order_id=limit_client_oid, max_wait_sec=maker_wait_sec)
                    phases["limit_query"] = q
                    _apply_fill(float(q.get("filled") or 0.0), float(q.get("avg_price") or 0.0))
                    _apply_fee(float(q.get("fee") or 0.0), str(q.get("fee_ccy") or ""))
                elif isinstance(client, BinanceSpotClient):
                    q = client.wait_for_fill(symbol=str(symbol), order_id=limit_order_id, client_order_id=limit_client_oid, max_wait_sec=maker_wait_sec)
                    phases["limit_query"] = q
                    _apply_fill(float(q.get("filled") or 0.0), float(q.get("avg_price") or 0.0))
                    _apply_fee(float(q.get("fee") or 0.0), str(q.get("fee_ccy") or ""))
                elif isinstance(client, OkxClient):
                    q = client.wait_for_fill(symbol=str(symbol), ord_id=limit_order_id, cl_ord_id=limit_client_oid, market_type=market_type, max_wait_sec=maker_wait_sec)
                    phases["limit_query"] = q
                    _apply_fill(float(q.get("filled") or 0.0), float(q.get("avg_price") or 0.0))
                    _apply_fee(float(q.get("fee") or 0.0), str(q.get("fee_ccy") or ""))
                elif isinstance(client, BitgetMixClient):
                    product_type = str(exchange_config.get("product_type") or exchange_config.get("productType") or "USDT-FUTURES")
                    # Bitget /mix/order/fills may lag behind order detail; use at least a few seconds for fee capture.
                    bg_limit_wait = max(float(maker_wait_sec or 0.0), 8.0)
                    q = client.wait_for_fill(symbol=str(symbol), product_type=product_type, order_id=limit_order_id, client_oid=limit_client_oid, max_wait_sec=bg_limit_wait)
                    phases["limit_query"] = q
                    _apply_fill(float(q.get("filled") or 0.0), float(q.get("avg_price") or 0.0))
                    _apply_fee(float(q.get("fee") or 0.0), str(q.get("fee_ccy") or ""))
                elif isinstance(client, BitgetSpotClient):
                    bg_spot_limit_wait = max(float(maker_wait_sec or 0.0), 8.0)
                    q = client.wait_for_fill(symbol=str(symbol), order_id=limit_order_id, client_order_id=limit_client_oid, max_wait_sec=bg_spot_limit_wait)
                    phases["limit_query"] = q
                    _apply_fill(float(q.get("filled") or 0.0), float(q.get("avg_price") or 0.0))
                    _apply_fee(float(q.get("fee") or 0.0), str(q.get("fee_ccy") or ""))
                elif isinstance(client, BybitClient):
                    q = client.wait_for_fill(symbol=str(symbol), order_id=limit_order_id, client_order_id=limit_client_oid, max_wait_sec=maker_wait_sec)
                    phases["limit_query"] = q
                    _apply_fill(float(q.get("filled") or 0.0), float(q.get("avg_price") or 0.0))
                    _apply_fee(float(q.get("fee") or 0.0), str(q.get("fee_ccy") or ""))
                elif isinstance(client, CoinbaseExchangeClient):
                    q = client.wait_for_fill(order_id=limit_order_id, client_order_id=limit_client_oid, max_wait_sec=maker_wait_sec)
                    phases["limit_query"] = q
                    _apply_fill(float(q.get("filled") or 0.0), float(q.get("avg_price") or 0.0))
                    _apply_fee(float(q.get("fee") or 0.0), str(q.get("fee_ccy") or ""))
                elif isinstance(client, KrakenClient):
                    q = client.wait_for_fill(order_id=limit_order_id, max_wait_sec=maker_wait_sec)
                    phases["limit_query"] = q
                    _apply_fill(float(q.get("filled") or 0.0), float(q.get("avg_price") or 0.0))
                    _apply_fee(float(q.get("fee") or 0.0), str(q.get("fee_ccy") or ""))
                elif isinstance(client, KrakenFuturesClient):
                    q = client.wait_for_fill(order_id=limit_order_id, client_order_id=limit_client_oid, max_wait_sec=maker_wait_sec)
                    phases["limit_query"] = q
                    _apply_fill(float(q.get("filled") or 0.0), float(q.get("avg_price") or 0.0))
                    _apply_fee(float(q.get("fee") or 0.0), str(q.get("fee_ccy") or ""))
                elif isinstance(client, KucoinSpotClient):
                    q = client.wait_for_fill(order_id=limit_order_id, max_wait_sec=maker_wait_sec)
                    phases["limit_query"] = q
                    _apply_fill(float(q.get("filled") or 0.0), float(q.get("avg_price") or 0.0))
                    _apply_fee(float(q.get("fee") or 0.0), str(q.get("fee_ccy") or ""))
                elif isinstance(client, KucoinFuturesClient):
                    q = client.wait_for_fill(order_id=limit_order_id, max_wait_sec=maker_wait_sec)
                    phases["limit_query"] = q
                    _apply_fill(float(q.get("filled") or 0.0), float(q.get("avg_price") or 0.0))
                    _apply_fee(float(q.get("fee") or 0.0), str(q.get("fee_ccy") or ""))
                elif isinstance(client, GateSpotClient):
                    gate_spot_limit_wait = max(float(maker_wait_sec or 0.0), 8.0)
                    q = client.wait_for_fill(order_id=limit_order_id, max_wait_sec=gate_spot_limit_wait)
                    phases["limit_query"] = q
                    _apply_fill(float(q.get("filled") or 0.0), float(q.get("avg_price") or 0.0))
                    _apply_fee(float(q.get("fee") or 0.0), str(q.get("fee_ccy") or ""))
                elif isinstance(client, GateUsdtFuturesClient):
                    gate_limit_wait = max(float(maker_wait_sec or 0.0), 8.0)
                    q = client.wait_for_fill(order_id=limit_order_id, contract=to_gate_currency_pair(str(symbol)), max_wait_sec=gate_limit_wait)
                    phases["limit_query"] = q
                    _apply_fill(float(q.get("filled") or 0.0), float(q.get("avg_price") or 0.0))
                    _apply_fee(float(q.get("fee") or 0.0), str(q.get("fee_ccy") or ""))
                elif isinstance(client, DeepcoinClient):
                    q = client.wait_for_fill(symbol=str(symbol), order_id=limit_order_id, client_order_id=limit_client_oid, max_wait_sec=maker_wait_sec)
                    phases["limit_query"] = q
                    _apply_fill(float(q.get("filled") or 0.0), float(q.get("avg_price") or 0.0))
                    _apply_fee(float(q.get("fee") or 0.0), str(q.get("fee_ccy") or ""))
                elif isinstance(client, HtxClient):
                    q = client.wait_for_fill(symbol=str(symbol), order_id=limit_order_id, client_order_id=limit_client_oid, max_wait_sec=maker_wait_sec)
                    phases["limit_query"] = q
                    _apply_fill(float(q.get("filled") or 0.0), float(q.get("avg_price") or 0.0))
                    _apply_fee(float(q.get("fee") or 0.0), str(q.get("fee_ccy") or ""))

                remaining = max(0.0, float(amount or 0.0) - total_base)

                # Tail guard: if remaining is below the exchange min tradable amount, do NOT chase it with a market order.
                # This avoids the common case: limit partially fills, remainder is too small => market phase fails, yet
                # the exchange already opened a position (user sees "failed" incorrectly).
                if remaining > 0 and isinstance(client, OkxClient) and market_type == "swap":
                    try:
                        inst_id = to_okx_swap_inst_id(str(symbol))
                        inst = client.get_instrument(inst_type="SWAP", inst_id=inst_id) or {}
                        lot_sz = float(inst.get("lotSz") or 0.0)  # contracts step
                        min_sz = float(inst.get("minSz") or 0.0)  # min contracts
                        ct_val = float(inst.get("ctVal") or 0.0)  # base per contract
                        # Convert contract min to base min (best-effort)
                        min_contract = min_sz if min_sz > 0 else (lot_sz if lot_sz > 0 else 0.0)
                        min_base = (min_contract * ct_val) if (min_contract > 0 and ct_val > 0) else 0.0
                        if min_base > 0 and remaining < (min_base * 0.999999):
                            phases["tail_guard"] = {
                                "exchange": "okx",
                                "inst_id": inst_id,
                                "remaining": remaining,
                                "min_base": min_base,
                            }
                            remaining = 0.0
                    except Exception:
                        pass

                # Cancel if not fully filled
                if remaining > max(0.0, float(amount or 0.0) * 0.001):
                    try:
                        if isinstance(client, BinanceFuturesClient):
                            phases["limit_cancel"] = client.cancel_order(symbol=str(symbol), order_id=limit_order_id, client_order_id=limit_client_oid)
                        elif isinstance(client, BinanceSpotClient):
                            phases["limit_cancel"] = client.cancel_order(symbol=str(symbol), order_id=limit_order_id, client_order_id=limit_client_oid)
                        elif isinstance(client, OkxClient):
                            phases["limit_cancel"] = client.cancel_order(market_type=market_type, symbol=str(symbol), ord_id=limit_order_id, cl_ord_id=limit_client_oid)
                        elif isinstance(client, BitgetMixClient):
                            product_type = str(exchange_config.get("product_type") or exchange_config.get("productType") or "USDT-FUTURES")
                            margin_coin = str(exchange_config.get("margin_coin") or exchange_config.get("marginCoin") or "USDT")
                            phases["limit_cancel"] = client.cancel_order(symbol=str(symbol), product_type=product_type, margin_coin=margin_coin, order_id=limit_order_id, client_oid=limit_client_oid)
                        elif isinstance(client, BitgetSpotClient):
                            phases["limit_cancel"] = client.cancel_order(symbol=str(symbol), client_order_id=limit_client_oid)
                        elif isinstance(client, BybitClient):
                            phases["limit_cancel"] = client.cancel_order(symbol=str(symbol), order_id=limit_order_id, client_order_id=limit_client_oid)
                        elif isinstance(client, CoinbaseExchangeClient):
                            phases["limit_cancel"] = client.cancel_order(order_id=limit_order_id, client_order_id=limit_client_oid)
                        elif isinstance(client, KrakenClient):
                            phases["limit_cancel"] = client.cancel_order(order_id=limit_order_id)
                        elif isinstance(client, KrakenFuturesClient):
                            phases["limit_cancel"] = client.cancel_order(order_id=limit_order_id, client_order_id=limit_client_oid)
                        elif isinstance(client, KucoinSpotClient):
                            phases["limit_cancel"] = client.cancel_order(order_id=limit_order_id, client_order_id=limit_client_oid)
                        elif isinstance(client, KucoinFuturesClient):
                            phases["limit_cancel"] = client.cancel_order(order_id=limit_order_id, client_order_id=limit_client_oid)
                        elif isinstance(client, GateSpotClient):
                            phases["limit_cancel"] = client.cancel_order(order_id=limit_order_id)
                        elif isinstance(client, GateUsdtFuturesClient):
                            phases["limit_cancel"] = client.cancel_order(order_id=limit_order_id)
                        elif isinstance(client, DeepcoinClient):
                            phases["limit_cancel"] = client.cancel_order(symbol=str(symbol), order_id=limit_order_id, client_order_id=limit_client_oid)
                        elif isinstance(client, HtxClient):
                            phases["limit_cancel"] = client.cancel_order(symbol=str(symbol), order_id=limit_order_id, client_order_id=limit_client_oid)
                    except Exception:
                        pass
            except LiveTradingError as e:
                logger.warning(f"live limit phase failed: pending_id={order_id}, strategy_id={strategy_id}, cfg={safe_cfg}, err={e}")
                # Fall back to market for full amount
                remaining = float(amount or 0.0)
                phases["limit_error"] = str(e)
                append_strategy_log(strategy_id, "error", f"Exchange limit order failed ({exchange_id} {symbol}): {e}, falling back to market")
            except Exception as e:
                logger.warning(f"live limit phase unexpected error: pending_id={order_id}, strategy_id={strategy_id}, cfg={safe_cfg}, err={e}")
                remaining = float(amount or 0.0)
                phases["limit_error"] = str(e)
                append_strategy_log(strategy_id, "error", f"Limit order unexpected error ({exchange_id} {symbol}): {e}, falling back to market")

        # Phase 2: market for remaining
        market_order_id = ""
        market_client_oid = _make_client_oid("mkt")
        if remaining > 0:
            try:
                if isinstance(client, BinanceFuturesClient):
                    res2 = client.place_market_order(
                        symbol=str(symbol),
                        side="BUY" if side == "buy" else "SELL",
                        quantity=remaining,
                        reduce_only=reduce_only,
                        position_side=pos_side,
                        client_order_id=market_client_oid,
                    )
                elif isinstance(client, BinanceSpotClient):
                    res2 = client.place_market_order(
                        symbol=str(symbol),
                        side="BUY" if side == "buy" else "SELL",
                        quantity=remaining,
                        client_order_id=market_client_oid,
                    )
                elif isinstance(client, OkxClient):
                    td_mode = str(payload.get("margin_mode") or payload.get("td_mode") or "cross")
                    if market_type == "swap":
                        try:
                            inst_id = to_okx_swap_inst_id(str(symbol))
                            client.set_leverage(inst_id=inst_id, lever=leverage, mgn_mode=td_mode, pos_side=pos_side)
                        except Exception:
                            pass
                    res2 = client.place_market_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        market_type=market_type,
                        pos_side=pos_side,
                        td_mode=td_mode,
                        reduce_only=reduce_only,
                        client_order_id=market_client_oid,
                    )
                elif isinstance(client, BitgetMixClient):
                    product_type = str(exchange_config.get("product_type") or exchange_config.get("productType") or "USDT-FUTURES")
                    margin_coin = str(exchange_config.get("margin_coin") or exchange_config.get("marginCoin") or "USDT")
                    margin_mode = str(payload.get("margin_mode") or payload.get("marginMode") or exchange_config.get("margin_mode") or exchange_config.get("marginMode") or "cross")
                    try:
                        if market_type == "swap":
                            client.set_leverage(
                                symbol=str(symbol),
                                leverage=leverage,
                                margin_coin=margin_coin,
                                product_type=product_type,
                                margin_mode=margin_mode,
                                hold_side=pos_side,
                            )
                    except Exception:
                        pass
                    res2 = client.place_market_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        margin_coin=margin_coin,
                        product_type=product_type,
                        margin_mode=margin_mode,
                        reduce_only=reduce_only,
                        client_order_id=market_client_oid,
                    )
                elif isinstance(client, BitgetSpotClient):
                    # For Bitget spot market BUY, convert base->quote using ref_price (hummingbot style).
                    mkt_size = remaining
                    if side == "buy" and ref_price > 0:
                        mkt_size = remaining * ref_price
                    res2 = client.place_market_order(
                        symbol=str(symbol),
                        side=side,
                        size=mkt_size,
                        client_order_id=market_client_oid,
                    )
                elif isinstance(client, BybitClient):
                    res2 = client.place_market_order(
                        symbol=str(symbol),
                        side=side,
                        qty=remaining,
                        reduce_only=reduce_only,
                        pos_side=pos_side,
                        client_order_id=market_client_oid,
                    )
                elif isinstance(client, CoinbaseExchangeClient):
                    res2 = client.place_market_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        client_order_id=market_client_oid,
                    )
                elif isinstance(client, KrakenClient):
                    res2 = client.place_market_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        client_order_id=market_client_oid,
                    )
                elif isinstance(client, KrakenFuturesClient):
                    res2 = client.place_market_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        reduce_only=reduce_only,
                        client_order_id=market_client_oid,
                    )
                elif isinstance(client, KucoinSpotClient):
                    # KuCoin market BUY expects quote funds; convert base->quote using ref_price.
                    if side == "buy" and ref_price > 0:
                        res2 = client.place_market_order(
                            symbol=str(symbol),
                            side=side,
                            size=float(remaining) * float(ref_price),
                            quote_size=True,
                            client_order_id=market_client_oid,
                        )
                    else:
                        res2 = client.place_market_order(
                            symbol=str(symbol),
                            side=side,
                            size=remaining,
                            quote_size=False,
                            client_order_id=market_client_oid,
                        )
                elif isinstance(client, KucoinFuturesClient):
                    try:
                        if market_type == "swap":
                            client.set_leverage(symbol=str(symbol), leverage=leverage)
                    except Exception:
                        pass
                    res2 = client.place_market_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        reduce_only=reduce_only,
                        client_order_id=market_client_oid,
                    )
                elif isinstance(client, GateSpotClient):
                    mkt_size = remaining
                    if side == "buy" and ref_price > 0:
                        mkt_size = remaining * ref_price
                    res2 = client.place_market_order(
                        symbol=str(symbol),
                        side=side,
                        size=mkt_size,
                        client_order_id=market_client_oid,
                    )
                elif isinstance(client, GateUsdtFuturesClient):
                    try:
                        client.set_leverage(contract=to_gate_currency_pair(str(symbol)), leverage=leverage)
                    except Exception:
                        pass
                    res2 = client.place_market_order(
                        symbol=str(symbol),
                        side=side,
                        size=remaining,
                        reduce_only=reduce_only,
                        client_order_id=market_client_oid,
                    )
                elif isinstance(client, DeepcoinClient):
                    if market_type == "swap":
                        try:
                            client.set_leverage(symbol=str(symbol), leverage=leverage)
                        except Exception:
                            pass
                    res2 = client.place_market_order(
                        symbol=str(symbol),
                        side=side,
                        qty=remaining,
                        reduce_only=reduce_only,
                        pos_side=pos_side,
                        client_order_id=market_client_oid,
                    )
                elif isinstance(client, HtxClient):
                    if market_type == "swap":
                        try:
                            client.set_leverage(symbol=str(symbol), leverage=leverage)
                        except Exception:
                            pass
                    res2 = client.place_market_order(
                        symbol=str(symbol),
                        side=side,
                        qty=remaining,
                        reduce_only=reduce_only,
                        pos_side=pos_side,
                        client_order_id=market_client_oid,
                    )
                else:
                    raise LiveTradingError(f"Unsupported client type: {type(client)}")

                market_order_id = str(res2.exchange_order_id or "")
                phases["market_place"] = res2.raw

                # Query fills (short wait)
                if isinstance(client, BinanceFuturesClient):
                    q2 = client.wait_for_fill(symbol=str(symbol), order_id=market_order_id, client_order_id=market_client_oid, max_wait_sec=5.0)
                    phases["market_query"] = q2
                    _apply_fill(float(q2.get("filled") or 0.0), float(q2.get("avg_price") or 0.0))
                    _apply_fee(float(q2.get("fee") or 0.0), str(q2.get("fee_ccy") or ""))
                elif isinstance(client, BinanceSpotClient):
                    q2 = client.wait_for_fill(symbol=str(symbol), order_id=market_order_id, client_order_id=market_client_oid, max_wait_sec=5.0)
                    phases["market_query"] = q2
                    _apply_fill(float(q2.get("filled") or 0.0), float(q2.get("avg_price") or 0.0))
                    _apply_fee(float(q2.get("fee") or 0.0), str(q2.get("fee_ccy") or ""))
                elif isinstance(client, OkxClient):
                    # OKX fills endpoint may lag shortly after execution; wait a bit longer to capture fee.
                    q2 = client.wait_for_fill(symbol=str(symbol), ord_id=market_order_id, cl_ord_id=market_client_oid, market_type=market_type, max_wait_sec=12.0)
                    phases["market_query"] = q2
                    _apply_fill(float(q2.get("filled") or 0.0), float(q2.get("avg_price") or 0.0))
                    _apply_fee(float(q2.get("fee") or 0.0), str(q2.get("fee_ccy") or ""))
                elif isinstance(client, BitgetMixClient):
                    product_type = str(exchange_config.get("product_type") or exchange_config.get("productType") or "USDT-FUTURES")
                    q2 = client.wait_for_fill(symbol=str(symbol), product_type=product_type, order_id=market_order_id, client_oid=market_client_oid, max_wait_sec=12.0)
                    phases["market_query"] = q2
                    _apply_fill(float(q2.get("filled") or 0.0), float(q2.get("avg_price") or 0.0))
                    _apply_fee(float(q2.get("fee") or 0.0), str(q2.get("fee_ccy") or ""))
                elif isinstance(client, BitgetSpotClient):
                    q2 = client.wait_for_fill(symbol=str(symbol), order_id=market_order_id, client_order_id=market_client_oid, max_wait_sec=12.0)
                    phases["market_query"] = q2
                    _apply_fill(float(q2.get("filled") or 0.0), float(q2.get("avg_price") or 0.0))
                    _apply_fee(float(q2.get("fee") or 0.0), str(q2.get("fee_ccy") or ""))
                elif isinstance(client, BybitClient):
                    q2 = client.wait_for_fill(symbol=str(symbol), order_id=market_order_id, client_order_id=market_client_oid, max_wait_sec=12.0)
                    phases["market_query"] = q2
                    _apply_fill(float(q2.get("filled") or 0.0), float(q2.get("avg_price") or 0.0))
                    _apply_fee(float(q2.get("fee") or 0.0), str(q2.get("fee_ccy") or ""))
                elif isinstance(client, CoinbaseExchangeClient):
                    q2 = client.wait_for_fill(order_id=market_order_id, client_order_id=market_client_oid, max_wait_sec=12.0)
                    phases["market_query"] = q2
                    _apply_fill(float(q2.get("filled") or 0.0), float(q2.get("avg_price") or 0.0))
                    _apply_fee(float(q2.get("fee") or 0.0), str(q2.get("fee_ccy") or ""))
                elif isinstance(client, KrakenClient):
                    q2 = client.wait_for_fill(order_id=market_order_id, max_wait_sec=12.0)
                    phases["market_query"] = q2
                    _apply_fill(float(q2.get("filled") or 0.0), float(q2.get("avg_price") or 0.0))
                    _apply_fee(float(q2.get("fee") or 0.0), str(q2.get("fee_ccy") or ""))
                elif isinstance(client, KrakenFuturesClient):
                    q2 = client.wait_for_fill(order_id=market_order_id, client_order_id=market_client_oid, max_wait_sec=12.0)
                    phases["market_query"] = q2
                    _apply_fill(float(q2.get("filled") or 0.0), float(q2.get("avg_price") or 0.0))
                    _apply_fee(float(q2.get("fee") or 0.0), str(q2.get("fee_ccy") or ""))
                elif isinstance(client, KucoinSpotClient):
                    q2 = client.wait_for_fill(order_id=market_order_id, max_wait_sec=12.0)
                    phases["market_query"] = q2
                    _apply_fill(float(q2.get("filled") or 0.0), float(q2.get("avg_price") or 0.0))
                    _apply_fee(float(q2.get("fee") or 0.0), str(q2.get("fee_ccy") or ""))
                elif isinstance(client, KucoinFuturesClient):
                    q2 = client.wait_for_fill(order_id=market_order_id, max_wait_sec=12.0)
                    phases["market_query"] = q2
                    _apply_fill(float(q2.get("filled") or 0.0), float(q2.get("avg_price") or 0.0))
                    _apply_fee(float(q2.get("fee") or 0.0), str(q2.get("fee_ccy") or ""))
                elif isinstance(client, GateSpotClient):
                    q2 = client.wait_for_fill(order_id=market_order_id, max_wait_sec=12.0)
                    phases["market_query"] = q2
                    _apply_fill(float(q2.get("filled") or 0.0), float(q2.get("avg_price") or 0.0))
                    _apply_fee(float(q2.get("fee") or 0.0), str(q2.get("fee_ccy") or ""))
                elif isinstance(client, GateUsdtFuturesClient):
                    q2 = client.wait_for_fill(order_id=market_order_id, contract=to_gate_currency_pair(str(symbol)), max_wait_sec=12.0)
                    phases["market_query"] = q2
                    _apply_fill(float(q2.get("filled") or 0.0), float(q2.get("avg_price") or 0.0))
                    _apply_fee(float(q2.get("fee") or 0.0), str(q2.get("fee_ccy") or ""))
                elif isinstance(client, DeepcoinClient):
                    q2 = client.wait_for_fill(symbol=str(symbol), order_id=market_order_id, client_order_id=market_client_oid, max_wait_sec=12.0)
                    phases["market_query"] = q2
                    _apply_fill(float(q2.get("filled") or 0.0), float(q2.get("avg_price") or 0.0))
                    _apply_fee(float(q2.get("fee") or 0.0), str(q2.get("fee_ccy") or ""))
                elif isinstance(client, HtxClient):
                    q2 = client.wait_for_fill(symbol=str(symbol), order_id=market_order_id, client_order_id=market_client_oid, max_wait_sec=12.0)
                    phases["market_query"] = q2
                    _apply_fill(float(q2.get("filled") or 0.0), float(q2.get("avg_price") or 0.0))
                    _apply_fee(float(q2.get("fee") or 0.0), str(q2.get("fee_ccy") or ""))
            except LiveTradingError as e:
                logger.warning(f"live market phase failed: pending_id={order_id}, strategy_id={strategy_id}, cfg={safe_cfg}, err={e}")
                phases["market_error"] = str(e)
                # If we already got any fills in the limit phase, treat as partial success instead of failing the whole order.
                if float(total_base or 0.0) > 0:
                    _console_print(
                        f"[worker] market tail failed but partial filled: strategy_id={strategy_id} pending_id={order_id} filled={total_base} err={e}"
                    )
                    remaining = 0.0
                    append_strategy_log(strategy_id, "error", f"Exchange market order partially failed ({symbol} {signal_type}): {e} (partial filled={total_base})")
                else:
                    self._mark_failed(order_id=order_id, error=str(e))
                    _console_print(f"[worker] order failed: strategy_id={strategy_id} pending_id={order_id} err={e}")
                    _notify_live_best_effort(status="failed", error=str(e), amount_hint=amount, price_hint=ref_price)
                    append_strategy_log(strategy_id, "error", f"Exchange order failed ({exchange_id} {symbol} {signal_type}): {e}")
                    return
            except Exception as e:
                logger.warning(f"live market phase unexpected error: pending_id={order_id}, strategy_id={strategy_id}, cfg={safe_cfg}, err={e}")
                self._mark_failed(order_id=order_id, error=str(e))
                _console_print(f"[worker] order unexpected error: strategy_id={strategy_id} pending_id={order_id} err={e}")
                _notify_live_best_effort(status="failed", error=str(e), amount_hint=amount, price_hint=ref_price)
                append_strategy_log(strategy_id, "error", f"Unexpected order error ({exchange_id} {symbol} {signal_type}): {e}")
                return

        # Build final result (best-effort)
        filled_final = float(total_base or 0.0)
        avg_final = float(_current_avg() or 0.0)
        if filled_final <= 0 and ref_price > 0:
            filled_final = float(amount or 0.0)
            avg_final = float(ref_price or 0.0)

        res = type("Tmp", (), {"exchange_id": str(exchange_config.get("exchange_id") or ""), "exchange_order_id": str(market_order_id or limit_order_id), "raw": phases, "filled": filled_final, "avg_price": avg_final})()

        executed_at = int(time.time())
        filled = filled_final
        avg_price = avg_final
        post_query: Dict[str, Any] = phases

        # Persist queue result first (idempotency / observability).
        try:
            self._mark_sent(
                order_id=order_id,
                note="live_order_sent",
                exchange_id=res.exchange_id,
                exchange_order_id=res.exchange_order_id,
                exchange_response_json=json.dumps({"phases": (post_query or {})}, ensure_ascii=False),
                filled=filled,
                avg_price=avg_price,
                executed_at=executed_at,
            )
            _console_print(f"[worker] order sent: strategy_id={strategy_id} pending_id={order_id} exchange={res.exchange_id} order_id={res.exchange_order_id} filled={filled} avg={avg_price}")
        except Exception as e:
            logger.warning(f"mark_sent failed: pending_id={order_id}, err={e}")

        # Record trade + update local position snapshot (best-effort).
        try:
            if filled > 0 and avg_price > 0:
                logger.info(
                    f"live record begin: pending_id={order_id} strategy_id={strategy_id} symbol={symbol} "
                    f"signal={signal_type} filled={filled} avg_price={avg_price} fee={total_fee} fee_ccy={fee_ccy}"
                )
                profit, _pos = apply_fill_to_local_position(
                    strategy_id=strategy_id,
                    symbol=str(symbol),
                    signal_type=str(signal_type),
                    filled=filled,
                    avg_price=avg_price,
                )
                # ``profit`` = trade P&L from position math (gross).
                # ``commission`` = fee synced from the exchange fill (see
                # ``total_fee`` above). Net realised P&L is always
                # ``profit - commission`` at read/aggregate time — do not
                # pre-subtract here or dashboards double-count the fee.
                record_trade(
                    strategy_id=strategy_id,
                    symbol=str(symbol),
                    trade_type=str(signal_type),
                    price=avg_price,
                    amount=filled,
                    commission=float(total_fee or 0.0),
                    commission_ccy=str(fee_ccy or "").strip().upper(),
                    profit=profit,
                )
                logger.info(f"live record done: pending_id={order_id} strategy_id={strategy_id} symbol={symbol} signal={signal_type}")
                _profit_str = f", profit={profit:.4f}" if profit is not None else ""
                _fee_str = f", fee={total_fee:.6f} {fee_ccy}" if total_fee > 0 else ""
                _reason_parts = []
                _reason = str(payload.get("reason") or "").strip()
                if _reason:
                    _reason_parts.append(f"reason={_reason}")
                for _key, _label in (
                    ("stop_loss_price", "sl"),
                    ("take_profit_price", "tp"),
                    ("trailing_stop_price", "trail"),
                ):
                    try:
                        _v = float(payload.get(_key) or 0.0)
                    except Exception:
                        _v = 0.0
                    if _v > 0:
                        _reason_parts.append(f"{_label}={_v:.6f}")
                _reason_str = f", {', '.join(_reason_parts)}" if _reason_parts else ""
                append_strategy_log(
                    strategy_id, "trade",
                    f"Trade executed: {signal_type} {symbol} filled={filled:.6f} @ {avg_price:.6f}{_fee_str}{_profit_str}{_reason_str} (exchange={res.exchange_id})",
                )
        except Exception as e:
            logger.warning(f"record_trade/update_position failed: pending_id={order_id}, err={e}")

        # Notify live results (best-effort; does not affect execution).
        _notify_live_best_effort(
            status="sent",
            exchange_id=res.exchange_id,
            exchange_order_id=res.exchange_order_id,
            price_hint=avg_price if avg_price > 0 else ref_price,
            amount_hint=filled if filled > 0 else amount,
        )

    def _execute_ibkr_order(
        self,
        *,
        order_id: int,
        order_row: Dict[str, Any],
        payload: Dict[str, Any],
        client,  # IBKRClient instance
        strategy_id: int,
        exchange_config: Dict[str, Any],
        _notify_live_best_effort,
        _console_print,
    ) -> None:
        """
        Execute order via Interactive Brokers for US stocks.

        Simplified flow compared to crypto (no maker->market fallback):
        - Place market order directly
        - Wait for fill
        - Record trade
        """
        signal_type = payload.get("signal_type") or order_row.get("signal_type")
        symbol = payload.get("symbol") or order_row.get("symbol")
        amount = float(payload.get("amount") or order_row.get("amount") or 0.0)
        ref_price = float(payload.get("ref_price") or payload.get("price") or order_row.get("price") or 0.0)

        sig = str(signal_type or "").strip().lower()

        # Stocks: no short selling in basic implementation
        if "short" in sig:
            self._mark_failed(order_id=order_id, error="ibkr_stock_short_not_supported")
            _console_print(f"[worker] IBKR order rejected: strategy_id={strategy_id} pending_id={order_id} short not supported")
            _notify_live_best_effort(status="failed", error="ibkr_stock_short_not_supported")
            return

        # Map signal to action (include stop/tp/trailing aliases)
        if sig in ("open_long", "add_long"):
            action = "buy"
        elif sig in ("close_long", "reduce_long", "close_long_stop", "close_long_profit", "close_long_trailing"):
            action = "sell"
        else:
            self._mark_failed(order_id=order_id, error=f"ibkr_unsupported_signal:{signal_type}")
            _console_print(f"[worker] IBKR order rejected: strategy_id={strategy_id} pending_id={order_id} unsupported signal {signal_type}")
            _notify_live_best_effort(status="failed", error=f"ibkr_unsupported_signal:{signal_type}")
            return

        # Get market type (USStock)
        market_type = str(
            payload.get("market_type") or
            payload.get("market_category") or
            exchange_config.get("market_type") or
            exchange_config.get("market_category") or
            "USStock"
        ).strip()

        try:
            # Place market order via IBKR
            result = client.place_market_order(
                symbol=symbol,
                side=action,
                quantity=amount,
                market_type=market_type,
            )

            if not result.success:
                self._mark_failed(order_id=order_id, error=f"ibkr_order_failed:{result.message}")
                _console_print(f"[worker] IBKR order failed: strategy_id={strategy_id} pending_id={order_id} err={result.message}")
                _notify_live_best_effort(status="failed", error=f"ibkr_order_failed:{result.message}")
                append_strategy_log(strategy_id, "error", f"IBKR order failed ({symbol} {signal_type}): {result.message}")
                return

            filled = float(result.filled or 0.0)
            avg_price = float(result.avg_price or 0.0)
            exchange_order_id = str(result.order_id or "")

            if avg_price <= 0 and ref_price > 0:
                logger.warning(f"[worker] IBKR order avg_price=0, using ref_price={ref_price} as fallback: strategy_id={strategy_id} pending_id={order_id}")
                avg_price = ref_price
            if filled <= 0:
                logger.warning(f"[worker] IBKR order filled=0, using amount={amount} as fallback: strategy_id={strategy_id} pending_id={order_id}")
                filled = amount

            executed_at = int(time.time())

            # Mark order as sent
            self._mark_sent(
                order_id=order_id,
                note="ibkr_order_sent",
                exchange_id="ibkr",
                exchange_order_id=exchange_order_id,
                exchange_response_json=json.dumps(result.raw or {}, ensure_ascii=False),
                filled=filled,
                avg_price=avg_price,
                executed_at=executed_at,
            )
            _console_print(f"[worker] IBKR order sent: strategy_id={strategy_id} pending_id={order_id} order_id={exchange_order_id} filled={filled} avg={avg_price}")

            # Record trade and update position
            try:
                if filled > 0 and avg_price > 0:
                    logger.info(
                        f"IBKR record begin: pending_id={order_id} strategy_id={strategy_id} symbol={symbol} "
                        f"signal={signal_type} filled={filled} avg_price={avg_price}"
                    )
                    profit, _pos = apply_fill_to_local_position(
                        strategy_id=strategy_id,
                        symbol=str(symbol),
                        signal_type=str(signal_type),
                        filled=filled,
                        avg_price=avg_price,
                    )
                    record_trade(
                        strategy_id=strategy_id,
                        symbol=str(symbol),
                        trade_type=str(signal_type),
                        price=avg_price,
                        amount=filled,
                        commission=0.0,  # IBKR commission is complex, skip for now
                        commission_ccy="USD",
                        profit=profit,
                    )
                    logger.info(f"IBKR record done: pending_id={order_id} strategy_id={strategy_id} symbol={symbol}")
                    _pstr = f", profit={profit:.4f}" if profit is not None else ""
                    append_strategy_log(
                        strategy_id, "trade",
                        f"Trade executed: {signal_type} {symbol} filled={filled:.6f} @ {avg_price:.6f}{_pstr} (exchange=ibkr)",
                    )
            except Exception as e:
                logger.warning(f"IBKR record_trade/update_position failed: pending_id={order_id}, err={e}")

            # Notify success
            _notify_live_best_effort(
                status="sent",
                exchange_id="ibkr",
                exchange_order_id=exchange_order_id,
                price_hint=avg_price,
                amount_hint=filled,
            )

        except Exception as e:
            logger.error(f"IBKR order execution failed: pending_id={order_id}, strategy_id={strategy_id}, err={e}")
            self._mark_failed(order_id=order_id, error=f"ibkr_exception:{e}")
            _console_print(f"[worker] IBKR order exception: strategy_id={strategy_id} pending_id={order_id} err={e}")
            _notify_live_best_effort(status="failed", error=str(e))
            append_strategy_log(strategy_id, "error", f"IBKR order exception ({symbol} {signal_type}): {e}")
            if is_fatal_exchange_error(str(e)):
                auto_stop_live_strategy(int(strategy_id), str(e), source="ibkr_order")

    def _execute_alpaca_order(
        self,
        *,
        order_id: int,
        order_row: Dict[str, Any],
        payload: Dict[str, Any],
        client,  # AlpacaClient instance
        strategy_id: int,
        exchange_config: Dict[str, Any],
        market_category: str,
        _notify_live_best_effort,
        _console_print,
    ) -> None:
        """
        Execute order via Alpaca for US stocks (USStock) or crypto.

        Mirrors `_execute_ibkr_order`: market order, brief poll for fill,
        record trade, mark sent. Long-only — short signals are rejected.
        """
        signal_type = payload.get("signal_type") or order_row.get("signal_type")
        symbol = payload.get("symbol") or order_row.get("symbol")
        amount = float(payload.get("amount") or order_row.get("amount") or 0.0)
        ref_price = float(payload.get("ref_price") or payload.get("price") or order_row.get("price") or 0.0)

        sig = str(signal_type or "").strip().lower()

        if "short" in sig:
            self._mark_failed(order_id=order_id, error="alpaca_short_not_supported")
            _console_print(f"[worker] Alpaca order rejected: strategy_id={strategy_id} pending_id={order_id} short not supported")
            _notify_live_best_effort(status="failed", error="alpaca_short_not_supported")
            return

        if sig in ("open_long", "add_long"):
            action = "buy"
        elif sig in ("close_long", "reduce_long", "close_long_stop", "close_long_profit", "close_long_trailing"):
            action = "sell"
        else:
            self._mark_failed(order_id=order_id, error=f"alpaca_unsupported_signal:{signal_type}")
            _console_print(f"[worker] Alpaca order rejected: strategy_id={strategy_id} pending_id={order_id} unsupported signal {signal_type}")
            _notify_live_best_effort(status="failed", error=f"alpaca_unsupported_signal:{signal_type}")
            return

        # Decide stock vs crypto leg of the Alpaca account based on the
        # strategy's market_category (USStock by default).
        mc = (market_category or "USStock").strip()
        market_type_for_client = "crypto" if mc.lower() in ("crypto", "cryptocurrency") else "USStock"

        try:
            result = client.place_market_order(
                symbol=symbol,
                side=action,
                quantity=amount,
                market_type=market_type_for_client,
            )

            if not result.success:
                self._mark_failed(order_id=order_id, error=f"alpaca_order_failed:{result.message}")
                _console_print(f"[worker] Alpaca order failed: strategy_id={strategy_id} pending_id={order_id} err={result.message}")
                _notify_live_best_effort(status="failed", error=f"alpaca_order_failed:{result.message}")
                append_strategy_log(strategy_id, "error", f"Alpaca order failed ({symbol} {signal_type}): {result.message}")
                return

            filled = float(result.filled or 0.0)
            avg_price = float(result.avg_price or 0.0)
            exchange_order_id = str(result.order_id or "")

            if avg_price <= 0 and ref_price > 0:
                logger.warning(
                    f"[worker] Alpaca order avg_price=0, using ref_price={ref_price} as fallback: "
                    f"strategy_id={strategy_id} pending_id={order_id}"
                )
                avg_price = ref_price
            if filled <= 0:
                logger.warning(
                    f"[worker] Alpaca order filled=0, using amount={amount} as fallback: "
                    f"strategy_id={strategy_id} pending_id={order_id}"
                )
                filled = amount

            executed_at = int(time.time())

            self._mark_sent(
                order_id=order_id,
                note="alpaca_order_sent",
                exchange_id="alpaca",
                exchange_order_id=exchange_order_id,
                exchange_response_json=json.dumps(result.raw or {}, ensure_ascii=False),
                filled=filled,
                avg_price=avg_price,
                executed_at=executed_at,
            )
            _console_print(
                f"[worker] Alpaca order sent: strategy_id={strategy_id} pending_id={order_id} "
                f"order_id={exchange_order_id} filled={filled} avg={avg_price}"
            )

            try:
                if filled > 0 and avg_price > 0:
                    profit, _pos = apply_fill_to_local_position(
                        strategy_id=strategy_id,
                        symbol=str(symbol),
                        signal_type=str(signal_type),
                        filled=filled,
                        avg_price=avg_price,
                    )
                    record_trade(
                        strategy_id=strategy_id,
                        symbol=str(symbol),
                        trade_type=str(signal_type),
                        price=avg_price,
                        amount=filled,
                        commission=0.0,  # Alpaca commissions are zero on stocks; crypto has fees in raw
                        commission_ccy="USD",
                        profit=profit,
                    )
                    _pstr = f", profit={profit:.4f}" if profit is not None else ""
                    append_strategy_log(
                        strategy_id, "trade",
                        f"Trade executed: {signal_type} {symbol} filled={filled:.6f} @ {avg_price:.6f}{_pstr} (exchange=alpaca)",
                    )
            except Exception as e:
                logger.warning(f"Alpaca record_trade/update_position failed: pending_id={order_id}, err={e}")

            _notify_live_best_effort(
                status="sent",
                exchange_id="alpaca",
                exchange_order_id=exchange_order_id,
                price_hint=avg_price,
                amount_hint=filled,
            )

        except Exception as e:
            logger.error(f"Alpaca order execution failed: pending_id={order_id}, strategy_id={strategy_id}, err={e}")
            self._mark_failed(order_id=order_id, error=f"alpaca_exception:{e}")
            _console_print(f"[worker] Alpaca order exception: strategy_id={strategy_id} pending_id={order_id} err={e}")
            _notify_live_best_effort(status="failed", error=str(e))
            append_strategy_log(strategy_id, "error", f"Alpaca order exception ({symbol} {signal_type}): {e}")

    def _execute_mt5_order(
        self,
        *,
        order_id: int,
        order_row: Dict[str, Any],
        payload: Dict[str, Any],
        client,  # MT5Client instance
        strategy_id: int,
        exchange_config: Dict[str, Any],
        _notify_live_best_effort,
        _console_print,
    ) -> None:
        """
        Execute order via MetaTrader 5 for forex trading.

        Simplified flow compared to crypto (no maker->market fallback):
        - Place market order directly
        - Wait for fill
        - Record trade
        """
        signal_type = payload.get("signal_type") or order_row.get("signal_type")
        symbol = payload.get("symbol") or order_row.get("symbol")
        amount = float(payload.get("amount") or order_row.get("amount") or 0.0)
        ref_price = float(payload.get("ref_price") or payload.get("price") or order_row.get("price") or 0.0)

        sig = str(signal_type or "").strip().lower()

        # Map signal to action (include stop/tp/trailing aliases)
        if sig in ("open_long", "add_long"):
            action = "buy"
        elif sig in ("close_long", "reduce_long", "close_long_stop", "close_long_profit", "close_long_trailing"):
            action = "sell"
        elif sig in ("open_short", "add_short"):
            action = "sell"
        elif sig in ("close_short", "reduce_short", "close_short_stop", "close_short_profit", "close_short_trailing"):
            action = "buy"
        else:
            self._mark_failed(order_id=order_id, error=f"mt5_unsupported_signal:{signal_type}")
            _console_print(f"[worker] MT5 order rejected: strategy_id={strategy_id} pending_id={order_id} unsupported signal {signal_type}")
            _notify_live_best_effort(status="failed", error=f"mt5_unsupported_signal:{signal_type}")
            return

        try:
            # Ensure client is connected before placing order
            if not client.connected:
                logger.warning(f"MT5 client not connected, attempting reconnect: strategy_id={strategy_id}, pending_id={order_id}")
                if not client.connect():
                    self._mark_failed(order_id=order_id, error="mt5_connection_failed")
                    _console_print(f"[worker] MT5 connection failed: strategy_id={strategy_id} pending_id={order_id}")
                    _notify_live_best_effort(status="failed", error="mt5_connection_failed")
                    return
            
            # Normalize symbol before placing order (MT5 requires specific format)
            from app.services.mt5_trading.symbols import normalize_symbol
            normalized_symbol = normalize_symbol(symbol)
            
            # Place market order via MT5
            result = client.place_market_order(
                symbol=normalized_symbol,
                side=action,
                volume=amount,
                comment="QuantDinger",
            )

            if not result.success:
                self._mark_failed(order_id=order_id, error=f"mt5_order_failed:{result.message}")
                _console_print(f"[worker] MT5 order failed: strategy_id={strategy_id} pending_id={order_id} err={result.message}")
                _notify_live_best_effort(status="failed", error=f"mt5_order_failed:{result.message}")
                append_strategy_log(strategy_id, "error", f"MT5 order failed ({symbol} {signal_type}): {result.message}")
                return

            filled = float(result.filled or 0.0)
            avg_price = float(result.price or 0.0)
            exchange_order_id = str(result.order_id or "")

            if avg_price <= 0 and ref_price > 0:
                logger.warning(f"[worker] MT5 order avg_price=0, using ref_price={ref_price} as fallback: strategy_id={strategy_id} pending_id={order_id}")
                avg_price = ref_price
            if filled <= 0:
                logger.warning(f"[worker] MT5 order filled=0, using amount={amount} as fallback: strategy_id={strategy_id} pending_id={order_id}")
                filled = amount

            executed_at = int(time.time())

            # Mark order as sent
            self._mark_sent(
                order_id=order_id,
                note="mt5_order_sent",
                exchange_id="mt5",
                exchange_order_id=exchange_order_id,
                exchange_response_json=json.dumps(result.raw or {}, ensure_ascii=False),
                filled=filled,
                avg_price=avg_price,
                executed_at=executed_at,
            )
            _console_print(f"[worker] MT5 order sent: strategy_id={strategy_id} pending_id={order_id} order_id={exchange_order_id} filled={filled} avg={avg_price}")

            # Record trade and update position
            try:
                if filled > 0 and avg_price > 0:
                    logger.info(
                        f"MT5 record begin: pending_id={order_id} strategy_id={strategy_id} symbol={symbol} "
                        f"signal={signal_type} filled={filled} avg_price={avg_price}"
                    )
                    profit, _pos = apply_fill_to_local_position(
                        strategy_id=strategy_id,
                        symbol=str(symbol),
                        signal_type=str(signal_type),
                        filled=filled,
                        avg_price=avg_price,
                    )
                    record_trade(
                        strategy_id=strategy_id,
                        symbol=str(symbol),
                        trade_type=str(signal_type),
                        price=avg_price,
                        amount=filled,
                        commission=0.0,  # MT5 commission is complex, skip for now
                        commission_ccy="USD",
                        profit=profit,
                    )
                    logger.info(f"MT5 record done: pending_id={order_id} strategy_id={strategy_id} symbol={symbol}")
                    _pstr = f", profit={profit:.4f}" if profit is not None else ""
                    append_strategy_log(
                        strategy_id, "trade",
                        f"Trade executed: {signal_type} {symbol} filled={filled:.6f} @ {avg_price:.6f}{_pstr} (exchange=mt5)",
                    )
            except Exception as e:
                logger.warning(f"MT5 record_trade/update_position failed: pending_id={order_id}, err={e}")

            # Notify success
            _notify_live_best_effort(
                status="sent",
                exchange_id="mt5",
                exchange_order_id=exchange_order_id,
                price_hint=avg_price,
                amount_hint=filled,
            )

        except Exception as e:
            logger.error(f"MT5 order execution failed: pending_id={order_id}, strategy_id={strategy_id}, err={e}")
            self._mark_failed(order_id=order_id, error=f"mt5_exception:{e}")
            _console_print(f"[worker] MT5 order exception: strategy_id={strategy_id} pending_id={order_id} err={e}")
            _notify_live_best_effort(status="failed", error=str(e))
            append_strategy_log(strategy_id, "error", f"MT5 order exception ({symbol} {signal_type}): {e}")

    def _mark_sent(
        self,
        order_id: int,
        note: str = "",
        exchange_id: str = "",
        exchange_order_id: str = "",
        exchange_response_json: str = "",
        filled: float = 0.0,
        avg_price: float = 0.0,
        executed_at: Optional[int] = None,
    ) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            # Use NOW() for timestamp fields; executed_at is set to NOW() if provided, else NULL
            cur.execute(
                """
                UPDATE pending_orders
                SET status = 'sent',
                    last_error = %s,
                    dispatch_note = %s,
                    sent_at = NOW(),
                    executed_at = CASE WHEN %s THEN NOW() ELSE NULL END,
                    exchange_id = %s,
                    exchange_order_id = %s,
                    exchange_response_json = %s,
                    filled = %s,
                    avg_price = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    "",
                    str(note or ""),
                    executed_at is not None,  # Boolean flag for CASE WHEN
                    str(exchange_id or ""),
                    str(exchange_order_id or ""),
                    str(exchange_response_json or ""),
                    float(filled or 0.0),
                    float(avg_price or 0.0),
                    int(order_id),
                ),
            )
            db.commit()
            cur.close()

    def _mark_failed(self, order_id: int, error: str) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE pending_orders
                SET status = 'failed',
                    last_error = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (str(error or "failed"), int(order_id)),
            )
            db.commit()
            cur.close()

    def _mark_deferred(self, order_id: int, reason: str) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE pending_orders
                SET status = 'deferred',
                    last_error = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (str(reason or "deferred"), int(order_id)),
            )
            db.commit()
            cur.close()


