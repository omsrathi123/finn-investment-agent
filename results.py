"""Quarterly-results card.

  1. The actual results filing (PDF) -> headline numbers via the cheap model (JSON, cached).
  2. QoQ / YoY are computed in code, never by the model.
  3. Cross-check against Screener's standardised quarterly table: if both agree within 3 % we say
     so; if they disagree we show both — that is where an investor should look twice.
  4. No API key / unreadable PDF -> fall back to the Screener table, clearly labelled.
"""
from datetime import datetime

from db import conn
from digest import parse_ts
from llm import ask_json, has_key, FAST
from pdftext import pdf_text

PROMPT = """From this Indian company's quarterly results filing, extract the headline numbers for the CONSOLIDATED
(or standalone if no consolidated) statement. Use ONLY the text. Return JSON:
{{"quarter":"e.g. Q2 FY27","period_end":"YYYY-MM","basis":"consolidated|standalone","unit":"Rs crore",
"revenue":{{"cur":n,"prev_q":n,"yoy":n}},"ebitda":{{"cur":n,"prev_q":n,"yoy":n}},
"pbt":{{"cur":n,"prev_q":n,"yoy":n}},"pat":{{"cur":n,"prev_q":n,"yoy":n}}}}
cur=this quarter, prev_q=immediately preceding quarter, yoy=same quarter last year. Revenue = revenue from operations.
EBITDA = PBT + finance cost + depreciation - other income if not stated. PAT = net profit for the period.
Use null if absent. Convert to Rs crore.
TEXT:
{t}"""
METRICS = [("revenue", "Revenue"), ("ebitda", "EBITDA"), ("pbt", "PBT"), ("pat", "PAT")]


def _pct(a, b):
    return None if a is None or not b else round((a / b - 1) * 100, 1)


def _margin(num, den):
    return None if num is None or not den else round(num / den * 100, 1)


def _screener_quarter(symbol, period):
    try:
        import screener
        return screener.quarter_numbers(screener.company(symbol), period)
    except Exception:
        return None


def _rows(d):
    rows = []
    for k, label in METRICS:
        v = d.get(k) or {}
        rows.append(dict(metric=label, key=k, cur=v.get("cur"), prev_q=v.get("prev_q"), yoy_base=v.get("yoy"),
                         qoq=_pct(v.get("cur"), v.get("prev_q")), yoy=_pct(v.get("cur"), v.get("yoy"))))
    return rows


def _headline(rows):
    by = {r["key"]: r for r in rows}
    rev, ebitda, pat = by["revenue"], by["ebitda"], by["pat"]
    bits = []
    if rev["yoy"] is not None:
        bits.append(f"Revenue {rev['yoy']:+.1f}% YoY")
    if pat["yoy"] is not None:
        bits.append(f"PAT {pat['yoy']:+.1f}% YoY")
    m_now, m_then = _margin(ebitda["cur"], rev["cur"]), _margin(ebitda["yoy_base"], rev["yoy_base"])
    if m_now is not None and m_then is not None:
        bits.append(f"EBITDA margin {m_now:.1f}% ({m_now - m_then:+.1f}pp YoY)")
    elif m_now is not None:
        bits.append(f"EBITDA margin {m_now:.1f}%")
    return " · ".join(bits) or None


def result_card(ann_id):
    row = conn().execute("SELECT * FROM announcements WHERE id=?", (ann_id,)).fetchone()
    if not row:
        raise ValueError("announcement not found")
    t = parse_ts(row["ts"]) or datetime.now()
    from screener import filing_quarter
    period = filing_quarter(t)
    extracted, source, note = None, None, None
    if row["attachment_url"]:
        try:  # cached extractions work even without a key
            extracted = ask_json(PROMPT.format(t=pdf_text(row["attachment_url"], 20000)), FAST)
            source = "filing"
        except Exception as e:
            note = ("No model key configured, so the filing PDF was not read; numbers are from Screener's "
                    "quarterly table." if not has_key() else
                    f"Could not read the filing ({type(e).__name__}); showing Screener's table instead.")
    if extracted and extracted.get("period_end"):
        try:
            y, m = extracted["period_end"].split("-")[:2]
            period = (int(y), int(m))
        except ValueError:
            pass

    sq = _screener_quarter(row["symbol"], period)
    if extracted is None:
        if not sq:
            raise ValueError(("The filing PDF needs a model key to read" if not has_key() else "The filing couldn't be read")
                             + ", and Screener's quarterly table isn't reachable right now. Open the filing to see the numbers.")
        extracted = {"quarter": sq["quarter_col"], "unit": "Rs crore", **sq["metrics"]}
        source = "screener"

    rows = _rows(extracted)
    check = None
    if source == "filing" and sq:
        diffs = []
        for r in rows:
            other = (sq["metrics"].get(r["key"]) or {}).get("cur")
            if r["cur"] is not None and other:
                diffs.append((r["metric"], round((r["cur"] / other - 1) * 100, 1), other))
        if diffs:
            bad = [d for d in diffs if abs(d[1]) > 3]
            check = {"agrees": not bad, "against": f"Screener {sq['quarter_col']}",
                     "detail": [{"metric": m, "diff_pct": p, "screener": o} for m, p, o in diffs]}
    return dict(symbol=row["symbol"], quarter=extracted.get("quarter"), basis=extracted.get("basis"),
                unit=extracted.get("unit", "Rs crore"), source=source, note=note,
                source_url=row["attachment_url"], source_ts=row["ts"], rows=rows,
                headline=_headline(rows), cross_check=check)
