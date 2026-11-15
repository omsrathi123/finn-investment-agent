"""Portfolio stock snapshots: local EOD history, Yahoo metrics/news, and exchange filings."""
import json
import math
import re
import threading
import time
from datetime import datetime

import yfinance as yf

from classify import classify
from config import CACHE, PORTFOLIO
from db import conn
from fetch_announcements import fetch_symbol
from fetch_prices import fetch_prices

YAHOO_CACHE_SECONDS = 900
REFRESH_COOLDOWN_SECONDS = 900
_refresh_lock = threading.Lock()


def _number(value, scale=1):
    try:
        result = float(value) * scale
    except (TypeError, ValueError):
        return None
    return round(result, 2) if math.isfinite(result) else None


def _metric(info, key, scale=1):
    return _number(info.get(key), scale)


def _published_at(content):
    value = content.get("pubDate") or content.get("publishedAt")
    if value:
        return value
    timestamp = content.get("providerPublishTime") or content.get("publish_time")
    try:
        return datetime.fromtimestamp(float(timestamp)).isoformat(sep=" ", timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _url(value):
    if isinstance(value, dict):
        value = value.get("url")
    return value if isinstance(value, str) and value.startswith(("https://", "http://")) else None


def _normalize_news(entries):
    news = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        content = entry.get("content") if isinstance(entry.get("content"), dict) else entry
        title = content.get("title")
        link = (
            _url(content.get("canonicalUrl"))
            or _url(content.get("clickThroughUrl"))
            or _url(content.get("link"))
            or _url(content.get("url"))
        )
        if not title or not link:
            continue
        provider = content.get("provider") or {}
        if not isinstance(provider, dict):
            provider = {}
        news.append({
            "title": str(title)[:240],
            "url": link,
            "publisher": provider.get("displayName") or entry.get("publisher") or "Yahoo Finance",
            "published_at": _published_at(content),
        })
    return news[:8]


def _yahoo_snapshot(symbol, force=False):
    cache_dir = CACHE / "stocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{symbol}.json"
    previous = None
    if cache_file.exists():
        try:
            previous = json.loads(cache_file.read_text(encoding="utf-8"))
            if not force and time.time() - previous.get("fetched_epoch", 0) < YAHOO_CACHE_SECONDS:
                return previous
        except (OSError, ValueError, TypeError):
            previous = None

    warnings = []
    ticker = yf.Ticker(f"{symbol}.NS")
    info = {}
    raw_news = []
    try:
        info = ticker.info or {}
    except Exception as exc:
        warnings.append(f"Yahoo Finance profile unavailable ({type(exc).__name__})")
    if not info:
        warnings.append("Yahoo Finance returned no profile data")
    try:
        raw_news = ticker.news or []
    except Exception as exc:
        warnings.append(f"Yahoo Finance news unavailable ({type(exc).__name__})")
    if not raw_news:
        warnings.append("Yahoo Finance returned no related-news links")

    metrics = {
        "market_cap_crore": _metric(info, "marketCap", 1 / 10_000_000),
        "trailing_pe": _metric(info, "trailingPE"),
        "forward_pe": _metric(info, "forwardPE"),
        "price_to_book": _metric(info, "priceToBook"),
        "ev_to_ebitda": _metric(info, "enterpriseToEbitda"),
        "ebitda_crore": _metric(info, "ebitda", 1 / 10_000_000),
        "revenue_crore": _metric(info, "totalRevenue", 1 / 10_000_000),
        "ebitda_margin_pct": _metric(info, "ebitdaMargins", 100),
        "net_margin_pct": _metric(info, "profitMargins", 100),
        "return_on_equity_pct": _metric(info, "returnOnEquity", 100),
        "debt_to_equity_pct": _metric(info, "debtToEquity"),
        "dividend_yield_pct": _metric(info, "dividendYield", 100),
        "revenue_growth_pct": _metric(info, "revenueGrowth", 100),
        "earnings_growth_pct": _metric(info, "earningsGrowth", 100),
        "free_cash_flow_crore": _metric(info, "freeCashflow", 1 / 10_000_000),
        "current_ratio": _metric(info, "currentRatio"),
        "beta": _metric(info, "beta"),
    }
    profile = {
        "name": info.get("longName") or info.get("shortName") or symbol,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "currency": info.get("currency") or "INR",
        "summary": (info.get("longBusinessSummary") or "")[:900],
        "website": _url(info.get("website")),
    }
    result = {
        "fetched_epoch": time.time(),
        "fetched_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "profile": profile,
        "metrics": metrics,
        "news": _normalize_news(raw_news),
        "warnings": warnings,
    }
    if info or raw_news:
        cache_file.write_text(json.dumps(result), encoding="utf-8")
    elif previous:
        previous["warnings"] = warnings + ["Showing the last cached Yahoo Finance snapshot"]
        return previous
    return result


def _parse_time(value):
    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%d-%b-%Y %H:%M:%S",
        "%d-%b-%Y %H:%M:%S.%f",
    ):
        try:
            return datetime.strptime((value or "")[:26].strip(), pattern)
        except ValueError:
            pass
    return None


def _return_percent(series, offset):
    if len(series) < 2 or len(series) <= offset:
        return None
    prior, latest = series[-offset - 1]["close"], series[-1]["close"]
    if not prior:
        return None
    return round((latest / prior - 1) * 100, 2)


def get_stock_detail(symbol, force_yahoo=False):
    symbol = symbol.upper()
    if not re.fullmatch(r"[A-Z0-9&.-]{1,20}", symbol):
        raise ValueError("Use a valid NSE ticker (letters, digits, dot, ampersand, or hyphen)")

    yahoo = _yahoo_snapshot(symbol, force=force_yahoo)
    c = conn()
    prices = c.execute(
        "SELECT date,close,volume FROM prices WHERE symbol=? ORDER BY date DESC LIMIT 180",
        (symbol,),
    ).fetchall()
    series = [
        {"date": row["date"], "close": round(row["close"], 2), "volume": row["volume"]}
        for row in reversed(prices)
    ]
    current_price = series[-1]["close"] if series else None
    previous_price = series[-2]["close"] if len(series) > 1 else None
    change_pct = (
        round((current_price / previous_price - 1) * 100, 2)
        if current_price is not None and previous_price
        else None
    )

    filing_rows = c.execute(
        "SELECT id,source,ts,category,subject,detail,attachment_url FROM announcements WHERE symbol=?",
        (symbol,),
    ).fetchall()
    filings = []
    for row in filing_rows:
        tier, kind = classify(row["category"], row["subject"], row["detail"])
        filings.append({
            "id": row["id"],
            "source": row["source"],
            "ts": row["ts"],
            "category": row["category"],
            "title": row["subject"] or row["category"] or "Company filing",
            "detail": (row["detail"] or "")[:500],
            "url": row["attachment_url"],
            "tier": tier,
            "kind": kind,
            "_sort": _parse_time(row["ts"]),
        })
    filings.sort(key=lambda item: item["_sort"] or datetime.min, reverse=True)
    filings = filings[:12]
    for filing in filings:
        filing.pop("_sort")

    metrics = yahoo["metrics"]
    returns = {
        "1m_pct": _return_percent(series, 21),
        "6m_pct": _return_percent(series, 125),
    }
    analysis = []
    if returns["1m_pct"] is not None:
        analysis.append(f"Price changed {returns['1m_pct']:+.1f}% over the last month of available sessions.")
    if returns["6m_pct"] is not None:
        analysis.append(f"Price changed {returns['6m_pct']:+.1f}% across the available six-month history.")
    if metrics["trailing_pe"] is not None:
        analysis.append(f"Trailing P/E is {metrics['trailing_pe']:.1f}×; this is a valuation snapshot, not a buy/sell signal.")
    if metrics["price_to_book"] is not None:
        analysis.append(f"Price-to-book is {metrics['price_to_book']:.1f}×.")
    if metrics["ebitda_margin_pct"] is not None:
        analysis.append(f"Reported EBITDA margin is {metrics['ebitda_margin_pct']:.1f}%.")
    if metrics["debt_to_equity_pct"] is not None:
        analysis.append(f"Debt-to-equity is {metrics['debt_to_equity_pct']:.1f}%; compare within the company's sector.")
    if filings:
        analysis.append(f"{len(filings)} exchange filings are available below; open the original source before drawing conclusions.")
    if not analysis:
        analysis.append("Not enough market history or fundamentals are available to generate observations yet.")

    return {
        "symbol": symbol,
        "profile": yahoo["profile"],
        "metrics": metrics,
        "price": current_price,
        "change_pct": change_pct,
        "returns": returns,
        "price_as_of": series[-1]["date"] if series else None,
        "chart": series,
        "analysis": analysis,
        "filings": filings,
        "news": yahoo["news"],
        "yahoo_as_of": yahoo.get("fetched_at"),
        "warnings": yahoo["warnings"],
        "demo_data": c.execute(
            "SELECT EXISTS(SELECT 1 FROM announcements WHERE source='DEMO')"
        ).fetchone()[0],
    }


def refresh_stock_detail(symbol):
    symbol = symbol.upper()
    if not re.fullmatch(r"[A-Z0-9&.-]{1,20}", symbol):
        raise ValueError("Use a valid NSE ticker (letters, digits, dot, ampersand, or hyphen)")
    if not _refresh_lock.acquire(blocking=False):
        raise RuntimeError("A portfolio refresh is already running")
    try:
        refresh_dir = CACHE / "refresh"
        refresh_dir.mkdir(parents=True, exist_ok=True)
        stamp_file = refresh_dir / f"{symbol}.json"
        now = time.time()
        if stamp_file.exists():
            try:
                previous = json.loads(stamp_file.read_text(encoding="utf-8"))
                elapsed = now - previous.get("refreshed_epoch", 0)
                if elapsed < REFRESH_COOLDOWN_SECONDS:
                    detail = get_stock_detail(symbol)
                    detail["refresh"] = {
                        "status": "cooldown",
                        "retry_after_seconds": int(REFRESH_COOLDOWN_SECONDS - elapsed),
                    }
                    return detail
            except (OSError, ValueError, TypeError):
                pass

        warnings = fetch_prices([symbol])
        announcements = fetch_symbol(symbol, PORTFOLIO.get(symbol), days=30)
        warnings.extend(announcements["warnings"])
        detail = get_stock_detail(symbol, force_yahoo=True)
        detail["warnings"].extend(warnings)
        detail["refresh"] = {
            "status": "completed",
            "announcements_added_or_updated": announcements["count"],
            "announcement_source": announcements["source"],
        }
        stamp_file.write_text(json.dumps({"refreshed_epoch": now}), encoding="utf-8")
        return detail
    finally:
        _refresh_lock.release()
