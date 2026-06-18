# Checkit Health

A 3-stage prototype pipeline for triaging medical misinformation in social
media posts.

**Flow:** local posts file → Claude Haiku classifier → SQLite + JSON of
high-confidence medical claims awaiting fact-check.

There is no X API key wired up yet — the ingestion stage reads a local
JSON/CSV file. When the key arrives, swap one function (`get_posts`) and
the rest of the pipeline is unchanged. See [Swapping in the X API
later](#swapping-in-the-x-twitter-api-later).

---

## Quickstart

```bash
# 1. install
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. set your Anthropic key
export ANTHROPIC_API_KEY=sk-ant-...

# 3. run the pipeline on the bundled sample data
python run.py --input data/posts.json --output output/claims.json
```

You should see a summary like:

```
========================================================
Checkit Health pipeline summary
========================================================
  Posts ingested:               25
  Classified as MEDICAL_CLAIM:  13
  Passed confidence >= 0.7:     11
  Wrote JSON:                   output/claims.json
  Wrote SQLite:                 db/claims.db
```

Sanity-check ingestion without burning any API credits:

```bash
python run.py --dry-run --input data/posts.json
```

---

## Project layout

```
checkit_health/
├── config.py          # all tunable params (model, threshold, batch size, paths)
├── ingestion.py       # Stage 1 — get_posts() reads JSON/CSV
├── classifier.py      # Stage 2 — Claude Haiku triage + filter
├── storage.py         # Stage 3 — SQLite + JSON output
├── run.py             # CLI entrypoint
├── data/posts.json    # 25 sample posts (mix of misinfo and normal)
├── db/                # SQLite output lives here (created on first run)
├── output/            # JSON output lives here (created on first run)
└── requirements.txt
```

---

## How the 3 stages work

### Stage 1 — Ingestion (`ingestion.py`)

`get_posts(source)` reads a JSON or CSV file and returns a list of post
dicts. The shape is validated up front so a bad row fails loud, not
silent.

### Stage 2 — Classification (`classifier.py`)

Each post is sent to Claude Haiku with a strict JSON-only system prompt.
The model returns:

- `label` — `MEDICAL_CLAIM` | `GENERAL_HEALTH` | `NOISE`
- `claim` — the specific assertion, one sentence (only if `MEDICAL_CLAIM`)
- `topic` — `vaccine` | `drug` | `treatment` | `nutrition` | `other`
- `confidence` — 0.0–1.0

Posts are processed in batches of `BATCH_SIZE` (default 5) to stay under
rate limits. Only `MEDICAL_CLAIM` posts with `confidence >=
CONFIDENCE_THRESHOLD` (default 0.7) move on to Stage 3.

### Stage 3 — Output (`storage.py`)

Surviving posts are written to:

- `output/claims.json` — pretty-printed JSON array
- `db/claims.db` — SQLite, `claims` table (primary key on `post_id`, so
  re-runs upsert rather than duplicate)

Every record is stamped with `timestamp_processed` and `status =
pending_fact_check` for the downstream fact-checking stage.

---

## CLI

```
python run.py [--input PATH] [--output PATH] [--db PATH] [--dry-run]
```

| Flag         | Default              | Purpose                                    |
|--------------|----------------------|--------------------------------------------|
| `--input`    | `data/posts.json`    | Path to input `.json` or `.csv`            |
| `--output`   | `output/claims.json` | Path for JSON output                       |
| `--db`       | `db/claims.db`       | Path for SQLite output                     |
| `--dry-run`  | off                  | Read input, print count, skip Anthropic    |

---

## Tuning (`config.py`)

| Param                  | Default              | What it does                                |
|------------------------|----------------------|---------------------------------------------|
| `MODEL_NAME`           | `claude-haiku-3-5`   | Anthropic model used for classification     |
| `CONFIDENCE_THRESHOLD` | `0.7`                | Min confidence to pass to output stage      |
| `BATCH_SIZE`           | `5`                  | Posts per batch (rate-limit cushion)        |
| `MAX_TOKENS`           | `512`                | Output cap per classification call          |

---

## Swapping in the X (Twitter) API later

Exactly one function changes:

**`ingestion.get_posts(source: str) -> list[dict]`** in `ingestion.py`.

Replace its body with an X API call. Return a list of dicts that match
the data contract below. **Nothing else in the pipeline changes** —
classifier, storage, CLI, and config all stay the same.

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

### Example replacement (sketch)

```python
# ingestion.py
import os, tweepy

def get_posts(source: str):
    client = tweepy.Client(bearer_token=os.environ["X_BEARER_TOKEN"])
    resp = client.search_recent_tweets(
        query=source,  # `source` is now an X search query
        max_results=100,
        tweet_fields=["created_at", "public_metrics", "author_id"],
        expansions=["author_id"],
        user_fields=["username"],
    )
    users = {u.id: u.username for u in (resp.includes.get("users") or [])}
    return [
        {
            "id": str(t.id),
            "text": t.text,
            "timestamp": t.created_at.isoformat(),
            "username": users.get(t.author_id, ""),
            "retweet_count": t.public_metrics["retweet_count"],
            "like_count": t.public_metrics["like_count"],
        }
        for t in (resp.data or [])
    ]
```

Then invoke with a query instead of a file path:

```bash
export X_BEARER_TOKEN=...
python run.py --input "medical misinformation -is:retweet lang:en" \
              --output output/claims.json
```

---

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
| `status`              | TEXT     | Always `pending_fact_check`    |

Re-running upserts on `post_id`, so the same post never appears twice.
