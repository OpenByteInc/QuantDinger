# Initiating Coverage Web Search Reference

## Purpose

Use `web_search` only to supplement qualitative information that `RQData CLI` does not directly provide for an initiating-coverage report.

## Allowed Coverage

- Management biographies, public career history, and governance events
- Industry size, competitive structure, policy environment, and regulatory changes
- Recent company product, capacity, organization, or partnership updates
- Competitor qualitative positioning that helps explain the peer set

## Prohibited Usage

- Do not replace financial statements, valuation multiples, prices, dividends, consensus, or peer ranking
- Do not use `web_search` to fabricate official disclosures or hard financial facts
- Do not let low-confidence external context dominate the core valuation or rating logic

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
- `related_entities`
- `stance`

## Allowed `finding_type`

- `management_update`
- `industry_context`
- `policy_context`
- `company_news`
- `competition_context`

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
5. Use the findings only as qualitative context for company research, industry framing, or governance interpretation.

## Fallback

1. Use the native `web_search` tool when available.
2. Otherwise use the configured network search tool in the current environment.
3. If neither is available:
   - do not fabricate real-time information
   - explicitly mark that the qualitative context is unavailable or unverified
   - keep the report at the structured-data level instead of pretending the research is complete

## Example

```json
{
  "data": [
    {
      "query": "公司名 董事长 简历 2026",
      "source_name": "公司官网",
      "source_type": "official",
      "title": "董事长简历",
      "url": "https://www.example.com/management",
      "published_at": "2026-03-01",
      "retrieved_at": "2026-04-07",
      "summary": "公司官网披露董事长曾在行业龙头和监管机构任职，拥有较长产业和管理经验。",
      "why_relevant": "可用于补充管理层与治理画像章节的定性背景。",
      "confidence": 5,
      "finding_type": "management_update",
      "subject": "董事长履历",
      "related_entities": ["示例公司"],
      "stance": "neutral"
    }
  ]
}
```
