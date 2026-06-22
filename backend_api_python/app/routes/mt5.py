"""
MetaTrader 5 Trading API Routes

Provides REST API for MT5 trading operations.
"""

from flask import jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.utils.auth import login_required

from app.utils.logger import get_logger
from app.utils.local_brokers import (
    local_desktop_brokers_allowed,
    desktop_broker_cloud_reject_message,
)

logger = get_logger(__name__)

mt5_blp = Blueprint("mt5", __name__)

# Lazy import MT5 client to avoid errors if not installed
MT5Client = None
MT5Config = None
_client = None


def _ensure_mt5_imports():
    """Ensure MT5 modules are imported."""
    global MT5Client, MT5Config
    if MT5Client is None or MT5Config is None:
        try:
            from app.services.mt5_trading import MT5Client as _MT5Client, MT5Config as _MT5Config
            MT5Client = _MT5Client
            MT5Config = _MT5Config
        except ImportError as e:
            raise ImportError(
                "MT5 trading requires MetaTrader5 library. "
                "Run: pip install MetaTrader5\n"
                "Note: This library only works on Windows."
            ) from e


def _get_client():
    """Get or create MT5 client instance."""
    global _client
    if _client is None:
        _ensure_mt5_imports()
        _client = MT5Client()
    return _client


# ==================== Connection Management ====================

@mt5_blp.route("/status", methods=["GET"])
@login_required
def get_status():
    """Get MT5 connection status."""
    try:
        if not local_desktop_brokers_allowed():
            return jsonify({
                "connected": False,
                "error": desktop_broker_cloud_reject_message(),
            }), 403

        _ensure_mt5_imports()
        client = _get_client()
        status = client.get_connection_status()
        return jsonify(status)
    except ImportError as e:
        return jsonify({
            "connected": False,
            "error": str(e),
            "hint": "MetaTrader5 library is not installed or not on Windows"
        })
    except Exception as e:
        logger.error(f"Get MT5 status failed: {e}")
        return jsonify({"connected": False, "error": str(e)})


@mt5_blp.route("/connect", methods=["POST"])
@login_required
def connect():
    """
    Connect to MT5 terminal.
    
    Request body:
    {
        "login": 12345678,      // MT5 account number
        "password": "xxx",      // MT5 password
        "server": "ICMarkets-Demo",  // Broker server
        "terminal_path": ""     // Optional: path to terminal64.exe
    }
    """
    global _client
    
    try:
        if not local_desktop_brokers_allowed():
            return jsonify({
                "success": False,
                "error": desktop_broker_cloud_reject_message(),
            }), 403

        _ensure_mt5_imports()
        
        data = request.get_json() or {}
        
        login = data.get("login") or data.get("mt5_login")
        password = data.get("password") or data.get("mt5_password")
        server = data.get("server") or data.get("mt5_server")
        terminal_path = data.get("terminal_path") or data.get("mt5_terminal_path") or ""
        
        if not login or not password or not server:
            return jsonify({
                "success": False,
                "error": "Missing required fields: login, password, server"
            }), 400
        
        config = MT5Config(
            login=int(login),
            password=str(password),
            server=str(server),
            terminal_path=str(terminal_path),
        )
        
        # Disconnect old client before creating new one
        if _client is not None:
            try:
                _client.disconnect()
            except Exception:
                pass

        _client = MT5Client(config)

        if _client.connect():
            account_info = _client.get_account_info()
            return jsonify({
                "success": True,
                "message": "Connected to MT5",
                "account": account_info
            })
        else:
            return jsonify({
                "success": False,
                "error": "Failed to connect to MT5. Check credentials and ensure terminal is running."
            }), 400
            
    except ImportError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
    except Exception as e:
        logger.error(f"MT5 connect failed: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@mt5_blp.route("/disconnect", methods=["POST"])
@login_required
def disconnect():
    """Disconnect from MT5 terminal."""
    global _client
    
    try:
        if not local_desktop_brokers_allowed():
            return jsonify({
                "success": False,
                "error": desktop_broker_cloud_reject_message(),
            }), 403

        if _client is not None:
            _client.disconnect()
            _client = None
        return jsonify({"success": True, "message": "Disconnected"})
    except Exception as e:
        logger.error(f"MT5 disconnect failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Account Queries ====================

@mt5_blp.route("/account", methods=["GET"])
@login_required
def get_account():
    """Get account information."""
    try:
        if not local_desktop_brokers_allowed():
            return jsonify({"success": False, "error": desktop_broker_cloud_reject_message()}), 403

        client = _get_client()
        if not client.connected:
            return jsonify({"success": False, "error": "Not connected to MT5"}), 400
        
        info = client.get_account_info()
        return jsonify(info)
    except Exception as e:
        logger.error(f"Get MT5 account failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mt5_blp.route("/positions", methods=["GET"])
@login_required
def get_positions():
    """Get open positions."""
    try:
        if not local_desktop_brokers_allowed():
            return jsonify({"success": False, "error": desktop_broker_cloud_reject_message()}), 403

        client = _get_client()
        if not client.connected:
            return jsonify({"success": False, "error": "Not connected to MT5"}), 400
        
        symbol = request.args.get("symbol")
        positions = client.get_positions(symbol=symbol)
        return jsonify({"success": True, "positions": positions})
    except Exception as e:
        logger.error(f"Get MT5 positions failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mt5_blp.route("/orders", methods=["GET"])
@login_required
def get_orders():
    """Get pending orders."""
    try:
        if not local_desktop_brokers_allowed():
            return jsonify({"success": False, "error": desktop_broker_cloud_reject_message()}), 403

        client = _get_client()
        if not client.connected:
            return jsonify({"success": False, "error": "Not connected to MT5"}), 400
        
        symbol = request.args.get("symbol")
        orders = client.get_orders(symbol=symbol)
        return jsonify({"success": True, "orders": orders})
    except Exception as e:
        logger.error(f"Get MT5 orders failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mt5_blp.route("/symbols", methods=["GET"])
@login_required
def get_symbols():
    """Get available symbols."""
    try:
        if not local_desktop_brokers_allowed():
            return jsonify({"success": False, "error": desktop_broker_cloud_reject_message()}), 403

        client = _get_client()
        if not client.connected:
            return jsonify({"success": False, "error": "Not connected to MT5"}), 400
        
        group = request.args.get("group", "*")
        symbols = client.get_symbols(group=group)
        return jsonify({"success": True, "symbols": symbols})
    except Exception as e:
        logger.error(f"Get MT5 symbols failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Trading ====================

@mt5_blp.route("/order", methods=["POST"])
@login_required
def place_order():
    """
    Place an order.
    
    Request body:
    {
        "symbol": "EURUSD",
        "side": "buy",          // "buy" or "sell"
        "volume": 0.1,          // Lot size
        "orderType": "market",  // "market" or "limit"
        "price": 1.0800         // Required for limit orders
    }
    """
    try:
        if not local_desktop_brokers_allowed():
            return jsonify({"success": False, "error": desktop_broker_cloud_reject_message()}), 403

        client = _get_client()
        if not client.connected:
            return jsonify({"success": False, "error": "Not connected to MT5"}), 400
        
        data = request.get_json() or {}
        
        symbol = data.get("symbol")
        side = data.get("side")
        volume = data.get("volume") or data.get("quantity")
        order_type = data.get("orderType", "market").lower()
        price = data.get("price")
        comment = data.get("comment", "QuantDinger")
        
        if not symbol or not side or not volume:
            return jsonify({
                "success": False,
                "error": "Missing required fields: symbol, side, volume"
            }), 400
        
        if order_type == "limit":
            if not price:
                return jsonify({
                    "success": False,
                    "error": "Limit order requires price"
                }), 400
            result = client.place_limit_order(
                symbol=symbol,
                side=side,
                volume=float(volume),
                price=float(price),
                comment=comment,
            )
        else:
            result = client.place_market_order(
                symbol=symbol,
                side=side,
                volume=float(volume),
                comment=comment,
            )
        
        if result.success:
            return jsonify({
                "success": True,
                "order_id": result.order_id,
                "deal_id": result.deal_id,
                "filled": result.filled,
                "price": result.price,
                "status": result.status,
                "message": result.message,
            })
        else:
            return jsonify({
                "success": False,
                "error": result.message
            }), 400
            
    except Exception as e:
        logger.error(f"MT5 place order failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mt5_blp.route("/close", methods=["POST"])
@login_required
def close_position():
    """
    Close a position.
    
    Request body:
    {
        "ticket": 123456789,    // Position ticket
        "volume": 0.1           // Optional: partial close volume
    }
    """
    try:
        if not local_desktop_brokers_allowed():
            return jsonify({"success": False, "error": desktop_broker_cloud_reject_message()}), 403

        client = _get_client()
        if not client.connected:
            return jsonify({"success": False, "error": "Not connected to MT5"}), 400
        
        data = request.get_json() or {}
        
        ticket = data.get("ticket")
        volume = data.get("volume")
        
        if not ticket:
            return jsonify({
                "success": False,
                "error": "Missing required field: ticket"
            }), 400
        
        result = client.close_position(
            ticket=int(ticket),
            volume=float(volume) if volume else None,
        )
        
        if result.success:
            return jsonify({
                "success": True,
                "order_id": result.order_id,
                "deal_id": result.deal_id,
                "filled": result.filled,
                "price": result.price,
                "message": result.message,
            })
        else:
            return jsonify({
                "success": False,
                "error": result.message
            }), 400
            
    except Exception as e:
        logger.error(f"MT5 close position failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mt5_blp.route("/order/<int:ticket>", methods=["DELETE"])
@login_required
def cancel_order(ticket: int):
    """Cancel a pending order."""
    try:
        if not local_desktop_brokers_allowed():
            return jsonify({"success": False, "error": desktop_broker_cloud_reject_message()}), 403

        client = _get_client()
        if not client.connected:
            return jsonify({"success": False, "error": "Not connected to MT5"}), 400
        
        if client.cancel_order(ticket):
            return jsonify({"success": True, "message": f"Order {ticket} cancelled"})
        else:
            return jsonify({"success": False, "error": "Failed to cancel order"}), 400
            
    except Exception as e:
        logger.error(f"MT5 cancel order failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Market Data ====================

@mt5_blp.route("/quote", methods=["GET"])
@login_required
def get_quote():
    """
    Get real-time quote.
    
    Query params:
    - symbol: Trading symbol (e.g., EURUSD)
    """
    try:
        if not local_desktop_brokers_allowed():
            return jsonify({"success": False, "error": desktop_broker_cloud_reject_message()}), 403

        client = _get_client()
        if not client.connected:
            return jsonify({"success": False, "error": "Not connected to MT5"}), 400
        
        symbol = request.args.get("symbol")
        if not symbol:
            return jsonify({"success": False, "error": "Missing symbol parameter"}), 400
        
        quote = client.get_quote(symbol)
        return jsonify(quote)
        
    except Exception as e:
        logger.error(f"MT5 get quote failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# openapi-compat: legacy import name
mt5_bp = mt5_blp
