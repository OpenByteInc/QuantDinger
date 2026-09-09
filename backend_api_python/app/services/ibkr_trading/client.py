"""
Interactive Brokers Trading Client

Uses ib_insync library to connect to TWS or IB Gateway for trading.
"""

import time
import threading
import asyncio
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from app.utils.logger import get_logger
from app.services.ibkr_trading.symbols import normalize_symbol, format_display_symbol

logger = get_logger(__name__)


def _ensure_event_loop():
    """
    Ensure there is an event loop in the current thread.
    
    ib_insync requires an asyncio event loop to function.
    When called from Flask request threads, there may not be one.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("Event loop is closed")
    except RuntimeError:
        # No event loop exists in this thread, create one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        logger.debug("Created new event loop for IBKR client")
    return loop

# Lazy import ib_insync to allow other features to work without it installed
ib_insync = None


def _ensure_ib_insync():
    """Ensure ib_insync is imported."""
    global ib_insync
    if ib_insync is None:
        try:
            import ib_insync as _ib
            ib_insync = _ib
        except ImportError:
            raise ImportError(
                "ib_insync is not installed. Run: pip install ib_insync"
            )
    return ib_insync


def _contract_multiplier(contract) -> float:
    """Contract multiplier, defaulting to 1 for stocks and malformed values."""
    try:
        multiplier = float(getattr(contract, "multiplier", None) or 1)
    except (TypeError, ValueError):
        return 1.0
    return multiplier if multiplier > 0 else 1.0


def _positive_or_none(value) -> Optional[float]:
    """IB reports an unset price as 0.0; treat that as absent."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


@dataclass
class IBKRConfig:
    """IBKR connection configuration."""
    host: str = "127.0.0.1"
    port: int = 7497  # TWS Live:7496, TWS Paper:7497 (IB defaults), Gateway Live:4001, Gateway Paper:4002
    client_id: int = 1
    readonly: bool = False
    account: str = ""  # Leave empty to auto-select first account
    timeout: float = 20.0  # Connection timeout in seconds


@dataclass
class OrderResult:
    """Order execution result."""
    success: bool
    order_id: int = 0
    filled: float = 0.0
    avg_price: float = 0.0
    status: str = ""
    message: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class IBKRClient:
    """
    Interactive Brokers Trading Client
    
    Usage:
        config = IBKRConfig(port=7497)  # IB default for TWS Paper
        client = IBKRClient(config)
        
        if client.connect():
            # Place order
            result = client.place_market_order("AAPL", "buy", 10, "USStock")
            
            # Get positions
            positions = client.get_positions()
            
            client.disconnect()
    """
    
    def __init__(self, config: Optional[IBKRConfig] = None):
        self.config = config or IBKRConfig()
        self._ib = None
        self._connected = False
        self._lock = threading.Lock()
        self._account = ""
    
    @property
    def connected(self) -> bool:
        """Check if connected."""
        if self._ib is None:
            return False
        return self._ib.isConnected()
    
    def connect(self) -> bool:
        """
        Connect to TWS or IB Gateway.
        
        Returns:
            True if connected successfully
        """
        with self._lock:
            if self.connected:
                return True
            
            try:
                # Ensure event loop exists in this thread (required by ib_insync)
                _ensure_event_loop()
                
                _ensure_ib_insync()
                
                if self._ib is None:
                    self._ib = ib_insync.IB()
                
                logger.info(f"Connecting to IBKR: {self.config.host}:{self.config.port} (clientId={self.config.client_id})")
                
                self._ib.connect(
                    host=self.config.host,
                    port=self.config.port,
                    clientId=self.config.client_id,
                    readonly=self.config.readonly,
                    timeout=self.config.timeout
                )
                
                self._connected = True
                
                # Get account
                accounts = self._ib.managedAccounts()
                if accounts:
                    self._account = self.config.account or accounts[0]
                    logger.info(f"IBKR connected, account: {self._account}")
                else:
                    logger.warning("IBKR connected but no account info retrieved")
                
                return True
                
            except Exception as e:
                logger.error(f"IBKR connection failed: {e}")
                self._connected = False
                return False
    
    def disconnect(self):
        """Disconnect from IBKR."""
        with self._lock:
            if self._ib is not None:
                try:
                    self._ib.disconnect()
                except Exception as e:
                    logger.warning(f"IBKR disconnect exception: {e}")
                finally:
                    self._connected = False
                    logger.info("IBKR disconnected")
    
    def _ensure_connected(self):
        """Ensure connection is established."""
        # Ensure event loop exists (may be called from different threads)
        _ensure_event_loop()
        if not self.connected:
            if not self.connect():
                raise ConnectionError("Cannot connect to IBKR")
    
    def _create_contract(self, symbol: str, market_type: str):
        """
        Create IB contract object.
        
        Args:
            symbol: Symbol code
            market_type: Market type (USStock)
        """
        _ensure_ib_insync()
        
        ib_symbol, exchange, currency = normalize_symbol(symbol, market_type)
        
        contract = ib_insync.Stock(
            symbol=ib_symbol,
            exchange=exchange,
            currency=currency
        )
        
        return contract
    
    def _qualify_contract(self, contract) -> bool:
        """Validate contract."""
        try:
            qualified = self._ib.qualifyContracts(contract)
            return len(qualified) > 0
        except Exception as e:
            logger.warning(f"Contract qualification failed: {e}")
            return False
    
    # ==================== Order Methods ====================

    @staticmethod
    def _trade_fee_snapshot(trade) -> Dict[str, Any]:
        fees: Dict[str, float] = {}
        for fill in list(getattr(trade, "fills", None) or []):
            report = getattr(fill, "commissionReport", None)
            if report is None:
                continue
            currency = str(getattr(report, "currency", "") or "").strip().upper()
            try:
                amount = abs(float(getattr(report, "commission", 0) or 0))
            except Exception:
                amount = 0.0
            if currency and amount > 0:
                fees[currency] = fees.get(currency, 0.0) + amount
        if len(fees) == 1:
            currency, amount = next(iter(fees.items()))
            return {"commission": amount, "commission_ccy": currency, "fees_by_ccy": fees}
        return {
            "commission": 0.0,
            "commission_ccy": "MIXED" if fees else "",
            "fees_by_ccy": fees,
        }

    def _trade_result(self, trade, *, message_prefix: str = "Order") -> OrderResult:
        order = trade.order
        order_status = trade.orderStatus
        status = str(getattr(order_status, "status", "") or "Unknown")
        rejected = status.lower() in ("cancelled", "apicancelled", "inactive")
        local_id = int(getattr(order, "orderId", 0) or 0)
        perm_id = int(getattr(order, "permId", 0) or 0)
        fee_snapshot = self._trade_fee_snapshot(trade)
        if not fee_snapshot.get("fees_by_ccy") and float(getattr(order_status, "filled", 0) or 0) > 0:
            fee_snapshot = self._execution_fee_snapshot(order)
        raw = {
            "orderId": local_id,
            "permId": perm_id,
            "status": status,
            "filled": float(getattr(order_status, "filled", 0) or 0),
            "remaining": float(getattr(order_status, "remaining", 0) or 0),
            "avgFillPrice": float(getattr(order_status, "avgFillPrice", 0) or 0),
            **fee_snapshot,
        }
        return OrderResult(
            success=not rejected,
            order_id=perm_id or local_id,
            filled=raw["filled"],
            avg_price=raw["avgFillPrice"],
            status=status,
            message=f"{message_prefix} {status}" if rejected else f"{message_prefix} submitted",
            raw=raw,
        )

    def _execution_fee_snapshot(self, order) -> Dict[str, Any]:
        method = getattr(self._ib, "reqExecutions", None)
        if not callable(method):
            return {"commission": 0.0, "commission_ccy": "", "fees_by_ccy": {}}
        local_id = str(getattr(order, "orderId", "") or "")
        perm_id = str(getattr(order, "permId", "") or "")
        try:
            fills = list(method() or [])
        except Exception:
            return {"commission": 0.0, "commission_ccy": "", "fees_by_ccy": {}}
        matched = []
        for fill in fills:
            execution = getattr(fill, "execution", None)
            candidate_ids = {
                str(getattr(execution, "orderId", "") or ""),
                str(getattr(execution, "permId", "") or ""),
            }
            if (local_id and local_id in candidate_ids) or (perm_id and perm_id in candidate_ids):
                matched.append(fill)
        return self._trade_fee_snapshot(type("ExecutionTrade", (), {"fills": matched})())
    
    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        market_type: str = "USStock",
    ) -> OrderResult:
        """
        Place a market order.
        
        Args:
            symbol: Symbol code (e.g., AAPL, 0700.HK)
            side: Direction ("buy" or "sell")
            quantity: Number of shares
            market_type: Market type ("USStock")
            
        Returns:
            OrderResult
        """
        try:
            self._ensure_connected()
            _ensure_ib_insync()
            
            contract = self._create_contract(symbol, market_type)
            if not self._qualify_contract(contract):
                return OrderResult(
                    success=False,
                    message=f"Invalid contract: {symbol}"
                )
            
            order = ib_insync.MarketOrder(
                action="BUY" if side.lower() == "buy" else "SELL",
                totalQuantity=quantity,
                account=self._account
            )
            
            trade = self._ib.placeOrder(contract, order)
            
            # Wait for order status update
            self._ib.sleep(2)
            
            return self._trade_result(trade)
            
        except Exception as e:
            logger.error(f"Order failed: {e}")
            return OrderResult(
                success=False,
                message=str(e)
            )
    
    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        market_type: str = "USStock",
    ) -> OrderResult:
        """
        Place a limit order.
        
        Args:
            symbol: Symbol code
            side: Direction ("buy" or "sell")
            quantity: Number of shares
            price: Limit price
            market_type: Market type
            
        Returns:
            OrderResult
        """
        try:
            self._ensure_connected()
            _ensure_ib_insync()
            
            contract = self._create_contract(symbol, market_type)
            if not self._qualify_contract(contract):
                return OrderResult(
                    success=False,
                    message=f"Invalid contract: {symbol}"
                )
            
            order = ib_insync.LimitOrder(
                action="BUY" if side.lower() == "buy" else "SELL",
                totalQuantity=quantity,
                lmtPrice=price,
                account=self._account
            )
            
            trade = self._ib.placeOrder(contract, order)
            self._ib.sleep(1)
            
            result = self._trade_result(trade, message_prefix="Limit order")
            result.raw["limitPrice"] = float(price)
            return result
            
        except Exception as e:
            logger.error(f"Limit order failed: {e}")
            return OrderResult(
                success=False,
                message=str(e)
            )

    def place_bracket_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        *,
        take_profit_price: float = 0.0,
        stop_loss_price: float = 0.0,
        limit_price: float = 0.0,
        market_type: str = "USStock",
    ) -> OrderResult:
        """Submit a parent order and attached take-profit/stop-loss orders atomically."""
        try:
            self._ensure_connected()
            _ensure_ib_insync()
            contract = self._create_contract(symbol, market_type)
            if not self._qualify_contract(contract):
                return OrderResult(success=False, message=f"Invalid contract: {symbol}")

            take_profit_price = float(take_profit_price or 0.0)
            stop_loss_price = float(stop_loss_price or 0.0)
            if take_profit_price <= 0 and stop_loss_price <= 0:
                return OrderResult(success=False, message="Bracket order requires a protection price")

            action = "BUY" if side.lower() == "buy" else "SELL"
            exit_action = "SELL" if action == "BUY" else "BUY"
            parent = (
                ib_insync.LimitOrder(action, quantity, float(limit_price), account=self._account)
                if float(limit_price or 0.0) > 0
                else ib_insync.MarketOrder(action, quantity, account=self._account)
            )
            parent.orderId = self._ib.client.getReqId()
            parent.transmit = False

            children = []
            if take_profit_price > 0:
                child = ib_insync.LimitOrder(
                    exit_action,
                    quantity,
                    take_profit_price,
                    account=self._account,
                    parentId=parent.orderId,
                    tif="GTC",
                    transmit=False,
                )
                children.append(child)
            if stop_loss_price > 0:
                child = ib_insync.StopOrder(
                    exit_action,
                    quantity,
                    stop_loss_price,
                    account=self._account,
                    parentId=parent.orderId,
                    tif="GTC",
                    transmit=False,
                )
                children.append(child)
            children[-1].transmit = True

            trade = self._ib.placeOrder(contract, parent)
            for child in children:
                self._ib.placeOrder(contract, child)
                self._ib.sleep(0.05)
            self._ib.sleep(2)
            result = self._trade_result(trade, message_prefix="Bracket order")
            result.raw["childOrderIds"] = [int(getattr(child, "orderId", 0) or 0) for child in children]
            result.raw["takeProfitPrice"] = take_profit_price
            result.raw["stopLossPrice"] = stop_loss_price
            return result
        except Exception as e:
            logger.error(f"Bracket order failed: {e}")
            return OrderResult(success=False, message=str(e))
    
    def cancel_order(self, order_id: int) -> bool:
        """
        Cancel an order.
        
        Args:
            order_id: Order ID
            
        Returns:
            True if cancelled successfully
        """
        try:
            self._ensure_connected()
            
            request_all = getattr(self._ib, "reqAllOpenOrders", None)
            trades = list(request_all() or []) if callable(request_all) else list(self._ib.openTrades() or [])
            requested = str(order_id or "").strip()
            for trade in trades:
                candidate_ids = {
                    str(getattr(trade.order, "orderId", "") or ""),
                    str(getattr(trade.order, "permId", "") or ""),
                }
                if requested in candidate_ids:
                    self._ib.cancelOrder(trade.order)
                    logger.info(f"Order {order_id} cancelled")
                    return True
            
            logger.warning(f"Order not found: {order_id}")
            return False
            
        except Exception as e:
            logger.error(f"Cancel order failed: {e}")
            return False

    def get_order_status(self, order_id: int) -> OrderResult:
        """Return the latest known status for an open or completed order."""
        try:
            self._ensure_connected()
            requested = str(order_id or "").strip()
            if not requested:
                return OrderResult(success=False, message="Missing order_id")

            trades = []
            for method_name, args in (
                ("reqAllOpenOrders", ()),
                ("openTrades", ()),
                ("trades", ()),
                ("reqCompletedOrders", (False,)),
            ):
                method = getattr(self._ib, method_name, None)
                if not callable(method):
                    continue
                try:
                    trades.extend(list(method(*args) or []))
                except Exception:
                    continue

            for trade in trades:
                order = getattr(trade, "order", None)
                order_status = getattr(trade, "orderStatus", None)
                candidate_ids = {
                    str(getattr(order, "orderId", "") or ""),
                    str(getattr(order, "permId", "") or ""),
                }
                if requested not in candidate_ids:
                    continue
                return self._trade_result(trade)
            return OrderResult(
                success=True,
                order_id=order_id,
                status="Unknown",
                message="Order not found in the current IBKR session",
            )
        except Exception as e:
            logger.error(f"Get order status failed: {e}")
            return OrderResult(success=False, order_id=order_id, message=str(e))

    # ==================== Query Methods ====================
    
    def get_account_summary(self) -> Dict[str, Any]:
        """
        Get account summary.
        
        Returns:
            Account info dictionary
        """
        try:
            self._ensure_connected()
            
            summary = self._ib.accountSummary(self._account)
            result = {}
            for item in summary:
                result[item.tag] = {
                    "value": item.value,
                    "currency": item.currency
                }
            
            return {
                "account": self._account,
                "summary": result,
                "success": True
            }
            
        except Exception as e:
            logger.error(f"Get account summary failed: {e}")
            return {"success": False, "error": str(e)}
    
    def get_positions(self) -> List[Dict[str, Any]]:
        """
        Get current positions.
        
        Returns:
            List of positions
        """
        try:
            self._ensure_connected()
            
            positions = self._ib.positions(self._account)
            result = []
            
            for pos in positions:
                contract = pos.contract
                exchange = contract.exchange or contract.primaryExchange or "SMART"
                multiplier = _contract_multiplier(contract)
                avg_cost = float(pos.avgCost)

                result.append({
                    "symbol": format_display_symbol(contract.symbol, exchange),
                    "ib_symbol": contract.symbol,
                    # Futures share a symbol across expiries; localSymbol (MGCV6)
                    # is what identifies the contract actually held.
                    "localSymbol": contract.localSymbol or "",
                    "lastTradeDate": contract.lastTradeDateOrContractMonth or "",
                    "secType": contract.secType,
                    "exchange": exchange,
                    "currency": contract.currency,
                    "multiplier": multiplier,
                    "quantity": float(pos.position),
                    # IB reports avgCost per contract, i.e. already multiplied.
                    "avgCost": avg_cost,
                    # Per unit, so it is comparable to quotes and stop prices.
                    "avgPrice": avg_cost / multiplier,
                    "marketValue": float(pos.position) * avg_cost,
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Get positions failed: {e}")
            return []
    
    def get_open_orders(self) -> List[Dict[str, Any]]:
        """
        Get open orders.
        
        Returns:
            List of orders
        """
        try:
            self._ensure_connected()
            
            request_all = getattr(self._ib, "reqAllOpenOrders", None)
            trades = list(request_all() or []) if callable(request_all) else list(self._ib.openTrades() or [])
            result = []
            
            for trade in trades:
                order = trade.order
                contract = trade.contract
                status = trade.orderStatus
                limit_price = _positive_or_none(getattr(order, "lmtPrice", None))
                # Stop and stop-limit orders carry their trigger in auxPrice;
                # lmtPrice is 0 for a plain STP, which reads as "no price".
                stop_price = _positive_or_none(getattr(order, "auxPrice", None))

                result.append({
                    # ``id`` is what order tables key rows and cancel actions on.
                    "id": order.orderId,
                    "orderId": order.orderId,
                    "permId": getattr(order, "permId", 0),
                    "symbol": contract.symbol,
                    "localSymbol": contract.localSymbol or "",
                    "action": order.action,
                    "quantity": float(order.totalQuantity),
                    "orderType": order.orderType,
                    "limitPrice": getattr(order, 'lmtPrice', None),
                    "stopPrice": stop_price,
                    # The price that actually governs this order.
                    "price": limit_price if limit_price is not None else stop_price,
                    "status": status.status,
                    "filled": float(status.filled or 0),
                    "remaining": float(status.remaining or 0),
                    "avgFillPrice": float(status.avgFillPrice or 0),
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Get orders failed: {e}")
            return []
    
    def get_quote(self, symbol: str, market_type: str = "USStock") -> Dict[str, Any]:
        """
        Get real-time quote.
        
        Args:
            symbol: Symbol code
            market_type: Market type
            
        Returns:
            Quote data
        """
        try:
            self._ensure_connected()
            
            contract = self._create_contract(symbol, market_type)
            if not self._qualify_contract(contract):
                return {"success": False, "error": f"Invalid contract: {symbol}"}
            
            # Request market data
            ticker = self._ib.reqMktData(contract, '', False, False)
            
            # Wait for data
            self._ib.sleep(2)
            
            result = {
                "success": True,
                "symbol": symbol,
                "bid": ticker.bid if ticker.bid and ticker.bid > 0 else None,
                "ask": ticker.ask if ticker.ask and ticker.ask > 0 else None,
                "last": ticker.last if ticker.last and ticker.last > 0 else None,
                "high": ticker.high if ticker.high and ticker.high > 0 else None,
                "low": ticker.low if ticker.low and ticker.low > 0 else None,
                "volume": ticker.volume if ticker.volume and ticker.volume > 0 else None,
                "close": ticker.close if ticker.close and ticker.close > 0 else None,
            }
            
            # Cancel subscription
            self._ib.cancelMktData(contract)
            
            return result
            
        except Exception as e:
            logger.error(f"Get quote failed: {e}")
            return {"success": False, "error": str(e)}
    
    def get_connection_status(self) -> Dict[str, Any]:
        """Get connection status."""
        return {
            "connected": self.connected,
            "host": self.config.host,
            "port": self.config.port,
            "clientId": self.config.client_id,
            "account": self._account,
            "readonly": self.config.readonly,
        }


# Global singleton (optional)
_global_client: Optional[IBKRClient] = None
_global_lock = threading.Lock()


def get_ibkr_client(config: Optional[IBKRConfig] = None) -> IBKRClient:
    """
    Get global IBKR client singleton.
    
    Args:
        config: Configuration (only effective on first call)
        
    Returns:
        IBKRClient instance
    """
    global _global_client
    
    with _global_lock:
        if _global_client is None:
            _global_client = IBKRClient(config)
        return _global_client


def reset_ibkr_client():
    """Reset global client (disconnect and clear instance)."""
    global _global_client
    
    with _global_lock:
        if _global_client is not None:
            _global_client.disconnect()
            _global_client = None
