"""Find annual-report + concall-transcript links on screener.in for any NSE symbol."""
import requests
from bs4 import BeautifulSoup
from config import HEADERS
from db import conn

def _classify(heading, label):
    h, l = heading.lower(), label.lower()
    if "annual" in h: return "annual_report"
    if "concall" in h or "transcript" in l: 
        return "transcript" if "transcript" in l else None
    return None  # ppt / notes / rec etc. ignored for now

def get_doc_links(symbol):
    for suffix in ("consolidated/", ""):
        r = requests.get(f"https://www.screener.in/company/{symbol}/{suffix}", headers=HEADERS, timeout=20)
        if r.status_code == 200 and 'id="documents"' in r.text:
            break
    else:
        raise RuntimeError(f"screener page / documents section not found for {symbol} (HTTP {r.status_code})")
    soup = BeautifulSoup(r.text, "lxml")
    sec = soup.find(id="documents")
    out = []
    for a in sec.find_all("a", href=True):
        h3 = a.find_previous("h3")
        kind = _classify(h3.get_text(" ", strip=True) if h3 else "", a.get_text(" ", strip=True))
        if kind:
            out.append((symbol, kind, " ".join(a.get_text().split()), a["href"]))
    return out

def fetch_docs(symbols):
    c = conn()
    for s in symbols:
        try:
            rows = get_doc_links(s)
            c.executemany("INSERT OR REPLACE INTO documents VALUES (?,?,?,?)", rows)
            c.commit()
            n_ar = sum(r[1] == "annual_report" for r in rows)
            n_tr = sum(r[1] == "transcript" for r in rows)
            print(f"[docs] {s}: {n_ar} annual reports, {n_tr} transcripts")
        except Exception as e:
            print(f"[docs] {s}: FAILED {e}")

if __name__ == "__main__":
    from config import PORTFOLIO, RESEARCH_EXTRA
    fetch_docs(list(PORTFOLIO) + RESEARCH_EXTRA)
