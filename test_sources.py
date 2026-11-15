import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app
import llm
import research
import results
import stock_detail


class SourceTests(unittest.TestCase):
    def _stock_db(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript(
            "CREATE TABLE prices (symbol TEXT, date TEXT, close REAL, volume REAL);"
            "CREATE TABLE announcements (id TEXT, symbol TEXT, source TEXT, ts TEXT, "
            "category TEXT, subject TEXT, detail TEXT, attachment_url TEXT);"
        )
        db.executemany(
            "INSERT INTO prices VALUES (?, ?, ?, ?)",
            [("TEST", f"2026-01-{day:02d}", 99 + day, 1000) for day in range(1, 23)],
        )
        db.execute(
            "INSERT INTO announcements VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("nse:1", "TEST", "NSE", "2026-01-22 12:00:00", "Financial Results",
             "Quarterly results", "Results filed", "https://nse.example/results.pdf"),
        )
        db.commit()
        return db

    def _yahoo_data(self):
        return {
            "profile": {"name": "Test Ltd", "sector": "Technology", "industry": "Software",
                        "currency": "INR", "summary": "", "website": None},
            "metrics": {"market_cap_crore": 1000, "trailing_pe": 20, "forward_pe": None,
                        "price_to_book": 4, "ev_to_ebitda": None, "ebitda_crore": 100,
                        "revenue_crore": 500, "ebitda_margin_pct": 20, "net_margin_pct": None,
                        "return_on_equity_pct": None, "debt_to_equity_pct": None,
                        "dividend_yield_pct": None, "revenue_growth_pct": None,
                        "earnings_growth_pct": None, "free_cash_flow_crore": None,
                        "current_ratio": None, "beta": None},
            "news": [], "warnings": [], "fetched_at": "2026-01-22 12:00:00",
        }

    def test_health_check_uses_database(self):
        db = sqlite3.connect(":memory:")
        with patch.object(app, "conn", return_value=db):
            self.assertEqual(app.health(), {"status": "ok"})
        db.close()

    def test_gemini_request_sends_api_key_in_header_not_url(self):
        response = MagicMock()
        response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}],
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(llm, "CACHE", Path(directory)), \
                 patch.object(llm.requests, "post", return_value=response) as post, \
                 patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-not-real"}):
                result = llm.ask_json("test prompt", "gemini-3.5-flash-lite")

        args, kwargs = post.call_args
        self.assertNotIn("key=", args[0])
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "test-key-not-real")
        self.assertEqual(result, {"ok": True})

    def test_results_card_returns_primary_filing_link(self):
        row = {
            "symbol": "TEST",
            "attachment_url": "https://example.com/results.pdf",
            "ts": "2026-10-31 12:00:00",
        }
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = row
        extracted = {
            "quarter": "Q2 FY27",
            "unit": "Rs crore",
            "revenue": {"cur": 100, "prev_q": 90, "yoy": 80},
        }

        with patch.object(results, "conn", return_value=db), \
             patch.object(results, "pdf_text", return_value="filing text"), \
             patch.object(results, "ask_json", return_value=extracted):
            card = results.result_card("nse:1")

        self.assertEqual(card["source_url"], row["attachment_url"])
        self.assertEqual(card["source_ts"], row["ts"])

    def test_research_returns_sources_in_analysis_order(self):
        docs = [
            {"label": "Transcript", "url": "https://example.com/newer.pdf"},
            {"label": "Transcript", "url": "https://example.com/older.pdf"},
        ]
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = docs
        model_responses = [
            {"period": "Q1 FY27", "guidance": [], "reported": []},
            {"period": "Q2 FY27", "guidance": [], "reported": []},
            {"verdict": "Evidence is mixed.", "items": [], "watch": "Margin trend"},
        ]

        with patch.object(research, "conn", return_value=db), \
             patch.object(research, "pdf_text", return_value="transcript text"), \
             patch.object(research, "ask_json", side_effect=model_responses):
            report = research.narrative_vs_numbers("TEST")

        self.assertIn("ORDER BY rowid ASC", db.execute.call_args.args[0])
        self.assertEqual(
            [source["url"] for source in report["sources"]],
            ["https://example.com/older.pdf", "https://example.com/newer.pdf"],
        )

    def test_status_reports_market_dates_and_demo_fixtures(self):
        db = sqlite3.connect(":memory:")
        db.executescript(
            "CREATE TABLE prices (date TEXT);"
            "CREATE TABLE announcements (source TEXT, ts TEXT);"
            "INSERT INTO prices VALUES ('2026-11-13');"
            "INSERT INTO announcements VALUES ('NSE', '13-Nov-2026 10:00:00');"
            "INSERT INTO announcements VALUES ('DEMO', '2026-11-15 12:00:00');"
        )
        with patch.object(app, "conn", return_value=db):
            state = app.status()

        self.assertEqual(state["prices_through"], "2026-11-13")
        self.assertEqual(state["filings_through"], "2026-11-13 10:00:00")
        self.assertEqual(state["demo_filings"], 1)
        db.close()

    def test_stock_detail_returns_prices_fundamentals_and_filing_source(self):
        db = self._stock_db()
        with patch.object(stock_detail, "conn", return_value=db), \
             patch.object(stock_detail, "_yahoo_snapshot", return_value=self._yahoo_data()):
            detail = stock_detail.get_stock_detail("TEST")

        self.assertEqual(detail["price"], 121)
        self.assertEqual(detail["returns"]["1m_pct"], 21)
        self.assertEqual(detail["metrics"]["trailing_pe"], 20)
        self.assertEqual(detail["filings"][0]["url"], "https://nse.example/results.pdf")
        self.assertEqual(detail["filings"][0]["source"], "NSE")
        db.close()

    def test_stock_detail_rejects_malformed_ticker_before_fetching(self):
        with patch.object(stock_detail, "_yahoo_snapshot") as yahoo:
            with self.assertRaises(ValueError):
                stock_detail.get_stock_detail("RELIANCE/..")
        yahoo.assert_not_called()

    def test_yahoo_news_normalizes_clickthrough_source_links(self):
        news = stock_detail._normalize_news([{
            "content": {
                "title": "Company update",
                "canonicalUrl": {"url": "https://example.com/story"},
                "provider": {"displayName": "Example News"},
                "pubDate": "2026-01-22T10:00:00Z",
            },
        }])

        self.assertEqual(news[0]["url"], "https://example.com/story")
        self.assertEqual(news[0]["publisher"], "Example News")
        self.assertEqual(news[0]["published_at"], "2026-01-22T10:00:00Z")

    def test_refresh_returns_source_failures_without_hiding_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(stock_detail, "CACHE", Path(directory)), \
                 patch.object(stock_detail, "fetch_prices", return_value=["Price source blocked"]), \
                 patch.object(stock_detail, "fetch_symbol", return_value={
                     "count": 0, "source": None, "warnings": ["NSE unavailable"],
                 }) as announcements, \
                 patch.object(stock_detail, "get_stock_detail", return_value={"warnings": []}):
                detail = stock_detail.refresh_stock_detail("TEST")

        self.assertEqual(detail["refresh"]["status"], "completed")
        self.assertEqual(detail["warnings"], ["Price source blocked", "NSE unavailable"])
        announcements.assert_called_once_with("TEST", None, days=30)

    def test_refresh_cooldown_does_not_repeat_upstream_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(stock_detail, "CACHE", Path(directory)), \
                 patch.object(stock_detail, "get_stock_detail", return_value={"symbol": "TEST"}), \
                 patch.object(stock_detail, "fetch_prices") as prices, \
                 patch.object(stock_detail, "fetch_symbol") as announcements:
                stamp = Path(directory) / "refresh" / "TEST.json"
                stamp.parent.mkdir()
                stamp.write_text('{"refreshed_epoch": %s}' % time.time())

                detail = stock_detail.refresh_stock_detail("TEST")

        self.assertEqual(detail["refresh"]["status"], "cooldown")
        prices.assert_not_called()
        announcements.assert_not_called()


if __name__ == "__main__":
    unittest.main()
