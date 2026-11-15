"""Pull: on-demand research on any NSE ticker, grounded in primary sources only.

Section                Source                                     Cost
---------------------  -----------------------------------------  ------------------------------
financial snapshot     Screener standardised tables (from filings) free  (parsed, computed in code)
trajectory             "                                          free
balance sheet          "                                          free
cash quality           "                                          free
business snapshot      latest annual report, keyword-chunked      1 FAST call
guidance               latest 2 concall transcripts               2 FAST calls (shared cache)
narrative vs numbers   last 4 transcripts + actual quarterly nos  4 FAST + 1 SMART
bull vs bear           all of the above (facts only)              1 SMART (+ shared FAST cache)

No broker notes, no news, and prompts forbid the model's outside knowledge. Each section is
computed lazily, only when the investor opens it, and cached.
"""
from db import conn
from llm import ask_json, has_key, FAST, SMART
from pdftext import pdf_text, relevant_pages

EXTRACT = """You are reading ONE earnings-call transcript. Use ONLY this text; no outside knowledge. Return JSON:
{{"period":"quarter discussed e.g. Q1 FY27","guidance":[{{"metric":"","statement":"forward-looking claim/target management made","quote":"<=20 words verbatim"}}],
"reported":[{{"metric":"","value":"actual figure/growth management stated for the quarter just reported","quote":"<=20 words verbatim"}}]}}
Max 6 items each; headline metrics only (growth, margins, capex, debt, volumes).
TEXT:
{t}"""
JUDGE = """Below are extractions from a company's last concalls (oldest to newest). Judge what management PROMISED vs what was
later DELIVERED, using only these extractions{actuals_hint}. Return JSON:
{{"verdict":"one sentence, blunt","items":[{{"promise":"","promised_in":"period","delivered":"what the later calls/numbers show","status":"met|missed|partial|unclear","quote":"supporting quote"}}],"watch":"one thing to watch next"}}
Max 6 items. Do not invent numbers.
DATA:
{d}"""
BUSINESS = """From these annual-report pages of {name}, describe the business. Use ONLY this text; no outside knowledge.
Return JSON: {{"what_it_does":"<=40 words","segments":[{{"name":"","share_pct":n or null,"note":"<=15 words"}}],
"revenue_mix":"<=30 words on mix by segment/geography/product as stated","drivers":["<=12 words each, max 4"],
"pages":[page numbers used]}}
Use null when a figure is not in the text.
PAGES:
{t}"""
BULLBEAR = """You are an analyst. Using ONLY the facts below (computed from {name}'s filings and its own concall statements),
write a tight bull case and bear case. No outside knowledge, no price targets. Every point must cite a fact given.
Return JSON: {{"bull":[{{"point":"<=18 words","evidence":"fact used"}}],"bear":[{{"point":"<=18 words","evidence":"fact used"}}],
"swing_factor":"the one variable that decides between them, <=20 words"}}  (3 points each)
FACTS:
{f}"""
AR_KEYWORDS = [r"segment", r"revenue mix", r"business overview", r"our business", r"products?", r"brands?",
               r"capacity", r"customers?", r"geograph", r"market share", r"manufactur", r"revenue from operations",
               r"subsidiar", r"key (highlights|performance)", r"verticals?", r"portfolio"]


# ---------------- documents ----------------
def _docs(symbol, kind, n):
    # fetch_docs stores Screener's newest-first order; rowid preserves that order.
    return conn().execute(
        "SELECT label,url FROM documents WHERE symbol=? AND kind=? ORDER BY rowid ASC LIMIT ?",
        (symbol, kind, n)).fetchall()


def _ensure_docs(symbol):
    try:
        from fetch_docs import ensure_docs
        ensure_docs(symbol)
    except Exception:
        pass


def _extract_call(url):
    e = ask_json(EXTRACT.format(t=pdf_text(url, 14000)), FAST)
    e["source"] = url
    return e


# ---------------- screener-backed, deterministic ----------------
def _fund(symbol):
    import screener
    return screener.company(symbol)


def _annual_cols(t):
    return [i for i, c in enumerate(t["columns"]) if c.startswith("Mar ") or (c[:3].isalpha() and c[-4:].isdigit())]


def _cagr(a, b, years):
    if a is None or b is None or a <= 0 or b <= 0 or years <= 0:
        return None
    return round(((b / a) ** (1 / years) - 1) * 100, 1)


def _r(x, d=1):
    return None if x is None else round(x, d)


def _pick(s, idx):
    return [s[i] if s and i < len(s) else None for i in idx]


def financial(data):
    from screener import series
    pl, q, tr = data["tables"]["profit-loss"], data["tables"]["quarters"], data["top_ratios"]
    cols = pl["columns"]
    i = len(cols) - 1 if cols else None
    rev, op, pat = series(pl, "revenue"), series(pl, "op_profit"), series(pl, "pat")
    v = lambda s: s[i] if s and i is not None and i < len(s) else None  # noqa: E731
    qrev, qpat = series(q, "revenue"), series(q, "pat")
    n = len(q["columns"])
    yoy = lambda s: (_r((s[-1] / s[-5] - 1) * 100) if s and n >= 5 and s[-1] is not None and s[-5] else None)  # noqa: E731
    opm = _r(v(op) / v(rev) * 100) if v(op) is not None and v(rev) else None
    tiles = [
        {"label": f"Revenue · {cols[i] if cols else ''}", "value": v(rev), "unit": "cr"},
        {"label": "EBITDA", "value": v(op), "unit": "cr"},
        {"label": "EBITDA margin", "value": opm, "unit": "%"},
        {"label": "PAT", "value": v(pat), "unit": "cr"},
        {"label": "Market cap", "value": tr.get("Market Cap"), "unit": "cr"},
        {"label": "P/E", "value": tr.get("Stock P/E"), "unit": "×"},
        {"label": "ROCE", "value": tr.get("ROCE"), "unit": "%"},
        {"label": "ROE", "value": tr.get("ROE"), "unit": "%"},
        {"label": "Dividend yield", "value": tr.get("Dividend Yield"), "unit": "%"},
    ]
    qy_rev, qy_pat = yoy(qrev), yoy(qpat)
    read = []
    if v(rev) is not None:
        read.append(f"{cols[i]} revenue ₹{v(rev):,.0f} cr" + (f" at {opm:.1f}% EBITDA margin" if opm is not None else ""))
    if qy_rev is not None:
        read.append(f"latest quarter ({q['columns'][-1]}) revenue {qy_rev:+.1f}% YoY"
                    + (f", PAT {qy_pat:+.1f}% YoY" if qy_pat is not None else ""))
    return {"tiles": tiles, "read": "; ".join(read) + "." if read else None,
            "latest_quarter": q["columns"][-1] if q["columns"] else None}


def trajectory(data):
    from screener import series
    pl, q = data["tables"]["profit-loss"], data["tables"]["quarters"]
    idx = [i for i in _annual_cols(pl)][-10:]
    years = [pl["columns"][i] for i in idx]
    rev, op, pat = (_pick(series(pl, k), idx) for k in ("revenue", "op_profit", "pat"))
    opm = [(_r(o / r * 100) if o is not None and r else None) for o, r in zip(op, rev)]
    out = {"years": years, "revenue": rev, "ebitda": op, "pat": pat, "opm": opm, "cagr": {}}
    for k, s in (("revenue", rev), ("ebitda", op), ("pat", pat)):
        out["cagr"][k] = {f"{n}y": _cagr(s[-1 - n], s[-1], n) if len(s) > n else None for n in (3, 5)}
    qi = list(range(max(0, len(q["columns"]) - 8), len(q["columns"])))
    qr, qo = _pick(series(q, "revenue"), qi), _pick(series(q, "op_profit"), qi)
    out["quarters"] = {"cols": [q["columns"][i] for i in qi], "revenue": qr,
                       "opm": [(_r(o / r * 100) if o is not None and r else None) for o, r in zip(qo, qr)],
                       "pat": _pick(series(q, "pat"), qi)}
    flags = []
    for k, name in (("revenue", "Revenue"), ("ebitda", "EBITDA"), ("pat", "PAT")):
        g = out["cagr"][k]["5y"] if out["cagr"][k]["5y"] is not None else out["cagr"][k]["3y"]
        if g is not None:
            flags.append({"what": name, "cagr": g, "dir": "growing" if g >= 10 else "flat" if g >= 0 else "shrinking"})
    m = [x for x in opm if x is not None]
    if len(m) >= 4:
        d = round(m[-1] - m[-4], 1)
        flags.append({"what": "EBITDA margin", "delta_pp_3y": d,
                      "dir": "expanding" if d >= 1.5 else "compressing" if d <= -1.5 else "stable"})
    out["flags"] = flags
    out["read"] = "; ".join(
        f"{f['what']} {f['dir']}" + (f" ({f['cagr']:+.1f}% CAGR)" if "cagr" in f else f" ({f['delta_pp_3y']:+.1f}pp over 3y)")
        for f in flags) or None
    return out


def balance(data):
    from screener import series
    bs, pl, ra = data["tables"]["balance-sheet"], data["tables"]["profit-loss"], data["tables"]["ratios"]
    idx = list(range(max(0, len(bs["columns"]) - 6), len(bs["columns"])))
    cols = [bs["columns"][i] for i in idx]
    eq = [(a or 0) + (b or 0) if a is not None or b is not None else None
          for a, b in zip(_pick(series(bs, "equity_capital"), idx), _pick(series(bs, "reserves"), idx))]
    debt = _pick(series(bs, "borrowings"), idx)
    de = [(_r(d / e, 2) if d is not None and e else None) for d, e in zip(debt, eq)]
    is_bank = series(bs, "deposits") is not None
    out = {"cols": cols, "equity": eq, "borrowings": debt, "debt_to_equity": de,
           "cwip": _pick(series(bs, "cwip"), idx), "is_bank": is_bank}
    if is_bank:
        out["deposits"] = _pick(series(bs, "deposits"), idx)
    pi = len(pl["columns"]) - 1
    op, oi, intr = (series(pl, k) for k in ("op_profit", "other_income", "interest"))
    if not is_bank and op and intr and pi < len(intr) and intr[pi]:
        out["interest_cover"] = _r(((op[pi] or 0) + ((oi or [0] * (pi + 1))[pi] or 0)) / intr[pi])
    ri = list(range(max(0, len(ra["columns"]) - 6), len(ra["columns"])))
    wc = {k: _pick(series(ra, k), ri) for k in ("debtor_days", "inventory_days", "payable_days", "ccc", "wc_days")}
    out["working_capital"] = {"cols": [ra["columns"][i] for i in ri], **{k: v for k, v in wc.items() if any(x is not None for x in v)}}
    read = []
    if is_bank:
        read.append("Bank: leverage is structural (deposits fund the book); judge capital adequacy and asset quality instead of D/E")
    elif de and de[-1] is not None:
        read.append(f"Debt/equity {de[-1]:.2f}× ({'net-cash-like' if de[-1] < 0.1 else 'moderate' if de[-1] < 1 else 'levered'})"
                    + (f", from {de[0]:.2f}× in {cols[0]}" if de[0] is not None and len(de) > 1 else ""))
    if out.get("interest_cover"):
        read.append(f"interest covered {out['interest_cover']:.1f}×")
    ccc = wc.get("ccc") or []
    if len([x for x in ccc if x is not None]) >= 2:
        a, b = next(x for x in ccc if x is not None), [x for x in ccc if x is not None][-1]
        read.append(f"cash conversion cycle {b:.0f} days ({'lengthening' if b - a > 10 else 'shortening' if a - b > 10 else 'steady'} vs {a:.0f})")
    out["read"] = "; ".join(read) + "." if read else None
    return out


def cash(data):
    from screener import series, col_to_period
    cf, pl, bs = data["tables"]["cash-flow"], data["tables"]["profit-loss"], data["tables"]["balance-sheet"]
    idx = list(range(max(0, len(cf["columns"]) - 7), len(cf["columns"])))
    cols = [cf["columns"][i] for i in idx]
    cfo = _pick(series(cf, "cfo"), idx)
    pl_pos = {c: i for i, c in enumerate(pl["columns"])}
    bs_pos = {c: i for i, c in enumerate(bs["columns"])}
    pat_s, dep_s = series(pl, "pat") or [], series(pl, "depreciation") or []
    fa_s, cw_s = series(bs, "fixed_assets") or [], series(bs, "cwip") or []
    g = lambda s, i: s[i] if i is not None and i < len(s) else None  # noqa: E731
    pat, conv, capex, fcf = [], [], [], []
    for c, o in zip(cols, cfo):
        p = g(pat_s, pl_pos.get(c))
        pat.append(p)
        conv.append(_r(o / p * 100, 0) if o is not None and p and p > 0 else None)
        bi = bs_pos.get(c)
        prev = None
        if bi is not None and col_to_period(c):
            y, m = col_to_period(c)
            prev = bs_pos.get(f"{c[:3]} {y - 1}")
        if bi is not None and prev is not None:
            net_now = (g(fa_s, bi) or 0) + (g(cw_s, bi) or 0)
            net_then = (g(fa_s, prev) or 0) + (g(cw_s, prev) or 0)
            cx = net_now - net_then + (g(dep_s, pl_pos.get(c)) or 0)
            capex.append(_r(cx, 0))
            fcf.append(_r(o - cx, 0) if o is not None else None)
        else:
            capex.append(None)
            fcf.append(None)
    pairs = [(o, p) for o, p in zip(cfo, pat) if o is not None and p][-5:]
    cum = _r(sum(o for o, _ in pairs) / sum(p for _, p in pairs) * 100, 0) if pairs and sum(p for _, p in pairs) > 0 else None
    out = {"cols": cols, "cfo": cfo, "pat": pat, "conversion_pct": conv, "capex_est": capex, "fcf_est": fcf,
           "cum_conversion_5y_pct": cum,
           "note": "Capex is estimated as Δ(net fixed assets + CWIP) + depreciation from the balance sheet."}
    if cum is not None:
        verdict = ("profits are turning into cash" if cum >= 85 else
                   "cash lags profits — check receivables and inventory" if cum >= 60 else
                   "profits are NOT turning into cash — a red flag to investigate")
        neg = sum(1 for x in fcf[-5:] if x is not None and x < 0)
        out["read"] = (f"Over the last {len(pairs)} years operating cash was {cum:.0f}% of PAT: {verdict}"
                       + (f"; estimated free cash flow negative in {neg} of them" if neg else "") + ".")
    else:
        out["read"] = None
    return out


# ---------------- document-backed (models) ----------------
def business(symbol, data=None):
    ar = _docs(symbol, "annual_report", 1)
    name = (data or {}).get("name") or symbol
    if not ar:
        raise ValueError(f"No annual report link found for {symbol}")
    text, pages = relevant_pages(ar[0]["url"], AR_KEYWORDS, budget_chars=16000, max_pages=120)
    out = ask_json(BUSINESS.format(name=name, t=text), FAST)
    out["source"] = {"label": ar[0]["label"], "url": ar[0]["url"], "pages_read": pages}
    return out


def guidance(symbol):
    docs = _docs(symbol, "transcript", 2)
    if not docs:
        raise ValueError(f"No concall transcripts found for {symbol}")
    calls = [_extract_call(d["url"]) for d in docs]  # newest first; same prompt as nvn => shared cache
    latest, prior = calls[0], calls[1] if len(calls) > 1 else None
    prev_metrics = {(g.get("metric") or "").lower() for g in (prior or {}).get("guidance", [])}
    items = [{**g, "new_this_call": (g.get("metric") or "").lower() not in prev_metrics} for g in latest.get("guidance", [])]
    return {"period": latest.get("period"), "items": items,
            "prior_period": prior.get("period") if prior else None,
            "dropped": [g for g in (prior or {}).get("guidance", [])
                        if (g.get("metric") or "").lower() not in {(x.get("metric") or "").lower() for x in latest.get("guidance", [])}],
            "sources": [{"period": c.get("period") or d["label"], "url": d["url"]} for c, d in zip(calls, docs)]}


def _actuals(symbol):
    """Actual reported quarterly figures (Screener table) so the judge can check promises against numbers."""
    try:
        from screener import series
        q = _fund(symbol)["tables"]["quarters"]
        cols, rev, op, pat = q["columns"], series(q, "revenue"), series(q, "op_profit"), series(q, "pat")
        out = []
        for i in range(max(4, len(cols) - 6), len(cols)):
            r, r4 = rev[i] if rev else None, rev[i - 4] if rev else None
            out.append({"quarter": cols[i], "revenue_cr": r,
                        "revenue_yoy_pct": _r((r / r4 - 1) * 100) if r and r4 else None,
                        "ebitda_margin_pct": _r(op[i] / r * 100) if op and r and op[i] is not None else None,
                        "pat_cr": pat[i] if pat else None})
        return out
    except Exception:
        return []


def narrative_vs_numbers(symbol, n=4):
    docs = conn().execute(
        "SELECT label,url FROM documents WHERE symbol=? AND kind='transcript' ORDER BY rowid ASC LIMIT ?",
        (symbol, n),
    ).fetchall()
    if not docs:
        raise ValueError(f"No concall transcripts found for {symbol}")
    ext = [_extract_call(d["url"]) for d in reversed(docs)]  # screener lists newest first
    actuals = _actuals(symbol)
    hint = " and the ACTUAL reported quarterly numbers (from the exchange filings) given at the end" if actuals else ""
    payload = ext + ([{"actual_quarterly_numbers": actuals}] if actuals else [])
    out = ask_json(JUDGE.format(d=payload, actuals_hint=hint), SMART)
    out["calls_used"] = [e.get("period") for e in ext]
    out["sources"] = [{"period": e.get("period") or d["label"], "url": d["url"]} for e, d in zip(ext, reversed(docs))]
    out["actuals"] = actuals
    out["symbol"] = symbol
    return out


def bull_bear(symbol, data=None):
    data = data or _fund(symbol)
    facts = {k: fn(data).get("read") for k, fn in
             (("financial", financial), ("trajectory", trajectory), ("balance_sheet", balance), ("cash", cash))}
    try:
        facts["guidance_latest_call"] = [f"{g.get('metric')}: {g.get('statement')}" for g in guidance(symbol)["items"]]
    except Exception:
        facts["guidance_latest_call"] = []
    out = ask_json(BULLBEAR.format(name=data.get("name") or symbol, f=facts), SMART)
    out["facts_used"] = facts
    return out


DETERMINISTIC = {"financial": financial, "trajectory": trajectory, "balance": balance, "cash": cash}
SECTIONS = [
    ("business", "Business snapshot", "1 fast-model call on the latest annual report"),
    ("financial", "Financial snapshot", "free — computed from filings"),
    ("trajectory", "Trajectory", "free — computed from filings"),
    ("balance", "Balance sheet", "free — computed from filings"),
    ("cash", "Cash quality", "free — computed from filings"),
    ("guidance", "Guidance", "2 fast-model calls on the latest transcripts"),
    ("nvn", "Promised vs delivered", "4 fast + 1 reasoning call; last 4 transcripts vs actual numbers"),
    ("bullbear", "Bull vs bear", "1 reasoning call on the facts above"),
]


def overview(symbol):
    symbol = symbol.upper()
    _ensure_docs(symbol)
    out = {"symbol": symbol, "sections": [{"key": k, "title": t, "cost": c} for k, t, c in SECTIONS],
           "model_ready": has_key(), "errors": {}}
    ar, tr = _docs(symbol, "annual_report", 3), _docs(symbol, "transcript", 4)
    out["documents"] = {"annual_reports": [dict(r) for r in ar], "transcripts": [dict(r) for r in tr]}
    try:
        data = _fund(symbol)
        out.update(name=data.get("name"), basis=data["variant"], screener_url=data["url"])
        for k, fn in DETERMINISTIC.items():
            try:
                out[k] = fn(data)
            except Exception as e:
                out["errors"][k] = f"{type(e).__name__}: {e}"
    except Exception as e:
        out["errors"]["fundamentals"] = str(e)
    return out


def section(symbol, key):
    symbol = symbol.upper()
    _ensure_docs(symbol)
    if key in DETERMINISTIC:
        return DETERMINISTIC[key](_fund(symbol))
    if key == "business":
        try:
            data = _fund(symbol)
        except Exception:
            data = None
        return business(symbol, data)
    if key == "guidance":
        return guidance(symbol)
    if key == "nvn":
        return narrative_vs_numbers(symbol)
    if key == "bullbear":
        return bull_bear(symbol)
    raise ValueError(f"Unknown section {key}")
