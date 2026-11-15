"""Corporate announcements. NSE first (needs cookie warm-up), BSE as fallback.

Two NSE modes:
  * per-symbol history  -> /api/corporate-announcements?symbol=X&from_date&to_date  (backfill, any ticker)
  * live, market-wide   -> /api/corporate-announcements?index=equities              (one call for the whole
                           portfolio; this is what the push poller uses), with NSE's public RSS feed as a
                           cookie-free fallback.
Every raw response is cached to disk so the demo survives a blocked source.
"""
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import requests

from config import HEADERS, CACHE, PORTFOLIO
from db import conn

NSE = "https://www.nseindia.com"
NSE_RSS = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
INSERT = """INSERT OR REPLACE INTO announcements
            VALUES (:id,:symbol,:source,:ts,:category,:subject,:detail,:attachment_url,:raw)"""


def _save_raw(name, data):
    try:
        (CACHE / f"{name}.json").write_text(json.dumps(data))
    except OSError:
        pass


# ---------- NSE ----------
def _nse_session():
    s = requests.Session()
    s.headers.update({**HEADERS, "Accept": "application/json, text/plain, */*",
                      "Referer": f"{NSE}/companies-listing/corporate-filings-announcements"})
    s.get(NSE, timeout=15)  # sets cookies
    return s


def nse_row(d, symbol=None):
    seq = d.get("seq_id") or ((d.get("an_dt") or "") + (d.get("attchmntText") or "")[:20])
    return dict(id=f"nse:{seq}", symbol=symbol or d.get("symbol"), source="NSE", ts=d.get("an_dt"),
                category=d.get("desc"), subject=d.get("desc"), detail=d.get("attchmntText"),
                attachment_url=d.get("attchmntFile"), raw=json.dumps(d))


def nse_announcements(sess, symbol, days=30):
    to, frm = datetime.now(), datetime.now() - timedelta(days=days)
    r = sess.get(f"{NSE}/api/corporate-announcements",
                 params={"index": "equities", "symbol": symbol,
                         "from_date": frm.strftime("%d-%m-%Y"), "to_date": to.strftime("%d-%m-%Y")},
                 timeout=20)
    r.raise_for_status()
    data = r.json()
    _save_raw(f"nse_ann_{symbol}", data)
    return [nse_row(d, symbol) for d in data]


def nse_live(sess=None, symbols=None):
    """Latest market-wide announcements in one request, filtered to `symbols`."""
    sess = sess or _nse_session()
    r = sess.get(f"{NSE}/api/corporate-announcements", params={"index": "equities"}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):  # NSE has shipped both shapes
        data = data.get("data") or data.get("rows") or []
    _save_raw("nse_live_latest", data[:300])
    wanted = {s.upper() for s in symbols} if symbols else None
    return [nse_row(d) for d in data if d.get("symbol") and (wanted is None or d["symbol"].upper() in wanted)]


def _company_names():
    """symbol <- lower-cased company name, learnt from earlier NSE payloads (for the RSS fallback)."""
    names = {}
    for r in conn().execute("SELECT symbol, raw FROM announcements WHERE source='NSE'"):
        try:
            n = json.loads(r["raw"] or "{}").get("sm_name")
        except ValueError:
            n = None
        if n:
            names[re.sub(r"\W+", " ", n.lower()).strip()] = r["symbol"]
    return names


def nse_rss(symbols=None):
    """Cookie-free fallback: NSE's public 'online announcements' RSS feed, mapped to symbols by name."""
    r = requests.get(NSE_RSS, headers=HEADERS, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    names = _company_names()
    wanted = {s.upper() for s in symbols} if symbols else None
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = (it.findtext("description") or "").strip()
        link = (it.findtext("link") or "").strip()
        key = re.sub(r"\W+", " ", title.lower()).strip()
        sym = names.get(key) or next((s for n, s in names.items() if n and (n in key or key in n)), None)
        if not sym or (wanted and sym not in wanted):
            continue
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate")).strftime("%d-%b-%Y %H:%M:%S")
        except (TypeError, ValueError):
            ts = datetime.now().strftime("%d-%b-%Y %H:%M:%S")
        subject = re.sub(r"^\|?\s*subject\s*:\s*", "", desc.split("|")[1] if "|" in desc else desc, flags=re.I)
        out.append(dict(id="rss:" + hashlib.sha1(link.encode()).hexdigest()[:16], symbol=sym, source="NSE",
                        ts=ts, category=subject[:80] or "Announcement", subject=subject[:200],
                        detail=desc[:1000], attachment_url=link or None,
                        raw=json.dumps({"rss": True, "title": title, "description": desc})))
    return out


# ---------- BSE ----------
def bse_announcements(symbol, bse_code, days=30):
    to, frm = datetime.now(), datetime.now() - timedelta(days=days)
    r = requests.get(
        "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w",
        params={"pageno": 1, "strCat": "-1", "strPrevDate": frm.strftime("%Y%m%d"),
                "strScrip": bse_code, "strSearch": "P", "strToDate": to.strftime("%Y%m%d"),
                "strType": "C", "subcategory": "-1"},
        headers={**HEADERS, "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"},
        timeout=20)
    r.raise_for_status()
    data = r.json()
    _save_raw(f"bse_ann_{symbol}", data)
    out = []
    for d in data.get("Table", []):
        att = d.get("ATTACHMENTNAME")
        out.append(dict(id=f"bse:{d['NEWSID']}", symbol=symbol, source="BSE", ts=d.get("NEWS_DT"),
                        category=d.get("SUBCATNAME") or d.get("CATEGORYNAME"),
                        subject=d.get("NEWSSUB"), detail=d.get("HEADLINE"),
                        attachment_url=f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}" if att else None,
                        raw=json.dumps(d)))
    return out


# ---------- storage ----------
def upsert(rows):
    """Store rows; return the ids that were not in the database before (= genuinely new filings)."""
    if not rows:
        return []
    c = conn()
    ids = [r["id"] for r in rows]
    have = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        have |= {r[0] for r in c.execute(
            f"SELECT id FROM announcements WHERE id IN ({','.join('?' * len(chunk))})", chunk)}
    c.executemany(INSERT, rows)
    c.commit()
    return [i for i in ids if i not in have]


def load_cached(directory=None):
    """Import every cached NSE payload (nse_ann_*.json) — the offline/demo path uses real filings."""
    directory = directory or CACHE
    n = 0
    for f in sorted(directory.glob("nse_ann_*.json")):
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        sym = f.stem.replace("nse_ann_", "")
        n += len(upsert([nse_row(d, sym) for d in data if isinstance(d, dict)]))
    return n


def fetch_symbol(symbol, bse_code=None, days=30):
    errors, rows, source = [], [], None
    try:
        sess = _nse_session()
        rows, source = nse_announcements(sess, symbol, days), "NSE"
    except Exception as e:
        errors.append(f"NSE failed ({type(e).__name__})")
    if not rows and bse_code:
        try:
            rows, source = bse_announcements(symbol, bse_code, days), "BSE"
        except Exception as e:
            errors.append(f"BSE failed ({type(e).__name__})")
    if rows:
        upsert(rows)
    elif not errors:
        errors.append("No announcements returned for this period")
    return {"count": len(rows), "source": source, "warnings": errors}


def fetch_live(symbols=None):
    """One poll of the live feed for the portfolio. Returns {"new": [ids], "source", "warnings"}."""
    symbols = list(symbols or PORTFOLIO)
    warnings = []
    for name, fn in (("NSE live", lambda: nse_live(None, symbols)), ("NSE RSS", lambda: nse_rss(symbols))):
        try:
            rows = fn()
            return {"new": upsert(rows), "seen": len(rows), "source": name, "warnings": warnings}
        except Exception as e:
            warnings.append(f"{name} failed ({type(e).__name__})")
    return {"new": [], "seen": 0, "source": None, "warnings": warnings}


def fetch_all(portfolio=PORTFOLIO, days=30):
    try:
        sess = _nse_session()
    except Exception as e:
        print(f"[ann] NSE warm-up failed: {e}")
        sess = None
    for sym, bse in portfolio.items():
        rows, src = [], None
        if sess:
            try:
                rows, src = nse_announcements(sess, sym, days), "NSE"
            except Exception as e:
                print(f"[ann] {sym}: NSE failed ({type(e).__name__}: {e})")
        if not rows and bse:
            try:
                rows, src = bse_announcements(sym, bse, days), "BSE"
            except Exception as e:
                print(f"[ann] {sym}: BSE failed ({type(e).__name__}: {e})")
        upsert(rows)
        print(f"[ann] {sym}: {len(rows)} via {src}")
        time.sleep(1)  # be polite


if __name__ == "__main__":
    fetch_all()
