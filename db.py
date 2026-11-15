import sqlite3
import threading
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
-- user-chosen alert rules (single-user prototype: one JSON row)
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
-- every alert that has fired, so a push is only sent once and the inbox keeps read state
CREATE TABLE IF NOT EXISTS pushed (
  id TEXT PRIMARY KEY, kind TEXT, symbol TEXT, ts TEXT, title TEXT, body TEXT,
  score INTEGER, ref TEXT, fired_at TEXT, seen INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_pushed_fired ON pushed(fired_at);
-- last successful live poll / EOD run, per job
CREATE TABLE IF NOT EXISTS jobs (name TEXT PRIMARY KEY, last_run TEXT, last_ok TEXT, note TEXT);
"""

_init_lock = threading.Lock()


def conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    c.row_factory = sqlite3.Row
    with _init_lock:
        c.executescript(SCHEMA)
    return c
