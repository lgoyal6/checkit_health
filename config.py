"""Tunable parameters for the Checkit Health pipeline."""

import os

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

MODEL_NAME = "claude-haiku-3-5"

CONFIDENCE_THRESHOLD = 0.7

BATCH_SIZE = 5

MAX_TOKENS = 512

DEFAULT_INPUT_PATH = "data/posts.json"
DEFAULT_OUTPUT_PATH = "output/claims.json"
DEFAULT_DB_PATH = "db/claims.db"

VALID_LABELS = {"MEDICAL_CLAIM", "GENERAL_HEALTH", "NOISE"}
VALID_TOPICS = {"vaccine", "drug", "treatment", "nutrition", "other"}
