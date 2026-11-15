# FINN — covering note

FINN is a mobile-first prototype for reducing the noise in an investor's daily workflow. It brings a prioritised filing feed, configurable end-of-day price/volume alerts, a quarterly-results card, and transcript-based management-guidance research into one small interface.

I chose to focus on two complementary jobs: proactively surfacing changes across a portfolio, and letting an investor investigate whether management's past commentary was borne out later. Announcement tiers use auditable rules; model calls are reserved for extracting filing/transcript information and judging promise versus delivery. Results comparisons are calculated in code, and the interface links back to the underlying filings and transcripts.

The prototype uses public NSE/BSE, Yahoo Finance and Screener.in sources. It is intentionally not a notification service: the investor can see the data's latest dates, but must refresh the data manually. Pull research currently focuses on concall transcripts rather than implementing every item in the challenge's research menu. Demo-seeded prices and filings are simulated and labeled; they are not real market information.

## Try the prototype

Follow the local setup steps in [README.md](README.md). For live filing extraction and transcript research, configure `GEMINI_API_KEY` and refresh the source data. The seed data provides an offline walkthrough of the feed and alert screens.

**Source repository:** https://github.com/omsrathi123/finn-investment-agent  
**Hosted app:** Pending Vercel deployment; add the public `*.vercel.app` URL after deployment.
**AI build history:** Attach the requested coding-agent/LLM conversation export or share link when submitting.
