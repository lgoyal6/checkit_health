"""Tunable parameters for the Checkit Health pipeline."""

import os

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# Postgres (Supabase) connection string. When set, the pipeline and API read and
# write claims here instead of the local SQLite file, so live web checks and
# scheduled ingestion share one durable, deploy-proof store.
DATABASE_URL = os.environ.get("DATABASE_URL")
ANALYST_API_KEY = os.environ.get("ANALYST_API_KEY")

# Bluesky monitoring source. Create an app password in Bluesky settings and set
# these in the scheduler/backend environment.
BLUESKY_IDENTIFIER = os.environ.get("BLUESKY_IDENTIFIER")
BLUESKY_APP_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD")

# Mastodon monitoring source. Create an application under Settings ->
# Development on your instance to get an access token (read:search scope is
# enough). Defaults to mastodon.social; point it at any instance you have a
# token for.
MASTODON_INSTANCE_URL = os.environ.get("MASTODON_INSTANCE_URL", "https://mastodon.social")
MASTODON_ACCESS_TOKEN = os.environ.get("MASTODON_ACCESS_TOKEN")

# YouTube monitoring source (search + video stats via the YouTube Data API
# v3). Create a key in Google Cloud Console with the YouTube Data API v3
# enabled: https://console.cloud.google.com/apis/credentials
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

MONITOR_QUERIES = [
    "vaccine",
    "ivermectin",
    "fluoride",
    "mRNA",
    "covid cure",
    "big pharma",
    "detox",
    "raw milk",
]

MODEL_NAME = "gemini-2.5-flash-lite"

CONFIDENCE_THRESHOLD = 0.7

BATCH_DELAY_SECONDS = int(os.environ.get("BATCH_DELAY_SECONDS", "5"))

RETRY_MAX_ATTEMPTS = int(os.environ.get("RETRY_MAX_ATTEMPTS", "5"))
RETRY_INITIAL_BACKOFF_SECONDS = int(os.environ.get("RETRY_INITIAL_BACKOFF_SECONDS", "10"))

MAX_TOKENS = 512
# Evidence reports (Rumor/Summary/Key Facts/Analysis/Conclusion) run longer
# than triage classification, so they get their own, higher token cap.
REPORT_MAX_TOKENS = int(os.environ.get("REPORT_MAX_TOKENS", "768"))
GEMINI_TIMEOUT_MS = int(os.environ.get("GEMINI_TIMEOUT_MS", "60000"))

# Client-side throttling (see rate_limiter.py) so we self-limit before Gemini
# or Google's Fact Check API rejects us. Gemini Flash's free tier is 15
# requests/minute; Fact Check Tools API has no published per-minute cap, so
# 60/min is just a conservative default to stay well within the daily quota.
GEMINI_RPM = int(os.environ.get("GEMINI_RPM", "15"))
FACT_CHECK_RPM = int(os.environ.get("FACT_CHECK_RPM", "60"))
EVIDENCE_RETRIEVAL_ENABLED = os.environ.get(
    "EVIDENCE_RETRIEVAL_ENABLED", "true"
).lower() not in {"0", "false", "no"}
EVIDENCE_CACHE_TTL_SECONDS = int(os.environ.get("EVIDENCE_CACHE_TTL_SECONDS", "21600"))
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "768"))
BACKGROUND_WORKERS = int(os.environ.get("BACKGROUND_WORKERS", "4"))

DEFAULT_INPUT_PATH = "data/posts.json"
DEFAULT_OUTPUT_PATH = "output/claims.json"
DEFAULT_DB_PATH = "db/claims.db"

VALID_LABELS = {"MEDICAL_CLAIM", "GENERAL_HEALTH", "NOISE"}
VALID_TOPICS = {"vaccine", "drug", "treatment", "nutrition", "other"}
