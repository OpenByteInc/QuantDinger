# Alpaca Trading Module

Trade US stocks, ETFs, and crypto via [Alpaca Markets](https://alpaca.markets) REST API.
Mirrors `ibkr_trading/` module structure for consistency.

## Why Alpaca?

- **Zero commission** on stocks/ETFs/crypto (vs IBKR's per-share or tiered fees)
- **No TWS/Gateway required** — pure stateless REST authentication
- **Paper trading built-in** — separate paper-api.alpaca.markets endpoint
- **Modern Python SDK** — `alpaca-py` is well-maintained, async-friendly
- **Crypto support** — same client handles stocks AND crypto (BTC/USD, ETH/USD, etc.)
- **Fractional shares** — buy $5 worth of TSLA without owning a full share

## Installation

```bash
pip install alpaca-py
```

## Configuration

Add to `backend_api_python/.env`:

```bash
# Paper trading (recommended for development)
ALPACA_API_KEY=PK********************
ALPACA_SECRET_KEY=********************
ALPACA_PAPER=true

# Live trading (only after thorough paper testing)
# ALPACA_API_KEY=AK********************  # Note: AK prefix = live
# ALPACA_SECRET_KEY=********************
# ALPACA_PAPER=false
```

**Key prefix tells you what mode you're in:**
- `PK*` = paper account (paper-api.alpaca.markets, no real money)
- `AK*` = live account (api.alpaca.markets, real money)

Get your keys at:
- Paper: https://app.alpaca.markets/paper/dashboard/overview → "Generate New Key"
- Live: https://app.alpaca.markets/brokerage/dashboard/overview → "Generate New Key"

## Basic usage

```python
from app.services.alpaca_trading import AlpacaClient, AlpacaConfig

config = AlpacaConfig(
    api_key="PK********************",
    secret_key="********************",
    paper=True,  # paper trading
)
client = AlpacaClient(config)
client.connect()  # Verifies credentials via /account endpoint

# Account snapshot
acct = client.get_account_summary()
print(f"Buying power: ${acct['summary']['BuyingPower']['value']}")

# Place a market BUY of 1 share of SPY
result = client.place_market_order(symbol="SPY", side="buy", quantity=1)
print(f"Order {result.order_id}: filled={result.filled} @ ${result.avg_price}")

# Get current positions
for pos in client.get_positions():
    print(f"{pos['symbol']}: {pos['quantity']} @ ${pos['avgCost']}, unrealized=${pos['unrealizedPnL']:+.2f}")

# Cancel any open order
client.cancel_order(order_id="...")

client.disconnect()
```

## Crypto trading

Alpaca supports crypto using the same client — just pass `market_type="crypto"`:

```python
result = client.place_market_order(
    symbol="BTC/USD",  # or "BTCUSD" — auto-normalized
    side="buy",
    quantity=0.01,
    market_type="crypto",
)
```

Note: crypto orders use `time_in_force=GTC` (good-till-cancelled) since the market is 24/7;
stock orders default to `time_in_force=DAY`.

## Extended-hours trading

For limit orders on US equities, you can trade in pre-market (4:00-9:30 ET) or
after-hours (16:00-20:00 ET):

```python
result = client.place_limit_order(
    symbol="SPY",
    side="buy",
    quantity=1,
    price=740.00,
    extended_hours=True,  # Enables pre/post-market trading
)
```

## API endpoints (when registered as Flask blueprint)

All endpoints require `@login_required`:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/alpaca/status` | Connection state |
| POST | `/api/alpaca/connect` | Connect with API key/secret |
| POST | `/api/alpaca/disconnect` | Clear local state |
| GET | `/api/alpaca/account` | Account summary (buying power, equity, etc.) |
| GET | `/api/alpaca/positions` | Current positions |
| GET | `/api/alpaca/orders` | Open orders |
| POST | `/api/alpaca/order` | Place market or limit order |
| DELETE | `/api/alpaca/order/<id>` | Cancel order |
| GET | `/api/alpaca/quote/<symbol>?marketType=USStock` | Latest bid/ask |

## Differences from IBKR module

| Feature | IBKR | Alpaca |
|---------|------|--------|
| Auth | TWS/Gateway socket (host/port/clientId) | REST API key+secret |
| Order IDs | Integer | UUID string |
| Connection | Stateful, persistent socket | Stateless REST |
| Paper mode (IBKR TWS/Gateway) | Port 7497/4002 | Different base URL |
| Symbol format | `AAPL` (stocks), `0700.HK` (HK) | `AAPL` (US), `BRK.B` (US class), `BTC/USD` (crypto) |
| Extended hours | Configurable per-order | Configurable per-order (limit only) |
| Crypto | No (separate IBKR Crypto product) | Yes, same client |

## Troubleshooting: WebSocket `400 invalid syntax`

QuantDinger **connection test** and live orders use the **REST Trading API** (`TradingClient.get_account()`), **not** the market-data WebSocket at `wss://stream.data.alpaca.markets/...`.

If you see Alpaca error **code 400 / invalid syntax** from the [streaming docs](https://docs.alpaca.markets/us/docs/streaming-market-data#authentication), that is almost always from a **WebSocket client** (custom script, another app, or a chart feed), not from `/api/alpaca/connect` or `/strategies/test-connection`.

Common causes:

| Issue | Fix |
|-------|-----|
| Subscribe before auth | After `connected`, send `{"action":"auth","key":"...","secret":"..."}` within 10s |
| Wrong JSON shape | Use `action` + channel keys, e.g. `{"action":"subscribe","trades":["AAPL"]}` |
| Invalid symbol | US stocks: `AAPL` (no `BTC/USDT`). Crypto: `BTC/USD` (Alpaca does **not** use `BTC/USDT`) |
| Class shares | `BRK/B` → `BRK.B` for REST; do not treat as a crypto pair |

This module normalizes symbols (`symbols.py`): `BTC/USDT` → `BTC/USD` for crypto, `BRK/B` → `BRK.B` for equities.

## Limitations / TODO

- [ ] No bracket orders yet (Alpaca supports them; not yet wrapped)
- [ ] No stop or stop-limit orders yet (alpaca-py supports `StopOrderRequest`, `StopLimitOrderRequest`)
- [ ] No historical bar fetching helpers (use `StockHistoricalDataClient.get_stock_bars()` directly)
- [ ] No WebSocket streaming (alpaca-py supports it; not yet wired)
- [ ] No options trading (Alpaca recently added; not implemented)

## Reference

- alpaca-py docs: https://alpaca.markets/sdks/python
- Alpaca API reference: https://docs.alpaca.markets/reference
