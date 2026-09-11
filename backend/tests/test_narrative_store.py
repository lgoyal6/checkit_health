import random
from datetime import datetime, timedelta, timezone

import pytest

import ledger
import narrative_store as ns
import storage

BASE = datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)


def at(hours: float) -> str:
    return (BASE + timedelta(hours=hours)).isoformat()


def make_post(post_id, claim, likes=10, reposts=1, source="bluesky", topic="vaccine"):
    return {
        "post_id": post_id, "username": f"u{post_id}", "text": claim, "claim": claim,
        "topic": topic, "confidence": 0.9, "timestamp": at(-2),
        "timestamp_processed": at(0), "status": "pending_fact_check",
        "source": source, "like_count": likes, "retweet_count": reposts,
        "review_status": "unreviewed", "evidence_state": "insufficient",
    }


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "claims.db")


def ingest(records, db_path, observed_at=None, rate=0.0, seed=1):
    storage.write_sqlite(records, db_path)
    return ledger.ingest(
        records, db_path=db_path, observed_at=observed_at or at(0),
        exploration_rate=rate, rng=random.Random(seed),
    )


# --- assignment ------------------------------------------------------------


def test_paraphrases_join_one_narrative_and_distinct_claims_do_not(db):
    records = [
        make_post("a1", "Bleach cures autism", 100, 20),
        make_post("a2", "Drinking bleach cures autism", 40, 10, source="reddit"),
        make_post("b1", "Fluoride lowers IQ in children", 60, 5),
    ]
    storage.write_sqlite(records, db)
    ns.assign_narratives(records, db_path=db, threshold=0.6, adjudicate=False)

    ids = {r["post_id"]: r["narrative_id"] for r in records}
    assert ids["a1"] == ids["a2"]
    assert ids["b1"] != ids["a1"]


def test_assignment_is_idempotent_across_runs(db):
    first = [make_post("a1", "Bleach cures autism")]
    storage.write_sqlite(first, db)
    ns.assign_narratives(first, db_path=db, threshold=0.6, adjudicate=False)

    second = [make_post("a2", "Drinking bleach cures autism")]
    storage.write_sqlite(second, db)
    ns.assign_narratives(second, db_path=db, threshold=0.6, adjudicate=False)

    assert first[0]["narrative_id"] == second[0]["narrative_id"]
    assert len(ns.fetch_narratives(db_path=db)) == 1


def test_narrative_id_is_written_back_onto_the_claims_row(db):
    records = [make_post("a1", "Bleach cures autism")]
    storage.write_sqlite(records, db)
    ns.assign_narratives(records, db_path=db, adjudicate=False)
    ns.persist_claim_narratives(records, db_path=db)

    members = ns.fetch_narrative_members(records[0]["narrative_id"], db_path=db)
    assert [m["post_id"] for m in members] == ["a1"]


def test_empty_batch_is_a_no_op(db):
    assert ns.assign_narratives([], db_path=db) == []
    assert ledger.ingest([], db_path=db)["claims"] == 0


def test_clustering_quality_discloses_the_offline_fallback():
    quality = ns.clustering_quality()
    assert quality["embedding_mode"] == "hashed_fallback"
    assert quality["semantic"] is False
    assert "GOOGLE_API_KEY" in quality["note"]


# --- snapshots -------------------------------------------------------------


def test_snapshots_survive_the_upsert_that_overwrites_engagement(db):
    """The claims table upserts on post_id, so re-ingesting a post destroys its
    previous like/repost counts. Growth is only measurable because snapshots
    are appended instead of updated."""
    ingest([make_post("a1", "Bleach cures autism", 100, 20)], db, at(0))
    ingest([make_post("a1", "Bleach cures autism", 900, 260)], db, at(6))

    narrative_id = ns.fetch_narratives(db_path=db)[0]["narrative_id"]
    snapshots = ns.fetch_snapshots(narrative_id, db_path=db)
    assert len(snapshots) == 2
    assert sorted(s["like_count"] for s in snapshots) == [100, 900]


def test_snapshots_do_not_duplicate_within_one_observation(db):
    record = make_post("a1", "Bleach cures autism")
    ingest([record], db, at(0))
    ns.record_snapshots([record], observed_at=at(0), db_path=db)

    narrative_id = record["narrative_id"]
    assert len(ns.fetch_snapshots(narrative_id, db_path=db)) == 1


def test_growth_is_derived_from_snapshots(db):
    ingest([make_post("a1", "Bleach cures autism", 10, 0)], db, at(0))
    ingest([make_post("a1", "Bleach cures autism", 60, 0)], db, at(1))
    ingest([make_post("a1", "Bleach cures autism", 400, 0)], db, at(2))

    narrative_id = ns.fetch_narratives(db_path=db)[0]["narrative_id"]
    result = ns.refresh_lifecycle(
        narrative_id, now=datetime.fromisoformat(at(2)), db_path=db
    )
    assert result["trajectory"]["growth_per_hour"] == 340.0


def test_a_young_spiking_narrative_is_emerging_so_prebunking_is_offered(db):
    """Emerging is the state where a rumor can still be pre-bunked, so a fresh
    spike must land there rather than in the debunk-only accelerating state."""
    post = make_post("a1", "Bleach cures autism", 10, 0)
    ingest([post], db, at(0))
    ingest([make_post("a1", "Bleach cures autism", 400, 0)], db, at(2))

    narrative_id = ns.fetch_narratives(db_path=db)[0]["narrative_id"]
    result = ns.refresh_lifecycle(
        narrative_id, now=datetime.fromisoformat(at(2)), db_path=db
    )
    assert result["lifecycle_state"] == "emerging"


def test_an_older_spiking_narrative_is_accelerating(db):
    """Past the emerging window the same growth means a debunk, not a
    pre-bunk: the audience has already seen the claim."""
    old = make_post("a1", "Bleach cures autism", 10, 0)
    old["timestamp"] = at(-200)
    ingest([old], db, at(0))
    ingest([old | {"like_count": 60}], db, at(1))
    ingest([old | {"like_count": 400}], db, at(2))

    narrative_id = ns.fetch_narratives(db_path=db)[0]["narrative_id"]
    result = ns.refresh_lifecycle(
        narrative_id, now=datetime.fromisoformat(at(2)), db_path=db
    )
    assert result["lifecycle_state"] == "accelerating"


def test_member_count_matches_real_posts_after_reingestion(db):
    """The counter used to blend centroids bumps on every assignment, so
    re-ingesting the same posts must not inflate the reported member count."""
    batch = [
        make_post("a1", "Bleach cures autism"),
        make_post("a2", "Bleach cures autism"),
    ]
    ingest(batch, db, at(0))
    ingest(batch, db, at(6))
    ingest(batch, db, at(12))

    (row,) = ns.fetch_narratives(db_path=db)
    assert row["post_count"] == 2
    assert row["member_count"] == 2


# --- scores ----------------------------------------------------------------


def test_the_triage_score_is_persisted_with_its_weights_version(db):
    ingest([make_post("a1", "Bleach cures autism", 5000, 5000)], db)

    rows = ns.fetch_scored_outcomes(db_path=db)
    assert rows == []  # nothing reviewed yet, so nothing to learn from

    storage.write_sqlite([], db)
    with ns.connect(db) as (conn, backend):
        conn.execute(
            "UPDATE claims SET review_status = 'accepted' WHERE post_id = 'a1'"
        )
        conn.commit()

    rows = ns.fetch_scored_outcomes(db_path=db)
    assert len(rows) == 1
    assert rows[0]["review_status"] == "accepted"
    assert rows[0]["weights_version"]
    assert rows[0]["reach_component"] is not None


def test_a_reviewed_post_contributes_one_training_row_per_post(db):
    """Every scheduled run appends a fresh score. Joining them all would weight
    a frequently re-ingested post far above one seen once."""
    record = make_post("a1", "Bleach cures autism", 500, 100)
    ingest([record], db, at(0))
    ingest([record], db, at(6))
    ingest([record], db, at(12))

    with ns.connect(db) as (conn, _backend):
        conn.execute("UPDATE claims SET review_status='accepted' WHERE post_id='a1'")
        conn.commit()

    rows = ns.fetch_scored_outcomes(db_path=db)
    assert len(rows) == 1
    assert rows[0]["post_id"] == "a1"


def test_reach_actually_moves_the_score(db):
    """The reach weight is the largest of the three, so a high-reach post must
    outrank an identical low-reach one."""
    quiet = ledger.score_records([make_post("a1", "Bleach cures autism", 1, 0)])
    loud = ledger.score_records([make_post("a2", "Bleach cures autism", 9000, 9000)])
    assert loud[0]["score"] > quiet[0]["score"]
    assert loud[0]["components"]["reach"] == 1.0
    assert quiet[0]["components"]["reach"] < 0.01


def test_the_exploration_slot_is_recorded_and_roughly_the_configured_rate(db):
    records = [make_post(f"p{i}", f"Claim number {i}") for i in range(200)]
    entries = ledger.score_records(records, exploration_rate=0.1, rng=random.Random(5))
    explored = sum(1 for e in entries if e["selected_by"] == "exploration")
    assert 5 <= explored <= 35  # ~10% of 200, generous band for a seeded draw


def test_exploration_rate_of_zero_selects_nothing(db):
    entries = ledger.score_records(
        [make_post("a1", "Bleach cures autism")], exploration_rate=0.0
    )
    assert entries[0]["selected_by"] == "rank"


# --- rollup ----------------------------------------------------------------


def test_rollup_aggregates_reach_posts_and_platforms(db):
    ingest([
        make_post("a1", "Bleach cures autism", 100, 20),
        make_post("a2", "Bleach cures autism", 40, 10, source="reddit"),
        make_post("a3", "Bleach cures autism", 30, 5, source="mastodon"),
    ], db)

    (row,) = ns.fetch_narratives(db_path=db)
    assert row["post_count"] == 3
    assert row["reach"] == 205
    assert row["platform_count"] == 3


def test_rollup_can_filter_by_lifecycle_state(db):
    ingest([make_post("a1", "Bleach cures autism")], db)
    assert ns.fetch_narratives(lifecycle_state="nonexistent", db_path=db) == []


def test_narrative_status_can_be_set_and_missing_ids_report_failure(db):
    ingest([make_post("a1", "Bleach cures autism")], db)
    narrative_id = ns.fetch_narratives(db_path=db)[0]["narrative_id"]

    assert ns.set_narrative_status(narrative_id, "reviewing", db_path=db) is True
    assert ns.fetch_narrative(narrative_id, db_path=db)["status"] == "reviewing"
    assert ns.set_narrative_status("nope", "reviewing", db_path=db) is False


def test_fetch_narrative_returns_none_when_missing(db):
    assert ns.fetch_narrative("nope", db_path=db) is None


# --- evidence and responses ------------------------------------------------


def test_evidence_attaches_to_the_narrative_and_upserts(db):
    ingest([make_post("a1", "Bleach cures autism")], db)
    narrative_id = ns.fetch_narratives(db_path=db)[0]["narrative_id"]

    ns.attach_evidence(narrative_id, [
        {"id": "pubmed:1", "title": "A study", "url": "u", "publisher": "PubMed",
         "passage": "first", "relevance_score": 0.4},
    ], db_path=db)
    ns.attach_evidence(narrative_id, [
        {"id": "pubmed:1", "title": "A study", "url": "u", "publisher": "PubMed",
         "passage": "second", "relevance_score": 0.9},
    ], db_path=db)

    (row,) = ns.fetch_narrative_evidence(narrative_id, db_path=db)
    assert row["passage"] == "second"
    assert row["relevance_score"] == 0.9


def test_a_response_is_saved_as_a_draft_and_needs_a_named_approver(db):
    ingest([make_post("a1", "Bleach cures autism")], db)
    narrative_id = ns.fetch_narratives(db_path=db)[0]["narrative_id"]

    saved = ns.save_response(
        narrative_id, "debunk", "protocol", {"fact": "Bleach is not a treatment."},
        ["pubmed:1"], "contradicted", db_path=db,
    )
    assert saved["status"] == "draft"

    decided = ns.decide_response(
        saved["response_id"], "approved", "dr-lee", "checked", db_path=db
    )
    assert decided["status"] == "approved"
    assert decided["approved_by"] == "dr-lee"
    assert decided["draft"]["fact"] == "Bleach is not a treatment."


def test_deciding_an_unknown_response_returns_none(db):
    assert ns.decide_response("nope", "approved", "dr-lee", db_path=db) is None


def test_a_response_cannot_be_decided_into_an_arbitrary_state(db):
    with pytest.raises(ValueError):
        ns.decide_response("any", "published", "dr-lee", db_path=db)
