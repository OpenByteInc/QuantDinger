"""Bounded, attributable asset news and issuer disclosures, independent of LLMs."""
from __future__ import annotations

import html
import io
import re
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from time import monotonic
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests

from app.data_providers import get_cached, set_cached


def clean_text(value) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


def published_at(value, local_timezone="UTC") -> str:
    if not value:
        return ""
    try:
        if isinstance(value, (int, float)):
            date = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
        else:
            try:
                date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                date = parsedate_to_datetime(str(value))
        if date.tzinfo is None:
            date = date.replace(tzinfo=ZoneInfo(local_timezone))
        return date.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError, OSError):
        return ""


def asset_terms(market, symbol, name="") -> list[str]:
    base = str(symbol).split("/")[0].split("@")[0]
    names = [clean_text(name)] if name and name != symbol else []
    if market in {"CNStock", "HKStock"}:
        code = "".join(c for c in base.split(".")[0] if c.isdigit())
        names.extend([code, code.lstrip("0")] if market == "HKStock" else [code])
    else:
        names.append(base)
        if market == "USStock" and name:
            short_name = re.sub(r"(?i)[,\s]+(?:inc\.?|incorporated|corporation|corp\.?|limited|ltd\.?|plc)$", "", clean_text(name)).strip()
            if len(short_name) >= 4:
                names.append(short_name)
        if market == "Crypto":
            names.append({"BTC": "Bitcoin", "ETH": "Ethereum"}.get(base.upper(), ""))
    return list(dict.fromkeys(term for term in names if term))


def attributable(text, terms) -> bool:
    text = clean_text(text).casefold()
    return any(re.search(r"(?<![\w])" + re.escape(term.casefold()) + r"(?![\w])", text)
               if term.isascii() else term.casefold() in text for term in terms)


def _item(title, date, url, source, *, summary="", kind="news", depth="headline"):
    return {"headline": clean_text(title), "datetime": date, "url": url,
            "source": source, "summary": clean_text(summary)[:6000],
            "asset_relevance": "direct", "event_kind": kind, "evidence_depth": depth,
            "sentiment": "unknown"}


def fetch_news_rss(market, symbol, name, *, http=requests, timeout=6):
    terms = asset_terms(market, symbol, name)
    subject = name or ({"BTC": "Bitcoin", "ETH": "Ethereum"}.get(symbol.split("/")[0], symbol))
    # Search in the issuer's language; instrument codes alone attract quote/SEO pages.
    query = f'"{subject}" when:7d'
    if market == "USStock":
        query = f'("{subject}" OR {symbol}) when:7d'
    locale = {"CNStock": ("zh-CN", "CN", "CN:zh-Hans"),
              "HKStock": ("zh-HK", "HK", "HK:zh-Hant")}.get(market, ("en-US", "US", "US:en"))
    response = http.get("https://news.google.com/rss/search", params={
        "q": query, "hl": locale[0], "gl": locale[1], "ceid": locale[2],
    }, timeout=timeout)
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError("News feed exceeds size limit")
    root = ElementTree.fromstring(response.content)
    if root.find("channel") is None:
        raise ValueError("Not an RSS news response")
    rows = []
    for node in root.findall("./channel/item")[:50]:
        title = node.findtext("title") or ""
        if not attributable(title, terms):
            continue
        if re.search(r"stock price,|stock price forecast|should you buy|price prediction|quote and history|live price,|price history|\$[A-Z]+.*(?:stake|stock bought|stock purchased|holdings|shares sold)|(?:LLC|Inc\.) (?:acquires|sells) [\d,]+ shares", title, re.I):
            continue
        rows.append(_item(title, published_at(node.findtext("pubDate")), node.findtext("link"),
                          node.findtext("source") or "Google News RSS"))
    return rows


def fetch_disclosures(market, symbol, *, http=requests, timeout=6, now=None):
    now = now or datetime.now(timezone.utc)
    code = "".join(c for c in symbol.split(".")[0] if c.isdigit())
    code = code.zfill(5 if market == "HKStock" else 6)
    response = http.post("https://www.cninfo.com.cn/new/hisAnnouncement/query", data={
        "pageNum": 1, "pageSize": 20, "column": "hke" if market == "HKStock" else "szse",
        "tabName": "fulltext", "stock": "", "searchkey": code, "isHLtitle": "false",
        "seDate": f"{(now - timedelta(days=30)):%Y-%m-%d}~{now:%Y-%m-%d}",
    }, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if "totalAnnouncement" not in payload:
        raise ValueError("Invalid disclosure response")
    rows = []
    for item in payload.get("announcements") or []:
        if str(item.get("secCode") or "").zfill(len(code)) != code:
            continue
        path = str(item.get("adjunctUrl") or "")
        if not re.fullmatch(r"finalpage/\d{4}-\d{2}-\d{2}/\d+\.PDF", path, re.I):
            continue
        rows.append(_item(item.get("announcementTitle"), published_at(item.get("announcementTime")),
                          "https://static.cninfo.com.cn/" + path, "CNINFO", kind="issuer_disclosure"))
    return rows


def read_disclosure(item, *, http=requests, timeout=5):
    """Read a bounded excerpt from the fixed disclosure host, never arbitrary URLs."""
    from pypdf import PdfReader

    if not re.fullmatch(r"https://static\.cninfo\.com\.cn/finalpage/\d{4}-\d{2}-\d{2}/\d+\.PDF", item["url"], re.I):
        raise ValueError("Unsupported disclosure URL")
    deadline = monotonic() + timeout
    with http.get(item["url"], timeout=timeout, stream=True, allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Disclosure redirect is not allowed")
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            if monotonic() > deadline:
                raise TimeoutError("Disclosure download budget exceeded")
            size += len(chunk)
            if size > 5_000_000:
                raise ValueError("Disclosure exceeds size limit")
            chunks.append(chunk)
    document = PdfReader(io.BytesIO(b"".join(chunks)))
    count = min(6, len(document.pages))
    text = clean_text("\n".join(document.pages[index].extract_text() or "" for index in range(count)))
    if len(text) < 80:
        raise ValueError("No usable disclosure text")
    letters = [char for char in text if char.isalpha()]
    unexpected = sum(not (char.isascii() or '\u3400' <= char <= '\u9fff' or '\uf900' <= char <= '\ufaff') for char in letters)
    if unexpected > max(5, len(letters) * 0.05):
        raise ValueError("Disclosure font encoding is not readable")
    return {**item, "summary": text[:6000], "evidence_depth": "document_excerpt",
            "excerpt_pages": count, "document_pages": len(document.pages), "excerpt_truncated": True}


def collect_asset_events(market, symbol, name=""):
    """Use existing cache infrastructure; failures have a short retry TTL."""
    cache_key = f"asset_events:v3:{market}:{symbol}:{name}"
    cached = get_cached(cache_key)
    if cached is not None:
        return cached
    started = monotonic()
    now = datetime.now(timezone.utc)
    jobs = {"news_rss": lambda: fetch_news_rss(market, symbol, name)}
    if market in {"CNStock", "HKStock"}:
        jobs["cninfo_disclosures"] = lambda: fetch_disclosures(market, symbol, now=now)
    pool = ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="asset-events")
    futures = {pool.submit(fn): key for key, fn in jobs.items()}
    done, pending = wait(futures, timeout=9)
    rows, providers = [], {}
    for future in done:
        key = futures[future]
        try:
            items = future.result()
            rows.extend(items)
            providers[key] = {"status": "available" if items else "checked_empty", "count": len(items)}
        except Exception as exc:
            # Status codes/types are safe to expose; upstream responses may contain secrets.
            providers[key] = {"status": "fetch_failed", "error": type(exc).__name__}
    for future in pending:
        future.cancel()
        providers[futures[future]] = {"status": "fetch_failed", "error": "timeout"}
    pool.shutdown(wait=False, cancel_futures=True)
    valid, seen = [], set()
    for row in sorted(rows, key=lambda x: x.get("datetime") or "", reverse=True):
        stamp = published_at(row.get("datetime"))
        if not stamp:
            continue
        date = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        days = 30 if row["event_kind"] == "issuer_disclosure" else 7
        if not now - timedelta(days=days) <= date <= now:
            continue
        identity = clean_text(row["headline"]).casefold()
        if identity in seen:
            continue
        seen.add(identity)
        valid.append(row)
    disclosures = [row for row in valid if row["event_kind"] == "issuer_disclosure"][:8]
    for index, row in enumerate(disclosures[:2]):
        remaining = 16 - (monotonic() - started)
        if remaining < 1:
            break
        try:
            disclosures[index] = read_disclosure(row, timeout=min(5, remaining))
        except Exception as exc:
            providers.setdefault("disclosure_text", {"status": "fetch_failed", "error": type(exc).__name__})
    news = [row for row in valid if row["event_kind"] != "issuer_disclosure"][:10]
    news = (disclosures[:2] + news)[:12]
    succeeded = any(p["status"] != "fetch_failed" for p in providers.values())
    status = "available" if news else "checked_empty" if succeeded else "fetch_failed"
    result = {"news": news, "disclosures": disclosures,
              "collection": {"status": status, "providers": providers,
                             "news_window_days": 7, "disclosure_window_days": 30,
                             "checked_at": now.isoformat().replace("+00:00", "Z")}}
    set_cached(cache_key, result, 600 if succeeded else 60)
    return result
