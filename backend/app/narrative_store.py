"""Persistence for the narrative ledger.

``storage.py`` owns the claims table, which is one row per post. This module
owns everything that makes a *narrative* durable and comparable over time:

``narratives``          one row per rumor, with a centroid and lifecycle state
``engagement_snapshots``append-only reach observations, so growth is measurable
``claim_scores``        the triage score at decision time, with its weights
``narrative_evidence``  evidence attached to the rumor, not to a single post
``responses``           counter-message drafts and their approval state

Two things to know about the design:

*Append-only where it matters.* The claims table upserts on ``post_id``, which
means a re-ingest overwrites ``like_count`` and ``retweet_count`` and the
previous value is gone. That is why growth was not measurable before. Snapshots
and scores are never updated in place, so the history survives re-ingestion.

*Both backends, same shape.* SQLite keeps vectors as JSON text and does
brute-force cosine in Python; Postgres keeps them as pgvector columns and lets
the HNSW index do the nearest-neighbor search. Everything above this module
sees one API, so the whole pipeline is demoable locally with no cloud services.
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import config
import narratives as nar

SQLITE = "sqlite"
POSTGRES = "postgres"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def backend_name() -> str:
    return POSTGRES if config.DATABASE_URL else SQLITE


# --- schema ----------------------------------------------------------------

_SQLITE_DDL = (
    """
    CREATE TABLE IF NOT EXISTS narratives (
        narrative_id TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        canonical_claim TEXT NOT NULL,
        topic TEXT,
        centroid TEXT,
        member_count INTEGER NOT NULL DEFAULT 0,
        first_seen_at TEXT,
        last_seen_at TEXT,
        lifecycle_state TEXT DEFAULT 'watching',
        status TEXT DEFAULT 'watching',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS engagement_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id TEXT NOT NULL,
        narrative_id TEXT,
        observed_at TEXT NOT NULL,
        like_count INTEGER NOT NULL DEFAULT 0,
        retweet_count INTEGER NOT NULL DEFAULT 0,
        UNIQUE (post_id, observed_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS claim_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id TEXT NOT NULL,
        narrative_id TEXT,
        scored_at TEXT NOT NULL,
        score REAL NOT NULL,
        reach_component REAL,
        harm_component REAL,
        uncertainty_component REAL,
        weights_version TEXT NOT NULL,
        rule_version TEXT,
        model_name TEXT,
        selected_by TEXT NOT NULL DEFAULT 'rank'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS narrative_evidence (
        narrative_id TEXT NOT NULL,
        evidence_id TEXT NOT NULL,
        title TEXT,
        url TEXT,
        publisher TEXT,
        passage TEXT,
        published_at TEXT,
        relevance_score REAL,
        attached_at TEXT NOT NULL,
        PRIMARY KEY (narrative_id, evidence_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS responses (
        response_id TEXT PRIMARY KEY,
        narrative_id TEXT NOT NULL,
        mode TEXT NOT NULL,
        protocol TEXT NOT NULL,
        draft_json TEXT NOT NULL,
        citations TEXT,
        evidence_state TEXT,
        status TEXT NOT NULL DEFAULT 'draft',
        author TEXT,
        approved_by TEXT,
        review_note TEXT,
        created_at TEXT NOT NULL,
        decided_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS snapshots_post_idx ON engagement_snapshots(post_id)",
    "CREATE INDEX IF NOT EXISTS snapshots_narrative_idx ON engagement_snapshots(narrative_id)",
    "CREATE INDEX IF NOT EXISTS scores_post_idx ON claim_scores(post_id)",
    "CREATE INDEX IF NOT EXISTS responses_narrative_idx ON responses(narrative_id)",
)

_SQLITE_CLAIM_MIGRATIONS = (
    "ALTER TABLE claims ADD COLUMN narrative_id TEXT",
    "ALTER TABLE claims ADD COLUMN claim_embedding TEXT",
)


def _postgres_ddl() -> Tuple[str, ...]:
    dims = config.EMBEDDING_DIMENSIONS
    return (
        "CREATE EXTENSION IF NOT EXISTS vector",
        f"""
        CREATE TABLE IF NOT EXISTS narratives (
            narrative_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            canonical_claim TEXT NOT NULL,
            topic TEXT,
            centroid vector({dims}),
            member_count INTEGER NOT NULL DEFAULT 0,
            first_seen_at TIMESTAMPTZ,
            last_seen_at TIMESTAMPTZ,
            lifecycle_state TEXT DEFAULT 'watching',
            status TEXT DEFAULT 'watching',
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS narratives_centroid_idx
        ON narratives USING hnsw (centroid vector_cosine_ops)
        """,
        """
        CREATE TABLE IF NOT EXISTS engagement_snapshots (
            id BIGSERIAL PRIMARY KEY,
            post_id TEXT NOT NULL,
            narrative_id TEXT,
            observed_at TIMESTAMPTZ NOT NULL,
            like_count INTEGER NOT NULL DEFAULT 0,
            retweet_count INTEGER NOT NULL DEFAULT 0,
            UNIQUE (post_id, observed_at)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS claim_scores (
            id BIGSERIAL PRIMARY KEY,
            post_id TEXT NOT NULL,
            narrative_id TEXT,
            scored_at TIMESTAMPTZ NOT NULL,
            score REAL NOT NULL,
            reach_component REAL,
            harm_component REAL,
            uncertainty_component REAL,
            weights_version TEXT NOT NULL,
            rule_version TEXT,
            model_name TEXT,
            selected_by TEXT NOT NULL DEFAULT 'rank'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS narrative_evidence (
            narrative_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            title TEXT, url TEXT, publisher TEXT, passage TEXT,
            published_at TEXT, relevance_score REAL,
            attached_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (narrative_id, evidence_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS responses (
            response_id TEXT PRIMARY KEY,
            narrative_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            protocol TEXT NOT NULL,
            draft_json JSONB NOT NULL,
            citations TEXT[],
            evidence_state TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            author TEXT, approved_by TEXT, review_note TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            decided_at TIMESTAMPTZ
        )
        """,
        "CREATE INDEX IF NOT EXISTS snapshots_post_idx ON engagement_snapshots(post_id)",
        "CREATE INDEX IF NOT EXISTS snapshots_narrative_idx ON engagement_snapshots(narrative_id)",
        "CREATE INDEX IF NOT EXISTS scores_post_idx ON claim_scores(post_id)",
        "CREATE INDEX IF NOT EXISTS responses_narrative_idx ON responses(narrative_id)",
        "ALTER TABLE claims ADD COLUMN IF NOT EXISTS narrative_id TEXT",
        f"ALTER TABLE claims ADD COLUMN IF NOT EXISTS claim_embedding vector({dims})",
    )


@contextmanager
def connect(db_path: Optional[str] = None):
    """Yield (connection, backend) with the narrative schema guaranteed."""
    if backend_name() == POSTGRES:
        import psycopg
        conn = psycopg.connect(config.DATABASE_URL)
        try:
            with conn.cursor() as cur:
                for statement in _postgres_ddl():
                    cur.execute(statement)
            conn.commit()
            yield conn, POSTGRES
        finally:
            conn.close()
        return

    path = Path(db_path or config.DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        import storage
        conn.execute(storage.SCHEMA)
        conn.execute(storage.AUDIT_SCHEMA)
        for statement in _SQLITE_DDL:
            conn.execute(statement)
        # Storage's own column migrations run here too. A database created
        # before `source` or `review_status` existed is exactly what the
        # backfill script is pointed at, and several narrative queries select
        # those columns.
        for statement in (*storage._MIGRATIONS, *_SQLITE_CLAIM_MIGRATIONS):
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()
        yield conn, SQLITE
    finally:
        conn.close()


def _q(sql: str, backend: str) -> str:
    """Translate the ``?`` placeholder to the backend's dialect."""
    return sql.replace("?", "%s") if backend == POSTGRES else sql


def _rows(cur, backend: str) -> List[Dict[str, Any]]:
    if backend == POSTGRES:
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    return [dict(row) for row in cur.fetchall()]


def _encode_vector(vec: Optional[Sequence[float]], backend: str):
    if vec is None:
        return None
    if backend == POSTGRES:
        return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
    return json.dumps([round(float(v), 6) for v in vec])


def _decode_vector(raw: Any) -> List[float]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [float(v) for v in raw]
    text = str(raw).strip()
    if not text:
        return []
    try:
        return [float(v) for v in json.loads(text)]
    except (ValueError, TypeError):
        return []


def _cursor(conn, backend: str):
    """A real cursor for both drivers; sqlite3 cursors inherit row_factory."""
    return conn.cursor()


# --- narrative assignment --------------------------------------------------


def fetch_all_canonical(conn, backend: str) -> List[Dict[str, Any]]:
    """Canonical claim text per narrative, for borderline-pair adjudication."""
    cur = _cursor(conn, backend)
    cur.execute("SELECT narrative_id, canonical_claim FROM narratives")
    return _rows(cur, backend)


def _load_candidates(conn, backend: str) -> List[nar.Candidate]:
    cur = _cursor(conn, backend)
    cur.execute("SELECT narrative_id, centroid, member_count FROM narratives")
    return [
        nar.Candidate(
            narrative_id=row["narrative_id"],
            centroid=_decode_vector(row["centroid"]),
            member_count=int(row["member_count"] or 1),
        )
        for row in _rows(cur, backend)
    ]


def clustering_quality() -> Dict[str, Any]:
    """Disclose how trustworthy the current grouping is.

    The offline hashed embedding cannot separate same-rumor pairs from
    different-rumor pairs (0.57 AUC on the labeled set), so in that mode the
    threshold is set to merge only near-identical wording. Surfacing this in
    the API means the UI can say so instead of presenting weak grouping as if
    it were semantic.
    """
    mode = nar.embedding_mode()
    managed = mode == "managed"
    return {
        "embedding_mode": mode,
        "threshold": nar.default_threshold(),
        "semantic": managed,
        "adjudication": managed,
        "note": (
            "Managed embeddings with model adjudication on borderline pairs."
            if managed else
            "Offline fallback: near-duplicate wording only. Set GOOGLE_API_KEY "
            "for semantic grouping."
        ),
    }


def _adjudicator():
    """A Gemini client for borderline pairs, or None when unavailable."""
    if not config.GOOGLE_API_KEY:
        return None
    try:
        from google import genai
        from google.genai import types
        return genai.Client(
            api_key=config.GOOGLE_API_KEY,
            http_options=types.HttpOptions(timeout=config.GEMINI_TIMEOUT_MS),
        )
    except Exception:
        return None


def assign_narratives(
    records: List[Dict[str, Any]],
    db_path: Optional[str] = None,
    threshold: Optional[float] = None,
    adjudicate: bool = True,
) -> List[Dict[str, Any]]:
    """Attach a ``narrative_id`` to every record, creating narratives as needed.

    Mutates and returns the records so the caller can persist them with the
    assignment already on board. Assignment is a single online pass in reach
    order, so the highest-reach post in a group tends to define the centroid
    and the label rather than whichever post happened to arrive first.
    """
    if not records:
        return records
    threshold = nar.default_threshold() if threshold is None else threshold
    ordered = sorted(
        records,
        key=lambda r: int(r.get("like_count") or 0) + int(r.get("retweet_count") or 0),
        reverse=True,
    )
    texts = [r.get("claim") or r.get("text") or "" for r in ordered]
    vectors = nar.embed(texts)

    client = _adjudicator() if adjudicate else None
    canonical: Dict[str, str] = {}

    with connect(db_path) as (conn, backend):
        candidates = _load_candidates(conn, backend)
        by_id = {c.narrative_id: c for c in candidates}
        for existing in fetch_all_canonical(conn, backend):
            canonical[existing["narrative_id"]] = existing["canonical_claim"]
        touched: Dict[str, Dict[str, Any]] = {}

        for record, text, vector in zip(ordered, texts, vectors):
            decision = nar.assign(vector, list(by_id.values()), threshold)
            # A borderline merge is where the embedding is least reliable and
            # where a wrong answer does the most damage, so spend one model
            # call there and nowhere else.
            if client and decision.ambiguous and decision.narrative_id:
                verdict = nar.adjudicate_pair(
                    client, text, canonical.get(decision.narrative_id, "")
                )
                if verdict is False:
                    decision = nar.Assignment(
                        narrative_id=None, similarity=decision.similarity,
                        is_new=True, ambiguous=True,
                    )
                    record["narrative_adjudicated"] = "split"
                elif verdict is True:
                    record["narrative_adjudicated"] = "merged"
            if decision.is_new:
                narrative_id = nar.narrative_id_for(text)
                # Collision is possible only for byte-identical claims, which
                # should share a narrative anyway; suffix defensively.
                if narrative_id in by_id:
                    narrative_id = f"{narrative_id}-{uuid.uuid4().hex[:4]}"
                by_id[narrative_id] = nar.Candidate(narrative_id, list(vector), 1)
                canonical[narrative_id] = text
                touched[narrative_id] = {
                    "canonical_claim": text,
                    "claims": [text],
                    "topic": record.get("topic"),
                    "new": True,
                }
            else:
                narrative_id = decision.narrative_id
                existing = by_id[narrative_id]
                existing.centroid = nar.merge_centroid(
                    existing.centroid, existing.member_count, vector
                )
                existing.member_count += 1
                entry = touched.setdefault(
                    narrative_id, {"claims": [], "topic": record.get("topic"), "new": False}
                )
                entry["claims"].append(text)

            record["narrative_id"] = narrative_id
            record["claim_embedding"] = vector
            record["narrative_similarity"] = round(decision.similarity, 4)
            record["narrative_ambiguous"] = decision.ambiguous

        _upsert_narratives(conn, backend, by_id, touched, records)
        conn.commit()
    return records


def _upsert_narratives(conn, backend, by_id, touched, records) -> None:
    """Write centroids, member counts, and first/last seen for touched rows."""
    cur = _cursor(conn, backend)
    now = _now()
    members: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        members.setdefault(record["narrative_id"], []).append(record)

    for narrative_id, info in touched.items():
        candidate = by_id[narrative_id]
        group = members.get(narrative_id, [])
        stamps = sorted(
            s for s in (r.get("timestamp") for r in group) if s
        )
        first_seen = stamps[0] if stamps else now
        last_seen = stamps[-1] if stamps else now

        cur.execute(
            _q("SELECT canonical_claim, first_seen_at, member_count FROM narratives "
               "WHERE narrative_id = ?", backend),
            (narrative_id,),
        )
        existing = _rows(cur, backend)
        claim_texts = [r.get("claim") or r.get("text") or "" for r in group]
        if existing:
            prior = existing[0]
            claim_texts.append(prior["canonical_claim"])
            prior_first = prior.get("first_seen_at")
            if prior_first:
                first_seen = min(first_seen, _as_text(prior_first))
        label = nar.label_for(claim_texts)
        canonical = label

        if existing:
            cur.execute(
                _q("UPDATE narratives SET label = ?, canonical_claim = ?, "
                   "centroid = ?, member_count = ?, first_seen_at = ?, "
                   "last_seen_at = ?, updated_at = ? WHERE narrative_id = ?", backend),
                (
                    label, canonical, _encode_vector(candidate.centroid, backend),
                    candidate.member_count, first_seen, last_seen, now, narrative_id,
                ),
            )
        else:
            cur.execute(
                _q("INSERT INTO narratives (narrative_id, label, canonical_claim, "
                   "topic, centroid, member_count, first_seen_at, last_seen_at, "
                   "lifecycle_state, status, created_at, updated_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", backend),
                (
                    narrative_id, label, canonical, info.get("topic"),
                    _encode_vector(candidate.centroid, backend),
                    candidate.member_count, first_seen, last_seen,
                    "watching", "watching", now, now,
                ),
            )


def _as_text(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def persist_claim_narratives(
    records: Sequence[Dict[str, Any]], db_path: Optional[str] = None
) -> None:
    """Write narrative_id and the claim embedding back onto the claims rows."""
    rows = [r for r in records if r.get("narrative_id")]
    if not rows:
        return
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        for record in rows:
            cur.execute(
                _q("UPDATE claims SET narrative_id = ?, claim_embedding = ? "
                   "WHERE post_id = ?", backend),
                (
                    record["narrative_id"],
                    _encode_vector(record.get("claim_embedding"), backend),
                    record["post_id"],
                ),
            )
        conn.commit()


# --- snapshots -------------------------------------------------------------


def record_snapshots(
    records: Sequence[Dict[str, Any]],
    observed_at: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Append one engagement observation per post.

    Append-only on purpose. The claims table upserts and overwrites engagement,
    so without this table there is exactly one reach number per post and growth
    cannot be computed at all.
    """
    if not records:
        return 0
    observed_at = observed_at or _now()
    written = 0
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        conflict = (
            "ON CONFLICT (post_id, observed_at) DO NOTHING"
            if backend == POSTGRES
            else "ON CONFLICT(post_id, observed_at) DO NOTHING"
        )
        for record in records:
            cur.execute(
                _q(
                    "INSERT INTO engagement_snapshots "
                    "(post_id, narrative_id, observed_at, like_count, retweet_count) "
                    f"VALUES (?, ?, ?, ?, ?) {conflict}",
                    backend,
                ),
                (
                    record.get("post_id"),
                    record.get("narrative_id"),
                    observed_at,
                    int(record.get("like_count") or 0),
                    int(record.get("retweet_count") or 0),
                ),
            )
            written += 1
        conn.commit()
    return written


def fetch_snapshots(
    narrative_id: str, db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        cur.execute(
            _q("SELECT post_id, observed_at, like_count, retweet_count "
               "FROM engagement_snapshots WHERE narrative_id = ? "
               "ORDER BY observed_at ASC", backend),
            (narrative_id,),
        )
        rows = _rows(cur, backend)
    for row in rows:
        row["observed_at"] = _as_text(row["observed_at"])
    return rows


# --- scores ----------------------------------------------------------------


def record_scores(
    entries: Sequence[Dict[str, Any]], db_path: Optional[str] = None
) -> int:
    """Append the triage score that was in force when a claim was surfaced.

    This is the feedback-loop dataset. Without a row here there is nothing to
    correlate a later analyst decision against, and no way to tell whether a
    retune actually improved anything.
    """
    if not entries:
        return 0
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        for entry in entries:
            components = entry.get("components") or {}
            cur.execute(
                _q(
                    "INSERT INTO claim_scores (post_id, narrative_id, scored_at, "
                    "score, reach_component, harm_component, uncertainty_component, "
                    "weights_version, rule_version, model_name, selected_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    backend,
                ),
                (
                    entry.get("post_id"),
                    entry.get("narrative_id"),
                    entry.get("scored_at") or _now(),
                    float(entry.get("score") or 0),
                    components.get("reach"),
                    components.get("potential_harm"),
                    components.get("uncertainty"),
                    entry.get("weights_version") or config.WEIGHTS_VERSION,
                    entry.get("rule_version"),
                    entry.get("model_name") or config.MODEL_NAME,
                    entry.get("selected_by") or "rank",
                ),
            )
        conn.commit()
    return len(entries)


def fetch_scored_outcomes(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Join stored scores to the analyst decision that eventually landed.

    One row per scored claim that a human has since ruled on. ``selected_by``
    marks whether the analyst saw it because it ranked highly or because it was
    drawn into the random exploration slot; only the latter is an unbiased
    sample of analyst judgment.
    """
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        # One row per post, not per ingest run. Every scheduled run appends a
        # fresh score, so joining them all would weight a post that happened to
        # be re-ingested ten times ten times as heavily as one seen once. The
        # most recent score is the one the analyst was looking at.
        cur.execute(
            _q(
                "SELECT s.post_id, s.narrative_id, s.score, s.reach_component, "
                "s.harm_component, s.uncertainty_component, s.weights_version, "
                "s.selected_by, s.scored_at, c.review_status, c.reviewed_at, c.claim "
                "FROM claim_scores s JOIN claims c ON c.post_id = s.post_id "
                "WHERE c.review_status IS NOT NULL "
                "AND c.review_status <> 'unreviewed' "
                "AND s.id = (SELECT MAX(inner_s.id) FROM claim_scores inner_s "
                "            WHERE inner_s.post_id = s.post_id) "
                "ORDER BY s.scored_at ASC",
                backend,
            ),
            (),
        )
        rows = _rows(cur, backend)
    for row in rows:
        for key in ("scored_at", "reviewed_at"):
            if row.get(key) is not None:
                row[key] = _as_text(row[key])
    return rows


# --- narratives ------------------------------------------------------------


def fetch_narratives(
    limit: int = 100,
    topic: Optional[str] = None,
    lifecycle_state: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Narrative rollup: one row per rumor with aggregate reach and members."""
    where, params = ["1=1"], []
    if topic:
        where.append("n.topic = ?")
        params.append(topic)
    if lifecycle_state:
        where.append("n.lifecycle_state = ?")
        params.append(lifecycle_state)
    params.append(limit)

    sql = (
        "SELECT n.narrative_id, n.label, n.canonical_claim, n.topic, "
        "n.member_count, n.first_seen_at, n.last_seen_at, n.lifecycle_state, "
        "n.status, "
        "COALESCE(SUM(COALESCE(c.like_count,0)+COALESCE(c.retweet_count,0)),0) AS reach, "
        "COUNT(c.post_id) AS post_count, "
        "COUNT(DISTINCT c.source) AS platform_count, "
        "MAX(c.confidence) AS max_confidence, "
        "SUM(CASE WHEN c.review_status IS NOT NULL "
        "         AND c.review_status <> 'unreviewed' THEN 1 ELSE 0 END) AS reviewed_count "
        "FROM narratives n LEFT JOIN claims c ON c.narrative_id = n.narrative_id "
        f"WHERE {' AND '.join(where)} "
        "GROUP BY n.narrative_id, n.label, n.canonical_claim, n.topic, "
        "n.member_count, n.first_seen_at, n.last_seen_at, n.lifecycle_state, n.status "
        "ORDER BY reach DESC, post_count DESC LIMIT ?"
    )
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        cur.execute(_q(sql, backend), tuple(params))
        rows = _rows(cur, backend)
    for row in rows:
        for key in ("first_seen_at", "last_seen_at"):
            if row.get(key) is not None:
                row[key] = _as_text(row[key])
        row["reach"] = int(row.get("reach") or 0)
        row["post_count"] = int(row.get("post_count") or 0)
    return rows


def fetch_narrative_members(
    narrative_id: str, db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        cur.execute(
            _q(
                "SELECT post_id, username, text, claim, topic, confidence, source, "
                "timestamp, timestamp_processed, like_count, retweet_count, status, "
                "fact_check_verdict, fact_check_source, fact_check_url, "
                "review_status, review_note, reviewer, reviewed_at, evidence_state "
                "FROM claims WHERE narrative_id = ? "
                "ORDER BY (COALESCE(like_count,0)+COALESCE(retweet_count,0)) DESC",
                backend,
            ),
            (narrative_id,),
        )
        rows = _rows(cur, backend)
    for row in rows:
        for key in ("timestamp", "timestamp_processed", "reviewed_at"):
            if row.get(key) is not None:
                row[key] = _as_text(row[key])
    return rows


def fetch_narrative(
    narrative_id: str, db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        cur.execute(
            _q("SELECT narrative_id, label, canonical_claim, topic, member_count, "
               "first_seen_at, last_seen_at, lifecycle_state, status, created_at, "
               "updated_at FROM narratives WHERE narrative_id = ?", backend),
            (narrative_id,),
        )
        rows = _rows(cur, backend)
    if not rows:
        return None
    row = rows[0]
    for key in ("first_seen_at", "last_seen_at", "created_at", "updated_at"):
        if row.get(key) is not None:
            row[key] = _as_text(row[key])
    return row


def refresh_lifecycle(
    narrative_id: str, now: Optional[datetime] = None, db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Recompute growth and lifecycle state from stored snapshots."""
    narrative = fetch_narrative(narrative_id, db_path=db_path)
    if not narrative:
        return {}
    snapshots = fetch_snapshots(narrative_id, db_path=db_path)
    traj = nar.trajectory(snapshots)
    # Dormancy means we have stopped *observing* activity, not that no member
    # post was authored recently. A months-old post that is spiking today is
    # very much alive, so the last observation wins over the post timestamp.
    last_observed = (
        snapshots[-1]["observed_at"] if snapshots else narrative.get("last_seen_at")
    )
    state = nar.lifecycle_state(
        traj,
        first_seen_at=narrative.get("first_seen_at"),
        last_seen_at=last_observed,
        now=now,
    )
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        # Recount members from the claims table rather than trusting the
        # running counter used to blend centroids: re-ingesting the same post
        # bumps that counter again, so it drifts above the real post count.
        cur.execute(
            _q("SELECT COUNT(*) AS n FROM claims WHERE narrative_id = ?", backend),
            (narrative_id,),
        )
        rows = _rows(cur, backend)
        member_count = int(rows[0]["n"]) if rows else 0
        cur.execute(
            _q("UPDATE narratives SET lifecycle_state = ?, member_count = ?, "
               "updated_at = ? WHERE narrative_id = ?", backend),
            (state, member_count, _now(), narrative_id),
        )
        conn.commit()
    return {"narrative_id": narrative_id, "lifecycle_state": state,
            "member_count": member_count, "trajectory": traj.as_dict()}


def set_narrative_status(
    narrative_id: str, status: str, db_path: Optional[str] = None
) -> bool:
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        cur.execute(
            _q("UPDATE narratives SET status = ?, updated_at = ? "
               "WHERE narrative_id = ?", backend),
            (status, _now(), narrative_id),
        )
        changed = cur.rowcount
        conn.commit()
    return bool(changed)


# --- narrative evidence ----------------------------------------------------


def attach_evidence(
    narrative_id: str,
    evidence: Iterable[Dict[str, Any]],
    db_path: Optional[str] = None,
) -> int:
    rows = [e for e in evidence if e.get("id")]
    if not rows:
        return 0
    now = _now()
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        conflict = (
            "ON CONFLICT (narrative_id, evidence_id) DO UPDATE SET "
            "relevance_score = EXCLUDED.relevance_score, passage = EXCLUDED.passage"
            if backend == POSTGRES
            else "ON CONFLICT(narrative_id, evidence_id) DO UPDATE SET "
                 "relevance_score = excluded.relevance_score, passage = excluded.passage"
        )
        for item in rows:
            cur.execute(
                _q(
                    "INSERT INTO narrative_evidence (narrative_id, evidence_id, title, "
                    "url, publisher, passage, published_at, relevance_score, attached_at) "
                    f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) {conflict}",
                    backend,
                ),
                (
                    narrative_id, item.get("id"), item.get("title"), item.get("url"),
                    item.get("publisher"), item.get("passage"), item.get("published_at"),
                    item.get("relevance_score"), now,
                ),
            )
        conn.commit()
    return len(rows)


def fetch_narrative_evidence(
    narrative_id: str, db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        cur.execute(
            _q("SELECT evidence_id AS id, title, url, publisher, passage, "
               "published_at, relevance_score FROM narrative_evidence "
               "WHERE narrative_id = ? "
               "ORDER BY COALESCE(relevance_score, 0) DESC", backend),
            (narrative_id,),
        )
        return _rows(cur, backend)


# --- responses -------------------------------------------------------------


def save_response(
    narrative_id: str,
    mode: str,
    protocol: str,
    draft: Dict[str, Any],
    citations: Sequence[str],
    evidence_state: str,
    author: str = "system",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    response_id = uuid.uuid4().hex
    created = _now()
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        cur.execute(
            _q(
                "INSERT INTO responses (response_id, narrative_id, mode, protocol, "
                "draft_json, citations, evidence_state, status, author, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)",
                backend,
            ),
            (
                response_id, narrative_id, mode, protocol,
                json.dumps(draft),
                list(citations) if backend == POSTGRES else json.dumps(list(citations)),
                evidence_state, author, created,
            ),
        )
        conn.commit()
    return {
        "response_id": response_id, "narrative_id": narrative_id, "mode": mode,
        "protocol": protocol, "draft": draft, "citations": list(citations),
        "evidence_state": evidence_state, "status": "draft", "author": author,
        "created_at": created,
    }


def decide_response(
    response_id: str,
    status: str,
    approved_by: str,
    note: str = "",
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Approve or reject a draft. Only approved drafts may be exported."""
    if status not in {"approved", "rejected"}:
        raise ValueError("status must be approved or rejected")
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        cur.execute(
            _q("UPDATE responses SET status = ?, approved_by = ?, review_note = ?, "
               "decided_at = ? WHERE response_id = ?", backend),
            (status, approved_by, note[:2000], _now(), response_id),
        )
        changed = cur.rowcount
        conn.commit()
    if not changed:
        return None
    return fetch_response(response_id, db_path=db_path)


def fetch_response(
    response_id: str, db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        cur.execute(
            _q("SELECT * FROM responses WHERE response_id = ?", backend),
            (response_id,),
        )
        rows = _rows(cur, backend)
    return _hydrate_response(rows[0]) if rows else None


def fetch_responses(
    narrative_id: Optional[str] = None,
    status: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    where, params = ["1=1"], []
    if narrative_id:
        where.append("narrative_id = ?")
        params.append(narrative_id)
    if status:
        where.append("status = ?")
        params.append(status)
    with connect(db_path) as (conn, backend):
        cur = _cursor(conn, backend)
        cur.execute(
            _q(f"SELECT * FROM responses WHERE {' AND '.join(where)} "
               "ORDER BY created_at DESC", backend),
            tuple(params),
        )
        rows = _rows(cur, backend)
    return [_hydrate_response(row) for row in rows]


def _hydrate_response(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    draft = out.pop("draft_json", None)
    if isinstance(draft, str):
        try:
            draft = json.loads(draft)
        except ValueError:
            draft = {}
    out["draft"] = draft or {}
    citations = out.get("citations")
    if isinstance(citations, str):
        try:
            citations = json.loads(citations)
        except ValueError:
            citations = []
    out["citations"] = citations or []
    for key in ("created_at", "decided_at"):
        if out.get(key) is not None:
            out[key] = _as_text(out[key])
    return out
