# FINN — covering note

**What I built.** FINN is a mobile-first web app (installable as a PWA) that acts as an investor's inbox for a
20-stock NSE portfolio. It covers both modes in the brief.

*Push:*
- A windowed digest over any period, including custom dates. Every filing is mapped to the 17 reference categories,
  given a visible 0–100 materiality score, clustered per event (a results day's 4–6 filings become one card), and
  routine items are collapsed.
- Triggered alerts on rules the investor sets (filing types, minimum materiality, volume multiple, daily move,
  lookback), fed by a watcher on NSE's live announcements feed and an after-close EOD price pull. Each alert fires
  once, as an inbox item and a system notification.
- Two enrichments the brief asked for. Results filings get Revenue/EBITDA/PBT/PAT with QoQ/YoY computed in code and
  cross-checked against Screener. Management changes get the incoming person's background, taken from the filing's
  own profile annexure and a web-grounded lookup, with sources and a LinkedIn link.

*Pull:* all eight research sections for any NSE ticker. Four are computed straight from the statements at zero model
cost. Business snapshot, guidance, promised-vs-delivered (transcripts checked against actual quarterly numbers) and
bull/bear read only primary documents. *Ask FINN* is a conversational copilot, typed or spoken, that keeps the
company in context across follow-ups. It answers from facts gathered by FINN's own tools (rules first; the model only
rewrites from those facts) and attaches the same cards used across the app. It keeps answering without a model key.

**Key decisions.**
- Rules, not a model, for anything that has to be fast, cheap and auditable: classification, scoring, clustering,
  routing, alerts and the brief. Models are kept for reading PDFs and for judgement.
- Fast/smart routing, caching per prompt and per PDF page, keyword chunking of annual reports, and research sections
  computed only when opened. `/api/usage` shows the spend.
- Showing the score: the investor can see *why* something ranks where it does and tune the alert threshold to it.
- A real-data snapshot ships with the repo, so the hosted demo shows actual NSE filings rather than simulated ones.
- Numbers are never invented. When the filing and Screener disagree, both are shown.

**Left out deliberately.** Accounts and multi-user, closed-app Web Push (the service worker is ready; it needs a VAPID
service), broker/news inputs on the research side, and intraday data (the brief is end-of-day).

**Next.** Server-sent push with VAPID, per-holding position sizes so materiality weighs exposure, a weekly
"promises tracker" that re-runs promised-vs-delivered after each concall, and evaluation sets for the taxonomy built
from labelled filings.

**Source repository:** https://github.com/omsrathi123/finn-investment-agent  
**Hosted app:** add the deployed URL here.  
**AI build history:** see [aihistory.md](aihistory.md) for the available conversation history.
