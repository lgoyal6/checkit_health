"""Tunable parameters for the Checkit Health pipeline."""

import os

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# Postgres (Supabase) connection string. When set, the pipeline and API read and
# write claims here instead of the local SQLite file, so live web checks and
# scheduled ingestion share one durable, deploy-proof store.
DATABASE_URL = os.environ.get("DATABASE_URL")

# Bluesky monitoring source. Create an app password in Bluesky settings and set
# these in the scheduler/backend environment.
BLUESKY_IDENTIFIER = os.environ.get("BLUESKY_IDENTIFIER")
BLUESKY_APP_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD")
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

BATCH_DELAY_SECONDS = 5

RETRY_MAX_ATTEMPTS = 5
RETRY_INITIAL_BACKOFF_SECONDS = 10

MAX_TOKENS = 512

DEFAULT_INPUT_PATH = "data/posts.json"
DEFAULT_OUTPUT_PATH = "output/claims.json"
DEFAULT_DB_PATH = "db/claims.db"

VALID_LABELS = {"MEDICAL_CLAIM", "GENERAL_HEALTH", "NOISE"}
VALID_TOPICS = {"vaccine", "drug", "treatment", "nutrition", "other"}
