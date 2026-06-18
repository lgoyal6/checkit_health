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
    status TEXT NOT NULL DEFAULT 'pending_fact_check'
);
"""


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
        conn.executemany(
            """
            INSERT OR REPLACE INTO claims
              (post_id, username, text, timestamp, retweet_count, like_count,
               claim, topic, confidence, timestamp_processed, status)
            VALUES
              (:post_id, :username, :text, :timestamp, :retweet_count, :like_count,
               :claim, :topic, :confidence, :timestamp_processed, :status)
            """,
            records,
        )
        conn.commit()
    finally:
        conn.close()
