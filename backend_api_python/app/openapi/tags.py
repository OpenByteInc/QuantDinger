"""OpenAPI tag names (English). Keep stable for published docs."""

HEALTH = "Health"
POLICY = "Policy"
AUTH = "Auth"
USERS = "Users"
MARKET = "Market"
UNIVERSE = "Universe"
FACTOR = "Factor"
INDICATOR = "Indicator"
BACKTEST = "Backtest"
STRATEGY = "Strategy"
ACCOUNT = "Account"
COMMUNITY = "Community"
CREDENTIALS = "Credentials"
DASHBOARD = "Dashboard"
PORTFOLIO = "Portfolio"
SETTINGS = "Settings"
BILLING = "Billing"
FAST_ANALYSIS = "FastAnalysis"
GLOBAL_MARKET = "GlobalMarket"
AI_CHAT = "AIChat"
QUICK_TRADE = "QuickTrade"
IBKR = "IBKR"
ALPACA = "Alpaca"
FUTU = "Futu"

ALL_TAGS = [
    {"name": HEALTH, "description": "Liveness and API metadata (Public)"},
    {"name": POLICY, "description": "Capability and broker policy discovery (Public)"},
    {"name": AUTH, "description": "Authentication and OAuth (Public)"},
    {"name": USERS, "description": "User profile and administration (Mixed)"},
    {"name": MARKET, "description": "Market data and watchlists (Public)"},
    {"name": UNIVERSE, "description": "Point-in-time strategy universes (Internal)"},
    {"name": FACTOR, "description": "Versioned factor catalog and research diagnostics (Internal)"},
    {"name": INDICATOR, "description": "Indicator IDE workspace (Public)"},
    {"name": BACKTEST, "description": "Unified V2 backtesting (Public)"},
    {"name": STRATEGY, "description": "Strategy runtime and bots (Internal)"},
    {"name": ACCOUNT, "description": "Trading account snapshots and positions (Internal)"},
    {"name": COMMUNITY, "description": "Indicator marketplace (Public)"},
    {"name": CREDENTIALS, "description": "Exchange credential vault (Internal)"},
    {"name": DASHBOARD, "description": "Dashboard aggregates (Internal)"},
    {"name": PORTFOLIO, "description": "Manual portfolio tracking (Internal)"},
    {"name": SETTINGS, "description": "System and brand settings (Mixed)"},
    {"name": BILLING, "description": "Membership and USDT billing (Internal)"},
    {"name": FAST_ANALYSIS, "description": "Fast AI analysis (Public)"},
    {"name": GLOBAL_MARKET, "description": "Global market overview (Public)"},
    {"name": AI_CHAT, "description": "Legacy AI chat compatibility (Internal)"},
    {"name": QUICK_TRADE, "description": "Manual quick trade (Internal)"},
    {"name": IBKR, "description": "Interactive Brokers adapter (Internal)"},
    {"name": ALPACA, "description": "Alpaca adapter (Internal)"},
    {"name": FUTU, "description": "Futu OpenAPI / OpenD adapter (Internal)"},
]
