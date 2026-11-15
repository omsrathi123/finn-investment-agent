"""Push, flavour 1: the windowed digest.

For any time window, every filing across the portfolio is scored (taxonomy.py), then
*clustered*: a results day typically produces 4-6 filings (board outcome, press release,
investor deck, newspaper ad, call notice). The investor needs one card for that event,
with the rest tucked underneath, not six cards. Routine paperwork is counted, not shown.
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from db import conn
from taxonomy import analyse

TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d-%b-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
              "%d-%b-%Y %H:%M:%S.%f", "%Y-%m-%d", "%d-%b-%Y")
CLUSTER_HOURS = 72
CLUSTERABLE = {"results", "earnings_call", "ma", "management_change", "litigation", "fund_raise",
               "dividend", "investor_meetings", "credit_rating"}


IST = ZoneInfo("Asia/Kolkata")


def ist_now():
    """Exchange timestamps are IST; hosted servers usually run on UTC. Compare like with like."""
    return datetime.now(IST).replace(tzinfo=None)


def parse_ts(ts):
    s = (ts or "").strip()
    for f in TS_FORMATS:
        try:
            return datetime.strptime(s[:26], f)
        except ValueError:
            pass
    return None


def _raw(r):
    try:
        return json.loads(r["raw"] or "{}") if "raw" in r.keys() else {}
    except (ValueError, TypeError):
        return {}


def filing_view(r):
    raw = _raw(r)
    a = analyse(r["category"], r["subject"], r["detail"], raw.get("voiceSummary"))
    t = parse_ts(r["ts"])
    summary = a["summary"]
    name = raw.get("sm_name")
    if summary and name and summary.lower().startswith(name.lower()):
        rest = summary[len(name):].strip(" ,")
        summary = rest[0].upper() + rest[1:] if len(rest) > 12 else summary
    actions = []
    if a["category"] == "results" and r["attachment_url"] and a["score"] >= 70:
        actions.append("results")
    if a["category"] == "management_change":
        actions.append("people")
    return dict(id=r["id"], symbol=r["symbol"], company=raw.get("sm_name") or r["symbol"],
                ts=r["ts"], iso=t.isoformat(sep=" ") if t else None, _t=t,
                kind=a["category"], label=a["label"], score=a["score"], tier=a["tier"],
                title=r["subject"] or r["category"] or "Filing", summary=summary, why=a["why"],
                detail=(r["detail"] or "")[:240], url=r["attachment_url"], source=r["source"],
                actions=actions, related=[])


def window(hours=None, start=None, end=None, now=None):
    now = now or ist_now()
    if start:
        s = parse_ts(start) or now - timedelta(hours=24)
        e = parse_ts(end) + timedelta(days=1) if end and len(end) <= 10 and parse_ts(end) else (parse_ts(end) if end else now)
        return s, e or now
    return now - timedelta(hours=hours or 24), now


def _cluster(items):
    """Merge filings of the same company + category that sit within CLUSTER_HOURS of each other."""
    items = sorted(items, key=lambda x: (-x["score"], x["_t"] or datetime.min))
    heads = []
    for it in items:
        if it["kind"] in CLUSTERABLE and it["_t"]:
            for h in heads:
                if (h["symbol"] == it["symbol"] and h["kind"] == it["kind"] and h["_t"]
                        and abs((h["_t"] - it["_t"]).total_seconds()) <= CLUSTER_HOURS * 3600):
                    h["related"].append({k: it[k] for k in ("id", "title", "ts", "url", "summary")})
                    break
            else:
                heads.append(it)
        else:
            heads.append(it)
    return heads


def build(hours=None, start=None, end=None, symbols=None, now=None, db=None):
    s, e = window(hours, start, end, now)
    rows = (db or conn()).execute("SELECT * FROM announcements").fetchall()
    items = []
    for r in rows:
        if symbols and r["symbol"] not in symbols:
            continue
        v = filing_view(r)
        if v["_t"] is None or not (s <= v["_t"] <= e):
            continue
        items.append(v)
    counts = {"critical": 0, "notable": 0, "routine": 0}
    for it in items:
        counts[it["tier"]] += 1
    tiers = {"critical": [], "notable": [], "routine": []}
    for h in _cluster(items):
        tiers[h["tier"]].append(h)
    for k in tiers:
        tiers[k].sort(key=lambda x: (-x["score"], -(x["_t"].timestamp() if x["_t"] else 0)))
        for x in tiers[k]:
            x.pop("_t", None)
    latest = max((it["iso"] for it in items if it["iso"]), default=None)
    return dict(hours=hours, start=s.isoformat(sep=" ", timespec="minutes"),
                end=e.isoformat(sep=" ", timespec="minutes"), counts=counts,
                events=sum(len(v) for v in tiers.values()), latest=latest, **tiers)
