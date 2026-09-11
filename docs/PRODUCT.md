# Checkit Health product guide

## Purpose

Checkit Health helps an analyst notice potentially harmful health claims that
are gaining reach on public social platforms. It is a monitoring and triage
tool - not a medical authority, automated moderation system, or substitute for
clinical advice.

The app answers five practical questions:

1. What potentially checkable health claims are circulating?
2. Which rumors are growing fastest right now, and where are they spreading?
3. What does the published medical literature actually say about each one?
4. Which ones has a qualified person already ruled on, and what did they decide?
5. If a response is warranted, what would a well-formed one look like?

It answers the fifth as a draft only. Checkit has no ability to publish
anything, by design.

## Current workflow

```text
Public post or pasted statement
  → identify a concrete health claim
  → determine whether it is specific and falsifiable
  → group it with every other post carrying the same rumor
  → record reach now, so growth is measurable on the next run
  → look for a matching ClaimReview fact-check and retrieve medical evidence
  → show the analyst the narrative, its trajectory, and its sources
  → the analyst decides; optionally draft a response for a human to send
```

The unit an analyst works is the narrative, not the post. A rumor that appears
in forty posts across four platforms is one thing to track, one set of evidence
to gather, and one decision to make.

For scheduled monitoring, the same workflow runs in batches after posts are
ranked by engagement. The Monitor page presents those stored results. For a
one-off statement, the Check page sends the text through the interactive API.

## Sources and collection

| Source | How Checkit gets data | Data used | Notes |
|---|---|---|---|
| Bluesky | Bluesky API search | Public post text, author, timestamp, likes, reposts | Requires an app password. Default scheduled source. |
| Mastodon | Mastodon instance search API | Public status text, account, timestamp, favourites, boosts | Requires an access token and instance URL. |
| YouTube | YouTube Data API v3 | Video title/description, channel, published time, view count, and like count | Requires a YouTube API key. Views are used as the reach proxy. |
| Reddit | Reddit API through PRAW | Post title/body, author, timestamp, score/comment proxy | Requires Reddit API credentials. |
| JSON/CSV | Local file ingestion | The same normalized post fields | Useful for demos and offline evaluation. |
| Manual Check | A person pastes text in the web app | Submitted statement only | Stored as `source=web` only when Postgres is configured. |

All sources are normalized to a common post shape: an ID, text, author,
timestamp, like count, repost count, and source. Engagement ranking is
currently calculated as likes plus reposts. A source's availability and exact
engagement fields depend on that platform's API and credentials.

## External APIs and models

| Service | Used for | Returned to Checkit |
|---|---|---|
| Gemini Flash Lite | Claim triage and optional analyst summary | Label, extracted claim, topic, confidence, rationale, and report sections |
| Google Fact Check Tools API | Search for existing ClaimReview records | Fact-check publisher, textual rating, and a link to the published review |
| Supabase Postgres | Durable hosted storage | Stored social claims, manual checks, and Monitor aggregates |
| Render | Backend hosting | FastAPI service |
| Vercel | Frontend hosting | React application |

The Fact Check API finding is a possible match, not proof that a claim has
been fully adjudicated. Checkit applies a keyword-overlap threshold before
displaying a result, but analysts should open the linked review and confirm it
addresses the exact claim and context.

## What the app presents

### Monitor

The Monitor page displays social claims that passed the pipeline's confidence
and falsifiability gates. It includes:

- engagement tiers (Viral, High, Medium, and Low), calculated from the current
  result set, plus an **All claims** view for the combined filtered list;
- total monitored claims, reach, likely-false count, and leading topic;
- filters for time window, topic, and source;
- search across extracted claim, post text, topic, and author;
- sorting by reach, likes, reposts, recency, or model confidence;
- full original post text, classification rationale, engagement metrics, and
  any published fact-check link; and
- a **Check claim** action that opens the claim in the Check view.

### Check

The Check page accepts a social post or statement. It shows whether the model
identified a specific medical claim and, when appropriate, its extracted claim,
topic, claim-detection confidence, falsifiability result, and a possible
published fact-check. It can also produce an AI-generated analyst summary.

Claim-detection confidence means “how likely this is a checkable medical
claim”; it does **not** measure whether the claim is true.

For each medical claim, the Check view also exposes deterministic analyst
signals: a normalized cluster identifier, conservative language detection,
an evidence-quality grade, adverse-event routing, configurable escalation
reasons, response guidance, and an audit timestamp. These signals never infer
account authenticity or coordinated behavior from a single post, never turn a
missing fact-check into a false verdict, and never replace human judgment.

Monitor responses include a cluster identifier and interactions-per-hour
velocity alongside total reach. This lets an analyst group wording variants
and distinguish a fast-growing claim from an old post with a large lifetime
total.

### History

History lists recent manual checks saved to Postgres. It separates items with a
matching published fact-check from those that still need evidence review.

## Pipeline decisions

1. **Rank first.** Higher-engagement posts receive the available model budget
   first.
2. **Pre-filter.** Obvious non-health content is discarded before model use.
3. **Classify.** Gemini returns `MEDICAL_CLAIM`, `GENERAL_HEALTH`, or `NOISE`.
4. **Gate.** Only confident medical claims proceed.
5. **Check falsifiability.** Vague opinions are removed from the monitor.
6. **Find prior verification.** Google Fact Check Tools is queried for a
   relevant ClaimReview item.
7. **Store and present.** Results are saved to JSON and SQLite locally, and to
   Postgres when configured.
8. **Attach transparent triage signals.** Deterministic rules describe
   evidence quality, language, cluster membership, adverse-event routing,
   escalation, privacy boundaries, and the recommended next analyst action.

## Limits and responsible use

- Model output can be inaccurate, incomplete, or outdated.
- Absence of a fact-check does not indicate that a claim is true.
- A fact-check match can be contextual or imperfect; inspect the source.
- The tool does not diagnose, treat, or advise individuals.
- Analysts should prioritize authoritative public-health sources, original
  research, and qualified human review for high-risk claims.
- Only collect and display public data in line with each platform's terms and
  applicable privacy rules.

## What it does now, briefly

Today, Checkit ingests or accepts health-related statements, surfaces the
highest-engagement specific medical claims, looks for existing fact-checks, and
gives an analyst a single place to review the original post, claim context,
engagement, and linked verification. It is ready for analyst triage; it is not
an autonomous medical fact-checker.
