import json

import pytest

from claim_report import _normalize, generate_claim_report


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, text=None, exc=None):
        self._text = text
        self._exc = exc

    def generate_content(self, **_kwargs):
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, text=None, exc=None):
        self.models = _FakeModels(text=text, exc=exc)


def test_normalize_defaults_an_invalid_confidence_level_to_low():
    report = _normalize({"confidence_level": "certain"})
    assert report.confidence_level == "low"


def test_normalize_coerces_key_facts_to_a_clean_list():
    report = _normalize({"key_facts": ["  a fact  ", "", "  "]})
    assert report.key_facts == ["a fact"]


def test_normalize_handles_key_facts_that_are_not_a_list():
    report = _normalize({"key_facts": "not a list"})
    assert report.key_facts == []


def test_generate_report_returns_a_structured_response():
    payload = json.dumps(
        {
            "rumor": "Does X cure Y?",
            "confidence_level": "high",
            "summary": "No.",
            "key_facts": ["fact one", "fact two"],
            "analysis": "detail",
            "conclusion": "unsupported",
        }
    )

    report = generate_claim_report(_FakeClient(text=payload), claim_text="X cures Y")

    assert report.rumor == "Does X cure Y?"
    assert report.confidence_level == "high"
    assert len(report.key_facts) == 2


def test_generate_report_raises_after_exhausting_retries():
    client = _FakeClient(exc=RuntimeError("429 RESOURCE_EXHAUSTED"))

    with pytest.raises(Exception):
        generate_claim_report(client, claim_text="X cures Y", max_attempts=1)
