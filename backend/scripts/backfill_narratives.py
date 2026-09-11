"""Fold existing claims into the narrative ledger.

The ledger is populated going forward by every ingest run, but claims stored
before it existed have no narrative, no engagement snapshot, and no recorded
triage score. This assigns all three retrospectively so an existing deployment
gets a populated narrative queue without waiting for the corpus to be
re-collected.

Safe to re-run: narrative assignment is idempotent, snapshots are unique on
(post_id, observed_at), and only claims missing a narrative are reassigned
unless ``--all`` is passed.

    cd backend
    PYTHONPATH=app python scripts/backfill_narratives.py --dry-run
    PYTHONPATH=app python scripts/backfill_narratives.py

One caveat worth stating plainly: a backfilled snapshot records the engagement
figure as it stands *now*, not as it stood when the post was collected. Growth
rates only become meaningful from the first real ingest after the backfill,
because the history the claims table overwrote is genuinely gone.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import config  # noqa: E402
import ledger  # noqa: E402
import narrative_store as ns  # noqa: E402


def load_claims(db_path: str, only_unassigned: bool) -> list:
    if ns.backend_name() == ns.POSTGRES:
        import psycopg
        from psycopg.rows import dict_row

        where = "WHERE narrative_id IS NULL" if only_unassigned else ""
        with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT post_id, username, text, claim, topic, confidence, "
                    f"source, timestamp, like_count, retweet_count FROM claims {where}"
                )
                return [dict(row) for row in cur.fetchall()]

    # ns.connect applies storage's own column migrations as well as the
    # narrative DDL, which matters here: a database created before `source` or
    # `narrative_id` existed is exactly the case this script is for.
    where = "WHERE narrative_id IS NULL" if only_unassigned else ""
    with ns.connect(db_path) as (conn, backend):
        cur = conn.cursor()
        cur.execute(
            "SELECT post_id, username, text, claim, topic, confidence, source, "
            f"timestamp, like_count, retweet_count FROM claims {where}"
        )
        return [dict(row) for row in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=config.DEFAULT_DB_PATH)
    parser.add_argument("--all", action="store_true",
                        help="Reassign every claim, not only unassigned ones")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing")
    parser.add_argument("--no-adjudicate", action="store_true",
                        help="Skip the model pass on borderline pairs")
    args = parser.parse_args()

    claims = load_claims(args.db, only_unassigned=not args.all)
    quality = ns.clustering_quality()
    print(f"backend    : {ns.backend_name()}")
    print(f"clustering : {quality['embedding_mode']} @ {quality['threshold']}")
    if not quality["semantic"]:
        print("  note: the offline embedding merges near-identical wording only.")
        print("  Set GOOGLE_API_KEY before backfilling for semantic grouping,")
        print("  otherwise most rumors will land in their own narrative.")
    print(f"claims     : {len(claims)}")

    if not claims:
        print("Nothing to backfill.")
        return 0
    if args.dry_run:
        print("[dry-run] no changes written")
        return 0

    summary = ledger.ingest(
        claims, db_path=args.db, adjudicate=not args.no_adjudicate
    )
    print()
    print(f"  narratives touched   : {summary['narratives']}")
    print(f"  snapshots appended   : {summary['snapshots']}")
    print(f"  scores recorded      : {summary['scores']}")
    print(f"  exploration selected : {summary.get('exploration_selected', 0)}")
    print()
    print("Growth rates stay flat until the next real ingest: a backfilled")
    print("snapshot captures engagement as it is now, and the history the")
    print("claims table overwrote cannot be recovered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
