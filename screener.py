"""screener.in company page -> documents (annual reports, transcripts) + standardised multi-year tables.

One HTTP request per company per day (HTML cached on disk). Tables are parsed deterministically,
so the financial/trajectory/balance-sheet/cash sections of research cost zero model tokens.
Works for any NSE symbol screener covers.
"""
import re
import time

import requests
from bs4 import BeautifulSoup

from config import CACHE, HEADERS

TTL = 24 * 3600
SECTIONS = ("quarters", "profit-loss", "balance-sheet", "cash-flow", "ratios", "shareholding")
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _cache(symbol, variant):
    d = CACHE / "screener"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{symbol}_{variant or 'standalone'}.html"


def page(symbol, refresh=False):
    """Return (html, variant). Prefers the consolidated view when it carries data."""
    symbol = symbol.upper()
    last_status = None
    for variant in ("consolidated", ""):
        f = _cache(symbol, variant)
        if f.exists() and not refresh and time.time() - f.stat().st_mtime < TTL:
            html = f.read_text(encoding="utf-8", errors="ignore")
        else:
            url = f"https://www.screener.in/company/{symbol}/{variant + '/' if variant else ''}"
            try:
                r = requests.get(url, headers=HEADERS, timeout=20)
            except requests.RequestException as e:
                if f.exists():  # stale cache beats nothing
                    html = f.read_text(encoding="utf-8", errors="ignore")
                    if 'id="profit-loss"' in html:
                        return html, variant or "standalone"
                raise RuntimeError("Couldn't reach screener.in for the financial statements; try again shortly") from e
            last_status = r.status_code
            if r.status_code != 200:
                continue
            html = r.text
            f.write_text(html, encoding="utf-8")
        if 'id="profit-loss"' in html and _has_numbers(html):
            return html, variant or "standalone"
    raise RuntimeError(f"Screener.in has no financials page for {symbol} (HTTP {last_status})")


def _has_numbers(html):
    sec = BeautifulSoup(html, "lxml").find(id="profit-loss")
    return bool(sec and sec.find("td", string=re.compile(r"\d")))


def _num(text):
    t = (text or "").replace(",", "").replace("₹", "").replace("%", "").replace("\xa0", " ").strip()
    if t in ("", "-", "--"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _label(td):
    t = td.get_text(" ", strip=True).replace("\xa0", " ")
    return re.sub(r"\s*\+\s*$", "", t).strip()


def parse_table(section):
    table = section.find("table") if section else None
    if not table:
        return {"columns": [], "rows": {}}
    head = table.find("thead")
    cols = [th.get_text(" ", strip=True) for th in (head.find_all("th") if head else [])][1:]
    rows = {}
    for tr in (table.find("tbody") or table).find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        lab = _label(tds[0])
        if not lab or lab.lower().startswith("raw pdf"):
            continue
        rows[lab] = [_num(td.get_text()) for td in tds[1:]]
    return {"columns": cols, "rows": rows}


def parse(html):
    soup = BeautifulSoup(html, "lxml")
    out = {"tables": {s: parse_table(soup.find(id=s)) for s in SECTIONS}}
    ratios = {}
    for li in soup.select("#top-ratios li"):
        n, v = li.select_one(".name"), li.select_one(".value")
        if n and v:
            nums = [_num(x.get_text()) for x in v.select(".number")]
            ratios[n.get_text(" ", strip=True)] = nums[0] if len(nums) == 1 else nums
    out["top_ratios"] = ratios
    name = soup.select_one("h1")
    out["name"] = name.get_text(" ", strip=True) if name else None
    about = soup.select_one(".company-profile .about") or soup.select_one(".about")
    out["about"] = about.get_text(" ", strip=True)[:800] if about else None
    docs = []
    sec = soup.find(id="documents")
    if sec:
        for a in sec.find_all("a", href=True):
            h3 = a.find_previous("h3")
            kind = doc_kind(h3.get_text(" ", strip=True) if h3 else "", a.get_text(" ", strip=True))
            if kind:
                docs.append((kind, " ".join(a.get_text().split()), a["href"]))
    out["documents"] = docs
    return out


def doc_kind(heading, label):
    h, l = heading.lower(), label.lower()
    if "annual" in h:
        return "annual_report"
    if "concall" in h or "transcript" in l:
        return "transcript" if "transcript" in l else None
    return None


def company(symbol, refresh=False):
    html, variant = page(symbol, refresh)
    data = parse(html)
    data["variant"] = variant
    data["symbol"] = symbol.upper()
    data["url"] = f"https://www.screener.in/company/{symbol.upper()}/{'consolidated/' if variant == 'consolidated' else ''}"
    return data


# ---------- helpers used by results + research ----------
ALIASES = {
    "revenue": ["Sales", "Revenue", "Revenue from Operations", "Total Revenue"],
    "expenses": ["Expenses"],
    "op_profit": ["Operating Profit", "Financing Profit"],
    "opm": ["OPM %", "Financing Margin %"],
    "other_income": ["Other Income"],
    "interest": ["Interest"],
    "depreciation": ["Depreciation"],
    "pbt": ["Profit before tax"],
    "tax_pct": ["Tax %"],
    "pat": ["Net Profit", "Profit after tax"],
    "eps": ["EPS in Rs"],
    "equity_capital": ["Equity Capital"],
    "reserves": ["Reserves"],
    "borrowings": ["Borrowings", "Borrowing"],
    "deposits": ["Deposits"],
    "other_liabilities": ["Other Liabilities"],
    "total": ["Total Liabilities", "Total Assets"],
    "fixed_assets": ["Fixed Assets"],
    "cwip": ["CWIP"],
    "investments": ["Investments"],
    "cfo": ["Cash from Operating Activity"],
    "cfi": ["Cash from Investing Activity"],
    "cff": ["Cash from Financing Activity"],
    "debtor_days": ["Debtor Days"],
    "inventory_days": ["Inventory Days"],
    "payable_days": ["Days Payable"],
    "ccc": ["Cash Conversion Cycle"],
    "wc_days": ["Working Capital Days"],
    "roce": ["ROCE %", "ROE %"],
    "promoters": ["Promoters"],
    "fii": ["FIIs"],
    "dii": ["DIIs"],
}


def series(table, key):
    rows = table.get("rows", {})
    for name in ALIASES.get(key, [key]):
        if name in rows:
            return rows[name]
    return None


def col_to_period(col):
    """'Sep 2026' -> (2026, 9); 'TTM' -> None."""
    m = re.match(r"([A-Z][a-z]{2})\s+(\d{4})", col or "")
    return (int(m.group(2)), MONTHS[m.group(1)]) if m else None


def quarter_numbers(data, period_end=None):
    """Headline numbers for one quarter + its QoQ and YoY comparators from the quarterly table.
    period_end: (year, month); default = latest column."""
    t = data["tables"]["quarters"]
    cols = t["columns"]
    periods = [col_to_period(c) for c in cols]
    if not cols:
        return None
    idx = len(cols) - 1
    if period_end and period_end in periods:
        idx = periods.index(period_end)
    elif period_end:
        return None

    def at(key, i):
        s = series(t, key)
        return s[i] if s and 0 <= i < len(s) else None

    def ebitda(i):
        op = at("op_profit", i)
        return op  # screener's Operating Profit = Sales - Expenses, i.e. EBITDA before other income

    yoy = periods.index((periods[idx][0] - 1, periods[idx][1])) if periods[idx] and (periods[idx][0] - 1, periods[idx][1]) in periods else None
    out = {"quarter_col": cols[idx], "unit": "Rs crore", "metrics": {}}
    for key, fn in (("revenue", lambda i: at("revenue", i)), ("ebitda", ebitda),
                    ("pbt", lambda i: at("pbt", i)), ("pat", lambda i: at("pat", i))):
        out["metrics"][key] = {"cur": fn(idx), "prev_q": fn(idx - 1) if idx > 0 else None,
                               "yoy": fn(yoy) if yoy is not None else None}
    return out


def filing_quarter(ts_dt):
    """A results filing made in Oct/Nov reports the quarter ending Sep, etc."""
    y, m = ts_dt.year, ts_dt.month
    end_month = {1: 12, 2: 12, 3: 12, 4: 3, 5: 3, 6: 3, 7: 6, 8: 6, 9: 6, 10: 9, 11: 9, 12: 9}[m]
    return (y - 1 if m <= 3 and end_month == 12 else y, end_month)
