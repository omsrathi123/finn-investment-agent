"""Smoke test: python run_day1.py [SYMBOL ...]   (defaults to 3 tickers)"""
import sys
from config import PORTFOLIO
from fetch_prices import fetch_prices
from fetch_announcements import fetch_all
from fetch_docs import fetch_docs

syms = sys.argv[1:] or ["RELIANCE", "TCS", "SBIN"]
fetch_prices(syms)
fetch_all({s: PORTFOLIO.get(s) for s in syms})
fetch_docs(syms)
