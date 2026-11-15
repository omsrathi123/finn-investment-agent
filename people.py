"""Management-change enrichment: don't just say "CFO resigned" — say who is coming in and where from.

Layered, cheapest first:
  1. Regex on the filing headline -> name, role, direction (appointed / resigned / retired). Free.
  2. The filing PDF itself. SEBI Reg. 30 requires a "brief profile" of an appointee in the filing,
     so the cheap model extracts background straight from the primary document.
  3. Web-grounded lookup (Gemini + Google Search) for a short professional bio with source links —
     the brief explicitly allows outside enrichment on the push side (e.g. LinkedIn).
Every layer is cached; a LinkedIn search link is always provided so the investor can verify.
"""
import json
import re
from urllib.parse import quote_plus

from db import conn
from llm import ask_grounded, ask_json, has_key, FAST
from pdftext import pdf_text
from taxonomy import clean_text

TITLE = r"(?:Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Shri|Smt\.?|Prof\.?|CA|Sri)"
NAME = r"([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,4})"
HEAD = re.compile(
    rf"(appointment|re-?appointment|resignation|retirement|cessation|demise|redesignation)\s+of\s+(?:{TITLE}\s*)?{NAME}"
    rf"(?:\s*\([^)]*\))?(?:(?:,?\s+(?:as|asthe|from the (?:post|position) of|from)\s+(?:the\s+)?|,\s+)([^.;]+?))?(?=\s+(?:with effect|w\.?e\.?f|effective|of the company|of [A-Z][\w&]+ (?:Bank|Limited|Ltd)|identified|and\s+(?:Vice|the))|[.;(]|$)",
    re.I)

PROMPT = """This is a stock-exchange filing about a management change at {company}. Use ONLY the text.
Return JSON: {{"people":[{{"name":"","role":"","change":"appointed|resigned|retired|redesignated|ceased",
"effective":"date or null","incoming":true,"background":"<=45 words: prior roles/employers, education, years of experience, exactly as stated",
"prior_roles":["..."],"education":"","reason":"stated reason for exit or null"}}]}}
List every person whose position changes. Use null where the filing is silent.
TEXT:
{t}"""

BIO = """Give a short professional background of {name}, who is {role} at {company} (India).
Write 3 bullet points, max 20 words each: current/previous employers and roles, education, one notable fact.
Only state facts you can find in search results. If you cannot identify this exact person, say "Not found"."""


def headline_people(text):
    out = []
    for m in HEAD.finditer(clean_text(text)):
        verb = m.group(1).lower()
        change = ("appointed" if "appoint" in verb else "resigned" if "resign" in verb else
                  "retired" if "retire" in verb else "redesignated" if "redesig" in verb else "ceased")
        name = re.sub(rf"^{TITLE}\s*", "", m.group(2).strip(), flags=re.I)
        if len(name.split()) < 2 and not name.isupper():
            continue
        role = re.sub(r"^(holding|having) DIN\s*\w+,?\s*(as\s*(the\s*)?)?", "", (m.group(3) or "").strip(" ,"), flags=re.I)
        if any(p["name"].lower() == name.lower() for p in out):
            continue
        out.append({"name": name, "role": role or None, "change": change, "incoming": change == "appointed"})
    return out


def linkedin_url(name, company):
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(f"{name} {company}")


def profile(ann_id, web=True):
    row = conn().execute("SELECT * FROM announcements WHERE id=?", (ann_id,)).fetchone()
    if not row:
        raise ValueError("announcement not found")
    try:
        raw = json.loads(row["raw"] or "{}")
    except ValueError:
        raw = {}
    company = raw.get("sm_name") or row["symbol"]
    text = " ".join(filter(None, [row["subject"], row["detail"], raw.get("voiceSummary")]))
    people, notes, layer = headline_people(text), [], "headline"

    if row["attachment_url"]:
        try:
            got = ask_json(PROMPT.format(company=company, t=pdf_text(row["attachment_url"], 12000)), FAST)
            if got.get("people"):
                people, layer = got["people"], "filing"
        except Exception as e:
            notes.append("Filing PDF not read (no model key)" if not has_key() else
                         f"Filing PDF not read ({type(e).__name__})")

    for p in people:
        p["linkedin"] = linkedin_url(p.get("name", ""), company)
        p["web"] = None
    if web and has_key():
        for p in [p for p in people if p.get("name")][:2]:
            try:
                bio = ask_grounded(BIO.format(name=p["name"], role=p.get("role") or "a senior executive",
                                              company=company), FAST)
                if bio["text"] and not bio["text"].lower().startswith("not found"):
                    p["web"] = bio
            except Exception as e:
                notes.append(f"Web background lookup failed ({type(e).__name__})")
    # incoming people first: that is the question the investor has
    people.sort(key=lambda p: not p.get("incoming"))
    return {"id": ann_id, "symbol": row["symbol"], "company": company, "people": people,
            "layer": layer, "notes": notes, "source_url": row["attachment_url"], "ts": row["ts"]}
