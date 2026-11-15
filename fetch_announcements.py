"""Corporate announcements. NSE first (needs cookie warm-up), BSE as fallback.
Every raw response is cached to disk so the demo survives a blocked source."""
import json, time, requests
from datetime import datetime, timedelta
from config import HEADERS, CACHE, PORTFOLIO
from db import conn

def _save_raw(name, data):
    (CACHE / f"{name}.json").write_text(json.dumps(data))

# ---------- NSE ----------
def _nse_session():
    s = requests.Session()
    s.headers.update({**HEADERS, "Accept": "application/json, text/plain, */*",
                      "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"})
    s.get("https://www.nseindia.com", timeout=15)  # sets cookies
    return s

def nse_announcements(sess, symbol, days=30):
    to, frm = datetime.now(), datetime.now() - timedelta(days=days)
    r = sess.get("https://www.nseindia.com/api/corporate-announcements",
                 params={"index": "equities", "symbol": symbol,
                         "from_date": frm.strftime("%d-%m-%Y"), "to_date": to.strftime("%d-%m-%Y")},
                 timeout=20)
    r.raise_for_status()
    data = r.json()
    _save_raw(f"nse_ann_{symbol}", data)
    return [dict(id=f"nse:{d.get('seq_id') or d.get('an_dt')+d.get('attchmntText','')[:20]}",
                 symbol=symbol, source="NSE", ts=d.get("an_dt"), category=d.get("desc"),
                 subject=d.get("desc"), detail=d.get("attchmntText"),
                 attachment_url=d.get("attchmntFile"), raw=json.dumps(d)) for d in data]

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
        c = conn()
        c.executemany("""INSERT OR REPLACE INTO announcements
            VALUES (:id,:symbol,:source,:ts,:category,:subject,:detail,:attachment_url,:raw)""", rows)
        c.commit()
    elif not errors:
        errors.append("No announcements returned for this period")
    return {"count": len(rows), "source": source, "warnings": errors}

def fetch_all(portfolio=PORTFOLIO, days=30):
    c = conn()
    try:
        sess = _nse_session()
    except Exception as e:
        print(f"[ann] NSE warm-up failed: {e}"); sess = None
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
        c.executemany("""INSERT OR REPLACE INTO announcements
            VALUES (:id,:symbol,:source,:ts,:category,:subject,:detail,:attachment_url,:raw)""", rows)
        c.commit()
        print(f"[ann] {sym}: {len(rows)} via {src}")
        time.sleep(1)  # be polite

if __name__ == "__main__":
    fetch_all()
