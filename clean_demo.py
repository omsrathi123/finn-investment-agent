"""Remove simulated demo data (seed_demo.py) from the local database."""
from db import conn

c = conn()
c.execute("DELETE FROM announcements WHERE source='DEMO'")
# seeded prices are flat bars (open = high = low = close) and include weekends; real EOD bars never are
n = c.execute("DELETE FROM prices WHERE open=high AND high=low AND low=close").rowcount
c.commit()
print(f"cleaned demo filings and {n} simulated price rows")
