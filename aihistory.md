# AI build history

## From idea to working product

I brought the FINN idea and the investment-agent challenge brief. I used AI as an execution partner to turn that direction into a usable product: reviewing the existing code, prioritizing the work, implementing features, improving the experience, and getting the app online.

I stayed involved throughout. I set goals, chose what to build next, checked the results, and raised issues when something did not behave as expected. AI helped me move faster from each decision to working code.

## How AI helped

- **Turned the brief into a build plan.** We focused on an investor's daily workflow: surface important filings, explain why they matter, and make company research easy to explore.
- **Built and refined the product.** AI assisted with filing digests and alerts, stock details, source-backed results and research, and the conversational Ask FINN experience.
- **Improved reliability.** We worked through data freshness, fallback behavior, model configuration, API-key handling, tests, and CI.
- **Made it presentable and shareable.** AI helped with the mobile interface, architecture documentation, screenshots, setup instructions, and deployment.

## Conversation highlights

The excerpts below capture how I directed the work. Prompts are lightly edited for readability; AI replies are concise summaries of the saved conversations.

> **Me:** “What is my repo doing?”
>
> **AI:** FINN was a stock research and monitoring prototype. I mapped its data sources, analysis modules, API, and interface, then explained what was working and what still needed attention.

> **Me:** “Is my repo solving this challenge?”
>
> **AI:** The prototype had a good foundation, but needed a more dependable investor workflow, clearer source evidence, better coverage, and a shareable deployment.

> **Me:** “How do we fill these gaps and make a final submission?”
>
> **AI:** Focus on one complete loop: refresh data, prioritize filings, show source-backed results, and let the investor research a company. Improve reliability and mobile usability before adding breadth.

> **Me:** “Build a working stock detail view with filings, analysis, charts, and metrics.”
>
> **AI:** Added a mobile-friendly stock detail workflow with market information, filings, related links, and manual refresh, then ran tests and a phone-width check.

> **Me:** “Is it using any AI model?” and “Check the whole repo.”
>
> **AI:** Explained FINN's Gemini workflows, then updated model defaults and API-key handling and made the live API smoke test opt-in so test discovery would not call the service.

> **Me:** “Include the complete architecture in the README.”
>
> **AI:** Documented the system diagram, modules, data flow, API, storage, deployment, and limitations.

> **Me:** “The feed shows the same data for every time window. Track it down.”
>
> **AI:** The date-window issue was addressed in the feed work; commit `344c691` fixes the demo time-window behavior.

> **Me:** “Deploy it on my existing Vercel.”
>
> **AI:** Pushed the implementation to `main`, verified Vercel's production deployment, and confirmed the app at [finnportfoliobyiom.vercel.app](https://finnportfoliobyiom.vercel.app/).

> **Me:** “Add the website link to the README” and “Update my GitHub repo.”
>
> **AI:** Added the live URL and pushed the README update in commit `0069f05`.

## Milestones

| Stage | What we worked on |
|---|---|
| Product direction | Reviewed the challenge, assessed the existing prototype, and chose a focused investor workflow instead of building a broad set of unfinished features. |
| Core experience | Added filing prioritization, source links, stock details, results and research workflows, and clearer data/error states. |
| Reliability | Improved model configuration and key handling, added automated tests, and used GitHub Actions to validate changes. |
| Interaction and polish | Expanded the mobile-first UI and added conversational Ask FINN with company context and follow-up questions. |
| Release | Published the project to GitHub and deployed it to Vercel. The live app is [finnportfoliobyiom.vercel.app](https://finnportfoliobyiom.vercel.app/). |

## Selected build commits

- `cd78a5d` — stock detail workflow
- `8929db9` — model defaults and test-discovery fixes
- `0c2755a` — Vercel deployment setup
- `dd9b37d` — push and pull features
- `e96b285` — conversational Ask FINN and UI updates
- `0069f05` — live app link in the README

## Outcome

The result is a deployed, mobile-first investment research prototype. AI accelerated the path from product direction to implementation; I guided the scope, reviewed the behavior, and decided how the product should evolve.

This is a concise account of the AI-assisted build process, not a verbatim transcript.
