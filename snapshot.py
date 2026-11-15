"""Real-data snapshot committed to the repo (data/snapshot/), so a fresh deploy boots with genuine
NSE filings, EOD prices and Screener document links instead of simulated data.

  python snapshot.py export   # after a live ingest: DB -> data/snapshot/
  python snapshot.py load     # DB <- data/snapshot/   (done automatically when the DB is empty)
"""
import csv
import gzip
import json
import sys
from pathlib import Path

from db import conn

SNAP = Path(__file__).parent / "data" / "snapshot"


def export():
    SNAP.mkdir(parents=True, exist_ok=True)
    c = conn()
    with gzip.open(SNAP / "prices.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "date", "open", "high", "low", "close", "volume"])
        for r in c.execute("SELECT * FROM prices ORDER BY symbol, date"):
            w.writerow([r["symbol"], r["date"]] + [round(r[k], 4) if r[k] is not None else "" for k in
                                                   ("open", "high", "low", "close")] + [int(r["volume"] or 0)])
    anns = [dict(r) for r in c.execute("SELECT * FROM announcements WHERE source != 'DEMO'")]
    with gzip.open(SNAP / "announcements.json.gz", "wt") as f:
        json.dump(anns, f)
    docs = [dict(r) for r in c.execute("SELECT * FROM documents")]
    (SNAP / "documents.json").write_text(json.dumps(docs, indent=0))
    print(f"exported {len(anns)} filings, {len(docs)} document links")


def load(force=False):
    if not SNAP.exists():
        return False
    c = conn()
    if not force and c.execute("SELECT 1 FROM announcements WHERE source != 'DEMO' LIMIT 1").fetchone():
        return False
    p = SNAP / "prices.csv.gz"
    if p.exists():
        with gzip.open(p, "rt") as f:
            rows = [(r["symbol"], r["date"], *(float(r[k]) if r[k] else None for k in
                                                ("open", "high", "low", "close")), float(r["volume"] or 0))
                    for r in csv.DictReader(f)]
        c.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", rows)
    a = SNAP / "announcements.json.gz"
    if a.exists():
        with gzip.open(a, "rt") as f:
            anns = json.load(f)
        c.executemany("INSERT OR REPLACE INTO announcements VALUES "
                      "(:id,:symbol,:source,:ts,:category,:subject,:detail,:attachment_url,:raw)", anns)
    d = SNAP / "documents.json"
    if d.exists():
        c.executemany("INSERT OR REPLACE INTO documents VALUES (:symbol,:kind,:label,:url)",
                      json.loads(d.read_text()))
    c.commit()
    return True


if __name__ == "__main__":
    (export if sys.argv[1:] == ["export"] else lambda: print("loaded" if load(force=True) else "nothing"))()
