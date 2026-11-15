"""Universe + shared settings. Edit PORTFOLIO freely.
BSE codes are optional (used as announcement fallback) - VERIFY them on bseindia.com."""
import os
from pathlib import Path

ROOT = Path(__file__).parent
CACHE = Path(os.getenv("FINN_CACHE_DIR") or ROOT / "data" / "cache")
DB_PATH = Path(os.getenv("FINN_DB_PATH") or ROOT / "data" / "finn.db")
CACHE.mkdir(parents=True, exist_ok=True)

# symbol (NSE) -> BSE scrip code
PORTFOLIO = {
    "RELIANCE":   "500325",
    "TCS":        "532540",
    "HDFCBANK":   "500180",
    "INFY":       "500209",
    "ITC":        "500875",
    "SBIN":       "500112",
    "SUNPHARMA":  "524715",
    "LT":         "500510",
    "MARUTI":     "532500",
    "BHARTIARTL": "532454",
    "COALINDIA":  "533278",
    "TATASTEEL":  "500470",
    # --- Mid caps ---
    "POLYCAB":    None,
    "DIXON":      None,
    "PERSISTENT": None,
    "MAXHEALTH":  None,
    "IRCTC":      None,
    "ASTRAL":     None,
    # --- Small caps ---
    "CDSL":       None,
    "SUZLON":     None,
}
# TODO: swap a few for mid/small caps so the demo has range (look up their BSE codes).

RESEARCH_EXTRA = ["PIDILITIND", "DMART", "TRENT"]  # ad-hoc research names

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}
