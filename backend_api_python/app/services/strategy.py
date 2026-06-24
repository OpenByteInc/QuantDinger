import os
import time
import json
import threading
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.utils.logger import get_logger
from app.utils.db import get_db_connection
from app.services.symbol_name import normalize_crypto_symbol
from app.services.exchange_execution import coalesce_exchange_config_from_payload

logger = get_logger(__name__)

_strategy_service_singleton: Optional["StrategyService"] = None


def get_strategy_service() -> "StrategyService":
    """Return a shared strategy service for callers that use service factories."""
    global _strategy_service_singleton
    if _strategy_service_singleton is None:
        _strategy_service_singleton = StrategyService()
    return _strategy_service_singleton


def _normalize_cross_sectional_symbol_list(
    symbol_list: List[Any],
    market_category: str,
) -> List[str]:
    """Normalize universe entries to ``Market:SYMBOL`` (Crypto symbols canonicalized)."""
    out: List[str] = []
    default_market = (market_category or "Crypto").strip() or "Crypto"
    for entry in symbol_list or []:
        raw = str(entry or "").strip()
        if not raw:
            continue
        if ":" in raw:
            mkt, sym = raw.split(":", 1)
            mkt = (mkt or default_market).strip() or default_market
        else:
            mkt, sym = default_market, raw
        sym = sym.strip()
        if not sym:
            continue
        if mkt == "Crypto":
            sym = normalize_crypto_symbol(sym)
        out.append(f"{mkt}:{sym}")
    return out


def _apply_cross_sectional_trading_config(
    trading_config: Dict[str, Any],
    *,
    cs_strategy_type: str,
    symbol_list: List[Any],
    portfolio_size: Any,
    long_ratio: Any,
    rebalance_frequency: Any,
    market_category: str,
    market_type: str,
) -> Dict[str, Any]:
    """Validate and persist cross-sectional fields inside trading_config."""
    tc = dict(trading_config or {})
    cs = (cs_strategy_type or "single").strip().lower()
    if cs != "cross_sectional":
        tc["cs_strategy_type"] = "single"
        return tc

    norm_list = _normalize_cross_sectional_symbol_list(symbol_list, market_category)
    if len(norm_list) < 2:
        raise ValueError("cross_sectional requires at least 2 symbols in symbol_list")

    try:
        psize = int(portfolio_size or 10)
    except (TypeError, ValueError):
        psize = 10
    if psize < 1:
        raise ValueError("portfolio_size must be >= 1")
    if psize > len(norm_list):
        raise ValueError("portfolio_size cannot exceed the number of symbols in symbol_list")

    try:
        lr = float(long_ratio if long_ratio is not None else 0.5)
    except (TypeError, ValueError):
        lr = 0.5
    lr = max(0.0, min(1.0, lr))
    mt = (market_type or "swap").strip().lower()
    if mt == "spot" and lr < 1.0:
        lr = 1.0

    freq = (rebalance_frequency or "daily").strip().lower()
    if freq not in ("daily", "weekly", "monthly"):
        freq = "daily"

    tc["cs_strategy_type"] = "cross_sectional"
    tc["symbol_list"] = norm_list
    tc["portfolio_size"] = psize
    tc["long_ratio"] = lr
    tc["rebalance_frequency"] = freq
    tc["strategy_type"] = "cross_sectional"
    primary_sym = norm_list[0].split(":", 1)[-1]
    tc["symbol"] = primary_sym
    return tc


def _apply_default_strict_mode(trading_config: Dict[str, Any]) -> Dict[str, Any]:
    """Default new strategies to strict (backtest-aligned) live execution."""
    tc = dict(trading_config or {})
    if "strict_mode" not in tc and "strictMode" not in tc:
        tc["strict_mode"] = True
    return tc


def _strip_legacy_risk_pct_basis(trading_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    SL / TP / trailing percentages are unified as the underlying's % price
    move directly (no leverage scaling at trigger time). The legacy
    ``risk_pct_basis`` toggle is removed; any stale value on incoming
    payloads is discarded so it cannot re-introduce a margin-vs-price
    branch on the server.
    """
    tc = dict(trading_config or {})
    tc.pop("risk_pct_basis", None)
    tc.pop("riskPctBasis", None)
    return tc


def _apply_risk_flat_from_indicator_code(
    trading_config: Dict[str, Any],
    indicator_config: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    When indicator code declares @strategy risk keys, persist matching flat
    trading_config.*_pct fields (percent units) so DB/UI/backtest-center stay
    aligned with the same semantics as live runtime code parsing.
    """
    ic = indicator_config if isinstance(indicator_config, dict) else {}
    code = ic.get("indicator_code") or ""
    if not str(code).strip():
        return dict(trading_config or {})
    from app.services.indicator_params import StrategyConfigParser

    flat = StrategyConfigParser.to_trading_config_risk_flat(str(code))
    if not flat:
        return dict(trading_config or {})
    tc = dict(trading_config or {})
    tc.update(flat)
    return tc


# Note: broker / market / market_type / trade_direction / bot_type compatibility
# rules used to live in this file as scattered if-blocks plus a local
# _enforce_long_only_for_stock_brokers helper. They have been moved into
# `app.services.broker_market_policy.validate_strategy_config`, which is the
# single source of truth shared by create/update/batch CRUD, the live worker,
# and the frontend (via GET /api/policy/broker-market). Do not re-introduce
# inline broker checks here.


class StrategyService:
    """Strategy service."""
    
    # Class variable: limit connection test concurrency
    _connection_test_semaphore = threading.Semaphore(5)
    
    def __init__(self):
        # Local deployment: do not use encryption/decryption.
        pass
        
    def get_running_strategies(self) -> List[Dict[str, Any]]:
        """Get all running strategies (ID only)"""
        try:
            with get_db_connection() as db:
                cursor = db.cursor()
                query = "SELECT id FROM qd_strategies_trading WHERE status = 'running'"
                cursor.execute(query)
                results = cursor.fetchall()
                cursor.close()
                return [row['id'] for row in results]
        except Exception as e:
            logger.error(f"Failed to fetch running strategies: {str(e)}")
            return []

    def get_running_strategies_with_type(self) -> List[Dict[str, Any]]:
        """Get all running strategies (with type info)"""
        try:
            with get_db_connection() as db:
                cursor = db.cursor()
                # Assume qd_strategies_trading table has strategy_type field
                # If not, may need join query or determine from other fields
                # Here we assume table structure is updated
                query = "SELECT id, strategy_type FROM qd_strategies_trading WHERE status = 'running'"
                cursor.execute(query)
                results = cursor.fetchall()
                cursor.close()
                
                strategies = [{'id': row['id'], 'strategy_type': row.get('strategy_type', '')} for row in results]
                logger.info(f"Found {len(strategies)} running strategies: {strategies}")
                return strategies
                
        except Exception as e:
            logger.error(f"Failed to fetch running strategies: {str(e)}")
            return []
    
    def get_exchange_symbols(self, exchange_config: Dict[str, Any], user_id: int = 1) -> Dict[str, Any]:
        """
        Get exchange trading pairs (no API Key required)
        """
        try:
            from app.services.exchange_execution import resolve_exchange_config

            resolved = resolve_exchange_config(exchange_config or {}, user_id=int(user_id or 1))
            exchange_id = (resolved.get("exchange_id") or exchange_config.get("exchange_id") or "")
            proxies = resolved.get("proxies") or exchange_config.get("proxies")

            if not str(exchange_id).strip():
                return {'success': False, 'message': 'Please select an exchange', 'symbols': []}

            ex = str(exchange_id or "").strip().lower()

            # IBKR / MT5 are not CCXT exchanges; do not fall through to crypto symbol list.
            if ex in ("ibkr", "mt5"):
                from app.utils.local_brokers import desktop_broker_cloud_reject_message, local_desktop_brokers_allowed

                if not local_desktop_brokers_allowed():
                    return {"success": False, "message": desktop_broker_cloud_reject_message(), "symbols": []}

                if ex == "ibkr":
                    # US tickers for convenience; full universe is broker-side.
                    common = [
                        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AMD", "NFLX", "INTC",
                        "SPY", "QQQ", "IWM", "DIA", "VOO", "BABA", "JD", "PDD", "COIN", "MSTR",
                    ]
                    return {
                        "success": True,
                        "message": "IBKR: 以下为常用美股代码示例，也可手动输入其他在 TWS 中可交易的代码。",
                        "symbols": common,
                    }

                if ex == "mt5":
                    try:
                        from app.services.live_trading.factory import create_mt5_client

                        mt5_client = create_mt5_client(resolved)
                        if mt5_client and mt5_client.connected:
                            infos = mt5_client.get_symbols(group="*") or []
                            names: List[str] = []
                            for info in infos:
                                if isinstance(info, dict):
                                    n = str(info.get("name") or "").strip()
                                    if n:
                                        names.append(n)
                            names = sorted(set(names))[:2000]
                            return {
                                "success": True,
                                "message": f"MT5: {len(names)} symbols from terminal",
                                "symbols": names,
                            }
                        return {
                            "success": False,
                            "message": "MT5 未连接：请确认本机已启动 MT5 终端且账号配置正确。",
                            "symbols": [],
                        }
                    except Exception as e:
                        logger.error(f"MT5 get_symbols failed: {e}")
                        return {
                            "success": False,
                            "message": f"MT5 品种列表失败: {e}",
                            "symbols": [],
                        }

            # For these exchanges, prefer direct REST (no ccxt), aligned with local live-trading design.
            if ex in ("bybit", "coinbaseexchange", "coinbase_exchange", "kraken", "gate"):
                import requests

                def _req_json(url: str) -> Any:
                    r = requests.get(url, timeout=15, proxies=proxies)
                    r.raise_for_status()
                    return r.json()

                symbols: List[str] = []
                market_type = str(exchange_config.get("market_type") or exchange_config.get("defaultType") or "spot").strip().lower()
                if market_type in ("futures", "future", "perp", "perpetual"):
                    market_type = "swap"
                if ex == "bybit":
                    base = str(exchange_config.get("base_url") or exchange_config.get("baseUrl") or "https://api.bybit.com").rstrip("/")
                    cat = "spot" if market_type == "spot" else "linear"
                    j = _req_json(f"{base}/v5/market/instruments-info?category={cat}")
                    lst = (((j.get("result") or {}).get("list")) if isinstance(j, dict) else None) or []
                    if isinstance(lst, list):
                        for it in lst:
                            if not isinstance(it, dict):
                                continue
                            sym = str(it.get("symbol") or "")
                            status = str(it.get("status") or "").lower()
                            if not sym or (status and status not in ("trading", "tradable", "online")):
                                continue
                            if sym.endswith("USDT") and len(sym) > 4:
                                symbols.append(f"{sym[:-4]}/USDT")
                    symbols = sorted(list(set(symbols)))
                    return {'success': True, 'message': f'Success, {len(symbols)} trading pairs', 'symbols': symbols}

                if ex in ("coinbaseexchange", "coinbase_exchange"):
                    base = str(exchange_config.get("base_url") or exchange_config.get("baseUrl") or "https://api.exchange.coinbase.com").rstrip("/")
                    j = _req_json(f"{base}/products")
                    if isinstance(j, list):
                        for it in j:
                            if not isinstance(it, dict):
                                continue
                            if str(it.get("status") or "").lower() not in ("online", ""):
                                continue
                            base_ccy = str(it.get("base_currency") or "").upper()
                            quote_ccy = str(it.get("quote_currency") or "").upper()
                            if quote_ccy == "USDT" and base_ccy:
                                symbols.append(f"{base_ccy}/USDT")
                    symbols = sorted(list(set(symbols)))
                    return {'success': True, 'message': f'Success, {len(symbols)} trading pairs', 'symbols': symbols}

                if ex == "kraken":
                    if market_type == "spot":
                        j = _req_json("https://api.kraken.com/0/public/AssetPairs")
                        res = (j.get("result") if isinstance(j, dict) else None) or {}
                        if isinstance(res, dict):
                            for _k, v in res.items():
                                if not isinstance(v, dict):
                                    continue
                                wsname = str(v.get("wsname") or "")
                                if not wsname or "/" not in wsname:
                                    continue
                                base_ccy, quote_ccy = wsname.split("/", 1)
                                if str(quote_ccy).upper() == "USDT":
                                    symbols.append(f"{str(base_ccy).upper()}/USDT")
                    else:
                        base = str(exchange_config.get("futures_base_url") or exchange_config.get("futuresBaseUrl") or "https://futures.kraken.com").rstrip("/")
                        j = _req_json(f"{base}/derivatives/api/v3/instruments")
                        instruments = j.get("instruments") if isinstance(j, dict) else None
                        if isinstance(instruments, list):
                            for it in instruments:
                                if not isinstance(it, dict):
                                    continue
                                sym = str(it.get("symbol") or "")
                                typ = str(it.get("type") or "").lower()
                                if sym and ("perpetual" in typ or typ.startswith("pf") or sym.startswith("PF_")):
                                    symbols.append(sym)
                    symbols = sorted(list(set(symbols)))
                    return {'success': True, 'message': f'Success, {len(symbols)} trading pairs', 'symbols': symbols}

                if ex == "gate":
                    base = str(exchange_config.get("base_url") or exchange_config.get("baseUrl") or "https://api.gateio.ws").rstrip("/")
                    if market_type == "spot":
                        j = _req_json(f"{base}/api/v4/spot/currency_pairs")
                        if isinstance(j, list):
                            for it in j:
                                if not isinstance(it, dict):
                                    continue
                                if str(it.get("trade_status") or "").lower() not in ("tradable", "trading", ""):
                                    continue
                                base_ccy = str(it.get("base") or "").upper()
                                quote_ccy = str(it.get("quote") or "").upper()
                                if quote_ccy == "USDT" and base_ccy:
                                    symbols.append(f"{base_ccy}/USDT")
                    else:
                        j = _req_json(f"{base}/api/v4/futures/usdt/contracts")
                        if isinstance(j, list):
                            for it in j:
                                if not isinstance(it, dict):
                                    continue
                                name = str(it.get("name") or it.get("contract") or "")
                                if name and name.upper().endswith("_USDT"):
                                    symbols.append(name.replace("_", "/"))
                    symbols = sorted(list(set(symbols)))
                    return {'success': True, 'message': f'Success, {len(symbols)} trading pairs', 'symbols': symbols}

            import ccxt
            
            # Create exchange instance (public only)
            exchange_class = getattr(ccxt, exchange_id, None)
            if not exchange_class:
                return {'success': False, 'message': f'Unsupported exchange: {exchange_id}', 'symbols': []}
            
            exchange_config_dict = {
                'enableRateLimit': True,
                'options': {'defaultType': 'swap'}  # Default to swap
            }
            if proxies:
                exchange_config_dict['proxies'] = proxies
            
            exchange = exchange_class(exchange_config_dict)
            markets = exchange.load_markets()
            
            symbols = []
            for symbol, market in markets.items():
                if market.get('active', False) and market.get('quote') == 'USDT':
                    symbols.append(symbol)
            
            symbols.sort()
            return {'success': True, 'message': f'Success, {len(symbols)} trading pairs', 'symbols': symbols}
            
        except Exception as e:
            logger.error(f"Failed to fetch symbols: {str(e)}")
            return {'success': False, 'message': f'Failed to get trading pairs: {str(e)}', 'symbols': []}
    
    def test_exchange_connection(self, exchange_config: Dict[str, Any], user_id: int = 1) -> Dict[str, Any]:
        """
        Test exchange connection via direct REST clients (no ccxt).

        Notes:
        - This is local-only; failures are returned as user-friendly messages.
        - We do not log secrets.
        """
        # Limit concurrency to protect CPU / rate limits
        with StrategyService._connection_test_semaphore:
            try:
                from app.services.exchange_execution import resolve_exchange_config, safe_exchange_config_for_log
                from app.services.live_trading.factory import create_client, exchange_demo_mode_enabled
                from app.services.live_trading.binance import BinanceFuturesClient
                from app.services.live_trading.binance_spot import BinanceSpotClient
                from app.services.live_trading.okx import OkxClient
                from app.services.live_trading.bitget import BitgetMixClient
                from app.services.live_trading.bitget_spot import BitgetSpotClient
                from app.services.live_trading.bybit import BybitClient
                from app.services.live_trading.coinbase_exchange import CoinbaseExchangeClient
                from app.services.live_trading.kraken import KrakenClient
                from app.services.live_trading.kraken_futures import KrakenFuturesClient
                from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
                from app.services.live_trading.htx import HtxClient

                resolved = resolve_exchange_config(exchange_config or {}, user_id=user_id)
                safe_cfg = safe_exchange_config_for_log(resolved)

                exchange_id = (resolved.get("exchange_id") or "").strip().lower()
                if not exchange_id:
                    return {'success': False, 'message': 'Missing exchange_id', 'data': None}

                if exchange_id in ("ibkr", "mt5"):
                    from app.utils.local_brokers import desktop_broker_cloud_reject_message, local_desktop_brokers_allowed

                    if not local_desktop_brokers_allowed():
                        return {
                            "success": False,
                            "message": desktop_broker_cloud_reject_message(),
                            "data": {"exchange": safe_cfg},
                        }

                # Handle MT5 (Forex) connection test
                if exchange_id == 'mt5':
                    # Validate that MT5 is only used for Forex market
                    market_category = str(resolved.get("market_category") or exchange_config.get("market_category") or "").strip()
                    if market_category and market_category != "Forex":
                        return {
                            'success': False,
                            'message': f'MT5 can only be used for Forex trading, but market_category is {market_category}. Please use MT5 only with Forex market.',
                            'data': {'exchange': safe_cfg}
                        }
                    
                    try:
                        from app.services.live_trading.factory import create_mt5_client
                        mt5_client = create_mt5_client(resolved)
                        if mt5_client and mt5_client.connected:
                            # Get account info if available
                            account_info = None
                            try:
                                account_info = mt5_client.get_account_info()
                            except Exception:
                                pass
                            return {
                                'success': True,
                                'message': 'MT5 connection successful',
                                'data': {
                                    'exchange': safe_cfg,
                                    'account': account_info
                                }
                            }
                        else:
                            return {
                                'success': False,
                                'message': 'Failed to connect to MT5. Please check credentials and ensure terminal is running.',
                                'data': {'exchange': safe_cfg}
                            }
                    except Exception as e:
                        error_msg = str(e)
                        return {
                            'success': False,
                            'message': f'MT5 connection failed: {error_msg}',
                            'data': {'exchange': safe_cfg}
                        }

                # Handle IBKR (US Stocks) connection test
                if exchange_id == 'ibkr':
                    market_category_ib = str(
                        resolved.get("market_category") or exchange_config.get("market_category") or ""
                    ).strip()
                    if market_category_ib and market_category_ib != "USStock":
                        return {
                            "success": False,
                            "message": (
                                f"IBKR only supports US stocks (market_category=USStock), "
                                f"but got '{market_category_ib}'. Please switch market type to US Stock."
                            ),
                            "data": {"exchange": safe_cfg},
                        }
                    try:
                        from app.services.live_trading.factory import create_ibkr_client
                        ibkr_client = create_ibkr_client(resolved)
                        # create_ibkr_client already connects, so if it returns, connection is successful
                        if ibkr_client and ibkr_client.connected:
                            # Get account summary if available
                            account_summary = None
                            try:
                                account_summary = ibkr_client.get_account_summary()
                            except Exception:
                                pass
                            return {
                                'success': True,
                                'message': 'IBKR connection successful',
                                'data': {
                                    'exchange': safe_cfg,
                                    'account': account_summary
                                }
                            }
                        else:
                            return {
                                'success': False,
                                'message': 'Failed to connect to IBKR. Please check TWS/Gateway is running and credentials are correct.',
                                'data': {'exchange': safe_cfg}
                            }
                    except Exception as e:
                        error_msg = str(e)
                        return {
                            'success': False,
                            'message': f'IBKR connection failed: {error_msg}',
                            'data': {'exchange': safe_cfg}
                        }

                # Handle Alpaca (US Stocks + Crypto via REST, no local terminal needed)
                if exchange_id == 'alpaca':
                    market_category_al = str(
                        resolved.get("market_category") or exchange_config.get("market_category") or ""
                    ).strip()
                    if market_category_al and market_category_al not in ("USStock", "Crypto"):
                        return {
                            "success": False,
                            "message": (
                                f"Alpaca supports US stocks and Crypto only (market_category=USStock|Crypto), "
                                f"but got '{market_category_al}'."
                            ),
                            "data": {"exchange": safe_cfg},
                        }
                    try:
                        from app.services.live_trading.factory import create_alpaca_client
                        alpaca_client = create_alpaca_client(resolved)
                        if alpaca_client and alpaca_client.connected:
                            account_summary = None
                            try:
                                account_summary = alpaca_client.get_account_summary()
                            except Exception:
                                pass
                            return {
                                'success': True,
                                'message': 'Alpaca connection successful',
                                'data': {
                                    'exchange': safe_cfg,
                                    'account': account_summary
                                }
                            }
                        else:
                            return {
                                'success': False,
                                'message': 'Failed to connect to Alpaca. Please check API key/secret and paper/live mode.',
                                'data': {'exchange': safe_cfg}
                            }
                    except Exception as e:
                        return {
                            'success': False,
                            'message': f'Alpaca connection failed: {e}',
                            'data': {'exchange': safe_cfg}
                        }

                # Best-effort detect current egress IP (for Binance IP whitelist debugging).
                egress_ip = ""
                try:
                    import requests as _rq
                    egress_ip = str(_rq.get("https://ifconfig.me/ip", timeout=5).text or "").strip()
                except Exception:
                    egress_ip = ""

                def _validate_private(client, market_type: str):
                    if isinstance(client, BinanceFuturesClient):
                        return client.get_account()
                    if isinstance(client, BinanceSpotClient):
                        return client.get_account()
                    if isinstance(client, OkxClient):
                        return client.get_balance()
                    if isinstance(client, BitgetMixClient):
                        product_type = str(resolved.get("product_type") or resolved.get("productType") or "USDT-FUTURES")
                        return client.get_accounts(product_type=product_type)
                    if isinstance(client, BitgetSpotClient):
                        return client.get_assets()
                    if isinstance(client, BybitClient):
                        return client.get_wallet_balance()
                    if isinstance(client, CoinbaseExchangeClient):
                        return client.get_accounts()
                    if isinstance(client, KrakenClient):
                        return client.get_balance()
                    if isinstance(client, KrakenFuturesClient):
                        return client.get_accounts()
                    if isinstance(client, GateSpotClient):
                        return client.get_accounts()
                    if isinstance(client, GateUsdtFuturesClient):
                        return client.get_accounts()
                    if isinstance(client, HtxClient):
                        return client.get_balance()
                    return None

                def _probe_market_type(market_type: str):
                    try:
                        client = create_client(resolved, market_type=market_type)
                    except Exception as e:
                        return {
                            'success': False,
                            'message': f'Create client failed: {str(e)}',
                            'data': {
                                'exchange': safe_cfg,
                                'market_type': market_type,
                                'egress_ip': egress_ip,
                            },
                        }
                    client_kind = type(client).__name__

                    ok_public = False
                    try:
                        ok_public = bool(getattr(client, "ping")())
                    except Exception:
                        ok_public = False
                    if not ok_public:
                        return {
                            'success': False,
                            'message': f'Public ping failed: {exchange_id}',
                            'data': {
                                'exchange': safe_cfg,
                                'client': client_kind,
                                'market_type': market_type,
                                'egress_ip': egress_ip,
                                'base_url': getattr(client, "base_url", "") or "",
                            },
                        }

                    try:
                        priv_data = _validate_private(client, market_type)
                    except Exception as e:
                        msg = str(e)
                        if exchange_id == "binance" and ("-2015" in msg or "Invalid API-key, IP, or permissions" in msg):
                            alt_market_type = "spot" if market_type != "spot" else "swap"
                            alt_client_kind = ""
                            alt_base_url = ""
                            alt_ok = False
                            try:
                                alt_client = create_client(resolved, market_type=alt_market_type)
                                alt_client_kind = type(alt_client).__name__
                                alt_base_url = getattr(alt_client, "base_url", "") or ""
                                if isinstance(alt_client, (BinanceFuturesClient, BinanceSpotClient)):
                                    _ = alt_client.get_account()
                                    alt_ok = True
                            except Exception:
                                alt_ok = False

                            base_url = getattr(client, "base_url", "") or ""
                            is_demo = exchange_demo_mode_enabled(resolved)
                            hint = (
                                f"Binance auth failed (-2015). Verify: "
                                f"(1) IP whitelist includes this server egress IP={egress_ip or 'unknown'}, "
                                f"(2) API key permissions match market_type={market_type} "
                                f"(spot requires Spot permissions; swap requires Futures permissions), "
                                f"(3) you're using the correct key set for base_url={base_url or 'unknown'}."
                            )
                            if is_demo:
                                hint += (
                                    " Demo mode is enabled. You must use a Binance Testnet API key issued on "
                                    "testnet.binance.vision (Spot) or testnet.binancefuture.com (Futures); "
                                    "mainnet keys do NOT work on the testnet."
                                )
                            else:
                                hint += " Mainnet mode is enabled, so you must use binance.com mainnet keys."
                            if alt_ok:
                                hint += (
                                    f" Auto-check: your key works for market_type={alt_market_type} "
                                    f"(client={alt_client_kind}, base_url={alt_base_url or 'unknown'}) "
                                    f"but fails for market_type={market_type}. This is almost always a permissions/product mismatch."
                                )
                            msg = f"{msg} | {hint}"
                            hint_cn = (
                                "币安接口返回 -2015（密钥/IP/权限不匹配）。请逐项核对："
                                "① API Key 是否勾选与当前测试一致的业务（现货选现货权限，合约选合约/U 本位权限）；"
                                "② 若启用 IP 白名单，是否包含当前服务器出口 IP（见下方 egress_ip）；"
                                "③ base_url 与密钥环境一致（主网密钥配 api.binance.com / fapi.binance.com，"
                                "模拟盘需在 testnet.binance.vision / testnet.binancefuture.com 单独申请的 Testnet Key，主网 Key 在测试网无效）；"
                                "④ 无多余空格、复制完整 Secret。"
                            )
                            if alt_ok:
                                hint_cn += (
                                    f" 自动探测：同一密钥在 market_type={alt_market_type} 可通过，"
                                    f"当前选择的 {market_type} 与密钥权限不一致的可能性很大。"
                                )
                        else:
                            hint_cn = ""

                        fail_payload = {
                            'exchange': safe_cfg,
                            'client': client_kind,
                            'market_type': market_type,
                            'egress_ip': egress_ip,
                            'base_url': getattr(client, "base_url", "") or "",
                        }
                        if hint_cn:
                            fail_payload['hint_cn'] = hint_cn

                        return {
                            'success': False,
                            'message': f'Auth failed: {msg}',
                            'data': fail_payload,
                        }

                    return {
                        'success': True,
                        'message': 'Connection OK',
                        'data': {
                            'exchange': safe_cfg,
                            'client': client_kind,
                            'market_type': market_type,
                            'egress_ip': egress_ip,
                            'base_url': getattr(client, "base_url", "") or "",
                            'private': priv_data,
                        },
                    }

                raw_market_type = str(resolved.get("market_type") or resolved.get("defaultType") or "").strip().lower()
                if raw_market_type in ("futures", "future", "perp", "perpetual"):
                    raw_market_type = "swap"
                explicit_market_type = raw_market_type in ("spot", "swap")
                if exchange_id in ("coinbaseexchange", "coinbase_exchange"):
                    market_candidates = ["spot"]
                else:
                    market_candidates = [raw_market_type] if explicit_market_type else ["spot", "swap"]

                last_failure = None
                for market_type in market_candidates:
                    result = _probe_market_type(market_type)
                    if result.get('success'):
                        if not explicit_market_type and len(market_candidates) > 1:
                            result['message'] = f"Connection OK ({market_type})"
                        return result
                    last_failure = result

                if last_failure and not explicit_market_type and len(market_candidates) > 1:
                    tried = "/".join(market_candidates)
                    last_failure['message'] = f"{last_failure.get('message')}. Tried market_type={tried}"
                return last_failure or {'success': False, 'message': 'Connection failed', 'data': None}
            except Exception as e:
                logger.error(f"test_exchange_connection failed: {str(e)}")
                return {'success': False, 'message': f'Connection failed: {str(e)}', 'data': None}

    def get_strategy_type(self, strategy_id: int) -> str:
        """Get strategy type from DB."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    "SELECT strategy_type FROM qd_strategies_trading WHERE id = ?",
                    (strategy_id,)
                )
                row = cur.fetchone()
                cur.close()
            return (row or {}).get('strategy_type') or 'IndicatorStrategy'
        except Exception:
            return 'IndicatorStrategy'

    def update_strategy_status(self, strategy_id: int, status: str, user_id: int = None) -> bool:
        """Update strategy status. If user_id is provided, verify ownership."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                if user_id is not None:
                    cur.execute(
                        "UPDATE qd_strategies_trading SET status = ?, updated_at = NOW() WHERE id = ? AND user_id = ?",
                        (status, strategy_id, user_id)
                    )
                else:
                    cur.execute(
                        "UPDATE qd_strategies_trading SET status = ?, updated_at = NOW() WHERE id = ?",
                        (status, strategy_id)
                    )
                db.commit()
                cur.close()
            return True
        except Exception as e:
            logger.error(f"update_strategy_status failed: {e}")
            return False

    def _safe_json_loads(self, value: Any, default: Any):
        """Load JSON string into Python object (local deployment: plaintext only)."""
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        if not isinstance(value, str):
            return default
        s = value.strip()
        if not s:
            return default
        try:
            return json.loads(s)
        except Exception:
            return default

    def _to_float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return float(default or 0.0)

    def _to_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return int(default or 0)

    def _display_item(
        self,
        key: str,
        label_key: str,
        value: Any,
        value_type: str = 'text',
        value_key: str = '',
    ) -> Dict[str, Any]:
        return {
            'key': key,
            'label_key': label_key,
            'value': value,
            'value_type': value_type,
            'value_key': value_key or ''
        }

    @staticmethod
    def _sanitize_grid_trading_config(trading_config: Dict[str, Any]) -> Dict[str, Any]:
        tc = dict(trading_config or {})
        bot_type = str(tc.get('bot_type') or '').strip().lower()
        if bot_type != 'grid':
            return tc
        from app.services.grid.config import sanitize_grid_bot_params

        bp = tc.get('bot_params') if isinstance(tc.get('bot_params'), dict) else {}
        tc['bot_params'] = sanitize_grid_bot_params(bp)
        return tc

    def _build_bot_display(self, trading_config: Dict[str, Any]) -> Dict[str, Any]:
        tc = trading_config if isinstance(trading_config, dict) else {}
        bot_type = str(tc.get('bot_type') or '').strip().lower()
        if not bot_type:
            return {}

        params = tc.get('bot_params') if isinstance(tc.get('bot_params'), dict) else {}
        initial_capital = self._to_float(tc.get('initial_capital'), 0.0)

        display = {
            'bot_type': bot_type,
            'capital_label_key': 'trading-bot.wizard.initialCapital',
            'capital_value': initial_capital,
            'capital_value_type': 'usdt',
            'strategy_params': [],
            'risk_params': []
        }

        if bot_type == 'martingale':
            display['capital_label_key'] = 'trading-bot.martingale.totalBudget'
            multiplier = self._to_float(params.get('multiplier'), 2.0)
            max_layers = max(1, self._to_int(params.get('maxLayers'), 5))
            geo_sum = 0.0
            for i in range(max_layers):
                geo_sum += pow(multiplier, i)
            first_order = max(0.0, (initial_capital / geo_sum) if geo_sum > 0 else 0.0)

            display['strategy_params'] = [
                self._display_item('initialAmount', 'trading-bot.martingale.initialAmountAuto', first_order, 'usdt'),
                self._display_item('multiplier', 'trading-bot.martingale.multiplier', self._to_float(params.get('multiplier'), 2.0), 'number'),
                self._display_item('maxLayers', 'trading-bot.martingale.maxLayers', max_layers, 'number'),
                self._display_item('priceDropPct', 'trading-bot.martingale.priceDropTrigger', self._to_float(params.get('priceDropPct'), 0.0), 'percent'),
                self._display_item('takeProfitPct', 'trading-bot.martingale.avgEntryTakeProfit', self._to_float(params.get('takeProfitPct'), 0.0), 'percent'),
                self._display_item('stopLossPct', 'trading-bot.martingale.avgEntryStopLoss', self._to_float(params.get('stopLossPct'), 0.0), 'percent'),
                self._display_item('direction', 'trading-bot.martingale.direction', params.get('direction') or 'long', 'enum', f"trading-bot.martingale.{params.get('direction') or 'long'}"),
                self._display_item('waterfallProtection', 'trading-bot.martingale.waterfallProtection', bool(params.get('waterfallProtection', True)), 'boolean'),
                self._display_item('waterfallDropPct', 'trading-bot.martingale.waterfallDropPct', self._to_float(params.get('waterfallDropPct'), 0.04) * 100, 'percent'),
            ]
            if self._to_float(tc.get('max_daily_loss'), 0.0) > 0:
                display['risk_params'].append(
                    self._display_item('maxDailyLoss', 'trading-bot.martingale.maxDailyLossAdvanced', self._to_float(tc.get('max_daily_loss'), 0.0), 'usdt')
                )
            return display

        if bot_type == 'grid':
            direction = str(params.get('gridDirection') or 'neutral').strip().lower()
            grid_items = [
                self._display_item('upperPrice', 'trading-bot.grid.upperPrice', self._to_float(params.get('upperPrice'), 0.0), 'usdt'),
                self._display_item('lowerPrice', 'trading-bot.grid.lowerPrice', self._to_float(params.get('lowerPrice'), 0.0), 'usdt'),
                self._display_item('gridCount', 'trading-bot.grid.gridCount', self._to_int(params.get('gridCount'), 0), 'number'),
                self._display_item('amountPerGrid', 'trading-bot.grid.amountPerGrid', self._to_float(params.get('amountPerGrid'), 0.0), 'usdt'),
                self._display_item('gridMode', 'trading-bot.grid.mode', params.get('gridMode') or 'arithmetic', 'enum', f"trading-bot.grid.{params.get('gridMode') or 'arithmetic'}"),
                self._display_item('gridDirection', 'trading-bot.grid.direction', direction, 'enum', f"trading-bot.grid.{direction}"),
            ]
            if direction in ('long', 'short'):
                grid_items.append(
                    self._display_item('initialPositionPct', 'trading-bot.grid.initialPositionPct', self._to_float(params.get('initialPositionPct'), 0.0), 'percent')
                )
            grid_items.append(
                self._display_item('boundaryAction', 'trading-bot.grid.boundaryAction', params.get('boundaryAction') or 'pause', 'enum', {
                    'pause': 'trading-bot.grid.boundaryPause',
                    'stop_loss': 'trading-bot.grid.boundaryStopLoss',
                    'hold': 'trading-bot.grid.boundaryHold',
                }.get(params.get('boundaryAction') or 'pause', 'trading-bot.grid.boundaryPause'))
            )
            display['strategy_params'] = grid_items
            display['strategy_params'].extend([
                self._display_item('orderMode', 'trading-bot.grid.orderType', params.get('orderMode') or 'maker', 'enum', 'trading-bot.grid.limitOrder' if (params.get('orderMode') or 'maker') == 'maker' else 'trading-bot.grid.marketOrder'),
                self._display_item('adaptiveBounds', 'trading-bot.grid.adaptiveBounds', bool(params.get('adaptiveBounds', True)), 'boolean'),
                self._display_item('adaptiveAtrMult', 'trading-bot.grid.adaptiveAtrMult', self._to_float(params.get('adaptiveAtrMult'), 2.0), 'number'),
                self._display_item('waterfallProtection', 'trading-bot.grid.waterfallProtection', bool(params.get('waterfallProtection', True)), 'boolean'),
                self._display_item('waterfallDropPct', 'trading-bot.grid.waterfallDropPct', self._to_float(params.get('waterfallDropPct'), 0.03) * 100, 'percent'),
            ])
        elif bot_type == 'trend':
            direction = params.get('direction') or 'long'
            direction_key = {
                'long': 'trading-bot.trend.longOnly',
                'short': 'trading-bot.trend.shortOnly',
                'both': 'trading-bot.trend.bothSides'
            }.get(direction, 'trading-bot.trend.longOnly')
            display['strategy_params'] = [
                self._display_item('maPeriod', 'trading-bot.trend.maPeriod', self._to_int(params.get('maPeriod'), 0), 'number'),
                self._display_item('maType', 'trading-bot.trend.maType', params.get('maType') or 'EMA', 'text'),
                self._display_item('confirmBars', 'trading-bot.trend.confirmBars', self._to_int(params.get('confirmBars'), 0), 'number'),
                self._display_item('positionPct', 'trading-bot.trend.positionPct', self._to_float(params.get('positionPct'), 0.0), 'percent'),
                self._display_item('direction', 'trading-bot.trend.direction', direction, 'enum', direction_key),
            ]
        elif bot_type == 'dca':
            frequency = params.get('frequency') or 'daily'
            frequency_key = {
                'every_bar': 'trading-bot.dca.everyBar',
                'hourly': 'trading-bot.dca.hourly',
                '4h': '',
                'daily': 'trading-bot.dca.daily',
                'weekly': 'trading-bot.dca.weekly',
                'biweekly': 'trading-bot.dca.biweekly',
                'monthly': 'trading-bot.dca.monthly'
            }.get(frequency, '')
            display['strategy_params'] = [
                self._display_item('amountEach', 'trading-bot.dca.amountEach', self._to_float(params.get('amountEach'), 0.0), 'usdt'),
                self._display_item('frequency', 'trading-bot.dca.frequency', frequency, 'enum', frequency_key),
                self._display_item('totalBudget', 'trading-bot.dca.totalBudget', self._to_float(params.get('totalBudget'), 0.0), 'usdt'),
                self._display_item('dipBuyEnabled', 'trading-bot.dca.dipBuy', bool(params.get('dipBuyEnabled')), 'bool'),
                self._display_item('dipThreshold', 'trading-bot.dca.dipThreshold', self._to_float(params.get('dipThreshold'), 0.0), 'percent'),
            ]

        if self._to_float(tc.get('stop_loss_pct'), 0.0) > 0:
            display['risk_params'].append(
                self._display_item('stopLossPct', 'trading-bot.risk.stopLossPct', self._to_float(tc.get('stop_loss_pct'), 0.0), 'percent')
            )
        if self._to_float(tc.get('take_profit_pct'), 0.0) > 0:
            display['risk_params'].append(
                self._display_item('takeProfitPct', 'trading-bot.risk.takeProfitPct', self._to_float(tc.get('take_profit_pct'), 0.0), 'percent')
            )
        if self._to_float(tc.get('max_position'), 0.0) > 0:
            display['risk_params'].append(
                self._display_item('maxPosition', 'trading-bot.risk.maxPosition', self._to_float(tc.get('max_position'), 0.0), 'usdt')
            )
        if self._to_float(tc.get('max_daily_loss'), 0.0) > 0:
            display['risk_params'].append(
                self._display_item('maxDailyLoss', 'trading-bot.risk.maxDailyLoss', self._to_float(tc.get('max_daily_loss'), 0.0), 'usdt')
            )
        return display

    def _dump_json_or_encrypt(self, obj: Any, encrypt: bool = False) -> str:
        if obj is None:
            return ''
        # Local deployment: always store plaintext JSON.
        return json.dumps(obj, ensure_ascii=False)

    def _compute_runtime_metrics(self, strategy_ids: List[int]) -> Dict[int, Dict[str, float]]:
        """批量计算策略的已实现盈亏 / 未实现盈亏 / 当前权益。"""
        result: Dict[int, Dict[str, float]] = {}
        if not strategy_ids:
            return result
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                placeholders = ','.join(['%s'] * len(strategy_ids))
                cur.execute(
                    f"""
                    SELECT strategy_id,
                           COALESCE(SUM(COALESCE(profit, 0) - COALESCE(commission, 0)), 0) AS realized_pnl
                    FROM qd_strategy_trades
                    WHERE strategy_id IN ({placeholders})
                    GROUP BY strategy_id
                    """,
                    tuple(strategy_ids),
                )
                for row in (cur.fetchall() or []):
                    sid = int(row.get('strategy_id'))
                    result.setdefault(sid, {'realized_pnl': 0.0, 'unrealized_pnl': 0.0})
                    result[sid]['realized_pnl'] = float(row.get('realized_pnl') or 0.0)
                cur.execute(
                    f"""
                    SELECT strategy_id,
                           COALESCE(SUM(COALESCE(unrealized_pnl, 0)), 0) AS unrealized_pnl
                    FROM qd_strategy_positions
                    WHERE strategy_id IN ({placeholders})
                    GROUP BY strategy_id
                    """,
                    tuple(strategy_ids),
                )
                for row in (cur.fetchall() or []):
                    sid = int(row.get('strategy_id'))
                    result.setdefault(sid, {'realized_pnl': 0.0, 'unrealized_pnl': 0.0})
                    result[sid]['unrealized_pnl'] = float(row.get('unrealized_pnl') or 0.0)
                cur.close()
        except Exception as e:
            logger.warning(f"compute runtime metrics failed: {e}")
        return result

    def list_strategies(self, user_id: int = 1) -> List[Dict[str, Any]]:
        """List strategies for the specified user."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT *
                    FROM qd_strategies_trading
                    WHERE user_id = ?
                    ORDER BY id DESC
                    """,
                    (user_id,)
                )
                rows = cur.fetchall() or []
                cur.close()

            ids = [int(r['id']) for r in rows if r.get('id') is not None]
            metrics = self._compute_runtime_metrics(ids)

            out = []
            for r in rows:
                ex = self._safe_json_loads(r.get('exchange_config'), {})
                ind = self._safe_json_loads(r.get('indicator_config'), {})
                tr = self._safe_json_loads(r.get('trading_config'), {})
                ai = self._safe_json_loads(r.get('ai_model_config'), {})
                notify = self._safe_json_loads(r.get('notification_config'), {})
                m = metrics.get(int(r['id']), {'realized_pnl': 0.0, 'unrealized_pnl': 0.0})
                init_cap = float(r.get('initial_capital') or 0.0)
                current_equity = max(0.0, init_cap + m['realized_pnl'] + m['unrealized_pnl'])
                total_pnl = m['realized_pnl'] + m['unrealized_pnl']
                total_pnl_pct = round(total_pnl / init_cap * 100.0, 2) if init_cap > 0 else 0.0
                out.append({
                    **r,
                    'exchange_config': ex,
                    'indicator_config': ind,
                    'trading_config': tr,
                    'ai_model_config': ai,
                    'notification_config': notify,
                    'bot_display': self._build_bot_display(tr),
                    'realized_pnl': m['realized_pnl'],
                    'unrealized_pnl': m['unrealized_pnl'],
                    'total_pnl': total_pnl,
                    'total_pnl_pct': total_pnl_pct,
                    'current_equity': current_equity,
                })
            return out
        except Exception as e:
            logger.error(f"list_strategies failed: {e}")
            return []

    def get_strategy(self, strategy_id: int, user_id: int = None) -> Optional[Dict[str, Any]]:
        """Get strategy by ID. If user_id is provided, verify ownership."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                if user_id is not None:
                    cur.execute("SELECT * FROM qd_strategies_trading WHERE id = ? AND user_id = ?", (strategy_id, user_id))
                else:
                    cur.execute("SELECT * FROM qd_strategies_trading WHERE id = ?", (strategy_id,))
                r = cur.fetchone()
                cur.close()
            if not r:
                return None
            r['exchange_config'] = self._safe_json_loads(r.get('exchange_config'), {})
            r['indicator_config'] = self._safe_json_loads(r.get('indicator_config'), {})
            r['trading_config'] = self._safe_json_loads(r.get('trading_config'), {})
            r['ai_model_config'] = self._safe_json_loads(r.get('ai_model_config'), {})
            r['notification_config'] = self._safe_json_loads(r.get('notification_config'), {})
            r['bot_display'] = self._build_bot_display(r['trading_config'])
            try:
                m = self._compute_runtime_metrics([int(r['id'])]).get(int(r['id']), {})
                rp = float(m.get('realized_pnl') or 0.0)
                up = float(m.get('unrealized_pnl') or 0.0)
                init_cap = float(r.get('initial_capital') or 0.0)
                r['realized_pnl'] = rp
                r['unrealized_pnl'] = up
                r['total_pnl'] = rp + up
                r['total_pnl_pct'] = round((rp + up) / init_cap * 100.0, 2) if init_cap > 0 else 0.0
                r['current_equity'] = max(0.0, init_cap + rp + up)
            except Exception:
                pass
            return r
        except Exception as e:
            logger.error(f"get_strategy failed: {e}")
            return None

    def create_strategy(self, payload: Dict[str, Any]) -> int:
        name = (payload.get('strategy_name') or '').strip()
        if not name:
            raise ValueError("strategy_name is required")

        user_id = payload.get('user_id') or 1
        strategy_type = payload.get('strategy_type') or 'IndicatorStrategy'
        market_category = payload.get('market_category') or 'Crypto'
        execution_mode = payload.get('execution_mode') or 'signal'
        notification_config = payload.get('notification_config') or {}

        indicator_config = payload.get('indicator_config') or {}
        if strategy_type == 'IndicatorStrategy':
            from app.services.indicator_workspace import link_indicator_config
            indicator_config = link_indicator_config(
                int(user_id or 1),
                indicator_config,
                auto_save=True,
            )
            payload['indicator_config'] = indicator_config
        trading_config = _strip_legacy_risk_pct_basis(
            _apply_default_strict_mode(payload.get('trading_config') or {})
        )
        trading_config = self._sanitize_grid_trading_config(trading_config)
        if strategy_type == 'IndicatorStrategy':
            trading_config = _apply_risk_flat_from_indicator_code(
                trading_config, indicator_config
            )
        from app.services.exchange_execution import coalesce_exchange_config_from_payload, resolve_exchange_config

        exchange_config = coalesce_exchange_config_from_payload(payload)

        resolved_ex_cfg = resolve_exchange_config(
            exchange_config if isinstance(exchange_config, dict) else {},
            user_id=int(user_id or 1),
        )

        # Validate broker / market / market_type / direction / bot_type as a
        # single unit through the centralized policy (broker_market_policy.py).
        # That module is the single source of truth — adding a new broker or
        # tightening a rule should only need a change in one place.
        from app.services.broker_market_policy import validate_strategy_config
        exchange_id = (resolved_ex_cfg.get('exchange_id') or '').strip().lower() if isinstance(resolved_ex_cfg, dict) else ''
        marketplace_delivery = bool(payload.get('marketplace_delivery'))
        validate_strategy_config(
            exchange_id=exchange_id,
            market_category=market_category,
            market_type=(trading_config or {}).get('market_type'),
            trade_direction=(trading_config or {}).get('trade_direction'),
            bot_type=(trading_config or {}).get('bot_type'),
            require_exchange=(execution_mode == 'live') and not marketplace_delivery,
        )
        if market_category == 'MOEX':
            raise ValueError(
                "MOEX (Moscow Exchange) is supported for analysis and backtesting only. "
                "Live order placement on MOEX is not implemented."
            )

        # When credential_id is present, strip raw API keys to avoid
        # storing secrets in the strategy record — they live in qd_exchange_credentials.
        if isinstance(exchange_config, dict) and exchange_config.get('credential_id'):
            for _secret_key in ('api_key', 'secret_key', 'passphrase', 'apiKey', 'secret', 'password'):
                exchange_config.pop(_secret_key, None)

        # Strategy group fields
        strategy_group_id = payload.get('strategy_group_id') or ''
        group_base_name = payload.get('group_base_name') or ''

        # Denormalized fields for quick list rendering
        symbol = (trading_config or {}).get('symbol')

        # Canonicalise Crypto symbols (``BTC`` -> ``BTC/USDT``) right before
        # we persist so the DB never holds two different shapes for the same
        # underlying pair. Equity / FX / futures markets pass through
        # unchanged. Write the normalised value back into trading_config so
        # the JSON copy stored alongside the denormalised column also reflects
        # the canonical form — the live trading worker and execution layer
        # both read symbol from trading_config['symbol'].
        if market_category == 'Crypto' and isinstance(symbol, str) and symbol:
            symbol = normalize_crypto_symbol(symbol)
            if isinstance(trading_config, dict):
                trading_config['symbol'] = symbol
        timeframe = (trading_config or {}).get('timeframe')
        initial_capital = (trading_config or {}).get('initial_capital') or payload.get('initial_capital') or 1000
        leverage = (trading_config or {}).get('leverage') or 1
        market_type = (trading_config or {}).get('market_type') or 'swap'
        
        # Cross-sectional strategy fields (store in trading_config to avoid DB schema changes)
        cs_strategy_type = payload.get('cs_strategy_type') or trading_config.get('cs_strategy_type') or 'single'
        if (cs_strategy_type or '').strip().lower() == 'cross_sectional':
            trading_config = _apply_cross_sectional_trading_config(
                trading_config,
                cs_strategy_type=cs_strategy_type,
                symbol_list=payload.get('symbol_list') or trading_config.get('symbol_list') or [],
                portfolio_size=payload.get('portfolio_size') or trading_config.get('portfolio_size') or 10,
                long_ratio=payload.get('long_ratio') if payload.get('long_ratio') is not None else trading_config.get('long_ratio'),
                rebalance_frequency=payload.get('rebalance_frequency') or trading_config.get('rebalance_frequency'),
                market_category=market_category,
                market_type=market_type,
            )
            symbol = trading_config.get('symbol') or symbol

        strategy_mode = payload.get('strategy_mode') or 'signal'
        strategy_code = payload.get('strategy_code') or ''
        if strategy_type == 'ScriptStrategy' or strategy_mode in ('script', 'bot'):
            script_source_id = (trading_config or {}).get('script_source_id') or (trading_config or {}).get('scriptSourceId')
            if script_source_id:
                from app.services.script_source import get_script_source_service
                source = get_script_source_service().get_source(int(script_source_id), user_id=user_id)
                if not source:
                    raise ValueError("script source not found")
                trading_config['script_source_id'] = int(script_source_id)
                trading_config['script_role'] = trading_config.get('script_role') or 'runtime'

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_strategies_trading
                (user_id, strategy_name, strategy_type, market_category, execution_mode, notification_config,
                 status, symbol, timeframe, initial_capital, leverage, market_type,
                 exchange_config, indicator_config, trading_config, ai_model_config, decide_interval,
                 strategy_group_id, group_base_name, strategy_mode, strategy_code,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())
                """,
                (
                    user_id,
                    name,
                    strategy_type,
                    market_category,
                    execution_mode,
                    self._dump_json_or_encrypt(notification_config, encrypt=False),
                    payload.get('status') or 'stopped',
                    symbol,
                    timeframe,
                    float(initial_capital or 1000),
                    int(leverage or 1),
                    market_type,
                    self._dump_json_or_encrypt(exchange_config, encrypt=False) if exchange_config else '',
                    self._dump_json_or_encrypt(indicator_config, encrypt=False),
                    self._dump_json_or_encrypt(trading_config, encrypt=False),
                    self._dump_json_or_encrypt(payload.get('ai_model_config') or {}, encrypt=False),
                    int(payload.get('decide_interval') or 300),
                    strategy_group_id,
                    group_base_name,
                    strategy_mode,
                    strategy_code
                )
            )
            new_id = cur.lastrowid
            db.commit()
            cur.close()
        return int(new_id)

    def batch_create_strategies(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Batch create strategies (multi-symbol)
        
        Args:
            payload: Contains symbols (array) and other strategy config
            
        Returns:
            {
                'success': True/False,
                'strategy_group_id': '...',
                'created_ids': [1, 2, 3],
                'failed_symbols': []
            }
        """
        symbols = payload.get('symbols') or []
        if not symbols or not isinstance(symbols, list):
            raise ValueError("symbols array is required")
        
        base_name = (payload.get('strategy_name') or '').strip()
        if not base_name:
            raise ValueError("strategy_name is required")
        
        # Validate broker / market combination via centralized policy.
        # Each per-symbol create_strategy() call below will re-validate, but
        # checking once up front fails the whole batch fast on a mismatch.
        market_category = payload.get('market_category') or 'Crypto'
        exchange_config = coalesce_exchange_config_from_payload(payload)
        batch_trading_config = payload.get('trading_config') if isinstance(payload.get('trading_config'), dict) else {}
        batch_execution_mode = (payload.get('execution_mode') or 'signal').strip().lower()
        from app.services.exchange_execution import resolve_exchange_config as _resolve_ex
        from app.services.broker_market_policy import validate_strategy_config as _validate_policy

        uid_bc = int(payload.get('user_id') or 1)
        _resolved_bc = _resolve_ex(exchange_config if isinstance(exchange_config, dict) else {}, user_id=uid_bc)
        exchange_id = (_resolved_bc.get('exchange_id') or '').strip().lower() if isinstance(_resolved_bc, dict) else ''
        _validate_policy(
            exchange_id=exchange_id,
            market_category=market_category,
            market_type=batch_trading_config.get('market_type'),
            trade_direction=batch_trading_config.get('trade_direction'),
            bot_type=batch_trading_config.get('bot_type'),
            require_exchange=(batch_execution_mode == 'live'),
        )
        if market_category == 'MOEX':
            raise ValueError(
                "MOEX (Moscow Exchange) is supported for analysis and backtesting only. "
                "Live order placement on MOEX is not implemented."
            )

        # Generate strategy group ID
        strategy_group_id = str(uuid.uuid4())[:8]
        
        created_ids = []
        failed_symbols = []
        
        for symbol in symbols:
            try:
                # Create individual strategy for each symbol
                single_payload = dict(payload)
                
                # Parse symbol (may be "Market:SYMBOL" format)
                if isinstance(symbol, str) and ':' in symbol:
                    parts = symbol.split(':', 1)
                    market_category = parts[0]
                    symbol_name = parts[1]
                else:
                    market_category = payload.get('market_category') or 'Crypto'
                    symbol_name = symbol

                # Canonicalise Crypto here so the *strategy name* suffix
                # matches the symbol that will end up in the DB. Without this
                # the user sees "Strategy-BTC" but the row stores "BTC/USDT",
                # which is fine functionally but confusing in the UI.
                if market_category == 'Crypto' and isinstance(symbol_name, str) and symbol_name:
                    symbol_name = normalize_crypto_symbol(symbol_name)

                # Strategy name with symbol suffix
                single_payload['strategy_name'] = f"{base_name}-{symbol_name}"
                single_payload['strategy_group_id'] = strategy_group_id
                single_payload['group_base_name'] = base_name
                single_payload['market_category'] = market_category
                
                # Update symbol in trading_config
                trading_config = dict(single_payload.get('trading_config') or {})
                trading_config['symbol'] = symbol_name
                single_payload['trading_config'] = trading_config
                
                new_id = self.create_strategy(single_payload)
                created_ids.append(new_id)
                
            except Exception as e:
                logger.error(f"Failed to create strategy for symbol {symbol}: {e}")
                failed_symbols.append({'symbol': symbol, 'error': str(e)})
        
        return {
            'success': len(created_ids) > 0,
            'strategy_group_id': strategy_group_id,
            'group_base_name': base_name,
            'created_ids': created_ids,
            'failed_symbols': failed_symbols,
            'total_created': len(created_ids),
            'total_failed': len(failed_symbols)
        }

    def batch_start_strategies(self, strategy_ids: List[int], user_id: int = None) -> Dict[str, Any]:
        """Batch start strategies. If user_id is provided, verify ownership."""
        success_ids = []
        failed_ids = []

        for sid in strategy_ids:
            try:
                self.update_strategy_status(sid, 'running', user_id=user_id)
                success_ids.append(sid)
            except Exception as e:
                logger.error(f"Failed to start strategy {sid}: {e}")
                failed_ids.append({'id': sid, 'error': str(e)})

        return {
            'success': len(success_ids) > 0,
            'success_ids': success_ids,
            'failed_ids': failed_ids
        }

    def batch_stop_strategies(self, strategy_ids: List[int], user_id: int = None) -> Dict[str, Any]:
        """Batch stop strategies. If user_id is provided, verify ownership."""
        success_ids = []
        failed_ids = []

        for sid in strategy_ids:
            try:
                self.update_strategy_status(sid, 'stopped', user_id=user_id)
                success_ids.append(sid)
            except Exception as e:
                logger.error(f"Failed to stop strategy {sid}: {e}")
                failed_ids.append({'id': sid, 'error': str(e)})

        return {
            'success': len(success_ids) > 0,
            'success_ids': success_ids,
            'failed_ids': failed_ids
        }

    def patch_trading_config(self, strategy_id: int, patch: Dict[str, Any], user_id: int = None) -> bool:
        """Merge keys into trading_config without touching other strategy fields."""
        if not patch or not isinstance(patch, dict):
            return False
        existing = self.get_strategy(strategy_id, user_id=user_id)
        if not existing:
            return False
        tc = dict(existing.get('trading_config') or {})
        tc.update(patch)
        with get_db_connection() as db:
            cur = db.cursor()
            if user_id is not None:
                cur.execute(
                    """
                    UPDATE qd_strategies_trading
                    SET trading_config = ?, updated_at = NOW()
                    WHERE id = ? AND user_id = ?
                    """,
                    (self._dump_json_or_encrypt(tc, encrypt=False), strategy_id, user_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE qd_strategies_trading
                    SET trading_config = ?, updated_at = NOW()
                    WHERE id = ?
                    """,
                    (self._dump_json_or_encrypt(tc, encrypt=False), strategy_id),
                )
            db.commit()
            cur.close()
        return True

    def batch_delete_strategies(self, strategy_ids: List[int], user_id: int = None) -> Dict[str, Any]:
        """Batch delete strategies. If user_id is provided, verify ownership."""
        success_ids = []
        failed_ids = []
        
        for sid in strategy_ids:
            try:
                self.delete_strategy(sid, user_id=user_id)
                success_ids.append(sid)
            except Exception as e:
                logger.error(f"Failed to delete strategy {sid}: {e}")
                failed_ids.append({'id': sid, 'error': str(e)})
        
        return {
            'success': len(success_ids) > 0,
            'success_ids': success_ids,
            'failed_ids': failed_ids
        }

    def get_strategies_by_group(self, strategy_group_id: str, user_id: int = None) -> List[Dict[str, Any]]:
        """Get all strategies in a group. If user_id is provided, filter by user."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                if user_id is not None:
                    cur.execute(
                        "SELECT id FROM qd_strategies_trading WHERE strategy_group_id = ? AND user_id = ?",
                        (strategy_group_id, user_id)
                    )
                else:
                    cur.execute(
                        "SELECT id FROM qd_strategies_trading WHERE strategy_group_id = ?",
                        (strategy_group_id,)
                    )
                rows = cur.fetchall() or []
                cur.close()
            return [row['id'] for row in rows]
        except Exception as e:
            logger.error(f"get_strategies_by_group failed: {e}")
            return []

    def update_strategy(self, strategy_id: int, payload: Dict[str, Any], user_id: int = None) -> bool:
        existing = self.get_strategy(strategy_id, user_id=user_id)
        if not existing:
            return False

        # Merge: allow partial updates
        name = (payload.get('strategy_name') or existing.get('strategy_name') or '').strip()
        market_category = payload.get('market_category') or existing.get('market_category') or 'Crypto'
        execution_mode = payload.get('execution_mode') or existing.get('execution_mode') or 'signal'
        notification_config = payload.get('notification_config') if payload.get('notification_config') is not None else (existing.get('notification_config') or {})

        indicator_config = payload.get('indicator_config') if payload.get('indicator_config') is not None else (existing.get('indicator_config') or {})
        exchange_config = payload.get('exchange_config') if payload.get('exchange_config') is not None else (existing.get('exchange_config') or {})
        ai_model_config = payload.get('ai_model_config') if payload.get('ai_model_config') is not None else (existing.get('ai_model_config') or {})

        existing_tc = existing.get('trading_config') or {}
        if not isinstance(existing_tc, dict):
            existing_tc = {}
        if payload.get('trading_config') is not None:
            incoming_tc = payload.get('trading_config') or {}
            if not isinstance(incoming_tc, dict):
                incoming_tc = {}
            # Discard the legacy margin/price toggle on every write so stale
            # clients can't re-introduce a divide-by-leverage branch.
            incoming_tc = _strip_legacy_risk_pct_basis(incoming_tc)
            merged_tc = dict(existing_tc)
            merged_tc.update(incoming_tc)
            merged_tc = _strip_legacy_risk_pct_basis(merged_tc)
            runtime_protected_keys = (
                'script_runtime_state',
                'last_signal_time',
                'last_execution_time',
                'bot_runtime_stats',
            )
            for _k in runtime_protected_keys:
                if _k not in incoming_tc and _k in existing_tc:
                    merged_tc[_k] = existing_tc[_k]
            trading_config = merged_tc
        else:
            trading_config = existing_tc

        if (existing.get('strategy_type') or payload.get('strategy_type') or 'IndicatorStrategy') == 'IndicatorStrategy':
            trading_config = _apply_risk_flat_from_indicator_code(
                trading_config, indicator_config
            )

        trading_config = self._sanitize_grid_trading_config(trading_config)

        # When credential_id is present, strip raw API keys to avoid
        # storing secrets in the strategy record — they live in qd_exchange_credentials.
        if isinstance(exchange_config, dict) and exchange_config.get('credential_id'):
            for _secret_key in ('api_key', 'secret_key', 'passphrase', 'apiKey', 'secret', 'password'):
                exchange_config.pop(_secret_key, None)

        # Handle cross-sectional strategy config updates
        _upd_cs = payload.get('cs_strategy_type')
        if _upd_cs is None:
            _upd_cs = trading_config.get('cs_strategy_type')
        if (_upd_cs or '').strip().lower() == 'cross_sectional':
            trading_config = _apply_cross_sectional_trading_config(
                trading_config,
                cs_strategy_type='cross_sectional',
                symbol_list=(
                    payload.get('symbol_list')
                    if payload.get('symbol_list') is not None
                    else trading_config.get('symbol_list') or []
                ),
                portfolio_size=(
                    payload.get('portfolio_size')
                    if payload.get('portfolio_size') is not None
                    else trading_config.get('portfolio_size')
                ),
                long_ratio=(
                    payload.get('long_ratio')
                    if payload.get('long_ratio') is not None
                    else trading_config.get('long_ratio')
                ),
                rebalance_frequency=(
                    payload.get('rebalance_frequency')
                    if payload.get('rebalance_frequency') is not None
                    else trading_config.get('rebalance_frequency')
                ),
                market_category=market_category,
                market_type=(trading_config.get('market_type') or payload.get('market_type') or 'swap'),
            )
            symbol = trading_config.get('symbol') or symbol

        from app.services.exchange_execution import coalesce_exchange_config_from_payload, resolve_exchange_config as _resolve_ex_upd

        exchange_config = coalesce_exchange_config_from_payload({
            **payload,
            'exchange_config': exchange_config,
            'trading_config': trading_config,
        })

        _merged_ex = _resolve_ex_upd(
            exchange_config if isinstance(exchange_config, dict) else {},
            user_id=int(user_id or 1),
        )
        ex_id = (_merged_ex.get('exchange_id') or '').strip().lower() if isinstance(_merged_ex, dict) else ''
        # Resolve effective execution_mode (payload may override existing).
        _upd_exec_mode = ((payload.get('execution_mode') if payload.get('execution_mode') is not None
                           else existing.get('execution_mode')) or 'signal').strip().lower()
        from app.services.broker_market_policy import validate_strategy_config as _validate_policy_upd
        _validate_policy_upd(
            exchange_id=ex_id,
            market_category=market_category,
            market_type=(trading_config or {}).get('market_type') or existing.get('market_type'),
            trade_direction=(trading_config or {}).get('trade_direction'),
            bot_type=(trading_config or {}).get('bot_type'),
            require_exchange=(_upd_exec_mode == 'live'),
        )
        if market_category == 'MOEX':
            raise ValueError(
                "MOEX (Moscow Exchange) is supported for analysis and backtesting only. "
                "Live order placement on MOEX is not implemented."
            )

        symbol = (trading_config or {}).get('symbol')
        # Same canonicalisation as create_strategy: keep the persisted shape
        # consistent regardless of whether the row was written by create or
        # update. The runtime executor reads symbol from trading_config, so
        # we also write the normalised value back into the JSON copy.
        if market_category == 'Crypto' and isinstance(symbol, str) and symbol:
            symbol = normalize_crypto_symbol(symbol)
            if isinstance(trading_config, dict):
                trading_config['symbol'] = symbol
        timeframe = (trading_config or {}).get('timeframe')
        initial_capital = (trading_config or {}).get('initial_capital') or existing.get('initial_capital') or 1000
        leverage = (trading_config or {}).get('leverage') or existing.get('leverage') or 1
        market_type = (trading_config or {}).get('market_type') or existing.get('market_type') or 'swap'

        strategy_type = (payload.get('strategy_type') if payload.get('strategy_type') is not None
                         else existing.get('strategy_type')) or 'IndicatorStrategy'
        strategy_mode = (payload.get('strategy_mode') if payload.get('strategy_mode') is not None
                         else existing.get('strategy_mode')) or 'signal'
        if 'strategy_code' in payload:
            strategy_code = payload.get('strategy_code') or ''
        else:
            strategy_code = existing.get('strategy_code') or ''
        if strategy_type == 'ScriptStrategy' or strategy_mode in ('script', 'bot'):
            script_source_id = (trading_config or {}).get('script_source_id') or (trading_config or {}).get('scriptSourceId')
            if script_source_id:
                from app.services.script_source import get_script_source_service
                source = get_script_source_service().get_source(int(script_source_id), user_id=int(existing.get('user_id') or 1))
                if not source:
                    raise ValueError("script source not found")
                trading_config['script_source_id'] = int(script_source_id)
                trading_config['script_role'] = trading_config.get('script_role') or 'runtime'

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_strategies_trading
                SET strategy_name = ?,
                    strategy_type = ?,
                    strategy_mode = ?,
                    strategy_code = ?,
                    market_category = ?,
                    execution_mode = ?,
                    notification_config = ?,
                    symbol = ?,
                    timeframe = ?,
                    initial_capital = ?,
                    leverage = ?,
                    market_type = ?,
                    exchange_config = ?,
                    indicator_config = ?,
                    trading_config = ?,
                    ai_model_config = ?,
                    updated_at = NOW()
                WHERE id = ?
                """,
                (
                    name,
                    strategy_type,
                    strategy_mode,
                    strategy_code,
                    market_category,
                    execution_mode,
                    self._dump_json_or_encrypt(notification_config, encrypt=False),
                    symbol,
                    timeframe,
                    float(initial_capital or 1000),
                    int(leverage or 1),
                    market_type,
                    self._dump_json_or_encrypt(exchange_config, encrypt=False) if exchange_config else '',
                    self._dump_json_or_encrypt(indicator_config, encrypt=False),
                    self._dump_json_or_encrypt(trading_config, encrypt=False),
                    self._dump_json_or_encrypt(ai_model_config, encrypt=False),
                    strategy_id
                )
            )
            db.commit()
            cur.close()
        return True

    def delete_strategy(self, strategy_id: int, user_id: int = None) -> bool:
        """Delete strategy. If user_id is provided, verify ownership."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                if user_id is not None:
                    cur.execute("DELETE FROM qd_strategies_trading WHERE id = ? AND user_id = ?", (strategy_id, user_id))
                else:
                    cur.execute("DELETE FROM qd_strategies_trading WHERE id = ?", (strategy_id,))
                db.commit()
                cur.close()
            return True
        except Exception as e:
            logger.error(f"delete_strategy failed: {e}")
            return False
