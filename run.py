"""Checkit Health pipeline CLI.

Usage:
    python run.py --input data/posts.json --output output/claims.json
"""

import argparse
import sys

import config
from ingestion import get_posts
from classifier import classify_posts, filter_for_review
from storage import build_records, write_json, write_sqlite


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Checkit Health misinformation pipeline")
    p.add_argument("--input", default=config.DEFAULT_INPUT_PATH,
                   help="Path to input file (.json or .csv)")
    p.add_argument("--output", default=config.DEFAULT_OUTPUT_PATH,
                   help="Path to output JSON file")
    p.add_argument("--db", default=config.DEFAULT_DB_PATH,
                   help="Path to SQLite database")
    p.add_argument("--dry-run", action="store_true",
                   help="Skip Anthropic calls; useful for ingestion sanity checks")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    posts = get_posts(args.input)
    ingested = len(posts)

    if args.dry_run:
        print(f"[dry-run] Ingested {ingested} posts from {args.input}")
        return 0

    if not config.ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY env var not set.", file=sys.stderr)
        return 1

    classified = classify_posts(posts)
    medical_claims = [p for p in classified if p["classification"]["label"] == "MEDICAL_CLAIM"]
    filtered = filter_for_review(classified)

    records = build_records(filtered)
    write_json(records, args.output)
    write_sqlite(records, args.db)

    print("=" * 56)
    print("Checkit Health pipeline summary")
    print("=" * 56)
    print(f"  Posts ingested:               {ingested}")
    print(f"  Classified as MEDICAL_CLAIM:  {len(medical_claims)}")
    print(f"  Passed confidence >= {config.CONFIDENCE_THRESHOLD}:    {len(filtered)}")
    print(f"  Wrote JSON:                   {args.output}")
    print(f"  Wrote SQLite:                 {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
