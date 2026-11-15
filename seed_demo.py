"""Offline demo data so the UI runs even if scrapers are blocked: python seed_demo.py"""
import random, json
from datetime import datetime, timedelta
from config import PORTFOLIO
from db import conn

def seed_demo():
    random.seed(7)
    c = conn()
    now = datetime.now()
    for s in PORTFOLIO:
        p = random.uniform(300, 3000)
        for i in range(40, -1, -1):
            d = (now - timedelta(days=i)).strftime("%Y-%m-%d"); p *= 1 + random.gauss(0, .012)
            v = random.uniform(8e5, 1.2e6) * (3.1 if i == 0 and s in ("SBIN", "TITAN") else 1)
            if i == 0 and s == "ITC": p *= 1.065
            c.execute("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", (s, d, p, p, p, p, v))
    demo = [("RELIANCE", "Financial Results", "Outcome of Board Meeting - Q2 results"), ("INFY", "Change in Management", "Resignation of Chief Financial Officer"),
            ("LT", "Acquisition", "Acquisition of 51% stake in engineering firm"), ("TCS", "Trading Window", "Closure of trading window"),
            ("SBIN", "Newspaper Publication", "Newspaper advertisement"), ("ITC", "Order", "Receipt of order worth Rs 400 cr"), ("TITAN", "Certificate", "Reg 74(5) certificate")]
    for i, (s, cat, sub) in enumerate(demo):
        ts = (now - timedelta(hours=2 + i * 3)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT OR REPLACE INTO announcements VALUES (?,?,?,?,?,?,?,?,?)", (f"demo:{i}", s, "DEMO", ts, cat, sub, sub, None, "{}"))
    c.commit()
    print("seeded demo data")

if __name__ == "__main__":
    seed_demo()
