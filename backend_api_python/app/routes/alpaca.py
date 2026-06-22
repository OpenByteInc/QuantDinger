"""
Alpaca Markets API Routes

Standalone API endpoints for US stocks, ETFs, and crypto trading via Alpaca.
Mirrors the structure of routes/ibkr.py for consistency.

Multi-tenancy: connections are isolated per authenticated user via
:class:`BrokerSessionRegistry` instead of a process-wide global, so users
cannot accidentally place orders through someone else's Alpaca account.
"""

import json

from flask import g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.utils.auth import login_required
from app.utils.logger import get_logger
from app.utils.broker_session import BrokerSessionRegistry
from app.utils.credential_crypto import decrypt_credential_blob, encrypt_credential_blob
from app.utils.db import get_db_connection
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


def _api_key_hint(api_key: str) -> str:
    if not api_key:
        return ""
    s = str(api_key)
    if len(s) <= 8:
        return s[:2] + "***"
    return f"{s[:4]}...{s[-4:]}"


def _alpaca_env_tag(api_key: str) -> str:
    return "paper" if str(api_key or "").upper().startswith("PK") else "live"


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "paper")
    return bool(value)


def _config_from_request(data: dict) -> AlpacaConfig:
    api_key = str(data.get("apiKey") or data.get("api_key") or "").strip()
    secret_key = str(data.get("secretKey") or data.get("secret_key") or "").strip()
    if not api_key or not secret_key:
        raise ValueError("apiKey and secretKey required")
    paper_raw = data.get("paper")
    if paper_raw is None:
        paper = api_key.upper().startswith("PK")
    else:
        paper = _as_bool(paper_raw)
    return AlpacaConfig(
        api_key=api_key,
        secret_key=secret_key,
        paper=paper,
        base_url=data.get("baseUrl") or data.get("base_url") or None,
    )


def _config_to_vault_dict(config: AlpacaConfig) -> dict:
    return {
        "exchange_id": "alpaca",
        "api_key": config.api_key,
        "secret_key": config.secret_key,
        "paper": bool(config.paper),
        "base_url": config.base_url or "",
        "market_category": "USStock",
        "market_type": "spot",
    }


def _load_saved_alpaca_config(user_id: int) -> dict:
    """Load the most recent saved Alpaca credential for this user."""
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, encrypted_config
            FROM qd_exchange_credentials
            WHERE user_id = %s AND exchange_id = 'alpaca'
            ORDER BY updated_at DESC NULLS LAST, id DESC
            LIMIT 1
            """,
            (int(user_id),),
        )
        row = cur.fetchone() or {}
        cur.close()
    if not row:
        return {}
    try:
        plain = decrypt_credential_blob(row.get("encrypted_config"))
        cfg = json.loads(plain) if plain else {}
        if not isinstance(cfg, dict):
            return {}
        cfg["credential_id"] = int(row.get("id") or 0)
        return cfg
    except Exception as e:
        logger.warning("Failed to load saved Alpaca credential: %s", e)
        return {}


def _save_or_update_alpaca_credential(user_id: int, config: AlpacaConfig) -> int:
    """Persist the connected Alpaca credential so refresh/restart can restore it."""
    vault_cfg = _config_to_vault_dict(config)
    api_key = str(config.api_key or "").strip()
    hint = f"{_api_key_hint(api_key)} ({_alpaca_env_tag(api_key)})"
    encrypted = encrypt_credential_blob(json.dumps(vault_cfg, ensure_ascii=False))

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, encrypted_config
            FROM qd_exchange_credentials
            WHERE user_id = %s AND exchange_id = 'alpaca'
            ORDER BY id DESC
            """,
            (int(user_id),),
        )
        rows = cur.fetchall() or []
        target_id = 0
        for row in rows:
            try:
                plain = decrypt_credential_blob(row.get("encrypted_config"))
                existing = json.loads(plain) if plain else {}
                if isinstance(existing, dict) and str(existing.get("api_key") or "").strip() == api_key:
                    target_id = int(row.get("id") or 0)
                    break
            except Exception:
                continue

        if target_id > 0:
            cur.execute(
                """
                UPDATE qd_exchange_credentials
                SET api_key_hint = %s,
                    encrypted_config = %s,
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s
                """,
                (hint, encrypted, target_id, int(user_id)),
            )
            cred_id = target_id
        else:
            cur.execute(
                """
                INSERT INTO qd_exchange_credentials
                    (user_id, name, exchange_id, api_key_hint, encrypted_config, created_at, updated_at)
                VALUES (%s, %s, 'alpaca', %s, %s, NOW(), NOW())
                RETURNING id
                """,
                (int(user_id), f"Alpaca {_alpaca_env_tag(api_key).title()}", hint, encrypted),
            )
            cred_id = int((cur.fetchone() or {}).get("id") or 0)
        db.commit()
        cur.close()
    return cred_id


def _client_from_saved_credential():
    user_id = int(getattr(g, "user_id", 1) or 1)
    cfg = _load_saved_alpaca_config(user_id)
    if not cfg:
        return None
    config = AlpacaConfig(
        api_key=str(cfg.get("api_key") or cfg.get("apiKey") or "").strip(),
        secret_key=str(cfg.get("secret_key") or cfg.get("secretKey") or cfg.get("secret") or "").strip(),
        paper=_as_bool(cfg.get("paper"), str(cfg.get("api_key") or "").upper().startswith("PK")),
        base_url=cfg.get("base_url") or cfg.get("baseUrl") or None,
    )
    if not config.api_key or not config.secret_key:
        return None
    client = AlpacaClient(config)
    if not client.connect():
        return None
    _sessions.set(client)
    return client


# ==================== Connection Management ====================

@alpaca_blp.route('/status', methods=['GET'])
@login_required
def get_status():
    """Get Alpaca connection status."""
    try:
        client = _sessions.get()
        if client is None:
            client = _client_from_saved_credential()
        if client is None:
            saved = _load_saved_alpaca_config(int(getattr(g, "user_id", 1) or 1))
            data = _placeholder_status()
            if saved:
                data.update(
                    {
                        "saved": True,
                        "credential_id": saved.get("credential_id") or 0,
                        "paper": _as_bool(saved.get("paper"), str(saved.get("api_key") or "").upper().startswith("PK")),
                    }
                )
            return jsonify({"success": True, "data": data})
        data = client.get_connection_status()
        saved = _load_saved_alpaca_config(int(getattr(g, "user_id", 1) or 1))
        if saved:
            data["credential_id"] = saved.get("credential_id") or 0
            data["saved"] = True
        return jsonify({"success": True, "data": data})
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
        try:
            config = _config_from_request(data)
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 400

        client = AlpacaClient(config)
        success = client.connect()
        if success:
            cred_id = _save_or_update_alpaca_credential(int(getattr(g, "user_id", 1) or 1), config)
            _sessions.set(client)
            status = client.get_connection_status()
            status["credential_id"] = cred_id
            status["saved"] = True
            return jsonify({
                "success": True,
                "message": "Connected successfully",
                "data": status,
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
        client = _client_from_saved_credential()
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
