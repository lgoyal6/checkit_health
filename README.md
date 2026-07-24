# Checkit Health

Checkit Health is a prototype for monitoring health misinformation on public social platforms. It ranks high-engagement posts, identifies specific medical claims, looks for matching published fact-checks, and provides a dashboard plus a manual claim-checking workflow.

> **Important:** This is an analyst-support and research prototype, not medical advice or an automated truth service. AI-generated classifications and summaries can be wrong. Review any linked, published fact-check and its sources before acting on a claim.

**Live demo:** https://checkit-health-s9p4.vercel.app<br>
**API:** https://checkit-health-api.onrender.com

## What it does

```text
Bluesky / Mastodon / YouTube / Reddit / file
  → engagement ranking → medical keyword filter → Gemini claim triage
  → confidence + falsifiability gates → Google Fact Check lookup
  → Postgres / SQLite / JSON → FastAPI → React monitor and Check view
```

- **Monitor:** browse ranked social claims by topic, source, time window, engagement tier, normalized claim cluster, and growth velocity. **Check claim** sends any monitor row to the manual checker.
- **Check:** classify a pasted statement, find a possible matching published fact-check, grade the retrieved evidence, expose escalation and adverse-event signals, and optionally generate an AI summary for analyst review.
- **History:** review saved manual checks when Postgres is configured.
- **Grounded evidence:** retrieve and rank ClaimReview, PubMed, and
  ClinicalTrials.gov results; reports cite the exact returned passages or
  explicitly return insufficient evidence.
- **Analyst governance:** record human review state, notes, reviewer identity,
  and an append-only audit trail.

## Quick start

Requirements: Python 3.11+ and a Gemini API key. Node 20+ is needed for the frontend.

```bash
git clone <your-repo-url>
cd checkit_health/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export GOOGLE_API_KEY=AIza...
export PYTHONPATH=app

# Validate bundled data without making API calls
python scripts/run.py --dry-run --input data/posts.json

# Run the complete pipeline
python scripts/run.py --input data/posts.json --output output/claims.json
```

The bundled sample has 296 posts. The dry run should report 44 keyword-filtered posts and 252 posts queued for classification.

## Run the web app

Start the API from `backend/app` so its flat module imports resolve:

```bash
cd backend/app
../.venv/bin/uvicorn api:app --reload
```

Then start the frontend in another terminal:

```bash
cd frontend
npm ci
npm run dev
```

The frontend defaults to `http://localhost:8000`. Set `VITE_API_URL` in `frontend/.env` to point it elsewhere.

### Optional services

```bash
export GOOGLE_FACT_CHECK_KEY=...       # Enables existing fact-check lookup
export DATABASE_URL=postgresql://...   # Durable production storage
export BLUESKY_IDENTIFIER=...
export BLUESKY_APP_PASSWORD=...
export MASTODON_ACCESS_TOKEN=...
export MASTODON_INSTANCE_URL=https://mastodon.social
export YOUTUBE_API_KEY=...
```

Without `DATABASE_URL`, the CLI writes SQLite and JSON locally. The Monitor endpoint is intentionally Postgres-only, so it returns no rows in SQLite-only mode.

## Project layout

```text
backend/
  app/                 # FastAPI app and pipeline modules
  scripts/run.py       # CLI entry point
  data/posts.json      # Bundled sample data
  tests/               # API and pipeline unit tests
  requirements.txt
  render.yaml
frontend/
  src/                 # React pages, components, and tests
  package.json
.github/workflows/     # CI, scheduled ingestion, keep-warm
```

## Pipeline and CLI

The pipeline sorts posts by `like_count + retweet_count`, applies a medical-keyword filter, uses Gemini to classify a post as `MEDICAL_CLAIM`, `GENERAL_HEALTH`, or `NOISE`, and retains sufficiently confident, falsifiable medical claims. It searches Google's ClaimReview index for a closely matching published fact-check before persisting the result.

```bash
cd backend
export PYTHONPATH=app

python scripts/run.py --source bluesky --limit 50
python scripts/run.py --source mastodon --query vaccine --mastodon-limit 25
python scripts/run.py --source youtube --query "raw milk" --youtube-limit 25
python scripts/run.py --source reddit --subreddit conspiracy --reddit-limit 50
```

| Flag | Purpose |
|---|---|
| `--input PATH` | Read a JSON or CSV file (default: `data/posts.json`) |
| `--source` | `file`, `reddit`, `bluesky`, `mastodon`, or `youtube` |
| `--query TEXT` | Source search query; defaults to monitor terms |
| `--limit N` | Limit ranked posts before model work |
| `--dry-run` | Validate input and prefilter counts without API calls |
| `--skip-prefilter` | Send every post to Gemini |
| `--skip-fact-check` | Skip Google Fact Check lookup |

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness check |
| `GET /health/db` | Non-secret Postgres connectivity diagnostic |
| `POST /check` | Classify one submitted statement |
| `POST /jobs/check` | Queue claim processing and return a status URL |
| `GET /jobs/{job_id}` | Read queued check status/result |
| `POST /report` | Generate an AI analyst summary for a classified claim |
| `GET /history` | Recent saved checks |
| `GET /monitor` | Social claims ranked by reach |
| `GET /stats` | Monitor aggregates |
| `PATCH /claims/{post_id}/review` | Save an analyst decision and audit event |
| `GET /claims/{post_id}/audit` | Read the review audit trail |

`/monitor` accepts `window` (`24h`, `7d`, `30d`, or `all`), `topic`, `source`, and `limit`. `/monitor` and `/stats` exclude manual web checks.

## Testing and quality checks

```bash
# Backend
cd backend
export PYTHONPATH=app
pytest
ruff check app scripts tests

# Frontend
cd ../frontend
npm ci
npm run test
npm run build
```

CI runs these checks for pull requests and pushes to both `main` and `dev`. Scheduled ingestion runs every six hours. Bluesky is required; Mastodon and YouTube run when their corresponding repository secrets are configured.

Evidence architecture, configuration, limitations, and evaluation are
documented in [docs/EVIDENCE.md](docs/EVIDENCE.md).

## Deployment

- Deploy `backend/render.yaml` to Render and set its listed secrets.
- Deploy `frontend/` to Vercel and set `VITE_API_URL` to the Render URL.
- Use Supabase Postgres via `DATABASE_URL` for durable production data.

## Data contract

Input posts require `id`, `text`, `timestamp`, `username`, `retweet_count`, and `like_count`. Persisted claims include the original post, extracted claim, topic, confidence, engagement counts, processing time, source, and any matched fact-check publisher, verdict, and URL. Re-runs upsert records by `post_id`.

For the full product, data-source, API, and user-workflow reference, see
[docs/PRODUCT.md](docs/PRODUCT.md).
