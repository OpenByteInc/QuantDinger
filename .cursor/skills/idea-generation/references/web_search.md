# Idea Generation Web Search Reference

## Purpose

Use `web_search` only to supplement real-time information that `RQData CLI` does not directly provide for theme validation after the quantitative screen is finished.

## Allowed Coverage

- Theme validation for sectors or concepts highlighted by the screen
- Policy or regulatory changes relevant to candidate industries
- Important recent company news or conference signals that may affect research priority
- External catalysts that help explain why a candidate may deserve the next round of work

## Prohibited Usage

- Do not replace financial statements, valuation multiples, stock pool construction, or candidate ranking
- Do not use `web_search` to fabricate official disclosures or structured financial data
- Do not promote low-confidence media snippets into final investment conclusions

## Required Output File

All external findings must be written to `web_search_findings.json`.

Each record must contain:

- `query`
- `source_name`
- `source_type`
- `title`
- `url`
- `published_at`
- `retrieved_at`
- `summary`
- `why_relevant`
- `confidence`
- `finding_type`

Recommended fields:

- `subject`
- `related_tickers`
- `stance`

## Allowed `finding_type`

- `theme_validation`
- `industry_context`
- `policy_context`
- `company_news`
- `catalyst`

## Source Types And Confidence Ceiling

- `official`: max confidence `5`
- `government`: max confidence `4`
- `association`: max confidence `4`
- `authoritative_media`: max confidence `4`
- `general_news`: max confidence `3`
- `inference`: max confidence `1`

## Search Workflow

1. Confirm the needed information is not directly available from `RQData CLI`.
2. Prefer official and primary sources first.
3. Save the finding into `web_search_findings.json` with structured metadata.
4. Keep the summary factual and keep the relevance note concrete.
5. Use the findings only to validate or prioritize themes, not to rewrite quantitative facts.

## Fallback

1. Use the native `web_search` tool when available.
2. Otherwise use the configured network search tool in the current environment.
3. If neither is available:
   - do not fabricate real-time information
   - explicitly mark that theme validation is unavailable or unverified
   - keep the report at the quantitative-candidate level instead of pretending the theme is confirmed

## Example

```json
{
  "data": [
    {
      "query": "人形机器人 政策 2026 4月",
      "source_name": "工业和信息化部",
      "source_type": "government",
      "title": "关于推动智能制造场景建设的通知",
      "url": "https://www.example.com/policy",
      "published_at": "2026-04-02",
      "retrieved_at": "2026-04-07",
      "summary": "文件强调高端装备和智能制造场景建设，强化产业链设备投资预期。",
      "why_relevant": "可用于验证量化筛选中高端装备候选的景气主线是否仍在强化。",
      "confidence": 4,
      "finding_type": "policy_context",
      "subject": "智能制造政策",
      "related_tickers": ["300124.XSHE", "688777.XSHG"],
      "stance": "positive"
    }
  ]
}
```
