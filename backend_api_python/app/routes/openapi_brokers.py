from __future__ import annotations

import json
from typing import Any, Dict

from flask import g, jsonify, request

from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.services.openapi_brokers import TigerClient, TigerConfig, WebullClient, WebullConfig
from app.utils.auth import login_required
from app.utils.broker_session import BrokerSessionRegistry
from app.utils.credential_crypto import decrypt_credential_blob, encrypt_credential_blob
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.trading_venue_visibility import is_trading_venue_enabled

logger = get_logger(__name__)

openapi_brokers_blp = Blueprint("openapi_brokers", __name__)
_BROKERS = {"tiger", "webull"}
_sessions = {broker: BrokerSessionRegistry(broker) for broker in _BROKERS}
_PUBLIC_ERROR_KEYS = {
    key: key
    for key in (
        "brokerAccounts.accountSelectionRequired",
        "brokerAccounts.accountUnavailable",
        "brokerAccounts.brokerAccountRequired",
        "brokerAccounts.brokerAccountUnavailable",
        "brokerAccounts.credentialsRequired",
        "brokerAccounts.invalidLimitPrice",
        "brokerAccounts.invalidOrder",
        "brokerAccounts.noAuthorizedAccounts",
        "brokerAccounts.orderUnavailable",
        "brokerAccounts.tiger.invalidEnvironment",
        "brokerAccounts.tiger.invalidPrivateKey",
        "brokerAccounts.tiger.sdkUnavailable",
        "brokerAccounts.tradingVenueDisabled",
        "brokerAccounts.unsupportedBroker",
        "brokerAccounts.unsupportedOrderType",
        "brokerAccounts.webull.invalidEnvironment",
        "brokerAccounts.webull.usStocksOnly",
    )
}


def _public_error(exc: BaseException, fallback: str) -> str:
    value = exc.args[0] if exc.args else None
    if not isinstance(value, str):
        return fallback
    return _PUBLIC_ERROR_KEYS.get(value, fallback)


def _broker_id(value: str) -> str:
    broker = str(value or "").strip().lower()
    if broker not in _BROKERS:
        raise ValueError("brokerAccounts.unsupportedBroker")
    if not is_trading_venue_enabled(broker):
        raise ValueError("brokerAccounts.tradingVenueDisabled")
    return broker


def _hint(value: str) -> str:
    text = str(value or "").strip()
    if len(text) <= 8:
        return f"{text[:2]}***" if text else ""
    return f"{text[:4]}...{text[-4:]}"


def _credential_id() -> int | None:
    body = request.get_json(silent=True) or {}
    value = request.args.get("credential_id", body.get("credential_id"))
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("brokerAccounts.accountSelectionRequired") from exc
    if parsed <= 0:
        raise ValueError("brokerAccounts.accountSelectionRequired")
    return parsed


def _rows(user_id: int, broker: str) -> list:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, name, api_key_hint, encrypted_config
            FROM qd_exchange_credentials
            WHERE user_id = %s AND exchange_id = %s
            ORDER BY updated_at DESC NULLS LAST, id DESC
            """,
            (int(user_id), broker),
        )
        rows = cur.fetchall() or []
        cur.close()
    return rows


def _load_config(user_id: int, broker: str) -> dict:
    rows = _rows(user_id, broker)
    selected = _credential_id()
    if selected is not None:
        rows = [row for row in rows if int(row.get("id") or 0) == selected]
        if not rows:
            raise ValueError("brokerAccounts.accountUnavailable")
    elif len(rows) > 1:
        raise ValueError("brokerAccounts.accountSelectionRequired")
    if not rows:
        return {}
    try:
        payload = json.loads(decrypt_credential_blob(rows[0].get("encrypted_config")) or "{}")
    except Exception as exc:
        logger.warning("Failed to decrypt %s credential: %s", broker, exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    payload["credential_id"] = int(rows[0].get("id") or 0)
    return payload


def _request_config(broker: str, data: dict) -> dict:
    if broker == "tiger":
        tiger_id = str(data.get("tigerId") or data.get("tiger_id") or "").strip()
        account_id = str(data.get("accountId") or data.get("account_id") or "").strip()
        private_key = str(data.get("privateKey") or data.get("private_key") or "").strip()
        if not tiger_id or not account_id or not private_key:
            raise ValueError("brokerAccounts.credentialsRequired")
        if "BEGIN" not in private_key or "PRIVATE KEY" not in private_key or "END" not in private_key:
            raise ValueError("brokerAccounts.tiger.invalidPrivateKey")
        environment = str(data.get("environment") or "live").strip().lower()
        if environment not in ("live", "paper"):
            raise ValueError("brokerAccounts.tiger.invalidEnvironment")
        return {
            "exchange_id": broker,
            "tiger_id": tiger_id,
            "account_id": account_id,
            "private_key": private_key,
            "license": str(data.get("license") or "").strip(),
            "environment": environment,
            "market_category": "USStock",
            "market_type": "spot",
        }
    app_key = str(data.get("appKey") or data.get("app_key") or "").strip()
    app_secret = str(data.get("appSecret") or data.get("app_secret") or "").strip()
    if not app_key or not app_secret:
        raise ValueError("brokerAccounts.credentialsRequired")
    environment = str(data.get("environment") or "sandbox").strip().lower()
    if environment not in ("live", "sandbox"):
        raise ValueError("brokerAccounts.webull.invalidEnvironment")
    return {
        "exchange_id": broker,
        "app_key": app_key,
        "app_secret": app_secret,
        "access_token": str(data.get("accessToken") or data.get("access_token") or "").strip(),
        "account_id": str(data.get("accountId") or data.get("account_id") or "").strip(),
        "environment": environment,
        "market_category": "USStock",
        "market_type": "spot",
    }


def _client(broker: str, config: dict):
    if broker == "tiger":
        return TigerClient(TigerConfig(
            tiger_id=str(config.get("tiger_id") or ""),
            account_id=str(config.get("account_id") or ""),
            private_key=str(config.get("private_key") or ""),
            license=str(config.get("license") or ""),
            environment=str(config.get("environment") or "live"),
        ))
    return WebullClient(WebullConfig(
        app_key=str(config.get("app_key") or ""),
        app_secret=str(config.get("app_secret") or ""),
        access_token=str(config.get("access_token") or ""),
        environment=str(config.get("environment") or "sandbox"),
        account_id=str(config.get("account_id") or ""),
    ))


def _save(user_id: int, broker: str, config: dict, name: str) -> int:
    public_key = str(config.get("app_key") or config.get("tiger_id") or "")
    account_id = str(config.get("account_id") or "")
    key = f"{public_key}:{account_id}"
    environment = str(config.get("environment") or "live")
    encrypted = encrypt_credential_blob(json.dumps(config, ensure_ascii=False))
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, encrypted_config FROM qd_exchange_credentials
            WHERE user_id = %s AND exchange_id = %s ORDER BY id DESC
            """,
            (int(user_id), broker),
        )
        target_id = 0
        for row in cur.fetchall() or []:
            try:
                existing = json.loads(decrypt_credential_blob(row.get("encrypted_config")) or "{}")
                existing_public_key = str(existing.get("app_key") or existing.get("tiger_id") or "")
                existing_key = f"{existing_public_key}:{str(existing.get('account_id') or '')}"
                if existing_key == key and str(existing.get("environment") or "live") == environment:
                    target_id = int(row.get("id") or 0)
                    break
            except Exception:
                continue
        key_hint = f"{_hint(public_key)} ({environment})"
        display_name = str(name or "").strip()[:100] or f"{broker.title()} {environment.title()}"
        if target_id:
            cur.execute(
                """
                UPDATE qd_exchange_credentials
                SET name = %s, api_key_hint = %s, encrypted_config = %s, updated_at = NOW()
                WHERE id = %s AND user_id = %s
                """,
                (display_name, key_hint, encrypted, target_id, int(user_id)),
            )
            credential_id = target_id
        else:
            cur.execute(
                """
                INSERT INTO qd_exchange_credentials
                    (user_id, name, exchange_id, api_key_hint, encrypted_config, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW()) RETURNING id
                """,
                (int(user_id), display_name, broker, key_hint, encrypted),
            )
            credential_id = int((cur.fetchone() or {}).get("id") or 0)
        db.commit()
        cur.close()
    return credential_id


def _connected_client(broker: str):
    config = _load_config(int(g.user_id), broker)
    if not config:
        return None, (jsonify({"success": False, "error": "brokerAccounts.notConnected"}), 400)
    credential_id = int(config.get("credential_id") or 0)
    client = _sessions[broker].get(credential_id)
    if client is None or not client.connected:
        try:
            client = _client(broker, config)
            client.connect()
            _sessions[broker].set(client, credential_id)
        except Exception as exc:
            logger.warning("%s reconnect failed: %s", broker, exc)
            return None, (jsonify({
                "success": False,
                "error": _public_error(exc, "brokerAccounts.snapshotConnectionFailed"),
            }), 400)
    return client, None


@openapi_brokers_blp.route("/<broker>/accounts", methods=["GET"])
@login_required
def accounts(broker: str):
    try:
        broker = _broker_id(broker)
        return jsonify({"success": True, "data": [
            {key: row.get(key) for key in ("id", "name", "api_key_hint")}
            for row in _rows(int(g.user_id), broker)
        ]})
    except ValueError as exc:
        return jsonify({"success": False, "error": _public_error(
            exc, "brokerAccounts.accountLoadFailed"
        )}), 400


@openapi_brokers_blp.route("/<broker>/status", methods=["GET"])
@login_required
def status(broker: str):
    try:
        broker = _broker_id(broker)
        config = _load_config(int(g.user_id), broker)
        if not config:
            return jsonify({"success": True, "data": {"connected": False, "paper": False}})
        credential_id = int(config.get("credential_id") or 0)
        client = _sessions[broker].get(credential_id)
        if client is None or not client.connected:
            try:
                client = _client(broker, config)
                client.connect()
                _sessions[broker].set(client, credential_id)
            except Exception as exc:
                logger.warning("%s status refresh failed: %s", broker, exc)
                return jsonify({"success": True, "data": {
                    "connected": False,
                    "saved": True,
                    "credential_id": credential_id,
                    "error": _public_error(exc, "brokerAccounts.snapshotConnectionFailed"),
                    "paper": str(config.get("environment") or "").strip().lower() in ("sandbox", "paper", "demo"),
                }})
        data = client.get_connection_status()
        data.update({"saved": True, "credential_id": credential_id})
        return jsonify({"success": True, "data": data})
    except ValueError as exc:
        return jsonify({"success": False, "error": _public_error(
            exc, "brokerAccounts.snapshotConnectionFailed"
        )}), 400


@openapi_brokers_blp.route("/<broker>/connect", methods=["POST"])
@login_required
def connect(broker: str):
    try:
        broker = _broker_id(broker)
        data = request.get_json(silent=True) or {}
        config = _request_config(broker, data)
        client = _client(broker, config)
        client.connect()
        config["account_id"] = client.account_id
        credential_id = _save(int(g.user_id), broker, config, str(data.get("name") or ""))
        _sessions[broker].set(client, credential_id)
        status_data = client.get_connection_status()
        status_data.update({"saved": True, "credential_id": credential_id})
        return jsonify({"success": True, "message": "brokerAccounts.connectSuccess", "data": status_data})
    except (ValueError, RuntimeError) as exc:
        logger.warning("%s connection failed: %s", broker, exc)
        return jsonify({"success": False, "error": _public_error(
            exc, "brokerAccounts.snapshotConnectionFailed"
        )}), 400
    except Exception:
        logger.exception("Broker connection failed")
        return jsonify({
            "success": False,
            "error": "brokerAccounts.snapshotConnectionFailed",
        }), 500


@openapi_brokers_blp.route("/<broker>/disconnect", methods=["POST"])
@login_required
def disconnect(broker: str):
    try:
        broker = _broker_id(broker)
        config = _load_config(int(g.user_id), broker)
        if config:
            _sessions[broker].disconnect_current(int(config.get("credential_id") or 0))
        return jsonify({"success": True, "message": "brokerAccounts.disconnectSuccess"})
    except ValueError as exc:
        return jsonify({"success": False, "error": _public_error(
            exc, "brokerAccounts.snapshotConnectionFailed"
        )}), 400


@openapi_brokers_blp.route("/<broker>/account", methods=["GET"])
@login_required
def account(broker: str):
    try:
        broker = _broker_id(broker)
        client, error = _connected_client(broker)
        if error:
            return error
        return jsonify({"success": True, "data": client.get_account_summary()})
    except Exception as exc:
        logger.warning("%s account summary failed: %s", broker, exc)
        return jsonify({"success": False, "error": "brokerAccounts.accountLoadFailed"}), 500


@openapi_brokers_blp.route("/<broker>/positions", methods=["GET"])
@login_required
def positions(broker: str):
    try:
        broker = _broker_id(broker)
        client, error = _connected_client(broker)
        if error:
            return error
        return jsonify({"success": True, "data": client.get_positions()})
    except Exception as exc:
        logger.warning("%s positions request failed: %s", broker, exc)
        return jsonify({"success": False, "error": "brokerAccounts.snapshotPositionsFailed"}), 500


@openapi_brokers_blp.route("/<broker>/orders", methods=["GET"])
@login_required
def orders(broker: str):
    try:
        broker = _broker_id(broker)
        client, error = _connected_client(broker)
        if error:
            return error
        return jsonify({"success": True, "data": client.get_orders(
            status=str(request.args.get("status") or "all"),
            limit=int(request.args.get("limit") or 100),
            symbol=str(request.args.get("symbol") or ""),
        )})
    except Exception as exc:
        logger.warning("%s orders request failed: %s", broker, exc)
        return jsonify({"success": False, "error": "brokerAccounts.ordersLoadFailed"}), 500


@openapi_brokers_blp.route("/<broker>/fills", methods=["GET"])
@login_required
def fills(broker: str):
    try:
        broker = _broker_id(broker)
        client, error = _connected_client(broker)
        if error:
            return error
        data = None
        if hasattr(client, "get_transactions"):
            try:
                data = client.get_transactions(
                    symbol=str(request.args.get("symbol") or ""),
                    limit=int(request.args.get("limit") or 100),
                )
            except Exception as exc:
                logger.info("Transaction history unavailable for %s; using filled orders: %s", broker, exc)
        if data is None:
            symbol = str(request.args.get("symbol") or "").strip().upper()
            rows = client.get_orders(
                status="filled",
                limit=int(request.args.get("limit") or 100),
                symbol=symbol,
            )
            data = [row for row in rows if str(row.get("status") or "").lower() == "filled"]
            if symbol:
                data = [row for row in data if str(row.get("symbol") or "").strip().upper() == symbol]
        return jsonify({"success": True, "data": data})
    except Exception as exc:
        logger.warning("%s fills request failed: %s", broker, exc)
        return jsonify({"success": False, "error": "brokerAccounts.ordersLoadFailed"}), 500


@openapi_brokers_blp.route("/<broker>/order", methods=["POST"])
@login_required
def place_order(broker: str):
    try:
        broker = _broker_id(broker)
        client, error = _connected_client(broker)
        if error:
            return error
        data = request.get_json(silent=True) or {}
        symbol = str(data.get("symbol") or "").strip()
        side = str(data.get("side") or "").strip().lower()
        order_type = str(data.get("orderType") or data.get("order_type") or "market").strip().lower()
        quantity = float(data.get("quantity") or data.get("size") or 0)
        price = float(data.get("price") or data.get("limitPrice") or 0)
        if not symbol or side not in ("buy", "sell") or quantity <= 0:
            raise ValueError("brokerAccounts.invalidOrder")
        if order_type == "limit":
            if price <= 0:
                raise ValueError("brokerAccounts.invalidLimitPrice")
            result = client.place_limit_order(symbol=symbol, side=side, quantity=quantity, price=price,
                                                market_type=str(data.get("marketType") or "USStock"))
        elif order_type == "market":
            result = client.place_market_order(symbol=symbol, side=side, quantity=quantity,
                                                 market_type=str(data.get("marketType") or "USStock"))
        else:
            raise ValueError("brokerAccounts.unsupportedOrderType")
        return jsonify({"success": result.success, "message": result.message, "data": {
            "orderId": result.order_id,
            "filled": result.filled,
            "avgPrice": result.avg_price,
            "status": result.status,
            "raw": result.raw,
        }})
    except ValueError as exc:
        return jsonify({"success": False, "error": _public_error(
            exc, "brokerAccounts.orderFailed"
        )}), 400
    except Exception as exc:
        logger.warning("%s order failed: %s", broker, exc)
        return jsonify({"success": False, "error": _public_error(
            exc, "brokerAccounts.orderFailed"
        )}), 502


@openapi_brokers_blp.route("/<broker>/order/<order_id>", methods=["DELETE"])
@login_required
def cancel_order(broker: str, order_id: str):
    try:
        broker = _broker_id(broker)
        client, error = _connected_client(broker)
        if error:
            return error
        return jsonify({"success": bool(client.cancel_order(order_id)), "message": "brokerAccounts.cancelRequested"})
    except Exception as exc:
        logger.warning("%s order cancellation failed: %s", broker, exc)
        return jsonify({"success": False, "error": _public_error(
            exc, "brokerAccounts.orderFailed"
        )}), 502
