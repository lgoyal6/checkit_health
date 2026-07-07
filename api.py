"""FastAPI wrapper around the Checkit Health pipeline.

Exposes the existing classifier over HTTP without touching run.py or the CLI.

Run locally:
    uvicorn api:app --reload

Endpoints:
    GET  /health   -> {"status": "ok"}
    POST /check    -> classify one claim, with optional fact-check lookup
    GET  /history  -> last 50 stored claims, newest first
"""

import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import config
import storage
from classifier import _classify_one, is_falsifiable
from fact_checker import check_claim

# Rate-limit the LLM-backed endpoint so a bot can't burn the Gemini quota.
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Checkit Health API", version="1.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Open CORS so the Vercel-hosted frontend (any origin) can call the API. Tighten
# to the deployed frontend origin if this ever handles anything sensitive.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CheckRequest(BaseModel):
    text: str


class CheckResponse(BaseModel):
    label: str
    claim: Optional[str] = None
    topic: Optional[str] = None
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    falsifiable: Optional[bool] = None
    fact_check_verdict: Optional[str] = None
    fact_check_source: Optional[str] = None
    fact_check_url: Optional[str] = None
    error: Optional[str] = None


def _genai_client() -> Any:
    from google import genai
    return genai.Client(api_key=config.GOOGLE_API_KEY)


@app.get("/")
def root() -> Dict[str, Any]:
    """Friendly landing payload so the bare URL isn't a bare 404."""
    return {
        "service": "Checkit Health API",
        "docs": "/docs",
        "endpoints": {
            "GET /health": "liveness check",
            "POST /check": 'classify a claim: {"text": "..."}',
            "GET /history": "last 50 stored claims",
        },
    }


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/check", response_model=CheckResponse)
@limiter.limit("20/minute")
def check(request: Request, req: CheckRequest) -> CheckResponse:
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text must not be empty")
    if not config.GOOGLE_API_KEY:
        raise HTTPException(status_code=503, detail="GOOGLE_API_KEY not configured")

    client = _genai_client()
    # Interactive request: fail fast (at most 2 tries, 2s backoff) so a throttled
    # Gemini returns a quick "try again" instead of hanging on the user.
    classification = _classify_one(client, text, max_attempts=2, initial_backoff=2)

    # A classifier error (e.g. Gemini briefly overloaded) comes back as a NOISE
    # fallback with an `error` set. Surface it as a real error so the UI can say
    # "try again" instead of silently showing a wrong "Not a health claim".
    if classification.get("error"):
        raise HTTPException(
            status_code=503,
            detail="The AI model is busy right now — please try again in a moment.",
        )

    resp = CheckResponse(**classification)

    # Only run the extra passes for real medical claims that clear the gate.
    is_claim = (
        classification["label"] == "MEDICAL_CLAIM"
        and classification.get("confidence") is not None
        and classification["confidence"] >= config.CONFIDENCE_THRESHOLD
    )
    if is_claim:
        claim_text = classification["claim"] or text
        resp.falsifiable = is_falsifiable(claim_text, client=client)
        fc = check_claim(claim_text)
        if fc:
            resp.fact_check_verdict = fc.get("verdict")
            resp.fact_check_source = fc.get("publisher")
            resp.fact_check_url = fc.get("url")
        _persist_check(text, resp)
    return resp


def _persist_check(original_text: str, resp: CheckResponse) -> None:
    """Save a live web check to Postgres so it shows up in /history.

    Best-effort: a storage hiccup must never break the user's check response.
    No-op when DATABASE_URL isn't configured (e.g. local SQLite-only dev).
    """
    if not storage.postgres_enabled():
        return
    # Deterministic id from the claim so re-checking the same thing upserts one
    # row instead of piling up duplicates in History.
    claim_text = resp.claim or original_text
    claim_key = hashlib.sha1(claim_text.strip().lower().encode()).hexdigest()[:16]
    record = {
        "post_id": f"web-{claim_key}",
        "username": "web",
        "text": original_text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "retweet_count": 0,
        "like_count": 0,
        "claim": resp.claim or original_text,
        "topic": resp.topic,
        "confidence": resp.confidence,
        "timestamp_processed": datetime.now(timezone.utc).isoformat(),
        "status": "verified" if resp.fact_check_verdict else "unverified",
        "fact_check_verdict": resp.fact_check_verdict,
        "fact_check_source": resp.fact_check_source,
        "fact_check_url": resp.fact_check_url,
        "source": "web",
    }
    try:
        storage.write_postgres([record])
    except Exception as e:  # pragma: no cover - defensive, logged only
        print(f"[warn] failed to persist web check: {e}")


@app.get("/history")
def history() -> List[Dict[str, Any]]:
    """Return the most recent 50 stored claims, newest first.

    Reads Postgres when DATABASE_URL is set (the deployed path), otherwise
    falls back to the local SQLite file for offline dev.
    """
    if storage.postgres_enabled():
        try:
            return storage.fetch_recent(50)
        except Exception as e:
            print(f"[warn] postgres history read failed: {e}")
            return []

    db_path = config.DEFAULT_DB_PATH
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        cur = conn.execute(
            """
            SELECT post_id, username, text, timestamp, claim, topic, confidence,
                   timestamp_processed, status,
                   fact_check_verdict, fact_check_source, fact_check_url
            FROM claims
            ORDER BY timestamp_processed DESC
            LIMIT 50
            """
        )
        return [dict(row) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        # Table doesn't exist yet (pipeline never run).
        return []
    finally:
        conn.close()
