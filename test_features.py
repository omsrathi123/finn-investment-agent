"""Tests for the push (digest, alerts, live NSE feed, enrichment) and pull (research) features."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import ask
import db
import digest
import fetch_announcements as fa
import people
import push
import research
import results
import screener
from taxonomy import analyse, one_liner

FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "screener_TESTCO.html"
NOW = datetime(2026, 11, 14, 18, 0, 0)


def nse(seq, sym, desc, text, hours_ago, voice=None, url="https://nsearchives.nseindia.com/x.pdf", name=None):
    ts = (NOW - timedelta(hours=hours_ago)).strftime("%d-%b-%Y %H:%M:%S")
    return {"seq_id": str(seq), "symbol": sym, "desc": desc, "attchmntText": text, "an_dt": ts,
            "attchmntFile": url, "sm_name": name or f"{sym} Limited", "voiceSummary": voice}


class TempDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = patch.object(db, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def load(self, rows):
        return fa.upsert([fa.nse_row(r) for r in rows])


class TaxonomyTests(unittest.TestCase):
    def test_reference_categories_and_tiers(self):
        cases = [
            ("Outcome of Board Meeting", "X has submitted to the Exchange, the financial results for the period ended September 30, 2026", "results", "critical"),
            ("Change in Management", "Resignation of Mr. A B as Chief Financial Officer", "management_change", "critical"),
            ("Analysts/Institutional Investor Meet/Con. Call Updates", "X has informed the Exchange about Analyst(s)/Investor(s) meet.", "investor_meetings", "routine"),
            ("Analysts/Institutional Investor Meet/Con. Call Updates", "X has informed the Exchange about Transcript", "earnings_call", "routine"),
            ("Copy of Newspaper Publication", "Newspaper advertisement of financial results", "results", "routine"),
            ("Corporate Insolvency Resolution Process", "Order passed by NCLAT", "litigation", "critical"),
            ("ESOP/ESOS/ESPS", "Allotment of 3403 shares", "unclassified", "routine"),
            ("Credit Rating", "CRISIL downgraded the long-term rating", "credit_rating", "critical"),
        ]
        for desc, text, cat, tier in cases:
            a = analyse(desc, desc, text)
            self.assertEqual((a["category"], a["tier"]), (cat, tier), desc + " / " + text)

    def test_reappointment_is_not_urgent(self):
        a = analyse("Change in Director(s)", "", "regarding Change in Director(s)",
                    "The bank received approval for the re-appointment of Mr. V. Rangan")
        self.assertLess(a["score"], 50)

    def test_one_liner_strips_boilerplate_and_refuses_empty_restatements(self):
        self.assertIsNone(one_liner("ABC Limited has informed the Exchange about Credit Rating", desc="Credit Rating"))
        self.assertEqual(one_liner("In continuation of our letter dated October 27, 2026, we wish to inform you that "
                                   "the Board approved a buyback"), "The Board approved a buyback")
        self.assertTrue(one_liner("", "Approved re-appointment of Mr. V. Rangan as Director. Term 3 years.")
                        .startswith("Approved re-appointment of Mr. V. Rangan as Director."))


class DigestTests(TempDB):
    def test_window_clusters_one_event_and_hides_routine(self):
        self.load([
            nse(1, "AAA", "Outcome of Board Meeting", "Financial results for the quarter ended September 30, 2026", 5),
            nse(2, "AAA", "Press Release", "Media release on financial results for the quarter", 4),
            nse(3, "AAA", "Investor Presentation", "Investor presentation on results", 3),
            nse(4, "BBB", "ESOP/ESOS/ESPS", "Allotment of 100 shares", 2),
            nse(5, "CCC", "Change in Management", "Appointment of Mr. Ravi Kumar as Chief Executive Officer", 30),
        ])
        d = digest.build(hours=24, now=NOW)
        self.assertEqual(d["counts"]["routine"], 2)  # ESOP + investor deck (earnings_call)
        self.assertEqual(len(d["critical"]), 1)
        head = d["critical"][0]
        self.assertEqual((head["symbol"], head["kind"]), ("AAA", "results"))
        self.assertEqual(len(head["related"]), 1)  # press release folded into the results event
        self.assertIn("results", head["actions"])
        wider = digest.build(hours=72, now=NOW)
        self.assertIn("people", wider["critical"][0]["actions"] + wider["critical"][1]["actions"])

    def test_custom_date_range(self):
        self.load([nse(1, "AAA", "Dividend", "Interim dividend of Rs 5 declared", 24 * 10)])
        d = digest.build(start=(NOW - timedelta(days=11)).strftime("%Y-%m-%d"),
                         end=(NOW - timedelta(days=9)).strftime("%Y-%m-%d"), now=NOW)
        self.assertEqual(sum(d["counts"].values()), 1)


class PushTests(TempDB):
    def _prices(self, sym, closes, vols):
        c = db.conn()
        start = datetime(2026, 10, 1)
        c.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,?)",
                      [(sym, (start + timedelta(days=i)).strftime("%Y-%m-%d"), x, x, x, x, v)
                       for i, (x, v) in enumerate(zip(closes, vols))])
        c.commit()

    def test_rules_drive_filing_and_price_alerts_and_fire_once(self):
        self.load([nse(1, "AAA", "Acquisition", "Acquisition of 100% stake in XYZ Pvt Ltd for Rs 900 crore", 3),
                   nse(2, "AAA", "Dividend", "Interim dividend declared", 3)])
        self._prices("AAA", [100] * 25 + [107], [1000] * 25 + [3500])
        alerts, new = push.evaluate(now=NOW)
        kinds = sorted(a["kind"] for a in alerts)
        self.assertEqual(kinds, ["filing", "price"])  # dividend not watched by default
        price = next(a for a in alerts if a["kind"] == "price")
        self.assertAlmostEqual(price["vol_mult"], 3.5)
        self.assertEqual(price["move_pct"], 7.0)
        self.assertEqual(len(new), 1)  # the filing is fresh; the old-dated price bar is history
        _, again = push.evaluate(now=NOW)
        self.assertEqual(again, [])  # never pushed twice

        push.save_rules({"vol_mult": 5, "move_pct": 10, "categories": ["dividend"]})
        alerts, _ = push.evaluate(now=NOW)
        self.assertEqual([a["category"] for a in alerts if a["kind"] == "filing"], [])  # dividend score < 50
        push.save_rules({"min_score": 30})
        alerts, _ = push.evaluate(now=NOW)
        self.assertEqual([a["category"] for a in alerts], ["dividend"])

    def test_tick_polls_live_feed_once_per_interval(self):
        with patch("fetch_announcements.fetch_live", return_value={"new": [], "seen": 3, "source": "NSE live",
                                                                    "warnings": []}) as live, \
                patch.object(push, "_eod_due", return_value=False):
            push.tick()
            push.tick()
        self.assertEqual(live.call_count, 1)
        self.assertIn("NSE live", push.status()["live"]["note"])


class LiveFeedTests(TempDB):
    def test_nse_live_filters_portfolio_and_reports_new_ids(self):
        sess = MagicMock()
        sess.get.return_value.json.return_value = [
            nse(10, "AAA", "Acquisition", "deal", 1), nse(11, "ZZZ", "Acquisition", "other co", 1)]
        with patch.object(fa, "CACHE", Path(self.tmp.name)):
            rows = fa.nse_live(sess, ["AAA"])
        self.assertEqual([r["symbol"] for r in rows], ["AAA"])
        self.assertEqual(sess.get.call_args.kwargs["params"], {"index": "equities"})
        self.assertEqual(fa.upsert(rows), ["nse:10"])
        self.assertEqual(fa.upsert(rows), [])

    def test_fetch_live_falls_back_to_rss(self):
        self.load([nse(1, "AAA", "Dividend", "x", 1)])  # teaches the name map "aaa limited" -> AAA
        rss = b"""<rss><channel><item><title>AAA Limited</title><link>https://nsearchives.nseindia.com/a.pdf</link>
        <description>|SUBJECT: Acquisition of stake|</description><pubDate>Sat, 14 Nov 2026 10:00:00 +0530</pubDate></item>
        <item><title>Other Co Ltd</title><link>https://x/b.pdf</link><description>x</description></item></channel></rss>"""
        resp = MagicMock(content=rss)
        with patch.object(fa, "nse_live", side_effect=RuntimeError("blocked")), \
                patch.object(fa.requests, "get", return_value=resp):
            out = fa.fetch_live(["AAA"])
        self.assertEqual(out["source"], "NSE RSS")
        self.assertEqual(len(out["new"]), 1)
        self.assertIn("NSE live failed", out["warnings"][0])


class EnrichmentTests(TempDB):
    def test_people_from_headline_without_model(self):
        self.load([nse(1, "MAXHEALTH", "Change in Management",
                       "Max Healthcare has informed the Exchange about appointment of Dr. Dharmender Kumar as "
                       "Director & Chief - Public Affairs, identified as Senior Management Personnel", 2, url=None)])
        with patch.object(people, "has_key", return_value=False):
            p = people.profile("nse:1")
        self.assertEqual(p["people"][0]["name"], "Dharmender Kumar")
        self.assertEqual(p["people"][0]["role"], "Director & Chief - Public Affairs")
        self.assertIn("linkedin.com", p["people"][0]["linkedin"])

    def test_people_uses_filing_profile_and_web_background(self):
        self.load([nse(1, "AAA", "Change in Management", "Appointment of Ms. Priya Shah as CFO", 2)])
        filing = {"people": [{"name": "Priya Shah", "role": "CFO", "change": "appointed", "incoming": True,
                              "background": "20 years at Unilever and Tata Motors; CA"}]}
        with patch.object(people, "pdf_text", return_value="profile annexure"), \
                patch.object(people, "ask_json", return_value=filing), \
                patch.object(people, "has_key", return_value=True), \
                patch.object(people, "ask_grounded", return_value={"text": "- Ex-CFO, Tata Motors", "sources": [
                    {"title": "linkedin", "url": "https://linkedin.com/in/x"}]}):
            p = people.profile("nse:1")
        self.assertEqual(p["layer"], "filing")
        self.assertEqual(p["people"][0]["web"]["sources"][0]["title"], "linkedin")

    def test_results_cross_checks_against_screener(self):
        self.load([nse(1, "TESTCO", "Outcome of Board Meeting", "Financial results for the quarter", 2)])
        data = screener.parse(FIXTURE.read_text())
        extracted = {"quarter": "Q2 FY27", "period_end": "2026-09", "unit": "Rs crore",
                     "revenue": {"cur": 820, "prev_q": 805, "yoy": 760}, "ebitda": {"cur": 164},
                     "pbt": {"cur": 135}, "pat": {"cur": 101, "prev_q": 99, "yoy": 92.25}}
        with patch.object(results, "pdf_text", return_value="x"), \
                patch.object(results, "ask_json", return_value=extracted), \
                patch.object(screener, "company", return_value=data):
            card = results.result_card("nse:1")
        self.assertTrue(card["cross_check"]["agrees"])
        self.assertEqual(card["rows"][0]["yoy"], 7.9)
        self.assertIn("Revenue +7.9% YoY", card["headline"])

    def test_results_falls_back_to_screener_without_a_key(self):
        self.load([nse(1, "TESTCO", "Outcome of Board Meeting", "Financial results for the quarter", 2)])
        data = screener.parse(FIXTURE.read_text())
        with patch.object(results, "pdf_text", return_value="x"), \
                patch.object(results, "ask_json", side_effect=RuntimeError("GEMINI_API_KEY not set")), \
                patch.object(results, "has_key", return_value=False), \
                patch.object(screener, "company", return_value=data):
            card = results.result_card("nse:1")
        self.assertEqual(card["source"], "screener")
        self.assertEqual(card["quarter"], "Sep 2026")
        self.assertEqual(card["rows"][3]["qoq"], 2.0)


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.data = screener.parse(FIXTURE.read_text())

    def test_screener_parse(self):
        self.assertEqual(self.data["top_ratios"]["Stock P/E"], 32.5)
        self.assertEqual(self.data["documents"][0][0], "annual_report")
        self.assertEqual(sum(d[0] == "transcript" for d in self.data["documents"]), 2)

    def test_deterministic_sections(self):
        t = research.trajectory(self.data)
        self.assertEqual(t["cagr"]["revenue"]["5y"], 12.0)
        b = research.balance(self.data)
        self.assertLess(b["debt_to_equity"][-1], 0.1)
        self.assertIn("net-cash-like", b["read"])
        c = research.cash(self.data)
        self.assertEqual(c["cum_conversion_5y_pct"], 90)
        self.assertIn("turning into cash", c["read"])
        f = research.financial(self.data)
        self.assertIn("+7.9% YoY", f["read"])

    def test_bull_bear_only_sees_computed_facts(self):
        with patch.object(research, "guidance", return_value={"items": [{"metric": "Revenue", "statement": "15% growth"}]}), \
                patch.object(research, "ask_json", return_value={"bull": [], "bear": []}) as model:
            out = research.bull_bear("TESTCO", self.data)
        prompt = model.call_args.args[0]
        self.assertIn("Revenue: 15% growth", prompt)
        self.assertIn("operating cash was 90% of PAT", prompt)
        self.assertEqual(model.call_args.args[1], research.SMART)
        self.assertIn("facts_used", out)


class AskTests(TempDB):
    def test_routing(self):
        self.load([nse(1, "DIXON", "Dividend", "x", 1)])
        with patch.object(ask, "has_key", return_value=False):
            self.assertEqual(ask.route("what changed this week")["intent"], "digest")
            self.assertEqual(ask.route("what changed this week")["hours"], 168)
            self.assertEqual(ask.route("any unusual volume?")["intent"], "alerts")
            r = ask.route("did Dixon deliver on its guidance")
            self.assertEqual((r["intent"], r["symbol"]), ("nvn", "DIXON"))
            self.assertEqual(ask.route("POLYCAB debt")["intent"], "balance")
            self.assertEqual(ask.route("bull and bear on TRENT")["intent"], "bullbear")


if __name__ == "__main__":
    unittest.main()


class ChatTests(TempDB):
    def setUp(self):
        super().setUp()
        import chat
        self.chat = chat
        self.load([nse(1, "MAXHEALTH", "Change in Management",
                       "Max Healthcare has informed the Exchange about appointment of Dr. Dharmender Kumar as "
                       "Director & Chief - Public Affairs, identified as Senior Management Personnel", 30, url=None,
                       name="Max Healthcare Institute Limited"),
                   nse(2, "CDSL", "Acquisition", "Acquisition of 51% stake in ABC Pvt Ltd", 20)])
        c = db.conn()
        start = datetime(2026, 10, 19)  # session 25 = 13 Nov, the day of the CDSL filing
        for sym, last in (("CDSL", 108), ("MAXHEALTH", 99)):
            c.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,?)",
                          [(sym, (start + timedelta(days=i)).strftime("%Y-%m-%d"), 100, 100, 100,
                            last if i == 25 else 100, 3000 if i == 25 else 1000) for i in range(26)])
        c.commit()

    def test_replies_without_a_model_key(self):
        with patch.object(self.chat, "has_key", return_value=False), patch("ask.has_key", return_value=False), \
                patch("research.overview", side_effect=RuntimeError("offline")):
            r = self.chat.chat("how is my portfolio doing?")
            self.assertEqual(r["intent"], "portfolio")
            self.assertIn("holdings closed higher", r["reply"])
            r = self.chat.chat("who joined Max Healthcare?")
            self.assertEqual((r["intent"], r["symbol"]), ("people", "MAXHEALTH"))
            self.assertIn("Dharmender Kumar", r["reply"])
            r = self.chat.chat("why did CDSL move?")
            self.assertIn("+8.0%", r["reply"])
            self.assertIn("same day as its m&a filing", r["reply"])
            self.assertEqual(r["engine"], "rules")
            self.assertTrue(r["suggestions"])
            self.assertEqual(self.chat.chat("hello")["intent"], "greet")

    def test_follow_up_uses_the_company_in_context(self):
        with patch.object(self.chat, "has_key", return_value=False), patch("ask.has_key", return_value=False), \
                patch("research.section", return_value={"read": "Debt/equity 0.10×."}) as sec:
            r = self.chat.chat("what about their debt?", context_symbol="CDSL")
        self.assertEqual((r["intent"], r["symbol"]), ("balance", "CDSL"))
        sec.assert_called_once_with("CDSL", "balance")
        self.assertIn("Debt/equity", r["reply"])

    def test_model_writes_reply_from_facts_and_history(self):
        hist = [{"role": "user", "text": "tell me about CDSL"}, {"role": "model", "text": "CDSL closed at ..."}]
        with patch.object(self.chat, "has_key", return_value=True), patch("ask.has_key", return_value=False), \
                patch("research.overview", side_effect=RuntimeError("offline")), \
                patch.object(self.chat, "ask_text", return_value="CDSL jumped **8%** on 3× volume.") as model:
            r = self.chat.chat("why did CDSL move?", hist)
        self.assertEqual(r["engine"], "model")
        self.assertEqual(r["reply"], "CDSL jumped **8%** on 3× volume.")
        prompt, model_name, system, history = model.call_args.args
        self.assertIn("recent_unusual_moves", prompt)
        self.assertEqual(model_name, self.chat.SMART)  # "why" questions get the reasoning model
        self.assertIn("ONLY the FACTS", system)
        self.assertEqual(history, hist)

    def test_model_failure_falls_back_to_rules(self):
        with patch.object(self.chat, "has_key", return_value=True), patch("ask.has_key", return_value=False), \
                patch.object(self.chat, "ask_text", side_effect=RuntimeError("quota")):
            r = self.chat.chat("any unusual moves?")
        self.assertEqual(r["engine"], "rules")
        self.assertIn("CDSL", r["reply"])
