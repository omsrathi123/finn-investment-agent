"""Annual-report + concall-transcript links from screener.in for any NSE symbol (shares the page cache)."""
from db import conn
from screener import company, doc_kind as _classify  # noqa: F401  (kept for older imports)


def get_doc_links(symbol):
    return [(symbol, kind, label, url) for kind, label, url in company(symbol)["documents"]]


def ensure_docs(symbol):
    """Fetch document links on demand so research works on a ticker nobody pre-loaded."""
    c = conn()
    if not c.execute("SELECT 1 FROM documents WHERE symbol=? LIMIT 1", (symbol,)).fetchone():
        rows = get_doc_links(symbol)
        c.executemany("INSERT OR REPLACE INTO documents VALUES (?,?,?,?)", rows)
        c.commit()


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
