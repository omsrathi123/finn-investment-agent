"""Unusual-move detector: pure SQL/python, thresholds chosen by the user. No LLM needed."""
from statistics import mean
from db import conn
def spikes(vol_mult=2.0, move_pct=4.0, lookback=20):
    c, out = conn(), []
    for (s,) in c.execute("SELECT DISTINCT symbol FROM prices"):
        rows = c.execute("SELECT date,close,volume FROM prices WHERE symbol=? ORDER BY date DESC LIMIT ?", (s, lookback + 1)).fetchall()
        if len(rows) < 6: continue
        last, prev, hist = rows[0], rows[1], rows[1:]
        avg = mean(r["volume"] for r in hist)
        vm = last["volume"] / avg if avg else 0
        mv = (last["close"] / prev["close"] - 1) * 100
        why = []
        if vm >= vol_mult: why.append(f"Volume {vm:.1f}x {len(hist)}-day avg")
        if abs(mv) >= move_pct: why.append(f"Price {mv:+.1f}% on the day")
        if why: out.append(dict(symbol=s, date=last["date"], close=round(last["close"], 2), move_pct=round(mv, 2), vol_mult=round(vm, 2), why=why))
    return sorted(out, key=lambda x: -(abs(x["move_pct"]) + x["vol_mult"]))
