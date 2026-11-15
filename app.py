from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from db import conn
from classify import classify
from alerts import spikes
app = FastAPI(title="FINN")

@app.get("/healthz")
def health():
    conn().execute("SELECT 1").fetchone()
    return {"status": "ok"}

def _parse(ts):
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d-%b-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try: return datetime.strptime((ts or "")[:26].strip(), f)
        except ValueError: pass

@app.get("/api/feed")
def feed(hours: int = 24):
    cutoff = datetime.now() - timedelta(hours=hours)
    tiers = {"critical": [], "notable": [], "routine": []}
    for r in conn().execute("SELECT * FROM announcements ORDER BY ts DESC"):
        t = _parse(r["ts"])
        if t and t < cutoff: continue
        tier, kind = classify(r["category"], r["subject"], r["detail"])
        tiers[tier].append(dict(id=r["id"], symbol=r["symbol"], ts=r["ts"], kind=kind, title=r["subject"] or r["category"],
                                detail=(r["detail"] or "")[:240], url=r["attachment_url"], source=r["source"]))
    return dict(hours=hours, counts={k: len(v) for k, v in tiers.items()}, **tiers)

@app.get("/api/alerts")
def alerts(vol: float = 2.0, move: float = 4.0): return spikes(vol, move)

@app.get("/api/status")
def status():
    c = conn()
    prices_through = c.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    filing_times = [
        parsed for (ts,) in c.execute("SELECT ts FROM announcements WHERE source != 'DEMO'")
        if (parsed := _parse(ts)) is not None
    ]
    demo_filings = c.execute(
        "SELECT COUNT(*) FROM announcements WHERE source='DEMO'"
    ).fetchone()[0]
    return {
        "prices_through": prices_through,
        "filings_through": max(filing_times).isoformat(sep=" ") if filing_times else None,
        "demo_filings": demo_filings,
    }

@app.get("/api/results/{ann_id:path}")
def results(ann_id: str):
    from results import result_card
    try: return result_card(ann_id)
    except Exception as e: raise HTTPException(500, str(e))

@app.get("/api/research/{symbol}")
def research(symbol: str):
    from research import narrative_vs_numbers
    try: return narrative_vs_numbers(symbol.upper())
    except Exception as e: raise HTTPException(500, str(e))

@app.get("/api/universe")
def universe(): return [r[0] for r in conn().execute("SELECT DISTINCT symbol FROM prices ORDER BY 1")]

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
@app.get("/")
def index(): return FileResponse(Path(__file__).parent / "static" / "index.html")
