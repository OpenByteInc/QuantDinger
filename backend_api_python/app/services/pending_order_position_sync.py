"""Exchange position reconciliation for the pending-order runtime."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from app.services.exchange_execution import (
    load_strategy_configs,
    resolve_exchange_config,
    safe_exchange_config_for_log,
)
from app.services.live_trading.account_positions import (
    account_legs_from_exchange_maps,
    sync_account_positions,
)
from app.services.live_trading.base import is_file_descriptor_exhausted
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.factory import create_client
from app.services.live_trading.gate import GateUsdtFuturesClient
from app.services.live_trading.leg_context import credential_id_from_exchange_config
from app.services.live_trading.okx import OkxClient
from app.services.live_trading.records import normalize_strategy_symbol, strategy_allowed_symbols
from app.services.live_trading.strategy_position_sync import strategy_uses_fill_ledger
from app.services.pending_orders.position_sync_cache import (
    exchange_sync_backoff_sec,
    get_position_sync_snapshot,
    is_exchange_rate_limit_error,
    is_exchange_sync_backoff,
    position_sync_cache_key,
    set_exchange_sync_backoff,
    set_position_sync_snapshot,
)
from app.services.strategy_lifecycle import (
    auto_stop_live_strategy,
    is_fatal_exchange_error,
    should_skip_position_sync,
)
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

IBKRClient = None
AlpacaClient = None
FutuClient = None
_POSITION_SYNC_FD_BACKOFF_UNTIL = 0.0


def _position_sync_fd_backoff_sec() -> float:
    try:
        return max(30.0, float(os.getenv("POSITION_SYNC_FD_BACKOFF_SEC", "90")))
    except Exception:
        return 90.0


def _is_position_sync_fd_backoff_active() -> bool:
    return time.time() < float(_POSITION_SYNC_FD_BACKOFF_UNTIL or 0.0)


def _activate_position_sync_fd_backoff(reason: str) -> None:
    global _POSITION_SYNC_FD_BACKOFF_UNTIL
    seconds = _position_sync_fd_backoff_sec()
    _POSITION_SYNC_FD_BACKOFF_UNTIL = time.time() + seconds
    logger.error(
        "[PositionSync] process file descriptors exhausted; pausing exchange position sync for %ss. error=%s",
        int(seconds),
        reason,
    )


class PendingOrderPositionSyncMixin:
    def _sync_positions_best_effort(self, target_strategy_id: Optional[int] = None) -> None:
        """
        Best-effort reconciliation:
        - If exchange position is flat, delete local row from qd_strategy_positions.
        - If exchange position size differs, update local size (optional best-effort).

        This prevents "ghost positions" when positions are closed externally on the exchange.
        """
        if _is_position_sync_fd_backoff_active():
            logger.debug("[PositionSync] skipped: file-descriptor backoff active")
            return

        # 1) Load local positions (filtered if target_strategy_id is provided).
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

        # Removed early return to allow syncing active strategies even if local DB is empty.
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
        # If target_strategy_id is set but sid_to_rows is empty, add it so the
        # logic below still enters and calls client.get_positions().
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
                bot_type = str(
                    sc.get("bot_type")
                    or (sc.get("trading_config") or {}).get("bot_type")
                    or ""
                ).strip().lower()
                if strategy_uses_fill_ledger(sc):
                    logger.debug(
                        "[PositionSync] Strategy %s skipped: fill-ledger strategy (L3)",
                        sid,
                    )
                    continue
                if exec_mode != "live":
                    logger.debug(f"[PositionSync] Strategy {sid} skipped: execution_mode='{exec_mode}'")
                    continue
                sync_user_id = int(sc.get("user_id") or 1)
                exchange_config = resolve_exchange_config(sc.get("exchange_config") or {}, user_id=sync_user_id)
                safe_cfg = safe_exchange_config_for_log(exchange_config)

                # Signal mode may not have an exchange configured.
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
                allowed_symbols = strategy_allowed_symbols(sc)

                # Lazy import IBKR / Alpaca clients here so the elif chain
                # below can rely on isinstance() checks without paying the import
                # cost on systems that don't ship those broker libs.
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

                global FutuClient
                if FutuClient is None:
                    try:
                        from app.services.futu_trading import FutuClient as _FutuClient
                        FutuClient = _FutuClient
                    except ImportError:
                        pass

                cache_key = position_sync_cache_key(sync_user_id, exchange_id, market_type, exchange_config)
                cached_snap = get_position_sync_snapshot(cache_key)
                exch_size: Dict[str, Dict[str, float]] = {}
                exch_entry_price: Dict[str, Dict[str, float]] = {}
                exch_inst_id: Dict[str, Dict[str, str]] = {}

                if cached_snap is not None:
                    exch_size, exch_entry_price, exch_inst_id = cached_snap
                else:
                    if is_exchange_sync_backoff(cache_key):
                        logger.warning(
                            "[PositionSync] Strategy %s skipped: %s sync backoff active (key=%s)",
                            sid,
                            exchange_id,
                            cache_key,
                        )
                        continue

                    # Try to create the client; skip strategies with invalid exchange config.
                    try:
                        client = create_client(
                            exchange_config,
                            market_type=market_type,
                            need_quote=str(exchange_id or "").strip().lower() != "futu",
                        )
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
                            if is_file_descriptor_exhausted(e):
                                set_exchange_sync_backoff(cache_key, seconds=_position_sync_fd_backoff_sec())
                                _activate_position_sync_fd_backoff(msg)
                                return
                            if is_fatal_exchange_error(msg):
                                logger.error(f"[PositionSync] Strategy {sid} fatal auth error; auto-stopping. error={msg}")
                                auto_stop_live_strategy(int(sid), msg, source="position_sync_binance")
                                continue
                            if is_exchange_rate_limit_error(msg):
                                set_exchange_sync_backoff(cache_key)
                                logger.error(
                                    "[PositionSync] Binance rate limit for key=%s; backing off %ss. error=%s",
                                    cache_key,
                                    int(exchange_sync_backoff_sec()),
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
                            if is_file_descriptor_exhausted(e):
                                set_exchange_sync_backoff(cache_key, seconds=_position_sync_fd_backoff_sec())
                                _activate_position_sync_fd_backoff(msg)
                                return
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
                                if pos_side == "long":
                                    side = "long"
                                elif pos_side == "short":
                                    side = "short"
                                elif pos_side == "net":
                                    side = "long" if pos > 0 else "short"
                                else:
                                    side = "long" if pos > 0 else "short"
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
                                exch_inst_id.setdefault(hb_sym, {"long": "", "short": ""})[side] = inst_id

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
                        # Bybit v5 requires symbol or settleCoin; use USDT for full linear book.
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
                                from app.services.live_trading.position_row_parse import (
                                    infer_position_side_from_row,
                                )
                                side = infer_position_side_from_row(p)
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

                    elif IBKRClient is not None and isinstance(client, IBKRClient):
                        # IBKR US-stock positions. `quantity` is signed: >0 = long, <0 = short.
                        # Mirror both sides because IBKR stock positions are signed.
                        try:
                            positions = client.get_positions() or []
                        except Exception as e:
                            msg = str(e)
                            if is_file_descriptor_exhausted(e):
                                set_exchange_sync_backoff(cache_key, seconds=_position_sync_fd_backoff_sec())
                                _activate_position_sync_fd_backoff(msg)
                                return
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
                        # "BTC/USD" is the same format the strategy stores, so no extra
                        # normalization is needed here.
                        try:
                            positions = client.get_positions() or []
                        except Exception as e:
                            if is_file_descriptor_exhausted(e):
                                set_exchange_sync_backoff(cache_key, seconds=_position_sync_fd_backoff_sec())
                                _activate_position_sync_fd_backoff(str(e))
                                return
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

                    elif FutuClient is not None and isinstance(client, FutuClient):
                        try:
                            positions = client.get_positions() or []
                        except Exception as e:
                            msg = str(e)
                            if is_file_descriptor_exhausted(e):
                                set_exchange_sync_backoff(cache_key, seconds=_position_sync_fd_backoff_sec())
                                _activate_position_sync_fd_backoff(msg)
                                return
                            if is_fatal_exchange_error(msg):
                                logger.error(
                                    "[PositionSync] Strategy %s Futu fatal error; auto-stopping. error=%s",
                                    sid,
                                    msg,
                                )
                                auto_stop_live_strategy(int(sid), msg, source="position_sync_futu")
                            else:
                                logger.error(
                                    f"[PositionSync] Strategy {sid} Futu get_positions failed: {e}",
                                    exc_info=True,
                                )
                            continue
                        finally:
                            disconnect = getattr(client, "disconnect", None)
                            if callable(disconnect):
                                try:
                                    disconnect()
                                except Exception:
                                    pass
                        if isinstance(positions, list):
                            for p in positions:
                                if not isinstance(p, dict):
                                    continue
                                sym = str(p.get("symbol") or p.get("futu_code") or "").strip()
                                try:
                                    qty = float(p.get("quantity") or p.get("qty") or 0.0)
                                except Exception:
                                    qty = 0.0
                                try:
                                    avg = float(p.get("avgCost") or p.get("avg_cost") or 0.0)
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

                    elif market_type == "spot":
                        from app.services.live_trading.spot_wallet_snapshot import list_spot_wallet_positions

                        try:
                            spot_rows = list_spot_wallet_positions(client) or []
                        except Exception as e:
                            if is_file_descriptor_exhausted(e):
                                set_exchange_sync_backoff(cache_key, seconds=_position_sync_fd_backoff_sec())
                                _activate_position_sync_fd_backoff(str(e))
                                return
                            logger.error(
                                f"[PositionSync] Strategy {sid} spot wallet sync failed: {e}",
                                exc_info=True,
                            )
                            continue
                        for row in spot_rows:
                            if not isinstance(row, dict):
                                continue
                            sym = normalize_strategy_symbol(str(row.get("symbol") or "")) or str(
                                row.get("symbol") or ""
                            ).strip()
                            side = str(row.get("side") or "long").strip().lower()
                            try:
                                sz = float(row.get("size") or 0.0)
                            except Exception:
                                sz = 0.0
                            if not sym or side not in ("long", "short") or sz <= 1e-12:
                                continue
                            exch_size.setdefault(sym, {"long": 0.0, "short": 0.0})[side] = sz
                            ep = float(row.get("entry_price") or 0.0)
                            if ep > 0:
                                exch_entry_price.setdefault(sym, {"long": 0.0, "short": 0.0})[side] = ep
                            iid = str(row.get("inst_id") or "")
                            if iid:
                                exch_inst_id.setdefault(sym, {"long": "", "short": ""})[side] = iid

                    else:
                        logger.debug(f"position sync: skip unsupported market/client: sid={sid}, cfg={safe_cfg}, market_type={market_type}, client={type(client)}")
                        continue

                    set_position_sync_snapshot(cache_key, exch_size, exch_entry_price, exch_inst_id)
                    try:
                        cred_id = credential_id_from_exchange_config(exchange_config)
                        legs = account_legs_from_exchange_maps(
                            exch_size, exch_entry_price, exch_inst_id
                        )
                        sync_account_positions(
                            user_id=int(sync_user_id),
                            credential_id=cred_id,
                            exchange_id=str(exchange_id or ""),
                            market_type=str(market_type or "swap"),
                            legs=legs,
                        )
                    except Exception as l1_err:
                        logger.warning("[PositionSync] L1 account sync failed key=%s: %s", cache_key, l1_err)

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
                    logger.debug(f"[PositionSync] Strategy {sid} ({safe_cfg.get('exchange_id', 'unknown')}) positions: {'; '.join(pos_summary_parts)}")
                else:
                    logger.debug(f"[PositionSync] Strategy {sid} ({safe_cfg.get('exchange_id', 'unknown')}) has NO positions on exchange.")

                # Keep exchange truth in L1 only. Strategy positions (L3) must be
                # produced by that strategy's own fills, otherwise two live
                # strategies sharing ETH/USDT would both inherit the same
                # exchange account position.
            except Exception as e:
                msg = str(e)
                if is_file_descriptor_exhausted(e):
                    _activate_position_sync_fd_backoff(msg)
                    return
                if is_fatal_exchange_error(msg):
                    logger.error(f"[PositionSync] Strategy {sid} fatal error; auto-stopping. error={msg}", exc_info=True)
                    auto_stop_live_strategy(int(sid), msg, source="position_sync")
                else:
                    logger.error(f"position sync: strategy_id={sid} failed: {e}", exc_info=True)
