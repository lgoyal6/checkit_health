import csv
import io
import random
from datetime import datetime, timedelta, timezone

import pytest

import ledger
import narrative_store as ns
import reporting
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
def seeded(tmp_path):
    db = str(tmp_path / "claims.db")
    first = [
        make_post("a1", "Bleach cures autism", 100, 20),
        make_post("a2", "Bleach cures autism", 40, 10, source="reddit"),
    ]
    storage.write_sqlite(first, db)
    ledger.ingest(first, db_path=db, observed_at=at(0), exploration_rate=0.0,
                  rng=random.Random(1))
    second = [make_post("a1", "Bleach cures autism", 900, 260)]
    storage.write_sqlite(second, db)
    ledger.ingest(second, db_path=db, observed_at=at(6), exploration_rate=0.0,
                  rng=random.Random(2))

    narrative_id = ns.fetch_narratives(db_path=db)[0]["narrative_id"]
    ns.attach_evidence(narrative_id, [{
        "id": "pubmed:1", "title": "Chlorine dioxide", "url": "https://example.org/1",
        "publisher": "PubMed", "passage": "No therapeutic benefit was found.",
        "published_at": "2021", "relevance_score": 0.88,
    }], db_path=db)
    return db, narrative_id


# --- CSV -------------------------------------------------------------------


def test_claims_csv_has_a_stable_header_and_derives_reach():
    body = reporting.claims_csv([
        {"post_id": "a1", "claim": "x", "like_count": 10, "retweet_count": 5},
    ])
    rows = list(csv.DictReader(io.StringIO(body)))
    assert list(rows[0].keys()) == list(reporting.CSV_COLUMNS)
    assert rows[0]["reach"] == "15"


def test_claims_csv_handles_no_rows():
    body = reporting.claims_csv([])
    assert body.strip() == ",".join(reporting.CSV_COLUMNS)


def test_claims_csv_ignores_unexpected_columns():
    body = reporting.claims_csv([{"post_id": "a1", "surprise": "ignored"}])
    assert "surprise" not in body


def test_narratives_csv_exports_the_rollup(seeded):
    db, _ = seeded
    body = reporting.narratives_csv(ns.fetch_narratives(db_path=db))
    rows = list(csv.DictReader(io.StringIO(body)))
    assert rows[0]["post_count"] == "2"
    assert rows[0]["reach"] == "1210"


# --- situation report ------------------------------------------------------


def test_a_report_for_an_unknown_narrative_is_none(tmp_path):
    db = str(tmp_path / "claims.db")
    storage.write_sqlite([], db)
    assert reporting.build_situation_report("nope", db_path=db) is None


def test_the_report_assembles_members_evidence_and_trajectory(seeded):
    db, narrative_id = seeded
    report = reporting.build_situation_report(narrative_id, db_path=db)

    assert report["summary"]["post_count"] == 2
    assert report["summary"]["reach"] == 1210
    assert sorted(report["summary"]["platforms"]) == ["bluesky", "reddit"]
    assert [e["id"] for e in report["evidence"]] == ["pubmed:1"]
    assert report["trajectory"]["observations"] == 2
    assert report["trajectory"]["growth_per_hour"] > 0


def test_the_report_is_stamped_so_it_can_be_regenerated_identically(seeded):
    """Reproducibility is what separates this from a screenshot."""
    db, narrative_id = seeded
    prov = reporting.build_situation_report(narrative_id, db_path=db)["provenance"]

    assert prov["weights_version"]
    assert prov["escalation_rule_version"]
    assert prov["model"]
    assert prov["evidence_ids"] == ["pubmed:1"]
    assert prov["clustering"]["embedding_mode"] == "hashed_fallback"


def test_the_report_carries_the_not_medical_advice_disclaimer(seeded):
    db, narrative_id = seeded
    report = reporting.build_situation_report(narrative_id, db_path=db)
    assert "not medical advice" in report["disclaimer"]


def test_analyst_decisions_appear_in_the_report(seeded):
    db, narrative_id = seeded
    with ns.connect(db) as (conn, _backend):
        conn.execute(
            "UPDATE claims SET review_status='accepted', reviewer='dr-lee' "
            "WHERE post_id='a1'"
        )
        conn.commit()

    report = reporting.build_situation_report(narrative_id, db_path=db)
    assert report["summary"]["reviewed_count"] == 1
    assert report["reviews"][0]["reviewer"] == "dr-lee"


def test_the_strongest_evidence_state_wins_over_unretrieved_members(seeded):
    """One contradicted member must not be washed out by members nobody has
    retrieved evidence for yet."""
    db, narrative_id = seeded
    with ns.connect(db) as (conn, _backend):
        conn.execute(
            "UPDATE claims SET evidence_state='contradicted' WHERE post_id='a1'"
        )
        conn.commit()

    report = reporting.build_situation_report(narrative_id, db_path=db)
    assert report["summary"]["evidence_state"] == "contradicted"


# --- HTML ------------------------------------------------------------------


def test_the_html_report_renders_the_sections_an_analyst_needs(seeded):
    db, narrative_id = seeded
    html = reporting.render_html(
        reporting.build_situation_report(narrative_id, db_path=db)
    )
    for heading in ("At a glance", "Reach over time", "Evidence",
                    "Analyst decisions"):
        assert heading in html
    assert "pubmed:1" in html
    assert "not medical advice" in html


def test_the_html_report_plots_growth_once_there_are_two_observations(seeded):
    db, narrative_id = seeded
    html = reporting.render_html(
        reporting.build_situation_report(narrative_id, db_path=db)
    )
    assert "<svg" in html and "polyline" in html


def test_a_single_observation_says_so_instead_of_drawing_a_flat_line(tmp_path):
    db = str(tmp_path / "claims.db")
    records = [make_post("a1", "Bleach cures autism")]
    storage.write_sqlite(records, db)
    ledger.ingest(records, db_path=db, observed_at=at(0), exploration_rate=0.0)

    narrative_id = ns.fetch_narratives(db_path=db)[0]["narrative_id"]
    html = reporting.render_html(
        reporting.build_situation_report(narrative_id, db_path=db)
    )
    assert "Not enough observations" in html
    assert "polyline" not in html


def test_the_html_report_escapes_claim_text(tmp_path):
    """Claim text is attacker-controlled: it comes straight off a social
    platform and lands in a page an analyst opens."""
    db = str(tmp_path / "claims.db")
    records = [make_post("a1", "<script>alert('xss')</script> cures autism")]
    storage.write_sqlite(records, db)
    ledger.ingest(records, db_path=db, observed_at=at(0), exploration_rate=0.0)

    narrative_id = ns.fetch_narratives(db_path=db)[0]["narrative_id"]
    html = reporting.render_html(
        reporting.build_situation_report(narrative_id, db_path=db)
    )
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_no_evidence_is_reported_as_unchecked_not_as_disproof(tmp_path):
    db = str(tmp_path / "claims.db")
    records = [make_post("a1", "Bleach cures autism")]
    storage.write_sqlite(records, db)
    ledger.ingest(records, db_path=db, observed_at=at(0), exploration_rate=0.0)

    narrative_id = ns.fetch_narratives(db_path=db)[0]["narrative_id"]
    html = reporting.render_html(
        reporting.build_situation_report(narrative_id, db_path=db)
    )
    assert "not evidence the claim is true or false" in html
