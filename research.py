"""Pull: narrative vs numbers from the last 4 concall transcripts ONLY (primary source). Fast model extracts per call,
smart model judges promised-vs-delivered. Everything cached; works for any symbol screener.in covers."""
from db import conn
from llm import ask_json, FAST, SMART
from pdftext import pdf_text
EXTRACT = """You are reading ONE earnings-call transcript. Use ONLY this text; no outside knowledge. Return JSON:
{{"period":"quarter discussed e.g. Q1 FY27","guidance":[{{"metric":"","statement":"forward-looking claim/target management made","quote":"<=20 words verbatim"}}],
"reported":[{{"metric":"","value":"actual figure/growth management stated for the quarter just reported","quote":"<=20 words verbatim"}}]}}
Max 6 items each; headline metrics only (growth, margins, capex, debt, volumes).
TEXT:
{t}"""
JUDGE = """Below are extractions from a company's last concalls (oldest to newest). Judge what management PROMISED vs what was
later DELIVERED, using only these extractions. Return JSON:
{{"verdict":"one sentence, blunt","items":[{{"promise":"","promised_in":"period","delivered":"what the later calls/numbers show","status":"met|missed|partial|unclear","quote":"supporting quote"}}],"watch":"one thing to watch next"}}
Max 6 items. Do not invent numbers.
DATA:
{d}"""
def narrative_vs_numbers(symbol, n=4):
    # fetch_docs stores Screener's newest-first order; rowid preserves that order.
    docs = conn().execute(
        "SELECT label,url FROM documents WHERE symbol=? AND kind='transcript' ORDER BY rowid ASC LIMIT ?",
        (symbol, n),
    ).fetchall()
    if not docs: raise ValueError(f"No concall transcripts found for {symbol}")
    ext = []
    for d in reversed(docs):  # screener lists newest first
        e = ask_json(EXTRACT.format(t=pdf_text(d["url"], 14000)), FAST)
        e["source"] = d["url"]; ext.append(e)
    out = ask_json(JUDGE.format(d=ext), SMART)
    out["calls_used"] = [e.get("period") for e in ext]
    out["sources"] = [
        {"period": e.get("period") or d["label"], "url": d["url"]}
        for e, d in zip(ext, reversed(docs))
    ]
    out["symbol"] = symbol
    return out
