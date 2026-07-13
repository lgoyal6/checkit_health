"""Checkit Health pipeline CLI.

Usage:
    python run.py --input data/posts.json --output output/claims.json
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import config
from ingestion import get_posts
from classifier import classify_posts, filter_for_review
from storage import build_records, write_json, write_sqlite, write_postgres, postgres_enabled


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
    p.add_argument("--limit", type=int, default=None,
                   help="Only process the first N posts (smoke-test the pipeline cheaply)")
    p.add_argument("--debug", action="store_true",
                   help="Print every classification and dump raw results to output/debug.json")
    p.add_argument("--source", choices=["file", "reddit", "bluesky", "mastodon", "youtube"],
                   default="file",
                   help="Where to pull posts from (default: file via --input)")
    p.add_argument("--query", default=None,
                   help="Search query when --source bluesky/mastodon/youtube. "
                        "If omitted, MONITOR_QUERIES are used.")
    p.add_argument("--subreddit", default=None,
                   help="Subreddit to scrape when --source reddit (e.g. conspiracy)")
    p.add_argument("--reddit-limit", type=int, default=50,
                   help="Max posts to pull when --source reddit")
    p.add_argument("--bluesky-limit", type=int, default=50,
                   help="Max posts to pull per Bluesky query")
    p.add_argument("--mastodon-limit", type=int, default=50,
                   help="Max posts to pull per Mastodon query")
    p.add_argument("--mastodon-instance", default=None,
                   help="Mastodon instance base URL (default: MASTODON_INSTANCE_URL "
                        "env var, or https://mastodon.social)")
    p.add_argument("--youtube-limit", type=int, default=50,
                   help="Max videos to pull per YouTube query")
    p.add_argument("--skip-prefilter", action="store_true",
                   help="Bypass the keyword pre-filter and send every post to the classifier")
    p.add_argument("--skip-fact-check", action="store_true",
                   help="Skip the Google Fact Check lookup stage")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.source == "reddit":
        if not args.subreddit:
            print("ERROR: --source reddit requires --subreddit.", file=sys.stderr)
            return 1
        from ingestion import get_posts_reddit
        posts = get_posts_reddit(args.subreddit, args.reddit_limit)
        source_desc = f"r/{args.subreddit}"
    elif args.source == "bluesky":
        from ingestion import get_posts_bluesky
        queries = [args.query] if args.query else config.MONITOR_QUERIES
        seen = set()
        posts = []
        for query in queries:
            for post in get_posts_bluesky(query, limit=args.bluesky_limit, sort="top"):
                post_id = post["id"]
                if post_id in seen:
                    continue
                seen.add(post_id)
                posts.append(post)
        source_desc = args.query or ", ".join(queries)
    elif args.source == "mastodon":
        from ingestion import get_posts_mastodon
        queries = [args.query] if args.query else config.MONITOR_QUERIES
        seen = set()
        posts = []
        for query in queries:
            for post in get_posts_mastodon(
                query, limit=args.mastodon_limit, instance_url=args.mastodon_instance
            ):
                post_id = post["id"]
                if post_id in seen:
                    continue
                seen.add(post_id)
                posts.append(post)
        source_desc = args.query or ", ".join(queries)
    elif args.source == "youtube":
        from ingestion import get_posts_youtube
        queries = [args.query] if args.query else config.MONITOR_QUERIES
        seen = set()
        posts = []
        for query in queries:
            for post in get_posts_youtube(query, limit=args.youtube_limit):
                post_id = post["id"]
                if post_id in seen:
                    continue
                seen.add(post_id)
                posts.append(post)
        source_desc = args.query or ", ".join(queries)
    else:
        posts = get_posts(args.input)
        source_desc = args.input
    for p in posts:
        p.setdefault("source", args.source)
    posts.sort(
        key=lambda p: int(p.get("like_count") or 0) + int(p.get("retweet_count") or 0),
        reverse=True,
    )
    if args.limit is not None:
        posts = posts[: args.limit]
    ingested = len(posts)

    # Stage 1.5: cheap keyword pre-filter (before any LLM spend).
    prefiltered_out = 0
    if not args.skip_prefilter:
        from prefilter import is_possibly_medical
        kept = [p for p in posts if is_possibly_medical(p["text"])]
        prefiltered_out = len(posts) - len(kept)
        posts = kept

    if args.dry_run:
        print(f"[dry-run] Ingested {ingested} posts from {source_desc}")
        if not args.skip_prefilter:
            print(f"[dry-run] Filtered by keyword pre-check: {prefiltered_out}")
            print(f"[dry-run] Would classify: {len(posts)}")
        return 0

    if not config.GOOGLE_API_KEY:
        print("ERROR: GOOGLE_API_KEY env var not set.", file=sys.stderr)
        return 1

    checkpoint_path = str(Path(args.output).parent / "checkpoint.json")

    def progress(i, total, row, skipped):
        c = row["classification"]
        conf = f"{c['confidence']:.2f}" if c['confidence'] is not None else "n/a"
        tag = "[resumed]" if skipped else "         "
        snippet = row["text"][:60].replace("\n", " ")
        print(f"  {tag} [{i}/{total}] [{c['label']:<15}] conf={conf}  {snippet!r}", flush=True)

    print(f"Classifying {len(posts)} posts (checkpoint: {checkpoint_path})")
    classified = classify_posts(posts, checkpoint_path=checkpoint_path, on_progress=progress)

    label_counts = Counter(p["classification"]["label"] for p in classified)
    error_count = sum(1 for p in classified if p["classification"].get("error"))
    filtered = filter_for_review(classified)

    # Stage 2.5: falsifiability second pass — drop claims that aren't checkable
    # against evidence, even if they cleared the confidence gate.
    from classifier import is_falsifiable
    falsifiable = [p for p in filtered if is_falsifiable(p["classification"]["claim"] or p["text"])]

    if args.debug:
        debug_path = Path(args.output).parent / "debug.json"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        with debug_path.open("w", encoding="utf-8") as f:
            json.dump(classified, f, indent=2, ensure_ascii=False)
        print(f"[debug] full classifications dumped to {debug_path}")
        for p in classified:
            c = p["classification"]
            conf = f"{c['confidence']:.2f}" if c['confidence'] is not None else "n/a"
            err = f"  ERROR: {c['error'][:80]}" if c.get("error") else ""
            print(f"  [{c['label']:<15}] conf={conf}  {p['text'][:70]!r}{err}")

    records = build_records(falsifiable)

    # Stage 2.75: attach existing fact-check verdicts where they exist.
    fact_checked_count = 0
    if not args.skip_fact_check:
        from fact_checker import check_claim
        for rec in records:
            result = check_claim(rec["claim"])
            if result:
                rec["fact_check_verdict"] = result.get("verdict")
                rec["fact_check_source"] = result.get("publisher")
                rec["fact_check_url"] = result.get("url")
                rec["status"] = "verified"
                fact_checked_count += 1
            else:
                rec["status"] = "unverified"

    write_json(records, args.output)
    write_sqlite(records, args.db)
    wrote_postgres = False
    if postgres_enabled():
        write_postgres(records)
        wrote_postgres = True

    print("=" * 56)
    print("Checkit Health pipeline summary")
    print("=" * 56)
    print(f"  Posts ingested:               {ingested}")
    if not args.skip_prefilter:
        print(f"  Filtered by keyword pre-check:{prefiltered_out:>4}")
    for label, count in sorted(label_counts.items()):
        print(f"  Classified as {label:<15} {count}")
    if error_count:
        print(f"  Classifier errors:            {error_count}  <-- re-run with --debug to inspect")
    print(f"  Passed confidence >= {config.CONFIDENCE_THRESHOLD}:    {len(filtered)}")
    print(f"  Passed falsifiability check:  {len(falsifiable)}")
    if not args.skip_fact_check:
        print(f"  Matched existing fact-check:  {fact_checked_count}")
    print(f"  Wrote JSON:                   {args.output}")
    print(f"  Wrote SQLite:                 {args.db}")
    if wrote_postgres:
        print(f"  Wrote Postgres:               {len(records)} rows upserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())