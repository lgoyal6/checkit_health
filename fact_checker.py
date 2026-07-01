"""Stage 2.75: existing fact-check lookup via the Google Fact Check Tools API.

Searches Google's ClaimReview index (https://toolbox.google.com/factcheck/apis)
for a published fact check matching a claim. This is a free API; get a key at
https://console.cloud.google.com/ by enabling "Fact Check Tools API" and set it
as GOOGLE_FACT_CHECK_KEY.

If no key is configured or no match is found, `check_claim` returns None and the
pipeline marks the record `unverified`.
"""

import os
from typing import Any, Dict, Optional

import requests

API_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
FACT_CHECK_KEY = os.environ.get("GOOGLE_FACT_CHECK_KEY")
TIMEOUT_SECONDS = 10


def check_claim(claim: str, api_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Look up an existing fact check for `claim`.

    Returns a dict with keys `publisher`, `verdict`, and `url` if a published
    fact check is found, otherwise None. Never raises — network/parse failures
    return None so the pipeline keeps moving.
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
    if not claims:
        return None

    reviews = claims[0].get("claimReview") or []
    if not reviews:
        return None

    review = reviews[0]
    publisher = (review.get("publisher") or {}).get("name")
    return {
        "publisher": publisher,
        "verdict": review.get("textualRating"),
        "url": review.get("url"),
    }
