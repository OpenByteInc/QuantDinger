# Thesis Tracker Web Search Reference

## Purpose

Use `web_search` only to supplement company news, management changes, industry trend, competition, and analyst-view context that `RQData CLI` does not directly provide for a thesis-tracker report.

## Allowed Coverage

- Company news and major operating developments
- Management changes, governance developments, and organizational updates
- Industry trend and demand-cycle context
- Competitive structure and peer positioning context
- Analyst-view changes or external expectation shifts

## Prohibited Usage

- Do not replace prices, financials, valuation factors, dividends, shareholder structure, or announcements
- Do not fabricate company disclosures, target prices, or hard financial facts
- Do not let low-confidence external information rewrite the core thesis on its own

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

## Allowed `finding_type`

- `company_news`
- `management_change`
- `industry_trend`
- `competition_context`
- `analyst_view`

## Source Types And Confidence Ceiling

- `official`: max confidence `5`
- `government`: max confidence `4`
- `association`: max confidence `4`
- `authoritative_media`: max confidence `4`
- `general_news`: max confidence `3`
- `inference`: max confidence `1`

## Search Workflow

1. Confirm the needed information is not directly available from `RQData CLI`.
2. Prefer official, exchange, company, regulator, and primary-media sources first.
3. Save the findings into `web_search_findings.json` with structured metadata.
4. Keep the summary factual and keep the relevance note tied to thesis validation or falsification.
5. Use the findings only as supplementary evidence for catalyst tracking, risk monitoring, and thesis context.

## Fallback

1. Use the native `web_search` tool when available.
2. Otherwise use the configured network search tool in the current environment.
3. If neither is available:
   - do not fabricate real-time information
   - explicitly mark the related context as unavailable or unverified
   - keep the report at the RQData-driven thesis-tracking level

## Example

```json
{
  "data": [
    {
      "query": "中国平安 管理层 2026-04",
      "source_name": "中国平安",
      "source_type": "official",
      "title": "公司发布高管调整公告",
      "url": "https://www.example.com/pingan",
      "published_at": "2026-04-07",
      "retrieved_at": "2026-04-08",
      "summary": "公司披露管理层分工调整与组织安排。",
      "why_relevant": "可用于验证管理层执行力与战略推进节奏是否发生变化。",
      "confidence": 5,
      "finding_type": "management_change",
      "subject": "管理层调整"
    }
  ]
}
```
