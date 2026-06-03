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

from app.utils.logger import get_logger

logger = get_logger(__name__)

# 配置缓存
_config_cache: Optional[Dict[str, Any]] = None


def load_addon_config() -> Dict[str, Any]:
    """
    Build config from environment variables (.env / OS env).

    NOTE: We intentionally do NOT load secrets from the database.

    Returns:
        Nested config dict (PHP-compatible shape)
    """
    global _config_cache
    
    # 如果缓存存在，直接返回
    if _config_cache is not None:
        return _config_cache
    
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
        
        # Tavily (AI-optimized search)
        ('TAVILY_API_KEYS', 'tavily.api_keys', 'string'),
        
        # SerpAPI (Google/Bing scraper)
        ('SERPAPI_KEYS', 'serpapi.api_keys', 'string'),
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
    根据类型转换配置值（与PHP端convertConfigValue方法保持一致）
    
    Args:
        value: 配置值字符串（可能为None）
        value_type: 配置类型
        
    Returns:
        转换后的配置值
    """
    # 处理 None 或空值
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
        # 转换失败时返回默认值
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
    获取内部API密钥（优先从环境变量读取）
    
    Returns:
        内部API密钥，如果未配置则返回None
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
    """
    清除配置缓存（配置更新后调用）
    """
    global _config_cache
    _config_cache = None
    logger.debug("Addon config cache cleared")

