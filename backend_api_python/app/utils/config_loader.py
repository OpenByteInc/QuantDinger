"""
Config loader (local-only).

This project is fully localized: all sensitive configuration should come from
`backend_api_python/.env` (or OS environment variables).

We keep the return shape compatible with the old PHP `loadConfig`:
flat keys like `openrouter.api_key` become nested dicts like:
{
  "openrouter": {"api_key": "..."}
}
"""
from typing import Dict, Any, Optional, List, Tuple
import os
from pathlib import Path

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Config cache.
_config_cache: Optional[Dict[str, Any]] = None
_env_loaded = False


def _load_env_files_once() -> None:
    """Load repo/backend .env files when the process entrypoint did not do it."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    try:
        from dotenv import load_dotenv
    except Exception as e:
        logger.debug(f"python-dotenv unavailable; using process env only: {e}")
        return

    backend_dir = Path(__file__).resolve().parents[2]
    root_dir = backend_dir.parent
    for env_path in (root_dir / ".env", backend_dir / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=True)


def load_addon_config() -> Dict[str, Any]:
    """
    Build config from environment variables (.env / OS env).

    NOTE: We intentionally do NOT load secrets from the database.

    Returns:
        Nested config dict (PHP-compatible shape)
    """
    global _config_cache
    
    # Return cached config when available.
    if _config_cache is not None:
        return _config_cache
    _load_env_files_once()
    
    config: Dict[str, Any] = {}

    def set_nested(cfg: Dict[str, Any], dotted_key: str, value: Any) -> None:
        keys = dotted_key.split('.')
        ref = cfg
        for i, k in enumerate(keys):
            if i == len(keys) - 1:
                ref[k] = value
            else:
                if k not in ref or not isinstance(ref[k], dict):
                    ref[k] = {}
                ref = ref[k]

    def env_get(name: str) -> Optional[str]:
        val = os.getenv(name)
        if val is None:
            return None
        val = str(val).strip()
        return val if val != '' else None

    # Map env vars to PHP-style dotted keys.
    mappings: List[Tuple[str, str, str]] = [
        # internal
        ('INTERNAL_API_KEY', 'internal_api.key', 'string'),

        # OpenRouter / LLM
        ('OPENROUTER_API_KEY', 'openrouter.api_key', 'string'),
        ('OPENROUTER_API_URL', 'openrouter.api_url', 'string'),
        ('OPENROUTER_MODEL', 'openrouter.model', 'string'),
        ('OPENROUTER_TEMPERATURE', 'openrouter.temperature', 'float'),
        ('OPENROUTER_MAX_TOKENS', 'openrouter.max_tokens', 'int'),
        ('OPENROUTER_TIMEOUT', 'openrouter.timeout', 'int'),
        ('OPENROUTER_CONNECT_TIMEOUT', 'openrouter.connect_timeout', 'int'),
        
        # OpenAI Direct
        ('OPENAI_API_KEY', 'openai.api_key', 'string'),
        ('OPENAI_BASE_URL', 'openai.base_url', 'string'),
        ('OPENAI_MODEL', 'openai.model', 'string'),
        
        # Google Gemini
        ('GOOGLE_API_KEY', 'google.api_key', 'string'),
        ('GOOGLE_MODEL', 'google.model', 'string'),
        
        # DeepSeek
        ('DEEPSEEK_API_KEY', 'deepseek.api_key', 'string'),
        ('DEEPSEEK_BASE_URL', 'deepseek.base_url', 'string'),
        ('DEEPSEEK_MODEL', 'deepseek.model', 'string'),
        
        # xAI Grok
        ('GROK_API_KEY', 'grok.api_key', 'string'),
        ('GROK_BASE_URL', 'grok.base_url', 'string'),
        ('GROK_MODEL', 'grok.model', 'string'),

        # AtlasCloud
        ('ATLASCLOUD_API_KEY', 'atlascloud.api_key', 'string'),
        ('ATLASCLOUD_BASE_URL', 'atlascloud.base_url', 'string'),
        ('ATLASCLOUD_MODEL', 'atlascloud.model', 'string'),

        # Custom OpenAI-compatible endpoint (see LLMProvider.CUSTOM)
        ('CUSTOM_API_KEY', 'custom.api_key', 'string'),
        ('CUSTOM_API_URL', 'custom.base_url', 'string'),
        ('CUSTOM_MODEL', 'custom.model', 'string'),

        # MiniMax
        ('MINIMAX_API_KEY', 'minimax.api_key', 'string'),
        ('MINIMAX_BASE_URL', 'minimax.base_url', 'string'),
        ('MINIMAX_MODEL', 'minimax.model', 'string'),

        # LiteLLM
        ('LITELLM_API_KEY', 'litellm.api_key', 'string'),
        ('LITELLM_BASE_URL', 'litellm.base_url', 'string'),
        ('LITELLM_MODEL', 'litellm.model', 'string'),

        # LLM Provider Selection
        ('LLM_PROVIDER', 'llm.provider', 'string'),
        ('LLM_PROXY_URL', 'llm.proxy_url', 'string'),
        ('LLM_USE_SYSTEM_PROXY', 'llm.use_system_proxy', 'bool'),

        # App
        ('RATE_LIMIT', 'app.rate_limit', 'int'),
        ('ENABLE_CACHE', 'app.enable_cache', 'bool'),
        ('ENABLE_REQUEST_LOG', 'app.enable_request_log', 'bool'),

        # Data source common
        ('DATA_SOURCE_TIMEOUT', 'data_source.timeout', 'int'),
        ('DATA_SOURCE_RETRY', 'data_source.retry_count', 'int'),
        ('DATA_SOURCE_RETRY_BACKOFF', 'data_source.retry_backoff', 'float'),

        # Finnhub
        ('FINNHUB_API_KEY', 'finnhub.api_key', 'string'),
        ('FINNHUB_TIMEOUT', 'finnhub.timeout', 'int'),
        ('FINNHUB_RATE_LIMIT', 'finnhub.rate_limit', 'int'),
        ('FINNHUB_FREE_ONLY', 'finnhub.free_only', 'bool'),

        # Trading Economics calendar (guest/free works without a paid key)
        ('TRADING_ECONOMICS_CLIENT', 'tradingeconomics.client', 'string'),
        ('TRADING_ECONOMICS_KEY', 'tradingeconomics.key', 'string'),
        ('TRADING_ECONOMICS_BASE_URL', 'tradingeconomics.base_url', 'string'),
        ('TRADING_ECONOMICS_TIMEOUT', 'tradingeconomics.timeout', 'int'),

        # Macro research sources
        ('FRED_API_KEY', 'fred.api_key', 'string'),
        ('FRED_BASE_URL', 'fred.base_url', 'string'),
        ('FRED_TIMEOUT', 'fred.timeout', 'int'),
        ('BLS_API_KEY', 'bls.api_key', 'string'),
        ('BLS_BASE_URL', 'bls.base_url', 'string'),
        ('BLS_TIMEOUT', 'bls.timeout', 'int'),
        ('BEA_API_KEY', 'bea.api_key', 'string'),
        ('BEA_BASE_URL', 'bea.base_url', 'string'),
        ('BEA_TIMEOUT', 'bea.timeout', 'int'),

        # Crypto analytics
        ('COINGLASS_API_KEY', 'coinglass.api_key', 'string'),
        ('CRYPTOQUANT_API_KEY', 'cryptoquant.api_key', 'string'),

        # CCXT
        ('CCXT_DEFAULT_EXCHANGE', 'ccxt.default_exchange', 'string'),
        ('CCXT_TIMEOUT', 'ccxt.timeout', 'int'),

        # Other sources
        ('YFINANCE_TIMEOUT', 'yfinance.timeout', 'int'),
        ('AKSHARE_TIMEOUT', 'akshare.timeout', 'int'),
        ('TIINGO_API_KEY', 'tiingo.api_key', 'string'),
        ('TIINGO_TIMEOUT', 'tiingo.timeout', 'int'),
        ('TWELVE_DATA_API_KEY', 'twelve_data.api_key', 'string'),

        # Search (Google CSE / Bing)
        ('SEARCH_PROVIDER', 'search.provider', 'string'),
        ('SEARCH_MAX_RESULTS', 'search.max_results', 'int'),
        ('SEARCH_GOOGLE_API_KEY', 'search.google.api_key', 'string'),
        ('SEARCH_GOOGLE_CX', 'search.google.cx', 'string'),
        ('SEARCH_BING_API_KEY', 'search.bing.api_key', 'string'),
        ('SEARCH_SEARXNG_BASE_URL', 'search.searxng.base_url', 'string'),
        ('SEARCH_SEARXNG_ENGINES', 'search.searxng.engines', 'string'),
        ('SEARCH_SEARXNG_CATEGORIES', 'search.searxng.categories', 'string'),
        ('SEARCH_SEARXNG_LANGUAGE', 'search.searxng.language', 'string'),
        ('SEARCH_SEARXNG_TIMEOUT', 'search.searxng.timeout', 'int'),
        
        # Tavily (AI-optimized search)
        ('TAVILY_API_KEYS', 'tavily.api_keys', 'string'),
        
        # SerpAPI (Google/Bing scraper)
        ('SERPAPI_KEYS', 'serpapi.api_keys', 'string'),

        # Free/global news and company news/sentiment
        ('GDELT_BASE_URL', 'gdelt.base_url', 'string'),
        ('GDELT_TIMEOUT', 'gdelt.timeout', 'int'),
        ('GDELT_MAX_RESULTS', 'gdelt.max_results', 'int'),
        ('ALPHA_VANTAGE_API_KEY', 'alpha_vantage.api_key', 'string'),
        ('ALPHA_VANTAGE_BASE_URL', 'alpha_vantage.base_url', 'string'),
        ('ALPHA_VANTAGE_TIMEOUT', 'alpha_vantage.timeout', 'int'),
        ('ALPHA_VANTAGE_NEWS_LIMIT', 'alpha_vantage.news_limit', 'int'),
    ]

    for env_name, dotted_key, value_type in mappings:
        raw = env_get(env_name)
        if raw is None:
            continue
        try:
            value = _convert_config_value(raw, value_type)
            set_nested(config, dotted_key, value)
        except Exception as e:
            logger.warning(f"Config env parse failed: {env_name} -> {dotted_key}: {e}")

    _config_cache = config
    return config


def _convert_config_value(value: str, value_type: str) -> Any:
    """
    Convert configuration values by type.

    Args:
        value: Raw configuration value string.
        value_type: Target configuration type.

    Returns:
        Converted configuration value.
    """
    # Handle None or empty values.
    if value is None or value == '':
        if value_type == 'int':
            return 0
        elif value_type == 'float':
            return 0.0
        elif value_type == 'bool':
            return False
        elif value_type == 'json':
            return {}
        else:
            return ''
    
    try:
        if value_type == 'int':
            return int(value)
        elif value_type == 'float':
            return float(value)
        elif value_type == 'bool':
            return bool(value) or value == '1' or value == 'true' or value == 'True'
        elif value_type == 'json':
            import json
            try:
                return json.loads(value) if value else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        else:
            return str(value) if value is not None else ''
    except (ValueError, TypeError) as e:
        logger.warning(f"Config value type conversion failed: value={value}, type={value_type}, error={str(e)}")
        # Return the raw value when conversion fails.
        if value_type == 'int':
            return 0
        elif value_type == 'float':
            return 0.0
        elif value_type == 'bool':
            return False
        elif value_type == 'json':
            return {}
        else:
            return str(value) if value is not None else ''


def get_internal_api_key() -> Optional[str]:
    """
    Get the internal API key from environment-backed configuration.

    Returns:
        Internal API key, or None when unset.
    """
    try:
        env_val = os.getenv('INTERNAL_API_KEY', '').strip()
        if env_val:
            return env_val

        config = load_addon_config()
        api_key = config.get('internal_api', {}).get('key')
        
        if api_key:
            logger.debug(f"Loaded INTERNAL_API_KEY from env-config shape, length: {len(api_key)}")
        else:
            logger.warning("Missing INTERNAL_API_KEY (env).")
        
        return api_key
    except Exception as e:
        logger.error(f"Failed to load internal API key: {str(e)}")
        return None


def clear_config_cache():
    """Clear the configuration cache."""
    global _config_cache, _env_loaded
    _config_cache = None
    _env_loaded = False
    logger.debug("Addon config cache cleared")

