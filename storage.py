"""Stage 3: Output to Postgres (Supabase), SQLite, and JSON."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any

import config

# Columns shared by every backend, in insert order.
_COLUMNS = (
    "post_id", "username", "text", "timestamp", "retweet_count", "like_count",
    "claim", "topic", "confidence", "timestamp_processed", "status",
    "fact_check_verdict", "fact_check_source", "fact_check_url", "source",
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    post_id TEXT PRIMARY KEY,
    username TEXT,
    text TEXT NOT NULL,
    timestamp TEXT,
    retweet_count INTEGER,
    like_count INTEGER,
    claim TEXT NOT NULL,
    topic TEXT,
    confidence REAL,
    timestamp_processed TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_fact_check',
    fact_check_verdict TEXT,
    fact_check_source TEXT,
    fact_check_url TEXT,
    source TEXT DEFAULT 'file'
);
"""

# Columns added after the original schema shipped. Applied on every write so
# databases created by earlier runs pick them up without a manual migration.
_MIGRATIONS = (
    "ALTER TABLE claims ADD COLUMN fact_check_verdict TEXT",
    "ALTER TABLE claims ADD COLUMN fact_check_source TEXT",
    "ALTER TABLE claims ADD COLUMN fact_check_url TEXT",
    "ALTER TABLE claims ADD COLUMN source TEXT DEFAULT 'file'",
)


def build_records(filtered: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    processed_at = datetime.now(timezone.utc).isoformat()
    records = []
    for post in filtered:
        c = post["classification"]
        records.append({
            "post_id": post["id"],
            "username": post["username"],
            "text": post["text"],
            "timestamp": post["timestamp"],
            "retweet_count": post["retweet_count"],
            "like_count": post["like_count"],
            "claim": c["claim"] or post["text"],
            "topic": c["topic"],
            "confidence": c["confidence"],
            "timestamp_processed": processed_at,
            "status": "pending_fact_check",
            "fact_check_verdict": None,
            "fact_check_source": None,
            "fact_check_url": None,
            "source": post.get("source", "file"),
        })
    return records


def write_json(records: List[Dict[str, Any]], path: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def write_sqlite(records: List[Dict[str, Any]], db_path: str) -> None:
    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        conn.execute(SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.executemany(
            """
            INSERT OR REPLACE INTO claims
              (post_id, username, text, timestamp, retweet_count, like_count,
               claim, topic, confidence, timestamp_processed, status,
               fact_check_verdict, fact_check_source, fact_check_url, source)
            VALUES
              (:post_id, :username, :text, :timestamp, :retweet_count, :like_count,
               :claim, :topic, :confidence, :timestamp_processed, :status,
               :fact_check_verdict, :fact_check_source, :fact_check_url, :source)
            """,
            [{**{c: None for c in _COLUMNS}, **r} for r in records],
        )
        conn.commit()
    finally:
        conn.close()


# --- Postgres (Supabase) backend ------------------------------------------
#
# Used whenever config.DATABASE_URL is set. Both the CLI pipeline and the API
# share this so scheduled ingestion and live web checks land in one table that
# survives redeploys (unlike SQLite on Render's ephemeral disk).


def postgres_enabled() -> bool:
    return bool(config.DATABASE_URL)


def _connect_postgres():
    import psycopg  # imported lazily so SQLite-only runs don't need the driver
    return psycopg.connect(config.DATABASE_URL)


def write_postgres(records: List[Dict[str, Any]]) -> None:
    """Upsert records into the Postgres claims table (on post_id)."""
    if not records:
        return
    cols = ", ".join(_COLUMNS)
    placeholders = ", ".join(f"%({c})s" for c in _COLUMNS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _COLUMNS if c != "post_id")
    sql = (
        f"INSERT INTO claims ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT (post_id) DO UPDATE SET {updates}"
    )
    rows = [{**{c: None for c in _COLUMNS}, **r} for r in records]
    with _connect_postgres() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()


def fetch_recent(limit: int = 50) -> List[Dict[str, Any]]:
    """Read the most recent claims from Postgres, newest first."""
    import psycopg
    from psycopg.rows import dict_row

    sql = (
        "SELECT post_id, username, text, timestamp, claim, topic, confidence, "
        "timestamp_processed, status, fact_check_verdict, fact_check_source, "
        "fact_check_url, source FROM claims "
        "ORDER BY timestamp_processed DESC LIMIT %s"
    )
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall()
    # timestamp_processed comes back as datetime; make it JSON-friendly.
    for r in rows:
        ts = r.get("timestamp_processed")
        if hasattr(ts, "isoformat"):
            r["timestamp_processed"] = ts.isoformat()
    return rows
