"""Market, news, and macro data source configuration."""
import os


def _addon(section: str, key: str):
    from app.utils.config_loader import load_addon_config
    value = load_addon_config()
    for part in section.split('.'):
        if not isinstance(value, dict):
            return None
        value = value.get(part, {})
    return value.get(key) if isinstance(value, dict) else None


def _config_str(section: str, key: str, env_name: str, default: str = "") -> str:
    value = _addon(section, key)
    return str(value or os.getenv(env_name, default) or "").strip()


def _config_int(section: str, key: str, env_name: str, default: int) -> int:
    value = _addon(section, key)
    if value is None:
        value = os.getenv(env_name, str(default))
    return int(value)


def _config_float(section: str, key: str, env_name: str, default: float) -> float:
    value = _addon(section, key)
    if value is None:
        value = os.getenv(env_name, str(default))
    return float(value)


class MetaDataSourceConfig(type):
    @property
    def DEFAULT_TIMEOUT(cls):
        return _config_int('data_source', 'timeout', 'DATA_SOURCE_TIMEOUT', 30)

    @property
    def RETRY_COUNT(cls):
        return _config_int('data_source', 'retry_count', 'DATA_SOURCE_RETRY', 3)

    @property
    def RETRY_BACKOFF(cls):
        return _config_float('data_source', 'retry_backoff', 'DATA_SOURCE_RETRY_BACKOFF', 0.5)


class DataSourceConfig(metaclass=MetaDataSourceConfig):
    """Common data source settings."""
    pass


class MetaFinnhubConfig(type):
    @property
    def BASE_URL(cls):
        return "https://finnhub.io/api/v1"

    @property
    def TIMEOUT(cls):
        return _config_int('finnhub', 'timeout', 'FINNHUB_TIMEOUT', 10)

    @property
    def RATE_LIMIT(cls):
        return _config_int('finnhub', 'rate_limit', 'FINNHUB_RATE_LIMIT', 60)

    @property
    def RATE_LIMIT_PERIOD(cls):
        return 60

    @property
    def FREE_ONLY(cls):
        value = _addon('finnhub', 'free_only')
        if value is None:
            value = os.getenv('FINNHUB_FREE_ONLY', 'true')
        return str(value).strip().lower() not in ('0', 'false', 'no', 'off')


class FinnhubConfig(metaclass=MetaFinnhubConfig):
    """Finnhub data source configuration."""
    pass


class MetaTradingEconomicsConfig(type):
    @property
    def BASE_URL(cls):
        return _config_str('tradingeconomics', 'base_url', 'TRADING_ECONOMICS_BASE_URL', 'https://api.tradingeconomics.com').rstrip('/')

    @property
    def CLIENT(cls):
        return _config_str('tradingeconomics', 'client', 'TRADING_ECONOMICS_CLIENT')

    @property
    def KEY(cls):
        return _config_str('tradingeconomics', 'key', 'TRADING_ECONOMICS_KEY')

    @property
    def TIMEOUT(cls):
        return _config_int('tradingeconomics', 'timeout', 'TRADING_ECONOMICS_TIMEOUT', 10)

    @property
    def CREDENTIALS(cls):
        client = str(cls.CLIENT or '').strip()
        key = str(cls.KEY or '').strip()
        if ':' in client and not key:
            return client
        if not client or not key:
            return ''
        return f"{client}:{key}"

    @property
    def CONFIGURED(cls):
        credentials = str(cls.CREDENTIALS or '').strip().lower()
        return bool(credentials and credentials not in ('guest', 'guest:guest'))


class TradingEconomicsConfig(metaclass=MetaTradingEconomicsConfig):
    """Trading Economics calendar configuration."""
    pass


class MetaFredConfig(type):
    @property
    def BASE_URL(cls):
        return _config_str('fred', 'base_url', 'FRED_BASE_URL', 'https://api.stlouisfed.org/fred').rstrip('/')

    @property
    def API_KEY(cls):
        return _config_str('fred', 'api_key', 'FRED_API_KEY')

    @property
    def TIMEOUT(cls):
        return _config_int('fred', 'timeout', 'FRED_TIMEOUT', 10)

    @property
    def CONFIGURED(cls):
        return bool(cls.API_KEY)


class FredConfig(metaclass=MetaFredConfig):
    """FRED macro time-series configuration."""
    pass


class MetaBLSConfig(type):
    @property
    def BASE_URL(cls):
        return _config_str('bls', 'base_url', 'BLS_BASE_URL', 'https://api.bls.gov/publicAPI/v2').rstrip('/')

    @property
    def API_KEY(cls):
        return _config_str('bls', 'api_key', 'BLS_API_KEY')

    @property
    def TIMEOUT(cls):
        return _config_int('bls', 'timeout', 'BLS_TIMEOUT', 10)

    @property
    def CONFIGURED(cls):
        return True


class BLSConfig(metaclass=MetaBLSConfig):
    """BLS official labor and CPI data configuration."""
    pass


class MetaBEAConfig(type):
    @property
    def BASE_URL(cls):
        return _config_str('bea', 'base_url', 'BEA_BASE_URL', 'https://apps.bea.gov/api/data')

    @property
    def API_KEY(cls):
        return _config_str('bea', 'api_key', 'BEA_API_KEY')

    @property
    def TIMEOUT(cls):
        return _config_int('bea', 'timeout', 'BEA_TIMEOUT', 10)

    @property
    def CONFIGURED(cls):
        return bool(cls.API_KEY)


class BEAConfig(metaclass=MetaBEAConfig):
    """BEA official national accounts data configuration."""
    pass


class MetaGDELTConfig(type):
    @property
    def BASE_URL(cls):
        return _config_str('gdelt', 'base_url', 'GDELT_BASE_URL', 'https://api.gdeltproject.org/api/v2/doc/doc')

    @property
    def TIMEOUT(cls):
        return _config_int('gdelt', 'timeout', 'GDELT_TIMEOUT', 12)

    @property
    def MAX_RESULTS(cls):
        return _config_int('gdelt', 'max_results', 'GDELT_MAX_RESULTS', 10)

    @property
    def CONFIGURED(cls):
        return True


class GDELTConfig(metaclass=MetaGDELTConfig):
    """GDELT DOC 2.0 global news fallback configuration."""
    pass


class MetaSearXNGConfig(type):
    @property
    def BASE_URL(cls):
        return _config_str('search.searxng', 'base_url', 'SEARCH_SEARXNG_BASE_URL')

    @property
    def ENGINES(cls):
        return _config_str('search.searxng', 'engines', 'SEARCH_SEARXNG_ENGINES')

    @property
    def CATEGORIES(cls):
        return _config_str('search.searxng', 'categories', 'SEARCH_SEARXNG_CATEGORIES', 'general')

    @property
    def LANGUAGE(cls):
        return _config_str('search.searxng', 'language', 'SEARCH_SEARXNG_LANGUAGE', 'auto')

    @property
    def TIMEOUT(cls):
        return _config_int('search.searxng', 'timeout', 'SEARCH_SEARXNG_TIMEOUT', 12)

    @property
    def CONFIGURED(cls):
        return bool(cls.BASE_URL)


class SearXNGConfig(metaclass=MetaSearXNGConfig):
    """Self-hosted SearXNG metasearch configuration."""
    pass


class MetaAlphaVantageConfig(type):
    @property
    def BASE_URL(cls):
        return _config_str('alpha_vantage', 'base_url', 'ALPHA_VANTAGE_BASE_URL', 'https://www.alphavantage.co/query')

    @property
    def API_KEY(cls):
        return _config_str('alpha_vantage', 'api_key', 'ALPHA_VANTAGE_API_KEY')

    @property
    def TIMEOUT(cls):
        return _config_int('alpha_vantage', 'timeout', 'ALPHA_VANTAGE_TIMEOUT', 12)

    @property
    def NEWS_LIMIT(cls):
        return _config_int('alpha_vantage', 'news_limit', 'ALPHA_VANTAGE_NEWS_LIMIT', 20)

    @property
    def CONFIGURED(cls):
        return bool(cls.API_KEY)


class AlphaVantageConfig(metaclass=MetaAlphaVantageConfig):
    """Alpha Vantage NEWS_SENTIMENT configuration."""
    pass


class MetaTiingoConfig(type):
    @property
    def BASE_URL(cls):
        return "https://api.tiingo.com/tiingo"

    @property
    def TIMEOUT(cls):
        return _config_int('tiingo', 'timeout', 'TIINGO_TIMEOUT', 10)


class TiingoConfig(metaclass=MetaTiingoConfig):
    """Tiingo data source configuration."""
    pass


class MetaYFinanceConfig(type):
    @property
    def TIMEOUT(cls):
        return _config_int('yfinance', 'timeout', 'YFINANCE_TIMEOUT', 30)

    @property
    def INTERVAL_MAP(cls):
        return {
            '1m': '1m',
            '3m': '3m',
            '5m': '5m',
            '15m': '15m',
            '30m': '30m',
            '1H': '1h',
            '4H': '4h',
            '1D': '1d',
            '1W': '1wk',
        }


class YFinanceConfig(metaclass=MetaYFinanceConfig):
    """Yahoo Finance data source configuration."""
    pass


class MetaCCXTConfig(type):
    @property
    def DEFAULT_EXCHANGE(cls):
        value = _addon('ccxt', 'default_exchange')
        return value if value else os.getenv('CCXT_DEFAULT_EXCHANGE', 'binance')

    @property
    def TIMEOUT(cls):
        return _config_int('ccxt', 'timeout', 'CCXT_TIMEOUT', 10000)

    @property
    def ENABLE_RATE_LIMIT(cls):
        return True

    @property
    def TIMEFRAME_MAP(cls):
        return {
            '1m': '1m',
            '3m': '3m',
            '5m': '5m',
            '15m': '15m',
            '30m': '30m',
            '1H': '1h',
            '4H': '4h',
            '1D': '1d',
            '1W': '1w',
        }

    @property
    def PROXY(cls):
        proxy_url = (os.getenv('PROXY_URL') or '').strip()
        if proxy_url:
            return proxy_url

        for key in ['HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY']:
            value = (os.getenv(key) or '').strip()
            if value:
                return value

        return ''


class CCXTConfig(metaclass=MetaCCXTConfig):
    """CCXT crypto market data configuration."""
    pass


class MetaAkshareConfig(type):
    @property
    def TIMEOUT(cls):
        return _config_int('akshare', 'timeout', 'AKSHARE_TIMEOUT', 30)

    @property
    def PERIOD_MAP(cls):
        return {
            '1D': 'daily',
            '1W': 'weekly',
        }


class AkshareConfig(metaclass=MetaAkshareConfig):
    """AkShare data source configuration."""
    pass
