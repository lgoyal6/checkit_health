import json

from classifier import (
    _classify_one,
    _normalize,
    _parse_json,
    filter_for_review,
    is_falsifiable,
)


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    """Minimal stand-in for genai client.models with a scripted reply."""

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


# --- _parse_json ----------------------------------------------------------


def test_parse_json_reads_a_bare_object():
    assert _parse_json('{"label": "NOISE"}') == {"label": "NOISE"}


def test_parse_json_strips_markdown_json_fences():
    assert _parse_json('```json\n{"label": "NOISE"}\n```') == {"label": "NOISE"}


def test_parse_json_strips_bare_fences():
    assert _parse_json('```\n{"label": "NOISE"}\n```') == {"label": "NOISE"}


# --- _normalize -----------------------------------------------------------


def test_normalize_rejects_an_unknown_label():
    assert _normalize({"label": "BANANA"})["label"] == "NOISE"


def test_normalize_coerces_an_unknown_topic_to_other():
    assert _normalize({"label": "MEDICAL_CLAIM", "topic": "aliens"})["topic"] == "other"


def test_normalize_clamps_confidence_into_range():
    assert _normalize({"label": "MEDICAL_CLAIM", "confidence": 1.7})["confidence"] == 1.0
    assert _normalize({"label": "MEDICAL_CLAIM", "confidence": -0.3})["confidence"] == 0.0


def test_normalize_parses_a_stringified_confidence():
    assert _normalize({"label": "MEDICAL_CLAIM", "confidence": "0.5"})["confidence"] == 0.5


def test_normalize_drops_a_non_numeric_confidence():
    assert _normalize({"label": "MEDICAL_CLAIM", "confidence": "high"})["confidence"] is None


# --- _classify_one --------------------------------------------------------


def test_classify_one_returns_a_normalized_classification():
    payload = json.dumps(
        {"label": "MEDICAL_CLAIM", "claim": "X cures Y.", "topic": "drug", "confidence": 0.9}
    )

    result = _classify_one(_FakeClient(text=payload), "X cures Y.")

    assert result["label"] == "MEDICAL_CLAIM"
    assert result["topic"] == "drug"
    assert result["confidence"] == 0.9


def test_classify_one_falls_back_to_noise_on_error():
    client = _FakeClient(exc=RuntimeError("429 RESOURCE_EXHAUSTED"))

    result = _classify_one(client, "anything", max_attempts=1)

    assert result["label"] == "NOISE"
    assert "429" in result["error"]


# --- filter_for_review ----------------------------------------------------


def _post(label, confidence):
    return {"classification": {"label": label, "confidence": confidence}}


def test_filter_keeps_confident_medical_claims():
    kept = filter_for_review([_post("MEDICAL_CLAIM", 0.8)])
    assert len(kept) == 1


def test_filter_drops_low_confidence_and_non_claims():
    posts = [
        _post("MEDICAL_CLAIM", 0.4),
        _post("GENERAL_HEALTH", 0.99),
        _post("MEDICAL_CLAIM", None),
    ]
    assert filter_for_review(posts) == []


# --- is_falsifiable (fail-open) -------------------------------------------


def test_falsifiability_rejects_an_empty_claim():
    assert is_falsifiable("   ") is False


def test_falsifiability_fails_open_on_error():
    client = _FakeClient(exc=RuntimeError("network down"))
    assert is_falsifiable("Vitamin C prevents colds.", client=client) is True


def test_falsifiability_reads_the_model_answer():
    client = _FakeClient(text='{"falsifiable": true}')
    assert is_falsifiable("Vitamin C prevents colds.", client=client) is True
