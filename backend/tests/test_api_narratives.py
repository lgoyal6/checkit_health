"""Integration tests for the narrative, export, response, and tuning surfaces.

Each test points the whole stack at a temporary SQLite file so the endpoints
exercise real persistence rather than mocks.
"""

import random
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api
import config
import ledger
import narrative_store as ns
import response as response_layer
import storage

BASE = datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)


def at(hours: float) -> str:
    return (BASE + timedelta(hours=hours)).isoformat()


def make_post(post_id, claim, likes=10, reposts=1, source="bluesky"):
    return {
        "post_id": post_id, "username": f"u{post_id}", "text": claim, "claim": claim,
        "topic": "vaccine", "confidence": 0.9, "timestamp": at(-2),
        "timestamp_processed": at(0), "status": "pending_fact_check",
        "source": source, "like_count": likes, "retweet_count": reposts,
        "review_status": "unreviewed", "evidence_state": "insufficient",
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    """An API client backed by a throwaway SQLite database."""
    db = str(tmp_path / "claims.db")
    monkeypatch.setattr(config, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(config, "DATABASE_URL", None)
    monkeypatch.setattr(config, "ANALYST_API_KEY", None)
    return TestClient(api.app), db


def seed(db, evidence_state="insufficient", lifecycle="peaking"):
    records = [
        make_post("a1", "Bleach cures autism", 900, 260),
        make_post("a2", "Bleach cures autism", 40, 10, source="reddit"),
    ]
    storage.write_sqlite(records, db)
    ledger.ingest(records, db_path=db, observed_at=at(0), exploration_rate=0.0,
                  rng=random.Random(1))
    narrative_id = ns.fetch_narratives(db_path=db)[0]["narrative_id"]

    with ns.connect(db) as (conn, _backend):
        conn.execute(
            "UPDATE claims SET evidence_state = ? WHERE narrative_id = ?",
            (evidence_state, narrative_id),
        )
        conn.execute(
            "UPDATE narratives SET lifecycle_state = ? WHERE narrative_id = ?",
            (lifecycle, narrative_id),
        )
        conn.commit()

    ns.attach_evidence(narrative_id, [{
        "id": "pubmed:1", "title": "Chlorine dioxide", "url": "https://example.org/1",
        "publisher": "PubMed", "passage": "No therapeutic benefit.",
        "published_at": "2021", "relevance_score": 0.9,
    }], db_path=db)
    return narrative_id


# --- meta ------------------------------------------------------------------


def test_meta_discloses_clustering_quality_and_that_nothing_publishes(client):
    api_client, _db = client
    body = api_client.get("/meta").json()

    assert body["clustering"]["embedding_mode"] == "hashed_fallback"
    assert body["response_drafting"]["publishes"] is False
    assert body["weights_version"]
    assert body["exploration_rate"] == config.EXPLORATION_RATE


# --- narratives ------------------------------------------------------------


def test_narratives_are_listed_as_a_rollup(client):
    api_client, db = client
    seed(db)

    rows = api_client.get("/narratives").json()
    assert len(rows) == 1
    assert rows[0]["post_count"] == 2
    assert rows[0]["reach"] == 1210


def test_narrative_detail_includes_trajectory_and_response_mode(client):
    api_client, db = client
    narrative_id = seed(db)

    body = api_client.get(f"/narratives/{narrative_id}").json()
    assert body["narrative"]["narrative_id"] == narrative_id
    assert len(body["members"]) == 2
    assert "trajectory" in body
    assert body["response_mode"] == response_layer.DEBUNK


def test_an_unknown_narrative_is_a_404(client):
    api_client, _db = client
    assert api_client.get("/narratives/nope").status_code == 404


def test_narrative_status_can_be_updated_and_is_validated(client):
    api_client, db = client
    narrative_id = seed(db)

    ok = api_client.patch(
        f"/narratives/{narrative_id}/status", json={"status": "reviewing"}
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "reviewing"

    bad = api_client.patch(
        f"/narratives/{narrative_id}/status", json={"status": "published"}
    )
    assert bad.status_code == 422


# --- reports and exports ---------------------------------------------------


def test_a_situation_report_is_available_as_json_and_html(client):
    api_client, db = client
    narrative_id = seed(db)

    payload = api_client.get(f"/narratives/{narrative_id}/report").json()
    assert payload["provenance"]["evidence_ids"] == ["pubmed:1"]

    page = api_client.get(f"/narratives/{narrative_id}/report?format=html")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "At a glance" in page.text


def test_an_unsupported_report_format_is_rejected(client):
    api_client, db = client
    narrative_id = seed(db)
    response = api_client.get(f"/narratives/{narrative_id}/report?format=pdf")
    assert response.status_code == 422


def test_narrative_csv_export_downloads_with_a_filename(client):
    api_client, db = client
    seed(db)

    response = api_client.get("/export/narratives.csv")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "checkit-narratives-" in response.headers["content-disposition"]
    assert response.text.splitlines()[0].startswith("narrative_id,label")


def test_claims_csv_export_is_empty_but_valid_without_postgres(client):
    api_client, _db = client
    response = api_client.get("/export/claims.csv")
    assert response.status_code == 200
    assert response.text.strip().startswith("narrative_id,narrative_label")


# --- response drafting -----------------------------------------------------


def test_drafting_is_refused_when_evidence_is_insufficient(client):
    """The gate that stops the tool inventing rebuttals it cannot support."""
    api_client, db = client
    narrative_id = seed(db, evidence_state="insufficient")

    response = api_client.post(f"/narratives/{narrative_id}/responses", json={})
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "insufficient_evidence"


def test_drafting_is_refused_for_a_fading_narrative(client):
    api_client, db = client
    narrative_id = seed(db, evidence_state="contradicted", lifecycle="declining")

    response = api_client.post(f"/narratives/{narrative_id}/responses", json={})
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "narrative_declining"


def test_drafting_a_response_for_an_unknown_narrative_is_a_404(client):
    api_client, _db = client
    assert api_client.post("/narratives/nope/responses", json={}).status_code == 404


def test_a_generated_draft_is_saved_as_draft_and_needs_approval(client, monkeypatch):
    api_client, db = client
    narrative_id = seed(db, evidence_state="contradicted", lifecycle="peaking")

    monkeypatch.setattr(config, "GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(api, "_genai_client", lambda: object())
    monkeypatch.setattr(
        response_layer, "generate_draft",
        lambda *_a, **_k: response_layer.ResponseDraft(
            mode=response_layer.DEBUNK,
            fact="Chlorine dioxide is not a treatment for autism.",
            myth="Bleach cures autism.",
            suggested_post="Chlorine dioxide is not a treatment. [pubmed:1]",
            citations=["pubmed:1"],
        ),
    )

    created = api_client.post(f"/narratives/{narrative_id}/responses", json={})
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "draft"
    assert body["mode"] == response_layer.DEBUNK
    assert body["citations"] == ["pubmed:1"]

    # An unapproved draft must not be exportable.
    blocked = api_client.get(f"/responses/{body['response_id']}/text")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["reason"] == "not_approved"

    approved = api_client.patch(
        f"/responses/{body['response_id']}",
        json={"status": "approved", "approved_by": "dr-lee"},
    )
    assert approved.status_code == 200
    assert approved.json()["approved_by"] == "dr-lee"

    exported = api_client.get(f"/responses/{body['response_id']}/text")
    assert exported.status_code == 200
    assert "does not publish" in exported.text
    assert "pubmed:1" in exported.text


def test_a_response_cannot_be_marked_published(client):
    api_client, db = client
    seed(db)
    response = api_client.patch(
        "/responses/whatever", json={"status": "published", "approved_by": "x"}
    )
    assert response.status_code == 422


def test_there_is_no_endpoint_that_publishes_a_response():
    """Guards the product's core boundary: the system drafts, a person sends."""
    paths = {route.path for route in api.app.routes}
    for forbidden in ("publish", "post-to", "send"):
        assert not any(forbidden in path for path in paths)


# --- review works without Postgres -----------------------------------------


def test_review_works_on_the_local_backend(client):
    """Analyst decisions are the training signal for retuning the weights, so
    a review path that only exists on Postgres collects nothing in local
    development or a demo."""
    api_client, db = client
    seed(db)

    response = api_client.patch(
        "/claims/a1/review",
        json={"status": "accepted", "note": "checked", "actor": "dr-lee"},
    )
    assert response.status_code == 200
    assert response.json()["review_status"] == "accepted"

    audit = api_client.get("/claims/a1/audit").json()
    assert audit[0]["action"] == "review:accepted"
    assert audit[0]["actor"] == "dr-lee"


def test_reviewing_an_unknown_claim_is_a_404(client):
    api_client, db = client
    seed(db)
    assert api_client.patch(
        "/claims/nope/review", json={"status": "accepted"}
    ).status_code == 404


def test_a_local_review_feeds_the_tuning_dataset(client):
    """End to end: score persisted at triage, decision recorded, pair joined."""
    api_client, db = client
    seed(db)

    api_client.patch("/claims/a1/review", json={"status": "accepted"})
    api_client.patch("/claims/a2/review", json={"status": "rejected"})

    body = api_client.get("/tuning/weights").json()
    assert body["samples"] == 2


# --- tuning ----------------------------------------------------------------


def test_tuning_reports_no_data_before_any_review(client):
    api_client, db = client
    seed(db)

    body = api_client.get("/tuning/weights").json()
    assert body["samples"] == 0
    assert body["recommendation"] == "keep"
    assert body["applies_automatically"] is False
    assert body["live_weights"] == config.PRIORITY_WEIGHTS


def test_tuning_picks_up_analyst_decisions_and_never_auto_applies(client):
    api_client, db = client
    narrative_id = seed(db)

    with ns.connect(db) as (conn, _backend):
        conn.execute(
            "UPDATE claims SET review_status = 'accepted' WHERE narrative_id = ?",
            (narrative_id,),
        )
        conn.commit()

    before = dict(config.PRIORITY_WEIGHTS)
    body = api_client.get("/tuning/weights").json()
    assert body["samples"] == 2
    assert body["applies_automatically"] is False
    assert config.PRIORITY_WEIGHTS == before


# --- review from the monitor ----------------------------------------------


def test_monitor_rows_carry_the_priority_score(monkeypatch):
    """The score was computed and thrown away before; the monitor is where an
    analyst actually needs it."""
    rows = api.add_monitor_signals([
        {"post_id": "a1", "claim": "Bleach cures autism", "like_count": 9000,
         "retweet_count": 9000, "confidence": 0.9, "timestamp": at(-2)},
        {"post_id": "a2", "claim": "Bleach cures autism", "like_count": 1,
         "retweet_count": 0, "confidence": 0.9, "timestamp": at(-2)},
    ])
    assert rows[0]["priority"]["score"] > rows[1]["priority"]["score"]
    assert rows[0]["priority"]["weights_version"]
    assert set(rows[0]["priority"]["components"]) == {
        "reach", "potential_harm", "uncertainty"
    }
