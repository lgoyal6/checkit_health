import fact_checker
from fact_checker import _keywords, _overlap, check_claim


def test_overlap_accepts_a_close_fact_check_match():
    assert _overlap(
        "Ivermectin cures COVID-19.",
        "Fact check: Ivermectin does not cure COVID-19.",
    ) >= 0.5


def test_overlap_rejects_an_unrelated_fact_check():
    assert _overlap(
        "Ivermectin cures COVID-19.",
        "Vaccines contain microchips.",
    ) < 0.5


def test_keywords_drops_stopwords_and_short_tokens():
    assert _keywords("The vaccine is on it") == {"vaccine"}


class _FakeHTTPResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_check_claim_returns_none_without_a_key():
    assert check_claim("Ivermectin cures COVID-19.", api_key=None) is None


def test_check_claim_returns_the_best_matching_review(monkeypatch):
    payload = {
        "claims": [
            {
                "text": "Ivermectin does not cure COVID-19.",
                "claimReview": [
                    {
                        "publisher": {"name": "Example Health"},
                        "textualRating": "False",
                        "url": "https://example.test/iv",
                    }
                ],
            }
        ]
    }
    monkeypatch.setattr(
        fact_checker.requests, "get", lambda *a, **k: _FakeHTTPResponse(payload)
    )

    result = check_claim("Ivermectin cures COVID-19.", api_key="test-key")

    assert result["publisher"] == "Example Health"
    assert result["verdict"] == "False"
    assert result["url"] == "https://example.test/iv"


def test_check_claim_rejects_a_tangential_match(monkeypatch):
    payload = {
        "claims": [
            {
                "text": "Vaccines contain microchips.",
                "claimReview": [
                    {"publisher": {"name": "X"}, "textualRating": "False", "url": "u"}
                ],
            }
        ]
    }
    monkeypatch.setattr(
        fact_checker.requests, "get", lambda *a, **k: _FakeHTTPResponse(payload)
    )

    assert check_claim("Ivermectin cures COVID-19.", api_key="test-key") is None


def test_check_claim_swallows_network_errors(monkeypatch):
    def _boom(*_a, **_k):
        raise fact_checker.requests.RequestException("timeout")

    monkeypatch.setattr(fact_checker.requests, "get", _boom)

    assert check_claim("Ivermectin cures COVID-19.", api_key="test-key") is None
