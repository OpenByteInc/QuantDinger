# Catalyst Calendar Web Search Reference

## Purpose

Use `web_search` only to supplement macro / industry catalysts that `RQData CLI` cannot directly provide.

## Allowed Coverage

- Macro release dates and policy meeting schedules
- Industry conferences, forums, exhibitions, consultation windows, regulatory milestones
- Cross-company thematic catalysts that may affect the covered stock pool

## Prohibited Usage

- Do not use `web_search` to replace earnings dates, dividends, exchange announcements, shareholder meetings, or other formal company events
- Do not use `web_search` to replace structured financial, valuation, or price data
- Do not fill missing real-time dates from memory

## Required Output File

All web-searched catalysts must be written to `web_search_events.json`.

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
- `event_scope`
- `scope_name`
- `event_title`
- `date_type`
- `event_date` or `window_start` + `window_end`
- `impact_level`

## Field Rules

- `event_scope` only allows `macro` or `industry`
- `date_type=exact` requires `event_date`
- `date_type=estimated_window` requires `window_start` and `window_end`
- Estimated-window web events cannot have confidence above `3`
- `published_at` is the source publication date, not the event date
- `retrieved_at` is the actual search / retrieval date

## Source Types And Confidence Ceiling

- `official`: max confidence `5`
- `government`: max confidence `4`
- `association`: max confidence `4`
- `authoritative_media`: max confidence `4`
- `general_news`: max confidence `3`
- `inference`: max confidence `1`

## Search Workflow

1. Search only for information `RQData CLI` cannot directly supply.
2. Prefer official and primary sources first.
3. Extract the actual event date or expected window from the source.
4. Write the result to `web_search_events.json` with source metadata and relevance notes.
5. If the date is still not confirmed, downgrade to `estimated_window` and lower confidence.

## Fallback

1. Use the native `web_search` tool when available.
2. Otherwise use the configured network search tool in the current environment.
3. If neither is available:
   - do not fabricate dates
   - mark the item as unavailable or unverified
   - do not upgrade confidence artificially

## Example

```json
{
  "data": [
    {
      "query": "2026年4月 CPI 发布日期 国家统计局",
      "source_name": "国家统计局",
      "source_type": "government",
      "title": "2026年4月居民消费价格数据发布时间安排",
      "url": "https://www.stats.gov.cn/...",
      "published_at": "2026-04-01",
      "retrieved_at": "2026-04-07",
      "summary": "国家统计局更新了 4 月价格数据发布时间。",
      "why_relevant": "宏观数据发布可能影响利率预期和高估值板块风险偏好。",
      "confidence": 4,
      "event_scope": "macro",
      "scope_name": "宏观",
      "event_title": "4月 CPI / PPI 数据发布",
      "date_type": "exact",
      "event_date": "2026-04-10",
      "impact_level": "H"
    }
  ]
}
```
