"""Stage 1: Data ingestion.

Reads posts from a local JSON or CSV file. The `get_posts` function is the
single integration point — swap its body for an X API call later without
touching the rest of the pipeline. See README for the data contract.
"""

import csv
import json
import os
import re
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


def get_posts_mastodon(query: str, limit: int = 50, instance_url: str = None) -> List[Dict[str, Any]]:
    """Search public Mastodon statuses and map them to the standard data contract.

    Requires MASTODON_ACCESS_TOKEN in the environment (Settings -> Development
    -> New application on your instance; the `read:search` scope is enough).
    Defaults to the mastodon.social instance via MASTODON_INSTANCE_URL, but
    works against any instance you hold a token for. Mastodon has no repost
    concept in the search API response, so `retweet_count` is filled with
    `replies_count` as a reach proxy and `like_count` with `favourites_count`.
    """
    import requests  # imported lazily so file-based runs need no network deps

    access_token = os.environ.get("MASTODON_ACCESS_TOKEN")
    if not access_token:
        raise RuntimeError(
            "Mastodon ingestion needs a MASTODON_ACCESS_TOKEN environment "
            "variable. Create one under Settings -> Development on your "
            "instance (Preferences -> Development -> New application)."
        )
    base_url = (
        instance_url
        or os.environ.get("MASTODON_INSTANCE_URL")
        or "https://mastodon.social"
    ).rstrip("/")

    search_resp = requests.get(
        f"{base_url}/api/v2/search",
        params={"q": query, "type": "statuses", "limit": limit, "resolve": "false"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    search_resp.raise_for_status()

    posts: List[Dict[str, Any]] = []
    for status in search_resp.json().get("statuses", []):
        text = re.sub(r"<[^>]+>", " ", status.get("content") or "")
        text = re.sub(r"\s+", " ", text).strip()
        account = status.get("account") or {}
        posts.append({
            "id": f"mastodon_{status.get('id')}",
            "text": text,
            "timestamp": status.get("created_at") or "",
            "username": account.get("acct") or "",
            "retweet_count": int(status.get("reblogs_count") or 0),
            "like_count": int(status.get("favourites_count") or 0),
        })

    _validate(posts)
    return posts


def get_posts_youtube(query: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Search YouTube videos and map them to the standard data contract.

    Requires YOUTUBE_API_KEY in the environment (Google Cloud Console ->
    APIs & Services -> Credentials, with the YouTube Data API v3 enabled).
    Title and description are combined into `text`. YouTube has no repost
    concept, so `retweet_count` is filled with `viewCount` as a reach proxy
    and `like_count` with the video's actual `likeCount`.
    """
    import requests  # imported lazily so file-based runs need no network deps

    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "YouTube ingestion needs a YOUTUBE_API_KEY environment variable. "
            "Create one at https://console.cloud.google.com/apis/credentials "
            "with the YouTube Data API v3 enabled."
        )

    search_resp = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "key": api_key,
            "q": query,
            "part": "snippet",
            "type": "video",
            "maxResults": min(limit, 50),
            "order": "relevance",
        },
        timeout=30,
    )
    search_resp.raise_for_status()
    video_ids = [
        item["id"]["videoId"]
        for item in search_resp.json().get("items", [])
        if item.get("id", {}).get("videoId")
    ]
    if not video_ids:
        return []

    stats_resp = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"key": api_key, "id": ",".join(video_ids), "part": "snippet,statistics"},
        timeout=30,
    )
    stats_resp.raise_for_status()

    posts: List[Dict[str, Any]] = []
    for item in stats_resp.json().get("items", []):
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        title = (snippet.get("title") or "").strip()
        description = (snippet.get("description") or "").strip()
        text = f"{title}\n\n{description}" if description else title
        posts.append({
            "id": f"youtube_{item.get('id')}",
            "text": text,
            "timestamp": snippet.get("publishedAt") or "",
            "username": snippet.get("channelTitle") or "",
            "retweet_count": int(stats.get("viewCount") or 0),
            "like_count": int(stats.get("likeCount") or 0),
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
