---
name: quantdinger-ricequant-data
description: >-
  Routes QuantDinger CN stocks, HK stocks, and Chinese futures market data
  through Ricequant RQData (rqdatac.get_price). Use when editing kline/ticker
  adapters, DATE archives, CN_STOCK_PROVIDER / HK_STOCK_PROVIDER /
  FUTURES_CN_PROVIDER, or Ricequant skill wiring.
---

# QuantDinger Ricequant data source

On branch `feat/ricequant-data-source`, research bars for **CNStock**, **HKStock**, and **CN futures** come from Ricequant RQData first.

## Defaults

- `CN_STOCK_PROVIDER=rqdata`
- `HK_STOCK_PROVIDER=rqdata`
- `FUTURES_CN_PROVIDER=rqdata`
- Credentials: `RQDATAC_LICENSE` / `RQDATAC_URI` / `RQDATAC_USERNAME`+`RQDATAC_PASSWORD`
- Licensed client: `pip install rqdatac` (not in the default requirements lock)

## Symbol mapping

- A-share: `600519` → `600519.XSHG`, `000001` → `000001.XSHE`
- Index: `000001.SH` → `000001.XSHG`, `399006.SZ` → `399006.XSHE` (`adjust_type=none`)
- HK: `0700.HK` → `00700.XHKG`
- CN futures: existing `to_rqdata_order_book_id` (`RB0` → `RB88`)

## Code

- Shared init: `backend_api_python/app/data_sources/rqdata_futures.py`
- Equities: `backend_api_python/app/data_sources/rqdata_equity.py`
- Callers: `cn_stock.py`, `hk_stock.py`, `futures.py`
- Local CSV archive still writes through `DataSourceFactory` into `DATE/`

## Agent skills

Installed from https://github.com/Jason55-1118/ricequant-skills into `.cursor/skills/` (`ricequant`, `rqdata-python`, `rqams`, research skills). For RQData API lookup, follow `.cursor/skills/rqdata-python/SKILL.md`.
