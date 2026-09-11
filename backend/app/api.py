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
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import config
import ledger
import narrative_store as ns
import reporting
import response as response_layer
import storage
import tuning
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


class NarrativeStatusRequest(BaseModel):
    status: str


class ResponseRequest(BaseModel):
    """Ask for a counter-message draft. Mode is derived, not chosen.

    The caller cannot force a debunk on a narrative that is still emerging:
    restating a rumor to an audience that has not seen it spreads it, so the
    lifecycle state decides the mode.
    """
    author: str = "analyst"


class ResponseDecisionRequest(BaseModel):
    status: str
    approved_by: str = "analyst"
    note: str = ""


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


@app.get("/health/vector")
def health_vector() -> Dict[str, Any]:
    if not storage.postgres_enabled():
        return {"postgres_configured": False, "ok": False}
    try:
        return storage.vector_health()
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
        # A pasted statement has no social reach of its own, so the reach
        # component is genuinely zero here. Reach-weighted scoring happens on
        # the monitor path, where the number is real; see ledger.score_records.
        resp.assessment = assess_claim(
            claim_text,
            resp.confidence,
            resp.fact_check_source,
            resp.fact_check_url,
            reach=0,
            narrative=_narrative_hint(claim_text),
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


def _narrative_hint(claim_text: str) -> Dict[str, Any]:
    """Which tracked narrative this pasted claim looks like, if any.

    Read-only lookup so the Check page can say "this is the 5th post carrying
    this rumor" instead of showing an opaque cluster hash. Failure is silent:
    a missing narrative link must never break a classification.
    """
    try:
        import narratives as nar_mod
        vector = nar_mod.embed([claim_text])[0]
        with ns.connect() as (conn, backend):
            candidates = ns._load_candidates(conn, backend)
            canonical = {
                row["narrative_id"]: row["canonical_claim"]
                for row in ns.fetch_all_canonical(conn, backend)
            }
        decision = nar_mod.assign(vector, candidates)
        if decision.is_new or not decision.narrative_id:
            return {}
        narrative = ns.fetch_narrative(decision.narrative_id) or {}
        return {
            "narrative_id": decision.narrative_id,
            "label": narrative.get("label") or canonical.get(decision.narrative_id),
            "member_count": narrative.get("member_count"),
            "lifecycle_state": narrative.get("lifecycle_state"),
            "similarity": round(decision.similarity, 4),
        }
    except Exception:
        return {}


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
        return
    # Fold it into the narrative ledger: a pasted statement is usually a
    # variant of something already circulating, and an analyst should see it
    # against that history rather than as an isolated row.
    try:
        ledger.ingest([record])
    except Exception as e:  # pragma: no cover - defensive, logged only
        print(f"[warn] failed to add web check to the narrative ledger: {e}")


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
    result = storage.update_review(post_id, req.status, req.note[:2000], req.actor[:120])
    if not result:
        raise HTTPException(status_code=404, detail="Claim not found")
    return result


@app.get("/claims/{post_id}/audit")
def claim_audit(post_id: str, request: Request) -> List[Dict[str, Any]]:
    _authorize_analyst(request)
    return storage.fetch_audit(post_id)


# --- narrative ledger ------------------------------------------------------
#
# The narrative, not the post, is the unit an analyst tracks. These endpoints
# expose the ledger: what rumors are circulating, how each is growing, what
# evidence is attached, what a human decided, and what response was drafted.


@app.get("/meta")
def meta() -> Dict[str, Any]:
    """Versions and capability disclosure, so the UI can tell the truth.

    Clustering quality in particular: with no managed embedding configured the
    grouping only merges near-identical wording, and the interface should say
    that rather than implying semantic narrative detection.
    """
    return {
        "model": config.MODEL_NAME,
        "weights_version": config.WEIGHTS_VERSION,
        "priority_weights": config.PRIORITY_WEIGHTS,
        "escalation_rule_version": config.ESCALATION_RULE_VERSION,
        "exploration_rate": config.EXPLORATION_RATE,
        "clustering": ns.clustering_quality(),
        "response_drafting": {
            "enabled": config.RESPONSE_ENABLED,
            "allowed_evidence_states": sorted(config.RESPONSE_ALLOWED_EVIDENCE_STATES),
            "publishes": False,
            "protocols": response_layer.PROTOCOLS,
        },
        "report_version": reporting.REPORT_VERSION,
    }


@app.get("/narratives")
def list_narratives(
    limit: int = 100,
    topic: Optional[str] = None,
    lifecycle_state: Optional[str] = None,
) -> List[Dict[str, Any]]:
    try:
        return ns.fetch_narratives(
            limit=max(1, min(limit, 500)),
            topic=topic or None,
            lifecycle_state=lifecycle_state or None,
        )
    except Exception as e:
        print(f"[warn] narrative list failed: {e}")
        return []


@app.get("/narratives/{narrative_id}")
def get_narrative(narrative_id: str) -> Dict[str, Any]:
    narrative = ns.fetch_narrative(narrative_id)
    if not narrative:
        raise HTTPException(status_code=404, detail="Narrative not found")
    snapshots = ns.fetch_snapshots(narrative_id)
    import narratives as nar_mod
    return {
        "narrative": narrative,
        "members": ns.fetch_narrative_members(narrative_id),
        "evidence": ns.fetch_narrative_evidence(narrative_id),
        "responses": ns.fetch_responses(narrative_id=narrative_id),
        "trajectory": nar_mod.trajectory(snapshots).as_dict(),
        "snapshots": snapshots,
        "response_mode": response_layer.mode_for_lifecycle(
            narrative.get("lifecycle_state")
        ),
    }


@app.patch("/narratives/{narrative_id}/status")
def update_narrative_status(
    narrative_id: str, req: NarrativeStatusRequest, request: Request
) -> Dict[str, Any]:
    _authorize_analyst(request)
    allowed = {"watching", "reviewing", "responded", "archived", "resolved"}
    if req.status not in allowed:
        raise HTTPException(
            status_code=422, detail=f"status must be one of: {', '.join(sorted(allowed))}"
        )
    if not ns.set_narrative_status(narrative_id, req.status):
        raise HTTPException(status_code=404, detail="Narrative not found")
    return {"narrative_id": narrative_id, "status": req.status}


@app.get("/narratives/{narrative_id}/report")
def narrative_report(narrative_id: str, format: str = "json"):
    """Situation report for one narrative, assembled from stored rows only.

    ``format=html`` returns a printable page; the browser's own print-to-PDF is
    the PDF path, which keeps a rendering dependency out of the deployment.
    """
    report = reporting.build_situation_report(narrative_id)
    if not report:
        raise HTTPException(status_code=404, detail="Narrative not found")
    if format == "html":
        return HTMLResponse(reporting.render_html(report))
    if format == "json":
        return report
    raise HTTPException(status_code=422, detail="format must be json or html")


# --- exports ---------------------------------------------------------------


def _csv_response(body: str, filename: str) -> Response:
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/claims.csv")
def export_claims(
    window: str = "7d",
    topic: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 500,
) -> Response:
    """The analyst's working export: the rows the monitor is showing, as CSV.

    Same filters as /monitor on purpose, so what you export is what you see.
    """
    rows: List[Dict[str, Any]] = []
    if storage.postgres_enabled():
        try:
            rows = storage.fetch_trending(
                window=window, topic=topic or None, source=source or None,
                limit=max(1, min(limit, 2000)),
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        except Exception as e:
            print(f"[warn] export read failed: {e}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return _csv_response(reporting.claims_csv(rows), f"checkit-claims-{stamp}.csv")


@app.get("/export/narratives.csv")
def export_narratives(limit: int = 500) -> Response:
    try:
        rows = ns.fetch_narratives(limit=max(1, min(limit, 2000)))
    except Exception as e:
        print(f"[warn] narrative export failed: {e}")
        rows = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return _csv_response(
        reporting.narratives_csv(rows), f"checkit-narratives-{stamp}.csv"
    )


# --- response drafting -----------------------------------------------------
#
# Drafts only. There is no endpoint here that posts, schedules, or authenticates
# to any platform, and there deliberately never will be: the moment this system
# can publish, it stops being decision support.


@app.post("/narratives/{narrative_id}/responses", status_code=201)
@limiter.limit("10/minute")
def draft_response(
    narrative_id: str, req: ResponseRequest, request: Request
) -> Dict[str, Any]:
    _authorize_analyst(request)
    narrative = ns.fetch_narrative(narrative_id)
    if not narrative:
        raise HTTPException(status_code=404, detail="Narrative not found")

    report = reporting.build_situation_report(narrative_id)
    evidence = report["evidence"]
    evidence_state = report["summary"]["evidence_state"]

    try:
        mode = response_layer.check_preconditions(
            evidence_state, evidence, narrative.get("lifecycle_state")
        )
    except response_layer.ResponseRefusal as refusal:
        # 409, not 400: the request is well-formed, the narrative is simply not
        # in a state where drafting a response is defensible.
        raise HTTPException(
            status_code=409,
            detail={"reason": refusal.reason, "message": refusal.detail},
        ) from refusal

    if not config.GOOGLE_API_KEY:
        raise HTTPException(status_code=503, detail="GOOGLE_API_KEY not configured")

    try:
        draft = response_layer.generate_draft(
            _genai_client(), narrative["canonical_claim"], evidence, mode
        )
    except response_layer.ResponseRefusal as refusal:
        raise HTTPException(
            status_code=409,
            detail={"reason": refusal.reason, "message": refusal.detail},
        ) from refusal
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail="The AI model is busy right now — please try again in a moment.",
        ) from e

    saved = ns.save_response(
        narrative_id=narrative_id,
        mode=mode,
        protocol=response_layer.PROTOCOLS[mode],
        draft=draft.model_dump(),
        citations=draft.citations,
        evidence_state=evidence_state,
        author=req.author,
    )
    storage_ok = True
    try:
        storage.record_audit(narrative_id, f"response:drafted:{mode}", req.author, "")
    except Exception:
        storage_ok = False
    saved["audit_recorded"] = storage_ok
    return saved


@app.get("/narratives/{narrative_id}/responses")
def list_responses(narrative_id: str) -> List[Dict[str, Any]]:
    return ns.fetch_responses(narrative_id=narrative_id)


@app.patch("/responses/{response_id}")
def decide_response(
    response_id: str, req: ResponseDecisionRequest, request: Request
) -> Dict[str, Any]:
    """Approve or reject a draft. Only approved drafts can be exported."""
    _authorize_analyst(request)
    if req.status not in {"approved", "rejected"}:
        raise HTTPException(
            status_code=422, detail="status must be approved or rejected"
        )
    result = ns.decide_response(
        response_id, req.status, req.approved_by, req.note
    )
    if not result:
        raise HTTPException(status_code=404, detail="Response not found")
    try:
        storage.record_audit(
            result["narrative_id"], f"response:{req.status}", req.approved_by, req.note
        )
    except Exception:
        pass
    return result


@app.get("/responses/{response_id}/text")
def response_text(response_id: str, request: Request) -> PlainTextResponse:
    """Export an approved draft as plain text for a human to send.

    Gated on approval: an unapproved draft is not an artifact anyone should be
    able to copy out of the tool by guessing a URL.
    """
    _authorize_analyst(request)
    saved = ns.fetch_response(response_id)
    if not saved:
        raise HTTPException(status_code=404, detail="Response not found")
    if saved.get("status") != "approved":
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "not_approved",
                "message": "Only an approved draft can be exported. Have a "
                           "reviewer approve it first.",
            },
        )
    narrative = ns.fetch_narrative(saved["narrative_id"]) or {}
    return PlainTextResponse(
        response_layer.render_plain_text(saved["draft"], narrative.get("label", ""))
    )


# --- feedback loop ---------------------------------------------------------


@app.get("/tuning/weights")
def tuning_weights(k: int = 20, request: Request = None) -> Dict[str, Any]:
    """Compare the weights in force against ones fitted to analyst decisions.

    Read-only and advisory: this never changes the live weights. Shipping a
    candidate is a deliberate deploy of WEIGHTS_VERSION, so a retune is always
    something a person did, not something that happened.
    """
    try:
        rows = ns.fetch_scored_outcomes()
    except Exception as e:
        print(f"[warn] tuning read failed: {e}")
        rows = []
    result = tuning.fit(rows, k=max(1, min(k, 200)))
    payload = result.as_dict()
    payload["live_weights"] = config.PRIORITY_WEIGHTS
    payload["live_weights_version"] = config.WEIGHTS_VERSION
    payload["applies_automatically"] = False
    return payload
