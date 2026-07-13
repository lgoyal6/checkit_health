"""Stage 3: Output to Postgres (Supabase), SQLite, and JSON."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

import config

# Columns shared by every backend, in insert order.
_COLUMNS = (
    "post_id", "username", "text", "timestamp", "retweet_count", "like_count",
    "claim", "topic", "confidence", "timestamp_processed", "status",
    "fact_check_verdict", "fact_check_source", "fact_check_url", "source",
    "classification_reasoning",
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
    source TEXT DEFAULT 'file',
    classification_reasoning TEXT
);
"""

# Columns added after the original schema shipped. Applied on every write so
# databases created by earlier runs pick them up without a manual migration.
_MIGRATIONS = (
    "ALTER TABLE claims ADD COLUMN fact_check_verdict TEXT",
    "ALTER TABLE claims ADD COLUMN fact_check_source TEXT",
    "ALTER TABLE claims ADD COLUMN fact_check_url TEXT",
    "ALTER TABLE claims ADD COLUMN source TEXT DEFAULT 'file'",
    "ALTER TABLE claims ADD COLUMN classification_reasoning TEXT",
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
            "classification_reasoning": c.get("reasoning"),
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
               fact_check_verdict, fact_check_source, fact_check_url, source,
               classification_reasoning)
            VALUES
              (:post_id, :username, :text, :timestamp, :retweet_count, :like_count,
               :claim, :topic, :confidence, :timestamp_processed, :status,
               :fact_check_verdict, :fact_check_source, :fact_check_url, :source,
               :classification_reasoning)
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


def _ensure_postgres_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
        cur.execute("ALTER TABLE claims ADD COLUMN IF NOT EXISTS fact_check_verdict TEXT")
        cur.execute("ALTER TABLE claims ADD COLUMN IF NOT EXISTS fact_check_source TEXT")
        cur.execute("ALTER TABLE claims ADD COLUMN IF NOT EXISTS fact_check_url TEXT")
        cur.execute("ALTER TABLE claims ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'file'")
        cur.execute("ALTER TABLE claims ADD COLUMN IF NOT EXISTS classification_reasoning TEXT")


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
        _ensure_postgres_schema(conn)
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
        "fact_check_url, source, classification_reasoning FROM claims "
        "ORDER BY timestamp_processed DESC LIMIT %s"
    )
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        _ensure_postgres_schema(conn)
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall()
    # timestamp_processed comes back as datetime; make it JSON-friendly.
    for r in rows:
        ts = r.get("timestamp_processed")
        if hasattr(ts, "isoformat"):
            r["timestamp_processed"] = ts.isoformat()
    return rows


_WINDOW_INTERVALS = {
    "24h": "24 hours",
    "7d": "7 days",
    "30d": "30 days",
}


def _window_predicate(window: str) -> tuple[str, List[Any]]:
    if window == "all":
        return "", []
    interval = _WINDOW_INTERVALS.get(window)
    if not interval:
        raise ValueError("window must be one of: 24h, 7d, 30d, all")
    return "AND timestamp_processed::timestamptz > now() - %s::interval", [interval]


def _json_ready(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for row in rows:
        for key, value in list(row.items()):
            if hasattr(value, "isoformat"):
                row[key] = value.isoformat()
    return rows


def fetch_trending(
    window: str,
    topic: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Read monitored claims ranked by social reach from Postgres."""
    import psycopg
    from psycopg.rows import dict_row

    where = ["COALESCE(source, '') <> 'web'"]
    params: List[Any] = []
    window_sql, window_params = _window_predicate(window)
    if window_sql:
        where.append(window_sql.removeprefix("AND "))
        params.extend(window_params)
    if topic:
        where.append("topic = %s")
        params.append(topic)
    if source:
        where.append("source = %s")
        params.append(source)
    params.append(limit)

    sql = (
        "SELECT post_id, username, text, timestamp, retweet_count, like_count, "
        "claim, topic, confidence, timestamp_processed, status, "
        "fact_check_verdict, fact_check_source, fact_check_url, source, "
        "classification_reasoning, "
        "(COALESCE(like_count, 0) + COALESCE(retweet_count, 0)) AS reach "
        "FROM claims "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY (COALESCE(like_count, 0) + COALESCE(retweet_count, 0)) DESC "
        "LIMIT %s"
    )
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        _ensure_postgres_schema(conn)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return _json_ready(rows)


def fetch_stats(window: str) -> Dict[str, Any]:
    """Aggregate monitor KPI counts and reach from Postgres."""
    import psycopg
    from psycopg.rows import dict_row

    where = ["COALESCE(source, '') <> 'web'"]
    params: List[Any] = []
    window_sql, window_params = _window_predicate(window)
    if window_sql:
        where.append(window_sql.removeprefix("AND "))
        params.extend(window_params)
    where_sql = " AND ".join(where)

    likely_false_terms = [
        "%false%",
        "%untrue%",
        "%debunk%",
        "%no evidence%",
        "%incorrect%",
        "%myth%",
        "%hoax%",
        "%misinformation%",
        "%baseless%",
        "%unfounded%",
        "%not true%",
    ]
    verdict_predicate = " OR ".join(["fact_check_verdict ILIKE %s"] * len(likely_false_terms))

    sql_totals = (
        "SELECT COUNT(*) AS flagged_claims, "
        "COALESCE(SUM(COALESCE(like_count, 0) + COALESCE(retweet_count, 0)), 0) AS total_reach, "
        "COUNT(*) FILTER (WHERE status = 'verified') AS verified_count, "
        f"COUNT(*) FILTER (WHERE fact_check_verdict IS NOT NULL AND ({verdict_predicate})) AS likely_false_count "
        "FROM claims "
        f"WHERE {where_sql}"
    )
    sql_group = (
        "SELECT {group_col} AS name, COUNT(*) AS count, "
        "COALESCE(SUM(COALESCE(like_count, 0) + COALESCE(retweet_count, 0)), 0) AS reach "
        "FROM claims "
        f"WHERE {where_sql} "
        "GROUP BY {group_col} "
        "ORDER BY reach DESC, count DESC"
    )

    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        _ensure_postgres_schema(conn)
        with conn.cursor() as cur:
            cur.execute(sql_totals, [*likely_false_terms, *params])
            totals = dict(cur.fetchone() or {})
            cur.execute(sql_group.format(group_col="topic"), params)
            by_topic = cur.fetchall()
            cur.execute(sql_group.format(group_col="source"), params)
            by_source = cur.fetchall()

    return {
        "flagged_claims": int(totals.get("flagged_claims") or 0),
        "total_reach": int(totals.get("total_reach") or 0),
        "verified_count": int(totals.get("verified_count") or 0),
        "likely_false_count": int(totals.get("likely_false_count") or 0),
        "by_topic": _json_ready(by_topic),
        "by_source": _json_ready(by_source),
    }
