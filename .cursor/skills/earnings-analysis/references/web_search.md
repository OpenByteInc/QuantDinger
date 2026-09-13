# Earnings Analysis Web Search Reference

## Purpose

Use `web_search` only to supplement real-time information that `RQData CLI` does not directly provide for a post-earnings report.

## Allowed Coverage

- Earnings call schedules or management public remarks
- Important post-earnings company news
- Industry or policy context relevant to the earnings interpretation
- External signals that help explain expectation revisions or market reaction

## Prohibited Usage

- Do not replace financial statements, valuation multiples, prices, official announcements, or consensus data
- Do not use `web_search` to fabricate earnings dates or official disclosure details
- Do not promote low-confidence media snippets into core earnings conclusions

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
- `stance`

## Allowed `finding_type`

- `company_news`
- `management_update`
- `earnings_call`
- `industry_context`
- `policy_context`

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
4. Keep a short summary and a concrete relevance note.
5. If confidence is low, keep it as context only and do not let it dominate the conclusion.

## Fallback

1. Use the native `web_search` tool when available.
2. Otherwise use the configured network search tool in the current environment.
3. If neither is available:
   - do not fabricate real-time information
   - explicitly mark that context as unavailable or unverified
   - lower confidence rather than guessing

## Example

```json
{
  "data": [
    {
      "query": "贵州茅台 2026 业绩说明会 时间",
      "source_name": "贵州茅台官网",
      "source_type": "official",
      "title": "2025年度业绩说明会召开公告",
      "url": "https://www.example.com/ir-call",
      "published_at": "2026-03-30",
      "retrieved_at": "2026-04-07",
      "summary": "公司披露 2025 年度业绩说明会将在 4 月中旬召开。",
      "why_relevant": "有助于判断财报后管理层沟通节奏和市场关注焦点。",
      "confidence": 5,
      "finding_type": "earnings_call",
      "subject": "业绩说明会",
      "stance": "neutral"
    }
  ]
}
```
