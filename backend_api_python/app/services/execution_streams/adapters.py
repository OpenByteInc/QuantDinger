"""Private execution stream adapters for all supported venues."""

from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import ssl
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urlencode, urlparse

import requests

from app.services.execution_streams.events import ExecutionEvent, FeeComponent
from app.services.execution_streams.normalizers import (
    parse_alpaca,
    parse_binance,
    parse_bitget,
    parse_bybit,
    parse_gate,
    parse_htx,
    parse_ibkr_execution,
    parse_okx,
)
from app.services.live_trading.base import _get_requests_verify
from app.services.live_trading.factory import exchange_trading_environment
from app.utils.logger import get_logger

logger = get_logger(__name__)

EventCallback = Callable[[ExecutionEvent], None]
StateCallback = Callable[[str, str, bool], None]


def _json_message(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, bytes):
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw or "{}"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _b64_hmac(secret: str, value: str, algorithm=hashlib.sha256) -> str:
    digest = hmac.new(str(secret).encode(), str(value).encode(), algorithm).digest()
    return base64.b64encode(digest).decode()


class PrivateWebSocketAdapter:
    """Reconnectable websocket-client wrapper with venue-specific hooks."""

    def __init__(
        self,
        *,
        credential_id: int,
        user_id: int,
        exchange_id: str,
        market_type: str,
        config: Dict[str, Any],
        symbols: Iterable[str],
        on_event: EventCallback,
        on_state: StateCallback,
    ) -> None:
        self.credential_id = int(credential_id or 0)
        self.user_id = int(user_id or 1)
        self.exchange_id = str(exchange_id or "").lower()
        self.market_type = str(market_type or "").lower()
        self.config = dict(config or {})
        self.symbols = sorted({str(value or "").strip() for value in symbols if str(value or "").strip()})
        self.on_event = on_event
        self.on_state = on_state
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ws: Any = None
        self._connected = False
        self._reconnects = 0
        self._last_error = ""
        self._last_message_at = 0.0

    @property
    def stream_key(self) -> str:
        return f"{self.exchange_id}:{self.credential_id}:{self.market_type or 'all'}"

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.is_alive:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"ExecStream-{self.stream_key}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if self.is_alive:
            self._thread.join(timeout=timeout)
        self._connected = False
        return not self.is_alive

    def url(self) -> str:
        raise NotImplementedError

    def on_open_messages(self) -> List[Dict[str, Any]]:
        return []

    def ready_on_open(self) -> bool:
        return True

    def mark_ready(self) -> None:
        self._connected = True
        self._last_error = ""
        self.on_state("connected", "", self._reconnects > 0)

    def parse(self, payload: Dict[str, Any]) -> List[ExecutionEvent]:
        raise NotImplementedError

    def handle_control(self, ws: Any, payload: Dict[str, Any]) -> bool:
        if payload.get("ping") is not None:
            ws.send(json.dumps({"pong": payload.get("ping")}))
            return True
        if payload.get("op") == "ping":
            ws.send(json.dumps({"op": "pong", "ts": payload.get("ts")}))
            return True
        return False

    def application_heartbeat_interval(self) -> float:
        """Return the venue-level heartbeat interval, or 0 when not required."""
        return 0.0

    def application_heartbeat_payload(self) -> Any:
        """Return the venue-level heartbeat payload sent after an idle interval."""
        return None

    def _application_heartbeat_loop(self, ws: Any, connection_stop: threading.Event) -> None:
        interval = max(0.0, float(self.application_heartbeat_interval() or 0.0))
        if interval <= 0:
            return
        while not self._stop.is_set() and not connection_stop.wait(interval):
            # Incoming frames must not defer a 20-second ping to the 40-second tick.
            payload = self.application_heartbeat_payload()
            if payload is None:
                return
            try:
                if isinstance(payload, str):
                    ws.send(payload)
                else:
                    ws.send(json.dumps(payload, separators=(",", ":")))
            except Exception:
                # run_forever owns reconnect/error reporting; a failed heartbeat
                # only means this connection is already on its way down.
                return

    def prepare(self) -> None:
        return None

    def _run(self) -> None:
        try:
            import websocket
        except ImportError:
            self.on_state("error", "websocket-client dependency is missing", False)
            return
        backoff = 1.0
        while not self._stop.is_set():
            heartbeat_stop = threading.Event()
            heartbeat_thread: Optional[threading.Thread] = None
            try:
                self.prepare()
                url = self.url()

                def _open(ws):
                    nonlocal heartbeat_thread
                    self._last_message_at = time.monotonic()
                    if self.ready_on_open():
                        self.mark_ready()
                    else:
                        self._connected = False
                        self.on_state("authenticating", "", self._reconnects > 0)
                    for message in self.on_open_messages():
                        ws.send(json.dumps(message, separators=(",", ":")))
                    if self.application_heartbeat_interval() > 0:
                        heartbeat_thread = threading.Thread(
                            target=self._application_heartbeat_loop,
                            args=(ws, heartbeat_stop),
                            name=f"ExecHeartbeat-{self.stream_key}",
                            daemon=True,
                        )
                        heartbeat_thread.start()

                def _message(ws, raw):
                    self._last_message_at = time.monotonic()
                    payload = _json_message(raw)
                    if not payload or self.handle_control(ws, payload):
                        return
                    for event in self.parse(payload):
                        event.credential_id = self.credential_id
                        event.user_id = self.user_id
                        self.on_event(event)

                def _error(_ws, error):
                    self._last_error = str(error or "")
                    logger.warning(
                        "Private execution stream error stream=%s error=%s",
                        self.stream_key,
                        self._last_error,
                    )
                    self.on_state("error", self._last_error, False)

                def _close(_ws, _code, reason):
                    self._connected = False
                    close_reason = str(reason or self._last_error or "")
                    self.on_state("disconnected", close_reason, False)

                self._ws = websocket.WebSocketApp(
                    url,
                    header={"X-Gate-Size-Decimal": "1"} if isinstance(self, GateExecutionAdapter) else None,
                    on_open=_open,
                    on_message=_message,
                    on_error=_error,
                    on_close=_close,
                )
                sslopt: Dict[str, Any] = {}
                verify = _get_requests_verify()
                if verify is False:
                    sslopt = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}
                elif isinstance(verify, str):
                    sslopt = {"ca_certs": verify}
                try:
                    self._ws.run_forever(
                        ping_interval=20,
                        ping_timeout=10,
                        sslopt=sslopt,
                        skip_utf8_validation=True,
                    )
                finally:
                    heartbeat_stop.set()
                    if heartbeat_thread and heartbeat_thread.is_alive():
                        heartbeat_thread.join(timeout=1.0)
            except Exception as exc:
                self._connected = False
                self.on_state("error", str(exc), False)
            if self._stop.wait(backoff):
                break
            self._reconnects += 1
            backoff = min(30.0, backoff * 2)
        self._connected = False


class BinanceExecutionAdapter(PrivateWebSocketAdapter):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._listen_key = ""
        self._listen_key_base = ""
        self._listen_key_path = ""
        self._keepalive_thread: Optional[threading.Thread] = None
        self._subscription_request_id = ""

    def _uses_spot_ws_api(self) -> bool:
        return self.market_type == "spot"

    def ready_on_open(self) -> bool:
        return not self._uses_spot_ws_api()

    def on_open_messages(self) -> List[Dict[str, Any]]:
        if not self._uses_spot_ws_api():
            return []
        timestamp = int(time.time() * 1000)
        api_key = str(self.config.get("api_key") or self.config.get("apiKey") or "")
        secret = str(self.config.get("secret_key") or self.config.get("secret") or "")
        params: Dict[str, Any] = {
            "apiKey": api_key,
            "timestamp": timestamp,
        }
        signature_payload = urlencode(sorted(params.items()))
        params["signature"] = hmac.new(
            secret.encode(), signature_payload.encode(), hashlib.sha256
        ).hexdigest()
        self._subscription_request_id = f"qd-user-stream-{self.credential_id}-{timestamp}"
        return [{
            "id": self._subscription_request_id,
            "method": "userDataStream.subscribe.signature",
            "params": params,
        }]

    def stop(self, timeout: float = 5.0) -> bool:
        main_stopped = super().stop(timeout=timeout)
        keepalive = self._keepalive_thread
        if keepalive and keepalive.is_alive():
            keepalive.join(timeout=timeout)
        return main_stopped and not bool(keepalive and keepalive.is_alive())

    def prepare(self) -> None:
        if self._uses_spot_ws_api():
            self._listen_key = ""
            return
        api_key = str(self.config.get("api_key") or self.config.get("apiKey") or "")
        environment = exchange_trading_environment(self.config, "binance")
        base = "https://demo-fapi.binance.com" if environment != "live" else str(
            self.config.get("base_url") or "https://fapi.binance.com"
        )
        path = "/fapi/v1/listenKey"
        self._listen_key_base = base.rstrip("/")
        self._listen_key_path = path
        response = requests.post(
            f"{base.rstrip('/')}{path}",
            headers={"X-MBX-APIKEY": api_key, "Connection": "close"},
            timeout=15,
            verify=_get_requests_verify(),
        )
        response.raise_for_status()
        self._listen_key = str((response.json() or {}).get("listenKey") or "")
        if not self._listen_key:
            raise RuntimeError("Binance user data stream did not return listenKey")
        if not self._keepalive_thread or not self._keepalive_thread.is_alive():
            self._keepalive_thread = threading.Thread(
                target=self._keepalive_loop,
                name=f"BinanceListenKey-{self.credential_id}-{self.market_type}",
                daemon=True,
            )
            self._keepalive_thread.start()

    def _keepalive_loop(self) -> None:
        while not self._stop.wait(25 * 60):
            try:
                api_key = str(self.config.get("api_key") or self.config.get("apiKey") or "")
                response = requests.put(
                    f"{self._listen_key_base}{self._listen_key_path}",
                    params=None,
                    headers={"X-MBX-APIKEY": api_key, "Connection": "close"},
                    timeout=15,
                    verify=_get_requests_verify(),
                )
                response.raise_for_status()
            except Exception as exc:
                self.on_state("error", f"Binance listenKey keepalive failed: {exc}", False)

    def url(self) -> str:
        environment = exchange_trading_environment(self.config, "binance")
        if self._uses_spot_ws_api():
            if environment == "testnet":
                return "wss://ws-api.testnet.binance.vision/ws-api/v3"
            if environment == "demo":
                return "wss://demo-ws-api.binance.com/ws-api/v3"
            return "wss://ws-api.binance.com:443/ws-api/v3"
        host = "wss://fstream.binancefuture.com/ws" if environment != "live" else "wss://fstream.binance.com/ws"
        return f"{host}/{self._listen_key}"

    def handle_control(self, ws: Any, payload: Dict[str, Any]) -> bool:
        if self._uses_spot_ws_api() and payload.get("id") == self._subscription_request_id:
            try:
                status = int(payload.get("status") or 0)
            except (TypeError, ValueError):
                status = 0
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            if status == 200 and result.get("subscriptionId") is not None:
                self.mark_ready()
            else:
                error = payload.get("error")
                message = error.get("msg") if isinstance(error, dict) else error
                self.on_state(
                    "error",
                    f"Binance user stream subscription failed: {message or payload}",
                    False,
                )
                try:
                    ws.close()
                except Exception:
                    pass
            return True
        return super().handle_control(ws, payload)

    def parse(self, payload: Dict[str, Any]) -> List[ExecutionEvent]:
        event = payload.get("event")
        return parse_binance(
            event if isinstance(event, dict) else payload,
            market_type=self.market_type,
        )


class OkxExecutionAdapter(PrivateWebSocketAdapter):
    def ready_on_open(self) -> bool:
        return False

    def application_heartbeat_interval(self) -> float:
        # OKX requires the literal text frame "ping" before 30 seconds idle.
        return 20.0

    def application_heartbeat_payload(self) -> str:
        return "ping"

    def url(self) -> str:
        if exchange_trading_environment(self.config, "okx") != "live":
            return "wss://wspap.okx.com/ws/v5/private"
        return "wss://ws.okx.com/ws/v5/private"

    def on_open_messages(self) -> List[Dict[str, Any]]:
        timestamp = str(int(time.time()))
        sign = _b64_hmac(
            str(self.config.get("secret_key") or self.config.get("secret") or ""),
            f"{timestamp}GET/users/self/verify",
        )
        return [{
            "op": "login",
            "args": [{
                "apiKey": str(self.config.get("api_key") or self.config.get("apiKey") or ""),
                "passphrase": str(self.config.get("passphrase") or self.config.get("password") or ""),
                "timestamp": timestamp,
                "sign": sign,
            }],
        }]

    def handle_control(self, ws: Any, payload: Dict[str, Any]) -> bool:
        if payload.get("event") == "login":
            if str(payload.get("code") or "0") == "0":
                self.mark_ready()
                ws.send(json.dumps({"op": "subscribe", "args": [{"channel": "orders", "instType": "ANY"}]}))
            else:
                self.on_state("error", f"OKX login failed: {payload}", False)
            return True
        return super().handle_control(ws, payload)

    def parse(self, payload: Dict[str, Any]) -> List[ExecutionEvent]:
        return parse_okx(payload)


class BybitExecutionAdapter(PrivateWebSocketAdapter):
    def ready_on_open(self) -> bool:
        return False

    def application_heartbeat_interval(self) -> float:
        return 20.0

    def application_heartbeat_payload(self) -> Dict[str, str]:
        return {"op": "ping"}

    def url(self) -> str:
        environment = exchange_trading_environment(self.config, "bybit")
        if environment == "demo":
            return "wss://stream-demo.bybit.com/v5/private"
        if environment == "testnet":
            return "wss://stream-testnet.bybit.com/v5/private"
        return "wss://stream.bybit.com/v5/private"

    def on_open_messages(self) -> List[Dict[str, Any]]:
        expires = int((time.time() + 10) * 1000)
        secret = str(self.config.get("secret_key") or self.config.get("secret") or "")
        signature = hmac.new(secret.encode(), f"GET/realtime{expires}".encode(), hashlib.sha256).hexdigest()
        return [{
            "op": "auth",
            "args": [
                str(self.config.get("api_key") or self.config.get("apiKey") or ""),
                expires,
                signature,
            ],
        }]

    def handle_control(self, ws: Any, payload: Dict[str, Any]) -> bool:
        if payload.get("op") == "ping" and str(payload.get("ret_msg") or "").lower() == "pong":
            return True
        if payload.get("op") == "auth":
            if bool(payload.get("success")):
                self.mark_ready()
                ws.send(json.dumps({"op": "subscribe", "args": ["execution"]}))
            else:
                self.on_state("error", f"Bybit authentication failed: {payload}", False)
            return True
        return super().handle_control(ws, payload)

    def parse(self, payload: Dict[str, Any]) -> List[ExecutionEvent]:
        return parse_bybit(payload)


class BitgetExecutionAdapter(PrivateWebSocketAdapter):
    def ready_on_open(self) -> bool:
        return False

    def application_heartbeat_interval(self) -> float:
        # Bitget recommends a literal text "ping" every 30 seconds.  Use a
        # small safety margin so scheduling jitter cannot cross the deadline.
        return 25.0

    def application_heartbeat_payload(self) -> str:
        return "ping"

    def url(self) -> str:
        if exchange_trading_environment(self.config, "bitget") != "live":
            return "wss://wspap.bitget.com/v3/ws/private"
        return "wss://ws.bitget.com/v3/ws/private"

    def on_open_messages(self) -> List[Dict[str, Any]]:
        timestamp = str(int(time.time() * 1000))
        secret = str(self.config.get("secret_key") or self.config.get("secret") or "")
        sign = _b64_hmac(secret, f"{timestamp}GET/user/verify")
        return [{
            "op": "login",
            "args": [{
                "apiKey": str(self.config.get("api_key") or self.config.get("apiKey") or ""),
                "passphrase": str(self.config.get("passphrase") or self.config.get("password") or ""),
                "timestamp": timestamp,
                "sign": sign,
            }],
        }]

    def handle_control(self, ws: Any, payload: Dict[str, Any]) -> bool:
        if payload.get("event") == "login":
            if str(payload.get("code") or "0") == "0":
                self.mark_ready()
                ws.send(json.dumps({
                    "op": "subscribe",
                    "args": [{"instType": "UTA", "topic": "fill"}],
                }))
            else:
                self.on_state("error", f"Bitget login failed: {payload}", False)
            return True
        return super().handle_control(ws, payload)

    def parse(self, payload: Dict[str, Any]) -> List[ExecutionEvent]:
        return parse_bitget(payload)


class GateExecutionAdapter(PrivateWebSocketAdapter):
    def ready_on_open(self) -> bool:
        return False

    def url(self) -> str:
        testnet = exchange_trading_environment(self.config, "gate") != "live"
        if self.market_type == "spot":
            return "wss://ws-testnet.gate.com/v4/ws/spot" if testnet else "wss://api.gateio.ws/ws/v4/"
        return (
            "wss://ws-testnet.gate.com/v4/ws/futures/usdt"
            if testnet
            else "wss://fx-ws.gateio.ws/v4/ws/usdt"
        )

    def _subscription(self, channel: str, *, user_id: str = "") -> Dict[str, Any]:
        event = "subscribe"
        timestamp = int(time.time())
        secret = str(self.config.get("secret_key") or self.config.get("secret") or "")
        signature = hmac.new(
            secret.encode(),
            f"channel={channel}&event={event}&time={timestamp}".encode(),
            hashlib.sha512,
        ).hexdigest()
        return {
            "time": timestamp,
            "channel": channel,
            "event": event,
            "payload": [str(user_id), "!all"] if channel == "futures.usertrades" else ["!all"],
            "auth": {
                "method": "api_key",
                "KEY": str(self.config.get("api_key") or self.config.get("apiKey") or ""),
                "SIGN": signature,
            },
        }

    def _futures_login(self) -> Dict[str, Any]:
        """Authenticate the Gate futures WebSocket API and obtain its exchange UID."""
        timestamp = int(time.time())
        channel = "futures.login"
        request_id = f"qd-login-{self.credential_id}-{timestamp}"
        secret = str(self.config.get("secret_key") or self.config.get("secret") or "")
        signature = hmac.new(
            secret.encode(),
            f"api\n{channel}\n\n{timestamp}".encode(),
            hashlib.sha512,
        ).hexdigest()
        return {
            "time": timestamp,
            "channel": channel,
            "event": "api",
            "payload": {
                "api_key": str(self.config.get("api_key") or self.config.get("apiKey") or ""),
                "signature": signature,
                "timestamp": str(timestamp),
                "req_id": request_id,
            },
        }

    def on_open_messages(self) -> List[Dict[str, Any]]:
        if self.market_type == "spot":
            return [self._subscription("spot.usertrades")]
        return [self._futures_login()]

    def parse(self, payload: Dict[str, Any]) -> List[ExecutionEvent]:
        return parse_gate(payload, market_type=self.market_type)

    def handle_control(self, ws: Any, payload: Dict[str, Any]) -> bool:
        header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
        if header.get("channel") == "futures.login":
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            result = data.get("result") if isinstance(data.get("result"), dict) else {}
            errors = data.get("errs")
            uid = str(result.get("uid") or "").strip()
            if str(header.get("status") or "") == "200" and uid and not errors:
                ws.send(json.dumps(self._subscription("futures.usertrades", user_id=uid)))
            else:
                self.on_state("error", f"Gate futures authentication failed: {payload}", False)
            return True
        if payload.get("event") == "subscribe":
            if not payload.get("error"):
                self.mark_ready()
            else:
                self.on_state("error", f"Gate subscription failed: {payload}", False)
            return True
        return super().handle_control(ws, payload)


class HtxExecutionAdapter(PrivateWebSocketAdapter):
    def ready_on_open(self) -> bool:
        return False

    def url(self) -> str:
        return "wss://api.htx.com/ws/v2" if self.market_type == "spot" else "wss://api.hbdm.com/linear-swap-notification"

    def _auth_params(self) -> Dict[str, str]:
        parsed = urlparse(self.url())
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        params = {
            "AccessKeyId": str(self.config.get("api_key") or self.config.get("apiKey") or ""),
            "SignatureMethod": "HmacSHA256",
            "SignatureVersion": "2",
            "Timestamp": timestamp,
        }
        if self.market_type == 'spot':
            params = {'accessKey': params['AccessKeyId'], 'signatureMethod': 'HmacSHA256',
                      'signatureVersion': '2.1', 'timestamp': timestamp}
        path = parsed.path
        encoded = urlencode(sorted(params.items()))
        payload = f"GET\n{parsed.hostname}\n{path}\n{encoded}"
        params['signature' if self.market_type == 'spot' else 'Signature'] = _b64_hmac(
            str(self.config.get("secret_key") or self.config.get("secret") or ""),
            payload,
        )
        return params

    def on_open_messages(self) -> List[Dict[str, Any]]:
        auth = self._auth_params()
        if self.market_type == "spot":
            return [{"action": "req", "ch": "auth", "params": {"authType": "api", **auth}}]
        return [{"op": "auth", "type": "api", **auth}]

    def _subscription_messages(self) -> List[Dict[str, Any]]:
        if self.market_type == "spot":
            return [
                {
                    "action": "sub",
                    "ch": f"trade.clearing#{symbol.replace('/', '').replace('-', '').lower()}",
                }
                for symbol in self.symbols
            ]
        return [
            {"op": "sub", "topic": "matchOrders.*", "cid": f"qd-{self.credential_id}-isolated"},
            {"op": "sub", "topic": "matchOrders_cross.*", "cid": f"qd-{self.credential_id}-cross"},
        ]

    def handle_control(self, ws: Any, payload: Dict[str, Any]) -> bool:
        if payload.get("ch") == "auth" and payload.get("action") == "req":
            if int(payload.get("code") or 0) == 200:
                self.mark_ready()
                for message in self._subscription_messages():
                    ws.send(json.dumps(message))
            else:
                self.on_state("error", f"HTX spot authentication failed: {payload}", False)
            return True
        if payload.get("op") == "auth":
            if int(payload.get("err-code") or 0) == 0:
                self.mark_ready()
                for message in self._subscription_messages():
                    ws.send(json.dumps(message))
            else:
                self.on_state("error", f"HTX futures authentication failed: {payload}", False)
            return True
        if payload.get("op") == "ping":
            ws.send(json.dumps({"op": "pong", "ts": payload.get("ts")}))
            return True
        if payload.get("action") == "ping":
            ws.send(json.dumps({"action": "pong", "data": {"ts": payload.get("data", {}).get("ts")}}))
            return True
        return super().handle_control(ws, payload)

    def parse(self, payload: Dict[str, Any]) -> List[ExecutionEvent]:
        return parse_htx(payload, market_type=self.market_type)


class AlpacaExecutionAdapter(PrivateWebSocketAdapter):
    def ready_on_open(self) -> bool:
        return False

    def url(self) -> str:
        paper = bool(self.config.get("paper", self.config.get("paper_trading", True)))
        return "wss://paper-api.alpaca.markets/stream" if paper else "wss://api.alpaca.markets/stream"

    def on_open_messages(self) -> List[Dict[str, Any]]:
        return [{
            "action": "auth",
            "key": str(self.config.get("api_key") or self.config.get("apiKey") or ""),
            "secret": str(self.config.get("secret_key") or self.config.get("secret") or ""),
        }]

    def handle_control(self, ws: Any, payload: Dict[str, Any]) -> bool:
        if payload.get("stream") == "authorization":
            status = str((payload.get("data") or {}).get("status") or "")
            if status == "authorized":
                self.mark_ready()
                ws.send(json.dumps({"action": "listen", "data": {"streams": ["trade_updates"]}}))
            else:
                self.on_state("error", f"Alpaca authentication failed: {payload}", False)
            return True
        return super().handle_control(ws, payload)

    def parse(self, payload: Dict[str, Any]) -> List[ExecutionEvent]:
        return parse_alpaca(payload)


class IBKRExecutionAdapter:
    """Attach execution and commission callbacks to one ib_insync session."""

    def __init__(
        self,
        *,
        credential_id: int,
        user_id: int,
        config: Dict[str, Any],
        on_event: EventCallback,
        on_state: StateCallback,
        **_: Any,
    ) -> None:
        self.credential_id = int(credential_id or 0)
        self.user_id = int(user_id or 1)
        self.config = dict(config or {})
        self.on_event = on_event
        self.on_state = on_state
        self._client: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._events: Dict[str, ExecutionEvent] = {}

    @property
    def stream_key(self) -> str:
        return f"ibkr:{self.credential_id}:usstock"

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.connected)

    @property
    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.is_alive:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"ExecStream-{self.stream_key}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
        if self.is_alive:
            self._thread.join(timeout=timeout)
        return not self.is_alive

    def _run(self) -> None:
        try:
            from app.services.live_trading.factory import create_ibkr_client

            self._client = create_ibkr_client(self.config)
            if not self._client.connected and not self._client.connect():
                raise RuntimeError("IBKR connection failed")
            ib = self._client._ib
            ib.execDetailsEvent += self._on_exec_details
            ib.commissionReportEvent += self._on_commission
            self.on_state("connected", "", False)
            while not self._stop.is_set() and self._client.connected:
                ib.sleep(0.25)
        except Exception as exc:
            self.on_state("error", str(exc), False)
        finally:
            self.on_state("disconnected", "", False)

    def _on_exec_details(self, trade: Any, fill: Any) -> None:
        execution = getattr(fill, "execution", None)
        contract = getattr(fill, "contract", None) or getattr(trade, "contract", None)
        if execution is None:
            return
        event = parse_ibkr_execution(execution, contract)
        event.credential_id = self.credential_id
        event.user_id = self.user_id
        exec_id = event.exchange_fill_id
        self._events[exec_id] = event
        self.on_event(event)

    def _on_commission(self, trade: Any, fill: Any, report: Any) -> None:
        exec_id = str(getattr(report, "execId", "") or "")
        base_event = self._events.get(exec_id)
        if base_event is None:
            execution = getattr(fill, "execution", None)
            if execution is None:
                return
            base_event = parse_ibkr_execution(execution, getattr(fill, "contract", None))
        event = ExecutionEvent(**{**base_event.__dict__})
        event.fee_status = "actual"
        event.fees = [
            FeeComponent(
                currency=str(getattr(report, "currency", "") or "USD").upper(),
                amount=float(getattr(report, "commission", 0) or 0.0),
                source="commission_report",
            )
        ]
        from app.services.execution_streams.reported_pnl import number
        reported_pnl = number(getattr(report, "realizedPNL", None))
        reported_pnl = float(reported_pnl) if reported_pnl is not None else None
        event.raw = {
            **base_event.raw,
            "commission": float(getattr(report, "commission", 0) or 0.0),
            "currency": str(getattr(report, "currency", "") or ""),
            "realizedPNL": reported_pnl,
        }
        event.quantity = 0.0
        event.cumulative_quantity = base_event.cumulative_quantity
        event.is_cumulative = True
        # Commission is a second authoritative update for the same execution.
        event.exchange_fill_id = f"{exec_id}:commission"
        event.realized_pnl = reported_pnl
        event.credential_id = self.credential_id
        event.user_id = self.user_id
        self.on_event(event)


ADAPTERS = {
    "binance": BinanceExecutionAdapter,
    "okx": OkxExecutionAdapter,
    "bybit": BybitExecutionAdapter,
    "bitget": BitgetExecutionAdapter,
    "gate": GateExecutionAdapter,
    "htx": HtxExecutionAdapter,
    "alpaca": AlpacaExecutionAdapter,
    "ibkr": IBKRExecutionAdapter,
}
