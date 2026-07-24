import sqlite3

import config
import storage
from storage import _window_predicate, build_records, postgres_enabled, write_sqlite


def _classified_post(post_id="p1", claim="Vitamin C cures cancer.", confidence=0.9):
    return {
        "id": post_id,
        "username": "alice",
        "text": "raw post text",
        "timestamp": "2026-07-01T00:00:00Z",
        "retweet_count": 3,
        "like_count": 7,
        "source": "bluesky",
        "classification": {
            "claim": claim,
            "topic": "treatment",
            "confidence": confidence,
            "reasoning": "specific assertion",
        },
    }


def test_build_records_maps_fields_and_sets_pending_status():
    (record,) = build_records([_classified_post()])

    assert record["post_id"] == "p1"
    assert record["claim"] == "Vitamin C cures cancer."
    assert record["source"] == "bluesky"
    assert record["status"] == "pending_fact_check"


def test_build_records_defaults_claim_to_post_text():
    post = _classified_post(claim=None)

    (record,) = build_records([post])

    assert record["claim"] == "raw post text"


def test_write_sqlite_persists_and_upserts_on_post_id(tmp_path):
    db_path = str(tmp_path / "claims.db")
    records = build_records([_classified_post(confidence=0.9)])

    write_sqlite(records, db_path)
    # Re-write the same post_id with a new confidence: should replace, not dup.
    updated = build_records([_classified_post(confidence=0.5)])
    write_sqlite(updated, db_path)

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT confidence FROM claims WHERE post_id = 'p1'").fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0][0] == 0.5


def test_postgres_enabled_tracks_the_database_url(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", None)
    assert postgres_enabled() is False

    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://localhost/x")
    assert postgres_enabled() is True


def test_window_predicate_maps_known_windows():
    sql, params = _window_predicate("24h")
    assert "interval" in sql
    assert params == ["24 hours"]


def test_window_predicate_allows_all_with_no_filter():
    assert _window_predicate("all") == ("", [])


def test_window_predicate_rejects_an_unknown_window():
    try:
        _window_predicate("bogus")
    except ValueError as e:
        assert "window" in str(e)
    else:
        raise AssertionError("expected ValueError for an unknown window")


def test_json_ready_serializes_datetimes():
    from datetime import datetime, timezone

    rows = storage._json_ready([{"ts": datetime(2026, 7, 1, tzinfo=timezone.utc)}])

    assert rows[0]["ts"] == "2026-07-01T00:00:00+00:00"
