# Interactive Brokers (IBKR) Trading Guide

QuantDinger supports US stocks live trading via Interactive Brokers TWS or IB Gateway.

## Overview

This feature enables automated trading execution for US stock markets through your Interactive Brokers account. Once configured, your trading strategies can automatically place orders via the IBKR API.

## Prerequisites

- Interactive Brokers account
- TWS (Trader Workstation) or IB Gateway installed
- Market data subscription (for real-time quotes)

## Installation

The `ib_insync` library is already included in `requirements.txt`. If you need to install manually:

```bash
pip install ib_insync
```

## Port Reference

| Client | Live Port | Paper Port |
|--------|-----------|------------|
| TWS    | 7496      | 7497       |
| IB Gateway | 4001  | 4002       |

> Per IBKR TWS API defaults: production TWS uses **7496**, paper TWS uses **7497**. Ports are configurable in TWS/Gateway, so your client must match the value shown under API settings.

## TWS / IB Gateway Configuration

1. Open TWS or IB Gateway
2. Go to **Configure** → **API** → **Settings**
3. Enable the following options:
   - ✅ Enable ActiveX and Socket Clients
   - ✅ Allow connections from localhost only
4. Set Socket port (refer to the table above)
5. Click Apply / OK

## Strategy Configuration

When creating a strategy for US stocks, configure the IBKR connection in the "Live Trading" section:

| Field | Description | Example |
|-------|-------------|---------|
| **Broker** | Select "Interactive Brokers" | - |
| **Host** | TWS/Gateway host address | `127.0.0.1` |
| **Port** | TWS/Gateway API port | `7496` (TWS Live), `7497` (TWS Paper) |
| **Client ID** | Unique client identifier | `1` |
| **Account** | Account ID (optional) | Leave empty to auto-select |

## Symbol Format

| Market | Format | Examples |
|--------|--------|----------|
| US Stock | Ticker symbol | `AAPL`, `TSLA`, `GOOGL`, `MSFT` |

## Trading Flow

```
Strategy Signal → Pending Order Queue → IBKR Execution → Position Update
```

1. Your strategy generates a buy/sell signal
2. The signal is queued as a pending order
3. The background worker connects to IBKR and executes the order
4. Position and trade records are updated

## Supported Signal Types

| Signal | Action | Description |
|--------|--------|-------------|
| `open_long` | BUY | Open a long position |
| `add_long` | BUY | Add to existing long position |
| `close_long` | SELL | Close long position |
| `reduce_long` | SELL | Reduce long position |

> **Note**: Short selling is not supported in the current implementation.

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

### Test Connection (via curl)

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
```

## Important Notes

1. **TWS/Gateway must be running**: Ensure TWS or IB Gateway is started and logged in before trading
2. **Market data subscription**: Real-time quotes may require market data subscription from IBKR
3. **Client ID**: Use different clientId if multiple programs connect to the same TWS/Gateway
4. **Account selection**: Specify `account` parameter if you have multiple sub-accounts
5. **Trading hours**: Orders will only execute during market hours

## Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| Connection failed | TWS/Gateway not running | Start and login to TWS/Gateway |
| Connection failed | Wrong port | Check API port setting in TWS/Gateway |
| Connection failed | API not enabled | Enable Socket API in TWS/Gateway settings |
| Client ID conflict | Same clientId already connected | Use a different clientId |
| Invalid contract | Wrong symbol format | Check symbol format |
| Order rejected | Insufficient funds/margin | Check account balance |

## Docker Deployment

When running QuantDinger in Docker, TWS/IB Gateway must be accessible from the container:

1. Run TWS/Gateway on host machine
2. Use `host.docker.internal` as the host address (Docker Desktop)
3. Or configure host network mode

## Security Recommendations

- Only enable "Allow connections from localhost only" in TWS/Gateway
- Use paper trading account for testing
- Set appropriate position limits in your strategy
- Monitor your account regularly

## See Also

- [Python Strategy Development Guide](STRATEGY_DEV_GUIDE.md)
- [Interactive Brokers API Documentation](https://interactivebrokers.github.io/tws-api/)
