import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import digest
from alerts import spikes
from config import PORTFOLIO
from db import conn

STATIC = Path(__file__).parent / "static"
SERVERLESS = os.getenv("VERCEL") == "1"


def _boot():
    """Fresh deploy: load the committed real-data snapshot; simulated seed only as a last resort."""
    try:
        import snapshot
        snapshot.load()
        if not conn().execute("SELECT 1 FROM prices LIMIT 1").fetchone():
            from seed_demo import seed_demo
            seed_demo()
    except Exception as e:  # never block startup on data
        print(f"[boot] {type(e).__name__}: {e}")


if os.getenv("FINN_NO_BOOT") != "1":
    _boot()


@asynccontextmanager
async def lifespan(_app):
    if not SERVERLESS and os.getenv("FINN_LIVE", "1") != "0":
        import push
        push.start_background()  # long-lived server: the watcher runs on its own
    yield


app = FastAPI(title="FINN", lifespan=lifespan)


def _err(e, code=500):
    raise HTTPException(code, str(e))


@app.get("/healthz")
def health():
    conn().execute("SELECT 1").fetchone()
    return {"status": "ok"}


# ---------------- push: digest ----------------
@app.get("/api/feed")
def feed(hours: int = 24, start: str = None, end: str = None):
    return digest.build(hours=hours, start=start, end=end, db=conn())


@app.get("/api/brief")
def brief(hours: int = 24):
    from ask import brief as _brief
    return _brief(hours)


@app.get("/api/categories")
def categories():
    from taxonomy import CATEGORIES, WHY
    return [{"key": k, "label": v, "why": WHY[k]} for k, v in CATEGORIES.items()]


# ---------------- push: triggered alerts ----------------
@app.get("/api/rules")
def get_rules():
    import push
    return push.get_rules()


@app.put("/api/rules")
def put_rules(rules: dict = Body(...)):
    import push
    try:
        return push.save_rules(rules)
    except (TypeError, ValueError) as e:
        _err(e, 400)


@app.get("/api/inbox")
def inbox():
    import push
    alerts, new = push.evaluate()
    return {"alerts": alerts, "unread": sum(not a["seen"] for a in alerts), "new": len(new),
            "rules": push.get_rules(), "watcher": push.status()}


@app.post("/api/inbox/seen")
def inbox_seen(ids: list = Body(default=None, embed=True)):
    import push
    push.mark_seen(ids)
    return {"ok": True}


@app.get("/api/push/poll")
def push_poll(since: str = None, live: int = 1):
    """Called by the open app every minute. Runs the (throttled) live NSE poll + EOD check, then
    returns alerts fired after `since` so the client can raise a system notification."""
    import push
    report = push.tick(live=bool(live))
    alerts, _ = push.evaluate()
    fresh = [a for a in alerts if not since or (a.get("fired_at") or "") > since]
    return {"alerts": fresh[:20], "unread": sum(not a["seen"] for a in alerts), "watcher": push.status(),
            "server_time": digest.ist_now().isoformat(sep=" ", timespec="seconds"),
            "live": (report or {}).get("live")}


@app.post("/api/live/refresh")
def live_refresh():
    import push
    r = push.tick(force=True)
    return {"live": r.get("live"), "new": r.get("new", []), "watcher": push.status()}


@app.get("/api/alerts")
def alerts(vol: float = 2.0, move: float = 4.0):
    return spikes(vol, move)


# ---------------- status ----------------
@app.get("/api/status")
def status():
    c = conn()
    prices_through = c.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    filing_times = [
        parsed for (ts,) in c.execute("SELECT ts FROM announcements WHERE source != 'DEMO'")
        if (parsed := digest.parse_ts(ts)) is not None
    ]
    demo_filings = c.execute("SELECT COUNT(*) FROM announcements WHERE source='DEMO'").fetchone()[0]
    out = {
        "prices_through": prices_through,
        "filings_through": max(filing_times).isoformat(sep=" ") if filing_times else None,
        "demo_filings": demo_filings,
    }
    try:
        import push
        from llm import has_key, usage
        out.update(watcher=push.status(), model_ready=has_key(), usage=usage())
    except Exception:
        pass
    return out


@app.get("/api/usage")
def usage():
    from llm import usage as _usage, FAST, SMART
    return {"fast_model": FAST, "smart_model": SMART, "by_model": _usage()}


# ---------------- push enrichment ----------------
@app.get("/api/results/{ann_id:path}")
def results(ann_id: str):
    from results import result_card
    try:
        return result_card(ann_id)
    except ValueError as e:
        _err(e, 404)
    except Exception as e:
        _err(e)


@app.get("/api/people/{ann_id:path}")
def people(ann_id: str, web: int = 1):
    from people import profile
    try:
        return profile(ann_id, web=bool(web))
    except ValueError as e:
        _err(e, 404)
    except Exception as e:
        _err(e)


# ---------------- pull: research ----------------
@app.get("/api/research/{symbol}")
def research(symbol: str):
    import research as r
    try:
        return r.overview(symbol.upper())
    except Exception as e:
        _err(e)


@app.get("/api/research/{symbol}/{section}")
def research_section(symbol: str, section: str):
    import research as r
    try:
        return r.section(symbol.upper(), section)
    except ValueError as e:
        _err(e, 404)
    except Exception as e:
        _err(e)


@app.get("/api/ask")
def ask(q: str):
    from ask import answer
    return answer(q)


# ---------------- portfolio ----------------
@app.get("/api/config")
def config_view():
    from config import RESEARCH_EXTRA
    from llm import has_key
    return {"portfolio": list(PORTFOLIO), "research_extra": RESEARCH_EXTRA, "model_ready": has_key()}


@app.get("/api/universe")
def universe():
    return [r[0] for r in conn().execute("SELECT DISTINCT symbol FROM prices ORDER BY 1")]


@app.get("/api/portfolio")
def portfolio():
    c = conn()
    stocks = []
    for symbol in PORTFOLIO:
        rows = c.execute("SELECT date,close,volume FROM prices WHERE symbol=? ORDER BY date DESC LIMIT 30",
                         (symbol,)).fetchall()
        price = rows[0]["close"] if rows else None
        previous = rows[1]["close"] if len(rows) > 1 else None
        stocks.append({
            "symbol": symbol,
            "price": round(price, 2) if price is not None else None,
            "change_pct": round((price / previous - 1) * 100, 2) if price is not None and previous else None,
            "price_as_of": rows[0]["date"] if rows else None,
            "spark": [round(r["close"], 2) for r in reversed(rows)],
            "month_pct": round((price / rows[-1]["close"] - 1) * 100, 1) if len(rows) > 5 and rows[-1]["close"] else None,
        })
    return stocks


@app.get("/api/stocks/{symbol}")
def stock_detail(symbol: str):
    from stock_detail import get_stock_detail
    try:
        return get_stock_detail(symbol)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(502, f"Could not load stock detail: {e}")


@app.post("/api/stocks/{symbol}/refresh")
def refresh_stock(symbol: str):
    from stock_detail import refresh_stock_detail
    try:
        return refresh_stock_detail(symbol)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(502, f"Could not refresh stock data: {e}")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
