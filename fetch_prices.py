"""EOD price + volume via yfinance (.NS = NSE)."""
import yfinance as yf
from db import conn

def fetch_prices(symbols, period="6mo"):
    c, errors = conn(), []
    for s in symbols:
        try:
            df = yf.Ticker(f"{s}.NS").history(period=period, auto_adjust=False)
            if df.empty:
                message = f"[prices] {s}: EMPTY"
                errors.append(message)
                print(message)
                continue
            rows = [(s, d.strftime("%Y-%m-%d"), r.Open, r.High, r.Low, r.Close, r.Volume)
                    for d, r in df.iterrows()]
            c.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", rows)
            c.commit()
            print(f"[prices] {s}: {len(rows)} days, last {rows[-1][1]}")
        except Exception as e:
            message = f"[prices] {s}: FAILED {type(e).__name__}: {e}"
            errors.append(message)
            print(message)
    return errors

if __name__ == "__main__":
    from config import PORTFOLIO
    fetch_prices(list(PORTFOLIO))
