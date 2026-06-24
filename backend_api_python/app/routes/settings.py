"""
Settings API — read and write .env configuration.

Admin-only endpoints for system configuration management.
"""
import os
import re
import importlib
from flask import jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
from app._version import APP_VERSION
from app.markets.registry import market_options
from app.utils.logger import get_logger
from app.utils.config_loader import clear_config_cache
from app.utils.auth import login_required, admin_required
from dotenv import load_dotenv

logger = get_logger(__name__)

settings_blp = Blueprint('settings', __name__)

ENV_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')


def _reload_runtime_env() -> None:
    """
    Reload .env into current process so settings take effect immediately.
    Priority keeps backend_api_python/.env over repo-root/.env.
    """
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    root_dir = os.path.dirname(backend_dir)

    # Load root first, then backend .env to keep backend file higher priority
    load_dotenv(os.path.join(root_dir, '.env'), override=True)
    load_dotenv(os.path.join(backend_dir, '.env'), override=True)


def _refresh_runtime_services() -> None:
    """
    Reset singleton services so new env/config is picked up lazily
    on next request without restarting the Python process.
    """
    # Prefer dedicated reset function where available.
    try:
        search_mod = importlib.import_module('app.services.search')
        if hasattr(search_mod, 'reset_search_service'):
            search_mod.reset_search_service()
    except Exception as e:
        logger.warning(f"reset_search_service skipped: {e}")

    # Generic singleton fields used across services.
    singleton_fields = [
        ('app.services.fast_analysis', '_fast_analysis_service'),
        ('app.services.billing_service', '_billing_service'),
        ('app.services.security_service', '_security_service'),
        ('app.services.mfa_service', '_mfa_service'),
        ('app.services.oauth_service', '_oauth_service'),
        ('app.services.user_service', '_user_service'),
        ('app.services.email_service', '_email_service'),
        ('app.services.community_service', '_community_service'),
        ('app.services.usdt_payment_service', '_svc'),
        ('app.services.usdt_payment_service', '_worker'),
        ('app.services.analysis_memory', '_memory_instance'),
    ]

    for module_name, field_name in singleton_fields:
        try:
            mod = importlib.import_module(module_name)
            if hasattr(mod, field_name):
                setattr(mod, field_name, None)
        except Exception as e:
            logger.warning(f"Singleton reset skipped: {module_name}.{field_name}: {e}")

# ---------------------------------------------------------------
# ---------------------------------------------------------------

# Keys that should land in the "Advanced" tab of the Settings page.  Anything
# not listed here defaults to the "Basic" tab.  Keep this list small and
# intentional — only put truly rarely changed knobs here so the basic tab stays
# useful for day-to-day operators.
ADVANCED_KEYS = {
    # AI tuning
    'OPENROUTER_TEMPERATURE',
    'AI_ANALYSIS_CONSENSUS_TIMEFRAMES', 'SEARCH_MAX_RESULTS',
    'SEARCH_GOOGLE_API_KEY', 'SEARCH_GOOGLE_CX', 'SEARCH_BING_API_KEY', 'SERPAPI_KEYS',
    'SEARCH_SEARXNG_ENGINES', 'SEARCH_SEARXNG_CATEGORIES', 'SEARCH_SEARXNG_LANGUAGE', 'SEARCH_SEARXNG_TIMEOUT',
    'GDELT_BASE_URL', 'GDELT_TIMEOUT', 'GDELT_MAX_RESULTS',
    'ALPHA_VANTAGE_API_KEY', 'ALPHA_VANTAGE_BASE_URL', 'ALPHA_VANTAGE_TIMEOUT', 'ALPHA_VANTAGE_NEWS_LIMIT',
    'AI_CODE_GEN_MODEL', 'LLM_PROXY_URL', 'LLM_USE_SYSTEM_PROXY',
    'OPENAI_BASE_URL', 'DEEPSEEK_BASE_URL', 'GROK_BASE_URL', 'ATLASCLOUD_BASE_URL', 'MINIMAX_BASE_URL',
    # Trading internals
    'ORDER_MODE', 'MAKER_WAIT_SEC',
    'SPOT_CLOSE_SAFETY_RATIO', 'SPOT_OPEN_QUOTE_BUFFER',
    'FINNHUB_API_KEY', 'FINNHUB_FREE_ONLY',
    'TRADING_ECONOMICS_CLIENT', 'TRADING_ECONOMICS_KEY',
    'COINGLASS_API_KEY', 'CRYPTOQUANT_API_KEY', 'TIINGO_API_KEY',
    'TWELVE_DATA_API_KEY', 'ADANOS_API_KEY',
    # Agent gateway (operator-level)
    'AGENT_JOBS_MAX_WORKERS',
    'ENABLE_PENDING_ORDER_WORKER', 'DISABLE_RESTORE_RUNNING_STRATEGIES',
    # OAuth advanced
    'OAUTH_ALLOWED_REDIRECTS', 'OAUTH_STATE_TTL_MINUTES',
    'GOOGLE_REDIRECT_URI', 'GITHUB_REDIRECT_URI',
    # Security rate-limit / verification code tuning
    'SECURITY_IP_MAX_ATTEMPTS', 'SECURITY_IP_WINDOW_MINUTES', 'SECURITY_IP_BLOCK_MINUTES',
    'SECURITY_ACCOUNT_MAX_ATTEMPTS', 'SECURITY_ACCOUNT_WINDOW_MINUTES', 'SECURITY_ACCOUNT_BLOCK_MINUTES',
    'VERIFICATION_CODE_EXPIRE_MINUTES', 'VERIFICATION_CODE_RATE_LIMIT',
    'VERIFICATION_CODE_IP_HOURLY_LIMIT', 'VERIFICATION_CODE_MAX_ATTEMPTS',
    'VERIFICATION_CODE_LOCK_MINUTES',
    'MFA_CHALLENGE_EXPIRE_MINUTES', 'MFA_MAX_ATTEMPTS',
    # AI reflection / calibration
    'REFLECTION_WORKER_INTERVAL_SEC', 'REFLECTION_MIN_AGE_DAYS', 'REFLECTION_VALIDATE_LIMIT',
    'AI_CALIBRATION_MARKETS', 'AI_CALIBRATION_LOOKBACK_DAYS', 'AI_CALIBRATION_MIN_SAMPLES',
    # USDT pay internals
    'USDT_TRC20_CONTRACT', 'USDT_BEP20_CONTRACT', 'USDT_ERC20_CONTRACT', 'USDT_SOL_MINT',
    'TRONGRID_BASE_URL', 'ETHERSCAN_V2_BASE_URL', 'BSC_RPC_URLS', 'ETH_RPC_URLS',
    'SOLANA_RPC_URL', 'BEP20_PREFER_EXPLORER', 'ERC20_PREFER_EXPLORER',
    'USDT_PAY_CONFIRM_SECONDS', 'USDT_PAY_EXPIRE_MINUTES',
    'USDT_AMOUNT_SUFFIX_DECIMALS', 'USDT_WORKER_POLL_INTERVAL',
    # Adanos sentiment
    'ADANOS_SENTIMENT_SOURCE', 'ADANOS_API_BASE_URL',
    # Provider internals / rarely changed endpoints
    'TRADING_ECONOMICS_BASE_URL', 'TRADING_ECONOMICS_TIMEOUT',
    'FRED_BASE_URL', 'FRED_TIMEOUT', 'BLS_BASE_URL', 'BLS_TIMEOUT', 'BEA_BASE_URL', 'BEA_TIMEOUT',
    # Brand internals
    'BRAND_FAVICON_URL',
    'BRAND_LEGAL_USER_AGREEMENT_TEXT', 'BRAND_LEGAL_PRIVACY_POLICY_TEXT',
}

CONFIG_SCHEMA = {

    # Frontend reads these via /api/settings/brand-config (no auth) so logos,
    # social links, version label and legal modals can be rebranded without
    # touching the Vue source.
    'brand': {
        'title': 'Brand & Identity',
        'icon': 'crown',
        'order': 0,
        'items': [
            {
                'key': 'BRAND_APP_NAME',
                'label': 'App Name',
                'type': 'text',
                'default': 'QuantDinger',
                'description': 'Product name shown in the browser tab title and footer copyright.'
            },
            {
                'key': 'BRAND_COPYRIGHT',
                'label': 'Footer Copyright',
                'type': 'text',
                'default': '© 2025-2026 QuantDinger. All rights reserved.',
                'description': 'Plain-text copyright line shown at the bottom of every page.'
            },
            {
                'key': 'BRAND_LOGO_LIGHT_URL',
                'label': 'Logo URL (Light theme)',
                'type': 'text',
                'required': False,
                'description': 'Public URL to a wide logo for the light theme. Recommended size 240x60 px (PNG / SVG / WebP, ~4:1 aspect ratio, transparent background). Leave empty to use the bundled default (src/assets/logo.png).'
            },
            {
                'key': 'BRAND_LOGO_DARK_URL',
                'label': 'Logo URL (Dark theme)',
                'type': 'text',
                'required': False,
                'description': 'Public URL to a wide logo for the dark theme. Recommended size 240x60 px (PNG / SVG / WebP, ~4:1 aspect ratio, transparent background). Leave empty to use the bundled logo_w.png.'
            },
            {
                'key': 'BRAND_LOGO_COLLAPSED_URL',
                'label': 'Logo URL (Collapsed sidebar)',
                'type': 'text',
                'required': False,
                'description': 'Public URL to a square / mark-only logo shown when the sidebar is collapsed. Recommended size 64x64 px (PNG / SVG, 1:1 aspect ratio).'
            },
            {
                'key': 'BRAND_FAVICON_URL',
                'label': 'Favicon URL',
                'type': 'text',
                'required': False,
                'description': 'Public URL to the browser tab icon. Recommended size 32x32 px (PNG or ICO).'
            },
        ]
    },

    'contact': {
        'title': 'Contact & Support',
        'icon': 'customer-service',
        'order': 0,
        'items': [
            {
                'key': 'BRAND_CONTACT_EMAIL',
                'label': 'Support Email',
                'type': 'text',
                'default': 'support@quantdinger.com',
                'description': 'Public support email shown in the sidebar footer (mailto:).'
            },
            {
                'key': 'BRAND_CONTACT_SUPPORT_URL',
                'label': 'Support / Help URL',
                'type': 'text',
                'default': 'https://t.me/quantdinger',
                'description': 'Link target for the "Support" footer item (Telegram group, ticket portal, etc.).'
            },
            {
                'key': 'BRAND_CONTACT_LIVE_CHAT_URL',
                'label': 'Live Chat URL',
                'type': 'text',
                'default': 'https://t.me/quantdinger',
                'description': 'Link target for the "Live Chat" footer item.'
            },
            {
                'key': 'BRAND_CONTACT_FEATURE_REQUEST_URL',
                'label': 'Feature Request URL',
                'type': 'text',
                'default': 'https://github.com/brokermr810/QuantDinger/issues',
                'description': 'Where to send users who want to file an issue or feature request.'
            },
        ]
    },

    'social': {
        'title': 'Social Accounts',
        'icon': 'team',
        'order': 0,
        'items': [
            {
                'key': 'BRAND_SOCIAL_GITHUB',
                'label': 'GitHub URL',
                'type': 'text',
                'required': False,
                'description': 'Leave empty to hide this icon in the sidebar footer.'
            },
            {
                'key': 'BRAND_SOCIAL_X',
                'label': 'X (Twitter) URL',
                'type': 'text',
                'required': False,
                'description': 'Leave empty to hide this icon in the sidebar footer.'
            },
            {
                'key': 'BRAND_SOCIAL_DISCORD',
                'label': 'Discord URL',
                'type': 'text',
                'required': False,
                'description': 'Leave empty to hide this icon in the sidebar footer.'
            },
            {
                'key': 'BRAND_SOCIAL_TELEGRAM',
                'label': 'Telegram URL',
                'type': 'text',
                'required': False,
                'description': 'Leave empty to hide this icon in the sidebar footer.'
            },
            {
                'key': 'BRAND_SOCIAL_YOUTUBE',
                'label': 'YouTube URL',
                'type': 'text',
                'required': False,
                'description': 'Leave empty to hide this icon in the sidebar footer.'
            },
        ]
    },

    'legal': {
        'title': 'Legal & Mobile App',
        'icon': 'safety-certificate',
        'order': 0,
        'items': [
            {
                'key': 'BRAND_LEGAL_USER_AGREEMENT_URL',
                'label': 'User Agreement URL',
                'type': 'text',
                'required': False,
                'description': 'External Terms of Service URL. Takes priority — when set, the "User Agreement" link opens in a new tab. Leave empty to use inline text or the built-in default copy.'
            },
            {
                'key': 'BRAND_LEGAL_USER_AGREEMENT_TEXT',
                'label': 'User Agreement (inline text)',
                'type': 'text',
                'required': False,
                'description': 'Inline Terms of Service text shown in the modal. Used only when the URL above is empty.'
            },
            {
                'key': 'BRAND_LEGAL_PRIVACY_POLICY_URL',
                'label': 'Privacy Policy URL',
                'type': 'text',
                'required': False,
                'description': 'External privacy policy URL. URL takes priority over inline text.'
            },
            {
                'key': 'BRAND_LEGAL_PRIVACY_POLICY_TEXT',
                'label': 'Privacy Policy (inline text)',
                'type': 'text',
                'required': False,
                'description': 'Inline privacy policy text shown in the modal. Used only when the URL above is empty.'
            },
            {
                'key': 'MOBILE_APP_LATEST_VERSION',
                'label': 'Mobile App Latest Version',
                'type': 'text',
                'required': False,
                'description': 'Semver-like version string for the in-app upgrade prompt. Leave empty to disable the prompt.'
            },
            {
                'key': 'MOBILE_APP_DOWNLOAD_URL',
                'label': 'Mobile App Download URL',
                'type': 'text',
                'required': False,
                'description': 'APK / install page URL surfaced when the mobile app reports an old version.'
            },
        ]
    },

    'auth': {
        'title': 'Security & Authentication',
        'icon': 'lock',
        'order': 1,
        'items': [
            {
                'key': 'SECRET_KEY',
                'label': 'Secret Key',
                'type': 'password',
                'default': 'quantdinger-secret-key-change-me',
                'description': 'JWT signing secret key. MUST change in production for security'
            },
            {
                'key': 'ADMIN_USER',
                'label': 'Admin Username',
                'type': 'text',
                'default': 'quantdinger',
                'description': 'Administrator login username'
            },
            {
                'key': 'ADMIN_PASSWORD',
                'label': 'Admin Password',
                'type': 'password',
                'default': '123456',
                'description': 'Administrator login password. MUST change in production'
            },
            {
                'key': 'ADMIN_EMAIL',
                'label': 'Admin Email',
                'type': 'text',
                'default': 'admin@example.com',
                'description': 'Administrator email for password reset and notifications'
            },
        ]
    },

    'ai': {
        'title': 'AI / LLM',
        'icon': 'robot',
        'order': 2,
        'items': [
            {
                'key': 'LLM_PROVIDER',
                'label': 'LLM Provider',
                'type': 'select',
                'default': 'openrouter',
                'options': [
                    {'value': 'openrouter', 'label': 'OpenRouter (Multi-model gateway)'},
                    {'value': 'openai', 'label': 'OpenAI Direct'},
                    {'value': 'google', 'label': 'Google Gemini'},
                    {'value': 'deepseek', 'label': 'DeepSeek'},
                    {'value': 'grok', 'label': 'xAI Grok'},
                    {'value': 'atlascloud', 'label': 'AtlasCloud'},
                    {'value': 'custom', 'label': 'Custom API (OpenAI-compatible)'},
                    {'value': 'minimax', 'label': 'MiniMax'},
                    {'value': 'litellm', 'label': 'LiteLLM (100+ providers)'},
                ],
                'description': 'Select your preferred LLM provider'
            },
            {
                'key': 'AI_CODE_GEN_MODEL',
                'label': 'Code Generation Model',
                'type': 'text',
                'default': '',
                'required': False,
                'description': 'Optional model override for AI code generation. If empty, uses provider default model'
            },
            # OpenRouter
            {
                'key': 'OPENROUTER_API_KEY',
                'label': 'OpenRouter API Key',
                'type': 'password',
                'required': False,
                'link': 'https://openrouter.ai/keys',
                'link_text': 'settings.link.getApiKey',
                'description': 'OpenRouter API key. Supports 100+ models via single API',
                'group': 'openrouter'
            },
            {
                'key': 'OPENROUTER_MODEL',
                'label': 'OpenRouter Model',
                'type': 'text',
                'default': 'openai/gpt-5.4',
                'link': 'https://openrouter.ai/models',
                'link_text': 'settings.link.viewModels',
                'description': 'OpenRouter model ID in provider/model format, e.g. openai/gpt-5.4, anthropic/claude-sonnet-4.5',
                'group': 'openrouter'
            },
            # OpenAI Direct
            {
                'key': 'OPENAI_API_KEY',
                'label': 'OpenAI API Key',
                'type': 'password',
                'required': False,
                'link': 'https://platform.openai.com/api-keys',
                'link_text': 'settings.link.getApiKey',
                'description': 'OpenAI official API key',
                'group': 'openai'
            },
            {
                'key': 'OPENAI_MODEL',
                'label': 'OpenAI Model',
                'type': 'text',
                'default': 'gpt-5.4',
                'link': 'https://platform.openai.com/docs/models',
                'link_text': 'settings.link.viewModels',
                'description': 'OpenAI direct model name without provider prefix, e.g. gpt-5.4, gpt-4o-mini',
                'group': 'openai'
            },
            {
                'key': 'OPENAI_BASE_URL',
                'label': 'OpenAI Base URL',
                'type': 'text',
                'default': 'https://api.openai.com/v1',
                'description': 'Custom API endpoint (for proxies or Azure)',
                'group': 'openai'
            },
            # Google Gemini
            {
                'key': 'GOOGLE_API_KEY',
                'label': 'Google API Key',
                'type': 'password',
                'required': False,
                'link': 'https://aistudio.google.com/apikey',
                'link_text': 'settings.link.getApiKey',
                'description': 'Google AI Studio API key for Gemini',
                'group': 'google'
            },
            {
                'key': 'GOOGLE_MODEL',
                'label': 'Gemini Model',
                'type': 'text',
                'default': 'gemini-1.5-flash',
                'link': 'https://ai.google.dev/gemini-api/docs/models',
                'link_text': 'settings.link.viewModels',
                'description': 'Model: gemini-1.5-flash, gemini-1.5-pro, gemini-2.0-flash-exp',
                'group': 'google'
            },
            # DeepSeek
            {
                'key': 'DEEPSEEK_API_KEY',
                'label': 'DeepSeek API Key',
                'type': 'password',
                'required': False,
                'link': 'https://platform.deepseek.com/api_keys',
                'link_text': 'settings.link.getApiKey',
                'description': 'DeepSeek API key',
                'group': 'deepseek'
            },
            {
                'key': 'DEEPSEEK_MODEL',
                'label': 'DeepSeek Model',
                'type': 'text',
                'default': 'deepseek-chat',
                'link': 'https://api-docs.deepseek.com/quick_start/pricing',
                'link_text': 'settings.link.viewModels',
                'description': 'Model: deepseek-chat, deepseek-coder',
                'group': 'deepseek'
            },
            {
                'key': 'DEEPSEEK_BASE_URL',
                'label': 'DeepSeek Base URL',
                'type': 'text',
                'default': 'https://api.deepseek.com/v1',
                'description': 'DeepSeek API endpoint',
                'group': 'deepseek'
            },
            # xAI Grok
            {
                'key': 'GROK_API_KEY',
                'label': 'Grok API Key',
                'type': 'password',
                'required': False,
                'link': 'https://console.x.ai/',
                'link_text': 'settings.link.getApiKey',
                'description': 'xAI Grok API key',
                'group': 'grok'
            },
            {
                'key': 'GROK_MODEL',
                'label': 'Grok Model',
                'type': 'text',
                'default': 'grok-beta',
                'link': 'https://docs.x.ai/docs/models',
                'link_text': 'settings.link.viewModels',
                'description': 'Model: grok-beta, grok-2',
                'group': 'grok'
            },
            {
                'key': 'GROK_BASE_URL',
                'label': 'Grok Base URL',
                'type': 'text',
                'default': 'https://api.x.ai/v1',
                'description': 'xAI Grok API endpoint',
                'group': 'grok'
            },
            # AtlasCloud
            {
                'key': 'ATLASCLOUD_API_KEY',
                'label': 'AtlasCloud API Key',
                'type': 'password',
                'required': False,
                'link': 'https://www.atlascloud.ai/docs/api-keys',
                'link_text': 'settings.link.getApiKey',
                'description': 'AtlasCloud API key. Uses the official OpenAI-compatible LLM endpoint.',
                'group': 'atlascloud'
            },
            {
                'key': 'ATLASCLOUD_MODEL',
                'label': 'AtlasCloud Model',
                'type': 'text',
                'default': 'openai/gpt-5.4',
                'link': 'https://www.atlascloud.ai/docs/models/llm',
                'link_text': 'settings.link.viewModels',
                'description': 'AtlasCloud model ID. Use the exact ID listed by AtlasCloud, e.g. openai/gpt-5.4 or deepseek-v3.',
                'group': 'atlascloud'
            },
            {
                'key': 'ATLASCLOUD_BASE_URL',
                'label': 'AtlasCloud Base URL',
                'type': 'text',
                'default': 'https://api.atlascloud.ai/v1',
                'description': 'AtlasCloud OpenAI-compatible API endpoint. Must include /v1.',
                'group': 'atlascloud'
            },
            # Custom API (OpenAI-compatible)
            {
                'key': 'CUSTOM_API_URL',
                'label': 'Custom API URL',
                'type': 'text',
                'default': '',
                'description': 'Your custom API endpoint (OpenAI-compatible, e.g. https://api.example.com/v1)',
                'group': 'custom'
            },
            {
                'key': 'CUSTOM_API_KEY',
                'label': 'Custom API Key',
                'type': 'password',
                'required': False,
                'description': 'API key for your custom endpoint. Leave empty for local OpenAI-compatible servers without auth (e.g. Ollama on localhost)',
                'group': 'custom'
            },
            {
                'key': 'CUSTOM_MODEL',
                'label': 'Custom Model',
                'type': 'text',
                'default': '',
                'description': 'Model name to use (e.g. gpt-4o, claude-3-opus)',
                'group': 'custom'
            },
            # MiniMax
            {
                'key': 'MINIMAX_API_KEY',
                'label': 'MiniMax API Key',
                'type': 'password',
                'required': False,
                'link': 'https://platform.minimax.io',
                'link_text': 'settings.link.getApiKey',
                'description': 'MiniMax API key',
                'group': 'minimax'
            },
            {
                'key': 'MINIMAX_MODEL',
                'label': 'MiniMax Model',
                'type': 'text',
                'default': 'MiniMax-M2.7',
                'link': 'https://platform.minimax.io/docs',
                'link_text': 'settings.link.viewModels',
                'description': 'Model: MiniMax-M2.7, MiniMax-M2.7-highspeed',
                'group': 'minimax'
            },
            {
                'key': 'MINIMAX_BASE_URL',
                'label': 'MiniMax Base URL',
                'type': 'text',
                'default': 'https://api.minimax.io/v1',
                'description': 'MiniMax API endpoint',
                'group': 'minimax'
            },
            # LiteLLM
            {
                'key': 'LITELLM_API_KEY',
                'label': 'LiteLLM API Key',
                'type': 'password',
                'required': False,
                'link': 'https://docs.litellm.ai/docs/providers',
                'link_text': 'settings.link.viewProviders',
                'description': 'Optional. LiteLLM reads provider-specific env vars (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.) automatically',
                'group': 'litellm'
            },
            {
                'key': 'LITELLM_MODEL',
                'label': 'LiteLLM Model',
                'type': 'text',
                'default': 'openai/gpt-5.4',
                'link': 'https://docs.litellm.ai/docs/providers',
                'link_text': 'settings.link.viewProviders',
                'description': 'LiteLLM model ID, usually provider/model format, e.g. openai/gpt-5.4, anthropic/claude-sonnet-4-20250514, gemini/gemini-2.5-flash',
                'group': 'litellm'
            },
            {
                'key': 'LITELLM_BASE_URL',
                'label': 'LiteLLM Base URL',
                'type': 'text',
                'default': '',
                'description': 'Optional. Override provider base URL (e.g. Azure endpoint)',
                'group': 'litellm'
            },
            # Common settings
            {
                'key': 'OPENROUTER_TEMPERATURE',
                'label': 'Temperature',
                'type': 'number',
                'default': '0.7',
                'description': 'Model creativity (0-1). Lower = more deterministic'
            },
            {
                'key': 'AI_ANALYSIS_CONSENSUS_TIMEFRAMES',
                'label': 'Consensus Timeframes',
                'type': 'text',
                'default': '1D,4H',
                'required': False,
                'description': 'Multi-timeframe consensus for fast AI analysis. Comma-separated, e.g. "1D,4H"'
            },
            {
                'key': 'LLM_PROXY_URL',
                'label': 'LLM Proxy URL',
                'type': 'text',
                'default': '',
                'required': False,
                'description': 'Optional dedicated proxy for LLM provider requests. Leave empty for direct LLM access; PROXY_URL is reserved for market data and exchange APIs.'
            },
            {
                'key': 'LLM_USE_SYSTEM_PROXY',
                'label': 'Use System Proxy for LLM',
                'type': 'boolean',
                'default': 'False',
                'description': 'When enabled, LLM requests inherit HTTP_PROXY / HTTPS_PROXY / ALL_PROXY. Keep disabled unless the configured system proxy is reachable from this backend.'
            },
        ]
    },

    'trading': {
        'title': 'Live Trading',
        'icon': 'stock',
        'order': 3,
        'items': [
            {
                'key': 'ORDER_MODE',
                'label': 'Order Execution Mode',
                'type': 'select',
                'options': ['market', 'maker'],
                'default': 'market',
                'description': 'market: Market order (instant fill, recommended), maker: Limit order first (lower fees but may not fill)'
            },
            {
                'key': 'MAKER_WAIT_SEC',
                'label': 'Limit Order Wait (sec)',
                'type': 'number',
                'default': '10',
                'description': 'Wait time for limit order fill before switching to market order'
            },
            {
                'key': 'SPOT_CLOSE_SAFETY_RATIO',
                'label': 'Spot Close Safety Ratio',
                'type': 'number',
                'default': '0.998',
                'description': 'When closing spot long, sell qty is capped to (exchange free base × this ratio), then floored to lot step. Lower if full close fails due to fees (valid range 0.9–1.0).'
            },
            {
                'key': 'SPOT_OPEN_QUOTE_BUFFER',
                'label': 'Spot Open Quote Buffer',
                'type': 'number',
                'default': '0.995',
                'description': 'Fraction of USDT/notional used on spot open (reserve headroom for buy fees). Example 0.995 uses 99.5% of allocated quote (valid range 0.9–1.0).'
            },
            {
                'key': 'ALLOW_LOCAL_DESKTOP_BROKERS',
                'label': 'Allow IBKR / MT5 (local desktop brokers)',
                'type': 'boolean',
                'default': 'True',
                'description': 'Disable on a multi-tenant SaaS deployment so users see a clear "broker not supported" message instead of broken connect flows. Crypto exchange API keys are unaffected.'
            },
            {
                'key': 'ENABLE_PENDING_ORDER_WORKER',
                'label': 'Enable Pending Order Worker',
                'type': 'boolean',
                'default': 'True',
                'description': 'Background worker that syncs broker positions, manages pending limit orders, and triggers strategy auto-stop on fatal errors. Disable only when running multiple API replicas where another node already runs the worker.'
            },
            {
                'key': 'DISABLE_RESTORE_RUNNING_STRATEGIES',
                'label': 'Disable Auto-Restore Strategies on Boot',
                'type': 'boolean',
                'default': 'False',
                'description': 'When False, strategies running before a server restart are automatically resumed. Set to True only if you want to inspect state on next boot before any strategy resumes trading.'
            },
        ]
    },

    'data_source': {
        'title': 'Data Sources',
        'icon': 'database',
        'order': 4,
        'items': [
            {
                'key': 'ENABLED_MARKETS',
                'label': 'Enabled Markets',
                'type': 'market_multiselect',
                'default': '',
                'options': market_options(),
                'description': 'Markets exposed to research, strategy, market data, Agent API, and live-trading entry points. Saved as ENABLED_MARKETS in .env for backward compatibility. Empty = whitelist disabled and legacy SHOW_* flags apply.'
            },
            {
                'key': 'CCXT_DEFAULT_EXCHANGE',
                'label': 'Default Crypto Exchange',
                'type': 'text',
                'default': 'binance',
                'link': 'https://github.com/ccxt/ccxt#supported-cryptocurrency-exchange-markets',
                'link_text': 'settings.link.supportedExchanges',
                'description': 'Default exchange for crypto market data (binance recommended for BTC/USDT; coinbase uses USD pairs)'
            },
            {
                'key': 'FINNHUB_API_KEY',
                'label': 'Finnhub API Key',
                'type': 'password',
                'required': False,
                'link': 'https://finnhub.io/register',
                'link_text': 'settings.link.freeRegister',
                'description': 'Optional Finnhub API key for US stock quotes, company profile and news. Paid-only endpoints such as Economic Calendar are skipped by default.'
            },
            {
                'key': 'FINNHUB_FREE_ONLY',
                'label': 'Finnhub Free-only Mode',
                'type': 'boolean',
                'default': 'True',
                'description': 'Keep enabled for free Finnhub plans. When enabled, paid-only Finnhub endpoints such as Economic Calendar and Social Sentiment are not called.'
            },
            {
                'key': 'TRADING_ECONOMICS_CLIENT',
                'label': 'Trading Economics Client',
                'type': 'text',
                'default': '',
                'required': False,
                'link': 'https://docs.tradingeconomics.com/',
                'link_text': 'settings.link.viewDocs',
                'description': 'Optional official international economic calendar provider. Leave empty to use the free AkShare/WallstreetCN fallback; enter your TE client name if you have a Trading Economics API key.'
            },
            {
                'key': 'TRADING_ECONOMICS_KEY',
                'label': 'Trading Economics Key',
                'type': 'password',
                'default': '',
                'required': False,
                'link': 'https://docs.tradingeconomics.com/',
                'link_text': 'settings.link.viewDocs',
                'description': 'Optional Trading Economics API key. The legacy guest account is discontinued; leave blank unless you have credentials.'
            },
            {
                'key': 'TRADING_ECONOMICS_BASE_URL',
                'label': 'Trading Economics Base URL',
                'type': 'text',
                'default': 'https://api.tradingeconomics.com',
                'description': 'Trading Economics API endpoint. Change only if you use a proxy or mirror.'
            },
            {
                'key': 'TRADING_ECONOMICS_TIMEOUT',
                'label': 'Trading Economics Timeout (sec)',
                'type': 'number',
                'default': '10',
                'description': 'Timeout for economic calendar requests.'
            },
            {
                'key': 'FRED_API_KEY',
                'label': 'FRED API Key',
                'type': 'password',
                'default': '',
                'required': False,
                'link': 'https://fred.stlouisfed.org/docs/api/api_key.html',
                'link_text': 'settings.link.getApiKey',
                'description': 'FRED key for stable US macro time series such as rates, CPI, unemployment, payrolls and financial conditions.'
            },
            {
                'key': 'FRED_BASE_URL',
                'label': 'FRED Base URL',
                'type': 'text',
                'default': 'https://api.stlouisfed.org/fred',
                'link': 'https://fred.stlouisfed.org/docs/api/fred/',
                'link_text': 'settings.link.viewDocs',
                'description': 'FRED API endpoint. Change only when using a proxy.'
            },
            {
                'key': 'FRED_TIMEOUT',
                'label': 'FRED Timeout (sec)',
                'type': 'number',
                'default': '10',
                'description': 'Timeout for FRED macro time-series requests.'
            },
            {
                'key': 'BLS_API_KEY',
                'label': 'BLS API Key',
                'type': 'password',
                'default': '',
                'required': False,
                'link': 'https://www.bls.gov/developers/',
                'link_text': 'settings.link.getApiKey',
                'description': 'Optional BLS registration key for higher official CPI, jobs, wages and labor data limits.'
            },
            {
                'key': 'BLS_BASE_URL',
                'label': 'BLS Base URL',
                'type': 'text',
                'default': 'https://api.bls.gov/publicAPI/v2',
                'link': 'https://www.bls.gov/developers/',
                'link_text': 'settings.link.viewDocs',
                'description': 'BLS public API endpoint. A key is optional but recommended.'
            },
            {
                'key': 'BLS_TIMEOUT',
                'label': 'BLS Timeout (sec)',
                'type': 'number',
                'default': '10',
                'description': 'Timeout for BLS official macro series requests.'
            },
            {
                'key': 'BEA_API_KEY',
                'label': 'BEA API Key',
                'type': 'password',
                'default': '',
                'required': False,
                'link': 'https://apps.bea.gov/API/signup/',
                'link_text': 'settings.link.getApiKey',
                'description': 'BEA key for official GDP, income, consumption and national accounts data.'
            },
            {
                'key': 'BEA_BASE_URL',
                'label': 'BEA Base URL',
                'type': 'text',
                'default': 'https://apps.bea.gov/api/data',
                'link': 'https://apps.bea.gov/api/_pdf/bea_web_service_api_user_guide.pdf',
                'link_text': 'settings.link.viewDocs',
                'description': 'BEA API endpoint. Change only when using a proxy.'
            },
            {
                'key': 'BEA_TIMEOUT',
                'label': 'BEA Timeout (sec)',
                'type': 'number',
                'default': '10',
                'description': 'Timeout for BEA official macro data requests.'
            },
            {
                'key': 'COINGLASS_API_KEY',
                'label': 'Coinglass API Key',
                'type': 'password',
                'required': False,
                'link': 'https://docs.coinglass.com/reference/getting-started-with-your-api',
                'link_text': 'settings.link.getApiKey',
                'description': 'Coinglass API key for crypto derivatives, funding rate, long/short ratio, and exchange flow data. Open the official docs to view signup and key management instructions.'
            },
            {
                'key': 'CRYPTOQUANT_API_KEY',
                'label': 'CryptoQuant API Key',
                'type': 'password',
                'required': False,
                'link': 'https://cryptoquant.com/docs',
                'link_text': 'settings.link.getApiKey',
                'description': 'CryptoQuant API key for on-chain and stablecoin flow metrics used in crypto AI analysis. API access is tied to paid plans; see the official docs for activation details.'
            },
            {
                'key': 'TIINGO_API_KEY',
                'label': 'Tiingo API Key',
                'type': 'password',
                'required': False,
                'link': 'https://www.tiingo.com/account/api/token',
                'link_text': 'settings.link.getToken',
                'description': 'Tiingo API key for Forex/Metals data'
            },
            {
                'key': 'TWELVE_DATA_API_KEY',
                'label': 'Twelve Data API Key',
                'type': 'password',
                'required': False,
                'link': 'https://twelvedata.com/apikey',
                'link_text': 'settings.link.getApiKey',
                'description': 'Twelve Data API key for CN/HK stock K-lines (free 800 credits/day)'
            },
            {
                'key': 'ADANOS_API_KEY',
                'label': 'Adanos API Key',
                'type': 'password',
                'required': False,
                'link': 'https://adanos.org',
                'link_text': 'settings.link.getApiKey',
                'description': 'Adanos market sentiment API key. Leave empty to disable the US-stock sentiment widget.'
            },
            {
                'key': 'ADANOS_SENTIMENT_SOURCE',
                'label': 'Adanos Sentiment Source',
                'type': 'text',
                'default': 'reddit',
                'description': 'Sentiment source channel (reddit, etc.). See Adanos docs for valid values.'
            },
            {
                'key': 'ADANOS_API_BASE_URL',
                'label': 'Adanos API Base URL',
                'type': 'text',
                'default': 'https://api.adanos.org',
                'description': 'Adanos API endpoint. Change only if you have a self-hosted or mirrored instance.'
            },
        ]
    },

    'search': {
        'title': 'Search & News Sources',
        'icon': 'search',
        'order': 4.5,
        'items': [
            {
                'key': 'SEARCH_PROVIDER',
                'label': 'Search Provider',
                'type': 'select',
                'options': ['tavily', 'searxng', 'gdelt', 'serpapi', 'google', 'bing', 'duckduckgo', 'none'],
                'default': 'tavily',
                'description': 'Primary news/web search provider used by AI analysis. QuantDinger falls back to configured providers, then GDELT and DuckDuckGo when available'
            },
            {
                'key': 'SEARCH_MAX_RESULTS',
                'label': 'Search Max Results',
                'type': 'number',
                'default': '10',
                'description': 'Maximum number of search/news results returned per AI analysis request'
            },
            {
                'key': 'TAVILY_API_KEYS',
                'label': 'Tavily API Keys',
                'type': 'password',
                'required': False,
                'link': 'https://tavily.com/',
                'link_text': 'settings.link.getApiKey',
                'description': 'Tavily search API keys (comma-separated). Recommended lightweight search source for AI analysis'
            },
            {
                'key': 'SEARCH_SEARXNG_BASE_URL',
                'label': 'SearXNG Base URL',
                'type': 'text',
                'required': False,
                'default': '',
                'link': 'https://docs.searxng.org/',
                'link_text': 'settings.link.viewDocs',
                'description': 'Base URL of a trusted or self-hosted SearXNG instance, for example https://search.example.com'
            },
            {
                'key': 'GDELT_BASE_URL',
                'label': 'GDELT DOC Base URL',
                'type': 'text',
                'default': 'https://api.gdeltproject.org/api/v2/doc/doc',
                'link': 'https://api.gdeltproject.org/api/v2/doc/doc',
                'link_text': 'settings.link.viewDocs',
                'description': 'Free global news fallback endpoint. GDELT requires no API key and is used when paid search sources are unavailable.'
            },
            {
                'key': 'SERPAPI_KEYS',
                'label': 'SerpAPI Keys',
                'type': 'password',
                'required': False,
                'link': 'https://serpapi.com/',
                'link_text': 'settings.link.getApiKey',
                'description': 'SerpAPI keys (comma-separated)'
            },
            {
                'key': 'SEARCH_GOOGLE_API_KEY',
                'label': 'Google Search API Key',
                'type': 'password',
                'required': False,
                'link': 'https://console.cloud.google.com/apis/credentials',
                'link_text': 'settings.link.getApiKey',
                'description': 'Google Custom Search JSON API key'
            },
            {
                'key': 'SEARCH_GOOGLE_CX',
                'label': 'Google Search Engine ID (CX)',
                'type': 'text',
                'required': False,
                'link': 'https://programmablesearchengine.google.com/',
                'link_text': 'settings.link.getApiKey',
                'description': 'Google Programmable Search Engine ID'
            },
            {
                'key': 'SEARCH_BING_API_KEY',
                'label': 'Bing Search API Key',
                'type': 'password',
                'required': False,
                'link': 'https://portal.azure.com/',
                'link_text': 'settings.link.getApiKey',
                'description': 'Microsoft Bing Web Search API key'
            },
            {
                'key': 'SEARCH_SEARXNG_ENGINES',
                'label': 'SearXNG Engines',
                'type': 'text',
                'required': False,
                'default': '',
                'description': 'Optional comma-separated SearXNG engines. Leave empty to use the instance defaults.'
            },
            {
                'key': 'SEARCH_SEARXNG_CATEGORIES',
                'label': 'SearXNG Categories',
                'type': 'text',
                'required': False,
                'default': 'general',
                'description': 'Comma-separated SearXNG categories used for AI research search.'
            },
            {
                'key': 'SEARCH_SEARXNG_LANGUAGE',
                'label': 'SearXNG Language',
                'type': 'text',
                'required': False,
                'default': 'auto',
                'description': 'SearXNG language code. Use auto to keep the instance default.'
            },
            {
                'key': 'SEARCH_SEARXNG_TIMEOUT',
                'label': 'SearXNG Timeout (sec)',
                'type': 'number',
                'default': '12',
                'description': 'Timeout for SearXNG search requests.'
            },
            {
                'key': 'GDELT_TIMEOUT',
                'label': 'GDELT Timeout (sec)',
                'type': 'number',
                'default': '12',
                'description': 'Timeout for GDELT DOC 2.0 global news queries.'
            },
            {
                'key': 'GDELT_MAX_RESULTS',
                'label': 'GDELT Max Results',
                'type': 'number',
                'default': '10',
                'description': 'Default maximum GDELT article count used by fallback news/event search.'
            },
            {
                'key': 'ALPHA_VANTAGE_API_KEY',
                'label': 'Alpha Vantage API Key',
                'type': 'password',
                'required': False,
                'link': 'https://www.alphavantage.co/support/#api-key',
                'link_text': 'settings.link.getApiKey',
                'description': 'Optional low-cost company news and sentiment source using Alpha Vantage NEWS_SENTIMENT.'
            },
            {
                'key': 'ALPHA_VANTAGE_BASE_URL',
                'label': 'Alpha Vantage Base URL',
                'type': 'text',
                'default': 'https://www.alphavantage.co/query',
                'link': 'https://www.alphavantage.co/documentation/',
                'link_text': 'settings.link.viewDocs',
                'description': 'Alpha Vantage API endpoint for NEWS_SENTIMENT and related market-data functions.'
            },
            {
                'key': 'ALPHA_VANTAGE_TIMEOUT',
                'label': 'Alpha Vantage Timeout (sec)',
                'type': 'number',
                'default': '12',
                'description': 'Timeout for Alpha Vantage company news and sentiment requests.'
            },
            {
                'key': 'ALPHA_VANTAGE_NEWS_LIMIT',
                'label': 'Alpha Vantage News Limit',
                'type': 'number',
                'default': '20',
                'description': 'Maximum NEWS_SENTIMENT feed items requested per company-news lookup.'
            },
        ]
    },

    'email': {
        'title': 'Email (SMTP)',
        'icon': 'mail',
        'order': 5,
        'items': [
            {
                'key': 'SMTP_HOST',
                'label': 'SMTP Server',
                'type': 'text',
                'required': False,
                'description': 'SMTP server hostname (e.g. smtp.gmail.com)'
            },
            {
                'key': 'SMTP_PORT',
                'label': 'SMTP Port',
                'type': 'number',
                'default': '587',
                'description': 'SMTP port (587 for TLS, 465 for SSL)'
            },
            {
                'key': 'SMTP_USER',
                'label': 'SMTP Username',
                'type': 'text',
                'required': False,
                'description': 'SMTP authentication username (usually email address)'
            },
            {
                'key': 'SMTP_PASSWORD',
                'label': 'SMTP Password',
                'type': 'password',
                'required': False,
                'description': 'SMTP authentication password or app-specific password'
            },
            {
                'key': 'SMTP_FROM',
                'label': 'Sender Address',
                'type': 'text',
                'required': False,
                'description': 'Email sender address (From header)'
            },
            {
                'key': 'SMTP_USE_TLS',
                'label': 'Use TLS',
                'type': 'boolean',
                'default': 'True',
                'description': 'Enable STARTTLS encryption (recommended for port 587)'
            },
            {
                'key': 'SMTP_USE_SSL',
                'label': 'Use SSL',
                'type': 'boolean',
                'default': 'False',
                'description': 'Enable SSL encryption (for port 465)'
            },
        ]
    },

    'sms': {
        'title': 'SMS (Twilio)',
        'icon': 'phone',
        'order': 6,
        'items': [
            {
                'key': 'TWILIO_ACCOUNT_SID',
                'label': 'Account SID',
                'type': 'password',
                'required': False,
                'link': 'https://console.twilio.com/',
                'link_text': 'settings.link.getApi',
                'description': 'Twilio Account SID from console dashboard'
            },
            {
                'key': 'TWILIO_AUTH_TOKEN',
                'label': 'Auth Token',
                'type': 'password',
                'required': False,
                'description': 'Twilio Auth Token from console dashboard'
            },
            {
                'key': 'TWILIO_FROM_NUMBER',
                'label': 'Sender Number',
                'type': 'text',
                'required': False,
                'description': 'Twilio phone number for sending SMS (e.g. +1234567890)'
            },
        ]
    },

    # ==================== 7. AI Agent ====================
    'agent': {
        'title': 'AI Agent',
        'icon': 'experiment',
        'order': 7,
        'items': [
            # Agent Gateway (/api/agent/v1) deployment knobs
            {
                'key': 'AGENT_LIVE_TRADING_ENABLED',
                'label': 'Agent Live Trading',
                'type': 'boolean',
                'default': 'False',
                'description': 'Hard kill switch for live trading from agent tokens. When False, T-class agent calls always record paper orders even if the token allows live mode.'
            },
            {
                'key': 'QUANTDINGER_DEPLOYMENT_MODE',
                'label': 'Deployment Mode',
                'type': 'select',
                'options': [
                    {'value': '', 'label': 'Single-tenant / self-hosted'},
                    {'value': 'saas', 'label': 'SaaS / hosted (multi-tenant)'},
                    {'value': 'hosted', 'label': 'Hosted (alias of saas)'},
                ],
                'default': '',
                'description': 'Set to "saas" on multi-tenant hosted instances. Users self-manage Agent Tokens in Profile; T scope is allowed with in-app risk disclosure. Live trading still requires AGENT_LIVE_TRADING_ENABLED.'
            },
            {
                'key': 'AGENT_JOBS_MAX_WORKERS',
                'label': 'Agent Jobs Max Workers',
                'type': 'number',
                'default': '4',
                'description': 'Thread pool size for async agent jobs (backtests, experiment pipelines).'
            },
            {
                'key': 'ENABLE_REFLECTION_WORKER',
                'label': 'Enable Auto Reflection',
                'type': 'boolean',
                'default': 'False',
                'description': 'Enable background worker for automatic trade reflection and calibration'
            },
            {
                'key': 'REFLECTION_WORKER_INTERVAL_SEC',
                'label': 'Reflection Interval (sec)',
                'type': 'number',
                'default': '86400',
                'description': 'Reflection worker run interval in seconds (86400 = 1 day)'
            },
            {
                'key': 'REFLECTION_MIN_AGE_DAYS',
                'label': 'Min Age for Validation (days)',
                'type': 'number',
                'default': '7',
                'description': 'Only validate analyses older than N days'
            },
            {
                'key': 'REFLECTION_VALIDATE_LIMIT',
                'label': 'Validation Batch Limit',
                'type': 'number',
                'default': '200',
                'description': 'Max records to validate per reflection cycle'
            },
            {
                'key': 'ENABLE_CONFIDENCE_CALIBRATION',
                'label': 'Enable Confidence Calibration',
                'type': 'boolean',
                'default': 'False',
                'description': 'Adjust confidence by historical accuracy in each bucket'
            },
            {
                'key': 'ENABLE_AI_ENSEMBLE',
                'label': 'Enable Multi-Model Voting',
                'type': 'boolean',
                'default': 'False',
                'description': 'Use 2-3 models and majority vote for more stable decisions'
            },
            {
                'key': 'AI_ENSEMBLE_MODELS',
                'label': 'Ensemble Models',
                'type': 'text',
                'default': 'openai/gpt-5.4,openai/gpt-4o-mini',
                'description': 'Comma-separated model IDs for ensemble voting'
            },
            {
                'key': 'AI_CALIBRATION_MARKETS',
                'label': 'Calibration Markets',
                'type': 'text',
                'default': 'Crypto',
                'description': 'Comma-separated markets to run threshold calibration'
            },
            {
                'key': 'AI_CALIBRATION_LOOKBACK_DAYS',
                'label': 'Calibration Lookback (days)',
                'type': 'number',
                'default': '30',
                'description': 'Days of validated data for calibration'
            },
            {
                'key': 'AI_CALIBRATION_MIN_SAMPLES',
                'label': 'Calibration Min Samples',
                'type': 'number',
                'default': '80',
                'description': 'Minimum validated samples required for calibration'
            },
        ]
    },

    'network': {
        'title': 'Network & Proxy',
        'icon': 'global',
        'order': 8,
        'items': [
            {
                'key': 'PROXY_URL',
                'label': 'Proxy URL',
                'type': 'text',
                'required': False,
                'description': 'Proxy URL for market data, exchange and broker APIs. LLM provider calls are direct by default; use LLM Proxy URL only when an LLM provider must go through a proxy.'
            },
        ]
    },

    'security': {
        'title': 'Registration & OAuth',
        'icon': 'safety',
        'order': 10,
        'items': [
            {
                'key': 'ENABLE_REGISTRATION',
                'label': 'Enable Registration',
                'type': 'boolean',
                'default': 'True',
                'description': 'Allow new users to register accounts'
            },
            {
                'key': 'MFA_ENABLED',
                'label': 'Enable Authenticator App MFA',
                'type': 'boolean',
                'default': 'False',
                'description': 'Allow users to opt in to authenticator-app two-step verification. This does not force all users to bind MFA.'
            },
            {
                'key': 'MFA_RISK_LOGIN_ONLY',
                'label': 'MFA Only for Risky Login',
                'type': 'boolean',
                'default': 'True',
                'description': 'When enabled, users who bound MFA are challenged only on new-location or new-device logins. Turn off to challenge every password login for MFA-enabled users.'
            },
            {
                'key': 'FRONTEND_URL',
                'label': 'Frontend URL',
                'type': 'text',
                'default': 'http://localhost:8080',
                'description': 'Frontend URL for OAuth redirects'
            },
            {
                'key': 'OAUTH_ALLOWED_REDIRECTS',
                'label': 'Extra OAuth Redirect Targets',
                'type': 'text',
                'required': False,
                'description': 'Comma-separated scheme+host (+ optional port) of additional frontends allowed as OAuth post-login redirect targets, e.g. https://m.quantdinger.com,https://app.quantdinger.com. FRONTEND_URL is always allowed implicitly.'
            },
            {
                'key': 'OAUTH_STATE_TTL_MINUTES',
                'label': 'OAuth State TTL (min)',
                'type': 'number',
                'default': '20',
                'description': 'OAuth CSRF state token lifetime in minutes. Clamped to [5,120].'
            },
            {
                'key': 'TURNSTILE_SITE_KEY',
                'label': 'Turnstile Site Key',
                'type': 'text',
                'required': False,
                'link': 'https://dash.cloudflare.com/?to=/:account/turnstile',
                'link_text': 'settings.link.getTurnstileKey',
                'description': 'Cloudflare Turnstile site key for CAPTCHA'
            },
            {
                'key': 'TURNSTILE_SECRET_KEY',
                'label': 'Turnstile Secret Key',
                'type': 'password',
                'required': False,
                'description': 'Cloudflare Turnstile secret key'
            },
            {
                'key': 'GOOGLE_CLIENT_ID',
                'label': 'Google OAuth Client ID',
                'type': 'text',
                'required': False,
                'link': 'https://console.cloud.google.com/apis/credentials',
                'link_text': 'settings.link.getGoogleCredentials',
                'description': 'Google OAuth Client ID for Google login'
            },
            {
                'key': 'GOOGLE_CLIENT_SECRET',
                'label': 'Google OAuth Secret',
                'type': 'password',
                'required': False,
                'description': 'Google OAuth Client Secret'
            },
            {
                'key': 'GOOGLE_REDIRECT_URI',
                'label': 'Google OAuth Redirect URI',
                'type': 'text',
                'required': False,
                'description': 'Must match the redirect URI registered in your Google Cloud Console. Typically <api-host>/api/auth/oauth/google/callback.'
            },
            {
                'key': 'GITHUB_CLIENT_ID',
                'label': 'GitHub OAuth Client ID',
                'type': 'text',
                'required': False,
                'link': 'https://github.com/settings/developers',
                'link_text': 'settings.link.getGithubCredentials',
                'description': 'GitHub OAuth Client ID for GitHub login'
            },
            {
                'key': 'GITHUB_CLIENT_SECRET',
                'label': 'GitHub OAuth Secret',
                'type': 'password',
                'required': False,
                'description': 'GitHub OAuth Client Secret'
            },
            {
                'key': 'GITHUB_REDIRECT_URI',
                'label': 'GitHub OAuth Redirect URI',
                'type': 'text',
                'required': False,
                'description': 'Must match the callback URL configured for your GitHub OAuth app. Typically <api-host>/api/auth/oauth/github/callback.'
            },

            # ===== Login / verification-code rate limiting (advanced) =====
            {
                'key': 'SECURITY_IP_MAX_ATTEMPTS',
                'label': 'IP Lockout: Max Attempts',
                'type': 'number',
                'default': '10',
                'description': 'How many failed login attempts from one IP before blocking. Advanced — tune only if you face credential-stuffing.'
            },
            {
                'key': 'SECURITY_IP_WINDOW_MINUTES',
                'label': 'IP Lockout: Window (min)',
                'type': 'number',
                'default': '5',
                'description': 'Time window used to count failed attempts from an IP.'
            },
            {
                'key': 'SECURITY_IP_BLOCK_MINUTES',
                'label': 'IP Lockout: Block (min)',
                'type': 'number',
                'default': '15',
                'description': 'How long to block an IP after the threshold is hit.'
            },
            {
                'key': 'SECURITY_ACCOUNT_MAX_ATTEMPTS',
                'label': 'Account Lockout: Max Attempts',
                'type': 'number',
                'default': '5',
                'description': 'How many failed logins for a single account before locking it.'
            },
            {
                'key': 'SECURITY_ACCOUNT_WINDOW_MINUTES',
                'label': 'Account Lockout: Window (min)',
                'type': 'number',
                'default': '60',
                'description': 'Time window for counting failed logins per account.'
            },
            {
                'key': 'SECURITY_ACCOUNT_BLOCK_MINUTES',
                'label': 'Account Lockout: Block (min)',
                'type': 'number',
                'default': '30',
                'description': 'How long an account stays locked after exceeding attempts.'
            },
            {
                'key': 'VERIFICATION_CODE_EXPIRE_MINUTES',
                'label': 'Verification Code Expiry (min)',
                'type': 'number',
                'default': '10',
                'description': 'How long an email / SMS verification code is valid.'
            },
            {
                'key': 'VERIFICATION_CODE_RATE_LIMIT',
                'label': 'Verification Code Rate Limit (sec)',
                'type': 'number',
                'default': '60',
                'description': 'Minimum seconds between two verification-code requests for the same target.'
            },
            {
                'key': 'VERIFICATION_CODE_IP_HOURLY_LIMIT',
                'label': 'Verification Code IP Hourly Limit',
                'type': 'number',
                'default': '10',
                'description': 'Maximum verification codes one IP may request per hour.'
            },
            {
                'key': 'VERIFICATION_CODE_MAX_ATTEMPTS',
                'label': 'Verification Code Max Attempts',
                'type': 'number',
                'default': '5',
                'description': 'Wrong verification-code attempts allowed before locking.'
            },
            {
                'key': 'VERIFICATION_CODE_LOCK_MINUTES',
                'label': 'Verification Code Lock (min)',
                'type': 'number',
                'default': '30',
                'description': 'How long to block code submissions after the attempt limit is hit.'
            },
            {
                'key': 'MFA_CHALLENGE_EXPIRE_MINUTES',
                'label': 'MFA Challenge Expiry (min)',
                'type': 'number',
                'default': '5',
                'description': 'How long a post-password MFA challenge is valid.'
            },
            {
                'key': 'MFA_MAX_ATTEMPTS',
                'label': 'MFA Max Attempts',
                'type': 'number',
                'default': '5',
                'description': 'Wrong authenticator-code attempts allowed before the login challenge must be restarted.'
            },
        ]
    },

    'billing': {
        'title': 'Billing & Credits',
        'icon': 'dollar',
        'order': 11,
        'items': [
            {
                'key': 'BILLING_ENABLED',
                'label': 'Enable Billing',
                'type': 'boolean',
                'default': 'False',
                'description': 'Enable billing system. Users need credits to use certain features'
            },

            # ===== Membership Plans (3 tiers) =====
            {
                'key': 'MEMBERSHIP_MONTHLY_PRICE_USD',
                'label': 'Monthly Membership Price (USD)',
                'type': 'number',
                'default': '19.9',
                'description': 'Monthly membership price in USD (USDT checkout uses equivalent amount in USDT)'
            },
            {
                'key': 'MEMBERSHIP_MONTHLY_CREDITS',
                'label': 'Monthly Membership Bonus Credits',
                'type': 'number',
                'default': '500',
                'description': 'Credits granted immediately after purchasing monthly membership'
            },
            {
                'key': 'MEMBERSHIP_YEARLY_PRICE_USD',
                'label': 'Yearly Membership Price (USD)',
                'type': 'number',
                'default': '199',
                'description': 'Yearly membership price in USD (USDT checkout uses equivalent amount in USDT)'
            },
            {
                'key': 'MEMBERSHIP_YEARLY_CREDITS',
                'label': 'Yearly Membership Bonus Credits',
                'type': 'number',
                'default': '8000',
                'description': 'Credits granted immediately after purchasing yearly membership'
            },
            {
                'key': 'MEMBERSHIP_LIFETIME_PRICE_USD',
                'label': 'Lifetime Membership Price (USD)',
                'type': 'number',
                'default': '499',
                'description': 'Lifetime membership price in USD (USDT checkout uses equivalent amount in USDT)'
            },
            {
                'key': 'MEMBERSHIP_LIFETIME_MONTHLY_CREDITS',
                'label': 'Lifetime Membership Monthly Credits',
                'type': 'number',
                'default': '800',
                'description': 'Credits granted every 30 days for lifetime members'
            },

            # ===== USDT Pay (v3.0.6+: one fixed address per chain + amount-suffix matching) =====
            # Model: each chain has a single receiving address. Orders are
            # disambiguated by a unique amount suffix in the low decimals
            # (e.g. 19.991234 USDT, where .001234 is the order tag), so funds
            # land directly in the operator wallet without per-order HD
            # derivation or batched consolidation.
            {
                'key': 'USDT_PAY_ENABLED',
                'label': 'Enable USDT Pay',
                'type': 'boolean',
                'default': 'False',
                'description': 'Master switch for USDT scan-to-pay checkout (multi-chain, single address + amount-suffix matching).'
            },
            {
                'key': 'USDT_PAY_ENABLED_CHAINS',
                'label': 'Enabled Chains',
                'type': 'text',
                'default': 'TRC20,BEP20,ERC20,SOL',
                'description': 'Comma-separated chain whitelist. Any code not in this list is rejected at order creation. Valid codes: TRC20 / BEP20 / ERC20 / SOL.'
            },
            {
                'key': 'USDT_TRC20_ADDRESS',
                'label': 'TRC20 Receiving Address',
                'type': 'text',
                'required': False,
                'description': 'Your TRON wallet address (starts with T...). Leave blank to hide TRC20 from the chain picker.'
            },
            {
                'key': 'USDT_BEP20_ADDRESS',
                'label': 'BEP20 Receiving Address',
                'type': 'text',
                'required': False,
                'description': 'Your BSC wallet address (0x...). Reconciliation runs on public BSC RPC by default — no API key needed.'
            },
            {
                'key': 'USDT_ERC20_ADDRESS',
                'label': 'ERC20 Receiving Address',
                'type': 'text',
                'required': False,
                'description': 'Your Ethereum wallet address (0x...). Reconciliation prefers Etherscan V2 (free plan covers ETH), with public Ethereum RPC fallback.'
            },
            {
                'key': 'USDT_SOL_ADDRESS',
                'label': 'Solana Receiving Address',
                'type': 'text',
                'required': False,
                'description': 'Your Solana wallet address (base58). The SPL USDT mint ATA is derived on-chain by the sender wallet.'
            },
            {
                'key': 'TRONGRID_API_KEY',
                'label': 'TronGrid API Key',
                'type': 'password',
                'required': False,
                'description': 'Optional. Higher TronGrid rate-limit / stability for TRC20 reconciliation. Get one at https://www.trongrid.io.'
            },
            {
                'key': 'ETHERSCAN_API_KEY',
                'label': 'Etherscan API Key',
                'type': 'password',
                'required': False,
                'description': 'Optional. Used for ERC20 reconciliation via Etherscan V2 (free plan covers Ethereum mainnet). BEP20 ignores this — it uses public BSC RPC. Get a key at https://etherscan.io/myapikey.'
            },
            {
                'key': 'USDT_PAY_CONFIRM_SECONDS',
                'label': 'Confirm Delay (sec)',
                'type': 'number',
                'default': '30',
                'description': 'Seconds to wait after detecting a transfer before marking the order confirmed and activating the membership.'
            },
            {
                'key': 'USDT_PAY_EXPIRE_MINUTES',
                'label': 'Order Expire (min)',
                'type': 'number',
                'default': '30',
                'description': 'Minutes a pending USDT order stays open before expiring. Users can re-open the modal to generate a fresh amount suffix.'
            },
            {
                'key': 'USDT_WORKER_POLL_INTERVAL',
                'label': 'Worker Poll Interval (sec)',
                'type': 'number',
                'default': '30',
                'description': 'How often the background worker re-scans pending/paid orders against on-chain data.'
            },
            {
                'key': 'BILLING_COST_AI_ANALYSIS',
                'label': 'AI Analysis Cost (per symbol)',
                'type': 'number',
                'default': '10',
                'description': 'Credits per symbol (instant analysis, AI filter, scheduled tasks all use this price)'
            },
            {
                'key': 'BILLING_COST_AI_CODE_GEN',
                'label': 'AI Code Generation Cost',
                'type': 'number',
                'default': '30',
                'description': 'Credits per AI strategy/indicator code generation (higher token usage)'
            },
            {
                'key': 'BILLING_COST_AI_TUNING',
                'label': 'AI Parameter Tuning Cost',
                'type': 'number',
                'default': '50',
                'description': 'Credits per AI parameter tuning run (multi-round model calls plus backtests)'
            },
            {
                'key': 'BILLING_COST_AI_COPILOT_CHAT',
                'label': 'AI Copilot Chat Cost',
                'type': 'number',
                'default': '5',
                'description': 'Credits per AI Copilot conversation turn'
            },
            {
                'key': 'BILLING_COST_AI_COPILOT_IMAGE',
                'label': 'AI Copilot Image Analysis Cost',
                'type': 'number',
                'default': '15',
                'description': 'Extra credits charged when a Copilot message includes chart images'
            },
            {
                'key': 'BILLING_COST_AI_COPILOT_RADAR',
                'label': 'AI Copilot Radar Cost',
                'type': 'number',
                'default': '20',
                'description': 'Credits per AI opportunity radar / market scan request'
            },
            {
                'key': 'CREDITS_REGISTER_BONUS',
                'label': 'Register Bonus',
                'type': 'number',
                'default': '100',
                'description': 'Credits awarded to new users on registration'
            },
            {
                'key': 'CREDITS_REFERRAL_BONUS',
                'label': 'Referral Bonus',
                'type': 'number',
                'default': '50',
                'description': 'Credits awarded to referrer for each signup'
            },
        ]
    },

}


def read_env_file():
    """读取 .env 文件"""
    env_values = {}
    
    if not os.path.exists(ENV_FILE_PATH):
        logger.warning(f".env file not found at {ENV_FILE_PATH}")
        return env_values
    
    try:
        with open(ENV_FILE_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    if (value.startswith('"') and value.endswith('"')) or \
                       (value.startswith("'") and value.endswith("'")):
                        value = value[1:-1]
                    env_values[key] = value
    except Exception as e:
        logger.error(f"Failed to read .env file: {e}")
    
    return env_values


def write_env_file(env_values):
    """写入 .env 文件，保留注释和格式"""
    lines = []
    existing_keys = set()
    
    if os.path.exists(ENV_FILE_PATH):
        try:
            with open(ENV_FILE_PATH, 'r', encoding='utf-8') as f:
                for line in f:
                    original_line = line
                    stripped = line.strip()
                    
                    if not stripped or stripped.startswith('#'):
                        lines.append(original_line)
                        continue
                    
                    if '=' in stripped:
                        key = stripped.split('=', 1)[0].strip()
                        if key in env_values:
                            existing_keys.add(key)
                            value = env_values[key]
                            if ' ' in str(value) or '"' in str(value) or "'" in str(value):
                                lines.append(f'{key}="{value}"\n')
                            else:
                                lines.append(f'{key}={value}\n')
                        else:
                            lines.append(original_line)
                    else:
                        lines.append(original_line)
        except Exception as e:
            logger.error(f"Failed to read .env file for update: {e}")
    
    new_keys = set(env_values.keys()) - existing_keys
    if new_keys:
        if lines and not lines[-1].endswith('\n'):
            lines.append('\n')
        lines.append('\n# Added by Settings UI\n')
        for key in sorted(new_keys):
            value = env_values[key]
            if ' ' in str(value) or '"' in str(value) or "'" in str(value):
                lines.append(f'{key}="{value}"\n')
            else:
                lines.append(f'{key}={value}\n')
    
    try:
        with open(ENV_FILE_PATH, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        return True
    except Exception as e:
        logger.error(f"Failed to write .env file: {e}")
        return False


def _schema_with_advanced_flags():
    """Return a deep-ish copy of CONFIG_SCHEMA with ``is_advanced`` annotated on
    every item according to ``ADVANCED_KEYS``. Lets the frontend split settings
    into a Basic / Advanced tab without each item needing a manual flag."""
    annotated = {}
    for group_key, group in CONFIG_SCHEMA.items():
        items = []
        for item in group.get('items', []):
            new_item = dict(item)
            new_item['is_advanced'] = item['key'] in ADVANCED_KEYS
            items.append(new_item)
        annotated[group_key] = {**group, 'items': items}
    return annotated


@settings_blp.route('/schema', methods=['GET'])
@login_required
@admin_required
def get_settings_schema():
    """Return settings schema definition (admin only)."""
    return jsonify({
        'code': 1,
        'msg': 'success',
        'data': _schema_with_advanced_flags()
    })


@settings_blp.route('/public-config', methods=['GET'])
@login_required
def get_public_config():
    """Return non-sensitive config values needed by frontend widgets."""
    from app.config.data_sources import CCXTConfig
    return jsonify({
        'code': 1,
        'data': {
            'ccxt_default_exchange': (CCXTConfig.DEFAULT_EXCHANGE or 'binance').lower(),
        }
    })


# Default brand values. Used when the matching ENV var is empty or absent so a
# fresh install still ships with sane copy / links instead of blanks.
_BRAND_DEFAULTS = {
    'app_name': 'QuantDinger',
    'copyright': '© 2025-2026 QuantDinger. All rights reserved.',
    'contact_email': 'support@quantdinger.com',
    'contact_support_url': 'https://t.me/quantdinger',
    'contact_feature_request_url': 'https://github.com/brokermr810/QuantDinger/issues',
    'contact_live_chat_url': 'https://t.me/quantdinger',
    'social_github': 'https://github.com/brokermr810/QuantDinger',
    'social_x': 'https://x.com/quantdinger_en',
    'social_discord': 'https://discord.com/invite/tyx5B6TChr',
    'social_telegram': 'https://t.me/quantdinger',
    'social_youtube': 'https://youtube.com/@quantdinger',
}


def _brand_env(name: str, default: str = '') -> str:
    """Read a BRAND_* env var and fall back to the bundled default."""
    value = os.getenv(name, '')
    if value is None:
        value = ''
    value = value.strip()
    if value:
        return value
    return _BRAND_DEFAULTS.get(default, '')


@settings_blp.route('/brand-config', methods=['GET'])
def get_brand_config():
    """Public, no-auth endpoint exposing branding / legal / contact info.

    Drives the frontend's logo, footer, social links, legal modals and version
    label entirely from backend ENV vars so operators can rebrand a deployment
    by editing ``.env`` (or the Settings page) — no frontend rebuild required.

    Empty ENV values fall back to the bundled QuantDinger defaults so a fresh
    install still ships with working links instead of blanks.
    """
    social_specs = [
        ('GitHub', 'github', 'BRAND_SOCIAL_GITHUB', 'social_github'),
        ('X', 'x', 'BRAND_SOCIAL_X', 'social_x'),
        ('Discord', 'discord', 'BRAND_SOCIAL_DISCORD', 'social_discord'),
        ('Telegram', 'telegram', 'BRAND_SOCIAL_TELEGRAM', 'social_telegram'),
        ('YouTube', 'youtube', 'BRAND_SOCIAL_YOUTUBE', 'social_youtube'),
    ]
    social_accounts = []
    for name, icon, env_key, default_key in social_specs:
        url = _brand_env(env_key, default_key)
        if url:
            social_accounts.append({'name': name, 'icon': icon, 'url': url})

    return jsonify({
        'code': 1,
        'msg': 'success',
        'data': {
            'app_name': _brand_env('BRAND_APP_NAME', 'app_name'),
            'app_version': APP_VERSION,
            'copyright': _brand_env('BRAND_COPYRIGHT', 'copyright'),
            'logos': {
                'light': _brand_env('BRAND_LOGO_LIGHT_URL'),
                'dark': _brand_env('BRAND_LOGO_DARK_URL'),
                'collapsed': _brand_env('BRAND_LOGO_COLLAPSED_URL'),
                'favicon': _brand_env('BRAND_FAVICON_URL'),
            },
            'contact': {
                'email': _brand_env('BRAND_CONTACT_EMAIL', 'contact_email'),
                'support_url': _brand_env('BRAND_CONTACT_SUPPORT_URL', 'contact_support_url'),
                'feature_request_url': _brand_env('BRAND_CONTACT_FEATURE_REQUEST_URL', 'contact_feature_request_url'),
                'live_chat_url': _brand_env('BRAND_CONTACT_LIVE_CHAT_URL', 'contact_live_chat_url'),
            },
            'social_accounts': social_accounts,
            'legal': {
                'user_agreement_url': _brand_env('BRAND_LEGAL_USER_AGREEMENT_URL'),
                'user_agreement_text': _brand_env('BRAND_LEGAL_USER_AGREEMENT_TEXT'),
                'privacy_policy_url': _brand_env('BRAND_LEGAL_PRIVACY_POLICY_URL'),
                'privacy_policy_text': _brand_env('BRAND_LEGAL_PRIVACY_POLICY_TEXT'),
            },
            'mobile_app': {
                'latest_version': _brand_env('MOBILE_APP_LATEST_VERSION'),
                'download_url': _brand_env('MOBILE_APP_DOWNLOAD_URL'),
            },
        }
    })


@settings_blp.route('/values', methods=['GET'])
@login_required
@admin_required
def get_settings_values():
    """Return current settings values including secrets (admin only)."""
    env_values = read_env_file()
    
    result = {}
    for group_key, group in CONFIG_SCHEMA.items():
        result[group_key] = {}
        for item in group['items']:
            key = item['key']
            value = env_values.get(key, item.get('default', ''))
            result[group_key][key] = value
            if item['type'] == 'password':
                result[group_key][f'{key}_configured'] = bool(value)
    
    return jsonify({
        'code': 1,
        'msg': 'success',
        'data': result
    })


@settings_blp.route('/save', methods=['POST'])
@login_required
@admin_required
def save_settings():
    """Save settings to .env (admin only)."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'code': 0, 'msg': 'Invalid request payload'})
        
        current_env = read_env_file()
        
        updates = {}
        for group_key, group_values in data.items():
            if group_key not in CONFIG_SCHEMA:
                continue
            
            for item in CONFIG_SCHEMA[group_key]['items']:
                key = item['key']
                if key in group_values:
                    new_value = group_values[key]
                    
                    if new_value is None or new_value == '':
                        if not item.get('required', True):
                            updates[key] = ''
                    else:
                        updates[key] = str(new_value)

        admin_email_sync = None
        if 'ADMIN_EMAIL' in updates:
            requested_admin_email = str(updates.get('ADMIN_EMAIL') or '').strip().lower()
            if requested_admin_email and requested_admin_email != 'admin@example.com':
                if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', requested_admin_email):
                    return jsonify({'code': 0, 'msg': 'Invalid admin email'}), 400

                from app.services.user_service import get_user_service
                user_service = get_user_service()
                admin_username = str(
                    updates.get('ADMIN_USER')
                    or current_env.get('ADMIN_USER')
                    or os.getenv('ADMIN_USER')
                    or ''
                ).strip()
                if not admin_username:
                    try:
                        from app.config.settings import Config
                        admin_username = str(Config.ADMIN_USER or 'quantdinger')
                    except Exception:
                        admin_username = 'quantdinger'

                admin_user = user_service.get_user_by_username(admin_username)
                if not admin_user:
                    first_id = user_service.get_first_user_id()
                    admin_user = user_service.get_user_by_id(first_id) if first_id is not None else None

                existing = user_service.get_user_by_email(requested_admin_email)
                if existing and admin_user and int(existing.get('id')) != int(admin_user.get('id')):
                    return jsonify({
                        'code': 0,
                        'msg': 'Admin email is already used by another account'
                    }), 409
        
        current_env.update(updates)
        
        if write_env_file(current_env):
            clear_config_cache()
            _reload_runtime_env()
            _refresh_runtime_services()

            if 'ADMIN_EMAIL' in updates:
                try:
                    from app.services.user_service import get_user_service
                    admin_email_sync = get_user_service().sync_admin_email_from_config(
                        updates.get('ADMIN_EMAIL'),
                        overwrite_existing=True,
                    )
                except Exception as sync_error:
                    logger.warning(f"Failed to sync admin email after settings save: {sync_error}")
                    admin_email_sync = {
                        'synced': False,
                        'reason': 'error',
                        'message': str(sync_error),
                    }

            response_data = {
                'updated_keys': list(updates.keys()),
                'requires_restart': False,
                'hot_reloaded': True,
                'services_refreshed': True
            }
            if admin_email_sync is not None:
                response_data['admin_email_sync'] = admin_email_sync
            
            return jsonify({
                'code': 1,
                'msg': 'Settings saved successfully',
                'data': response_data
            })
        else:
            return jsonify({'code': 0, 'msg': 'Failed to save settings'})
    
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        return jsonify({'code': 0, 'msg': f'Save failed: {str(e)}'})


@settings_blp.route('/openrouter-balance', methods=['GET'])
@login_required
@admin_required
def get_openrouter_balance():
    """Query OpenRouter account balance (admin only)."""
    try:
        import requests
        from app.config.api_keys import APIKeys
        
        api_key = APIKeys.OPENROUTER_API_KEY
        if not api_key:
            return jsonify({
                'code': 0, 
                'msg': 'OpenRouter API Key 未配置',
                'data': None
            })
        
        # https://openrouter.ai/docs#limits
        resp = requests.get(
            'https://openrouter.ai/api/v1/auth/key',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            },
            timeout=10
        )
        
        if resp.status_code == 200:
            data = resp.json()
            key_data = data.get('data', {})
            usage = key_data.get('usage', 0)  # 已使用金额
            limit = key_data.get('limit')  # 限额（可能为null表示无限制）
            limit_remaining = key_data.get('limit_remaining')  # 剩余额度
            is_free_tier = key_data.get('is_free_tier', False)
            rate_limit = key_data.get('rate_limit', {})
            
            return jsonify({
                'code': 1,
                'msg': 'success',
                'data': {
                    'usage': round(usage, 4),  # 已使用（美元）
                    'limit': limit,  # 总限额
                    'limit_remaining': round(limit_remaining, 4) if limit_remaining is not None else None,  # 剩余额度
                    'is_free_tier': is_free_tier,
                    'rate_limit': rate_limit,
                    'label': key_data.get('label', '')
                }
            })
        elif resp.status_code == 401:
            return jsonify({
                'code': 0,
                'msg': 'API Key 无效或已过期',
                'data': None
            })
        else:
            return jsonify({
                'code': 0,
                'msg': f'查询失败: HTTP {resp.status_code}',
                'data': None
            })
            
    except requests.exceptions.Timeout:
        return jsonify({
            'code': 0,
            'msg': '请求超时，请检查网络连接',
            'data': None
        })
    except Exception as e:
        logger.error(f"Get OpenRouter balance failed: {e}")
        return jsonify({
            'code': 0,
            'msg': f'查询失败: {str(e)}',
            'data': None
        })


@settings_blp.route('/test-connection', methods=['POST'])
@login_required
@admin_required
def test_connection():
    """Test third-party API connectivity (admin only)."""
    try:
        data = request.get_json()
        service = data.get('service')
        
        if service == 'openrouter':
            from app.services.llm import LLMService
            llm = LLMService()
            result = llm.test_connection()
            if result:
                return jsonify({'code': 1, 'msg': 'OpenRouter connection successful'})
            else:
                return jsonify({'code': 0, 'msg': 'OpenRouter connection failed'})
        
        elif service == 'finnhub':
            import requests
            api_key = data.get('api_key') or os.getenv('FINNHUB_API_KEY')
            if not api_key:
                return jsonify({'code': 0, 'msg': 'API key is not configured'})
            resp = requests.get(
                f'https://finnhub.io/api/v1/quote?symbol=AAPL&token={api_key}',
                timeout=10
            )
            if resp.status_code == 200:
                return jsonify({'code': 1, 'msg': 'Finnhub connection successful'})
            else:
                return jsonify({'code': 0, 'msg': f'Finnhub connection failed: {resp.status_code}'})
        
        return jsonify({'code': 0, 'msg': 'Unknown service'})
    
    except Exception as e:
        logger.error(f"Connection test failed: {e}")
        return jsonify({'code': 0, 'msg': f'Test failed: {str(e)}'})

# openapi-compat: legacy import name
settings_bp = settings_blp
