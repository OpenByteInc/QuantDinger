"""
Interactive Brokers (IBKR) Trading Module

Supports US stocks trading via TWS or IB Gateway.

Port Reference:
- TWS Live: 7496, TWS Paper: 7497
- IB Gateway Live: 4001, IB Gateway Paper: 4002
"""

from app.services.ibkr_trading.client import IBKRClient, IBKRConfig
from app.services.ibkr_trading.symbols import normalize_symbol, parse_symbol

__all__ = ['IBKRClient', 'IBKRConfig', 'normalize_symbol', 'parse_symbol']
