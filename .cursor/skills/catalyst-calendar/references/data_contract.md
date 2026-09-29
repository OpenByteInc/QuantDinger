# Catalyst Calendar Data Contract

## Raw Inputs

The generator looks for these files inside `--data-dir`:

- `stock_pool.json`
- `earnings_express_raw.json`
- `latest_financial_raw.json`
- `dividend_raw.json`
- `instrument_meta.json`
- `announcement_raw.json`
- `web_search_events.json` (optional)

Each file may be:

- `{ "data": [...] }`
- `{ "data": { ... } }`
- `[...]`
- `{ ... }`

## Common Identifier Fields

Ticker fields:

- `order_book_id`
- `ticker`
- `stock_code`
- `symbol`
- `code`

Company name fields:

- `display_name`
- `name`
- `stock_name`
- `company_name`
- `symbol_name`
- `symbol`

## `web_search_events.json` Contract

`web_search_events.json` is only for macro / industry catalysts that cannot be directly sourced from `RQData CLI`.

Each record must contain at least:

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
- `impact_level`

Date fields:

- `date_type = exact` requires `event_date`
- `date_type = estimated_window` requires both `window_start` and `window_end`

Allowed `event_scope` values:

- `macro`
- `industry`

Recommended `source_type` values:

- `official`
- `government`
- `association`
- `authoritative_media`
- `general_news`
- `inference`

Example:

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

## Normalized Event Fields

Each normalized event contains:

- `event_id`
- `ticker`
- `company`
- `event_type`
- `event_title`
- `event_date`
- `date_type`
- `window_start`
- `window_end`
- `impact_level`
- `source`
- `confidence`
- `evidence`
- `notes`
- `disclosure_date`
- `source_link`

## Validation Rules

- Company structured events should come from `RQData CLI`
- `web_search_events.json` must not be used to replace earnings dates, dividend dates, or official announcement events
- Web-search estimated windows must not exceed confidence `3`
- If a past announcement explicitly states a future meeting or activity date, the normalized `event_date` should use the actual future date, while `disclosure_date` keeps the original announcement date
- Raw announcement PDFs should be read selectively: only titles that are likely to contain useful future dates or windows should trigger PDF parsing

## Date Rules

- `date_type = exact`: the event has an explicit date from source data
- `date_type = estimated_window`: the event only has an expected disclosure or occurrence window
- When only a quarter or period is known, the generator must use an estimated window instead of a fabricated exact date
- Dividend events should be queried with a long enough lookback window, but should only enter the calendar when `ex_dividend_date` falls inside the observation window
- Announcement events should be queried with an additional lookback before the report start date; for catalyst-calendar, about 120 days is recommended because past announcements may already contain exact future event dates
- For web events, `published_at` is the disclosure / publication date of the source, not the event occurrence date
