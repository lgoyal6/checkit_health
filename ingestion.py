"""Stage 1: Data ingestion.

Reads posts from a local JSON or CSV file. The `get_posts` function is the
single integration point — swap its body for an X API call later without
touching the rest of the pipeline. See README for the data contract.
"""

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any


REQUIRED_FIELDS = ("id", "text", "timestamp", "username", "retweet_count", "like_count")


def get_posts(source: str) -> List[Dict[str, Any]]:
    """Return a list of post dicts matching the documented contract.

    `source` is a path to a .json or .csv file. To swap in the X API, replace
    the body of this function with an API call that returns the same shape.
    """
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Input source not found: {source}")

    if path.suffix.lower() == ".json":
        posts = _load_json(path)
    elif path.suffix.lower() == ".csv":
        posts = _load_csv(path)
    else:
        raise ValueError(f"Unsupported input format: {path.suffix}")

    _validate(posts)
    return posts


def get_posts_reddit(subreddit: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Pull recent posts from a subreddit, mapped to the standard data contract.

    This is a drop-in alternative to `get_posts` — same output shape, different
    source. Requires PRAW and Reddit API credentials in the environment:
    REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, and REDDIT_USER_AGENT.

    Post title and body are combined into `text`. Reddit has no retweet concept,
    so `retweet_count` is filled with `num_comments` as a rough reach proxy and
    `like_count` with the post score.
    """
    import praw  # imported lazily so the file-based flow needs no reddit deps

    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user_agent = os.environ.get("REDDIT_USER_AGENT", "checkit-health/0.1")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Reddit ingestion needs REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET "
            "environment variables. Create an app at "
            "https://www.reddit.com/prefs/apps"
        )

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )

    posts: List[Dict[str, Any]] = []
    for submission in reddit.subreddit(subreddit).hot(limit=limit):
        if submission.stickied:
            continue
        body = (submission.title or "").strip()
        if submission.selftext:
            body = f"{body}\n\n{submission.selftext.strip()}"
        posts.append({
            "id": f"reddit_{submission.id}",
            "text": body,
            "timestamp": datetime.fromtimestamp(
                submission.created_utc, tz=timezone.utc
            ).isoformat(),
            "username": str(submission.author) if submission.author else "[deleted]",
            "retweet_count": int(submission.num_comments),
            "like_count": int(submission.score),
        })

    _validate(posts)
    return posts


def get_posts_bluesky(query: str, limit: int = 50, sort: str = "top") -> List[Dict[str, Any]]:
    """Search Bluesky posts and map them to the standard data contract.

    Requires BLUESKY_IDENTIFIER and BLUESKY_APP_PASSWORD in the environment.
    Uses the public XRPC API directly so this does not need a Bluesky SDK.
    """
    import requests  # imported lazily so file-based runs need no network deps

    identifier = os.environ.get("BLUESKY_IDENTIFIER")
    app_password = os.environ.get("BLUESKY_APP_PASSWORD")
    if not identifier or not app_password:
        raise RuntimeError(
            "Bluesky ingestion needs BLUESKY_IDENTIFIER and BLUESKY_APP_PASSWORD "
            "environment variables. Create an app password in Bluesky settings."
        )

    session_resp = requests.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": identifier, "password": app_password},
        timeout=20,
    )
    session_resp.raise_for_status()
    access_jwt = session_resp.json().get("accessJwt")
    if not access_jwt:
        raise RuntimeError("Bluesky session response did not include accessJwt")

    search_resp = requests.get(
        "https://bsky.social/xrpc/app.bsky.feed.searchPosts",
        params={"q": query, "sort": sort, "limit": limit},
        headers={"Authorization": f"Bearer {access_jwt}"},
        timeout=30,
    )
    search_resp.raise_for_status()

    posts: List[Dict[str, Any]] = []
    for item in search_resp.json().get("posts", []):
        record = item.get("record") or {}
        author = item.get("author") or {}
        posts.append({
            "id": item.get("uri") or item.get("cid"),
            "text": record.get("text") or "",
            "timestamp": record.get("createdAt") or "",
            "username": author.get("handle") or "",
            "retweet_count": int(item.get("repostCount") or 0),
            "like_count": int(item.get("likeCount") or 0),
        })

    _validate(posts)
    return posts


def _load_json(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_csv(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["retweet_count"] = int(row.get("retweet_count", 0) or 0)
            row["like_count"] = int(row.get("like_count", 0) or 0)
            out.append(row)
    return out


def _validate(posts: List[Dict[str, Any]]) -> None:
    if not isinstance(posts, list):
        raise ValueError("Input must be a list of post objects")
    for i, p in enumerate(posts):
        missing = [k for k in REQUIRED_FIELDS if k not in p]
        if missing:
            raise ValueError(f"Post at index {i} missing required fields: {missing}")
