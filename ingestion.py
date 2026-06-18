"""Stage 1: Data ingestion.

Reads posts from a local JSON or CSV file. The `get_posts` function is the
single integration point — swap its body for an X API call later without
touching the rest of the pipeline. See README for the data contract.
"""

import csv
import json
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
