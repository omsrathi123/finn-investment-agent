# FINN — financial investment agent

FINN is a mobile-first prototype for an investor's daily workflow:

- **Feed:** prioritised company announcements for a configurable time window; routine items are collapsed.
- **Alerts:** configurable end-of-day volume and price-move thresholds.
- **Results:** extracts Revenue, EBITDA, PBT and PAT from a results filing; calculates QoQ/YoY in Python and links back to the filing.
- **Research:** compares management guidance with later concall reporting and links to the source transcripts.

## Run locally

Requires Python 3.10+.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python seed_demo.py
uvicorn app:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000). To open it on a phone on the same Wi-Fi, use the computer's LAN IP and start Uvicorn with `--host 0.0.0.0`.

The seed script creates simulated prices and clearly marked demo filings so the feed and alert screens can be tried without scrapers. Do not treat seeded values as market data.

## Deploy a shareable app

**Source repository:** [omsrathi123/finn-investment-agent](https://github.com/omsrathi123/finn-investment-agent)

GitHub hosts the source; Render runs the FastAPI backend. GitHub Pages alone cannot run FINN's Python API. This repository includes a Render Blueprint in `render.yaml` and a GitHub Actions workflow that runs focused tests on pushes and pull requests.

1. Sign in to Render with GitHub and choose **New → Blueprint**.
2. Select `omsrathi123/finn-investment-agent`; Render reads its `render.yaml`.
3. Set `GEMINI_API_KEY` in Render's environment settings to enable PDF and transcript analysis. Do not commit the key or put it in GitHub files.
4. Share the deployed `*.onrender.com` URL with reviewers; the mobile-friendly app is served at `/`.

The free service uses temporary storage. Demo data is seeded at startup, but manually fetched data and cached model/PDF results can be lost when the service restarts. Data refresh is not scheduled.

`.gitignore` excludes local environments, `.env`, the SQLite database and fetched caches. The demo dataset can be recreated with `python seed_demo.py`.

## Live data and research

Set `GEMINI_API_KEY` (or add it to a local `.env` file) for results extraction and transcript analysis. Then run:

```powershell
python run_day1.py
```

`run_day1.py` fetches prices, announcements and Screener.in document links for the selected symbols. The Feed shows the latest data dates; it does not refresh itself or send notifications while closed. Use `python fetch_prices.py`, `python fetch_announcements.py` or `python fetch_docs.py` for separate refreshes.

## Implementation choices and limitations

- Announcement prioritisation is deterministic rules, not an LLM classification call.
- FAST/SMART model routing, local PDF text caching and prompt-result caching limit model cost.
- Pull analysis is intentionally focused on transcript-based narrative versus numbers; it is not investment advice and does not yet cover the full research menu in the challenge.
- Public-source scrapers may be blocked or change format. Results and research require source PDFs and a configured model key; generated claims should be checked against the linked primary sources.
- This is a prototype, not a live notification service or a trading system.

For the submission covering note and remaining handoff checklist, see [SUBMISSION.md](SUBMISSION.md).
