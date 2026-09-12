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

DEFAULT_MAX_TOKENS = 16_384

RETRYABLE_LLM_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504, 529}
RETRYABLE_LLM_ERROR_TYPES = {
    "premature_eof",
    "malformed_stream",
    "provider_overloaded",
    "provider_unavailable",
    "rate_limit_exceeded",
    "server",
    "timeout",
    "unmapped",
}
LLM_ERROR_TYPE_ALIASES = {
    "api_error": "server",
    "authentication_error": "authentication",
    "insufficient_quota": "payment_required",
    "internal_server_error": "server",
    "invalid_request_error": "invalid_request",
    "overloaded_error": "provider_overloaded",
    "permission_error": "permission_denied",
    "rate_limit_error": "rate_limit_exceeded",
    "server_error": "server",
    "service_unavailable": "provider_unavailable",
    "timeout_error": "timeout",
}
MINIMAX_ERROR_TYPES = {
    1000: "server",
    1001: "timeout",
    1002: "rate_limit_exceeded",
    1004: "authentication",
    1008: "payment_required",
    1024: "server",
    1026: "content_policy_violation",
    1027: "content_policy_violation",
    1033: "provider_unavailable",
    1039: "max_tokens_exceeded",
    1041: "rate_limit_exceeded",
    2013: "invalid_request",
    2049: "authentication",
    2056: "rate_limit_exceeded",
}


class LLMAPIError(ValueError):
    """Provider error with protocol metadata preserved for safe recovery decisions."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        request_id: str = "",
        generation_id: str = "",
        error_type: str = "",
        finish_reason: str = "",
        retryable: Optional[bool] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.generation_id = generation_id
        normalized_error_type = str(error_type or "").strip().lower()
        self.error_type = LLM_ERROR_TYPE_ALIASES.get(normalized_error_type, normalized_error_type)
        self.finish_reason = str(finish_reason or "").strip().lower()
        if retryable is None:
            retryable = (
                self.error_type in RETRYABLE_LLM_ERROR_TYPES
                if self.error_type
                else status_code in RETRYABLE_LLM_STATUS_CODES
            )
        self.retryable = bool(retryable)


class LLMProvider(Enum):
    """Supported LLM providers"""
    OPENROUTER = "openrouter"
    REQUESTY = "requesty"
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
    LLMProvider.REQUESTY: {
        "base_url": "https://router.requesty.ai/v1",
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
        "fallback_model": "openai/gpt-5.4",
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
            LLMProvider.REQUESTY,
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
            LLMProvider.REQUESTY: APIKeys.REQUESTY_API_KEY,
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

    def get_max_tokens(self) -> int:
        """Get the shared maximum output-token budget for every LLM provider."""
        config = load_addon_config()
        configured = config.get('llm', {}).get('max_tokens', DEFAULT_MAX_TOKENS)
        try:
            return max(1, int(configured))
        except (TypeError, ValueError):
            logger.warning(
                "Invalid LLM_MAX_TOKENS=%r; using %s",
                configured,
                DEFAULT_MAX_TOKENS,
            )
            return DEFAULT_MAX_TOKENS

    def is_configured(self, provider: LLMProvider = None) -> bool:
        """Return whether the provider has enough configuration to make a request."""
        p = provider or self.provider
        if (self.get_api_key(p) or "").strip():
            return True
        if p == LLMProvider.CUSTOM:
            return bool((self.get_base_url(p) or "").strip())
        return p == LLMProvider.LITELLM

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

    @staticmethod
    def _normalize_error_type(value: Any) -> str:
        error_type = str(value or "").strip().lower()
        return LLM_ERROR_TYPE_ALIASES.get(error_type, error_type)

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
            if not stream and not proxy_url:
                session.close()
                post_kwargs = dict(kwargs)
                post_kwargs.pop("stream", None)
                return requests.post(url, **post_kwargs)
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
            "max_tokens": self.get_max_tokens(),
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
            normalized_base_url = (base_url or "").lower()
            if "atlascloud" in normalized_base_url:
                provider_name = "AtlasCloud"
            elif "openrouter" in normalized_base_url:
                provider_name = "OpenRouter"
            else:
                provider_name = "LLM"
            err_text = self._extract_provider_error(response)
            request_id = self._provider_request_id(response)
            generation_id = self._provider_generation_id(response)
            error_payload = None
            try:
                error_payload = response.json()
            except Exception:
                pass
            error_type = self._provider_error_type_from_value(error_payload)
            metadata = [f"model={model}"]
            if request_id:
                metadata.append(f"request_id={request_id}")
            if generation_id:
                metadata.append(f"generation_id={generation_id}")
            error_msg = (
                f"{provider_name} API {response.status_code} "
                f"({', '.join(metadata)})"
            )

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

            raise LLMAPIError(
                error_msg,
                status_code=response.status_code,
                request_id=request_id,
                generation_id=generation_id,
                error_type=error_type,
            )
        
        result = response.json()
        if "choices" in result and len(result["choices"]) > 0:
            choice = result["choices"][0]
            finish_reason = str(choice.get("finish_reason") or "").strip().lower()
            provider_error = choice.get("error") or result.get("error")
            if provider_error or finish_reason in {"error", "insufficient_system_resource"}:
                error_type = self._provider_error_type_from_value(provider_error or result)
                if finish_reason == "insufficient_system_resource" and not error_type:
                    error_type = "provider_unavailable"
                raise LLMAPIError(
                    "LLM generation failed "
                    f"(model={model}, finish_reason={finish_reason or 'error'}): "
                    f"{self._format_provider_error_value(provider_error) or 'provider interrupted generation'}",
                    status_code=self._status_code_from_provider_error(provider_error),
                    request_id=self._provider_request_id(response),
                    generation_id=str(result.get("id") or self._provider_generation_id(response) or "")[:200],
                    error_type=error_type or "provider_unavailable",
                    finish_reason=finish_reason or "error",
                )
            if finish_reason == "length":
                raise LLMAPIError(
                    f"LLM output reached the configured token limit (model={model})",
                    status_code=400,
                    request_id=self._provider_request_id(response),
                    generation_id=str(result.get("id") or self._provider_generation_id(response) or "")[:200],
                    error_type="max_tokens_exceeded",
                    finish_reason="length",
                    retryable=False,
                )
            if finish_reason == "content_filter":
                raise LLMAPIError(
                    f"LLM output was stopped by the provider content filter (model={model})",
                    status_code=400,
                    request_id=self._provider_request_id(response),
                    generation_id=str(result.get("id") or self._provider_generation_id(response) or "")[:200],
                    error_type="content_policy_violation",
                    finish_reason=finish_reason,
                    retryable=False,
                )
            content = (choice.get("message") or {}).get("content")
            if not content:
                raise ValueError(f"Model {model} returned empty content")
            return content
        else:
            raise ValueError("API response is missing 'choices'")

    @staticmethod
    def _provider_request_id(response) -> str:
        headers = getattr(response, "headers", None) or {}
        for name in (
            "x-request-id",
            "request-id",
            "x-correlation-id",
            "cf-ray",
        ):
            value = headers.get(name) or headers.get(name.title())
            if value:
                return str(value).strip()[:200]
        return ""

    @staticmethod
    def _provider_generation_id(response) -> str:
        headers = getattr(response, "headers", None) or {}
        for name in ("x-generation-id", "generation-id"):
            value = headers.get(name) or headers.get(name.title())
            if value:
                return str(value).strip()[:200]
        return ""

    @classmethod
    def _provider_error_type_from_value(cls, value) -> str:
        if not isinstance(value, dict):
            return ""

        metadata = value.get("metadata")
        if isinstance(metadata, dict) and metadata.get("error_type"):
            return cls._normalize_error_type(metadata.get("error_type"))
        if value.get("error_type"):
            return cls._normalize_error_type(value.get("error_type"))

        nested = value.get("error")
        if isinstance(nested, dict):
            nested_type = cls._provider_error_type_from_value(nested)
            if nested_type:
                return nested_type

        raw_type = value.get("type")
        if isinstance(raw_type, str) and raw_type.strip():
            return cls._normalize_error_type(raw_type)
        return ""

    @staticmethod
    def _status_code_from_provider_error(value, default: int = 502) -> int:
        raw_code = value.get("code") if isinstance(value, dict) else None
        try:
            status_code = int(raw_code)
        except (TypeError, ValueError):
            status_code = default
        if status_code < 400 or status_code > 599:
            return default
        return status_code

    @staticmethod
    def _minimax_stream_error(payload) -> tuple[int, str, str]:
        if not isinstance(payload, dict):
            return 0, "", ""
        base_resp = payload.get("base_resp")
        if not isinstance(base_resp, dict):
            return 0, "", ""
        try:
            code = int(base_resp.get("status_code") or 0)
        except (TypeError, ValueError):
            return 0, "", ""
        if code == 0:
            return 0, "", ""
        message = str(base_resp.get("status_msg") or f"MiniMax error {code}").strip()
        return code, MINIMAX_ERROR_TYPES.get(code, "unmapped"), message

    @classmethod
    def _extract_provider_error(cls, response) -> str:
        payload = None
        try:
            payload = response.json()
        except Exception:
            pass

        detail = cls._format_provider_error_value(payload)
        if not detail:
            detail = str(getattr(response, "text", "") or "").strip()
        return " ".join(detail.split())[:1000]

    @classmethod
    def _format_provider_error_value(cls, value) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts = [cls._format_provider_error_value(item) for item in value]
            return "; ".join(part for part in parts if part)
        if not isinstance(value, dict):
            return ""

        parts = []
        location = value.get("loc") or value.get("location")
        if isinstance(location, (list, tuple)):
            location = ".".join(str(item) for item in location)
        if location:
            parts.append(str(location).strip())

        for key in ("error", "message", "msg", "detail", "reason"):
            if key not in value:
                continue
            text = cls._format_provider_error_value(value.get(key))
            if text and text not in parts:
                parts.append(text)

        if not parts:
            for key in ("code", "type", "status"):
                item = value.get(key)
                if isinstance(item, (str, int, float)) and str(item).strip():
                    parts.append(f"{key}={item}")
        return ": ".join(parts)

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
                "maxOutputTokens": self.get_max_tokens(),
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
            finish_reason = str(candidate.get("finishReason") or "").strip().upper()
            finish_message = str(candidate.get("finishMessage") or "").strip()
            if finish_reason == "MAX_TOKENS":
                raise LLMAPIError(
                    f"Gemini output reached the configured token limit (model={model})",
                    status_code=400,
                    error_type="max_tokens_exceeded",
                    finish_reason="length",
                    retryable=False,
                )
            if finish_reason in {
                "SAFETY",
                "RECITATION",
                "BLOCKLIST",
                "PROHIBITED_CONTENT",
                "SPII",
                "IMAGE_SAFETY",
            }:
                raise LLMAPIError(
                    f"Gemini stopped output for {finish_reason.lower()}"
                    f"{f': {finish_message}' if finish_message else ''}",
                    status_code=400,
                    error_type="content_policy_violation",
                    finish_reason=finish_reason.lower(),
                    retryable=False,
                )
            if finish_reason in {"OTHER", "FINISH_REASON_UNSPECIFIED"}:
                raise LLMAPIError(
                    f"Gemini stopped output unexpectedly (finish_reason={finish_reason}, model={model})"
                    f"{f': {finish_message}' if finish_message else ''}",
                    status_code=502,
                    error_type="provider_unavailable",
                    finish_reason=finish_reason.lower(),
                    retryable=True,
                )
            if finish_reason in {"MALFORMED_FUNCTION_CALL", "LANGUAGE"}:
                raise LLMAPIError(
                    f"Gemini could not produce a usable response (finish_reason={finish_reason}, model={model})",
                    status_code=422,
                    error_type="unprocessable",
                    finish_reason=finish_reason.lower(),
                    retryable=False,
                )
            if "content" in candidate and "parts" in candidate["content"]:
                text = candidate["content"]["parts"][0].get("text", "")
                if text:
                    return text

        prompt_feedback = result.get("promptFeedback") or {}
        block_reason = str(prompt_feedback.get("blockReason") or "").strip()
        if block_reason:
            raise LLMAPIError(
                f"Gemini blocked the prompt (block_reason={block_reason})",
                status_code=400,
                error_type="content_policy_violation",
                finish_reason=block_reason.lower(),
                retryable=False,
            )
        
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
            "max_tokens": self.get_max_tokens(),
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
            raw_status = getattr(e, "status_code", None)
            try:
                status_code = int(raw_status or 502)
            except (TypeError, ValueError):
                status_code = 502
            raw_error_type = (
                getattr(e, "code", "")
                or getattr(e, "type", "")
                or e.__class__.__name__
            )
            error_type = self._normalize_error_type(raw_error_type)
            if error_type.endswith("error") and error_type not in RETRYABLE_LLM_ERROR_TYPES:
                error_type = ""
            raise LLMAPIError(
                f"LiteLLM API error ({model}): {e}",
                status_code=status_code,
                request_id=str(getattr(e, "request_id", "") or ""),
                error_type=error_type,
            ) from e

        if response.choices and len(response.choices) > 0:
            choice = response.choices[0]
            finish_reason = str(getattr(choice, "finish_reason", "") or "").strip().lower()
            if finish_reason == "length":
                raise LLMAPIError(
                    f"LiteLLM output reached the configured token limit (model={model})",
                    status_code=400,
                    error_type="max_tokens_exceeded",
                    finish_reason=finish_reason,
                    retryable=False,
                )
            if finish_reason == "content_filter":
                raise LLMAPIError(
                    f"LiteLLM output was stopped by a content filter (model={model})",
                    status_code=400,
                    error_type="content_policy_violation",
                    finish_reason=finish_reason,
                    retryable=False,
                )
            if finish_reason in {"error", "insufficient_system_resource"}:
                raise LLMAPIError(
                    f"LiteLLM provider interrupted generation (model={model}, finish_reason={finish_reason})",
                    status_code=503,
                    error_type="provider_unavailable",
                    finish_reason=finish_reason,
                    retryable=True,
                )
            content = choice.message.content
            if not content:
                raise ValueError(f"Model {model} returned empty content")
            return content
        else:
            raise ValueError("LiteLLM response is missing 'choices'")

    @staticmethod
    def _iter_sse_data(response):
        """Yield complete SSE data fields while ignoring comments and event metadata."""
        data_lines = []
        for raw_line in response.iter_lines(decode_unicode=False):
            if isinstance(raw_line, bytes):
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r")
            else:
                line = str(raw_line or "").rstrip("\r")

            if not line:
                if data_lines:
                    yield "\n".join(data_lines)
                    data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
                continue
            if line.lstrip().startswith(("{", "[DONE]")):
                if data_lines:
                    yield "\n".join(data_lines)
                    data_lines = []
                yield line.strip()

        if data_lines:
            yield "\n".join(data_lines)

    def _stream_openai_compatible(
        self,
        messages: list,
        model: str,
        temperature: float,
        api_key: str,
        base_url: str,
        timeout: int,
        provider: LLMProvider = None,
    ):
        """Stream OpenAI-compatible deltas and require a provider terminal signal."""
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
            "max_tokens": self.get_max_tokens(),
            "stream": True,
        }
        response = self._llm_post(url, headers=headers, json_payload=data, timeout=timeout, stream=True)
        if response.status_code >= 400:
            err_text = self._extract_provider_error(response)
            error_payload = None
            try:
                error_payload = response.json()
            except Exception:
                pass
            error_type = self._provider_error_type_from_value(error_payload)
            request_id = self._provider_request_id(response)
            generation_id = self._provider_generation_id(response)
            session = getattr(response, "_quantdinger_llm_session", None)
            response.close()
            if session is not None:
                session.close()
            raise LLMAPIError(
                f"LLM API {response.status_code} (model={model}): {err_text}".strip(),
                status_code=response.status_code,
                request_id=request_id,
                generation_id=generation_id,
                error_type=error_type,
            )

        provider_name = (provider or self.provider).value
        saw_terminal = False
        generation_id = self._provider_generation_id(response)
        request_id = self._provider_request_id(response)
        try:
            for event_data in self._iter_sse_data(response):
                if event_data.strip() == "[DONE]":
                    saw_terminal = True
                    break
                try:
                    payload = json.loads(event_data)
                except Exception as exc:
                    raise LLMAPIError(
                        f"Malformed LLM SSE data (provider={provider_name}, model={model})",
                        status_code=502,
                        request_id=request_id,
                        generation_id=generation_id,
                        error_type="malformed_stream",
                        retryable=True,
                    ) from exc

                if not isinstance(payload, dict):
                    continue
                generation_id = str(payload.get("id") or generation_id or "").strip()[:200]
                stream_error = payload.get("error")
                if stream_error:
                    error_text = self._format_provider_error_value(stream_error)
                    error_type = self._provider_error_type_from_value(stream_error)
                    status_code = self._status_code_from_provider_error(stream_error)
                    raise LLMAPIError(
                        "LLM stream error "
                        f"(provider={provider_name}, model={model}, error_type={error_type or 'unknown'}): "
                        f"{error_text or stream_error}",
                        status_code=status_code,
                        request_id=request_id,
                        generation_id=generation_id,
                        error_type=error_type or "provider_unavailable",
                        finish_reason="error",
                    )

                minimax_code, minimax_type, minimax_message = self._minimax_stream_error(payload)
                if minimax_code:
                    raise LLMAPIError(
                        f"MiniMax stream error {minimax_code} (model={model}): {minimax_message}",
                        status_code=502,
                        request_id=request_id,
                        generation_id=generation_id,
                        error_type=minimax_type,
                    )

                choices = payload.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content

                finish_reason = str(choice.get("finish_reason") or "").strip().lower()
                if not finish_reason:
                    continue
                if finish_reason in {"stop", "tool_calls", "function_call"}:
                    saw_terminal = True
                    continue
                if finish_reason == "length":
                    raise LLMAPIError(
                        f"LLM output reached the configured token limit (model={model})",
                        status_code=400,
                        request_id=request_id,
                        generation_id=generation_id,
                        error_type="max_tokens_exceeded",
                        finish_reason=finish_reason,
                        retryable=False,
                    )
                if finish_reason == "content_filter":
                    raise LLMAPIError(
                        f"LLM output was stopped by the provider content filter (model={model})",
                        status_code=400,
                        request_id=request_id,
                        generation_id=generation_id,
                        error_type="content_policy_violation",
                        finish_reason=finish_reason,
                        retryable=False,
                    )
                if finish_reason == "insufficient_system_resource":
                    raise LLMAPIError(
                        f"LLM provider interrupted generation due to insufficient resources (model={model})",
                        status_code=503,
                        request_id=request_id,
                        generation_id=generation_id,
                        error_type="provider_unavailable",
                        finish_reason=finish_reason,
                        retryable=True,
                    )
                raise LLMAPIError(
                    f"LLM stream stopped with unsupported finish_reason={finish_reason} (model={model})",
                    status_code=502,
                    request_id=request_id,
                    generation_id=generation_id,
                    error_type="unmapped",
                    finish_reason=finish_reason,
                )
            if not saw_terminal:
                raise LLMAPIError(
                    f"LLM stream ended before a terminal event (provider={provider_name}, model={model})",
                    status_code=502,
                    request_id=request_id,
                    generation_id=generation_id,
                    error_type="premature_eof",
                    retryable=True,
                )
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
        cfg = load_addon_config()
        explicit_provider_name = str(
            cfg.get('llm', {}).get('provider') or os.getenv('LLM_PROVIDER', '')
        ).strip().lower()
        explicit_provider = None
        if explicit_provider_name:
            try:
                explicit_provider = LLMProvider(explicit_provider_name)
            except ValueError:
                explicit_provider = None
        override_provider = None
        if self._provider_override:
            try:
                override_provider = LLMProvider(self._provider_override.lower())
            except ValueError:
                override_provider = None

        # Infer a provider from the model only when no provider was selected explicitly.
        provider_is_locked = provider is not None or override_provider is not None or explicit_provider is not None
        if model and not provider_is_locked:
            detected_provider = self._detect_provider_from_model(model)
            if detected_provider and detected_provider != LLMProvider.OPENROUTER:
                # Check if we have API key for the detected provider
                if self.get_api_key(detected_provider):
                    provider = detected_provider
                    logger.debug(f"Auto-detected provider '{provider.value}' from model '{model}'")
        
        p = provider or self.provider
        api_key = (self.get_api_key(p) or "").strip()
        base_url = (self.get_base_url(p) or "").strip()
        if not self.is_configured(p):
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
                        break
            
            if not self.is_configured(p):
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
        if use_fallback:
            configured_default = self._normalize_model_for_provider(
                self.get_default_model(p),
                p,
            )
            static_fallback = self._normalize_model_for_provider(
                PROVIDER_CONFIGS[p].get("fallback_model") or "",
                p,
            )
            for candidate in (configured_default, static_fallback):
                if candidate and candidate not in models_to_try:
                    models_to_try.append(candidate)
        
        last_error = None
        last_status_code = None
        attempt_errors = []
        
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
                    
            except LLMAPIError as e:
                status_code = e.status_code
                last_status_code = status_code
                last_error = str(e)
                attempt_errors.append((current_model, str(e)))
                logger.warning(
                    "%s API HTTP error (%s): %s",
                    p.value,
                    current_model,
                    e,
                )

                if (
                    status_code in (402, 403)
                    and try_alternative_providers
                    and current_model == models_to_try[-1]
                ):
                    logger.warning(
                        "%s returned %s. Trying alternative providers...",
                        p.value,
                        status_code,
                    )
                    return self._try_alternative_providers(
                        messages,
                        original_model,
                        temperature,
                        use_json_mode,
                        excluded_provider=p,
                    )

                if not use_fallback:
                    raise

                if current_model == models_to_try[-1]:
                    if len(attempt_errors) > 1:
                        attempts = "; ".join(
                            f"{attempt_model}: {attempt_error}"
                            for attempt_model, attempt_error in attempt_errors
                        )
                        raise LLMAPIError(
                            f"All model calls failed for {p.value}. Attempts: {attempts}",
                            status_code=status_code,
                            request_id=e.request_id,
                        ) from e
                    raise

                logger.warning(
                    "%s returned %s for model %s; trying fallback model...",
                    p.value,
                    status_code,
                    current_model,
                )
                continue

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
        if not self.is_configured(p):
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
        yield from self._stream_openai_compatible(
            messages,
            model,
            temperature,
            api_key,
            base_url,
            timeout,
            provider=p,
        )
    
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
