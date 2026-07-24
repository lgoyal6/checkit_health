"""FastAPI wrapper around the Checkit Health pipeline.

Exposes the existing classifier over HTTP without touching run.py or the CLI.

Run locally:
    uvicorn api:app --reload

Endpoints:
    GET  /health   -> {"status": "ok"}
    POST /check    -> classify one claim, with optional fact-check lookup
    GET  /history  -> last 50 stored claims, newest first
    GET  /monitor  -> viral monitored claims ranked by reach
    GET  /stats    -> aggregate monitor KPIs
"""

import hashlib
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import config
import storage
from classifier import _classify_one, is_falsifiable
from claim_report import generate_claim_report, ClaimReportResponse
from fact_checker import check_claim
from evidence import retrieve_evidence
from intelligence import adverse_event_routing, add_monitor_signals, assess_claim

# Rate-limit the LLM-backed endpoint so a bot can't burn the Gemini quota.
limiter = Limiter(key_func=get_remote_address)
job_pool = ThreadPoolExecutor(max_workers=config.BACKGROUND_WORKERS)
jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.Lock()

app = FastAPI(title="Checkit Health Monitor API", version="1.0.0")
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


class ReportRequest(BaseModel):
    claim: str
    topic: Optional[str] = None
    fact_check_verdict: Optional[str] = None
    fact_check_source: Optional[str] = None
    evidence: List[Dict[str, Any]] = Field(default_factory=list)


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
    assessment: Optional[Dict[str, Any]] = None
    retrieval: Optional[Dict[str, Any]] = None
    evidence_state: Optional[str] = None
    safety: Optional[Dict[str, Any]] = None


class ReviewRequest(BaseModel):
    status: str
    note: str = ""
    actor: str = "analyst"


def _genai_client() -> Any:
    from google import genai
    from google.genai import types
    return genai.Client(
        api_key=config.GOOGLE_API_KEY,
        http_options=types.HttpOptions(timeout=config.GEMINI_TIMEOUT_MS),
    )


@app.get("/")
def root() -> Dict[str, Any]:
    """Friendly landing payload so the bare URL isn't a bare 404."""
    return {
        "service": "Checkit Health API",
        "docs": "/docs",
        "endpoints": {
            "GET /health": "liveness check",
            "POST /check": 'classify a claim: {"text": "..."}',
            "POST /report": "structured evidence report for a claim",
            "GET /history": "last 50 stored claims",
            "GET /monitor": "viral monitored claims ranked by reach",
            "GET /stats": "monitor KPI aggregates",
        },
    }


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db")
def health_db() -> Dict[str, Any]:
    """Non-secret Postgres health check for deployment debugging."""
    if not storage.postgres_enabled():
        return {"postgres_configured": False, "ok": False}
    try:
        import psycopg
        with psycopg.connect(config.DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM claims")
                count = cur.fetchone()[0]
        return {"postgres_configured": True, "ok": True, "claims_count": count}
    except Exception as e:
        return {
            "postgres_configured": True,
            "ok": False,
            "error": type(e).__name__,
            "detail": str(e)[:300],
        }


@app.post("/check", response_model=CheckResponse)
@limiter.limit("20/minute")
def check(request: Request, req: CheckRequest) -> CheckResponse:
    return _run_check(req.text)


def _run_check(submitted_text: str) -> CheckResponse:
    text = (submitted_text or "").strip()
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
    resp.safety = adverse_event_routing(text)

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
        resp.retrieval = retrieve_evidence(claim_text, fact_check=fc)
        resp.evidence_state = (
            "retrieved" if resp.retrieval.get("evidence") else "insufficient"
        )
        resp.assessment = assess_claim(
            claim_text,
            resp.confidence,
            resp.fact_check_source,
            resp.fact_check_url,
        )
        _persist_check(text, resp)
    return resp


def _execute_check_job(job_id: str, text: str) -> None:
    with jobs_lock:
        jobs[job_id] = {"id": job_id, "status": "running"}
    try:
        result = _run_check(text)
        payload = {"id": job_id, "status": "completed", "result": result.model_dump()}
    except HTTPException as exc:
        payload = {
            "id": job_id, "status": "failed",
            "error": str(exc.detail), "status_code": exc.status_code,
        }
    except Exception:
        payload = {"id": job_id, "status": "failed", "error": "processing_error"}
    with jobs_lock:
        jobs[job_id] = payload


@app.post("/jobs/check", status_code=202)
@limiter.limit("30/minute")
def enqueue_check(request: Request, req: CheckRequest) -> Dict[str, Any]:
    if not (req.text or "").strip():
        raise HTTPException(status_code=422, detail="text must not be empty")
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"id": job_id, "status": "queued"}
    job_pool.submit(_execute_check_job, job_id, req.text)
    return {"id": job_id, "status": "queued", "status_url": f"/jobs/{job_id}"}


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> Dict[str, Any]:
    with jobs_lock:
        result = jobs.get(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


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
        "classification_reasoning": resp.reasoning,
        "review_status": "unreviewed",
        "evidence_json": (
            __import__("json").dumps(resp.retrieval.get("evidence", []))
            if resp.retrieval else "[]"
        ),
        "evidence_state": resp.evidence_state or "insufficient",
    }
    try:
        storage.write_postgres([record])
    except Exception as e:  # pragma: no cover - defensive, logged only
        print(f"[warn] failed to persist web check: {e}")


@app.post("/report", response_model=ClaimReportResponse)
@limiter.limit("20/minute")
def report(request: Request, req: ReportRequest) -> ClaimReportResponse:
    """Generate the structured Rumor/Confidence/Summary/Key Facts/Analysis/
    Conclusion evidence report for one claim.

    This is a separate, heavier call from /check on purpose — it's only
    fetched when a user explicitly asks to see the full write-up for a claim
    that already passed triage, not on every classification.
    """
    claim_text = (req.claim or "").strip()
    if not claim_text:
        raise HTTPException(status_code=422, detail="claim must not be empty")
    if not config.GOOGLE_API_KEY:
        raise HTTPException(status_code=503, detail="GOOGLE_API_KEY not configured")

    client = _genai_client()
    try:
        # Fail fast, same as /check: at most 2 tries, 2s backoff, so a
        # throttled Gemini returns a quick "try again" instead of hanging.
        return generate_claim_report(
            client,
            claim_text=claim_text,
            topic=req.topic,
            fact_check_verdict=req.fact_check_verdict,
            fact_check_source=req.fact_check_source,
            evidence=req.evidence,
            max_attempts=2,
            initial_backoff=2,
        )
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail="The AI model is busy right now — please try again in a moment.",
        ) from e


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


@app.get("/monitor")
def monitor(
    window: str = "7d",
    topic: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return monitored social claims ranked by reach.

    This is Postgres-only by design; manual web checks and local SQLite dev data
    are excluded from the monitor surface.
    """
    if not storage.postgres_enabled():
        return []
    try:
        rows = storage.fetch_trending(
            window=window,
            topic=topic or None,
            source=source or None,
            limit=max(1, min(limit, 200)),
        )
        return add_monitor_signals(rows)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:
        print(f"[warn] postgres monitor read failed: {e}")
        return []


@app.get("/stats")
def stats(window: str = "7d") -> Dict[str, Any]:
    """Return aggregate KPIs for the monitoring dashboard."""
    if not storage.postgres_enabled():
        return {}
    try:
        return storage.fetch_stats(window)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:
        print(f"[warn] postgres stats read failed: {e}")
        return {}


def _authorize_analyst(request: Request) -> None:
    if config.ANALYST_API_KEY and request.headers.get("x-analyst-key") != config.ANALYST_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid analyst credentials")


@app.patch("/claims/{post_id}/review")
def review_claim(post_id: str, req: ReviewRequest, request: Request) -> Dict[str, Any]:
    _authorize_analyst(request)
    if req.status not in {"unreviewed", "in_review", "accepted", "rejected", "needs_evidence"}:
        raise HTTPException(status_code=422, detail="Invalid review status")
    if not storage.postgres_enabled():
        raise HTTPException(status_code=503, detail="Review workflow requires Postgres")
    result = storage.update_review(post_id, req.status, req.note[:2000], req.actor[:120])
    if not result:
        raise HTTPException(status_code=404, detail="Claim not found")
    return result


@app.get("/claims/{post_id}/audit")
def claim_audit(post_id: str, request: Request) -> List[Dict[str, Any]]:
    _authorize_analyst(request)
    if not storage.postgres_enabled():
        return []
    return storage.fetch_audit(post_id)
