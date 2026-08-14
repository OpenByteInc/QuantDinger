"""
Exchange credentials vault.

encrypted_config stores Fernet ciphertext managed by app.utils.credential_crypto.
"""

import traceback
import json
from flask import g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.openapi.schemas.high_risk import (
    CredentialCreateRequestSchema,
    CredentialCreatedResponseSchema,
    StrategyIdQuerySchema,
)

import requests as rq

from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.auth import login_required
from app.utils.credential_crypto import encrypt_credential_blob, decrypt_credential_blob
from app.services.live_trading.factory import (
    create_client,
    exchange_demo_mode_enabled,
    exchange_market_scope,
    exchange_trading_environment,
    validate_exchange_environment,
)
from app.services.live_trading.capabilities import supported_crypto_exchange_ids

logger = get_logger(__name__)

credentials_blp = Blueprint('credentials', __name__)


@credentials_blp.route('/desktop-brokers-policy', methods=['GET'])
@login_required
def desktop_brokers_policy():
    """
    Whether IBKR / Futu (local TWS, IB Gateway, or FutuOpenD) may be configured.
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
                'local_desktop_brokers': ['ibkr', 'futu'],
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
            if str(item.get('exchange_id') or '').strip().lower() not in {*CRYPTO_EXCHANGES, 'ibkr', 'alpaca', 'futu'}:
                continue
            item['enable_demo_trading'] = False
            item['environment'] = 'live'
            item['market_scope'] = 'both'
            try:
                plain = decrypt_credential_blob(item.get('encrypted_config'))
                cfg = json.loads(plain) if plain else {}
                item['enable_demo_trading'] = exchange_demo_mode_enabled(cfg if isinstance(cfg, dict) else {})
                item['environment'] = exchange_trading_environment(cfg if isinstance(cfg, dict) else {}, item.get('exchange_id'))
                item['market_scope'] = exchange_market_scope(cfg if isinstance(cfg, dict) else {})
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


def _crypto_credential_config(data: dict, exchange_id: str) -> dict:
    api_key = (data.get('api_key') or data.get('apiKey') or '').strip()
    secret_key = (data.get('secret_key') or data.get('secretKey') or '').strip()
    if not api_key or not secret_key:
        raise ValueError('MISSING_EXCHANGE_API_CREDENTIALS')
    environment = exchange_trading_environment(data, exchange_id)
    market_scope = exchange_market_scope(data)
    validate_exchange_environment(exchange_id, environment, market_scope)
    return {
        'exchange_id': exchange_id,
        'api_key': api_key,
        'secret_key': secret_key,
        'passphrase': (data.get('passphrase') or '').strip(),
        'environment': environment,
        'market_scope': market_scope,
        'enable_demo_trading': environment != 'live',
    }


def _probe_crypto_credential(config: dict) -> list[str]:
    scope = exchange_market_scope(config)
    markets = ['spot', 'swap'] if scope == 'both' else [scope]
    tested = []
    for market_type in markets:
        client = create_client(config, market_type=market_type)
        if hasattr(client, 'get_account'):
            client.get_account()
        elif market_type == 'spot' and hasattr(client, 'get_assets'):
            client.get_assets()
        elif hasattr(client, 'get_wallet_balance'):
            client.get_wallet_balance()
        elif hasattr(client, 'get_balance'):
            client.get_balance()
        elif hasattr(client, 'get_accounts'):
            client.get_accounts()
        else:
            raise ValueError('EXCHANGE_PRIVATE_ACCOUNT_PROBE_UNAVAILABLE')
        tested.append(market_type)
    return tested


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
                "ip": ipv4 or ipv6 or None,
            },
        }
    )


@credentials_blp.route('/test', methods=['POST'])
@login_required
@credentials_blp.arguments(CredentialCreateRequestSchema, location="json")
def test_credential(data):
    """Validate credentials against the selected private trading environment."""
    try:
        exchange_id = str(data.get('exchange_id') or '').strip().lower()
        if exchange_id in CRYPTO_EXCHANGES:
            config = _crypto_credential_config(data, exchange_id)
            tested = _probe_crypto_credential(config)
            return jsonify({
                'code': 1,
                'msg': 'CREDENTIAL_CONNECTION_OK',
                'data': {
                    'environment': config['environment'],
                    'market_scope': config['market_scope'],
                    'tested_markets': tested,
                },
            })
        if exchange_id == 'alpaca':
            config = {
                'exchange_id': exchange_id,
                'api_key': str(data.get('api_key') or data.get('apiKey') or '').strip(),
                'secret_key': str(data.get('secret_key') or data.get('secretKey') or '').strip(),
                'base_url': str(data.get('base_url') or data.get('baseUrl') or '').strip(),
            }
            client = create_client(config, market_type='spot')
            if hasattr(client, 'connect') and not client.connect():
                raise ValueError('CREDENTIAL_CONNECTION_FAILED')
            return jsonify({'code': 1, 'msg': 'CREDENTIAL_CONNECTION_OK', 'data': {'environment': 'paper' if str(config['api_key']).upper().startswith('PK') else 'live'}})
        if exchange_id == 'ibkr':
            config = {
                'exchange_id': exchange_id,
                'ibkr_host': str(data.get('ibkr_host') or '127.0.0.1').strip(),
                'ibkr_port': int(data.get('ibkr_port') or 7497),
                'ibkr_client_id': int(data.get('ibkr_client_id') or 7),
                'ibkr_account': str(data.get('ibkr_account') or '').strip(),
            }
            client = create_client(config, market_type='spot')
            if hasattr(client, 'connect') and not client.connect():
                raise ValueError('CREDENTIAL_CONNECTION_FAILED')
            return jsonify({'code': 1, 'msg': 'CREDENTIAL_CONNECTION_OK', 'data': None})
        if exchange_id == 'futu':
            from app.utils.local_brokers import desktop_broker_cloud_reject_message, local_desktop_brokers_allowed
            from app.services.futu_trading.config import normalize_trade_env, normalize_trade_market

            if not local_desktop_brokers_allowed():
                raise ValueError(desktop_broker_cloud_reject_message('futu'))
            trade_env = normalize_trade_env(
                data.get('trade_env') or data.get('environment') or 'demo',
                default='demo',
            )
            config = {
                'exchange_id': exchange_id,
                'futu_host': str(data.get('futu_host') or data.get('host') or '127.0.0.1').strip(),
                'futu_port': int(data.get('futu_port') or data.get('port') or 11111),
                'trade_env': trade_env,
                'environment': trade_env,
                'trade_market': normalize_trade_market(
                    data.get('trade_market') or data.get('tradeMarket'),
                    market_category=str(data.get('market_category') or ''),
                ),
                'security_firm': str(data.get('security_firm') or data.get('securityFirm') or 'FUTUSECURITIES').strip(),
                'acc_id': int(data.get('acc_id') or data.get('accId') or 0),
                'unlock_password': str(data.get('unlock_password') or data.get('unlockPassword') or ''),
            }
            client = create_client(config, market_type='spot', pooled=False)
            try:
                probe = client.probe_permissions() if hasattr(client, 'probe_permissions') else {}
                return jsonify({
                    'code': 1,
                    'msg': 'CREDENTIAL_CONNECTION_OK',
                    'data': {
                        'environment': trade_env,
                        'market_scope': 'spot',
                        'probe': probe,
                        'status': client.get_connection_status() if hasattr(client, 'get_connection_status') else {},
                    },
                })
            finally:
                disconnect = getattr(client, 'disconnect', None)
                if callable(disconnect):
                    try:
                        disconnect()
                    except Exception:
                        pass
        return jsonify({'code': 0, 'msg': 'UNSUPPORTED_EXCHANGE', 'data': None}), 400
    except Exception as exc:
        return jsonify({'code': 0, 'msg': str(exc) or 'CREDENTIAL_CONNECTION_FAILED', 'data': None}), 400


@credentials_blp.route('/create', methods=['POST'])
@credentials_blp.response(200, CredentialCreatedResponseSchema)
@login_required
@credentials_blp.arguments(CredentialCreateRequestSchema, location="json")
def create_credential(data):
    """Create a new credential for the current user.

    Supports crypto exchanges, IBKR (US stocks), Alpaca, and Futu (HK/US).
    """
    try:
        user_id = g.user_id
        name = (data.get('name') or '').strip()
        exchange_id = (data.get('exchange_id') or '').strip().lower()

        if not exchange_id:
            return jsonify({'code': 0, 'msg': 'Missing exchange_id', 'data': None}), 400

        if exchange_id in ('ibkr', 'futu'):
            from app.utils.local_brokers import desktop_broker_cloud_reject_message, local_desktop_brokers_allowed

            if not local_desktop_brokers_allowed():
                return jsonify({
                    'code': 0,
                    'msg': desktop_broker_cloud_reject_message(exchange_id),
                    'data': None,
                }), 403

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
        elif exchange_id == 'futu':
            from app.services.futu_trading.config import normalize_trade_env, normalize_trade_market

            trade_env = normalize_trade_env(
                data.get('trade_env') or data.get('environment') or 'demo',
                default='demo',
            )
            trade_market = normalize_trade_market(
                data.get('trade_market') or data.get('tradeMarket'),
                market_category=str(data.get('market_category') or data.get('marketCategory') or ''),
            )
            config.update({
                'futu_host': (data.get('futu_host') or data.get('host') or '127.0.0.1').strip(),
                'futu_port': int(data.get('futu_port') or data.get('port') or 11111),
                'trade_env': trade_env,
                'environment': trade_env,
                'trade_market': trade_market,
                'security_firm': (data.get('security_firm') or data.get('securityFirm') or 'FUTUSECURITIES').strip(),
                'acc_id': int(data.get('acc_id') or data.get('accId') or 0),
                # Prefer GUI unlock; store only when explicitly provided for headless OpenD.
                'unlock_password': str(data.get('unlock_password') or data.get('unlockPassword') or ''),
                'market_category': 'USStock' if trade_market == 'US' else 'HKStock',
            })
            hint = f"{config['futu_host']}:{config['futu_port']} ({trade_env}/{trade_market})"
        elif exchange_id in CRYPTO_EXCHANGES:
            # Crypto exchanges
            try:
                config = _crypto_credential_config(data, exchange_id)
            except Exception as exc:
                return jsonify({'code': 0, 'msg': str(exc), 'data': None}), 400
            hint = _api_key_hint(config['api_key'])
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
@credentials_blp.arguments(StrategyIdQuerySchema, location="query")
def delete_credential(query):
    """Delete a credential for the current user."""
    try:
        user_id = g.user_id
        cred_id = query["id"]
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


# openapi-compat: legacy import name
credentials_bp = credentials_blp
