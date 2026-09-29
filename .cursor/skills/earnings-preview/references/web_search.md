# Earnings Preview Web Search Reference

## Purpose

Use `web_search` only to supplement earnings release date, conference-call arrangement, recent company developments, and industry context that `RQData CLI` does not directly provide for an earnings-preview report.

## Allowed Coverage

- Expected earnings release date for the target quarter
- Conference call / results briefing time and registration details
- Recent industry trend or policy changes relevant to the upcoming quarter
- Recent company news that may affect the quarter preview or guidance discussion

## Prohibited Usage

- Do not replace historical financials, prices, consensus data, research reports, or announcements
- Do not fabricate release dates, conference calls, or guidance
- Do not let low-confidence network-search findings replace the structured prediction framework

## Required Output File

All network-search findings must be written to `web_search_findings.json`.

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
- `event_date`
- `expected_window`

## Allowed `finding_type`

- `earnings_release_date`
- `conference_call`
- `industry_context`
- `company_news`

## Source Types And Confidence Ceiling

- `official`: max confidence `5`
- `government`: max confidence `4`
- `association`: max confidence `4`
- `authoritative_media`: max confidence `4`
- `general_news`: max confidence `3`
- `inference`: max confidence `1`

## Search Workflow

1. Confirm the needed information is not directly available from `RQData CLI`.
2. For release dates and conference calls, prefer exchange, company IR, and official announcement sources first.
3. Save the findings into `web_search_findings.json` with structured metadata.
4. Keep summaries factual and tie the relevance note to the quarter preview, guidance risk, or timing risk.
5. Use the findings only to supplement the prediction framework, not to replace it.

## Fallback

1. Use the native `web_search` tool when available.
2. Otherwise use the configured network search tool in the current environment.
3. If neither is available:
   - do not fabricate real-time dates or call arrangements
   - explicitly mark the relevant timing information as unavailable or unverified
   - keep the report at the structured preview level

## Example

```json
{
  "data": [
    {
      "query": "贵州茅台 2026Q1 业绩发布日期",
      "source_name": "上交所",
      "source_type": "official",
      "title": "定期报告预约披露日公告",
      "url": "https://www.example.com/sse",
      "published_at": "2026-04-10",
      "retrieved_at": "2026-04-11",
      "summary": "公司披露 2026Q1 定期报告预约披露日。",
      "why_relevant": "可用于确定财报前窗口和交易节奏安排。",
      "confidence": 5,
      "finding_type": "earnings_release_date",
      "event_date": "2026-04-28",
      "subject": "定期报告预约披露日"
    }
  ]
}
```
