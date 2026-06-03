"""
Exchange credentials vault.

encrypted_config stores Fernet ciphertext derived from SECRET_KEY (see app.utils.credential_crypto).
"""

import traceback
import json
from flask import g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint

import requests as rq

from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.auth import login_required
from app.utils.credential_crypto import encrypt_credential_blob, decrypt_credential_blob
from app.services.live_trading.factory import exchange_demo_mode_enabled
from app.services.live_trading.capabilities import supported_crypto_exchange_ids

logger = get_logger(__name__)

credentials_blp = Blueprint('credentials', __name__)


@credentials_blp.route('/desktop-brokers-policy', methods=['GET'])
@login_required
def desktop_brokers_policy():
    """
    Whether IBKR / MT5 (local TWS or MT5 terminal) may be configured on this deployment.
    Frontend uses this to disable options and show guidance before save/test.
    """
    from app.utils.local_brokers import desktop_broker_cloud_reject_message, local_desktop_brokers_allowed

    allowed = local_desktop_brokers_allowed()
    return jsonify(
        {
            'code': 1,
            'msg': 'success',
            'data': {
                'allow_local_desktop_brokers': allowed,
                'disabled_message': None if allowed else desktop_broker_cloud_reject_message(),
            },
        }
    )


def _api_key_hint(api_key: str) -> str:
    if not api_key:
        return ''
    s = str(api_key)
    if len(s) <= 8:
        return s[:2] + '***'
    return f"{s[:4]}...{s[-4:]}"


@credentials_blp.route('/list', methods=['GET'])
@login_required
def list_credentials():
    """List all credentials for the current user."""
    try:
        user_id = g.user_id

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, user_id, name, exchange_id, api_key_hint, encrypted_config, created_at, updated_at
                FROM qd_exchange_credentials
                WHERE user_id = %s
                ORDER BY id DESC
                """,
                (user_id,)
            )
            rows = cur.fetchall() or []
            cur.close()

        items = []
        for row in rows:
            item = dict(row or {})
            item['enable_demo_trading'] = False
            try:
                plain = decrypt_credential_blob(item.get('encrypted_config'))
                cfg = json.loads(plain) if plain else {}
                item['enable_demo_trading'] = exchange_demo_mode_enabled(cfg if isinstance(cfg, dict) else {})
            except Exception:
                item['enable_demo_trading'] = False
            item.pop('encrypted_config', None)
            items.append(item)

        return jsonify({'code': 1, 'msg': 'success', 'data': {'items': items}})
    except Exception as e:
        logger.error(f"list_credentials failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': {'items': []}}), 500


CRYPTO_EXCHANGES = sorted(supported_crypto_exchange_ids(include_aliases=True))


def _egress_ipify(url: str) -> str:
    try:
        r = rq.get(url, timeout=8)
        if r.status_code != 200:
            return ""
        j = r.json()
        if not isinstance(j, dict):
            return ""
        return str(j.get("ip") or "").strip()
    except Exception:
        return ""


@credentials_blp.route('/egress-ip', methods=['GET'])
@login_required
def get_egress_ip():
    """
    Public egress IPv4/IPv6 of this API server (for exchange API key IP whitelist).
    Uses ipify's v4-only / v6-only endpoints so each family is detected independently.
    """
    ipv4 = _egress_ipify("https://api4.ipify.org?format=json")
    ipv6 = _egress_ipify("https://api6.ipify.org?format=json")
    return jsonify(
        {
            "code": 1,
            "msg": "success",
            "data": {
                "ipv4": ipv4 or None,
                "ipv6": ipv6 or None,
                # 兼容旧前端：优先 IPv4，否则 IPv6
                "ip": ipv4 or ipv6 or None,
            },
        }
    )


@credentials_blp.route('/create', methods=['POST'])
@login_required
def create_credential():
    """Create a new credential for the current user.

    Supports crypto exchanges, IBKR (US stocks) and MT5 (Forex).
    """
    try:
        user_id = g.user_id
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        exchange_id = (data.get('exchange_id') or '').strip().lower()

        if not exchange_id:
            return jsonify({'code': 0, 'msg': 'Missing exchange_id', 'data': None}), 400

        if exchange_id in ('ibkr', 'mt5'):
            from app.utils.local_brokers import desktop_broker_cloud_reject_message, local_desktop_brokers_allowed

            if not local_desktop_brokers_allowed():
                return jsonify({'code': 0, 'msg': desktop_broker_cloud_reject_message(), 'data': None}), 403

        config = {'exchange_id': exchange_id}
        hint = ''

        if exchange_id == 'alpaca':
            # Alpaca: REST-only broker (no local terminal). Paper/live is decided
            # by the API key prefix at runtime — PK* hits paper-api.alpaca.markets,
            # AK* hits api.alpaca.markets. We deliberately do NOT expose a paper
            # toggle in the UI: the user provides whichever key matches the env
            # they want to trade in, and factory.create_alpaca_client routes
            # automatically. base_url is still accepted as an explicit override
            # (rare — only useful behind a corporate proxy or for unit tests).
            api_key = (data.get('api_key') or data.get('apiKey') or '').strip()
            secret_key = (data.get('secret_key') or data.get('secretKey') or '').strip()
            if not api_key or not secret_key:
                return jsonify({'code': 0, 'msg': 'Missing api_key/secret_key', 'data': None}), 400

            config.update({
                'api_key': api_key,
                'secret_key': secret_key,
                'base_url': (data.get('base_url') or data.get('baseUrl') or '').strip(),
            })
            # Surface the inferred env in the hint so the credential list still
            # tells users at a glance whether this key targets paper or live.
            env_tag = 'paper' if api_key.upper().startswith('PK') else 'live'
            hint = f"{_api_key_hint(api_key)} ({env_tag})"
        elif exchange_id == 'ibkr':
            # Interactive Brokers (US stocks)
            # clientId must differ from manual /api/ibkr/connect (defaults to 1) or TWS drops one session.
            _ib_cid = data.get('ibkr_client_id')
            try:
                ibkr_client_id = int(_ib_cid) if _ib_cid not in (None, '') else 7
            except (TypeError, ValueError):
                ibkr_client_id = 7
            config.update({
                'ibkr_host': (data.get('ibkr_host') or '127.0.0.1').strip(),
                'ibkr_port': int(data.get('ibkr_port') or 7497),
                'ibkr_client_id': ibkr_client_id,
                'ibkr_account': (data.get('ibkr_account') or '').strip()
            })
            hint = f"{config['ibkr_host']}:{config['ibkr_port']}"
        elif exchange_id == 'mt5':
            # MetaTrader 5 (Forex)
            mt5_server = (data.get('mt5_server') or '').strip()
            mt5_login = str(data.get('mt5_login') or '').strip()
            mt5_password = (data.get('mt5_password') or '').strip()
            if not mt5_server or not mt5_login or not mt5_password:
                return jsonify({'code': 0, 'msg': 'Missing mt5_server/mt5_login/mt5_password', 'data': None}), 400
            config.update({
                'mt5_server': mt5_server,
                'mt5_login': mt5_login,
                'mt5_password': mt5_password,
                'mt5_terminal_path': (data.get('mt5_terminal_path') or '').strip()
            })
            hint = f"{mt5_server}/{mt5_login}"
        elif exchange_id in CRYPTO_EXCHANGES:
            # Crypto exchanges
            api_key = (data.get('api_key') or '').strip()
            secret_key = (data.get('secret_key') or '').strip()
            if not api_key or not secret_key:
                return jsonify({'code': 0, 'msg': 'Missing api_key/secret_key', 'data': None}), 400
            config.update({
                'api_key': api_key,
                'secret_key': secret_key,
                'passphrase': (data.get('passphrase') or '').strip(),
                'enable_demo_trading': exchange_demo_mode_enabled(data),
            })
            hint = _api_key_hint(api_key)
        else:
            return jsonify({'code': 0, 'msg': f'Unsupported exchange: {exchange_id}', 'data': None}), 400

        plaintext_config = json.dumps(config, ensure_ascii=False)
        stored_blob = encrypt_credential_blob(plaintext_config)

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_exchange_credentials (user_id, name, exchange_id, api_key_hint, encrypted_config, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id
                """,
                (user_id, name, exchange_id, hint, stored_blob)
            )
            row = cur.fetchone()
            new_id = (row or {}).get('id')
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success', 'data': {'id': new_id}})
    except Exception as e:
        logger.error(f"create_credential failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@credentials_blp.route('/update-name', methods=['PUT', 'PATCH'])
@login_required
def update_credential_name():
    """Update display name (alias) only — API keys in encrypted_config are untouched."""
    try:
        user_id = g.user_id
        data = request.get_json() or {}
        cred_id = data.get('id')
        if cred_id is None:
            cred_id = request.args.get('id', type=int)
        try:
            cred_id = int(cred_id)
        except (TypeError, ValueError):
            cred_id = None
        if not cred_id:
            return jsonify({'code': 0, 'msg': 'Missing id', 'data': None}), 400

        name = (data.get('name') or '').strip()
        if len(name) > 128:
            return jsonify({'code': 0, 'msg': 'Name too long (max 128 characters)', 'data': None}), 400

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_exchange_credentials
                SET name = %s, updated_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id, name, exchange_id, api_key_hint, created_at, updated_at
                """,
                (name, cred_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                cur.close()
                return jsonify({'code': 0, 'msg': 'Not found', 'data': None}), 404
            db.commit()
            cur.close()

        item = dict(row or {})
        return jsonify({'code': 1, 'msg': 'success', 'data': item})
    except Exception as e:
        logger.error(f"update_credential_name failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@credentials_blp.route('/delete', methods=['DELETE'])
@login_required
def delete_credential():
    """Delete a credential for the current user."""
    try:
        user_id = g.user_id
        cred_id = request.args.get('id', type=int)
        if not cred_id:
            return jsonify({'code': 0, 'msg': 'Missing id', 'data': None}), 400

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "DELETE FROM qd_exchange_credentials WHERE id = %s AND user_id = %s",
                (cred_id, user_id)
            )
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success', 'data': None})
    except Exception as e:
        logger.error(f"delete_credential failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@credentials_blp.route('/get', methods=['GET'])
@login_required
def get_credential():
    """
    Return decrypted credential for form auto-fill.
    """
    try:
        user_id = g.user_id
        cred_id = request.args.get('id', type=int)
        if not cred_id:
            return jsonify({'code': 0, 'msg': 'Missing id', 'data': None}), 400

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, user_id, name, exchange_id, encrypted_config, api_key_hint, created_at, updated_at
                FROM qd_exchange_credentials
                WHERE id = %s AND user_id = %s
                """,
                (cred_id, user_id)
            )
            row = cur.fetchone()
            cur.close()

        if not row:
            return jsonify({'code': 0, 'msg': 'Not found', 'data': None}), 404

        raw = row.get('encrypted_config')
        plain = decrypt_credential_blob(raw)
        decrypted = json.loads(plain) if plain else {}
        # Ensure exchange_id is present
        decrypted['exchange_id'] = row.get('exchange_id') or decrypted.get('exchange_id')

        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'id': row.get('id'),
                'name': row.get('name'),
                'exchange_id': row.get('exchange_id'),
                'api_key_hint': row.get('api_key_hint'),
                'config': decrypted
            }
        })
    except Exception as e:
        logger.error(f"get_credential failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500



# openapi-compat: legacy import name
credentials_bp = credentials_blp
