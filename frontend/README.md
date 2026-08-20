# TRACE Frontend

Build the frontend for "TRACE" — an AI-powered decision intelligence

dashboard for software teams. It lets developers ask natural-language

questions about why past decisions were made in their codebase, and get

back evidence-grounded answers with citations, related past decisions,

and confidence scoring.

TECH: React, Tailwind CSS, clean component structure. Use mock/placeholder

data everywhere for now — structure it so it's easy to swap for real API

calls later (assume a REST backend at endpoints like /repos, /query, /recall).

AESTHETIC: Modern developer-tool feel, but warm and editorial rather than

sterile/corporate — think clean sans-serif typography, generous whitespace,

a restrained accent color (deep navy or indigo) against a light neutral

background, soft rounded corners, subtle borders instead of heavy shadows.

Support both light and dark mode.

PAGES / SCREENS:

1. Repo Selector (landing view)

   - A simple header "TRACE" with a short tagline

   - A dropdown/searchable list to pick a repository (mock 3 repos:

     "auth-service", "MiniHooked", "CaseSense")

   - Once selected, navigate to the main dashboard for that repo

2. Main Dashboard (core screen)

   - Top: repo name shown, with a way to switch repos

   - A prominent question input box with placeholder text like

     "Why was OAuth chosen over JWT in the auth module?" and an "Ask" button

   - Below it, an Answer Panel that shows:

     - The generated answer text

     - Citation chips below the answer (e.g. "commit a3f21c", "PR #142")

       — clickable, styled as small pill/tag buttons

     - A confidence badge (High / Medium / Low confidence), color-coded

       (green/amber/red), positioned near the answer

     - If applicable, a Contradiction Warning banner above the answer —

       amber/red alert style, e.g. "This may conflict with a past decision:

       commit a3f21c removed basic-auth fallback for security reasons"

   - A "Related Past Decisions" section below the answer panel: a list of

     3-5 items, each showing a short title and a relative time ("3 months

     ago"), styled as a clean list with dividers

   - Include a loading state (skeleton or spinner) for when a question is

     "submitted", and an empty state for before any question is asked

3. Issue Recall View (secondary screen, can be a tab or separate route)

   - A simple form to paste in a new issue's title + description

   - A "Find Similar" button

   - Results shown as a list of matching past issues/decisions with

     similarity indicators

Use realistic mock data (a few sample Q&A pairs, sample related decisions,

sample citations) so the app feels alive and demoable without a backend

connected. Keep components modular (QuestionInput, AnswerPanel,

CitationChip, ConfidenceBadge, ContradictionBanner, RelatedDecisionsList,

RepoSelector) so they're easy to wire to a real API later.

## Development

Prefer working locally? You need Node.js and npm — [install with nvm](https://github.com/nvm-sh/nvm#installing-and-updating).

```sh
git clone <this-repository-url>
cd <repository-name>
npm i
npm run dev
```
