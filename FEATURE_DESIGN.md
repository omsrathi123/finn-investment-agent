# FINN — Feature Design Document

> **How to read this document.** Each feature is written up the way it was designed. It starts with the problem
> the investor has, then the thinking that shaped the approach, then what was built, then the edge cases that
> changed the design. Features marked **★ Distinctive** go beyond what a typical portfolio app or LLM wrapper does.
> They are summarised at the end.

> **Live app:** [finnportfoliobyiom.vercel.app](https://finnportfoliobyiom.vercel.app/)  
> **Source repository:** [omsrathi123/finn-investment-agent](https://github.com/omsrathi123/finn-investment-agent)  
> **AI build history:** [aihistory.md](aihistory.md)
>
> **Build approach.** I set the product direction and guided each iteration; AI helped turn the idea into code,
> tests, documentation, and a deployed prototype. The build-history file includes representative chat excerpts.

---

## 0. Starting point: the thesis behind every decision

The brief names the real problem: an investor isn't short of information. They're short of **filtering**. Everything
in FINN follows from four principles derived from that:

#
Principle
What it means in practice

P1
**An inbox, not a dashboard**
A dashboard shows everything and makes you look. An inbox triages for you and tells you when you're done. FINN shows what needs attention, ranked, and says how much it hid.

P2
**Show the ranking, don't hide it**
Every filing carries a visible 0–100 materiality score. If the investor can see *why* something ranks where it does, they can trust it and tune it.

P3
**Rules where you can, models where you must**
Classification, scoring, clustering, alerting and routing are deterministic: free, instant and auditable. Language models are used only to *read* documents and to *judge*.

P4
**Every claim traces to a primary source**
Each number links back to the filing, statement or transcript it came from. When two sources disagree, both are shown.

These four principles are referred to as P1–P4 below.

---

## Part A — The data foundation

### A1. Three data sources, wired for failure

**Problem.** The brief calls the backend "plumbing", and plumbing breaks: NSE needs cookies and blocks some cloud
IPs, Yahoo rate-limits, Screener changes layout.

**Thinking.** A demo that doesn't run is the worst outcome the brief names. So every source needs a fallback, and
every failure must show up in the interface instead of failing silently.

**What was built.**

Source
Primary path
Fallback
Used for

NSE/BSE announcements
NSE corporate-announcements API (cookie warm-up session)
BSE API (by scrip code) and NSE's public RSS feed
Push: digest and alerts

End-of-day prices
Yahoo Finance (`yfinance`, `.NS` tickers)
Last stored history
Alerts, holdings, charts

Fundamentals
screener.in company page: statements and document links
Stale cache (better than nothing)
Pull: research, results cross-check

Every raw response is cached to disk, and any warnings ("NSE live failed: blocked") are shown to the user.

### A2. ★ Real-data snapshot shipped with the code

**Problem.** Hosted free tiers use temporary storage. Each cold start had an empty database, so the old version
filled it with *simulated* filings and prices. Reviewers would have judged the product on fake data.

**Thinking.** The first impression should be real announcements from real companies.

**What was built.** `data/snapshot/` (about 250 KB, compressed) holds 267 real NSE filings (16 Oct – 14 Nov 2026),
six months of real Yahoo EOD prices for all 20 holdings, and 918 Screener document links. An empty database loads
it automatically on boot. `run_day1.py` refreshes the data and re-exports the snapshot. The simulated seed survives
only as a last resort.

### A3. ★ Data-integrity repair

**Problem found during the build.** Earlier demo seeding had mixed **156 simulated price rows** into the real price
history. They were flat bars (open = high = low = close), and 132 fell on weekends. They produced phantom alerts,
such as Bharti Airtel apparently falling 30% in one day.

**What was built.** The bad rows were detected by their signature (flat bars) and removed. `clean_demo.py` now purges
them. The price-alert detector also adds *"verify: a move this size can be a split/bonus or a data error"* to any
move beyond ±20%. A data tool should be suspicious of its own data.

### A4. IST-correct time handling

**Problem.** NSE timestamps are in Indian time (IST), but Render and Vercel servers run on UTC. A "last 24 hours"
window would silently be off by 5.5 hours, and the "after market close" price check would run at the wrong time.

**What was built.** All windows, alert timing and the polling cursor use an IST-aware clock (`digest.ist_now()`).
The client stores the *server's* timestamp as its polling cursor, so phone and server clocks can never disagree.

---

## Part B — Push: the app reaches out to you

### B1. Announcement taxonomy: the 17 reference categories

**Problem.** NSE labels filings in its own vocabulary of 30+ labels ("General Updates", "Updates", "Outcome of
Board Meeting"…). These are inconsistent, and several are ambiguous.

**Thinking.** Map onto the reference document's 17 categories, because they match how an investor thinks (results,
M&A, management change…). Do it in three layers, from most reliable to least:

1. **NSE's own label**, when it's specific ("Change in Management" → management_change).
2. **Resolving ambiguous labels by their text.** "Outcome of Board Meeting" can be results, dividend or management.
A newspaper ad *about results* is results (as the reference document says) but routine. "Analysts/Institutional
Investor Meet/**Con. Call** Updates" is checked on its *body text only*, because the label itself contains
"con. call". That was a bug caught on real data: every investor meeting had been tagged as an earnings call.
3. **Ordered text rules** as the final fallback.

**Result on the real data:** 267 filings map cleanly. 151 are routine, so more than half the feed is noise that
never needs to be seen.

### B2. ★ Visible materiality score (0–100)

**Problem.** "Critical / notable / routine" alone is a black box.

**Thinking (P2).** A number invites calibration: the investor learns that 92 means results day and 45 means a
renewal. It also makes the alert threshold intuitive ("alert me at 50 and above").

**How the score is built.** Each category has a base score (results 90, M&A 82, pledging 75, management change 72,
fund-raise 66 … investor meetings 10, routine paperwork 4), then adjusted by content:

Signal
Adjustment
Why

CEO / CFO / MD / Chairman named
→ 88
Leadership changes move stocks

Resignation / cessation
+4
Exits carry more information than appointments

**Re-appointment / renewal**
capped at 45
Someone continuing in the seat is not news (caught on a real HDFC Bank filing)

Rating **downgrade** / watch
→ 80
An asymmetric risk signal

Insolvency / fraud / default
→ 85
Severe tail risk

Exchange asked for a **rumour / news clarification**
→ 62
The market is already reacting to something

Order / contract win
→ 52
Material but routine for order-driven businesses

Newspaper ad / compliance certificate
capped at 15
Paperwork, even when it's *about* results

Wholly-owned subsidiary incorporation
→ 50
Technically M&A, rarely material

Tiers follow from the score: **≥ 70 Needs you**, **35–69 Worth knowing**, **< 35 routine (collapsed)**.

### B3. ★ One-line summaries without a model

**Problem.** The brief asks: for each type, should it get a one-line summary or just the filing? Generating
summaries for hundreds of filings costs money and risks hallucination.

**Thinking.** Most filings already contain their own summary, hidden under boilerplate.

**What was built.** A summariser that:

- strips Indian regulatory boilerplate ("X Limited has informed the Exchange about…", "In continuation of our letter
dated…, we wish to inform you that…", "Pursuant to Regulation 30…"), applied repeatedly;
- prefers the **filing's own words** when they say something substantive, falling back to **NSE's own summary**
(`voiceSummary`), which costs nothing;
- **refuses to summarise** when the text only restates the label ("…informed the Exchange about Credit Rating"). It
returns nothing rather than an empty sentence, and the card shows a "why this matters" line instead;
- uses an abbreviation-aware sentence splitter, so "Mr. V. Srinivasa Rangan" and "Rs. 400 crore" aren't cut
mid-name;
- removes the company's name from the start of a summary when the card already shows the ticker.

### B4. ★ Event clustering

**Problem.** A single results day produces 4–6 filings: board outcome, press release, investor deck, newspaper ad,
call notice. Without clustering, one event looks like five, and the important one is buried.

**Thinking (P1).** The investor's unit of attention is an **event**, not a filing.

**What was built.** Filings from the same company in the same category within 72 hours fold into one card, led by
the highest-scoring filing, with "+ N related filings from the same event" collapsed underneath. Counts still report
*filings*, so nothing is hidden from the totals.

### B5. Windowed digest: "what changed while I was away"

**Brief requirement.** Show every filing for a chosen window, default 24 hours, prioritised, with routine items
collapsed.

**What was built.**

- Window presets: **24 hours · 3 days · 7 days · 30 days**, plus **custom from/to dates**.
- Sections: **Needs you** → **Worth knowing** → "**N routine filings, collapsed**" (expandable).
- Each row shows the score, ticker (tap for company detail), category, time, summary, and inline actions:
*Headline numbers* (results), *Who is this?* (management), *Filing* (primary source).

### B6. ★ The noise funnel hero

**Thinking.** The product's value is filtering, so the first thing on screen should *show the filtering*.

**What was built.** The Today screen opens with a large **"4 / 29"**: *events worth your attention out of filings in
this window*. A three-colour bar shows the split (needs you / worth knowing / routine, hidden). It's FINN's value in
one number.

### B7. ★ Never open on an empty screen

**Problem found in review.** On a Monday morning the last filings are from the weekend, so the default 24-hour
window was empty. A reviewer's first screen would have read "Nothing material."

**What was built.** If the user hasn't chosen a window yet, FINN widens the window step by step. It prefers the
first window with something that *needs you*, and otherwise the first with anything material. It always says so:
*"Nothing material in the last 24 hours, so here are the last 7 days."* Once the user picks a window, their choice
is respected.

### B8. Triggered alerts: you decide what "unusual" means

**Brief requirement.** Alert when something crosses a threshold: a material announcement type, or an EOD
volume/price anomaly, with configurable thresholds.

**What was built.** A rules sheet, saved on the server so the background watcher uses it too:

Rule
Default
Range

Filing categories to watch
Results, M&A, management change, litigation, fund-raise, pledging, credit rating, insider trading, capex, bonus/split
any of the 16

Minimum materiality score
50
10–95

Volume ≥ N × trailing average
2.0×
1.2–6×

Daily close-to-close move beyond
±4%
1–12%

Volume lookback
20 sessions
5–60

Two detectors run against the rules: **filing alerts** (category watched *and* score ≥ minimum) and
**price/volume alerts**, which scan every session in the last ten, not just the latest, so the history is useful.

### B9. ★ Fire-once alert log with read state

**Problem.** Pollers re-notify. Changing a rule can suddenly match a week of history and flood the phone.

**What was built.**

- A `pushed` table records every alert that has fired, so **no alert is sent twice**.
- When rules change or the app first runs, matching events **older than 48 hours are filed as already read**. They
appear in the inbox as history, but they don't send notifications.
- Unread dots, a badge count on the Alerts tab, and "Mark all read".

### B10. ★ Price moves explained by same-day filings

**Thinking.** "SBIN +2.4% on 2× volume" invites the question *why?* The answer is often a filing.

**What was built.** Each price alert is checked against the company's filings from the day before to the end of
that day. If a material one exists, the alert says *"…same day as its results filing"* and links to it. If none
exists, Ask FINN states that the move *"looks market- or flow-driven."* Example from the real data:
**ASTRAL −9.7% on 13.2× volume, the same day as its results filing.**

### B11. ★ Live NSE watcher using one request for the whole portfolio

**Problem.** Checking 20 companies one by one every few minutes means 20 requests, which is a fast way to get
blocked.

**Thinking.** NSE's announcements endpoint without a symbol returns the latest filings *market-wide*. One request,
filtered locally to the portfolio, covers every holding.

**What was built** (`push.tick()`):

1. **Live NSE feed**, one call (throttled to every 5 minutes, configurable).
2. **Fallback:** NSE's public RSS feed, which needs no cookies. Company names are matched to tickers using names
learnt from earlier NSE responses.
3. **End-of-day prices** pulled once per weekday after 16:00 IST.
4. Evaluate against the rules, then record and return only *new* alerts.

It runs two ways, so it works on any host:

- **Long-running servers** (local, Render): a background thread.
- **Serverless hosts** (Vercel): the open app polls `/api/push/poll` every minute, which runs the same throttled
check.

The last run, its source and any errors are shown on the Alerts screen ("Live NSE feed last checked 6m ago").

### B12. Notifications and installable app

**What was built.** A web app manifest and service worker, so FINN can be added to a phone's home screen and work
like an app. Notifications go through the service worker's `showNotification`, which is the path that works on
Android Chrome. Notifications are tagged per alert, so duplicates collapse. The service worker already handles
standard server push events, so true closed-app push needs only a push service added on the server.

### B13. ★ Results card, cross-checked against a second source

**Brief requirement.** From the results filing, give Revenue, EBITDA, PBT and PAT, quarter-on-quarter and
year-on-year.

**Thinking.** Language models misread tables, so its output shouldn't be trusted alone. Keep the model to
*extraction*, do all arithmetic in code, and **verify against a second source**.

**How it works:**

1. **Extract** from the actual filing PDF with the cheap model, returning structured numbers, the quarter's end date,
and whether figures are consolidated or standalone. EBITDA follows the reference definition when it isn't stated.
2. **Compute** QoQ and YoY in Python. The model never does maths.
3. **Cross-check** against Screener's quarterly table for the same quarter. Within 3%: "**Matches** Screener Sep 2026".
Otherwise: "**Differs from**…", with each difference listed and a prompt to check consolidated vs standalone.
4. **Headline line** computed in code: *"Revenue +7.9% YoY · PAT +9.5% YoY · EBITDA margin 20.0% (+0.0pp YoY)"*.
5. **No model key or unreadable PDF:** fall back to Screener's table, clearly labelled, so the card still works.
Cached extractions work without a key too.

### B14. ★ Management change: who is coming in, and from where

**Brief requirement.** Don't just say "CFO resigned". Give the incoming person's background.

**Thinking.** Use the cheapest reliable source first. Indian rules (SEBI Regulation 30) require appointment filings
to include a **brief profile** of the appointee, so the primary document often already has the bio.

**Four layers:**

Layer
Source
Cost

1
Pattern-matching on the filing headline: name, role, direction (appointed / resigned / retired / redesignated / ceased), with titles (Mr/Dr/Shri/Smt), DIN numbers and typos like "asthe" handled
free

2
The filing's own profile section: prior roles, education, reason for exit
1 cheap-model call

3
Web-grounded background (Gemini with Google Search), shown with source links
1 call, push side only, as the brief allows

4
LinkedIn people-search link (name + company)
free

Incoming people are listed first, because that's the investor's question. Tested on real filings: *Dr. Dharmender
Kumar, Director & Chief – Public Affairs (Max Healthcare)*, *Noel Naval Tata, retired (Tata Steel)*,
*V. Srinivasa Rangan, Whole-time Director (HDFC Bank)*.

### B15. ★ The spoken brief

**What was built.** "Read me the brief" speaks a written-for-listening digest in an Indian English voice:
*"In the last 7 days: 1 needs attention, 3 worth knowing. MARUTI, results: … Unusual moves: CDSL +3.4% (12 Nov)…
24 routine filings hidden."* It's composed by rules, so it **costs nothing and can't hallucinate**, and "QoQ" and
"YoY" are expanded for speech.

---

## Part C — Pull: you ask it to dig in

### C1. Research for any NSE ticker

**Brief requirement.** The engine mustn't be hard-wired to the demo set.

**What was built.** Type any ticker. If FINN hasn't seen it before, it fetches the company's Screener page on
demand, collects its annual-report and transcript links, and builds the report. Suggested tickers cover the
portfolio plus three extra companies: PIDILITIND, DMART and TRENT.

### C2. ★ All eight research sections, priced and built only when opened

**Thinking (P3).** Four of the eight sections are arithmetic on financial statements and need no language model.
The other four require reading documents. Show the investor what each costs, and only run a section when it's
opened.

Section
Source (primary only)
Method
Cost

Financial snapshot
Statements
Latest revenue, EBITDA, margin, PAT; market cap, P/E, ROCE, ROE, dividend yield; latest quarter YoY
**free**

Trajectory
Statements, up to 10 years
3-year and 5-year growth rates for revenue, EBITDA and PAT; margin trend; flags such as *growing / flat / shrinking* and *expanding / compressing*
**free**

Balance sheet
Statements
Equity, borrowings, debt-to-equity trend, interest cover, unfinished capex, working-capital days (receivables, inventory, payables, cash cycle)
**free**

Cash quality
Cash-flow statement + P&L + balance sheet
Operating cash ÷ PAT each year, 5-year total, estimated capex and free cash flow, with a verdict
**free**

Business snapshot
Latest annual report
Keyword-scored page selection, then segments, revenue mix and drivers, citing page numbers
1 cheap call

Guidance
Latest 2 concall transcripts
What management guided; **new this call** vs **dropped since last call**
2 cheap calls (shared cache)

Promised vs delivered
Last 4 transcripts **+ actual quarterly numbers**
Each promise marked met / partial / missed / unclear, with a supporting quote
4 cheap + 1 strong call

Bull vs bear
Only the facts above
3 points each way, each citing its evidence, plus the **swing factor**
1 strong call

Each computed section also gets a **plain-English reading written by rules**, for example *"Over the last 5 years
operating cash was 90% of PAT: profits are turning into cash."* Two edge cases are handled: banks (where
debt-to-equity is meaningless, flagged as such) and companies with only standalone figures.

### C3. ★ Promised vs delivered, checked against actual numbers

**Thinking.** The brief's worked example ("the difference between summarising management and analysing them") is
what the brief values most. Comparing management's words in one call with their words in the next only measures
whether they're consistent. Comparing their words with the **numbers they actually reported** measures whether
they delivered.

**What was built.** The judging step receives the four transcript extractions *plus* the actual reported figures for
the last six quarters (revenue, YoY growth, EBITDA margin, PAT). The judge is told to use only that data and not to
invent numbers.

### C4. ★ Bull vs bear from facts only

**Thinking.** A bull/bear case from a model's general knowledge is secondhand commentary, which the brief rules
out.

**What was built.** The strong model receives *only* the four computed readings and the company's own latest
guidance. Every point must cite one of those facts, and the output names the single **swing factor** that decides
between the two cases. A test confirms the prompt contains the computed facts and goes to the strong model.

### C5. Keeping to primary sources

- Research uses annual reports, concall transcripts and statements derived from filings. No news, no broker notes.
- Every research prompt forbids outside knowledge about the company.
- Web search is allowed **only** on the push side, for a person's background, exactly as the brief permits.
- Every section links to its source documents (annual report and pages read, transcripts by period).

---

## Part D — The interaction model

### D1. ★ Ask FINN: a grounded conversational copilot

**Problem.** A chatbot sitting on top of an LLM either hallucinates or says "I don't have access to that."

**Thinking.** The chatbot shouldn't be a separate brain. It should be a **conversational front door to FINN's own
tools**, with the language model limited to *phrasing* answers from facts those tools return.

**How each reply is built (`chat.py`):**

1. **Understand the question.** Rules route most questions instantly and for free. The cheap model is consulted
only when the rules can't tell what was asked.
2. **Resolve follow-ups.** "What about *their* debt?" is applied to the company currently being discussed. The
screen shows *"Talking about CDSL · clear"*.
3. **Gather facts.** It calls the digest, alerts, results, people, research sections and holdings tools, depending
on the question.
4. **Always write a factual answer.** FINN never goes silent, even with no API key or when the model fails.
5. **With a key, the model rewrites it conversationally,** using *only* those facts, the last 8 turns of
conversation and strict instructions: lead with the answer, at most about 130 words, no buy/sell advice, say so
when the facts don't answer the question. "Why / should / compare" questions go to the stronger model.
6. **Reply with cards and next questions.** The same results tables, filing rows and holdings tables used elsewhere,
plus 2–4 follow-up suggestions.
7. **Say how the reply was made.** "Written by the model from the facts shown" or "Answered from FINN's data, no
model call."

**What it handles:** greetings and thanks · "what changed this week / today / in 3 days" · unusual moves (ranked by
size) · "how is my portfolio doing" (equal-weight day move, leaders and laggards) · "why did X move" · results ·
"who joined X" · business · valuation · growth · debt · cash · guidance · did they deliver · bull vs bear ·
"tell me about X" · a helpful fallback for anything off-topic.

Company names are understood in plain English ("Max Healthcare", "Bharti Airtel"), not just tickers, using the
names in the NSE data.

### D2. Chat experience details

- Typing indicator, then the reply **revealed word by word**, then cards fade in. Motion is off when the phone asks
for reduced motion.
- **Voice in:** tap the mic and speak (Indian English, live transcription into the box, sent automatically).
- **Voice out:** a toggle reads replies aloud. Spoken questions are always answered aloud.
- **Memory:** the conversation persists between visits. "+" starts a new conversation.
- **Welcome screen:** a greeting for the time of day and six starter cards (Today, Moves, Portfolio, People, Results,
Research).
- **Action suggestions:** "Open X research" and "Make alerts stricter" act directly (opening research, or the rules
sheet) instead of sending a message.

### D3. ★ Ask from anywhere

A floating **Ask FINN** button sits on every screen. On a company or research page it reads **"Ask about CDSL"** and
opens the chat already set to that company. The chat is a layer over the whole product, not a separate tab.

### D4. Holdings and company detail

- **Holdings:** 20 companies with closing price, day move, a 30-session sparkline and 30-session return. The header
shows "N of 20 closed higher", the **equal-weight day move**, and the best and worst over 30 sessions.
- **Company detail:** 6-month price chart, valuation figures (market cap, P/E, P/B, EBITDA margin, ROE,
debt-to-equity), stored filings with source links, a **"Refresh from NSE & Yahoo"** button (with a 15-minute
cooldown to stay polite to the sources), and a one-tap jump to Research.

---

## Part E — Design: built for a phone, judged on any screen

### E1. Visual system

Choice
Rationale

One typeface (Instrument Sans) with **tabular figures** throughout
Numbers line up in columns. This is a numbers product.

A cool paper-grey background, ink text, indigo for "needs you"
Indigo is kept separate from market green and red, so *important* never looks like *down*.

**Score as the visual anchor** of each row
The ranking is the product (P2).

Thin dividing lines instead of a stack of identical cards
Denser and calmer, and avoids the generic "dashboard of cards" look.

Plain sentences as headlines ("1 thing needs you", "You're caught up.")
The interface talks like a person.

### E2. Polish

- **Dark mode** that follows the phone's setting.
- **Skeleton loaders** (grey placeholder bars) instead of "Loading…" text.
- One subtle transition when switching screens, and none when the phone asks for reduced motion.
- A **first-run intro card** that explains the product in two sentences and can be dismissed.
- Visible keyboard focus, accessible labels on icon buttons and charts, and safe areas for phone notches.
- Empty states that tell you what to do next ("Widen to 7 days", "Loosen a threshold to see more").
- Errors written in plain language, for example *"Couldn't reach screener.in for the financial statements; try
again shortly"* instead of a raw proxy error.

### E3. ★ Desktop presentation for reviewers

Above 980 px wide, FINN appears as a **phone-width app** with a frame and shadow, its tab bar and chat box attached
to the frame, and a short **explainer panel** beside it covering push, pull and how to try it on a phone. A reviewer
opening the link on a laptop sees the mobile-first product as intended.

---

## Part F — Cost discipline (efficiency earns bonus points)

Technique
Where
Effect

**Rules instead of models**
Taxonomy, scoring, clustering, summaries, alerts, routing, spoken brief, four research sections
The everyday product has zero model cost

**Cheap/strong model routing**
Extraction → cheap model (configurable); judgement (promised vs delivered, bull/bear, "why" chat) → strong model
The strong model runs only where reasoning matters

**Caching by model and prompt**
Every model call (structured, plain-text, web-grounded, chat)
The same question never costs twice, and cached answers work without a key

**Shared cache across features**
Guidance reuses the transcript extraction prompt from promised vs delivered
Two sections for the price of one

**Page-level PDF cache + keyword chunking**
Annual reports: only the ~15 highest-scoring pages, within a character budget
A 400-page report becomes one small call

**Built only when opened**
Each research section
No spend on sections nobody reads

**Free summaries first**
NSE's own `voiceSummary` and the filing text
No summarisation spend

**One request per poll**
Market-wide live NSE feed
20× fewer requests than polling each company

**Usage meter**
`/api/usage`: calls, cache hits, tokens per model
Spend is measured, not guessed

---

## Part G — Engineering quality

- **32 automated tests** (`test_sources.py` and `test_features.py`), run by GitHub Actions on every push: taxonomy
edge cases, clustering, custom windows, rules → alerts → fire-once, live feed filtering and RSS fallback, both
enrichment paths, results cross-check and fallback, Screener parsing, all computed sections, bull/bear grounding,
chat routing, follow-ups, the model path with history, and fallback on model failure.
- A **Screener-shaped test fixture**, so the parsers are tested offline.
- **Compatible with Python 3.11** (checked), since hosts often default to it.
- **Deploy-ready:** Render blueprint (background watcher thread) and Vercel (polling-driven), with
`/healthz`, environment-based configuration, and the real-data snapshot on cold start.
- Inputs validated (ticker format, rule bounds), all external links checked for `http(s)`, all output escaped.

---

## ★ Highlights: what isn't normally present

#
Feature
Why it's unusual

1
**Visible materiality score**
Most feeds sort silently. FINN shows the number and lets you set alerts by it.

2
**Noise-funnel hero ("4 / 29")**
It shows the filtering itself, which is the product's value.

3
**Event clustering**
One results day = one card, not six.

4
**Model-free summaries that refuse to state the obvious**
No hallucination, no cost, and no empty "informed the Exchange about Credit Rating".

5
**Results cross-checked against a second source**
Disagreement between the filing and Screener is shown, not hidden.

6
**Bios from the filing's own SEBI profile, then the web**
Uses a regulatory requirement most builders don't know exists.

7
**Price moves explained by same-day filings**
Answers "why did it move?" automatically.

8
**Fire-once alerts with history filed as read**
No duplicate notifications, and no flood when rules change.

9
**One request watches the whole portfolio, with RSS fallback**
Efficient and harder to block.

10
**Promised vs delivered checked against reported numbers**
Analysing management, not just summarising them.

11
**Bull vs bear that must cite computed facts**
No outside opinion leaks in.

12
**Research priced per section, built only when opened**
Cost is visible to the user.

13
**Ask FINN: facts first, model second, never silent**
Grounded, works without a key, says how each answer was made.

14
**Follow-up memory, plus Ask from any screen**
"What about their debt?" just works, anywhere.

15
**Voice in and voice out, Indian English**
A spoken morning brief, free and accurate.

16
**Never opens on an empty screen**
Quiet days widen the window and say so.

17
**Real-data snapshot + data-integrity repair**
Reviewers see real filings; phantom moves were found and removed.

18
**IST-correct across server time zones**
A subtle hosting bug, avoided.

19
**Desktop shows the phone app with an explainer**
Built for how reviewers will actually open it.

---

## Honest limits and next steps

Limit today
Next step

NSE blocks some cloud IP ranges; when it does, the app reports it and runs on stored data
Ingest from a residential or Indian-region runner on a schedule

Notifications fire while the app is open or installed
Add a push service (VAPID); the service worker already handles push events

Single user, no accounts
Per-user rules and position sizes, so materiality accounts for how much you hold

Taxonomy rules are tuned by hand on ~270 real filings
A labelled evaluation set and precision/recall per category

Promised vs delivered runs on demand
A promises tracker that re-runs after every concall and alerts on a miss

*FINN is a research aid built from companies' own disclosures. It is not investment advice.*