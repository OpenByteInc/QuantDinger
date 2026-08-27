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
from collections import OrderedDict
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
    parse_futu_deal,
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
            try:
                self.prepare()
                url = self.url()

                def _open(ws):
                    if self.ready_on_open():
                        self.mark_ready()
                    else:
                        self._connected = False
                        self.on_state("authenticating", "", self._reconnects > 0)
                    for message in self.on_open_messages():
                        ws.send(json.dumps(message, separators=(",", ":")))

                def _message(ws, raw):
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
                self._ws.run_forever(
                    ping_interval=20,
                    ping_timeout=10,
                    sslopt=sslopt,
                    skip_utf8_validation=True,
                )
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

    def stop(self, timeout: float = 5.0) -> bool:
        main_stopped = super().stop(timeout=timeout)
        keepalive = self._keepalive_thread
        if keepalive and keepalive.is_alive():
            keepalive.join(timeout=timeout)
        return main_stopped and not bool(keepalive and keepalive.is_alive())

    def prepare(self) -> None:
        api_key = str(self.config.get("api_key") or self.config.get("apiKey") or "")
        environment = exchange_trading_environment(self.config, "binance")
        if self.market_type == "spot":
            base = "https://demo-api.binance.com" if environment != "live" else str(
                self.config.get("base_url") or "https://api.binance.com"
            )
            path = "/api/v3/userDataStream"
        else:
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
                    params={"listenKey": self._listen_key} if self.market_type == "spot" else None,
                    headers={"X-MBX-APIKEY": api_key, "Connection": "close"},
                    timeout=15,
                    verify=_get_requests_verify(),
                )
                response.raise_for_status()
            except Exception as exc:
                self.on_state("error", f"Binance listenKey keepalive failed: {exc}", False)

    def url(self) -> str:
        environment = exchange_trading_environment(self.config, "binance")
        if self.market_type == "spot":
            host = "wss://demo-stream.binance.com/ws" if environment != "live" else "wss://stream.binance.com:9443/ws"
        else:
            host = "wss://fstream.binancefuture.com/ws" if environment != "live" else "wss://fstream.binance.com/ws"
        return f"{host}/{self._listen_key}"

    def parse(self, payload: Dict[str, Any]) -> List[ExecutionEvent]:
        return parse_binance(payload, market_type=self.market_type)


class OkxExecutionAdapter(PrivateWebSocketAdapter):
    def ready_on_open(self) -> bool:
        return False

    def url(self) -> str:
        if exchange_trading_environment(self.config, "okx") != "live":
            return "wss://wspap.okx.com:8443/ws/v5/private"
        return "wss://ws.okx.com:8443/ws/v5/private"

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

    def url(self) -> str:
        return "wss://ws.bitget.com/v2/ws/private"

    def on_open_messages(self) -> List[Dict[str, Any]]:
        timestamp = str(int(time.time()))
        secret = str(self.config.get("secret_key") or self.config.get("secret") or "")
        sign = _b64_hmac(secret, f"{timestamp}GET/user/verify")
        inst_type = "SPOT" if self.market_type == "spot" else str(
            self.config.get("product_type") or self.config.get("productType") or "USDT-FUTURES"
        ).upper()
        self._inst_type = inst_type
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
                    "args": [{"instType": self._inst_type, "channel": "fill", "instId": "default"}],
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
        return "wss://api.htx.com/ws/v2" if self.market_type == "spot" else "wss://api.hbdm.com/swap-notification"

    def _auth_params(self) -> Dict[str, str]:
        parsed = urlparse(self.url())
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        params = {
            "AccessKeyId": str(self.config.get("api_key") or self.config.get("apiKey") or ""),
            "SignatureMethod": "HmacSHA256",
            "SignatureVersion": "2",
            "Timestamp": timestamp,
        }
        path = parsed.path or ("/ws/v2" if self.market_type == "spot" else "/swap-notification")
        encoded = urlencode(sorted(params.items()))
        payload = f"GET\n{parsed.hostname}\n{path}\n{encoded}"
        params["Signature"] = _b64_hmac(
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
        event.raw = {
            **base_event.raw,
            "commission": float(getattr(report, "commission", 0) or 0.0),
            "currency": str(getattr(report, "currency", "") or ""),
            "realizedPNL": float(getattr(report, "realizedPNL", 0) or 0.0),
        }
        event.quantity = 0.0
        event.cumulative_quantity = base_event.cumulative_quantity
        event.is_cumulative = True
        # Commission is a second authoritative update for the same execution.
        event.exchange_fill_id = f"{exec_id}:commission"
        event.realized_pnl = float(getattr(report, "realizedPNL", 0) or 0.0)
        event.credential_id = self.credential_id
        event.user_id = self.user_id
        self.on_event(event)


class FutuExecutionAdapter:
    """Poll / push FutuOpenD deal updates through TradeDealHandler callbacks."""

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
        self._fill_lock = threading.Lock()
        self._deal_ids_by_order: Dict[str, set[str]] = {}
        self._deal_quantity_by_order: Dict[str, float] = {}
        self._order_cumulative_by_order: Dict[str, float] = {}
        self._fill_order_lru: OrderedDict[str, None] = OrderedDict()
        self.orphaned = False
        self._connect_backoff_sec = 1.0

    @property
    def stream_key(self) -> str:
        return f"futu:{self.credential_id}:stock"

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.connected)

    @property
    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.orphaned:
            logger.warning(
                "Refusing to start a second Futu execution stream while an orphaned thread is alive key=%s",
                self.stream_key,
            )
            return
        if self.is_alive:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"ExecStream-{self.stream_key}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        self._release_client()
        if self.is_alive:
            self._thread.join(timeout=timeout)
        if self.is_alive:
            self.orphaned = True
            logger.warning("Futu execution stream thread did not stop; marking orphaned key=%s", self.stream_key)
            return False
        self.orphaned = False
        return True

    def _release_client(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            client.disconnect()
        except Exception:
            pass

    def _emit_deal(self, payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        order_id = str(payload.get("order_id") or payload.get("orderId") or "")
        deal_id = str(
            payload.get("deal_id")
            or payload.get("exchange_fill_id")
            or payload.get("exec_id")
            or ""
        )
        order_key = order_id or f"deal:{deal_id}"
        try:
            quantity = abs(float(payload.get("qty") or payload.get("quantity") or 0.0))
        except (TypeError, ValueError):
            quantity = 0.0
        with self._fill_lock:
            self._touch_fill_order_unlocked(order_key)
            deal_ids = self._deal_ids_by_order.setdefault(order_key, set())
            if deal_id and deal_id in deal_ids:
                return
            previous_deals = float(self._deal_quantity_by_order.get(order_key) or 0.0)
            deal_total = previous_deals + quantity
            covered_by_order = float(self._order_cumulative_by_order.get(order_key) or 0.0)
            if deal_total <= covered_by_order + 1e-12:
                if deal_id:
                    deal_ids.add(deal_id)
                self._deal_quantity_by_order[order_key] = deal_total
                return
            for event in parse_futu_deal(payload):
                event.credential_id = self.credential_id
                event.user_id = self.user_id
                self.on_event(event)
            if deal_id:
                deal_ids.add(deal_id)
            self._deal_quantity_by_order[order_key] = deal_total

    def _emit_order(self, payload: Dict[str, Any]) -> None:
        # Treat order push with dealt_qty as a deal-like update for REST audit gaps.
        if not isinstance(payload, dict):
            return
        dealt = float(payload.get("dealt_qty") or payload.get("filled") or 0.0)
        if dealt <= 0:
            return
        order_id = str(payload.get("order_id") or payload.get("orderId") or "")
        if not order_id:
            return
        with self._fill_lock:
            self._touch_fill_order_unlocked(order_id)
            previous_order = float(self._order_cumulative_by_order.get(order_id) or 0.0)
            observed_deals = float(self._deal_quantity_by_order.get(order_id) or 0.0)
            if dealt <= max(previous_order, observed_deals) + 1e-12:
                self._order_cumulative_by_order[order_id] = max(previous_order, dealt)
                return
            for event in parse_futu_deal(payload):
                event.credential_id = self.credential_id
                event.user_id = self.user_id
                self.on_event(event)
            self._order_cumulative_by_order[order_id] = max(previous_order, dealt)

    def _touch_fill_order_unlocked(self, order_id: str) -> None:
        """Bound cross-channel dedupe state for long-running adapters."""
        self._fill_order_lru.pop(order_id, None)
        self._fill_order_lru[order_id] = None
        while len(self._fill_order_lru) > 4096:
            stale_order_id, _ = self._fill_order_lru.popitem(last=False)
            self._deal_ids_by_order.pop(stale_order_id, None)
            self._deal_quantity_by_order.pop(stale_order_id, None)
            self._order_cumulative_by_order.pop(stale_order_id, None)

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    from app.services.live_trading.factory import create_futu_client

                    self._client = create_futu_client(self.config, need_quote=False)
                    if not self._client.connected and not self._client.connect(need_quote=False):
                        raise RuntimeError("FutuOpenD connection failed")
                    self._client.add_deal_handler(self._emit_deal)
                    self._client.add_order_handler(self._emit_order)
                    self._client.start_push()
                    self.on_state("connected", "", False)
                    self._connect_backoff_sec = 1.0
                    while not self._stop.is_set() and self._client and self._client.connected:
                        time.sleep(0.5)
                except Exception as exc:
                    self.on_state("error", str(exc), False)
                    if self._stop.wait(self._connect_backoff_sec):
                        break
                    self._connect_backoff_sec = min(30.0, self._connect_backoff_sec * 2)
                finally:
                    self._release_client()
        finally:
            self.on_state("orphaned" if self.orphaned else "disconnected", "", False)


ADAPTERS = {
    "binance": BinanceExecutionAdapter,
    "okx": OkxExecutionAdapter,
    "bybit": BybitExecutionAdapter,
    "bitget": BitgetExecutionAdapter,
    "gate": GateExecutionAdapter,
    "htx": HtxExecutionAdapter,
    "alpaca": AlpacaExecutionAdapter,
    "ibkr": IBKRExecutionAdapter,
    "futu": FutuExecutionAdapter,
}
