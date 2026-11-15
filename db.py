import sqlite3
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
  symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
  PRIMARY KEY (symbol, date));
CREATE TABLE IF NOT EXISTS announcements (
  id TEXT PRIMARY KEY, symbol TEXT, source TEXT, ts TEXT,
  category TEXT, subject TEXT, detail TEXT, attachment_url TEXT, raw TEXT);
CREATE INDEX IF NOT EXISTS ix_ann_sym_ts ON announcements(symbol, ts);
CREATE TABLE IF NOT EXISTS documents (
  symbol TEXT, kind TEXT, label TEXT, url TEXT,
  PRIMARY KEY (symbol, kind, url));
"""

def conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c
