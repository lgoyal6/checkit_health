# Checkit Health - v1 Demo

**TL;DR:** v1 works end-to-end. 296 tweets in → 34 medical claims surfaced for review.
To get to v2 (live data + fact-checking) we need an **X API key (~$200/mo)** and **paid Gemini (<$5/mo)**.

---

## What v1 does

3-stage pipeline: **ingest** posts → **classify** with Gemini → **output** flagged claims to JSON + SQLite, each stamped `pending_fact_check`.

Run: `python run.py --input data/posts.json --output output/claims.json`

## Results

| Metric | Value |
|---|---:|
| Posts ingested | 296 |
| Classified cleanly | 65 |
| Flagged as medical claims (conf ≥ 0.70) | 34 |

Topic mix: **22 vaccine · 7 treatment · 3 other · 1 nutrition · 1 drug**

Example: *"The Covid vaccine is the cause of Covid, not the cure."* - vaccine, conf 1.00

Full interactive list in attached `dashboard.html`.

## Limits of v1

- No live data (Apify scrape, not X API)
- No fact-checking yet - only triage
- Free-tier Gemini rate-limited 231 of 296 posts in this run

## Asks for v2

| Need | Cost | Unlocks |
|---|---|---|
| **X API Basic** | ~$200/mo | Live ingestion + real engagement data |
| **Gemini paid tier** | <$5/mo | Removes daily quota wall |
| Eng time | - | Fact-checking stage + claim dedupe |

The X API key is the single highest-leverage ask.
