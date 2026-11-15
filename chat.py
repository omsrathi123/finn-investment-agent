"""Ask FINN — the conversational copilot.

Every reply is built in three steps:
  1. Route the message (rules first, cheap model only for ambiguous text) and resolve follow-ups
     ("what about their debt?") against the company already being discussed.
  2. Gather grounded context by calling FINN's own tools: digest, alerts, results, people,
     research sections, holdings. These are the only facts the reply may use.
  3. Write the reply. A deterministic answer is always composed from the facts, so FINN answers
     even without a model key. With a key, the model rewrites it conversationally from the same
     facts plus the chat history, and is told not to add outside knowledge.
The reply carries cards (the same components as the rest of the app) and follow-up suggestions.
"""
import json
import re

from ask import SECTION_RULES, brief, find_symbol, parse_hours, route, _latest
from config import PORTFOLIO
from db import conn
from llm import ask_text, has_key, FAST, SMART

SYSTEM = """You are FINN, a calm, sharp investing copilot for an Indian equity investor who holds a 20-stock NSE portfolio.
Answer using ONLY the FACTS provided (they come from NSE filings, the company's financial statements, concall
transcripts, end-of-day prices and the user's own alert rules). Do not use outside knowledge about any company.
Style: direct and specific, like a good analyst on chat. Lead with the answer. 2-5 short sentences or up to 5 tight
bullets ("- "), max ~130 words. Use tickers, ₹ crore and % as given. Use **bold** sparingly for the key number.
If the facts don't answer the question, say exactly what you do know and what to ask instead.
Never tell the user to buy or sell; you may lay out what would matter for the decision.
Cards with tables/filings are shown under your reply, so don't repeat every number — interpret them."""

SMALL_TALK = [
    (r"^\s*(hi|hey|hello|hola|namaste|yo|good (morning|afternoon|evening))\b", "greet"),
    (r"\b(thanks|thank you|thx|great|awesome|nice)\b\s*[.!]*\s*$", "thanks"),
    (r"\b(who are you|what can you do|help|how do (i|you) use)\b", "help"),
]
PORTFOLIO_Q = r"\b(portfolio|holdings|my stocks|best|worst|top (gainer|loser|performer)s?|perform|winners?|losers?|green|red)\b"
FOLLOW_UP = r"\b(it|its|it's|they|their|them|this (company|stock|one)|that (company|stock|one)|same)\b"


def _d(date):
    """'2026-11-13' or '13-Nov-2026 10:00:00' -> '13 Nov'."""
    from digest import parse_ts
    t = parse_ts(str(date))
    return f"{t.day} {t.strftime('%b')}" if t else str(date)


def _pct(v):
    return "–" if v is None else f"{v:+.1f}%"


def _holdings():
    c = conn()
    out = []
    for s in PORTFOLIO:
        rows = c.execute("SELECT date, close FROM prices WHERE symbol=? ORDER BY date DESC LIMIT 22", (s,)).fetchall()
        if len(rows) < 2:
            continue
        day = (rows[0]["close"] / rows[1]["close"] - 1) * 100
        month = (rows[0]["close"] / rows[-1]["close"] - 1) * 100
        out.append({"symbol": s, "close": round(rows[0]["close"], 2), "date": rows[0]["date"],
                    "day_pct": round(day, 2), "month_pct": round(month, 1)})
    return out


def _resolve(message, context_symbol):
    r = route(message)
    low = message.lower()
    if not r["symbol"] and context_symbol and (re.search(FOLLOW_UP, low) or r["intent"] in ("help", None)):
        sec = next((k for k, p in SECTION_RULES if re.search(p, low)), None)
        if sec or re.search(FOLLOW_UP, low):
            r = {"intent": sec or "stock", "symbol": context_symbol, "hours": r.get("hours"), "follow_up": True}
    if r["intent"] in ("help", "digest", None) and not r["symbol"] and re.search(PORTFOLIO_Q, low):
        if not re.search(r"what (changed|happened)|filings?|announce|news|brief|digest", low):
            r["intent"] = "portfolio"
    for pat, kind in SMALL_TALK:
        if re.search(pat, low) and not r["symbol"] and r["intent"] in ("help", None):
            r["intent"] = kind
    return r


# ---------------- context gathering ----------------
def _gather(r, message):
    """Return (facts dict for the model, deterministic reply, cards, suggestions)."""
    intent, sym = r["intent"], r.get("symbol")
    facts, cards, sugg = {}, [], []

    if intent in ("greet", "thanks", "help"):
        b = brief(24)
        facts["last_24h_brief"] = b["text"]
        if intent == "thanks":
            text = "Anytime. I'll flag anything that crosses your alert rules."
        elif intent == "greet":
            text = f"Hi! Here's where things stand. {b['text']}"
        else:
            text = ("I watch your portfolio's NSE filings and price/volume moves, and research any listed company from its "
                    "own annual reports and concall transcripts. Ask me what changed, about unusual moves, a company's "
                    "results, who joined its management, its debt, cash quality, guidance, or the bull and bear case.")
        sugg = ["What changed this week?", "Any unusual moves?", "How is my portfolio doing?"]
        return facts, text, cards, sugg

    if intent == "digest":
        hours = r.get("hours") or 24
        b = brief(hours)
        if not b["top"] and hours < 168:
            wider = brief(168)
            if wider["top"]:
                b, hours = wider, 168
        facts.update(window_hours=hours, brief=b["lines"],
                     top_filings=[{k: x[k] for k in ("symbol", "label", "score", "summary", "ts")} for x in b["top"]])
        text = b["text"] + ("" if (r.get("hours") or 24) == hours else " (Nothing material in the last day, so I widened to 7 days.)")
        if b["top"]:
            cards.append({"type": "filings", "items": b["top"]})
            s0 = b["top"][0]["symbol"]
            sugg = [f"{s0} results" if b["top"][0]["kind"] == "results" else f"Tell me about {s0}",
                    "Any unusual moves?", "Last 30 days"]
        else:
            sugg = ["Last 30 days", "Any unusual moves?"]
        return facts, text, cards, sugg

    if intent == "alerts":
        from push import evaluate, get_rules
        alerts, _ = evaluate()
        moves = sorted((a for a in alerts if a["kind"] == "price"),
                       key=lambda a: -(abs(a["move_pct"]) + 2 * a["vol_mult"]))[:6]
        rules = get_rules()
        facts.update(rules={k: rules[k] for k in ("vol_mult", "move_pct", "lookback")},
                     moves=[{k: a.get(k) for k in ("symbol", "ts", "move_pct", "vol_mult", "body")} for a in moves])
        if moves:
            text = (f"{len(moves)} move{'s' if len(moves) > 1 else ''} crossed your lines (volume ≥ {rules['vol_mult']}× or "
                    f"±{rules['move_pct']}%) in the last 10 sessions. Biggest: " +
                    "; ".join(f"{a['symbol']} {_pct(a['move_pct'])} on {a['vol_mult']:.1f}× volume ({_d(a['ts'])})" for a in moves[:3]) + ".")
            cards.append({"type": "alerts", "items": moves})
            sugg = [f"Why did {moves[0]['symbol']} move?", "Make alerts stricter", "What changed this week?"]
        else:
            text = f"Nothing crossed your thresholds (volume ≥ {rules['vol_mult']}×, move ±{rules['move_pct']}%) in the last 10 sessions."
            sugg = ["What changed this week?", "How is my portfolio doing?"]
        return facts, text, cards, sugg

    if intent == "portfolio":
        h = _holdings()
        if not h:
            return facts, "I don't have prices for your holdings yet.", cards, sugg
        up = [x for x in h if x["day_pct"] > 0]
        avg = sum(x["day_pct"] for x in h) / len(h)
        best = sorted(h, key=lambda x: -x["month_pct"])
        facts.update(holdings=h, equal_weight_day_pct=round(avg, 2))
        lead = ", ".join("%s %s" % (x["symbol"], _pct(x["month_pct"])) for x in best[:3])
        lag = ", ".join("%s %s" % (x["symbol"], _pct(x["month_pct"])) for x in best[-2:])
        text = (f"On {_d(h[0]['date'])}, {len(up)} of {len(h)} holdings closed higher; an equal-weight portfolio moved "
                f"**{_pct(avg)}**. Over about a month the leaders are {lead}; the laggards are {lag}.")
        cards.append({"type": "holdings", "items": best[:5] + best[-3:]})
        sugg = [f"Tell me about {best[0]['symbol']}", f"Why is {best[-1]['symbol']} lagging?", "Any unusual moves?"]
        return facts, text, cards, sugg

    if not sym:
        b = brief(168)
        facts["portfolio_brief_7d"] = b["text"]
        return facts, None, cards, ["What changed this week?", "Any unusual moves?", "How is my portfolio doing?"]

    # ---- company-specific ----
    if intent in ("results", "people"):
        kind = "results" if intent == "results" else "management_change"
        f = _latest(sym, kind)
        if not f:
            text = f"I don't have a {'results' if intent == 'results' else 'management-change'} filing for {sym} in the stored data."
            facts["missing"] = text
            r["intent"] = intent = "stock"
        elif intent == "results":
            from results import result_card
            try:
                card = result_card(f["id"])
                facts["results"] = {k: card[k] for k in ("quarter", "basis", "unit", "rows", "headline", "cross_check", "source")}
                text = f"{sym} {card.get('quarter') or ''}: {card['headline'] or 'numbers below'}."
                if card.get("cross_check"):
                    text += " The filing matches Screener's table." if card["cross_check"]["agrees"] else \
                            " The filing and Screener's table disagree; check consolidated vs standalone."
                cards.append({"type": "results", "data": card})
            except Exception as e:
                text = f"{sym} filed results on {_d(f['ts'])}. {e}"
                cards.append({"type": "filings", "items": [f]})
            sugg = [f"{sym} guidance", f"Did {sym} deliver on promises?", f"{sym} cash quality"]
            return facts, text, cards, sugg
        else:
            from people import profile
            try:
                p = profile(f["id"])
                facts["management_change"] = p["people"]
                ppl = p["people"]
                if ppl:
                    x = ppl[0]
                    text = f"{sym}: {x.get('name')} — {x.get('change')}{', ' + x['role'] if x.get('role') else ''}."
                    if x.get("background"):
                        text += f" Background per the filing: {x['background']}"
                    elif x.get("web"):
                        text += " Background from the web is below."
                else:
                    text = f"{sym} filed a management change on {f['ts']}: {f['summary'] or f['title']}"
                cards.append({"type": "people", "data": p})
            except Exception as e:
                text = f"{sym}: {f['summary'] or f['title']} ({e})"
            sugg = [f"{sym} results", f"Tell me about {sym}", "What changed this week?"]
            return facts, text, cards, sugg

    if intent in ("business", "financial", "trajectory", "balance", "cash", "guidance", "nvn", "bullbear"):
        import research
        try:
            d = research.section(sym, intent)
            facts[f"{intent}_section"] = d
            text = _section_text(sym, intent, d)
            cards.append({"type": "section", "key": intent, "symbol": sym, "data": d})
        except Exception as e:
            text = f"I couldn't build {sym}'s {intent.replace('nvn', 'promised-vs-delivered')} view: {e}"
            facts["error"] = text
        nxt = {"financial": ["trajectory", "balance"], "trajectory": ["cash", "guidance"], "balance": ["cash", "bullbear"],
               "cash": ["balance", "nvn"], "guidance": ["nvn", "bullbear"], "nvn": ["guidance", "bullbear"],
               "bullbear": ["nvn", "cash"], "business": ["financial", "guidance"]}[intent]
        label = {"trajectory": "growth trend", "balance": "debt", "cash": "cash quality", "guidance": "guidance",
                 "nvn": "Did they deliver?", "bullbear": "bull vs bear", "financial": "valuation", "business": "business"}
        sugg = [f"{sym} {label[n]}" if n != "nvn" else f"Did {sym} deliver on promises?" for n in nxt] + [f"Open {sym} research"]
        return facts, text, cards, sugg

    # intent == "stock": a general look at one company
    from digest import filing_view
    c = conn()
    px = c.execute("SELECT date, close FROM prices WHERE symbol=? ORDER BY date DESC LIMIT 22", (sym,)).fetchall()
    filings = sorted((filing_view(x) for x in c.execute("SELECT * FROM announcements WHERE symbol=?", (sym,))),
                     key=lambda v: v["iso"] or "", reverse=True)
    material, seen = [], set()
    for v in filings:  # one card per kind of event: a results day files the same news several times
        if v["score"] >= 35 and v["kind"] not in seen:
            seen.add(v["kind"])
            material.append(v)
    material = material[:4]
    bits = []
    if px:
        day = (px[0]["close"] / px[1]["close"] - 1) * 100 if len(px) > 1 else None
        month = (px[0]["close"] / px[-1]["close"] - 1) * 100 if len(px) > 5 else None
        facts["price"] = {"close": round(px[0]["close"], 2), "date": px[0]["date"], "day_pct": day, "month_pct": month}
        bits.append(f"{sym} closed at ₹{px[0]['close']:,.2f} on {_d(px[0]['date'])} ({_pct(day)} on the day, {_pct(month)} over ~a month).")
    try:
        from push import evaluate
        spikes = [a for a in evaluate()[0] if a["kind"] == "price" and a["symbol"] == sym][:3]
    except Exception:
        spikes = []
    if spikes:
        facts["recent_unusual_moves"] = [{k: a.get(k) for k in ("ts", "move_pct", "vol_mult", "body", "context")} for a in spikes]
        a = spikes[0]
        why = f" — the same day as its {a['context']['label'].lower()} filing ({a['context']['title']})" if a.get("context") else \
              " with no material filing that day, so the move looks market- or flow-driven"
        bits.append(f"Most recent unusual session: {_d(a['ts'])}, {_pct(a['move_pct'])} on {a['vol_mult']:.1f}× normal volume{why}.")
        cards.insert(0, {"type": "alerts", "items": spikes})
    if material:
        facts["recent_material_filings"] = [{k: v[k] for k in ("label", "score", "summary", "ts")} for v in material]
        bits.append(f"Latest material filing: {material[0]['label'].lower()} — {material[0]['summary'] or material[0]['title']}")
        cards.append({"type": "filings", "items": material[:3]})
    try:
        import research
        o = research.overview(sym)
        reads = {k: (o.get(k) or {}).get("read") for k in ("financial", "trajectory", "balance", "cash")}
        reads = {k: v for k, v in reads.items() if v}
        if reads:
            facts["fundamentals"] = reads
            bits.append(next(iter(reads.values())))
    except Exception:
        pass
    if r.get("text"):
        bits.insert(0, r["text"])
    text = " ".join(bits) or f"I don't have stored data for {sym} yet. Open research to fetch its statements."
    cards.append({"type": "stock", "symbol": sym})
    sugg = [f"{sym} results", f"{sym} bull vs bear", f"Did {sym} deliver on promises?"]
    return facts, text, cards, sugg


def _section_text(sym, k, d):
    if k in ("financial", "trajectory", "balance", "cash"):
        return f"{sym}: {d.get('read') or 'details below.'}"
    if k == "business":
        return f"{sym}: {d.get('what_it_does') or 'business details below.'}"
    if k == "guidance":
        items = d.get("items") or []
        if not items:
            return f"{sym} gave no explicit numeric guidance on the {d.get('period') or 'latest'} call."
        return f"On the {d.get('period') or 'latest'} call {sym} guided: " + "; ".join(
            f"{g.get('metric')}: {g.get('statement')}" for g in items[:3]) + "."
    if k == "nvn":
        st = [i.get("status") for i in d.get("items") or []]
        tally = ", ".join(f"{st.count(s)} {s}" for s in ("met", "partial", "missed", "unclear") if st.count(s))
        return f"{d.get('verdict', '')} ({tally})." if tally else d.get("verdict", "")
    if k == "bullbear":
        b, br = (d.get("bull") or [{}])[0].get("point"), (d.get("bear") or [{}])[0].get("point")
        return f"Bull: {b}. Bear: {br}. What decides it: {d.get('swing_factor')}"
    return ""


# ---------------- reply ----------------
def _model_for(intent, message):
    if intent in ("bullbear", "nvn") or re.search(r"\b(why|should|compare|versus|vs\.?|better|risk)\b", message.lower()):
        return SMART
    return FAST


def chat(message, history=None, context_symbol=None):
    message = (message or "").strip()[:500]
    history = [h for h in (history or []) if h.get("role") in ("user", "model") and h.get("text")][-8:]
    if not message:
        return {"reply": "Ask me anything about your portfolio or any NSE company.", "cards": [], "suggestions": []}
    r = _resolve(message, context_symbol)
    facts, text, cards, sugg = _gather(r, message)
    engine = "rules"
    if has_key() and (text is None or r["intent"] not in ("thanks",)):
        prompt = (f"QUESTION: {message}\n\nFACTS (JSON):\n{json.dumps(facts, default=str)[:12000]}\n\n"
                  f"DRAFT ANSWER FROM FACTS (improve it, keep it accurate): {text or '(none)'}")
        try:
            text = ask_text(prompt, _model_for(r["intent"], message), SYSTEM, history)
            engine = "model"
        except Exception:
            pass
    if text is None:
        text = ("I can answer questions about your portfolio's filings and moves, or any NSE company's results, "
                "management, growth, debt, cash, guidance and bull/bear case. " + facts.get("portfolio_brief_7d", ""))
    return {"reply": text, "intent": r["intent"], "symbol": r.get("symbol"), "cards": cards,
            "suggestions": [s for s in sugg if s][:4], "engine": engine}
