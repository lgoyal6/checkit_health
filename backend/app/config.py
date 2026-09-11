"""Tunable parameters for the Checkit Health pipeline."""

import os
from pathlib import Path

# Anchored to backend/ rather than the process working directory. The CLI runs
# from backend/ and the API runs from backend/app/, so a relative default made
# them read and write two different SQLite files. That was harmless while the
# monitor was Postgres-only; it is not once the narrative ledger works locally.
_BACKEND_DIR = Path(__file__).resolve().parents[1]

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
DEFAULT_DB_PATH = os.environ.get(
    "CHECKIT_DB_PATH", str(_BACKEND_DIR / "db" / "claims.db")
)

VALID_LABELS = {"MEDICAL_CLAIM", "GENERAL_HEALTH", "NOISE"}
VALID_TOPICS = {"vaccine", "drug", "treatment", "nutrition", "other"}

# --- Narrative layer -------------------------------------------------------
# Cosine similarity above which two claims are treated as the same rumor.
#
# The right value is a property of the embedding, not a universal constant: a
# managed sentence embedding and the offline hashed fallback put paraphrases in
# completely different similarity bands, so one number cannot serve both. Both
# defaults come from the F1 sweep in scripts/calibrate_threshold.py against the
# labeled pairs in data/claim_pairs.json. Re-run it whenever the embedding
# model or its dimensionality changes.
NARRATIVE_THRESHOLD_MANAGED = float(
    os.environ.get("NARRATIVE_THRESHOLD_MANAGED", "0.75")
)
# The offline fallback scores 0.57 AUC on data/claim_pairs.json, which is
# barely better than chance: surface features cannot tell "MMR causes autism"
# from "MMR causes seizures". So the fallback threshold is set where measured
# precision was 1.0, meaning it only merges near-identical wording and
# otherwise leaves claims apart. Under-merging is the safe failure: a missed
# merge costs an analyst a duplicate row, a wrong merge attaches the wrong
# evidence to a rumor.
NARRATIVE_THRESHOLD_HASHED = float(
    os.environ.get("NARRATIVE_THRESHOLD_HASHED", "0.80")
)


def narrative_threshold() -> float:
    """Threshold for whichever embedding path is currently active."""
    override = os.environ.get("NARRATIVE_THRESHOLD")
    if override:
        return float(override)
    return (
        NARRATIVE_THRESHOLD_MANAGED if GOOGLE_API_KEY
        else NARRATIVE_THRESHOLD_HASHED
    )

# --- Review priority weights ----------------------------------------------
# Explainable 0-100 triage score = 100 * (w_reach*reach + w_harm*harm +
# w_uncertainty*uncertainty). These start as hand-set heuristics and are meant
# to be replaced by weights fitted against real analyst decisions; every score
# is stored with the WEIGHTS_VERSION that produced it so old scores stay
# attributable and a retune is auditable rather than silent.
PRIORITY_WEIGHTS = {
    "reach": float(os.environ.get("PRIORITY_WEIGHT_REACH", "0.45")),
    "potential_harm": float(os.environ.get("PRIORITY_WEIGHT_HARM", "0.35")),
    "uncertainty": float(os.environ.get("PRIORITY_WEIGHT_UNCERTAINTY", "0.20")),
}
WEIGHTS_VERSION = os.environ.get("WEIGHTS_VERSION", "heuristic-2026-07-23")

# Reach that saturates the reach component. Above this, more reach does not
# raise priority, so one mega-viral post cannot crowd out everything else.
REACH_SATURATION = int(os.environ.get("REACH_SATURATION", "10000"))

# Fraction of the review queue surfaced at random regardless of score. This is
# the only unbiased sample of analyst judgment we ever get: labels drawn from a
# score-ranked queue can only confirm the ranking that produced them. Weight
# fitting evaluates on this slice.
EXPLORATION_RATE = float(os.environ.get("EXPLORATION_RATE", "0.10"))

# --- Response layer --------------------------------------------------------
# Counter-message drafting. Drafts are never published by this system: there is
# no posting integration by design, and an approved draft is exported for a
# human to send. Generation is refused unless the narrative's evidence actually
# contradicts the claim, so the tool cannot manufacture a rebuttal it has no
# grounds for.
RESPONSE_ENABLED = os.environ.get("RESPONSE_ENABLED", "true").lower() not in {
    "0", "false", "no",
}
RESPONSE_MAX_TOKENS = int(os.environ.get("RESPONSE_MAX_TOKENS", "900"))
RESPONSE_ALLOWED_EVIDENCE_STATES = {"contradicted", "mixed"}

# Escalation rule thresholds. Versioned so a stored escalation stays
# attributable to the rules that produced it.
ESCALATION_REACH = int(os.environ.get("ESCALATION_REACH", "10000"))
ESCALATION_CONFIDENCE = float(os.environ.get("ESCALATION_CONFIDENCE", "0.8"))
ESCALATION_RULE_VERSION = os.environ.get("ESCALATION_RULE_VERSION", "2026-07-23")
