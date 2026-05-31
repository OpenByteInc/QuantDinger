"""
Alpaca Markets API Routes

Standalone API endpoints for US stocks, ETFs, and crypto trading via Alpaca.
Mirrors the structure of routes/ibkr.py for consistency.

Multi-tenancy: connections are isolated per authenticated user via
:class:`BrokerSessionRegistry` instead of a process-wide global, so users
cannot accidentally place orders through someone else's Alpaca account.
"""

from flask import jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.utils.auth import login_required
from app.utils.logger import get_logger
from app.utils.broker_session import BrokerSessionRegistry
from app.services.alpaca_trading import AlpacaClient, AlpacaConfig

logger = get_logger(__name__)

alpaca_blp = Blueprint('alpaca', __name__)

# Per-user client cache keyed by (user_id, 'alpaca')
_sessions = BrokerSessionRegistry('alpaca')


def _placeholder_status():
    """Return a stable 'not connected' status when no client exists yet."""
    return {
        "connected": False,
        "paper": True,
        "base_url": "https://paper-api.alpaca.markets",
        "account_id": None,
    }


# ==================== Connection Management ====================

@alpaca_blp.route('/status', methods=['GET'])
@login_required
def get_status():
    """Get Alpaca connection status."""
    try:
        client = _sessions.get()
        if client is None:
            return jsonify({"success": True, "data": _placeholder_status()})
        return jsonify({"success": True, "data": client.get_connection_status()})
    except Exception as e:
        logger.error(f"Get status failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@alpaca_blp.route('/connect', methods=['POST'])
@login_required
def connect():
    """
    Connect to Alpaca.

    Request body:
        apiKey (required): API key (PK prefix = paper, AK = live)
        secretKey (required): Secret key
        paper (optional, default true): Use paper trading
        baseUrl (optional): Override API base URL
    """
    try:
        data = request.get_json() or {}
        api_key = data.get('apiKey', '')
        secret_key = data.get('secretKey', '')
        if not api_key or not secret_key:
            return jsonify({"success": False, "error": "apiKey and secretKey required"}), 400

        config = AlpacaConfig(
            api_key=api_key,
            secret_key=secret_key,
            paper=bool(data.get('paper', True)),
            base_url=data.get('baseUrl') or None,
        )

        client = AlpacaClient(config)
        success = client.connect()
        if success:
            _sessions.set(client)
            return jsonify({
                "success": True,
                "message": "Connected successfully",
                "data": client.get_connection_status(),
            })
        return jsonify({
            "success": False,
            "error": "Connection failed. Verify API keys and network access to api.alpaca.markets.",
        }), 400
    except ImportError:
        return jsonify({
            "success": False,
            "error": "alpaca-py not installed. Run: pip install alpaca-py",
        }), 500
    except Exception as e:
        logger.error(f"Alpaca connection failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@alpaca_blp.route('/disconnect', methods=['POST'])
@login_required
def disconnect():
    """Disconnect from Alpaca."""
    try:
        _sessions.disconnect_current()
        return jsonify({"success": True, "message": "Disconnected"})
    except Exception as e:
        logger.error(f"Disconnect failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Account Queries ====================

def _require_connected_client():
    client = _sessions.get()
    if client is None or not client.connected:
        return None, (jsonify({"success": False, "error": "Not connected to Alpaca"}), 400)
    return client, None


@alpaca_blp.route('/account', methods=['GET'])
@login_required
def get_account():
    """Get Alpaca account information."""
    try:
        client, err = _require_connected_client()
        if err is not None:
            return err
        return jsonify({"success": True, "data": client.get_account_summary()})
    except Exception as e:
        logger.error(f"Get account info failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@alpaca_blp.route('/positions', methods=['GET'])
@login_required
def get_positions():
    """Get Alpaca open positions."""
    try:
        client, err = _require_connected_client()
        if err is not None:
            return err
        return jsonify({"success": True, "data": client.get_positions()})
    except Exception as e:
        logger.error(f"Get positions failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@alpaca_blp.route('/orders', methods=['GET'])
@login_required
def get_orders():
    """Get Alpaca open orders."""
    try:
        client, err = _require_connected_client()
        if err is not None:
            return err
        return jsonify({"success": True, "data": client.get_open_orders()})
    except Exception as e:
        logger.error(f"Get orders failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Trading ====================

@alpaca_blp.route('/order', methods=['POST'])
@login_required
def place_order():
    """
    Place an Alpaca order.

    Request body:
        symbol (required): Ticker, e.g. AAPL
        side (required): buy or sell
        quantity (required): Share quantity
        marketType (optional): USStock or crypto (default USStock)
        orderType (optional): market or limit (default market)
        price (required for limit): Limit price
        extendedHours (optional): Allow extended-hours limit orders
    """
    try:
        client, err = _require_connected_client()
        if err is not None:
            return err

        data = request.get_json() or {}
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
        order_type = (data.get('orderType') or 'market').lower()

        if order_type == 'limit':
            price = data.get('price')
            if not price or float(price) <= 0:
                return jsonify({"success": False, "error": "Limit order requires price"}), 400
            result = client.place_limit_order(
                symbol=symbol, side=side, quantity=float(quantity), price=float(price),
                market_type=market_type, extended_hours=bool(data.get('extendedHours', False)),
            )
        else:
            result = client.place_market_order(
                symbol=symbol, side=side, quantity=float(quantity), market_type=market_type,
            )

        if result.success:
            return jsonify({
                "success": True,
                "message": result.message,
                "data": {
                    "orderId": result.order_id, "filled": result.filled,
                    "avgPrice": result.avg_price, "status": result.status, "raw": result.raw,
                },
            })
        return jsonify({"success": False, "error": result.message, "data": result.raw}), 400
    except Exception as e:
        logger.error(f"Place order failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@alpaca_blp.route('/order/<order_id>', methods=['DELETE'])
@login_required
def cancel_order(order_id):
    """Cancel an Alpaca order by ID."""
    try:
        client, err = _require_connected_client()
        if err is not None:
            return err
        ok = client.cancel_order(order_id)
        return jsonify({"success": ok, "message": "Cancelled" if ok else "Cancel failed"})
    except Exception as e:
        logger.error(f"Cancel order failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Market Data ====================

@alpaca_blp.route('/quote/<symbol>', methods=['GET'])
@login_required
def get_quote(symbol):
    """
    Get a real-time Alpaca quote.

    Query params:
        marketType (optional): USStock or crypto (default USStock)
    """
    try:
        client, err = _require_connected_client()
        if err is not None:
            return err
        market_type = request.args.get('marketType', 'USStock')
        result = client.get_quote(symbol, market_type=market_type)
        if result.get('success'):
            return jsonify({"success": True, "data": result})
        return jsonify({"success": False, "error": result.get('error', 'Quote failed')}), 400
    except Exception as e:
        logger.error(f"Get quote failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# openapi-compat: legacy import name
alpaca_bp = alpaca_blp
