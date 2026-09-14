# Morning Note Web Search Reference

## Purpose

Use `web_search` only to supplement macro, policy, overseas market, commodity, and industry context that `RQData CLI` does not directly provide for a morning-note report.

## Allowed Coverage

- Macro and regulatory updates
- Overseas market developments and major policy signals
- Industry and theme-chain news relevant to the covered stocks
- Commodity or supply-chain context that helps explain sector sentiment

## Prohibited Usage

- Do not replace stock prices, benchmark moves, announcements, earnings, or dividend records
- Do not use `web_search` to fabricate company disclosures or hard financial facts
- Do not let low-confidence external context dominate the morning call

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

- `macro_context`
- `policy_context`
- `industry_context`
- `global_market_context`
- `commodity_context`

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
4. Keep the summary factual and keep the relevance note tied to the morning call.
5. Use the findings only as context for the overnight view, watchlist, and risk framing.

## Fallback

1. Use the native `web_search` tool when available.
2. Otherwise use the configured network search tool in the current environment.
3. If neither is available:
   - do not fabricate real-time information
   - explicitly mark that the external context is unavailable or unverified
   - keep the report at the structured-data level instead of pretending the morning note is complete

## Example

```json
{
  "data": [
    {
      "query": "央行 逆回购 2026-04-07",
      "source_name": "中国人民银行",
      "source_type": "government",
      "title": "公开市场业务交易公告",
      "url": "https://www.example.com/pboc",
      "published_at": "2026-04-07",
      "retrieved_at": "2026-04-07",
      "summary": "央行披露当日公开市场操作规模和利率安排。",
      "why_relevant": "可用于补充盘前流动性与风险偏好判断。",
      "confidence": 4,
      "finding_type": "policy_context",
      "subject": "流动性操作"
    }
  ]
}
```
