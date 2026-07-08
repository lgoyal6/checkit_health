# Checkit Health

A prototype pipeline **and web app** for monitoring viral health misinformation
on social media.

**Flow:** Bluesky/social posts → viral ranking → keyword pre-filter → Gemini
Flash classifier → confidence gate → falsifiability second pass → Google Fact
Check lookup → Postgres/SQLite/JSON. A FastAPI backend exposes monitor
endpoints, and a React frontend shows a monitoring dashboard first, with manual
claim checks as a secondary analyst tool.

**Live demo:** https://checkit-health-s9p4.vercel.app (backend:
https://checkit-health-api.onrender.com)

The scheduled monitor uses Bluesky because it has a free, open API and real
engagement counts for ranking viral posts.

---

## Quickstart

```bash
# 1. install
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. set your Gemini key (get one free at https://aistudio.google.com/apikey)
export GOOGLE_API_KEY=AIza...

# 3. run the pipeline on the bundled sample data
python run.py --input data/posts.json --output output/claims.json
```

You should see a summary like:

```
========================================================
Checkit Health pipeline summary
========================================================
  Posts ingested:               296
  Filtered by keyword pre-check:  44
  Classified as GENERAL_HEALTH   118
  Classified as MEDICAL_CLAIM     92
  Classified as NOISE             42
  Passed confidence >= 0.7:       71
  Passed falsifiability check:    63
  Matched existing fact-check:    18
  Wrote JSON:                   output/claims.json
  Wrote SQLite:                 db/claims.db
```

(The bundled `data/posts.json` has 296 sample posts; exact counts vary per run.)

Sanity-check ingestion without burning any API credits:

```bash
python run.py --dry-run --input data/posts.json
```

---

## Project layout

```
checkit_health/
├── config.py          # all tunable params (model, threshold, batch size, paths)
├── ingestion.py       # Stage 1 — file / Reddit / Bluesky ingestion
├── prefilter.py       # Stage 1.5 — cheap keyword pre-filter
├── classifier.py      # Stage 2 — Gemini triage, filter, falsifiability check
├── fact_checker.py    # Stage 2.75 — Google Fact Check Tools lookup
├── storage.py         # Stage 3 — SQLite + JSON output
├── run.py             # CLI entrypoint
├── api.py             # FastAPI wrapper (/monitor, /stats, /check, /history)
├── render.yaml        # Render deploy blueprint for the backend
├── frontend/          # React + Vite + Tailwind app
├── data/posts.json    # sample posts (mix of misinfo and normal)
├── db/                # SQLite output lives here (created on first run)
├── output/            # JSON output lives here (created on first run)
└── requirements.txt
```

---

## How the pipeline works

1. **Ingestion** (`ingestion.py`) — `get_posts()` reads a JSON/CSV file,
   `get_posts_reddit()` pulls from a subreddit, and `get_posts_bluesky()`
   searches Bluesky with engagement counts. The shape is validated up front so
   a bad row fails loud, not silent.
2. **Viral ranking** (`run.py`) — posts are sorted by
   `like_count + retweet_count` before `--limit` is applied, so scarce LLM
   budget goes to the highest-reach posts first.
3. **Keyword pre-filter** (`prefilter.py`) — `is_possibly_medical()` drops
   obviously non-medical posts before any LLM spend. Bypass with
   `--skip-prefilter`.
4. **Classification** (`classifier.py`) — each surviving post goes to Gemini in
   strict JSON-only mode and comes back with:
   - `label` — `MEDICAL_CLAIM` | `GENERAL_HEALTH` | `NOISE`
   - `claim` — the specific assertion, one sentence (only if `MEDICAL_CLAIM`)
   - `topic` — `vaccine` | `drug` | `treatment` | `nutrition` | `other`
   - `confidence` — 0.0–1.0

   The system prompt carries few-shot examples so political rants no longer slip
   through as claims. Posts are processed one at a time with a
   `BATCH_DELAY_SECONDS` pause between calls to stay under the free-tier
   15 req/min cap; rate-limit errors are retried with exponential backoff.
5. **Confidence gate** — keep only `MEDICAL_CLAIM` at/above
   `CONFIDENCE_THRESHOLD` (0.7).
6. **Falsifiability second pass** (`classifier.is_falsifiable`) — a second LLM
   call drops claims that can't be checked against evidence.
7. **Fact-check lookup** (`fact_checker.py`) — `check_claim()` queries the free
   Google Fact Check Tools API for an existing verdict. Bypass with
   `--skip-fact-check`.
8. **Output** (`storage.py`) — write `output/claims.json` (pretty JSON array)
   and `db/claims.db` (SQLite `claims` table, primary key on `post_id` so
   re-runs upsert rather than duplicate). Each record is stamped with
   `timestamp_processed` and a `status` of `verified` (a fact check was found)
   or `unverified` (none found).

## CLI

```
python run.py [--input PATH] [--output PATH] [--db PATH] [options]
```

| Flag                | Default              | Purpose                                       |
|---------------------|----------------------|-----------------------------------------------|
| `--input`           | `data/posts.json`    | Path to input `.json` or `.csv`               |
| `--output`          | `output/claims.json` | Path for JSON output                          |
| `--db`              | `db/claims.db`       | Path for SQLite output                        |
| `--dry-run`         | off                  | Read input, print counts, skip all API calls  |
| `--limit N`         | off                  | Only process the first N posts (smoke test)   |
| `--debug`           | off                  | Print every classification, dump `debug.json` |
| `--source`          | `file`               | `file`, `reddit`, or `bluesky`                |
| `--query TEXT`      | —                    | Bluesky query; defaults to configured monitor terms |
| `--subreddit NAME`  | —                    | Subreddit to scrape when `--source reddit`    |
| `--reddit-limit N`  | 50                   | Max posts to pull from Reddit                 |
| `--bluesky-limit N` | 50                   | Max posts to pull per Bluesky query           |
| `--skip-prefilter`  | off                  | Send every post to the classifier             |
| `--skip-fact-check` | off                  | Skip the Google Fact Check lookup             |

### Pulling from Reddit instead of a file

```bash
export REDDIT_CLIENT_ID=...        # create an app at reddit.com/prefs/apps
export REDDIT_CLIENT_SECRET=...
export REDDIT_USER_AGENT="checkit-health/0.1"
python run.py --source reddit --subreddit conspiracy --reddit-limit 50
```

### Monitoring Bluesky

Create a free Bluesky app password in Settings -> App Passwords, then set:

```bash
export BLUESKY_IDENTIFIER=your-handle.bsky.social
export BLUESKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

Run a single query:

```bash
python run.py --source bluesky --query "vaccine" --limit 25
```

Run the default monitor terms from `config.MONITOR_QUERIES`:

```bash
python run.py --source bluesky --limit 50
```

## Web app

### Backend (FastAPI)

```bash
pip install -r requirements.txt
export GOOGLE_API_KEY=AIza...
export GOOGLE_FACT_CHECK_KEY=...   # optional; enables fact-check lookups
export DATABASE_URL=postgresql://... # optional; Supabase Postgres, else SQLite
uvicorn api:app --reload           # http://localhost:8000
```

| Endpoint        | Method | Returns                                             |
|-----------------|--------|-----------------------------------------------------|
| `/health`       | GET    | `{"status": "ok"}`                                  |
| `/check`        | POST   | Classify `{"text": "..."}`; falsifiability + fact-check for real claims |
| `/history`      | GET    | Last 50 stored claims, newest first                 |
| `/monitor`      | GET    | Viral monitored claims ranked by reach              |
| `/stats`        | GET    | Monitor KPI aggregates                              |

Monitor query parameters:

```bash
curl "http://localhost:8000/monitor?window=7d&topic=vaccine&source=bluesky&limit=50"
curl "http://localhost:8000/stats?window=7d"
```

`window` can be `24h`, `7d`, `30d`, or `all`. `/monitor` and `/stats` read
Postgres only and exclude `source='web'` so manual analyst checks do not pollute
the monitor dashboard.

### Frontend (React + Vite + Tailwind)

```bash
cd frontend
npm install
npm run dev                        # http://localhost:5173
```

`VITE_API_URL` (in `frontend/.env`) points the app at the backend; defaults to
`http://localhost:8000`.

## Deployment

**Backend → Render.** A `render.yaml` blueprint is included. Push to GitHub,
create a new Blueprint in Render pointed at this repo, then set `GOOGLE_API_KEY`,
`GOOGLE_FACT_CHECK_KEY`, `DATABASE_URL`, `BLUESKY_IDENTIFIER`, and
`BLUESKY_APP_PASSWORD` in the dashboard. Render runs
`uvicorn api:app --host 0.0.0.0 --port $PORT`; `/health` is the health check.

**Frontend → Vercel.** Import the repo in Vercel with the root set to
`frontend/` (a `frontend/vercel.json` configures the Vite build and SPA
routing). Set `VITE_API_URL` to your live Render URL, then redeploy. Put the
resulting URL at the top of this README.

**Database → Supabase (Postgres).** SQLite works locally, but Render's disk is
ephemeral, so production uses a free Supabase Postgres project instead. Set the
`DATABASE_URL` env var (the Supabase connection string) on the backend and the
API will read/write there automatically. Without `DATABASE_URL`, manual checks
can still run locally, but the monitor endpoints return empty payloads. Schema:
a single `claims` table (same columns as the SQLite one, plus a `source` column
tagging `web` / `bluesky` / `reddit` / `file`).

### Automation (GitHub Actions)

Two workflows keep the deployed app healthy and growing:

- **`keepwarm.yml`** — pings `/health` every 10 min so Render's free tier
  doesn't cold-start on the next visitor. No secrets needed.
- **`ingest.yml`** — every 6 hours (and on-demand) run of the Bluesky monitor
  pipeline into Postgres, so the dashboard fills up on its own. Needs repo
  secrets: `GOOGLE_API_KEY`, `DATABASE_URL`, `GOOGLE_FACT_CHECK_KEY`,
  `BLUESKY_IDENTIFIER`, `BLUESKY_APP_PASSWORD`.

---

## Tuning (`config.py`)

| Param                           | Default               | What it does                                     |
|---------------------------------|-----------------------|--------------------------------------------------|
| `MODEL_NAME`                    | `gemini-2.5-flash-lite` | Gemini model used for classification           |
| `CONFIDENCE_THRESHOLD`          | `0.7`                 | Min confidence to pass the gate                  |
| `BATCH_DELAY_SECONDS`           | `5`                   | Sleep between classification calls (free-tier RPM) |
| `RETRY_MAX_ATTEMPTS`            | `5`                   | Attempts per call before giving up on rate limits |
| `RETRY_INITIAL_BACKOFF_SECONDS` | `10`                  | First backoff on a 429, doubled each retry       |
| `MAX_TOKENS`                    | `512`                 | Output cap per classification call               |
| `BLUESKY_IDENTIFIER`            | env                   | Bluesky handle or account identifier             |
| `BLUESKY_APP_PASSWORD`          | env                   | Bluesky app password for XRPC auth               |
| `MONITOR_QUERIES`               | health terms          | Default Bluesky searches when `--query` is omitted |

---

### Data contract

Each post must be a dict with these keys:

| Key             | Type | Notes                                   |
|-----------------|------|-----------------------------------------|
| `id`            | str  | Unique post ID (used as primary key)    |
| `text`          | str  | Post body                               |
| `timestamp`     | str  | ISO 8601, e.g. `2026-06-15T08:14:22Z`   |
| `username`      | str  | Author handle (no `@`)                  |
| `retweet_count` | int  | 0 or more                               |
| `like_count`    | int  | 0 or more                               |

## Output schema

`output/claims.json` and the `claims` table in `db/claims.db` share this
shape:

| Column                | Type     | Notes                          |
|-----------------------|----------|--------------------------------|
| `post_id`             | TEXT PK  | From original post             |
| `username`            | TEXT     |                                |
| `text`                | TEXT     | Full original post text        |
| `timestamp`           | TEXT     | Original post time             |
| `retweet_count`       | INTEGER  |                                |
| `like_count`          | INTEGER  |                                |
| `claim`               | TEXT     | Extracted claim sentence       |
| `topic`               | TEXT     | vaccine/drug/treatment/...     |
| `confidence`          | REAL     | 0.0–1.0                        |
| `timestamp_processed` | TEXT     | ISO 8601, pipeline run time    |
| `status`              | TEXT     | `verified` or `unverified`     |
| `fact_check_verdict`  | TEXT     | Publisher's rating, or null    |
| `fact_check_source`   | TEXT     | Fact-check publisher, or null  |
| `fact_check_url`      | TEXT     | Link to the fact check, or null |
| `source`              | TEXT     | `bluesky`, `web`, `reddit`, or `file` |
| `classification_reasoning` | TEXT | Short classifier rationale      |

Re-running upserts on `post_id`, so the same post never appears twice.
