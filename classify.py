"""Tiering by rules (free, instant, auditable). LLM is reserved for what rules can't do (extraction)."""
import re
ROUTINE = r"trading window|newspaper|certificate|reg(ulation)?\.? ?74|loss of share|duplicate|closure of|compliance|record date|schedule of analyst|intimation of (board|analyst)|shareholding pattern"
KINDS = [
 ("results", r"financial result|outcome of board|quarterly result|unaudited"),
 ("mgmt",    r"resign|cessation|appointment|change in (director|management)|key managerial|cfo|ceo"),
 ("ma",      r"acqui|merger|amalgam|scheme of arrangement|demerger|stake|joint venture"),
 ("capital", r"buyback|bonus|split|dividend|fund ?raising|qip|rights issue|preferential"),
 ("risk",    r"fraud|default|insolvency|penalty|raid|litigation|show cause|downgrad"),
 ("order",   r"order|contract|award"),
 ("rating",  r"credit rating|rating"),
]
CRITICAL = {"results", "mgmt", "ma", "capital", "risk"}
def classify(category, subject, detail=""):
    t = f"{category} {subject} {detail}".lower()
    if re.search(ROUTINE, t) and not re.search(r"financial result", t): return "routine", "routine"
    for kind, pat in KINDS:
        if re.search(pat, t): return ("critical" if kind in CRITICAL else "notable"), kind
    return "routine", "other"
