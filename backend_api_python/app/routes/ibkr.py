"""
Interactive Brokers API Routes

Standalone API endpoints for US stock trading.

Multi-tenancy: connections are isolated per authenticated user via
:class:`BrokerSessionRegistry` so users cannot accidentally place orders
through someone else's IBKR/TWS account.
"""

from flask import jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.utils.auth import login_required

from app.utils.logger import get_logger
from app.utils.broker_session import BrokerSessionRegistry
from app.services.ibkr_trading import IBKRClient, IBKRConfig

logger = get_logger(__name__)

ibkr_blp = Blueprint('ibkr', __name__)

# Per-user client cache keyed by (user_id, 'ibkr')
_sessions = BrokerSessionRegistry('ibkr')


def _placeholder_status():
    """Return a stable 'not connected' status when no client exists yet."""
    return {
        "connected": False,
        "host": "",
        "port": 0,
        "client_id": 0,
        "account": "",
    }


def _require_connected_client():
    client = _sessions.get()
    if client is None or not client.connected:
        return None, (jsonify({"success": False, "error": "Not connected to IBKR"}), 400)
    return client, None


# ==================== Connection Management ====================

@ibkr_blp.route('/status', methods=['GET'])
@login_required
def get_status():
    """Get IBKR connection status."""
    try:
        client = _sessions.get()
        if client is None:
            return jsonify({"success": True, "data": _placeholder_status()})
        return jsonify({
            "success": True,
            "data": client.get_connection_status()
        })
    except Exception as e:
        logger.error(f"Get status failed: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@ibkr_blp.route('/connect', methods=['POST'])
@login_required
def connect():
    """
    Connect to TWS / IB Gateway.

    Request body:
        host (optional, default 127.0.0.1): TWS/Gateway host
        port (optional, default 7497): TWS Live 7497, TWS Paper 7496, Gateway Live 4001, Gateway Paper 4002
        clientId (optional, default 1): Client ID
        account (optional): Account for multi-account setups
        readonly (optional, default false): Read-only mode
    """
    try:
        data = request.get_json() or {}

        config = IBKRConfig(
            host=data.get('host', '127.0.0.1'),
            port=int(data.get('port', 7497)),
            client_id=int(data.get('clientId', 1)),
            account=data.get('account', ''),
            readonly=data.get('readonly', False),
        )

        client = IBKRClient(config)
        success = client.connect()

        if success:
            _sessions.set(client)
            return jsonify({
                "success": True,
                "message": "Connected successfully",
                "data": client.get_connection_status()
            })
        else:
            return jsonify({
                "success": False,
                "error": "Connection failed. Please check if TWS/Gateway is running."
            }), 400

    except ImportError:
        return jsonify({
            "success": False,
            "error": "ib_insync not installed. Run: pip install ib_insync"
        }), 500
    except Exception as e:
        logger.error(f"Connection failed: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@ibkr_blp.route('/disconnect', methods=['POST'])
@login_required
def disconnect():
    """Disconnect from IBKR."""
    try:
        _sessions.disconnect_current()
        return jsonify({
            "success": True,
            "message": "Disconnected"
        })
    except Exception as e:
        logger.error(f"Disconnect failed: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ==================== Account Queries ====================

@ibkr_blp.route('/account', methods=['GET'])
@login_required
def get_account():
    """Get IBKR account information."""
    try:
        client, err = _require_connected_client()
        if err is not None:
            return err

        return jsonify({
            "success": True,
            "data": client.get_account_summary()
        })
    except Exception as e:
        logger.error(f"Get account info failed: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@ibkr_blp.route('/positions', methods=['GET'])
@login_required
def get_positions():
    """Get IBKR positions."""
    try:
        client, err = _require_connected_client()
        if err is not None:
            return err

        positions = client.get_positions()
        return jsonify({
            "success": True,
            "data": positions
        })
    except Exception as e:
        logger.error(f"Get positions failed: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@ibkr_blp.route('/orders', methods=['GET'])
@login_required
def get_orders():
    """Get open IBKR orders."""
    try:
        client, err = _require_connected_client()
        if err is not None:
            return err

        orders = client.get_open_orders()
        return jsonify({
            "success": True,
            "data": orders
        })
    except Exception as e:
        logger.error(f"Get orders failed: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ==================== Trading ====================

@ibkr_blp.route('/order', methods=['POST'])
@login_required
def place_order():
    """
    Place an IBKR order.

    Request body:
        symbol (required): Ticker, e.g. AAPL
        side (required): buy or sell
        quantity (required): Number of shares
        marketType (optional, default USStock): Market type
        orderType (optional, default market): market or limit
        price (required for limit orders): Limit price
    """
    try:
        client, err = _require_connected_client()
        if err is not None:
            return err

        data = request.get_json() or {}

        # Validate parameters
        symbol = data.get('symbol')
        side = data.get('side')
        quantity = data.get('quantity')

        if not symbol:
            return jsonify({"success": False, "error": "Missing symbol"}), 400
        if not side or side.lower() not in ('buy', 'sell'):
            return jsonify({"success": False, "error": "side must be buy or sell"}), 400
        if not quantity or float(quantity) <= 0:
            return jsonify({"success": False, "error": "quantity must be > 0"}), 400

        market_type = data.get('marketType', 'USStock')
        order_type = data.get('orderType', 'market').lower()

        # Place order
        if order_type == 'limit':
            price = data.get('price')
            if not price or float(price) <= 0:
                return jsonify({"success": False, "error": "Limit order requires price"}), 400

            result = client.place_limit_order(
                symbol=symbol,
                side=side,
                quantity=float(quantity),
                price=float(price),
                market_type=market_type
            )
        else:
            result = client.place_market_order(
                symbol=symbol,
                side=side,
                quantity=float(quantity),
                market_type=market_type
            )

        if result.success:
            return jsonify({
                "success": True,
                "message": result.message,
                "data": {
                    "orderId": result.order_id,
                    "filled": result.filled,
                    "avgPrice": result.avg_price,
                    "status": result.status,
                    "raw": result.raw
                }
            })
        else:
            return jsonify({
                "success": False,
                "error": result.message
            }), 400

    except Exception as e:
        logger.error(f"Place order failed: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@ibkr_blp.route('/order/<int:order_id>', methods=['DELETE'])
@login_required
def cancel_order(order_id: int):
    """Cancel an IBKR order by ID."""
    try:
        client, err = _require_connected_client()
        if err is not None:
            return err

        success = client.cancel_order(order_id)

        if success:
            return jsonify({
                "success": True,
                "message": f"Order {order_id} cancelled"
            })
        else:
            return jsonify({
                "success": False,
                "error": f"Order {order_id} not found"
            }), 404

    except Exception as e:
        logger.error(f"Cancel order failed: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ==================== Market Data ====================

@ibkr_blp.route('/quote', methods=['GET'])
@login_required
def get_quote():
    """Get real-time IBKR quote (query: symbol, marketType)."""
    try:
        client, err = _require_connected_client()
        if err is not None:
            return err

        symbol = request.args.get('symbol')
        market_type = request.args.get('marketType', 'USStock')

        if not symbol:
            return jsonify({"success": False, "error": "Missing symbol"}), 400

        quote = client.get_quote(symbol, market_type)
        return jsonify(quote)

    except Exception as e:
        logger.error(f"Get quote failed: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# openapi-compat: legacy import name
ibkr_bp = ibkr_blp
