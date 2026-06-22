"""
LLM service.
Supports multiple providers: OpenRouter, OpenAI, Google Gemini, DeepSeek, Grok,
AtlasCloud, Custom (OpenAI-compatible), MiniMax.
Kept separate from AnalysisService to avoid circular imports.
"""
import json
import os
import requests
from typing import Dict, Any, Optional, List
from enum import Enum

from app.utils.logger import get_logger
from app.config import APIKeys
from app.utils.config_loader import load_addon_config

logger = get_logger(__name__)


class LLMProvider(Enum):
    """Supported LLM providers"""
    OPENROUTER = "openrouter"
    OPENAI = "openai"
    GOOGLE = "google"
    DEEPSEEK = "deepseek"
    GROK = "grok"
    ATLASCLOUD = "atlascloud"
    CUSTOM = "custom"
    MINIMAX = "minimax"
    LITELLM = "litellm"


# Provider configurations
PROVIDER_CONFIGS = {
    LLMProvider.OPENROUTER: {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-5.4",
        "fallback_model": "openai/gpt-4o-mini",
    },
    LLMProvider.OPENAI: {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-5.4",
        "fallback_model": "gpt-4o-mini",
    },
    LLMProvider.GOOGLE: {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "gemini-1.5-flash",
        "fallback_model": "gemini-1.5-flash",
    },
    LLMProvider.DEEPSEEK: {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "fallback_model": "deepseek-chat",
    },
    LLMProvider.GROK: {
        "base_url": "https://api.x.ai/v1",
        "default_model": "grok-beta",
        "fallback_model": "grok-beta",
    },
    LLMProvider.ATLASCLOUD: {
        "base_url": "https://api.atlascloud.ai/v1",
        "default_model": "openai/gpt-5.4",
        "fallback_model": "deepseek-v3",
    },
    LLMProvider.CUSTOM: {
        "base_url": "",  # User configured via CUSTOM_API_URL
        "default_model": "",  # User configured via CUSTOM_MODEL
        "fallback_model": "",
    },
    LLMProvider.MINIMAX: {
        "base_url": "https://api.minimax.io/v1",
        "default_model": "MiniMax-M2.7",
        "fallback_model": "MiniMax-M2.7-highspeed",
    },
    LLMProvider.LITELLM: {
        "base_url": "",  # LiteLLM SDK handles routing
        "default_model": "openai/gpt-5.4",
        "fallback_model": "gpt-4o-mini",
    },
}


class LLMService:
    """LLM provider wrapper with multi-provider support."""

    def __init__(self, provider: str = None):
        """
        Initialize LLM service.

        Args:
            provider: Override the default provider (openrouter, openai, google, deepseek, grok, atlascloud, custom, minimax)
        """
        self._provider_override = provider

    @property
    def provider(self) -> LLMProvider:
        """Get the active LLM provider."""
        if self._provider_override:
            try:
                return LLMProvider(self._provider_override.lower())
            except ValueError:
                pass
        
        # Check env/config for provider selection
        config = load_addon_config()
        provider_name = config.get('llm', {}).get('provider') or os.getenv('LLM_PROVIDER', '')
        
        if provider_name:
            try:
                # Explicit selection should always be respected.
                # API key validation happens later in call path.
                selected = LLMProvider(provider_name.lower())
                return selected
            except ValueError:
                pass
        
        # Auto-detect: find any provider with a configured API key
        # Priority: DeepSeek > AtlasCloud > Grok > MiniMax > OpenAI > Google > OpenRouter
        # (LiteLLM excluded from auto-detect; must be set explicitly via LLM_PROVIDER=litellm)
        priority_order = [
            LLMProvider.DEEPSEEK,
            LLMProvider.ATLASCLOUD,
            LLMProvider.GROK,
            LLMProvider.MINIMAX,
            LLMProvider.OPENAI,
            LLMProvider.GOOGLE,
            LLMProvider.OPENROUTER,
        ]
        
        for p in priority_order:
            if self.get_api_key(p):
                logger.info(f"Auto-detected LLM provider: {p.value}")
                return p
        
        # Fallback to OpenRouter (will fail later if no key)
        return LLMProvider.OPENROUTER

    def get_api_key(self, provider: LLMProvider = None) -> str:
        """Get API key for the specified provider."""
        p = provider or self.provider
        
        key_map = {
            LLMProvider.OPENROUTER: APIKeys.OPENROUTER_API_KEY,
            LLMProvider.OPENAI: APIKeys.OPENAI_API_KEY,
            LLMProvider.GOOGLE: APIKeys.GOOGLE_API_KEY,
            LLMProvider.DEEPSEEK: APIKeys.DEEPSEEK_API_KEY,
            LLMProvider.GROK: APIKeys.GROK_API_KEY,
            LLMProvider.ATLASCLOUD: APIKeys.ATLASCLOUD_API_KEY,
            LLMProvider.CUSTOM: APIKeys.CUSTOM_API_KEY,
            LLMProvider.MINIMAX: APIKeys.MINIMAX_API_KEY,
            LLMProvider.LITELLM: APIKeys.LITELLM_API_KEY,
        }
        return key_map.get(p, "") or ""

    def get_base_url(self, provider: LLMProvider = None) -> str:
        """Get base URL for the specified provider."""
        p = provider or self.provider
        config = load_addon_config()
        
        # Check for custom base URL in config
        provider_config = config.get(p.value, {})
        custom_url = provider_config.get('base_url') or os.getenv(f'{p.value.upper()}_BASE_URL', '').strip()
        # PR #56 uses CUSTOM_API_URL (not CUSTOM_BASE_URL); APIKeys mirrors env + addon.
        if p == LLMProvider.CUSTOM and not custom_url:
            custom_url = (os.getenv("CUSTOM_API_URL", "").strip() or (APIKeys.CUSTOM_API_URL or "")).strip()

        if custom_url:
            return custom_url.rstrip('/')
        
        return PROVIDER_CONFIGS[p]["base_url"]

    def get_default_model(self, provider: LLMProvider = None) -> str:
        """Get default model for the specified provider."""
        p = provider or self.provider
        config = load_addon_config()
        
        provider_config = config.get(p.value, {})
        custom_model = provider_config.get('model') or os.getenv(f'{p.value.upper()}_MODEL', '').strip()
        
        if custom_model:
            return custom_model
        
        return PROVIDER_CONFIGS[p]["default_model"]

    def get_code_generation_model(self, provider: LLMProvider = None) -> str:
        """Get model for AI code generation; fallback to provider default when unset."""
        model = os.getenv('AI_CODE_GEN_MODEL', '').strip()
        if model:
            return model
        return self.get_default_model(provider)

    # Legacy properties for backward compatibility
    @property
    def api_key(self):
        return self.get_api_key()

    @property
    def base_url(self):
        return self.get_base_url()

    @staticmethod
    def _truthy(value: Any) -> bool:
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def _llm_proxy_url(self) -> str:
        config = load_addon_config()
        return str(
            config.get("llm", {}).get("proxy_url")
            or os.getenv("LLM_PROXY_URL", "")
            or ""
        ).strip()

    def _llm_use_system_proxy(self) -> bool:
        config = load_addon_config()
        value = config.get("llm", {}).get("use_system_proxy")
        if value is None:
            value = os.getenv("LLM_USE_SYSTEM_PROXY", "false")
        return self._truthy(value)

    def _llm_post(self, url: str, *, headers: dict, json_payload: dict, timeout: int, stream: bool = False):
        """
        Send LLM HTTP requests without inheriting exchange/data-source proxies.

        PROXY_URL is intentionally global for market data and broker/exchange APIs,
        but LLM providers should not be routed through it unless explicitly requested.
        This avoids failures such as host.docker.internal:7890 refusing LLM traffic.
        """
        session = requests.Session()
        proxy_url = self._llm_proxy_url()
        use_system_proxy = self._llm_use_system_proxy()
        session.trust_env = use_system_proxy and not proxy_url

        kwargs = {
            "headers": headers,
            "json": json_payload,
            "timeout": timeout,
            "stream": stream,
        }
        if proxy_url:
            kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}

        try:
            response = session.post(url, **kwargs)
        except requests.exceptions.RequestException as exc:
            session.close()
            hint = ""
            msg = str(exc)
            if "SOCKS" in msg or "Proxy" in msg or "proxy" in msg:
                hint = (
                    " LLM request was routed through a proxy. Leave LLM_PROXY_URL empty "
                    "for direct LLM access, or set it to a reachable proxy and keep "
                    "LLM_USE_SYSTEM_PROXY disabled unless you really want system proxy env vars."
                )
            raise requests.exceptions.ConnectionError(f"{msg}{hint}") from exc

        if stream:
            response._quantdinger_llm_session = session
        else:
            session.close()
        return response

    def _call_openai_compatible(self, messages: list, model: str, temperature: float, 
                                 api_key: str, base_url: str, timeout: int,
                                 use_json_mode: bool = True) -> str:
        """Call OpenAI-compatible API (OpenAI, DeepSeek, Grok, AtlasCloud, OpenRouter)."""
        url = f"{base_url}/chat/completions"
        
        headers = {"Content-Type": "application/json"}
        if (api_key or "").strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"
        
        # OpenRouter specific headers
        if "openrouter" in base_url:
            headers["HTTP-Referer"] = "https://quantdinger.com"
            headers["X-Title"] = "QuantDinger Analysis"

        data = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        
        # AtlasCloud documents the OpenAI-compatible ChatCompletion shape, but
        # its public parameter table currently lists model/messages/temperature/
        # max_tokens/stream/top_p and not response_format. Keep prompts JSON-
        # oriented while avoiding a provider-side 400 from an unsupported knob.
        if use_json_mode and "atlascloud" not in (base_url or "").lower():
            data["response_format"] = {"type": "json_object"}

        response = self._llm_post(url, headers=headers, json_payload=data, timeout=timeout)
        
        # Handle non-2xx with provider/model-aware details
        if response.status_code >= 400:
            provider_name = "OpenRouter" if "openrouter" in (base_url or "").lower() else "LLM"
            error_msg = f"{provider_name} API {response.status_code}"
            err_text = ""
            try:
                error_data = response.json() or {}
                error_detail = error_data.get("error")
                if isinstance(error_detail, dict):
                    err_text = str(error_detail.get("message") or "").strip()
                elif isinstance(error_detail, str):
                    err_text = error_detail.strip()
            except Exception:
                err_text = (response.text or "").strip()[:300]

            if err_text:
                error_msg = f"{error_msg}: {err_text}"

            # OpenRouter targeted hints
            if "openrouter" in (base_url or "").lower():
                from app.config.api_keys import APIKeys
                if not APIKeys.OPENROUTER_API_KEY:
                    error_msg += ". OPENROUTER_API_KEY 未配置，请在 backend_api_python/.env 中设置"
                elif response.status_code == 403:
                    error_msg += ". 可能原因：API 密钥无效/过期、余额不足、或无模型权限。请检查 https://openrouter.ai/keys"
                elif response.status_code == 404:
                    error_msg += ". 可能原因：模型不可用或账户隐私/数据策略限制。请检查 https://openrouter.ai/settings/privacy"

            raise ValueError(error_msg)
        
        result = response.json()
        if "choices" in result and len(result["choices"]) > 0:
            content = result["choices"][0]["message"]["content"]
            if not content:
                raise ValueError(f"Model {model} returned empty content")
            return content
        else:
            raise ValueError("API response is missing 'choices'")

    def _call_google_gemini(self, messages: list, model: str, temperature: float,
                           api_key: str, base_url: str, timeout: int) -> str:
        """Call Google Gemini API."""
        url = f"{base_url}/models/{model}:generateContent?key={api_key}"
        
        # Convert OpenAI message format to Gemini format
        contents = []
        system_instruction = None
        
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            
            parts = []
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "text":
                        parts.append({"text": str(item.get("text") or "")})
                    elif item.get("type") == "image_url":
                        image_url = item.get("image_url") or {}
                        data_url = image_url.get("url") if isinstance(image_url, dict) else None
                        if data_url and data_url.startswith("data:image/") and ";base64," in data_url:
                            header, b64 = data_url.split(",", 1)
                            mime_type = header.replace("data:", "").split(";", 1)[0]
                            parts.append({"inline_data": {"mime_type": mime_type, "data": b64}})
            else:
                parts.append({"text": str(content or "")})

            if role == "system":
                system_instruction = str(content or "") if not isinstance(content, list) else ""
            elif role == "user":
                contents.append({"role": "user", "parts": parts or [{"text": ""}]})
            elif role == "assistant":
                contents.append({"role": "model", "parts": parts or [{"text": ""}]})
        
        data = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
            }
        }
        
        if system_instruction:
            data["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        
        headers = {"Content-Type": "application/json"}
        
        response = self._llm_post(url, headers=headers, json_payload=data, timeout=timeout)
        response.raise_for_status()
        
        result = response.json()
        if "candidates" in result and len(result["candidates"]) > 0:
            candidate = result["candidates"][0]
            if "content" in candidate and "parts" in candidate["content"]:
                text = candidate["content"]["parts"][0].get("text", "")
                if text:
                    return text
        
        raise ValueError("Gemini API response is missing content")

    def _call_litellm(self, messages: list, model: str, temperature: float,
                      api_key: str, base_url: str, timeout: int,
                      use_json_mode: bool = True) -> str:
        """Call LLM via LiteLLM SDK (supports 100+ providers)."""
        try:
            import litellm
        except ImportError as e:
            raise ImportError(
                "litellm is required for the LiteLLM provider. "
                "Install it with: pip install 'litellm>=1.80,<1.87'"
            ) from e

        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "timeout": timeout,
            "drop_params": True,
        }

        if use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if (api_key or "").strip():
            kwargs["api_key"] = api_key.strip()
        if (base_url or "").strip():
            kwargs["api_base"] = base_url.strip().rstrip('/')

        try:
            response = litellm.completion(**kwargs)
        except Exception as e:
            raise ValueError(f"LiteLLM API error ({model}): {e}") from e

        if response.choices and len(response.choices) > 0:
            content = response.choices[0].message.content
            if not content:
                raise ValueError(f"Model {model} returned empty content")
            return content
        else:
            raise ValueError("LiteLLM response is missing 'choices'")

    def _stream_openai_compatible(self, messages: list, model: str, temperature: float,
                                  api_key: str, base_url: str, timeout: int):
        """Stream text deltas from an OpenAI-compatible chat endpoint."""
        url = f"{base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if (api_key or "").strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"
        if "openrouter" in base_url:
            headers["HTTP-Referer"] = "https://quantdinger.com"
            headers["X-Title"] = "QuantDinger Analysis"

        data = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        response = self._llm_post(url, headers=headers, json_payload=data, timeout=timeout, stream=True)
        if response.status_code >= 400:
            err_text = ""
            try:
                err = response.json().get("error")
                err_text = err.get("message") if isinstance(err, dict) else str(err or "")
            except Exception:
                err_text = (response.text or "").strip()[:300]
            session = getattr(response, "_quantdinger_llm_session", None)
            response.close()
            if session is not None:
                session.close()
            raise ValueError(f"LLM API {response.status_code}: {err_text}".strip())

        try:
            for raw_line in response.iter_lines(decode_unicode=False):
                if not raw_line:
                    continue
                if isinstance(raw_line, bytes):
                    line = raw_line.decode("utf-8", errors="replace").strip()
                else:
                    line = str(raw_line).strip()
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                choices = payload.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content
        finally:
            session = getattr(response, "_quantdinger_llm_session", None)
            response.close()
            if session is not None:
                session.close()

    def _normalize_model_for_provider(self, model: str, provider: LLMProvider) -> str:
        """
        Normalize model name for the target provider.
        
        Frontend may send OpenRouter-style model names (e.g., 'openai/gpt-5.4').
        This converts them to the correct format for each provider.
        """
        if not model:
            return self.get_default_model(provider)
        
        model = model.strip()
        
        # LiteLLM and OpenRouter use provider/model format natively.
        if provider in (LLMProvider.OPENROUTER, LLMProvider.LITELLM):
            return model

        # AtlasCloud is OpenAI-compatible and may expose routed model ids such
        # as openai/gpt-5.4. Keep third-party prefixes intact, while still
        # accepting atlascloud/model as a convenience alias.
        if provider == LLMProvider.ATLASCLOUD:
            if '/' in model:
                prefix, actual_model = model.split('/', 1)
                if prefix.lower() in ('atlascloud', 'atlas'):
                    return actual_model
                return model
            return model
        
        # For direct providers, extract the model name from OpenRouter format
        # e.g., 'openai/gpt-5.4' -> 'gpt-5.4'
        #       'google/gemini-1.5-flash' -> 'gemini-1.5-flash'
        #       'deepseek/deepseek-chat' -> 'deepseek-chat'
        #       'x-ai/grok-beta' -> 'grok-beta'
        
        if '/' in model:
            prefix, actual_model = model.split('/', 1)
            prefix_lower = prefix.lower()
            
            # Map OpenRouter prefixes to providers
            prefix_to_provider = {
                'openai': LLMProvider.OPENAI,
                'google': LLMProvider.GOOGLE,
                'deepseek': LLMProvider.DEEPSEEK,
                'x-ai': LLMProvider.GROK,
                'xai': LLMProvider.GROK,
                'atlascloud': LLMProvider.ATLASCLOUD,
                'atlas': LLMProvider.ATLASCLOUD,
                'minimax': LLMProvider.MINIMAX,
            }
            
            # If the model prefix matches the current provider, use the extracted model name
            matched_provider = prefix_to_provider.get(prefix_lower)
            if matched_provider == provider:
                return actual_model
            
            # If model prefix doesn't match current provider, use provider's default model
            # This prevents sending a wrong provider's model name to DeepSeek, etc.
            logger.warning(f"Model '{model}' doesn't match provider '{provider.value}', using default model")
            return self.get_default_model(provider)
        
        # Model name without prefix - use as is
        return model

    def _detect_provider_from_model(self, model: str) -> Optional[LLMProvider]:
        """
        Detect which provider a model belongs to based on its name.
        Returns None if detection fails.
        """
        if not model or '/' not in model:
            return None
        
        prefix = model.split('/')[0].lower()
        
        prefix_to_provider = {
            'openai': LLMProvider.OPENAI,
            'google': LLMProvider.GOOGLE,
            'deepseek': LLMProvider.DEEPSEEK,
            'x-ai': LLMProvider.GROK,
            'xai': LLMProvider.GROK,
            'atlascloud': LLMProvider.ATLASCLOUD,
            'atlas': LLMProvider.ATLASCLOUD,
            'minimax': LLMProvider.MINIMAX,
            'anthropic': LLMProvider.OPENROUTER,  # Anthropic only via OpenRouter
            'meta': LLMProvider.OPENROUTER,  # Meta/Llama only via OpenRouter
            'mistral': LLMProvider.OPENROUTER,  # Mistral only via OpenRouter
        }
        
        return prefix_to_provider.get(prefix)

    def call_llm_api(self, messages: list, model: str = None, temperature: float = 0.7, 
                     use_fallback: bool = True, provider: LLMProvider = None,
                     use_json_mode: bool = True, try_alternative_providers: bool = True) -> str:
        """
        Call LLM API with the specified or default provider.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model name (uses provider default if not specified). Supports OpenRouter format (e.g., 'openai/gpt-5.4')
            temperature: Sampling temperature
            use_fallback: Whether to try fallback model on failure
            provider: Override the service's default provider
            use_json_mode: Whether to request JSON output format (default True for analysis, False for code generation)
            try_alternative_providers: Whether to try alternative providers when current provider fails with 403/402
        
        Returns:
            Generated text content
        
        Model Resolution Priority:
            1. If model is specified and matches a direct provider (openai/, google/, deepseek/, x-ai/),
               use that provider directly if its API key is configured
            2. Otherwise, use the configured LLM_PROVIDER with normalized model name
            3. Fall back to provider's default model if model name is incompatible
        """
        # Smart provider detection: if model specifies a provider and we have its API key, use it
        if model and not provider:
            detected_provider = self._detect_provider_from_model(model)
            if detected_provider and detected_provider != LLMProvider.OPENROUTER:
                # Check if we have API key for the detected provider
                if self.get_api_key(detected_provider):
                    provider = detected_provider
                    logger.debug(f"Auto-detected provider '{provider.value}' from model '{model}'")
        
        p = provider or self.provider
        cfg = load_addon_config()
        explicit_provider_name = str(cfg.get('llm', {}).get('provider') or os.getenv('LLM_PROVIDER', '')).strip().lower()
        explicit_provider = None
        if explicit_provider_name:
            try:
                explicit_provider = LLMProvider(explicit_provider_name)
            except ValueError:
                explicit_provider = None
        api_key = (self.get_api_key(p) or "").strip()
        base_url = (self.get_base_url(p) or "").strip()
        # Local OpenAI-compatible servers (e.g. Ollama) often use no API key when base_url is set.
        # LiteLLM reads provider-specific env vars (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.) directly.
        custom_ok_without_key = (p == LLMProvider.CUSTOM and bool(base_url)) or p == LLMProvider.LITELLM

        if not api_key and not custom_ok_without_key:
            # If provider is explicitly configured by user, don't silently switch.
            if explicit_provider is not None and p == explicit_provider:
                if p == LLMProvider.CUSTOM:
                    raise ValueError(
                        "已选择自定义 OpenAI 兼容接口：请配置 CUSTOM_API_URL（例如本机 Ollama："
                        "http://127.0.0.1:11434/v1）。本地 Ollama 通常无需填写 API Key。"
                    )
                raise ValueError(
                    f"API key not configured for explicit provider: {p.value}. "
                    f"Please set {p.value.upper()}_API_KEY in settings."
                )
            # If no API key for current provider, try to find any available provider
            if try_alternative_providers:
                for alt_provider in [LLMProvider.DEEPSEEK, LLMProvider.ATLASCLOUD, LLMProvider.GROK, LLMProvider.MINIMAX, LLMProvider.OPENAI, LLMProvider.GOOGLE, LLMProvider.OPENROUTER]:
                    if alt_provider != p and self.get_api_key(alt_provider):
                        logger.warning(f"No API key for {p.value}, switching to {alt_provider.value}")
                        p = alt_provider
                        api_key = (self.get_api_key(p) or "").strip()
                        base_url = (self.get_base_url(p) or "").strip()
                        custom_ok_without_key = p == LLMProvider.CUSTOM and bool(base_url)
                        break
            
            if not api_key and not custom_ok_without_key:
                raise ValueError(f"API key not configured for provider: {p.value}. Please configure at least one LLM provider API key.")

        if p == LLMProvider.CUSTOM and not base_url:
            raise ValueError(
                "Custom LLM base URL 未配置：请在后台设置或 .env 中填写 CUSTOM_API_URL "
                "（须为 OpenAI 兼容网关的根地址，例如 https://api.example.com/v1）。"
            )

        # Normalize model name for the provider
        original_model = model
        model = self._normalize_model_for_provider(model, p)
        
        config = load_addon_config()
        timeout = int(config.get(p.value, {}).get('timeout', 120))
        
        # Build model candidates
        models_to_try = [model]
        provider_default_model = PROVIDER_CONFIGS[p]["default_model"]
        if use_fallback:
            fallback = PROVIDER_CONFIGS[p].get("fallback_model")
            if fallback and fallback != model:
                models_to_try.append(fallback)
        
        last_error = None
        last_status_code = None
        
        for current_model in models_to_try:
            try:
                if p == LLMProvider.LITELLM:
                    return self._call_litellm(
                        messages, current_model, temperature,
                        api_key, base_url, timeout,
                        use_json_mode=use_json_mode
                    )
                elif p == LLMProvider.GOOGLE:
                    return self._call_google_gemini(
                        messages, current_model, temperature,
                        api_key, base_url, timeout
                    )
                else:
                    # OpenAI-compatible providers
                    return self._call_openai_compatible(
                        messages, current_model, temperature,
                        api_key, base_url, timeout,
                        use_json_mode=use_json_mode
                    )
                    
            except requests.exceptions.HTTPError as e:
                error_detail = e.response.text if e.response else str(e)
                status_code = e.response.status_code if e.response else None
                last_status_code = status_code
                
                logger.error(f"{p.value} API HTTP error ({current_model}): {status_code} - {error_detail}")
                last_error = str(e)
                
                # 403/402 errors usually mean API key issue - try alternative provider
                if status_code in (402, 403) and try_alternative_providers and current_model == models_to_try[-1]:
                    # Only try alternative providers after all models in current provider failed
                    logger.warning(f"{p.value} returned {status_code} (likely API key issue). Trying alternative providers...")
                    return self._try_alternative_providers(
                        messages, original_model, temperature, 
                        use_json_mode, excluded_provider=p
                    )
                
                # Check for recoverable errors - try fallback model
                # 402: Payment required, 403: Forbidden (invalid key), 404: Model not found, 429: Rate limit
                if status_code in (402, 403, 404, 429):
                    logger.warning(f"{p.value} returned {status_code} for model {current_model}; trying fallback...")
                    continue
                
                if not use_fallback or current_model == models_to_try[-1]:
                    raise
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"{p.value} API request error ({current_model}): {str(e)}")
                last_error = str(e)
                if not use_fallback or current_model == models_to_try[-1]:
                    raise
                    
            except ValueError as e:
                logger.warning(f"Model {current_model} returned invalid data: {str(e)}")
                last_error = str(e)
                if current_model == models_to_try[-1]:
                    raise
        
        error_msg = f"All model calls failed for {p.value}. Last error: {last_error}"
        if last_status_code in (402, 403):
            error_msg += f"\nStatus {last_status_code} usually means: API key invalid/expired, insufficient balance, or no access to model."
            error_msg += f"\nPlease check your {p.value} API key configuration and account balance."
        
        logger.error(error_msg)
        raise Exception(error_msg)

    def stream_llm_api(self, messages: list, model: str = None, temperature: float = 0.7):
        """Stream LLM response deltas for providers with OpenAI-compatible streaming."""
        p = self.provider
        api_key = (self.get_api_key(p) or "").strip()
        base_url = (self.get_base_url(p) or "").strip()
        custom_ok_without_key = (p == LLMProvider.CUSTOM and bool(base_url))
        if not api_key and not custom_ok_without_key:
            raise ValueError(f"API key not configured for provider: {p.value}. Please set {p.value.upper()}_API_KEY in settings.")
        if p == LLMProvider.GOOGLE:
            yield self.call_llm_api(messages, model=model, temperature=temperature, use_json_mode=False)
            return
        if p == LLMProvider.LITELLM:
            yield self.call_llm_api(messages, model=model, temperature=temperature, use_json_mode=False)
            return

        model = self._normalize_model_for_provider(model, p)
        config = load_addon_config()
        timeout = int(config.get(p.value, {}).get('timeout', 120))
        yield from self._stream_openai_compatible(messages, model, temperature, api_key, base_url, timeout)
    
    def _try_alternative_providers(self, messages: list, model: str, temperature: float,
                                  use_json_mode: bool, excluded_provider: LLMProvider = None) -> str:
        """
        Try alternative providers when current provider fails.

        Priority: DeepSeek > AtlasCloud > Grok > MiniMax > OpenAI > Google > OpenRouter
        """
        priority_order = [
            LLMProvider.DEEPSEEK,
            LLMProvider.ATLASCLOUD,
            LLMProvider.GROK,
            LLMProvider.MINIMAX,
            LLMProvider.OPENAI,
            LLMProvider.GOOGLE,
            LLMProvider.OPENROUTER,
        ]
        
        for alt_provider in priority_order:
            if alt_provider == excluded_provider:
                continue
            
            api_key = self.get_api_key(alt_provider)
            if not api_key:
                continue
            
            logger.info(f"Trying alternative provider: {alt_provider.value}")
            try:
                return self.call_llm_api(
                    messages, model, temperature,
                    use_fallback=True, provider=alt_provider,
                    use_json_mode=use_json_mode,
                    try_alternative_providers=False  # Prevent infinite recursion
                )
            except Exception as e:
                logger.warning(f"Alternative provider {alt_provider.value} also failed: {str(e)}")
                continue
        
        raise Exception(f"All LLM providers failed. Please check your API key configurations.")

    # Legacy method for backward compatibility
    def call_openrouter_api(self, messages: list, model: str = None, temperature: float = 0.7, use_fallback: bool = True) -> str:
        """Call LLM API (legacy method name for backward compatibility)."""
        return self.call_llm_api(messages, model, temperature, use_fallback)

    def safe_call_llm(self, system_prompt: str, user_prompt: str, default_structure: Dict[str, Any], 
                      model: str = None, provider: LLMProvider = None) -> Dict[str, Any]:
        """Safe LLM call with robust JSON parsing and fallback structure."""
        response_text = ""
        try:
            response_text = self.call_llm_api([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ], model=model, provider=provider)
            
            # Strip markdown fences if present
            clean_text = response_text.strip()
            if clean_text.startswith("```"):
                first_newline = clean_text.find("\n")
                if first_newline != -1:
                    clean_text = clean_text[first_newline+1:]
                if clean_text.endswith("```"):
                    clean_text = clean_text[:-3]
            clean_text = clean_text.strip()
            
            # Parse JSON
            result = json.loads(clean_text)
            return result
        except json.JSONDecodeError:
            logger.error(f"JSON parse failed. Raw text: {response_text[:200] if response_text else 'N/A'}")
            
            # Try extracting JSON substring
            try:
                if response_text:
                    start = response_text.find('{')
                    end = response_text.rfind('}') + 1
                    if start >= 0 and end > start:
                        result = json.loads(response_text[start:end])
                        return result
            except:
                pass
            
            default_structure['report'] = f"Failed to parse analysis result JSON. Raw output (partial): {response_text[:500] if response_text else 'N/A'}"
            return default_structure
        except Exception as e:
            logger.error(f"LLM call failed: {str(e)}")
            default_structure['report'] = f"Analysis failed: {str(e)}"
            return default_structure

    @classmethod
    def get_available_providers(cls) -> List[Dict[str, Any]]:
        """Get list of available (configured) providers."""
        providers = []
        
        for p in LLMProvider:
            service = cls()
            api_key = service.get_api_key(p)
            providers.append({
                "id": p.value,
                "name": p.value.title(),
                "configured": bool(api_key),
                "default_model": PROVIDER_CONFIGS[p]["default_model"],
            })
        
        return providers
