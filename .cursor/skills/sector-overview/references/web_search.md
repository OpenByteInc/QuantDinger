# Sector Overview Web Search Reference

## Purpose

Use `web_search` only to supplement market size, industry trend, policy, competition, and M&A information that `RQData CLI` does not directly provide for a sector-overview report.

## Allowed Coverage

- Market size and growth outlook
- Industry trend and demand drivers
- Regulatory and policy developments
- Competitive structure and market share context
- M&A and industry consolidation updates

## Prohibited Usage

- Do not replace prices, valuation factors, financials, or announcements
- Do not use `web_search` to fabricate hard financial facts
- Do not let low-confidence network-search information override structured sector data

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

## Allowed `finding_type`

- `market_size_context`
- `trend_context`
- `policy_context`
- `competition_context`
- `mna_context`
