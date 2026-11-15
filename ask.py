"""Ask FINN: one text/voice box that routes a question to the right tool.

Routing is rules first (free, instant); the cheap model is only consulted when the rules
can't tell what was asked. The router returns *what to show*, the UI renders it with the
same cards used everywhere else, so an answer is never a wall of generated prose.
"""
import json
import re

from config import PORTFOLIO, RESEARCH_EXTRA
from db import conn
from digest import build, filing_view
from llm import ask_json, has_key, FAST

STOP = {"I", "A", "THE", "AND", "OR", "VS", "QOQ", "YOY", "PAT", "PBT", "EBITDA", "CEO", "CFO", "MD", "NSE", "BSE",
        "AGM", "EGM", "TTM", "FY", "Q1", "Q2", "Q3", "Q4", "AR", "WHAT", "WHO", "HOW", "WHY", "IS", "IT", "ME", "MY",
        "OF", "IN", "ON", "FOR", "TO", "DID", "DO", "ANY", "IPO", "ESG", "GST", "RBI", "SEBI", "IT'S", "OK"}
SECTION_RULES = [
    ("results", r"\bresults?\b|\bquarter(ly)?\b|headline numbers|\bq[1-4]\b|qoq|yoy|earnings(?! call)"),
    ("people", r"who (is|are|joined)|appoint|resign|\bcfo\b|\bceo\b|management change|new (md|chief)"),
    ("nvn", r"promis|deliver|narrative|credib|kept (its|their) word|track record|said vs"),
    ("guidance", r"guidance|guide[ds]?\b|outlook|target"),
    ("bullbear", r"\bbull|\bbear|case for|case against|should i (buy|sell)|risks? and"),
    ("balance", r"\bdebt\b|leverage|balance sheet|borrow|working capital|d/?e\b"),
    ("cash", r"\bcash\b|fcf|free cash|conversion"),
    ("trajectory", r"grow|trajectory|trend|cagr|over (the )?(last|past) \d+ years|compress"),
    ("financial", r"margin|valuation|p/?e\b|ratio|snapshot|how big|market cap|\broe\b|\broce\b"),
    ("business", r"what does .* do|business|segment|revenue mix|products?|what is [a-z]+\b"),
]
PORTFOLIO_RULES = [
    ("alerts", r"unusual|spike|volume|mover|moved|jump|crash|fell|rose|alert"),
    ("digest", r"what (changed|happened|matters|did i miss)|today|brief|digest|filings?|announce|news|update|overnight|week"),
]
ROUTER = """Route an investor's question for an app with these intents:
digest (portfolio filings in a time window), alerts (unusual price/volume moves), results (a company's latest
quarterly numbers), people (management change), business, financial, trajectory, balance, cash, guidance,
nvn (promised vs delivered), bullbear, stock (general company view), help.
Known tickers: {tickers}.
Return JSON {{"intent":"", "symbol": "NSE ticker or null", "hours": number or null}}.
Question: {q}"""


def _names():
    names = {}
    for r in conn().execute("SELECT DISTINCT symbol, raw FROM announcements WHERE source='NSE'"):
        try:
            n = json.loads(r["raw"] or "{}").get("sm_name") or ""
        except ValueError:
            n = ""
        n = re.sub(r"\b(limited|ltd|india|industries|the|of|and|corporation|company|institute)\b", " ", n.lower())
        words = [w for w in re.split(r"\W+", n) if len(w) > 2]
        if words:
            names[" ".join(words[:2])] = r["symbol"]
            names.setdefault(words[0], r["symbol"])
    return names


def find_symbol(q):
    known = set(PORTFOLIO) | set(RESEARCH_EXTRA) | {r[0] for r in conn().execute("SELECT DISTINCT symbol FROM prices")}
    up = q.upper()
    for tok in re.findall(r"[A-Z][A-Z0-9&-]{1,14}", up):
        if tok in known:
            return tok
    low = q.lower()
    for name, sym in sorted(_names().items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(name)}\b", low):
            return sym
    for tok in re.findall(r"\b[A-Z][A-Z0-9&-]{2,14}\b", q):  # typed in caps but not in our universe
        if tok not in STOP:
            return tok
    return None


def parse_hours(q):
    low = q.lower()
    m = re.search(r"(\d+)\s*(h|hr|hour|d|day|w|wk|week|m|month)s?\b", low)
    if m:
        n, u = int(m.group(1)), m.group(2)[0]
        return n * {"h": 1, "d": 24, "w": 168, "m": 720}[u]
    for k, h in (("yesterday", 48), ("today", 24), ("overnight", 18), ("week", 168), ("month", 720), ("fortnight", 336)):
        if k in low:
            return h
    return None


def _latest(symbol, kind):
    best = None
    for r in conn().execute("SELECT * FROM announcements WHERE symbol=?", (symbol,)):
        v = filing_view(r)
        if v["kind"] == kind and (not best or (v["iso"] or "") > (best["iso"] or "")) and (kind != "results" or v["url"]):
            if kind == "results" and v["score"] < 70:
                continue
            best = v
    return best


def route(q):
    q = (q or "").strip()
    if not q:
        return {"intent": "help"}
    sym, hours = find_symbol(q), parse_hours(q)
    intent = None
    low = q.lower()
    if sym:
        intent = next((k for k, p in SECTION_RULES if re.search(p, low)), "stock")
    else:
        intent = next((k for k, p in PORTFOLIO_RULES if re.search(p, low)), None)
    if intent is None and has_key():
        try:
            got = ask_json(ROUTER.format(tickers=", ".join(sorted(PORTFOLIO)), q=q), FAST)
            intent, sym = got.get("intent") or "help", got.get("symbol") or sym
            hours = got.get("hours") or hours
        except Exception:
            intent = "help"
    return {"intent": intent or "help", "symbol": sym, "hours": hours}


def answer(q):
    r = route(q)
    intent, sym = r["intent"], r["symbol"]
    if intent in ("results", "people") and sym:
        f = _latest(sym, "results" if intent == "results" else "management_change")
        if not f:
            r["text"] = f"No {'results' if intent == 'results' else 'management-change'} filing for {sym} in the stored window."
            r["intent"] = "stock"
        else:
            r.update(id=f["id"], filing=f, text=f"{sym} · {f['label']} · {f['ts']}")
    elif intent == "digest":
        r["hours"] = r["hours"] or 24
        r["text"] = brief(r["hours"])["text"]
    elif intent == "help":
        r["text"] = ("Try: “what changed today”, “unusual moves”, “TCS results”, “who is Max Healthcare's new "
                     "director”, “did Dixon deliver on guidance”, “Polycab balance sheet”, “bull and bear on TRENT”.")
    return r


def brief(hours=24):
    """A spoken-length digest. Deterministic: costs nothing and never hallucinates."""
    from push import evaluate
    d = build(hours=hours)
    crit, nota = d["critical"], d["notable"]
    alerts, _ = evaluate()
    moves = [a for a in alerts if a["kind"] == "price"][:3]
    span = f"{hours // 24} days" if hours >= 48 else "24 hours" if hours == 24 else f"{hours} hours"
    lines = []
    if not crit and not nota:
        lines.append(f"Quiet {span}: nothing material across your {len(PORTFOLIO)} holdings.")
    else:
        lines.append(f"In the last {span}: {len(crit)} need{'s' if len(crit) == 1 else ''} attention, "
                     f"{len(nota)} worth knowing.")
        for x in crit[:4]:
            from taxonomy import SENT
            summ = SENT.split(x["summary"] or x["why"])[0].rstrip(".")
            lines.append(f"{x['symbol']}, {x['label'].lower()}: {summ}.")
    if moves:
        lines.append("Unusual moves: " + "; ".join(f"{m['symbol']} {m['move_pct']:+.1f}% ({m['ts']})" for m in moves) + ".")
    if d["counts"]["routine"]:
        lines.append(f"{d['counts']['routine']} routine filings hidden.")
    return {"text": " ".join(lines), "lines": lines, "hours": hours, "top": crit[:4], "moves": moves}
