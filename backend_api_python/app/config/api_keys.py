"""
API key configuration.
All third-party keys should be provided via environment variables (recommended: backend_api_python/.env).
"""
import os

class MetaAPIKeys(type):
    """API Keys 元类，用于支持类属性的动态获取"""
    
    @property
    def FINNHUB_API_KEY(cls):
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('finnhub', {}).get('api_key')
        return val if val else os.getenv('FINNHUB_API_KEY', '')

    @property
    def COINGLASS_API_KEY(cls):
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('coinglass', {}).get('api_key')
        return val if val else os.getenv('COINGLASS_API_KEY', '')

    @property
    def CRYPTOQUANT_API_KEY(cls):
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('cryptoquant', {}).get('api_key')
        return val if val else os.getenv('CRYPTOQUANT_API_KEY', '')
    
    @property
    def TIINGO_API_KEY(cls):
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('tiingo', {}).get('api_key')
        return val if val else os.getenv('TIINGO_API_KEY', '')

    @property
    def TWELVE_DATA_API_KEY(cls):
        env_val = os.getenv('TWELVE_DATA_API_KEY', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('twelve_data', {}).get('api_key')
        return val if val else ''

    @property
    def ADANOS_API_KEY(cls):
        """Adanos Market Sentiment API key (optional)."""
        env_val = os.getenv('ADANOS_API_KEY', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('adanos', {}).get('api_key')
        return val if val else ''

    @property
    def FRED_API_KEY(cls):
        """FRED API key for US macro time series."""
        env_val = os.getenv('FRED_API_KEY', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('fred', {}).get('api_key')
        return val if val else ''

    @property
    def BLS_API_KEY(cls):
        """Optional BLS registration key for official US labor/CPI data."""
        env_val = os.getenv('BLS_API_KEY', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('bls', {}).get('api_key')
        return val if val else ''

    @property
    def BEA_API_KEY(cls):
        """BEA API key for official US national accounts data."""
        env_val = os.getenv('BEA_API_KEY', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('bea', {}).get('api_key')
        return val if val else ''

    @property
    def ALPHA_VANTAGE_API_KEY(cls):
        """Alpha Vantage key for NEWS_SENTIMENT company news data."""
        env_val = os.getenv('ALPHA_VANTAGE_API_KEY', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('alpha_vantage', {}).get('api_key')
        return val if val else ''
    
    @property
    def OPENROUTER_API_KEY(cls):
        # Always check env var first to avoid stale cache issues
        env_val = os.getenv('OPENROUTER_API_KEY', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('openrouter', {}).get('api_key')
        return val if val else ''
    
    @property
    def OPENAI_API_KEY(cls):
        """OpenAI direct API key"""
        env_val = os.getenv('OPENAI_API_KEY', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('openai', {}).get('api_key')
        return val if val else ''
    
    @property
    def GOOGLE_API_KEY(cls):
        """Google Gemini API key"""
        env_val = os.getenv('GOOGLE_API_KEY', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('google', {}).get('api_key')
        return val if val else ''
    
    @property
    def DEEPSEEK_API_KEY(cls):
        """DeepSeek API key"""
        env_val = os.getenv('DEEPSEEK_API_KEY', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('deepseek', {}).get('api_key')
        return val if val else ''
    
    @property
    def GROK_API_KEY(cls):
        """xAI Grok API key"""
        env_val = os.getenv('GROK_API_KEY', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('grok', {}).get('api_key')
        return val if val else ''

    @property
    def ATLASCLOUD_API_KEY(cls):
        """AtlasCloud API key"""
        env_val = os.getenv('ATLASCLOUD_API_KEY', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('atlascloud', {}).get('api_key')
        return val if val else ''

    @property
    def CUSTOM_API_KEY(cls):
        """Custom LLM API key (for OpenAI-compatible custom endpoints)"""
        env_val = os.getenv('CUSTOM_API_KEY', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('custom', {}).get('api_key')
        return val if val else ''

    @property
    def CUSTOM_API_URL(cls):
        """Custom LLM API base URL (e.g., https://your-api.com/v1)"""
        env_val = os.getenv('CUSTOM_API_URL', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('custom', {}).get('base_url')
        return val if val else ''

    @property
    def CUSTOM_MODEL(cls):
        """Custom LLM model name"""
        env_val = os.getenv('CUSTOM_MODEL', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('custom', {}).get('model')
        return val if val else ''

    @property
    def MINIMAX_API_KEY(cls):
        """MiniMax API key"""
        env_val = os.getenv('MINIMAX_API_KEY', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('minimax', {}).get('api_key')
        return val if val else ''

    @property
    def LITELLM_API_KEY(cls):
        """LiteLLM API key (optional, litellm reads provider env vars automatically)"""
        env_val = os.getenv('LITELLM_API_KEY', '').strip()
        if env_val:
            return env_val
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('litellm', {}).get('api_key')
        return val if val else ''
    
    @property
    def TAVILY_API_KEYS(cls):
        """Tavily Search API keys (comma-separated for rotation)"""
        env_val = os.getenv('TAVILY_API_KEYS', '').strip()
        if env_val:
            return [k.strip() for k in env_val.split(',') if k.strip()]
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('tavily', {}).get('api_keys', '')
        if val:
            return [k.strip() for k in val.split(',') if k.strip()]
        return []
    
    @property
    def SERPAPI_KEYS(cls):
        """SerpAPI keys (comma-separated for rotation)"""
        env_val = os.getenv('SERPAPI_KEYS', '').strip()
        if env_val:
            return [k.strip() for k in env_val.split(',') if k.strip()]
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('serpapi', {}).get('api_keys', '')
        if val:
            return [k.strip() for k in val.split(',') if k.strip()]
        return []


class APIKeys(metaclass=MetaAPIKeys):
    """API 密钥配置类"""
    
    @classmethod
    def get(cls, key_name: str, default: str = '') -> str:
        """获取 API 密钥"""
        if hasattr(cls, key_name):
            return getattr(cls, key_name)
        return default
    
    @classmethod
    def is_configured(cls, key_name: str) -> bool:
        """检查 API 密钥是否已配置"""
        value = cls.get(key_name)
        return bool(value and value.strip())
