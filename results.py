"""Quarterly-results card: filing PDF -> structured JSON (cheap model) -> QoQ/YoY computed in code, not by the LLM."""
from db import conn
from llm import ask_json, FAST
from pdftext import pdf_text
PROMPT = """From this Indian company's quarterly results filing, extract the headline numbers for the CONSOLIDATED
(or standalone if no consolidated) statement. Use ONLY the text. Return JSON:
{{"quarter":"e.g. Q2 FY27","unit":"Rs crore","revenue":{{"cur":n,"prev_q":n,"yoy":n}},"ebitda":{{"cur":n,"prev_q":n,"yoy":n}},
"pbt":{{"cur":n,"prev_q":n,"yoy":n}},"pat":{{"cur":n,"prev_q":n,"yoy":n}}}}
cur=this quarter, prev_q=immediately preceding quarter, yoy=same quarter last year. EBITDA = PBT + finance cost + depreciation - other income if not stated.
Use null if absent. Convert to Rs crore.
TEXT:
{t}"""
def _pct(a, b): return None if a is None or not b else round((a / b - 1) * 100, 1)
def result_card(ann_id):
    row = conn().execute("SELECT * FROM announcements WHERE id=?", (ann_id,)).fetchone()
    if not row or not row["attachment_url"]: raise ValueError("no attachment for this announcement")
    d = ask_json(PROMPT.format(t=pdf_text(row["attachment_url"], 20000)), FAST)
    rows = []
    for k, label in [("revenue", "Revenue"), ("ebitda", "EBITDA"), ("pbt", "PBT"), ("pat", "PAT")]:
        v = d.get(k) or {}
        rows.append(dict(metric=label, cur=v.get("cur"), qoq=_pct(v.get("cur"), v.get("prev_q")), yoy=_pct(v.get("cur"), v.get("yoy"))))
    return dict(symbol=row["symbol"], quarter=d.get("quarter"), unit=d.get("unit", "Rs crore"),
                source_url=row["attachment_url"], source_ts=row["ts"], rows=rows)
