"""Stage 3: Output to SQLite and JSON."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any


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
    fact_check_url TEXT
);
"""

# Columns added after the original schema shipped. Applied on every write so
# databases created by earlier runs pick them up without a manual migration.
_MIGRATIONS = (
    "ALTER TABLE claims ADD COLUMN fact_check_verdict TEXT",
    "ALTER TABLE claims ADD COLUMN fact_check_source TEXT",
    "ALTER TABLE claims ADD COLUMN fact_check_url TEXT",
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
               fact_check_verdict, fact_check_source, fact_check_url)
            VALUES
              (:post_id, :username, :text, :timestamp, :retweet_count, :like_count,
               :claim, :topic, :confidence, :timestamp_processed, :status,
               :fact_check_verdict, :fact_check_source, :fact_check_url)
            """,
            records,
        )
        conn.commit()
    finally:
        conn.close()
