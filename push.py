"""Push, flavour 2: triggered alerts.

The investor decides what "unusual" means (rules); FINN watches and fires once per event.

  rules     categories to watch + min materiality, volume multiple, daily move %, lookback
  filings   any new filing whose category is watched and whose score clears the bar
  prices    any session where volume >= N x trailing average, or |close-to-close| >= X %
  pushed    table of everything already fired (so a notification is never repeated) + read state

The live loop (`tick`) is idempotent and cheap: one NSE request for the whole portfolio, a
throttled EOD price pull after market close, then a pure-SQL/python evaluation. It runs on a
background thread when the server is long-lived, and is also triggered by the app's own
polling, so it works on serverless hosts with no scheduler.
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta
from statistics import mean

from config import PORTFOLIO
from db import conn
from digest import filing_view, ist_now, parse_ts

DEFAULT_RULES = {
    "categories": ["results", "ma", "management_change", "litigation", "fund_raise", "pledging",
                   "credit_rating", "insider_trading", "capex", "bonus_split"],
    "min_score": 50,
    "vol_mult": 2.0,
    "move_pct": 4.0,
    "lookback": 20,
    "backfill_days": 7,
}
LIVE_INTERVAL = int(os.getenv("FINN_LIVE_INTERVAL", "300"))  # seconds between NSE live polls
_tick_lock = threading.Lock()


# ---------- rules ----------
def get_rules():
    row = conn().execute("SELECT value FROM settings WHERE key='rules'").fetchone()
    rules = dict(DEFAULT_RULES)
    if row:
        try:
            rules.update(json.loads(row["value"]))
        except ValueError:
            pass
    return rules


def save_rules(update):
    rules = get_rules()
    for k, v in (update or {}).items():
        if k not in DEFAULT_RULES:
            continue
        if k == "categories":
            rules[k] = [str(x) for x in v][:20]
        elif k in ("vol_mult", "move_pct"):
            rules[k] = max(0.5, min(50.0, float(v)))
        else:
            rules[k] = max(1, min(100, int(v)))
    c = conn()
    c.execute("INSERT OR REPLACE INTO settings VALUES ('rules', ?)", (json.dumps(rules),))
    c.commit()
    return rules


# ---------- detectors ----------
def filing_alerts(rules, since):
    out = []
    for r in conn().execute("SELECT * FROM announcements"):
        v = filing_view(r)
        t = parse_ts(r["ts"])
        if not t or t < since:
            continue
        if v["kind"] in rules["categories"] and v["score"] >= rules["min_score"]:
            out.append(dict(id=f"f:{v['id']}", kind="filing", symbol=v["symbol"], ts=v["iso"],
                            title=f"{v['label']}: {v['title']}", body=v["summary"] or v["why"],
                            score=v["score"], ref=v["id"], category=v["kind"], url=v["url"],
                            actions=v["actions"]))
    return out


def price_events(rules, sessions=10, symbols=None):
    """Every session in the last `sessions` where a user threshold was crossed."""
    c, out = conn(), []
    lb = int(rules["lookback"])
    syms = symbols or [r[0] for r in c.execute("SELECT DISTINCT symbol FROM prices")]
    for s in syms:
        rows = c.execute("SELECT date,close,volume FROM prices WHERE symbol=? ORDER BY date DESC LIMIT ?",
                         (s, sessions + lb + 1)).fetchall()[::-1]
        for i in range(max(6, len(rows) - sessions), len(rows)):
            hist = rows[max(0, i - lb):i]
            if len(hist) < 5:
                continue
            cur, prev = rows[i], rows[i - 1]
            avg = mean(h["volume"] or 0 for h in hist)
            vm = (cur["volume"] or 0) / avg if avg else 0
            mv = (cur["close"] / prev["close"] - 1) * 100 if prev["close"] else 0
            why = []
            if vm >= rules["vol_mult"]:
                why.append(f"Volume {vm:.1f}× its {len(hist)}-day average")
            if abs(mv) >= rules["move_pct"]:
                why.append(f"Price {mv:+.1f}% on the day")
            if abs(mv) >= 20:
                why.append("verify: a move this size can be a split/bonus or a data error")
            if why:
                out.append(dict(id=f"p:{s}:{cur['date']}", kind="price", symbol=s, ts=cur["date"],
                                title=f"{s} {mv:+.1f}%" + (f" on {vm:.1f}× volume" if vm >= rules["vol_mult"] else ""),
                                body=" · ".join(why), score=int(min(99, 40 + abs(mv) * 6 + max(0, vm - 1) * 8)),
                                ref=None, close=round(cur["close"], 2), move_pct=round(mv, 2),
                                vol_mult=round(vm, 2), why=why))
    return out


def _link_price_to_filings(alerts):
    """A move on the day of a filing is explained by it: say so on the card."""
    c = conn()
    for a in alerts:
        if a["kind"] != "price":
            continue
        d = parse_ts(a["ts"])
        best = None
        for r in c.execute("SELECT * FROM announcements WHERE symbol=?", (a["symbol"],)):
            t = parse_ts(r["ts"])
            if t and d - timedelta(days=1) <= t <= d + timedelta(hours=23, minutes=59):
                v = filing_view(r)
                if v["score"] >= 35 and (not best or v["score"] > best["score"]):
                    best = v
        if best:
            a["context"] = {"id": best["id"], "label": best["label"], "title": best["title"], "url": best["url"]}
            a["body"] += f" · same day as {best['label'].lower()} filing"


# ---------- evaluation + inbox ----------
def evaluate(rules=None, now=None):
    """Return all alerts matching current rules (with fired_at / seen) and record the new ones."""
    rules = rules or get_rules()
    now = now or ist_now()
    since = now - timedelta(days=int(rules["backfill_days"]))
    alerts = filing_alerts(rules, since) + price_events(rules)
    _link_price_to_filings(alerts)
    c = conn()
    known = {r["id"]: r for r in c.execute("SELECT id, fired_at, seen FROM pushed")}
    stamp = now.isoformat(sep=" ", timespec="seconds")
    new = []
    for a in alerts:
        if a["id"] in known:
            a["fired_at"], a["seen"] = known[a["id"]]["fired_at"], bool(known[a["id"]]["seen"])
        else:
            a["fired_at"], a["seen"] = stamp, False
            new.append(a)
    old = now - timedelta(hours=48)
    for a in new:  # on first run / rule change, older events are history, not news
        t = parse_ts(a["ts"])
        if t and t < old:
            a["seen"] = True
    c.executemany("INSERT OR IGNORE INTO pushed (id,kind,symbol,ts,title,body,score,ref,fired_at,seen) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)",
                  [(a["id"], a["kind"], a["symbol"], a["ts"], a["title"], a["body"], a["score"], a.get("ref"),
                    stamp, int(a["seen"])) for a in new])
    c.commit()
    alerts.sort(key=lambda a: (parse_ts(a["ts"]) or datetime.min), reverse=True)
    return alerts, [a for a in new if not a["seen"]]


def mark_seen(ids=None):
    c = conn()
    if ids:
        c.executemany("UPDATE pushed SET seen=1 WHERE id=?", [(i,) for i in ids])
    else:
        c.execute("UPDATE pushed SET seen=1")
    c.commit()


# ---------- the live loop ----------
def _job(name):
    r = conn().execute("SELECT * FROM jobs WHERE name=?", (name,)).fetchone()
    return dict(r) if r else {}


def _job_set(name, ok, note=""):
    c = conn()
    now = ist_now().isoformat(sep=" ", timespec="seconds")
    prev = _job(name)
    c.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?,?)",
              (name, now, now if ok else prev.get("last_ok"), note[:300]))
    c.commit()


def _due(name, seconds):
    last = parse_ts(_job(name).get("last_run"))
    return not last or (ist_now() - last).total_seconds() >= seconds


def _eod_due(now):
    """Prices are end-of-day: pull once per weekday after 16:00 IST (server clock assumed IST)."""
    if now.weekday() >= 5 or now.hour < 16:
        return False
    last = parse_ts(_job("eod").get("last_run"))
    return not last or last.date() < now.date() or last.hour < 16


def tick(force=False, live=True):
    """One pass of the push pipeline. Safe to call often; does network work only when due."""
    if not _tick_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        report = {"live": None, "eod": None}
        if live and (force or _due("live", LIVE_INTERVAL)):
            from fetch_announcements import fetch_live
            res = fetch_live(list(PORTFOLIO))
            _job_set("live", bool(res["source"]),
                     f"{res['source'] or 'no source'}: {res['seen']} portfolio filings, {len(res['new'])} new; "
                     + "; ".join(res["warnings"]))
            report["live"] = res
        now = ist_now()
        if live and _eod_due(now):
            try:
                from fetch_prices import fetch_prices
                errs = fetch_prices(list(PORTFOLIO), period="1mo")
                _job_set("eod", len(errs) < len(PORTFOLIO), f"{len(PORTFOLIO) - len(errs)} symbols updated")
                report["eod"] = {"errors": errs[:5]}
            except Exception as e:  # yfinance missing / blocked
                _job_set("eod", False, f"{type(e).__name__}: {e}")
        alerts, new = evaluate()
        report.update(new=new, total=len(alerts))
        return report
    finally:
        _tick_lock.release()


def status():
    return {"live": _job("live"), "eod": _job("eod"), "interval_seconds": LIVE_INTERVAL}


def start_background(interval=None):
    """Long-lived servers (local, Render): poll on a daemon thread."""
    interval = interval or LIVE_INTERVAL

    def loop():
        while True:
            try:
                tick()
            except Exception as e:  # never let the watcher die
                _job_set("live", False, f"{type(e).__name__}: {e}")
            time.sleep(interval)

    t = threading.Thread(target=loop, name="finn-live", daemon=True)
    t.start()
    return t
