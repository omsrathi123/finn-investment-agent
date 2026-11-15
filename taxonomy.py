"""Announcement taxonomy + materiality scoring. Rules, not an LLM: free, instant, auditable.

Maps an NSE/BSE filing onto the 17 categories in the challenge reference document, gives it a
0-100 materiality score, a tier (critical / notable / routine), a one-line summary and a
"why you should care" line. The LLM is reserved for what rules can't do (reading PDFs).
"""
import html
import re

# Reference-document categories, with display labels.
CATEGORIES = {
    "results": "Results",
    "ma": "M&A",
    "fund_raise": "Fund raise",
    "management_change": "Management change",
    "litigation": "Litigation / regulatory",
    "insider_trading": "Insider trading",
    "earnings_call": "Earnings call",
    "investor_meetings": "Investor meetings",
    "credit_rating": "Credit rating",
    "dividend": "Dividend",
    "bonus_split": "Bonus / split",
    "capex": "Capex",
    "press_release": "Press release",
    "postal_ballot": "Postal ballot",
    "agm_egm": "AGM / EGM",
    "pledging": "Pledging",
    "unclassified": "Routine",
}

# Exact NSE "desc" labels -> category (checked first; NSE's own label is the strongest signal).
NSE_DESC = {
    "financial result updates": "results",
    "integrated filing- financial": "results",
    "outcome of board meeting": None,  # ambiguous: decided by text below
    "board meeting intimation": None,
    "change in management": "management_change",
    "change in director(s)": "management_change",
    "change in directorate": "management_change",
    "retirement": "management_change",
    "resignation": "management_change",
    "appointment": "management_change",
    "cessation": "management_change",
    "acquisition": "ma",
    "amalgamation/merger": "ma",
    "scheme of arrangement": "ma",
    "other restructuring": "ma",
    "joint venture": "ma",
    "sale or disposal": "ma",
    "allotment of securities": "fund_raise",
    "issue of securities": "fund_raise",
    "buy back": "fund_raise",
    "qualified institutional placement": "fund_raise",
    "rights issue": "fund_raise",
    "preferential issue": "fund_raise",
    "action(s) taken or orders passed": "litigation",
    "pendency of litigation(s)/dispute(s) or the outcome impacting the company": "litigation",
    "corporate insolvency resolution process": "litigation",
    "fraud/default/arrest": "litigation",
    "insider trading / sast": "insider_trading",
    "acquisition/disposal of shares": "insider_trading",
    "analysts/institutional investor meet/con. call updates": None,  # call vs meeting: text
    "investor presentation": "earnings_call",
    "credit rating": "credit_rating",
    "dividend": "dividend",
    "record date": "dividend",
    "bonus": "bonus_split",
    "split": "bonus_split",
    "sub-division": "bonus_split",
    "bagging/receiving of orders/contracts": "press_release",
    "press release": "press_release",
    "rumour verification - regulation 30(11)": "press_release",
    "news verification": "press_release",
    "updates": "press_release",
    "general updates": None,
    "postal ballot": "postal_ballot",
    "shareholders meeting": None,
    "annual general meeting": "agm_egm",
    "extra ordinary general meeting": "agm_egm",
    "pledge": "pledging",
    "copy of newspaper publication": None,
    "esop/esos/esps": "unclassified",
    "trading window": "unclassified",
    "committee meeting updates": "unclassified",
    "monitoring agency report": "unclassified",
    "statement of deviation(s) or variation(s) under reg. 32": "unclassified",
    "certificate under sebi (depositories and participants) regulations, 2018": "unclassified",
    "loss of share certificates": "unclassified",
    "duplicate share certificate": "unclassified",
    "shareholding pattern": "unclassified",
    "compliance certificate": "unclassified",
}

# Free-text rules, in priority order (first hit wins).
TEXT_RULES = [
    ("results", r"financial results?|quarterly results?|unaudited.*results|audited.*results|results? for the quarter"),
    ("pledging", r"\bpledg|encumbrance"),
    ("insider_trading", r"insider trading|sast|regulation 7\(2\)|regulation 29|disposal of shares by promoter|acquisition of shares by promoter"),
    ("management_change", r"resign|cessation|retire|appoint|re-?designat|chief (executive|financial|operating)|managing director|\bcfo\b|\bceo\b|\bcoo\b|key managerial|senior management|whole[- ]time director"),
    ("ma", r"acqui|merger|amalgam|demerg|scheme of arrangement|joint venture|\bstake\b|divest|disinvest|slump sale|subsidiary.*(incorporat|invest)"),
    ("fund_raise", r"buy-?back|\bqip\b|qualified institutional|rights issue|preferential (issue|allotment)|fund ?rais|commercial paper|non[- ]convertible|debentures?|\bncds?\b|bonds?\b"),
    ("litigation", r"litigation|penalty|show cause|insolvency|nclt|nclat|\border passed\b|gst order|tax demand|demand order|sebi order|warning letter|fraud|default|raid|search and seizure|arbitration|court"),
    ("credit_rating", r"credit rating|rating agency|\b(crisil|icra|care ratings|india ratings|moody|fitch|s&p)\b"),
    ("bonus_split", r"\bbonus\b|stock split|sub-?division"),
    ("dividend", r"dividend"),
    ("capex", r"capex|capital expenditure|new plant|greenfield|brownfield|capacity expansion|commission(ing|ed) of"),
    ("earnings_call", r"transcript|earnings call|conference call|con\. ?call|audio recording|investor presentation"),
    ("investor_meetings", r"analyst|investor(s)? meet|institutional investor|one-on-one|non-deal roadshow|conference"),
    ("postal_ballot", r"postal ballot|e-?voting"),
    ("agm_egm", r"annual general meeting|\bagm\b|\begm\b|extra-?ordinary general"),
    ("press_release", r"order|contract|award|launch|partnership|press release|media statement|clarification|rumou?r|news"),
]

ROUTINE_TEXT = (r"trading window|newspaper|certificate|reg(ulation)?\.? ?74|loss of share|duplicate|"
                r"closure of|compliance|shareholding pattern|esop|esos|esps|monitoring agency|"
                r"statement of deviation|book closure|change in (registrar|rta)|address change")

BASE_SCORE = {
    "results": 90, "ma": 82, "management_change": 72, "litigation": 70, "pledging": 75,
    "fund_raise": 66, "insider_trading": 58, "credit_rating": 48, "bonus_split": 60,
    "capex": 64, "dividend": 40, "press_release": 36, "earnings_call": 26,
    "investor_meetings": 10, "postal_ballot": 14, "agm_egm": 18, "unclassified": 4,
}

WHY = {
    "results": "Quarterly numbers are out — open for Revenue, EBITDA, PBT and PAT vs last quarter and last year.",
    "ma": "Changes what the company owns. Check deal size against market cap and how it is funded.",
    "management_change": "Who runs the company changed. See who is coming in and where they came from.",
    "litigation": "A legal or regulatory action. Check the amount involved and whether it is provisioned.",
    "pledging": "Promoter collateral changed. Rising pledges are a risk signal.",
    "fund_raise": "Share count or debt changes. Check dilution and what the money is for.",
    "insider_trading": "An insider traded their own shares — direction and size matter.",
    "credit_rating": "A rating agency's view of the company's debt.",
    "bonus_split": "Share count changes; no cash moves. Mostly mechanical.",
    "capex": "New capacity spend — a forward signal on growth and on cash.",
    "dividend": "Cash returned to shareholders. Note the record date.",
    "press_release": "Company news outside the standard categories.",
    "earnings_call": "Concall material — transcripts feed the research view.",
    "investor_meetings": "Routine investor meeting; no new information is disclosed at these.",
    "postal_ballot": "Shareholder vote paperwork.",
    "agm_egm": "General meeting paperwork.",
    "unclassified": "Routine compliance paperwork.",
}

_BOILER = re.compile(
    r"^(.*?has (informed|submitted to) the exchange,? (about|regarding|that|with|on)?\s*(the\s*)?|"
    r"'+|\d{4},\s*|in continuation (of|to) our letter[^,]*,\s*|(we|this is to) (wish to |hereby )?(inform|intimate) you that\s*|"
    r"please find (enclosed|attached)( herewith)?\s*|we attach\s*|pursuant to regulation \d+[^,]*,\s*|"
    r"inter alia,?\s*)",
    re.I)


_ABBR = re.compile(r"(\b(Mr|Ms|Mrs|Dr|Shri|Smt|No|Rs|Ltd|Co|Pvt|Inc|viz|i\.e|e\.g)|\b[A-Z])\.$")


class _Sentences:
    """Sentence splitter that doesn't break on 'Mr. V. Rangan' or 'Rs. 400'."""
    _ws = re.compile(r"(?<=[.!?])\s+")

    def split(self, text):
        out = []
        for piece in self._ws.split(text or ""):
            if out and _ABBR.search(out[-1]):
                out[-1] += " " + piece
            else:
                out.append(piece)
        return out


SENT = _Sentences()


def clean_text(text):
    t = html.unescape(text or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()


def one_liner(detail, voice_summary=None, desc=None, limit=220):
    """Best free summary available: NSE's own summary, else the filing text minus boilerplate.
    Returns None when the text only restates the category (then the filing itself must be read)."""
    vs = clean_text(voice_summary)
    own = _from_detail(detail, desc, limit)
    if own and len(own) >= 50:  # the filing's own words, when they say something, beat a generated summary
        return own
    if vs:
        first = SENT.split(vs)
        out = first[0] if len(first[0]) > 60 or len(first) == 1 else " ".join(first[:2])
        return out[:limit]
    return own


def _from_detail(detail, desc, limit):
    t = clean_text(detail)
    for _ in range(4):
        t2 = _BOILER.sub("", t, count=1).strip(" .:-,")
        if t2 == t:
            break
        t = t2
    if not t:
        return None
    label = (desc or "").lower().strip()
    if t.lower().strip(" .") in (label, label.rstrip("s"), "updates", "general updates") or len(t) < 12:
        return None
    t = t[0].upper() + t[1:]
    return t if len(t) <= limit else t[:limit - 1].rsplit(" ", 1)[0] + "…"


def categorize(desc, subject="", detail=""):
    d = (desc or "").strip().lower()
    text = f"{desc} {subject} {detail}".lower()
    cat = NSE_DESC.get(d, "unset")
    if cat not in (None, "unset"):
        # NSE label is specific; but a "press release" about results is results.
        if cat == "press_release" and re.search(TEXT_RULES[0][1], text):
            return "results"
        return cat
    if d == "copy of newspaper publication":
        return "results" if re.search(TEXT_RULES[0][1], text) else "unclassified"
    if d == "shareholders meeting":
        return "postal_ballot" if "postal" in text else "agm_egm"
    if d == "analysts/institutional investor meet/con. call updates":
        body = f"{subject} {detail}".lower().replace(d, "")
        return "earnings_call" if re.search(r"transcript|recording|audio|earnings call|conference call (is|will|scheduled|on)|con\. ?call (is|on|scheduled)", body) else "investor_meetings"
    for c, pat in TEXT_RULES:
        if re.search(pat, text):
            return c
    return "unclassified"


def score(cat, desc="", subject="", detail=""):
    text = f"{desc} {subject} {detail}".lower()
    s = BASE_SCORE[cat]
    if re.search(ROUTINE_TEXT, text) and cat not in ("litigation", "ma"):
        s = min(s, 15)  # e.g. newspaper ad confirming results were published
    if cat == "management_change":
        if re.search(r"\b(ceo|cfo|coo|managing director|chief executive|chief financial|whole[- ]time director|chairman)\b", text):
            s = 88
        elif re.search(r"senior (management|vice)|\bsmp\b|head\b", text):
            s = 52
        if re.search(r"resign|cessation", text):
            s += 4  # exits are more informative than routine appointments
        elif re.search(r"re-?appoint|continuation of|approval .* for (the )?re-?appointment", text):
            s = min(s, 45)  # a renewal of someone already in the seat
    if cat == "credit_rating":
        if re.search(r"downgrad|negative|watch", text):
            s = 80
        elif re.search(r"upgrad", text):
            s = 60
    if cat == "litigation" and re.search(r"insolvency|fraud|default|arrest|raid|suspend", text):
        s = 85
    if cat == "press_release":
        if re.search(r"order|contract", text):
            s = 52
        if re.search(r"clarif|rumou?r|news (item|report)|verification", text):
            s = 62  # the exchange asked the company to respond to a news report
    if cat == "ma" and re.search(r"incorporat\w* of (a )?(wholly[- ]owned )?subsidiary|wholly owned subsidiary", text) and not re.search(r"acquisition of|merger|amalgam", text):
        s = 50
    if cat == "results" and re.search(r"outcome of board|financial results", text) and s > 15:
        s = 92
    if cat == "dividend" and "record date" in text:
        s = 30
    if cat == "earnings_call" and "transcript" in text:
        s = 32
    return max(0, min(100, s))


def tier_of(s):
    return "critical" if s >= 70 else "notable" if s >= 35 else "routine"


def analyse(desc, subject="", detail="", voice_summary=None):
    cat = categorize(desc, subject, detail)
    s = score(cat, desc, subject, f"{detail or ''} {voice_summary or ''}")
    return {
        "category": cat,
        "label": CATEGORIES[cat],
        "score": s,
        "tier": tier_of(s),
        "summary": one_liner(detail, voice_summary, desc),
        "why": WHY[cat],
    }


def classify(category, subject, detail=""):
    """Backwards-compatible (tier, kind) used by stock detail and older callers."""
    a = analyse(category, subject, detail)
    return a["tier"], a["category"]
