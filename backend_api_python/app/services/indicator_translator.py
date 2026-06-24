"""
Indicator Translator - 指标多语言翻译服务

设计目标
========
解决「指标市场里来自全球作者的 name/description 语言混杂、用户看不懂」的问题。
做法：作者点发布时，调用 LLM 把 name + description 同时翻译成全部支持语言，
将结果写到 qd_indicator_codes.name_i18n / description_i18n (JSONB 字典)，
读取接口按 Accept-Language 取对应键即可。

支持的语言列表来源
==================
QuantDinger-Vue-src/src/locales/lang/ 下的所有 .js — 10 种：
zh-CN, en-US, zh-TW, ar-SA, de-DE, fr-FR, ja-JP, ko-KR, th-TH, vi-VN。

容错策略
========
LLM 调用本身是网络密集且偶尔会失败的：
1. 任何步骤抛异常时，本模块永远 *不会* 让上游 save_indicator 失败 ——
   只在日志里 warn，并把 name_i18n/description_i18n 留为 None；
   下游接口会自动 fallback 到原始 name/description。
2. 如果 LLM 返回的 JSON 缺某些语言，沿用已有 / 留空（仍可 fallback）。
3. 原始语言对应键直接写入用户原文，不再过 LLM，避免 LLM 译回原文丢失味道。
4. 翻译是 "best-effort"：即使所有语言都翻译失败，只要 source_language 设置正确，
   接口仍然能展示原文（只是非原生用户体验下降，不会 5xx）。

性能考虑
========
- 同步阻塞：save_indicator 等待翻译完成再返回。一次 LLM 调用约 2-5s，
  对作者发布操作来说在可接受范围。如果以后 P99 拖太长，可改成后台 worker。
- 单次调用：一次 prompt 让 LLM 输出所有目标语言的 JSON，比 10 次单独调用快得多。
"""
from __future__ import annotations

import json
import re
from typing import Dict, Optional, Tuple

from app.services.llm import LLMService
from app.utils.logger import get_logger

logger = get_logger(__name__)


SUPPORTED_LANGUAGES: Dict[str, str] = {
    'zh-CN': 'Simplified Chinese',
    'zh-TW': 'Traditional Chinese',
    'en-US': 'English',
    'ar-SA': 'Arabic',
    'de-DE': 'German',
    'fr-FR': 'French',
    'ja-JP': 'Japanese',
    'ko-KR': 'Korean',
    'th-TH': 'Thai',
    'vi-VN': 'Vietnamese',
}


_ASCII_RATIO_FOR_LATIN = 0.85


def detect_source_language(text: str, fallback: str = 'en-US') -> str:
    """根据文本字符分布做一个粗糙的语言检测，仅用于兜底。

    精确语言检测建议在前端按用户 UI 语言决定后传给后端，本函数只在前端没传时
    避免空值。常见场景：
      - 中文比例 > 30%  → zh-CN
      - 日文假名出现    → ja-JP
      - 韩文音节出现    → ko-KR
      - 阿拉伯字符出现  → ar-SA
      - 泰文字符出现    → th-TH
      - 越南组合元音    → vi-VN
      - 其它 → fallback (默认 en-US)
    """
    if not text:
        return fallback

    has_cjk     = bool(re.search(r'[\u4e00-\u9fff]', text))
    has_kana    = bool(re.search(r'[\u3040-\u30ff]', text))
    has_hangul  = bool(re.search(r'[\uac00-\ud7af]', text))
    has_arabic  = bool(re.search(r'[\u0600-\u06ff]', text))
    has_thai    = bool(re.search(r'[\u0e00-\u0e7f]', text))
    has_viet    = bool(re.search(r'[\u00C0-\u1EF9]', text))

    if has_kana:   return 'ja-JP'
    if has_hangul: return 'ko-KR'
    if has_arabic: return 'ar-SA'
    if has_thai:   return 'th-TH'
    if has_cjk:    return 'zh-CN'
    if has_viet:   return 'vi-VN'
    return fallback


def _build_prompt(name: str, description: str, source_lang: str) -> Tuple[str, str]:
    """组装 system + user prompt。"""
    target_pairs = "\n".join(
        f"  - {code}: {label}"
        for code, label in SUPPORTED_LANGUAGES.items()
    )

    system_prompt = (
        "You are a professional translator specialised in quantitative trading and "
        "technical indicators. Translate the given indicator name and description into "
        "all of the following languages.\n\n"
        "Rules:\n"
        "1. Preserve all technical jargon (RSI, MACD, EMA, Bollinger Bands, "
        "ATR, ADX, etc.) verbatim in every language — never translate the "
        "ticker / formula names.\n"
        "2. Keep the name SHORT (ideally <= 4 words / <=14 CJK characters).\n"
        "3. Keep the description tight: 1-2 sentences, plain prose, no markdown, "
        "no emojis, no quotes.\n"
        "4. If a target language is the same as the source language, copy the "
        "original text unchanged.\n"
        "5. Output STRICT JSON only — no markdown fences, no commentary.\n\n"
        f"Target languages:\n{target_pairs}\n\n"
        'Output schema (every code must appear, even if value is identical to source):\n'
        '{\n'
        '  "name":        { "<code>": "...", ... },\n'
        '  "description": { "<code>": "...", ... }\n'
        '}'
    )

    user_prompt = (
        f"Source language: {source_lang} ({SUPPORTED_LANGUAGES.get(source_lang, source_lang)})\n\n"
        f"Indicator name:\n{name}\n\n"
        f"Indicator description:\n{description}\n"
    )

    return system_prompt, user_prompt


def _coerce_str(val) -> str:
    """LLM 偶尔输出 list / dict / None — 兜底转 str。"""
    if val is None:
        return ''
    if isinstance(val, str):
        return val.strip()
    try:
        return str(val).strip()
    except Exception:
        return ''


def translate_indicator(
    name: str,
    description: str,
    source_language: Optional[str] = None,
) -> Tuple[Optional[Dict[str, str]], Optional[Dict[str, str]], str]:
    """把指标 name / description 翻译成全部支持的语言。

    Args:
        name: 原始 name (允许任意语言)
        description: 原始 description
        source_language: 原始语言代码 (zh-CN / en-US / ...)。若为 None 或不在支持列表，
                         自动用 ``detect_source_language`` 做粗判。

    Returns:
        (name_i18n, description_i18n, resolved_source_language)
          - name_i18n / description_i18n: dict[lang_code -> translated_text]
            翻译失败时返回 None（save_indicator 视作 NULL 入库，读取接口走 fallback）。
          - resolved_source_language: 最终确定的原始语言代码（已做兜底）。
    """
    src = source_language if source_language in SUPPORTED_LANGUAGES else None
    if not src:
        src = detect_source_language(description or name, fallback='en-US')

    name = (name or '').strip()
    description = (description or '').strip()

    if not name and not description:
        return None, None, src

    try:
        system_prompt, user_prompt = _build_prompt(name, description, src)

        llm = LLMService()
        result = llm.safe_call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            default_structure={'name': {}, 'description': {}},
        )

        raw_name = result.get('name') if isinstance(result, dict) else None
        raw_desc = result.get('description') if isinstance(result, dict) else None

        if not isinstance(raw_name, dict) and not isinstance(raw_desc, dict):
            logger.warning(
                "translate_indicator: LLM returned no usable dict (raw=%s)",
                str(result)[:200],
            )
            return None, None, src

        name_i18n: Dict[str, str] = {}
        desc_i18n: Dict[str, str] = {}

        for code in SUPPORTED_LANGUAGES.keys():
            n_val = _coerce_str((raw_name or {}).get(code))
            d_val = _coerce_str((raw_desc or {}).get(code))

            if code == src:
                if name:        n_val = name
                if description: d_val = description

            if n_val:
                name_i18n[code] = n_val
            if d_val:
                desc_i18n[code] = d_val

        if src not in name_i18n and src not in desc_i18n:
            logger.warning(
                "translate_indicator: no entry for source language %s after coercion",
                src,
            )
            return None, None, src

        return (name_i18n or None), (desc_i18n or None), src

    except Exception as e:
        logger.warning(f"translate_indicator failed (non-fatal): {e}")
        return None, None, src


def pick_localized(
    raw_text: str,
    i18n_payload,
    accept_lang: str,
    source_lang: Optional[str] = None,
) -> str:
    """根据请求语言选最合适的字段。

    优先级：
      1. accept_lang 精确匹配 i18n_payload
      2. accept_lang 的主语言（zh-* → 任意 zh-*）模糊匹配
      3. en-US (universal fallback)
      4. source_lang 原始语言
      5. 原始 raw_text

    Args:
        raw_text: indicators 表里原始 name 或 description 字段
        i18n_payload: name_i18n 或 description_i18n (dict 或 JSON str 或 None)
        accept_lang: 当前请求语言代码，例如 'zh-CN'
        source_lang: 原始语言代码
    """
    if not i18n_payload:
        return raw_text or ''

    if isinstance(i18n_payload, str):
        try:
            i18n_payload = json.loads(i18n_payload)
        except Exception:
            return raw_text or ''

    if not isinstance(i18n_payload, dict):
        return raw_text or ''

    val = i18n_payload.get(accept_lang)
    if val:
        return val

    if accept_lang and '-' in accept_lang:
        prefix = accept_lang.split('-')[0].lower() + '-'
        for k, v in i18n_payload.items():
            if isinstance(k, str) and k.lower().startswith(prefix) and v:
                return v

    # English universal fallback
    if 'en-US' in i18n_payload and i18n_payload['en-US']:
        return i18n_payload['en-US']

    if source_lang and source_lang in i18n_payload and i18n_payload[source_lang]:
        return i18n_payload[source_lang]

    return raw_text or ''
