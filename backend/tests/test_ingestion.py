import json

import pytest

from ingestion import _validate, get_posts


def _valid_post(**overrides):
    post = {
        "id": "1",
        "text": "hello",
        "timestamp": "2026-07-01T00:00:00Z",
        "username": "bob",
        "retweet_count": 0,
        "like_count": 0,
    }
    post.update(overrides)
    return post


def test_validate_accepts_a_well_formed_post():
    _validate([_valid_post()])  # must not raise


def test_validate_rejects_a_non_list():
    with pytest.raises(ValueError):
        _validate({"id": "1"})


def test_validate_reports_missing_required_fields():
    with pytest.raises(ValueError) as exc:
        _validate([{"id": "1", "text": "hi"}])

    assert "missing required fields" in str(exc.value)


def test_get_posts_raises_for_a_missing_file():
    with pytest.raises(FileNotFoundError):
        get_posts("/no/such/file.json")


def test_get_posts_rejects_an_unsupported_extension(tmp_path):
    bad = tmp_path / "posts.txt"
    bad.write_text("whatever", encoding="utf-8")

    with pytest.raises(ValueError):
        get_posts(str(bad))


def test_get_posts_loads_a_valid_json_file(tmp_path):
    path = tmp_path / "posts.json"
    path.write_text(json.dumps([_valid_post(id="42")]), encoding="utf-8")

    posts = get_posts(str(path))

    assert posts[0]["id"] == "42"


def test_get_posts_reads_csv_and_casts_engagement_counts(tmp_path):
    path = tmp_path / "posts.csv"
    path.write_text(
        "id,text,timestamp,username,retweet_count,like_count\n"
        "9,hi,2026-07-01T00:00:00Z,carol,4,10\n",
        encoding="utf-8",
    )

    posts = get_posts(str(path))

    assert posts[0]["retweet_count"] == 4
    assert posts[0]["like_count"] == 10
    assert isinstance(posts[0]["like_count"], int)
