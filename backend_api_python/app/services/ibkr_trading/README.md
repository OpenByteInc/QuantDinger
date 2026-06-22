# Interactive Brokers Trading Module

Supports US stocks trading via TWS or IB Gateway.

## Installation

```bash
pip install ib_insync
```

Or the dependency is already in `requirements.txt`.

## Port Reference

| Client | Live Port | Paper Port |
|--------|-----------|------------|
| TWS    | 7496      | 7497       |
| IB Gateway | 4001  | 4002       |

## TWS / IB Gateway Configuration

1. Open TWS or IB Gateway
2. Go to **Configure** -> **API** -> **Settings**
3. Enable the following options:
   - ✅ Enable ActiveX and Socket Clients
   - ✅ Allow connections from localhost only
4. Set Socket port (refer to the table above)
5. Click Apply / OK

### Client ID (important)

Interactive Brokers allows **one API connection per `clientId`**. If two programs use the same id, **the newer connection replaces the older** — often within a few seconds it looks like a random disconnect, and live orders then fail with “Failed to connect”.

- `POST /api/ibkr/connect` (admin UI test) defaults to **clientId=1**.
- Strategy / live-order clients use **`ibkr_client_id` from credentials** (default **7** when omitted) or env **`IBKR_ORDER_CLIENT_ID`** (default `7`).

Keep manual testing on **1** and automation on **7** (or any other unused id). If you still see drops on Windows, ensure `ib_insync` runs with asyncio patched (QuantDinger calls `util.patchAsyncio()` at app startup).

## API Endpoints

### Connection Management

```
GET  /api/ibkr/status          # Get connection status
POST /api/ibkr/connect         # Connect to TWS/Gateway
POST /api/ibkr/disconnect      # Disconnect
```

### Account Queries

```
GET  /api/ibkr/account         # Account information
GET  /api/ibkr/positions       # Current positions
GET  /api/ibkr/orders          # Open orders
```

### Trading

```
POST   /api/ibkr/order         # Place order
DELETE /api/ibkr/order/<id>    # Cancel order
```

### Market Data

```
GET  /api/ibkr/quote?symbol=AAPL&marketType=USStock
```

## Usage Examples

### Connect

```bash
curl -X POST http://localhost:5000/api/ibkr/connect \
  -H "Content-Type: application/json" \
  -d '{"host": "127.0.0.1", "port": 7497, "clientId": 1}'  # TWS Paper default
```

### Place Order

```bash
# Market order: buy 10 shares of AAPL
curl -X POST http://localhost:5000/api/ibkr/order \
  -H "Content-Type: application/json" \
  -d '{"symbol": "AAPL", "side": "buy", "quantity": 10, "marketType": "USStock"}'

# Limit order: sell 100 shares of MSFT
curl -X POST http://localhost:5000/api/ibkr/order \
  -H "Content-Type: application/json" \
  -d '{"symbol": "MSFT", "side": "sell", "quantity": 100, "marketType": "USStock", "orderType": "limit", "price": 400}'
```

### Get Positions

```bash
curl http://localhost:5000/api/ibkr/positions
```

## Symbol Format

| Market | Format | Examples |
|--------|--------|----------|
| US Stock | Ticker symbol | `AAPL`, `TSLA`, `GOOGL` |

## Important Notes

1. **TWS/Gateway must be running**: Ensure TWS or IB Gateway is started and logged in before using the API
2. **Market data subscription**: Real-time quotes may require market data subscription
3. **Client ID**: Use different clientId if multiple programs connect to the same TWS/Gateway
4. **Readonly mode**: Set `readonly: true` to only query without trading
5. **Multi-account**: Specify `account` parameter if you have multiple sub-accounts

## Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| Connection failed | TWS/Gateway not running | Start and login to TWS/Gateway |
| Connection failed | Wrong port | Check API port setting in TWS/Gateway |
| Connection failed | API not enabled | Enable Socket API in TWS/Gateway settings |
| Client ID conflict | Same clientId already connected | Use a different clientId |
| Invalid contract | Wrong symbol format | Check symbol format |

## Removing This Module

To remove this module, delete the following files/directories:

```
backend_api_python/app/services/ibkr_trading/    # Entire directory
backend_api_python/app/routes/ibkr.py            # Route file
```

Then remove the related import and registration code in `app/routes/__init__.py`.

## Docker Note

When running in Docker, IBKR trading requires TWS/IB Gateway to be accessible from the container. 
For local deployment, you can:

1. Run TWS/Gateway on host machine
2. Use host network mode or configure port mapping
3. Set `host` to the host machine's IP address (e.g., `host.docker.internal` on Docker Desktop)

> **Note**: IBKR connection parameters are configured per-strategy in the frontend, not via environment variables.
