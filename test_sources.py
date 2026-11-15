import sqlite3
import unittest
from unittest.mock import MagicMock, patch

import app
import research
import results


class SourceTests(unittest.TestCase):
    def test_health_check_uses_database(self):
        db = sqlite3.connect(":memory:")
        with patch.object(app, "conn", return_value=db):
            self.assertEqual(app.health(), {"status": "ok"})
        db.close()

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


if __name__ == "__main__":
    unittest.main()
