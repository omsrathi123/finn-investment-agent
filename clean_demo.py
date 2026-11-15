import sqlite3
from config import PORTFOLIO

c = sqlite3.connect("data/finn.db")
c.execute("DELETE FROM announcements WHERE source='DEMO'")
marks = ",".join("?" * len(PORTFOLIO))
c.execute(f"DELETE FROM prices WHERE symbol NOT IN ({marks})", list(PORTFOLIO))
c.commit()
print("cleaned")