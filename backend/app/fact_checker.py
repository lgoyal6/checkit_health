"""Stage 2.75: existing fact-check lookup via the Google Fact Check Tools API.

Searches Google's ClaimReview index (https://toolbox.google.com/factcheck/apis)
for a published fact check matching a claim. This is a free API; get a key at
https://console.cloud.google.com/ by enabling "Fact Check Tools API" and set it
as GOOGLE_FACT_CHECK_KEY.

If no key is configured or no match is found, `check_claim` returns None and the
pipeline marks the record `unverified`.
"""

# at the top of fact_checker.py
from rate_limiter import fact_check_limiter


import os
import re
from typing import Any, Dict, Optional

import requests

API_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
FACT_CHECK_KEY = os.environ.get("GOOGLE_FACT_CHECK_KEY")
TIMEOUT_SECONDS = 10

# Minimum fraction of our claim's meaningful words that must appear in the
# fact-checked claim for us to trust the match. Google matches loosely on
# keywords, so without this a true claim can pick up an unrelated "False"
# rating. 0.5 keeps real matches while dropping tangential ones.
MATCH_THRESHOLD = 0.5

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being", "of",
    "to", "in", "on", "for", "and", "or", "that", "this", "it", "as", "by",
    "with", "at", "from", "do", "does", "did", "can", "will", "your", "you",
}


def _keywords(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _overlap(claim: str, candidate: str) -> float:
    """Fraction of the claim's keywords also present in the candidate text."""
    a = _keywords(claim)
    if not a:
        return 0.0
    b = _keywords(candidate)
    return len(a & b) / len(a)

# immediately before the Google Fact Check Tools HTTP call in check_claim()
fact_check_limiter.acquire()
response = requests.get(FACT_CHECK_KEY, params=params, timeout=...)

def check_claim(claim: str, api_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Look up an existing fact check for `claim`.

    Returns a dict with keys `publisher`, `verdict`, and `url` for the best
    matching published fact check, or None if nothing matches closely enough.
    Never raises — network/parse failures return None so the pipeline keeps
    moving.
    """
    key = api_key or FACT_CHECK_KEY
    if not key or not claim or not claim.strip():
        return None

    try:
        resp = requests.get(
            API_URL,
            params={"query": claim, "key": key, "languageCode": "en"},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    claims = data.get("claims") or []

    # Pick the candidate whose fact-checked text overlaps our claim the most,
    # and only accept it if it clears the relevance threshold.
    best = None
    best_score = 0.0
    for c in claims:
        score = _overlap(claim, c.get("text", ""))
        if score > best_score and (c.get("claimReview") or []):
            best, best_score = c, score

    if best is None or best_score < MATCH_THRESHOLD:
        return None

    review = best["claimReview"][0]
    publisher = (review.get("publisher") or {}).get("name")
    return {
        "publisher": publisher,
        "verdict": review.get("textualRating"),
        "url": review.get("url"),
    }
